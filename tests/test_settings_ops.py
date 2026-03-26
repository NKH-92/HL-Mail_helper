from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.core.config_manager import AppConfig, ConfigManager
from app.core.security import GEMINI_API_KEY, MAIL_PASSWORD_KEY
from app.ui.settings_ops import apply_settings_update


class _FakeSecretStore:
    def __init__(self, *, fail_on_key: str | None = None) -> None:
        self.values: dict[str, str] = {}
        self.fail_on_key = fail_on_key

    def set_secret(self, key: str, value: str) -> None:
        if key == self.fail_on_key:
            raise RuntimeError(f"failed secret write: {key}")
        self.values[key] = value

    def get_secret(self, key: str) -> str:
        return self.values.get(key, "")

    def delete_secret(self, key: str) -> None:
        self.values.pop(key, None)


class _FakeSyncService:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.calls: list[int] = []

    def prune_local_mail_retention(self, *, days: int) -> int:
        self.calls.append(days)
        if self.should_fail:
            raise RuntimeError("prune failed")
        return 3


class _FakeSchedulerManager:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.refresh_count = 0

    def refresh_jobs(self) -> None:
        self.refresh_count += 1
        if self.should_fail:
            raise RuntimeError("refresh failed")


class _FailingRollbackConfigManager:
    def __init__(self, current_config: AppConfig, next_config: AppConfig) -> None:
        self.current_config = current_config
        self.next_config = next_config
        self.saved_config = current_config
        self.rollback_attempted = False

    def save(self, config: AppConfig) -> None:
        if config.user_email == self.current_config.user_email and self.saved_config.user_email == self.next_config.user_email:
            self.rollback_attempted = True
            raise RuntimeError("rollback save failed")
        self.saved_config = config

    def load(self) -> AppConfig:
        return self.saved_config


class SettingsOpsTests(unittest.TestCase):
    def test_apply_settings_update_rolls_back_when_secret_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            config_manager = ConfigManager(settings_path)
            current_config = AppConfig(user_email="old@example.com", mailbox="INBOX")
            config_manager.save(current_config)

            secret_store = _FakeSecretStore()
            secret_store.set_secret(MAIL_PASSWORD_KEY, "old-password")
            secret_store.set_secret(GEMINI_API_KEY, "old-api-key")
            secret_store.fail_on_key = GEMINI_API_KEY
            context = SimpleNamespace(
                config_manager=config_manager,
                secret_store=secret_store,
                sync_service=_FakeSyncService(),
                scheduler_manager=_FakeSchedulerManager(),
                logger=SimpleNamespace(warning=lambda *args, **kwargs: None),
            )
            next_config = AppConfig(user_email="new@example.com", mailbox="INBOX")

            with self.assertRaises(RuntimeError):
                apply_settings_update(
                    context,
                    current_config,
                    next_config,
                    password="new-password",
                    api_key="new-api-key",
                    hanlim_api_key=None,
                )

            restored = config_manager.load()
            self.assertEqual(restored.user_email, "old@example.com")
            self.assertEqual(secret_store.get_secret(MAIL_PASSWORD_KEY), "old-password")
            self.assertEqual(secret_store.get_secret(GEMINI_API_KEY), "old-api-key")

    def test_apply_settings_update_returns_warning_when_follow_up_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            config_manager = ConfigManager(settings_path)
            current_config = AppConfig(user_email="old@example.com", mailbox="INBOX")
            config_manager.save(current_config)

            secret_store = _FakeSecretStore()
            context = SimpleNamespace(
                config_manager=config_manager,
                secret_store=secret_store,
                sync_service=_FakeSyncService(),
                scheduler_manager=_FakeSchedulerManager(should_fail=True),
                logger=SimpleNamespace(warning=lambda *args, **kwargs: None),
            )
            next_config = AppConfig(user_email="new@example.com", mailbox="INBOX")

            deleted_old_count, follow_up_warning = apply_settings_update(
                context,
                current_config,
                next_config,
                password="new-password",
                api_key=None,
                hanlim_api_key=None,
            )

            saved = config_manager.load()
            self.assertEqual(saved.user_email, "new@example.com")
            self.assertEqual(secret_store.get_secret(MAIL_PASSWORD_KEY), "new-password")
            self.assertEqual(deleted_old_count, 3)
            self.assertIsNotNone(follow_up_warning)
            assert follow_up_warning is not None
            self.assertIn("설정은 저장됐지만", follow_up_warning)

    def test_apply_settings_update_clears_mail_password_when_account_changes_without_new_password(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            config_manager = ConfigManager(settings_path)
            current_config = AppConfig(user_email="old@example.com", mailbox="INBOX")
            config_manager.save(current_config)

            secret_store = _FakeSecretStore()
            secret_store.set_secret(MAIL_PASSWORD_KEY, "old-password")
            context = SimpleNamespace(
                config_manager=config_manager,
                secret_store=secret_store,
                sync_service=_FakeSyncService(),
                scheduler_manager=_FakeSchedulerManager(),
                logger=SimpleNamespace(warning=lambda *args, **kwargs: None),
            )
            next_config = AppConfig(user_email="new@example.com", mailbox="INBOX")

            deleted_old_count, follow_up_warning = apply_settings_update(
                context,
                current_config,
                next_config,
                password=None,
                api_key=None,
                hanlim_api_key=None,
            )

            saved = config_manager.load()
            self.assertEqual(saved.user_email, "new@example.com")
            self.assertEqual(secret_store.get_secret(MAIL_PASSWORD_KEY), "")
            self.assertEqual(deleted_old_count, 3)
            self.assertIsNotNone(follow_up_warning)
            assert follow_up_warning is not None
            self.assertIn("기존 비밀번호를 삭제했습니다", follow_up_warning)

    def test_apply_settings_update_restores_secrets_even_if_config_rollback_fails(self) -> None:
        current_config = AppConfig(user_email="old@example.com", mailbox="INBOX")
        next_config = AppConfig(user_email="new@example.com", mailbox="INBOX")
        config_manager = _FailingRollbackConfigManager(current_config, next_config)
        secret_store = _FakeSecretStore()
        secret_store.set_secret(MAIL_PASSWORD_KEY, "old-password")
        secret_store.set_secret(GEMINI_API_KEY, "old-api-key")
        secret_store.fail_on_key = GEMINI_API_KEY
        context = SimpleNamespace(
            config_manager=config_manager,
            secret_store=secret_store,
            sync_service=_FakeSyncService(),
            scheduler_manager=_FakeSchedulerManager(),
            logger=SimpleNamespace(warning=lambda *args, **kwargs: None),
        )

        with self.assertRaises(RuntimeError):
            apply_settings_update(
                context,
                current_config,
                next_config,
                password="new-password",
                api_key="new-api-key",
                hanlim_api_key=None,
            )

        self.assertTrue(config_manager.rollback_attempted)
        self.assertEqual(secret_store.get_secret(MAIL_PASSWORD_KEY), "old-password")
        self.assertEqual(secret_store.get_secret(GEMINI_API_KEY), "old-api-key")


if __name__ == "__main__":
    unittest.main()
