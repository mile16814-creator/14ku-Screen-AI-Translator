import sys
import os
import shutil
import subprocess
from pathlib import Path
from PyQt6.QtWidgets import QMessageBox, QFileDialog, QProgressDialog
from PyQt6.QtCore import Qt

class Installer:
    def __init__(self):
        self.is_frozen = getattr(sys, 'frozen', False)
        self.current_exe = Path(sys.executable)
        self.resource_root = Path(getattr(sys, '_MEIPASS', Path(__file__).parent.parent.parent))
        
    def is_shortcut_hint_skipped(self) -> bool:
        """检查是否已经提示过创建快捷方式"""
        # 1. 检查标记文件
        if (self.current_exe.parent / "shortcuts_created.tag").exists():
            return True
            
        # 2. 检查配置文件
        try:
            from config import ConfigManager
            config = ConfigManager(str(self.current_exe.parent))
            if config.get_bool('general', 'skip_shortcut_hint', False):
                return True
        except Exception:
            pass
            
        return False

    def mark_shortcut_hint_skipped(self):
        """记录为已提示过快捷方式创建"""
        try:
            (self.current_exe.parent / "shortcuts_created.tag").touch()
        except Exception:
            pass

    def create_all_shortcuts(self):
        """为当前位置的程序创建快捷方式"""
        try:
            name = "屏幕翻译工具"
            self.create_shortcut(self.current_exe, name)
            self.create_start_menu_shortcut(self.current_exe, name)
        except Exception:
            pass

    def is_installed(self) -> bool:
        """检查程序是否已安装或用户选择跳过安装"""
        # 1. 检查安装标记文件
        if (self.current_exe.parent / "installed.tag").exists():
            return True
            
        # 2. 检查配置文件中的标记
        try:
            from config import ConfigManager
            config = ConfigManager(str(self.current_exe.parent))
            if config.get_bool('general', 'skip_installation_hint', False):
                return True
        except Exception:
            pass
            
        return False

    def install(self, parent_window=None) -> bool:
        """执行安装逻辑"""
        if not self.is_frozen:
            return True # 开发环境跳过
            
        # 1. 选择安装目录
        default_path = str(Path(os.environ.get("LOCALAPPDATA", "C:")) / "ScreenTranslator")
        dest_dir = QFileDialog.getExistingDirectory(
            parent_window, 
            "选择安装位置 (安装到本地磁盘启动更快捷)",
            default_path,
            QFileDialog.Option.ShowDirsOnly
        )
        
        if not dest_dir:
            # 如果用户取消安装，询问是否以后不再提示
            reply = QMessageBox.question(
                parent_window,
                "提示",
                "是否以后不再提示安装？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                try:
                    from config import ConfigManager
                    config = ConfigManager(str(self.current_exe.parent))
                    config.set('general', 'skip_installation_hint', 'true')
                except Exception:
                    pass
            return False
            
        dest_path = Path(dest_dir)
        try:
            # 如果目录不为空，提醒用户
            if dest_path.exists() and any(dest_path.iterdir()):
                reply = QMessageBox.warning(
                    parent_window,
                    "目录不为空",
                    f"所选目录 {dest_dir} 不为空，安装可能会覆盖现有文件。是否继续？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.No:
                    return False

            dest_path.mkdir(parents=True, exist_ok=True)
            
            # 显示进度对话框
            progress = QProgressDialog("正在复制文件...", "取消", 0, 100, parent_window)
            progress.setWindowModality(Qt.WindowModality.WindowModal)
            progress.setMinimumDuration(0)
            progress.setValue(10)
            
            # 2. 复制程序目录内容
            # 在 onedir 模式下，sys.executable 所在目录就是我们的完整运行环境
            app_source_dir = self.current_exe.parent
            
            # 这里的逻辑改为：将源目录的所有内容复制到目标目录
            # 排除掉已经存在的 logs 或其他不需要的文件
            for item in app_source_dir.iterdir():
                if item.name == "logs" or item.name == "installed.tag":
                    continue
                
                dest_item = dest_path / item.name
                try:
                    if item.is_dir():
                        if dest_item.exists():
                            shutil.rmtree(dest_item)
                        shutil.copytree(item, dest_item)
                    else:
                        shutil.copy2(item, dest_item)
                except Exception as e:
                    print(f"Skipping {item.name}: {e}")
            
            target_exe = dest_path / self.current_exe.name
            progress.setValue(80)
            
            # 3. 创建安装标记
            (dest_path / "installed.tag").touch()
            
            # 4. 创建桌面快捷方式
            self.create_shortcut(target_exe, "屏幕翻译工具")
            progress.setValue(90)
            
            # 5. 创建开始菜单快捷方式
            self.create_start_menu_shortcut(target_exe, "屏幕翻译工具")
            progress.setValue(100)
            
            QMessageBox.information(parent_window, "安装成功", f"程序已成功安装至: {dest_dir}\n\n将自动为您创建桌面快捷方式。点击确定重启程序。")
            
            # 6. 启动新位置的程序并退出当前程序
            subprocess.Popen([str(target_exe)], cwd=str(dest_path))
            sys.exit(0)
            
        except Exception as e:
            QMessageBox.critical(parent_window, "安装失败", f"安装过程中出现错误: {str(e)}")
            return False

    def create_shortcut(self, target_path: Path, name: str):
        """创建桌面快捷方式"""
        desktop = Path(os.environ["USERPROFILE"]) / "Desktop"
        self._create_lnk(target_path, desktop / f"{name}.lnk")

    def create_start_menu_shortcut(self, target_path: Path, name: str):
        """创建开始菜单快捷方式"""
        start_menu = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs"
        self._create_lnk(target_path, start_menu / f"{name}.lnk")

    def _create_lnk(self, target_path: Path, shortcut_path: Path):
        """通用的 .lnk 创建函数 (PowerShell)"""
        try:
            ps_script = f"""
            $WshShell = New-Object -ComObject WScript.Shell
            $Shortcut = $WshShell.CreateShortcut("{shortcut_path}")
            $Shortcut.TargetPath = "{target_path}"
            $Shortcut.WorkingDirectory = "{target_path.parent}"
            $Shortcut.IconLocation = "{target_path}"
            $Shortcut.Save()
            """
            subprocess.run(["powershell", "-Command", ps_script], capture_output=True)
        except Exception:
            pass

