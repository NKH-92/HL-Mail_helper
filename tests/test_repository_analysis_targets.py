from __future__ import annotations

import gc
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app.db.database import DatabaseManager
from app.db.models import ParsedMail
from app.db.repositories import MailRepository


def _parsed_mail(
    message_id: str,
    subject: str,
    received_at: datetime,
    *,
    in_reply_to: str | None = None,
    references: list[str] | None = None,
) -> ParsedMail:
    return ParsedMail(
        message_id=message_id,
        subject=subject,
        normalized_subject=subject.replace("Re: ", ""),
        thread_key=message_id,
        sender_name="Sender",
        sender_email="sender@example.com",
        to_list=["user@example.com"],
        cc_list=[],
        received_at=received_at,
        body_text=f"{subject} full body",
        raw_preview=f"{subject} body",
        in_reply_to=in_reply_to,
        references=references or [],
        attachment_names=[],
        attachment_paths=[],
    )


def _analysis_result(
    *,
    category: str = "ACT",
    priority: str = "medium",
    summary: str = "Summary",
    due_date: str | None = None,
    my_action_status: str = "direct_action",
    action_classification: str = "ACTION_SELF",
    action_owner: str = "me",
    action_type: list[str] | None = None,
    mail_action_items: list[str] | None = None,
    my_action_items: list[str] | None = None,
    suggested_task_title: str | None = None,
    confidence: float = 0.9,
) -> dict[str, object]:
    return {
        "category": category,
        "priority": priority,
        "classification": action_classification,
        "one_line_summary": summary,
        "summary_3lines": [summary],
        "mail_action_items": mail_action_items or [],
        "my_action_required": my_action_status != "reference_only",
        "my_action_status": my_action_status,
        "my_action_items": my_action_items or [],
        "action_owner": action_owner,
        "action_type": action_type or ["none"],
        "due_date": due_date,
        "deadline_raw": due_date,
        "evidence": [],
        "ownership_reason": ["test"],
        "reason": "test",
        "suggested_task_title": suggested_task_title,
        "confidence": confidence,
    }


class RepositoryAnalysisTargetTests(unittest.TestCase):
    def test_list_analysis_targets_can_include_failed_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseManager(Path(temp_dir) / "app.db")
            repository = MailRepository(database)

            pending_id = repository.create_from_parsed_mail(
                _parsed_mail("pending-1", "Pending", datetime(2026, 3, 8, 9, 0, 0))
            )
            failed_id = repository.create_from_parsed_mail(
                _parsed_mail("failed-1", "Failed", datetime(2026, 3, 8, 8, 0, 0))
            )

            self.assertIsNotNone(pending_id)
            self.assertIsNotNone(failed_id)
            repository.mark_analysis_failed(int(failed_id), "temporary error")

            pending_only = repository.list_analysis_targets(limit=10, include_failed=False)
            with_failed = repository.list_analysis_targets(limit=10, include_failed=True)

            self.assertEqual([mail.message_id for mail in pending_only], ["pending-1"])
            self.assertEqual([mail.message_id for mail in with_failed], ["pending-1", "failed-1"])
            del repository
            del database
            gc.collect()

    def test_list_analysis_targets_reserves_room_for_failed_retries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseManager(Path(temp_dir) / "app.db")
            repository = MailRepository(database)

            for index in range(3):
                repository.create_from_parsed_mail(
                    _parsed_mail(
                        f"pending-{index}",
                        f"Pending {index}",
                        datetime(2026, 3, 8, 9, 0, 0) - timedelta(minutes=index),
                    )
                )
            failed_id = repository.create_from_parsed_mail(
                _parsed_mail("failed-retry", "Failed retry", datetime(2026, 3, 8, 8, 0, 0))
            )

            self.assertIsNotNone(failed_id)
            repository.mark_analysis_failed(int(failed_id), "temporary error")

            targets = repository.list_analysis_targets(limit=3, include_failed=True)

            self.assertEqual([mail.message_id for mail in targets[:2]], ["pending-0", "pending-1"])
            self.assertEqual(targets[-1].message_id, "failed-retry")
            del repository
            del database
            gc.collect()

    def test_filter_existing_message_ids_returns_only_known_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseManager(Path(temp_dir) / "app.db")
            repository = MailRepository(database)

            repository.create_from_parsed_mail(
                _parsed_mail("known-1", "Known One", datetime(2026, 3, 8, 9, 0, 0))
            )
            repository.create_from_parsed_mail(
                _parsed_mail("known-2", "Known Two", datetime(2026, 3, 8, 10, 0, 0))
            )

            existing_ids = repository.filter_existing_message_ids(["known-1", "missing", "known-2", ""])

            self.assertEqual(existing_ids, {"known-1", "known-2"})
            del repository
            del database
            gc.collect()

    def test_thread_overview_cache_is_invalidated_after_status_update(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseManager(Path(temp_dir) / "app.db")
            repository = MailRepository(database)

            mail_id = repository.create_from_parsed_mail(
                _parsed_mail("cache-1", "Cache Check", datetime(2026, 3, 8, 9, 0, 0))
            )

            self.assertIsNotNone(mail_id)
            repository.save_analysis_result(
                int(mail_id),
                _analysis_result(
                    priority="medium",
                    summary="Initial status should be todo.",
                    due_date=None,
                    my_action_status="direct_action",
                    action_type=["reply"],
                ),
            )

            initial_threads = repository.list_thread_overviews(user_email="user@example.com")
            self.assertEqual(initial_threads[0].latest_mail_status, "todo")

            repository.update_status(int(mail_id), "done")

            refreshed_threads = repository.list_thread_overviews(user_email="user@example.com")
            self.assertEqual(refreshed_threads[0].latest_mail_status, "done")
            del initial_threads
            del refreshed_threads
            del repository
            del database
            gc.collect()

    def test_thread_overview_cache_rebuilds_only_invalidated_threads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseManager(Path(temp_dir) / "app.db")
            repository = MailRepository(database)

            first_mail_id = repository.create_from_parsed_mail(
                _parsed_mail("thread-a-root", "Thread A", datetime(2026, 3, 8, 9, 0, 0))
            )
            second_mail_id = repository.create_from_parsed_mail(
                _parsed_mail("thread-b-root", "Thread B", datetime(2026, 3, 8, 10, 0, 0))
            )
            self.assertIsNotNone(first_mail_id)
            self.assertIsNotNone(second_mail_id)

            repository.save_analysis_result(int(first_mail_id), _analysis_result(summary="A summary"))
            repository.save_analysis_result(int(second_mail_id), _analysis_result(summary="B summary"))

            initial_threads = repository.list_thread_overviews(user_email="user@example.com")
            self.assertEqual(len(initial_threads), 2)

            repository.update_status(int(first_mail_id), "done")

            with patch.object(repository, "_build_missing_thread_overviews", wraps=repository._build_missing_thread_overviews) as wrapped:
                refreshed_threads = repository.list_thread_overviews(user_email="user@example.com")

            self.assertEqual(len(refreshed_threads), 2)
            wrapped.assert_called_once()
            self.assertEqual(wrapped.call_args.args[0], "user@example.com")
            self.assertEqual(wrapped.call_args.args[1], ["thread-a-root"])
            del initial_threads
            del refreshed_threads
            del repository
            del database
            gc.collect()

    def test_build_thread_summary_keeps_head_and_tail_of_previous_mail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseManager(Path(temp_dir) / "app.db")
            repository = MailRepository(database)

            root = _parsed_mail("root-1", "Root", datetime(2026, 3, 8, 9, 0, 0))
            root.body_text = f"{'HEAD ' * 60}{'middle ' * 200}{'TAIL ' * 60}"
            root.raw_preview = root.body_text[:4000]
            root_id = repository.create_from_parsed_mail(root)

            reply = _parsed_mail(
                "reply-1",
                "Re: Root",
                datetime(2026, 3, 8, 10, 0, 0),
                in_reply_to="root-1",
                references=["root-1"],
            )
            reply.body_text = "Short follow-up"
            reply.raw_preview = reply.body_text
            reply_id = repository.create_from_parsed_mail(reply)

            self.assertIsNotNone(root_id)
            self.assertIsNotNone(reply_id)

            summary = repository.build_thread_summary(int(reply_id))

            self.assertIn("Previous message 1", summary)
            self.assertIn("HEAD HEAD", summary)
            self.assertIn("TAIL TAIL", summary)
            del repository
            del database
            gc.collect()

    def test_thread_overview_cache_preserves_concurrent_invalidations_during_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseManager(Path(temp_dir) / "app.db")
            repository = MailRepository(database)

            mail_id = repository.create_from_parsed_mail(
                _parsed_mail("thread-race-root", "Thread Race", datetime(2026, 3, 8, 9, 0, 0))
            )
            self.assertIsNotNone(mail_id)
            repository.save_analysis_result(int(mail_id), _analysis_result(summary="Race summary"))

            repository.list_thread_overviews(user_email="user@example.com")
            repository.update_status(int(mail_id), "done")

            original_build = repository._build_missing_thread_overviews

            def _racing_build(user_email: str, thread_keys: list[str]):
                built = original_build(user_email, thread_keys)
                repository.update_status(int(mail_id), "todo")
                return built

            with patch.object(repository, "_build_missing_thread_overviews", side_effect=_racing_build):
                raced_threads = repository.list_thread_overviews(user_email="user@example.com")

            refreshed_threads = repository.list_thread_overviews(user_email="user@example.com")

            self.assertEqual(raced_threads[0].latest_mail_status, "done")
            self.assertEqual(refreshed_threads[0].latest_mail_status, "todo")
            del raced_threads
            del refreshed_threads
            del repository
            del database
            gc.collect()

    def test_move_mail_retention_bucket_to_completed_marks_mail_done_and_closes_my_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseManager(Path(temp_dir) / "app.db")
            repository = MailRepository(database)

            mail_id = repository.create_from_parsed_mail(
                _parsed_mail("complete-1", "Complete me", datetime(2026, 3, 8, 9, 0, 0))
            )

            self.assertIsNotNone(mail_id)
            analysis_result = _analysis_result(
                summary="Send the follow-up",
                action_type=["reply"],
                mail_action_items=["Track the reply"],
                my_action_items=["Send the follow-up"],
            )
            repository.save_analysis_bundle(
                int(mail_id),
                analysis_result,
                mail_action_items=list(analysis_result["mail_action_items"]),
                my_action_items=list(analysis_result["my_action_items"]),
                due_date=None,
                current_user_email="user@example.com",
            )

            moved_mail = repository.move_mail_retention_bucket(int(mail_id), "completed")
            open_tasks = repository.list_open_my_action_items()
            completed_tasks = repository.list_completed_my_action_items()

            self.assertIsNotNone(moved_mail)
            self.assertEqual(moved_mail.status, "done")
            self.assertEqual(moved_mail.retention_bucket, "completed")
            self.assertEqual(repository.count_open_action_items(int(mail_id), "my"), 0)
            self.assertEqual(len(open_tasks), 0)
            self.assertEqual(len(completed_tasks), 1)
            self.assertEqual(completed_tasks[0].mail_id, int(mail_id))
            self.assertIsNotNone(completed_tasks[0].completed_at)
            del open_tasks
            del completed_tasks
            del repository
            del database
            gc.collect()

    def test_move_mail_retention_bucket_restore_reopens_mail_and_my_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseManager(Path(temp_dir) / "app.db")
            repository = MailRepository(database)

            mail_id = repository.create_from_parsed_mail(
                _parsed_mail("restore-1", "Restore me", datetime(2026, 3, 8, 9, 0, 0))
            )

            self.assertIsNotNone(mail_id)
            analysis_result = _analysis_result(
                summary="Reply to the sender",
                action_type=["reply"],
                mail_action_items=["Track the response"],
                my_action_items=["Reply to the sender"],
            )
            repository.save_analysis_bundle(
                int(mail_id),
                analysis_result,
                mail_action_items=list(analysis_result["mail_action_items"]),
                my_action_items=list(analysis_result["my_action_items"]),
                due_date=None,
                current_user_email="user@example.com",
            )

            repository.move_mail_retention_bucket(int(mail_id), "completed")
            restored_mail = repository.move_mail_retention_bucket(int(mail_id), "classified")
            open_tasks = repository.list_open_my_action_items()
            completed_tasks = repository.list_completed_my_action_items()

            self.assertIsNotNone(restored_mail)
            self.assertEqual(restored_mail.status, "todo")
            self.assertEqual(restored_mail.retention_bucket, "classified")
            self.assertEqual(repository.count_open_action_items(int(mail_id), "my"), 1)
            self.assertEqual(len(open_tasks), 1)
            self.assertEqual(open_tasks[0].mail_id, int(mail_id))
            self.assertEqual(open_tasks[0].action_text, "Reply to the sender")
            self.assertIsNone(open_tasks[0].completed_at)
            self.assertEqual(len(completed_tasks), 0)
            del open_tasks
            del completed_tasks
            del repository
            del database
            gc.collect()

    def test_thread_overview_cache_persists_across_repository_instances(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseManager(Path(temp_dir) / "app.db")
            first_repository = MailRepository(database)

            mail_id = first_repository.create_from_parsed_mail(
                _parsed_mail("persisted-cache-1", "Persistent Cache", datetime(2026, 3, 8, 9, 0, 0))
            )

            self.assertIsNotNone(mail_id)
            first_repository.save_analysis_result(
                int(mail_id),
                _analysis_result(
                    priority="medium",
                    summary="Initial persisted overview.",
                    my_action_status="direct_action",
                    action_type=["reply"],
                ),
            )

            first_threads = first_repository.list_thread_overviews(user_email="user@example.com")
            self.assertEqual(first_threads[0].latest_mail_status, "todo")

            connection = database.connect()
            try:
                cache_count = connection.execute(
                    "SELECT COUNT(*) AS count FROM thread_overview_cache WHERE user_email = ?",
                    ("user@example.com",),
                ).fetchone()["count"]
            finally:
                connection.close()
            self.assertEqual(cache_count, 1)

            second_repository = MailRepository(database)
            second_threads = second_repository.list_thread_overviews(user_email="user@example.com")
            self.assertEqual(second_threads[0].latest_mail_status, "todo")

            second_repository.update_status(int(mail_id), "done")

            third_repository = MailRepository(database)
            third_threads = third_repository.list_thread_overviews(user_email="user@example.com")
            self.assertEqual(third_threads[0].latest_mail_status, "done")
            del first_threads
            del second_threads
            del third_threads
            del first_repository
            del second_repository
            del third_repository
            del database
            gc.collect()

    def test_reply_mail_inherits_parent_thread_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseManager(Path(temp_dir) / "app.db")
            repository = MailRepository(database)

            root_id = repository.create_from_parsed_mail(
                _parsed_mail("root-1", "Weekly Report", datetime(2026, 3, 8, 9, 0, 0))
            )
            reply_id = repository.create_from_parsed_mail(
                _parsed_mail(
                    "reply-1",
                    "Re: Weekly Report",
                    datetime(2026, 3, 8, 10, 0, 0),
                    in_reply_to="root-1",
                    references=["root-1"],
                )
            )

            self.assertIsNotNone(root_id)
            self.assertIsNotNone(reply_id)

            root_mail = repository.get_mail(int(root_id))
            reply_mail = repository.get_mail(int(reply_id))

            self.assertIsNotNone(root_mail)
            self.assertIsNotNone(reply_mail)
            self.assertEqual(root_mail.thread_key, reply_mail.thread_key)
            self.assertEqual(len(repository.list_thread_mails(int(reply_id))), 2)
            del repository
            del database
            gc.collect()

    def test_thread_helpers_list_actions_and_mails_by_thread_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseManager(Path(temp_dir) / "app.db")
            repository = MailRepository(database)

            root_id = repository.create_from_parsed_mail(
                _parsed_mail("thread-root", "Quote Request", datetime(2026, 3, 10, 9, 0, 0))
            )
            reply_id = repository.create_from_parsed_mail(
                _parsed_mail(
                    "thread-reply",
                    "Re: Quote Request",
                    datetime(2026, 3, 10, 10, 0, 0),
                    in_reply_to="thread-root",
                    references=["thread-root"],
                )
            )

            self.assertIsNotNone(root_id)
            self.assertIsNotNone(reply_id)

            repository.replace_action_items(
                int(root_id),
                scope="my",
                action_items=["Prepare revised quote"],
                due_date="2026-03-10 17:00:00",
                owner="user@example.com",
            )
            repository.replace_action_items(
                int(reply_id),
                scope="mail",
                action_items=["Track vendor response"],
                due_date=None,
                owner=None,
            )

            root_mail = repository.get_mail(int(root_id))
            self.assertIsNotNone(root_mail)
            assert root_mail is not None

            thread_mails = repository.list_thread_mails_by_key(root_mail.thread_key, limit=5)
            my_actions = repository.list_thread_action_items(root_mail.thread_key, scope="my")
            mail_actions = repository.list_thread_action_items(root_mail.thread_key, scope="mail")

            self.assertEqual([mail.message_id for mail in thread_mails], ["thread-reply", "thread-root"])
            self.assertEqual([action.action_text for action in my_actions], ["Prepare revised quote"])
            self.assertEqual([action.action_text for action in mail_actions], ["Track vendor response"])
            del thread_mails
            del my_actions
            del mail_actions
            del root_mail
            del repository
            del database
            gc.collect()

    def test_completed_action_items_keep_note_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseManager(Path(temp_dir) / "app.db")
            repository = MailRepository(database)

            mail_id = repository.create_from_parsed_mail(
                _parsed_mail("mail-1", "Action Mail", datetime(2026, 3, 8, 9, 0, 0))
            )
            self.assertIsNotNone(mail_id)

            repository.replace_action_items(
                int(mail_id),
                scope="my",
                action_items=["자료 검토"],
                due_date="2026-03-08 18:00:00",
                owner="user@example.com",
            )
            open_items = repository.list_open_my_action_items()
            self.assertEqual(len(open_items), 1)

            action_id = open_items[0].id
            repository.update_action_item_note(action_id, "팀장 확인 후 회신")
            repository.mark_action_item_done(action_id, True)

            completed_items = repository.list_completed_my_action_items(limit=10)
            self.assertEqual(len(completed_items), 1)
            self.assertEqual(completed_items[0].note, "팀장 확인 후 회신")
            self.assertIsNotNone(completed_items[0].completed_at)
            del repository
            del database
            gc.collect()

    def test_replace_action_items_preserves_existing_note_and_done_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseManager(Path(temp_dir) / "app.db")
            repository = MailRepository(database)

            mail_id = repository.create_from_parsed_mail(
                _parsed_mail("mail-2", "Preserve Action", datetime(2026, 3, 8, 9, 0, 0))
            )
            self.assertIsNotNone(mail_id)

            repository.replace_action_items(
                int(mail_id),
                scope="my",
                action_items=["자료 검토"],
                due_date="2026-03-08 18:00:00",
                owner="user@example.com",
            )
            action = repository.list_open_my_action_items()[0]
            repository.update_action_item_note(action.id, "초안 작성 완료")
            repository.mark_action_item_done(action.id, True)

            repository.replace_action_items(
                int(mail_id),
                scope="my",
                action_items=["자료 검토"],
                due_date="2026-03-09 09:00:00",
                owner="user@example.com",
            )

            completed = repository.list_completed_my_action_items(limit=10)
            self.assertEqual(len(completed), 1)
            self.assertEqual(completed[0].note, "초안 작성 완료")
            self.assertEqual(completed[0].action_text, "자료 검토")
            self.assertEqual(completed[0].due_date, "2026-03-09 09:00:00")
            del repository
            del database
            gc.collect()

    def test_update_status_done_can_close_open_my_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseManager(Path(temp_dir) / "app.db")
            repository = MailRepository(database)

            mail_id = repository.create_from_parsed_mail(
                _parsed_mail("mail-3", "Close Action", datetime(2026, 3, 8, 9, 0, 0))
            )
            self.assertIsNotNone(mail_id)

            repository.replace_action_items(
                int(mail_id),
                scope="my",
                action_items=["회신 보내기"],
                due_date="2026-03-08 18:00:00",
                owner="user@example.com",
            )

            repository.update_status(int(mail_id), "done", sync_my_action_items=True)

            self.assertEqual(repository.count_open_action_items(int(mail_id), "my"), 0)
            completed = repository.list_completed_my_action_items(limit=10)
            self.assertEqual(len(completed), 1)
            del repository
            del database
            gc.collect()

    def test_open_action_items_are_ranked_by_due_date_then_priority(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseManager(Path(temp_dir) / "app.db")
            repository = MailRepository(database)

            overdue_mail_id = repository.create_from_parsed_mail(
                _parsed_mail("mail-overdue", "Overdue", datetime(2026, 3, 8, 9, 0, 0))
            )
            high_mail_id = repository.create_from_parsed_mail(
                _parsed_mail("mail-high", "High Priority", datetime(2026, 3, 8, 10, 0, 0))
            )
            low_mail_id = repository.create_from_parsed_mail(
                _parsed_mail("mail-low", "Low Priority", datetime(2026, 3, 8, 11, 0, 0))
            )

            self.assertIsNotNone(overdue_mail_id)
            self.assertIsNotNone(high_mail_id)
            self.assertIsNotNone(low_mail_id)

            repository.save_analysis_result(
                int(overdue_mail_id),
                {
                    "category": "ACT",
                    "priority": "medium",
                    "one_line_summary": "Overdue task",
                    "summary_3lines": ["one", "two", "three"],
                    "due_date": "2026-03-07 18:00:00",
                    "my_action_required": True,
                    "my_action_status": "direct_action",
                    "ownership_reason": ["Assigned to me"],
                    "confidence": 0.9,
                },
            )
            repository.save_analysis_result(
                int(high_mail_id),
                {
                    "category": "ACT",
                    "priority": "high",
                    "one_line_summary": "High task",
                    "summary_3lines": ["one", "two", "three"],
                    "due_date": None,
                    "my_action_required": True,
                    "my_action_status": "direct_action",
                    "ownership_reason": ["Direct request"],
                    "confidence": 0.9,
                },
            )
            repository.save_analysis_result(
                int(low_mail_id),
                {
                    "category": "FYI",
                    "priority": "low",
                    "one_line_summary": "Low task",
                    "summary_3lines": ["one", "two", "three"],
                    "due_date": None,
                    "my_action_required": True,
                    "my_action_status": "reference_only",
                    "ownership_reason": ["Reference"],
                    "confidence": 0.9,
                },
            )

            repository.replace_action_items(
                int(overdue_mail_id),
                scope="my",
                action_items=["Fix overdue"],
                due_date="2026-03-07 18:00:00",
                owner="user@example.com",
            )
            repository.replace_action_items(
                int(high_mail_id),
                scope="my",
                action_items=["Handle high"],
                due_date=None,
                owner="user@example.com",
            )
            repository.replace_action_items(
                int(low_mail_id),
                scope="my",
                action_items=["Check low"],
                due_date=None,
                owner="user@example.com",
            )

            items = repository.list_open_my_action_items()
            self.assertEqual([item.action_text for item in items], ["Fix overdue", "Handle high", "Check low"])
            del repository
            del database
            gc.collect()

    def test_list_mails_limit_returns_only_latest_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseManager(Path(temp_dir) / "app.db")
            repository = MailRepository(database)

            for index in range(5):
                created_id = repository.create_from_parsed_mail(
                    _parsed_mail(
                        f"mail-{index}",
                        f"Mail {index}",
                        datetime(2026, 3, 8, 9 + index, 0, 0),
                    )
                )
                self.assertIsNotNone(created_id)

            latest_mails = repository.list_mails(limit=2)

            self.assertEqual([mail.message_id for mail in latest_mails], ["mail-4", "mail-3"])
            del repository
            del database
            gc.collect()

    def test_save_analysis_result_persists_new_action_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseManager(Path(temp_dir) / "app.db")
            repository = MailRepository(database)

            mail_id = repository.create_from_parsed_mail(
                _parsed_mail("mail-meta", "Approval Mail", datetime(2026, 3, 8, 13, 0, 0))
            )
            self.assertIsNotNone(mail_id)

            repository.save_analysis_result(
                int(mail_id),
                {
                    "category": "APR",
                    "priority": "medium",
                    "classification": "APPROVAL_REQUEST",
                    "one_line_summary": "결재 요청",
                    "summary_3lines": ["결재 요청", "오늘 처리 필요"],
                    "mail_action_items": ["결재 승인"],
                    "my_action_required": True,
                    "my_action_status": "direct_action",
                    "my_action_items": ["결재 승인"],
                    "action_owner": "me",
                    "action_type": ["approve"],
                    "due_date": "2026-03-08 17:00:00",
                    "deadline_raw": "오늘 오후 5시까지",
                    "evidence": ["오늘 오후 5시까지 승인 부탁드립니다."],
                    "ownership_reason": ["승인 요청이 직접 확인됨"],
                    "reason": "승인 요청이 직접 확인되어 사용자의 액션으로 분류했습니다.",
                    "suggested_task_title": "결재 승인",
                    "confidence": 0.92,
                },
            )

            stored = repository.get_mail(int(mail_id))

            self.assertIsNotNone(stored)
            assert stored is not None
            self.assertEqual(stored.action_classification, "APPROVAL_REQUEST")
            self.assertEqual(stored.action_owner, "me")
            self.assertEqual(stored.action_types, ["approve"])
            self.assertEqual(stored.deadline_raw, "오늘 오후 5시까지")
            self.assertEqual(stored.evidence, ["오늘 오후 5시까지 승인 부탁드립니다."])
            self.assertEqual(stored.analysis_reason, "승인 요청이 직접 확인되어 사용자의 액션으로 분류했습니다.")
            self.assertEqual(stored.suggested_task_title, "결재 승인")
            del stored
            del repository
            del database
            gc.collect()

    def test_review_queue_includes_unclear_classification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseManager(Path(temp_dir) / "app.db")
            repository = MailRepository(database)

            mail_id = repository.create_from_parsed_mail(
                _parsed_mail("mail-unclear", "Ambiguous Mail", datetime(2026, 3, 8, 14, 0, 0))
            )
            self.assertIsNotNone(mail_id)

            repository.save_analysis_result(
                int(mail_id),
                {
                    "category": "ETC",
                    "priority": "unknown",
                    "classification": "UNCLEAR",
                    "one_line_summary": "판단 보류",
                    "summary_3lines": ["판단 보류"],
                    "mail_action_items": [],
                    "my_action_required": False,
                    "my_action_status": "reference_only",
                    "my_action_items": [],
                    "action_owner": "unknown",
                    "action_type": ["none"],
                    "due_date": None,
                    "deadline_raw": None,
                    "evidence": [],
                    "ownership_reason": ["근거 부족"],
                    "reason": "근거가 부족해 판단을 보류했습니다.",
                    "suggested_task_title": None,
                    "confidence": 0.7,
                },
            )

            review_queue = repository.list_review_queue(limit=10)

            self.assertEqual([mail.message_id for mail in review_queue], ["mail-unclear"])
            del review_queue
            del repository
            del database
            gc.collect()

    def test_thread_overviews_rank_reply_due_today_ahead_of_reference_threads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseManager(Path(temp_dir) / "app.db")
            repository = MailRepository(database)
            today_text = datetime.now().date().isoformat()

            urgent_root_id = repository.create_from_parsed_mail(
                _parsed_mail("thread-root", "Client Quote", datetime(2026, 3, 11, 9, 0, 0))
            )
            urgent_reply_id = repository.create_from_parsed_mail(
                _parsed_mail(
                    "thread-reply",
                    "Re: Client Quote",
                    datetime(2026, 3, 11, 10, 0, 0),
                    in_reply_to="thread-root",
                    references=["thread-root"],
                )
            )
            fyi_mail_id = repository.create_from_parsed_mail(
                _parsed_mail("fyi-1", "Weekly Notice", datetime(2026, 3, 11, 8, 0, 0))
            )

            self.assertIsNotNone(urgent_root_id)
            self.assertIsNotNone(urgent_reply_id)
            self.assertIsNotNone(fyi_mail_id)

            repository.save_analysis_result(
                int(urgent_reply_id),
                _analysis_result(
                    priority="high",
                    summary="Customer asked for a revised quote today.",
                    due_date=f"{today_text} 17:00:00",
                    my_action_status="direct_action",
                    action_type=["reply"],
                    suggested_task_title="Send revised quote",
                ),
            )
            repository.replace_action_items(
                int(urgent_reply_id),
                scope="my",
                action_items=["Send revised quote"],
                due_date=f"{today_text} 17:00:00",
                owner="user@example.com",
            )

            repository.save_analysis_result(
                int(fyi_mail_id),
                _analysis_result(
                    category="FYI",
                    priority="low",
                    summary="Reference notice only.",
                    due_date=None,
                    my_action_status="reference_only",
                    action_classification="FYI",
                    action_owner="other",
                    action_type=["none"],
                ),
            )

            threads = repository.list_thread_overviews(user_email="user@example.com")

            self.assertEqual(threads[0].latest_mail_id, int(urgent_reply_id))
            self.assertEqual(threads[0].follow_up_status, "deadline_soon")
            self.assertGreater(threads[0].priority_score, threads[1].priority_score)
            self.assertIn("Send revised quote", threads[0].current_conclusion)
            self.assertEqual(threads[1].follow_up_status, "tracked")
            del threads
            del repository
            del database
            gc.collect()

    def test_thread_overviews_track_waiting_reply_after_outbound_mail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseManager(Path(temp_dir) / "app.db")
            repository = MailRepository(database)

            outbound_at = datetime.now().replace(microsecond=0) - timedelta(days=4)
            outbound_mail_id = repository.create_from_parsed_mail(
                ParsedMail(
                    message_id="outbound-1",
                    subject="Vendor Follow-up",
                    normalized_subject="Vendor Follow-up",
                    thread_key="outbound-1",
                    sender_name="Me",
                    sender_email="user@example.com",
                    to_list=["vendor@example.com"],
                    cc_list=[],
                    received_at=outbound_at,
                    body_text="Checking whether the vendor can confirm the schedule.",
                    raw_preview="Checking whether the vendor can confirm the schedule.",
                    attachment_names=[],
                    attachment_paths=[],
                )
            )
            self.assertIsNotNone(outbound_mail_id)

            repository.save_analysis_result(
                int(outbound_mail_id),
                _analysis_result(
                    priority="medium",
                    summary="Waiting for vendor confirmation.",
                    due_date=None,
                    my_action_status="reference_only",
                    action_classification="ACTION_SHARED",
                    action_owner="other",
                    action_type=["monitor"],
                ),
            )

            threads = repository.list_thread_overviews(user_email="user@example.com")

            self.assertEqual(len(threads), 1)
            self.assertEqual(threads[0].follow_up_status, "no_reply_3d")
            self.assertEqual(threads[0].follow_up_label, "No reply in 3d")
            self.assertIn("Last outbound mail", threads[0].follow_up_detail)
            del threads
            del repository
            del database
            gc.collect()

    def test_thread_overviews_keep_older_unresolved_direct_action_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseManager(Path(temp_dir) / "app.db")
            repository = MailRepository(database)

            incoming_mail_id = repository.create_from_parsed_mail(
                _parsed_mail("mask-root", "Budget Request", datetime(2026, 3, 10, 9, 0, 0))
            )
            outbound_mail_id = repository.create_from_parsed_mail(
                ParsedMail(
                    message_id="mask-reply",
                    subject="Re: Budget Request",
                    normalized_subject="Budget Request",
                    thread_key="mask-reply",
                    sender_name="Me",
                    sender_email="user@example.com",
                    to_list=["finance@example.com"],
                    cc_list=[],
                    received_at=datetime(2026, 3, 10, 10, 0, 0),
                    body_text="Following up on the budget request.",
                    raw_preview="Following up on the budget request.",
                    in_reply_to="mask-root",
                    references=["mask-root"],
                    attachment_names=[],
                    attachment_paths=[],
                )
            )

            self.assertIsNotNone(incoming_mail_id)
            self.assertIsNotNone(outbound_mail_id)

            repository.save_analysis_result(
                int(incoming_mail_id),
                _analysis_result(
                    priority="high",
                    summary="Approve the budget request.",
                    due_date=None,
                    my_action_status="direct_action",
                    action_classification="APPROVAL_REQUEST",
                    action_type=["approve"],
                    suggested_task_title="Approve budget request",
                ),
            )
            repository.save_analysis_result(
                int(outbound_mail_id),
                _analysis_result(
                    category="FYI",
                    priority="low",
                    summary="Follow-up sent to finance.",
                    due_date=None,
                    my_action_status="reference_only",
                    action_classification="ACTION_SHARED",
                    action_owner="other",
                    action_type=["monitor"],
                ),
            )

            threads = repository.list_thread_overviews(user_email="user@example.com")

            self.assertEqual(len(threads), 1)
            self.assertIn(threads[0].follow_up_status, {"approval_pending", "action_needed"})
            self.assertNotEqual(threads[0].follow_up_status, "waiting_for_reply")
            self.assertIn("Approve budget request", threads[0].current_conclusion)
            del threads
            del repository
            del database
            gc.collect()

    def test_mark_thread_done_closes_personal_actions_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseManager(Path(temp_dir) / "app.db")
            repository = MailRepository(database)

            mail_id = repository.create_from_parsed_mail(
                _parsed_mail("done-thread", "Client Follow-up", datetime(2026, 3, 12, 9, 0, 0))
            )

            self.assertIsNotNone(mail_id)
            repository.save_analysis_result(
                int(mail_id),
                _analysis_result(
                    priority="high",
                    summary="Reply to the client and confirm the scope.",
                    due_date=None,
                    my_action_status="direct_action",
                    action_type=["reply"],
                    suggested_task_title="Reply to client",
                ),
            )

            updated_count = repository.mark_thread_done("done-thread")
            threads = repository.list_thread_overviews(user_email="user@example.com")

            self.assertEqual(updated_count, 1)
            self.assertEqual(repository.count_open_action_items(int(mail_id), "my"), 0)
            self.assertEqual(threads[0].latest_mail_status, "done")
            del threads
            del repository
            del database
            gc.collect()


if __name__ == "__main__":
    unittest.main()
