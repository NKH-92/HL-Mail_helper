"""Typed models for database and service layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

MailCategory = Literal["ACT", "FYI", "APR", "SCH", "QLT", "ETC"]
MailPriority = Literal["high", "medium", "low", "unknown"]
MailOwnershipStatus = Literal["direct_action", "review_needed", "reference_only"]
MailActionClassification = Literal["ACTION_SELF", "ACTION_SHARED", "APPROVAL_REQUEST", "FYI", "ANNOUNCEMENT", "UNCLEAR"]
MailActionOwner = Literal["me", "team", "other", "unknown"]
MailActionType = Literal["reply", "review", "approve", "submit", "prepare", "attend", "monitor", "none"]
MailWorkStatus = Literal["todo", "doing", "done"]
AnalysisStatus = Literal["pending", "success", "failed"]
TemplateRepeatType = Literal["none", "daily", "weekly", "monthly"]


@dataclass(slots=True)
class ParsedMail:
    """Normalized mail payload after parsing."""

    message_id: str
    subject: str
    normalized_subject: str
    thread_key: str
    sender_name: str
    sender_email: str
    to_list: list[str]
    cc_list: list[str]
    received_at: datetime | None
    raw_preview: str
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)
    attachment_names: list[str] = field(default_factory=list)
    attachment_paths: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MailRecord:
    """Mail row plus decoded list fields."""

    id: int
    message_id: str
    subject: str
    normalized_subject: str
    thread_key: str
    in_reply_to: str | None
    references: list[str]
    sender_name: str
    sender_email: str
    to_list: list[str]
    cc_list: list[str]
    received_at: str | None
    raw_preview: str
    attachment_names: list[str]
    attachment_paths: list[str]
    category: str | None
    priority: str | None
    summary_short: str | None
    summary_long: list[str]
    due_date: str | None
    my_action_required: bool
    my_action_status: str | None
    ownership_reason: list[str]
    confidence: float | None
    status: str
    analysis_status: str
    analysis_error: str | None
    created_at: str
    updated_at: str
    action_classification: str | None = None
    action_owner: str | None = None
    action_types: list[str] = field(default_factory=list)
    deadline_raw: str | None = None
    evidence: list[str] = field(default_factory=list)
    analysis_reason: str | None = None
    suggested_task_title: str | None = None


@dataclass(slots=True)
class ActionItemRecord:
    """Action item linked to a mail."""

    id: int
    mail_id: int
    scope: str
    action_text: str
    due_date: str | None
    owner: str | None
    note: str
    done_flag: bool
    completed_at: str | None
    created_at: str
    updated_at: str


@dataclass(slots=True)
class TodoItemRecord:
    """Open personal action item with mail context for dashboard rendering."""

    id: int
    mail_id: int
    action_text: str
    due_date: str | None
    note: str
    created_at: str
    completed_at: str | None
    mail_subject: str
    sender_email: str
    received_at: str | None
    mail_status: str


@dataclass(slots=True)
class DashboardStats:
    """Aggregated dashboard counters."""

    total_mails: int
    total_threads: int
    open_mail_count: int
    open_my_action_count: int
    due_today_action_count: int
    overdue_action_count: int
    failed_analysis_count: int
    review_queue_count: int
    completed_my_action_count: int


@dataclass(slots=True)
class ThreadOverview:
    """Thread-level summary used for priority ranking and follow-up tracking."""

    thread_key: str
    latest_mail_id: int
    thread_subject: str
    latest_received_at: str | None
    latest_sender_name: str
    latest_sender_email: str
    latest_summary: str
    overall_summary: str
    changed_since_last: str
    current_conclusion: str
    follow_up_status: str
    follow_up_label: str
    follow_up_detail: str
    due_date: str | None
    priority: str | None
    category: str | None
    latest_mail_status: str
    latest_action_status: str | None
    mail_count: int
    participant_count: int
    participants: list[str]
    open_action_count: int
    importance_score: int
    urgency_score: int
    reply_score: int
    approval_score: int
    today_score: int
    priority_score: int
    priority_reasons: list[str]
    needs_review: bool = False
    has_failed_analysis: bool = False


@dataclass(slots=True)
class AppSettingsSnapshot:
    """Single-row app settings snapshot."""

    sync_days: int
    mailbox: str
    gemini_model: str
    last_sync_at: str | None
    created_at: str
    updated_at: str
    sync_cursor_before_uid: int | None = None
    sync_cursor_mailbox: str | None = None
    sync_cursor_days: int | None = None
    sync_backfill_completed_at: str | None = None


@dataclass(slots=True)
class SendTemplate:
    """Template used for manual and scheduled sending."""

    id: int | None
    template_name: str
    subject: str
    body: str
    to_list: list[str]
    cc_list: list[str]
    attachment_paths: list[str]
    repeat_type: TemplateRepeatType
    send_time: str
    first_send_at: str
    enabled: bool
    created_at: str | None = None
    updated_at: str | None = None
    last_sent_at: str | None = None


@dataclass(slots=True)
class MailTemplate:
    """Saved mail form preset used to refill the send form."""

    id: int | None
    template_name: str
    subject: str
    body: str
    to_list: list[str]
    cc_list: list[str]
    attachment_paths: list[str]
    repeat_type: TemplateRepeatType
    send_time: str
    first_send_at: str
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(slots=True)
class SendLogRecord:
    """Log entry for template sending."""

    id: int
    template_id: int | None
    sent_at: str
    result: str
    recipients: list[str]
    subject: str
    error_message: str | None


@dataclass(slots=True)
class SyncResult:
    """Summary of one mailbox synchronization run."""

    scanned_count: int
    fetched_count: int
    saved_count: int
    skipped_old_count: int
    duplicate_count: int
    parse_error_count: int
    limited_by_batch: bool
    deleted_old_count: int = 0


@dataclass(slots=True)
class FetchedMessage:
    """Raw IMAP message bytes plus metadata gathered during header scan."""

    raw_bytes: bytes
    fallback_message_id: str | None = None
    received_at: datetime | None = None


@dataclass(slots=True)
class SyncFetchResult:
    """Raw IMAP fetch batch after lightweight filtering."""

    messages: list[FetchedMessage]
    scanned_count: int
    limited_by_batch: bool
    next_cursor_before_uid: int | None = None
    had_fetch_failures: bool = False
