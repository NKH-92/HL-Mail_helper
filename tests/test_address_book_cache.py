from __future__ import annotations

import csv
import tempfile
import time
import unittest
from pathlib import Path

from app.core.address_book import AddressBookService


class AddressBookCacheTests(unittest.TestCase):
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

    def test_contact_options_are_copied_and_refreshed_when_source_changes(self) -> None:
        source_csv = self.bundle_root / "addressbook.csv"
        self._write_csv(
            source_csv,
            [
                {
                    "name": "Alice",
                    "department": "Ops",
                    "title": "Manager",
                    "email": "alice@example.com",
                    "company": "MailAI",
                }
            ],
        )

        service = AddressBookService(data_root=self.data_root, bundle_root=self.bundle_root)

        options = service.list_contact_options(limit=10)
        options[0]["label"] = "mutated"
        cached_options = service.list_contact_options(limit=10)
        cached_contact = service.get_contact("alice@example.com")

        self.assertEqual(cached_options[0]["label"], "Alice / Ops / Manager <alice@example.com>")
        self.assertIsNotNone(cached_contact)
        assert cached_contact is not None
        self.assertEqual(cached_contact.name, "Alice")

        time.sleep(0.02)
        copied_csv = self.data_root / "addressbook" / source_csv.name
        self._write_csv(
            copied_csv,
            [
                {
                    "name": "Alicia",
                    "department": "Ops",
                    "title": "Director",
                    "email": "alice@example.com",
                    "company": "MailAI",
                },
                {
                    "name": "Bob",
                    "department": "Sales",
                    "title": "Lead",
                    "email": "bob@example.com",
                    "company": "MailAI",
                },
            ],
        )

        refreshed_options = service.list_contact_options(limit=10)
        refreshed_contact = service.get_contact("alice@example.com")

        self.assertEqual([entry["email"] for entry in refreshed_options], ["alice@example.com", "bob@example.com"])
        self.assertIsNotNone(refreshed_contact)
        assert refreshed_contact is not None
        self.assertEqual(refreshed_contact.name, "Alicia")
        self.assertEqual(refreshed_contact.title, "Director")


if __name__ == "__main__":
    unittest.main()
