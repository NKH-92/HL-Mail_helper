"""Configuration management for MailAI Portable."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

COMPANY_MAIL_HOST = "omail.hanlim.com"
COMPANY_IMAP_PORT = 143
COMPANY_SMTP_PORT = 25
AI_PROVIDER_GEMINI = "gemini"
AI_PROVIDER_HANLIM = "hanlim_openai"
LEGACY_AI_PROVIDER_HANLIM = "hanlim_google_compat"
DEFAULT_HANLIM_AI_BASE_URL = "https://ai.hanliminve.com/llm_hub/api/v1"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_HANLIM_MODEL = "hanlimAI"
SUPPORTED_AI_PROVIDERS = {AI_PROVIDER_GEMINI, AI_PROVIDER_HANLIM, LEGACY_AI_PROVIDER_HANLIM}


@dataclass(slots=True)
class AppConfig:
    """Application settings stored in JSON."""

    user_email: str = ""
    user_display_name: str = ""
    user_department: str = ""
    user_job_title: str = ""
    imap_host: str = COMPANY_MAIL_HOST
    imap_port: int = COMPANY_IMAP_PORT
    imap_use_ssl: bool = False
    imap_use_starttls: bool = True
    smtp_host: str = COMPANY_MAIL_HOST
    smtp_port: int = COMPANY_SMTP_PORT
    smtp_use_ssl: bool = False
    smtp_use_tls: bool = True
    mailbox: str = "INBOX"
    sync_days: int = 30
    sync_batch_size: int = 50
    sync_scan_limit: int = 200
    sync_interval_minutes: int = 60
    store_raw_body: bool = False
    preview_max_chars: int = 4000
    ai_provider: str = AI_PROVIDER_GEMINI
    ai_base_url: str = ""
    gemini_model: str = DEFAULT_GEMINI_MODEL
    gemini_timeout_seconds: int = 60
    ui_language: str = "ko"

    def is_ready(self) -> bool:
        """Return True when minimum settings exist for app usage."""

        return bool(
            self.user_email.strip()
            and self.imap_host.strip()
            and self.smtp_host.strip()
            and self.mailbox.strip()
        )

    def normalized(self) -> "AppConfig":
        """Force company mail defaults while preserving user-specific values."""

        data = asdict(self)
        data["user_email"] = str(data.get("user_email", "") or "").strip()
        data["user_display_name"] = str(data.get("user_display_name", "") or "").strip()
        data["user_department"] = str(data.get("user_department", "") or "").strip()
        data["user_job_title"] = str(data.get("user_job_title", "") or "").strip()
        data["imap_host"] = COMPANY_MAIL_HOST
        data["imap_port"] = COMPANY_IMAP_PORT
        data["imap_use_ssl"] = False
        data["imap_use_starttls"] = True
        data["smtp_host"] = COMPANY_MAIL_HOST
        data["smtp_port"] = COMPANY_SMTP_PORT
        data["smtp_use_ssl"] = False
        data["smtp_use_tls"] = True
        data["mailbox"] = str(data.get("mailbox", "") or "").strip() or "INBOX"
        data["sync_days"] = _coerce_int(data.get("sync_days"), 30, min_value=1, max_value=365)
        data["sync_batch_size"] = _coerce_int(data.get("sync_batch_size"), 50, min_value=10, max_value=300)
        data["sync_scan_limit"] = _coerce_int(
            data.get("sync_scan_limit"),
            max(data["sync_batch_size"], 200),
            min_value=data["sync_batch_size"],
            max_value=1000,
        )
        data["sync_interval_minutes"] = _coerce_int(
            data.get("sync_interval_minutes"),
            60,
            min_value=15,
            max_value=720,
        )
        data["store_raw_body"] = bool(data.get("store_raw_body", False))
        data["preview_max_chars"] = _coerce_int(data.get("preview_max_chars"), 4000, min_value=500, max_value=50000)
        ai_provider = str(data.get("ai_provider", "") or AI_PROVIDER_GEMINI).strip().lower()
        if ai_provider == LEGACY_AI_PROVIDER_HANLIM:
            ai_provider = AI_PROVIDER_HANLIM
        if ai_provider not in SUPPORTED_AI_PROVIDERS:
            ai_provider = AI_PROVIDER_GEMINI
        data["ai_provider"] = ai_provider
        ai_base_url = str(data.get("ai_base_url", "") or "").strip()
        data["ai_base_url"] = ai_base_url if ai_provider == AI_PROVIDER_HANLIM else ""
        if ai_provider == AI_PROVIDER_HANLIM and not data["ai_base_url"]:
            data["ai_base_url"] = DEFAULT_HANLIM_AI_BASE_URL
        default_model = DEFAULT_HANLIM_MODEL if ai_provider == AI_PROVIDER_HANLIM else DEFAULT_GEMINI_MODEL
        data["gemini_model"] = str(data.get("gemini_model", "") or default_model).strip() or default_model
        data["gemini_timeout_seconds"] = _coerce_int(
            data.get("gemini_timeout_seconds"),
            60,
            min_value=10,
            max_value=300,
        )
        data["ui_language"] = str(data.get("ui_language", "") or "ko").strip() or "ko"
        return AppConfig(**data)


class ConfigManager:
    """Read and write JSON settings."""

    def __init__(self, settings_path: Path) -> None:
        self.settings_path = settings_path
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.settings_path.exists():
            self.save(AppConfig())

    def load(self) -> AppConfig:
        """Load settings from disk."""

        try:
            with self.settings_path.open("r", encoding="utf-8") as file:
                raw = json.load(file)
        except FileNotFoundError:
            fallback = AppConfig()
            self.save(fallback)
            return fallback
        except (OSError, json.JSONDecodeError, TypeError):
            self._backup_corrupt_file()
            fallback = AppConfig()
            self.save(fallback)
            return fallback
        if not isinstance(raw, dict):
            self._backup_corrupt_file()
            fallback = AppConfig()
            self.save(fallback)
            return fallback
        try:
            defaults = asdict(AppConfig())
            merged = {**defaults, **raw}
            return AppConfig(**merged).normalized()
        except (TypeError, ValueError):
            self._backup_corrupt_file()
            fallback = AppConfig()
            self.save(fallback)
            return fallback

    def save(self, config: AppConfig) -> None:
        """Persist settings to disk."""

        normalized = config.normalized()
        temp_path = self.settings_path.with_suffix(".tmp")
        with temp_path.open("w", encoding="utf-8") as file:
            json.dump(asdict(normalized), file, indent=2, ensure_ascii=False)
        temp_path.replace(self.settings_path)

    def _backup_corrupt_file(self) -> None:
        """Keep a copy of an unreadable settings file before resetting."""

        if not self.settings_path.exists():
            return
        backup_path = self.settings_path.with_suffix(f".broken-{self.settings_path.stat().st_mtime_ns}.json")
        try:
            self.settings_path.replace(backup_path)
        except OSError:
            pass


def _coerce_int(value: object, default: int, min_value: int | None = None, max_value: int | None = None) -> int:
    """Convert values to a bounded integer, falling back on invalid input."""

    try:
        result = int(value) if value is not None else default
    except (TypeError, ValueError):
        result = default
    if min_value is not None:
        result = max(min_value, result)
    if max_value is not None:
        result = min(max_value, result)
    return result
