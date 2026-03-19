"""Read-only popup views opened from the desktop tray."""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any

from apscheduler.schedulers.base import STATE_PAUSED, STATE_RUNNING
import streamlit as st


POPUP_TODO_VIEW = "todos"
POPUP_AUTO_SEND_VIEW = "autosend"
SUPPORTED_POPUP_VIEWS = {POPUP_TODO_VIEW, POPUP_AUTO_SEND_VIEW}


def popup_page_title(popup_mode: str | None) -> str:
    if popup_mode == POPUP_TODO_VIEW:
        return "MailAI | 내 할일"
    if popup_mode == POPUP_AUTO_SEND_VIEW:
        return "MailAI | 자동발송"
    return "MailAI Portable"


def is_supported_popup_view(popup_mode: str | None) -> bool:
    return popup_mode in SUPPORTED_POPUP_VIEWS


def render_tray_popup(context: Any, popup_mode: str) -> None:
    _inject_popup_css()
    if popup_mode == POPUP_TODO_VIEW:
        _render_todo_popup(context)
        return
    if popup_mode == POPUP_AUTO_SEND_VIEW:
        _render_autosend_popup(context)
        return
    st.error("지원하지 않는 팝업입니다.")


def _inject_popup_css() -> None:
    st.markdown(
        """
        <style>
            :root {
                color-scheme: light;
                --popup-bg: linear-gradient(180deg, #f5efe6 0%, #ebe2d4 100%);
                --popup-card: linear-gradient(180deg, rgba(255, 250, 243, 0.98), rgba(247, 240, 232, 0.98));
                --popup-line: #ddd1c2;
                --popup-line-strong: #ccbdaa;
                --popup-text: #0f1a2b;
                --popup-muted: #4c6178;
                --popup-primary: #1976d2;
                --popup-primary-soft: rgba(25, 118, 210, 0.12);
                --popup-danger-bg: #ffebee;
                --popup-danger-text: #c62828;
                --popup-warn-bg: #fff3e0;
                --popup-warn-text: #ef6c00;
            }

            html, body, [data-testid="stAppViewContainer"], .stApp {
                background: var(--popup-bg) !important;
            }

            [data-testid="stAppViewContainer"] > .main {
                padding-top: 0 !important;
            }

            .block-container {
                max-width: 100% !important;
                padding: 0.95rem 0.95rem 1rem !important;
            }

            .tray-popup-header {
                margin-bottom: 0.75rem;
                padding: 0.95rem 1rem;
                border-radius: 18px;
                border: 1px solid var(--popup-line);
                background:
                    radial-gradient(circle at top right, rgba(25, 118, 210, 0.12), rgba(25, 118, 210, 0) 28%),
                    linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(246, 249, 253, 0.98));
                box-shadow: 0 14px 34px rgba(15, 23, 42, 0.06);
            }

            .tray-popup-kicker {
                display: inline-flex;
                align-items: center;
                gap: 0.35rem;
                padding: 0.28rem 0.58rem;
                border-radius: 999px;
                background: var(--popup-primary-soft);
                color: var(--popup-primary);
                font-size: 0.68rem;
                font-weight: 800;
                letter-spacing: 0.04em;
                text-transform: uppercase;
            }

            .tray-popup-title {
                margin-top: 0.55rem;
                font-size: 1.35rem;
                font-weight: 800;
                line-height: 1.14;
                color: var(--popup-text);
            }

            .tray-popup-copy {
                margin-top: 0.35rem;
                color: var(--popup-muted);
                line-height: 1.55;
                font-size: 0.82rem;
            }

            [data-testid="stHorizontalBlock"] {
                gap: 0.55rem !important;
            }

            [data-testid="stMetric"] {
                border: 1px solid var(--popup-line);
                border-radius: 16px;
                background: var(--popup-card);
                box-shadow: none !important;
                padding: 0.7rem 0.78rem;
            }

            [data-testid="stMetricLabel"] {
                color: var(--popup-muted) !important;
                font-size: 0.68rem !important;
                font-weight: 700 !important;
                letter-spacing: 0.04em;
                text-transform: uppercase;
            }

            [data-testid="stMetricValue"] {
                color: var(--popup-text) !important;
                font-size: 1rem !important;
                font-weight: 800 !important;
                line-height: 1.1 !important;
            }

            .stCaptionContainer {
                color: var(--popup-muted) !important;
                font-size: 0.72rem !important;
            }

            .stButton > button {
                width: 100%;
                border-radius: 12px;
                border: 1px solid rgba(25, 118, 210, 0.18);
                background: #ffffff;
                color: var(--popup-primary);
                font-size: 0.78rem;
                font-weight: 700;
                padding: 0.45rem 0.75rem;
                box-shadow: none !important;
            }

            .stButton > button:hover {
                border-color: rgba(25, 118, 210, 0.28);
                background: #f8fbff;
            }

            .tray-popup-card {
                padding: 0.82rem 0.88rem;
                border-radius: 16px;
                border: 1px solid var(--popup-line);
                background: var(--popup-card);
                box-shadow: none;
            }

            .tray-popup-card + .tray-popup-card {
                margin-top: 0.58rem;
            }

            .tray-popup-row {
                display: flex;
                justify-content: space-between;
                gap: 0.8rem;
                align-items: flex-start;
            }

            .tray-popup-name {
                font-size: 0.9rem;
                font-weight: 800;
                color: var(--popup-text);
                line-height: 1.35;
                margin-bottom: 0.16rem;
            }

            .tray-popup-subject {
                color: #22384d;
                line-height: 1.5;
                font-size: 0.8rem;
                margin-bottom: 0.3rem;
            }

            .tray-popup-meta {
                color: var(--popup-muted);
                font-size: 0.74rem;
                line-height: 1.45;
            }

            .tray-popup-chip {
                display: inline-flex;
                align-items: center;
                padding: 0.28rem 0.58rem;
                border-radius: 999px;
                background: #edf4ff;
                color: var(--popup-primary);
                font-size: 0.68rem;
                font-weight: 800;
                white-space: nowrap;
                border: 1px solid rgba(25, 118, 210, 0.12);
            }

            .tray-popup-chip.is-danger {
                background: var(--popup-danger-bg);
                border-color: #ffcdd2;
                color: var(--popup-danger-text);
            }

            .tray-popup-chip.is-warn {
                background: var(--popup-warn-bg);
                border-color: #ffcc80;
                color: var(--popup-warn-text);
            }

            .tray-popup-chip.is-muted {
                background: #f3f6fb;
                border-color: #dde6f0;
                color: #51667d;
            }

            [data-testid="stAlert"] {
                border-radius: 14px;
                border-color: rgba(239, 108, 0, 0.16) !important;
                background: #fff7ed !important;
                color: #b45309 !important;
            }

            @media (max-width: 640px) {
                .tray-popup-row {
                    flex-direction: column;
                }
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_header(kicker: str, title: str, copy: str) -> None:
    st.markdown(
        (
            "<div class='tray-popup-header'>"
            f"<div class='tray-popup-kicker'>{escape(kicker)}</div>"
            f"<div class='tray-popup-title'>{escape(title)}</div>"
            f"<div class='tray-popup-copy'>{escape(copy)}</div>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def _render_todo_popup(context: Any) -> None:
    todos = context.mail_repository.list_open_my_action_items()
    now = datetime.now()
    today = now.date()
    overdue_count = 0
    due_today_count = 0

    for item in todos:
        due_value = _parse_datetime(item.due_date)
        if due_value is None:
            continue
        if due_value.date() < today:
            overdue_count += 1
        elif due_value.date() == today:
            due_today_count += 1

    _render_header(
        "Tray",
        "내 할일",
        "메인 창을 열지 않아도 열린 할일과 마감 상태를 빠르게 확인할 수 있습니다.",
    )

    top_cols = st.columns([1.0, 1.0, 1.0, 0.9], gap="small")
    top_cols[0].metric("열린 할일", len(todos))
    top_cols[1].metric("오늘 마감", due_today_count)
    top_cols[2].metric("지연", overdue_count)
    with top_cols[3]:
        st.caption(f"마지막 갱신 {_format_datetime(now)}")
        if st.button("새로고침", use_container_width=True, key="todo-popup-refresh"):
            st.rerun()

    if not todos:
        st.info("열린 할일이 없습니다.")
        return

    for item in todos:
        due_label, due_class = _due_chip(item.due_date)
        due_chip_class = f"tray-popup-chip {due_class}".strip()
        sender_name = context.address_book_service.resolve_display_name(item.sender_email, item.sender_email)
        st.markdown(
            (
                "<div class='tray-popup-card'>"
                "<div class='tray-popup-row'>"
                "<div>"
                f"<div class='tray-popup-name'>{escape(item.action_text or '할일')}</div>"
                f"<div class='tray-popup-subject'>{escape(item.mail_subject or '(제목 없음)')}</div>"
                f"<div class='tray-popup-meta'>보낸 사람 {escape(sender_name)}</div>"
                f"<div class='tray-popup-meta'>수신 {_format_datetime(item.received_at)}</div>"
                "</div>"
                f"<div class='{due_chip_class}'>{escape(due_label)}</div>"
                "</div>"
                "</div>"
            ),
            unsafe_allow_html=True,
        )


def _render_autosend_popup(context: Any) -> None:
    templates = context.template_service.list_templates()
    enabled_templates = [template for template in templates if template.enabled]
    scheduled_items: list[tuple[Any, datetime]] = []
    invalid_schedule_count = 0

    for template in enabled_templates:
        next_run = context.send_service.calculate_next_run(template)
        if next_run is None:
            invalid_schedule_count += 1
            continue
        scheduled_items.append((template, next_run))

    scheduled_items.sort(key=lambda item: (item[1], item[0].template_name.lower(), item[0].id or 0))
    next_run_label = _format_datetime(scheduled_items[0][1]) if scheduled_items else "-"

    _render_header(
        "Tray",
        "자동발송",
        "예정된 자동발송과 다음 실행 시각을 빠르게 확인할 수 있습니다.",
    )

    status_label = _scheduler_status_label(context.scheduler_manager)
    top_cols = st.columns([1.0, 1.0, 1.0, 0.9], gap="small")
    top_cols[0].metric("활성 템플릿", len(enabled_templates))
    top_cols[1].metric("예약 건수", len(scheduled_items))
    top_cols[2].metric("스케줄러", status_label)
    with top_cols[3]:
        st.caption(f"다음 실행 {next_run_label}")
        if st.button("새로고침", use_container_width=True, key="autosend-popup-refresh"):
            st.rerun()

    if invalid_schedule_count:
        st.warning(f"일정이 올바르지 않아 건너뛴 활성 항목이 {invalid_schedule_count}건 있습니다.")

    if not scheduled_items:
        st.info("예약된 자동발송이 없습니다.")
        return

    for template, next_run in scheduled_items:
        recipients = len(template.to_list) + len(template.cc_list)
        repeat_label = _repeat_label(template.repeat_type)
        st.markdown(
            (
                "<div class='tray-popup-card'>"
                "<div class='tray-popup-row'>"
                "<div>"
                f"<div class='tray-popup-name'>{escape(template.template_name or '자동발송')}</div>"
                f"<div class='tray-popup-subject'>{escape(template.subject or '(제목 없음)')}</div>"
                f"<div class='tray-popup-meta'>수신 {recipients}명 / 반복 {escape(repeat_label)}</div>"
                f"<div class='tray-popup-meta'>첫 발송 {_format_datetime(template.first_send_at)}</div>"
                "</div>"
                f"<div class='tray-popup-chip'>{escape(_format_datetime(next_run))}</div>"
                "</div>"
                "</div>"
            ),
            unsafe_allow_html=True,
        )


def _scheduler_status_label(scheduler_manager: Any) -> str:
    if not scheduler_manager.started:
        return "중지"
    if scheduler_manager.scheduler.state == STATE_PAUSED:
        return "일시중지"
    if scheduler_manager.scheduler.state == STATE_RUNNING:
        return "실행 중"
    return "대기"


def _repeat_label(repeat_type: str) -> str:
    return {
        "none": "1회",
        "daily": "매일",
        "weekly": "매주",
        "monthly": "매월",
    }.get(repeat_type, repeat_type or "설정 없음")


def _format_datetime(value: datetime | str | None, *, include_time: bool | None = None) -> str:
    parsed = value if isinstance(value, datetime) else _parse_datetime(value)
    if parsed is None:
        return str(value or "-")

    now = datetime.now()
    today = now.date()
    target_date = parsed.date()
    show_time = include_time if include_time is not None else any((parsed.hour, parsed.minute, parsed.second))
    weekday = "월화수목금토일"[parsed.weekday()]

    if target_date == today:
        return f"오늘 {parsed:%H:%M}" if show_time else "오늘"
    if (target_date - today).days == 1:
        return f"내일 {parsed:%H:%M}" if show_time else "내일"
    if (target_date - today).days == -1:
        return f"어제 {parsed:%H:%M}" if show_time else "어제"

    if parsed.year == now.year:
        base = f"{parsed.month}월 {parsed.day}일 ({weekday})"
    else:
        base = f"{parsed.year}년 {parsed.month}월 {parsed.day}일 ({weekday})"
    return f"{base} {parsed:%H:%M}" if show_time else base


def _due_chip(value: str | None) -> tuple[str, str]:
    due_value = _parse_datetime(value)
    if due_value is None:
        return ("기한 없음", "is-muted")

    today = datetime.now().date()
    if due_value.date() < today:
        return (f"지연 {_format_datetime(due_value)}", "is-danger")
    if due_value.date() == today:
        return (f"오늘 {due_value:%H:%M}", "is-warn")
    return (_format_datetime(due_value), "")


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
