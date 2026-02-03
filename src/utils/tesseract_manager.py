"""
Tesseract-OCR 管理器
负责自动下载、安装和配置 Tesseract-OCR
"""

import os
import sys
import zipfile
import tempfile
import shutil
import subprocess
import re
from pathlib import Path
from typing import Optional, Tuple, List
from urllib.parse import urljoin

import requests
from PyQt6.QtCore import QThread, pyqtSignal


class TesseractManager:
    """Tesseract-OCR 管理器"""
    
    _configured = False
    _configured_path = None
    
    def __init__(self, app_dir: str):
        self.app_dir = Path(app_dir)
        self.tesseract_dir = self.app_dir / "tesseract"
        self.tesseract_exe = self.tesseract_dir / "tesseract.exe"
        self.tessdata_dir = self.tesseract_dir / "tessdata"
        
        # 创建目录
        self.tesseract_dir.mkdir(exist_ok=True)
        self.tessdata_dir.mkdir(exist_ok=True)
        
        # Tesseract 下载信息
        self.tesseract_version = "5.3.3"
        # UB Mannheim 主页（用于展示给用户查看安装说明）
        self.tesseract_url = "https://github.com/UB-Mannheim/tesseract/wiki"
        # Mannheim Windows 安装包目录（从这里自动解析出最新的安装包）
        self.tesseract_download_base = "https://digi.bib.uni-mannheim.de/tesseract/"
        
        # 语言包信息
        self.language_packs = ['eng', 'jpn', 'kor']
        self.tessdata_base_url = "https://github.com/tesseract-ocr/tessdata/raw/main/"
    
    def is_tesseract_available(self) -> bool:
        """检查 Tesseract 是否可用（通过实际运行测试）"""
        if self.configure_pytesseract():
            return True
        # 兜底检查本地文件是否存在
        return self.tesseract_exe.exists() and self.tesseract_exe.is_file()
    
    def check_language_packs(self) -> bool:
        """检查语言包是否完整"""
        for lang in self.language_packs:
            lang_file = self.tessdata_dir / f"{lang}.traineddata"
            if not lang_file.exists():
                return False
        return True
    
    def get_tesseract_version(self) -> Optional[str]:
        """获取 Tesseract 版本"""
        if not self.is_tesseract_available():
            return None
        
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            result = subprocess.run(
                [str(self.tesseract_exe), "--version"],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='ignore',
                creationflags=creationflags,
            )
            if result.returncode == 0:
                # 提取版本信息
                for line in result.stdout.split('\n'):
                    if 'tesseract' in line.lower():
                        return line.strip()
            return None
        except Exception:
            return None
    
    def configure_pytesseract(self) -> bool:
        """配置 pytesseract 使用本地 Tesseract（只配置一次，避免重复日志）"""
        try:
            import pytesseract
        except ImportError:
            return False
        
        if TesseractManager._configured and TesseractManager._configured_path:
            pytesseract.pytesseract.tesseract_cmd = TesseractManager._configured_path
            if os.environ.get("TESSDATA_PREFIX"):
                pass
            return True

        # 候选 Tesseract 可执行文件路径（按优先级）
        candidates: List[Path | str] = []

        # 1. 优先尝试程序自带的 tesseract.exe
        if self.tesseract_exe.exists():
            candidates.append(self.tesseract_exe)

        # 2. 再尝试系统安装路径
        candidates.extend([
            Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
            Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
        ])

        # 3. 最后尝试 PATH
        candidates.append("tesseract")

        for candidate in candidates:
            try:
                cmd = str(candidate)
                if isinstance(candidate, Path) and not candidate.exists():
                    continue
                
                # 设置环境变量，防止因为找不到训练数据而报错
                # 如果是本地路径，尝试设置 TESSDATA_PREFIX
                current_env = os.environ.copy()
                if isinstance(candidate, Path):
                    tessdata_path = candidate.parent / "tessdata"
                    if tessdata_path.exists():
                        current_env["TESSDATA_PREFIX"] = str(tessdata_path)

                # 测试命令是否真的可用
                creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                result = subprocess.run(
                    [cmd, "--version"], 
                    capture_output=True, 
                    text=True, 
                    shell=False, 
                    env=current_env,
                    creationflags=creationflags
                )
                
                if result.returncode == 0:
                    pytesseract.pytesseract.tesseract_cmd = cmd
                    if "TESSDATA_PREFIX" in current_env:
                        os.environ["TESSDATA_PREFIX"] = current_env["TESSDATA_PREFIX"]
                    
                    TesseractManager._configured = True
                    TesseractManager._configured_path = cmd
                    print(f"成功配置 Tesseract: {cmd}")
                    return True
            except Exception as e:
                print(f"尝试 Tesseract 候选路径 {candidate} 失败: {e}")
                continue

        return False
    
    def download_file(self, url: str, destination: Path) -> bool:
        """下载文件"""
        try:
            response = requests.get(url, stream=True, timeout=30)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            with open(destination, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        # 可以在这里添加进度回调
                        # if total_size > 0:
                        #     progress = (downloaded / total_size) * 100
                        #     print(f"下载进度: {progress:.1f}%")
            
            return True
        except Exception as e:
            print(f"下载失败: {e}")
            if destination.exists():
                destination.unlink()
            return False

    def _get_latest_windows_installer_url(self) -> Optional[str]:
        """
        从 Mannheim 下载目录中自动解析最新的 Windows 64 位安装包链接。
        
        返回：完整的安装包 URL，找不到时返回 None。
        """
        try:
            resp = requests.get(self.tesseract_download_base, timeout=30)
            resp.raise_for_status()
            html = resp.text

            # 匹配类似 tesseract-ocr-w64-setup-5.3.3.20231005.exe 的文件名
            pattern = r"tesseract-ocr-w64-setup-[\d\.]+(?:\.\d+)?\.exe"
            matches = re.findall(pattern, html)
            if not matches:
                return None

            # 去重并选择“最大”的版本字符串作为最新版本
            unique = sorted(set(matches))
            latest_name = unique[-1]

            return urljoin(self.tesseract_download_base, latest_name)
        except Exception:
            return None
    
    def extract_zip(self, zip_path: Path, extract_to: Path) -> bool:
        """解压 ZIP 文件"""
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_to)
            return True
        except Exception as e:
            print(f"解压失败: {e}")
            return False
    
    def download_and_setup_tesseract(self) -> Tuple[bool, str]:
        """
        下载并设置 Tesseract-OCR（自动检查 / 自动下载安装到当前程序目录）
        返回: (成功与否, 消息)
        """
        try:
            # 目前仅实现 Windows 下的自动安装逻辑
            if not sys.platform.startswith("win"):
                return False, "自动安装当前仅支持 Windows，请手动安装 Tesseract-OCR。"

            # 创建临时目录
            temp_dir = Path(tempfile.gettempdir()) / "tesseract_download"
            temp_dir.mkdir(exist_ok=True)

            # 1. 下载 Tesseract 安装包（UB Mannheim 提供的 Windows 安装程序）
            installer_url = self._get_latest_windows_installer_url()
            if not installer_url:
                return False, "无法在 Mannheim 下载页上找到 Windows 安装包，请稍后重试或手动安装 Tesseract。"

            installer_name = installer_url.rsplit("/", 1)[-1]
            installer_path = temp_dir / installer_name
            print(f"正在下载 Tesseract-OCR 安装包: {installer_name} ...")

            if not self.download_file(installer_url, installer_path):
                return False, "下载 Tesseract 安装包失败，请检查网络连接或稍后重试。"

            try:
                subprocess.Popen([str(installer_path)], cwd=str(temp_dir))
            except Exception as e:
                return False, f"启动 Tesseract 安装程序失败: {e}"

            return True, "已启动 Tesseract 安装程序，请完成安装后重启应用或重新检测。"

        except Exception as e:
            return False, f"设置失败: {str(e)}"
    
    def _create_mock_tesseract(self):
        """创建模拟的 tesseract.exe（用于测试）"""
        # 在实际项目中，应该下载真实的 Tesseract
        # 这里创建一个简单的批处理文件来模拟 tesseract
        mock_content = """@echo off
echo Tesseract 5.3.3
echo.
if "%1"=="--version" (
    echo tesseract 5.3.3.20231005
    echo leptonica-1.83.0
    echo  libgif 5.2.1 : libjpeg 9e : libpng 1.6.39 : libtiff 4.5.1 : zlib 1.2.13 : libwebp 1.3.2 : libopenjp2 2.5.0
    exit /b 0
)
echo Usage: tesseract --help for more information
exit /b 1
"""
        
        with open(self.tesseract_exe, 'w', encoding='utf-8') as f:
            f.write(mock_content)
    
    def _create_mock_language_file(self, file_path: Path):
        """创建模拟的语言包文件"""
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(f"Mock language data for {file_path.name}\n")
            f.write("This is a placeholder for actual .traineddata file\n")
    
    def cleanup(self):
        """清理临时文件"""
        temp_dir = Path(tempfile.gettempdir()) / "tesseract_download"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


class TesseractInstallThread(QThread):
    """Tesseract 安装线程（用于后台安装）"""
    
    progress = pyqtSignal(int, str)  # 进度百分比, 状态消息
    finished = pyqtSignal(bool, str)  # 成功与否, 最终消息
    
    def __init__(self, app_dir: str):
        super().__init__()
        self.app_dir = app_dir
        self.manager = TesseractManager(app_dir)
    
    def run(self):
        """线程运行函数"""
        try:
            self.progress.emit(0, "正在检查 Tesseract-OCR...")
            
            if self.manager.is_tesseract_available() and self.manager.check_language_packs():
                self.progress.emit(100, "Tesseract-OCR 已就绪")
                self.finished.emit(True, "Tesseract-OCR 已就绪")
                return
            
            self.progress.emit(10, "开始下载 Tesseract-OCR...")
            
            # 下载并设置 Tesseract
            success, message = self.manager.download_and_setup_tesseract()
            
            if success:
                self.progress.emit(100, "安装程序已启动")
                self.finished.emit(True, "Tesseract-OCR 安装程序已启动")
            else:
                self.progress.emit(0, f"安装失败: {message}")
                self.finished.emit(False, f"安装失败: {message}")
                
        except Exception as e:
            error_msg = f"安装过程中发生错误: {str(e)}"
            self.progress.emit(0, error_msg)
            self.finished.emit(False, error_msg)
        finally:
            self.manager.cleanup()
