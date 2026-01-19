# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

_ROOT = Path(os.getcwd()).resolve()

datas = []
binaries = []
hiddenimports = []

# Collect all frida dependencies to ensure frida-helper is included
try:
    tmp_datas, tmp_binaries, tmp_hidden = collect_all('frida')
    datas += tmp_datas
    binaries += tmp_binaries
    hiddenimports += tmp_hidden
except Exception:
    pass

a = Analysis(
    ['hook_agent.py'],
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
    name='HookAgent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='HookAgent',
)

