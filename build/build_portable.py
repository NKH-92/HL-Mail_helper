"""Build a one-folder portable package with PyInstaller."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config_manager import (
    AI_PROVIDER_HANLIM,
    AppConfig,
    DEFAULT_HANLIM_AI_BASE_URL,
    DEFAULT_HANLIM_MODEL,
)


def _build_release_config() -> AppConfig:
    """Return clean packaged defaults aligned to the local Hanlim AI provider."""

    return AppConfig(
        ai_provider=AI_PROVIDER_HANLIM,
        ai_base_url=DEFAULT_HANLIM_AI_BASE_URL,
        gemini_model=DEFAULT_HANLIM_MODEL,
    ).normalized()


def _create_clean_config_dir() -> Path:
    """Stage a clean config directory so user-specific settings are not packaged."""

    temp_root = Path(tempfile.mkdtemp(prefix="mailai-build-"))
    config_dir = temp_root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    with (config_dir / "settings.json").open("w", encoding="utf-8") as handle:
        json.dump(asdict(_build_release_config()), handle, indent=2, ensure_ascii=False)
    return temp_root


def _stage_release_folder(root: Path, bundle_dir: Path) -> Path:
    """Create a timestamped release folder with the packaged onedir bundle."""

    release_root = root / "release"
    release_root.mkdir(parents=True, exist_ok=True)
    release_dir = release_root / f"MailAI_Portable_Deploy_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    release_dir.mkdir(parents=True, exist_ok=True)
    target_dir = release_dir / bundle_dir.name
    shutil.copytree(bundle_dir, target_dir, dirs_exist_ok=True)
    return release_dir


def _cleanup_previous_build_outputs(root: Path) -> None:
    bundle_dir = root / "dist" / "MailAI_Portable"
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir, ignore_errors=True)

    for executable_name in ["MailAI_Portable.exe", "HL-Mail.exe"]:
        executable_path = root / "dist" / executable_name
        if executable_path.exists():
            executable_path.unlink(missing_ok=True)


def _append_add_data_args(
    command: list[str],
    source_path: Path,
    target_name: str,
    *,
    required: bool = False,
) -> None:
    """Append one --add-data pair when the source exists."""

    if not source_path.exists():
        if required:
            raise FileNotFoundError(f"Required PyInstaller data source is missing: {source_path}")
        return
    command.extend(["--add-data", f"{source_path};{target_name}"])


def _build_pyinstaller_command(root: Path, entrypoint: Path, clean_config_root: Path) -> list[str]:
    """Build the PyInstaller command with optional runtime assets."""

    icon_path = root / "icon.png"
    spec_dir = clean_config_root / "pyinstaller-spec"
    spec_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--name",
        "MailAI_Portable",
        "--specpath",
        str(spec_dir),
        "--copy-metadata",
        "streamlit",
        "--copy-metadata",
        "pywebview",
        "--copy-metadata",
        "pystray",
        "--copy-metadata",
        "openai",
        "--copy-metadata",
        "keyring",
        "--collect-data",
        "streamlit",
        "--collect-all",
        "keyring",
        "--collect-all",
        "webview",
        "--collect-all",
        "pystray",
        "--collect-submodules",
        "openai",
        "--collect-submodules",
        "app",
        "--hidden-import",
        "streamlit.runtime.scriptrunner.magic_funcs",
    ]
    if icon_path.exists():
        command.extend(["--icon", str(icon_path)])
    _append_add_data_args(command, root / "app", "app", required=True)
    _append_add_data_args(command, clean_config_root / "config", "config", required=True)
    _append_add_data_args(command, root / ".streamlit", ".streamlit")
    _append_add_data_args(command, root / "addressbook", "addressbook")
    command.append(str(entrypoint))
    return command


def main() -> int:
    root = ROOT
    entrypoint = root / "run_portable.py"
    clean_config_root = _create_clean_config_dir()
    command = _build_pyinstaller_command(root, entrypoint, clean_config_root)
    try:
        _cleanup_previous_build_outputs(root)
        result = subprocess.call(command, cwd=root)
        if result == 0:
            bundle_dir = root / "dist" / "MailAI_Portable"
            release_dir = _stage_release_folder(root, bundle_dir)
            print(f"Release folder created at: {release_dir}")
        return result
    finally:
        shutil.rmtree(clean_config_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
