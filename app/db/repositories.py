"""Repository classes for SQLite persistence."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from threading import RLock
from typing import Any

from app.db.database import DatabaseManager
from app.db.models import (
    ActionItemRecord,
    AppSettingsSnapshot,
    DashboardStats,
    MailTemplate,
    MailRecord,
    ParsedMail,
    SendLogRecord,
    SendTemplate,
    ThreadOverview,
    TodoItemRecord,
)


LOW_CONFIDENCE_THRESHOLD = 0.65


def now_iso() -> str:
    """Return current timestamp in ISO format without timezone."""

    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse a stored datetime value when possible."""

    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _normalized_email(value: str | None) -> str:
    """Normalize email-like identifiers for comparisons."""

    return str(value or "").strip().lower()


def _priority_weight(priority: str | None) -> int:
    """Return a numeric weight for priority comparisons."""

    return {"high": 3, "medium": 2, "low": 1}.get(str(priority or "").lower(), 0)


def _collapse_text(value: str | None, limit: int = 160) -> str:
    """Compact free text into a short single-line snippet."""

    collapsed = " ".join((value or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[: max(0, limit - 3)].rstrip()}..."


def _format_short_datetime(value: str | None) -> str:
    """Format stored timestamps for compact UI copy."""

    parsed = _parse_datetime(value)
    if not parsed:
        return value or "-"
    return parsed.strftime("%Y-%m-%d %H:%M")


def _thread_sort_key(thread: ThreadOverview) -> tuple[int, datetime, int, int, int, int, int]:
    """Sort high-priority threads first, then pull near deadlines upward."""

    latest_at = _parse_datetime(thread.latest_received_at) or datetime.min
    due_at = _parse_datetime(thread.due_date) or datetime.max
    return (
        -thread.priority_score,
        due_at,
        -latest_at.toordinal(),
        -latest_at.hour,
        -latest_at.minute,
        -latest_at.second,
        -thread.latest_mail_id,
    )


@dataclass(slots=True)
class _ThreadOverviewCacheState:
    """Per-user in-memory cache for thread overviews."""

    threads_by_key: dict[str, ThreadOverview] = field(default_factory=dict)
    sorted_threads: list[ThreadOverview] = field(default_factory=list)
    invalid_keys: set[str] = field(default_factory=set)


class MailRepository:
    """Persistence helpers for mails and their analysis."""

    def __init__(self, database: DatabaseManager) -> None:
        self.database = database
        self._thread_overview_cache_lock = RLock()
        self._thread_overview_cache: dict[str, _ThreadOverviewCacheState] = {}
        self._thread_overview_cache_serial = 0

    def _invalidate_thread_overview_cache(self, thread_keys: list[str] | None = None) -> None:
        normalized_thread_keys = _normalize_thread_keys(thread_keys or [])
        with self._thread_overview_cache_lock:
            self._thread_overview_cache_serial += 1
            if normalized_thread_keys:
                for cache_state in self._thread_overview_cache.values():
                    cache_state.invalid_keys.update(normalized_thread_keys)
            else:
                self._thread_overview_cache.clear()
        with self.database.connect() as connection:
            if normalized_thread_keys:
                placeholders = ", ".join("?" for _ in normalized_thread_keys)
                connection.execute(
                    f"DELETE FROM thread_overview_cache WHERE thread_key IN ({placeholders})",
                    normalized_thread_keys,
                )
            else:
                connection.execute("DELETE FROM thread_overview_cache")
            connection.commit()

    def _get_cached_thread_overview_snapshot(
        self,
        user_email: str,
    ) -> tuple[dict[str, ThreadOverview], list[ThreadOverview], set[str], int] | None:
        cache_key = _normalized_email(user_email)
        with self._thread_overview_cache_lock:
            cache_state = self._thread_overview_cache.get(cache_key)
            if cache_state is None:
                return None
            return (
                dict(cache_state.threads_by_key),
                list(cache_state.sorted_threads),
                set(cache_state.invalid_keys),
                self._thread_overview_cache_serial,
            )

    def _store_cached_thread_overviews(
        self,
        user_email: str,
        *,
        threads_by_key: dict[str, ThreadOverview],
        sorted_threads: list[ThreadOverview],
        cleared_invalid_keys: set[str] | None = None,
        snapshot_serial: int | None = None,
    ) -> None:
        cache_key = _normalized_email(user_email)
        with self._thread_overview_cache_lock:
            concurrent_invalidation = (
                snapshot_serial is not None and snapshot_serial != self._thread_overview_cache_serial
            )
            cache_state = self._thread_overview_cache.get(cache_key)
            if cache_state is None:
                if concurrent_invalidation:
                    return
                cache_state = self._thread_overview_cache.setdefault(cache_key, _ThreadOverviewCacheState())
            existing_invalid_keys = set(cache_state.invalid_keys)
            cache_state.threads_by_key = dict(threads_by_key)
            cache_state.sorted_threads = list(sorted_threads)
            if not threads_by_key and not sorted_threads:
                cache_state.invalid_keys = existing_invalid_keys if concurrent_invalidation else set()
                return
            if concurrent_invalidation:
                cache_state.invalid_keys = existing_invalid_keys
                return
            if cleared_invalid_keys:
                cache_state.invalid_keys.difference_update(cleared_invalid_keys)

    def _list_persisted_thread_overviews(
        self,
        user_email: str,
        thread_keys: list[str],
    ) -> dict[str, ThreadOverview]:
        normalized_user_email = _normalized_email(user_email)
        normalized_thread_keys = _normalize_thread_keys(thread_keys)
        if not normalized_thread_keys:
            return {}

        placeholders = ", ".join("?" for _ in normalized_thread_keys)
        params: list[Any] = [normalized_user_email, *normalized_thread_keys]
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT thread_key, overview_json
                FROM thread_overview_cache
                WHERE user_email = ? AND thread_key IN ({placeholders})
                """,
                params,
            ).fetchall()

        persisted: dict[str, ThreadOverview] = {}
        for row in rows:
            thread_key = str(row["thread_key"] or "").strip().lower()
            if not thread_key:
                continue
            try:
                payload = json.loads(row["overview_json"] or "{}")
                persisted[thread_key] = ThreadOverview(**payload)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return persisted

    def _persist_thread_overviews(self, user_email: str, threads: list[ThreadOverview]) -> None:
        normalized_user_email = _normalized_email(user_email)
        rows = [
            (
                str(thread.thread_key or "").strip().lower(),
                normalized_user_email,
                json.dumps(asdict(thread), ensure_ascii=False),
                now_iso(),
            )
            for thread in threads
            if str(thread.thread_key or "").strip()
        ]
        if not rows:
            return

        with self.database.connect() as connection:
            connection.executemany(
                """
                INSERT INTO thread_overview_cache (thread_key, user_email, overview_json, computed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(thread_key, user_email) DO UPDATE SET
                    overview_json = excluded.overview_json,
                    computed_at = excluded.computed_at
                """,
                rows,
            )
            connection.commit()

    def _build_missing_thread_overviews(
        self,
        user_email: str,
        thread_keys: list[str],
    ) -> dict[str, ThreadOverview]:
        normalized_thread_keys = _normalize_thread_keys(thread_keys)
        if not normalized_thread_keys:
            return {}

        mails_by_thread = self.list_thread_mails_by_keys(normalized_thread_keys, limit_per_thread=None)
        open_actions_by_thread = self.list_thread_action_items_by_keys(normalized_thread_keys)

        built_threads: dict[str, ThreadOverview] = {}
        for thread_key in normalized_thread_keys:
            mails = mails_by_thread.get(thread_key, [])
            if not mails:
                continue
            built_threads[thread_key] = self._build_thread_overview(
                mails=mails,
                open_actions=open_actions_by_thread.get(thread_key, []),
                user_email=user_email,
            )

        self._persist_thread_overviews(user_email, list(built_threads.values()))
        return built_threads

    @staticmethod
    def _get_mail_thread_key(connection: Any, mail_id: int) -> str | None:
        row = connection.execute("SELECT thread_key FROM mails WHERE id = ?", (mail_id,)).fetchone()
        if not row or not row["thread_key"]:
            return None
        return str(row["thread_key"]).strip().lower()

    @staticmethod
    def _get_action_item_thread_key(connection: Any, action_item_id: int) -> tuple[int | None, str | None]:
        row = connection.execute(
            """
            SELECT ai.mail_id, m.thread_key
            FROM action_items ai
            JOIN mails m ON m.id = ai.mail_id
            WHERE ai.id = ?
            """,
            (action_item_id,),
        ).fetchone()
        if not row:
            return None, None
        thread_key = str(row["thread_key"] or "").strip().lower() if row["thread_key"] else None
        return int(row["mail_id"]), thread_key

    def create_from_parsed_mail(self, parsed_mail: ParsedMail) -> int | None:
        """Insert a new mail if message-id does not already exist."""

        timestamp = now_iso()
        with self.database.connect() as connection:
            thread_key = self._resolve_thread_key(connection, parsed_mail)
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO mails (
                    message_id, subject, normalized_subject, thread_key, in_reply_to, references_json,
                    sender_name, sender_email, to_list_json, cc_list_json, received_at, raw_preview,
                    attachment_names_json, attachment_paths_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    parsed_mail.message_id,
                    parsed_mail.subject,
                    parsed_mail.normalized_subject,
                    thread_key,
                    parsed_mail.in_reply_to,
                    json.dumps(parsed_mail.references, ensure_ascii=False),
                    parsed_mail.sender_name,
                    parsed_mail.sender_email,
                    json.dumps(parsed_mail.to_list, ensure_ascii=False),
                    json.dumps(parsed_mail.cc_list, ensure_ascii=False),
                    parsed_mail.received_at.isoformat(sep=" ") if parsed_mail.received_at else None,
                    parsed_mail.raw_preview,
                    json.dumps(parsed_mail.attachment_names, ensure_ascii=False),
                    json.dumps(parsed_mail.attachment_paths, ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )
            connection.commit()
            if cursor.lastrowid:
                self._invalidate_thread_overview_cache([thread_key])
                return int(cursor.lastrowid)
            return None

    def get_message_id_set(self) -> set[str]:
        """Return known message ids for duplicate avoidance."""

        with self.database.connect() as connection:
            rows = connection.execute("SELECT message_id FROM mails").fetchall()
        return {row["message_id"] for row in rows if row["message_id"]}

    def filter_existing_message_ids(self, message_ids: list[str] | set[str]) -> set[str]:
        """Return the subset of message ids that already exist locally."""

        normalized_ids = [str(message_id or "").strip() for message_id in message_ids if str(message_id or "").strip()]
        if not normalized_ids:
            return set()

        existing_ids: set[str] = set()
        chunk_size = 250
        with self.database.connect() as connection:
            for start in range(0, len(normalized_ids), chunk_size):
                chunk = normalized_ids[start : start + chunk_size]
                placeholders = ", ".join("?" for _ in chunk)
                rows = connection.execute(
                    f"SELECT message_id FROM mails WHERE message_id IN ({placeholders})",
                    chunk,
                ).fetchall()
                existing_ids.update(str(row["message_id"]) for row in rows if row["message_id"])
        return existing_ids

    def get_latest_received_at(self) -> datetime | None:
        """Return the most recent received timestamp stored locally."""

        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT received_at
                FROM mails
                WHERE received_at IS NOT NULL
                ORDER BY received_at DESC
                LIMIT 1
                """
            ).fetchone()
        if not row or not row["received_at"]:
            return None
        try:
            return datetime.fromisoformat(row["received_at"])
        except ValueError:
            return None

    def delete_mails_older_than(self, cutoff: datetime) -> tuple[int, list[str]]:
        """Delete local mails older than the cutoff and return attachment paths to clean up."""

        cutoff_text = cutoff.replace(microsecond=0).isoformat(sep=" ")
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT attachment_paths_json
                FROM mails
                WHERE received_at IS NOT NULL AND datetime(received_at) < datetime(?)
                """,
                (cutoff_text,),
            ).fetchall()
            attachment_paths: list[str] = []
            for row in rows:
                try:
                    attachment_paths.extend(json.loads(row["attachment_paths_json"] or "[]"))
                except json.JSONDecodeError:
                    continue
            cursor = connection.execute(
                """
                DELETE FROM mails
                WHERE received_at IS NOT NULL AND datetime(received_at) < datetime(?)
                """,
                (cutoff_text,),
            )
            connection.commit()
        deleted_count = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else len(rows)
        if deleted_count:
            self._invalidate_thread_overview_cache()
        return deleted_count, [path for path in attachment_paths if path]

    def count_analysis_backlog(self) -> dict[str, int]:
        """Return pending and failed analysis counts for the dashboard status area."""

        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT analysis_status, COUNT(*) AS item_count
                FROM mails
                WHERE analysis_status IN ('pending', 'failed')
                GROUP BY analysis_status
                """
            ).fetchall()
        counts = {"pending": 0, "failed": 0}
        for row in rows:
            status = str(row["analysis_status"] or "").strip().lower()
            if status in counts:
                counts[status] = int(row["item_count"] or 0)
        return counts

    def list_mails(self, search_text: str = "", ui_filter: str = "전체", limit: int | None = None) -> list[MailRecord]:
        """Return filtered mail list for the dashboard."""

        clauses: list[str] = []
        params: list[Any] = []

        if search_text.strip():
            clauses.append(
                """
                (
                    subject LIKE ? OR normalized_subject LIKE ? OR sender_email LIKE ? OR
                    sender_name LIKE ? OR raw_preview LIKE ? OR category LIKE ?
                )
                """
            )
            like_value = f"%{search_text.strip()}%"
            params.extend([like_value, like_value, like_value, like_value, like_value, like_value])

        filter_map = {
            "직접조치": "my_action_status = 'direct_action'",
            "검토필요": "my_action_status = 'review_needed'",
            "참고전달": "my_action_status = 'reference_only'",
            "품질이슈": "category = 'QLT'",
            "오늘 마감": "date(due_date) = date('now', 'localtime')",
            "미처리": "status = 'todo'",
            "분석실패": "analysis_status = 'failed'",
            "AI 검토": (
                "analysis_status = 'failed' OR "
                "(analysis_status = 'success' AND ("
                "COALESCE(confidence, 0) < ? OR my_action_status = 'review_needed' OR action_classification = 'UNCLEAR'"
                "))"
            ),
        }
        if ui_filter in filter_map:
            clauses.append(filter_map[ui_filter])
            if ui_filter == "AI 검토":
                params.append(LOW_CONFIDENCE_THRESHOLD)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT *
            FROM mails
            {where_sql}
            ORDER BY received_at DESC, id DESC
        """
        if limit is not None and limit > 0:
            query += "\n LIMIT ?"
            params.append(limit)
        with self.database.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_mail_record(row) for row in rows]

    def get_mail(self, mail_id: int) -> MailRecord | None:
        """Fetch one mail by id."""

        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM mails WHERE id = ?", (mail_id,)).fetchone()
        return self._row_to_mail_record(row) if row else None

    def list_pending_analysis(self, limit: int = 20) -> list[MailRecord]:
        """Fetch mails that still need AI processing."""

        return self.list_analysis_targets(limit=limit, include_failed=False)

    def list_analysis_targets(self, limit: int = 20, include_failed: bool = False) -> list[MailRecord]:
        """Fetch analysis backlog, optionally including previously failed mails."""

        if limit <= 0:
            return []

        pending_rows = self._list_analysis_targets_by_status("pending", limit)
        if not include_failed or len(pending_rows) >= limit:
            return pending_rows

        remaining = limit - len(pending_rows)
        failed_rows = self._list_analysis_targets_by_status("failed", remaining)
        return [*pending_rows, *failed_rows]

    def _list_analysis_targets_by_status(self, status: str, limit: int) -> list[MailRecord]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM mails
                WHERE analysis_status = ?
                ORDER BY received_at DESC, id DESC
                LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        return [self._row_to_mail_record(row) for row in rows]

    def save_analysis_result(self, mail_id: int, result: dict[str, Any]) -> None:
        """Save AI analysis output into the mail row."""

        with self.database.connect() as connection:
            thread_key = self._get_mail_thread_key(connection, mail_id)
            self._save_analysis_result_in_connection(connection, mail_id, result)
            connection.commit()
        self._invalidate_thread_overview_cache([thread_key] if thread_key else None)

    def save_analysis_bundle(
        self,
        mail_id: int,
        result: dict[str, Any],
        *,
        mail_action_items: list[str],
        my_action_items: list[str],
        due_date: str | None,
        current_user_email: str,
    ) -> None:
        """Persist analysis output and both action scopes in one transaction."""

        with self.database.connect() as connection:
            thread_key = self._get_mail_thread_key(connection, mail_id)
            self._save_analysis_result_in_connection(connection, mail_id, result)
            self._replace_action_items_in_connection(
                connection,
                mail_id=mail_id,
                scope="mail",
                action_items=mail_action_items,
                due_date=due_date,
                owner=None,
            )
            self._replace_action_items_in_connection(
                connection,
                mail_id=mail_id,
                scope="my",
                action_items=my_action_items,
                due_date=due_date,
                owner=current_user_email,
            )
            connection.commit()
        self._invalidate_thread_overview_cache([thread_key] if thread_key else None)

    def mark_analysis_failed(self, mail_id: int, error_message: str) -> None:
        """Persist failure state when analysis fails."""

        with self.database.connect() as connection:
            thread_key = self._get_mail_thread_key(connection, mail_id)
            connection.execute(
                """
                UPDATE mails
                SET analysis_status = 'failed', analysis_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (error_message[:500], now_iso(), mail_id),
            )
            connection.commit()
        self._invalidate_thread_overview_cache([thread_key] if thread_key else None)

    def update_status(self, mail_id: int, status: str, sync_my_action_items: bool = False) -> None:
        """Update work status."""

        if status not in {"todo", "doing", "done"}:
            raise ValueError(f"Invalid mail status: {status}")
        timestamp = now_iso()
        with self.database.connect() as connection:
            thread_key = self._get_mail_thread_key(connection, mail_id)
            connection.execute(
                "UPDATE mails SET status = ?, updated_at = ? WHERE id = ?",
                (status, timestamp, mail_id),
            )
            if sync_my_action_items and status == "done":
                connection.execute(
                    """
                    UPDATE action_items
                    SET done_flag = 1, completed_at = COALESCE(completed_at, ?), updated_at = ?
                    WHERE mail_id = ? AND scope = 'my' AND done_flag = 0
                    """,
                    (timestamp, timestamp, mail_id),
                )
            connection.commit()
        self._invalidate_thread_overview_cache([thread_key] if thread_key else None)

    def list_action_items(self, mail_id: int, scope: str | None = None) -> list[ActionItemRecord]:
        """Fetch mail action items."""

        clauses = ["mail_id = ?"]
        params: list[Any] = [mail_id]
        if scope:
            clauses.append("scope = ?")
            params.append(scope)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM action_items WHERE {' AND '.join(clauses)} ORDER BY id",
                params,
            ).fetchall()
        return [self._row_to_action_item(row) for row in rows]

    def replace_action_items(
        self,
        mail_id: int,
        scope: str,
        action_items: list[str],
        due_date: str | None,
        owner: str | None,
    ) -> None:
        """Merge action items of a given scope while preserving user history."""

        with self.database.connect() as connection:
            thread_key = self._get_mail_thread_key(connection, mail_id)
            self._replace_action_items_in_connection(
                connection,
                mail_id=mail_id,
                scope=scope,
                action_items=action_items,
                due_date=due_date,
                owner=owner,
            )
            connection.commit()
        self._invalidate_thread_overview_cache([thread_key] if thread_key else None)

    def _save_analysis_result_in_connection(
        self,
        connection: Any,
        mail_id: int,
        result: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            UPDATE mails
            SET category = ?, priority = ?, summary_short = ?, summary_long_json = ?,
                due_date = ?, my_action_required = ?, my_action_status = ?,
                action_classification = ?, action_owner = ?, action_type_json = ?,
                deadline_raw = ?, evidence_json = ?, ownership_reason_json = ?,
                analysis_reason = ?, suggested_task_title = ?, confidence = ?, analysis_status = 'success',
                analysis_error = NULL, updated_at = ?
            WHERE id = ?
            """,
            (
                result["category"],
                result["priority"],
                result["one_line_summary"],
                json.dumps(result["summary_3lines"], ensure_ascii=False),
                result.get("due_date"),
                int(bool(result["my_action_required"])),
                result["my_action_status"],
                result.get("classification"),
                result.get("action_owner"),
                json.dumps(result.get("action_type", []), ensure_ascii=False),
                result.get("deadline_raw"),
                json.dumps(result.get("evidence", []), ensure_ascii=False),
                json.dumps(result["ownership_reason"], ensure_ascii=False),
                result.get("reason"),
                result.get("suggested_task_title"),
                result["confidence"],
                now_iso(),
                mail_id,
            ),
        )

    def _replace_action_items_in_connection(
        self,
        connection: Any,
        *,
        mail_id: int,
        scope: str,
        action_items: list[str],
        due_date: str | None,
        owner: str | None,
    ) -> None:
        timestamp = now_iso()
        existing_rows = connection.execute(
            "SELECT * FROM action_items WHERE mail_id = ? AND scope = ? ORDER BY id",
            (mail_id, scope),
        ).fetchall()
        existing_by_key: dict[str, list[Any]] = {}
        for row in existing_rows:
            existing_by_key.setdefault(_action_key(row["action_text"]), []).append(row)

        preserved_ids: set[int] = set()
        for item in action_items:
            key = _action_key(item)
            matches = existing_by_key.get(key) or []
            if matches:
                matched_row = matches.pop(0)
                preserved_ids.add(int(matched_row["id"]))
                connection.execute(
                    """
                    UPDATE action_items
                    SET action_text = ?, due_date = ?, owner = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (item, due_date, owner, timestamp, matched_row["id"]),
                )
                continue
            connection.execute(
                """
                INSERT INTO action_items (
                    mail_id, scope, action_text, due_date, owner, note, done_flag,
                    created_at, updated_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, '', 0, ?, ?, NULL)
                """,
                (mail_id, scope, item, due_date, owner, timestamp, timestamp),
            )
        for row in existing_rows:
            row_id = int(row["id"])
            if row_id in preserved_ids or bool(row["done_flag"]):
                continue
            connection.execute("DELETE FROM action_items WHERE id = ?", (row_id,))

    def list_open_my_action_items(self) -> list[TodoItemRecord]:
        """Return unfinished personal action items with mail context."""

        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    ai.id,
                    ai.mail_id,
                    ai.action_text,
                    ai.due_date,
                    ai.note,
                    ai.created_at,
                    ai.completed_at,
                    m.subject AS mail_subject,
                    m.sender_email,
                    m.received_at,
                    m.status AS mail_status
                FROM action_items ai
                JOIN mails m ON m.id = ai.mail_id
                WHERE ai.scope = 'my' AND ai.done_flag = 0
                ORDER BY
                    CASE
                        WHEN ai.due_date IS NOT NULL
                             AND ai.due_date != ''
                             AND date(ai.due_date) < date('now', 'localtime')
                        THEN 0
                        WHEN ai.due_date IS NOT NULL
                             AND ai.due_date != ''
                             AND date(ai.due_date) = date('now', 'localtime')
                        THEN 1
                        WHEN m.my_action_status = 'direct_action'
                             AND m.priority = 'high'
                        THEN 2
                        WHEN m.my_action_status = 'direct_action'
                        THEN 3
                        WHEN m.my_action_status = 'review_needed'
                        THEN 4
                        WHEN m.priority = 'high'
                        THEN 5
                        WHEN m.priority = 'medium'
                        THEN 6
                        ELSE 7
                    END,
                    CASE WHEN ai.due_date IS NULL OR ai.due_date = '' THEN 1 ELSE 0 END,
                    ai.due_date ASC,
                    CASE m.priority
                        WHEN 'high' THEN 0
                        WHEN 'medium' THEN 1
                        WHEN 'low' THEN 2
                        ELSE 3
                    END,
                    m.received_at DESC,
                    ai.id ASC
                """
            ).fetchall()
        return [self._row_to_todo_item(row) for row in rows]

    def list_completed_my_action_items(self, limit: int = 50) -> list[TodoItemRecord]:
        """Return recently completed personal action items for history."""

        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    ai.id,
                    ai.mail_id,
                    ai.action_text,
                    ai.due_date,
                    ai.note,
                    ai.created_at,
                    ai.completed_at,
                    m.subject AS mail_subject,
                    m.sender_email,
                    m.received_at,
                    m.status AS mail_status
                FROM action_items ai
                JOIN mails m ON m.id = ai.mail_id
                WHERE ai.scope = 'my' AND ai.done_flag = 1
                ORDER BY ai.completed_at DESC, ai.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_todo_item(row) for row in rows]

    def list_due_my_action_items(self, limit: int = 10) -> list[TodoItemRecord]:
        """Return due and overdue personal action items for reminder banners."""

        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    ai.id,
                    ai.mail_id,
                    ai.action_text,
                    ai.due_date,
                    ai.note,
                    ai.created_at,
                    ai.completed_at,
                    m.subject AS mail_subject,
                    m.sender_email,
                    m.received_at,
                    m.status AS mail_status
                FROM action_items ai
                JOIN mails m ON m.id = ai.mail_id
                WHERE ai.scope = 'my'
                  AND ai.done_flag = 0
                  AND ai.due_date IS NOT NULL
                  AND ai.due_date != ''
                  AND date(ai.due_date) <= date('now', 'localtime')
                ORDER BY ai.due_date ASC, ai.id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_todo_item(row) for row in rows]

    def update_action_item_note(self, action_item_id: int, note: str) -> int | None:
        """Save a user note for one action item and return its parent mail id."""

        timestamp = now_iso()
        with self.database.connect() as connection:
            mail_id, thread_key = self._get_action_item_thread_key(connection, action_item_id)
            if mail_id is None:
                return None
            connection.execute(
                "UPDATE action_items SET note = ?, updated_at = ? WHERE id = ?",
                (note.strip(), timestamp, action_item_id),
            )
            connection.commit()
        self._invalidate_thread_overview_cache([thread_key] if thread_key else None)
        return mail_id

    def mark_action_item_done(self, action_item_id: int, done_flag: bool = True, note: str | None = None) -> int | None:
        """Mark an action item as done or reopened and return its parent mail id."""

        timestamp = now_iso()
        completed_at = timestamp if done_flag else None
        with self.database.connect() as connection:
            mail_id, thread_key = self._get_action_item_thread_key(connection, action_item_id)
            if mail_id is None:
                return None
            connection.execute(
                """
                UPDATE action_items
                SET done_flag = ?, completed_at = ?, updated_at = ?, note = COALESCE(?, note)
                WHERE id = ?
                """,
                (int(done_flag), completed_at, timestamp, note.strip() if note is not None else None, action_item_id),
            )
            connection.commit()
        self._invalidate_thread_overview_cache([thread_key] if thread_key else None)
        return mail_id

    def mark_thread_done(self, thread_key: str) -> int:
        """Mark every mail in a thread as done and complete open personal actions."""

        normalized_thread_key = str(thread_key or "").strip().lower()
        if not normalized_thread_key:
            return 0

        timestamp = now_iso()
        with self.database.connect() as connection:
            mail_rows = connection.execute(
                "SELECT id FROM mails WHERE thread_key = ?",
                (normalized_thread_key,),
            ).fetchall()
            if not mail_rows:
                return 0

            connection.execute(
                """
                UPDATE mails
                SET status = 'done', updated_at = ?
                WHERE thread_key = ?
                """,
                (timestamp, normalized_thread_key),
            )
            connection.execute(
                """
                UPDATE action_items
                SET done_flag = 1, completed_at = ?, updated_at = ?
                WHERE done_flag = 0
                  AND scope = 'my'
                  AND mail_id IN (
                      SELECT id
                      FROM mails
                      WHERE thread_key = ?
                  )
                """,
                (timestamp, timestamp, normalized_thread_key),
            )
            connection.commit()

        self._invalidate_thread_overview_cache([normalized_thread_key])
        return len(mail_rows)

    def count_open_action_items(self, mail_id: int, scope: str = "my") -> int:
        """Return the number of unfinished action items for a mail."""

        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS open_count
                FROM action_items
                WHERE mail_id = ? AND scope = ? AND done_flag = 0
                """,
                (mail_id, scope),
            ).fetchone()
        return int(row["open_count"]) if row else 0

    def list_thread_action_items(
        self,
        thread_key: str,
        *,
        scope: str | None = None,
        include_done: bool = False,
    ) -> list[ActionItemRecord]:
        """Return action items that belong to one thread."""

        normalized_thread_key = str(thread_key or "").strip().lower()
        if not normalized_thread_key:
            return []

        clauses = ["m.thread_key = ?"]
        params: list[Any] = [normalized_thread_key]
        if scope:
            clauses.append("ai.scope = ?")
            params.append(scope)
        if not include_done:
            clauses.append("ai.done_flag = 0")

        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT ai.*
                FROM action_items ai
                JOIN mails m ON m.id = ai.mail_id
                WHERE {' AND '.join(clauses)}
                ORDER BY
                    CASE WHEN ai.done_flag = 0 THEN 0 ELSE 1 END,
                    CASE WHEN ai.due_date IS NULL OR ai.due_date = '' THEN 1 ELSE 0 END,
                    ai.due_date ASC,
                    ai.id ASC
                """,
                params,
            ).fetchall()
        return [self._row_to_action_item(row) for row in rows]

    def list_thread_action_items_by_keys(
        self,
        thread_keys: list[str],
        *,
        include_done: bool = False,
    ) -> dict[str, list[ActionItemRecord]]:
        """Return action items grouped by thread key for a batch of threads."""

        normalized_thread_keys = _normalize_thread_keys(thread_keys)
        if not normalized_thread_keys:
            return {}

        placeholders = ", ".join("?" for _ in normalized_thread_keys)
        clauses = [f"m.thread_key IN ({placeholders})"]
        params: list[Any] = [*normalized_thread_keys]
        if not include_done:
            clauses.append("ai.done_flag = 0")

        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT m.thread_key AS thread_key, ai.*
                FROM action_items ai
                JOIN mails m ON m.id = ai.mail_id
                WHERE {' AND '.join(clauses)}
                ORDER BY
                    m.thread_key ASC,
                    CASE WHEN ai.done_flag = 0 THEN 0 ELSE 1 END,
                    CASE WHEN ai.due_date IS NULL OR ai.due_date = '' THEN 1 ELSE 0 END,
                    ai.due_date ASC,
                    ai.id ASC
                """,
                params,
            ).fetchall()

        grouped: dict[str, list[ActionItemRecord]] = {thread_key: [] for thread_key in normalized_thread_keys}
        for row in rows:
            thread_key = str(row["thread_key"] or "").lower()
            grouped.setdefault(thread_key, []).append(self._row_to_action_item(row))
        return grouped

    def list_review_queue(self, limit: int = 20) -> list[MailRecord]:
        """Return mails that deserve human review."""

        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM mails
                WHERE analysis_status = 'failed'
                   OR (
                        analysis_status = 'success' AND (
                            COALESCE(confidence, 0) < ?
                            OR my_action_status = 'review_needed'
                            OR action_classification = 'UNCLEAR'
                        )
                   )
                ORDER BY
                    CASE WHEN analysis_status = 'failed' THEN 0 ELSE 1 END,
                    COALESCE(confidence, 1) ASC,
                    received_at DESC,
                    id DESC
                LIMIT ?
                """,
                (LOW_CONFIDENCE_THRESHOLD, limit),
            ).fetchall()
        return [self._row_to_mail_record(row) for row in rows]

    def list_thread_mails(self, mail_id: int, limit: int = 8) -> list[MailRecord]:
        """Return the most recent mails in the same thread as the target mail."""

        mail = self.get_mail(mail_id)
        if not mail:
            return []

        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM mails
                WHERE thread_key = ?
                ORDER BY received_at DESC, id DESC
                LIMIT ?
                """,
                (mail.thread_key, limit),
            ).fetchall()

        thread_mails = [self._row_to_mail_record(row) for row in rows]
        if thread_mails:
            return thread_mails

        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM mails
                WHERE normalized_subject = ?
                ORDER BY received_at DESC, id DESC
                LIMIT ?
                """,
                (mail.normalized_subject, limit),
            ).fetchall()
        return [self._row_to_mail_record(row) for row in rows]

    def list_thread_mails_by_key(self, thread_key: str, limit: int = 8) -> list[MailRecord]:
        """Return the most recent mails in the given thread key."""

        normalized_thread_key = str(thread_key or "").strip().lower()
        if not normalized_thread_key:
            return []

        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM mails
                WHERE thread_key = ?
                ORDER BY received_at DESC, id DESC
                LIMIT ?
                """,
                (normalized_thread_key, limit),
            ).fetchall()
        return [self._row_to_mail_record(row) for row in rows]

    def list_thread_mails_by_keys(
        self,
        thread_keys: list[str],
        *,
        limit_per_thread: int | None = 8,
    ) -> dict[str, list[MailRecord]]:
        """Return recent mails grouped by thread key for a batch of threads."""

        normalized_thread_keys = _normalize_thread_keys(thread_keys)
        if not normalized_thread_keys:
            return {}
        if limit_per_thread is not None and limit_per_thread <= 0:
            return {}

        placeholders = ", ".join("?" for _ in normalized_thread_keys)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM mails
                WHERE thread_key IN ({placeholders})
                ORDER BY thread_key ASC, received_at DESC, id DESC
                """,
                normalized_thread_keys,
            ).fetchall()

        grouped: dict[str, list[MailRecord]] = {thread_key: [] for thread_key in normalized_thread_keys}
        for row in rows:
            thread_key = str(row["thread_key"] or "").lower()
            bucket = grouped.setdefault(thread_key, [])
            if limit_per_thread is not None and len(bucket) >= limit_per_thread:
                continue
            bucket.append(self._row_to_mail_record(row))
        return grouped

    def build_thread_summary(self, mail_id: int, limit: int = 5) -> str:
        """Build a compact thread summary for AI input."""

        current_mail = self.get_mail(mail_id)
        if not current_mail:
            return ""

        context_lines: list[str] = []
        for thread_mail in reversed(self.list_thread_mails(mail_id=mail_id, limit=limit + 1)):
            if thread_mail.id == mail_id:
                continue
            snippet = thread_mail.summary_short or thread_mail.raw_preview.replace("\n", " ")
            snippet = snippet[:180].strip()
            context_lines.append(
                f"- {thread_mail.received_at or '-'} | {thread_mail.sender_email or '-'} | "
                f"{thread_mail.subject} | {snippet}"
            )
            if len(context_lines) >= limit:
                break
        return "\n".join(context_lines)

    def get_dashboard_stats(self) -> DashboardStats:
        """Return aggregated counters for the dashboard header."""

        with self.database.connect() as connection:
            total_mails = int(connection.execute("SELECT COUNT(*) AS count FROM mails").fetchone()["count"])
            total_threads = int(
                connection.execute(
                    "SELECT COUNT(DISTINCT thread_key) AS count FROM mails WHERE thread_key IS NOT NULL AND thread_key != ''"
                ).fetchone()["count"]
            )
            open_mail_count = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM mails WHERE status IN ('todo', 'doing')"
                ).fetchone()["count"]
            )
            open_my_action_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM action_items
                    WHERE scope = 'my' AND done_flag = 0
                    """
                ).fetchone()["count"]
            )
            due_today_action_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM action_items
                    WHERE scope = 'my'
                      AND done_flag = 0
                      AND due_date IS NOT NULL
                      AND due_date != ''
                      AND date(due_date) = date('now', 'localtime')
                    """
                ).fetchone()["count"]
            )
            overdue_action_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM action_items
                    WHERE scope = 'my'
                      AND done_flag = 0
                      AND due_date IS NOT NULL
                      AND due_date != ''
                      AND date(due_date) < date('now', 'localtime')
                    """
                ).fetchone()["count"]
            )
            failed_analysis_count = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM mails WHERE analysis_status = 'failed'"
                ).fetchone()["count"]
            )
            review_queue_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM mails
                     WHERE analysis_status = 'failed'
                        OR (
                             analysis_status = 'success' AND (
                                 COALESCE(confidence, 0) < ?
                                 OR my_action_status = 'review_needed'
                                 OR action_classification = 'UNCLEAR'
                             )
                        )
                     """,
                    (LOW_CONFIDENCE_THRESHOLD,),
                ).fetchone()["count"]
            )
            completed_my_action_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM action_items
                    WHERE scope = 'my' AND done_flag = 1
                    """
                ).fetchone()["count"]
            )
        return DashboardStats(
            total_mails=total_mails,
            total_threads=total_threads,
            open_mail_count=open_mail_count,
            open_my_action_count=open_my_action_count,
            due_today_action_count=due_today_action_count,
            overdue_action_count=overdue_action_count,
            failed_analysis_count=failed_analysis_count,
            review_queue_count=review_queue_count,
            completed_my_action_count=completed_my_action_count,
        )

    def list_thread_overviews(self, user_email: str = "", limit: int | None = None) -> list[ThreadOverview]:
        """Aggregate mails into thread-level priority and follow-up views."""

        with self.database.connect() as connection:
            thread_rows = connection.execute(
                """
                SELECT DISTINCT thread_key
                FROM mails
                WHERE thread_key IS NOT NULL AND thread_key != ''
                ORDER BY thread_key ASC
                """
            ).fetchall()

        thread_keys = [str(row["thread_key"]).strip().lower() for row in thread_rows if row["thread_key"]]
        if not thread_keys:
            self._store_cached_thread_overviews(
                user_email,
                threads_by_key={},
                sorted_threads=[],
                cleared_invalid_keys=set(),
            )
            return []

        cache_snapshot = self._get_cached_thread_overview_snapshot(user_email)
        cached_threads_by_key = cache_snapshot[0] if cache_snapshot is not None else {}
        cached_sorted_threads = cache_snapshot[1] if cache_snapshot is not None else []
        invalid_keys = cache_snapshot[2] if cache_snapshot is not None else set(thread_keys)
        snapshot_serial = cache_snapshot[3] if cache_snapshot is not None else None
        current_thread_key_set = set(thread_keys)

        removed_keys = [thread_key for thread_key in cached_threads_by_key if thread_key not in current_thread_key_set]
        for thread_key in removed_keys:
            cached_threads_by_key.pop(thread_key, None)

        refresh_keys = [
            thread_key
            for thread_key in thread_keys
            if thread_key not in cached_threads_by_key or thread_key in invalid_keys
        ]

        if refresh_keys:
            persisted_threads = self._list_persisted_thread_overviews(user_email, refresh_keys)
            missing_thread_keys = [thread_key for thread_key in refresh_keys if thread_key not in persisted_threads]
            built_threads = self._build_missing_thread_overviews(user_email, missing_thread_keys)
            resolved_threads = {**persisted_threads, **built_threads}
            for thread_key in refresh_keys:
                thread = resolved_threads.get(thread_key)
                if thread is None:
                    cached_threads_by_key.pop(thread_key, None)
                    continue
                cached_threads_by_key[thread_key] = thread

        threads = [cached_threads_by_key[thread_key] for thread_key in thread_keys if thread_key in cached_threads_by_key]
        if (
            cache_snapshot is not None
            and not refresh_keys
            and not removed_keys
            and not invalid_keys
            and cached_sorted_threads
        ):
            threads = list(cached_sorted_threads)
        else:
            threads.sort(key=_thread_sort_key)

        self._store_cached_thread_overviews(
            user_email,
            threads_by_key=cached_threads_by_key,
            sorted_threads=threads,
            cleared_invalid_keys=set([*refresh_keys, *removed_keys]),
            snapshot_serial=snapshot_serial,
        )
        if limit is not None and limit > 0:
            return threads[:limit]
        return threads

    def _build_thread_overview(
        self,
        mails: list[MailRecord],
        open_actions: list[ActionItemRecord],
        user_email: str,
    ) -> ThreadOverview:
        """Derive one ranked thread view from stored mail and action data."""

        sorted_mails = sorted(
            mails,
            key=lambda item: (_parse_datetime(item.received_at) or datetime.min, item.id),
            reverse=True,
        )
        latest_mail = sorted_mails[0]
        previous_mail = sorted_mails[1] if len(sorted_mails) > 1 else None
        oldest_mail = sorted_mails[-1]
        user_email_normalized = _normalized_email(user_email)
        today = date.today()
        now = datetime.now()
        unresolved_thread_mails = [mail for mail in sorted_mails if mail.status != "done"]

        def _mail_focus_score(mail: MailRecord) -> tuple[int, datetime, int]:
            due_at = _parse_datetime(mail.due_date)
            score = {"direct_action": 60, "review_needed": 30, "reference_only": 5}.get(mail.my_action_status or "", 0)
            if due_at and due_at.date() < today:
                score += 35
            elif due_at and due_at.date() == today:
                score += 28
            elif due_at and due_at.date() <= today + timedelta(days=1):
                score += 18
            score += {"high": 15, "medium": 9, "low": 3}.get(mail.priority or "", 0)
            if mail.suggested_task_title:
                score += 4
            return score, _parse_datetime(mail.received_at) or datetime.min, mail.id

        focus_mail = max(unresolved_thread_mails or sorted_mails, key=_mail_focus_score)

        participants: list[str] = []
        participant_keys: set[str] = set()
        for mail in sorted_mails:
            label = mail.sender_name or mail.sender_email or "-"
            participant_key = _normalized_email(mail.sender_email) or label.lower()
            if participant_key in participant_keys:
                continue
            participant_keys.add(participant_key)
            participants.append(label)

        my_open_actions = [item for item in open_actions if item.scope == "my"]
        mail_open_actions = [item for item in open_actions if item.scope == "mail"]

        due_candidates: list[tuple[datetime, str]] = []
        for due_text in [mail.due_date for mail in sorted_mails if mail.status != "done"]:
            due_at = _parse_datetime(due_text)
            if due_at:
                due_candidates.append((due_at, str(due_text)))
        for action in open_actions:
            due_at = _parse_datetime(action.due_date)
            if due_at:
                due_candidates.append((due_at, str(action.due_date)))
        due_candidates.sort(key=lambda item: item[0])
        thread_due_date = due_candidates[0][1] if due_candidates else None
        due_at = due_candidates[0][0] if due_candidates else None
        is_overdue = bool(due_at and due_at.date() < today)
        is_due_today = bool(due_at and due_at.date() == today)
        is_due_soon = bool(due_at and today <= due_at.date() <= today + timedelta(days=1))

        highest_priority_mail = max(
            sorted_mails,
            key=lambda item: (_priority_weight(item.priority), _parse_datetime(item.received_at) or datetime.min),
        )
        latest_at = _parse_datetime(latest_mail.received_at)
        latest_from_me = bool(
            user_email_normalized and _normalized_email(latest_mail.sender_email) == user_email_normalized
        )
        latest_outbound = next(
            (
                mail
                for mail in sorted_mails
                if user_email_normalized and _normalized_email(mail.sender_email) == user_email_normalized
            ),
            None,
        )
        latest_inbound = next(
            (
                mail
                for mail in sorted_mails
                if not user_email_normalized or _normalized_email(mail.sender_email) != user_email_normalized
            ),
            None,
        )
        latest_outbound_at = _parse_datetime(latest_outbound.received_at) if latest_outbound else None
        latest_inbound_at = _parse_datetime(latest_inbound.received_at) if latest_inbound else None
        has_new_external_reply = bool(
            latest_inbound_at and (latest_outbound_at is None or latest_inbound_at > latest_outbound_at)
        )
        has_reply_signal = any("reply" in mail.action_types for mail in unresolved_thread_mails or sorted_mails)
        has_approval_signal = any(
            mail.action_classification == "APPROVAL_REQUEST" or "approve" in mail.action_types
            for mail in unresolved_thread_mails or sorted_mails
        )
        direct_action_needed = bool(
            my_open_actions or any(mail.my_action_status == "direct_action" for mail in unresolved_thread_mails)
        )
        review_needed_thread = bool(any(mail.my_action_status == "review_needed" for mail in unresolved_thread_mails))
        reply_needed = bool((has_reply_signal or has_new_external_reply) and direct_action_needed)
        waiting_for_reply = bool(
            user_email_normalized
            and latest_from_me
            and not my_open_actions
            and not direct_action_needed
            and latest_mail.status != "done"
        )
        no_reply_for_three_days = bool(waiting_for_reply and latest_at and latest_at <= now - timedelta(days=3))

        has_failed_analysis = any(mail.analysis_status == "failed" for mail in sorted_mails)
        needs_review = has_failed_analysis or any(
            mail.analysis_status == "success"
            and (
                (mail.confidence or 0) < LOW_CONFIDENCE_THRESHOLD
                or mail.my_action_status == "review_needed"
                or mail.action_classification == "UNCLEAR"
            )
            for mail in sorted_mails
        )

        if is_overdue:
            follow_up_status = "overdue"
            follow_up_label = "Overdue"
        elif is_due_today or is_due_soon:
            follow_up_status = "deadline_soon"
            follow_up_label = "Deadline soon"
        elif has_approval_signal and (direct_action_needed or review_needed_thread):
            follow_up_status = "approval_pending"
            follow_up_label = "Approval pending"
        elif reply_needed:
            follow_up_status = "reply_needed"
            follow_up_label = "Reply needed"
        elif no_reply_for_three_days:
            follow_up_status = "no_reply_3d"
            follow_up_label = "No reply in 3d"
        elif waiting_for_reply:
            follow_up_status = "waiting_for_reply"
            follow_up_label = "Waiting reply"
        elif direct_action_needed:
            follow_up_status = "action_needed"
            follow_up_label = "Action needed"
        elif review_needed_thread or needs_review:
            follow_up_status = "review_needed"
            follow_up_label = "Review needed"
        else:
            follow_up_status = "tracked"
            follow_up_label = "Tracked"

        latest_summary = latest_mail.summary_short or _collapse_text(latest_mail.raw_preview, limit=160) or latest_mail.subject
        root_summary = oldest_mail.summary_short or _collapse_text(oldest_mail.raw_preview, limit=120) or oldest_mail.subject
        overall_summary = latest_summary
        if len(sorted_mails) > 1 and root_summary and root_summary != latest_summary:
            overall_summary = f"{root_summary} -> {latest_summary}"

        if previous_mail is None:
            changed_since_last = "First mail in this thread."
        elif latest_mail.due_date and latest_mail.due_date != previous_mail.due_date:
            changed_since_last = f"Deadline moved to {_format_short_datetime(latest_mail.due_date)}."
        else:
            latest_attachments = set(latest_mail.attachment_names)
            previous_attachments = set(previous_mail.attachment_names)
            new_attachments = [name for name in latest_mail.attachment_names if name in latest_attachments - previous_attachments]
            if new_attachments:
                changed_since_last = f"New attachment: {', '.join(new_attachments[:2])}."
            elif _normalized_email(latest_mail.sender_email) != _normalized_email(previous_mail.sender_email):
                changed_since_last = f"New reply from {latest_mail.sender_name or latest_mail.sender_email or 'sender'}."
            elif latest_mail.summary_short and latest_mail.summary_short != previous_mail.summary_short:
                changed_since_last = latest_mail.summary_short
            else:
                changed_since_last = _collapse_text(latest_mail.raw_preview, limit=150) or latest_mail.subject

        my_action_texts = [item.action_text for item in my_open_actions if item.action_text.strip()]
        mail_action_texts = [item.action_text for item in mail_open_actions if item.action_text.strip()]
        if my_action_texts:
            current_conclusion = "; ".join(my_action_texts[:2])
        elif focus_mail.suggested_task_title:
            current_conclusion = focus_mail.suggested_task_title
        elif focus_mail.my_action_status == "direct_action":
            current_conclusion = focus_mail.summary_short or _collapse_text(focus_mail.raw_preview, limit=150) or focus_mail.subject
        elif has_approval_signal:
            current_conclusion = "Approval or decision is still pending."
        elif no_reply_for_three_days:
            current_conclusion = "Send a follow-up because there has been no reply for 3 days."
        elif waiting_for_reply:
            current_conclusion = "Monitor the thread until the other side replies."
        elif mail_action_texts:
            current_conclusion = "; ".join(mail_action_texts[:2])
        else:
            current_conclusion = latest_summary

        if follow_up_status == "overdue":
            follow_up_detail = f"Due at {_format_short_datetime(thread_due_date)}."
        elif follow_up_status == "deadline_soon":
            follow_up_detail = f"Due at {_format_short_datetime(thread_due_date)}."
        elif follow_up_status == "approval_pending":
            follow_up_detail = current_conclusion
        elif follow_up_status == "reply_needed":
            if my_action_texts:
                follow_up_detail = my_action_texts[0]
            elif focus_mail.sender_email:
                follow_up_detail = f"Reply to {focus_mail.sender_email}."
            else:
                follow_up_detail = current_conclusion
        elif follow_up_status == "no_reply_3d":
            follow_up_detail = f"Last outbound mail was {_format_short_datetime(latest_mail.received_at)}."
        elif follow_up_status == "waiting_for_reply":
            follow_up_detail = f"Waiting since {_format_short_datetime(latest_mail.received_at)}."
        elif follow_up_status == "action_needed":
            follow_up_detail = my_action_texts[0] if my_action_texts else current_conclusion
        elif follow_up_status == "review_needed":
            follow_up_detail = focus_mail.analysis_reason or latest_mail.analysis_reason or "AI confidence is low or the owner is unclear."
        else:
            follow_up_detail = latest_summary

        importance_score = {"high": 35, "medium": 22, "low": 8}.get(str(highest_priority_mail.priority or "").lower(), 0)
        if direct_action_needed:
            importance_score += 12
        elif latest_mail.my_action_status == "review_needed":
            importance_score += 6
        if has_approval_signal:
            importance_score += 10
        importance_score = min(45, importance_score)

        if is_overdue:
            urgency_score = 35
        elif is_due_today:
            urgency_score = 28
        elif is_due_soon:
            urgency_score = 18
        elif latest_at and latest_at.date() == today and direct_action_needed:
            urgency_score = 10
        else:
            urgency_score = 0

        if reply_needed:
            reply_score = 20
        elif no_reply_for_three_days:
            reply_score = 16
        elif waiting_for_reply:
            reply_score = 10
        else:
            reply_score = 0

        approval_score = 18 if has_approval_signal else 0
        if is_due_today:
            today_score = 15
        elif latest_at and latest_at.date() == today and direct_action_needed:
            today_score = 10
        elif no_reply_for_three_days:
            today_score = 12
        else:
            today_score = 0

        priority_reasons: list[str] = []
        if is_overdue:
            priority_reasons.append("Overdue deadline")
        elif is_due_today:
            priority_reasons.append("Due today")
        elif is_due_soon:
            priority_reasons.append("Deadline soon")
        if reply_needed:
            priority_reasons.append("Reply needed")
        if has_approval_signal:
            priority_reasons.append("Approval pending")
        if no_reply_for_three_days:
            priority_reasons.append("No reply for 3 days")
        if highest_priority_mail.priority == "high":
            priority_reasons.append("High priority")
        if my_open_actions and not reply_needed and not has_approval_signal:
            priority_reasons.append("Open action item")
        if not priority_reasons:
            priority_reasons.append("Recent thread")

        return ThreadOverview(
            thread_key=latest_mail.thread_key,
            latest_mail_id=latest_mail.id,
            thread_subject=latest_mail.normalized_subject or latest_mail.subject,
            latest_received_at=latest_mail.received_at,
            latest_sender_name=latest_mail.sender_name,
            latest_sender_email=latest_mail.sender_email,
            latest_summary=latest_summary,
            overall_summary=overall_summary,
            changed_since_last=changed_since_last,
            current_conclusion=current_conclusion,
            follow_up_status=follow_up_status,
            follow_up_label=follow_up_label,
            follow_up_detail=follow_up_detail,
            due_date=thread_due_date,
            priority=highest_priority_mail.priority or latest_mail.priority,
            category=latest_mail.category or highest_priority_mail.category,
            latest_mail_status=latest_mail.status,
            latest_action_status=latest_mail.my_action_status,
            mail_count=len(sorted_mails),
            participant_count=len(participant_keys),
            participants=participants[:4],
            open_action_count=len(my_open_actions),
            importance_score=importance_score,
            urgency_score=urgency_score,
            reply_score=reply_score,
            approval_score=approval_score,
            today_score=today_score,
            priority_score=importance_score + urgency_score + reply_score + approval_score + today_score,
            priority_reasons=priority_reasons,
            needs_review=needs_review,
            has_failed_analysis=has_failed_analysis,
        )

    def _resolve_thread_key(self, connection: Any, parsed_mail: ParsedMail) -> str:
        """Choose a stable thread key from stored parents or reply headers."""

        candidate_ids = [*parsed_mail.references, parsed_mail.in_reply_to]
        normalized_candidates = [str(item).strip() for item in candidate_ids if str(item or "").strip()]
        if normalized_candidates:
            unique_candidates = list(dict.fromkeys(normalized_candidates))
            placeholders = ", ".join("?" for _ in unique_candidates)
            rows = connection.execute(
                f"SELECT message_id, thread_key FROM mails WHERE message_id IN ({placeholders})",
                unique_candidates,
            ).fetchall()
            thread_key_by_message_id = {
                str(row["message_id"]).strip(): str(row["thread_key"]).lower()
                for row in rows
                if row["message_id"] and row["thread_key"]
            }
            for candidate in normalized_candidates:
                thread_key = thread_key_by_message_id.get(candidate)
                if thread_key:
                    return thread_key

        if parsed_mail.references:
            return parsed_mail.references[0].lower()
        if parsed_mail.in_reply_to:
            return parsed_mail.in_reply_to.lower()
        return parsed_mail.thread_key.lower()

    def _row_to_mail_record(self, row: Any) -> MailRecord:
        return MailRecord(
            id=row["id"],
            message_id=row["message_id"],
            subject=row["subject"],
            normalized_subject=row["normalized_subject"] or row["subject"] or "",
            thread_key=row["thread_key"] or row["message_id"],
            in_reply_to=row["in_reply_to"],
            references=json.loads(row["references_json"] or "[]"),
            sender_name=row["sender_name"] or "",
            sender_email=row["sender_email"] or "",
            to_list=json.loads(row["to_list_json"] or "[]"),
            cc_list=json.loads(row["cc_list_json"] or "[]"),
            received_at=row["received_at"],
            raw_preview=row["raw_preview"] or "",
            attachment_names=json.loads(row["attachment_names_json"] or "[]"),
            attachment_paths=_safe_json_list(row["attachment_paths_json"] if "attachment_paths_json" in row.keys() else "[]"),
            category=row["category"],
            priority=row["priority"],
            summary_short=row["summary_short"],
            summary_long=json.loads(row["summary_long_json"] or "[]"),
            due_date=row["due_date"],
            my_action_required=bool(row["my_action_required"]),
            my_action_status=row["my_action_status"],
            ownership_reason=json.loads(row["ownership_reason_json"] or "[]"),
            confidence=row["confidence"],
            status=row["status"],
            analysis_status=row["analysis_status"],
            analysis_error=row["analysis_error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            action_classification=row["action_classification"] if "action_classification" in row.keys() else None,
            action_owner=row["action_owner"] if "action_owner" in row.keys() else None,
            action_types=_safe_json_list(row["action_type_json"] if "action_type_json" in row.keys() else "[]"),
            deadline_raw=row["deadline_raw"] if "deadline_raw" in row.keys() else None,
            evidence=_safe_json_list(row["evidence_json"] if "evidence_json" in row.keys() else "[]"),
            analysis_reason=row["analysis_reason"] if "analysis_reason" in row.keys() else None,
            suggested_task_title=row["suggested_task_title"] if "suggested_task_title" in row.keys() else None,
        )

    def _row_to_action_item(self, row: Any) -> ActionItemRecord:
        return ActionItemRecord(
            id=row["id"],
            mail_id=row["mail_id"],
            scope=row["scope"],
            action_text=row["action_text"],
            due_date=row["due_date"],
            owner=row["owner"],
            note=row["note"] or "",
            done_flag=bool(row["done_flag"]),
            completed_at=row["completed_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"] or row["created_at"],
        )

    def _row_to_todo_item(self, row: Any) -> TodoItemRecord:
        return TodoItemRecord(
            id=row["id"],
            mail_id=row["mail_id"],
            action_text=row["action_text"],
            due_date=row["due_date"],
            note=row["note"] or "",
            created_at=row["created_at"],
            completed_at=row["completed_at"],
            mail_subject=row["mail_subject"] or "",
            sender_email=row["sender_email"] or "",
            received_at=row["received_at"],
            mail_status=row["mail_status"] or "todo",
        )


class TemplateRepository:
    """Persistence helpers for send templates."""

    def __init__(self, database: DatabaseManager) -> None:
        self.database = database

    def upsert(self, template: SendTemplate) -> int:
        """Create or update a template."""

        timestamp = now_iso()
        with self.database.connect() as connection:
            if template.id is None:
                cursor = connection.execute(
                    """
                    INSERT INTO templates (
                        template_name, subject, body, to_list_json, cc_list_json,
                        attachment_paths_json, repeat_type, send_time, first_send_at,
                        enabled, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        template.template_name,
                        template.subject,
                        template.body,
                        json.dumps(template.to_list, ensure_ascii=False),
                        json.dumps(template.cc_list, ensure_ascii=False),
                        json.dumps(template.attachment_paths, ensure_ascii=False),
                        template.repeat_type,
                        template.send_time,
                        template.first_send_at,
                        int(template.enabled),
                        timestamp,
                        timestamp,
                    ),
                )
                template_id = int(cursor.lastrowid)
            else:
                connection.execute(
                    """
                    UPDATE templates
                    SET template_name = ?, subject = ?, body = ?, to_list_json = ?,
                        cc_list_json = ?, attachment_paths_json = ?, repeat_type = ?,
                        send_time = ?, first_send_at = ?, enabled = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        template.template_name,
                        template.subject,
                        template.body,
                        json.dumps(template.to_list, ensure_ascii=False),
                        json.dumps(template.cc_list, ensure_ascii=False),
                        json.dumps(template.attachment_paths, ensure_ascii=False),
                        template.repeat_type,
                        template.send_time,
                        template.first_send_at,
                        int(template.enabled),
                        timestamp,
                        template.id,
                    ),
                )
                template_id = template.id
            connection.commit()
        return template_id

    def list_all(self) -> list[SendTemplate]:
        """Fetch all templates."""

        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM templates ORDER BY updated_at DESC, id DESC").fetchall()
        return [template for template in (self._row_to_template(row) for row in rows) if template is not None]

    def list_enabled(self) -> list[SendTemplate]:
        """Fetch templates that should be scheduled."""

        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM templates WHERE enabled = 1 ORDER BY id").fetchall()
        return [template for template in (self._row_to_template(row) for row in rows) if template is not None]

    def get(self, template_id: int) -> SendTemplate | None:
        """Fetch one template by id."""

        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM templates WHERE id = ?", (template_id,)).fetchone()
        return self._row_to_template(row) if row else None

    def set_enabled(self, template_id: int, enabled: bool) -> None:
        """Enable or disable a template."""

        with self.database.connect() as connection:
            connection.execute(
                "UPDATE templates SET enabled = ?, updated_at = ? WHERE id = ?",
                (int(enabled), now_iso(), template_id),
            )
            connection.commit()

    def delete(self, template_id: int) -> None:
        """Delete a template."""

        with self.database.connect() as connection:
            connection.execute("DELETE FROM templates WHERE id = ?", (template_id,))
            connection.commit()

    def update_last_sent_at(self, template_id: int, sent_at: str) -> None:
        """Track latest send time."""

        with self.database.connect() as connection:
            connection.execute(
                "UPDATE templates SET last_sent_at = ?, updated_at = ? WHERE id = ?",
                (sent_at, now_iso(), template_id),
            )
            connection.commit()

    def _row_to_template(self, row: Any) -> SendTemplate | None:
        if row is None:
            return None
        return SendTemplate(
            id=row["id"],
            template_name=row["template_name"] or "",
            subject=row["subject"] or "",
            body=row["body"] or "",
            to_list=_safe_json_list(row["to_list_json"]),
            cc_list=_safe_json_list(row["cc_list_json"]),
            attachment_paths=_safe_json_list(row["attachment_paths_json"]),
            repeat_type=(row["repeat_type"] or "none"),
            send_time=row["send_time"] or "09:00",
            first_send_at=row["first_send_at"] or "",
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_sent_at=row["last_sent_at"] if "last_sent_at" in row.keys() else None,
        )


class MailTemplateRepository:
    """Persistence helpers for saved mail form presets."""

    def __init__(self, database: DatabaseManager) -> None:
        self.database = database

    def upsert(self, template: MailTemplate) -> int:
        """Create or update a saved mail template."""

        timestamp = now_iso()
        with self.database.connect() as connection:
            if template.id is None:
                cursor = connection.execute(
                    """
                    INSERT INTO mail_templates (
                        template_name, subject, body, to_list_json, cc_list_json,
                        attachment_paths_json, repeat_type, send_time, first_send_at,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        template.template_name,
                        template.subject,
                        template.body,
                        json.dumps(template.to_list, ensure_ascii=False),
                        json.dumps(template.cc_list, ensure_ascii=False),
                        json.dumps(template.attachment_paths, ensure_ascii=False),
                        template.repeat_type,
                        template.send_time,
                        template.first_send_at,
                        timestamp,
                        timestamp,
                    ),
                )
                template_id = int(cursor.lastrowid)
            else:
                connection.execute(
                    """
                    UPDATE mail_templates
                    SET template_name = ?, subject = ?, body = ?, to_list_json = ?,
                        cc_list_json = ?, attachment_paths_json = ?, repeat_type = ?,
                        send_time = ?, first_send_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        template.template_name,
                        template.subject,
                        template.body,
                        json.dumps(template.to_list, ensure_ascii=False),
                        json.dumps(template.cc_list, ensure_ascii=False),
                        json.dumps(template.attachment_paths, ensure_ascii=False),
                        template.repeat_type,
                        template.send_time,
                        template.first_send_at,
                        timestamp,
                        template.id,
                    ),
                )
                template_id = template.id
            connection.commit()
        return template_id

    def list_all(self) -> list[MailTemplate]:
        """Fetch all saved mail templates."""

        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM mail_templates ORDER BY updated_at DESC, id DESC").fetchall()
        return [template for template in (self._row_to_template(row) for row in rows) if template is not None]

    def get(self, template_id: int) -> MailTemplate | None:
        """Fetch one saved mail template by id."""

        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM mail_templates WHERE id = ?", (template_id,)).fetchone()
        return self._row_to_template(row) if row else None

    def delete(self, template_id: int) -> None:
        """Delete one saved mail template."""

        with self.database.connect() as connection:
            connection.execute("DELETE FROM mail_templates WHERE id = ?", (template_id,))
            connection.commit()

    def _row_to_template(self, row: Any) -> MailTemplate | None:
        if row is None:
            return None
        return MailTemplate(
            id=row["id"],
            template_name=row["template_name"] or "",
            subject=row["subject"] or "",
            body=row["body"] or "",
            to_list=_safe_json_list(row["to_list_json"]),
            cc_list=_safe_json_list(row["cc_list_json"]),
            attachment_paths=_safe_json_list(row["attachment_paths_json"]),
            repeat_type=(row["repeat_type"] or "none"),
            send_time=row["send_time"] or "09:00",
            first_send_at=row["first_send_at"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class SendLogRepository:
    """Persistence helpers for send logs."""

    def __init__(self, database: DatabaseManager) -> None:
        self.database = database

    def create(
        self,
        template_id: int | None,
        result: str,
        recipients: list[str],
        subject: str,
        error_message: str | None,
    ) -> None:
        """Insert a send log row."""

        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO send_logs (template_id, sent_at, result, recipients_json, subject, error_message)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    template_id,
                    now_iso(),
                    result,
                    json.dumps(recipients, ensure_ascii=False),
                    subject,
                    error_message,
                ),
            )
            connection.commit()

    def list_recent(self, limit: int = 100) -> list[SendLogRecord]:
        """Fetch recent send logs."""

        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM send_logs ORDER BY sent_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            SendLogRecord(
                id=row["id"],
                template_id=row["template_id"],
                sent_at=row["sent_at"],
                result=row["result"],
                recipients=json.loads(row["recipients_json"] or "[]"),
                subject=row["subject"],
                error_message=row["error_message"],
            )
            for row in rows
        ]


class AppSettingsRepository:
    """Small helper table for app sync metadata."""

    def __init__(self, database: DatabaseManager) -> None:
        self.database = database

    def upsert(
        self,
        sync_days: int,
        mailbox: str,
        gemini_model: str,
        last_sync_at: str | None,
        *,
        sync_cursor_before_uid: int | None = None,
        sync_cursor_mailbox: str | None = None,
        sync_cursor_days: int | None = None,
        sync_backfill_completed_at: str | None = None,
    ) -> None:
        """Persist current app state snapshot."""

        timestamp = now_iso()
        with self.database.connect() as connection:
            row = connection.execute("SELECT id FROM app_settings ORDER BY id LIMIT 1").fetchone()
            if row:
                connection.execute(
                    """
                    UPDATE app_settings
                    SET sync_days = ?, mailbox = ?, gemini_model = ?, last_sync_at = ?,
                        sync_cursor_before_uid = ?, sync_cursor_mailbox = ?, sync_cursor_days = ?,
                        sync_backfill_completed_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        sync_days,
                        mailbox,
                        gemini_model,
                        last_sync_at,
                        sync_cursor_before_uid,
                        sync_cursor_mailbox,
                        sync_cursor_days,
                        sync_backfill_completed_at,
                        timestamp,
                        row["id"],
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO app_settings (
                        sync_days, mailbox, gemini_model, last_sync_at,
                        sync_cursor_before_uid, sync_cursor_mailbox, sync_cursor_days,
                        sync_backfill_completed_at, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sync_days,
                        mailbox,
                        gemini_model,
                        last_sync_at,
                        sync_cursor_before_uid,
                        sync_cursor_mailbox,
                        sync_cursor_days,
                        sync_backfill_completed_at,
                        timestamp,
                        timestamp,
                    ),
                )
            connection.commit()

    def get_snapshot(self) -> AppSettingsSnapshot | None:
        """Return the latest app settings snapshot when present."""

        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    sync_days,
                    mailbox,
                    gemini_model,
                    last_sync_at,
                    sync_cursor_before_uid,
                    sync_cursor_mailbox,
                    sync_cursor_days,
                    sync_backfill_completed_at,
                    created_at,
                    updated_at
                FROM app_settings
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        if not row:
            return None
        return AppSettingsSnapshot(
            sync_days=row["sync_days"],
            mailbox=row["mailbox"],
            gemini_model=row["gemini_model"],
            last_sync_at=row["last_sync_at"],
            sync_cursor_before_uid=row["sync_cursor_before_uid"],
            sync_cursor_mailbox=row["sync_cursor_mailbox"],
            sync_cursor_days=row["sync_cursor_days"],
            sync_backfill_completed_at=row["sync_backfill_completed_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def _safe_json_list(value: str | None) -> list[str]:
    """Decode list-like JSON columns without raising on malformed rows."""

    try:
        decoded = json.loads(value or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [str(item).strip() for item in decoded if str(item).strip()]


def _action_key(value: str | None) -> str:
    """Build a stable lookup key for preserving action item rows."""

    return " ".join((value or "").strip().lower().split())


def _normalize_thread_keys(values: list[str]) -> list[str]:
    """Normalize and deduplicate thread keys while preserving order."""

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = str(value or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append(key)
    return normalized
