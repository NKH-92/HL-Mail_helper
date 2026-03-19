"""Shared runtime bootstrap for desktop and Streamlit entrypoints."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.ai.gemini_client import GeminiClient
from app.ai.prompts import PromptManager
from app.core.address_book import AddressBookService
from app.core.config_manager import ConfigManager
from app.core.logger import configure_logger
from app.core.scheduler_manager import SchedulerManager
from app.core.security import SecretStore
from app.db.database import DatabaseManager
from app.db.repositories import (
    AppSettingsRepository,
    MailRepository,
    MailTemplateRepository,
    SendLogRepository,
    TemplateRepository,
)
from app.mail.imap_client import IMAPClient
from app.mail.smtp_client import SMTPClient
from app.services.analysis_service import AnalysisService
from app.services.mail_template_service import MailTemplateService
from app.services.mailbox_service import MailboxService
from app.services.send_service import SendService
from app.services.sync_service import SyncService
from app.services.template_service import TemplateService


@dataclass(slots=True)
class AppContext:
    """Shared runtime services for the app."""

    address_book_service: AddressBookService
    config_manager: ConfigManager
    secret_store: SecretStore
    logger: logging.Logger
    logger_path: Path
    mail_repository: MailRepository
    send_log_repository: SendLogRepository
    mail_template_service: MailTemplateService
    template_service: TemplateService
    sync_service: SyncService
    mailbox_service: MailboxService
    send_service: SendService
    imap_client: IMAPClient
    scheduler_manager: SchedulerManager


def build_app_context(data_root: Path, code_root: Path) -> AppContext:
    """Create the runtime service graph for the app."""

    for name in ["config", "data", "logs", "cache", "templates", "prompts", "addressbook"]:
        (data_root / name).mkdir(parents=True, exist_ok=True)

    config_manager = ConfigManager(data_root / "config" / "settings.json")
    address_book_service = AddressBookService(data_root=data_root, bundle_root=code_root)
    secret_store = SecretStore()
    logger = configure_logger(data_root / "logs")
    database = DatabaseManager(data_root / "data" / "app.db")

    mail_repository = MailRepository(database)
    mail_template_repository = MailTemplateRepository(database)
    template_repository = TemplateRepository(database)
    send_log_repository = SendLogRepository(database)
    app_settings_repository = AppSettingsRepository(database)

    imap_client = IMAPClient(secret_store=secret_store, logger=logger)
    smtp_client = SMTPClient(secret_store=secret_store, logger=logger, storage_root=data_root)
    prompt_manager = PromptManager(
        prompt_dir=data_root / "prompts",
        fallback_prompt_dir=code_root / "prompts",
    )
    gemini_client = GeminiClient(secret_store=secret_store, logger=logger)

    sync_service = SyncService(
        config_manager=config_manager,
        imap_client=imap_client,
        mail_repository=mail_repository,
        app_settings_repository=app_settings_repository,
        logger=logger,
        storage_root=data_root,
    )
    analysis_service = AnalysisService(
        config_manager=config_manager,
        address_book_service=address_book_service,
        prompt_manager=prompt_manager,
        gemini_client=gemini_client,
        mail_repository=mail_repository,
        logger=logger,
    )
    mailbox_service = MailboxService(
        sync_service=sync_service,
        analysis_service=analysis_service,
        logger=logger,
    )
    send_service = SendService(
        config_manager=config_manager,
        smtp_client=smtp_client,
        template_repository=template_repository,
        send_log_repository=send_log_repository,
        logger=logger,
    )
    mail_template_service = MailTemplateService(mail_template_repository, portable_root=data_root)
    template_service = TemplateService(template_repository, portable_root=data_root)
    scheduler_manager = SchedulerManager(
        config_manager=config_manager,
        template_repository=template_repository,
        send_service=send_service,
        mailbox_service=mailbox_service,
        logger=logger,
    )
    scheduler_manager.start()

    return AppContext(
        address_book_service=address_book_service,
        config_manager=config_manager,
        secret_store=secret_store,
        logger=logger,
        logger_path=data_root / "logs" / "mailai.log",
        mail_repository=mail_repository,
        send_log_repository=send_log_repository,
        mail_template_service=mail_template_service,
        template_service=template_service,
        sync_service=sync_service,
        mailbox_service=mailbox_service,
        send_service=send_service,
        imap_client=imap_client,
        scheduler_manager=scheduler_manager,
    )
