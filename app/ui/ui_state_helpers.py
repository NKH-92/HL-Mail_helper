"""Pure helper functions shared by UI renderers."""

from __future__ import annotations

from datetime import date, datetime
from math import ceil
from pathlib import Path
from typing import Any

from app.core.config_manager import AI_PROVIDER_GEMINI, AppConfig
from app.core.time_utils import parse_datetime_text, parse_time_text
from app.db.models import ActionItemRecord, MailRecord, MailTemplate, SendTemplate, ThreadOverview, TodoItemRecord


DASHBOARD_THREAD_PAGE_SIZE = 10
_DASHBOARD_THREAD_FILTER_KEYS = ("all", "today", "reply", "approval", "waiting", "review")


def read_log_tail(log_file_path: Path, limit: int = 20000) -> str:
    if not log_file_path.exists():
        return ""
    try:
        with log_file_path.open("rb") as handle:
            handle.seek(0, 2)
            file_size = handle.tell()
            handle.seek(max(0, file_size - limit))
            return handle.read().decode("utf-8", errors="ignore")
    except OSError:
        return ""


def normalize_dashboard_thread_filter(value: object) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in _DASHBOARD_THREAD_FILTER_KEYS else "all"


def normalize_dashboard_thread_page(value: object, *, default: int = 1) -> int:
    try:
        page = int(value)
    except (TypeError, ValueError):
        page = default
    return page if page > 0 else max(1, default)


def matches_dashboard_thread_filter(thread: ThreadOverview, filter_key: str) -> bool:
    normalized_filter = normalize_dashboard_thread_filter(filter_key)
    follow_up_status = str(thread.follow_up_status or "").strip().lower()
    if normalized_filter == "today":
        return follow_up_status in {"overdue", "deadline_soon"}
    if normalized_filter == "reply":
        return follow_up_status == "reply_needed"
    if normalized_filter == "approval":
        return follow_up_status == "approval_pending"
    if normalized_filter == "waiting":
        return follow_up_status in {"waiting_for_reply", "no_reply_3d"}
    if normalized_filter == "review":
        return follow_up_status == "review_needed" or bool(thread.needs_review)
    return True


def build_dashboard_thread_filter_counts(threads: list[ThreadOverview]) -> dict[str, int]:
    counts = {key: 0 for key in _DASHBOARD_THREAD_FILTER_KEYS}
    counts["all"] = len(threads)
    for thread in threads:
        follow_up_status = str(thread.follow_up_status or "").strip().lower()
        if follow_up_status in {"overdue", "deadline_soon"}:
            counts["today"] += 1
        if follow_up_status == "reply_needed":
            counts["reply"] += 1
        if follow_up_status == "approval_pending":
            counts["approval"] += 1
        if follow_up_status in {"waiting_for_reply", "no_reply_3d"}:
            counts["waiting"] += 1
        if follow_up_status == "review_needed" or bool(thread.needs_review):
            counts["review"] += 1
    return counts


def build_dashboard_thread_page_state(
    threads: list[ThreadOverview],
    *,
    filter_key: object,
    page: object,
    page_size: int = DASHBOARD_THREAD_PAGE_SIZE,
) -> tuple[list[ThreadOverview], dict[str, object], dict[str, int]]:
    normalized_filter = normalize_dashboard_thread_filter(filter_key)
    normalized_page_size = max(1, int(page_size or DASHBOARD_THREAD_PAGE_SIZE))
    filtered_threads = [thread for thread in threads if matches_dashboard_thread_filter(thread, normalized_filter)]
    total_items = len(filtered_threads)
    total_pages = max(1, ceil(total_items / normalized_page_size)) if total_items else 1
    current_page = min(normalize_dashboard_thread_page(page), total_pages)
    start_index = (current_page - 1) * normalized_page_size
    end_index = min(start_index + normalized_page_size, total_items)
    page_threads = filtered_threads[start_index:end_index]
    pagination = {
        "filter": normalized_filter,
        "page": current_page,
        "page_size": normalized_page_size,
        "total_items": total_items,
        "total_pages": total_pages,
        "start_item": start_index + 1 if total_items else 0,
        "end_item": end_index,
        "has_previous": current_page > 1,
        "has_next": current_page < total_pages,
    }
    return page_threads, pagination, build_dashboard_thread_filter_counts(threads)


def build_dashboard_task_dicts(tasks: list[TodoItemRecord]) -> list[dict[str, object]]:
    task_dicts: list[dict[str, object]] = []
    today = date.today()
    for task in tasks:
        due_str = ""
        is_urgent = False
        if task.due_date:
            parsed_due_datetime = parse_datetime_text(task.due_date)
            due_str = (
                parsed_due_datetime.date().isoformat()
                if parsed_due_datetime is not None
                else str(task.due_date).split("T")[0].split(" ")[0]
            )
            try:
                parsed_due = (
                    parsed_due_datetime.date()
                    if parsed_due_datetime is not None
                    else datetime.fromisoformat(task.due_date).date()
                )
                if parsed_due <= today:
                    is_urgent = True
            except Exception:
                pass
        task_dicts.append(
            {
                "id": task.id,
                "action_text": task.action_text,
                "source": task.mail_subject or "?쒕ぉ ?놁쓬",
                "due_date": due_str,
                "is_urgent": is_urgent,
            }
        )
    return task_dicts


def _format_task_timestamp(value: str | None, *, include_time: bool) -> str:
    if not value:
        return ""
    parsed = parse_datetime_text(value)
    if parsed is not None:
        return parsed.strftime("%Y-%m-%d %H:%M" if include_time else "%Y-%m-%d")
    text = str(value)
    if include_time:
        return text.replace("T", " ")[:16]
    return text.split("T")[0].split(" ")[0]


def build_completed_task_dicts(tasks: list[TodoItemRecord]) -> list[dict[str, object]]:
    task_dicts: list[dict[str, object]] = []
    for task in tasks:
        task_dicts.append(
            {
                "id": task.id,
                "action_text": task.action_text,
                "source": task.mail_subject or "?쒕ぉ ?놁쓬",
                "due_date": _format_task_timestamp(task.due_date, include_time=False),
                "completed_at": _format_task_timestamp(task.completed_at, include_time=True),
                "note": task.note or "",
            }
        )
    return task_dicts


def _collapse_preview(value: str | None, limit: int = 160) -> str:
    collapsed = " ".join((value or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[: max(0, limit - 3)].rstrip()}..."


def _format_thread_datetime(value: str | None, *, include_time: bool = True) -> str:
    if not value:
        return ""
    parsed = parse_datetime_text(value)
    if parsed is not None:
        return parsed.strftime("%Y-%m-%d %H:%M" if include_time else "%Y-%m-%d")
    text = str(value)
    if include_time:
        return text.replace("T", " ")[:16]
    return text.split("T")[0].split(" ")[0]


def _build_thread_action_dicts(
    actions: list[ActionItemRecord],
    mail_lookup: dict[int, MailRecord],
) -> list[dict[str, object]]:
    action_dicts: list[dict[str, object]] = []
    for action in actions:
        source_mail = mail_lookup.get(action.mail_id)
        action_dicts.append(
            {
                "id": action.id,
                "mail_id": action.mail_id,
                "text": action.action_text,
                "due_date": _format_thread_datetime(action.due_date),
                "owner": action.owner or "",
                "note": action.note or "",
                "source": source_mail.subject if source_mail else "",
                "sender_email": source_mail.sender_email if source_mail else "",
                "done": bool(action.done_flag),
            }
        )
    return action_dicts


def _build_compact_thread_action_dicts(actions: list[ActionItemRecord]) -> list[dict[str, object]]:
    return [
        {
            "id": action.id,
            "text": action.action_text,
        }
        for action in actions
        if str(action.action_text or "").strip()
    ]


def _build_thread_timeline_dicts(
    mails: list[MailRecord],
    address_book_service: Any,
    current_user_email: str,
) -> list[dict[str, object]]:
    timeline_dicts: list[dict[str, object]] = []
    current_user_normalized = str(current_user_email or "").strip().lower()
    for mail in mails:
        sender_name = address_book_service.resolve_display_name(mail.sender_email, mail.sender_name or None)
        summary = mail.summary_short or _collapse_preview(mail.raw_preview, limit=150) or mail.subject
        direction = (
            "outbound"
            if current_user_normalized and str(mail.sender_email or "").strip().lower() == current_user_normalized
            else "inbound"
        )
        timeline_dicts.append(
            {
                "id": mail.id,
                "subject": mail.subject,
                "sender": sender_name,
                "sender_email": mail.sender_email,
                "received_at": _format_thread_datetime(mail.received_at),
                "summary": summary,
                "direction": direction,
                "priority": mail.priority or "unknown",
                "action_status": mail.my_action_status or "",
                "status": mail.status,
                "due_date": _format_thread_datetime(mail.due_date),
                "deadline_raw": mail.deadline_raw or "",
                "attachments": mail.attachment_names[:3],
                "attachment_paths": mail.attachment_paths,
                "to_list": mail.to_list,
                "cc_list": mail.cc_list,
                "raw_preview": mail.raw_preview or "",
            }
        )
    return timeline_dicts


def _select_thread_deadline_mail(mails: list[MailRecord]) -> MailRecord | None:
    candidates = [mail for mail in mails if mail.due_date or mail.deadline_raw]
    if not candidates:
        return None

    def _deadline_key(mail: MailRecord) -> tuple[int, datetime, int]:
        due_at = parse_datetime_text(mail.due_date)
        due_rank = 0 if due_at is not None else 1
        due_value = due_at or datetime.max
        return due_rank, due_value, -mail.id

    return min(candidates, key=_deadline_key)


def _build_latest_mail_dict(
    latest_mail: MailRecord | None,
    *,
    address_book_service: Any,
    current_user_email: str,
) -> dict[str, object]:
    if latest_mail is None:
        return {}
    current_user_normalized = str(current_user_email or "").strip().lower()
    direction = (
        "outbound"
        if current_user_normalized and str(latest_mail.sender_email or "").strip().lower() == current_user_normalized
        else "inbound"
    )
    return {
        "id": latest_mail.id,
        "subject": latest_mail.subject,
        "sender": address_book_service.resolve_display_name(
            latest_mail.sender_email,
            latest_mail.sender_name or None,
        ),
        "sender_email": latest_mail.sender_email,
        "received_at": _format_thread_datetime(latest_mail.received_at),
        "to_list": latest_mail.to_list,
        "cc_list": latest_mail.cc_list,
        "raw_preview": latest_mail.raw_preview or "",
        "attachments": latest_mail.attachment_names,
        "attachment_paths": latest_mail.attachment_paths,
        "direction": direction,
    }


def build_sync_status_dict(
    config: AppConfig,
    *,
    sync_snapshot: dict[str, object] | None,
    scheduler_status: dict[str, object] | None,
    analysis_warning: str | None,
    backlog_counts: dict[str, int] | None,
) -> dict[str, object]:
    snapshot = sync_snapshot or {}
    scheduler = scheduler_status or {}
    backlog = backlog_counts or {}
    sync_warning = str(snapshot.get("sync_warning") or "").strip()
    latest_warning = str(scheduler.get("last_warning") or "").strip()
    current_warning = sync_warning or latest_warning or str(analysis_warning or "").strip()
    return {
        "mailbox": str(snapshot.get("mailbox") or config.mailbox or "INBOX"),
        "sync_days": int(snapshot.get("sync_days") or config.sync_days),
        "interval_minutes": int(scheduler.get("interval_minutes") or config.sync_interval_minutes),
        "last_sync_at": snapshot.get("last_sync_at"),
        "next_run_at": scheduler.get("next_run_at"),
        "last_finished_at": scheduler.get("last_finished_at"),
        "last_result_summary": scheduler.get("last_result_summary"),
        "backfill_active": bool(snapshot.get("backfill_active")),
        "backfill_completed_at": snapshot.get("backfill_completed_at"),
        "cursor_before_uid": snapshot.get("cursor_before_uid"),
        "pending_analysis_count": int(backlog.get("pending", 0)),
        "failed_analysis_count": int(backlog.get("failed", 0)),
        "current_warning": current_warning,
        "analysis_warning": analysis_warning or "",
        "scheduler_state": str(scheduler.get("scheduler_state") or "stopped"),
    }


def validate_send_template(
    template: SendTemplate,
    *,
    require_schedule: bool,
) -> list[str]:
    errors: list[str] = []
    if require_schedule and not template.template_name.strip():
        errors.append("등록 이름을 입력해 주세요.")
    if not template.to_list:
        errors.append("받는 사람(To)을 한 명 이상 입력해 주세요.")
    if require_schedule:
        if parse_datetime_text(template.first_send_at) is None:
            errors.append("첫 발송 일시를 올바르게 입력해 주세요.")
        if parse_time_text(template.send_time) is None:
            errors.append("발송 시간을 올바르게 입력해 주세요.")
    return errors


def build_follow_up_mail_template(
    thread: ThreadOverview,
    thread_mails: list[MailRecord],
    *,
    current_user_email: str,
    current_user_name: str,
) -> MailTemplate:
    if not thread_mails:
        raise ValueError("후속 초안을 만들 메일이 없습니다.")

    normalized_self = str(current_user_email or "").strip().lower()
    latest_mail = thread_mails[0]
    recipients: list[str] = []
    cc_recipients: list[str] = []

    latest_sender = str(latest_mail.sender_email or "").strip()
    latest_sender_normalized = latest_sender.lower()
    if latest_sender and latest_sender_normalized != normalized_self:
        recipients.append(latest_sender)
        cc_candidates = [*latest_mail.to_list, *latest_mail.cc_list]
    else:
        cc_candidates = []
        for candidate in latest_mail.to_list:
            normalized_candidate = str(candidate or "").strip().lower()
            if normalized_candidate and normalized_candidate != normalized_self:
                recipients.append(str(candidate).strip())
        cc_candidates.extend(latest_mail.cc_list)

    seen_cc: set[str] = {value.strip().lower() for value in recipients if value.strip()}
    for candidate in cc_candidates:
        normalized_candidate = str(candidate or "").strip().lower()
        if not normalized_candidate or normalized_candidate == normalized_self or normalized_candidate in seen_cc:
            continue
        seen_cc.add(normalized_candidate)
        cc_recipients.append(str(candidate).strip())

    if not recipients:
        raise ValueError("후속 초안에 넣을 수신자를 찾지 못했습니다.")

    subject = latest_mail.subject.strip() or thread.thread_subject.strip() or "후속 메일"
    if not subject.lower().startswith(("re:", "fw:", "fwd:")):
        subject = f"Re: {subject}"

    summary_lines: list[str] = []
    if thread.current_conclusion.strip():
        summary_lines.append(f"- 현재 결론: {thread.current_conclusion.strip()}")
    if thread.follow_up_detail.strip() and thread.follow_up_detail.strip() != thread.current_conclusion.strip():
        summary_lines.append(f"- 후속 메모: {thread.follow_up_detail.strip()}")
    if thread.overall_summary.strip():
        summary_lines.append(f"- 요약: {thread.overall_summary.strip()}")

    body_lines = [
        "안녕하세요,",
        "",
        "이전 메일 흐름과 관련해 후속 연락드립니다.",
        *summary_lines,
        "",
        "확인 부탁드립니다.",
        "",
        "감사합니다.",
    ]
    if current_user_name.strip():
        body_lines.append(current_user_name.strip())

    now_text = datetime.now().replace(microsecond=0).strftime("%Y-%m-%d %H:%M")
    return MailTemplate(
        id=None,
        template_name=f"후속 초안 - {subject[:40]}",
        subject=subject,
        body="\n".join(body_lines).strip(),
        to_list=recipients,
        cc_list=cc_recipients,
        attachment_paths=[],
        repeat_type="none",
        send_time=datetime.now().strftime("%H:%M"),
        first_send_at=now_text,
    )


def build_priority_thread_dicts(
    threads: list[ThreadOverview],
    *,
    mail_repository: Any,
    address_book_service: Any,
    current_user_email: str,
    include_details: bool = True,
) -> list[dict[str, object]]:
    priority_threads: list[dict[str, object]] = []
    thread_keys = [thread.thread_key for thread in threads if thread.thread_key]
    thread_actions_by_key = mail_repository.list_thread_action_items_by_keys(thread_keys)
    thread_mails_by_key = (
        mail_repository.list_thread_mails_by_keys(thread_keys, limit_per_thread=10)
        if include_details
        else {}
    )
    for thread in threads:
        thread_actions = thread_actions_by_key.get(thread.thread_key, [])
        my_actions = [action for action in thread_actions if action.scope == "my"]
        latest_sender = address_book_service.resolve_display_name(
            thread.latest_sender_email,
            thread.latest_sender_name or None,
        )
        thread_dict: dict[str, object] = {
            "thread_key": thread.thread_key,
            "latest_mail_id": thread.latest_mail_id,
            "subject": thread.thread_subject,
            "latest_sender": latest_sender,
            "latest_sender_email": thread.latest_sender_email,
            "latest_received_at": _format_thread_datetime(thread.latest_received_at),
            "latest_summary": thread.latest_summary,
            "overall_summary": thread.overall_summary,
            "changed_since_last": thread.changed_since_last,
            "current_conclusion": thread.current_conclusion,
            "follow_up_status": thread.follow_up_status,
            "follow_up_label": thread.follow_up_label,
            "follow_up_detail": thread.follow_up_detail,
            "due_date": _format_thread_datetime(thread.due_date),
            "priority": thread.priority or "unknown",
            "category": thread.category or "",
            "latest_mail_status": thread.latest_mail_status,
            "latest_action_status": thread.latest_action_status or "",
            "mail_count": thread.mail_count,
            "open_action_count": thread.open_action_count,
            "priority_score": thread.priority_score,
            "priority_reasons": thread.priority_reasons,
            "needs_review": thread.needs_review,
            "has_failed_analysis": thread.has_failed_analysis,
            "my_actions": _build_compact_thread_action_dicts(my_actions) if not include_details else [],
        }
        if include_details:
            thread_mails = thread_mails_by_key.get(thread.thread_key, [])
            latest_mail = thread_mails[0] if thread_mails else None
            mail_lookup = {mail.id: mail for mail in thread_mails}
            mail_actions = [action for action in thread_actions if action.scope == "mail"]
            deadline_mail = _select_thread_deadline_mail(thread_mails)
            thread_dict.update(
                {
                    "deadline_raw": deadline_mail.deadline_raw if deadline_mail else "",
                    "participant_count": thread.participant_count,
                    "participants": thread.participants,
                    "importance_score": thread.importance_score,
                    "urgency_score": thread.urgency_score,
                    "reply_score": thread.reply_score,
                    "approval_score": thread.approval_score,
                    "today_score": thread.today_score,
                    "latest_mail": _build_latest_mail_dict(
                        latest_mail,
                        address_book_service=address_book_service,
                        current_user_email=current_user_email,
                    ),
                    "timeline": _build_thread_timeline_dicts(
                        thread_mails,
                        address_book_service,
                        current_user_email,
                    ),
                    "my_actions": _build_thread_action_dicts(my_actions, mail_lookup),
                    "mail_actions": _build_thread_action_dicts(mail_actions, mail_lookup),
                }
            )
        priority_threads.append(thread_dict)
    return priority_threads


def _coerce_component_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_component_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
        return default
    return bool(value)


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _split_attachment_values(raw_value: object) -> list[str]:
    return [value.strip() for value in str(raw_value or "").split(",") if value.strip()]


def _merge_component_config(current_config: AppConfig, payload: object) -> AppConfig:
    cfg = payload if isinstance(payload, dict) else {}
    return AppConfig(
        user_email=str(cfg.get("user_email", current_config.user_email) or "").strip(),
        user_display_name=str(cfg.get("user_display_name", current_config.user_display_name) or "").strip(),
        user_department=str(cfg.get("user_department", current_config.user_department) or "").strip(),
        user_job_title=str(cfg.get("user_job_title", current_config.user_job_title) or "").strip(),
        mailbox=str(cfg.get("mailbox", current_config.mailbox) or "").strip() or current_config.mailbox or "INBOX",
        sync_days=_coerce_component_int(cfg.get("sync_days"), current_config.sync_days),
        sync_batch_size=_coerce_component_int(cfg.get("sync_batch_size"), current_config.sync_batch_size),
        sync_scan_limit=_coerce_component_int(cfg.get("sync_scan_limit"), current_config.sync_scan_limit),
        sync_interval_minutes=_coerce_component_int(
            cfg.get("sync_interval_minutes"),
            current_config.sync_interval_minutes,
        ),
        store_raw_body=_coerce_component_bool(cfg.get("store_raw_body"), current_config.store_raw_body),
        preview_max_chars=_coerce_component_int(cfg.get("preview_max_chars"), current_config.preview_max_chars),
        ai_provider=str(cfg.get("ai_provider", current_config.ai_provider) or current_config.ai_provider or AI_PROVIDER_GEMINI)
        .strip()
        .lower()
        or AI_PROVIDER_GEMINI,
        ai_base_url=str(cfg.get("ai_base_url", current_config.ai_base_url) or current_config.ai_base_url or "").strip(),
        gemini_model=str(cfg.get("gemini_model", current_config.gemini_model) or current_config.gemini_model).strip()
        or current_config.gemini_model,
        gemini_timeout_seconds=_coerce_component_int(
            cfg.get("gemini_timeout_seconds"),
            current_config.gemini_timeout_seconds,
        ),
        ui_language=current_config.ui_language,
    ).normalized()


def build_settings_submission(
    current_config: AppConfig,
    payload: object,
) -> tuple[AppConfig, str | None, str | None, str | None]:
    raw_payload = payload if isinstance(payload, dict) else {}
    next_config = _merge_component_config(current_config, raw_payload.get("config"))
    return (
        next_config,
        _optional_text(raw_payload.get("password")),
        _optional_text(raw_payload.get("api_key")),
        _optional_text(raw_payload.get("hanlim_api_key")),
    )


def build_mailbox_test_submission(current_config: AppConfig, payload: object) -> tuple[AppConfig, str | None]:
    next_config, password, _, _ = build_settings_submission(current_config, payload)
    return next_config, password


def _normalize_component_datetime(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = parse_datetime_text(text)
    return parsed.strftime("%Y-%m-%d %H:%M") if parsed is not None else text


def _normalize_component_time(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = parse_time_text(text)
    return parsed.strftime("%H:%M") if parsed is not None else text


def build_mail_template_from_payload(address_book_service: Any, payload: object) -> MailTemplate:
    raw_payload = payload if isinstance(payload, dict) else {}
    template_id = raw_payload.get("id")
    to_raw = raw_payload.get("to_raw", "")
    cc_raw = raw_payload.get("cc_raw", "")
    return MailTemplate(
        id=int(template_id) if template_id else None,
        template_name=str(raw_payload.get("name", "") or "").strip(),
        subject=str(raw_payload.get("subject", "") or "").strip(),
        body=str(raw_payload.get("body", "") or ""),
        to_list=address_book_service.resolve_recipient_tokens(str(to_raw or "")),
        cc_list=address_book_service.resolve_recipient_tokens(str(cc_raw or "")),
        attachment_paths=_split_attachment_values(raw_payload.get("attachment_raw")),
        repeat_type=str(raw_payload.get("repeat_type", "none") or "none"),
        send_time=_normalize_component_time(raw_payload.get("send_time")),
        first_send_at=_normalize_component_datetime(raw_payload.get("first_send_at")),
    )


def build_send_registration_from_payload(address_book_service: Any, payload: object) -> SendTemplate:
    raw_payload = payload if isinstance(payload, dict) else {}
    template_id = raw_payload.get("id")
    to_raw = raw_payload.get("to_raw", "")
    cc_raw = raw_payload.get("cc_raw", "")
    return SendTemplate(
        id=int(template_id) if template_id else None,
        template_name=str(raw_payload.get("name", "") or "").strip(),
        subject=str(raw_payload.get("subject", "") or "").strip(),
        body=str(raw_payload.get("body", "") or ""),
        to_list=address_book_service.resolve_recipient_tokens(str(to_raw or "")),
        cc_list=address_book_service.resolve_recipient_tokens(str(cc_raw or "")),
        attachment_paths=_split_attachment_values(raw_payload.get("attachment_raw")),
        repeat_type=str(raw_payload.get("repeat_type", "none") or "none"),
        send_time=_normalize_component_time(raw_payload.get("send_time")),
        first_send_at=_normalize_component_datetime(raw_payload.get("first_send_at")),
        enabled=_coerce_component_bool(raw_payload.get("enabled"), True),
    )


def format_mailbox_cycle_message(result: Any) -> str:
    message = (
        f"가져옴 {result.fetched_count}건 / "
        f"저장 {result.saved_count}건 / "
        f"AI 성공 {result.analysis_success_count}건 / "
        f"AI 실패 {result.analysis_failure_count}건"
    )
    if getattr(result, "deleted_old_count", 0):
        message += f" / 정리 {result.deleted_old_count}건"
    if getattr(result, "analysis_warning", None):
        message += f" / {result.analysis_warning}"
    if getattr(result, "limited_by_batch", False):
        message += " / 최신 배치 범위까지만 처리됨"
    return message
