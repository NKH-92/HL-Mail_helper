"""Rule, normalization, and decision helpers for the mail action classifier."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.core.time_utils import extract_due_date_hint, normalize_due_date_text


class _MailSemanticResultBase(BaseModel):
    """Shared semantic fields returned by the LLM passes."""

    request_present: bool
    request_target: Literal["me", "other", "group", "unknown"]
    request_target_is_me: bool
    action_types: list[
        Literal["REPLY", "REVIEW", "APPROVE", "SUBMIT", "MODIFY", "SCHEDULE", "FOLLOW_UP", "DECIDE", "NONE"]
    ] = Field(default_factory=list)
    due_date: str | None = None
    urgency: Literal["high", "medium", "low", "none", "unknown"] = "unknown"
    evidence: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=1, max_length=200)
    llm_category: Literal[1, 2, 3]
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("action_types")
    @classmethod
    def validate_action_types(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            if item and item not in cleaned:
                cleaned.append(item)
        if len(cleaned) > 1 and "NONE" in cleaned:
            cleaned = [item for item in cleaned if item != "NONE"]
        if not cleaned:
            return ["NONE"]
        return cleaned[:4]

    @field_validator("evidence")
    @classmethod
    def validate_evidence(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item and item.strip()][:5]

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        cleaned = " ".join(value.split()).strip()
        if not cleaned:
            raise ValueError("summary must not be empty")
        return cleaned[:200]


class MailAnalysisResult(_MailSemanticResultBase):
    """Structured first-pass LLM response for one mail."""


class MailValidationCorrectedResult(_MailSemanticResultBase):
    """Corrected semantic result emitted by the validator."""

    final_category: Literal[1, 2, 3]


class MailValidationResult(BaseModel):
    """Structured validator response for one mail."""

    is_valid: bool
    corrected_result: MailValidationCorrectedResult
    issues: list[str] = Field(default_factory=list)

    @field_validator("issues")
    @classmethod
    def validate_issues(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            text = " ".join(str(item or "").split()).strip()
            if text and text not in cleaned:
                cleaned.append(text[:200])
        return cleaned[:8]


@dataclass(slots=True)
class MailRuleResult:
    """Structure-only facts that the rule engine owns."""

    is_to_me: bool
    is_cc_me: bool
    recipient_role: Literal["TO", "CC", "NONE"]
    is_system_sender: bool
    is_newsletter_like: bool
    thread_id: str
    message_id: str
    sender_type: Literal["internal", "external", "system"]
    rule_category: Literal[1, 2, 3]


def build_rule_result(
    *,
    user_email: str,
    known_recipient_addresses: list[str] | None = None,
    cc_only_recipient_addresses: list[str] | None = None,
    sender_email: str,
    to_list: list[str],
    cc_list: list[str],
    subject: str,
    body_text: str,
    thread_id: str,
    message_id: str,
) -> MailRuleResult:
    """Return rule-engine facts from immutable mail structure."""

    normalized_user = _normalized_email(user_email)
    known_addresses = {_normalized_email(value) for value in (known_recipient_addresses or []) if _normalized_email(value)}
    cc_only_addresses = {
        _normalized_email(value) for value in (cc_only_recipient_addresses or []) if _normalized_email(value)
    }
    if normalized_user:
        known_addresses.add(normalized_user)
    cc_only_addresses.difference_update(known_addresses)
    normalized_to = {_normalized_email(value) for value in to_list}
    normalized_cc = {_normalized_email(value) for value in cc_list}

    direct_to_hit = bool(known_addresses.intersection(normalized_to))
    direct_cc_hit = bool(known_addresses.intersection(normalized_cc))
    cc_only_hit = bool(cc_only_addresses.intersection(normalized_to.union(normalized_cc)))

    is_to_me = direct_to_hit
    is_cc_me = direct_cc_hit or (not direct_to_hit and cc_only_hit)
    if is_to_me:
        recipient_role: Literal["TO", "CC", "NONE"] = "TO"
    elif is_cc_me:
        recipient_role = "CC"
    else:
        recipient_role = "NONE"

    is_system_sender = _looks_like_system_sender(sender_email)
    is_newsletter_like = _looks_like_newsletter(sender_email=sender_email, subject=subject, body_text=body_text)
    sender_type = _resolve_sender_type(
        user_email=user_email,
        sender_email=sender_email,
        is_system_sender=is_system_sender,
    )

    if is_system_sender or is_newsletter_like:
        rule_category: Literal[1, 2, 3] = 3
    elif is_to_me:
        rule_category = 1
    elif is_cc_me:
        rule_category = 2
    else:
        rule_category = 3

    return MailRuleResult(
        is_to_me=is_to_me,
        is_cc_me=is_cc_me,
        recipient_role=recipient_role,
        is_system_sender=is_system_sender,
        is_newsletter_like=is_newsletter_like,
        thread_id=thread_id,
        message_id=message_id,
        sender_type=sender_type,
        rule_category=rule_category,
    )


def normalize_analysis_payload(
    data: dict[str, Any],
    fallback_subject: str = "",
    *,
    body_text: str = "",
    thread_summary: str = "",
    received_at: str | None = None,
) -> dict[str, Any]:
    """Coerce raw model output into the strict LLM schema."""

    payload = _unwrap_payload(data)

    summary = (
        _pick_string(payload, "summary", "one_line_summary", "summary_short", "short_summary")
        or fallback_subject
        or "메일 분류 확인 필요"
    )[:200]

    request_present = _coerce_bool(_pick_value(payload, "request_present", "action_required", "requires_action"))
    request_target = _normalize_request_target(_pick_string(payload, "request_target", "action_owner", "owner"))
    request_target_is_me = _coerce_bool(_pick_value(payload, "request_target_is_me", "target_is_me", "is_for_me"))
    llm_category = _normalize_category_value(_pick_value(payload, "llm_category", "category", "mail_category"))
    urgency = _normalize_urgency(_pick_string(payload, "urgency", "priority", "importance")) or "unknown"
    confidence = _coerce_float(_pick_value(payload, "confidence", "score", "certainty"), default=0.45)
    evidence = _dedupe_preserve(
        _coerce_string_list(_pick_value(payload, "evidence", "grounds", "quotes", "supporting_sentences"))
    )[:5]
    action_types = _normalize_action_types(_pick_value(payload, "action_types", "action_type"))
    deadline_raw, due_date = _extract_deadline(payload, reference_datetime=received_at)

    if request_target_is_me is None and request_target == "me":
        request_target_is_me = True
    if request_target is None and request_target_is_me:
        request_target = "me"
    if request_target is None:
        request_target = _target_from_llm_category(llm_category)
    if request_present is None:
        request_present = _request_present_from_context(
            llm_category=llm_category,
            action_types=action_types,
            request_target_is_me=request_target_is_me,
        )
    if request_target_is_me is None:
        request_target_is_me = request_present and request_target == "me"
    if llm_category is None:
        llm_category = _infer_llm_category(
            request_present=request_present,
            request_target=request_target,
            request_target_is_me=request_target_is_me,
        )

    deadline_raw, due_date = _coalesce_deadline_context(
        deadline_raw=deadline_raw,
        due_date=due_date,
        evidence=evidence,
        body_text=body_text,
        thread_summary=thread_summary,
        received_at=received_at,
    )

    if request_present and not evidence:
        fallback_evidence = _extract_fallback_evidence(body_text, thread_summary)
        if fallback_evidence:
            evidence = [fallback_evidence]
        else:
            confidence = min(confidence, 0.35)

    if request_present and not action_types:
        action_types = _infer_action_types(summary, evidence)
    if not request_present:
        action_types = ["NONE"]
        llm_category = 3
        request_target = "unknown"
        request_target_is_me = False

    return finalize_analysis_payload(
        {
            "request_present": request_present,
            "request_target": request_target,
            "request_target_is_me": request_target_is_me,
            "action_types": action_types,
            "due_date": due_date,
            "urgency": urgency,
            "evidence": evidence,
            "summary": summary,
            "llm_category": llm_category,
            "confidence": confidence,
            "deadline_raw": deadline_raw,
        }
    )


def finalize_analysis_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Enforce consistency after normalization."""

    request_present = bool(data.get("request_present"))
    request_target = _normalize_request_target(str(data.get("request_target") or "")) or "unknown"
    request_target_is_me = bool(data.get("request_target_is_me")) if request_present else False
    action_types = _normalize_action_types(data.get("action_types"))
    summary = (_trim_optional_text(data.get("summary"), limit=200) or "메일 분류 확인 필요").strip()
    evidence = _dedupe_preserve(_coerce_string_list(data.get("evidence")))[:5]
    due_date = normalize_due_date_text(_trim_optional_text(data.get("due_date"), limit=120))
    urgency = _normalize_urgency(str(data.get("urgency") or "")) or "unknown"
    llm_category = _normalize_category_value(data.get("llm_category"))
    confidence = _coerce_float(data.get("confidence"), default=0.45)
    deadline_raw = _trim_optional_text(data.get("deadline_raw"), limit=120)

    if request_target_is_me:
        request_target = "me"
    if not request_present:
        request_target = "unknown"
        request_target_is_me = False
        action_types = ["NONE"]
        llm_category = 3
        urgency = "none"
    elif not action_types:
        action_types = _infer_action_types(summary, evidence)
    elif urgency == "none":
        urgency = "unknown"

    if llm_category is None:
        llm_category = _infer_llm_category(
            request_present=request_present,
            request_target=request_target,
            request_target_is_me=request_target_is_me,
        )
    if not deadline_raw and due_date:
        deadline_raw = due_date

    return {
        "request_present": request_present,
        "request_target": request_target,
        "request_target_is_me": request_target_is_me,
        "action_types": action_types,
        "due_date": due_date,
        "urgency": urgency,
        "evidence": evidence,
        "summary": summary,
        "llm_category": llm_category,
        "confidence": confidence,
        "deadline_raw": deadline_raw,
    }


def normalize_validation_payload(
    data: dict[str, Any],
    *,
    fallback_subject: str = "",
    body_text: str = "",
    thread_summary: str = "",
    received_at: str | None = None,
    rule_result: MailRuleResult,
) -> dict[str, Any]:
    """Coerce validator output into the strict audit schema."""

    payload = _unwrap_payload(data)
    corrected_source = payload.get("corrected_result")
    if not isinstance(corrected_source, dict):
        corrected_source = payload

    normalized_corrected = normalize_analysis_payload(
        corrected_source,
        fallback_subject=fallback_subject,
        body_text=body_text,
        thread_summary=thread_summary,
        received_at=received_at,
    )
    validated_corrected = validate_analysis(normalized_corrected)
    computed_final_category = decide_final_category(rule_result=rule_result, analysis=validated_corrected)
    reported_final_category = _normalize_category_value(
        _pick_value(corrected_source, "final_category", "category_final", "policy_category")
    )

    issues = _dedupe_preserve(_coerce_string_list(_pick_value(payload, "issues", "findings", "warnings")))[:8]
    is_valid = _coerce_bool(_pick_value(payload, "is_valid", "valid"))
    if is_valid is None:
        is_valid = False
    if reported_final_category is not None and reported_final_category != computed_final_category:
        issues.append("Validator final_category was adjusted to match the policy engine.")
        is_valid = False

    return {
        "is_valid": is_valid,
        "corrected_result": {
            **validated_corrected.model_dump(),
            "deadline_raw": normalized_corrected.get("deadline_raw"),
            "final_category": computed_final_category,
        },
        "issues": _dedupe_preserve(issues)[:8],
    }


def validate_analysis(data: dict[str, Any]) -> MailAnalysisResult:
    """Validate normalized output."""

    try:
        return MailAnalysisResult.model_validate(data)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


def validate_validation(data: dict[str, Any]) -> MailValidationResult:
    """Validate normalized validator output."""

    try:
        return MailValidationResult.model_validate(data)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


def decide_final_category(*, rule_result: MailRuleResult, analysis: MailAnalysisResult) -> Literal[1, 2, 3]:
    """Apply the final policy table."""

    if not analysis.request_present:
        return 3
    if analysis.request_target_is_me:
        return 1
    if rule_result.is_to_me:
        return 1
    if rule_result.is_cc_me:
        return 2
    return 3


def build_decision_payload(
    *,
    rule_result: MailRuleResult,
    analysis: MailAnalysisResult,
    model_name: str,
    analyzed_at: str,
    raw_llm_json: str,
    deadline_raw: str | None = None,
) -> dict[str, Any]:
    """Combine rule and LLM outputs into the stored final classification."""

    final_category = decide_final_category(rule_result=rule_result, analysis=analysis)
    correction_applied = final_category != rule_result.rule_category
    correction_reason = _build_correction_reason(
        rule_result=rule_result,
        analysis=analysis,
        final_category=final_category,
        correction_applied=correction_applied,
    )
    conflict_type = _build_conflict_type(
        rule_result=rule_result,
        llm_category=analysis.llm_category,
        final_category=final_category,
    )
    compatibility = _build_compatibility_fields(
        rule_result=rule_result,
        analysis=analysis,
        final_category=final_category,
        deadline_raw=deadline_raw,
    )

    return {
        **compatibility,
        **asdict(rule_result),
        "request_present": analysis.request_present,
        "request_target": analysis.request_target,
        "request_target_is_me": analysis.request_target_is_me,
        "urgency": analysis.urgency,
        "llm_category": analysis.llm_category,
        "final_category": final_category,
        "correction_applied": correction_applied,
        "correction_reason": correction_reason,
        "conflict_type": conflict_type,
        "model_name": model_name,
        "analyzed_at": analyzed_at,
        "raw_llm_json": raw_llm_json,
    }


def build_failed_analysis_fallback(
    *,
    rule_result: MailRuleResult,
    fallback_subject: str,
    error_message: str,
    model_name: str,
    analyzed_at: str,
) -> dict[str, Any]:
    """Return a provisional stored result when LLM analysis fails."""

    final_category = rule_result.rule_category
    compatibility = _build_fallback_compatibility_fields(
        rule_result=rule_result,
        fallback_subject=fallback_subject,
    )

    return {
        **compatibility,
        **asdict(rule_result),
        "request_present": None,
        "request_target": "unknown",
        "request_target_is_me": None,
        "urgency": "unknown",
        "llm_category": None,
        "final_category": final_category,
        "correction_applied": False,
        "correction_reason": "LLM analysis failed; provisional rule classification retained.",
        "conflict_type": "LLM_FAILED",
        "model_name": model_name,
        "analyzed_at": analyzed_at,
        "raw_llm_json": "",
        "analysis_status": "failed",
        "analysis_error": error_message[:500],
    }


def _build_compatibility_fields(
    *,
    rule_result: MailRuleResult,
    analysis: MailAnalysisResult,
    final_category: int,
    deadline_raw: str | None = None,
) -> dict[str, Any]:
    request_present = analysis.request_present
    summary = analysis.summary
    action_items = [summary] if request_present else []
    my_action_items = [summary] if final_category == 1 and request_present else []

    return {
        "category": "ACT" if final_category in {1, 2} and request_present else "FYI",
        "priority": analysis.urgency,
        "one_line_summary": summary,
        "summary_3lines": [summary],
        "mail_action_items": action_items,
        "my_action_required": final_category in {1, 2} and request_present,
        "my_action_status": {1: "direct_action", 2: "review_needed", 3: "reference_only"}[final_category],
        "my_action_items": my_action_items,
        "action_owner": _derive_action_owner(
            rule_result=rule_result,
            analysis=analysis,
            final_category=final_category,
        ),
        "action_type": _normalize_legacy_action_types(analysis.action_types),
        "due_date": analysis.due_date,
        "deadline_raw": deadline_raw or analysis.due_date,
        "evidence": analysis.evidence,
        "ownership_reason": _build_ownership_reason(
            rule_result=rule_result,
            analysis=analysis,
            final_category=final_category,
        ),
        "reason": summary,
        "suggested_task_title": summary if final_category == 1 and request_present else None,
        "confidence": analysis.confidence,
        "classification": _derive_action_classification(
            rule_result=rule_result,
            analysis=analysis,
            final_category=final_category,
        ),
    }


def _build_fallback_compatibility_fields(*, rule_result: MailRuleResult, fallback_subject: str) -> dict[str, Any]:
    final_category = rule_result.rule_category
    summary = fallback_subject or "메일 분류 확인 필요"
    return {
        "category": "ACT" if final_category in {1, 2} else "FYI",
        "priority": "unknown",
        "one_line_summary": summary,
        "summary_3lines": [summary],
        "mail_action_items": [summary] if final_category in {1, 2} else [],
        "my_action_required": final_category in {1, 2},
        "my_action_status": {1: "direct_action", 2: "review_needed", 3: "reference_only"}[final_category],
        "my_action_items": [summary] if final_category == 1 else [],
        "action_owner": "me" if final_category == 1 else "other" if final_category == 2 else "unknown",
        "action_type": ["none"],
        "due_date": None,
        "deadline_raw": None,
        "evidence": [],
        "ownership_reason": [f"Rule engine provisional classification {final_category} retained after LLM failure."],
        "reason": summary,
        "suggested_task_title": summary if final_category == 1 else None,
        "confidence": 0.0,
        "classification": "UNCLEAR",
    }


def _build_ownership_reason(
    *,
    rule_result: MailRuleResult,
    analysis: MailAnalysisResult,
    final_category: int,
) -> list[str]:
    reasons = [f"Rule engine recipient role: {rule_result.recipient_role}."]
    if analysis.request_present:
        reasons.append("LLM detected an actionable request in the mail body.")
    else:
        reasons.append("LLM judged the mail as informational without a concrete request.")
    if analysis.request_target_is_me:
        reasons.append("LLM determined the request target is the user.")
    elif final_category == 1 and rule_result.is_to_me and analysis.request_present:
        reasons.append("User is a TO recipient, so the mail stays in category 1 to avoid misses.")
    elif final_category == 2:
        reasons.append("User is a CC recipient on a request mail, so the mail is tracked for review.")
    return reasons[:4]


def _derive_action_classification(
    *,
    rule_result: MailRuleResult,
    analysis: MailAnalysisResult,
    final_category: int,
) -> str:
    if not analysis.request_present:
        return "ANNOUNCEMENT" if rule_result.is_system_sender or rule_result.is_newsletter_like else "FYI"
    if analysis.request_target_is_me or final_category == 1:
        return "ACTION_SELF"
    if final_category == 2:
        return "ACTION_SHARED"
    return "UNCLEAR"


def _derive_action_owner(
    *,
    rule_result: MailRuleResult,
    analysis: MailAnalysisResult,
    final_category: int,
) -> str:
    if final_category == 1:
        return "me"
    if final_category == 2:
        if analysis.request_target == "group":
            return "team"
        if analysis.request_target == "unknown":
            return "unknown"
        return "other"
    if not analysis.request_present:
        return "other"
    return "unknown"


def _normalize_legacy_action_types(value: list[str]) -> list[str]:
    mapping = {
        "REPLY": "reply",
        "REVIEW": "review",
        "APPROVE": "approve",
        "SUBMIT": "submit",
        "MODIFY": "prepare",
        "SCHEDULE": "attend",
        "FOLLOW_UP": "monitor",
        "DECIDE": "review",
        "NONE": "none",
    }
    normalized: list[str] = []
    for item in value:
        mapped = mapping.get(item)
        if mapped and mapped not in normalized:
            normalized.append(mapped)
    return normalized or ["none"]


def _build_correction_reason(
    *,
    rule_result: MailRuleResult,
    analysis: MailAnalysisResult,
    final_category: int,
    correction_applied: bool,
) -> str | None:
    if not correction_applied:
        return None
    if not analysis.request_present:
        return "LLM detected no actionable request."
    if analysis.request_target_is_me and rule_result.rule_category != 1:
        return "LLM determined request is explicitly directed to user."
    if rule_result.is_to_me and final_category == 1:
        return "TO recipient with actionable request kept in category 1 to avoid misses."
    if rule_result.is_cc_me and final_category == 2:
        return "CC recipient request mail moved to review category."
    return "Rule-based provisional category was adjusted after LLM request analysis."


def _build_conflict_type(*, rule_result: MailRuleResult, llm_category: int, final_category: int) -> str | None:
    if rule_result.rule_category != llm_category:
        return f"RULE_{rule_result.rule_category}_TO_LLM_{llm_category}"
    if llm_category != final_category:
        return f"LLM_{llm_category}_TO_FINAL_{final_category}"
    return None


def _unwrap_payload(data: dict[str, Any]) -> dict[str, Any]:
    payload = data if isinstance(data, dict) else {}
    for _ in range(3):
        nested = None
        for key in ("analysis", "result", "output", "response", "data"):
            value = payload.get(key)
            if isinstance(value, dict):
                nested = value
                break
        if nested is None:
            break
        payload = nested
    return payload


def _pick_value(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _pick_string(payload: dict[str, Any], *keys: str) -> str | None:
    value = _pick_value(payload, *keys)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _coerce_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [line.strip("- ").strip() for line in text.splitlines() if line.strip()] or ([text] if text else [])
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            coerced = _coerce_string_item(item)
            if coerced:
                items.append(coerced)
        return items
    coerced = _coerce_string_item(value)
    return [coerced] if coerced else []


def _coerce_string_item(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("action_text", "action", "task", "item", "description", "text", "summary", "reason", "note"):
            nested = value.get(key)
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
        parts = [str(item).strip() for item in value.values() if isinstance(item, str) and str(item).strip()]
        return " | ".join(parts[:2]).strip()
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _extract_deadline(payload: dict[str, Any], reference_datetime: str | None = None) -> tuple[str | None, str | None]:
    deadline = _pick_value(payload, "deadline")
    raw_text: str | None = None
    iso_text: str | None = None
    if isinstance(deadline, dict):
        raw_text = _pick_string(deadline, "raw", "text", "label")
        iso_text = _pick_string(deadline, "iso", "normalized", "datetime", "value")
    elif isinstance(deadline, str) and deadline.strip():
        raw_text = deadline.strip()
        iso_text = raw_text
    raw_text = raw_text or _pick_string(payload, "deadline_raw")
    iso_text = iso_text or _pick_string(payload, "due_date", "deadline_iso", "due", "target_date")
    return raw_text, normalize_due_date_text(iso_text or raw_text, reference_datetime=reference_datetime)


def _coalesce_deadline_context(
    *,
    deadline_raw: str | None,
    due_date: str | None,
    evidence: list[str],
    body_text: str,
    thread_summary: str,
    received_at: str | None,
) -> tuple[str | None, str | None]:
    if deadline_raw and due_date:
        return deadline_raw, due_date
    for source_text, require_signal in [*[(item, False) for item in evidence], (body_text, True), (thread_summary, True)]:
        raw_candidate, due_candidate = extract_due_date_hint(
            source_text,
            reference_datetime=received_at,
            require_signal=require_signal,
        )
        if not due_candidate:
            continue
        if not due_date:
            due_date = due_candidate
        if not deadline_raw:
            deadline_raw = raw_candidate
        if deadline_raw and due_date:
            break
    if not deadline_raw and due_date:
        deadline_raw = due_date
    return deadline_raw, due_date


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "y", "1"}:
            return True
        if normalized in {"false", "no", "n", "0"}:
            return False
    return None


def _coerce_float(value: Any, default: float) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(float(value), 1.0))
    if isinstance(value, str):
        try:
            return max(0.0, min(float(value.strip()), 1.0))
        except ValueError:
            return default
    return default


def _normalize_request_target(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    mapping = {
        "me": "me",
        "self": "me",
        "myself": "me",
        "user": "me",
        "other": "other",
        "others": "other",
        "group": "group",
        "team": "group",
        "shared": "group",
        "all": "group",
        "unknown": "unknown",
        "unclear": "unknown",
    }
    mapped = mapping.get(normalized, normalized)
    return mapped if mapped in {"me", "other", "group", "unknown"} else None


def _normalize_urgency(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    return {
        "high": "high",
        "urgent": "high",
        "critical": "high",
        "medium": "medium",
        "normal": "medium",
        "mid": "medium",
        "low": "low",
        "minor": "low",
        "none": "none",
        "no_action": "none",
        "unknown": "unknown",
        "unclear": "unknown",
    }.get(normalized, "unknown")


def _normalize_action_types(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    normalized: list[str] = []
    for item in values:
        action_type = _normalize_action_type_item(item)
        if action_type and action_type not in normalized:
            normalized.append(action_type)
    if len(normalized) > 1 and "NONE" in normalized:
        normalized = [item for item in normalized if item != "NONE"]
    return normalized[:4]


def _normalize_action_type_item(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("type") or value.get("action") or value.get("label")
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper().replace("-", "_").replace(" ", "_")
    mapping = {
        "REPLY": "REPLY",
        "RESPOND": "REPLY",
        "RESPONSE": "REPLY",
        "REVIEW": "REVIEW",
        "CHECK": "REVIEW",
        "APPROVE": "APPROVE",
        "APPROVAL": "APPROVE",
        "SUBMIT": "SUBMIT",
        "SEND": "SUBMIT",
        "MODIFY": "MODIFY",
        "UPDATE": "MODIFY",
        "SCHEDULE": "SCHEDULE",
        "MEETING": "SCHEDULE",
        "ATTEND": "SCHEDULE",
        "FOLLOW_UP": "FOLLOW_UP",
        "FOLLOWUP": "FOLLOW_UP",
        "MONITOR": "FOLLOW_UP",
        "DECIDE": "DECIDE",
        "DECISION": "DECIDE",
        "NONE": "NONE",
        "NO_ACTION": "NONE",
    }
    return mapping.get(normalized)


def _normalize_category_value(value: Any) -> int | None:
    if isinstance(value, int) and value in {1, 2, 3}:
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if normalized in {"1", "2", "3"}:
            return int(normalized)
        return {"action": 1, "direct_action": 1, "review": 2, "reference": 3, "fyi": 3}.get(normalized.lower())
    return None


def _dedupe_preserve(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _trim_optional_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned[:limit] if cleaned else None


def _normalized_email(value: str | None) -> str:
    return str(value or "").strip().lower()


def _looks_like_system_sender(sender_email: str) -> bool:
    normalized = _normalized_email(sender_email)
    if not normalized or "@" not in normalized:
        return False
    local_part = normalized.split("@", 1)[0]
    signals = ("noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", "daemon", "system", "bot")
    return any(signal in local_part for signal in signals)


def _looks_like_newsletter(*, sender_email: str, subject: str, body_text: str) -> bool:
    lowered_subject = str(subject or "").lower()
    lowered_sender = _normalized_email(sender_email)
    lowered_body = str(body_text or "").lower()
    subject_signals = ("newsletter", "digest", "weekly update", "daily briefing", "광고", "뉴스레터")
    body_signals = ("unsubscribe", "수신거부", "view in browser")
    sender_signals = ("news@", "newsletter@", "marketing@", "updates@")
    return any(signal in lowered_subject for signal in subject_signals) or any(
        signal in lowered_body for signal in body_signals
    ) or any(signal in lowered_sender for signal in sender_signals)


def _resolve_sender_type(*, user_email: str, sender_email: str, is_system_sender: bool) -> Literal["internal", "external", "system"]:
    if is_system_sender:
        return "system"
    user_domain = _normalized_email(user_email).split("@", 1)[1] if "@" in _normalized_email(user_email) else ""
    sender_domain = _normalized_email(sender_email).split("@", 1)[1] if "@" in _normalized_email(sender_email) else ""
    if user_domain and sender_domain and user_domain == sender_domain:
        return "internal"
    return "external"


def _request_present_from_context(
    *,
    llm_category: int | None,
    action_types: list[str],
    request_target_is_me: bool | None,
) -> bool:
    if llm_category in {1, 2} or request_target_is_me:
        return True
    return any(item != "NONE" for item in action_types)


def _target_from_llm_category(value: int | None) -> Literal["me", "other", "group", "unknown"]:
    if value == 1:
        return "me"
    if value == 2:
        return "unknown"
    return "unknown"


def _infer_llm_category(
    *,
    request_present: bool,
    request_target: str,
    request_target_is_me: bool,
) -> Literal[1, 2, 3]:
    if not request_present:
        return 3
    if request_target_is_me or request_target == "me":
        return 1
    return 2


def _extract_fallback_evidence(body_text: str, thread_summary: str) -> str:
    for source in (body_text, thread_summary):
        sentences = [segment.strip() for segment in str(source or "").replace("\r", "\n").splitlines() if segment.strip()]
        if sentences:
            return sentences[0][:200]
    return ""


def _infer_action_types(summary: str, evidence: list[str]) -> list[str]:
    combined = " ".join([summary, *evidence]).lower()
    keyword_map = {
        "REPLY": ("reply", "respond", "회신", "답장"),
        "REVIEW": ("review", "check", "검토", "확인"),
        "APPROVE": ("approve", "approval", "승인", "결재"),
        "SUBMIT": ("submit", "send", "제출", "발송"),
        "MODIFY": ("modify", "update", "revise", "수정", "보완"),
        "SCHEDULE": ("schedule", "meeting", "attend", "일정", "회의", "참석"),
        "FOLLOW_UP": ("follow up", "follow-up", "monitor", "추적", "후속"),
        "DECIDE": ("decide", "decision", "결정", "판단"),
    }
    inferred = [action_type for action_type, signals in keyword_map.items() if any(signal in combined for signal in signals)]
    return inferred or ["NONE"]
