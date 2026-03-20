"""SMTP send helpers."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path

from app.core.config_manager import AppConfig
from app.core.security import MAIL_PASSWORD_KEY, SecretStore, mask_sensitive_text


class SMTPClient:
    """SMTP sender used for test and template-based delivery."""

    def __init__(self, secret_store: SecretStore, logger: logging.Logger, storage_root: Path | None = None) -> None:
        self.secret_store = secret_store
        self.logger = logger
        self.storage_root = storage_root

    def test_connection(self, config: AppConfig, password_override: str | None = None) -> tuple[bool, str]:
        """Validate SMTP login."""

        try:
            with self._connect(config, password_override=password_override):
                return True, "SMTP 연결 성공"
        except Exception as exc:  # noqa: BLE001
            self.logger.exception("SMTP connection test failed: %s", exc)
            return False, f"SMTP 연결 실패: {mask_sensitive_text(str(exc))}"

    def send_mail(
        self,
        config: AppConfig,
        subject: str,
        body: str,
        to_list: list[str],
        cc_list: list[str],
        attachment_paths: list[str],
    ) -> None:
        """Send a templated email without AI involvement."""

        if not to_list:
            raise ValueError("최소 한 명 이상의 To 수신자가 필요합니다.")

        message = EmailMessage()
        message["From"] = config.user_email
        message["To"] = ", ".join(to_list)
        if cc_list:
            message["Cc"] = ", ".join(cc_list)
        message["Subject"] = subject
        message.set_content(body)

        for path_value in attachment_paths:
            path = self._resolve_attachment_path(path_value)
            if not path.exists() or not path.is_file():
                raise FileNotFoundError(f"첨부파일을 찾을 수 없습니다: {path}")
            data = path.read_bytes()
            message.add_attachment(
                data,
                maintype="application",
                subtype="octet-stream",
                filename=path.name,
            )

        with self._connect(config) as smtp:
            smtp.send_message(message, to_addrs=to_list + cc_list)

    def _connect(self, config: AppConfig, password_override: str | None = None) -> smtplib.SMTP:
        password = password_override if password_override is not None else self.secret_store.get_secret(MAIL_PASSWORD_KEY)
        if not password:
            raise ValueError("메일 비밀번호가 keyring에 저장되어 있지 않습니다.")

        if config.smtp_use_ssl:
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=30)
        else:
            smtp = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30)
            smtp.ehlo()
            if config.smtp_use_tls:
                smtp.starttls()
                smtp.ehlo()
        smtp.login(config.user_email, password)
        return smtp

    def _resolve_attachment_path(self, path_value: str) -> Path:
        """Resolve stored attachment paths relative to the portable root when needed."""

        path = Path(path_value).expanduser()
        if path.is_absolute() or self.storage_root is None:
            return path
        primary = (self.storage_root / path).resolve()
        if primary.exists() or (path.parts and path.parts[0].lower() == "cache"):
            return primary
        fallback = (self.storage_root / "cache" / path).resolve()
        return fallback if fallback.exists() else primary
