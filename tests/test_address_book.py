from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from app.core.address_book import AddressBookService
from app.core.config_manager import AppConfig, ConfigManager


class AddressBookServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.bundle_root = self.root / "bundle"
        self.data_root = self.root / "data"
        self.bundle_root.mkdir(parents=True, exist_ok=True)
        self.data_root.mkdir(parents=True, exist_ok=True)

    def _write_csv(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["name", "department", "title", "email", "company"])
            writer.writeheader()
            writer.writerows(rows)

    def test_bootstrap_copies_bundle_csv_into_data_addressbook(self) -> None:
        source_csv = self.bundle_root / "directory.csv"
        self._write_csv(
            source_csv,
            [
                {
                    "name": "Hong Gil-dong",
                    "department": "Operations",
                    "title": "Manager",
                    "email": "hong@example.com",
                    "company": "MailAI",
                }
            ],
        )

        service = AddressBookService(data_root=self.data_root, bundle_root=self.bundle_root)

        copied_csv = self.data_root / "addressbook" / source_csv.name
        self.assertTrue(copied_csv.exists())
        self.assertEqual(service.resolve_display_name("hong@example.com"), "Hong Gil-dong")

    def test_merge_config_profile_uses_email_match(self) -> None:
        source_csv = self.bundle_root / "directory.csv"
        self._write_csv(
            source_csv,
            [
                {
                    "name": "Dana Lee",
                    "department": "Quality",
                    "title": "Lead",
                    "email": "dana@example.com",
                    "company": "MailAI",
                }
            ],
        )
        service = AddressBookService(data_root=self.data_root, bundle_root=self.bundle_root)

        merged = service.merge_config_profile(AppConfig(user_email="dana@example.com"))

        self.assertEqual(merged.user_display_name, "Dana Lee")
        self.assertEqual(merged.user_department, "Quality")
        self.assertEqual(merged.user_job_title, "Lead")

    def test_sync_user_profile_persists_matched_profile(self) -> None:
        source_csv = self.bundle_root / "directory.csv"
        self._write_csv(
            source_csv,
            [
                {
                    "name": "Kim Seo",
                    "department": "Finance",
                    "title": "Analyst",
                    "email": "kim@example.com",
                    "company": "MailAI",
                }
            ],
        )
        service = AddressBookService(data_root=self.data_root, bundle_root=self.bundle_root)
        config_manager = ConfigManager(self.data_root / "config" / "settings.json")
        config_manager.save(AppConfig(user_email="kim@example.com"))

        merged = service.sync_user_profile(config_manager)

        self.assertEqual(merged.user_display_name, "Kim Seo")
        self.assertEqual(config_manager.load().user_department, "Finance")

    def test_resolve_recipient_tokens_accepts_name_and_name_with_email(self) -> None:
        source_csv = self.bundle_root / "directory.csv"
        self._write_csv(
            source_csv,
            [
                {
                    "name": "Dana Lee",
                    "department": "Quality",
                    "title": "Lead",
                    "email": "dana@example.com",
                    "company": "MailAI",
                },
                {
                    "name": "Kim Seo",
                    "department": "Finance",
                    "title": "Analyst",
                    "email": "kim@example.com",
                    "company": "MailAI",
                },
            ],
        )
        service = AddressBookService(data_root=self.data_root, bundle_root=self.bundle_root)

        recipients = service.resolve_recipient_tokens("Dana Lee, Kim Seo <kim@example.com>")

        self.assertEqual(recipients, ["dana@example.com", "kim@example.com"])

    def test_resolve_recipient_tokens_rejects_ambiguous_names(self) -> None:
        source_csv = self.bundle_root / "directory.csv"
        self._write_csv(
            source_csv,
            [
                {
                    "name": "Alex Kim",
                    "department": "A",
                    "title": "Manager",
                    "email": "alex1@example.com",
                    "company": "MailAI",
                },
                {
                    "name": "Alex Kim",
                    "department": "B",
                    "title": "Director",
                    "email": "alex2@example.com",
                    "company": "MailAI",
                },
            ],
        )
        service = AddressBookService(data_root=self.data_root, bundle_root=self.bundle_root)

        with self.assertRaises(ValueError):
            service.resolve_recipient_tokens("Alex Kim")


if __name__ == "__main__":
    unittest.main()
