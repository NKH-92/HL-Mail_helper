from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from app.db.database import DatabaseManager
from app.db.models import MailTemplate
from app.db.repositories import MailTemplateRepository
from app.services.mail_template_service import MailTemplateService


class MailTemplatePersistenceTests(unittest.TestCase):
    def test_mail_template_repository_round_trip(self) -> None:
        temp_dir = Path(tempfile.mkdtemp())
        try:
            database = DatabaseManager(temp_dir / "app.db")
            repository = MailTemplateRepository(database)

            template_id = repository.upsert(
                MailTemplate(
                    id=None,
                    template_name="Morning Brief",
                    subject="Daily update",
                    body="Body",
                    to_list=["to@example.com"],
                    cc_list=["cc@example.com"],
                    attachment_paths=["templates/presets/example.txt"],
                    repeat_type="daily",
                    send_time="09:00",
                    first_send_at="2026-03-09 09:00:00",
                )
            )

            saved = repository.get(template_id)

            self.assertIsNotNone(saved)
            assert saved is not None
            self.assertEqual(saved.template_name, "Morning Brief")
            self.assertEqual(saved.to_list, ["to@example.com"])
            self.assertEqual(saved.cc_list, ["cc@example.com"])
            self.assertEqual(saved.repeat_type, "daily")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_mail_template_service_copies_external_attachment(self) -> None:
        portable_root = Path(tempfile.mkdtemp())
        try:
            source_path = portable_root / "external.txt"
            source_path.write_text("attachment", encoding="utf-8")

            database = DatabaseManager(portable_root / "app.db")
            repository = MailTemplateRepository(database)
            service = MailTemplateService(repository, portable_root=portable_root)

            template_id = service.save_template(
                MailTemplate(
                    id=None,
                    template_name="Preset",
                    subject="Subject",
                    body="Body",
                    to_list=[],
                    cc_list=[],
                    attachment_paths=[str(source_path)],
                    repeat_type="none",
                    send_time="09:00",
                    first_send_at="2026-03-09 09:00:00",
                )
            )

            saved = repository.get(template_id)

            self.assertIsNotNone(saved)
            assert saved is not None
            self.assertTrue(saved.attachment_paths)
            self.assertTrue((portable_root / saved.attachment_paths[0]).exists())
        finally:
            shutil.rmtree(portable_root, ignore_errors=True)

    def test_mail_template_service_preserves_copied_attachments_on_update(self) -> None:
        portable_root = Path(tempfile.mkdtemp())
        external_root = Path(tempfile.mkdtemp())
        try:
            source_path = external_root / "external.txt"
            source_path.write_text("attachment", encoding="utf-8")

            database = DatabaseManager(portable_root / "app.db")
            repository = MailTemplateRepository(database)
            service = MailTemplateService(repository, portable_root=portable_root)

            template_id = service.save_template(
                MailTemplate(
                    id=None,
                    template_name="Preset",
                    subject="Subject",
                    body="Body",
                    to_list=[],
                    cc_list=[],
                    attachment_paths=[str(source_path)],
                    repeat_type="none",
                    send_time="09:00",
                    first_send_at="2026-03-09 09:00:00",
                )
            )

            saved = service.get_template(template_id)

            self.assertIsNotNone(saved)
            assert saved is not None

            updated_id = service.save_template(
                MailTemplate(
                    id=template_id,
                    template_name="Preset",
                    subject="Updated subject",
                    body="Updated body",
                    to_list=[],
                    cc_list=["cc@example.com"],
                    attachment_paths=saved.attachment_paths,
                    repeat_type="none",
                    send_time="10:30",
                    first_send_at="2026-03-10 10:30:00",
                )
            )

            updated = service.get_template(updated_id)

            self.assertEqual(updated_id, template_id)
            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertEqual(updated.attachment_paths, saved.attachment_paths)
            self.assertTrue((portable_root / updated.attachment_paths[0]).exists())
        finally:
            shutil.rmtree(portable_root, ignore_errors=True)
            shutil.rmtree(external_root, ignore_errors=True)

    def test_mail_template_service_rolls_back_new_template_when_attachment_is_missing(self) -> None:
        portable_root = Path(tempfile.mkdtemp())
        try:
            database = DatabaseManager(portable_root / "app.db")
            repository = MailTemplateRepository(database)
            service = MailTemplateService(repository, portable_root=portable_root)

            with self.assertRaises(FileNotFoundError):
                service.save_template(
                    MailTemplate(
                        id=None,
                        template_name="Broken preset",
                        subject="Subject",
                        body="Body",
                        to_list=[],
                        cc_list=[],
                        attachment_paths=["missing.txt"],
                        repeat_type="none",
                        send_time="09:00",
                        first_send_at="2026-03-09 09:00:00",
                    )
                )

            self.assertEqual(service.list_templates(), [])
        finally:
            shutil.rmtree(portable_root, ignore_errors=True)

    def test_mail_template_service_copies_cached_mail_attachments_into_preset_storage(self) -> None:
        portable_root = Path(tempfile.mkdtemp())
        try:
            cached_attachment = portable_root / "cache" / "mail_attachments" / "message-2" / "brief.txt"
            cached_attachment.parent.mkdir(parents=True, exist_ok=True)
            cached_attachment.write_text("attachment", encoding="utf-8")

            database = DatabaseManager(portable_root / "app.db")
            repository = MailTemplateRepository(database)
            service = MailTemplateService(repository, portable_root=portable_root)

            template_id = service.save_template(
                MailTemplate(
                    id=None,
                    template_name="Preset",
                    subject="Subject",
                    body="Body",
                    to_list=[],
                    cc_list=[],
                    attachment_paths=[cached_attachment.relative_to(portable_root).as_posix()],
                    repeat_type="none",
                    send_time="09:00",
                    first_send_at="2026-03-09 09:00:00",
                )
            )

            saved = service.get_template(template_id)

            self.assertIsNotNone(saved)
            assert saved is not None
            self.assertEqual(len(saved.attachment_paths), 1)
            self.assertTrue(saved.attachment_paths[0].startswith(f"templates/presets/preset_{template_id}/"))
            self.assertTrue((portable_root / saved.attachment_paths[0]).exists())
        finally:
            shutil.rmtree(portable_root, ignore_errors=True)

    def test_mail_template_service_reads_legacy_relative_paths_after_cache_relocation(self) -> None:
        portable_root = Path(tempfile.mkdtemp())
        try:
            legacy_relative_path = Path("templates") / "presets" / "legacy.txt"
            relocated_path = portable_root / "cache" / legacy_relative_path
            relocated_path.parent.mkdir(parents=True, exist_ok=True)
            relocated_path.write_text("attachment", encoding="utf-8")

            database = DatabaseManager(portable_root / "app.db")
            repository = MailTemplateRepository(database)
            service = MailTemplateService(
                repository,
                portable_root=portable_root,
                attachments_subdir=Path("cache") / "templates" / "presets",
            )

            template_id = service.save_template(
                MailTemplate(
                    id=None,
                    template_name="Preset",
                    subject="Subject",
                    body="Body",
                    to_list=[],
                    cc_list=[],
                    attachment_paths=[legacy_relative_path.as_posix()],
                    repeat_type="none",
                    send_time="09:00",
                    first_send_at="2026-03-09 09:00:00",
                )
            )

            saved = service.get_template(template_id)

            self.assertIsNotNone(saved)
            assert saved is not None
            self.assertTrue(saved.attachment_paths[0].startswith(f"cache/templates/presets/preset_{template_id}/"))
            self.assertTrue((portable_root / saved.attachment_paths[0]).exists())
        finally:
            shutil.rmtree(portable_root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
