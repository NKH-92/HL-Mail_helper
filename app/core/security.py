"""Secret storage and masking helpers."""

from __future__ import annotations

import re

import keyring

SERVICE_NAME = "MailAI_Portable"
MAIL_PASSWORD_KEY = "mail_password"
GEMINI_API_KEY = "gemini_api_key"
HANLIM_API_KEY = "hanlim_api_key"


class SecretStore:
    """Wrapper around OS keyring."""

    def __init__(self, service_name: str = SERVICE_NAME) -> None:
        self.service_name = service_name

    def set_secret(self, key: str, value: str) -> None:
        """Store a secret in the keyring."""

        keyring.set_password(self.service_name, key, value)

    def get_secret(self, key: str) -> str:
        """Read a secret from the keyring."""

        return keyring.get_password(self.service_name, key) or ""

    def has_secret(self, key: str) -> bool:
        """Return whether a secret is stored for the given key."""

        return bool(self.get_secret(key))

    def delete_secret(self, key: str) -> None:
        """Remove a secret from the keyring when present."""

        try:
            keyring.delete_password(self.service_name, key)
        except keyring.errors.PasswordDeleteError:
            return


def mask_sensitive_text(value: str) -> str:
    """Mask common secret patterns before logging."""

    masked = value
    masked = re.sub(r"(?i)(password|passwd|pwd)\s*[:=]\s*[^,\s]+", r"\1=***", masked)
    masked = re.sub(r"(?i)(authorization|x-goog-api-key)\s*[:=]\s*[^,\s]+", r"\1=***", masked)
    masked = re.sub(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})", r"***@\2", masked)
    return masked
