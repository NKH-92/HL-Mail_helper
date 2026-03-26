from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


_BUILD_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "build" / "build_portable.py"
_BUILD_SCRIPT_SPEC = importlib.util.spec_from_file_location("test_build_portable_module", _BUILD_SCRIPT_PATH)
assert _BUILD_SCRIPT_SPEC is not None and _BUILD_SCRIPT_SPEC.loader is not None
build_portable = importlib.util.module_from_spec(_BUILD_SCRIPT_SPEC)
_BUILD_SCRIPT_SPEC.loader.exec_module(build_portable)


class BuildPortableTests(unittest.TestCase):
    def test_build_pyinstaller_command_uses_onedir_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app").mkdir()
            entrypoint = root / "run_portable.py"
            entrypoint.write_text("print('ok')", encoding="utf-8")

            clean_config_root = root / "clean"
            (clean_config_root / "config").mkdir(parents=True)

            command = build_portable._build_pyinstaller_command(root, entrypoint, clean_config_root)

            self.assertIn("--onedir", command)
            self.assertNotIn("--onefile", command)

    def test_build_pyinstaller_command_skips_missing_optional_addressbook(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app").mkdir()
            (root / ".streamlit").mkdir()
            entrypoint = root / "run_portable.py"
            entrypoint.write_text("print('ok')", encoding="utf-8")

            clean_config_root = root / "clean"
            (clean_config_root / "config").mkdir(parents=True)

            command = build_portable._build_pyinstaller_command(root, entrypoint, clean_config_root)

            self.assertNotIn(f"{root / 'addressbook'};addressbook", command)
            self.assertIn(f"{root / 'app'};app", command)
            self.assertIn(f"{clean_config_root / 'config'};config", command)

    def test_build_pyinstaller_command_includes_addressbook_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app").mkdir()
            entrypoint = root / "run_portable.py"
            entrypoint.write_text("print('ok')", encoding="utf-8")

            clean_config_root = root / "clean"
            (clean_config_root / "config").mkdir(parents=True)
            (root / "addressbook").mkdir()

            command = build_portable._build_pyinstaller_command(root, entrypoint, clean_config_root)

            self.assertIn(f"{root / 'addressbook'};addressbook", command)

    def test_build_pyinstaller_command_collects_keyring_for_lazy_imports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app").mkdir()
            entrypoint = root / "run_portable.py"
            entrypoint.write_text("print('ok')", encoding="utf-8")

            clean_config_root = root / "clean"
            (clean_config_root / "config").mkdir(parents=True)

            command = build_portable._build_pyinstaller_command(root, entrypoint, clean_config_root)
            pairs = [command[index : index + 2] for index in range(len(command) - 1)]

            self.assertIn(["--copy-metadata", "keyring"], pairs)
            self.assertIn(["--collect-all", "keyring"], pairs)

    def test_stage_release_folder_copies_entire_bundle_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle_dir = root / "dist" / "MailAI_Portable"
            bundle_dir.mkdir(parents=True)
            (bundle_dir / "MailAI_Portable.exe").write_text("exe", encoding="utf-8")
            internal_dir = bundle_dir / "_internal"
            internal_dir.mkdir()
            (internal_dir / "app.txt").write_text("data", encoding="utf-8")

            release_dir = build_portable._stage_release_folder(root, bundle_dir)

            copied_bundle_dir = release_dir / "MailAI_Portable"
            self.assertTrue((copied_bundle_dir / "MailAI_Portable.exe").exists())
            self.assertTrue((copied_bundle_dir / "_internal" / "app.txt").exists())

    def test_cleanup_previous_build_outputs_removes_bundle_and_legacy_executables(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle_dir = root / "dist" / "MailAI_Portable"
            bundle_dir.mkdir(parents=True)
            (bundle_dir / "MailAI_Portable.exe").write_text("exe", encoding="utf-8")
            (root / "dist" / "MailAI_Portable.exe").write_text("legacy", encoding="utf-8")
            (root / "dist" / "HL-Mail.exe").write_text("legacy", encoding="utf-8")

            build_portable._cleanup_previous_build_outputs(root)

            self.assertFalse(bundle_dir.exists())
            self.assertFalse((root / "dist" / "MailAI_Portable.exe").exists())
            self.assertFalse((root / "dist" / "HL-Mail.exe").exists())


if __name__ == "__main__":
    unittest.main()
