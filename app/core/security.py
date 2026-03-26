"""Secret storage and masking helpers."""

from __future__ import annotations

import importlib
import re

SERVICE_NAME = "MailAI_Portable"
MAIL_PASSWORD_KEY = "mail_password"
GEMINI_API_KEY = "gemini_api_key"
HANLIM_API_KEY = "hanlim_api_key"


def _keyring_module():
    """Import keyring lazily so non-secret code paths don't block at module import time."""

    return importlib.import_module("keyring")


class SecretStore:
    """Wrapper around OS keyring."""

    def __init__(self, service_name: str = SERVICE_NAME) -> None:
        self.service_name = service_name

    def set_secret(self, key: str, value: str) -> None:
        """Store a secret in the keyring."""

        _keyring_module().set_password(self.service_name, key, value)

    def get_secret(self, key: str) -> str:
        """Read a secret from the keyring."""

        return _keyring_module().get_password(self.service_name, key) or ""

    def has_secret(self, key: str) -> bool:
        """Return whether a secret is stored for the given key."""

        return bool(self.get_secret(key))

    def delete_secret(self, key: str) -> None:
        """Remove a secret from the keyring when present."""

        keyring_module = _keyring_module()
        try:
            keyring_module.delete_password(self.service_name, key)
        except Exception as exc:  # noqa: BLE001
            delete_error = getattr(getattr(keyring_module, "errors", None), "PasswordDeleteError", None)
            if delete_error is not None and isinstance(exc, delete_error):
                return
            raise


def mask_sensitive_text(value: str) -> str:
    """Mask common secret patterns before logging."""

    masked = value
    masked = re.sub(r"(?i)(password|passwd|pwd)\s*[:=]\s*[^,\s]+", r"\1=***", masked)
    masked = re.sub(r"(?i)(authorization|x-goog-api-key)\s*[:=]\s*[^,\s]+", r"\1=***", masked)
    masked = re.sub(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})", r"***@\2", masked)
    return masked
