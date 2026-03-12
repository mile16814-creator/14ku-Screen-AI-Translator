# -*- mode: python ; coding: utf-8 -*-
# 单文件打包：输出一个 ScreenTranslator.exe，无 _internal 目录

import os
from pathlib import Path

_ROOT = Path(os.getcwd()).resolve()
_BUNDLE_MODELS = os.environ.get("SCREEN_TRANSLATOR_BUNDLE_MODELS", "0") == "1"
_BUNDLE_TESSERACT = os.environ.get("SCREEN_TRANSLATOR_BUNDLE_TESSERACT", "0") == "1"

_datas = [
    ("assets", "assets"),
    ("config", "config"),
]

if (_ROOT / "native" / "memscan.dll").exists():
    _datas.append(("native\\memscan.dll", "native"))
elif (_ROOT / "src" / "native" / "memscan.dll").exists():
    _datas.append(("src\\native\\memscan.dll", "native"))

if _BUNDLE_MODELS and (_ROOT / "models").exists():
    _datas.append(("models", "models"))

if _BUNDLE_TESSERACT and (_ROOT / "tesseract").exists():
    _datas.append(("tesseract", "tesseract"))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=_datas,
    hiddenimports=[
        'transformers',
        'transformers.models',
        'transformers.models.auto',
        'transformers.models.m2m_100',
        'transformers.models.m2m_100.modeling_m2m_100',
        'transformers.models.m2m_100.tokenization_m2m_100',
        'transformers.models.nllb',
        'transformers.models.nllb.tokenization_nllb',
        'transformers.tokenization_utils',
        'transformers.tokenization_utils_base',
        'transformers.modeling_utils',
        'transformers.configuration_utils',
        'transformers.file_utils',
        'transformers.utils',
        'sentencepiece',
        'sentencepiece.sentencepiece_pb2',
        'torch',
        'torch.nn',
        'torch.nn.functional',
        'torch._C',
        'torch.jit',
        'numpy',
        'tokenizers',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'pandas', 'pyarrow',
        'sklearn', 'scikit_learn', 'scipy',
        'matplotlib', 'numba', 'llvmlite', 'librosa',
        'tkinter', '_tkinter', 'tcl', 'tk', 'turtle', 'idlelib',
        'pyautogui', 'pyscreeze', 'pymsgbox', 'mouseinfo', 'PIL.ImageTk',
        'setuptools', 'pkg_resources',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ScreenTranslator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\icons\\app_icon.ico'],
)
