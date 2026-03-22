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


if __name__ == "__main__":
    unittest.main()
