"""Mail analysis service using Gemini-compatible providers."""

from __future__ import annotations

import logging
from typing import Callable

from app.ai.analyzer import MailAnalysisResult, enforce_ownership_rules, normalize_analysis_payload, validate_analysis
from app.ai.gemini_client import GeminiClient
from app.ai.prompts import PromptManager
from app.core.address_book import AddressBookService
from app.core.config_manager import AI_PROVIDER_HANLIM, ConfigManager
from app.db.repositories import MailRepository


class AnalysisService:
    """Analyze pending mails and persist structured results."""

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
            try:
                result = self._analyze_mail(config, mail)
                result_payload = result.model_dump()
                self.mail_repository.save_analysis_bundle(
                    mail.id,
                    result_payload,
                    mail_action_items=result.mail_action_items,
                    my_action_items=result.my_action_items,
                    due_date=result.due_date,
                    current_user_email=config.user_email,
                )
                success_count += 1
            except Exception as exc:  # noqa: BLE001
                self.logger.exception("Mail analysis failed for mail_id=%s: %s", mail.id, exc)
                self.mail_repository.mark_analysis_failed(mail.id, str(exc))
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

    def _analyze_mail(self, config, mail) -> MailAnalysisResult:
        system_prompt = self.prompt_manager.build_system_prompt()
        thread_summary = self.mail_repository.build_thread_summary(mail.id)
        body_char_limit = min(
            12000,
            max(config.preview_max_chars, 4000) if config.store_raw_body else max(2000, config.preview_max_chars),
        )
        user_prompt = self.prompt_manager.build_user_prompt(
            mail,
            thread_summary=thread_summary,
            model_name=config.gemini_model,
            body_char_limit=body_char_limit,
            current_user={
                "email": config.user_email,
                "display_name": config.user_display_name,
                "department": config.user_department,
                "job_title": config.user_job_title,
            },
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
                    body_text=mail.raw_preview,
                    thread_summary=thread_summary,
                    received_at=mail.received_at,
                )
                validated = validate_analysis(normalized_result)
                return enforce_ownership_rules(
                    result=validated,
                    user_email=config.user_email,
                    user_display_name=config.user_display_name,
                    user_department=config.user_department,
                    user_job_title=config.user_job_title,
                    to_list=mail.to_list,
                    cc_list=mail.cc_list,
                    body_text=mail.raw_preview,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc

        raise RuntimeError(f"AI 분석 실패: {last_error}")
