"""Modern unified UI shell using Streamlit Custom Component."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock, Thread
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from app.core.config_manager import AppConfig
from app.core.security import GEMINI_API_KEY, HANLIM_API_KEY, MAIL_PASSWORD_KEY
from app.ui.page_config import (
    ARCHIVE_PAGE,
    AUTO_SEND_PAGE,
    COMPLETED_PAGE,
    DASHBOARD_PAGE,
    resolve_page_id as _shared_resolve_page_id,
)
from app.ui.ui_state_helpers import (
    DASHBOARD_THREAD_PAGE_SIZE as _DASHBOARD_THREAD_PAGE_SIZE,
    build_classified_mail_dicts as _build_classified_mail_dicts,
    build_dashboard_mail_page_context as _build_dashboard_mail_page_context,
    build_follow_up_mail_template as _build_follow_up_mail_template,
    build_dashboard_mail_category_counts as _build_dashboard_mail_category_counts,
    build_dashboard_thread_page_state as _build_dashboard_thread_page_state,
    build_completed_task_dicts as _build_completed_task_dicts,
    build_dashboard_task_dicts as _build_dashboard_task_dicts,
    build_mail_template_from_payload as _build_mail_template_from_payload,
    build_mailbox_test_submission as _build_mailbox_test_submission,
    build_priority_thread_dicts as _build_priority_thread_dicts,
    build_send_registration_from_payload as _build_send_registration_from_payload,
    build_settings_submission as _build_settings_submission,
    build_sync_status_dict as _build_sync_status_dict,
    format_mailbox_cycle_message as _format_mailbox_cycle_message,
    normalize_dashboard_mail_tab as _normalize_dashboard_mail_tab,
    normalize_dashboard_mail_view as _normalize_dashboard_mail_view,
    normalize_dashboard_thread_filter as _normalize_dashboard_thread_filter,
    normalize_dashboard_thread_page as _normalize_dashboard_thread_page,
    read_log_tail as _read_log_tail,
    resolve_dashboard_mail_tab_for_counts as _resolve_dashboard_mail_tab_for_counts,
    resolve_dashboard_mail_tab_key as _resolve_dashboard_mail_tab_key,
    validate_send_template as _validate_send_template,
)

_COMPONENT_DIR = Path(__file__).resolve().parent / "custom_board"

_modern_dashboard = components.declare_component(
    "modern_dashboard",
    path=str(_COMPONENT_DIR),
)

_AUTO_SEND_MAIL_TEMPLATE_SELECTION_KEY = "autosend_selected_mail_template_id"
_AUTO_SEND_REGISTRATION_SELECTION_KEY = "autosend_selected_send_registration_id"
_MODERN_UI_LAST_ACTION_ID_KEY = "modern_ui_last_client_action_id"
_MODERN_UI_CLIENT_STATE_VERSION_KEY = "modern_ui_client_state_version"
_MODERN_UI_FLASH_KEY = "modern_ui_flash"
_DASHBOARD_MAIL_TAB_KEY = "dashboard_mail_tab"
_DASHBOARD_MAIL_VIEW_KEY = "dashboard_mail_view"
_DASHBOARD_SELECTED_MAIL_ID_KEY = "dashboard_selected_mail_id"
_ADDRESS_BOOK_PAGE_IDS = {"autosend"}
_DASHBOARD_PAGE_IDS = {"dashboard", "archive", "completed"}


@dataclass(slots=True)
class _StreamlitSyncProgress:
    running: bool = False
    stage: str = "idle"
    message: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    updated_at: str | None = None
    scanned_count: int = 0
    fetched_count: int = 0
    saved_count: int = 0
    analysis_total: int = 0
    analysis_completed: int = 0
    analysis_success_count: int = 0
    analysis_failure_count: int = 0
    error: str | None = None


_STREAMLIT_SYNC_LOCK = RLock()
_STREAMLIT_SYNC_PROGRESS = _StreamlitSyncProgress()
_STREAMLIT_SYNC_THREAD: Thread | None = None
_STREAMLIT_SYNC_FLASH_MESSAGE: str | None = None


def _resolve_page_id(current_page: str) -> str:
    return _shared_resolve_page_id(current_page)


def _normalize_positive_int(value: object) -> int:
    try:
        normalized = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return normalized if normalized > 0 else 0


def _pop_autosend_selection(key: str) -> int:
    return _normalize_positive_int(st.session_state.pop(key, 0))


def _set_autosend_selection(*, mail_template_id: int = 0, send_registration_id: int = 0) -> None:
    st.session_state[_AUTO_SEND_MAIL_TEMPLATE_SELECTION_KEY] = _normalize_positive_int(mail_template_id)
    st.session_state[_AUTO_SEND_REGISTRATION_SELECTION_KEY] = _normalize_positive_int(send_registration_id)


def _consume_component_action_id(state: dict[str, object], result: object) -> bool:
    """Return True when the component action was already processed."""

    if not isinstance(result, dict):
        return False

    action_id = str(result.get("client_action_id") or "").strip()
    if not action_id:
        return False

    if state.get(_MODERN_UI_LAST_ACTION_ID_KEY) == action_id:
        return True

    state[_MODERN_UI_LAST_ACTION_ID_KEY] = action_id
    return False


def _current_timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _serialize_streamlit_sync_progress() -> dict[str, object]:
    with _STREAMLIT_SYNC_LOCK:
        return asdict(_STREAMLIT_SYNC_PROGRESS)


def _consume_streamlit_sync_flash_message() -> str | None:
    global _STREAMLIT_SYNC_FLASH_MESSAGE

    with _STREAMLIT_SYNC_LOCK:
        message = _STREAMLIT_SYNC_FLASH_MESSAGE
        _STREAMLIT_SYNC_FLASH_MESSAGE = None
        return message


def _update_streamlit_sync_progress(event: dict[str, object] | None) -> None:
    if not isinstance(event, dict):
        return

    with _STREAMLIT_SYNC_LOCK:
        now = _current_timestamp()
        for key in (
            "running",
            "stage",
            "message",
            "started_at",
            "finished_at",
            "scanned_count",
            "fetched_count",
            "saved_count",
            "analysis_total",
            "analysis_completed",
            "analysis_success_count",
            "analysis_failure_count",
            "error",
        ):
            if key in event:
                setattr(_STREAMLIT_SYNC_PROGRESS, key, event[key])
        _STREAMLIT_SYNC_PROGRESS.updated_at = now


def _run_streamlit_sync_job(context: Any) -> None:
    global _STREAMLIT_SYNC_FLASH_MESSAGE, _STREAMLIT_SYNC_THREAD

    try:
        result = context.scheduler_manager.run_mailbox_cycle(
            include_failed_analysis=True,
            source="manual",
            progress_callback=_update_streamlit_sync_progress,
        )
    except Exception as exc:  # noqa: BLE001
        with _STREAMLIT_SYNC_LOCK:
            now = _current_timestamp()
            error_message = str(exc) or "메일 동기화에 실패했습니다."
            _STREAMLIT_SYNC_PROGRESS.running = False
            _STREAMLIT_SYNC_PROGRESS.stage = "error"
            _STREAMLIT_SYNC_PROGRESS.message = error_message
            _STREAMLIT_SYNC_PROGRESS.error = error_message
            _STREAMLIT_SYNC_PROGRESS.finished_at = now
            _STREAMLIT_SYNC_PROGRESS.updated_at = now
            _STREAMLIT_SYNC_THREAD = None
            _STREAMLIT_SYNC_FLASH_MESSAGE = error_message
        return

    with _STREAMLIT_SYNC_LOCK:
        now = _current_timestamp()
        _STREAMLIT_SYNC_PROGRESS.running = False
        _STREAMLIT_SYNC_PROGRESS.stage = "complete"
        _STREAMLIT_SYNC_PROGRESS.message = _format_mailbox_cycle_message(result)
        _STREAMLIT_SYNC_PROGRESS.finished_at = now
        _STREAMLIT_SYNC_PROGRESS.updated_at = now
        _STREAMLIT_SYNC_PROGRESS.error = None
        _STREAMLIT_SYNC_PROGRESS.scanned_count = int(getattr(result, "scanned_count", 0) or 0)
        _STREAMLIT_SYNC_PROGRESS.fetched_count = int(getattr(result, "fetched_count", 0) or 0)
        _STREAMLIT_SYNC_PROGRESS.saved_count = int(getattr(result, "saved_count", 0) or 0)
        _STREAMLIT_SYNC_PROGRESS.analysis_success_count = int(getattr(result, "analysis_success_count", 0) or 0)
        _STREAMLIT_SYNC_PROGRESS.analysis_failure_count = int(getattr(result, "analysis_failure_count", 0) or 0)
        _STREAMLIT_SYNC_PROGRESS.analysis_total = (
            _STREAMLIT_SYNC_PROGRESS.analysis_success_count + _STREAMLIT_SYNC_PROGRESS.analysis_failure_count
        )
        _STREAMLIT_SYNC_PROGRESS.analysis_completed = _STREAMLIT_SYNC_PROGRESS.analysis_total
        _STREAMLIT_SYNC_THREAD = None
        _STREAMLIT_SYNC_FLASH_MESSAGE = _STREAMLIT_SYNC_PROGRESS.message


def _start_streamlit_sync(context: Any) -> bool:
    global _STREAMLIT_SYNC_PROGRESS, _STREAMLIT_SYNC_THREAD

    with _STREAMLIT_SYNC_LOCK:
        if _STREAMLIT_SYNC_PROGRESS.running and _STREAMLIT_SYNC_THREAD and _STREAMLIT_SYNC_THREAD.is_alive():
            return False

        now = _current_timestamp()
        _STREAMLIT_SYNC_PROGRESS = _StreamlitSyncProgress(
            running=True,
            stage="syncing",
            message="메일 수집 중",
            started_at=now,
            finished_at=None,
            updated_at=now,
        )
        _STREAMLIT_SYNC_THREAD = Thread(
            target=_run_streamlit_sync_job,
            args=(context,),
            name="mailai-streamlit-sync",
            daemon=True,
        )
        _STREAMLIT_SYNC_THREAD.start()
        return True


def _sync_dashboard_client_state(result: object) -> None:
    if not isinstance(result, dict):
        return

    try:
        client_state_version = int(result.get("client_state_version") or 0)
    except (TypeError, ValueError):
        client_state_version = 0
    last_seen_version = int(st.session_state.get(_MODERN_UI_CLIENT_STATE_VERSION_KEY, 0) or 0)
    if client_state_version < last_seen_version:
        return
    st.session_state[_MODERN_UI_CLIENT_STATE_VERSION_KEY] = client_state_version

    client_state = result.get("client_state") if isinstance(result.get("client_state"), dict) else {}
    st.session_state[_DASHBOARD_MAIL_TAB_KEY] = _normalize_dashboard_mail_tab(
        client_state.get("dashboard_mail_tab"),
    )
    st.session_state[_DASHBOARD_MAIL_VIEW_KEY] = _normalize_dashboard_mail_view(
        client_state.get("dashboard_mail_view"),
    )
    st.session_state[_DASHBOARD_SELECTED_MAIL_ID_KEY] = _normalize_positive_int(
        client_state.get("selected_mail_id"),
    )


def render_modern_dashboard(context: object, current_page: str) -> None:
    """Render the dashboard using the Tailwind-based HTML custom component for all pages."""

    st.markdown(
        """
        <style>
            html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stAppViewContainer"] > .main, [data-testid="stMain"] {
                height: 100vh !important;
                height: 100dvh !important;
                overflow: hidden !important;
            }
            section[data-testid="stSidebar"] { display: none !important; }
            [data-testid="stAppViewContainer"] > .main { padding: 0 !important; }
            [data-testid="stMainBlockContainer"], .block-container { padding: 0 !important; max-width: 100% !important; background-color: transparent !important; }
            div[data-testid="stVerticalBlock"] { gap: 0 !important; }
            header[data-testid="stHeader"] { display: none !important; }
            iframe { border: none !important; display: block !important; height: 100vh !important; height: 100dvh !important; width: 100% !important; margin: 0 !important; overflow: hidden !important; }
            .stApp > header { display: none; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    page_id = _resolve_page_id(current_page)
    config: AppConfig = context.address_book_service.merge_config_profile(context.config_manager.load())
    user_info = {
        "email": config.user_email or "admin@mailos.local",
        "name": config.user_display_name or "User",
    }

    component_args = {
        "page": current_page,
        "page_id": page_id,
        "client_state_version": int(st.session_state.get(_MODERN_UI_CLIENT_STATE_VERSION_KEY, 0) or 0),
        "sync_progress": _serialize_streamlit_sync_progress(),
        "user": user_info,
        "address_book_contacts": (
            context.address_book_service.list_contact_options()
            if page_id in _ADDRESS_BOOK_PAGE_IDS
            else []
        ),
    }

    if page_id in _DASHBOARD_PAGE_IDS:
        dashboard_page_context = _build_dashboard_mail_page_context(page_id)
        component_args["sync_status"] = _build_sync_status_dict(
            config,
            sync_snapshot=context.sync_service.get_status_snapshot(),
            scheduler_status=context.scheduler_manager.get_mailbox_status(),
            analysis_warning=context.mailbox_service.get_analysis_warning(),
            backlog_counts=context.mail_repository.count_analysis_backlog(),
        )
        classified_mails = context.mail_repository.list_dashboard_mails(
            str(dashboard_page_context.get("bucket_key") or "classified"),
            limit=200,
        )
        category_counts = _build_dashboard_mail_category_counts(classified_mails)
        visible_mail_ids = [int(mail.id) for mail in classified_mails]
        selected_mail_id = _normalize_positive_int(st.session_state.get(_DASHBOARD_SELECTED_MAIL_ID_KEY, 0))
        if selected_mail_id not in visible_mail_ids:
            selected_mail_id = visible_mail_ids[0] if visible_mail_ids else 0
        st.session_state[_DASHBOARD_SELECTED_MAIL_ID_KEY] = selected_mail_id
        dashboard_mail_tab = _resolve_dashboard_mail_tab_for_counts(
            st.session_state.get(_DASHBOARD_MAIL_TAB_KEY, "category_1"),
            category_counts,
        )
        dashboard_mail_view = _normalize_dashboard_mail_view(
            st.session_state.get(_DASHBOARD_MAIL_VIEW_KEY, "list"),
        )
        st.session_state[_DASHBOARD_MAIL_TAB_KEY] = dashboard_mail_tab
        st.session_state[_DASHBOARD_MAIL_VIEW_KEY] = dashboard_mail_view

        component_args["classified_mails"] = _build_classified_mail_dicts(
            classified_mails,
            address_book_service=context.address_book_service,
        )
        component_args["dashboard_section"] = dashboard_page_context
        component_args["dashboard_mail_category_counts"] = category_counts
        component_args["dashboard_mail_tab"] = dashboard_mail_tab
        component_args["dashboard_mail_view"] = dashboard_mail_view
        component_args["selected_mail_id"] = selected_mail_id
        component_args["flash_msg"] = (
            st.session_state.pop(_MODERN_UI_FLASH_KEY, None) or _consume_streamlit_sync_flash_message()
        )

    elif page_id == "autosend":
        mail_templates = context.mail_template_service.list_templates()
        registrations = context.template_service.list_templates()

        mail_template_dicts = []
        for template in mail_templates:
            recipients = len(template.to_list) + len(template.cc_list)
            mail_template_dicts.append(
                {
                    "id": template.id,
                    "name": template.template_name,
                    "subject": template.subject,
                    "recipients": recipients,
                    "repeat_type": template.repeat_type,
                    "body": template.body,
                    "to_raw": ", ".join(template.to_list),
                    "cc_raw": ", ".join(template.cc_list),
                    "attachment_raw": ", ".join(template.attachment_paths),
                    "first_send_at": template.first_send_at,
                    "send_time": template.send_time,
                    "updated_at": template.updated_at or template.created_at or "",
                }
            )

        registration_dicts = []
        for registration in registrations:
            next_run = context.send_service.calculate_next_run(registration)
            next_run_str = next_run.strftime("%Y-%m-%d %H:%M") if next_run else "?덉빟 ?놁쓬"
            recipients = len(registration.to_list) + len(registration.cc_list)
            registration_dicts.append(
                {
                    "id": registration.id,
                    "name": registration.template_name,
                    "subject": registration.subject,
                    "enabled": registration.enabled,
                    "next_run": next_run_str,
                    "recipients": recipients,
                    "repeat_type": registration.repeat_type,
                    "body": registration.body,
                    "to_raw": ", ".join(registration.to_list),
                    "cc_raw": ", ".join(registration.cc_list),
                    "attachment_raw": ", ".join(registration.attachment_paths),
                    "first_send_at": registration.first_send_at,
                    "send_time": registration.send_time,
                }
            )

        component_args["mail_templates"] = mail_template_dicts
        component_args["send_registrations"] = registration_dicts
        component_args["scheduler_started"] = context.scheduler_manager.started
        component_args["scheduler_state"] = (
            str(context.scheduler_manager.scheduler.state) if context.scheduler_manager.started else "stopped"
        )
        component_args["selected_mail_template_id"] = _pop_autosend_selection(
            _AUTO_SEND_MAIL_TEMPLATE_SELECTION_KEY
        )
        component_args["selected_send_registration_id"] = _pop_autosend_selection(
            _AUTO_SEND_REGISTRATION_SELECTION_KEY
        )
        component_args["flash_msg"] = (
            st.session_state.pop("templates_page_flash", None) or _consume_streamlit_sync_flash_message()
        )

    elif page_id == "logs":
        send_logs = context.send_log_repository.list_recent()
        app_log_content = _read_log_tail(context.logger_path)
        log_dicts = []
        for log in send_logs:
            log_dicts.append(
                {
                    "id": log.id,
                    "subject": log.subject or "(?쒕ぉ ?놁쓬)",
                    "recipients": len(log.recipients) if log.recipients else 0,
                    "recipients_list": ", ".join(log.recipients) if log.recipients else "",
                    "result": (log.result or "").lower(),
                    "sent_at": log.sent_at,
                    "error": log.error_message or "",
                }
            )
        component_args["send_logs"] = log_dicts
        component_args["app_logs"] = app_log_content

    elif page_id == "settings":
        stored_pwd = context.secret_store.has_secret(MAIL_PASSWORD_KEY)
        stored_api = context.secret_store.has_secret(GEMINI_API_KEY)
        stored_hanlim_api = context.secret_store.has_secret(HANLIM_API_KEY)
        component_args["config"] = {
            "user_email": config.user_email,
            "user_display_name": config.user_display_name,
            "user_department": config.user_department,
            "user_job_title": config.user_job_title,
            "mailbox": config.mailbox or "INBOX",
            "sync_days": config.sync_days,
            "sync_batch_size": config.sync_batch_size,
            "sync_scan_limit": config.sync_scan_limit,
            "sync_interval_minutes": config.sync_interval_minutes,
            "store_raw_body": config.store_raw_body,
            "preview_max_chars": config.preview_max_chars,
            "ai_provider": config.ai_provider,
            "ai_base_url": config.ai_base_url,
            "gemini_model": config.gemini_model,
            "gemini_timeout_seconds": config.gemini_timeout_seconds,
        }
        component_args["secrets"] = {
            "password": stored_pwd,
            "api_key": stored_api,
            "hanlim_api_key": stored_hanlim_api,
        }
        component_args["mailboxes"] = st.session_state.get("mailbox_candidates", [])
        component_args["flash_msg"] = (
            st.session_state.pop("settings_flash", None) or _consume_streamlit_sync_flash_message()
        )

    result = _modern_dashboard(**component_args, key="modern_ui_comp")

    if not result:
        return
    if _consume_component_action_id(st.session_state, result):
        return
    _sync_dashboard_client_state(result)

    action = result.get("action")
    payload = result.get("payload", {})

    if action == "navigate":
        st.session_state["current_page"] = result.get("page")
        st.rerun()
        return

    if action == "sync_mail":
        if _start_streamlit_sync(context):
            st.session_state[_MODERN_UI_FLASH_KEY] = "동기화를 시작했습니다."
        else:
            st.session_state[_MODERN_UI_FLASH_KEY] = "이미 동기화가 진행 중입니다."
        st.rerun()
        return

    if action == "refresh_dashboard":
        st.rerun()
        return

    if action == "select_mail":
        st.rerun()
        return

    if action == "archive_mail":
        mail_id = _normalize_positive_int(payload.get("mail_id"))
        if mail_id:
            moved_mail = context.mail_repository.move_mail_retention_bucket(mail_id, "archived")
            st.session_state[_MODERN_UI_FLASH_KEY] = (
                "메일을 보관함으로 이동했습니다."
                if moved_mail is not None
                else "메일 이동에 실패했습니다. 다시 시도해 주세요."
            )
        st.rerun()
        return

    if action == "complete_mail":
        mail_id = _normalize_positive_int(payload.get("mail_id"))
        if mail_id:
            mail = context.mail_repository.move_mail_retention_bucket(mail_id, "completed")
            if mail is not None and st.session_state.get("current_page") == ARCHIVE_PAGE:
                st.session_state["current_page"] = COMPLETED_PAGE
                st.session_state[_DASHBOARD_MAIL_TAB_KEY] = _resolve_dashboard_mail_tab_key(mail)
                st.session_state[_DASHBOARD_SELECTED_MAIL_ID_KEY] = mail_id
                st.session_state[_DASHBOARD_MAIL_VIEW_KEY] = "detail"
            st.session_state[_MODERN_UI_FLASH_KEY] = (
                "메일을 완료 목록으로 이동했습니다."
                if mail is not None
                else "메일 이동에 실패했습니다. 다시 시도해 주세요."
            )
        st.rerun()
        return

    if action == "restore_mail":
        mail_id = _normalize_positive_int(payload.get("mail_id"))
        if mail_id:
            mail = context.mail_repository.move_mail_retention_bucket(mail_id, "classified")
            if mail is not None:
                st.session_state["current_page"] = DASHBOARD_PAGE
                st.session_state[_DASHBOARD_MAIL_TAB_KEY] = _resolve_dashboard_mail_tab_key(mail)
                st.session_state[_DASHBOARD_SELECTED_MAIL_ID_KEY] = mail_id
                st.session_state[_DASHBOARD_MAIL_VIEW_KEY] = "detail"
            st.session_state[_MODERN_UI_FLASH_KEY] = (
                "메일을 분류 목록으로 복구했습니다."
                if mail is not None
                else "메일 복구에 실패했습니다. 다시 시도해 주세요."
            )
        st.rerun()
        return

    if action == "save_settings":
        next_config, pwd, api_key, hanlim_api_key = _build_settings_submission(
            context.config_manager.load(),
            payload,
        )
        next_config = context.address_book_service.merge_config_profile(next_config)

        context.config_manager.save(next_config)
        deleted_old_count = context.sync_service.prune_local_mail_retention(days=next_config.sync_days)
        context.scheduler_manager.refresh_jobs()
        if pwd:
            context.secret_store.set_secret(MAIL_PASSWORD_KEY, pwd)
        if api_key:
            context.secret_store.set_secret(GEMINI_API_KEY, api_key)
        if hanlim_api_key:
            context.secret_store.set_secret(HANLIM_API_KEY, hanlim_api_key)

        flash_message = "?ㅼ젙????ν뻽?듬땲??"
        if deleted_old_count:
            flash_message += f" 蹂댁〈湲곌컙??踰쀬뼱??硫붿씪 {deleted_old_count}嫄댁쓣 ?뺣━?덉뒿?덈떎."
        st.session_state["settings_flash"] = flash_message
        st.rerun()
        return

    if action == "mailbox_test":
        try:
            pending_config, password_override = _build_mailbox_test_submission(context.config_manager.load(), payload)
            pending_config = context.address_book_service.merge_config_profile(pending_config)
            mailboxes = context.imap_client.list_mailboxes(pending_config, password_override=password_override)
            st.session_state["mailbox_candidates"] = mailboxes
            st.session_state["settings_flash"] = f"硫붿씪??{len(mailboxes)}媛쒕? 媛?몄솕?듬땲??"
        except Exception as exc:  # noqa: BLE001
            st.session_state["settings_flash"] = f"?ㅻ쪟: {exc}"
        st.rerun()
        return

    if action == "apply_mailbox":
        box = payload.get("mailbox")
        current_config = context.config_manager.load()
        current_config.mailbox = box
        context.config_manager.save(current_config)
        st.session_state["settings_flash"] = f"硫붿씪?⑥쓣 {box}濡??곸슜?덉뒿?덈떎."
        st.rerun()
        return

    if action == "clear_secret":
        secret_type = payload.get("type")
        if secret_type == "password":
            context.secret_store.delete_secret(MAIL_PASSWORD_KEY)
        elif secret_type == "api_key":
            context.secret_store.delete_secret(GEMINI_API_KEY)
        elif secret_type == "hanlim_api_key":
            context.secret_store.delete_secret(HANLIM_API_KEY)
        st.session_state["settings_flash"] = "誘쇨컧?뺣낫瑜???젣?덉뒿?덈떎."
        st.rerun()
        return

    if action in {"toggle_send_registration", "toggle_template"}:
        template_id = payload.get("id")
        enabled = payload.get("enabled")
        context.template_service.set_enabled(template_id, enabled)
        context.scheduler_manager.refresh_jobs()
        st.rerun()
        return

    if action == "delete_mail_template":
        template_id = payload.get("id")
        context.mail_template_service.delete_template(template_id)
        st.session_state["templates_page_flash"] = "?쒗뵆由우쓣 ??젣?덉뒿?덈떎."
        st.rerun()
        return

    if action in {"delete_send_registration", "delete_template"}:
        template_id = payload.get("id")
        context.template_service.delete_template(template_id)
        context.scheduler_manager.refresh_jobs()
        st.session_state["templates_page_flash"] = "諛쒖넚?깅줉????젣?덉뒿?덈떎."
        st.rerun()
        return

    if action == "save_mail_template":
        try:
            template = _build_mail_template_from_payload(context.address_book_service, payload)
            saved_id = context.mail_template_service.save_template(template)
            _set_autosend_selection(mail_template_id=saved_id)
            st.session_state["templates_page_flash"] = "?쒗뵆由우쓣 ??ν뻽?듬땲??"
        except Exception as exc:  # noqa: BLE001
            st.session_state["templates_page_flash"] = f"????ㅽ뙣: {exc}"
        st.rerun()
        return

    if action in {"save_send_registration", "save_template"}:
        try:
            registration = _build_send_registration_from_payload(context.address_book_service, payload)
            validation_errors = _validate_send_template(registration, require_schedule=True)
            if validation_errors:
                raise ValueError("\n".join(validation_errors))
            saved_id = context.template_service.save_template(registration)
            _set_autosend_selection(send_registration_id=saved_id)
            context.scheduler_manager.refresh_jobs()
            st.session_state["templates_page_flash"] = "諛쒖넚?깅줉????ν뻽?듬땲??"
        except Exception as exc:  # noqa: BLE001
            st.session_state["templates_page_flash"] = f"????ㅽ뙣: {exc}"
        st.rerun()
        return

    if action == "test_send_registration":
        try:
            registration = _build_send_registration_from_payload(context.address_book_service, payload)
            validation_errors = _validate_send_template(registration, require_schedule=False)
            if validation_errors:
                raise ValueError("\n".join(validation_errors))
            _, message = context.send_service.send_test_template(registration)
            st.session_state["templates_page_flash"] = message
        except Exception as exc:  # noqa: BLE001
            st.session_state["templates_page_flash"] = f"??뀒?ㅽ듃 諛쒖넚 ?ㅽ뙣: {exc}"
        st.rerun()
        return

    if action == "mark_thread_done":
        thread_key = str(payload.get("thread_key") or "").strip().lower()
        updated_count = context.mail_repository.mark_thread_done(thread_key)
        st.session_state[_MODERN_UI_FLASH_KEY] = (
            f"?ㅻ젅?쒖쓽 硫붿씪 {updated_count}嫄댁쓣 ?꾨즺 ?곹깭濡??뺤떊?덉뒿?덈떎."
            if updated_count
            else "?꾨즺 泥섎━???ㅻ젅?쒕? 李얠? 紐삵뻽?듬땲??"
        )
        st.rerun()
        return

    if action == "create_follow_up_draft":
        thread_key = str(payload.get("thread_key") or "").strip().lower()
        try:
            thread = next(
                (
                    item
                    for item in context.mail_repository.list_thread_overviews(user_email=config.user_email)
                    if str(item.thread_key or "").strip().lower() == thread_key
                ),
                None,
            )
            if thread is None:
                raise ValueError("?좏깮???ㅻ젅?쒕? 李얠? 紐삵뻽?듬땲??")

            thread_mails = context.mail_repository.list_thread_mails_by_keys(
                [thread_key],
                limit_per_thread=None,
            ).get(thread_key, [])
            draft = _build_follow_up_mail_template(
                thread,
                thread_mails,
                current_user_email=config.user_email,
                current_user_name=config.user_display_name,
            )
            saved_id = context.mail_template_service.save_template(draft)
            _set_autosend_selection(mail_template_id=saved_id)
            st.session_state["current_page"] = AUTO_SEND_PAGE
            st.session_state["templates_page_flash"] = "?꾩냽 珥덉븞????ν븯怨??먮룞諛쒖넚 ?붾줈 ?대룞?⑸땲??"
        except Exception as exc:  # noqa: BLE001
            st.session_state[_MODERN_UI_FLASH_KEY] = str(exc) or "?꾩냽 珥덉븞 ?앹꽦??ㅽ뙣?덉뒿?덈떎."
        st.rerun()
        return

    if action == "scheduler_cmd":
        cmd = payload.get("cmd")
        if cmd == "pause":
            context.scheduler_manager.pause_all()
        elif cmd == "resume":
            context.scheduler_manager.resume_all()
            context.scheduler_manager.refresh_jobs()
        st.rerun()

