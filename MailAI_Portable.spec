# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import copy_metadata


ROOT = Path(__file__).resolve().parent

datas = [
    (str(ROOT / "app"), "app"),
    (str(ROOT / ".streamlit"), ".streamlit"),
    (str(ROOT / "addressbook"), "addressbook"),
]
binaries = []
hiddenimports = ["streamlit.runtime.scriptrunner.magic_funcs"]

datas += collect_data_files("streamlit")
datas += copy_metadata("streamlit")
datas += copy_metadata("pywebview")
datas += copy_metadata("pystray")
datas += copy_metadata("openai")
hiddenimports += collect_submodules("openai")
hiddenimports += collect_submodules("app")

tmp_ret = collect_all("webview")
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]

tmp_ret = collect_all("pystray")
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]


a = Analysis(
    [str(ROOT / "run_portable.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="MailAI_Portable",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
