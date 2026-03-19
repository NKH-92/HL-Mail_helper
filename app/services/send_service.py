"""Template-based mail sending service."""

from __future__ import annotations

import logging
from calendar import monthrange
from datetime import datetime, timedelta

from app.core.config_manager import ConfigManager
from app.core.security import mask_sensitive_text
from app.core.time_utils import combine_date_and_time, parse_datetime_text, parse_time_text
from app.db.models import SendTemplate
from app.db.repositories import SendLogRepository, TemplateRepository, now_iso
from app.mail.smtp_client import SMTPClient


class SendService:
    """Perform manual and scheduled SMTP sending."""

    RETRY_DELAY = timedelta(minutes=5)

    def __init__(
        self,
        config_manager: ConfigManager,
        smtp_client: SMTPClient,
        template_repository: TemplateRepository,
        send_log_repository: SendLogRepository,
        logger: logging.Logger,
    ) -> None:
        self.config_manager = config_manager
        self.smtp_client = smtp_client
        self.template_repository = template_repository
        self.send_log_repository = send_log_repository
        self.logger = logger

    def send_test_template(self, template: SendTemplate) -> tuple[bool, str]:
        """Send a template immediately."""

        config = self.config_manager.load()
        recipients = template.to_list + template.cc_list
        if not template.to_list:
            return False, "받는 사람(To)을 한 명 이상 입력해 주세요."
        try:
            self.smtp_client.send_mail(
                config=config,
                subject=template.subject,
                body=template.body,
                to_list=template.to_list,
                cc_list=template.cc_list,
                attachment_paths=template.attachment_paths,
            )
            self.send_log_repository.create(template.id, "success", recipients, template.subject, None)
            return True, "테스트 메일을 발송했습니다."
        except Exception as exc:  # noqa: BLE001
            masked_error = mask_sensitive_text(str(exc))
            self.send_log_repository.create(template.id, "failed", recipients, template.subject, masked_error)
            self.logger.exception("Test send failed: %s", exc)
            return False, f"테스트 메일 발송 실패: {masked_error}"

    def send_scheduled_template(self, template_id: int) -> datetime | None:
        """Send one scheduled template by id and return a retry time when needed."""

        template = self.template_repository.get(template_id)
        if not template or not template.enabled:
            return None

        should_disable_after_success = template.repeat_type == "none"
        recipients = template.to_list + template.cc_list

        if not template.to_list:
            error_message = "받는 사람(To)을 한 명 이상 입력해 주세요."
            self.send_log_repository.create(template_id, "failed", recipients, template.subject, error_message)
            self.logger.warning("Scheduled template %s is missing To recipients", template_id)
            return self._next_retry_at()

        try:
            config = self.config_manager.load()
            self.smtp_client.send_mail(
                config=config,
                subject=template.subject,
                body=template.body,
                to_list=template.to_list,
                cc_list=template.cc_list,
                attachment_paths=template.attachment_paths,
            )
            sent_at = now_iso()
            self.template_repository.update_last_sent_at(template_id, sent_at)
            self.send_log_repository.create(template_id, "success", recipients, template.subject, None)
            if should_disable_after_success:
                self.template_repository.set_enabled(template_id, False)
            return None
        except Exception as exc:  # noqa: BLE001
            masked_error = mask_sensitive_text(str(exc))
            self.send_log_repository.create(template_id, "failed", recipients, template.subject, masked_error)
            self.logger.exception("Scheduled send failed for template %s: %s", template_id, exc)
            return self._next_retry_at()

    def calculate_next_run(self, template: SendTemplate, after: datetime | None = None) -> datetime | None:
        """Calculate the next run based on first_send_at and repeat_type."""

        base = parse_datetime_text(template.first_send_at)
        send_clock = parse_time_text(template.send_time)
        if base is None or send_clock is None:
            return None

        anchor = after or datetime.now()
        last_sent = parse_datetime_text(template.last_sent_at)

        if template.repeat_type == "none":
            if last_sent:
                return None
            return base if base >= anchor else anchor

        next_run = base
        if last_sent is not None:
            while next_run <= last_sent:
                next_run = self._advance_run(next_run, template.repeat_type, send_clock)
                if next_run is None:
                    return None

        while next_run < anchor:
            next_run = self._advance_run(next_run, template.repeat_type, send_clock)
            if next_run is None:
                return None
        return next_run

    def _advance_run(self, value: datetime, repeat_type: str, send_clock) -> datetime | None:
        """Advance one scheduled occurrence while preserving the configured send time."""

        if repeat_type == "daily":
            return combine_date_and_time(value + timedelta(days=1), send_clock)
        if repeat_type == "weekly":
            return combine_date_and_time(value + timedelta(weeks=1), send_clock)
        if repeat_type == "monthly":
            return combine_date_and_time(self._add_month(value), send_clock)
        return None

    def _add_month(self, value: datetime) -> datetime:
        year = value.year + (1 if value.month == 12 else 0)
        month = 1 if value.month == 12 else value.month + 1
        day = min(value.day, monthrange(year, month)[1])
        return value.replace(year=year, month=month, day=day)

    def _next_retry_at(self, anchor: datetime | None = None) -> datetime:
        base = anchor or datetime.now()
        return base + self.RETRY_DELAY
