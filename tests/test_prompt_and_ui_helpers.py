from __future__ import annotations

import re
import tempfile
import time
import unittest
from datetime import date, datetime
from pathlib import Path

from app.ai.prompts import PromptManager
from app.core.config_manager import AI_PROVIDER_HANLIM, AppConfig
from app.ui import ui_state_helpers
from app.ui.modern_dashboard import _build_completed_task_dicts
from app.db.models import ActionItemRecord, MailRecord, ThreadOverview, TodoItemRecord
from app.ui import modern_dashboard
from app.ui.modern_dashboard import _build_mail_template_from_payload
from app.ui.modern_dashboard import _build_send_registration_from_payload
from app.ui.modern_dashboard import _build_mailbox_test_submission
from app.ui.modern_dashboard import _build_settings_submission
from app.ui.modern_dashboard import _build_dashboard_task_dicts
from app.ui.modern_dashboard import _build_priority_thread_dicts
from app.ui.modern_dashboard import _consume_component_action_id


class PromptAndUiHelperTests(unittest.TestCase):
    class _FakeAddressBookService:
        @staticmethod
        def resolve_recipient_tokens(raw_value: str) -> list[str]:
            return [item.strip() for item in raw_value.split(",") if item.strip()]

        @staticmethod
        def resolve_display_name(email: str, fallback_name: str | None = None) -> str:
            return fallback_name or email or "-"

    class _FakeMailRepository:
        def __init__(
            self,
            mails_by_thread: dict[str, list[MailRecord]],
            actions_by_thread: dict[str, list[ActionItemRecord]],
        ) -> None:
            self._mails_by_thread = mails_by_thread
            self._actions_by_thread = actions_by_thread

        def list_thread_mails_by_key(self, thread_key: str, limit: int = 10) -> list[MailRecord]:
            return self._mails_by_thread.get(thread_key, [])[:limit]

        def list_thread_mails_by_keys(
            self,
            thread_keys: list[str],
            *,
            limit_per_thread: int = 10,
        ) -> dict[str, list[MailRecord]]:
            return {
                thread_key: self._mails_by_thread.get(thread_key, [])[:limit_per_thread]
                for thread_key in thread_keys
            }

        def list_thread_action_items(self, thread_key: str, scope: str | None = None) -> list[ActionItemRecord]:
            actions = self._actions_by_thread.get(thread_key, [])
            if scope is None:
                return actions
            return [action for action in actions if action.scope == scope]

        def list_thread_action_items_by_keys(self, thread_keys: list[str]) -> dict[str, list[ActionItemRecord]]:
            return {thread_key: list(self._actions_by_thread.get(thread_key, [])) for thread_key in thread_keys}

    @staticmethod
    def _make_thread(
        thread_key: str,
        *,
        follow_up_status: str = "tracked",
        needs_review: bool = False,
    ) -> ThreadOverview:
        return ThreadOverview(
            thread_key=thread_key,
            latest_mail_id=1,
            thread_subject=f"Subject {thread_key}",
            latest_received_at="2026-03-11 10:00:00",
            latest_sender_name="Sender",
            latest_sender_email="sender@example.com",
            latest_summary="Latest summary",
            overall_summary="Overall summary",
            changed_since_last="Changed since last",
            current_conclusion="Current conclusion",
            follow_up_status=follow_up_status,
            follow_up_label=follow_up_status,
            follow_up_detail="Follow up detail",
            due_date=None,
            priority="medium",
            category="ACT",
            latest_mail_status="todo",
            latest_action_status="direct_action",
            mail_count=1,
            participant_count=1,
            participants=["Sender"],
            open_action_count=1,
            importance_score=10,
            urgency_score=5,
            reply_score=3,
            approval_score=0,
            today_score=0,
            priority_score=18,
            priority_reasons=["Recent thread"],
            needs_review=needs_review,
            has_failed_analysis=False,
        )

    def test_prompt_manager_uses_fallback_directory(self) -> None:
        with tempfile.TemporaryDirectory() as primary_dir, tempfile.TemporaryDirectory() as fallback_dir:
            fallback_path = Path(fallback_dir)
            (fallback_path / "classify_prompt.txt").write_text("fallback prompt", encoding="utf-8")

            manager = PromptManager(Path(primary_dir), fallback_prompt_dir=fallback_path)

            self.assertEqual(manager._read("classify_prompt.txt"), "fallback prompt")

    def test_prompt_manager_uses_builtin_defaults_when_files_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as primary_dir:
            manager = PromptManager(Path(primary_dir))

            prompt = manager.build_system_prompt()

            self.assertIn("enterprise-grade email action classification engine", prompt)
            self.assertIn("Use only these enum values:", prompt)
            self.assertIn("Determine whether the request is directed to:", prompt)
            self.assertIn("summary must be written in Korean by default.", prompt)
            self.assertIn("공정 밸리데이션(Process Validation)", prompt)

    def test_prompt_manager_includes_korean_summary_policy_in_validation_prompt(self) -> None:
        manager = PromptManager(Path(tempfile.gettempdir()))

        prompt = manager.build_validation_system_prompt()

        self.assertIn("corrected_result.summary must be written in Korean by default.", prompt)
        self.assertIn("공정 밸리데이션(Process Validation)", prompt)

    def test_prompt_manager_rebuilds_cached_system_prompt_when_source_changes(self) -> None:
        with tempfile.TemporaryDirectory() as prompt_dir:
            prompt_path = Path(prompt_dir)
            (prompt_path / "classify_prompt.txt").write_text("classify v1", encoding="utf-8")
            (prompt_path / "summarize_prompt.txt").write_text("summarize v1", encoding="utf-8")
            (prompt_path / "ownership_prompt.txt").write_text("ownership v1", encoding="utf-8")

            manager = PromptManager(prompt_path)

            initial_prompt = manager.build_system_prompt()
            cached_prompt = manager.build_system_prompt()

            self.assertEqual(initial_prompt, cached_prompt)
            self.assertIn("ownership v1", initial_prompt)

            time.sleep(0.02)
            (prompt_path / "ownership_prompt.txt").write_text("ownership v2", encoding="utf-8")

            rebuilt_prompt = manager.build_system_prompt()

            self.assertIn("ownership v2", rebuilt_prompt)
            self.assertNotEqual(initial_prompt, rebuilt_prompt)

    def test_prompt_manager_builds_sectioned_user_prompt(self) -> None:
        manager = PromptManager(Path(tempfile.gettempdir()))
        mail = MailRecord(
            id=1,
            message_id="msg-1",
            subject="Review request",
            normalized_subject="Review request",
            thread_key="thread-1",
            in_reply_to=None,
            references=[],
            sender_name="Sender",
            sender_email="sender@example.com",
            to_list=["user@example.com"],
            cc_list=["team@example.com"],
            received_at="2026-03-19 09:00:00",
            body_text="Please review the attached file and reply today.",
            raw_preview="Please review the attached file and reply today.",
            attachment_names=["report.xlsx"],
            attachment_paths=[],
            category="ACT",
            priority="high",
            summary_short=None,
            summary_long=[],
            due_date=None,
            my_action_required=True,
            my_action_status="direct_action",
            ownership_reason=[],
            confidence=None,
            status="todo",
            analysis_status="pending",
            analysis_error=None,
            created_at="2026-03-19 09:00:00",
            updated_at="2026-03-19 09:00:00",
        )

        prompt = manager.build_user_prompt(
            mail,
            thread_summary="- 2026-03-18 | sender@example.com | Prior note | Please proceed.",
            current_user={
                "email": "user@example.com",
                "display_name": "남광현",
                "job_title": "선임",
            },
            rule_context={
                "is_to_me": True,
                "is_cc_me": False,
                "recipient_role": "TO",
                "sender_type": "internal",
            },
        )

        self.assertIn("[target_user]", prompt)
        self.assertIn("[routing_facts]", prompt)
        self.assertIn("[sender]", prompt)
        self.assertIn("[email]", prompt)
        self.assertIn("[attachments]", prompt)
        self.assertIn("[thread_context]", prompt)
        self.assertIn("- name: 남광현", prompt)
        self.assertIn("- sender_type: internal", prompt)
        self.assertIn("- report.xlsx", prompt)
        self.assertIn("Return JSON only.", prompt)

    def test_prompt_manager_builds_validation_prompt_with_candidate_json(self) -> None:
        manager = PromptManager(Path(tempfile.gettempdir()))
        mail = MailRecord(
            id=2,
            message_id="msg-2",
            subject="FYI",
            normalized_subject="FYI",
            thread_key="thread-2",
            in_reply_to=None,
            references=[],
            sender_name="Sender",
            sender_email="sender@example.com",
            to_list=["user@example.com"],
            cc_list=[],
            received_at="2026-03-19 10:00:00",
            body_text="Sharing the latest status only.",
            raw_preview="Sharing the latest status only.",
            attachment_names=[],
            attachment_paths=[],
            category="FYI",
            priority="none",
            summary_short=None,
            summary_long=[],
            due_date=None,
            my_action_required=False,
            my_action_status="reference_only",
            ownership_reason=[],
            confidence=None,
            status="todo",
            analysis_status="pending",
            analysis_error=None,
            created_at="2026-03-19 10:00:00",
            updated_at="2026-03-19 10:00:00",
        )

        prompt = manager.build_validation_user_prompt(
            mail=mail,
            thread_summary="",
            current_user={
                "email": "user@example.com",
                "display_name": "Dana Lee",
                "job_title": "Lead",
            },
            rule_context={"is_to_me": True, "is_cc_me": False, "recipient_role": "TO"},
            candidate_result={"request_present": False, "final_category": 3},
        )

        self.assertIn("Validate the candidate classification result.", prompt)
        self.assertIn("[target_user]", prompt)
        self.assertIn("[sender]", prompt)
        self.assertIn("[attachments]", prompt)
        self.assertIn("[candidate_result]", prompt)
        self.assertIn('"final_category": 3', prompt)

    def test_prompt_manager_truncates_long_body_with_head_and_tail(self) -> None:
        manager = PromptManager(Path(tempfile.gettempdir()))
        head = "HEAD " * 80
        tail = "TAIL " * 80
        long_body = f"{head}{'middle ' * 400}{tail}"
        mail = MailRecord(
            id=3,
            message_id="msg-3",
            subject="Long body",
            normalized_subject="Long body",
            thread_key="thread-3",
            in_reply_to=None,
            references=[],
            sender_name="Sender",
            sender_email="sender@example.com",
            to_list=["user@example.com"],
            cc_list=[],
            received_at="2026-03-19 11:00:00",
            body_text=long_body,
            raw_preview=long_body,
            attachment_names=[],
            attachment_paths=[],
            category="ACT",
            priority="medium",
            summary_short=None,
            summary_long=[],
            due_date=None,
            my_action_required=True,
            my_action_status="direct_action",
            ownership_reason=[],
            confidence=None,
            status="todo",
            analysis_status="pending",
            analysis_error=None,
            created_at="2026-03-19 11:00:00",
            updated_at="2026-03-19 11:00:00",
        )

        prompt = manager.build_user_prompt(
            mail,
            body_char_limit=400,
            current_user={"email": "user@example.com"},
            rule_context={},
        )

        self.assertIn("[... truncated ...]", prompt)
        self.assertIn("HEAD HEAD", prompt)
        self.assertIn("TAIL TAIL", prompt)

    def test_component_action_id_is_consumed_once(self) -> None:
        state: dict[str, object] = {}
        result = {"action": "save_send_registration", "client_action_id": "action-123"}

        self.assertFalse(_consume_component_action_id(state, result))
        self.assertTrue(_consume_component_action_id(state, result))

    def test_modern_dashboard_reuses_shared_ui_state_helpers(self) -> None:
        self.assertIs(modern_dashboard._build_dashboard_task_dicts, ui_state_helpers.build_dashboard_task_dicts)
        self.assertIs(modern_dashboard._build_completed_task_dicts, ui_state_helpers.build_completed_task_dicts)
        self.assertIs(modern_dashboard._build_priority_thread_dicts, ui_state_helpers.build_priority_thread_dicts)
        self.assertIs(modern_dashboard._build_settings_submission, ui_state_helpers.build_settings_submission)
        self.assertIs(modern_dashboard._build_mailbox_test_submission, ui_state_helpers.build_mailbox_test_submission)
        self.assertIs(modern_dashboard._build_mail_template_from_payload, ui_state_helpers.build_mail_template_from_payload)
        self.assertIs(
            modern_dashboard._build_send_registration_from_payload,
            ui_state_helpers.build_send_registration_from_payload,
        )

    def test_missing_component_action_id_does_not_dedupe(self) -> None:
        state: dict[str, object] = {}
        result = {"action": "save_send_registration"}

        self.assertFalse(_consume_component_action_id(state, result))
        self.assertFalse(_consume_component_action_id(state, result))

    def test_read_log_tail_reads_only_the_requested_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "app.log"
            contents = "0123456789" * 4000
            log_path.write_text(contents, encoding="utf-8")

            tail = ui_state_helpers.read_log_tail(log_path, limit=25)

            self.assertEqual(tail, contents[-25:])

    def test_dashboard_task_builder_keeps_open_items_without_due_date(self) -> None:
        task = TodoItemRecord(
            id=7,
            mail_id=3,
            action_text="Follow up",
            due_date=None,
            note="",
            created_at="2026-03-09 10:00:00",
            completed_at=None,
            mail_subject="Status update",
            sender_email="sender@example.com",
            received_at="2026-03-09 09:00:00",
            mail_status="todo",
        )

        cards = _build_dashboard_task_dicts([task])

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["id"], 7)
        self.assertEqual(cards[0]["source"], "Status update")
        self.assertEqual(cards[0]["due_date"], "")
        self.assertFalse(cards[0]["is_urgent"])

    def test_dashboard_task_builder_marks_due_today_as_urgent(self) -> None:
        today = date.today().isoformat()
        task = TodoItemRecord(
            id=8,
            mail_id=4,
            action_text="Urgent review",
            due_date=f"{today} 18:00:00",
            note="",
            created_at="2026-03-09 10:00:00",
            completed_at=None,
            mail_subject="QA review",
            sender_email="sender@example.com",
            received_at="2026-03-09 09:00:00",
            mail_status="todo",
        )

        cards = _build_dashboard_task_dicts([task])

        self.assertEqual(cards[0]["due_date"], today)
        self.assertTrue(cards[0]["is_urgent"])

    def test_completed_task_builder_formats_history_fields(self) -> None:
        task = TodoItemRecord(
            id=9,
            mail_id=5,
            action_text="Send reply",
            due_date="2026-03-09 18:00:00",
            note="Sent to vendor",
            created_at="2026-03-09 10:00:00",
            completed_at="2026-03-10 09:45:00",
            mail_subject="Vendor follow-up",
            sender_email="sender@example.com",
            received_at="2026-03-09 09:00:00",
            mail_status="done",
        )

        cards = _build_completed_task_dicts([task])

        self.assertEqual(cards[0]["id"], 9)
        self.assertEqual(cards[0]["source"], "Vendor follow-up")
        self.assertEqual(cards[0]["due_date"], "2026-03-09")
        self.assertEqual(cards[0]["completed_at"], "2026-03-10 09:45")
        self.assertEqual(cards[0]["note"], "Sent to vendor")

    def test_priority_thread_builder_includes_thread_timeline_and_actions(self) -> None:
        thread = ThreadOverview(
            thread_key="thread-1",
            latest_mail_id=11,
            thread_subject="Vendor Quote",
            latest_received_at="2026-03-11 10:00:00",
            latest_sender_name="Vendor",
            latest_sender_email="vendor@example.com",
            latest_summary="Vendor asked for the revised quote.",
            overall_summary="Need to revise and send the quote.",
            changed_since_last="Deadline moved to 2026-03-11 17:00.",
            current_conclusion="Send the revised quote today.",
            follow_up_status="reply_needed",
            follow_up_label="Reply needed",
            follow_up_detail="Send the revised quote before 17:00.",
            due_date="2026-03-11 17:00:00",
            priority="high",
            category="ACT",
            latest_mail_status="todo",
            latest_action_status="direct_action",
            mail_count=2,
            participant_count=2,
            participants=["Vendor", "Me"],
            open_action_count=1,
            importance_score=40,
            urgency_score=28,
            reply_score=20,
            approval_score=0,
            today_score=15,
            priority_score=103,
            priority_reasons=["Due today", "Reply needed"],
            needs_review=False,
            has_failed_analysis=False,
        )
        mail = MailRecord(
            id=11,
            message_id="msg-11",
            subject="Re: Vendor Quote",
            normalized_subject="Vendor Quote",
            thread_key="thread-1",
            in_reply_to="msg-10",
            references=["msg-10"],
            sender_name="Vendor",
            sender_email="vendor@example.com",
            to_list=["user@example.com"],
            cc_list=[],
            received_at="2026-03-11 10:00:00",
            body_text="Please send the revised quote before 5 PM.",
            raw_preview="Please send the revised quote before 5 PM.",
            attachment_names=["quote.xlsx"],
            attachment_paths=["templates/quote.xlsx"],
            category="ACT",
            priority="high",
            summary_short="Vendor asked for the revised quote.",
            summary_long=["Vendor follow-up"],
            due_date="2026-03-11 17:00:00",
            my_action_required=True,
            my_action_status="direct_action",
            ownership_reason=["Direct request"],
            confidence=0.93,
            status="todo",
            analysis_status="success",
            analysis_error=None,
            created_at="2026-03-11 10:00:00",
            updated_at="2026-03-11 10:01:00",
            action_classification="ACTION_SELF",
            action_owner="me",
            action_types=["reply"],
            deadline_raw="today 5 PM",
            evidence=["Please send the revised quote before 5 PM."],
            analysis_reason="Direct request to send a revised quote.",
            suggested_task_title="Send revised quote",
        )
        my_action = ActionItemRecord(
            id=21,
            mail_id=11,
            scope="my",
            action_text="Send revised quote",
            due_date="2026-03-11 17:00:00",
            owner="user@example.com",
            note="",
            done_flag=False,
            completed_at=None,
            created_at="2026-03-11 10:02:00",
            updated_at="2026-03-11 10:02:00",
        )
        mail_action = ActionItemRecord(
            id=22,
            mail_id=11,
            scope="mail",
            action_text="Track vendor response",
            due_date=None,
            owner=None,
            note="",
            done_flag=False,
            completed_at=None,
            created_at="2026-03-11 10:02:00",
            updated_at="2026-03-11 10:02:00",
        )

        cards = _build_priority_thread_dicts(
            [thread],
            mail_repository=self._FakeMailRepository({"thread-1": [mail]}, {"thread-1": [my_action, mail_action]}),
            address_book_service=self._FakeAddressBookService(),
            current_user_email="user@example.com",
        )

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["thread_key"], "thread-1")
        self.assertEqual(cards[0]["priority_score"], 103)
        self.assertEqual(cards[0]["priority_reasons"], ["Due today", "Reply needed"])
        self.assertEqual(cards[0]["deadline_raw"], "today 5 PM")
        self.assertEqual(cards[0]["timeline"][0]["direction"], "inbound")
        self.assertEqual(cards[0]["timeline"][0]["due_date"], "2026-03-11 17:00")
        self.assertEqual(cards[0]["timeline"][0]["deadline_raw"], "today 5 PM")
        self.assertEqual(cards[0]["timeline"][0]["attachments"], ["quote.xlsx"])
        self.assertEqual(cards[0]["timeline"][0]["to_list"], ["user@example.com"])
        self.assertEqual(cards[0]["latest_mail"]["sender_email"], "vendor@example.com")
        self.assertEqual(cards[0]["my_actions"][0]["text"], "Send revised quote")
        self.assertEqual(cards[0]["mail_actions"][0]["text"], "Track vendor response")

    def test_priority_thread_builder_can_emit_compact_dashboard_payload(self) -> None:
        thread = self._make_thread("thread-compact", follow_up_status="reply_needed")
        my_action = ActionItemRecord(
            id=31,
            mail_id=11,
            scope="my",
            action_text="Send revised quote",
            due_date="2026-03-11 17:00:00",
            owner="user@example.com",
            note="",
            done_flag=False,
            completed_at=None,
            created_at="2026-03-11 10:02:00",
            updated_at="2026-03-11 10:02:00",
        )
        cards = _build_priority_thread_dicts(
            [thread],
            mail_repository=self._FakeMailRepository({}, {"thread-compact": [my_action]}),
            address_book_service=self._FakeAddressBookService(),
            current_user_email="user@example.com",
            include_details=False,
        )

        self.assertEqual(cards[0]["thread_key"], "thread-compact")
        self.assertEqual(cards[0]["my_actions"], [{"id": 31, "text": "Send revised quote"}])
        self.assertNotIn("timeline", cards[0])
        self.assertNotIn("latest_mail", cards[0])
        self.assertNotIn("mail_actions", cards[0])

    def test_dashboard_thread_page_state_filters_and_paginates(self) -> None:
        reply_threads = [self._make_thread(f"reply-{index:02d}", follow_up_status="reply_needed") for index in range(1, 13)]
        waiting_threads = [
            self._make_thread(f"waiting-{index:02d}", follow_up_status="waiting_for_reply")
            for index in range(1, 4)
        ]

        page_threads, pagination, counts = ui_state_helpers.build_dashboard_thread_page_state(
            reply_threads + waiting_threads,
            filter_key="reply",
            page=2,
            page_size=10,
        )

        self.assertEqual([thread.thread_key for thread in page_threads], ["reply-11", "reply-12"])
        self.assertEqual(pagination["filter"], "reply")
        self.assertEqual(pagination["page"], 2)
        self.assertEqual(pagination["total_items"], 12)
        self.assertEqual(pagination["total_pages"], 2)
        self.assertEqual(pagination["start_item"], 11)
        self.assertEqual(pagination["end_item"], 12)
        self.assertEqual(counts["all"], 15)
        self.assertEqual(counts["reply"], 12)
        self.assertEqual(counts["waiting"], 3)

    def test_resolve_dashboard_mail_tab_for_counts_falls_back_to_first_non_empty_tab(self) -> None:
        resolved = ui_state_helpers.resolve_dashboard_mail_tab_for_counts(
            "category_1",
            {"category_1": 0, "category_2": 3, "category_3": 1},
        )

        self.assertEqual(resolved, "category_2")

    def test_settings_submission_preserves_hidden_advanced_values(self) -> None:
        current = AppConfig(
            user_email="saved@example.com",
            mailbox="INBOX",
            sync_batch_size=80,
            sync_scan_limit=800,
            sync_interval_minutes=45,
            store_raw_body=True,
            preview_max_chars=45000,
            ai_provider="gemini",
            gemini_timeout_seconds=120,
        )

        next_config, password, api_key, hanlim_api_key = _build_settings_submission(
            current,
            {
                "config": {
                    "user_email": "draft@example.com",
                    "mailbox": "QA",
                    "sync_days": "14",
                    "sync_batch_size": "120",
                    "sync_interval_minutes": "30",
                    "ai_provider": AI_PROVIDER_HANLIM,
                    "ai_base_url": " https://ai.hanliminve.com/llm_hub/api/v1 ",
                    "gemini_model": "gemma-3-27b-it",
                },
                "password": " draft-pass ",
                "api_key": "",
                "hanlim_api_key": " corp-key ",
            },
        )

        self.assertEqual(next_config.user_email, "draft@example.com")
        self.assertEqual(next_config.mailbox, "QA")
        self.assertEqual(next_config.sync_days, 14)
        self.assertEqual(next_config.sync_batch_size, 120)
        self.assertEqual(next_config.sync_scan_limit, 800)
        self.assertEqual(next_config.sync_interval_minutes, 30)
        self.assertTrue(next_config.store_raw_body)
        self.assertEqual(next_config.preview_max_chars, 45000)
        self.assertEqual(next_config.gemini_timeout_seconds, 120)
        self.assertEqual(next_config.ai_provider, AI_PROVIDER_HANLIM)
        self.assertEqual(next_config.ai_base_url, "https://ai.hanliminve.com/llm_hub/api/v1")
        self.assertEqual(password, "draft-pass")
        self.assertIsNone(api_key)
        self.assertEqual(hanlim_api_key, "corp-key")

    def test_validate_send_template_requires_schedule_fields_for_registration(self) -> None:
        template = _build_send_registration_from_payload(
            self._FakeAddressBookService(),
            {
                "name": "",
                "subject": "Report",
                "to_raw": "",
                "repeat_type": "daily",
                "first_send_at": "",
                "send_time": "",
                "enabled": True,
            },
        )

        errors = ui_state_helpers.validate_send_template(template, require_schedule=True)

        self.assertGreaterEqual(len(errors), 3)
        self.assertTrue(any("등록 이름" in error for error in errors))
        self.assertTrue(any("받는 사람" in error for error in errors))
        self.assertTrue(any("첫 발송" in error for error in errors))

    def test_build_follow_up_mail_template_prefills_recipients_and_subject(self) -> None:
        thread = self._make_thread("follow-up")
        thread.thread_subject = "Vendor Quote"
        thread.current_conclusion = "Revised quote should be sent."
        thread.follow_up_detail = "Please confirm the amount and resend."
        mail = MailRecord(
            id=31,
            message_id="msg-31",
            subject="Vendor Quote",
            normalized_subject="Vendor Quote",
            thread_key="follow-up",
            in_reply_to=None,
            references=[],
            sender_name="Vendor",
            sender_email="vendor@example.com",
            to_list=["user@example.com"],
            cc_list=["finance@example.com"],
            received_at="2026-03-12 11:20:00",
            body_text="Can you send the revised version today?",
            raw_preview="Can you send the revised version today?",
            attachment_names=[],
            attachment_paths=[],
            category="ACT",
            priority="high",
            summary_short="Vendor requested a revised version.",
            summary_long=[],
            due_date=None,
            my_action_required=True,
            my_action_status="direct_action",
            ownership_reason=[],
            confidence=0.8,
            status="todo",
            analysis_status="success",
            analysis_error=None,
            created_at="2026-03-12 11:20:00",
            updated_at="2026-03-12 11:21:00",
        )

        draft = ui_state_helpers.build_follow_up_mail_template(
            thread,
            [mail],
            current_user_email="user@example.com",
            current_user_name="Tester",
        )

        self.assertEqual(draft.to_list, ["vendor@example.com"])
        self.assertEqual(draft.cc_list, ["finance@example.com"])
        self.assertTrue(draft.subject.startswith("Re: "))
        self.assertIn("후속 연락드립니다", draft.body)
        self.assertIn("Tester", draft.body)

    def test_build_sync_status_dict_exposes_warning_and_backlog_counts(self) -> None:
        status = ui_state_helpers.build_sync_status_dict(
            AppConfig(mailbox="INBOX", sync_days=14, sync_interval_minutes=30),
            sync_snapshot={
                "mailbox": "INBOX",
                "sync_days": 14,
                "last_sync_at": "2026-03-13 09:00:00",
                "backfill_active": True,
                "backfill_completed_at": None,
                "cursor_before_uid": 12345,
                "sync_warning": "",
            },
            scheduler_status={
                "interval_minutes": 30,
                "next_run_at": "2026-03-13 09:30:00",
                "last_finished_at": "2026-03-13 09:01:00",
                "last_result_summary": "manual: saved 5",
                "last_warning": "429 Too Many Requests",
                "scheduler_state": "1",
            },
            analysis_warning="",
            backlog_counts={"pending": 7, "failed": 2},
        )

        self.assertEqual(status["interval_minutes"], 30)
        self.assertTrue(status["backfill_active"])
        self.assertEqual(status["pending_analysis_count"], 7)
        self.assertEqual(status["failed_analysis_count"], 2)
        self.assertEqual(status["current_warning"], "429 Too Many Requests")

    def test_mailbox_test_submission_uses_unsaved_password(self) -> None:
        current = AppConfig(user_email="saved@example.com", mailbox="INBOX")

        next_config, password = _build_mailbox_test_submission(
            current,
            {
                "config": {
                    "user_email": "draft@example.com",
                    "mailbox": "Review",
                },
                "password": " new-pass ",
            },
        )

        self.assertEqual(next_config.user_email, "draft@example.com")
        self.assertEqual(next_config.mailbox, "Review")
        self.assertEqual(password, "new-pass")

    def test_custom_board_uses_local_assets_only(self) -> None:
        html = (Path(__file__).resolve().parents[1] / "app" / "ui" / "custom_board" / "index.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("tailwind.local.css", html)
        self.assertIn('<script src="ui_patch.js"></script>', html)
        self.assertNotIn("https://cdn.tailwindcss.com", html)
        self.assertNotIn("fonts.googleapis.com", html)
        self.assertNotIn("const Streamlit = {", html)

    def test_custom_board_does_not_contain_broken_html_closing_tags(self) -> None:
        html = (Path(__file__).resolve().parents[1] / "app" / "ui" / "custom_board" / "index.html").read_text(
            encoding="utf-8"
        )

        self.assertIsNone(re.search(r'(?<!<)/(title|div|h2|h3|span|button|label|option)>', html))
        self.assertIsNone(re.search(r'placeholder="[^"\r\n<>]* class=', html))

    def test_custom_board_contains_expected_korean_labels(self) -> None:
        html = (Path(__file__).resolve().parents[1] / "app" / "ui" / "custom_board" / "index.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("HL-Mail Helper 대시보드", html)
        self.assertIn("HL-Mail Helper", html)
        self.assertIn("title=\"사이드바 접기 또는 펼치기\"", html)
        self.assertIn("사용자 계정", html)
        self.assertIn("불러오는 중...", html)
        self.assertIn("메일 분류", html)
        self.assertIn("보관함", html)
        self.assertIn("완료", html)
        self.assertIn("환경 설정", html)
        self.assertIn("템플릿 자동발송", html)
        self.assertIn("본문", html)
        self.assertNotIn("??쒕낫??", html)
        self.assertNotIn("遺덈윭?ㅻ뒗 以?..", html)
        self.assertNotIn("?쒗뵆由?", html)

    def test_custom_board_uses_responsive_form_layouts(self) -> None:
        html = (Path(__file__).resolve().parents[1] / "app" / "ui" / "custom_board" / "index.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("dashboard-split grid grid-cols-1 gap-4 md:gap-6 flex-1 min-h-0 overflow-hidden", html)
        self.assertIn("@media (min-width: 64rem)", html)
        self.assertIn("grid-template-columns: minmax(0, 1fr);", html)
        self.assertIn("grid grid-cols-1 xl:grid-cols-2 gap-4 md:gap-5", html)
        self.assertIn("flex flex-col sm:flex-row gap-2", html)
        self.assertIn("flex flex-col 2xl:flex-row gap-4 md:gap-6 flex-1 min-h-0 overflow-hidden", html)

    def test_custom_board_dashboard_contains_mail_classification_shell(self) -> None:
        html = (Path(__file__).resolve().parents[1] / "app" / "ui" / "custom_board" / "index.html").read_text(
            encoding="utf-8"
        )
        js = (Path(__file__).resolve().parents[1] / "app" / "ui" / "custom_board" / "ui_patch.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("dashboard-thread-panel", html)
        self.assertIn('id="priority-thread-list"', html)
        self.assertIn('id="priority-filter-container"', html)
        self.assertIn('id="priority-filter-shell"', html)
        self.assertIn('id="priority-thread-detail-view"', html)
        self.assertIn('id="dashboard-mail-summary-container"', html)
        self.assertIn("메일 분류 목록", html)
        self.assertNotIn("메일 원문", html)
        self.assertNotIn('id="mail-detail-container"', html)
        self.assertIn("뒤로가기", js)
        self.assertIn("HL-Mail Helper", html)
        self.assertIn('class="toolbar-actions"', html)
        self.assertIn('id="theme-light-btn"', html)
        self.assertIn('id="theme-dark-btn"', html)
        self.assertIn('id="dashboard-view-title"', html)
        self.assertIn('id="dashboard-view-subtitle"', html)
        self.assertIn('id="priority-list-title"', html)
        self.assertNotIn('sidebar-theme-section shrink-0', html)
        self.assertIn('id="tpl_attachment_dropzone"', html)
        self.assertIn('onclick="pickAttachmentFiles()"', html)
        self.assertIn('id="tpl_attachment_list"', html)
        self.assertNotIn("content-shell py-0", html)
        self.assertIn('dashboardMailTab: "category_1"', js)
        self.assertIn("dashboardSection: null", js)
        self.assertIn('dashboardMailView: "list"', js)
        self.assertIn('dashboard_mail_tab', js)
        self.assertIn('dashboard_mail_view', js)
        self.assertIn('selected_mail_id', js)
        self.assertIn("const DEFAULT_DASHBOARD_SECTIONS = {", js)
        self.assertIn("function renderDashboardPageCopy()", js)
        self.assertIn("function handleDashboardMailCollectionAction(event, action, mailId)", js)
        self.assertIn("function resolveDashboardMailTabWithCounts(tabKey)", js)
        self.assertIn("archive_mail", js)
        self.assertIn("complete_mail", js)
        self.assertIn("restore_mail", js)
        self.assertIn("복구", js)
        self.assertIn("window.handleDashboardMailCollectionAction = handleDashboardMailCollectionAction;", js)
        self.assertIn("function renderDashboardMailSummary()", js)
        self.assertIn("function showDashboardMailList()", js)
        self.assertIn("window.setDashboardMailTab = setDashboardMailTab;", js)
        self.assertIn("window.selectDashboardMail = selectDashboardMail;", js)
        self.assertIn("window.showDashboardMailList = showDashboardMailList;", js)
        self.assertIn('sidebarMode: "auto"', js)
        self.assertIn("const SIDEBAR_AUTO_COLLAPSE_BREAKPOINT = 1200;", js)
        self.assertIn("function syncResponsiveSidebar()", js)
        self.assertIn("const SYNC_POLL_INTERVAL_MS = 800;", js)
        self.assertIn("function renderSyncButton()", js)
        self.assertIn("function pollDashboardWhileSyncing()", js)
        self.assertIn("sync_progress", js)
        self.assertIn("function pickAttachmentFiles()", js)
        self.assertIn("function wireAttachmentDropzone()", js)
        self.assertIn("function clearAttachmentFiles()", js)
        self.assertIn('window.addEventListener("resize", syncResponsiveSidebar);', js)
        self.assertNotIn("window.setDashboardThreadFilter = setDashboardThreadFilter;", js)
        self.assertNotIn("window.setDashboardThreadPage = setDashboardThreadPage;", js)

    def test_custom_board_uses_clickable_datetime_inputs_for_autosend(self) -> None:
        html = (Path(__file__).resolve().parents[1] / "app" / "ui" / "custom_board" / "index.html").read_text(
            encoding="utf-8"
        )

        self.assertIn('type="datetime-local" step="60" id="tpl_first"', html)
        self.assertIn('type="time" step="60" id="tpl_time"', html)

    def test_send_registration_submission_normalizes_datetime_local_payload(self) -> None:
        template = _build_send_registration_from_payload(
            self._FakeAddressBookService(),
            {
                "name": "?덉빟 諛쒖넚",
                "subject": "?덇굔 怨듭쑀",
                "to_raw": "to@example.com",
                "cc_raw": "cc@example.com",
                "repeat_type": "daily",
                "first_send_at": "2026-03-10T09:30",
                "send_time": "09:30:00",
                "enabled": True,
            },
        )

        self.assertEqual(template.first_send_at, "2026-03-10 09:30")
        self.assertEqual(template.send_time, "09:30")

    def test_mail_template_submission_normalizes_datetime_local_payload(self) -> None:
        template = _build_mail_template_from_payload(
            self._FakeAddressBookService(),
            {
                "name": "Template",
                "subject": "蹂닿퀬",
                "to_raw": "to@example.com",
                "repeat_type": "none",
                "first_send_at": "2026-03-10 14:45:00",
                "send_time": "14:45",
            },
        )

        self.assertEqual(template.first_send_at, "2026-03-10 14:45")
        self.assertEqual(template.send_time, "14:45")


if __name__ == "__main__":
    unittest.main()

