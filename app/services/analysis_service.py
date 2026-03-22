"""Mail analysis service using Gemini-compatible providers."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from typing import Any, Callable

from app.ai.analyzer import (
    MailAnalysisResult,
    MailValidationResult,
    build_decision_payload,
    build_failed_analysis_fallback,
    build_rule_result,
    decide_final_category,
    normalize_analysis_payload,
    normalize_validation_payload,
    validate_analysis,
    validate_validation,
)
from app.ai.gemini_client import GeminiClient
from app.ai.prompts import PromptManager
from app.core.address_book import AddressBookService
from app.core.config_manager import AI_PROVIDER_HANLIM, AppConfig, ConfigManager
from app.db.repositories import MailRepository, now_iso


class AnalysisService:
    """Analyze pending mails and persist structured results."""

    VALIDATION_CONFIDENCE_THRESHOLD = 0.78
    SHORT_MAIL_THRESHOLD = 120

    def __init__(
        self,
        config_manager: ConfigManager,
        address_book_service: AddressBookService,
        prompt_manager: PromptManager,
        gemini_client: GeminiClient,
        mail_repository: MailRepository,
        logger: logging.Logger,
    ) -> None:
        self.config_manager = config_manager
        self.address_book_service = address_book_service
        self.prompt_manager = prompt_manager
        self.gemini_client = gemini_client
        self.mail_repository = mail_repository
        self.logger = logger
        self.response_schema = MailAnalysisResult.model_json_schema()
        self.validation_response_schema = MailValidationResult.model_json_schema()

    def get_unavailability_reason(self, config=None) -> str | None:
        """Return a user-facing reason when AI analysis cannot run."""

        active_config = config or self.address_book_service.merge_config_profile(self.config_manager.load())
        if not self.gemini_client.has_api_key(active_config):
            provider_label = "사내 AI 허브" if active_config.ai_provider == AI_PROVIDER_HANLIM else "Google AI"
            return f"{provider_label} API 키가 없어 AI 분석을 건너뛰었습니다."
        return None

    def analyze_pending_mails(
        self,
        limit: int = 10,
        include_failed: bool = False,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> tuple[int, int]:
        """Analyze backlog and return success/failure counts."""

        config = self.address_book_service.merge_config_profile(self.config_manager.load())
        unavailable_reason = self.get_unavailability_reason(config)
        if unavailable_reason:
            self.logger.warning(unavailable_reason)
            return 0, 0

        pending_mails = self.mail_repository.list_analysis_targets(limit=limit, include_failed=include_failed)
        success_count = 0
        failure_count = 0
        total_count = len(pending_mails)

        if progress_callback is not None:
            progress_callback(
                {
                    "stage": "analyzing",
                    "analysis_total": total_count,
                    "analysis_completed": 0,
                    "analysis_success_count": 0,
                    "analysis_failure_count": 0,
                }
            )

        for mail in pending_mails:
            current_user = self._build_current_user_context(config)
            routing_profile = self.address_book_service.resolve_user_routing_profile(config)
            rule_result = build_rule_result(
                user_email=config.user_email,
                known_recipient_addresses=list(routing_profile.direct_addresses),
                cc_only_recipient_addresses=list(routing_profile.cc_only_addresses),
                sender_email=mail.sender_email,
                to_list=mail.to_list,
                cc_list=mail.cc_list,
                subject=mail.subject,
                body_text=mail.body_text or mail.raw_preview,
                thread_id=mail.thread_key,
                message_id=mail.message_id,
            )
            try:
                result, audit_payload = self._analyze_mail(
                    config,
                    mail,
                    rule_result=rule_result,
                    current_user=current_user,
                )
                result_payload = build_decision_payload(
                    rule_result=rule_result,
                    analysis=result,
                    model_name=config.gemini_model,
                    analyzed_at=now_iso(),
                    raw_llm_json=json.dumps(audit_payload, ensure_ascii=False),
                    deadline_raw=(
                        str(audit_payload.get("final_deadline_raw") or "").strip() or None
                    ),
                )
                self.mail_repository.save_analysis_bundle(
                    mail.id,
                    result_payload,
                    mail_action_items=list(result_payload.get("mail_action_items", [])),
                    my_action_items=list(result_payload.get("my_action_items", [])),
                    due_date=result.due_date,
                    current_user_email=config.user_email,
                )
                success_count += 1
            except Exception as exc:  # noqa: BLE001
                self.logger.exception("Mail analysis failed for mail_id=%s: %s", mail.id, exc)
                fallback_payload = build_failed_analysis_fallback(
                    rule_result=rule_result,
                    fallback_subject=mail.subject,
                    error_message=str(exc),
                    model_name=config.gemini_model,
                    analyzed_at=now_iso(),
                )
                self.mail_repository.save_analysis_bundle(
                    mail.id,
                    fallback_payload,
                    mail_action_items=list(fallback_payload.get("mail_action_items", [])),
                    my_action_items=list(fallback_payload.get("my_action_items", [])),
                    due_date=None,
                    current_user_email=config.user_email,
                )
                failure_count += 1
            finally:
                if progress_callback is not None:
                    progress_callback(
                        {
                            "stage": "analyzing",
                            "analysis_total": total_count,
                            "analysis_completed": success_count + failure_count,
                            "analysis_success_count": success_count,
                            "analysis_failure_count": failure_count,
                        }
                    )

        return success_count, failure_count

    def _analyze_mail(self, config, mail, *, rule_result, current_user: dict[str, str] | None = None) -> tuple[MailAnalysisResult, dict[str, object]]:
        system_prompt = self.prompt_manager.build_system_prompt()
        thread_summary = self.mail_repository.build_thread_summary(mail.id)
        body_char_limit = min(
            12000,
            max(config.preview_max_chars, 4000) if config.store_raw_body else max(2000, config.preview_max_chars),
        )
        current_user = current_user or self._build_current_user_context(config)
        user_prompt = self.prompt_manager.build_user_prompt(
            mail,
            thread_summary=thread_summary,
            model_name=config.gemini_model,
            body_char_limit=body_char_limit,
            current_user=current_user,
            rule_context=asdict(rule_result),
        )
        prompt_input_hash = self._build_prompt_input_hash(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=self.response_schema,
        )
        last_error: Exception | None = None

        for _ in range(2):
            try:
                raw_result = self.gemini_client.generate_json(
                    config=config,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_schema=self.response_schema,
                )
                normalized_result = normalize_analysis_payload(
                    raw_result,
                    fallback_subject=mail.subject,
                    body_text=mail.body_text or mail.raw_preview,
                    thread_summary=thread_summary,
                    received_at=mail.received_at,
                )
                validated = validate_analysis(normalized_result)
                audit_payload: dict[str, object] = {
                    "prompt_input_hash": prompt_input_hash,
                    "first_pass_raw": raw_result,
                    "first_pass_normalized": validated.model_dump(),
                    "first_pass_deadline_raw": normalized_result.get("deadline_raw"),
                }
                validated, validation_audit, deadline_raw = self._maybe_run_validation_pass(
                    config=config,
                    mail=mail,
                    rule_result=rule_result,
                    analysis=validated,
                    thread_summary=thread_summary,
                    body_char_limit=body_char_limit,
                    current_user=current_user,
                    analysis_deadline_raw=normalized_result.get("deadline_raw"),
                )
                audit_payload.update(validation_audit)
                audit_payload["final_semantic_result"] = validated.model_dump()
                audit_payload["final_deadline_raw"] = deadline_raw
                return validated, audit_payload
            except Exception as exc:  # noqa: BLE001
                last_error = exc

        raise RuntimeError(f"AI analysis failed: {last_error}")

    def _maybe_run_validation_pass(
        self,
        *,
        config: AppConfig,
        mail,
        rule_result,
        analysis: MailAnalysisResult,
        thread_summary: str,
        body_char_limit: int,
        current_user: dict[str, str],
        analysis_deadline_raw: str | None,
    ) -> tuple[MailAnalysisResult, dict[str, object], str | None]:
        trigger_reasons = self._build_validation_trigger_reasons(
            mail=mail,
            rule_result=rule_result,
            analysis=analysis,
            thread_summary=thread_summary,
        )
        audit_payload: dict[str, object] = {
            "validator_used": bool(trigger_reasons),
            "validator_trigger_reasons": trigger_reasons,
            "validator_applied": False,
        }
        if not trigger_reasons:
            return analysis, audit_payload, analysis_deadline_raw

        candidate_result = {
            **analysis.model_dump(),
            "deadline_raw": analysis_deadline_raw,
            "final_category": decide_final_category(rule_result=rule_result, analysis=analysis),
        }
        validation_system_prompt = self.prompt_manager.build_validation_system_prompt()
        validation_user_prompt = self.prompt_manager.build_validation_user_prompt(
            mail=mail,
            thread_summary=thread_summary,
            current_user=current_user,
            rule_context=asdict(rule_result),
            candidate_result=candidate_result,
            body_char_limit=body_char_limit,
        )
        audit_payload["validator_prompt_input_hash"] = self._build_prompt_input_hash(
            system_prompt=validation_system_prompt,
            user_prompt=validation_user_prompt,
            response_schema=self.validation_response_schema,
        )

        try:
            raw_validation = self.gemini_client.generate_json(
                config=config,
                system_prompt=validation_system_prompt,
                user_prompt=validation_user_prompt,
                response_schema=self.validation_response_schema,
            )
            normalized_validation = normalize_validation_payload(
                raw_validation,
                fallback_subject=mail.subject,
                body_text=mail.body_text or mail.raw_preview,
                thread_summary=thread_summary,
                received_at=mail.received_at,
                rule_result=rule_result,
            )
            corrected_deadline_raw = (
                normalized_validation.get("corrected_result", {}).get("deadline_raw")
                if isinstance(normalized_validation.get("corrected_result"), dict)
                else None
            )
            validated_validation = validate_validation(normalized_validation)
            corrected_analysis = validate_analysis(
                validated_validation.corrected_result.model_dump(exclude={"final_category"})
            )
            validator_applied = corrected_analysis.model_dump() != analysis.model_dump()

            audit_payload.update(
                {
                    "validator_raw": raw_validation,
                    "validator_normalized": validated_validation.model_dump(),
                    "validator_is_valid": validated_validation.is_valid,
                    "validator_issues": validated_validation.issues,
                    "validator_applied": validator_applied,
                    "validator_deadline_raw": corrected_deadline_raw,
                }
            )
            return (
                corrected_analysis if validator_applied else analysis,
                audit_payload,
                corrected_deadline_raw if validator_applied else analysis_deadline_raw,
            )
        except Exception as exc:  # noqa: BLE001
            message = str(exc)[:500]
            self.logger.warning("Validation pass failed for mail_id=%s: %s", mail.id, message)
            audit_payload["validator_error"] = message
            return analysis, audit_payload, analysis_deadline_raw

    @staticmethod
    def _build_current_user_context(config: AppConfig) -> dict[str, str]:
        return {
            "email": config.user_email,
            "display_name": config.user_display_name,
            "department": config.user_department,
            "job_title": config.user_job_title,
        }

    def _build_validation_trigger_reasons(
        self,
        *,
        mail,
        rule_result,
        analysis: MailAnalysisResult,
        thread_summary: str,
    ) -> list[str]:
        body_text = str(mail.body_text or mail.raw_preview or "").strip()
        reasons: list[str] = []

        if analysis.confidence < self.VALIDATION_CONFIDENCE_THRESHOLD:
            reasons.append("low_confidence")
        if len(body_text) < self.SHORT_MAIL_THRESHOLD:
            reasons.append("short_mail")
        if thread_summary.strip() and self._looks_thread_dependent(body_text):
            reasons.append("thread_context_dependency")
        if rule_result.rule_category != analysis.llm_category:
            reasons.append("rule_llm_conflict")
        if analysis.request_present and analysis.request_target == "unknown":
            reasons.append("ambiguous_target")

        deduped: list[str] = []
        for item in reasons:
            if item not in deduped:
                deduped.append(item)
        return deduped[:5]

    @staticmethod
    def _looks_thread_dependent(body_text: str) -> bool:
        lowered = str(body_text or "").lower()
        if not lowered:
            return False
        signals = (
            "same as below",
            "as discussed",
            "please proceed",
            "following the previous",
            "follow the below",
            "see below",
            "per below",
            "아래",
            "상기",
            "전 메일",
            "앞 메일",
            "논의한",
            "진행 부탁",
            "동일",
        )
        return any(signal in lowered for signal in signals)

    @staticmethod
    def _build_prompt_input_hash(
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
    ) -> str:
        digest = hashlib.sha256()
        digest.update(system_prompt.encode("utf-8"))
        digest.update(b"\n---\n")
        digest.update(user_prompt.encode("utf-8"))
        digest.update(b"\n---\n")
        digest.update(json.dumps(response_schema, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        return digest.hexdigest()
