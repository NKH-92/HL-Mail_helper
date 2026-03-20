from __future__ import annotations

import sys
import time
import unittest
from datetime import datetime
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from app.core.security import GEMINI_API_KEY, HANLIM_API_KEY, MAIL_PASSWORD_KEY
from app.db.models import MailRecord
from app.ui.desktop_bridge import DesktopApi, TRAY_AUTO_SEND_POPUP, TRAY_TODO_POPUP
from app.ui.page_config import AUTO_SEND_PAGE, DASHBOARD_PAGE, HELP_PAGE, SETTINGS_PAGE


class DesktopBridgeTests(unittest.TestCase):
    class _FakeLogger:
        @staticmethod
        def exception(*args: object, **kwargs: object) -> None:
            del args, kwargs

    class _FakeConfig:
        def __init__(self, *, ready: bool) -> None:
            self._ready = ready
            self.user_email = "tester@example.com"
            self.user_display_name = "Tester"
            self.user_department = "SQA"
            self.user_job_title = "Engineer"
            self.mailbox = "INBOX"
            self.sync_days = 7
            self.sync_batch_size = 25
            self.sync_scan_limit = 250
            self.sync_interval_minutes = 60
            self.store_raw_body = False
            self.preview_max_chars = 300
            self.ai_provider = "gemini"
            self.ai_base_url = ""
            self.gemini_model = "gemini-2.5-flash"
            self.gemini_timeout_seconds = 30

        def is_ready(self) -> bool:
            return self._ready

    class _FakeConfigManager:
        def __init__(self, config: object) -> None:
            self._config = config

        def load(self) -> object:
            return self._config

    class _FakeSecretStore:
        def __init__(
            self,
            *,
            has_password: bool = False,
            has_api_key: bool = False,
            has_hanlim_api_key: bool = False,
        ) -> None:
            self._has_password = has_password
            self._has_api_key = has_api_key
            self._has_hanlim_api_key = has_hanlim_api_key

        def has_secret(self, key: str) -> bool:
            if key == MAIL_PASSWORD_KEY:
                return self._has_password
            if key == GEMINI_API_KEY:
                return self._has_api_key
            if key == HANLIM_API_KEY:
                return self._has_hanlim_api_key
            return False

    class _FakeMailRepository:
        def __init__(self, todos: list[object], *, classified_mails: list[MailRecord] | None = None) -> None:
            self._todos = todos
            self._classified_mails = classified_mails or []

        def list_open_my_action_items(self) -> list[object]:
            return list(self._todos)

        def list_completed_my_action_items(self, limit: int = 20) -> list[object]:
            del limit
            return []

        def list_classified_mails(self, limit: int = 200) -> list[MailRecord]:
            return list(self._classified_mails[:limit])

        @staticmethod
        def count_analysis_backlog() -> dict[str, int]:
            return {"pending": 0, "failed": 0}

    class _FakeTemplateService:
        def __init__(self, templates: list[object]) -> None:
            self._templates = templates

        def list_templates(self) -> list[object]:
            return list(self._templates)

    class _FakeSendService:
        @staticmethod
        def calculate_next_run(template: object) -> datetime | None:
            return getattr(template, "_next_run", None)

    class _FakeSchedulerManager:
        def __init__(self) -> None:
            self.started = True
            self.scheduler = SimpleNamespace(state=2)

        @staticmethod
        def run_mailbox_cycle(
            *,
            include_failed_analysis: bool,
            source: str,
            progress_callback=None,
        ) -> object:
            del include_failed_analysis, source
            if progress_callback is not None:
                progress_callback(
                    {
                        "stage": "complete",
                        "message": "동기화 완료",
                        "scanned_count": 2,
                        "fetched_count": 2,
                        "saved_count": 2,
                        "analysis_total": 2,
                        "analysis_completed": 2,
                        "analysis_success_count": 2,
                        "analysis_failure_count": 0,
                    }
                )
            return SimpleNamespace(
                scanned_count=2,
                fetched_count=2,
                saved_count=2,
                analysis_success_count=2,
                analysis_failure_count=0,
                deleted_old_count=0,
                limited_by_batch=False,
                analysis_warning=None,
            )

        @staticmethod
        def get_mailbox_status() -> dict[str, object]:
            return {
                "scheduler_state": "1",
                "interval_minutes": 60,
                "next_run_at": "2026-03-13 09:00:00",
                "last_finished_at": "2026-03-13 08:00:00",
                "last_result_summary": "manual: saved 2",
                "last_warning": "",
            }

    class _FakeAddressBookService:
        @staticmethod
        def resolve_display_name(email: str, fallback_name: str | None = None) -> str:
            return fallback_name or email or "-"

        @staticmethod
        def sync_user_profile(config_manager: object) -> object:
            return config_manager.load()

        @staticmethod
        def merge_config_profile(config: object) -> object:
            return config

        @staticmethod
        def list_contact_options() -> list[object]:
            return []

    @staticmethod
    def _make_mail(mail_id: int, *, final_category: int, subject: str, summary: str) -> MailRecord:
        category_map = {1: "direct_action", 2: "review_needed", 3: "reference_only"}
        return MailRecord(
            id=mail_id,
            message_id=f"msg-{mail_id}",
            subject=subject,
            normalized_subject=subject,
            thread_key=f"thread-{mail_id}",
            in_reply_to=None,
            references=[],
            sender_name="Sender",
            sender_email="sender@example.com",
            to_list=["tester@example.com"] if final_category == 1 else [],
            cc_list=["tester@example.com"] if final_category == 2 else [],
            received_at="2026-03-11 10:00:00",
            body_text=f"{subject} full body",
            raw_preview=f"{subject} preview",
            attachment_names=[],
            attachment_paths=[],
            category="ACT" if final_category in {1, 2} else "FYI",
            priority="medium",
            summary_short=summary,
            summary_long=[summary],
            due_date="2026-03-19 18:00:00" if final_category == 1 else None,
            my_action_required=final_category in {1, 2},
            my_action_status=category_map[final_category],
            ownership_reason=["test"],
            confidence=0.91,
            status="todo",
            analysis_status="success",
            analysis_error=None,
            created_at="2026-03-11 10:00:00",
            updated_at="2026-03-11 10:01:00",
            action_classification="ACTION_SELF" if final_category == 1 else "ACTION_SHARED",
            action_owner="me" if final_category == 1 else "other",
            action_types=["reply"] if final_category == 1 else ["review"] if final_category == 2 else ["none"],
            deadline_raw="2026-03-19 18:00:00" if final_category == 1 else None,
            evidence=["근거 문장"],
            analysis_reason="reason",
            suggested_task_title=summary if final_category == 1 else None,
            is_to_me=final_category == 1,
            is_cc_me=final_category == 2,
            recipient_role="TO" if final_category == 1 else "CC" if final_category == 2 else "NONE",
            is_system_sender=False,
            is_newsletter_like=False,
            sender_type="internal",
            rule_category=final_category,
            request_present=final_category in {1, 2},
            request_target="me" if final_category == 1 else "other" if final_category == 2 else "unknown",
            request_target_is_me=final_category == 1,
            urgency="high" if final_category == 1 else "medium",
            llm_category=final_category,
            final_category=final_category,
            correction_applied=False,
            correction_reason=None,
            conflict_type=None,
            model_name="gemini-2.5-flash",
            analyzed_at="2026-03-11 10:01:00",
            raw_llm_json="{}",
        )

    def _build_context(
        self,
        *,
        todos: list[object] | None = None,
        templates: list[object] | None = None,
        classified_mails: list[MailRecord] | None = None,
    ) -> object:
        return SimpleNamespace(
            mail_repository=self._FakeMailRepository(todos or [], classified_mails=classified_mails),
            template_service=self._FakeTemplateService(templates or []),
            send_service=self._FakeSendService(),
            scheduler_manager=self._FakeSchedulerManager(),
            address_book_service=self._FakeAddressBookService(),
            logger=self._FakeLogger(),
        )

    def _build_page_state_context(
        self,
        *,
        ready: bool,
        has_password: bool = False,
        has_api_key: bool = False,
        has_hanlim_api_key: bool = False,
        classified_mails: list[MailRecord] | None = None,
    ) -> object:
        config = self._FakeConfig(ready=ready)
        return SimpleNamespace(
            address_book_service=self._FakeAddressBookService(),
            config_manager=self._FakeConfigManager(config),
            secret_store=self._FakeSecretStore(
                has_password=has_password,
                has_api_key=has_api_key,
                has_hanlim_api_key=has_hanlim_api_key,
            ),
            mail_repository=self._FakeMailRepository([], classified_mails=classified_mails),
            template_service=self._FakeTemplateService([]),
            mail_template_service=SimpleNamespace(list_templates=lambda: []),
            send_service=self._FakeSendService(),
            scheduler_manager=self._FakeSchedulerManager(),
            sync_service=SimpleNamespace(
                get_status_snapshot=lambda: {
                    "mailbox": config.mailbox,
                    "sync_days": config.sync_days,
                    "last_sync_at": "2026-03-13 08:00:00",
                    "backfill_active": False,
                    "backfill_completed_at": "2026-03-12 19:00:00",
                    "cursor_before_uid": None,
                    "sync_warning": "",
                }
            ),
            mailbox_service=SimpleNamespace(get_analysis_warning=lambda: ""),
            send_log_repository=SimpleNamespace(list_recent=lambda: []),
            logger=self._FakeLogger(),
        )

    def test_todo_popup_html_is_localized_and_compact(self) -> None:
        html = DesktopApi(
            self._build_context(
                classified_mails=[
                    self._make_mail(1, final_category=1, subject="견적 확인", summary="금일 회신 필요"),
                ]
            )
        ).get_popup_html(TRAY_TODO_POPUP)

        self.assertIn('lang="ko"', html)
        self.assertIn("MailAI | 메일 분류", html)
        self.assertIn("메일 분류", html)
        self.assertIn("내가해야할일", html)
        self.assertIn("보낸 사람", html)
        self.assertIn("액션 회신", html)
        self.assertIn("새로고침", html)
        self.assertIn("font-size: 13.5px;", html)
        self.assertIn("#fdfcfb", html)
        self.assertIn('font-family: "Segoe UI Variable Text"', html)
        self.assertIn("grid-template-columns: repeat(4, minmax(0, 1fr));", html)
        self.assertIn("data-popup-tab-root", html)
        self.assertIn("data-popup-tab='category_1'", html)
        self.assertIn("data-popup-tab='category_2'", html)
        self.assertIn("data-popup-tab='category_3'", html)
        self.assertIn("function initPopupTabs()", html)
        self.assertNotIn("Open Tasks", html)

    def test_todo_popup_html_separates_cards_by_category_tabs(self) -> None:
        html = DesktopApi(
            self._build_context(
                classified_mails=[
                    self._make_mail(1, final_category=1, subject="Action mail", summary="Action summary"),
                    self._make_mail(2, final_category=2, subject="Review mail", summary="Review summary"),
                    self._make_mail(3, final_category=3, subject="Reference mail", summary="Reference summary"),
                ]
            )
        ).get_popup_html(TRAY_TODO_POPUP)

        category_1_panel = html.split("data-popup-tab-panel='category_1'", 1)[1].split(
            "data-popup-tab-panel='category_2'",
            1,
        )[0]
        category_2_panel = html.split("data-popup-tab-panel='category_2'", 1)[1].split(
            "data-popup-tab-panel='category_3'",
            1,
        )[0]
        category_3_panel = html.split("data-popup-tab-panel='category_3'", 1)[1]

        self.assertIn("Action summary", category_1_panel)
        self.assertNotIn("Review summary", category_1_panel)
        self.assertNotIn("Reference summary", category_1_panel)
        self.assertIn("Review summary", category_2_panel)
        self.assertNotIn("Action summary", category_2_panel)
        self.assertNotIn("Reference summary", category_2_panel)
        self.assertIn("Reference summary", category_3_panel)
        self.assertNotIn("Action summary", category_3_panel)
        self.assertNotIn("Review summary", category_3_panel)

    def test_not_ready_page_state_allows_help_but_redirects_dashboard(self) -> None:
        api = DesktopApi(self._build_page_state_context(ready=False, has_password=False))

        help_state = api._build_page_state(HELP_PAGE)
        dashboard_state = api._build_page_state(DASHBOARD_PAGE)

        self.assertEqual(help_state["page"], HELP_PAGE)
        self.assertEqual(help_state["page_id"], "help")
        self.assertEqual(dashboard_state["page"], SETTINGS_PAGE)
        self.assertEqual(dashboard_state["page_id"], "settings")

    def test_dashboard_page_state_uses_classified_mail_tabs_and_selection(self) -> None:
        mails = [
            self._make_mail(1, final_category=1, subject="Action mail", summary="회신 필요"),
            self._make_mail(2, final_category=2, subject="Review mail", summary="검토 필요"),
            self._make_mail(3, final_category=3, subject="FYI mail", summary="참고용"),
        ]
        context = self._build_page_state_context(ready=True, has_password=True, classified_mails=mails)
        api = DesktopApi(context)

        state = api.dispatch(
            {
                "page": DASHBOARD_PAGE,
                "action": "refresh_dashboard",
                "client_state": {
                    "page": DASHBOARD_PAGE,
                    "dashboard_mail_tab": "category_2",
                    "dashboard_mail_view": "detail",
                    "selected_mail_id": 2,
                },
                "client_state_version": 1,
            }
        )

        self.assertEqual(state["page"], DASHBOARD_PAGE)
        self.assertEqual(state["dashboard_mail_tab"], "category_2")
        self.assertEqual(state["dashboard_mail_view"], "detail")
        self.assertEqual(state["selected_mail_id"], 2)
        self.assertEqual(state["dashboard_mail_category_counts"]["category_1"], 1)
        self.assertEqual(state["dashboard_mail_category_counts"]["category_2"], 1)
        self.assertEqual(state["dashboard_mail_category_counts"]["category_3"], 1)
        self.assertEqual(len(state["classified_mails"]), 3)
        self.assertEqual(state["classified_mails"][1]["final_category"], 2)
        self.assertEqual(state["classified_mails"][1]["body_text"], "Review mail full body")
        self.assertEqual(state["sync_status"]["interval_minutes"], 60)

    def test_stale_client_state_version_does_not_override_newer_dashboard_state(self) -> None:
        mails = [
            self._make_mail(1, final_category=1, subject="Action mail", summary="회신 필요"),
            self._make_mail(2, final_category=2, subject="Review mail", summary="검토 필요"),
        ]
        context = self._build_page_state_context(ready=True, has_password=True, classified_mails=mails)
        api = DesktopApi(context)

        api.dispatch(
            {
                "page": DASHBOARD_PAGE,
                "action": "refresh_dashboard",
                "client_state": {
                    "page": DASHBOARD_PAGE,
                    "dashboard_mail_tab": "category_2",
                    "dashboard_mail_view": "detail",
                    "selected_mail_id": 2,
                },
                "client_state_version": 1,
            }
        )
        latest_state = api.dispatch(
            {
                "page": DASHBOARD_PAGE,
                "action": "refresh_dashboard",
                "client_state": {
                    "page": DASHBOARD_PAGE,
                    "dashboard_mail_tab": "category_1",
                    "dashboard_mail_view": "list",
                    "selected_mail_id": 1,
                },
                "client_state_version": 2,
            }
        )
        stale_state = api.dispatch(
            {
                "page": DASHBOARD_PAGE,
                "action": "refresh_dashboard",
                "client_state": {
                    "page": DASHBOARD_PAGE,
                    "dashboard_mail_tab": "category_2",
                    "dashboard_mail_view": "detail",
                    "selected_mail_id": 2,
                },
                "client_state_version": 1,
            }
        )

        self.assertEqual(latest_state["dashboard_mail_tab"], "category_1")
        self.assertEqual(latest_state["dashboard_mail_view"], "list")
        self.assertEqual(latest_state["selected_mail_id"], 1)
        self.assertEqual(stale_state["dashboard_mail_tab"], "category_1")
        self.assertEqual(stale_state["dashboard_mail_view"], "list")
        self.assertEqual(stale_state["selected_mail_id"], 1)
        self.assertEqual(stale_state["client_state_version"], 2)

    def test_sync_mail_dispatch_runs_in_background_and_updates_sync_progress(self) -> None:
        progress_started = Event()
        release_cycle = Event()
        scheduler_calls: list[tuple[bool, str]] = []

        class _BlockingSchedulerManager(self._FakeSchedulerManager):
            @staticmethod
            def run_mailbox_cycle(
                *,
                include_failed_analysis: bool,
                source: str,
                progress_callback=None,
            ) -> object:
                scheduler_calls.append((include_failed_analysis, source))
                if progress_callback is not None:
                    progress_callback(
                        {
                            "stage": "analyzing",
                            "message": "AI 분석 중",
                            "scanned_count": 6,
                            "fetched_count": 3,
                            "saved_count": 3,
                            "analysis_total": 3,
                            "analysis_completed": 1,
                            "analysis_success_count": 1,
                            "analysis_failure_count": 0,
                        }
                    )
                progress_started.set()
                release_cycle.wait(1.0)
                return SimpleNamespace(
                    scanned_count=6,
                    fetched_count=3,
                    saved_count=3,
                    analysis_success_count=3,
                    analysis_failure_count=0,
                    deleted_old_count=0,
                    limited_by_batch=False,
                    analysis_warning=None,
                )

        context = self._build_page_state_context(
            ready=True,
            has_password=True,
            classified_mails=[self._make_mail(1, final_category=1, subject="Action mail", summary="회신 필요")],
        )
        context.scheduler_manager = _BlockingSchedulerManager()
        api = DesktopApi(context)

        initial_state = api.dispatch(
            {
                "page": DASHBOARD_PAGE,
                "action": "sync_mail",
                "client_state": {"page": DASHBOARD_PAGE},
            }
        )

        self.assertTrue(progress_started.wait(0.5))
        running_state = api.dispatch(
            {
                "page": DASHBOARD_PAGE,
                "action": "refresh_dashboard",
                "client_state": {"page": DASHBOARD_PAGE},
            }
        )

        self.assertEqual(scheduler_calls, [(True, "manual")])
        self.assertTrue(initial_state["sync_progress"]["running"])
        self.assertTrue(running_state["sync_progress"]["running"])
        self.assertEqual(running_state["sync_progress"]["stage"], "analyzing")
        self.assertEqual(running_state["sync_progress"]["saved_count"], 3)
        self.assertEqual(running_state["sync_progress"]["analysis_completed"], 1)
        self.assertEqual(running_state["sync_progress"]["analysis_total"], 3)

        release_cycle.set()
        deadline = time.time() + 1.0
        while api._sync_thread is not None and time.time() < deadline:
            time.sleep(0.02)

        completed_state = api.dispatch(
            {
                "page": DASHBOARD_PAGE,
                "action": "refresh_dashboard",
                "client_state": {"page": DASHBOARD_PAGE},
            }
        )

        self.assertFalse(completed_state["sync_progress"]["running"])
        self.assertEqual(completed_state["sync_progress"]["stage"], "complete")
        self.assertEqual(completed_state["sync_progress"]["analysis_completed"], 3)
        self.assertEqual(completed_state["sync_progress"]["analysis_total"], 3)

    def test_address_book_contacts_are_only_loaded_for_autosend_page(self) -> None:
        class _AddressBookWithContacts(self._FakeAddressBookService):
            @staticmethod
            def list_contact_options() -> list[object]:
                return [{"email": "person@example.com", "label": "Person <person@example.com>"}]

        context = self._build_page_state_context(ready=True, has_password=True)
        context.address_book_service = _AddressBookWithContacts()
        api = DesktopApi(context)

        dashboard_state = api._build_page_state(DASHBOARD_PAGE)
        autosend_state = api._build_page_state(AUTO_SEND_PAGE)

        self.assertEqual(dashboard_state["address_book_contacts"], [])
        self.assertEqual(len(autosend_state["address_book_contacts"]), 1)
        self.assertEqual(autosend_state["address_book_contacts"][0]["email"], "person@example.com")

    def test_settings_page_state_includes_ai_provider_fields_and_both_secret_flags(self) -> None:
        context = self._build_page_state_context(
            ready=True,
            has_password=True,
            has_api_key=False,
            has_hanlim_api_key=True,
        )
        api = DesktopApi(context)

        settings_state = api._build_page_state(SETTINGS_PAGE)

        self.assertEqual(settings_state["config"]["ai_provider"], "gemini")
        self.assertEqual(settings_state["config"]["ai_base_url"], "")
        self.assertFalse(settings_state["secrets"]["api_key"])
        self.assertTrue(settings_state["secrets"]["hanlim_api_key"])

    def test_autosend_popup_html_is_localized(self) -> None:
        template = SimpleNamespace(
            template_name="주간 보고",
            enabled=True,
            to_list=["a@example.com", "b@example.com"],
            cc_list=["c@example.com"],
            subject="주간 보고 메일",
            repeat_type="daily",
            first_send_at="2026-03-12 08:00:00",
            id=7,
            _next_run=datetime(2026, 3, 13, 8, 0),
        )

        html = DesktopApi(self._build_context(templates=[template])).get_popup_html(TRAY_AUTO_SEND_POPUP)

        self.assertIn("MailAI | 자동발송", html)
        self.assertIn("활성 템플릿", html)
        self.assertIn("예약 건수", html)
        self.assertIn("다음 실행", html)
        self.assertIn("수신 3명 / 반복 매일", html)
        self.assertIn("일시중지", html)
        self.assertNotIn("Scheduled Mail", html)

    def test_pick_attachment_files_uses_main_window_file_dialog(self) -> None:
        class _FakeWindow:
            def __init__(self) -> None:
                self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

            def create_file_dialog(self, *args: object, **kwargs: object) -> list[str]:
                self.calls.append((args, kwargs))
                return [r"C:\docs\proposal.pdf", r"D:\files\quote.xlsx"]

        api = DesktopApi(self._build_context())
        fake_window = _FakeWindow()
        api.bind_main_window(fake_window)

        with patch.dict(sys.modules, {"webview": SimpleNamespace(OPEN_DIALOG="open-dialog")}):
            selected = api.pick_attachment_files()

        self.assertEqual(selected, [r"C:\docs\proposal.pdf", r"D:\files\quote.xlsx"])
        self.assertEqual(fake_window.calls[0][0][0], "open-dialog")
        self.assertTrue(fake_window.calls[0][1]["allow_multiple"])


if __name__ == "__main__":
    unittest.main()
