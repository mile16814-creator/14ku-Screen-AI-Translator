# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

# 默认不打包本地模型与 OCR(tesseract) 资源：
# - 你可以在安装/解压后，把 `models/`、`tesseract/` 手动放到 ScreenTranslator.exe 同级目录
# - 如需“内置打包”，构建前设置环境变量：
#   - set SCREEN_TRANSLATOR_BUNDLE_MODELS=1
#   - set SCREEN_TRANSLATOR_BUNDLE_TESSERACT=1

# PyInstaller 在执行 spec 时不一定提供 __file__，所以用构建时工作目录作为根目录。
# build.bat 已经 cd 到项目根目录，因此这里可靠。
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
        # Transformers 核心模块
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
        # SentencePiece
        'sentencepiece',
        'sentencepiece.sentencepiece_pb2',
        # Torch 核心模块
        'torch',
        'torch.nn',
        'torch.nn.functional',
        'torch._C',
        'torch.jit',
        # 其他可能需要的模块
        'numpy',
        'tokenizers',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 排除不需要的库以减小打包体积
    excludes=[
        'pandas', 'pyarrow',
        'sklearn', 'scikit_learn', 'scipy',
        'matplotlib', 'numba', 'llvmlite', 'librosa',
        # Not used by this app (PyQt UI). Excluding them prevents bundling large Tcl/Tk runtime data.
        'tkinter', '_tkinter', 'tcl', 'tk', 'turtle', 'idlelib',
        # pyautogui is not used (previously imported accidentally) and may pull in tkinter/Tcl.
        'pyautogui', 'pyscreeze', 'pymsgbox', 'mouseinfo', 'PIL.ImageTk',
        # Build/packaging helpers not needed at runtime
        'setuptools', 'pkg_resources', 'distutils',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ScreenTranslator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    # 注意: PyInstaller 6.x会自动检测Python架构并使用对应的bootloader
    # 如果Python是64位的，会自动使用64位bootloader，无需手动指定target_arch
    # 如果构建时出现"target_arch"相关错误，请移除下面这行
    # target_arch='x86_64',  # PyInstaller 6.x可能不再需要此参数
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\icons\\app_icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='ScreenTranslator',
)
