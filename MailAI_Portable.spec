# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.hooks import copy_metadata

datas = [('E:\\99.Codex Project\\PM_V2\\app', 'app'), ('C:\\Users\\User\\AppData\\Local\\Temp\\mailai-build-cuw3h_n1\\config', 'config'), ('E:\\99.Codex Project\\PM_V2\\.streamlit', '.streamlit'), ('E:\\99.Codex Project\\PM_V2\\prompts', 'prompts'), ('E:\\99.Codex Project\\PM_V2\\addressbook', 'addressbook')]
binaries = []
hiddenimports = ['streamlit.runtime.scriptrunner.magic_funcs']
datas += collect_data_files('streamlit')
datas += copy_metadata('streamlit')
datas += copy_metadata('pywebview')
datas += copy_metadata('pystray')
datas += copy_metadata('openai')
hiddenimports += collect_submodules('openai')
hiddenimports += collect_submodules('app')
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pystray')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['E:\\99.Codex Project\\PM_V2\\run_portable.py'],
    pathex=[],
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
    [],
    exclude_binaries=True,
    name='MailAI_Portable',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MailAI_Portable',
)
