from __future__ import annotations

import json
import logging
import tempfile
import unittest
import gc
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

from app.core.config_manager import AI_PROVIDER_HANLIM, AppConfig, ConfigManager, LEGACY_AI_PROVIDER_HANLIM
from app.core.scheduler_manager import SchedulerManager
from app.db.database import DatabaseManager
from app.db.models import ParsedMail, SendTemplate, SyncFetchResult, SyncResult
from app.db.repositories import AppSettingsRepository, MailRepository
from app.mail.imap_client import IMAPClient
from app.services.mailbox_service import MailboxService
from app.services.send_service import SendService
from app.services.sync_service import SyncService
from app.ui.modern_dashboard import _build_mailbox_test_submission, _build_settings_submission


class _DummySecretStore:
    def get_secret(self, key: str) -> str:
        return ""

    def has_secret(self, key: str) -> bool:
        return False


class _FakeImapClient:
    def uid(self, command, ids, query):
        payload = [
            (
                b"1 (UID 101 BODY[HEADER.FIELDS (MESSAGE-ID DATE)] {80}",
                b"Message-ID: <exists@example.com>\r\n\r\n",
            ),
            (
                b"2 (UID 102 BODY[HEADER.FIELDS (MESSAGE-ID DATE)] {80}",
                b"Date: Sat, 07 Mar 2026 23:30:00 -0800\r\n\r\n",
            ),
        ]
        return "OK", payload


class _EmptyFetchImapClient:
    def fetch_recent_messages(self, **kwargs) -> SyncFetchResult:
        return SyncFetchResult(messages=[], scanned_count=0, limited_by_batch=False)


class _RecordingFetchImapClient:
    def __init__(self, results: list[SyncFetchResult]) -> None:
        self.results = list(results)
        self.calls: list[dict[str, object]] = []

    def fetch_recent_messages(self, **kwargs) -> SyncFetchResult:
        self.calls.append(dict(kwargs))
        if not self.results:
            raise AssertionError("No queued fetch result")
        return self.results.pop(0)


def _parsed_mail(
    message_id: str,
    subject: str,
    received_at: datetime,
    *,
    attachment_paths: list[str] | None = None,
    attachment_names: list[str] | None = None,
) -> ParsedMail:
    return ParsedMail(
        message_id=message_id,
        subject=subject,
        normalized_subject=subject,
        thread_key=message_id.lower(),
        sender_name="Sender",
        sender_email="sender@example.com",
        to_list=["user@example.com"],
        cc_list=[],
        received_at=received_at,
        raw_preview=f"{subject} body",
        attachment_names=attachment_names or [],
        attachment_paths=attachment_paths or [],
    )


class _FakeSyncService:
    def get_unavailability_reason(self):
        return None

    def sync_recent_mail(self):
        return SyncResult(
            scanned_count=1,
            fetched_count=1,
            saved_count=0,
            skipped_old_count=0,
            duplicate_count=0,
            parse_error_count=0,
            limited_by_batch=False,
        )


class _FakeAnalysisService:
    def __init__(self) -> None:
        self.include_failed: bool | None = None

    def get_unavailability_reason(self):
        return None

    def analyze_pending_mails(self, limit: int = 10, include_failed: bool = False):
        self.include_failed = include_failed
        return 0, 0


class _FakeConfigManager:
    def load(self) -> AppConfig:
        return AppConfig(user_email="sender@example.com", mailbox="INBOX")


class _FakeSmtpClient:
    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.sent_messages: list[dict[str, object]] = []

    def send_mail(self, **kwargs) -> None:
        if self.should_fail:
            raise RuntimeError("smtp failed")
        self.sent_messages.append(kwargs)


class _FakeTemplateRepository:
    def __init__(self, template: SendTemplate | None) -> None:
        self.template = template
        self.last_sent_at: str | None = None
        self.enabled_updates: list[bool] = []

    def get(self, template_id: int) -> SendTemplate | None:
        if self.template and self.template.id == template_id:
            return self.template
        return None

    def update_last_sent_at(self, template_id: int, sent_at: str) -> None:
        if self.template and self.template.id == template_id:
            self.template.last_sent_at = sent_at
            self.last_sent_at = sent_at

    def set_enabled(self, template_id: int, enabled: bool) -> None:
        if self.template and self.template.id == template_id:
            self.template.enabled = enabled
            self.enabled_updates.append(enabled)

    def list_enabled(self) -> list[SendTemplate]:
        if self.template and self.template.enabled:
            return [self.template]
        return []


class _FakeSendLogRepository:
    def __init__(self) -> None:
        self.entries: list[tuple[int | None, str, list[str], str, str | None]] = []

    def create(
        self,
        template_id: int | None,
        result: str,
        recipients: list[str],
        subject: str,
        error_message: str | None,
    ) -> None:
        self.entries.append((template_id, result, recipients, subject, error_message))


class _FakeScheduler:
    def __init__(self) -> None:
        self.added_jobs: list[tuple[tuple, dict]] = []

    def add_job(self, *args, **kwargs) -> None:
        self.added_jobs.append((args, kwargs))


class _NoopMailboxService:
    def run_once(self):
        return None


class _DisablingSendService:
    def __init__(self, template_repository: _FakeTemplateRepository) -> None:
        self.template_repository = template_repository

    def send_scheduled_template(self, template_id: int) -> datetime | None:
        self.template_repository.set_enabled(template_id, False)
        return None

    def calculate_next_run(self, template: SendTemplate, after: datetime | None = None) -> datetime | None:
        return after


class _RetryingSendService:
    def __init__(self, retry_at: datetime) -> None:
        self.retry_at = retry_at
        self.calculate_next_run_called = False

    def send_scheduled_template(self, template_id: int) -> datetime | None:
        return self.retry_at

    def calculate_next_run(self, template: SendTemplate, after: datetime | None = None) -> datetime | None:
        self.calculate_next_run_called = True
        return after


class ConfigManagerTests(unittest.TestCase):
    def test_load_coerces_invalid_numeric_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "user_email": "user@example.com",
                        "mailbox": "INBOX",
                        "sync_days": "abc",
                        "sync_batch_size": "5",
                        "sync_scan_limit": "oops",
                        "preview_max_chars": "NaN",
                        "gemini_timeout_seconds": "bad",
                    }
                ),
                encoding="utf-8",
            )
            manager = ConfigManager(path)

            loaded = manager.load()

            self.assertEqual(loaded.sync_days, 30)
            self.assertEqual(loaded.sync_batch_size, 10)
            self.assertGreaterEqual(loaded.sync_scan_limit, loaded.sync_batch_size)
            self.assertEqual(loaded.preview_max_chars, 4000)
            self.assertEqual(loaded.gemini_timeout_seconds, 60)

    def test_load_maps_legacy_hanlim_provider_to_openai_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "user_email": "user@example.com",
                        "mailbox": "INBOX",
                        "ai_provider": LEGACY_AI_PROVIDER_HANLIM,
                        "ai_base_url": "",
                        "gemini_model": "hanlimAI",
                    }
                ),
                encoding="utf-8",
            )
            manager = ConfigManager(path)

            loaded = manager.load()

            self.assertEqual(loaded.ai_provider, AI_PROVIDER_HANLIM)
            self.assertEqual(loaded.ai_base_url, "https://ai.hanliminve.com/llm_hub/api/v1")
            self.assertEqual(loaded.gemini_model, "hanlimAI")

    def test_load_backs_up_corrupt_json_and_restores_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            path.write_text("{broken", encoding="utf-8")
            manager = ConfigManager(path)

            loaded = manager.load()

            backups = list(Path(temp_dir).glob("settings.broken-*.json"))
            self.assertEqual(loaded.user_email, "")
            self.assertTrue(path.exists())
            self.assertEqual(len(backups), 1)


class SchedulingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SendService(None, None, None, None, logging.getLogger("test"))

    def test_one_shot_template_catches_up_when_overdue_and_unsent(self) -> None:
        template = SendTemplate(
            id=1,
            template_name="one-shot",
            subject="subject",
            body="body",
            to_list=["to@example.com"],
            cc_list=[],
            attachment_paths=[],
            repeat_type="none",
            send_time="09:00",
            first_send_at="2026-03-08 09:00:00",
            enabled=True,
        )

        next_run = self.service.calculate_next_run(template, after=datetime(2026, 3, 8, 10, 0, 0))

        self.assertEqual(next_run, datetime(2026, 3, 8, 10, 0, 0))

    def test_recurring_template_uses_send_time_after_first_send(self) -> None:
        template = SendTemplate(
            id=2,
            template_name="daily",
            subject="subject",
            body="body",
            to_list=["to@example.com"],
            cc_list=[],
            attachment_paths=[],
            repeat_type="daily",
            send_time="09:00",
            first_send_at="2026-03-08 15:30:00",
            enabled=True,
            last_sent_at="2026-03-08 15:30:00",
        )

        next_run = self.service.calculate_next_run(template, after=datetime(2026, 3, 8, 18, 0, 0))

        self.assertEqual(next_run, datetime(2026, 3, 9, 9, 0, 0))

    def test_recurring_template_skips_past_slots_before_first_send(self) -> None:
        template = SendTemplate(
            id=4,
            template_name="daily",
            subject="subject",
            body="body",
            to_list=["to@example.com"],
            cc_list=[],
            attachment_paths=[],
            repeat_type="daily",
            send_time="09:00",
            first_send_at="2026-03-08 09:00:00",
            enabled=True,
        )

        next_run = self.service.calculate_next_run(template, after=datetime(2026, 3, 10, 15, 0, 0))

        self.assertEqual(next_run, datetime(2026, 3, 11, 9, 0, 0))

    def test_invalid_template_datetime_returns_none(self) -> None:
        template = SendTemplate(
            id=3,
            template_name="broken",
            subject="subject",
            body="body",
            to_list=["to@example.com"],
            cc_list=[],
            attachment_paths=[],
            repeat_type="daily",
            send_time="09:00",
            first_send_at="invalid",
            enabled=True,
        )

        self.assertIsNone(self.service.calculate_next_run(template))

    def test_one_shot_scheduled_template_is_disabled_after_success(self) -> None:
        template = SendTemplate(
            id=10,
            template_name="one-shot",
            subject="subject",
            body="body",
            to_list=["to@example.com"],
            cc_list=[],
            attachment_paths=[],
            repeat_type="none",
            send_time="09:00",
            first_send_at="2026-03-09 09:00:00",
            enabled=True,
        )
        template_repository = _FakeTemplateRepository(template)
        send_log_repository = _FakeSendLogRepository()
        smtp_client = _FakeSmtpClient()
        service = SendService(
            _FakeConfigManager(),
            smtp_client,
            template_repository,
            send_log_repository,
            logging.getLogger("test"),
        )

        service.send_scheduled_template(template.id)

        self.assertFalse(template.enabled)
        self.assertIsNotNone(template.last_sent_at)
        self.assertEqual(template_repository.enabled_updates, [False])
        self.assertEqual(len(smtp_client.sent_messages), 1)

    def test_one_shot_scheduled_template_is_disabled_after_failure(self) -> None:
        template = SendTemplate(
            id=11,
            template_name="one-shot",
            subject="subject",
            body="body",
            to_list=["to@example.com"],
            cc_list=[],
            attachment_paths=[],
            repeat_type="none",
            send_time="09:00",
            first_send_at="2026-03-09 09:00:00",
            enabled=True,
        )
        template_repository = _FakeTemplateRepository(template)
        send_log_repository = _FakeSendLogRepository()
        service = SendService(
            _FakeConfigManager(),
            _FakeSmtpClient(should_fail=True),
            template_repository,
            send_log_repository,
            logging.getLogger("test"),
        )

        before = datetime.now()
        retry_at = service.send_scheduled_template(template.id)
        after = datetime.now()

        self.assertTrue(template.enabled)
        self.assertIsNone(template.last_sent_at)
        self.assertEqual(template_repository.enabled_updates, [])
        self.assertEqual(send_log_repository.entries[0][1], "failed")
        self.assertIsNotNone(retry_at)
        assert retry_at is not None
        self.assertGreaterEqual(retry_at, before + SendService.RETRY_DELAY)
        self.assertLessEqual(retry_at, after + SendService.RETRY_DELAY + timedelta(seconds=1))

    def test_scheduler_does_not_reschedule_disabled_template_after_run(self) -> None:
        template = SendTemplate(
            id=12,
            template_name="one-shot",
            subject="subject",
            body="body",
            to_list=["to@example.com"],
            cc_list=[],
            attachment_paths=[],
            repeat_type="none",
            send_time="09:00",
            first_send_at="2026-03-09 09:00:00",
            enabled=True,
        )
        template_repository = _FakeTemplateRepository(template)
        send_service = _DisablingSendService(template_repository)
        manager = SchedulerManager(
            SimpleNamespace(load=lambda: AppConfig(sync_interval_minutes=60)),
            template_repository,
            send_service,
            _NoopMailboxService(),
            logging.getLogger("test"),
        )
        manager.scheduler = _FakeScheduler()

        manager._run_template_job(template.id)

        self.assertFalse(template.enabled)
        self.assertEqual(manager.scheduler.added_jobs, [])

    def test_scheduler_uses_retry_time_without_recomputing_schedule(self) -> None:
        template = SendTemplate(
            id=13,
            template_name="daily",
            subject="subject",
            body="body",
            to_list=["to@example.com"],
            cc_list=[],
            attachment_paths=[],
            repeat_type="daily",
            send_time="09:00",
            first_send_at="2026-03-09 09:00:00",
            enabled=True,
        )
        template_repository = _FakeTemplateRepository(template)
        retry_at = datetime(2026, 3, 10, 9, 5, 0)
        send_service = _RetryingSendService(retry_at)
        manager = SchedulerManager(
            SimpleNamespace(load=lambda: AppConfig(sync_interval_minutes=60)),
            template_repository,
            send_service,
            _NoopMailboxService(),
            logging.getLogger("test"),
        )
        manager.scheduler = _FakeScheduler()

        manager._run_template_job(template.id)

        self.assertFalse(send_service.calculate_next_run_called)
        self.assertEqual(len(manager.scheduler.added_jobs), 1)
        _, kwargs = manager.scheduler.added_jobs[0]
        self.assertEqual(kwargs["id"], "template_13")
        self.assertEqual(kwargs["args"], [template.id])
        self.assertEqual(kwargs["trigger"].run_date.replace(tzinfo=None), retry_at)

    def test_scheduler_serializes_manual_and_background_mailbox_cycles(self) -> None:
        started = Event()
        release = Event()

        class _BlockingMailboxService:
            @staticmethod
            def run_once(include_failed_analysis: bool, progress_callback=None):
                del include_failed_analysis, progress_callback
                started.set()
                release.wait(1.0)
                return SimpleNamespace(
                    saved_count=0,
                    analysis_success_count=0,
                    analysis_failure_count=0,
                    analysis_warning=None,
                )

        manager = SchedulerManager(
            SimpleNamespace(load=lambda: AppConfig(sync_interval_minutes=60)),
            _FakeTemplateRepository(None),
            SimpleNamespace(),
            _BlockingMailboxService(),
            logging.getLogger("test"),
        )

        worker_errors: list[Exception] = []

        def _run_manual() -> None:
            try:
                manager.run_mailbox_cycle(include_failed_analysis=True, source="manual")
            except Exception as exc:  # noqa: BLE001
                worker_errors.append(exc)

        worker = Thread(target=_run_manual, daemon=True)
        worker.start()
        self.assertTrue(started.wait(0.5))

        with self.assertRaises(RuntimeError):
            manager.run_mailbox_cycle(include_failed_analysis=False, source="auto")

        release.set()
        worker.join(1.0)
        self.assertEqual(worker_errors, [])


class ImapMetadataTests(unittest.TestCase):
    def test_metadata_batch_keeps_missing_date_and_generates_fallback_message_id(self) -> None:
        client = IMAPClient(_DummySecretStore(), logging.getLogger("test"))

        metadata = client._fetch_message_metadata_batch(_FakeImapClient(), [b"101", b"102"])

        self.assertEqual(metadata[b"101"][0], "<exists@example.com>")
        self.assertIsNone(metadata[b"101"][1])
        self.assertTrue(metadata[b"102"][0].startswith("missing-"))
        self.assertEqual(metadata[b"102"][1], datetime(2026, 3, 8, 16, 30, 0))


class SettingsHelperTests(unittest.TestCase):
    def test_custom_shell_settings_include_new_preview_model(self) -> None:
        html = (Path(__file__).resolve().parents[1] / "app" / "ui" / "custom_board" / "index.html").read_text(
            encoding="utf-8"
        )

        self.assertIn('option value="gemini-3.1-flash-lite-preview"', html)
        self.assertIn('option value="hanlimAI"', html)

    def test_settings_submission_uses_current_payload(self) -> None:
        base = AppConfig(user_email="saved@example.com", mailbox="INBOX")
        pending, password, api_key, hanlim_api_key = _build_settings_submission(
            base,
            {
                "config": {
                    "user_email": "draft@example.com",
                    "mailbox": "InboxQA",
                    "sync_days": 14,
                    "sync_batch_size": 80,
                    "sync_scan_limit": 120,
                    "preview_max_chars": 6000,
                    "gemini_timeout_seconds": 90,
                    "ai_provider": AI_PROVIDER_HANLIM,
                    "ai_base_url": "",
                    "gemini_model": "hanlimAI",
                    "store_raw_body": True,
                },
                "password": " draft-pass ",
                "api_key": "",
                "hanlim_api_key": " corp-key ",
            },
        )

        self.assertEqual(pending.user_email, "draft@example.com")
        self.assertEqual(pending.mailbox, "InboxQA")
        self.assertEqual(pending.sync_days, 14)
        self.assertEqual(pending.ai_provider, AI_PROVIDER_HANLIM)
        self.assertEqual(pending.ai_base_url, "https://ai.hanliminve.com/llm_hub/api/v1")
        self.assertEqual(pending.gemini_model, "hanlimAI")
        self.assertTrue(pending.store_raw_body)
        self.assertEqual(password, "draft-pass")
        self.assertIsNone(api_key)
        self.assertEqual(hanlim_api_key, "corp-key")

    def test_mailbox_submission_prefers_current_password(self) -> None:
        pending, secret = _build_mailbox_test_submission(
            AppConfig(user_email="saved@example.com", mailbox="INBOX"),
            {
                "config": {
                    "user_email": "draft@example.com",
                    "mailbox": "Review",
                },
                "password": " draft-pass ",
            },
        )

        self.assertEqual(pending.user_email, "draft@example.com")
        self.assertEqual(pending.mailbox, "Review")
        self.assertEqual(secret, "draft-pass")


class SyncServiceRetentionTests(unittest.TestCase):
    def test_sync_service_prunes_local_mails_older_than_sync_days(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_manager = ConfigManager(root / "config" / "settings.json")
            config_manager.save(
                AppConfig(
                    user_email="user@example.com",
                    mailbox="INBOX",
                    sync_days=3,
                )
            )
            database = DatabaseManager(root / "data" / "app.db")
            mail_repository = MailRepository(database)
            app_settings_repository = AppSettingsRepository(database)
            old_attachment_dir = root / "cache" / "mail_attachments" / "old-message"
            old_attachment_dir.mkdir(parents=True, exist_ok=True)
            old_attachment = old_attachment_dir / "report.txt"
            old_attachment.write_text("stale", encoding="utf-8")

            mail_repository.create_from_parsed_mail(
                _parsed_mail(
                    "old-message",
                    "Old mail",
                    datetime.now() - timedelta(days=5),
                    attachment_names=["report.txt"],
                    attachment_paths=[old_attachment.relative_to(root).as_posix()],
                )
            )
            mail_repository.create_from_parsed_mail(
                _parsed_mail(
                    "recent-message",
                    "Recent mail",
                    datetime.now() - timedelta(days=1),
                )
            )

            service = SyncService(
                config_manager=config_manager,
                imap_client=_EmptyFetchImapClient(),
                mail_repository=mail_repository,
                app_settings_repository=app_settings_repository,
                logger=logging.getLogger("test"),
                storage_root=root,
            )

            result = service.sync_recent_mail()

            self.assertEqual(result.deleted_old_count, 1)
            self.assertEqual([mail.message_id for mail in mail_repository.list_mails()], ["recent-message"])
            self.assertFalse(old_attachment.exists())
            self.assertFalse(old_attachment_dir.exists())
            del service
            del mail_repository
            del app_settings_repository
            del database
            gc.collect()

    def test_sync_service_continues_backfill_until_cursor_is_exhausted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_manager = ConfigManager(root / "config" / "settings.json")
            config_manager.save(AppConfig(user_email="user@example.com", mailbox="INBOX", sync_days=30))
            database = DatabaseManager(root / "data" / "app.db")
            mail_repository = MailRepository(database)
            app_settings_repository = AppSettingsRepository(database)

            mail_repository.create_from_parsed_mail(
                _parsed_mail(
                    "existing-message",
                    "Existing mail",
                    datetime(2026, 3, 10, 9, 0, 0),
                )
            )
            fake_imap = _RecordingFetchImapClient(
                [
                    SyncFetchResult(
                        messages=[],
                        scanned_count=200,
                        limited_by_batch=True,
                        next_cursor_before_uid=500,
                    ),
                    SyncFetchResult(
                        messages=[],
                        scanned_count=120,
                        limited_by_batch=False,
                        next_cursor_before_uid=None,
                    ),
                    SyncFetchResult(
                        messages=[],
                        scanned_count=5,
                        limited_by_batch=False,
                        next_cursor_before_uid=None,
                    ),
                ]
            )
            service = SyncService(
                config_manager=config_manager,
                imap_client=fake_imap,
                mail_repository=mail_repository,
                app_settings_repository=app_settings_repository,
                logger=logging.getLogger("test"),
                storage_root=root,
            )

            service.sync_recent_mail()
            snapshot = app_settings_repository.get_snapshot()

            self.assertEqual(fake_imap.calls[0]["before_uid"], None)
            self.assertIsNone(fake_imap.calls[0]["since_datetime"])
            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertEqual(snapshot.sync_cursor_before_uid, 500)
            self.assertEqual(snapshot.sync_cursor_mailbox, "INBOX")
            self.assertEqual(snapshot.sync_cursor_days, 30)
            self.assertIsNone(snapshot.sync_backfill_completed_at)

            service.sync_recent_mail()
            snapshot = app_settings_repository.get_snapshot()

            self.assertEqual(fake_imap.calls[1]["before_uid"], 500)
            self.assertIsNone(fake_imap.calls[1]["since_datetime"])
            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertIsNone(snapshot.sync_cursor_before_uid)
            self.assertIsNotNone(snapshot.sync_backfill_completed_at)

            service.sync_recent_mail()

            self.assertEqual(fake_imap.calls[2]["before_uid"], None)
            self.assertEqual(
                fake_imap.calls[2]["since_datetime"],
                datetime(2026, 3, 9, 9, 0, 0),
            )
            del service
            del mail_repository
            del app_settings_repository
            del database
            gc.collect()

    def test_sync_service_keeps_backfill_open_when_fetch_failures_occur(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_manager = ConfigManager(root / "config" / "settings.json")
            config_manager.save(AppConfig(user_email="user@example.com", mailbox="INBOX", sync_days=30))
            database = DatabaseManager(root / "data" / "app.db")
            mail_repository = MailRepository(database)
            app_settings_repository = AppSettingsRepository(database)
            fake_imap = _RecordingFetchImapClient(
                [
                    SyncFetchResult(
                        messages=[],
                        scanned_count=120,
                        limited_by_batch=False,
                        next_cursor_before_uid=None,
                        had_fetch_failures=True,
                    ),
                ]
            )
            service = SyncService(
                config_manager=config_manager,
                imap_client=fake_imap,
                mail_repository=mail_repository,
                app_settings_repository=app_settings_repository,
                logger=logging.getLogger("test"),
                storage_root=root,
            )

            service.sync_recent_mail()
            snapshot = app_settings_repository.get_snapshot()

            self.assertEqual(fake_imap.calls[0]["before_uid"], None)
            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertIsNone(snapshot.sync_cursor_before_uid)
            self.assertIsNone(snapshot.sync_backfill_completed_at)
            del service
            del mail_repository
            del app_settings_repository
            del database
            gc.collect()

    def test_completed_action_items_remain_visible_until_mail_retention_expires(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_manager = ConfigManager(root / "config" / "settings.json")
            config_manager.save(AppConfig(user_email="user@example.com", mailbox="INBOX", sync_days=3))
            database = DatabaseManager(root / "data" / "app.db")
            mail_repository = MailRepository(database)
            app_settings_repository = AppSettingsRepository(database)

            old_mail_id = mail_repository.create_from_parsed_mail(
                _parsed_mail(
                    "old-completed",
                    "Old completed mail",
                    datetime.now() - timedelta(days=5),
                )
            )
            recent_mail_id = mail_repository.create_from_parsed_mail(
                _parsed_mail(
                    "recent-completed",
                    "Recent completed mail",
                    datetime.now() - timedelta(days=1),
                )
            )
            assert old_mail_id is not None
            assert recent_mail_id is not None

            mail_repository.replace_action_items(
                int(old_mail_id),
                scope="my",
                action_items=["Archive old task"],
                due_date=None,
                owner="user@example.com",
            )
            mail_repository.replace_action_items(
                int(recent_mail_id),
                scope="my",
                action_items=["Keep recent task"],
                due_date=None,
                owner="user@example.com",
            )
            for item in mail_repository.list_open_my_action_items():
                mail_repository.mark_action_item_done(item.id, True)

            self.assertEqual(
                [item.action_text for item in mail_repository.list_completed_my_action_items(limit=10)],
                ["Keep recent task", "Archive old task"],
            )

            service = SyncService(
                config_manager=config_manager,
                imap_client=_EmptyFetchImapClient(),
                mail_repository=mail_repository,
                app_settings_repository=app_settings_repository,
                logger=logging.getLogger("test"),
                storage_root=root,
            )

            pruned_count = service.prune_local_mail_retention()

            self.assertEqual(pruned_count, 1)
            self.assertEqual(
                [item.action_text for item in mail_repository.list_completed_my_action_items(limit=10)],
                ["Keep recent task"],
            )
            del service
            del mail_repository
            del app_settings_repository
            del database
            gc.collect()


class MailboxServiceTests(unittest.TestCase):
    def test_run_once_retries_failed_analysis_by_default(self) -> None:
        analysis_service = _FakeAnalysisService()
        service = MailboxService(_FakeSyncService(), analysis_service, logging.getLogger("test"))

        service.run_once()

        self.assertTrue(analysis_service.include_failed)


if __name__ == "__main__":
    unittest.main()
