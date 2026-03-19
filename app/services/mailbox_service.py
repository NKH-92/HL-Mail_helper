"""Orchestrate mailbox sync and AI analysis."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from app.services.analysis_service import AnalysisService
from app.services.sync_service import SyncService


@dataclass(slots=True)
class MailboxRunResult:
    """Combined result for one sync and analyze cycle."""

    scanned_count: int
    fetched_count: int
    saved_count: int
    duplicate_count: int
    skipped_old_count: int
    parse_error_count: int
    analysis_success_count: int
    analysis_failure_count: int
    limited_by_batch: bool
    deleted_old_count: int = 0
    analysis_warning: str | None = None


class MailboxService:
    """Coordinate inbox synchronization and AI analysis."""

    def __init__(
        self,
        sync_service: SyncService,
        analysis_service: AnalysisService,
        logger: logging.Logger,
    ) -> None:
        self.sync_service = sync_service
        self.analysis_service = analysis_service
        self.logger = logger

    def get_analysis_warning(self) -> str | None:
        """Return the current AI availability warning when present."""

        return self.analysis_service.get_unavailability_reason()

    def get_sync_warning(self) -> str | None:
        """Return the current sync availability warning when present."""

        return self.sync_service.get_unavailability_reason()

    def run_once(
        self,
        analysis_limit: int | None = None,
        include_failed_analysis: bool = True,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> MailboxRunResult:
        """Sync mailbox and analyze pending mails in one pass."""

        sync_warning = self.sync_service.get_unavailability_reason()
        if sync_warning:
            raise RuntimeError(sync_warning)

        if progress_callback is not None:
            progress_callback(
                {
                    "stage": "syncing",
                    "message": "메일 수집 중",
                }
            )

        sync_result = self.sync_service.sync_recent_mail()
        limit = analysis_limit or max(sync_result.saved_count, 20)
        analysis_warning = self.analysis_service.get_unavailability_reason()

        if progress_callback is not None:
            progress_callback(
                {
                    "stage": "analyzing",
                    "message": "AI 분석 준비 중",
                    "scanned_count": sync_result.scanned_count,
                    "fetched_count": sync_result.fetched_count,
                    "saved_count": sync_result.saved_count,
                    "analysis_total": 0,
                    "analysis_completed": 0,
                    "analysis_success_count": 0,
                    "analysis_failure_count": 0,
                }
            )

        analysis_progress = None
        if progress_callback is not None:

            def analysis_progress(event: dict[str, object]) -> None:
                progress_callback(
                    {
                        "message": "AI 분석 진행 중",
                        "scanned_count": sync_result.scanned_count,
                        "fetched_count": sync_result.fetched_count,
                        "saved_count": sync_result.saved_count,
                        **event,
                    }
                )

        analyze_kwargs = {
            "limit": limit,
            "include_failed": include_failed_analysis,
        }
        if analysis_progress is not None:
            analyze_kwargs["progress_callback"] = analysis_progress

        success_count, failure_count = self.analysis_service.analyze_pending_mails(**analyze_kwargs)

        result = MailboxRunResult(
            scanned_count=sync_result.scanned_count,
            fetched_count=sync_result.fetched_count,
            saved_count=sync_result.saved_count,
            duplicate_count=sync_result.duplicate_count,
            skipped_old_count=sync_result.skipped_old_count,
            parse_error_count=sync_result.parse_error_count,
            analysis_success_count=success_count,
            analysis_failure_count=failure_count,
            limited_by_batch=sync_result.limited_by_batch,
            deleted_old_count=sync_result.deleted_old_count,
            analysis_warning=analysis_warning,
        )
        self.logger.info(
            (
                "Mailbox cycle completed: scanned=%s fetched=%s saved=%s pruned=%s "
                "analysis_success=%s analysis_failure=%s limited=%s warning=%s"
            ),
            result.scanned_count,
            result.fetched_count,
            result.saved_count,
            result.deleted_old_count,
            result.analysis_success_count,
            result.analysis_failure_count,
            result.limited_by_batch,
            result.analysis_warning,
        )

        if progress_callback is not None:
            progress_callback(
                {
                    "stage": "complete",
                    "message": "동기화 완료",
                    "scanned_count": result.scanned_count,
                    "fetched_count": result.fetched_count,
                    "saved_count": result.saved_count,
                    "analysis_total": result.analysis_success_count + result.analysis_failure_count,
                    "analysis_completed": result.analysis_success_count + result.analysis_failure_count,
                    "analysis_success_count": result.analysis_success_count,
                    "analysis_failure_count": result.analysis_failure_count,
                }
            )

        return result
