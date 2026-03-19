"""Ownership heuristics used to keep action classification conservative."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

REQUEST_PHRASES = [
    "검토 부탁",
    "회신 바랍니다",
    "확인 요청",
    "검토 바랍니다",
    "부탁드립니다",
    "작성 부탁",
    "제출 부탁",
    "조치 부탁",
    "respond",
    "reply",
    "please review",
    "please confirm",
    "please submit",
    "action required",
]

APPROVAL_PHRASES = [
    "승인 부탁",
    "결재 부탁",
    "재가 부탁",
    "approve",
    "approval",
    "review and approve",
]

REFERENCE_PHRASES = [
    "공유드립니다",
    "참고 바랍니다",
    "for your information",
    "fyi",
]

ANNOUNCEMENT_PHRASES = [
    "공지",
    "안내드립니다",
    "announcement",
    "notice",
]


@dataclass(slots=True)
class OwnershipSignals:
    """Signals extracted from mail metadata and body."""

    user_in_to: bool
    user_in_cc: bool
    directly_mentioned: bool
    request_phrase_found: bool
    approval_phrase_found: bool
    reference_phrase_found: bool
    announcement_phrase_found: bool


def detect_ownership_signals(
    user_email: str,
    user_display_name: str,
    user_department: str,
    user_job_title: str,
    to_list: list[str],
    cc_list: list[str],
    body_text: str,
) -> OwnershipSignals:
    """Extract simple ownership signals from the message."""

    lower_body = body_text.lower()
    markers = [
        user_display_name.strip(),
        user_department.strip(),
        user_job_title.strip(),
        user_email.strip(),
        user_email.split("@")[0].strip() if user_email else "",
    ]
    directly_mentioned = any(marker and marker.lower() in lower_body for marker in markers)
    request_phrase_found = any(phrase.lower() in lower_body for phrase in REQUEST_PHRASES)
    approval_phrase_found = any(phrase.lower() in lower_body for phrase in APPROVAL_PHRASES)
    reference_phrase_found = any(phrase.lower() in lower_body for phrase in REFERENCE_PHRASES)
    announcement_phrase_found = any(phrase.lower() in lower_body for phrase in ANNOUNCEMENT_PHRASES)
    return OwnershipSignals(
        user_in_to=user_email.lower() in [item.lower() for item in to_list],
        user_in_cc=user_email.lower() in [item.lower() for item in cc_list],
        directly_mentioned=directly_mentioned,
        request_phrase_found=request_phrase_found,
        approval_phrase_found=approval_phrase_found,
        reference_phrase_found=reference_phrase_found,
        announcement_phrase_found=announcement_phrase_found,
    )


def apply_conservative_ownership(result: dict, signals: OwnershipSignals) -> dict:
    """Post-process AI result to enforce ownership rules."""

    ownership_reason = _coerce_string_list(result.get("ownership_reason"))
    evidence = _coerce_string_list(result.get("evidence"))
    action_type = _coerce_string_list(result.get("action_type"))

    def note(message: str) -> None:
        if message not in ownership_reason:
            ownership_reason.append(message)

    def set_non_actionable(classification: str, owner: str) -> None:
        result["classification"] = classification
        result["my_action_required"] = False
        result["my_action_status"] = "reference_only"
        result["my_action_items"] = []
        result["action_owner"] = owner
        if not action_type:
            result["action_type"] = ["none"]

    if not evidence:
        set_non_actionable("UNCLEAR", "unknown")
        note("직접 근거 문장이 없어 액션 판정을 보류")
        if not result.get("reason"):
            result["reason"] = "본문과 스레드에서 사용자의 직접 액션 근거를 찾지 못해 판단을 보류했습니다."

    if signals.announcement_phrase_found and not signals.request_phrase_found and not signals.approval_phrase_found:
        if not signals.directly_mentioned:
            set_non_actionable("ANNOUNCEMENT", "other")
            note("공지/안내 표현이 중심이라 ANNOUNCEMENT 처리")

    if signals.reference_phrase_found and not signals.request_phrase_found and not signals.approval_phrase_found:
        if not signals.directly_mentioned and result.get("classification") != "ANNOUNCEMENT":
            set_non_actionable("FYI", "other")
            note("공유/참고 표현이 중심이라 FYI 처리")

    if signals.user_in_cc and not signals.user_in_to:
        if not signals.directly_mentioned and not signals.request_phrase_found and not signals.approval_phrase_found:
            if result.get("classification") not in {"ANNOUNCEMENT", "FYI"}:
                set_non_actionable("UNCLEAR", "unknown")
            else:
                set_non_actionable(str(result.get("classification") or "FYI"), "other")
            note("CC 수신이며 직접 지목 또는 명시 요청이 없어 액션 부여를 보류")
        elif result.get("classification") == "ACTION_SELF":
            result["classification"] = "ACTION_SHARED"
            result["my_action_status"] = "review_needed"
            result["action_owner"] = "team"
            note("CC 수신이라 단독 책임 판정을 ACTION_SHARED로 완화")

    if signals.user_in_to and (signals.request_phrase_found or signals.approval_phrase_found):
        classification = str(result.get("classification") or "")
        if classification in {"FYI", "ANNOUNCEMENT", "UNCLEAR"} and evidence:
            if signals.approval_phrase_found:
                result["classification"] = "APPROVAL_REQUEST"
                result["my_action_required"] = True
                result["my_action_status"] = "direct_action"
                result["action_owner"] = "me"
                if not action_type:
                    result["action_type"] = ["approve"]
                note("To 수신 + 승인/검토 표현이 있어 APPROVAL_REQUEST로 상향")
            else:
                result["classification"] = "ACTION_SELF" if signals.directly_mentioned else "ACTION_SHARED"
                result["my_action_required"] = True
                result["my_action_status"] = "direct_action" if signals.directly_mentioned else "review_needed"
                result["action_owner"] = "me" if signals.directly_mentioned else "team"
                note("To 수신 + 요청 표현이 있어 action classification을 상향")

    if result.get("classification") == "APPROVAL_REQUEST":
        result["my_action_required"] = True
        if result.get("action_owner") not in {"me", "other"}:
            result["action_owner"] = "me"
        result["my_action_status"] = "direct_action" if result.get("action_owner") == "me" else "review_needed"
        if not action_type:
            result["action_type"] = ["approve" if signals.approval_phrase_found else "review"]

    if result.get("classification") == "ACTION_SELF" and result.get("action_owner") == "other":
        result["classification"] = "ACTION_SHARED"
        result["my_action_status"] = "review_needed"
        result["action_owner"] = "team"
        note("행동 주체가 타인으로 보이면 ACTION_SELF를 유지하지 않음")

    result["ownership_reason"] = ownership_reason
    return result


def _coerce_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                result.append(item.strip())
        return result
    return []
