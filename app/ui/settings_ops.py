"""Shared settings update helpers for desktop and Streamlit UI flows."""

from __future__ import annotations

from typing import Any

from app.core.config_manager import AppConfig
from app.core.security import GEMINI_API_KEY, HANLIM_API_KEY, MAIL_PASSWORD_KEY, mask_sensitive_text

_MISSING_SECRET = object()


def apply_settings_update(
    context: Any,
    current_config: AppConfig,
    next_config: AppConfig,
    *,
    password: str | None,
    api_key: str | None,
    hanlim_api_key: str | None,
) -> tuple[int, str | None]:
    """Persist settings with best-effort rollback for config/keyring mismatches."""

    secret_updates = [
        (MAIL_PASSWORD_KEY, password),
        (GEMINI_API_KEY, api_key),
        (HANLIM_API_KEY, hanlim_api_key),
    ]
    secret_updates = [(key, value) for key, value in secret_updates if value]
    clear_stale_mail_password = (
        not password and _normalized_email(current_config.user_email) != _normalized_email(next_config.user_email)
    )
    tracked_secret_keys = [key for key, _ in secret_updates]
    if clear_stale_mail_password and MAIL_PASSWORD_KEY not in tracked_secret_keys:
        tracked_secret_keys.append(MAIL_PASSWORD_KEY)
    previous_secret_values = _read_previous_secret_values(context, tracked_secret_keys)
    updated_secret_keys: list[str] = []

    context.config_manager.save(next_config)
    try:
        for key, value in secret_updates:
            context.secret_store.set_secret(key, value)
            updated_secret_keys.append(key)
        if clear_stale_mail_password and MAIL_PASSWORD_KEY not in updated_secret_keys:
            context.secret_store.delete_secret(MAIL_PASSWORD_KEY)
            updated_secret_keys.append(MAIL_PASSWORD_KEY)
    except Exception:
        logger = getattr(context, "logger", None)
        try:
            context.config_manager.save(current_config)
        except Exception as rollback_exc:  # noqa: BLE001
            if logger is not None:
                logger.warning("Failed to roll back config after settings save error: %s", rollback_exc)
        try:
            _restore_secret_values(context, previous_secret_values, updated_secret_keys)
        except Exception as rollback_exc:  # noqa: BLE001
            if logger is not None:
                logger.warning("Failed to roll back secrets after settings save error: %s", rollback_exc)
        raise

    deleted_old_count = 0
    follow_up_warning: str | None = None
    if clear_stale_mail_password:
        follow_up_warning = "메일 계정이 변경되어 기존 비밀번호를 삭제했습니다. 새 계정 비밀번호를 다시 입력해 주세요."
    try:
        deleted_old_count = context.sync_service.prune_local_mail_retention(days=next_config.sync_days)
        context.scheduler_manager.refresh_jobs()
    except Exception as exc:  # noqa: BLE001
        follow_up_warning = _append_notice(
            follow_up_warning,
            (
                "설정은 저장됐지만 후속 정리 또는 스케줄 갱신에 실패했습니다: "
                f"{mask_sensitive_text(str(exc))}"
            ),
        )
        logger = getattr(context, "logger", None)
        if logger is not None:
            logger.warning("Settings follow-up step failed after save: %s", exc)

    return deleted_old_count, follow_up_warning


def _read_previous_secret_values(context: Any, secret_keys: list[str]) -> dict[str, object]:
    get_secret = getattr(context.secret_store, "get_secret", None)
    if not callable(get_secret):
        return {key: _MISSING_SECRET for key in secret_keys}
    previous_values: dict[str, object] = {}
    for key in secret_keys:
        previous_values[key] = get_secret(key)
    return previous_values


def _restore_secret_values(context: Any, previous_secret_values: dict[str, object], updated_secret_keys: list[str]) -> None:
    for key in updated_secret_keys:
        previous_value = previous_secret_values.get(key, _MISSING_SECRET)
        if previous_value is _MISSING_SECRET:
            continue
        if previous_value:
            context.secret_store.set_secret(key, str(previous_value))
        else:
            context.secret_store.delete_secret(key)


def _normalized_email(value: str | None) -> str:
    return str(value or "").strip().lower()


def _append_notice(current: str | None, message: str | None) -> str | None:
    normalized_message = str(message or "").strip()
    if not normalized_message:
        return current
    normalized_current = str(current or "").strip()
    if not normalized_current:
        return normalized_message
    return f"{normalized_current} {normalized_message}"
