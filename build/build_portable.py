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


def _stage_release_folder(root: Path, executable_path: Path) -> Path:
    """Create a timestamped release folder with the packaged executable only."""

    release_root = root / "release"
    release_root.mkdir(parents=True, exist_ok=True)
    release_dir = release_root / f"MailAI_Portable_Deploy_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    release_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(executable_path, release_dir / executable_path.name)
    return release_dir


def _cleanup_legacy_dist_folder(root: Path) -> None:
    legacy_dist_dir = root / "dist" / "MailAI_Portable"
    if legacy_dist_dir.exists():
        shutil.rmtree(legacy_dist_dir, ignore_errors=True)


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

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        "MailAI_Portable",
        "--copy-metadata",
        "streamlit",
        "--copy-metadata",
        "pywebview",
        "--copy-metadata",
        "pystray",
        "--copy-metadata",
        "openai",
        "--collect-data",
        "streamlit",
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
        _cleanup_legacy_dist_folder(root)
        result = subprocess.call(command, cwd=root)
        if result == 0:
            _cleanup_legacy_dist_folder(root)
            executable_path = root / "dist" / "MailAI_Portable.exe"
            release_dir = _stage_release_folder(root, executable_path)
            print(f"Release folder created at: {release_dir}")
        return result
    finally:
        shutil.rmtree(clean_config_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
