"""APScheduler integration for template sending and mailbox polling."""

from __future__ import annotations

import imaplib
import logging
from datetime import datetime, timedelta
from threading import Lock
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config_manager import ConfigManager
from app.db.repositories import TemplateRepository
from app.services.mailbox_service import MailboxService
from app.services.send_service import SendService


class SchedulerManager:
    """Manage background jobs while the app is running."""

    def __init__(
        self,
        config_manager: ConfigManager,
        template_repository: TemplateRepository,
        send_service: SendService,
        mailbox_service: MailboxService,
        logger: logging.Logger,
    ) -> None:
        self.config_manager = config_manager
        self.template_repository = template_repository
        self.send_service = send_service
        self.mailbox_service = mailbox_service
        self.logger = logger
        self.scheduler = BackgroundScheduler()
        self.started = False
        self._last_mailbox_started_at: datetime | None = None
        self._last_mailbox_finished_at: datetime | None = None
        self._last_mailbox_warning: str | None = None
        self._last_mailbox_result_summary: str | None = None
        self._mailbox_cycle_lock = Lock()
        self._mailbox_cycle_source: str | None = None

    def start(self) -> None:
        """Start scheduler once."""

        if self.started:
            return

        self.scheduler.start()
        self.started = True
        self.refresh_jobs()
        self._ensure_mailbox_jobs(schedule_startup=True)

    def shutdown(self) -> None:
        """Shutdown scheduler when needed."""

        if self.started:
            self.scheduler.shutdown(wait=False)
            self.started = False

    def pause_all(self) -> None:
        """Pause all scheduled work."""

        self.scheduler.pause()

    def resume_all(self) -> None:
        """Resume all scheduled work."""

        self.scheduler.resume()

    def refresh_jobs(self) -> None:
        """Rebuild template jobs without touching mailbox polling jobs."""

        availability_check = getattr(self.send_service, "get_unavailability_reason", None)
        try:
            send_unavailable_reason = availability_check() if callable(availability_check) else None
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Template scheduling availability probe failed: %s", exc)
            self._clear_template_jobs()
            self._ensure_mailbox_jobs(schedule_startup=False)
            return

        self._clear_template_jobs()

        try:
            enabled_templates = self.template_repository.list_enabled()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Template scheduling refresh skipped malformed rows: %s", exc)
            enabled_templates = []

        if send_unavailable_reason:
            self.logger.warning("Template scheduling paused: %s", send_unavailable_reason)
            self._ensure_mailbox_jobs(schedule_startup=False)
            return

        for template in enabled_templates:
            try:
                next_run_at = self.send_service.calculate_next_run(template)
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("Skipping template %s scheduling due to invalid data: %s", template.id, exc)
                continue
            if next_run_at is None:
                continue
            self.scheduler.add_job(
                self._run_template_job,
                trigger=DateTrigger(run_date=next_run_at),
                id=f"template_{template.id}",
                replace_existing=True,
                args=[template.id],
                max_instances=1,
                coalesce=True,
            )
            self.logger.info("Scheduled template %s at %s", template.id, next_run_at.isoformat())

        self._ensure_mailbox_jobs(schedule_startup=False)

    def _ensure_mailbox_jobs(self, schedule_startup: bool = True) -> None:
        """Ensure mailbox polling jobs exist."""

        config = self.config_manager.load()

        if schedule_startup and self.scheduler.get_job("mailbox_startup") is None:
            self.scheduler.add_job(
                self._run_mailbox_job,
                trigger=DateTrigger(run_date=datetime.now() + timedelta(seconds=10)),
                id="mailbox_startup",
                replace_existing=False,
                max_instances=1,
                coalesce=True,
            )

        legacy_job = self.scheduler.get_job("mailbox_hourly")
        if legacy_job is not None:
            self.scheduler.remove_job("mailbox_hourly")

        self.scheduler.add_job(
            self._run_mailbox_job,
            trigger=IntervalTrigger(minutes=config.sync_interval_minutes),
            id="mailbox_interval",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

    def run_mailbox_cycle(
        self,
        *,
        include_failed_analysis: bool,
        source: str,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> object:
        """Run one mailbox cycle and remember the latest result for the UI."""

        if not self._mailbox_cycle_lock.acquire(blocking=False):
            active_source = self._mailbox_cycle_source or "other"
            raise RuntimeError(f"이미 {active_source} 동기화가 진행 중입니다.")

        try:
            self._mailbox_cycle_source = source
            self._last_mailbox_started_at = datetime.now()
            try:
                result = self.mailbox_service.run_once(
                    include_failed_analysis=include_failed_analysis,
                    progress_callback=progress_callback,
                )
            except Exception as exc:
                self._last_mailbox_finished_at = datetime.now()
                self._last_mailbox_warning = str(exc) or "동기화에 실패했습니다."
                self._last_mailbox_result_summary = None
                raise

            self._last_mailbox_finished_at = datetime.now()
            self._last_mailbox_warning = getattr(result, "analysis_warning", None) or None
            self._last_mailbox_result_summary = (
                f"{source}: 저장 {result.saved_count}건 / "
                f"AI 성공 {result.analysis_success_count}건 / "
                f"AI 실패 {result.analysis_failure_count}건"
            )
            return result
        finally:
            self._mailbox_cycle_source = None
            self._mailbox_cycle_lock.release()

    def get_mailbox_status(self) -> dict[str, object]:
        """Expose scheduler and mailbox cycle state for the UI."""

        interval_job = self.scheduler.get_job("mailbox_interval")
        startup_job = self.scheduler.get_job("mailbox_startup")
        next_run = None
        if startup_job and startup_job.next_run_time is not None:
            next_run = startup_job.next_run_time
        elif interval_job and interval_job.next_run_time is not None:
            next_run = interval_job.next_run_time

        config = self.config_manager.load()
        return {
            "started": self.started,
            "scheduler_state": str(self.scheduler.state) if self.started else "stopped",
            "interval_minutes": config.sync_interval_minutes,
            "next_run_at": self._serialize_datetime(next_run),
            "last_started_at": self._serialize_datetime(self._last_mailbox_started_at),
            "last_finished_at": self._serialize_datetime(self._last_mailbox_finished_at),
            "last_warning": self._last_mailbox_warning,
            "last_result_summary": self._last_mailbox_result_summary,
        }

    @staticmethod
    def _serialize_datetime(value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.replace(microsecond=0).isoformat(sep=" ")

    def _run_mailbox_job(self) -> None:
        """Run sync and AI analysis for the inbox."""

        try:
            result = self.run_mailbox_cycle(include_failed_analysis=False, source="auto")
            self.logger.info(
                "Mailbox background cycle finished: saved=%s analyzed=%s failed=%s",
                result.saved_count,
                result.analysis_success_count,
                result.analysis_failure_count,
            )
        except RuntimeError as exc:
            self.logger.warning("Mailbox background cycle skipped: %s", exc)
        except imaplib.IMAP4.error as exc:
            if "LOGIN failed" in str(exc):
                self.logger.warning(
                    "Mailbox background cycle skipped due to IMAP login failure. 저장된 메일 비밀번호를 다시 확인해 주세요."
                )
            else:
                self.logger.warning("Mailbox background cycle skipped: %s", exc)
        except Exception as exc:  # noqa: BLE001
            self.logger.exception("Mailbox background cycle failed: %s", exc)

    def _run_template_job(self, template_id: int) -> None:
        """Send a scheduled template and enqueue the next occurrence."""

        retry_at: datetime | None = None
        try:
            retry_at = self.send_service.send_scheduled_template(template_id)
        except Exception as exc:  # noqa: BLE001
            self.logger.exception("Scheduled send failed for template %s: %s", template_id, exc)
        finally:
            lookup_template = getattr(self.template_repository, "get_by_id", None)
            if not callable(lookup_template):
                lookup_template = getattr(self.template_repository, "get", None)
            try:
                template = lookup_template(template_id) if callable(lookup_template) else None
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("Failed to reload template %s after send: %s", template_id, exc)
                return
            if template is None or not getattr(template, "enabled", False):
                return
            availability_check = getattr(self.send_service, "get_unavailability_reason", None)
            try:
                send_unavailable_reason = availability_check() if callable(availability_check) else None
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("Template %s availability probe failed after send: %s", template_id, exc)
                return
            if send_unavailable_reason:
                self.logger.warning("Template %s reschedule skipped: %s", template_id, send_unavailable_reason)
                return
            next_run_at = retry_at or self.send_service.calculate_next_run(template)
            if next_run_at is None:
                return
            self.scheduler.add_job(
                self._run_template_job,
                trigger=DateTrigger(run_date=next_run_at),
                id=f"template_{template_id}",
                replace_existing=True,
                args=[template_id],
                max_instances=1,
                coalesce=True,
            )
            self.logger.info("Rescheduled template %s at %s", template_id, next_run_at.isoformat())

    def _clear_template_jobs(self) -> None:
        for job in list(self.scheduler.get_jobs()):
            if job.id.startswith("template_"):
                self.scheduler.remove_job(job.id)
