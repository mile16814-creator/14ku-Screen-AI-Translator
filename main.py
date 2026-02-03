#!/usr/bin/env python3
"""
屏幕翻译工具 - 主程序入口
整合所有模块并启动应用程序
"""

import sys
import os
import logging
from pathlib import Path
import traceback
import time


def _enable_windows_per_monitor_dpi_awareness() -> None:
    """
    Make the process Per-Monitor DPI aware on Windows so QScreen reports correct per-display DPI/scales.
    Must be called BEFORE creating QApplication (ideally before importing Qt as well).
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes  # type: ignore

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        # Best: Per-Monitor V2 (Windows 10+)
        try:
            if hasattr(user32, "SetProcessDpiAwarenessContext"):
                DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
                user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
                return
        except Exception:
            pass

        # Fallback: shcore SetProcessDpiAwareness (Windows 8.1+)
        try:
            shcore = ctypes.windll.shcore  # type: ignore[attr-defined]
            PROCESS_PER_MONITOR_DPI_AWARE = 2
            shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE)
            return
        except Exception:
            pass

        # Last resort: system DPI aware
        try:
            if hasattr(user32, "SetProcessDPIAware"):
                user32.SetProcessDPIAware()
        except Exception:
            pass
    except Exception:
        pass

def get_resource_root() -> Path:
    """
    Directory that contains bundled resources.
    - dev: directory of this file
    - PyInstaller onefile: sys._MEIPASS (temp extraction dir)
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).parent


def get_app_root() -> Path:
    """
    Writable directory for logs/config.
    - dev: directory of this file
    - frozen: directory of the executable
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


resource_root = get_resource_root()
app_root = get_app_root()

# 添加项目根目录到Python路径（打包版通常不需要，但保留兼容性）
sys.path.insert(0, str(resource_root))

def _write_bootstrap_log(message: str):
    """Write early-startup diagnostics to app_root/logs/bootstrap.log."""
    try:
        log_dir = app_root / "logs"
        log_dir.mkdir(exist_ok=True, parents=True)
        log_file = log_dir / "bootstrap.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(message.rstrip() + "\n")
    except Exception:
        pass


def _start_console_hide_watcher():
    if sys.platform != "win32":
        return None, None
    try:
        import threading
        import ctypes
    except Exception:
        return None, None
    stop_event = threading.Event()
    try:
        u32 = ctypes.windll.user32
    except Exception:
        return None, None

    def _is_descendant(pid: int, root_pid: int) -> bool:
        if pid <= 0:
            return False
        if pid == root_pid:
            return True
        try:
            import psutil
        except Exception:
            return False
        try:
            p = psutil.Process(int(pid))
        except Exception:
            return False
        for _ in range(20):
            try:
                if p.pid == root_pid:
                    return True
                p = p.parent()
            except Exception:
                break
            if p is None:
                break
        return False

    def _hide_console_windows_for_children() -> None:
        root_pid = os.getpid()

        def _enum_proc(hwnd, _lparam):
            try:
                class_name = ctypes.create_unicode_buffer(256)
                if u32.GetClassNameW(hwnd, class_name, 256) == 0:
                    return True
                if class_name.value != "ConsoleWindowClass":
                    return True
                pid = ctypes.c_ulong()
                u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if not _is_descendant(int(pid.value or 0), root_pid):
                    return True
                try:
                    u32.ShowWindow(hwnd, 0)
                except Exception:
                    pass
            except Exception:
                pass
            return True

        try:
            cb = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(_enum_proc)
            u32.EnumWindows(cb, 0)
        except Exception:
            pass

    def _loop():
        while not stop_event.is_set():
            try:
                _hide_console_windows_for_children()
            except Exception:
                pass
            stop_event.wait(0.3)

    th = threading.Thread(target=_loop, daemon=True)
    th.start()
    return stop_event, th


class ScreenTranslatorApp:
    """屏幕翻译应用程序主类"""
    
    def __init__(self):
        """初始化应用程序"""
        self._startup_t0 = time.perf_counter()
        # 兜底：把未捕获异常写到 exe 同目录 logs/crash.log，避免“闪退无信息”
        self._install_crash_logger()
        _enable_windows_per_monitor_dpi_awareness()
        self._ensure_qt_plugin_paths()
        self._console_hide_stop, self._console_hide_thread = _start_console_hide_watcher()

        # Delay imports so that we can log failures (e.g. missing Qt DLLs) into bootstrap.log/crash.log.
        try:
            from PyQt6.QtWidgets import QApplication
            from PyQt6.QtGui import QIcon, QGuiApplication
            from PyQt6.QtCore import QTranslator, QLibraryInfo, QLocale, Qt
        except Exception as e:
            _write_bootstrap_log("Failed to import PyQt6. Exception:")
            _write_bootstrap_log(repr(e))
            _write_bootstrap_log("Traceback:")
            _write_bootstrap_log("".join(traceback.format_exc()))
            raise

        self._QIcon = QIcon
        self._QTranslator = QTranslator
        self._QLibraryInfo = QLibraryInfo
        self._QLocale = QLocale

        # 高 DPI/多显示器缩放：需在创建 QApplication 之前设置
        try:
            try:
                QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
                    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
                )
            except Exception:
                pass
            try:
                QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
            except Exception:
                pass
            # 兼容：Qt5/部分绑定下仍需要此开关（Qt6 下通常无害）
            try:
                QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
            except Exception:
                pass
        except Exception:
            pass

        self.app = QApplication(sys.argv)
        self.app.setApplicationName("屏幕翻译工具")
        self.app.setApplicationVersion("1.0.0")

        # 尝试加载 Qt 自带中文翻译包（用于 QColorDialog 等标准控件的中文化）
        self._qt_translators = []
        self._install_qt_translations()
        
        # 设置应用程序图标
        assets_root = app_root if (app_root / "assets").exists() else resource_root
        icon_path = assets_root / "assets" / "icons" / "app_icon.ico"
        if icon_path.exists():
            self.app.setWindowIcon(self._QIcon(str(icon_path)))
        
        # 初始化配置管理器
        # (delay import to avoid failing before crash logger is installed)
        from config import ConfigManager
        self.config_manager = ConfigManager(str(app_root))
        
        # 设置日志
        self.setup_logging()
        
        # 初始化核心组件
        self.tesseract_manager = None
        self.translator = None
        self.ocr_processor = None
        self.main_window = None
        self.hotkey_manager = None
        
        # 初始化状态
        self.is_initialized = False
        self.init_error = None

    def _install_crash_logger(self) -> None:
        def _write_crash(exc_type, exc, tb) -> None:
            try:
                log_dir = app_root / "logs"
                log_dir.mkdir(exist_ok=True, parents=True)
                crash_file = log_dir / "crash.log"
                with open(crash_file, "a", encoding="utf-8") as f:
                    f.write("=" * 80 + "\n")
                    f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
                    f.write("".join(traceback.format_exception(exc_type, exc, tb)))
            except Exception:
                pass

        try:
            sys.excepthook = _write_crash
        except Exception:
            pass

        try:
            import threading

            def _threading_hook(args) -> None:
                _write_crash(args.exc_type, args.exc_value, args.exc_traceback)

            threading.excepthook = _threading_hook
        except Exception:
            pass

    def _ensure_qt_plugin_paths(self) -> None:
        try:
            candidates: list[Path] = []

            if getattr(sys, "frozen", False):
                candidates.extend(
                    [
                        resource_root / "_internal" / "PyQt6" / "Qt6",
                        app_root / "_internal" / "PyQt6" / "Qt6",
                    ]
                )
            else:
                candidates.extend(
                    [
                        resource_root / "dist" / "ScreenTranslator" / "_internal" / "PyQt6" / "Qt6",
                        app_root / "dist" / "ScreenTranslator" / "_internal" / "PyQt6" / "Qt6",
                    ]
                )

            qt6_root = None
            for p in candidates:
                if (p / "plugins").exists():
                    qt6_root = p
                    break

            if qt6_root is None:
                return

            plugins_dir = qt6_root / "plugins"
            bin_dir = qt6_root / "bin"

            if plugins_dir.exists():
                os.environ.setdefault("QT_PLUGIN_PATH", str(plugins_dir))

            if bin_dir.exists():
                prev = os.environ.get("PATH", "")
                os.environ["PATH"] = str(bin_dir) + os.pathsep + prev
        except Exception:
            pass

    def _install_qt_translations(self) -> None:
        """
        Load Qt built-in translations (zh_CN) so that standard dialogs/widgets (e.g. QColorDialog)
        show Chinese labels when using non-native dialogs.
        """
        try:
            # 强制默认区域为中文（不影响你手写的 UI 文案，只影响 Qt 标准控件的默认文本）
            self._QLocale.setDefault(self._QLocale("zh_CN"))

            tr_path = self._QLibraryInfo.path(self._QLibraryInfo.LibraryPath.TranslationsPath)
            if not tr_path:
                return

            # Qt6 主要是 qtbase_zh_CN.qm；兼容性再尝试 qt_zh_CN.qm
            for base in ("qtbase", "qt"):
                tr = self._QTranslator()
                if tr.load(self._QLocale("zh_CN"), base, "_", tr_path):
                    self.app.installTranslator(tr)
                    self._qt_translators.append(tr)
        except Exception as e:
            # 不要影响启动，只写入 bootstrap.log 方便排查
            _write_bootstrap_log(f"Failed to install Qt translations: {e!r}")
        
    def setup_logging(self):
        """设置日志系统"""
        log_dir = app_root / "logs"
        log_dir.mkdir(exist_ok=True, parents=True)
        
        log_file = log_dir / "screen_translator.log"
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file, encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
        
        self.logger = logging.getLogger(__name__)
        self.logger.info("=" * 50)
        self.logger.info("屏幕翻译工具启动")
        self.logger.info(f"配置文件: {self.config_manager.config_file}")
        
    def initialize_components(self):
        """初始化所有组件"""
        try:
            self.logger.info("开始初始化组件...")
            
            # 1. 初始化Tesseract管理器
            self.logger.info("初始化Tesseract管理器...")
            # 优先使用程序运行目录下的 tesseract，如果不存在则使用打包资源中的
            tesseract_root = app_root if (app_root / "tesseract").exists() else resource_root
            from src.utils.tesseract_manager import TesseractManager
            self.tesseract_manager = TesseractManager(str(tesseract_root))

            # 尝试配置。
            if self.tesseract_manager.configure_pytesseract():
                import pytesseract
                self.logger.info(f"Tesseract 配置成功: {pytesseract.pytesseract.tesseract_cmd}")
            else:
                self.logger.warning("未能在程序目录或系统中找到 Tesseract。程序将继续启动，但翻译前需要手动放入 tesseract 文件夹或点击界面上的下载。")
            
            # 2. 初始化OCR处理器
            self.logger.info("初始化OCR处理器...")
            # 从配置中读取 OCR 语言列表（如 eng+jpn+kor 或 eng 等）
            from src.core.ocr import OCRProcessor
            ocr_languages = self.config_manager.get('ocr', 'languages', 'eng+jpn+kor')
            self.ocr_processor = OCRProcessor(ocr_languages)
            
            # 3. 初始化翻译器（使用本地AI模型）
            self.logger.info("初始化翻译器...")
            from src.core.local_translator import LocalAITranslator
            # 确定模型路径：优先使用app_root下的models，否则使用resource_root下的models
            if (app_root / "models").exists():
                model_path = str(app_root / "models")
            elif (resource_root / "models").exists():
                model_path = str(resource_root / "models")
            else:
                model_path = None  # 让LocalAITranslator自己查找
            try:
                self.translator = LocalAITranslator(model_path)
                self.logger.info(f"本地AI翻译器初始化成功，模型路径: {self.translator.model_path}")
            except Exception as e:
                self.logger.error(f"本地AI翻译器初始化失败: {e}")
                raise
            
            # 4. 初始化主窗口
            self.logger.info("初始化主窗口...")
            from src.ui.main_window import MainWindow
            self.main_window = MainWindow(
                config_manager=self.config_manager,
                ocr_processor=self.ocr_processor,
                translator=self.translator,
                tesseract_manager=self.tesseract_manager
            )
            
            # 5. 初始化快捷键管理器（全局快捷键）
            self.logger.info("初始化快捷键管理器...")
            from src.ui.hotkey import HotkeyManager, parse_hotkey_string
            self.hotkey_manager = HotkeyManager()

            # 设置快捷键（使用 hotkey.py 中的解析函数，支持大小写和组合键）
            hotkey = self.config_manager.get('hotkey', 'screenshot', 'b')
            parsed_hotkey = parse_hotkey_string(hotkey)
            self.hotkey_manager.set_hotkey(parsed_hotkey)

            # 将热键触发信号连接到主窗口的截图处理逻辑
            # 在翻译服务开启时，按下热键会开始截图并触发 OCR + 翻译
            if hasattr(self.main_window, "on_hotkey_triggered"):
                self.hotkey_manager.hotkey_triggered.connect(
                    self.main_window.on_hotkey_triggered
                )

            # 将 HotkeyManager 注入主窗口，以便在 UI 中修改快捷键时能动态更新全局监听
            if hasattr(self.main_window, "set_hotkey_manager"):
                self.main_window.set_hotkey_manager(self.hotkey_manager)
            
            self.is_initialized = True
            self.logger.info("所有组件初始化完成")
            
        except Exception as e:
            self.init_error = str(e)
            self.logger.error(f"初始化失败: {e}")
            self.is_initialized = False
            
    def show_error_dialog(self, message):
        """显示错误对话框"""
        from PyQt6.QtWidgets import QMessageBox
        
        msg_box = QMessageBox()
        msg_box.setIcon(QMessageBox.Icon.Critical)
        msg_box.setWindowTitle("初始化错误")
        msg_box.setText("应用程序初始化失败")
        msg_box.setInformativeText(message)
        msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg_box.exec()
        
    def run(self):
        """运行应用程序"""
        from PyQt6.QtWidgets import QMessageBox
        from PyQt6.QtCore import QTimer, Qt
        try:
            # 先显示 UI，再异步加载 OCR / 模型（避免启动卡住）
            self.logger.info("启动主窗口（异步初始化 OCR/模型）...")

            # 1) Tesseract 管理器：轻量，可先创建（实际配置放后台线程）
            try:
                tesseract_root = app_root if (app_root / "tesseract").exists() else resource_root
                from src.utils.tesseract_manager import TesseractManager
                self.tesseract_manager = TesseractManager(str(tesseract_root))
            except Exception as e:
                self.logger.warning(f"TesseractManager 初始化失败（将延后重试）: {e}")
                self.tesseract_manager = None

            # 2) MainWindow：允许 ocr_processor/translator 为 None，占位后再注入
            from src.ui.main_window import MainWindow
            self.main_window = MainWindow(
                config_manager=self.config_manager,
                ocr_processor=None,
                translator=None,
                tesseract_manager=self.tesseract_manager,
            )

            # 显示主窗口（立即）
            self.main_window.show()
            try:
                dt = time.perf_counter() - getattr(self, "_startup_t0", time.perf_counter())
                self.logger.info(f"主窗口已显示（组件加载中，耗时 {dt:.3f}s）")
            except Exception:
                self.logger.info("主窗口已显示（组件加载中）")

            # 尽量确保主窗口可见（某些场景下首次 show 可能在后台/未激活）
            try:
                if hasattr(self.main_window, "bring_to_front"):
                    QTimer.singleShot(0, self.main_window.bring_to_front)
            except Exception:
                pass

            if getattr(sys, "frozen", False) and os.environ.get("SCREEN_TRANSLATOR_ENABLE_SHORTCUT_HELPER", "0") == "1":
                def _maybe_prompt_shortcut():
                    try:
                        from src.utils.installer import Installer
                        installer = Installer()
                        if installer.is_shortcut_hint_skipped():
                            return

                        # 先尽量把主窗口拉到前台，避免弹窗无焦点/被遮挡
                        try:
                            if hasattr(self.main_window, "bring_to_front"):
                                self.main_window.bring_to_front()
                        except Exception:
                            pass

                        # 用显式 QMessageBox 对象，便于设置置顶/模态，避免被安装器遮挡导致“像卡死”
                        box = QMessageBox(self.main_window)
                        box.setIcon(QMessageBox.Icon.Question)
                        box.setWindowTitle("创建快捷方式")
                        box.setText("是否要在桌面和开始菜单创建快捷方式，以便下次快速启动？")
                        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                        box.setDefaultButton(QMessageBox.StandardButton.Yes)
                        try:
                            box.setWindowModality(Qt.WindowModality.ApplicationModal)
                        except Exception:
                            pass
                        try:
                            box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
                        except Exception:
                            pass
                        try:
                            box.show()
                            box.raise_()
                            box.activateWindow()
                        except Exception:
                            pass

                        reply = box.exec()
                        if reply == int(QMessageBox.StandardButton.Yes):
                            installer.create_all_shortcuts()

                        installer.mark_shortcut_hint_skipped()
                    except Exception as e:
                        try:
                            self.logger.warning(f"快捷方式提示失败（已忽略）: {e}")
                        except Exception:
                            pass

                # 让 UI 至少渲染一帧后再弹窗（更稳，也更不容易被安装器抢焦点）
                try:
                    QTimer.singleShot(200, _maybe_prompt_shortcut)
                except Exception:
                    _maybe_prompt_shortcut()

            # 2.5) 初始化快捷键管理器（全局快捷键）——轻量，仍在主线程同步完成
            self.logger.info("初始化快捷键管理器...")
            from src.ui.hotkey import HotkeyManager, parse_hotkey_string
            self.hotkey_manager = HotkeyManager()

            hotkey = self.config_manager.get('hotkey', 'screenshot', 'b')
            parsed_hotkey = parse_hotkey_string(hotkey)
            self.hotkey_manager.set_hotkey(parsed_hotkey)

            if hasattr(self.main_window, "on_hotkey_triggered"):
                self.hotkey_manager.hotkey_triggered.connect(self.main_window.on_hotkey_triggered)
            if hasattr(self.main_window, "set_hotkey_manager"):
                self.main_window.set_hotkey_manager(self.hotkey_manager)

            # 3) 计算模型路径，传给异步初始化线程
            if (app_root / "models").exists():
                model_path = str(app_root / "models")
            elif (resource_root / "models").exists():
                model_path = str(resource_root / "models")
            else:
                model_path = None

            # 4) 让 UI 先渲染一帧，再开始后台初始化（体验更“秒开”）
            try:
                QTimer.singleShot(
                    0,
                    lambda: self.main_window.begin_async_components_init(model_path=model_path),
                )
            except Exception:
                try:
                    self.main_window.begin_async_components_init(model_path=model_path)
                except Exception as e:
                    self.logger.error(f"启动异步初始化失败: {e}")
            try:
                dt = time.perf_counter() - getattr(self, "_startup_t0", time.perf_counter())
                self.logger.info(f"后台初始化已调度（耗时 {dt:.3f}s）")
            except Exception:
                pass
            
            # 启动快捷键监听
            self.hotkey_manager.start()
            hotkey = self.config_manager.get('hotkey', 'screenshot', 'b')
            self.logger.info(f"快捷键监听已启动 (快捷键: {hotkey})")
            
            # 运行应用程序
            self.logger.info("应用程序开始运行")
            exit_code = self.app.exec()
            
            # 清理资源
            self.cleanup()
            
            return exit_code
            
        except Exception as e:
            self.logger.error(f"应用程序运行错误: {e}")
            self.show_error_dialog(str(e))
            return 1
            
    def cleanup(self):
        """清理资源"""
        self.logger.info("正在清理资源...")
        try:
            if getattr(self, "_console_hide_stop", None) is not None:
                self._console_hide_stop.set()
        except Exception:
            pass
        
        # 停止快捷键监听
        if self.hotkey_manager:
            self.hotkey_manager.stop()
            
        # 保存配置
        if self.config_manager:
            self.config_manager.save_config()
            
        self.logger.info("资源清理完成")
        self.logger.info("=" * 50)

def main():
    """主函数"""
    try:
        # 创建应用程序实例
        app = ScreenTranslatorApp()

        # 运行应用程序
        exit_code = app.run()

        # 退出
        sys.exit(exit_code)
    except Exception as e:
        # If we crash before GUI/logging is ready, write bootstrap log to exe folder.
        _write_bootstrap_log("=" * 50)
        _write_bootstrap_log("Fatal error during startup:")
        _write_bootstrap_log(repr(e))
        _write_bootstrap_log("Traceback:")
        _write_bootstrap_log("".join(traceback.format_exc()))
        raise


if __name__ == "__main__":
    main()
