"""Application logger factory."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.security import mask_sensitive_text


class SensitiveFormatter(logging.Formatter):
    """Formatter that masks sensitive fragments."""

    def format(self, record: logging.LogRecord) -> str:
        return mask_sensitive_text(super().format(record))


def configure_logger(log_dir: Path) -> logging.Logger:
    """Create a shared application logger."""

    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("mailai")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    formatter = SensitiveFormatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s")

    file_handler = RotatingFileHandler(
        log_dir / "mailai.log",
        maxBytes=1_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    logger.propagate = False
    return logger
