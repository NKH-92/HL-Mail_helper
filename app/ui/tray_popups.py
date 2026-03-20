"""Read-only popup views opened from the desktop tray."""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any

from apscheduler.schedulers.base import STATE_PAUSED, STATE_RUNNING
import streamlit as st

from app.ui.ui_state_helpers import build_classified_mail_dicts, build_dashboard_mail_category_counts


POPUP_TODO_VIEW = "todos"
POPUP_AUTO_SEND_VIEW = "autosend"
SUPPORTED_POPUP_VIEWS = {POPUP_TODO_VIEW, POPUP_AUTO_SEND_VIEW}

_POPUP_CATEGORY_LABELS = {
    1: "내가해야할일",
    2: "내가검토할일",
    3: "단순 참고용",
}
_POPUP_CATEGORY_CHIP_CLASSES = {
    1: "is-danger",
    2: "is-warn",
    3: "is-muted",
}
_POPUP_ACTION_LABELS = {
    "REPLY": "회신",
    "REVIEW": "검토",
    "APPROVE": "승인",
    "SUBMIT": "제출",
    "MODIFY": "수정",
    "SCHEDULE": "일정",
    "FOLLOW_UP": "후속",
    "DECIDE": "결정",
}


def popup_page_title(popup_mode: str | None) -> str:
    if popup_mode == POPUP_TODO_VIEW:
        return "MailAI | 메일 분류"
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
                --popup-card: linear-gradient(180deg, rgba(255, 250, 243, 0.99), rgba(247, 240, 232, 0.98));
                --popup-line: #ddd1c2;
                --popup-line-strong: #ccbdaa;
                --popup-text: #2a1f15;
                --popup-muted: #6b5a4c;
                --popup-primary: #cc7d5e;
                --popup-primary-soft: rgba(204, 125, 94, 0.12);
                --popup-danger-bg: #fff1ec;
                --popup-danger-text: #b94f28;
                --popup-warn-bg: #fdf4ea;
                --popup-warn-text: #a5622b;
            }

            html, body, [data-testid="stAppViewContainer"], .stApp {
                background: var(--popup-bg) !important;
            }

            [data-testid="stAppViewContainer"] > .main {
                padding-top: 0 !important;
            }

            .block-container {
                max-width: 100% !important;
                padding: 1rem 1rem 1.05rem !important;
            }

            .tray-popup-header {
                margin-bottom: 0.8rem;
                padding: 1rem 1.05rem;
                border-radius: 20px;
                border: 1px solid var(--popup-line);
                background:
                    radial-gradient(circle at top right, rgba(204, 125, 94, 0.12), rgba(204, 125, 94, 0) 28%),
                    linear-gradient(180deg, rgba(255, 255, 255, 0.99), rgba(249, 243, 238, 0.98));
                box-shadow: 0 14px 30px rgba(42, 28, 15, 0.06);
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
                line-height: 1.6;
                font-size: 0.83rem;
            }

            [data-testid="stHorizontalBlock"] {
                gap: 0.55rem !important;
            }

            [data-testid="stMetric"] {
                border: 1px solid var(--popup-line);
                border-radius: 18px;
                background: var(--popup-card);
                box-shadow: none !important;
                padding: 0.75rem 0.82rem;
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
                border-radius: 14px;
                border: 1px solid rgba(204, 125, 94, 0.18);
                background: linear-gradient(180deg, #ffffff, #f9f1eb);
                color: var(--popup-primary);
                font-size: 0.79rem;
                font-weight: 800;
                padding: 0.5rem 0.78rem;
                box-shadow: none !important;
            }

            .stButton > button:hover {
                border-color: rgba(204, 125, 94, 0.28);
                background: linear-gradient(180deg, #fffdfa, #f4e8e0);
            }

            .tray-popup-card {
                padding: 0.9rem 0.94rem;
                border-radius: 18px;
                border: 1px solid var(--popup-line);
                background: var(--popup-card);
                box-shadow: none;
                position: relative;
                overflow: hidden;
            }

            .tray-popup-card::before {
                content: "";
                position: absolute;
                inset: 0 auto 0 0;
                width: 4px;
                background: transparent;
            }

            .tray-popup-card.is-category-1::before {
                background: var(--popup-primary);
            }

            .tray-popup-card.is-category-2::before {
                background: var(--popup-warn-text);
            }

            .tray-popup-card.is-category-3::before {
                background: #6f7c86;
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
                font-size: 0.92rem;
                font-weight: 800;
                color: var(--popup-text);
                line-height: 1.42;
                margin-bottom: 0.16rem;
            }

            .tray-popup-subject {
                color: #3c2f24;
                line-height: 1.58;
                font-size: 0.8rem;
                margin-bottom: 0.3rem;
            }

            .tray-popup-meta {
                color: var(--popup-muted);
                font-size: 0.75rem;
                line-height: 1.52;
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


def _load_popup_classified_mails(
    context: Any,
    *,
    visible_limit: int = 12,
    count_limit: int = 200,
) -> tuple[list[dict[str, object]], dict[str, int]]:
    mails = context.mail_repository.list_classified_mails(limit=count_limit)
    counts = build_dashboard_mail_category_counts(mails)
    return (
        build_classified_mail_dicts(
            mails[:visible_limit],
            address_book_service=context.address_book_service,
        ),
        counts,
    )


def _popup_category_label(final_category: object) -> str:
    try:
        category = int(final_category)
    except (TypeError, ValueError):
        category = 3
    return _POPUP_CATEGORY_LABELS.get(category, _POPUP_CATEGORY_LABELS[3])


def _popup_category_chip_class(final_category: object) -> str:
    try:
        category = int(final_category)
    except (TypeError, ValueError):
        category = 3
    return _POPUP_CATEGORY_CHIP_CLASSES.get(category, _POPUP_CATEGORY_CHIP_CLASSES[3])


def _popup_category_card_class(final_category: object) -> str:
    try:
        category = int(final_category)
    except (TypeError, ValueError):
        category = 3
    return f"is-category-{category if category in {1, 2, 3} else 3}"


def _popup_action_text(action_types: object) -> str:
    if not isinstance(action_types, (list, tuple)):
        return ""
    labels: list[str] = []
    for raw_value in action_types:
        normalized = str(raw_value or "").strip().upper()
        if not normalized or normalized == "NONE":
            continue
        labels.append(_POPUP_ACTION_LABELS.get(normalized, normalized))
    return ", ".join(labels)


def _render_todo_popup(context: Any) -> None:
    classified_mails, counts = _load_popup_classified_mails(context)
    now = datetime.now()

    _render_header(
        "Tray",
        "메일 분류",
        "현재 수신 메일을 내가해야할일, 내가검토할일, 단순 참고용으로 빠르게 확인합니다.",
    )

    top_cols = st.columns([1.0, 1.0, 1.0, 0.9], gap="small")
    top_cols[0].metric("내가해야할일", counts.get("category_1", 0))
    top_cols[1].metric("내가검토할일", counts.get("category_2", 0))
    top_cols[2].metric("단순 참고용", counts.get("category_3", 0))
    with top_cols[3]:
        st.caption(f"마지막 갱신 {_format_datetime(now)}")
        if st.button("새로고침", use_container_width=True, key="todo-popup-refresh"):
            st.rerun()

    if not classified_mails:
        st.info("표시할 메일이 없습니다.")
        return

    for mail in classified_mails:
        final_category = int(mail.get("final_category") or 3)
        summary = str(mail.get("summary") or mail.get("subject") or "메일")
        subject = str(mail.get("subject") or "(제목 없음)")
        sender = str(mail.get("sender") or "-")
        received_at = str(mail.get("received_at") or "-")
        due_date = str(mail.get("due_date") or "").strip()
        action_text = _popup_action_text(mail.get("action_types"))
        detail_bits = [f"분류 {_popup_category_label(final_category)}"]
        if action_text:
            detail_bits.append(f"액션 {action_text}")
        if due_date:
            detail_bits.append(f"기한 {due_date}")
        if bool(mail.get("correction_applied")):
            detail_bits.append("정책 보정")
        chip_class = _popup_category_chip_class(final_category)
        card_class = _popup_category_card_class(final_category)
        chip_markup_class = f"tray-popup-chip {chip_class}".strip()
        st.markdown(
            (
                f"<div class='tray-popup-card {card_class}'>"
                "<div class='tray-popup-row'>"
                "<div>"
                f"<div class='tray-popup-name'>{escape(summary)}</div>"
                f"<div class='tray-popup-subject'>{escape(subject)}</div>"
                f"<div class='tray-popup-meta'>보낸 사람 {escape(sender)} · 수신 {escape(received_at)}</div>"
                f"<div class='tray-popup-meta'>{escape(' · '.join(detail_bits))}</div>"
                "</div>"
                f"<div class='{chip_markup_class}'>{escape(_popup_category_label(final_category))}</div>"
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


def _load_popup_classified_mails(
    context: Any,
    *,
    visible_limit: int | None = None,
    count_limit: int = 200,
) -> tuple[list[dict[str, object]], dict[str, int]]:
    mails = context.mail_repository.list_classified_mails(limit=count_limit)
    counts = build_dashboard_mail_category_counts(mails)
    if visible_limit is not None:
        mails = mails[: max(0, int(visible_limit))]
    return (
        build_classified_mail_dicts(
            mails,
            address_book_service=context.address_book_service,
        ),
        counts,
    )


def _popup_category_tab_key(final_category: object) -> str:
    try:
        category = int(final_category)
    except (TypeError, ValueError):
        category = 3
    normalized_category = category if category in {1, 2, 3} else 3
    return f"category_{normalized_category}"


def _render_popup_mail_card(mail: dict[str, object]) -> None:
    final_category = int(mail.get("final_category") or 3)
    summary = str(mail.get("summary") or mail.get("subject") or "메일")
    subject = str(mail.get("subject") or "(제목 없음)")
    sender = str(mail.get("sender") or "-")
    received_at = str(mail.get("received_at") or "-")
    due_date = str(mail.get("due_date") or "").strip()
    action_text = _popup_action_text(mail.get("action_types"))
    detail_bits = [f"분류 {_popup_category_label(final_category)}"]
    if action_text:
        detail_bits.append(f"액션 {action_text}")
    if due_date:
        detail_bits.append(f"기한 {due_date}")
    if bool(mail.get("correction_applied")):
        detail_bits.append("정책 보정")
    chip_class = _popup_category_chip_class(final_category)
    card_class = _popup_category_card_class(final_category)
    chip_markup_class = f"tray-popup-chip {chip_class}".strip()
    st.markdown(
        (
            f"<div class='tray-popup-card {card_class}'>"
            "<div class='tray-popup-row'>"
            "<div>"
            f"<div class='tray-popup-name'>{escape(summary)}</div>"
            f"<div class='tray-popup-subject'>{escape(subject)}</div>"
            f"<div class='tray-popup-meta'>보낸 사람 {escape(sender)} · 수신 {escape(received_at)}</div>"
            f"<div class='tray-popup-meta'>{escape(' · '.join(detail_bits))}</div>"
            "</div>"
            f"<div class='{chip_markup_class}'>{escape(_popup_category_label(final_category))}</div>"
            "</div>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def _render_todo_popup(context: Any) -> None:
    classified_mails, counts = _load_popup_classified_mails(context)
    now = datetime.now()

    _render_header(
        "Tray",
        "메일 분류",
        "현재 수신 메일을 내가해야할일, 내가검토할일, 단순 참고용으로 빠르게 확인할 수 있습니다.",
    )

    top_cols = st.columns([1.0, 1.0, 1.0, 0.9], gap="small")
    top_cols[0].metric("내가해야할일", counts.get("category_1", 0))
    top_cols[1].metric("내가검토할일", counts.get("category_2", 0))
    top_cols[2].metric("단순 참고용", counts.get("category_3", 0))
    with top_cols[3]:
        st.caption(f"마지막 갱신 {_format_datetime(now)}")
        if st.button("새로고침", use_container_width=True, key="todo-popup-refresh"):
            st.rerun()

    mails_by_tab = {
        "category_1": [],
        "category_2": [],
        "category_3": [],
    }
    for mail in classified_mails:
        mails_by_tab[_popup_category_tab_key(mail.get("final_category"))].append(mail)

    tab_order = (
        ("category_1", _POPUP_CATEGORY_LABELS[1]),
        ("category_2", _POPUP_CATEGORY_LABELS[2]),
        ("category_3", _POPUP_CATEGORY_LABELS[3]),
    )
    tabs = st.tabs([f"{label} ({counts.get(tab_key, 0)})" for tab_key, label in tab_order])
    for tab, (tab_key, _label) in zip(tabs, tab_order, strict=False):
        with tab:
            tab_mails = mails_by_tab[tab_key]
            if not tab_mails:
                st.info("표시할 메일이 없습니다.")
                continue
            for mail in tab_mails:
                _render_popup_mail_card(mail)
