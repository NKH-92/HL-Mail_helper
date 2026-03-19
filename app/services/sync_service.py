"""Mail synchronization service."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

from app.core.config_manager import ConfigManager
from app.core.security import MAIL_PASSWORD_KEY
from app.db.models import ParsedMail, SyncResult
from app.db.repositories import AppSettingsRepository, MailRepository, now_iso
from app.mail.imap_client import IMAPClient
from app.mail.parser import parse_email_bytes


class SyncService:
    """Fetch recent mails and persist new records."""

    def __init__(
        self,
        config_manager: ConfigManager,
        imap_client: IMAPClient,
        mail_repository: MailRepository,
        app_settings_repository: AppSettingsRepository,
        logger: logging.Logger,
        storage_root: Path,
    ) -> None:
        self.config_manager = config_manager
        self.imap_client = imap_client
        self.mail_repository = mail_repository
        self.app_settings_repository = app_settings_repository
        self.logger = logger
        self.storage_root = storage_root
        self.attachment_storage_root = self.storage_root / "cache" / "mail_attachments"
        self.attachment_storage_root.mkdir(parents=True, exist_ok=True)

    def get_unavailability_reason(self) -> str | None:
        """Return a user-facing reason when sync cannot run."""

        config = self.config_manager.load()
        if not config.user_email.strip():
            return "메일 주소가 설정되지 않아 메일 동기화를 건너뛰었습니다."
        if not self.imap_client.secret_store.has_secret(MAIL_PASSWORD_KEY):
            return "메일 비밀번호가 저장되지 않아 메일 동기화를 건너뛰었습니다."
        return None

    def get_status_snapshot(self) -> dict[str, object]:
        """Return persisted sync progress and current backfill state for the UI."""

        config = self.config_manager.load()
        snapshot = self.app_settings_repository.get_snapshot()
        backfill_active, before_uid = self._resolve_backfill_cursor(snapshot, config.mailbox, config.sync_days)
        return {
            "mailbox": config.mailbox,
            "sync_days": config.sync_days,
            "last_sync_at": snapshot.last_sync_at if snapshot else None,
            "backfill_active": backfill_active,
            "backfill_completed_at": snapshot.sync_backfill_completed_at if snapshot else None,
            "cursor_before_uid": before_uid,
            "sync_warning": self.get_unavailability_reason(),
        }

    def sync_recent_mail(self, days: int | None = None) -> SyncResult:
        """Sync recent mailbox content and return a run summary."""

        config = self.config_manager.load()
        target_days = days or config.sync_days
        snapshot = self.app_settings_repository.get_snapshot()
        backlog_active, before_uid = self._resolve_backfill_cursor(snapshot, config.mailbox, target_days)
        latest_received_at = self.mail_repository.get_latest_received_at() if not backlog_active else None
        incremental_since = latest_received_at - timedelta(days=1) if latest_received_at else None
        fetch_result = self.imap_client.fetch_recent_messages(
            config=config,
            days=target_days,
            existing_message_id_lookup=self.mail_repository.filter_existing_message_ids,
            batch_size=config.sync_batch_size,
            since_datetime=incremental_since,
            before_uid=before_uid,
        )
        next_cursor_before_uid = before_uid if fetch_result.had_fetch_failures else fetch_result.next_cursor_before_uid
        fetched_messages = fetch_result.messages
        saved_count = 0
        duplicate_count = 0
        skipped_old_count = 0
        parse_error_count = 0
        preview_limit = max(config.preview_max_chars, 50_000) if config.store_raw_body else config.preview_max_chars
        cutoff = datetime.now() - timedelta(days=target_days)
        for fetched_message in fetched_messages:
            try:
                parsed_mail: ParsedMail = parse_email_bytes(
                    fetched_message.raw_bytes,
                    preview_max_chars=preview_limit,
                    fallback_message_id=fetched_message.fallback_message_id,
                    received_at_override=fetched_message.received_at,
                    attachment_storage_root=self.attachment_storage_root,
                    attachment_relative_root=self.storage_root,
                )
                if parsed_mail.received_at and parsed_mail.received_at < cutoff:
                    skipped_old_count += 1
                    continue
                inserted_id = self.mail_repository.create_from_parsed_mail(parsed_mail)
                if inserted_id:
                    saved_count += 1
                else:
                    duplicate_count += 1
            except Exception as exc:  # noqa: BLE001
                parse_error_count += 1
                self.logger.exception("Failed to parse/store a message: %s", exc)
        deleted_old_count = self.prune_local_mail_retention(days=target_days)
        current_sync_at = now_iso()
        next_backfill_completed_at = self._resolve_backfill_completed_at(
            snapshot=snapshot,
            mailbox=config.mailbox,
            target_days=target_days,
            backlog_active=backlog_active,
            next_cursor_before_uid=next_cursor_before_uid,
            had_fetch_failures=fetch_result.had_fetch_failures,
            current_sync_at=current_sync_at,
        )
        self.app_settings_repository.upsert(
            sync_days=target_days,
            mailbox=config.mailbox,
            gemini_model=config.gemini_model,
            last_sync_at=current_sync_at,
            sync_cursor_before_uid=next_cursor_before_uid,
            sync_cursor_mailbox=config.mailbox,
            sync_cursor_days=target_days,
            sync_backfill_completed_at=next_backfill_completed_at,
        )
        result = SyncResult(
            scanned_count=fetch_result.scanned_count,
            fetched_count=len(fetched_messages),
            saved_count=saved_count,
            skipped_old_count=skipped_old_count,
            duplicate_count=duplicate_count,
            parse_error_count=parse_error_count,
            limited_by_batch=fetch_result.limited_by_batch,
            deleted_old_count=deleted_old_count,
        )
        self.logger.info(
            "Sync result: scanned=%s fetched=%s saved=%s skipped_old=%s duplicates=%s parse_errors=%s pruned=%s limited=%s backfill=%s fetch_failures=%s next_cursor=%s",
            result.scanned_count,
            result.fetched_count,
            result.saved_count,
            result.skipped_old_count,
            result.duplicate_count,
            result.parse_error_count,
            result.deleted_old_count,
            result.limited_by_batch,
            backlog_active,
            fetch_result.had_fetch_failures,
            next_cursor_before_uid,
        )
        return result

    def prune_local_mail_retention(self, days: int | None = None) -> int:
        """Delete locally cached mails and attachments that fall outside the retention window."""

        config = self.config_manager.load()
        target_days = days or config.sync_days
        cutoff = datetime.now() - timedelta(days=target_days)
        deleted_count, attachment_paths = self.mail_repository.delete_mails_older_than(cutoff)
        for path_value in attachment_paths:
            self._delete_cached_attachment(path_value)
        if deleted_count:
            self.logger.info("Pruned %s local mails older than %s days.", deleted_count, target_days)
        return deleted_count

    def _delete_cached_attachment(self, path_value: str) -> None:
        attachment_path = Path(path_value).expanduser()
        resolved_path = attachment_path.resolve() if attachment_path.is_absolute() else (self.storage_root / attachment_path).resolve()
        if not self._is_within_directory(resolved_path, self.storage_root):
            return
        try:
            resolved_path.unlink(missing_ok=True)
        except OSError:
            self.logger.warning("Failed to delete cached attachment: %s", resolved_path, exc_info=True)
            return
        self._prune_empty_attachment_dirs(resolved_path.parent)

    def _prune_empty_attachment_dirs(self, start_dir: Path) -> None:
        current = start_dir
        attachment_root = self.attachment_storage_root.resolve()
        while self._is_within_directory(current, attachment_root) and current != attachment_root:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    @staticmethod
    def _is_within_directory(path: Path, directory: Path) -> bool:
        try:
            path.relative_to(directory.resolve())
            return True
        except ValueError:
            return False

    @staticmethod
    def _resolve_backfill_cursor(snapshot, mailbox: str, target_days: int) -> tuple[bool, int | None]:
        if snapshot is None:
            return True, None

        cursor_matches = snapshot.sync_cursor_mailbox == mailbox and snapshot.sync_cursor_days == target_days
        if cursor_matches and snapshot.sync_cursor_before_uid is not None:
            return True, int(snapshot.sync_cursor_before_uid)

        backfill_completed = bool(
            cursor_matches
            and snapshot.sync_backfill_completed_at
        )
        if backfill_completed:
            return False, None

        return True, None

    @staticmethod
    def _resolve_backfill_completed_at(
        *,
        snapshot,
        mailbox: str,
        target_days: int,
        backlog_active: bool,
        next_cursor_before_uid: int | None,
        had_fetch_failures: bool,
        current_sync_at: str,
    ) -> str | None:
        if backlog_active:
            if had_fetch_failures:
                return None
            if next_cursor_before_uid is None:
                return current_sync_at
            return None

        if (
            snapshot is not None
            and snapshot.sync_cursor_mailbox == mailbox
            and snapshot.sync_cursor_days == target_days
            and snapshot.sync_backfill_completed_at
        ):
            return snapshot.sync_backfill_completed_at

        return current_sync_at
