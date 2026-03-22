"""SQLite initialization and connection management."""

from __future__ import annotations

import sqlite3
from pathlib import Path


TABLES_SQL = """
CREATE TABLE IF NOT EXISTS mails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE,
    subject TEXT NOT NULL,
    normalized_subject TEXT NOT NULL DEFAULT '',
    thread_key TEXT NOT NULL DEFAULT '',
    in_reply_to TEXT,
    references_json TEXT NOT NULL DEFAULT '[]',
    sender_name TEXT,
    sender_email TEXT,
    to_list_json TEXT NOT NULL,
    cc_list_json TEXT NOT NULL,
    received_at TEXT,
    body_text TEXT NOT NULL DEFAULT '',
    raw_preview TEXT,
    attachment_names_json TEXT NOT NULL DEFAULT '[]',
    attachment_paths_json TEXT NOT NULL DEFAULT '[]',
    category TEXT,
    priority TEXT,
    summary_short TEXT,
    summary_long_json TEXT,
    due_date TEXT,
    my_action_required INTEGER NOT NULL DEFAULT 0,
    my_action_status TEXT,
    action_classification TEXT,
    action_owner TEXT,
    action_type_json TEXT NOT NULL DEFAULT '[]',
    deadline_raw TEXT,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    ownership_reason_json TEXT,
    analysis_reason TEXT,
    suggested_task_title TEXT,
    confidence REAL,
    is_to_me INTEGER NOT NULL DEFAULT 0,
    is_cc_me INTEGER NOT NULL DEFAULT 0,
    recipient_role TEXT,
    is_system_sender INTEGER NOT NULL DEFAULT 0,
    is_newsletter_like INTEGER NOT NULL DEFAULT 0,
    sender_type TEXT,
    rule_category INTEGER,
    request_present INTEGER,
    request_target TEXT,
    request_target_is_me INTEGER,
    urgency TEXT,
    llm_category INTEGER,
    final_category INTEGER,
    correction_applied INTEGER NOT NULL DEFAULT 0,
    correction_reason TEXT,
    conflict_type TEXT,
    model_name TEXT,
    analyzed_at TEXT,
    raw_llm_json TEXT,
    retention_bucket TEXT NOT NULL DEFAULT 'classified',
    status TEXT NOT NULL DEFAULT 'todo',
    analysis_status TEXT NOT NULL DEFAULT 'pending',
    analysis_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS action_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mail_id INTEGER NOT NULL,
    scope TEXT NOT NULL,
    action_text TEXT NOT NULL,
    due_date TEXT,
    owner TEXT,
    note TEXT NOT NULL DEFAULT '',
    done_flag INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT '',
    completed_at TEXT,
    FOREIGN KEY (mail_id) REFERENCES mails(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_name TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    to_list_json TEXT NOT NULL,
    cc_list_json TEXT NOT NULL,
    attachment_paths_json TEXT NOT NULL,
    repeat_type TEXT NOT NULL DEFAULT 'none',
    send_time TEXT NOT NULL,
    first_send_at TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_sent_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mail_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_name TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    to_list_json TEXT NOT NULL,
    cc_list_json TEXT NOT NULL,
    attachment_paths_json TEXT NOT NULL,
    repeat_type TEXT NOT NULL DEFAULT 'none',
    send_time TEXT NOT NULL DEFAULT '09:00',
    first_send_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS send_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER,
    sent_at TEXT NOT NULL,
    result TEXT NOT NULL,
    recipients_json TEXT NOT NULL,
    subject TEXT NOT NULL,
    error_message TEXT,
    FOREIGN KEY (template_id) REFERENCES templates(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sync_days INTEGER NOT NULL DEFAULT 30,
    mailbox TEXT NOT NULL DEFAULT 'INBOX',
    gemini_model TEXT NOT NULL DEFAULT 'gemini-2.5-flash',
    last_sync_at TEXT,
    sync_cursor_before_uid INTEGER,
    sync_cursor_mailbox TEXT,
    sync_cursor_days INTEGER,
    sync_backfill_completed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS thread_overview_cache (
    thread_key TEXT NOT NULL,
    user_email TEXT NOT NULL DEFAULT '',
    overview_json TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (thread_key, user_email)
);
"""

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_mails_subject ON mails(subject);
CREATE INDEX IF NOT EXISTS idx_mails_sender_email ON mails(sender_email);
CREATE INDEX IF NOT EXISTS idx_mails_received_at ON mails(received_at);
CREATE INDEX IF NOT EXISTS idx_mails_category ON mails(category);
CREATE INDEX IF NOT EXISTS idx_mails_action_status ON mails(my_action_status);
CREATE INDEX IF NOT EXISTS idx_mails_action_classification ON mails(action_classification);
CREATE INDEX IF NOT EXISTS idx_mails_status ON mails(status);
CREATE INDEX IF NOT EXISTS idx_mails_due_date ON mails(due_date);
CREATE INDEX IF NOT EXISTS idx_mails_thread_key ON mails(thread_key);
CREATE INDEX IF NOT EXISTS idx_mails_analysis_status_received ON mails(analysis_status, received_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_mails_thread_key_received ON mails(thread_key, received_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_mails_final_category ON mails(final_category);
CREATE INDEX IF NOT EXISTS idx_mails_rule_category ON mails(rule_category);
CREATE INDEX IF NOT EXISTS idx_mails_retention_bucket ON mails(retention_bucket);
CREATE INDEX IF NOT EXISTS idx_thread_overview_cache_user_email ON thread_overview_cache(user_email);
CREATE INDEX IF NOT EXISTS idx_action_items_mail_id ON action_items(mail_id);
CREATE INDEX IF NOT EXISTS idx_action_items_scope_done ON action_items(scope, done_flag);
CREATE INDEX IF NOT EXISTS idx_action_items_scope_done_due ON action_items(scope, done_flag, due_date, id);
CREATE INDEX IF NOT EXISTS idx_action_items_mail_scope_done ON action_items(mail_id, scope, done_flag);
CREATE INDEX IF NOT EXISTS idx_templates_enabled ON templates(enabled);
CREATE INDEX IF NOT EXISTS idx_mail_templates_updated_at ON mail_templates(updated_at);
CREATE INDEX IF NOT EXISTS idx_send_logs_template_id ON send_logs(template_id);
"""


class DatabaseManager:
    """Initialize and serve SQLite connections."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        """Open a connection with row access by column name."""

        connection = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        """Create tables and indexes."""

        with self.connect() as connection:
            connection.executescript(TABLES_SQL)
            self._ensure_column(connection, "mails", "normalized_subject", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "mails", "thread_key", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "mails", "in_reply_to", "TEXT")
            self._ensure_column(connection, "mails", "references_json", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(connection, "mails", "body_text", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "mails", "attachment_paths_json", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(connection, "mails", "action_classification", "TEXT")
            self._ensure_column(connection, "mails", "action_owner", "TEXT")
            self._ensure_column(connection, "mails", "action_type_json", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(connection, "mails", "deadline_raw", "TEXT")
            self._ensure_column(connection, "mails", "evidence_json", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(connection, "mails", "analysis_reason", "TEXT")
            self._ensure_column(connection, "mails", "suggested_task_title", "TEXT")
            self._ensure_column(connection, "mails", "is_to_me", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "mails", "is_cc_me", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "mails", "recipient_role", "TEXT")
            self._ensure_column(connection, "mails", "is_system_sender", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "mails", "is_newsletter_like", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "mails", "sender_type", "TEXT")
            self._ensure_column(connection, "mails", "rule_category", "INTEGER")
            self._ensure_column(connection, "mails", "request_present", "INTEGER")
            self._ensure_column(connection, "mails", "request_target", "TEXT")
            self._ensure_column(connection, "mails", "request_target_is_me", "INTEGER")
            self._ensure_column(connection, "mails", "urgency", "TEXT")
            self._ensure_column(connection, "mails", "llm_category", "INTEGER")
            self._ensure_column(connection, "mails", "final_category", "INTEGER")
            self._ensure_column(connection, "mails", "correction_applied", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "mails", "correction_reason", "TEXT")
            self._ensure_column(connection, "mails", "conflict_type", "TEXT")
            self._ensure_column(connection, "mails", "model_name", "TEXT")
            self._ensure_column(connection, "mails", "analyzed_at", "TEXT")
            self._ensure_column(connection, "mails", "raw_llm_json", "TEXT")
            self._ensure_column(connection, "mails", "retention_bucket", "TEXT NOT NULL DEFAULT 'classified'")
            self._ensure_column(connection, "action_items", "note", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "action_items", "updated_at", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "action_items", "completed_at", "TEXT")
            self._ensure_column(connection, "templates", "last_sent_at", "TEXT")
            self._ensure_column(connection, "app_settings", "sync_cursor_before_uid", "INTEGER")
            self._ensure_column(connection, "app_settings", "sync_cursor_mailbox", "TEXT")
            self._ensure_column(connection, "app_settings", "sync_cursor_days", "INTEGER")
            self._ensure_column(connection, "app_settings", "sync_backfill_completed_at", "TEXT")
            connection.executescript(INDEX_SQL)
            connection.execute(
                "UPDATE mails SET normalized_subject = subject WHERE normalized_subject IS NULL OR normalized_subject = ''"
            )
            connection.execute(
                "UPDATE mails SET thread_key = lower(message_id) WHERE thread_key IS NULL OR thread_key = ''"
            )
            connection.execute(
                "UPDATE mails SET references_json = '[]' WHERE references_json IS NULL OR references_json = ''"
            )
            connection.execute(
                "UPDATE mails SET body_text = COALESCE(raw_preview, '') WHERE body_text IS NULL OR body_text = ''"
            )
            connection.execute(
                "UPDATE mails SET attachment_paths_json = '[]' WHERE attachment_paths_json IS NULL OR attachment_paths_json = ''"
            )
            connection.execute(
                "UPDATE mails SET action_type_json = '[]' WHERE action_type_json IS NULL OR action_type_json = ''"
            )
            connection.execute(
                "UPDATE mails SET evidence_json = '[]' WHERE evidence_json IS NULL OR evidence_json = ''"
            )
            connection.execute(
                "UPDATE mails SET retention_bucket = 'classified' WHERE retention_bucket IS NULL OR retention_bucket = ''"
            )
            connection.execute(
                "UPDATE action_items SET note = '' WHERE note IS NULL"
            )
            connection.execute(
                "UPDATE action_items SET updated_at = created_at WHERE updated_at IS NULL OR updated_at = ''"
            )
            connection.execute(
                """
                UPDATE mails
                SET status = 'done'
                WHERE COALESCE(retention_bucket, 'classified') = 'completed'
                  AND status != 'done'
                """
            )
            connection.execute(
                """
                UPDATE action_items
                SET done_flag = 1,
                    completed_at = COALESCE(completed_at, NULLIF(updated_at, ''), created_at),
                    updated_at = COALESCE(NULLIF(updated_at, ''), created_at)
                WHERE scope = 'my'
                  AND done_flag = 0
                  AND mail_id IN (
                      SELECT id
                      FROM mails
                      WHERE COALESCE(retention_bucket, 'classified') = 'completed'
                  )
                """
            )
            connection.commit()

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table_name: str, column_name: str, column_sql: str) -> None:
        """Add a missing column to an existing table."""

        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        known_columns = {row["name"] for row in rows}
        if column_name in known_columns:
            return
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")
