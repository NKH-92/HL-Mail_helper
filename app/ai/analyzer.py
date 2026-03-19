"""AI output schema and orchestration."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.ai.ownership import apply_conservative_ownership, detect_ownership_signals
from app.core.time_utils import extract_due_date_hint, normalize_due_date_text

ACTIONABLE_CLASSIFICATIONS = {"ACTION_SELF", "ACTION_SHARED", "APPROVAL_REQUEST"}
NON_ACTIONABLE_CLASSIFICATIONS = {"FYI", "ANNOUNCEMENT", "UNCLEAR"}


class MailAnalysisResult(BaseModel):
    """Structured analysis response from Gemini."""

    category: Literal["ACT", "FYI", "APR", "SCH", "QLT", "ETC"]
    priority: Literal["high", "medium", "low", "unknown"]
    classification: Literal["ACTION_SELF", "ACTION_SHARED", "APPROVAL_REQUEST", "FYI", "ANNOUNCEMENT", "UNCLEAR"]
    one_line_summary: str = Field(min_length=1, max_length=200)
    summary_3lines: list[str] = Field(min_length=1, max_length=3)
    mail_action_items: list[str] = Field(default_factory=list)
    my_action_required: bool
    my_action_status: Literal["direct_action", "review_needed", "reference_only"]
    my_action_items: list[str] = Field(default_factory=list)
    action_owner: Literal["me", "team", "other", "unknown"]
    action_type: list[Literal["reply", "review", "approve", "submit", "prepare", "attend", "monitor", "none"]] = (
        Field(default_factory=list)
    )
    due_date: str | None = None
    deadline_raw: str | None = None
    evidence: list[str] = Field(default_factory=list)
    ownership_reason: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=400)
    suggested_task_title: str | None = Field(default=None, max_length=120)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("summary_3lines")
    @classmethod
    def strip_summary_lines(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        if not cleaned:
            raise ValueError("summary_3lines must not be empty")
        return cleaned[:3]

    @field_validator("mail_action_items", "my_action_items", "ownership_reason", "evidence")
    @classmethod
    def strip_list_values(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item and item.strip()]

    @field_validator("action_type")
    @classmethod
    def strip_action_types(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            if not item:
                continue
            normalized = item.strip()
            if normalized and normalized not in cleaned:
                cleaned.append(normalized)
        return cleaned[:4]

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("reason must not be empty")
        return cleaned[:400]

    @field_validator("suggested_task_title")
    @classmethod
    def strip_task_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned[:120] if cleaned else None

    @field_validator("deadline_raw")
    @classmethod
    def strip_deadline_raw(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned[:120] if cleaned else None


def normalize_analysis_payload(
    data: dict[str, Any],
    fallback_subject: str = "",
    *,
    body_text: str = "",
    thread_summary: str = "",
    received_at: str | None = None,
) -> dict[str, Any]:
    """Coerce slightly malformed model output into the expected schema."""

    payload = _unwrap_payload(data)

    one_line_summary = (
        _pick_string(
            payload,
            "one_line_summary",
            "summary_short",
            "summary",
            "short_summary",
            "one_line",
        )
        or fallback_subject
        or "메일 요약 확인 필요"
    )[:200]

    summary_lines = _coerce_string_list(
        _pick_value(
            payload,
            "summary_3lines",
            "summary_lines",
            "summary_3_lines",
            "three_line_summary",
            "three_line_summaries",
        )
    )
    if not summary_lines:
        summary_lines = [one_line_summary]

    mail_action_items = _coerce_string_list(
        _pick_value(payload, "mail_action_items", "action_items", "all_action_items", "mail_actions")
    )
    my_action_items = _coerce_string_list(
        _pick_value(payload, "my_action_items", "my_actions", "user_action_items", "my_tasks")
    )

    raw_action_type = _pick_value(payload, "action_type", "action_types")
    action_type = _normalize_action_types(raw_action_type)

    my_action_status = _normalize_status(
        _pick_string(
            payload,
            "my_action_status",
            "my_action_type",
            "my_role",
            "my_status",
        )
    )
    if my_action_status is None and isinstance(raw_action_type, str):
        my_action_status = _normalize_status(raw_action_type)

    my_action_required = _coerce_bool(
        _pick_value(
            payload,
            "my_action_required",
            "action_required",
            "requires_action",
            "need_my_action",
        )
    )

    classification = _normalize_classification(
        _pick_string(payload, "classification", "action_classification", "mail_classification")
    )
    priority = _normalize_priority(_pick_string(payload, "priority", "importance", "urgency"))
    category = _normalize_category(_pick_string(payload, "category", "mail_category"))
    action_owner = _normalize_action_owner(_pick_string(payload, "action_owner", "owner", "task_owner"))
    evidence = _coerce_string_list(_pick_value(payload, "evidence", "evidence_texts", "grounds", "quotes"))
    ownership_reason = _coerce_string_list(
        _pick_value(payload, "ownership_reason", "ownership_reasons", "reasons", "why_me")
    )
    reason = _pick_string(payload, "reason", "analysis_reason", "why", "rationale") or ""
    suggested_task_title = _pick_string(payload, "suggested_task_title", "task_title", "suggested_title")
    deadline_raw, due_date = _extract_deadline(payload, reference_datetime=received_at)
    confidence = _coerce_float(_pick_value(payload, "confidence", "score", "certainty"), default=0.45)

    if classification is None:
        classification = _classification_from_legacy(
            category=category,
            my_action_status=my_action_status,
            my_action_required=my_action_required,
            has_my_action_items=bool(my_action_items),
        )

    if category is None:
        category = _category_from_classification(classification)
    if priority is None:
        priority = "unknown"
    if my_action_status is None:
        my_action_status = _status_from_classification(classification, action_owner)
    if my_action_required is None:
        my_action_required = classification in ACTIONABLE_CLASSIFICATIONS or bool(my_action_items)
    if action_owner is None:
        action_owner = _owner_from_classification(classification)
    if not action_type:
        action_type = _action_types_from_classification(classification)
    if not my_action_items and my_action_required and suggested_task_title:
        my_action_items = [suggested_task_title]
    if not my_action_items and my_action_required and evidence:
        my_action_items = [_compact_task_text(evidence[0])]
    if not suggested_task_title and my_action_items:
        suggested_task_title = my_action_items[0]
    if not mail_action_items and my_action_items:
        mail_action_items = my_action_items[:]
    if not reason and ownership_reason:
        reason = " ".join(ownership_reason[:2])[:400]

    deadline_raw, due_date = _coalesce_deadline_context(
        deadline_raw=deadline_raw,
        due_date=due_date,
        evidence=evidence,
        body_text=body_text,
        thread_summary=thread_summary,
        received_at=received_at,
    )

    return finalize_analysis_payload(
        {
            "category": category,
            "priority": priority,
            "classification": classification,
            "one_line_summary": one_line_summary,
            "summary_3lines": summary_lines[:3],
            "mail_action_items": mail_action_items,
            "my_action_required": bool(my_action_required),
            "my_action_status": my_action_status,
            "my_action_items": my_action_items,
            "action_owner": action_owner,
            "action_type": action_type,
            "due_date": due_date or None,
            "deadline_raw": deadline_raw,
            "evidence": evidence,
            "ownership_reason": ownership_reason,
            "reason": reason,
            "suggested_task_title": suggested_task_title,
            "confidence": confidence,
        }
    )


def finalize_analysis_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Enforce internal consistency after normalization or rule-based adjustments."""

    payload = dict(data)

    classification = _normalize_classification(str(payload.get("classification") or "")) or "UNCLEAR"
    evidence = _dedupe_preserve(_coerce_string_list(payload.get("evidence")))[:5]
    ownership_reason = _dedupe_preserve(_coerce_string_list(payload.get("ownership_reason")))[:5]
    action_type = _normalize_action_types(payload.get("action_type"))
    suggested_task_title = _trim_optional_text(payload.get("suggested_task_title"), limit=120)
    reason = (_trim_optional_text(payload.get("reason"), limit=400) or "").strip()
    due_date = normalize_due_date_text(_trim_optional_text(payload.get("due_date"), limit=120))
    deadline_raw = _trim_optional_text(payload.get("deadline_raw"), limit=120)
    priority = _normalize_priority(str(payload.get("priority") or "")) or "unknown"
    category = _normalize_category(str(payload.get("category") or "")) or _category_from_classification(classification)
    action_owner = _normalize_action_owner(str(payload.get("action_owner") or "")) or _owner_from_classification(classification)
    my_action_required = bool(payload.get("my_action_required"))
    my_action_status = _normalize_status(str(payload.get("my_action_status") or "")) or _status_from_classification(
        classification, action_owner
    )
    my_action_items = _dedupe_preserve(_coerce_string_list(payload.get("my_action_items")))[:3]
    mail_action_items = _dedupe_preserve(_coerce_string_list(payload.get("mail_action_items")))[:5]
    confidence = _coerce_float(payload.get("confidence"), default=0.45)

    if classification in {"FYI", "ANNOUNCEMENT"}:
        my_action_required = False
        my_action_status = "reference_only"
        my_action_items = []
        action_owner = "other" if action_owner == "me" else action_owner
        action_type = ["none"]
    elif classification == "UNCLEAR":
        my_action_required = False
        my_action_status = "reference_only"
        my_action_items = []
        action_owner = "unknown"
        action_type = ["none"]
        confidence = min(confidence, 0.45)
        ownership_reason.append("직접 행동 요청 근거가 부족해 UNCLEAR로 보수 처리")
    elif classification == "ACTION_SHARED":
        my_action_required = True
        my_action_status = "review_needed"
        if action_owner not in {"me", "team"}:
            action_owner = "team"
    elif classification == "ACTION_SELF":
        my_action_required = True
        if action_owner == "other":
            classification = "ACTION_SHARED"
            my_action_status = "review_needed"
            action_owner = "team"
        else:
            action_owner = "me" if action_owner == "unknown" else action_owner
            my_action_status = "direct_action"
    elif classification == "APPROVAL_REQUEST":
        my_action_required = True
        action_owner = "me" if action_owner == "unknown" else action_owner
        my_action_status = "direct_action" if action_owner == "me" else "review_needed"
        if not action_type:
            action_type = ["review"]

    if my_action_required and not evidence:
        classification = "UNCLEAR"
        my_action_required = False
        my_action_status = "reference_only"
        my_action_items = []
        action_owner = "unknown"
        action_type = ["none"]
        confidence = min(confidence, 0.4)
        ownership_reason.append("본문 또는 스레드에서 직접 근거 문장을 확보하지 못함")
        if not reason:
            reason = "본문과 스레드에서 사용자의 직접 액션을 뒷받침하는 근거를 찾지 못해 판단을 보류했습니다."

    if not my_action_required:
        my_action_items = []
        if not action_type:
            action_type = ["none"]
    elif not my_action_items:
        if suggested_task_title:
            my_action_items = [suggested_task_title]
        elif evidence:
            my_action_items = [_compact_task_text(evidence[0])]

    if not suggested_task_title and my_action_items:
        suggested_task_title = my_action_items[0]
    if not mail_action_items and my_action_items:
        mail_action_items = my_action_items[:]
    if not action_type:
        action_type = ["none"] if classification in NON_ACTIONABLE_CLASSIFICATIONS else []

    if not reason:
        if classification == "UNCLEAR":
            reason = "근거가 부족해 사용자의 직접 액션 여부를 보류했습니다."
        elif classification in {"FYI", "ANNOUNCEMENT"}:
            reason = "행동 요청보다 공유 또는 공지 성격이 강해 참조 메일로 분류했습니다."
        elif classification == "APPROVAL_REQUEST":
            reason = "승인 또는 검토 요청이 확인되어 사용자의 직접 검토 대상으로 분류했습니다."
        elif classification == "ACTION_SHARED":
            reason = "행동 요청은 있으나 사용자 단독 책임이 명확하지 않아 공동 액션으로 보았습니다."
        else:
            reason = "본문 근거를 바탕으로 사용자의 직접 액션이 필요한 메일로 판단했습니다."

    if not deadline_raw and due_date:
        deadline_raw = due_date

    ownership_reason = _dedupe_preserve(ownership_reason)[:5]

    return {
        "category": category,
        "priority": priority,
        "classification": classification,
        "one_line_summary": _trim_optional_text(payload.get("one_line_summary"), limit=200) or "메일 요약 확인 필요",
        "summary_3lines": _dedupe_preserve(_coerce_string_list(payload.get("summary_3lines")))[:3]
        or [_trim_optional_text(payload.get("one_line_summary"), limit=200) or "메일 요약 확인 필요"],
        "mail_action_items": mail_action_items,
        "my_action_required": my_action_required,
        "my_action_status": my_action_status,
        "my_action_items": my_action_items,
        "action_owner": action_owner,
        "action_type": action_type,
        "due_date": due_date or None,
        "deadline_raw": deadline_raw,
        "evidence": evidence,
        "ownership_reason": ownership_reason,
        "reason": reason,
        "suggested_task_title": suggested_task_title,
        "confidence": confidence,
    }


def validate_analysis(data: dict[str, Any]) -> MailAnalysisResult:
    """Validate raw JSON response."""

    try:
        return MailAnalysisResult.model_validate(data)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


def enforce_ownership_rules(
    result: MailAnalysisResult,
    user_email: str,
    user_display_name: str,
    user_department: str,
    user_job_title: str,
    to_list: list[str],
    cc_list: list[str],
    body_text: str,
) -> MailAnalysisResult:
    """Apply deterministic rules after AI analysis."""

    payload = result.model_dump()
    signals = detect_ownership_signals(
        user_email=user_email,
        user_display_name=user_display_name,
        user_department=user_department,
        user_job_title=user_job_title,
        to_list=to_list,
        cc_list=cc_list,
        body_text=body_text,
    )
    adjusted = apply_conservative_ownership(payload, signals)
    finalized = finalize_analysis_payload(adjusted)
    return MailAnalysisResult.model_validate(finalized)


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
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _coerce_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        split_lines = [line.strip("- ").strip() for line in text.splitlines() if line.strip()]
        return split_lines if split_lines else [text]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            coerced = _coerce_string_item(item)
            if coerced:
                result.append(coerced)
        return result
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


def _extract_deadline(
    payload: dict[str, Any],
    reference_datetime: str | None = None,
) -> tuple[str | None, str | None]:
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

    for source_text, require_signal in [
        *[(item, False) for item in evidence],
        (body_text, True),
        (thread_summary, True),
    ]:
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


def _normalize_status(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower().replace(" ", "_").replace("-", "_")
    mapping = {
        "direct": "direct_action",
        "direct_action": "direct_action",
        "action_required": "direct_action",
        "review": "review_needed",
        "review_needed": "review_needed",
        "needs_review": "review_needed",
        "shared_action": "review_needed",
        "reference": "reference_only",
        "reference_only": "reference_only",
        "for_reference": "reference_only",
    }
    mapped = mapping.get(normalized, normalized)
    return mapped if mapped in {"direct_action", "review_needed", "reference_only"} else None


def _normalize_classification(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().upper().replace("-", "_").replace(" ", "_")
    mapping = {
        "ACTIONSELF": "ACTION_SELF",
        "ACTION_SELF": "ACTION_SELF",
        "DIRECT_ACTION": "ACTION_SELF",
        "SELF_ACTION": "ACTION_SELF",
        "ACTIONSHARED": "ACTION_SHARED",
        "ACTION_SHARED": "ACTION_SHARED",
        "TEAM_ACTION": "ACTION_SHARED",
        "SHARED_ACTION": "ACTION_SHARED",
        "APPROVALREQUEST": "APPROVAL_REQUEST",
        "APPROVAL_REQUEST": "APPROVAL_REQUEST",
        "APPROVAL": "APPROVAL_REQUEST",
        "REVIEW_REQUEST": "APPROVAL_REQUEST",
        "FYI": "FYI",
        "ANNOUNCEMENT": "ANNOUNCEMENT",
        "NOTICE": "ANNOUNCEMENT",
        "UNCLEAR": "UNCLEAR",
        "UNKNOWN": "UNCLEAR",
    }
    mapped = mapping.get(normalized, normalized)
    return mapped if mapped in {"ACTION_SELF", "ACTION_SHARED", "APPROVAL_REQUEST", "FYI", "ANNOUNCEMENT", "UNCLEAR"} else None


def _normalize_category(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().upper()
    return normalized if normalized in {"ACT", "FYI", "APR", "SCH", "QLT", "ETC"} else None


def _normalize_priority(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    mapping = {
        "high": "high",
        "urgent": "high",
        "critical": "high",
        "medium": "medium",
        "normal": "medium",
        "mid": "medium",
        "low": "low",
        "minor": "low",
        "unknown": "unknown",
        "unclear": "unknown",
        "n/a": "unknown",
    }
    return mapping.get(normalized, "unknown")


def _normalize_action_owner(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower().replace(" ", "_").replace("-", "_")
    mapping = {
        "me": "me",
        "self": "me",
        "mine": "me",
        "team": "team",
        "shared": "team",
        "other": "other",
        "others": "other",
        "unknown": "unknown",
    }
    mapped = mapping.get(normalized, normalized)
    return mapped if mapped in {"me", "team", "other", "unknown"} else None


def _normalize_action_types(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    normalized: list[str] = []
    for item in values:
        action_type = _normalize_action_type_item(item)
        if action_type and action_type not in normalized:
            normalized.append(action_type)
    return normalized[:4]


def _normalize_action_type_item(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace(" ", "_").replace("-", "_")
    mapping = {
        "reply": "reply",
        "respond": "reply",
        "response": "reply",
        "review": "review",
        "check": "review",
        "approve": "approve",
        "approval": "approve",
        "submit": "submit",
        "send": "submit",
        "prepare": "prepare",
        "draft": "prepare",
        "attend": "attend",
        "join": "attend",
        "monitor": "monitor",
        "watch": "monitor",
        "none": "none",
        "no_action": "none",
    }
    return mapping.get(normalized)


def _classification_from_legacy(
    category: str | None,
    my_action_status: str | None,
    my_action_required: bool | None,
    has_my_action_items: bool,
) -> str:
    if category == "APR":
        return "APPROVAL_REQUEST"
    if my_action_status == "direct_action":
        return "ACTION_SELF"
    if my_action_status == "review_needed":
        return "ACTION_SHARED" if my_action_required else "UNCLEAR"
    if bool(my_action_required) or has_my_action_items:
        return "ACTION_SHARED"
    if category == "FYI":
        return "FYI"
    return "UNCLEAR"


def _category_from_classification(classification: str) -> str:
    mapping = {
        "ACTION_SELF": "ACT",
        "ACTION_SHARED": "ACT",
        "APPROVAL_REQUEST": "APR",
        "FYI": "FYI",
        "ANNOUNCEMENT": "FYI",
        "UNCLEAR": "ETC",
    }
    return mapping.get(classification, "ETC")


def _status_from_classification(classification: str, action_owner: str | None) -> str:
    if classification == "ACTION_SELF":
        return "review_needed" if action_owner == "other" else "direct_action"
    if classification == "ACTION_SHARED":
        return "review_needed"
    if classification == "APPROVAL_REQUEST":
        return "review_needed" if action_owner == "other" else "direct_action"
    return "reference_only"


def _owner_from_classification(classification: str) -> str:
    mapping = {
        "ACTION_SELF": "me",
        "ACTION_SHARED": "team",
        "APPROVAL_REQUEST": "me",
        "FYI": "other",
        "ANNOUNCEMENT": "other",
        "UNCLEAR": "unknown",
    }
    return mapping.get(classification, "unknown")


def _action_types_from_classification(classification: str) -> list[str]:
    if classification == "APPROVAL_REQUEST":
        return ["review"]
    if classification in NON_ACTIONABLE_CLASSIFICATIONS:
        return ["none"]
    return []


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
    cleaned = value.strip()
    return cleaned[:limit] if cleaned else None


def _compact_task_text(value: str) -> str:
    cleaned = " ".join(value.replace("\n", " ").split())
    return cleaned[:120]
