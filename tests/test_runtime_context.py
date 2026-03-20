from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from app.runtime_context import _bootstrap_bundled_file, _move_legacy_runtime_dir, _runtime_root


class RuntimeContextHelperTests(unittest.TestCase):
    def test_runtime_root_uses_cache_folder(self) -> None:
        root = Path("C:/MailAI")

        self.assertEqual(_runtime_root(root), root / "cache")

    def test_move_legacy_runtime_dir_moves_top_level_folder_into_cache(self) -> None:
        temp_dir = Path(tempfile.mkdtemp())
        try:
            runtime_root = _runtime_root(temp_dir)
            legacy_dir = temp_dir / "templates"
            legacy_dir.mkdir(parents=True, exist_ok=True)
            (legacy_dir / "sample.txt").write_text("content", encoding="utf-8")

            _move_legacy_runtime_dir(temp_dir, runtime_root, "templates")

            self.assertFalse(legacy_dir.exists())
            self.assertTrue((runtime_root / "templates" / "sample.txt").exists())
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_bootstrap_bundled_file_copies_missing_bundle_asset(self) -> None:
        temp_dir = Path(tempfile.mkdtemp())
        try:
            bundle_root = temp_dir / "bundle"
            target_root = temp_dir / "portable"
            source_path = bundle_root / "config" / "settings.json"
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text('{"mailbox":"INBOX"}', encoding="utf-8")

            target_path = target_root / "cache" / "config" / "settings.json"
            _bootstrap_bundled_file(bundle_root, Path("config") / "settings.json", target_path)

            self.assertTrue(target_path.exists())
            self.assertEqual(target_path.read_text(encoding="utf-8"), '{"mailbox":"INBOX"}')
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
