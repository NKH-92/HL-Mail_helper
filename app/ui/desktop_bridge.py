"""Direct pywebview bridge for the desktop shell."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from html import escape
import re
from threading import RLock, Thread
from typing import Any

from app.core.security import GEMINI_API_KEY, HANLIM_API_KEY, MAIL_PASSWORD_KEY, mask_sensitive_text
from app.db.models import MailTemplate, SendTemplate
from app.runtime_context import AppContext
from app.ui.page_config import (
    ALL_PAGES,
    ARCHIVE_PAGE,
    AUTO_SEND_PAGE,
    COMPLETED_PAGE,
    DASHBOARD_PAGE,
    HELP_PAGE,
    LOGS_PAGE,
    SETTINGS_PAGE,
    normalize_page as _normalize_page,
    resolve_page_id,
)
from app.ui.settings_ops import apply_settings_update
from app.ui.ui_state_helpers import (
    build_classified_mail_dicts,
    build_dashboard_mail_category_counts,
    build_dashboard_mail_page_context,
    build_mail_template_from_payload,
    build_mailbox_test_submission,
    build_send_registration_from_payload,
    build_settings_submission,
    build_sync_status_dict,
    format_mailbox_cycle_message,
    normalize_dashboard_mail_tab,
    normalize_dashboard_mail_view,
    read_log_tail,
    resolve_dashboard_mail_tab_for_counts,
    resolve_dashboard_mail_tab_key,
    validate_send_template,
)


TRAY_TODO_POPUP = "todos"
TRAY_AUTO_SEND_POPUP = "autosend"
_ADDRESS_BOOK_PAGES = {AUTO_SEND_PAGE}
_DASHBOARD_COLLECTION_PAGES = {DASHBOARD_PAGE, ARCHIVE_PAGE, COMPLETED_PAGE}


@dataclass(slots=True)
class DesktopViewState:
    """Transient UI state for the desktop shell."""

    current_page: str = DASHBOARD_PAGE
    dashboard_mail_tab: str = "category_1"
    dashboard_mail_view: str = "list"
    mailbox_candidates: list[str] = field(default_factory=list)
    selected_mail_id: int = 0
    selected_mail_template_id: int = 0
    selected_send_registration_id: int = 0
    flash_message: str | None = None
    last_client_action_id: str = ""
    last_client_state_version: int = 0


@dataclass(slots=True)
class DesktopSyncProgress:
    """Transient progress state for a manual mailbox cycle."""

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


class DesktopApi:
    """Expose app actions to the local pywebview frontend."""

    def __init__(self, context: AppContext) -> None:
        self.context = context
        self.state = DesktopViewState()
        self._sync_progress = DesktopSyncProgress()
        self._lock = RLock()
        self._main_window: Any | None = None
        self._sync_thread: Thread | None = None

    def bind_main_window(self, window: Any) -> None:
        with self._lock:
            self._main_window = window

    def bootstrap(self) -> dict[str, object]:
        """Return the initial UI state for the main window."""

        with self._lock:
            return self._build_page_state(self.state.current_page)

    def dispatch(self, request: dict[str, Any] | None = None) -> dict[str, object]:
        """Handle one UI action and return the refreshed page state."""

        with self._lock:
            message = request if isinstance(request, dict) else {}
            action = str(message.get("action") or "").strip()
            payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
            client_state = message.get("client_state") if isinstance(message.get("client_state"), dict) else {}
            try:
                client_state_version = int(message.get("client_state_version") or 0)
            except (TypeError, ValueError):
                client_state_version = 0
            client_action_id = str(message.get("client_action_id") or "").strip()
            if client_action_id and client_action_id == self.state.last_client_action_id:
                return self._build_page_state(self.state.current_page)
            if client_action_id:
                self.state.last_client_action_id = client_action_id
            should_apply_client_state = client_state_version >= self.state.last_client_state_version
            if should_apply_client_state:
                self.state.last_client_state_version = client_state_version
                self.state.dashboard_mail_tab = normalize_dashboard_mail_tab(
                    client_state.get("dashboard_mail_tab"),
                )
                self.state.dashboard_mail_view = normalize_dashboard_mail_view(
                    client_state.get("dashboard_mail_view"),
                )
                self.state.selected_mail_id = _normalize_positive_int(client_state.get("selected_mail_id"))

            requested_page = _normalize_page(
                str(
                    (
                        message.get("page")
                        if should_apply_client_state
                        else None
                    )
                    or (
                        client_state.get("page")
                        if should_apply_client_state
                        else None
                    )
                    or self.state.current_page
                    or ""
                )
            )
            if action == "navigate":
                if should_apply_client_state:
                    requested_page = _normalize_page(str(message.get("page") or payload.get("page") or requested_page))
                    self.state.current_page = requested_page
                return self._build_page_state(self.state.current_page)

            page_override: str | None = None
            try:
                page_override = self._handle_action(action, payload)
            except Exception as exc:  # noqa: BLE001
                self.context.logger.exception("Desktop UI action failed: %s", exc)
                self.state.flash_message = mask_sensitive_text(str(exc)) or "Unexpected error"

            if page_override in ALL_PAGES:
                self.state.current_page = page_override
            elif requested_page in ALL_PAGES:
                self.state.current_page = requested_page
            return self._build_page_state(self.state.current_page)

    def get_popup_html(self, popup_kind: str) -> str:
        """Return HTML for a read-only tray popup."""

        with self._lock:
            if popup_kind == TRAY_TODO_POPUP:
                return self._build_todo_popup_html()
            if popup_kind == TRAY_AUTO_SEND_POPUP:
                return self._build_autosend_popup_html()
            return self._wrap_popup_html("MailAI Portable", "<p>지원하지 않는 팝업입니다.</p>", popup_kind)

    def get_popup_collection_html(self, popup_kind: str, collection_key: str) -> str:
        """Return one lazily loaded tray popup collection section."""

        with self._lock:
            if popup_kind != TRAY_TODO_POPUP:
                return ""
            normalized_collection_key = _normalize_popup_collection_key(collection_key)
            if not normalized_collection_key:
                return "<div class='tray-popup-empty'>지원하지 않는 목록입니다.</div>"
            return _build_popup_collection_content_html(self.context, normalized_collection_key)

    def pick_attachment_files(self) -> list[str]:
        with self._lock:
            main_window = self._main_window

        if main_window is None:
            return []

        try:
            import webview

            selected_paths = main_window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=True,
                file_types=(
                    "모든 파일 (*.*)",
                    "문서 파일 (*.pdf;*.doc;*.docx;*.xls;*.xlsx;*.ppt;*.pptx;*.txt;*.csv;*.hwp)",
                    "이미지 파일 (*.png;*.jpg;*.jpeg;*.gif;*.webp)",
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self.context.logger.exception("Attachment picker failed: %s", exc)
            raise RuntimeError("첨부파일 선택창을 열지 못했습니다.") from exc

        if not selected_paths:
            return []
        return [str(path) for path in selected_paths if str(path).strip()]

    @staticmethod
    def _current_timestamp() -> str:
        return datetime.now().replace(microsecond=0).isoformat(sep=" ")

    def _serialize_sync_progress_locked(self) -> dict[str, object]:
        return asdict(self._sync_progress)

    def _is_manual_sync_running_locked(self) -> bool:
        return bool(self._sync_progress.running and self._sync_thread and self._sync_thread.is_alive())

    def _update_sync_progress(self, event: dict[str, object] | None) -> None:
        if not isinstance(event, dict):
            return

        with self._lock:
            now = self._current_timestamp()
            for key in (
                "stage",
                "message",
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
                    setattr(self._sync_progress, key, event[key])
            self._sync_progress.updated_at = now

    def _start_manual_sync_locked(self) -> None:
        now = self._current_timestamp()
        self._sync_progress = DesktopSyncProgress(
            running=True,
            stage="syncing",
            message="메일 수집 중",
            started_at=now,
            finished_at=None,
            updated_at=now,
        )
        self._sync_thread = Thread(
            target=self._run_manual_sync_job,
            name="mailai-manual-sync",
            daemon=True,
        )
        self._sync_thread.start()

    def _run_manual_sync_job(self) -> None:
        try:
            result = self.context.scheduler_manager.run_mailbox_cycle(
                include_failed_analysis=True,
                source="manual",
                progress_callback=self._update_sync_progress,
            )
        except Exception as exc:  # noqa: BLE001
            error_message = mask_sensitive_text(str(exc)) or "메일 동기화에 실패했습니다."
            with self._lock:
                now = self._current_timestamp()
                self._sync_progress.running = False
                self._sync_progress.stage = "error"
                self._sync_progress.message = error_message
                self._sync_progress.error = error_message
                self._sync_progress.finished_at = now
                self._sync_progress.updated_at = now
                self._sync_thread = None
                self.state.flash_message = error_message
            return

        with self._lock:
            now = self._current_timestamp()
            self._sync_progress.running = False
            self._sync_progress.stage = "complete"
            self._sync_progress.message = format_mailbox_cycle_message(result)
            self._sync_progress.finished_at = now
            self._sync_progress.updated_at = now
            self._sync_progress.error = None
            self._sync_progress.scanned_count = int(getattr(result, "scanned_count", 0) or 0)
            self._sync_progress.fetched_count = int(getattr(result, "fetched_count", 0) or 0)
            self._sync_progress.saved_count = int(getattr(result, "saved_count", 0) or 0)
            self._sync_progress.analysis_success_count = int(getattr(result, "analysis_success_count", 0) or 0)
            self._sync_progress.analysis_failure_count = int(getattr(result, "analysis_failure_count", 0) or 0)
            self._sync_progress.analysis_total = (
                self._sync_progress.analysis_success_count + self._sync_progress.analysis_failure_count
            )
            self._sync_progress.analysis_completed = self._sync_progress.analysis_total
            self._sync_thread = None
            self.state.flash_message = self._sync_progress.message

    def _handle_action(self, action: str, payload: dict[str, Any]) -> str | None:
        if not action:
            return

        if action == "sync_mail":
            if self._is_manual_sync_running_locked():
                self.state.flash_message = "이미 동기화가 진행 중입니다."
            else:
                self._start_manual_sync_locked()
                self.state.flash_message = "동기화를 시작했습니다."
            return

        if action == "toggle_task":
            task_id = _normalize_positive_int(payload.get("task_id"))
            checked = bool(payload.get("checked"))
            if task_id:
                mail_id = self.context.mail_repository.mark_action_item_done(task_id, checked)
                if mail_id and checked and self.context.mail_repository.count_open_action_items(mail_id, "my") == 0:
                    self.context.mail_repository.update_status(mail_id, "done", sync_my_action_items=True)
                elif mail_id and not checked:
                    self.context.mail_repository.update_status(mail_id, "doing")
            return

        if action == "select_mail":
            selected_mail_id = _normalize_positive_int(payload.get("mail_id"))
            if selected_mail_id:
                self.state.selected_mail_id = selected_mail_id
                self.state.dashboard_mail_view = "detail"
            return

        if action == "archive_mail":
            mail_id = _normalize_positive_int(payload.get("mail_id"))
            if mail_id:
                moved_mail = self.context.mail_repository.move_mail_retention_bucket(mail_id, "archived")
                if moved_mail is None:
                    self.state.flash_message = "메일 이동에 실패했습니다. 다시 시도해 주세요."
                    return
                if self.state.selected_mail_id == mail_id:
                    self.state.dashboard_mail_view = "list"
                self.state.flash_message = "메일을 보관함으로 이동했습니다."
            return

        if action == "complete_mail":
            mail_id = _normalize_positive_int(payload.get("mail_id"))
            if mail_id:
                mail = self.context.mail_repository.move_mail_retention_bucket(mail_id, "completed")
                if mail is None:
                    self.state.flash_message = "메일 이동에 실패했습니다. 다시 시도해 주세요."
                    return
                if self.state.current_page == ARCHIVE_PAGE:
                    self.state.dashboard_mail_tab = resolve_dashboard_mail_tab_key(mail)
                    self.state.selected_mail_id = mail_id
                    self.state.dashboard_mail_view = "detail"
                    self.state.flash_message = "메일을 완료 목록으로 이동했습니다."
                    return COMPLETED_PAGE
                if self.state.selected_mail_id == mail_id:
                    self.state.dashboard_mail_view = "list"
                self.state.flash_message = "메일을 완료 목록으로 이동했습니다."
            return

        if action == "restore_mail":
            mail_id = _normalize_positive_int(payload.get("mail_id"))
            if mail_id:
                mail = self.context.mail_repository.move_mail_retention_bucket(mail_id, "classified")
                if mail is None:
                    self.state.flash_message = "메일 복구에 실패했습니다. 다시 시도해 주세요."
                    return
                self.state.dashboard_mail_tab = resolve_dashboard_mail_tab_key(mail)
                self.state.selected_mail_id = mail_id
                self.state.dashboard_mail_view = "detail"
                self.state.flash_message = "메일을 분류 목록으로 복구했습니다."
                return DASHBOARD_PAGE
            return

        if action == "save_settings":
            current_config = self.context.config_manager.load()
            next_config, pwd, api_key, hanlim_api_key = build_settings_submission(
                current_config,
                payload,
            )
            next_config = self.context.address_book_service.merge_config_profile(next_config)
            deleted_old_count, follow_up_warning = apply_settings_update(
                self.context,
                current_config,
                next_config,
                password=pwd,
                api_key=api_key,
                hanlim_api_key=hanlim_api_key,
            )

            flash_message = "설정을 저장했습니다."
            if deleted_old_count:
                flash_message += f" 보관 기간을 벗어난 메일 {deleted_old_count}건을 정리했습니다."
            if follow_up_warning:
                flash_message += f" {follow_up_warning}"
            self.state.flash_message = flash_message
            return

        if action == "mailbox_test":
            pending_config, password_override = build_mailbox_test_submission(
                self.context.config_manager.load(),
                payload,
            )
            pending_config = self.context.address_book_service.merge_config_profile(pending_config)
            mailboxes = self.context.imap_client.list_mailboxes(pending_config, password_override=password_override)
            self.state.mailbox_candidates = mailboxes
            self.state.flash_message = f"메일함 {len(mailboxes)}개를 불러왔습니다."
            return

        if action == "apply_mailbox":
            mailbox = str(payload.get("mailbox") or "").strip()
            if mailbox:
                current_config = self.context.config_manager.load()
                current_config.mailbox = mailbox
                self.context.config_manager.save(current_config)
                self.state.flash_message = f"메일함을 {mailbox}로 적용했습니다."
            return

        if action == "clear_secret":
            secret_type = str(payload.get("type") or "").strip()
            if secret_type == "password":
                self.context.secret_store.delete_secret(MAIL_PASSWORD_KEY)
                self.context.scheduler_manager.refresh_jobs()
            elif secret_type == "api_key":
                self.context.secret_store.delete_secret(GEMINI_API_KEY)
            elif secret_type == "hanlim_api_key":
                self.context.secret_store.delete_secret(HANLIM_API_KEY)
            self.state.flash_message = "저장된 민감 정보를 삭제했습니다."
            return

        if action in {"toggle_send_registration", "toggle_template"}:
            template_id = _normalize_positive_int(payload.get("id"))
            enabled = bool(payload.get("enabled"))
            if template_id:
                self.context.template_service.set_enabled(template_id, enabled)
                self.context.scheduler_manager.refresh_jobs()
            return

        if action == "delete_mail_template":
            template_id = _normalize_positive_int(payload.get("id"))
            if template_id:
                self.context.mail_template_service.delete_template(template_id)
                self.state.selected_mail_template_id = 0
                self.state.flash_message = "메일 템플릿을 삭제했습니다."
            return

        if action in {"delete_send_registration", "delete_template"}:
            template_id = _normalize_positive_int(payload.get("id"))
            if template_id:
                self.context.template_service.delete_template(template_id)
                self.context.scheduler_manager.refresh_jobs()
                self.state.selected_send_registration_id = 0
                self.state.flash_message = "발송 등록을 삭제했습니다."
            return

        if action == "save_mail_template":
            try:
                template = build_mail_template_from_payload(self.context.address_book_service, payload)
                saved_id = self.context.mail_template_service.save_template(template)
                self.state.selected_mail_template_id = saved_id
                self.state.flash_message = "메일 템플릿을 저장했습니다."
            except Exception as exc:  # noqa: BLE001
                self.state.flash_message = f"저장에 실패했습니다: {exc}"
            return

        if action in {"save_send_registration", "save_template"}:
            try:
                registration = build_send_registration_from_payload(self.context.address_book_service, payload)
                validation_errors = validate_send_template(registration, require_schedule=True)
                if validation_errors:
                    raise ValueError("\n".join(validation_errors))
                saved_id = self.context.template_service.save_template(registration)
                self.state.selected_send_registration_id = saved_id
                refresh_warning = ""
                try:
                    self.context.scheduler_manager.refresh_jobs()
                except Exception as exc:  # noqa: BLE001
                    refresh_warning = mask_sensitive_text(str(exc))
                    self.context.logger.exception("Autosend refresh failed after save: %s", exc)
                send_warning = ""
                try:
                    send_warning = self.context.send_service.get_unavailability_reason() or ""
                except Exception as exc:  # noqa: BLE001
                    send_warning = f"자동발송 상태 확인에 실패했습니다: {mask_sensitive_text(str(exc))}"
                    self.context.logger.exception("Autosend availability probe failed after save: %s", exc)
                message_parts = ["발송 등록을 저장했습니다."]
                if send_warning:
                    message_parts.append(send_warning)
                if refresh_warning:
                    message_parts.append(f"스케줄 갱신에 실패했습니다: {refresh_warning}")
                self.state.flash_message = " ".join(message_parts)
            except Exception as exc:  # noqa: BLE001
                self.state.flash_message = f"저장에 실패했습니다: {exc}"
            return

        if action == "test_send_registration":
            try:
                registration = build_send_registration_from_payload(self.context.address_book_service, payload)
                validation_errors = validate_send_template(registration, require_schedule=False)
                if validation_errors:
                    raise ValueError("\n".join(validation_errors))
                ok, message = self.context.send_service.send_test_template(registration)
                self.state.flash_message = message if ok else message
            except Exception as exc:  # noqa: BLE001
                self.state.flash_message = f"테스트 발송에 실패했습니다: {exc}"
            return

        if action == "scheduler_cmd":
            cmd = str(payload.get("cmd") or "").strip()
            if cmd == "pause":
                self.context.scheduler_manager.pause_all()
            elif cmd == "resume":
                self.context.scheduler_manager.resume_all()
                self.context.scheduler_manager.refresh_jobs()
            return

    def _build_page_state(self, requested_page: str) -> dict[str, object]:
        config = self.context.address_book_service.sync_user_profile(self.context.config_manager)
        config = self.context.address_book_service.merge_config_profile(config)
        is_ready = config.is_ready() and self.context.secret_store.has_secret(MAIL_PASSWORD_KEY)
        default_page = DASHBOARD_PAGE if is_ready else SETTINGS_PAGE
        current_page = requested_page if requested_page in ALL_PAGES else default_page
        if not is_ready and current_page not in {SETTINGS_PAGE, HELP_PAGE}:
            current_page = SETTINGS_PAGE
        self.state.current_page = current_page

        component_args: dict[str, object] = {
            "page": current_page,
            "page_id": _page_id(current_page),
            "client_state_version": self.state.last_client_state_version,
            "user": {
                "email": config.user_email or "admin@mailos.local",
                "name": config.user_display_name or "User",
            },
            "address_book_contacts": (
                self.context.address_book_service.list_contact_options()
                if current_page in _ADDRESS_BOOK_PAGES
                else []
            ),
            "flash_msg": self.state.flash_message,
            "sync_progress": self._serialize_sync_progress_locked(),
        }
        self.state.flash_message = None

        if current_page in _DASHBOARD_COLLECTION_PAGES:
            dashboard_page_context = build_dashboard_mail_page_context(_page_id(current_page))
            component_args["sync_status"] = build_sync_status_dict(
                config,
                sync_snapshot=self.context.sync_service.get_status_snapshot(),
                scheduler_status=self.context.scheduler_manager.get_mailbox_status(),
                analysis_warning=self.context.mailbox_service.get_analysis_warning(),
                backlog_counts=self.context.mail_repository.count_analysis_backlog(),
            )
            classified_mails = self.context.mail_repository.list_dashboard_mails(
                str(dashboard_page_context.get("bucket_key") or "classified"),
                limit=200,
            )
            category_counts = build_dashboard_mail_category_counts(classified_mails)
            self.state.dashboard_mail_tab = resolve_dashboard_mail_tab_for_counts(
                self.state.dashboard_mail_tab,
                category_counts,
            )
            visible_mail_ids = [mail.id for mail in classified_mails]
            if self.state.selected_mail_id not in visible_mail_ids:
                self.state.selected_mail_id = visible_mail_ids[0] if visible_mail_ids else 0
            component_args["classified_mails"] = build_classified_mail_dicts(
                classified_mails,
                address_book_service=self.context.address_book_service,
            )
            component_args["dashboard_section"] = dashboard_page_context
            component_args["dashboard_mail_tab"] = self.state.dashboard_mail_tab
            component_args["dashboard_mail_view"] = self.state.dashboard_mail_view
            component_args["dashboard_mail_category_counts"] = category_counts
            component_args["selected_mail_id"] = self.state.selected_mail_id

        elif current_page == AUTO_SEND_PAGE:
            mail_templates = self.context.mail_template_service.list_templates()
            registrations = self.context.template_service.list_templates()
            component_args["mail_templates"] = [_mail_template_to_dict(template) for template in mail_templates]
            component_args["send_registrations"] = [
                _send_registration_to_dict(registration, self.context) for registration in registrations
            ]
            component_args["scheduler_started"] = self.context.scheduler_manager.started
            component_args["scheduler_state"] = (
                str(self.context.scheduler_manager.scheduler.state)
                if self.context.scheduler_manager.started
                else "stopped"
            )
            component_args["selected_mail_template_id"] = self.state.selected_mail_template_id
            component_args["selected_send_registration_id"] = self.state.selected_send_registration_id
            self.state.selected_mail_template_id = 0
            self.state.selected_send_registration_id = 0

        elif current_page == LOGS_PAGE:
            send_logs = self.context.send_log_repository.list_recent()
            component_args["send_logs"] = [
                {
                    "id": log.id,
                    "subject": log.subject or "(untitled)",
                    "recipients": len(log.recipients) if log.recipients else 0,
                    "recipients_list": ", ".join(log.recipients) if log.recipients else "",
                    "result": (log.result or "").lower(),
                    "sent_at": log.sent_at,
                    "error": log.error_message or "",
                }
                for log in send_logs
            ]
            component_args["app_logs"] = read_log_tail(self.context.logger_path)

        elif current_page == SETTINGS_PAGE:
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
                "password": self.context.secret_store.has_secret(MAIL_PASSWORD_KEY),
                "api_key": self.context.secret_store.has_secret(GEMINI_API_KEY),
                "hanlim_api_key": self.context.secret_store.has_secret(HANLIM_API_KEY),
            }
            component_args["mailboxes"] = list(self.state.mailbox_candidates)

        return component_args

    def _build_todo_popup_html(self) -> str:
        return _desktop_api_build_todo_popup_html_v2(self)

    def _build_autosend_popup_html(self) -> str:
        templates = self.context.template_service.list_templates()
        enabled_templates = [template for template in templates if template.enabled]
        scheduled_items: list[tuple[SendTemplate, datetime]] = []
        invalid_schedule_count = 0

        for template in enabled_templates:
            next_run = self.context.send_service.calculate_next_run(template)
            if next_run is None:
                invalid_schedule_count += 1
                continue
            scheduled_items.append((template, next_run))

        scheduled_items.sort(key=lambda item: (item[1], item[0].template_name.lower(), item[0].id or 0))
        next_run_label = scheduled_items[0][1].strftime("%Y-%m-%d %H:%M") if scheduled_items else "-"
        cards = []
        for template, next_run in scheduled_items:
            recipients = len(template.to_list) + len(template.cc_list)
            cards.append(
                (
                    "<div class='tray-popup-card'>"
                    "<div class='tray-popup-row'>"
                    "<div class='tray-popup-card-main'>"
                    f"<div class='tray-popup-name'>{escape(template.template_name or '자동발송')}</div>"
                    f"<div class='tray-popup-subject'>{escape(template.subject or '(제목 없음)')}</div>"
                    f"<div class='tray-popup-meta'>수신 {recipients}명 / 반복 {escape(_repeat_label(template.repeat_type))}</div>"
                    f"<div class='tray-popup-meta'>첫 발송 {_format_datetime(template.first_send_at)}</div>"
                    "</div>"
                    f"<div class='tray-popup-chip'>{escape(next_run.strftime('%Y-%m-%d %H:%M'))}</div>"
                    "</div>"
                    "</div>"
                )
            )

        warning_markup = (
            f"<div class='tray-popup-warning'>일정이 올바르지 않아 건너뛴 활성 항목이 {invalid_schedule_count}건 있습니다.</div>"
            if invalid_schedule_count
            else ""
        )
        summary = (
            "<div class='tray-popup-metrics'>"
            f"{_metric_card('활성 템플릿', len(enabled_templates))}"
            f"{_metric_card('예약 건수', len(scheduled_items))}"
            f"{_metric_card('스케줄러', _scheduler_status_label(self.context))}"
            f"{_metric_card('다음 실행', next_run_label)}"
            "</div>"
        )
        body = (
            f"{_popup_header('트레이', '자동발송', '예정된 자동발송과 다음 실행 시각을 빠르게 확인합니다.')}"
            f"{summary}"
            f"{_popup_refresh_bar(TRAY_AUTO_SEND_POPUP)}"
            f"{warning_markup}"
            + ("".join(cards) if cards else "<div class='tray-popup-empty'>예약된 자동발송이 없습니다.</div>")
        )
        return self._wrap_popup_html("MailAI | 자동발송", body, TRAY_AUTO_SEND_POPUP)

    def _wrap_popup_html(self, title: str, body: str, popup_kind: str) -> str:
        return f"""
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: linear-gradient(135deg, #f3ebdf 0%, #f8f2e8 55%, #ece1d1 100%);
      --panel: rgba(255, 249, 241, 0.96);
      --line: rgba(196, 175, 150, 0.26);
      --ink: #0f172a;
      --muted: #475569;
      --accent: #1d4ed8;
      --accent-soft: rgba(29, 78, 216, 0.10);
      --danger-soft: #fef2f2;
      --danger: #dc2626;
      --warn-soft: #fff7ed;
      --warn: #c2410c;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--ink);
      font-family: "Segoe UI", "Malgun Gothic", sans-serif;
      padding: 12px;
    }}
    .tray-popup-header {{
      margin-bottom: 10px;
      padding: 14px 16px;
      border-radius: 18px;
      border: 1px solid var(--line);
      background: linear-gradient(135deg, rgba(29, 78, 216, 0.12), rgba(255, 255, 255, 0.98));
      box-shadow: 0 24px 60px rgba(15, 23, 42, 0.08);
    }}
    .tray-popup-kicker {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 10px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }}
    .tray-popup-title {{
      margin-top: 8px;
      font-size: 22px;
      font-weight: 800;
      line-height: 1.12;
    }}
    .tray-popup-copy {{
      margin-top: 6px;
      color: var(--muted);
      line-height: 1.5;
      font-size: 13px;
    }}
    .tray-popup-metrics {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
      margin-bottom: 10px;
    }}
    .tray-popup-metric {{
      padding: 10px 12px;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: var(--panel);
      box-shadow: 0 12px 32px rgba(15, 23, 42, 0.05);
    }}
    .tray-popup-metric-label {{
      color: #64748b;
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }}
    .tray-popup-metric-value {{
      margin-top: 4px;
      font-size: 15px;
      font-weight: 800;
    }}
    .tray-popup-actions {{
      display: flex;
      justify-content: flex-end;
      margin-bottom: 8px;
    }}
    .tray-popup-button {{
      appearance: none;
      border: 1px solid rgba(29, 78, 216, 0.18);
      background: rgba(255, 255, 255, 0.92);
      color: var(--accent);
      border-radius: 999px;
      padding: 8px 12px;
      font-weight: 700;
      font-size: 12px;
      cursor: pointer;
    }}
    .tray-popup-card {{
      padding: 11px 12px;
      border-radius: 16px;
      border: 1px solid var(--line);
      background: var(--panel);
      box-shadow: 0 18px 42px rgba(15, 23, 42, 0.05);
      margin-bottom: 8px;
    }}
    .tray-popup-row {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: flex-start;
    }}
    .tray-popup-card-main {{
      min-width: 0;
      flex: 1 1 auto;
    }}
    .tray-popup-name {{
      font-size: 14px;
      font-weight: 800;
      margin-bottom: 3px;
    }}
    .tray-popup-subject {{
      color: #334155;
      line-height: 1.4;
      font-size: 13px;
      margin-bottom: 4px;
    }}
    .tray-popup-meta {{
      color: #64748b;
      font-size: 12px;
      line-height: 1.4;
    }}
    .tray-popup-chip {{
      display: inline-flex;
      align-items: center;
      padding: 5px 8px;
      border-radius: 999px;
      background: #eff6ff;
      color: var(--accent);
      font-size: 11px;
      font-weight: 800;
      white-space: nowrap;
    }}
    .is-danger {{
      background: var(--danger-soft);
      color: var(--danger);
    }}
    .is-warn {{
      background: var(--warn-soft);
      color: var(--warn);
    }}
    .is-muted {{
      background: #f8fafc;
      color: #475569;
    }}
    .tray-popup-warning {{
      margin-bottom: 10px;
      padding: 10px 12px;
      border-radius: 14px;
      background: var(--warn-soft);
      color: var(--warn);
      border: 1px solid rgba(194, 65, 12, 0.12);
      font-weight: 600;
      font-size: 12px;
    }}
    .tray-popup-empty {{
      padding: 16px;
      border-radius: 16px;
      border: 1px dashed rgba(148, 163, 184, 0.35);
      color: #64748b;
      background: rgba(255, 255, 255, 0.72);
      text-align: center;
      font-size: 13px;
    }}
    @media (max-width: 900px) {{
      .tray-popup-metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 640px) {{
      body {{ padding: 12px; }}
      .tray-popup-metrics {{ grid-template-columns: 1fr; }}
      .tray-popup-row {{ flex-direction: column; }}
    }}
  </style>
</head>
<body>
  {body}
  <script>
    document.addEventListener("wheel", (event) => {{
      if (event.ctrlKey) {{
        event.preventDefault();
      }}
    }}, {{ passive: false }});

    document.addEventListener("keydown", (event) => {{
      if (!event.ctrlKey) {{
        return;
      }}
      if (["+", "-", "=", "0"].includes(event.key)) {{
        event.preventDefault();
      }}
    }});

    async function reloadPopup() {{
      if (!(window.pywebview && window.pywebview.api && window.pywebview.api.get_popup_html)) {{
        window.location.reload();
        return;
      }}
      const html = await window.pywebview.api.get_popup_html("{escape(popup_kind)}");
      document.open();
      document.write(html);
      document.close();
    }}
  </script>
</body>
</html>
"""


def _page_id(current_page: str) -> str:
    return resolve_page_id(current_page)


def _normalize_positive_int(value: object) -> int:
    try:
        normalized = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return normalized if normalized > 0 else 0


def _mail_template_to_dict(template: MailTemplate) -> dict[str, object]:
    recipients = len(template.to_list) + len(template.cc_list)
    return {
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


def _send_registration_to_dict(registration: SendTemplate, context: AppContext) -> dict[str, object]:
    next_run = context.send_service.calculate_next_run(registration)
    next_run_str = next_run.strftime("%Y-%m-%d %H:%M") if next_run else "예약 없음"
    recipients = len(registration.to_list) + len(registration.cc_list)
    return {
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


def _popup_header(kicker: str, title: str, copy: str) -> str:
    return (
        "<div class='tray-popup-header'>"
        f"<div class='tray-popup-kicker'>{escape(kicker)}</div>"
        f"<div class='tray-popup-title'>{escape(title)}</div>"
        f"<div class='tray-popup-copy'>{escape(copy)}</div>"
        "</div>"
    )


def _metric_card(label: str, value: object) -> str:
    return (
        "<div class='tray-popup-metric'>"
        f"<div class='tray-popup-metric-label'>{escape(label)}</div>"
        f"<div class='tray-popup-metric-value'>{escape(str(value))}</div>"
        "</div>"
    )


def _popup_refresh_bar(popup_kind: str) -> str:
    del popup_kind
    return (
        "<div class='tray-popup-actions'>"
        "<button type='button' class='tray-popup-button' onclick='reloadPopup()'>새로고침</button>"
        "</div>"
    )


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _format_datetime(value: str | None) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return value or "-"
    now = datetime.now()
    today = now.date()
    target_date = parsed.date()
    show_time = any((parsed.hour, parsed.minute, parsed.second))
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


def _popup_due_chip_v2(value: str | None) -> tuple[str, str]:
    due_value = _parse_datetime(value)
    if due_value is None:
        return ("기한 없음", "is-muted")

    today = datetime.now().date()
    if due_value.date() < today:
        return (f"지연 {_format_datetime(due_value.isoformat(sep=' '))}", "is-danger")
    if due_value.date() == today:
        return (f"오늘 {due_value.strftime('%H:%M')}", "is-warn")
    return (_format_datetime(due_value.isoformat(sep=' ')), "")


def _popup_repeat_label_v2(repeat_type: str) -> str:
    return {
        "none": "1회",
        "daily": "매일",
        "weekly": "매주",
        "monthly": "매월",
    }.get(repeat_type, repeat_type or "설정 없음")


def _popup_scheduler_status_label_v2(context: AppContext) -> str:
    if not context.scheduler_manager.started:
        return "중지"
    state = context.scheduler_manager.scheduler.state
    if str(state) == "2":
        return "일시중지"
    if str(state) == "1":
        return "실행 중"
    return "대기"


def _popup_refresh_bar_v2() -> str:
    return (
        "<div class='tray-popup-actions'>"
        "<button type='button' class='tray-popup-button' onclick='reloadPopup()'>새로고침</button>"
        "</div>"
    )


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


_POPUP_COLLECTION_LABELS = {
    "classified": "메일 분류",
    "archived": "보관함",
    "completed": "완료",
}


def _build_popup_mail_collection_payload(
    context: AppContext,
    bucket: str,
    *,
    visible_limit: int | None = None,
) -> tuple[list[dict[str, object]], dict[str, int]]:
    counts = context.mail_repository.count_dashboard_mail_categories(bucket)
    mail_limit = max(0, int(visible_limit)) if visible_limit is not None else 200
    mails = context.mail_repository.list_dashboard_mails(bucket, limit=mail_limit)
    if visible_limit is not None:
        mails = mails[: max(0, int(visible_limit))]
    return (
        build_classified_mail_dicts(
            mails,
            address_book_service=context.address_book_service,
        ),
        counts,
    )


def _build_popup_classified_mail_payload(
    context: AppContext,
    *,
    visible_limit: int | None = None,
) -> tuple[list[dict[str, object]], dict[str, int]]:
    return _build_popup_mail_collection_payload(
        context,
        "classified",
        visible_limit=visible_limit,
    )


def _normalize_popup_collection_key(value: object) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in _POPUP_COLLECTION_LABELS else ""


def _popup_total_count(counts: dict[str, int] | None) -> int:
    if not isinstance(counts, dict):
        return 0
    return sum(int(value or 0) for value in counts.values())


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


def _popup_category_tab_key(final_category: object) -> str:
    try:
        category = int(final_category)
    except (TypeError, ValueError):
        category = 3
    normalized_category = category if category in {1, 2, 3} else 3
    return f"category_{normalized_category}"


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


def _build_popup_mail_card_html(mail: dict[str, object]) -> str:
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
    return (
        f"<div class='tray-popup-card {card_class}'>"
        "<div class='tray-popup-row'>"
        "<div class='tray-popup-card-main'>"
        f"<div class='tray-popup-name'>{escape(summary)}</div>"
        f"<div class='tray-popup-subject'>{escape(subject)}</div>"
        f"<div class='tray-popup-meta'>보낸 사람 {escape(sender)} · 수신 {escape(received_at)}</div>"
        f"<div class='tray-popup-meta'>{escape(' · '.join(detail_bits))}</div>"
        "</div>"
        f"<div class='tray-popup-chip {chip_class}'>{escape(_popup_category_label(final_category))}</div>"
        "</div>"
        "</div>"
    )


def _build_popup_tabbed_mail_sections(
    classified_mails: list[dict[str, object]],
    counts: dict[str, int],
) -> str:
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
    buttons: list[str] = []
    panels: list[str] = []
    for index, (tab_key, label) in enumerate(tab_order):
        is_active = index == 0
        active_class = " is-active" if is_active else ""
        selected_attr = "true" if is_active else "false"
        count = counts.get(tab_key, 0)
        panel_cards = "".join(_build_popup_mail_card_html(mail) for mail in mails_by_tab[tab_key])
        if not panel_cards:
            panel_cards = "<div class='tray-popup-empty'>표시할 메일이 없습니다.</div>"
        buttons.append(
            (
                "<button"
                " type='button'"
                f" class='tray-popup-tab-button{active_class}'"
                f" data-popup-tab='{tab_key}'"
                f" aria-selected='{selected_attr}'"
                " role='tab'>"
                f"<span>{escape(label)}</span>"
                f"<span class='tray-popup-tab-count'>{count}</span>"
                "</button>"
            )
        )
        panels.append(
            (
                "<section"
                f" class='tray-popup-tab-panel{active_class}'"
                f" data-popup-tab-panel='{tab_key}'"
                f" aria-hidden='{'false' if is_active else 'true'}'>"
                f"{panel_cards}"
                "</section>"
            )
        )

    return (
        "<div class='tray-popup-tab-shell' data-popup-tab-root>"
        f"<div class='tray-popup-tab-list' role='tablist'>{''.join(buttons)}</div>"
        f"{''.join(panels)}"
        "</div>"
    )


def _build_popup_collection_sections(
    context: AppContext,
    collection_counts: dict[str, dict[str, int]],
    *,
    active_bucket: str = "classified",
) -> str:
    tab_order = ("classified", "archived", "completed")
    buttons: list[str] = []
    panels: list[str] = []
    for index, bucket_key in enumerate(tab_order):
        counts = collection_counts.get(bucket_key, {})
        is_active = bucket_key == active_bucket if active_bucket in tab_order else index == 0
        active_class = " is-active" if is_active else ""
        selected_attr = "true" if is_active else "false"
        total_count = _popup_total_count(counts)
        label = _POPUP_COLLECTION_LABELS.get(bucket_key, bucket_key)
        panels.append(
            (
                "<section"
                f" class='tray-popup-tab-panel{active_class}'"
                f" data-popup-tab-panel='bucket_{bucket_key}'"
                f" data-popup-collection='{bucket_key}'"
                f" data-popup-loaded='{'true' if is_active else 'false'}'"
                f" aria-hidden='{'false' if is_active else 'true'}'>"
                f"{_build_popup_collection_content_html(context, bucket_key) if is_active else _build_popup_collection_loading_html()}"
                "</section>"
            )
        )
        buttons.append(
            (
                "<button"
                " type='button'"
                f" class='tray-popup-tab-button{active_class}'"
                f" data-popup-tab='bucket_{bucket_key}'"
                f" aria-selected='{selected_attr}'"
                " role='tab'>"
                f"<span>{escape(label)}</span>"
                f"<span class='tray-popup-tab-count'>{total_count}</span>"
                "</button>"
            )
        )

    return (
        "<div class='tray-popup-tab-shell' data-popup-tab-root>"
        f"<div class='tray-popup-tab-list' role='tablist'>{''.join(buttons)}</div>"
        f"{''.join(panels)}"
        "</div>"
    )


def _build_popup_collection_content_html(
    context: AppContext,
    bucket_key: str,
    *,
    visible_limit: int | None = None,
) -> str:
    mails, counts = _build_popup_mail_collection_payload(
        context,
        bucket_key,
        visible_limit=visible_limit,
    )
    return _humanize_popup_datetime_tokens(_build_popup_tabbed_mail_sections(mails, counts))


def _build_popup_collection_loading_html() -> str:
    return "<div class='tray-popup-empty'>불러오는 중...</div>"


def _desktop_api_build_todo_popup_html_v2(self: DesktopApi) -> str:
    classified_mails, counts = _build_popup_classified_mail_payload(self.context)
    now = datetime.now()
    cards: list[str] = []

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
        cards.append(
            (
                f"<div class='tray-popup-card {card_class}'>"
                "<div class='tray-popup-row'>"
                "<div class='tray-popup-card-main'>"
                f"<div class='tray-popup-name'>{escape(summary)}</div>"
                f"<div class='tray-popup-subject'>{escape(subject)}</div>"
                f"<div class='tray-popup-meta'>보낸 사람 {escape(sender)} · 수신 {escape(received_at)}</div>"
                f"<div class='tray-popup-meta'>{escape(' · '.join(detail_bits))}</div>"
                "</div>"
                f"<div class='tray-popup-chip {chip_class}'>{escape(_popup_category_label(final_category))}</div>"
                "</div>"
                "</div>"
            )
        )

    summary = (
        "<div class='tray-popup-metrics'>"
        f"{_metric_card('내가해야할일', counts.get('category_1', 0))}"
        f"{_metric_card('내가검토할일', counts.get('category_2', 0))}"
        f"{_metric_card('단순 참고용', counts.get('category_3', 0))}"
        f"{_metric_card('마지막 갱신', now.strftime('%Y-%m-%d %H:%M'))}"
        "</div>"
    )
    body = (
        f"{_popup_header('Tray', '메일 분류', '현재 수신 메일을 내가해야할일, 내가검토할일, 단순 참고용으로 빠르게 확인할 수 있습니다.')}"
        f"{summary}"
        f"{_popup_refresh_bar_v2()}"
        + ("".join(cards) if cards else "<div class='tray-popup-empty'>표시할 메일이 없습니다.</div>")
    )
    return self._wrap_popup_html("MailAI | 메일 분류", body, TRAY_TODO_POPUP)


def _desktop_api_build_autosend_popup_html_v2(self: DesktopApi) -> str:
    templates = self.context.template_service.list_templates()
    enabled_templates = [template for template in templates if template.enabled]
    scheduled_items: list[tuple[SendTemplate, datetime]] = []
    invalid_schedule_count = 0

    for template in enabled_templates:
        next_run = self.context.send_service.calculate_next_run(template)
        if next_run is None:
            invalid_schedule_count += 1
            continue
        scheduled_items.append((template, next_run))

    scheduled_items.sort(key=lambda item: (item[1], item[0].template_name.lower(), item[0].id or 0))
    next_run_label = scheduled_items[0][1].strftime("%Y-%m-%d %H:%M") if scheduled_items else "-"
    cards: list[str] = []

    for template, next_run in scheduled_items:
        recipients = len(template.to_list) + len(template.cc_list)
        cards.append(
            (
                "<div class='tray-popup-card'>"
                "<div class='tray-popup-row'>"
                "<div class='tray-popup-card-main'>"
                f"<div class='tray-popup-name'>{escape(template.template_name or '자동발송')}</div>"
                f"<div class='tray-popup-subject'>{escape(template.subject or '(제목 없음)')}</div>"
                f"<div class='tray-popup-meta'>수신 {recipients}명 / 반복 {escape(_popup_repeat_label_v2(template.repeat_type))}</div>"
                f"<div class='tray-popup-meta'>첫 발송 {_format_datetime(template.first_send_at)}</div>"
                "</div>"
                f"<div class='tray-popup-chip'>{escape(next_run.strftime('%Y-%m-%d %H:%M'))}</div>"
                "</div>"
                "</div>"
            )
        )

    warning_markup = (
        f"<div class='tray-popup-warning'>일정이 올바르지 않아 건너뛴 활성 항목이 {invalid_schedule_count}건 있습니다.</div>"
        if invalid_schedule_count
        else ""
    )
    summary = (
        "<div class='tray-popup-metrics'>"
        f"{_metric_card('활성 템플릿', len(enabled_templates))}"
        f"{_metric_card('예약 건수', len(scheduled_items))}"
        f"{_metric_card('스케줄러', _popup_scheduler_status_label_v2(self.context))}"
        f"{_metric_card('다음 실행', next_run_label)}"
        "</div>"
    )
    body = (
        f"{_popup_header('Tray', '자동발송', '예정된 자동발송과 다음 실행 시각을 빠르게 확인할 수 있습니다.')}"
        f"{summary}"
        f"{_popup_refresh_bar_v2()}"
        f"{warning_markup}"
        + ("".join(cards) if cards else "<div class='tray-popup-empty'>예약된 자동발송이 없습니다.</div>")
    )
    return self._wrap_popup_html("MailAI | 자동발송", body, TRAY_AUTO_SEND_POPUP)


def _desktop_api_wrap_popup_html_v2(self: DesktopApi, title: str, body: str, popup_kind: str) -> str:
    return f"""
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: linear-gradient(180deg, #f5efe6 0%, #ebe2d4 100%);
      --panel: linear-gradient(180deg, rgba(255, 250, 243, 0.98), rgba(247, 240, 232, 0.98));
      --line: #ddd1c2;
      --line-strong: #ccbdaa;
      --ink: #0f1a2b;
      --muted: #4c6178;
      --accent: #1976d2;
      --accent-soft: rgba(25, 118, 210, 0.12);
      --danger-soft: #ffebee;
      --danger: #c62828;
      --warn-soft: #fff3e0;
      --warn: #ef6c00;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--ink);
      font-family: "Roboto", "Noto Sans KR", "Segoe UI", sans-serif;
      padding: 10px;
    }}
    .tray-popup-header {{
      margin-bottom: 8px;
      padding: 14px 15px;
      border-radius: 18px;
      border: 1px solid var(--line);
      background:
        radial-gradient(circle at top right, rgba(25, 118, 210, 0.12), rgba(25, 118, 210, 0) 28%),
        linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(246, 249, 253, 0.98));
      box-shadow: 0 14px 34px rgba(15, 23, 42, 0.06);
    }}
    .tray-popup-kicker {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 5px 9px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }}
    .tray-popup-title {{
      margin-top: 8px;
      font-size: 22px;
      font-weight: 800;
      line-height: 1.12;
    }}
    .tray-popup-copy {{
      margin-top: 5px;
      color: var(--muted);
      line-height: 1.52;
      font-size: 13px;
    }}
    .tray-popup-metrics {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
      margin-bottom: 8px;
    }}
    .tray-popup-metric {{
      padding: 10px 11px;
      border-radius: 15px;
      border: 1px solid var(--line);
      background: var(--panel);
    }}
    .tray-popup-metric-label {{
      color: var(--muted);
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .tray-popup-metric-value {{
      margin-top: 4px;
      font-size: 14px;
      font-weight: 800;
      line-height: 1.2;
    }}
    .tray-popup-actions {{
      display: flex;
      justify-content: flex-end;
      margin-bottom: 8px;
    }}
    .tray-popup-button {{
      appearance: none;
      border: 1px solid rgba(25, 118, 210, 0.18);
      background: #ffffff;
      color: var(--accent);
      border-radius: 999px;
      padding: 7px 12px;
      font-weight: 700;
      font-size: 12px;
      cursor: pointer;
    }}
    .tray-popup-button:hover {{
      border-color: rgba(25, 118, 210, 0.28);
      background: #f8fbff;
    }}
    .tray-popup-card {{
      padding: 11px 12px;
      border-radius: 16px;
      border: 1px solid var(--line);
      background: var(--panel);
      margin-bottom: 7px;
    }}
    .tray-popup-row {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: flex-start;
    }}
    .tray-popup-card-main {{
      min-width: 0;
      flex: 1 1 auto;
    }}
    .tray-popup-name {{
      font-size: 14px;
      font-weight: 800;
      line-height: 1.35;
      margin-bottom: 3px;
    }}
    .tray-popup-subject {{
      color: #22384d;
      line-height: 1.48;
      font-size: 13px;
      margin-bottom: 4px;
    }}
    .tray-popup-meta {{
      color: var(--muted);
      font-size: 12px;
      line-height: 1.4;
    }}
    .tray-popup-chip {{
      display: inline-flex;
      align-items: center;
      padding: 5px 8px;
      border-radius: 999px;
      background: #edf4ff;
      border: 1px solid rgba(25, 118, 210, 0.12);
      color: var(--accent);
      font-size: 11px;
      font-weight: 800;
      white-space: nowrap;
    }}
    .is-danger {{
      background: var(--danger-soft);
      border-color: #ffcdd2;
      color: var(--danger);
    }}
    .is-warn {{
      background: var(--warn-soft);
      border-color: #ffcc80;
      color: var(--warn);
    }}
    .is-muted {{
      background: #f3f6fb;
      border-color: #dde6f0;
      color: #51667d;
    }}
    .tray-popup-warning {{
      margin-bottom: 8px;
      padding: 10px 12px;
      border-radius: 14px;
      background: var(--warn-soft);
      color: var(--warn);
      border: 1px solid rgba(239, 108, 0, 0.14);
      font-weight: 600;
      font-size: 12px;
    }}
    .tray-popup-empty {{
      padding: 14px;
      border-radius: 16px;
      border: 1px dashed var(--line-strong);
      color: var(--muted);
      background: rgba(255, 255, 255, 0.72);
      text-align: center;
      font-size: 13px;
    }}
    @media (max-width: 900px) {{
      .tray-popup-metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 640px) {{
      .tray-popup-metrics {{ grid-template-columns: 1fr; }}
      .tray-popup-row {{ flex-direction: column; }}
    }}
  </style>
</head>
<body>
  {body}
  <script>
    document.addEventListener("wheel", (event) => {{
      if (event.ctrlKey) {{
        event.preventDefault();
      }}
    }}, {{ passive: false }});

    document.addEventListener("keydown", (event) => {{
      if (!event.ctrlKey) {{
        return;
      }}
      if (["+", "-", "=", "0"].includes(event.key)) {{
        event.preventDefault();
      }}
    }});

    async function reloadPopup() {{
      if (!(window.pywebview && window.pywebview.api && window.pywebview.api.get_popup_html)) {{
        window.location.reload();
        return;
      }}
      const html = await window.pywebview.api.get_popup_html("{escape(popup_kind)}");
      document.open();
      document.write(html);
      document.close();
    }}
  </script>
</body>
</html>
"""


DesktopApi._build_todo_popup_html = _desktop_api_build_todo_popup_html_v2
DesktopApi._build_autosend_popup_html = _desktop_api_build_autosend_popup_html_v2
DesktopApi._wrap_popup_html = _desktop_api_wrap_popup_html_v2


_POPUP_NUMERIC_DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?")


def _format_datetime(value: datetime | str | None, *, include_time: bool | None = None) -> str:
    parsed = value if isinstance(value, datetime) else _parse_datetime(value)
    if parsed is None:
        return str(value or "-")

    now = datetime.now()
    today = now.date()
    target_date = parsed.date()
    show_time = include_time if include_time is not None else any((parsed.hour, parsed.minute, parsed.second))
    weekday = ["\uc6d4", "\ud654", "\uc218", "\ubaa9", "\uae08", "\ud1a0", "\uc77c"][parsed.weekday()]

    if target_date == today:
        return f"\uc624\ub298 {parsed:%H:%M}" if show_time else "\uc624\ub298"
    if (target_date - today).days == 1:
        return f"\ub0b4\uc77c {parsed:%H:%M}" if show_time else "\ub0b4\uc77c"
    if (target_date - today).days == -1:
        return f"\uc5b4\uc81c {parsed:%H:%M}" if show_time else "\uc5b4\uc81c"

    if parsed.year == now.year:
        base = f"{parsed.month}\uc6d4 {parsed.day}\uc77c ({weekday})"
    else:
        base = f"{parsed.year}\ub144 {parsed.month}\uc6d4 {parsed.day}\uc77c ({weekday})"
    return f"{base} {parsed:%H:%M}" if show_time else base


def _send_registration_to_dict(registration: SendTemplate, context: AppContext) -> dict[str, object]:
    next_run = context.send_service.calculate_next_run(registration)
    recipients = len(registration.to_list) + len(registration.cc_list)
    return {
        "id": registration.id,
        "name": registration.template_name,
        "subject": registration.subject,
        "enabled": registration.enabled,
        "next_run": _format_datetime(next_run) if next_run else "\uc608\uc57d \uc5c6\uc74c",
        "recipients": recipients,
        "repeat_type": registration.repeat_type,
        "body": registration.body,
        "to_raw": ", ".join(registration.to_list),
        "cc_raw": ", ".join(registration.cc_list),
        "attachment_raw": ", ".join(registration.attachment_paths),
        "first_send_at": registration.first_send_at,
        "send_time": registration.send_time,
    }


def _humanize_popup_datetime_tokens(html: str) -> str:
    return _POPUP_NUMERIC_DATETIME_RE.sub(lambda match: _format_datetime(match.group(0)), html)


def _desktop_api_build_todo_popup_html_v3(self: DesktopApi) -> str:
    return _humanize_popup_datetime_tokens(_desktop_api_build_todo_popup_html_v2(self))


def _desktop_api_build_autosend_popup_html_v3(self: DesktopApi) -> str:
    return _humanize_popup_datetime_tokens(_desktop_api_build_autosend_popup_html_v2(self))


DesktopApi._build_todo_popup_html = _desktop_api_build_todo_popup_html_v3
DesktopApi._build_autosend_popup_html = _desktop_api_build_autosend_popup_html_v3


def _due_chip(value: str | None) -> tuple[str, str]:
    due_value = _parse_datetime(value)
    if due_value is None:
        return ("기한 없음", "is-muted")

    today = datetime.now().date()
    if due_value.date() < today:
        return (f"지연 {due_value.strftime('%m-%d %H:%M')}", "is-danger")
    if due_value.date() == today:
        return (f"오늘 {due_value.strftime('%H:%M')}", "is-warn")
    return (due_value.strftime("%m-%d %H:%M"), "")


def _repeat_label(repeat_type: str) -> str:
    return {
        "none": "1회",
        "daily": "매일",
        "weekly": "매주",
        "monthly": "매월",
    }.get(repeat_type, repeat_type or "알 수 없음")


def _scheduler_status_label(context: AppContext) -> str:
    if not context.scheduler_manager.started:
        return "중지"
    state = context.scheduler_manager.scheduler.state
    if str(state) == "2":
        return "일시중지"
    if str(state) == "1":
        return "실행 중"
    return "대기"


def _desktop_api_wrap_popup_html_v4(self: DesktopApi, title: str, body: str, popup_kind: str) -> str:
    del self
    return f"""
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --popup-bg-top: #fdfcfb;
      --popup-bg-bottom: #f0ebe6;
      --popup-panel: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(253, 250, 248, 0.98));
      --popup-panel-strong: linear-gradient(180deg, rgba(255, 255, 255, 1), rgba(249, 243, 238, 0.98));
      --popup-line: #e0d3c8;
      --popup-line-strong: #cdbfb3;
      --popup-divider: rgba(180, 156, 138, 0.24);
      --popup-text: #2a1f15;
      --popup-muted: #6b5a4c;
      --popup-subtle: #8a7666;
      --popup-primary: #cc7d5e;
      --popup-primary-dark: #b5684a;
      --popup-primary-soft: rgba(204, 125, 94, 0.10);
      --popup-primary-soft-strong: rgba(204, 125, 94, 0.18);
      --popup-danger-bg: #fff1ec;
      --popup-danger-line: #f0c4b4;
      --popup-danger-text: #b94f28;
      --popup-warn-bg: #fdf4ea;
      --popup-warn-line: #ebc9a5;
      --popup-warn-text: #a5622b;
      --popup-muted-bg: #f7f1eb;
      --popup-muted-line: #e5d8cc;
      --popup-shadow-xs: 0 1px 2px rgba(42, 28, 15, 0.05), 0 10px 24px -18px rgba(42, 28, 15, 0.18);
      --popup-shadow-sm: 0 14px 28px -22px rgba(42, 28, 15, 0.20), 0 6px 14px rgba(42, 28, 15, 0.06);
    }}
    * {{ box-sizing: border-box; }}
    html, body {{
      margin: 0;
      min-height: 100%;
    }}
    body {{
      min-height: 100vh;
      background: linear-gradient(180deg, var(--popup-bg-top) 0%, var(--popup-bg-bottom) 100%);
      color: var(--popup-text);
      font-family: "Segoe UI Variable Text", "Segoe UI", "Malgun Gothic", "Noto Sans KR", sans-serif;
      font-size: 12.5px;
      line-height: 1.52;
      padding: 12px;
      -webkit-font-smoothing: subpixel-antialiased;
      text-rendering: auto;
      font-smooth: never;
    }}
    .tray-popup-shell {{
      display: flex;
      flex-direction: column;
      gap: 10px;
      min-height: calc(100vh - 24px);
    }}
    .tray-popup-header {{
      margin-bottom: 0;
      padding: 14px 15px;
      border-radius: 20px;
      border: 1px solid var(--popup-line);
      background:
        radial-gradient(circle at top right, rgba(204, 125, 94, 0.14), rgba(204, 125, 94, 0) 34%),
        linear-gradient(180deg, rgba(255, 255, 255, 0.99), rgba(249, 243, 238, 0.98));
      box-shadow: var(--popup-shadow-sm);
    }}
    .tray-popup-kicker {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 4px 8px;
      border-radius: 999px;
      background: var(--popup-primary-soft);
      color: var(--popup-primary);
      font-size: 10px;
      font-weight: 800;
      letter-spacing: 0.05em;
      text-transform: uppercase;
    }}
    .tray-popup-title {{
      margin-top: 7px;
      font-size: 18.5px;
      font-weight: 800;
      line-height: 1.12;
      letter-spacing: -0.01em;
      color: var(--popup-text);
    }}
    .tray-popup-copy {{
      margin-top: 5px;
      color: var(--popup-muted);
      line-height: 1.58;
      font-size: 11.8px;
    }}
    .tray-popup-metrics {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
      margin-bottom: 0;
    }}
    .tray-popup-metric {{
      min-width: 0;
      padding: 9px 10px;
      border-radius: 15px;
      border: 1px solid var(--popup-line);
      background: var(--popup-panel);
      box-shadow: var(--popup-shadow-xs);
    }}
    .tray-popup-metric-label {{
      color: var(--popup-subtle);
      font-size: 9.5px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      line-height: 1.2;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .tray-popup-metric-value {{
      margin-top: 3px;
      font-size: 12.5px;
      font-weight: 800;
      line-height: 1.25;
      color: var(--popup-text);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .tray-popup-actions {{
      display: flex;
      justify-content: flex-end;
      margin-bottom: 0;
    }}
    .tray-popup-button {{
      appearance: none;
      border: 1px solid rgba(204, 125, 94, 0.16);
      background: linear-gradient(180deg, #ffffff, #f9f1eb);
      color: var(--popup-primary);
      border-radius: 999px;
      padding: 7px 11px;
      font-weight: 800;
      font-size: 11.2px;
      line-height: 1.1;
      cursor: pointer;
      box-shadow: none;
      transition: background 0.16s ease, border-color 0.16s ease, color 0.16s ease;
    }}
    .tray-popup-button:hover {{
      border-color: rgba(204, 125, 94, 0.28);
      background: linear-gradient(180deg, #fffdfa, #f4e8e0);
      color: var(--popup-primary-dark);
    }}
    .tray-popup-card {{
      padding: 11px 12px;
      border-radius: 16px;
      border: 1px solid var(--popup-line);
      background: var(--popup-panel-strong);
      box-shadow: var(--popup-shadow-xs);
      margin-bottom: 8px;
      position: relative;
      overflow: hidden;
    }}
    .tray-popup-card::before {{
      content: "";
      position: absolute;
      inset: 0 auto 0 0;
      width: 4px;
      background: transparent;
    }}
    .tray-popup-card.is-category-1::before {{
      background: var(--popup-primary);
    }}
    .tray-popup-card.is-category-2::before {{
      background: #a5622b;
    }}
    .tray-popup-card.is-category-3::before {{
      background: #6f7c86;
    }}
    .tray-popup-row {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: flex-start;
    }}
    .tray-popup-card-main {{
      min-width: 0;
      flex: 1 1 auto;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }}
    .tray-popup-name {{
      font-size: 13.5px;
      font-weight: 800;
      line-height: 1.38;
      margin-bottom: 2px;
      color: var(--popup-text);
    }}
    .tray-popup-subject {{
      color: #3c2f24;
      line-height: 1.52;
      font-size: 12.2px;
      margin-bottom: 4px;
    }}
    .tray-popup-meta {{
      color: #6f5f50;
      font-size: 11.2px;
      line-height: 1.48;
    }}
    .tray-popup-chip {{
      display: inline-flex;
      align-items: center;
      padding: 4px 7px;
      border-radius: 999px;
      background: var(--popup-primary-soft);
      border: 1px solid rgba(204, 125, 94, 0.14);
      color: var(--popup-primary);
      font-size: 10px;
      font-weight: 800;
      line-height: 1.2;
      white-space: nowrap;
      flex-shrink: 0;
    }}
    .is-danger {{
      background: var(--popup-danger-bg);
      border-color: var(--popup-danger-line);
      color: var(--popup-danger-text);
    }}
    .is-warn {{
      background: var(--popup-warn-bg);
      border-color: var(--popup-warn-line);
      color: var(--popup-warn-text);
    }}
    .is-muted {{
      background: var(--popup-muted-bg);
      border-color: var(--popup-muted-line);
      color: #617286;
    }}
    .tray-popup-warning {{
      margin-bottom: 0;
      padding: 10px 11px;
      border-radius: 14px;
      background: var(--popup-warn-bg);
      color: var(--popup-warn-text);
      border: 1px solid rgba(165, 98, 43, 0.16);
      font-weight: 700;
      font-size: 11.2px;
      line-height: 1.45;
    }}
    .tray-popup-empty {{
      padding: 14px 12px;
      border-radius: 16px;
      border: 1px dashed var(--popup-line-strong);
      color: var(--popup-muted);
      background: rgba(255, 252, 248, 0.84);
      text-align: center;
      font-size: 11.8px;
      line-height: 1.55;
    }}
    @media (max-width: 560px) {{
      body {{ padding: 6px; }}
      .tray-popup-shell {{ min-height: calc(100vh - 12px); }}
      .tray-popup-metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 420px) {{
      .tray-popup-metrics {{ grid-template-columns: 1fr; }}
      .tray-popup-row {{ flex-direction: column; }}
      .tray-popup-chip {{ align-self: flex-start; }}
    }}
  </style>
</head>
<body>
  <div class="tray-popup-shell">
    {body}
  </div>
  <script>
    document.addEventListener("wheel", (event) => {{
      if (event.ctrlKey) {{
        event.preventDefault();
      }}
    }}, {{ passive: false }});

    document.addEventListener("keydown", (event) => {{
      if (!event.ctrlKey) {{
        return;
      }}
      if (["+", "-", "=", "0"].includes(event.key)) {{
        event.preventDefault();
      }}
    }});

    async function reloadPopup() {{
      if (!(window.pywebview && window.pywebview.api && window.pywebview.api.get_popup_html)) {{
        window.location.reload();
        return;
      }}
      const html = await window.pywebview.api.get_popup_html("{escape(popup_kind)}");
      document.open();
      document.write(html);
      document.close();
    }}
  </script>
</body>
</html>
"""


DesktopApi._wrap_popup_html = _desktop_api_wrap_popup_html_v4


def _desktop_api_build_todo_popup_html_v4(self: DesktopApi) -> str:
    collection_counts = {
        "classified": self.context.mail_repository.count_dashboard_mail_categories("classified"),
        "archived": self.context.mail_repository.count_dashboard_mail_categories("archived"),
        "completed": self.context.mail_repository.count_dashboard_mail_categories("completed"),
    }
    now = datetime.now()
    summary = (
        "<div class='tray-popup-metrics'>"
        f"{_metric_card('메일 분류', _popup_total_count(collection_counts['classified']))}"
        f"{_metric_card('보관함', _popup_total_count(collection_counts['archived']))}"
        f"{_metric_card('완료', _popup_total_count(collection_counts['completed']))}"
        f"{_metric_card('마지막 갱신', now.strftime('%Y-%m-%d %H:%M'))}"
        "</div>"
    )
    body = (
        f"{_popup_header('Tray', '메일 분류', '현재 수신 메일을 내가해야할일, 내가검토할일, 단순 참고용으로 빠르게 확인할 수 있습니다.')}"
        f"{summary}"
        f"{_popup_refresh_bar_v2()}"
        f"{_build_popup_collection_sections(self.context, collection_counts)}"
    )
    html = self._wrap_popup_html("MailAI | 메일 분류", body, TRAY_TODO_POPUP)
    return _humanize_popup_datetime_tokens(html)


def _desktop_api_wrap_popup_html_v5(self: DesktopApi, title: str, body: str, popup_kind: str) -> str:
    html = _desktop_api_wrap_popup_html_v4(self, title, body, popup_kind)
    tab_css = """
    .tray-popup-tab-shell {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .tray-popup-tab-list {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .tray-popup-tab-button {
      appearance: none;
      border: 1px solid var(--popup-line);
      background: rgba(255, 255, 255, 0.72);
      color: var(--popup-muted);
      border-radius: 999px;
      padding: 7px 11px;
      font-weight: 800;
      font-size: 11px;
      line-height: 1.1;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 7px;
      transition: border-color 0.16s ease, background 0.16s ease, color 0.16s ease;
    }
    .tray-popup-tab-button:hover {
      border-color: rgba(204, 125, 94, 0.24);
      color: var(--popup-primary-dark);
    }
    .tray-popup-tab-button.is-active {
      border-color: rgba(204, 125, 94, 0.28);
      background: var(--popup-primary-soft);
      color: var(--popup-primary-dark);
    }
    .tray-popup-tab-count {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 18px;
      height: 18px;
      padding: 0 6px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.8);
      color: inherit;
      font-size: 10px;
      line-height: 1;
    }
    .tray-popup-tab-panel {
      display: none;
    }
    .tray-popup-tab-panel.is-active {
      display: block;
    }
"""
    tab_js = """
    async function loadPopupCollectionPanel(panel) {
      if (!panel) {
        return;
      }
      const bucketKey = panel.getAttribute("data-popup-collection") || "";
      const loadedState = panel.getAttribute("data-popup-loaded") || "";
      if (!bucketKey || loadedState === "true" || loadedState === "loading") {
        return;
      }
      if (!(window.pywebview && window.pywebview.api && window.pywebview.api.get_popup_collection_html)) {
        panel.innerHTML = "<div class='tray-popup-empty'>세부 목록을 불러올 수 없습니다.</div>";
        panel.setAttribute("data-popup-loaded", "error");
        return;
      }

      panel.setAttribute("data-popup-loaded", "loading");
      panel.innerHTML = "<div class='tray-popup-empty'>불러오는 중...</div>";
      try {
        const html = await window.pywebview.api.get_popup_collection_html("todos", bucketKey);
        panel.innerHTML = html || "<div class='tray-popup-empty'>표시할 메일이 없습니다.</div>";
        panel.setAttribute("data-popup-loaded", "true");
        initPopupTabs(panel);
      } catch (error) {
        panel.innerHTML = "<div class='tray-popup-empty'>세부 목록을 불러오지 못했습니다.</div>";
        panel.setAttribute("data-popup-loaded", "error");
      }
    }

    function initPopupTabs(scope) {
      const rootNode = scope || document;
      const roots = Array.from(rootNode.querySelectorAll("[data-popup-tab-root]")).filter(
        (root) => root.getAttribute("data-popup-tab-initialized") !== "true",
      );
      roots.forEach((root) => {
        root.setAttribute("data-popup-tab-initialized", "true");
        const tabList = root.querySelector(":scope > .tray-popup-tab-list");
        const buttons = tabList ? Array.from(tabList.querySelectorAll(":scope > [data-popup-tab]")) : [];
        const panels = Array.from(root.querySelectorAll(":scope > [data-popup-tab-panel]"));
        if (!buttons.length || !panels.length) {
          return;
        }

        async function setActiveTab(tabKey) {
          let activePanel = null;
          buttons.forEach((button) => {
            const isActive = button.getAttribute("data-popup-tab") === tabKey;
            button.classList.toggle("is-active", isActive);
            button.setAttribute("aria-selected", isActive ? "true" : "false");
          });
          panels.forEach((panel) => {
            const isActive = panel.getAttribute("data-popup-tab-panel") === tabKey;
            if (isActive) {
              activePanel = panel;
            }
            panel.classList.toggle("is-active", isActive);
            panel.setAttribute("aria-hidden", isActive ? "false" : "true");
          });
          if (activePanel) {
            await loadPopupCollectionPanel(activePanel);
          }
        }

        buttons.forEach((button) => {
          button.addEventListener("click", () => {
            void setActiveTab(button.getAttribute("data-popup-tab") || "");
          });
        });

        const initialButton = buttons.find((button) => button.classList.contains("is-active")) || buttons[0];
        if (initialButton) {
          void setActiveTab(initialButton.getAttribute("data-popup-tab") || "");
        }
      });
    }

    initPopupTabs();
"""
    html = html.replace("  </style>", f"{tab_css}\n  </style>", 1)
    html = html.replace("  </script>", f"{tab_js}\n  </script>", 1)
    return html


DesktopApi._build_todo_popup_html = _desktop_api_build_todo_popup_html_v4
DesktopApi._wrap_popup_html = _desktop_api_wrap_popup_html_v5
