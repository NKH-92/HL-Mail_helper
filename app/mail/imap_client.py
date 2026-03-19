"""IMAP transport helpers."""

from __future__ import annotations

import email
import hashlib
import imaplib
import logging
import re
import socket
import ssl
from datetime import datetime, timedelta
from email.policy import default
from email.utils import parsedate_to_datetime
from typing import Callable

from app.core.config_manager import AppConfig
from app.core.time_utils import to_local_naive
from app.core.security import MAIL_PASSWORD_KEY, SecretStore
from app.db.models import FetchedMessage, SyncFetchResult


class IMAPClient:
    """Thin IMAP wrapper for testing and fetching recent mails."""

    def __init__(self, secret_store: SecretStore, logger: logging.Logger) -> None:
        self.secret_store = secret_store
        self.logger = logger

    def test_connection(self, config: AppConfig, password_override: str | None = None) -> tuple[bool, str]:
        """Validate login and mailbox selection."""

        try:
            with self._connect(config, password_override=password_override) as client:
                status, _ = client.select(config.mailbox)
                if status != "OK":
                    raise RuntimeError(f"메일함 선택 실패: {config.mailbox}")
            return True, "IMAP 연결 성공"
        except Exception as exc:  # noqa: BLE001
            self.logger.exception("IMAP connection test failed: %s", exc)
            return False, self._format_connection_error(exc, config)

    def list_mailboxes(self, config: AppConfig, password_override: str | None = None) -> list[str]:
        """Return mailbox names reported by the IMAP server."""

        with self._connect(config, password_override=password_override) as client:
            status, data = client.list()
            if status != "OK":
                raise RuntimeError("메일함 목록을 가져오지 못했습니다.")

        mailboxes: list[str] = []
        for raw_item in data or []:
            if not raw_item:
                continue
            decoded = raw_item.decode("utf-8", errors="replace") if isinstance(raw_item, bytes) else str(raw_item)
            mailbox_name = self._parse_mailbox_name(decoded)
            if mailbox_name and mailbox_name not in mailboxes:
                mailboxes.append(mailbox_name)
        return mailboxes

    def fetch_recent_messages(
        self,
        config: AppConfig,
        days: int | None = None,
        existing_message_ids: set[str] | None = None,
        existing_message_id_lookup: Callable[[list[str]], set[str]] | None = None,
        batch_size: int | None = None,
        since_datetime: datetime | None = None,
        before_uid: int | None = None,
    ) -> SyncFetchResult:
        """Fetch a limited batch of recent unseen messages."""

        target_days = days or config.sync_days
        target_batch_size = batch_size or config.sync_batch_size
        cutoff = datetime.now() - timedelta(days=target_days)
        effective_since = max(cutoff, since_datetime) if since_datetime else cutoff
        since_date = effective_since.strftime("%d-%b-%Y")
        scan_limit = max(target_batch_size, config.sync_scan_limit)
        messages: list[FetchedMessage] = []
        scanned_count = 0
        limited_by_batch = False
        selected_metadata: dict[bytes, tuple[str, datetime | None]] = {}
        had_fetch_failures = False

        with self._connect(config) as client:
            status, _ = client.select(config.mailbox)
            if status != "OK":
                raise RuntimeError(f"IMAP 메일함을 열 수 없습니다: {config.mailbox}")

            message_uids = self._search_candidate_uids(client, config.mailbox, since_date)
            if before_uid is not None:
                message_uids = [uid for uid in message_uids if self._uid_to_int(uid) < before_uid]
            total_candidate_count = len(message_uids)
            if total_candidate_count > scan_limit:
                message_uids = message_uids[-scan_limit:]
            candidate_uids = list(reversed(message_uids))
            selected_uids: list[bytes] = []
            examined_count = 0
            last_examined_uid: bytes | None = None

            for chunk in self._chunk_ids(candidate_uids, size=20):
                if examined_count >= scan_limit or len(selected_uids) >= target_batch_size:
                    break
                metadata_map = self._fetch_message_metadata_batch(client, chunk)
                if len(metadata_map) != len(chunk):
                    had_fetch_failures = True
                known_message_ids = (
                    existing_message_id_lookup(
                        [message_id for message_id, _ in metadata_map.values() if message_id]
                    )
                    if existing_message_id_lookup and metadata_map
                    else set()
                )
                for msg_uid in chunk:
                    if examined_count >= scan_limit or len(selected_uids) >= target_batch_size:
                        break
                    examined_count += 1
                    scanned_count += 1
                    last_examined_uid = msg_uid
                    metadata = metadata_map.get(msg_uid)
                    if metadata is None:
                        continue
                    message_id, received_at = metadata
                    if message_id and (
                        (existing_message_ids and message_id in existing_message_ids)
                        or (known_message_ids and message_id in known_message_ids)
                    ):
                        continue
                    if received_at and received_at < cutoff:
                        continue
                    selected_uids.append(msg_uid)
                    selected_metadata[msg_uid] = metadata

            for msg_uid in selected_uids:
                fetch_status, payload = client.uid("fetch", msg_uid, "(BODY.PEEK[] UID)")
                if fetch_status != "OK":
                    self.logger.warning("Skipping message fetch failure for uid=%s", msg_uid)
                    had_fetch_failures = True
                    continue
                appended = False
                for part in payload:
                    if isinstance(part, tuple) and len(part) == 2:
                        fallback_message_id, received_at = selected_metadata.get(msg_uid, (None, None))
                        messages.append(
                            FetchedMessage(
                                raw_bytes=part[1],
                                fallback_message_id=fallback_message_id if fallback_message_id and fallback_message_id.startswith("missing-") else None,
                                received_at=received_at,
                            )
                        )
                        appended = True
                if not appended:
                    had_fetch_failures = True

        remaining_candidates = max(0, total_candidate_count - examined_count)
        next_cursor_before_uid = (
            self._uid_to_int(last_examined_uid)
            if remaining_candidates > 0 and last_examined_uid is not None
            else None
        )
        limited_by_batch = len(selected_uids) >= target_batch_size and remaining_candidates > 0
        self.logger.info(
            "Fetched %s raw messages from mailbox=%s after scanning %s candidates (batch_limited=%s next_cursor=%s)",
            len(messages),
            config.mailbox,
            scanned_count,
            limited_by_batch,
            next_cursor_before_uid,
        )
        return SyncFetchResult(
            messages=messages,
            scanned_count=scanned_count,
            limited_by_batch=limited_by_batch,
            next_cursor_before_uid=next_cursor_before_uid,
            had_fetch_failures=had_fetch_failures,
        )

    def _search_candidate_uids(
        self,
        client: imaplib.IMAP4,
        mailbox: str,
        since_date: str,
    ) -> list[bytes]:
        status, data = client.uid("search", None, "SINCE", since_date)
        if status != "OK":
            self.logger.warning("IMAP SINCE search failed, falling back to ALL search.")
            status, data = client.uid("search", None, "ALL")
        if status != "OK":
            raise RuntimeError("IMAP 메일 검색에 실패했습니다.")

        message_uids = data[0].split() if data and data[0] else []
        if not message_uids:
            self.logger.info(
                "IMAP SINCE search returned 0 results for mailbox=%s since=%s. Falling back to recent ALL subset.",
                mailbox,
                since_date,
            )
            status, data = client.uid("search", None, "ALL")
            if status != "OK":
                raise RuntimeError("IMAP 전체 메일 검색에 실패했습니다.")
            message_uids = data[0].split() if data and data[0] else []
        return message_uids

    def _fetch_message_metadata_batch(
        self,
        client: imaplib.IMAP4,
        msg_ids: list[bytes],
    ) -> dict[bytes, tuple[str, datetime | None]]:
        if not msg_ids:
            return {}

        fetch_ids = b",".join(msg_ids)
        fetch_status, payload = client.uid("fetch", fetch_ids, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID DATE)] UID)")
        if fetch_status != "OK":
            self.logger.warning("Skipping header fetch failure for uids=%s", fetch_ids)
            return {}

        metadata: dict[bytes, tuple[str, datetime | None]] = {}
        for part in payload:
            if not isinstance(part, tuple) or len(part) != 2:
                continue

            response_line = part[0]
            header_bytes = part[1]
            match = re.search(rb"UID (\d+)", response_line)
            if not match:
                continue

            uid = match.group(1)
            message = email.message_from_bytes(header_bytes, policy=default)
            message_id = (message.get("Message-ID") or "").strip()
            if not message_id:
                message_id = f"missing-{self._stable_header_id(header_bytes)}"
            received_at: datetime | None = None
            if message.get("Date"):
                try:
                    parsed = parsedate_to_datetime(message.get("Date"))
                    received_at = to_local_naive(parsed)
                except Exception:  # noqa: BLE001
                    received_at = None
            metadata[uid] = (message_id, received_at)
        return metadata

    def _connect(self, config: AppConfig, password_override: str | None = None) -> imaplib.IMAP4:
        password = password_override if password_override is not None else self.secret_store.get_secret(MAIL_PASSWORD_KEY)
        if not password:
            raise ValueError("메일 비밀번호가 keyring에 저장되어 있지 않습니다.")

        if config.imap_use_ssl:
            client: imaplib.IMAP4 = imaplib.IMAP4_SSL(config.imap_host, config.imap_port)
        else:
            client = imaplib.IMAP4(config.imap_host, config.imap_port)
            if getattr(config, "imap_use_starttls", False):
                client.starttls(ssl_context=ssl.create_default_context())
        client.login(config.user_email, password)
        return client

    def _format_connection_error(self, exc: Exception, config: AppConfig) -> str:
        """Return a user-facing error message with likely fixes."""

        base = f"IMAP 연결 실패: {exc}"
        if isinstance(exc, ssl.SSLError) and "WRONG_VERSION_NUMBER" in str(exc).upper():
            if config.imap_use_ssl:
                return (
                    f"{base}\n\n"
                    "현재 설정은 IMAP SSL이 켜져 있는데, 서버가 해당 포트에서 SSL 연결을 받지 않는 것으로 보입니다.\n"
                    f"확인 항목: 서버={config.imap_host}, 포트={config.imap_port}, SSL=켜짐\n"
                    "일반적으로 993 포트는 SSL 켬, 143 포트는 SSL 끔 조합을 사용합니다."
                )
            return (
                f"{base}\n\n"
                "서버가 현재 포트에서 다른 암호화 방식을 기대하는 것으로 보입니다. "
                "IMAP 포트와 SSL 설정을 다시 확인하세요."
            )

        if isinstance(exc, socket.timeout):
            return (
                f"{base}\n\n"
                "서버 응답 시간이 초과되었습니다. 서버 주소, 포트, 사내망 또는 VPN 연결 상태를 확인하세요."
            )

        if isinstance(exc, socket.gaierror):
            return (
                f"{base}\n\n"
                "IMAP 서버 주소를 해석하지 못했습니다. 서버 주소 오타 여부를 확인하세요."
            )

        if isinstance(exc, imaplib.IMAP4.error):
            return (
                f"{base}\n\n"
                "로그인 또는 메일함 선택에 실패했습니다. 메일 주소, 비밀번호, 메일함 이름(INBOX)을 확인하세요."
            )

        return base

    @staticmethod
    def _parse_mailbox_name(list_response_line: str) -> str:
        """Extract the mailbox name from one IMAP LIST response line."""

        match = re.search(r' "([^"]+)"$', list_response_line)
        if match:
            return match.group(1)
        parts = list_response_line.rsplit(" ", 1)
        if len(parts) == 2:
            return parts[1].strip('"')
        return list_response_line.strip()

    @staticmethod
    def _chunk_ids(message_ids: list[bytes], size: int) -> list[list[bytes]]:
        return [message_ids[index : index + size] for index in range(0, len(message_ids), size)]

    @staticmethod
    def _stable_header_id(header_bytes: bytes) -> str:
        return hashlib.sha256(header_bytes).hexdigest()[:24]

    @staticmethod
    def _uid_to_int(value: bytes | str | int) -> int:
        if isinstance(value, bytes):
            return int(value.decode("ascii", errors="ignore") or "0")
        return int(value)
