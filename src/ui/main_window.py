"""
主窗口UI - 屏幕翻译工具的管理界面
"""
from __future__ import annotations

import sys
import os
import subprocess
import tempfile
import shutil
import logging
import time
from pathlib import Path
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLayout,
    QLabel, QPushButton, QComboBox, QGroupBox,
    QFrame,
    QCheckBox, QSpinBox, QDoubleSpinBox, QTextEdit,
    QSystemTrayIcon, QMenu, QApplication, QMessageBox,
    QLineEdit,
    QFileDialog,
    QColorDialog,
    QDialog,
    QToolButton,
    QSizePolicy,
    QGraphicsDropShadowEffect,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QListWidget,
    QListWidgetItem,
)
from PyQt6.QtCore import Qt, QRect, QThread, pyqtSignal, QTimer, QBuffer, QIODevice, QUrl, QObject, QEvent, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QIcon, QAction, QActionGroup, QFont, QDesktopServices, QTextCursor, QColor, QScreen, QGuiApplication
import io
import re



from src.utils.tesseract_manager import TesseractManager
from config import ConfigManager
from src.ui.hotkey import parse_hotkey_string
from src.core.languages import (
    ALL_LANGUAGES,
    display_name_for_key,
    key_for_display_name,
    normalize_lang_key,
    normalize_quick_language_keys,
)
from src.ui.language_picker import LanguagePickerDialog
from src.ui.language_manager import LanguageManager
from src.core.hook_client import HookTextThread, hook_log


class _ShadowHoverFilter(QObject):
    def __init__(
        self,
        target: QWidget,
        *,
        base_blur: int,
        hover_blur: int,
        pressed_blur: int,
        offset_y: int,
        color: QColor,
        duration_ms: int,
    ):
        super().__init__(target)
        self._target = target
        self._base_blur = float(base_blur)
        self._hover_blur = float(hover_blur)
        self._pressed_blur = float(pressed_blur)

        eff = QGraphicsDropShadowEffect(target)
        eff.setBlurRadius(self._base_blur)
        eff.setOffset(0, offset_y)
        eff.setColor(color)
        target.setGraphicsEffect(eff)
        self._effect = eff

        anim = QPropertyAnimation(eff, b"blurRadius", self)
        anim.setDuration(int(duration_ms))
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim = anim

        try:
            target.setMouseTracking(True)
        except Exception:
            pass
        target.installEventFilter(self)

    def _to(self, blur: float) -> None:
        try:
            self._anim.stop()
            self._anim.setStartValue(float(self._effect.blurRadius()))
            self._anim.setEndValue(float(blur))
            self._anim.start()
        except Exception:
            pass

    def set_shadow(
        self,
        *,
        base_blur: int,
        hover_blur: int,
        pressed_blur: int,
        offset_y: int,
        color: QColor,
    ) -> None:
        self._base_blur = float(base_blur)
        self._hover_blur = float(hover_blur)
        self._pressed_blur = float(pressed_blur)
        try:
            self._effect.setOffset(0, int(offset_y))
        except Exception:
            pass
        try:
            self._effect.setColor(color)
        except Exception:
            pass
        try:
            if self._target.underMouse():
                self._effect.setBlurRadius(self._hover_blur)
            else:
                self._effect.setBlurRadius(self._base_blur)
        except Exception:
            pass

    def eventFilter(self, watched, event):
        if watched is not self._target:
            return False
        try:
            et = event.type()
        except Exception:
            return False

        if et == QEvent.Type.Enter:
            self._to(self._hover_blur)
        elif et == QEvent.Type.Leave:
            self._to(self._base_blur)
        elif et == QEvent.Type.MouseButtonPress:
            self._to(self._pressed_blur)
        elif et == QEvent.Type.MouseButtonRelease:
            try:
                if self._target.underMouse():
                    self._to(self._hover_blur)
                else:
                    self._to(self._base_blur)
            except Exception:
                self._to(self._base_blur)
        return False


class _UpdateThread(QThread):
    finished = pyqtSignal(bool, str, object)  # ok, message, data(dict|None)

    def __init__(
        self,
        device_id: str,
        current_version: str,
        base_url: str,
        update_path: str,
        download_url: str,
        timeout: float,
        platform: str = "windows",
        app: str = "ScreenTranslator",
    ):
        super().__init__()
        self.device_id = device_id
        self.current_version = current_version
        self.base_url = base_url
        self.update_path = update_path
        self.download_url = download_url
        self.timeout = timeout
        self.platform = platform
        self.app = app

    def run(self):
        try:
            from src.core.auth_client import AuthClient

            client = AuthClient(
                base_url=self.base_url,
                update_path=self.update_path,
                timeout=self.timeout,
            )
            resp = client.check_client_update(
                device_id=self.device_id,
                current_version=self.current_version,
                platform=self.platform,
                app=self.app,
            )
            data = resp.data if isinstance(resp.data, dict) else {}
            # 若服务端未给下载链接，则使用本地配置的默认下载页
            if isinstance(data, dict) and not data.get("download_url") and self.download_url:
                data["download_url"] = self.download_url
            self.finished.emit(bool(resp.ok), str(resp.message or ("成功" if resp.ok else "失败")), data)
        except Exception as e:
            self.finished.emit(False, f"异常: {e}", None)


class _TranslationResult:
    """翻译结果类"""
    def __init__(self, success: bool, translated_text: str = "", error: str = "", original_text: str = ""):
        self.success = success
        self.translated_text = translated_text
        self.error = error
        self.original_text = original_text


class _TranslationThread(QThread):
    """后台翻译线程（避免阻塞 UI）"""
    translation_finished = pyqtSignal(_TranslationResult)  # result
    translation_progress = pyqtSignal(int, str)  # progress (0-100), status message

    def __init__(
        self,
        *,
        text: str,
        source_lang: str,
        target_lang: str,
        disable_preprocess: bool = False,
        translator=None,  # 复用已加载的翻译器实例
        model_path: str = None,  # 仅当translator为None时使用
        glossary_entries: list[tuple[str, str]] | None = None,
    ):
        super().__init__()
        self.text = text
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.disable_preprocess = bool(disable_preprocess)
        self.translator = translator  # 复用翻译器实例
        self.model_path = model_path  # 备用：仅在translator为None时使用
        self.glossary_entries = glossary_entries or []

    def run(self):
        try:
            # 复用已加载的翻译器实例，避免重复加载模型
            if self.translator is None:
                # 仅在translator未提供时才创建新实例（不推荐，会导致重复加载）
                from src.core.local_translator import LocalAITranslator
                self.translator = LocalAITranslator(self.model_path) if self.model_path else LocalAITranslator()

            def _translate_one(s: str) -> str:
                if not s:
                    return ""
                if s.strip() == "":
                    return s
                try:
                    r = self.translator.translate(
                        s,
                        target_lang=self.target_lang,
                        source_lang=self.source_lang,
                        preprocess=(not self.disable_preprocess),
                    )
                except TypeError:
                    r = self.translator.translate(
                        s,
                        target_lang=self.target_lang,
                        source_lang=self.source_lang,
                    )
                return str(getattr(r, "translated_text", "") or "")

            text_in = str(self.text or "")
            translated_text = ""
            if self.glossary_entries and text_in.strip():
                try:
                    self.translation_progress.emit(10, "应用词库…")
                except Exception:
                    pass

                compiled: list[tuple[str, str, object | None, bool]] = []
                for src, dst in list(self.glossary_entries or []):
                    s_src = str(src or "")
                    s_dst = str(dst or "")
                    if not s_src or not s_dst:
                        continue
                    if s_src.isascii():
                        try:
                            if re.fullmatch(r"[A-Za-z0-9_]+", s_src):
                                pat = re.compile(rf"\b{re.escape(s_src)}\b", re.IGNORECASE)
                            else:
                                pat = re.compile(re.escape(s_src), re.IGNORECASE)
                            compiled.append((s_src, s_dst, pat, True))
                        except Exception:
                            compiled.append((s_src, s_dst, None, True))
                    else:
                        compiled.append((s_src, s_dst, None, False))

                lines = []
                try:
                    lines = text_in.split("\n")
                except Exception:
                    lines = [text_in]

                out_lines: list[str] = []
                total = max(1, len(lines))
                for li, line in enumerate(lines):
                    s_line = str(line or "")
                    pos = 0
                    parts: list[tuple[bool, str]] = []
                    while pos < len(s_line):
                        best_start = None
                        best_end = None
                        best_dst = None
                        for src, dst, pat, is_ascii in compiled:
                            if pat is not None:
                                try:
                                    m = pat.search(s_line, pos)
                                except Exception:
                                    m = None
                                if not m:
                                    continue
                                st = int(m.start())
                                ed = int(m.end())
                            else:
                                if is_ascii:
                                    try:
                                        st = int(s_line.lower().find(src.lower(), pos))
                                    except Exception:
                                        st = int(s_line.find(src, pos))
                                else:
                                    st = int(s_line.find(src, pos))
                                if st < 0:
                                    continue
                                ed = st + len(src)
                            if best_start is None or st < best_start or (st == best_start and (ed - st) > (best_end - best_start)):
                                best_start = st
                                best_end = ed
                                best_dst = dst
                        if best_start is None:
                            parts.append((False, s_line[pos:]))
                            break
                        if best_start > pos:
                            parts.append((False, s_line[pos:best_start]))
                        parts.append((True, str(best_dst or "")))
                        pos = int(best_end)

                    seg_out: list[str] = []
                    for is_fixed, seg in parts:
                        if is_fixed:
                            seg_out.append(seg)
                        else:
                            seg_out.append(_translate_one(seg))
                    out_lines.append("".join(seg_out))

                    try:
                        p = 10 + int((li + 1) * 80 / total)
                        self.translation_progress.emit(min(95, max(10, p)), f"翻译中… ({li+1}/{total})")
                    except Exception:
                        pass

                translated_text = "\n".join(out_lines)
            else:
                try:
                    self.translation_progress.emit(20, "翻译中…")
                except Exception:
                    pass
                translated_text = _translate_one(text_in)
            
            if translated_text:
                self.translation_finished.emit(_TranslationResult(
                    success=True,
                    translated_text=translated_text,
                    original_text=self.text
                ))
            else:
                error_msg = "翻译失败"
                self.translation_finished.emit(_TranslationResult(
                    success=False,
                    error=error_msg,
                    original_text=self.text
                ))
                
        except Exception as e:
            self.translation_finished.emit(_TranslationResult(
                success=False,
                error=f"翻译异常: {e}",
                original_text=self.text
            ))


class _DeviceIDThread(QThread):
    """异步设备ID获取线程"""
    device_id_ready = pyqtSignal(str)

    def __init__(self):
        super().__init__()

    def run(self):
        try:
            # 在后台线程中获取设备ID
            from src.utils.hardware_id import get_hardware_id
            device_id = get_hardware_id(fallback="")
            self.device_id_ready.emit(device_id)
        except Exception:
            self.device_id_ready.emit("")


class _ComponentInitThread(QThread):
    """异步组件初始化线程"""
    progress = pyqtSignal(str)  # message
    component_ready = pyqtSignal(str, object, dict)  # name, component, stats
    init_finished = pyqtSignal(bool, dict, dict)  # success, components, stats

    def __init__(self, *, config_manager, tesseract_manager=None, model_path: str | None = None):
        super().__init__()
        self.config_manager = config_manager
        self.tesseract_manager = tesseract_manager
        self.model_path = model_path

    def run(self):
        try:
            components: dict = {}
            stats: dict = {
                "tesseract": {},
                "ocr": {},
                "translator": {},
            }

            # 尽量延迟导入重依赖，避免阻塞 UI 启动
            self.progress.emit("正在检查 Tesseract...")
            tm = self.tesseract_manager
            if tm is None:
                tm = TesseractManager(os.getcwd())
            try:
                ok = bool(tm.configure_pytesseract())
            except Exception:
                ok = False
            stats["tesseract"] = {"available": ok}
            components["tesseract_manager"] = tm
            self.component_ready.emit("tesseract", tm, stats["tesseract"])

            # OCR 初始化（轻量，但 cv2 导入较重，放后台）
            self.progress.emit("正在初始化 OCR...")
            from src.core.ocr import OCRProcessor
            from src.utils.resource_monitor import get_process_stats
            ps_ocr_before = get_process_stats()
            ocr_languages = self.config_manager.get("ocr", "languages", "eng+jpn+kor")
            ocr = OCRProcessor(ocr_languages)
            try:
                ocr.apply_config(self.config_manager)
            except Exception:
                pass
            ps_ocr_after = get_process_stats()
            stats["ocr"] = {
                "rss_delta_bytes": max(0, int(ps_ocr_after.rss_bytes) - int(ps_ocr_before.rss_bytes)),
                "languages": ocr_languages,
            }
            components["ocr_processor"] = ocr
            self.component_ready.emit("ocr", ocr, stats["ocr"])

            # 模型初始化（最重）
            self.progress.emit("正在加载本地翻译模型...")
            from src.core.local_translator import LocalAITranslator
            from src.utils.resource_monitor import get_process_stats, get_gpu_stats

            ps_before = get_process_stats()
            gs_before = get_gpu_stats()
            translator = LocalAITranslator(self.model_path)
            ps_after = get_process_stats()
            gs_after = get_gpu_stats()

            stats["translator"] = {
                "rss_delta_bytes": max(0, int(ps_after.rss_bytes) - int(ps_before.rss_bytes)),
                "gpu_allocated_delta_bytes": (
                    None
                    if (not gs_after.available or gs_before.allocated_bytes is None or gs_after.allocated_bytes is None)
                    else max(0, int(gs_after.allocated_bytes) - int(gs_before.allocated_bytes))
                ),
                "gpu_reserved_delta_bytes": (
                    None
                    if (not gs_after.available or gs_before.reserved_bytes is None or gs_after.reserved_bytes is None)
                    else max(0, int(gs_after.reserved_bytes) - int(gs_before.reserved_bytes))
                ),
                "device": getattr(translator, "device", None),
            }
            components["translator"] = translator
            self.component_ready.emit("translator", translator, stats["translator"])

            self.init_finished.emit(True, components, stats)
        except Exception as e:
            self.init_finished.emit(False, {}, {"error": str(e)})


class MainWindow(QMainWindow):
    """主窗口类，提供屏幕翻译工具的管理界面"""
    
    SHOW_MORE_TEXT = "显示更多…"

    def __init__(self, config_manager, ocr_processor=None, translator=None, tesseract_manager=None):
        super().__init__()

        # 日志（用于排查“跨屏缩放是否生效”等问题）
        self.logger = logging.getLogger(__name__)

        # 样式表/尺寸：统一按 scale_factor 自动缩放（把写死的 10px/12px 等一并缩放）
        # - _scaled_stylesheets: [(widget, base_css)] 记录控件的“基准 CSS”（不带缩放）
        #   注意：不用 dict[widget]，避免某些 PyQt 绑定下对象不可 hash 导致注册失败。
        self._scaled_stylesheets: list[tuple[object, str]] = []
        
        # 存储传入的组件
        self.config_manager = config_manager
        self.ocr_processor = ocr_processor
        self.translator = translator
        self.tesseract_manager = tesseract_manager
        self.hotkey_manager = None  # 由外部注入

        # 启动期状态
        self._async_init_thread = None
        self._init_progress_text = "未开始"
        self._component_stats = {"tesseract": {}, "ocr": {}, "translator": {}}
        self._model_path_for_init: str | None = None
        
        # 获取配置
        self.config = {
            'source_language': self.config_manager.get('translation', 'source_language', 'en'),
            'target_language': self.config_manager.get('translation', 'target_language', 'zh-CN'),
            'quick_languages_source': self.config_manager.get('translation', 'quick_languages_source', 'en,zh-CN,ja,ko'),
            'quick_languages_target': self.config_manager.get('translation', 'quick_languages_target', 'en,zh-CN,ja,ko'),
            'quick_languages': self.config_manager.get('translation', 'quick_languages', 'en,zh-CN,ja,ko'),
            'hotkey': self.config_manager.get('hotkey', 'screenshot', 'b'),
            'overlay_opacity': self.config_manager.get_float('overlay', 'opacity', 0.9),
            'overlay_timeout': self.config_manager.get_int('overlay', 'timeout', 10),
            'overlay_auto_hide': self.config_manager.get_bool('overlay', 'auto_hide', True),
            'keep_capture_region': self.config_manager.get_bool('screenshot', 'keep_capture_region', False),
            # 字芯颜色（用于复杂背景模式）
            'ocr_core_color': self.config_manager.get('ocr', 'core_color', '#FFFFFF'),
            # 新：颜色对话框“自定义颜色”槽位（最多 16 个，逗号分隔 #RRGGBB）
            'ocr_custom_colors': self.config_manager.get('ocr', 'custom_colors', ''),
            # OCR 识别模式：内部开关（复杂背景模式=开启；识别文本模式=关闭）
            'ocr_preprocess_enabled': self.config_manager.get_bool('ocr_preprocess', 'enabled', True),
            'hook_enabled': self.config_manager.get_bool('hook', 'enabled', False),
            'hook_port': self.config_manager.get_int('hook', 'port', 37123),
            'hook_target_process': self.config_manager.get('hook', 'target_process', ''),
            'hook_auto_start': self.config_manager.get_bool('hook', 'auto_start', False),
            'hook_prefer_frida_only': self.config_manager.get_bool('hook', 'prefer_frida_only', True),
        }

        # 启动时规范化翻译语言配置（兼容旧值：ZH/EN/中文等），并确保目标语言默认中文（简体）
        try:
            valid_keys = {l.key for l in ALL_LANGUAGES}
            raw_src = str(self.config_manager.get("translation", "source_language", "en") or "en")
            raw_tgt = str(self.config_manager.get("translation", "target_language", "zh-CN") or "zh-CN")
            src_key = normalize_lang_key(raw_src)
            tgt_key = normalize_lang_key(raw_tgt)
            if src_key not in valid_keys:
                src_key = "en"
            if tgt_key not in valid_keys:
                tgt_key = "zh-CN"

            raw_quick = str(self.config_manager.get("translation", "quick_languages", "en,zh-CN,ja,ko") or "")
            quick_keys = normalize_quick_language_keys([x.strip() for x in raw_quick.split(",") if x.strip()])

            # 写回规范化值（避免“重启后显示不一致”）
            if raw_src != src_key:
                self.config_manager.set("translation", "source_language", src_key)
            if raw_tgt != tgt_key:
                self.config_manager.set("translation", "target_language", tgt_key)
            normalized_quick = ",".join(quick_keys)
            if raw_quick != normalized_quick:
                self.config_manager.set("translation", "quick_languages", normalized_quick)

            # 同步到内存配置
            self.config["source_language"] = src_key
            self.config["target_language"] = tgt_key
            self.config["quick_languages"] = normalized_quick
        except Exception:
            pass
        
        self.screenshot_tool = None
        self.overlay = None
        self._eyedropper = None
        self._locked_capture_rect: QRect | None = None
        self._locked_region_frame = None
        
        # 状态变量
        self.is_translating = False
        self.last_translation = ""
        self._translation_glossary_maps: dict[int, list[tuple[str, str]]] = {}

        # 初始化语言管理器
        self.language_manager = LanguageManager(self.config_manager)

        # 计算屏幕缩放因子（基于主屏幕的DPI）
        self.scale_factor = self._calculate_scale_factor()
        # 多显示器/不同缩放：记录当前屏幕并在跨屏时自动刷新 UI 缩放
        self._last_screen_name = ""
        self._screen_tracking_installed = False
        self._scale_apply_debounce = QTimer(self)
        self._scale_apply_debounce.setSingleShot(True)
        self._scale_apply_debounce.timeout.connect(self._update_scale_factor_for_current_screen)
        # 启动期：确保“每次启动都按当前屏幕计算并应用一次”
        self._startup_scale_applied = False
        # 用于后续动态调整的引用
        self._main_layout = None
        self._title_label = None
        self._open_animation_played = False
        self._open_animation = None
        self._ui_effect_refs: list[object] = []
        self._main_page_card_targets: list[tuple[QWidget, int, int, int]] = []
        self._main_page_hover_filters: list[tuple[_ShadowHoverFilter, int, int, int, int, int]] = []

        # 翻译后台线程（避免 UI 卡顿 & 允许新请求覆盖旧请求）
        self._translation_thread = None
        self._translation_request_seq = 0
        self._active_translation_request_seq = 0

        self._hook_running = False
        self._hook_scan_thread = None
        self._last_hook_text = ""
        self._hook_any_text_received = False
        self._hook_log_current_path = ""
        self._hook_arch_switch_prompted = False
        self._hook_agent_process = None
        
        # 初始化UI
        self.init_ui()

        # 版本检查相关
        self.device_id = ""
        self._update_thread = None
        self._device_id_thread = None
        self._init_device_id()

        # 强制更新锁定（发现新客户端后会禁用所有功能）
        self._force_update_active = False
        self._force_update_reason = ""
        self._force_update_download_url = ""

        # 启动后检查版本更新：等 device_id 异步就绪后再触发
        
        # 设置系统托盘
        self.setup_system_tray()
        
        # 设置窗口属性
        self.setWindowTitle("14ku屏幕翻译工具")
        # 应用缩放因子到最小尺寸
        min_width = int(512 * self.scale_factor)
        min_height = int(700 * self.scale_factor)
        self.setMinimumSize(min_width, min_height)

        # 初始化资源监控（不阻塞 UI）
        # 注意：GPU 资源监控会触发 import torch（非常慢），绝不能在主窗口创建/首帧之前执行。
        # 做法：
        # - 先只显示 UI
        # - 延迟启动资源监控（CPU/内存）
        # - 仅当翻译器使用 CUDA 时才启用 GPU 监控（避免无意义导入 torch）
        self._gpu_stats_enabled = False
        self._resource_timer = QTimer(self)
        self._resource_timer.timeout.connect(self._refresh_system_status)
        try:
            QTimer.singleShot(1500, self._start_resource_monitoring)
        except Exception:
            # 兜底：至少刷新一次（不取 GPU）
            self._refresh_system_status()

    def _start_resource_monitoring(self) -> None:
        """延迟启动资源监控，确保主窗口已渲染一帧后再执行。"""
        try:
            rm = getattr(self, "_resource_monitor", None)
            if rm is None:
                from src.utils import resource_monitor as rm
                self._resource_monitor = rm
        except Exception:
            rm = None
        try:
            if rm is not None:
                rm.init_process_cpu_sampler()
        except Exception:
            pass
        try:
            if self._resource_timer is not None and not self._resource_timer.isActive():
                self._resource_timer.start(1000)
        except Exception:
            pass
        self._refresh_system_status()
        
    def _calculate_scale_factor(self) -> float:
        """计算屏幕缩放因子，基于主屏幕的DPI"""
        try:
            screen = QApplication.primaryScreen()
            return self._calculate_scale_factor_for_screen(screen)
        except Exception:
            pass
        return 1.0

    def _calculate_scale_factor_for_screen(self, screen: QScreen | None) -> float:
        """根据指定屏幕计算缩放因子（多显示器下使用）

        说明：
        - Windows 多显示器常见问题是 logical DPI 读不到/都为 96；此时仅靠 DPI 会导致缩放恒为 1。
        - 因此这里同时参考屏幕“可用分辨率比例”（以 1920x1080 为基准），确保不同屏幕上有可见的自适配效果。
        """
        try:
            if not screen:
                return 1.0
            dpi_scale = 1.0
            try:
                dpi = float(screen.logicalDotsPerInch())
                if dpi > 0:
                    dpi_scale = dpi / 96.0
            except Exception:
                dpi_scale = 1.0

            # 分辨率比例（按可用区域，避免任务栏等影响）
            geom_scale = 1.0
            try:
                g = screen.availableGeometry()
                base_w, base_h = 1920.0, 1080.0
                if g.width() > 0 and g.height() > 0:
                    geom_scale = min(float(g.width()) / base_w, float(g.height()) / base_h)
            except Exception:
                geom_scale = 1.0

            # 回退：logical DPI 在某些环境下可能不稳定
            if not (0.1 <= dpi_scale <= 10.0):
                try:
                    dpi_scale = float(screen.devicePixelRatio())
                except Exception:
                    dpi_scale = 1.0

            # 最终缩放：取两者较大者（保证不同屏幕“看得见”变化），并做合理范围限制
            scale = max(float(dpi_scale), float(geom_scale))

            # 限制缩放范围在 0.8 到 2.0 之间，避免 UI 过小/过大
            return max(0.8, min(2.0, scale))
        except Exception:
            return 1.0

    def _scale_stylesheet_px(self, css: str) -> str:
        """
        将 CSS 中的 Npx 按当前 scale_factor 缩放。
        例：font-size: 12px; -> font-size: 15px;（当 scale_factor=1.25）
        """
        if not css:
            return css
        try:
            def _repl(m: re.Match) -> str:
                try:
                    v = float(m.group(1))
                except Exception:
                    return m.group(0)
                if v == 0:
                    return "0px"
                scaled = int(round(v * float(self.scale_factor)))
                # 避免非零值被 round 到 0
                if scaled == 0:
                    scaled = 1 if v > 0 else -1
                return f"{scaled}px"

            return re.sub(r"(-?\d+(?:\.\d+)?)px", _repl, css)
        except Exception:
            return css

    def _set_scaled_stylesheet(self, widget, base_css: str) -> None:
        """为控件设置可随 scale_factor 自动刷新的样式表。"""
        try:
            if widget is None:
                return
            css = str(base_css or "")
            # 更新注册表（按 identity 找到旧条目）
            updated = False
            for i, (w, _) in enumerate(self._scaled_stylesheets):
                if w is widget:
                    self._scaled_stylesheets[i] = (widget, css)
                    updated = True
                    break
            if not updated:
                self._scaled_stylesheets.append((widget, css))
            widget.setStyleSheet(self._scale_stylesheet_px(css))
        except Exception:
            pass

    def _get_translate_button_base_css(self, *, active: bool) -> str:
        """启动/停止按钮的“基准样式”（所有 px 会由 _scale_stylesheet_px 统一缩放）。"""
        if active:
            return """
                QPushButton {
                    background-color: #f44336;
                    color: white;
                    font-weight: bold;
                    padding: 10px;
                    border-radius: 5px;
                }
                QPushButton:hover {
                    background-color: #d32f2f;
                }
                QPushButton:pressed {
                    background-color: #b71c1c;
                }
            """
        return """
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                padding: 10px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:pressed {
                background-color: #3d8b40;
            }
        """

    def _get_current_window_screen(self) -> QScreen | None:
        """获取当前窗口所在屏幕"""
        try:
            handle = self.windowHandle()
            if handle and handle.screen():
                return handle.screen()
        except Exception:
            pass

        # 回退：根据窗口中心点定位屏幕（对跨屏拖动也有效）
        try:
            center = self.frameGeometry().center()
            scr = QGuiApplication.screenAt(center)
            if scr:
                return scr
        except Exception:
            pass

        try:
            return QApplication.primaryScreen()
        except Exception:
            return None

    def _apply_scale_factor_to_ui(self) -> None:
        """把 scale_factor 应用到会用到缩放的 UI 属性上（无需重建整个 UI）"""
        # 最小尺寸
        try:
            self.setMinimumSize(int(512 * self.scale_factor), int(700 * self.scale_factor))
        except Exception:
            pass
        try:
            self._rescale_main_page_effects()
        except Exception:
            pass
        try:
            if hasattr(self, "_hero_menu_button") and self._hero_menu_button is not None:
                s = self._scale_size(36)
                self._hero_menu_button.setFixedSize(s, s)
                f = self._hero_menu_button.font()
                f.setPointSize(self._scale_font_size(14))
                self._hero_menu_button.setFont(f)
            if hasattr(self, "_hero_menu_placeholder") and self._hero_menu_placeholder is not None:
                self._hero_menu_placeholder.setFixedWidth(self._scale_size(36))
        except Exception:
            pass

    def _rescale_all_layouts_by_ratio(self, ratio: float) -> None:
        """按比例缩放所有子布局的 spacing / margins（用于跨屏缩放变化）。"""
        try:
            if not ratio or ratio == 1.0:
                return
            # 避免主布局被“比例缩放”后又被 _apply_scale_factor_to_ui 覆盖/叠加
            skip = self._main_layout
            for lay in self.findChildren(QLayout):
                try:
                    if skip is not None and lay is skip:
                        continue
                    s = lay.spacing()
                    if s is not None and int(s) >= 0:
                        lay.setSpacing(max(0, int(round(float(s) * ratio))))
                    l, t, r, b = lay.getContentsMargins()
                    lay.setContentsMargins(
                        max(0, int(round(float(l) * ratio))),
                        max(0, int(round(float(t) * ratio))),
                        max(0, int(round(float(r) * ratio))),
                        max(0, int(round(float(b) * ratio))),
                    )
                except Exception:
                    pass
        except Exception:
            pass

    def _apply_startup_window_size_for_screen(self, screen: QScreen | None) -> None:
        """启动时按当前屏幕设置一个“合理的初始大小”（每次启动都会计算）。"""
        try:
            if not screen:
                return
            g = screen.availableGeometry()
            if g.width() <= 0 or g.height() <= 0:
                return

            # 目标：不占满屏，但也别太小；并跟随 scale_factor
            target_w = int(round(596 * float(self.scale_factor)))
            target_h = int(round(760 * float(self.scale_factor))) + int(round(136 * float(self.scale_factor))) - int(round(350 * float(self.scale_factor)))

            # 约束在屏幕可用区域的 90% 内
            max_w = int(round(g.width() * 0.9))
            max_h = int(round(g.height() * 0.9))
            target_w = min(target_w, max_w)
            target_h = min(target_h, max_h)

            # 同时不小于最小尺寸（最小尺寸本身已随 scale_factor 更新）
            target_w = max(target_w, self.minimumWidth())
            target_h = max(target_h, self.minimumHeight())

            self.resize(target_w, target_h)
        except Exception:
            pass

        # 主布局间距/边距
        try:
            if self._main_layout is not None:
                self._main_layout.setSpacing(self._scale_size(15))
                self._main_layout.setContentsMargins(
                    self._scale_size(20),
                    self._scale_size(20),
                    self._scale_size(20),
                    self._scale_size(20),
                )
        except Exception:
            pass

        # 标题字体
        try:
            if self._title_label is not None:
                f = self._title_label.font()
                f.setPointSize(self._scale_font_size(16))
                f.setBold(True)
                self._title_label.setFont(f)
        except Exception:
            pass

        # 语言下拉框宽度
        try:
            if hasattr(self, "source_lang_combo") and self.source_lang_combo is not None:
                self.source_lang_combo.setMinimumWidth(self._scale_size(200))
            if hasattr(self, "target_lang_combo") and self.target_lang_combo is not None:
                self.target_lang_combo.setMinimumWidth(self._scale_size(200))
        except Exception:
            pass

        # 字芯颜色预览尺寸
        try:
            if hasattr(self, "ocr_core_color_preview") and self.ocr_core_color_preview is not None:
                self.ocr_core_color_preview.setFixedSize(self._scale_size(36), self._scale_size(18))
        except Exception:
            pass

        # 刷新登记过的样式表（把写死的 px 全部按当前 scale_factor 重算）
        try:
            for w, base_css in list(self._scaled_stylesheets):
                try:
                    w.setStyleSheet(self._scale_stylesheet_px(base_css))
                except Exception:
                    pass
        except Exception:
            pass

    def _update_scale_factor_for_current_screen(self, force: bool = False) -> None:
        """按当前窗口所在屏幕重算并应用缩放（多屏自动适配）"""
        try:
            screen = self._get_current_window_screen()
            new_scale = float(self._calculate_scale_factor_for_screen(screen))
            screen_name = ""
            try:
                if screen:
                    screen_name = str(screen.name() or "")
            except Exception:
                screen_name = ""

            if not force:
                if screen_name and screen_name == self._last_screen_name and abs(new_scale - float(self.scale_factor)) < 0.02:
                    return

            old_scale = float(self.scale_factor) if self.scale_factor else 1.0
            self.scale_factor = new_scale
            self._last_screen_name = screen_name
            # 先按比例缩放“所有子布局”，再做针对关键控件的绝对缩放刷新
            try:
                ratio = (float(new_scale) / float(old_scale)) if old_scale else 1.0
                if abs(ratio - 1.0) >= 0.02:
                    self._rescale_all_layouts_by_ratio(ratio)
            except Exception:
                pass
            self._apply_scale_factor_to_ui()
            try:
                self.logger.info(f"UI scale updated: screen={screen_name or 'unknown'} scale={self.scale_factor:.2f}")
            except Exception:
                pass
        except Exception:
            pass

    def _ensure_screen_tracking(self) -> None:
        """安装屏幕变化监听（只做一次）"""
        if self._screen_tracking_installed:
            return
        self._screen_tracking_installed = True

        # 跨屏时最准确：windowHandle().screenChanged
        try:
            handle = self.windowHandle()
            if handle is not None:
                handle.screenChanged.connect(lambda _s: self._update_scale_factor_for_current_screen(force=True))
        except Exception:
            pass
    
    def _scale_size(self, size: int) -> int:
        """根据缩放因子调整尺寸"""
        return int(size * self.scale_factor)
    
    def _scale_font_size(self, size: int) -> int:
        """根据缩放因子调整字体大小"""
        return int(size * self.scale_factor)
        
    def init_ui(self):
        """初始化用户界面"""
        # 创建中央部件
        central_widget = QWidget()
        central_widget.setObjectName("mainRoot")
        self.setCentralWidget(central_widget)
        self._apply_main_page_theme(central_widget)
        
        # 主布局
        main_layout = QVBoxLayout(central_widget)
        self._main_layout = main_layout
        main_layout.setSpacing(self._scale_size(15))
        main_layout.setContentsMargins(self._scale_size(20), self._scale_size(20), self._scale_size(20), self._scale_size(20))
        
        # 1. 标题区域
        header = QFrame()
        header.setObjectName("heroHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(self._scale_size(16), self._scale_size(14), self._scale_size(16), self._scale_size(14))
        header_layout.setSpacing(self._scale_size(8))

        self._hero_menu_button = QToolButton()
        self._hero_menu_button.setText("≡")
        self._hero_menu_button.setToolTip("菜单")
        self._hero_menu_button.setFixedSize(self._scale_size(36), self._scale_size(36))
        menu_font = self._hero_menu_button.font()
        menu_font.setPointSize(self._scale_font_size(14))
        self._hero_menu_button.setFont(menu_font)
        self._set_scaled_stylesheet(
            self._hero_menu_button,
            """
                QToolButton {
                    background-color: transparent;
                    border: 0px;
                    border-radius: 12px;
                    color: rgba(17, 24, 39, 0.72);
                }
                QToolButton:hover {
                    background-color: rgba(17, 24, 39, 0.06);
                    color: rgba(17, 24, 39, 0.86);
                }
                QToolButton:pressed {
                    background-color: rgba(59, 130, 246, 0.12);
                    color: rgba(17, 24, 39, 0.92);
                }
                QToolButton::menu-indicator {
                    image: none;
                }
            """,
        )

        hero_menu = QMenu(self._hero_menu_button)
        self._hero_menu_actions = {}
        self._hero_menu_view_group = QActionGroup(self)
        self._hero_menu_view_group.setExclusive(True)

        main_action = QAction("主界面", self)
        main_action.setCheckable(True)
        main_action.triggered.connect(self.show_main_view)
        self._hero_menu_view_group.addAction(main_action)
        hero_menu.addAction(main_action)
        self._hero_menu_actions["main"] = main_action

        status_action = QAction("系统状态", self)
        status_action.setCheckable(True)
        status_action.triggered.connect(self.show_system_status_dialog)
        self._hero_menu_view_group.addAction(status_action)
        hero_menu.addAction(status_action)
        self._hero_menu_actions["system_status"] = status_action

        history_action = QAction("历史记录", self)
        history_action.setCheckable(True)
        history_action.triggered.connect(self.show_history_dialog)
        self._hero_menu_view_group.addAction(history_action)
        hero_menu.addAction(history_action)
        self._hero_menu_actions["history"] = history_action

        glossary_action = QAction("翻译词库", self)
        glossary_action.setCheckable(True)
        glossary_action.triggered.connect(self.show_glossary_view)
        self._hero_menu_view_group.addAction(glossary_action)
        hero_menu.addAction(glossary_action)
        self._hero_menu_actions["glossary"] = glossary_action

        try:
            main_action.setChecked(True)
        except Exception:
            pass
        self._hero_menu_button.setMenu(hero_menu)
        self._hero_menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        header_layout.addWidget(self._hero_menu_button, 0, Qt.AlignmentFlag.AlignTop)

        header_center = QWidget()
        header_center_layout = QVBoxLayout(header_center)
        header_center_layout.setContentsMargins(0, 0, 0, 0)
        header_center_layout.setSpacing(self._scale_size(6))

        title_label = QLabel("14ku屏幕翻译工具")
        self._title_label = title_label
        title_label.setObjectName("heroTitle")
        title_font = QFont()
        title_font.setPointSize(self._scale_font_size(16))
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        subtitle_label = QLabel("官网14ku.date")
        subtitle_label.setObjectName("heroSubtitle")
        subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        header_center_layout.addWidget(title_label)
        header_center_layout.addWidget(subtitle_label)
        header_layout.addWidget(header_center, 1)

        self._hero_menu_placeholder = QWidget()
        self._hero_menu_placeholder.setFixedWidth(self._scale_size(36))
        header_layout.addWidget(self._hero_menu_placeholder, 0)
        main_layout.addWidget(header)

        self._view_main = QWidget()
        self._view_main_layout = QVBoxLayout(self._view_main)
        self._view_main_layout.setContentsMargins(0, 0, 0, 0)
        self._view_main_layout.setSpacing(self._scale_size(15))
        main_layout.addWidget(self._view_main, 1)

        self._view_status = QWidget()
        self._view_status_layout = QVBoxLayout(self._view_status)
        self._view_status_layout.setContentsMargins(0, 0, 0, 0)
        self._view_status_layout.setSpacing(self._scale_size(15))
        main_layout.addWidget(self._view_status, 1)

        self._view_history = QWidget()
        self._view_history_layout = QVBoxLayout(self._view_history)
        self._view_history_layout.setContentsMargins(0, 0, 0, 0)
        self._view_history_layout.setSpacing(self._scale_size(15))
        main_layout.addWidget(self._view_history, 1)

        self._view_hook = QWidget()
        self._view_hook_layout = QVBoxLayout(self._view_hook)
        self._view_hook_layout.setContentsMargins(0, 0, 0, 0)
        self._view_hook_layout.setSpacing(self._scale_size(15))
        main_layout.addWidget(self._view_hook, 1)

        self._view_glossary = QWidget()
        self._view_glossary_layout = QVBoxLayout(self._view_glossary)
        self._view_glossary_layout.setContentsMargins(0, 0, 0, 0)
        self._view_glossary_layout.setSpacing(self._scale_size(15))
        main_layout.addWidget(self._view_glossary, 1)
        
        # 2. 状态区域（启动期展示：组件状态 + 资源占用）
        status_group = QGroupBox("系统状态")
        self._system_status_group = status_group
        status_layout = QVBoxLayout()

        self.translation_status_label = QLabel("翻译服务: 未启用")
        status_layout.addWidget(self.translation_status_label)

        self.tesseract_status_label = QLabel("Tesseract: 初始化中…")
        status_layout.addWidget(self.tesseract_status_label)
        self.ocr_status_label = QLabel("OCR: 初始化中…")
        status_layout.addWidget(self.ocr_status_label)
        self.model_status_label = QLabel("模型: 初始化中…")
        status_layout.addWidget(self.model_status_label)

        self.model_resource_label = QLabel("模型资源: -")
        status_layout.addWidget(self.model_resource_label)
        self.ocr_resource_label = QLabel("OCR资源: -")
        status_layout.addWidget(self.ocr_resource_label)

        self.process_resource_label = QLabel("进程资源: -")
        status_layout.addWidget(self.process_resource_label)

        status_group.setLayout(status_layout)
        self._view_status_layout.addWidget(status_group)
        
        # 3. 控制区域
        control_group = QGroupBox("翻译控制")
        control_layout = QVBoxLayout()
        
        # 启动/停止按钮（快捷键信息稍后根据配置更新）
        self.translate_button = QPushButton()
        self._set_scaled_stylesheet(self.translate_button, self._get_translate_button_base_css(active=False))
        self.translate_button.clicked.connect(self.toggle_translation)
        control_layout.addWidget(self.translate_button)

        # 输入模式按钮（手动输入翻译，不走 OCR）
        self.text_mode_button = QPushButton("输入模式（手动输入）")
        self.text_mode_button.clicked.connect(self.open_text_mode)
        control_layout.addWidget(self.text_mode_button)

        self.hook_mode_button = QPushButton()
        try:
            self.hook_mode_button.setObjectName("hookModeButton")
        except Exception:
            pass
        self.hook_mode_button.clicked.connect(self.show_hook_view)
        control_layout.addWidget(self.hook_mode_button)
        
        # 测试按钮
        self.test_button = QPushButton("测试截图和翻译")
        self.test_button.clicked.connect(self.test_translation)
        control_layout.addWidget(self.test_button)
        
        control_group.setLayout(control_layout)
        self._view_main_layout.addWidget(control_group)

        # 启动期默认禁用（组件加载完成后再启用）
        if not self._components_ready_for_work():
            self.translate_button.setEnabled(False)
            self.text_mode_button.setEnabled(False)
            self.hook_mode_button.setEnabled(False)
            self.test_button.setEnabled(False)
        
        # 4. 设置区域
        settings_group = QGroupBox("设置")
        settings_layout = QVBoxLayout()
        
        # 语言设置
        lang_layout = QHBoxLayout()
        lang_layout.addWidget(QLabel("源语言:"))
        
        self.source_lang_combo = QComboBox()
        self.source_lang_combo.setMinimumWidth(self._scale_size(200))  # 设置下拉框宽度为200像素
        self.source_lang_combo.currentIndexChanged.connect(self._on_source_lang_combo_changed)
        lang_layout.addWidget(self.source_lang_combo)
        
        lang_layout.addWidget(QLabel("目标语言:"))
        
        self.target_lang_combo = QComboBox()
        self.target_lang_combo.setMinimumWidth(self._scale_size(200))  # 设置下拉框宽度为200像素
        self.target_lang_combo.currentIndexChanged.connect(self._on_target_lang_combo_changed)
        lang_layout.addWidget(self.target_lang_combo)
        
        settings_layout.addLayout(lang_layout)

        # 构建语言下拉框（主界面仍只显示 4 个快捷语言槽位 + "显示更多…"）
        self._rebuild_language_combos(apply_config_selection=True)
        
        # 快捷键设置
        hotkey_layout = QHBoxLayout()
        hotkey_layout.addWidget(QLabel("截图快捷键:"))
        
        self.hotkey_edit = QLineEdit()
        self.hotkey_edit.setPlaceholderText("例如: b")
        self.hotkey_edit.setText(self.config['hotkey'])
        self.hotkey_edit.editingFinished.connect(self.save_hotkey_setting)
        hotkey_layout.addWidget(self.hotkey_edit)
        
        settings_layout.addLayout(hotkey_layout)

        self.keep_capture_region_check = QCheckBox("保留框选区域（框选一次，快捷键重复翻译）")
        self.keep_capture_region_check.setChecked(bool(self.config.get("keep_capture_region", False)))
        self.keep_capture_region_check.stateChanged.connect(self.save_keep_capture_region_setting)
        settings_layout.addWidget(self.keep_capture_region_check)
        
        # OCR设置
        ocr_layout = QVBoxLayout()

        # OCR 识别模式（两档）：识别文本模式 / 复杂背景模式
        ocr_mode_layout = QHBoxLayout()
        ocr_mode_layout.addWidget(QLabel("OCR识别模式:"))

        self.ocr_mode_combo = QComboBox()
        self.ocr_mode_combo.addItems([
            "识别文本模式",
            "复杂背景模式",
        ])
        self.ocr_mode_combo.setToolTip("识别文本模式：适合干净的文字区域；复杂背景模式：适合背景复杂/有渐变/有噪声的场景。")
        # enabled=True -> 复杂背景模式；enabled=False -> 识别文本模式
        try:
            enabled = bool(self.config.get("ocr_preprocess_enabled", True))
        except Exception:
            enabled = True
        # 正确的映射关系：
        # 识别文本模式 (index=0) -> ocr_preprocess_enabled=False
        # 复杂背景模式 (index=1) -> ocr_preprocess_enabled=True
        self.ocr_mode_combo.setCurrentIndex(1 if enabled else 0)
        self.ocr_mode_combo.currentIndexChanged.connect(self.save_ocr_settings)
        ocr_mode_layout.addWidget(self.ocr_mode_combo)

        ocr_layout.addLayout(ocr_mode_layout)
        
        # 字芯颜色（仅复杂背景模式需要）
        # 用容器包起来，便于“识别文本模式”下直接隐藏整块 UI
        self.ocr_core_color_group = QFrame()
        core_color_layout = QHBoxLayout(self.ocr_core_color_group)
        core_color_layout.setContentsMargins(0, 0, 0, 0)
        self.ocr_core_color_label = QLabel("字芯颜色:")
        core_color_layout.addWidget(self.ocr_core_color_label)

        self.ocr_core_color_edit = QLineEdit()
        self.ocr_core_color_edit.setPlaceholderText("#RRGGBB 例如: #FFFFFF")
        self.ocr_core_color_edit.setText(self.config.get('ocr_core_color', '#FFFFFF'))
        self.ocr_core_color_edit.editingFinished.connect(self.save_ocr_settings)
        core_color_layout.addWidget(self.ocr_core_color_edit)

        self.ocr_core_color_preview = QLabel()
        self.ocr_core_color_preview.setFixedSize(self._scale_size(36), self._scale_size(18))
        self.ocr_core_color_preview.setToolTip("字芯颜色预览")
        core_color_layout.addWidget(self.ocr_core_color_preview)

        self.ocr_core_color_pick_btn = QPushButton("选择字芯颜色…")
        self.ocr_core_color_pick_btn.setToolTip("打开颜色选择器，设置字芯颜色")
        self.ocr_core_color_pick_btn.clicked.connect(self.choose_ocr_core_color)
        core_color_layout.addWidget(self.ocr_core_color_pick_btn)

        self.ocr_core_color_dropper_btn = QPushButton("吸管")
        self.ocr_core_color_dropper_btn.setToolTip("从屏幕上取色（点击取色，ESC/右键取消）")
        self.ocr_core_color_dropper_btn.clicked.connect(self.pick_ocr_core_color_with_eyedropper)
        core_color_layout.addWidget(self.ocr_core_color_dropper_btn)

        ocr_layout.addWidget(self.ocr_core_color_group)
        self._update_ocr_core_color_preview(self.ocr_core_color_edit.text())
        try:
            self._apply_ocr_mode_ui_state()
        except Exception:
            pass
        
        settings_layout.addLayout(ocr_layout)
        
        # 悬浮窗设置
        overlay_layout = QVBoxLayout()
        
        opacity_layout = QHBoxLayout()
        opacity_layout.addWidget(QLabel("悬浮窗透明度:"))
        
        self.opacity_spin = QDoubleSpinBox()
        self.opacity_spin.setRange(0.1, 1.0)
        self.opacity_spin.setSingleStep(0.1)
        self.opacity_spin.setValue(self.config['overlay_opacity'])
        self.opacity_spin.valueChanged.connect(self.save_overlay_settings)
        opacity_layout.addWidget(self.opacity_spin)
        
        overlay_layout.addLayout(opacity_layout)
        
        timeout_layout = QHBoxLayout()
        timeout_layout.addWidget(QLabel("显示时间(秒):"))
        
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 60)
        self.timeout_spin.setValue(self.config['overlay_timeout'])
        self.timeout_spin.valueChanged.connect(self.save_overlay_settings)
        timeout_layout.addWidget(self.timeout_spin)
        
        overlay_layout.addLayout(timeout_layout)
        
        self.auto_hide_check = QCheckBox("自动隐藏悬浮窗")
        self.auto_hide_check.setChecked(self.config['overlay_auto_hide'])
        self.auto_hide_check.stateChanged.connect(self.save_overlay_settings)
        overlay_layout.addWidget(self.auto_hide_check)

        # 说明：字芯颜色用于复杂背景模式下的识别增强
        
        settings_layout.addLayout(overlay_layout)
        
        settings_group.setLayout(settings_layout)
        self._view_main_layout.addWidget(settings_group)

        self._view_main_layout.addStretch()
        
        # 6. 底部按钮
        button_layout = QHBoxLayout()
        
        self.save_button = QPushButton("保存设置")
        self.save_button.clicked.connect(self.save_all_settings)
        button_layout.addWidget(self.save_button)
        
        self.about_button = QPushButton("如何操作")
        self.about_button.clicked.connect(self.show_how_to)
        button_layout.addWidget(self.about_button)

        self.installer = None
        if getattr(sys, "frozen", False) and os.environ.get("SCREEN_TRANSLATOR_ENABLE_SHORTCUT_HELPER", "0") == "1":
            try:
                from src.utils.installer import Installer
                self.installer = Installer()
                self.shortcut_btn = QPushButton("创建快捷方式")
                self._set_scaled_stylesheet(self.shortcut_btn, "background-color: #2196F3; color: white; font-weight: bold;")
                self.shortcut_btn.clicked.connect(self.run_shortcut_creator)
                button_layout.addWidget(self.shortcut_btn)
            except Exception:
                self.installer = None
        
        self.quit_button = QPushButton("退出")
        self.quit_button.clicked.connect(self.close)
        button_layout.addWidget(self.quit_button)
        
        self._view_main_layout.addLayout(button_layout)
        
        # 添加弹性空间
        self._view_status_layout.addStretch()

        log_group = QGroupBox("历史记录")
        self._history_group = log_group
        log_layout = QVBoxLayout()

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        try:
            self.log_text.setAcceptRichText(False)
        except Exception:
            pass
        try:
            self.log_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        except Exception:
            pass
        log_layout.addWidget(self.log_text)

        log_group.setLayout(log_layout)
        self._view_history_layout.addWidget(log_group, 1)
        self._view_history_layout.addStretch()

        hook_group = QGroupBox("Hook模式（系统钩子/外部钩子）")
        hook_layout = QVBoxLayout()

        hook_top = QHBoxLayout()
        self.hook_back_button = QPushButton("返回主界面")
        self.hook_back_button.clicked.connect(self.show_main_view)
        hook_top.addWidget(self.hook_back_button)

        self.hook_start_button = QPushButton()
        self.hook_start_button.clicked.connect(self.toggle_hook_mode)
        hook_top.addWidget(self.hook_start_button)
        hook_layout.addLayout(hook_top)

        self.hook_search_edit = QLineEdit()
        self.hook_search_edit.setPlaceholderText("搜索进程（按名称过滤）")
        self.hook_search_edit.textChanged.connect(self._hook_refresh_process_list)
        hook_layout.addWidget(self.hook_search_edit)

        self.hook_process_list = QListWidget()
        self.hook_process_list.itemSelectionChanged.connect(self._hook_on_process_selected)
        hook_layout.addWidget(self.hook_process_list, 1)

        proc_layout = QHBoxLayout()
        proc_layout.addWidget(QLabel("目标进程:"))
        self.hook_process_name_edit = QLineEdit()
        self.hook_process_name_edit.setPlaceholderText("例如: game.exe")
        self.hook_process_name_edit.setText(str(self.config.get("hook_target_process", "") or ""))
        self.hook_process_name_edit.editingFinished.connect(self._hook_save_fields_to_config)
        proc_layout.addWidget(self.hook_process_name_edit)
        hook_layout.addLayout(proc_layout)

        refresh_layout = QHBoxLayout()
        self.hook_refresh_button = QPushButton("刷新进程列表")
        self.hook_refresh_button.clicked.connect(self._hook_refresh_process_list)
        refresh_layout.addWidget(self.hook_refresh_button)
        refresh_layout.addStretch()
        hook_layout.addLayout(refresh_layout)

        intercept_group = QGroupBox("拦截文本")
        intercept_layout = QVBoxLayout()

        intercept_top = QHBoxLayout()
        self.hook_realtime_translate_checkbox = QCheckBox("实时翻译")
        try:
            self.hook_realtime_translate_checkbox.setChecked(True)
        except Exception:
            pass
        intercept_top.addWidget(self.hook_realtime_translate_checkbox)

        self.hook_translate_selected_button = QPushButton("翻译选中")
        self.hook_translate_selected_button.clicked.connect(self._hook_translate_selected_text)
        intercept_top.addWidget(self.hook_translate_selected_button)

        self.hook_clear_texts_button = QPushButton("清空")
        self.hook_clear_texts_button.clicked.connect(self._hook_clear_intercepted_texts)
        intercept_top.addWidget(self.hook_clear_texts_button)
        intercept_top.addStretch()
        intercept_layout.addLayout(intercept_top)

        intercept_filter = QHBoxLayout()
        intercept_filter.addWidget(QLabel("搜索:"))
        self.hook_intercepted_search_edit = QLineEdit()
        self.hook_intercepted_search_edit.setPlaceholderText("按内容过滤拦截文本")
        self.hook_intercepted_search_edit.textChanged.connect(self._hook_apply_intercepted_filter)
        intercept_filter.addWidget(self.hook_intercepted_search_edit, 1)
        intercept_layout.addLayout(intercept_filter)

        self.hook_intercepted_text_list = QListWidget()
        try:
            self.hook_intercepted_text_list.itemDoubleClicked.connect(lambda _it: self._hook_translate_selected_text())
        except Exception:
            pass
        try:
            self.hook_intercepted_text_list.itemSelectionChanged.connect(self._hook_on_intercepted_text_selected)
        except Exception:
            pass
        intercept_layout.addWidget(self.hook_intercepted_text_list, 1)
        intercept_group.setLayout(intercept_layout)
        hook_layout.addWidget(intercept_group, 2)

        hook_group.setLayout(hook_layout)
        self._view_hook_layout.addWidget(hook_group, 1)
        self._view_hook_layout.addStretch()

        glossary_group = QGroupBox("翻译词库")
        glossary_layout = QVBoxLayout()
        glossary_layout.setSpacing(self._scale_size(10))

        glossary_hint = QLabel("每行一条：原词=固定译法（例如：魔王=魔王大人）")
        glossary_layout.addWidget(glossary_hint)

        self.glossary_text_edit = QTextEdit()
        try:
            self.glossary_text_edit.setAcceptRichText(False)
        except Exception:
            pass
        self.glossary_text_edit.setPlaceholderText("例如：\n魔王=魔王大人\n勇者=勇者大人")
        glossary_layout.addWidget(self.glossary_text_edit, 1)

        glossary_btn_row = QHBoxLayout()
        self.glossary_save_button = QPushButton("保存词库")
        self.glossary_save_button.clicked.connect(self.save_glossary_settings)
        glossary_btn_row.addWidget(self.glossary_save_button)

        self.glossary_clear_button = QPushButton("清空")
        self.glossary_clear_button.clicked.connect(self.clear_glossary_settings)
        glossary_btn_row.addWidget(self.glossary_clear_button)

        glossary_btn_row.addStretch()
        glossary_layout.addLayout(glossary_btn_row)

        glossary_group.setLayout(glossary_layout)
        self._view_glossary_layout.addWidget(glossary_group, 1)
        self._view_glossary_layout.addStretch()

        try:
            self._view_status.hide()
            self._view_history.hide()
            self._view_hook.hide()
            self._view_glossary.hide()
        except Exception:
            pass

        # 初始化启动/停止按钮文本
        self.update_translate_button_label()
        try:
            self._update_hook_button_label()
        except Exception:
            pass
        self._install_main_page_effects()

    def show_system_status_dialog(self) -> None:
        try:
            self._set_active_view("system_status")
        except Exception:
            pass

    def show_history_dialog(self) -> None:
        try:
            self._set_active_view("history")
            try:
                self.log_text.setFocus()
            except Exception:
                pass
            try:
                self.log_text.moveCursor(QTextCursor.MoveOperation.End)
            except Exception:
                pass
        except Exception:
            pass

    def show_main_view(self) -> None:
        try:
            self._set_active_view("main")
        except Exception:
            pass

    def show_hook_view(self) -> None:
        try:
            self._set_active_view("hook")
        except Exception:
            return
        try:
            self._hook_sync_fields_from_config()
        except Exception:
            pass
        try:
            self._hook_refresh_process_list()
        except Exception:
            pass

    def show_glossary_view(self) -> None:
        try:
            self._set_active_view("glossary")
        except Exception:
            return
        try:
            self._load_glossary_into_editor()
        except Exception:
            pass

    def _get_glossary_raw(self) -> str:
        try:
            return str(self.config_manager.get("glossary", "entries", "") or "")
        except Exception:
            return ""

    def _load_glossary_into_editor(self) -> None:
        raw = self._get_glossary_raw()
        try:
            if hasattr(self, "glossary_text_edit") and self.glossary_text_edit is not None:
                self.glossary_text_edit.setPlainText(raw)
        except Exception:
            pass

    def _lookup_glossary_exact(self, text: str) -> str | None:
        raw = self._get_glossary_raw()
        entries = self._parse_glossary_entries(raw)
        if not entries:
            return None
        s = str(text or "")
        try:
            s = s.replace("\r\n", "\n").replace("\r", "\n").strip()
        except Exception:
            try:
                s = s.strip()
            except Exception:
                pass
        if not s:
            return None

        exact: dict[str, str] = {}
        try:
            exact = {k: v for k, v in entries if k and v}
        except Exception:
            exact = {}
        if s in exact:
            return exact.get(s)

        if s.isascii():
            folded = s.casefold()
            for k, v in entries:
                if not k or not v:
                    continue
                if k.isascii() and k.casefold() == folded:
                    return v

        return None

    def save_glossary_settings(self) -> None:
        raw = ""
        try:
            raw = str(self.glossary_text_edit.toPlainText() or "")
        except Exception:
            raw = ""
        try:
            raw = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
        except Exception:
            pass
        try:
            self.config_manager.set("glossary", "entries", raw)
        except Exception:
            pass

    def clear_glossary_settings(self) -> None:
        try:
            if hasattr(self, "glossary_text_edit") and self.glossary_text_edit is not None:
                self.glossary_text_edit.setPlainText("")
        except Exception:
            pass
        try:
            self.config_manager.set("glossary", "entries", "")
        except Exception:
            pass

    def _parse_glossary_entries(self, raw: str) -> list[tuple[str, str]]:
        text = str(raw or "")
        lines = []
        try:
            lines = text.splitlines()
        except Exception:
            lines = []
        items: list[tuple[str, str]] = []
        last_by_src: dict[str, str] = {}
        for ln in lines:
            s = str(ln or "").strip()
            if not s:
                continue
            if s.startswith("#") or s.startswith("//"):
                continue
            sep = None
            for cand in ("=>", "->", "=", "：", ":"):
                if cand in s:
                    sep = cand
                    break
            if not sep:
                continue
            a, b = s.split(sep, 1)
            src = str(a or "").strip()
            dst = str(b or "").strip()
            if not src or not dst:
                continue
            last_by_src[src] = dst
        for src, dst in last_by_src.items():
            items.append((src, dst))
        try:
            items.sort(key=lambda x: len(x[0]), reverse=True)
        except Exception:
            pass
        return items

    def _apply_glossary_placeholders(self, text: str) -> tuple[str, list[tuple[str, str]]]:
        raw = self._get_glossary_raw()
        entries = self._parse_glossary_entries(raw)
        if not entries:
            return text, []
        out = str(text or "")
        repls: list[tuple[str, str]] = []
        used: set[str] = set()
        idx = 0
        for src, dst in entries:
            if not src or not dst:
                continue
            if src.isascii():
                try:
                    if re.fullmatch(r"[A-Za-z0-9_]+", src):
                        pat = re.compile(rf"\b{re.escape(src)}\b", re.IGNORECASE)
                    else:
                        pat = re.compile(re.escape(src), re.IGNORECASE)
                    if not pat.search(out):
                        continue
                except Exception:
                    if src not in out:
                        continue
                    pat = None
            else:
                if src not in out:
                    continue
                pat = None
            ph = f"__GLOSSARYTOKEN_{idx}__"
            while ph in out or ph in used:
                idx += 1
                ph = f"__GLOSSARYTOKEN_{idx}__"
            if pat is not None:
                try:
                    out, n = pat.subn(ph, out)
                    if int(n or 0) <= 0:
                        idx += 1
                        continue
                except Exception:
                    out = out.replace(src, ph)
            else:
                out = out.replace(src, ph)
            repls.append((ph, dst))
            used.add(ph)
            idx += 1
        return out, repls

    def _set_active_view(self, view: str) -> None:
        v = (view or "").strip().lower()
        if v not in ("main", "system_status", "history", "hook", "glossary"):
            v = "main"

        try:
            self._view_main.setVisible(v == "main")
        except Exception:
            pass
        try:
            self._view_status.setVisible(v == "system_status")
        except Exception:
            pass
        try:
            self._view_history.setVisible(v == "history")
        except Exception:
            pass
        try:
            self._view_hook.setVisible(v == "hook")
        except Exception:
            pass
        try:
            self._view_glossary.setVisible(v == "glossary")
        except Exception:
            pass

        try:
            act = self._hero_menu_actions.get("main")
            if act is not None:
                act.setChecked(v == "main")
            act = self._hero_menu_actions.get("system_status")
            if act is not None:
                act.setChecked(v == "system_status")
            act = self._hero_menu_actions.get("history")
            if act is not None:
                act.setChecked(v == "history")
            act = self._hero_menu_actions.get("glossary")
            if act is not None:
                act.setChecked(v == "glossary")
        except Exception:
            pass

    def _apply_main_page_theme(self, root: QWidget) -> None:
        css = """
            #mainRoot {
                background-color: #F6F7FB;
            }

            #mainRoot #heroHeader {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #FFFFFF, stop:1 #EEF2FF);
                border: 1px solid rgba(17, 24, 39, 0.08);
                border-radius: 14px;
            }
            #mainRoot #heroTitle {
                color: #111827;
            }
            #mainRoot #heroSubtitle {
                color: rgba(17, 24, 39, 0.55);
                font-size: 12px;
            }

            #mainRoot QGroupBox {
                background-color: rgba(255, 255, 255, 0.92);
                border: 1px solid rgba(17, 24, 39, 0.08);
                border-radius: 14px;
                margin-top: 12px;
            }
            #mainRoot QGroupBox::title {
                subcontrol-origin: margin;
                left: 14px;
                padding: 0px 6px;
                color: rgba(17, 24, 39, 0.72);
                font-weight: 600;
            }

            #mainRoot QLabel {
                color: rgba(17, 24, 39, 0.86);
                font-size: 13px;
            }

            #mainRoot QPushButton {
                background-color: rgba(255, 255, 255, 0.96);
                border: 1px solid rgba(17, 24, 39, 0.10);
                border-radius: 12px;
                padding: 10px 12px;
            }
            #mainRoot QPushButton:hover {
                background-color: #FFFFFF;
                border-color: rgba(59, 130, 246, 0.28);
            }
            #mainRoot QPushButton:pressed {
                background-color: rgba(238, 242, 255, 0.92);
                border-color: rgba(59, 130, 246, 0.35);
            }
            #mainRoot QPushButton:disabled {
                color: rgba(17, 24, 39, 0.35);
                background-color: rgba(255, 255, 255, 0.55);
                border-color: rgba(17, 24, 39, 0.06);
            }

            #mainRoot #hookModeButton {
                background-color: #3B82F6;
                border-color: rgba(37, 99, 235, 0.40);
                color: #FFFFFF;
                font-weight: 700;
            }
            #mainRoot #hookModeButton:hover {
                background-color: #2563EB;
                border-color: rgba(37, 99, 235, 0.55);
            }
            #mainRoot #hookModeButton:pressed {
                background-color: #1D4ED8;
                border-color: rgba(29, 78, 216, 0.65);
            }
            #mainRoot #hookModeButton:disabled {
                background-color: rgba(59, 130, 246, 0.40);
                border-color: rgba(37, 99, 235, 0.22);
                color: rgba(255, 255, 255, 0.78);
            }

            #mainRoot QLineEdit,
            #mainRoot QComboBox,
            #mainRoot QSpinBox,
            #mainRoot QDoubleSpinBox,
            #mainRoot QTextEdit {
                background-color: rgba(255, 255, 255, 0.96);
                border: 1px solid rgba(17, 24, 39, 0.10);
                border-radius: 12px;
                padding: 8px 10px;
                selection-background-color: #C7D2FE;
            }
            #mainRoot QTextEdit {
                padding: 10px;
            }

            #mainRoot QComboBox::drop-down {
                border: 0px;
                width: 28px;
            }

            #mainRoot QScrollBar:vertical {
                background: transparent;
                width: 10px;
                margin: 2px;
            }
            #mainRoot QScrollBar::handle:vertical {
                background: rgba(17, 24, 39, 0.18);
                border-radius: 5px;
                min-height: 26px;
            }
            #mainRoot QScrollBar::handle:vertical:hover {
                background: rgba(17, 24, 39, 0.28);
            }
            #mainRoot QScrollBar::add-line:vertical,
            #mainRoot QScrollBar::sub-line:vertical {
                height: 0px;
            }
            #mainRoot QScrollBar::add-page:vertical,
            #mainRoot QScrollBar::sub-page:vertical {
                background: transparent;
            }
        """
        self._set_scaled_stylesheet(root, css)

    def _apply_card_shadow(self, w: QWidget, *, blur: int, offset_y: int, alpha: int) -> None:
        eff = QGraphicsDropShadowEffect(w)
        eff.setBlurRadius(float(blur))
        eff.setOffset(0, int(offset_y))
        eff.setColor(QColor(0, 0, 0, int(alpha)))
        w.setGraphicsEffect(eff)
        self._ui_effect_refs.append(eff)

    def _install_main_page_effects(self) -> None:
        try:
            root = self.centralWidget()
        except Exception:
            root = None
        if root is None:
            return

        if self._main_page_card_targets or self._main_page_hover_filters:
            self._rescale_main_page_effects()
            return

        try:
            header = root.findChild(QFrame, "heroHeader")
            if header is not None:
                self._main_page_card_targets.append((header, 22, 8, 28))
        except Exception:
            pass

        try:
            for gb in root.findChildren(QGroupBox):
                self._main_page_card_targets.append((gb, 20, 7, 26))
        except Exception:
            pass
        try:
            for w, blur_base, off_base, alpha in self._main_page_card_targets:
                self._apply_card_shadow(
                    w,
                    blur=self._scale_size(blur_base),
                    offset_y=self._scale_size(off_base),
                    alpha=alpha,
                )
        except Exception:
            pass

        try:
            btns: list[QWidget] = []
            for name in (
                "translate_button",
                "text_mode_button",
                "hook_mode_button",
                "test_button",
                "ocr_core_color_pick_btn",
                "ocr_core_color_dropper_btn",
                "save_button",
                "about_button",
                "quit_button",
                "shortcut_btn",
            ):
                w = getattr(self, name, None)
                if w is not None:
                    btns.append(w)

            for b in btns:
                flt = _ShadowHoverFilter(
                    b,
                    base_blur=self._scale_size(10),
                    hover_blur=self._scale_size(16),
                    pressed_blur=self._scale_size(8),
                    offset_y=self._scale_size(5),
                    color=QColor(0, 0, 0, 38),
                    duration_ms=160,
                )
                self._ui_effect_refs.append(flt)
                self._main_page_hover_filters.append((flt, 10, 16, 8, 5, 38))
        except Exception:
            pass

    def _rescale_main_page_effects(self) -> None:
        try:
            for w, blur_base, off_base, alpha in list(self._main_page_card_targets):
                eff = w.graphicsEffect()
                if isinstance(eff, QGraphicsDropShadowEffect):
                    eff.setBlurRadius(float(self._scale_size(int(blur_base))))
                    eff.setOffset(0, int(self._scale_size(int(off_base))))
                    eff.setColor(QColor(0, 0, 0, int(alpha)))
        except Exception:
            pass

        try:
            for flt, b_base, h_base, p_base, off_base, alpha in list(self._main_page_hover_filters):
                flt.set_shadow(
                    base_blur=self._scale_size(int(b_base)),
                    hover_blur=self._scale_size(int(h_base)),
                    pressed_blur=self._scale_size(int(p_base)),
                    offset_y=self._scale_size(int(off_base)),
                    color=QColor(0, 0, 0, int(alpha)),
                )
        except Exception:
            pass

    def set_hotkey_manager(self, hotkey_manager):
        """由外部注入 HotkeyManager，用于动态更新全局快捷键"""
        self.hotkey_manager = hotkey_manager
        # 同步一次当前配置到热键管理器
        hotkey = self.config.get('hotkey', 'b')
        parsed = parse_hotkey_string(hotkey)
        self.hotkey_manager.set_hotkey(parsed)
        self.update_translate_button_label()

    def begin_async_components_init(self, *, model_path: str | None = None) -> None:
        """
        异步初始化 OCR + 模型（窗口已显示后调用）。
        - 让 UI 先出来
        - 初始化完成后再启用“启动翻译/测试”等功能
        """
        # 已就绪则无需重复初始化
        if self._components_ready_for_work():
            return
        if self._async_init_thread and self._async_init_thread.isRunning():
            return

        self._model_path_for_init = model_path
        self._init_progress_text = "开始初始化"
        self._component_stats = {"tesseract": {}, "ocr": {}, "translator": {}}

        self.translate_button.setEnabled(False)
        try:
            if hasattr(self, "hook_mode_button") and self.hook_mode_button is not None:
                self.hook_mode_button.setEnabled(False)
        except Exception:
            pass
        try:
            self.test_button.setEnabled(False)
        except Exception:
            pass

        th = _ComponentInitThread(
            config_manager=self.config_manager,
            tesseract_manager=self.tesseract_manager,
            model_path=model_path,
        )
        self._async_init_thread = th
        th.progress.connect(self._on_init_progress)
        th.component_ready.connect(self._on_component_ready)
        th.init_finished.connect(self._on_components_init_finished)
        th.start()

        # 立即刷新一次，让状态区立刻可见
        self._refresh_system_status()

    def _components_ready_for_work(self) -> bool:
        return bool(self.ocr_processor) and bool(self.translator)

    def _on_init_progress(self, msg: str) -> None:
        self._init_progress_text = str(msg or "").strip() or "初始化中"
        try:
            self.update_translate_button_label()
        except Exception:
            pass
        self._refresh_system_status()

    def _on_component_ready(self, name: str, component: object, stats: dict) -> None:
        name = (name or "").strip().lower()
        try:
            self._component_stats[name] = stats or {}
        except Exception:
            pass

        if name == "tesseract":
            self.tesseract_manager = component  # type: ignore[assignment]
        elif name == "ocr":
            self.ocr_processor = component  # type: ignore[assignment]
        elif name == "translator":
            self.translator = component  # type: ignore[assignment]
            # 仅在 CUDA 模式下启用 GPU 监控（否则避免 import torch）
            try:
                dev = str(getattr(self.translator, "device", "") or "").lower()
                self._gpu_stats_enabled = (dev == "cuda")
            except Exception:
                self._gpu_stats_enabled = False
            # 文本模式只依赖翻译器：翻译器到位即可启用
            try:
                if hasattr(self, "text_mode_button") and self.text_mode_button is not None:
                    self.text_mode_button.setEnabled(True)
            except Exception:
                pass
            try:
                if hasattr(self, "hook_mode_button") and self.hook_mode_button is not None:
                    self.hook_mode_button.setEnabled(True)
            except Exception:
                pass

        # OCR 组件到位后，应用一次配置（确保 UI 改动实时生效）
        if name == "ocr" and self.ocr_processor:
            try:
                self.ocr_processor.apply_config(self.config_manager)
            except Exception:
                pass

        self._refresh_system_status()

    def _on_components_init_finished(self, success: bool, components: dict, stats: dict) -> None:
        if success and isinstance(components, dict):
            self.tesseract_manager = components.get("tesseract_manager", self.tesseract_manager)
            self.ocr_processor = components.get("ocr_processor", self.ocr_processor)
            self.translator = components.get("translator", self.translator)

        if isinstance(stats, dict):
            # stats 结构可能包含 error
            for k in ("tesseract", "ocr", "translator"):
                if k in stats and isinstance(stats.get(k), dict):
                    self._component_stats[k] = stats.get(k) or {}
            if stats.get("error"):
                self._init_progress_text = f"初始化失败: {stats.get('error')}"
        else:
            self._init_progress_text = "初始化完成" if success else "初始化失败"

        # 组件就绪后启用按钮
        if self._components_ready_for_work():
            self.translate_button.setEnabled(True)
            try:
                self.test_button.setEnabled(True)
            except Exception:
                pass
            self._init_progress_text = "初始化完成"

        # 文本模式只要求翻译器就绪
        if self.translator:
            # 仅在 CUDA 模式下启用 GPU 监控（否则避免 import torch）
            try:
                dev = str(getattr(self.translator, "device", "") or "").lower()
                self._gpu_stats_enabled = (dev == "cuda")
            except Exception:
                self._gpu_stats_enabled = False
            try:
                if hasattr(self, "text_mode_button") and self.text_mode_button is not None:
                    self.text_mode_button.setEnabled(True)
            except Exception:
                pass

        try:
            self.update_translate_button_label()
        except Exception:
            pass
        self._refresh_system_status()

    def _refresh_system_status(self) -> None:
        """
        每秒刷新一次（以及进度/完成时触发）：
        - 组件状态（Tesseract / OCR / 模型）
        - 资源占用（进程内存/CPU，GPU 显存）
        """
        rm = None
        try:
            rm = getattr(self, "_resource_monitor", None)
            if rm is None:
                from src.utils import resource_monitor as rm
                self._resource_monitor = rm

            ps = rm.get_process_stats()
            gs = rm.get_gpu_stats() if getattr(self, "_gpu_stats_enabled", False) else None
        except Exception:
            ps = None
            gs = None

        # 进程资源
        try:
            if ps is not None:
                mem = rm.format_bytes(ps.rss_bytes) if rm is not None else "-"
                cpu = "-" if ps.cpu_percent is None else f"{ps.cpu_percent:.1f}%"
                gpu_part = ""
                if gs is not None and gs.available:
                    alloc = rm.format_bytes(gs.allocated_bytes) if rm is not None else "-"
                    resv = rm.format_bytes(gs.reserved_bytes) if rm is not None else "-"
                    total = rm.format_bytes(gs.total_bytes) if rm is not None else "-"
                    gpu_part = f" | GPU: {alloc} 已分配 / {resv} 已保留 / {total} 总计"
                self.process_resource_label.setText(f"进程资源: 内存 {mem} | CPU {cpu}{gpu_part}")
        except Exception:
            pass

        # Tesseract 状态
        try:
            tm = self.tesseract_manager
            available = None
            if tm is not None:
                try:
                    available = bool(tm.is_tesseract_available())
                except Exception:
                    available = None
            if available is True:
                self.tesseract_status_label.setText("Tesseract: 可用")
            elif available is False:
                self.tesseract_status_label.setText("Tesseract: 未找到（可点击启动翻译后安装）")
            else:
                self.tesseract_status_label.setText("Tesseract: 初始化中…")
        except Exception:
            pass

        # OCR 状态
        try:
            if self.ocr_processor:
                self.ocr_status_label.setText("OCR: 就绪")
            else:
                self.ocr_status_label.setText(f"OCR: {self._init_progress_text or '初始化中…'}")
        except Exception:
            pass

        # 模型状态
        try:
            if self.translator:
                device = getattr(self.translator, "device", None) or "-"
                self.model_status_label.setText(f"模型: 就绪（{device}）")
            else:
                self.model_status_label.setText(f"模型: {self._init_progress_text or '初始化中…'}")
        except Exception:
            pass

        # 模型/ocr 资源（使用“加载前后差值”估算 + 实时 GPU/CPU）
        try:
            cpu_now = "-"
            if ps is not None and ps.cpu_percent is not None:
                cpu_now = f"{ps.cpu_percent:.1f}%"
        except Exception:
            cpu_now = "-"

        try:
            ocr_delta = self._component_stats.get("ocr", {}).get("rss_delta_bytes")
            ocr_delta_s = (rm.format_bytes(ocr_delta) if (rm is not None and ocr_delta) else "-")
            if gs is None or not getattr(gs, "available", False):
                self.ocr_resource_label.setText(f"OCR资源: 内存Δ {ocr_delta_s} | CPU {cpu_now}")
            else:
                self.ocr_resource_label.setText(f"OCR资源: 内存Δ {ocr_delta_s}")
        except Exception:
            pass

        try:
            tstats = self._component_stats.get("translator", {}) or {}
            rss_d = tstats.get("rss_delta_bytes")
            rss_s = (rm.format_bytes(rss_d) if (rm is not None and rss_d) else "-")
            if gs is None or not getattr(gs, "available", False):
                self.model_resource_label.setText(f"模型资源: 内存Δ {rss_s} | CPU {cpu_now}")
            else:
                ga = tstats.get("gpu_allocated_delta_bytes")
                gr = tstats.get("gpu_reserved_delta_bytes")
                ga_s = (rm.format_bytes(ga) if (rm is not None and ga) else "-")
                gr_s = (rm.format_bytes(gr) if (rm is not None and gr) else "-")
                self.model_resource_label.setText(f"模型资源: 内存Δ {rss_s} | 显存Δ {ga_s} 已分配 / {gr_s} 已保留")
        except Exception:
            pass

    def update_translate_button_label(self):
        """根据当前状态和快捷键更新启动/停止按钮文本"""
        hotkey = self.config.get('hotkey', 'b')
        if not self._components_ready_for_work():
            hint = self._init_progress_text or "初始化中"
            self.translate_button.setText(f"初始化中… ({hint}) (快捷键: {hotkey})")
            try:
                self.translate_button.setEnabled(False)
            except Exception:
                pass
            return
        if not self.is_translating and (not self._screenshot_translation_allowed_by_language()):
            self.translate_button.setText(f"请先设置语言(快捷键: {hotkey})")
        else:
            state_text = "停止翻译" if self.is_translating else "启动翻译"
            self.translate_button.setText(f"{state_text} (快捷键: {hotkey})")
        try:
            if self.is_translating:
                self._set_scaled_stylesheet(self.translate_button, self._get_translate_button_base_css(active=True))
            elif self._screenshot_translation_allowed_by_language():
                self._set_scaled_stylesheet(self.translate_button, self._get_translate_button_base_css(active=False))
            else:
                self._set_scaled_stylesheet(self.translate_button, self._get_translate_button_base_css(active=True))
            if getattr(self, "_force_update_active", False):
                self.translate_button.setEnabled(False)
            elif self.is_translating:
                self.translate_button.setEnabled(True)
            else:
                self.translate_button.setEnabled(bool(self._screenshot_translation_allowed_by_language()))
        except Exception:
            pass

    def _combo_selected_temp_language_unset(self, combo: "QComboBox") -> bool:
        try:
            return combo.currentData() == "temp:"
        except Exception:
            return False

    def _screenshot_translation_allowed_by_language(self) -> bool:
        try:
            if self._combo_selected_temp_language_unset(self.source_lang_combo):
                return False
            if self._combo_selected_temp_language_unset(self.target_lang_combo):
                return False
        except Exception:
            return True
        return True
            
    def setup_system_tray(self):
        """设置系统托盘"""
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon = QSystemTrayIcon(self)
            
            # 创建托盘菜单
            tray_menu = QMenu()
            
            self._tray_actions = {}

            show_action = QAction("显示主窗口", self)
            show_action.triggered.connect(self.bring_to_front)
            tray_menu.addAction(show_action)
            self._tray_actions["show"] = show_action
            
            toggle_action = QAction("启动/停止翻译", self)
            toggle_action.triggered.connect(self.toggle_translation)
            tray_menu.addAction(toggle_action)
            self._tray_actions["toggle"] = toggle_action

            text_mode_action = QAction("输入模式（手动输入）", self)
            text_mode_action.triggered.connect(self.open_text_mode)
            tray_menu.addAction(text_mode_action)
            self._tray_actions["text_mode"] = text_mode_action

            tray_menu.addSeparator()
            
            quit_action = QAction("退出", self)
            quit_action.triggered.connect(self.close)
            tray_menu.addAction(quit_action)
            self._tray_actions["quit"] = quit_action
            
            self.tray_icon.setContextMenu(tray_menu)
            
            # 设置托盘图标
            icon_path = os.path.join(os.path.dirname(__file__), "../../assets/icons/icon.png")
            if os.path.exists(icon_path):
                self.tray_icon.setIcon(QIcon(icon_path))
            else:
                # 使用默认图标（使用窗口图标，避免依赖 QStyle）
                self.tray_icon.setIcon(self.windowIcon())
            
            self.tray_icon.setToolTip("屏幕翻译工具")
            self.tray_icon.show()
            
            # 托盘图标点击事件
            self.tray_icon.activated.connect(self.on_tray_icon_activated)
            
    def on_tray_icon_activated(self, reason):
        """托盘图标激活事件"""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.bring_to_front()

    def bring_to_front(self):
        """
        强制恢复并置前主窗口（解决“任务栏有图标但窗口不可见/点不开”的常见场景）
        - 从最小化/隐藏恢复
        - raise + activate 争取拿到焦点
        """
        try:
            # 确保不是最小化状态
            try:
                if self.isMinimized():
                    self.showNormal()
            except Exception:
                pass

            # 有些情况下 showNormal 不够，先 show 再 showNormal 更稳
            try:
                self.show()
            except Exception:
                pass
            try:
                self.showNormal()
            except Exception:
                pass

            # 置前与激活
            try:
                self.raise_()
            except Exception:
                pass
            try:
                self.activateWindow()
            except Exception:
                pass
        except Exception:
            # 置前失败不应影响主逻辑
            return
            
    def toggle_translation(self):
        """切换翻译状态"""
        if getattr(self, "_force_update_active", False):
            self._show_force_update_dialog()
            return
        try:
            if getattr(self, "_hook_running", False) or (
                getattr(self, "_hook_scan_thread", None) is not None and self._hook_scan_thread.isRunning()
            ):
                self._stop_hook_service()
        except Exception:
            pass
        if not self._components_ready_for_work():
            QMessageBox.information(self, "正在初始化", "模型 / OCR 尚未就绪，请稍候（可在“系统状态”查看进度）。")
            return
        if not self.is_translating:
            if not self._screenshot_translation_allowed_by_language():
                QMessageBox.information(self, "请先设置语言", "源语言或目标语言处于“临时语言：未设置”，已禁用截图翻译。请先选择语言后再启动。")
                try:
                    self.update_translate_button_label()
                except Exception:
                    pass
                return
            # 启动翻译
            if (self.tesseract_manager is None) or (not self.tesseract_manager.is_tesseract_available()):
                reply = QMessageBox.question(
                    self, "安装Tesseract",
                    "Tesseract-OCR未安装，需要下载安装（约800MB）。是否继续？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self.install_tesseract()
                else:
                    return
            
            # 初始化截图工具
            from src.ui.screenshot import ScreenshotTool
            from src.ui.overlay import TranslationOverlay

            try:
                self._clear_locked_capture_region()
            except Exception:
                pass

            self.screenshot_tool = ScreenshotTool()
            self.screenshot_tool.screenshot_taken.connect(self.process_screenshot)
            
            # 初始化悬浮窗
            self.overlay = TranslationOverlay()
            self.overlay.retranslate_requested.connect(self.on_retranslate_requested)
            self.overlay.set_opacity(self.config['overlay_opacity'])
            self.overlay.set_timeout(self.config['overlay_timeout'])
            self.overlay.set_auto_hide(self.config['overlay_auto_hide'])
            
            # 设置快捷键
            # 注意：实际快捷键监听需要在hotkey.py中实现
            # 这里只是UI状态更新
            
            self.is_translating = True
            self.update_translate_button_label()
            self._set_scaled_stylesheet(self.translate_button, self._get_translate_button_base_css(active=True))
            self.translation_status_label.setText("翻译服务: 已启用")
            self.translation_status_label.setStyleSheet("color: green;")
            
            self.log_message("翻译服务已启动")
            
        else:
            # 停止翻译
            if self.screenshot_tool:
                # 截图工具本身是一个 QObject，没有窗口，直接置空即可
                self.screenshot_tool = None
            
            if self.overlay:
                self.overlay.hide()
                self.overlay = None

            try:
                self._clear_locked_capture_region()
            except Exception:
                pass
            
            self.is_translating = False
            self.update_translate_button_label()
            self._set_scaled_stylesheet(self.translate_button, self._get_translate_button_base_css(active=False))
            self.translation_status_label.setText("翻译服务: 未启用")
            self.translation_status_label.setStyleSheet("color: red;")

            self.log_message("翻译服务已停止")

    def _hook_sync_fields_from_config(self) -> None:
        target_process = str(self.config.get("hook_target_process", "") or "").strip()

        try:
            self.hook_process_name_edit.setText(target_process)
        except Exception:
            pass

    def _hook_save_fields_to_config(self, *_args) -> None:
        try:
            target_process = str(self.hook_process_name_edit.text() or "").strip()
        except Exception:
            target_process = str(self.config.get("hook_target_process", "") or "").strip()

        try:
            old_target = str(self.config.get("hook_target_process", "") or "").strip()
        except Exception:
            old_target = ""
        try:
            if target_process and old_target and target_process.lower() != old_target.lower():
                self._hook_selected_pid = 0
        except Exception:
            pass

        self.config["hook_target_process"] = target_process
        try:
            self.config_manager.set("hook", "target_process", target_process)
        except Exception:
            pass

        try:
            self._update_hook_button_label()
        except Exception:
            pass

    def _hook_refresh_process_list(self, *_args) -> None:
        def _iter_processes() -> list[tuple[str, int]]:
            result: list[tuple[str, int]] = []
            try:
                import psutil
                for p in psutil.process_iter(attrs=["pid", "name"]):
                    try:
                        info = getattr(p, "info", {}) or {}
                        name = str(info.get("name") or "").strip()
                        pid = int(info.get("pid") or 0)
                        if name and pid > 0:
                            result.append((name, pid))
                    except Exception:
                        continue
            except Exception:
                try:
                    import ctypes
                    import ctypes.wintypes as wt

                    TH32CS_SNAPPROCESS = 0x00000002
                    CreateToolhelp32Snapshot = ctypes.windll.kernel32.CreateToolhelp32Snapshot
                    Process32FirstW = ctypes.windll.kernel32.Process32FirstW
                    Process32NextW = ctypes.windll.kernel32.Process32NextW
                    CloseHandle = ctypes.windll.kernel32.CloseHandle

                    class PROCESSENTRY32W(ctypes.Structure):
                        _fields_ = [
                            ("dwSize", wt.DWORD),
                            ("cntUsage", wt.DWORD),
                            ("th32ProcessID", wt.DWORD),
                            ("th32DefaultHeapID", wt.ULONG_PTR),
                            ("th32ModuleID", wt.DWORD),
                            ("cntThreads", wt.DWORD),
                            ("th32ParentProcessID", wt.DWORD),
                            ("pcPriClassBase", wt.LONG),
                            ("dwFlags", wt.DWORD),
                            ("szExeFile", wt.WCHAR * 260),
                        ]

                    snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
                    if snap != wt.HANDLE(-1).value:
                        try:
                            pe = PROCESSENTRY32W()
                            pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
                            if Process32FirstW(snap, ctypes.byref(pe)):
                                while True:
                                    try:
                                        name = ctypes.wstring_at(pe.szExeFile).strip()
                                    except Exception:
                                        name = ""
                                    pid = int(pe.th32ProcessID or 0)
                                    if name and pid > 0:
                                        result.append((name, pid))
                                    if not Process32NextW(snap, ctypes.byref(pe)):
                                        break
                        finally:
                            CloseHandle(snap)
                except Exception:
                    pass
            return result

        try:
            query = str(self.hook_search_edit.text() or "").strip().lower()
        except Exception:
            query = ""

        items: list[tuple[str, int]] = []
        try:
            all_items = _iter_processes()
            if query:
                for name, pid in all_items:
                    nm = name.lower()
                    pid_str = str(pid)
                    if (query in nm) or (query in pid_str):
                        items.append((name, pid))
            else:
                items = all_items
        except Exception:
            items = []

        try:
            items.sort(key=lambda x: (x[0].lower(), x[1]))
        except Exception:
            pass

        try:
            current = str(self.hook_process_name_edit.text() or "").strip()
        except Exception:
            current = str(self.config.get("hook_target_process", "") or "").strip()

        self.hook_process_list.blockSignals(True)
        try:
            self.hook_process_list.clear()
            selected_row = -1
            for idx, (name, pid) in enumerate(items):
                it = QListWidgetItem(f"{name}  (PID {pid})")
                it.setData(Qt.ItemDataRole.UserRole, {"name": name, "pid": pid})
                self.hook_process_list.addItem(it)
                if current and name.lower() == current.lower() and selected_row < 0:
                    selected_row = idx
            if selected_row >= 0:
                self.hook_process_list.setCurrentRow(selected_row)
        finally:
            self.hook_process_list.blockSignals(False)

    def _hook_resolve_pid_from_name(self, exe_name: str) -> int:
        name = str(exe_name or "").strip()
        if not name:
            return 0
        try:
            name_l = name.lower()
        except Exception:
            name_l = name

        pids: list[int] = []
        try:
            import psutil
            for p in psutil.process_iter(attrs=["pid", "name"]):
                try:
                    info = getattr(p, "info", {}) or {}
                    n = str(info.get("name") or "").strip()
                    pid = int(info.get("pid") or 0)
                    if pid > 0 and n and n.lower() == name_l:
                        pids.append(pid)
                except Exception:
                    continue
        except Exception:
            try:
                import ctypes
                import ctypes.wintypes as wt

                TH32CS_SNAPPROCESS = 0x00000002
                CreateToolhelp32Snapshot = ctypes.windll.kernel32.CreateToolhelp32Snapshot
                Process32FirstW = ctypes.windll.kernel32.Process32FirstW
                Process32NextW = ctypes.windll.kernel32.Process32NextW
                CloseHandle = ctypes.windll.kernel32.CloseHandle

                class PROCESSENTRY32W(ctypes.Structure):
                    _fields_ = [
                        ("dwSize", wt.DWORD),
                        ("cntUsage", wt.DWORD),
                        ("th32ProcessID", wt.DWORD),
                        ("th32DefaultHeapID", wt.ULONG_PTR),
                        ("th32ModuleID", wt.DWORD),
                        ("cntThreads", wt.DWORD),
                        ("th32ParentProcessID", wt.DWORD),
                        ("pcPriClassBase", wt.LONG),
                        ("dwFlags", wt.DWORD),
                        ("szExeFile", wt.WCHAR * 260),
                    ]

                snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
                if snap != wt.HANDLE(-1).value:
                    try:
                        pe = PROCESSENTRY32W()
                        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
                        if Process32FirstW(snap, ctypes.byref(pe)):
                            while True:
                                try:
                                    n = ctypes.wstring_at(pe.szExeFile).strip()
                                except Exception:
                                    n = ""
                                pid = int(pe.th32ProcessID or 0)
                                if pid > 0 and n and n.lower() == name_l:
                                    pids.append(pid)
                                if not Process32NextW(snap, ctypes.byref(pe)):
                                    break
                    finally:
                        CloseHandle(snap)
            except Exception:
                pass

        if not pids:
            return 0
        try:
            return int(max(pids))
        except Exception:
            return int(pids[0])

    def _hook_on_process_selected(self) -> None:
        try:
            it = self.hook_process_list.currentItem()
        except Exception:
            it = None
        if it is None:
            return
        try:
            data = it.data(Qt.ItemDataRole.UserRole) or {}
        except Exception:
            data = {}
        name = str(data.get("name") or "").strip()
        try:
            pid = int(data.get("pid") or 0)
        except Exception:
            pid = 0
        if not name:
            return
        try:
            self.hook_process_name_edit.setText(name)
        except Exception:
            pass
        try:
            self._hook_selected_pid = pid if pid > 0 else 0
        except Exception:
            pass
        self.config["hook_target_process"] = name
        try:
            self.config_manager.set("hook", "target_process", name)
        except Exception:
            pass

    def save_hook_settings(self, *_args) -> None:
        self._hook_save_fields_to_config(*_args)

    def _update_hook_button_label(self) -> None:
        try:
            running = self._hook_scan_thread is not None and self._hook_scan_thread.isRunning()
        except Exception:
            running = False

        if running:
            main_text = "Hook模式：运行中"
            start_text = "停止Hook模式"
        else:
            main_text = "Hook模式"
            start_text = "启动Hook模式"

        try:
            self.hook_mode_button.setText(main_text)
        except Exception:
            pass
        try:
            self.hook_start_button.setText(start_text)
        except Exception:
            pass

        try:
            if running:
                self.translate_button.setEnabled(False)
            else:
                self.update_translate_button_label()
        except Exception:
            pass

    def toggle_hook_mode(self, *_args) -> None:
        if getattr(self, "_force_update_active", False):
            self._show_force_update_dialog()
            return

        try:
            if getattr(self, "_hook_running", False) or (
                getattr(self, "_hook_scan_thread", None) is not None and self._hook_scan_thread.isRunning()
            ):
                self._stop_hook_service()
                return
        except Exception:
            pass

        self._start_hook_service()

    def _start_hook_service(self) -> None:
        try:
            if self.is_translating:
                self.toggle_translation()
        except Exception:
            pass

        try:
            self.config["hook_enabled"] = True
            self.config_manager.set("hook", "enabled", "true")
        except Exception:
            pass

        try:
            self._hook_save_fields_to_config()
        except Exception:
            pass

        try:
            if (not str(self.config.get("hook_target_process", "") or "").strip()) and self.hook_process_list is not None:
                it = None
                try:
                    it = self.hook_process_list.currentItem()
                except Exception:
                    it = None
                if it is not None:
                    try:
                        data = it.data(Qt.ItemDataRole.UserRole) or {}
                    except Exception:
                        data = {}
                    name = str(data.get("name") or "").strip()
                    if name:
                        try:
                            self.hook_process_name_edit.setText(name)
                        except Exception:
                            pass
                        self.config["hook_target_process"] = name
                        try:
                            self.config_manager.set("hook", "target_process", name)
                        except Exception:
                            pass
        except Exception:
            pass

        target_name = str(self.config.get("hook_target_process", "") or "").strip()
        target_pid = 0
        try:
            target_pid = int(getattr(self, "_hook_selected_pid", 0) or 0)
        except Exception:
            target_pid = 0
        if target_pid <= 0 and target_name:
            try:
                target_pid = int(self._hook_resolve_pid_from_name(target_name) or 0)
            except Exception:
                target_pid = 0
            try:
                self._hook_selected_pid = target_pid if target_pid > 0 else 0
            except Exception:
                pass
        try:
            self._hook_target_pid = int(target_pid or 0)
        except Exception:
            self._hook_target_pid = 0

        if target_pid <= 0:
            QMessageBox.warning(self, "Hook模式", "请先填写目标进程名（例如 game.exe）")
            return

        try:
            self._hook_session_id = int(getattr(self, "_hook_session_id", 0)) + 1
        except Exception:
            self._hook_session_id = 1
        session_id = int(self._hook_session_id)
        try:
            self._hook_any_text_received = False
            self._hook_log_current_path = ""
            self._last_hook_text = ""
            self._hook_learned = False
            self._hook_pending_prefix = ""
            self._hook_pending_prefix_ts = 0.0
        except Exception:
            pass
        try:
            self._hook_clear_intercepted_texts()
        except Exception:
            pass

        self.translation_status_label.setText("翻译服务: Hook模式钩子抓取中…")
        try:
            self.translation_status_label.setStyleSheet("color: #d97706;")
        except Exception:
            pass

        if target_name:
            self.log_message(f"Hook钩子启动: {target_name}  (PID {target_pid})")
        else:
            self.log_message(f"Hook钩子启动: PID {target_pid}")

        hook_port = None
        try:
            hook_port = int(self.config.get("hook_port", 37123))
        except Exception:
            hook_port = 37123
        prefer_frida_only = bool(self.config.get("hook_prefer_frida_only", True))
        th = HookTextThread(
            pid=int(target_pid),
            listen_port=hook_port,
            enable_win_event=not prefer_frida_only,
            enable_socket=True,
            enable_uia=not prefer_frida_only,
            prefer_frida_only=prefer_frida_only,
        )
        setattr(th, "_hook_session_id", session_id)
        self._hook_scan_thread = th
        th.text_received.connect(lambda t: self._on_hook_text_received(session_id, t))
        th.status.connect(lambda s: self._on_hook_status(session_id, s))
        th.start()
        self._hook_running = True
        try:
            self._update_hook_button_label()
        except Exception:
            pass

    def _on_hook_status(self, session_id: int, status: str) -> None:
        try:
            if int(session_id) != int(getattr(self, "_hook_session_id", 0)):
                return
        except Exception:
            pass
        s = str(status or "")
        if s:
            try:
                if "Hook架构不匹配" in s or "Hook需要切换:" in s:
                    self._maybe_launch_other_arch(s)
            except Exception:
                pass
        if s:
            try:
                if "已学习读取规则" in s or "Hook钩子已就绪" in s or "Hook外部端口监听" in s or "Hook Frida 已启动" in s or "Hook UIA 轮询已启动" in s:
                    self._hook_learned = True
                elif "规则失效" in s or "学习读取规则失败" in s:
                    self._hook_learned = False
            except Exception:
                pass
        if status and not str(status).startswith("Hook 日志未找到:"):
            self.log_message(status)
        try:
            if status:
                hook_log(f"STATUS: {status}")
        except Exception:
            pass

    def _on_hook_text_received(self, session_id: int, text: str) -> None:
        try:
            if int(session_id) != int(getattr(self, "_hook_session_id", 0)):
                return
        except Exception:
            pass

        payload = str(text or "").strip()
        if not payload:
            return
        try:
            if len(payload) == 1 and payload.isalpha() and payload.isascii():
                self._hook_pending_prefix = payload
                self._hook_pending_prefix_ts = float(time.time())
                return
        except Exception:
            pass

        try:
            pfx = str(getattr(self, "_hook_pending_prefix", "") or "")
            ts = float(getattr(self, "_hook_pending_prefix_ts", 0.0) or 0.0)
            if pfx and len(pfx) == 1 and pfx.isalpha() and pfx.isascii():
                if (time.time() - ts) <= 1.2:
                    if payload and payload[0].isascii() and payload[0].isalpha() and payload[0].islower():
                        if not payload.startswith(pfx) and payload[0] != " ":
                            payload = pfx + payload
                self._hook_pending_prefix = ""
                self._hook_pending_prefix_ts = 0.0
        except Exception:
            pass

        if payload == str(getattr(self, "_last_hook_text", "") or ""):
            return
        self._last_hook_text = payload
        try:
            hook_log(f"TEXT: {payload[:400]}")
        except Exception:
            pass

        try:
            self._hook_append_intercepted_text(payload)
        except Exception:
            pass

        if payload.startswith("[HOOK_READY]"):
            return

        try:
            cb = getattr(self, "hook_realtime_translate_checkbox", None)
            if cb is not None and not bool(cb.isChecked()):
                return
        except Exception:
            pass
        try:
            if not bool(getattr(self, "_hook_learned", False)):
                return
        except Exception:
            return

        if not self._ensure_overlay():
            return

        try:
            self.overlay.show_text_mode(title_text="hook翻译")
        except Exception:
            try:
                self.overlay.show()
            except Exception:
                return

        try:
            self.overlay.original_text.setPlainText(payload)
        except Exception:
            pass

        try:
            src_display = self._get_effective_language_display(for_source=True)
            tgt_display = self._get_effective_language_display(for_source=False)
            self.overlay.language_label.setText(f"{src_display} → {tgt_display}")
        except Exception:
            pass

        if not self.translator:
            try:
                self.overlay.update_translation_result("模型尚未加载完成，请稍候…")
            except Exception:
                pass
            return

        try:
            self.overlay.update_translation_result("正在翻译…")
        except Exception:
            pass

        try:
            self._start_async_translation(
                text=payload,
                source_lang=self._get_effective_language_key(for_source=True),
                target_lang=self._get_effective_language_key(for_source=False),
                request_tag="Hook",
                disable_preprocess=True,
            )
        except Exception as e:
            self.log_message(f"Hook翻译启动失败: {e}")

    def _maybe_launch_other_arch(self, status_text: str) -> None:
        if bool(getattr(self, "_hook_arch_switch_prompted", False)):
            return
        target_arch = ""
        s = str(status_text or "")
        if "Hook需要切换:" in s:
            try:
                target_arch = s.split("Hook需要切换:", 1)[-1].strip()
            except Exception:
                target_arch = ""
        if not target_arch:
            if "目标进程 x86" in s:
                target_arch = "x86"
            elif "目标进程 x64" in s:
                target_arch = "x64"
        if target_arch not in ("x86", "x64"):
            return
        try:
            self._hook_arch_switch_prompted = True
        except Exception:
            pass
        self._launch_other_arch(target_arch)

    def _launch_other_arch(self, target_arch: str) -> None:
        try:
            from pathlib import Path
        except Exception:
            return
        if not getattr(sys, "frozen", False):
            self._launch_hook_agent_source(target_arch)
            return
        self._launch_hook_agent_frozen(target_arch)

    def _is_32bit_python_cmd(self, cmd: list[str]) -> bool:
        try:
            result = subprocess.run(
                cmd + ["-c", "import struct,sys;sys.exit(0 if struct.calcsize('P')==4 else 1)"],
                capture_output=True,
                timeout=2,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _resolve_py32_cmd(self) -> list[str] | None:
        try:
            configured = str(self.config_manager.get("hook", "py32", "") or "").strip()
        except Exception:
            configured = ""
        env = os.environ.get("SCREEN_TRANSLATOR_PY32") or os.environ.get("PY32")

        candidates: list[list[str]] = []
        if configured:
            candidates.append([configured])
        if env and env != configured:
            candidates.append([env])
        if shutil.which("py"):
            candidates.append(["py", "-3-32"])

        bases = [
            os.environ.get("LOCALAPPDATA"),
            os.environ.get("ProgramFiles(x86)"),
            os.environ.get("ProgramFiles"),
        ]
        for base in [b for b in bases if b]:
            try:
                base_path = Path(base)
                for p in base_path.glob("Programs/Python/Python3*-32/python.exe"):
                    candidates.append([str(p)])
                for p in base_path.glob("Python3*-32/python.exe"):
                    candidates.append([str(p)])
            except Exception:
                continue

        seen: set[tuple[str, ...]] = set()
        for cmd in candidates:
            key = tuple(cmd)
            if key in seen:
                continue
            seen.add(key)
            if self._is_32bit_python_cmd(cmd):
                return cmd
        return None

    @staticmethod
    def _popen_hidden(args: list[str]) -> subprocess.Popen:
        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            try:
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0
            except Exception:
                startupinfo = None
            try:
                creationflags |= subprocess.CREATE_NO_WINDOW
            except Exception:
                pass
        return subprocess.Popen(
            args,
            startupinfo=startupinfo,
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @staticmethod
    def _maybe_use_pythonw(cmd: list[str]) -> list[str]:
        if not cmd:
            return cmd
        exe = cmd[0]
        try:
            p = Path(exe)
        except Exception:
            return cmd
        try:
            if p.name.lower() == "python.exe":
                pw = p.with_name("pythonw.exe")
                if pw.exists():
                    return [str(pw)] + cmd[1:]
        except Exception:
            return cmd
        return cmd

    def _launch_hook_agent_frozen(self, target_arch: str) -> None:
        try:
            from pathlib import Path
        except Exception:
            return
        exe_path = Path(sys.executable).resolve()
        parent = exe_path.parent
        base = parent
        try:
            if parent.name.endswith("-x86") or parent.name.endswith("-x64"):
                base = parent.parent
        except Exception:
            base = parent
        if target_arch == "x86":
            # Search order:
            # 1. Standard sibling folder (e.g. dist/ScreenTranslator-x86)
            # 2. Nested folder (e.g. dist/ScreenTranslator-x64/ScreenTranslator-x86)
            # 3. Direct subfolder (e.g. dist/HookAgent)
            # 4. Dev path (e.g. screen-translator/ScreenTranslator-x86)
            candidates = [
                base / "ScreenTranslator-x86" / "HookAgent" / "HookAgent.exe",
                parent / "ScreenTranslator-x86" / "HookAgent" / "HookAgent.exe",
                base / "HookAgent" / "HookAgent.exe",
                base.parent / "ScreenTranslator-x86" / "HookAgent" / "HookAgent.exe"
            ]
            
            found = None
            for c in candidates:
                if c.exists():
                    found = c
                    break
            
            candidate = found if found else candidates[0]
        else:
            candidate = base / f"ScreenTranslator-{target_arch}" / "HookAgent" / "HookAgent.exe"
            if not candidate.exists():
                 candidate = base.parent / f"ScreenTranslator-{target_arch}" / "HookAgent" / "HookAgent.exe"
        if not candidate.exists():
            QMessageBox.warning(self, "Hook提示", f"未找到注入助手: {candidate}")
            return
        try:
            pid = int(getattr(self, "_hook_target_pid", 0) or 0)
        except Exception:
            pid = 0
        if pid <= 0:
            QMessageBox.warning(self, "Hook提示", "未获取到目标PID，无法启动注入助手。")
            return
        port = int(self.config.get("hook_port", 37123))
        prefer_frida_only = bool(self.config.get("hook_prefer_frida_only", True))
        args = [str(candidate), "--pid", str(pid), "--port", str(port)]
        if prefer_frida_only:
            args.append("--prefer-frida-only")
        try:
            self._hook_agent_process = self._popen_hidden(args)
        except Exception as e:
            QMessageBox.warning(self, "Hook提示", f"启动注入助手失败: {e}")
            return
        QMessageBox.information(self, "Hook提示", f"已启动 {target_arch} 注入助手，主程序继续运行。")

    def _launch_hook_agent_source(self, target_arch: str) -> None:
        try:
            from pathlib import Path
        except Exception:
            return
        try:
            pid = int(getattr(self, "_hook_target_pid", 0) or 0)
        except Exception:
            pid = 0
        if pid <= 0:
            QMessageBox.warning(self, "Hook提示", "未获取到目标PID，无法启动注入助手。")
            return
        if target_arch == "x86":
            cmd = self._resolve_py32_cmd()
            if not cmd:
                QMessageBox.information(
                    self,
                    "Hook提示",
                    "未找到32位Python，无法启动32位注入助手。\n\n"
                    "解决方案（任选其一）：\n"
                    "1) 安装32位Python并确认可用\n"
                    "2) 在 settings.ini 的 [hook] 中设置 py32=32位python.exe\n"
                    "3) 设置环境变量 SCREEN_TRANSLATOR_PY32 指向32位Python\n"
                    "4) 安装 py 启动器并确保 py -3-32 可用",
                )
                return
        else:
            cmd = [sys.executable]
        cmd = self._maybe_use_pythonw(cmd)
        port = int(self.config.get("hook_port", 37123))
        agent = Path(__file__).resolve().parents[2] / "hook_agent.py"
        if not agent.exists():
            QMessageBox.warning(self, "Hook提示", f"未找到 hook_agent.py: {agent}")
            return
        prefer_frida_only = bool(self.config.get("hook_prefer_frida_only", True))
        args = cmd + [str(agent), "--pid", str(pid), "--port", str(port)]
        if prefer_frida_only:
            args.append("--prefer-frida-only")
        try:
            self._hook_agent_process = self._popen_hidden(args)
        except Exception as e:
            QMessageBox.warning(self, "Hook提示", f"启动注入助手失败: {e}")
            return
        QMessageBox.information(self, "Hook提示", f"已启动 {target_arch} 注入助手，主程序继续运行。")

    def _hook_append_intercepted_text(self, text: str) -> None:
        if not self._hook_text_basic_filter(text):
            return
        payload = str(text or "").strip()
        if not payload:
            return
        lw = getattr(self, "hook_intercepted_text_list", None)
        if lw is None:
            return
        try:
            if lw.count() > 0:
                last = lw.item(lw.count() - 1)
                if last is not None and str(last.text() or "").strip() == payload:
                    return
        except Exception:
            pass

        try:
            lw.addItem(payload)
        except Exception:
            return
        try:
            self._hook_apply_intercepted_filter()
        except Exception:
            pass
        try:
            lw.scrollToBottom()
        except Exception:
            pass
        try:
            max_items = 200
            while lw.count() > max_items:
                lw.takeItem(0)
        except Exception:
            pass

    def _hook_clear_intercepted_texts(self, *_args) -> None:
        lw = getattr(self, "hook_intercepted_text_list", None)
        if lw is None:
            return
        try:
            lw.clear()
        except Exception:
            pass
        try:
            self._hook_apply_intercepted_filter()
        except Exception:
            pass

    @staticmethod
    def _hook_text_basic_filter(text: str) -> bool:
        t = str(text or "").strip()
        if not t:
            return False
        if len(t) < 2:
            return False
        total = 0
        bad = 0
        for ch in t:
            if ch.isspace():
                continue
            total += 1
            cp = ord(ch)
            is_cjk = 0x4E00 <= cp <= 0x9FFF
            is_kana = 0x3040 <= cp <= 0x30FF
            is_hangul = 0xAC00 <= cp <= 0xD7AF
            if not (ch.isalnum() or is_cjk or is_kana or is_hangul):
                bad += 1
        if total <= 0:
            return False
        try:
            bad_ratio = float(bad) / float(total)
        except Exception:
            bad_ratio = 0.0
        if bad_ratio > 0.6:
            return False
        return True

    def _terminate_hook_agent_process(self) -> None:
        p = getattr(self, "_hook_agent_process", None)
        if p is None:
            return
        try:
            if getattr(p, "poll", None) is not None and p.poll() is not None:
                self._hook_agent_process = None
                return
        except Exception:
            pass
        try:
            p.terminate()
        except Exception:
            pass
        try:
            p.wait(timeout=1.5)
        except Exception:
            pass
        try:
            if getattr(p, "poll", None) is not None and p.poll() is None:
                p.kill()
        except Exception:
            pass
        self._hook_agent_process = None

    def _hook_apply_intercepted_filter(self, *_args) -> None:
        lw = getattr(self, "hook_intercepted_text_list", None)
        if lw is None:
            return
        term = ""
        try:
            term = str(getattr(self, "hook_intercepted_search_edit", None).text() or "").strip()
        except Exception:
            term = ""
        term_l = term.lower()

        try:
            n = int(lw.count() or 0)
        except Exception:
            n = 0
        for i in range(n):
            it = None
            try:
                it = lw.item(i)
            except Exception:
                it = None
            if it is None:
                continue
            s = ""
            try:
                s = str(it.text() or "")
            except Exception:
                s = ""
            show = self._hook_text_basic_filter(s)
            if show and term_l:
                try:
                    show = term_l in s.lower()
                except Exception:
                    show = False
            try:
                it.setHidden(not bool(show))
            except Exception:
                pass

    def _hook_on_intercepted_text_selected(self) -> None:
        try:
            if bool(getattr(self, "_hook_intercepted_program_select", False)):
                return
        except Exception:
            pass
        lw = getattr(self, "hook_intercepted_text_list", None)
        if lw is None:
            return
        it = None
        try:
            it = lw.currentItem()
        except Exception:
            it = None
        if it is None:
            return
        sample_text = ""
        try:
            sample_text = str(it.text() or "").strip()
        except Exception:
            sample_text = ""
        if not sample_text:
            return
        th = getattr(self, "_hook_scan_thread", None)
        try:
            if th is None or not th.isRunning():
                return
        except Exception:
            return
        try:
            th.request_learn(sample_text)
        except Exception:
            pass

    def _hook_translate_selected_text(self, *_args) -> None:
        lw = getattr(self, "hook_intercepted_text_list", None)
        if lw is None:
            return
        it = None
        try:
            it = lw.currentItem()
        except Exception:
            it = None
        if it is None:
            try:
                if lw.count() > 0:
                    it = lw.item(lw.count() - 1)
            except Exception:
                it = None
        payload = ""
        try:
            payload = str(it.text() or "").strip() if it is not None else ""
        except Exception:
            payload = ""
        if not payload:
            return

        if not self._ensure_overlay():
            return
        try:
            self.overlay.show_text_mode(title_text="hook翻译")
        except Exception:
            try:
                self.overlay.show()
            except Exception:
                return

        try:
            self.overlay.original_text.setPlainText(payload)
        except Exception:
            pass

        if not self.translator:
            try:
                self.overlay.update_translation_result("模型尚未加载完成，请稍候…")
            except Exception:
                pass
            return

        try:
            self.overlay.update_translation_result("正在翻译…")
        except Exception:
            pass

        try:
            self._start_async_translation(
                text=payload,
                source_lang=self._get_effective_language_key(for_source=True),
                target_lang=self._get_effective_language_key(for_source=False),
                request_tag="Hook",
                disable_preprocess=True,
            )
        except Exception as e:
            try:
                self.log_message(f"Hook翻译启动失败: {e}")
            except Exception:
                pass

    def _stop_hook_service(self) -> None:
        try:
            self._terminate_hook_agent_process()
        except Exception:
            pass
        try:
            self._hook_session_id = int(getattr(self, "_hook_session_id", 0)) + 1
        except Exception:
            self._hook_session_id = 1

        try:
            if self._hook_scan_thread is not None and self._hook_scan_thread.isRunning():
                try:
                    self._hook_scan_thread.requestInterruption()
                except Exception:
                    pass
                try:
                    self._hook_scan_thread.wait(1500)
                except Exception:
                    pass
        except Exception:
            pass
        self._hook_scan_thread = None

        self._hook_running = False
        try:
            self._hook_learned = False
        except Exception:
            pass
        self.translation_status_label.setText("翻译服务: 未启用")
        try:
            self.translation_status_label.setStyleSheet("color: red;")
        except Exception:
            pass
        try:
            self._update_hook_button_label()
        except Exception:
            pass

    def _ensure_overlay(self) -> bool:
        """确保悬浮窗已创建并完成基础配置。"""
        try:
            if self.overlay is None:
                from src.ui.overlay import TranslationOverlay
                self.overlay = TranslationOverlay()
                self.overlay.retranslate_requested.connect(self.on_retranslate_requested)
            # 配置可能在运行时被修改，因此每次确保一下
            try:
                self.overlay.set_opacity(self.config.get('overlay_opacity', 0.9))
                self.overlay.set_timeout(self.config.get('overlay_timeout', 10))
                self.overlay.set_auto_hide(self.config.get('overlay_auto_hide', True))
            except Exception:
                pass
            return True
        except Exception as e:
            try:
                self.log_message(f"悬浮窗初始化失败: {e}")
            except Exception:
                pass
            return False

    def open_text_mode(self):
        """打开输入模式（手动输入翻译，不走 OCR，也不做任何处理）。"""
        if getattr(self, "_force_update_active", False):
            self._show_force_update_dialog()
            return
        if not self._ensure_overlay():
            return

        try:
            self.overlay.show_text_mode()
        except Exception:
            try:
                self.overlay.show()
            except Exception:
                return

        # 若模型尚未就绪，给出提示（仍允许用户先输入，等模型好了再点“翻译”）
        if not self.translator:
            try:
                self.overlay.update_translation_result("模型尚未加载完成，请稍候再点击“翻译”。")
            except Exception:
                pass

    def _game_find_committed_readable_region_for_addr(self, *, pid: int, addr: int) -> tuple[int, int]:
        if os.name != "nt":
            return 0, 0
        try:
            pid_i = int(pid or 0)
        except Exception:
            pid_i = 0
        try:
            addr_i = int(addr or 0)
        except Exception:
            addr_i = 0
        if pid_i <= 0 or addr_i <= 0:
            return 0, 0

        import ctypes
        import ctypes.wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        OpenProcess = k32.OpenProcess
        OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
        OpenProcess.restype = ctypes.wintypes.HANDLE

        VirtualQueryEx = k32.VirtualQueryEx
        VirtualQueryEx.argtypes = [
            ctypes.wintypes.HANDLE,
            ctypes.wintypes.LPCVOID,
            ctypes.wintypes.LPVOID,
            ctypes.c_size_t,
        ]
        VirtualQueryEx.restype = ctypes.c_size_t

        CloseHandle = k32.CloseHandle
        CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
        CloseHandle.restype = ctypes.wintypes.BOOL

        PROCESS_QUERY_INFORMATION = 0x0400
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

        h = OpenProcess(PROCESS_QUERY_INFORMATION, False, int(pid_i))
        if not h:
            h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid_i))
        if not h:
            return 0, 0

        try:
            class MEMORY_BASIC_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("BaseAddress", ctypes.c_void_p),
                    ("AllocationBase", ctypes.c_void_p),
                    ("AllocationProtect", ctypes.wintypes.DWORD),
                    ("RegionSize", ctypes.c_size_t),
                    ("State", ctypes.wintypes.DWORD),
                    ("Protect", ctypes.wintypes.DWORD),
                    ("Type", ctypes.wintypes.DWORD),
                ]

            mbi = MEMORY_BASIC_INFORMATION()
            got = int(VirtualQueryEx(h, ctypes.c_void_p(int(addr_i)), ctypes.byref(mbi), ctypes.sizeof(mbi)) or 0)
            if got <= 0:
                return 0, 0

            MEM_COMMIT = 0x1000
            PAGE_NOACCESS = 0x01
            PAGE_GUARD = 0x100

            state = int(mbi.State or 0)
            protect = int(mbi.Protect or 0)
            if state != MEM_COMMIT:
                return 0, 0
            if (protect & PAGE_GUARD) or (protect & PAGE_NOACCESS):
                return 0, 0

            base = int(mbi.BaseAddress or 0)
            size = int(mbi.RegionSize or 0)
            if base <= 0 or size <= 0:
                return 0, 0
            return base, size
        finally:
            try:
                CloseHandle(h)
            except Exception:
                pass



    def _init_device_id(self):
        # 生成硬件ID（用于版本检查）
        try:
            th = _DeviceIDThread()
            self._device_id_thread = th
            th.device_id_ready.connect(self._on_device_id_ready)
            th.start()
        except Exception:
            self.device_id = ""

    def _on_device_id_ready(self, device_id: str) -> None:
        self.device_id = device_id or ""
        if not self.device_id:
            return
        try:
            QTimer.singleShot(600, self._check_client_update)
        except Exception:
            pass

    def _make_auth_client(self):
        """创建认证客户端（仅用于版本检查）"""
        from src.core.auth_client import AuthClient

        base_url = self.config_manager.get("auth", "base_url", "https://14ku.date")
        update_path = self.config_manager.get("auth", "update_path", "/api/client_update")
        try:
            timeout = float(self.config_manager.get("auth", "timeout", "10"))
        except Exception:
            timeout = 10.0
        return AuthClient(
            base_url=base_url,
            update_path=update_path,
            timeout=timeout,
        )

    def _format_howto_html(self) -> str:
        hotkey = self.config.get('hotkey', 'b')
        return f"""
        <h3>如何操作</h3>
        <ul>
            <li>复杂背景模式下建议选择字芯颜色；识别文本模式无需选择</li>
            <li>先点击“启动翻译”按钮再按下快捷键 (<b>{hotkey}</b>) 。
                此时会进入截图模式，可以拖动鼠标选择区域。</li>
            <li>选区有效后松开鼠标或按 Enter 完成截图并翻译。</li>
            <li>按 Esc 可以关闭截图窗口。</li>
            <li>翻译结果会在悬浮窗中显示，可复制、重新翻译或固定查看。</li>
            <li>ctrl+滚轮可以调节历史记录页面大小</li>
            <li>hook模式只需要选择游戏进程<b>目前是beta版，可能不兼容或出现bug</b></li>
            <li>可以设置快捷键<b>Windows10用户可能需要用管理员权限启动才能修改快捷键</b></li>
            <li><b>还有问题以及支持我们到官网https://14ku.date/support</b></li>
        </ul>
        """


    def _start_async_translation(
        self,
        *,
        text: str,
        source_lang: str,
        target_lang: str,
        request_tag: str = "",
        disable_preprocess: bool = False,
    ) -> int:
        """
        启动一次后台翻译任务。

        返回 request_id，用于丢弃过期结果（用户连续触发截图/重译时避免旧结果覆盖新结果）。
        """
        # 生成新的请求号
        self._translation_request_seq += 1
        request_id = self._translation_request_seq
        self._active_translation_request_seq = request_id

        processed_text = str(text or "")
        direct = None
        try:
            direct = self._lookup_glossary_exact(processed_text)
        except Exception:
            direct = None
        if direct is not None:
            try:
                self._on_async_translation_finished(
                    request_id,
                    request_tag,
                    _TranslationResult(
                        success=True,
                        translated_text=str(direct or ""),
                        original_text=str(text or ""),
                    ),
                )
            except Exception:
                pass
            return request_id

        try:
            glossary_entries = self._parse_glossary_entries(self._get_glossary_raw())
        except Exception:
            glossary_entries = []

        # 复用已加载的翻译器实例，避免每次翻译都重新加载模型
        # 如果已有线程在跑，允许并行（旧结果会被 request_id 丢弃）；不强行终止，避免卡住
        th = _TranslationThread(
            text=processed_text,
            source_lang=source_lang,
            target_lang=target_lang,
            disable_preprocess=bool(disable_preprocess),
            translator=self.translator,
            glossary_entries=glossary_entries,
        )

        self._translation_thread = th
        th.translation_finished.connect(lambda res: self._on_async_translation_finished(request_id, request_tag, res))
        
        if self.overlay:
            th.translation_progress.connect(self.overlay.update_translation_progress)
        
        th.start()
        return request_id

    def _on_async_translation_finished(self, request_id: int, request_tag: str, result: _TranslationResult):
        """后台翻译完成回调（UI 线程）"""
        # 丢弃过期请求
        if request_id != getattr(self, "_active_translation_request_seq", 0):
            return

        repls: list[tuple[str, str]] = []
        try:
            repls = list(self._translation_glossary_maps.pop(int(request_id), []) or [])
        except Exception:
            repls = []
        if repls and getattr(result, "translated_text", ""):
            try:
                t = str(result.translated_text or "")
                for ph, dst in repls:
                    if ph and dst:
                        t = t.replace(ph, dst)
                result.translated_text = t
            except Exception:
                pass

        if result.success and result.translated_text:
            self.last_translation = result.translated_text
            # 日志里显示完整翻译内容（不截断），便于复制与排查
            self.log_message(
                f"翻译完成{('('+request_tag+')') if request_tag else ''}:\n{result.translated_text}"
            )

            if self.overlay:
                # 若是 OCR 场景，会先 show_ocr_result，再异步更新
                self.overlay.update_translation_result(result.translated_text)
                # 复位按钮文案（重译场景会变成“正在翻译...”）
                try:
                    btn_text = "重新翻译"
                    try:
                        if getattr(self.overlay, "_mode", "") == "input":
                            btn_text = "翻译"
                    except Exception:
                        pass
                    self.overlay.retranslate_button.setText(btn_text)
                    self.overlay.retranslate_button.setEnabled(True)
                except Exception:
                    pass
                # 保持语言标签（若当前仍是 OCR 状态，则 overlay 内会改成“翻译完成”）
                try:
                    if self.overlay.language_label and not self.overlay.language_label.text().startswith("OCR完成"):
                        src_display = self._get_effective_language_display(for_source=True)
                        tgt_display = self._get_effective_language_display(for_source=False)
                        self.overlay.language_label.setText(f"{src_display} → {tgt_display}")
                except Exception:
                    pass

            # “测试翻译”按钮：给明确弹窗反馈
            if request_tag == "测试":
                try:
                    QMessageBox.information(self, "测试成功", f"翻译功能测试成功！\n\n{result.translated_text}")
                except Exception:
                    pass
        else:
            err = result.error or "翻译失败"
            self.log_message(f"翻译失败{('('+request_tag+')') if request_tag else ''}: {err}")
            if self.overlay:
                # 将错误信息也显示出来，方便用户理解/重试
                self.overlay.update_translation_result(err)
                try:
                    btn_text = "翻译失败，重试"
                    try:
                        if getattr(self.overlay, "_mode", "") == "input":
                            btn_text = "出错，重试"
                    except Exception:
                        pass
                    self.overlay.retranslate_button.setText(btn_text)
                    self.overlay.retranslate_button.setEnabled(True)
                except Exception:
                    pass

            if request_tag == "测试":
                try:
                    QMessageBox.warning(self, "测试失败", f"请检查模型文件")
                except Exception:
                    pass

    def show_how_to(self):
        """显示如何操作"""
        box = QMessageBox(self)
        box.setWindowTitle("如何操作")
        box.setIcon(QMessageBox.Icon.Information)
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(self._format_howto_html())
        box.addButton("知道了", QMessageBox.ButtonRole.AcceptRole)
        box.exec()

    def _check_client_update(self):
        """检查客户端版本更新"""
        if getattr(self, "_force_update_active", False):
            self._show_force_update_dialog()
            return
        if not self.device_id:
            return

        if self._update_thread is not None and self._update_thread.isRunning():
            return

        try:
            base_url = self.config_manager.get("auth", "base_url", "https://14ku.date")
            update_path = self.config_manager.get("auth", "update_path", "/api/client_update")
            download_url = self.config_manager.get("auth", "download_url", "https://14ku.date/download")
            timeout = float(self.config_manager.get("auth", "timeout", "10"))
        except Exception:
            timeout = 10.0

        current_version = "1.0.0"  # 可以从配置文件或常量中获取
        self._update_thread = _UpdateThread(
            device_id=self.device_id,
            current_version=current_version,
            base_url=base_url,
            update_path=update_path,
            download_url=download_url,
            timeout=timeout,
        )
        self._update_thread.finished.connect(self._on_update_check_finished)
        self._update_thread.start()

    def _on_update_check_finished(self, ok: bool, message: str, data_obj: object):
        """版本检查完成回调"""
        data = data_obj if isinstance(data_obj, dict) else {}
        if ok and isinstance(data, dict):
            has_update = data.get("has_update", False)
            new_version = data.get("version", "")
            download_url = data.get("download_url", "")
            force_update = data.get("force_update", False)
            reason = data.get("reason", "")

            if has_update:
                self._force_update_active = force_update
                self._force_update_reason = reason or f"发现新版本 {new_version}"
                self._force_update_download_url = download_url
                self._show_force_update_dialog()

        # 释放线程引用
        try:
            self._update_thread.quit()
            self._update_thread.wait(2000)
        except Exception:
            pass
        self._update_thread = None

    def _show_force_update_dialog(self):
        """显示强制更新对话框"""
        if not self._force_update_active:
            return

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle("发现新版本")
        msg.setText("发现新版本，请更新后使用")
        msg.setInformativeText(self._force_update_reason)
        
        if self._force_update_download_url:
            download_btn = msg.addButton("前往下载", QMessageBox.ButtonRole.AcceptRole)
            msg.addButton("稍后提醒", QMessageBox.ButtonRole.RejectRole)
        else:
            msg.addButton("知道了", QMessageBox.ButtonRole.AcceptRole)

        reply = msg.exec()
        
        if self._force_update_download_url and reply == 0:
            QDesktopServices.openUrl(QUrl(self._force_update_download_url))
        
        # 如果强制更新，禁用所有功能
        if self._force_update_active:
            try:
                if self.is_translating:
                    self.toggle_translation()
            except Exception:
                pass
            self.translate_button.setEnabled(False)

    def on_hotkey_triggered(self):
        """全局快捷键触发时的处理：在翻译服务开启时启动截图"""
        if getattr(self, "_force_update_active", False):
            return
        if not self.is_translating:
            return
        if not self._screenshot_translation_allowed_by_language():
            msg = "源语言或目标语言处于“临时语言：未设置”，已禁用截图翻译。"
            self.log_message(msg)
            try:
                if getattr(self, "tray_icon", None):
                    self.tray_icon.showMessage("无法截图翻译", msg, QSystemTrayIcon.MessageIcon.Warning, 2500)
            except Exception:
                pass
            return
        if self.screenshot_tool:
            keep_region = bool(self.config.get("keep_capture_region", False))
            if keep_region and self._locked_capture_rect is not None and not self._locked_capture_rect.isNull():
                self.log_message("检测到快捷键，使用保留区域截图...")
                try:
                    self._ensure_locked_region_frame()
                except Exception:
                    pass
                try:
                    result = self.screenshot_tool.grab_rect(QRect(self._locked_capture_rect))
                except Exception as e:
                    self.log_message(f"保留区域截图失败: {e}")
                    return
                self.process_screenshot(result)
                return

            self.log_message("检测到快捷键，开始截图...")
            self.screenshot_tool.start_capture()
        else:
            self.log_message("截图工具未初始化，无法开始截图")

    def install_tesseract(self):
        """安装Tesseract-OCR"""
        if self.tesseract_manager is None:
            self.log_message("Tesseract 管理器未初始化，无法安装。")
            return
        self.log_message("开始安装Tesseract-OCR...")

        # 这里调用TesseractManager的安装方法（实际安装逻辑在 tesseract_manager.py 中）
        success, message = self.tesseract_manager.download_and_setup_tesseract()
        
        if success:
            self.log_message(f"Tesseract-OCR安装成功: {message}")
        else:
            self.log_message(f"Tesseract-OCR安装失败: {message}")
            
    def on_retranslate_requested(self, text, disable_preprocess: bool = False):
        """处理悬浮窗发出的重新翻译/翻译请求"""
        # 日志显示完整原文，便于核对/排查
        tag = "文本" if disable_preprocess else "重译"
        self.log_message(f"{tag}翻译请求:\n{text}")
        
        # 获取当前设置的语言（使用 key，如 en/zh-CN/...）
        target_lang = self._get_effective_language_key(for_source=False)
        source_lang = self._get_effective_language_key(for_source=True)

        # 后台执行：扣费 -> 翻译（避免阻塞 UI）
        try:
            self._start_async_translation(
                text=text,
                source_lang=source_lang,
                target_lang=target_lang,
                request_tag=tag,
                disable_preprocess=bool(disable_preprocess),
            )
        except Exception as e:
            self.log_message(f"重新翻译启动失败: {e}")
            if self.overlay:
                self.overlay.retranslate_button.setText("出错，重试")
                self.overlay.retranslate_button.setEnabled(True)

    def process_screenshot(self, result: ScreenshotResult):
        """处理截图结果"""
        # 处理失败或取消的情况
        if (not result) or (not result.success) or (not result.image):
            if result and result.error:
                self.log_message(f"截图失败: {result.error}")
            else:
                self.log_message("截图取消")
            return
            
        if not self._screenshot_translation_allowed_by_language():
            msg = "源语言或目标语言处于“临时语言：未设置”，已取消本次截图翻译。"
            self.log_message(msg)
            if self.overlay:
                try:
                    self.overlay.show_ocr_result("", result.rect or self.rect())
                    self.overlay.original_text.setPlainText("语言未设置")
                    self.overlay.update_translation_result(msg)
                    self.overlay.language_label.setText("无法翻译")
                    self.overlay.retranslate_button.setEnabled(False)
                except Exception:
                    pass
            return

        keep_region = bool(self.config.get("keep_capture_region", False))
        if keep_region and result.rect is not None:
            try:
                self._locked_capture_rect = QRect(result.rect)
                self._ensure_locked_region_frame()
            except Exception:
                pass

        self.log_message("开始处理截图...")
        
        # 将 QPixmap 转换为 PIL Image
        pil_image = None
        buffer = None
        try:
            from PIL import Image
            qimage = result.image.toImage()
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            qimage.save(buffer, "PNG")
            pil_image = Image.open(io.BytesIO(buffer.data()))
            # 确保图像数据被加载到内存
            pil_image.load()
        except Exception as e:
            self.log_message(f"截图图像转换失败: {e}")
            if pil_image:
                try:
                    pil_image.close()
                except:
                    pass
            try:
                if buffer:
                    buffer.close()
            except Exception:
                pass
            return

        try:
            if not self.ocr_processor:
                self.log_message("OCR 组件尚未就绪，请稍候。")
                return
            
            ui_source_lang = self.source_lang_combo.currentText()
            source_lang = self._get_effective_language_key(for_source=True)
            target_lang = self._get_effective_language_key(for_source=False)
            
            # 立即显示弹窗，用户看到截图完成后立刻有反馈
            if self.overlay:
                try:
                    self.overlay.show_ocr_result("", result.rect or self.rect())
                    self.overlay.original_text.setPlainText("正在识别文字...")
                    self.overlay.translation_text.setPlainText("请稍候...")
                    self.overlay.language_label.setText("OCR 识别中...")
                    self.overlay.retranslate_button.setEnabled(False)
                    self.overlay.progress_bar.setValue(0)
                    self.overlay.progress_label.setText("准备中...")
                except Exception:
                    pass
            
            ocr_result = self.ocr_processor.extract_text(pil_image, ui_source_lang)
            if not ocr_result or not ocr_result.text:
                if ocr_result and getattr(ocr_result, "error", None):
                    self.log_message(f"OCR识别失败: {ocr_result.error}")
                else:
                    self.log_message("OCR识别失败或未识别到文字")
                
                if self.overlay:
                    try:
                        self.overlay.original_text.setPlainText("OCR 识别失败")
                        self.overlay.translation_text.setPlainText("未识别到文字")
                        self.overlay.language_label.setText("OCR 失败")
                        self.overlay.retranslate_button.setEnabled(True)
                        self.overlay.retranslate_button.setText("重试")
                        self.overlay.progress_bar.setValue(0)
                        self.overlay.progress_label.setText("")
                    except Exception:
                        pass
                return

            self.log_message(f"识别到文字:\n{ocr_result.text}")
            
            if self.overlay:
                try:
                    self.overlay.original_text.setPlainText(ocr_result.text)
                    # 确保显示进度条，隐藏翻译结果
                    if hasattr(self.overlay, 'progress_container'):
                        self.overlay.progress_container.setVisible(True)
                        self.overlay.translation_text.setVisible(False)
                    self.overlay.language_label.setText("OCR 完成")
                    self.overlay.retranslate_button.setEnabled(False)
                    self.overlay.progress_bar.setValue(15)
                    self.overlay.progress_label.setText("开始翻译...")
                except Exception:
                    pass

            if not self.translator:
                self.log_message("模型尚未就绪，已显示 OCR 结果；请等待模型加载完成后再截图翻译。")
                if self.overlay:
                    try:
                        self.overlay.translation_text.setPlainText("模型未就绪，请重试")
                        self.overlay.retranslate_button.setEnabled(True)
                        self.overlay.progress_bar.setValue(0)
                        self.overlay.progress_label.setText("")
                    except Exception:
                        pass
                return
            
            self._start_async_translation(
                text=ocr_result.text,
                source_lang=source_lang,
                target_lang=target_lang,
                request_tag="OCR",
            )
        finally:
            if pil_image:
                try:
                    pil_image.close()
                except:
                    pass
            try:
                if buffer:
                    buffer.close()
            except Exception:
                pass
            
    def test_translation(self):
        """测试翻译功能"""
        if getattr(self, "_force_update_active", False):
            self._show_force_update_dialog()
            return
        self.log_message("开始测试翻译功能...")
        if not self.translator:
            QMessageBox.information(self, "正在初始化", "模型尚未加载完成，请稍后再测试。")
            return
        
        # 这里可以添加测试逻辑
        # 例如：使用示例图片进行测试
        
        test_text = "Hello, this is a test of the screen translation tool."
        self.log_message(f"测试文本: {test_text}")

        # 用后台线程执行翻译，避免 UI 卡顿
        try:
            # 这里固定目标语言为中文
            self._start_async_translation(
                text=test_text,
                source_lang="en",
                target_lang="zh-CN",
                request_tag="测试",
            )
        except Exception as e:
            self.log_message(f"测试翻译启动失败: {e}")
            QMessageBox.warning(self, "测试失败", f"测试翻译启动失败: {e}")
            
    def get_language_code(self, language_name):
        """将语言名称（UI显示名/旧值）转换为规范化语言 key（如 en/zh-CN）。"""
        if not language_name:
            return "zh-CN"
        if str(language_name).strip() == self.SHOW_MORE_TEXT:
            # “显示更多…”不是实际语言
            return "zh-CN"
        return normalize_lang_key(key_for_display_name(str(language_name).strip()))

    def _get_effective_language_key(self, *, for_source: bool) -> str:
        """返回当前翻译实际使用的语言 key"""
        # 优先使用临时语言（如果有的话）
        if for_source:
            temp_lang = self.language_manager.get_temp_language_source()
            if temp_lang:
                return temp_lang
        else:
            temp_lang = self.language_manager.get_temp_language_target()
            if temp_lang:
                return temp_lang
        
        # 如果没有临时语言，使用配置中的设置
        config_key = self.config.get("source_language" if for_source else "target_language")
        if not config_key:
            return "en" if for_source else "zh-CN"
        return normalize_lang_key(config_key)

    def _get_effective_language_display(self, *, for_source: bool) -> str:
        """返回用于展示的语言名称（考虑临时语言和默认值）。"""
        key = self._get_effective_language_key(for_source=for_source)
        if not key:
            return display_name_for_key("en") if for_source else display_name_for_key("zh-CN")
        
        display = display_name_for_key(key)
        return display or ("自动检测" if for_source else display_name_for_key("zh-CN"))

    def _on_source_lang_combo_changed(self, index: int) -> None:
        """源语言下拉框变化处理"""
        self.language_manager.on_source_lang_combo_changed(index, self.source_lang_combo, self)
        try:
            self.update_translate_button_label()
        except Exception:
            pass

    def _on_target_lang_combo_changed(self, index: int) -> None:
        """目标语言下拉框变化处理"""
        self.language_manager.on_target_lang_combo_changed(index, self.target_lang_combo, self)
        try:
            self.update_translate_button_label()
        except Exception:
            pass

    def _open_language_picker(self, *, for_source: bool, slot_index: int = -1) -> None:
        """打开全语言搜索选择器，并把选择结果回填到 4 个快捷槽位。"""
        try:
            dlg = LanguagePickerDialog(
                parent=self,
                title="选择源语言" if for_source else "选择目标语言",
                show_auto=False,
            )
            items = [(l.display_name, l.key) for l in ALL_LANGUAGES]
            dlg.set_languages(items)
            if dlg.exec() != int(QDialog.DialogCode.Accepted):
                # 用户取消：恢复到一个有效项（避免 combo 停留在“显示更多…”）
                self._rebuild_language_combos(apply_config_selection=True)
                return
            picked = dlg.selected_key()
            if not picked:
                self._rebuild_language_combos(apply_config_selection=True)
                return
            picked_key = normalize_lang_key(str(picked))
            selected_slot = dlg.selected_slot()

            effective_slot = selected_slot if selected_slot >= 0 else (max(0, min(3, int(slot_index))) if slot_index >= 0 else -1)
            
            # 选择了 1-4 槽位按钮：直接替换对应快捷槽位（不移动/不后退）。
            if effective_slot >= 0:
                from src.core.languages import normalize_quick_language_keys

                slot = max(0, min(3, int(effective_slot)))  # 0-3
                ui_index = slot + 1  # combo: 1-4

                quick_source = list(self.language_manager.quick_lang_keys_source)
                quick_target = list(self.language_manager.quick_lang_keys_target)

                def replace_with_optional_swap(lst: list[str], slot_idx: int, new_key: str) -> list[str]:
                    # 保证长度 4
                    cur = list(lst or [])
                    cur = normalize_quick_language_keys(cur, desired_len=4)
                    old = cur[slot_idx]
                    if new_key in cur and cur.index(new_key) != slot_idx:
                        j = cur.index(new_key)
                        cur[j] = old  # 交换，避免重复，同时不改变整体顺序结构
                    cur[slot_idx] = new_key
                    return normalize_quick_language_keys(cur, desired_len=4)

                if for_source:
                    new_quick_source = replace_with_optional_swap(quick_source, slot, picked_key)
                    self.language_manager.update_quick_languages(new_quick_source, quick_target)
                    # 选中快捷槽位时：清掉临时源语言，并把当前源语言写入配置
                    self.language_manager.reset_temp_language_source()
                    try:
                        self.config["source_language"] = picked_key
                        self.config_manager.set("translation", "source_language", picked_key)
                        self.config_manager.save_config()
                    except Exception:
                        pass
                else:
                    new_quick_target = replace_with_optional_swap(quick_target, slot, picked_key)
                    self.language_manager.update_quick_languages(quick_source, new_quick_target)
                    self.language_manager.reset_temp_language_target()
                    try:
                        self.config["target_language"] = picked_key
                        self.config_manager.set("translation", "target_language", picked_key)
                        self.config_manager.save_config()
                    except Exception:
                        pass

                # 重建并选中：槽位 1-4
                self._rebuild_language_combos(apply_config_selection=True)
                combo = self.source_lang_combo if for_source else self.target_lang_combo
                combo.blockSignals(True)
                try:
                    if combo.count() > ui_index:
                        combo.setCurrentIndex(ui_index)
                finally:
                    combo.blockSignals(False)

                self.log_message("快捷语言槽位已保存（直接替换）")
                return

            # 如果没有选择槽位，则设置为临时语言（完全独立于快捷语言槽位）
            if for_source:
                self.language_manager.set_temp_language_source(picked_key)
            else:
                self.language_manager.set_temp_language_target(picked_key)
            # 临时语言：只重建并选中 slot0，不写入配置
            self._rebuild_language_combos(apply_config_selection=True)
            combo = self.source_lang_combo if for_source else self.target_lang_combo
            combo.blockSignals(True)
            try:
                combo.setCurrentIndex(0)
            finally:
                combo.blockSignals(False)
            self.log_message("已设置临时语言（不保存到快捷槽位）")
        except Exception as e:
            self.log_message(f"打开语言选择器失败: {e}")
            self._rebuild_language_combos(apply_config_selection=True)



    def _rebuild_language_combos(self, *, apply_config_selection: bool) -> None:
        """重建两个语言下拉框：源(自动+4槽位+更多) / 目标(4槽位+更多)。"""
        self.language_manager.rebuild_language_combos_advanced(
            self.source_lang_combo, 
            self.target_lang_combo, 
            apply_config_selection
        )

    def get_ocr_language_code(self, language_name):
        """将界面语言名称转换为 Tesseract OCR 语言代码"""
        lang_map = {
            "自动检测": "auto",   # 使用 OCRProcessor 默认的 languages 配置
            "英语": "eng",
            "日语": "jpn",
            "韩语": "kor",
            "中文": "chi_sim",   # 简体中文，需要 chi_sim.traineddata
        }
        return lang_map.get(language_name, "auto")
    
    def save_language_settings(self):
        """保存语言设置"""
        # 分别处理源语言和目标语言，确保每个语言都能独立保存
        
        # 源语言处理
        src_data = self.source_lang_combo.currentData()
        if isinstance(src_data, str) and src_data and src_data != "show_more" and not src_data.startswith("temp:"):
            self.config["source_language"] = src_data
            try:
                self.config_manager.set("translation", "source_language", src_data)
            except Exception as e:
                self.logger.debug(f"保存源语言失败: {e}")
        
        # 目标语言处理
        tgt_data = self.target_lang_combo.currentData()
        if isinstance(tgt_data, str) and tgt_data and tgt_data != "show_more" and not tgt_data.startswith("temp:"):
            self.config["target_language"] = tgt_data
            try:
                self.config_manager.set("translation", "target_language", tgt_data)
            except Exception as e:
                self.logger.debug(f"保存目标语言失败: {e}")
        
        # 保存配置
        try:
            self.config_manager.save_config()
        except Exception as e:
            self.logger.debug(f"保存配置文件失败: {e}")
        
        self.log_message("语言设置已保存")
        
    def save_hotkey_setting(self):
        """保存快捷键设置"""
        new_hotkey = self.hotkey_edit.text().strip() or 'b'
        # 更新内存配置和配置文件
        self.config['hotkey'] = new_hotkey
        self.config_manager.set('hotkey', 'screenshot', new_hotkey)
        self.log_message(f"快捷键设置已保存: {new_hotkey}")

        # 更新全局热键监听
        if self.hotkey_manager is not None:
            parsed = parse_hotkey_string(new_hotkey)
            self.hotkey_manager.set_hotkey(parsed)

        # 更新按钮上的快捷键信息
        self.update_translate_button_label()
        
    def save_ocr_settings(self):
        """保存OCR设置"""
        # OCR 识别模式（映射到 ocr_preprocess.enabled）
        enabled = True
        try:
            if hasattr(self, "ocr_mode_combo") and self.ocr_mode_combo is not None:
                enabled = (int(self.ocr_mode_combo.currentIndex()) == 1)
            else:
                enabled = bool(self.config.get("ocr_preprocess_enabled", True))
        except Exception:
            enabled = True

        # 写回内存配置 + 配置文件
        try:
            self.config["ocr_preprocess_enabled"] = bool(enabled)
        except Exception:
            pass
        try:
            if self.config_manager:
                self.config_manager.set("ocr_preprocess", "enabled", "true" if enabled else "false")
        except Exception:
            pass

        # 字芯颜色（复杂背景模式使用）
        core_color = None
        try:
            if hasattr(self, "ocr_core_color_edit") and self.ocr_core_color_edit is not None:
                core_color = self._normalize_hex_color(self.ocr_core_color_edit.text() or "")
        except Exception:
            core_color = None
        if not core_color:
            core_color = "#FFFFFF"

        try:
            self.config["ocr_core_color"] = core_color
        except Exception:
            pass
        try:
            if self.config_manager:
                self.config_manager.set("ocr", "core_color", core_color)
        except Exception:
            pass

        # 关键：模式变化后立刻刷新 UI（否则启动时是“识别文本模式”时切换不会显示字芯颜色控件）
        try:
            self._update_ocr_core_color_preview(core_color)
        except Exception:
            pass
        try:
            self._apply_ocr_mode_ui_state()
        except Exception:
            pass

        # OCR 组件已初始化时，立即应用配置（让下一次截图立刻生效）
        try:
            if self.ocr_processor:
                self.ocr_processor.apply_config(self.config_manager)
        except Exception:
            # 兜底：尽量把关键项打进去
            try:
                if self.ocr_processor:
                    self.ocr_processor.set_core_color(core_color)
            except Exception:
                pass
            try:
                if self.ocr_processor:
                    self.ocr_processor.preprocess_enabled = bool(enabled)
            except Exception:
                pass
    
    def save_overlay_settings(self):
        """保存悬浮窗设置"""
        self.config['overlay_opacity'] = self.opacity_spin.value()
        self.config['overlay_timeout'] = self.timeout_spin.value()
        self.config['overlay_auto_hide'] = self.auto_hide_check.isChecked()
        try:
            self.config_manager.set('overlay', 'opacity', str(self.config['overlay_opacity']))
            self.config_manager.set('overlay', 'timeout', str(self.config['overlay_timeout']))
            self.config_manager.set('overlay', 'auto_hide', 'true' if self.config['overlay_auto_hide'] else 'false')
        except Exception:
            self.config_manager.save_config()
        
        # 更新悬浮窗设置（如果已创建）
        if self.overlay:
            self.overlay.set_opacity(self.config['overlay_opacity'])
            self.overlay.set_timeout(self.config['overlay_timeout'])
            self.overlay.set_auto_hide(self.config['overlay_auto_hide'])
            
        self.log_message("悬浮窗设置已保存")

    def save_keep_capture_region_setting(self):
        enabled = False
        try:
            enabled = bool(self.keep_capture_region_check.isChecked())
        except Exception:
            enabled = bool(self.config.get("keep_capture_region", False))

        self.config["keep_capture_region"] = bool(enabled)
        try:
            self.config_manager.set("screenshot", "keep_capture_region", "true" if enabled else "false")
        except Exception:
            try:
                self.config_manager.save_config()
            except Exception:
                pass

        if not enabled:
            try:
                self._clear_locked_capture_region()
            except Exception:
                pass

        self.log_message("保留框选区域设置已保存")
        
    def save_all_settings(self):
        """保存所有设置"""
        self.save_language_settings()
        self.save_hotkey_setting()
        self.save_keep_capture_region_setting()
        self.save_ocr_settings()
        self.save_overlay_settings()
        self.log_message("所有设置已保存")
        QMessageBox.information(self, "保存成功", "所有设置已保存！")

    def _clear_locked_capture_region(self) -> None:
        self._locked_capture_rect = None
        if self._locked_region_frame is not None:
            try:
                self._locked_region_frame.hide()
            except Exception:
                pass
            try:
                self._locked_region_frame.deleteLater()
            except Exception:
                pass
            self._locked_region_frame = None

    def _ensure_locked_region_frame(self) -> None:
        if self._locked_capture_rect is None or self._locked_capture_rect.isNull():
            return

        if self._locked_region_frame is None:
            from src.ui.screenshot import RegionFrameOverlay
            self._locked_region_frame = RegionFrameOverlay()

        try:
            self._locked_region_frame.set_global_rect(QRect(self._locked_capture_rect))
        except Exception:
            self._locked_region_frame.set_global_rect(self._locked_capture_rect)
        try:
            self._locked_region_frame.show()
            self._locked_region_frame.raise_()
        except Exception:
            pass

    def _normalize_hex_color(self, value: str) -> str | None:
        """把用户输入规范化为 #RRGGBB；不合法返回 None。"""
        if not value:
            return None
        v = value.strip()
        # 允许输入 "FFFFFF" / "#FFFFFF" / "#fff"
        if re.fullmatch(r"[0-9a-fA-F]{6}", v):
            v = "#" + v
        if re.fullmatch(r"#[0-9a-fA-F]{3}", v):
            v = "#" + "".join([c * 2 for c in v[1:]])
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
            return None
        return v.upper()

    def _update_ocr_core_color_preview(self, hex_color: str) -> None:
        if not hasattr(self, "ocr_core_color_preview"):
            return
        c = self._normalize_hex_color(hex_color) or "#FFFFFF"
        self._set_scaled_stylesheet(
            self.ocr_core_color_preview,
            f"background-color: {c}; border: 1px solid rgba(120,120,120,180); border-radius: 3px;",
        )

    def _apply_ocr_mode_ui_state(self) -> None:
        """
        根据 OCR 识别模式调整 UI：
        - 识别文本模式：隐藏字芯颜色相关设置
        - 复杂背景模式：显示字芯颜色相关设置
        """
        enabled = True
        try:
            if hasattr(self, "ocr_mode_combo") and self.ocr_mode_combo is not None:
                enabled = (int(self.ocr_mode_combo.currentIndex()) == 1)
            else:
                enabled = bool(self.config.get("ocr_preprocess_enabled", True))
        except Exception:
            enabled = True

        # 写回内存配置，方便其他地方读取
        self.config["ocr_preprocess_enabled"] = bool(enabled)

        # 字芯颜色控件：仅在“复杂背景模式”下有意义
        try:
            if hasattr(self, "ocr_core_color_group") and self.ocr_core_color_group is not None:
                self.ocr_core_color_group.setVisible(bool(enabled))
        except Exception:
            pass

        try:
            if hasattr(self, "ocr_core_color_edit"):
                self.ocr_core_color_edit.setEnabled(bool(enabled))
        except Exception:
            pass
        try:
            if hasattr(self, "ocr_core_color_pick_btn"):
                self.ocr_core_color_pick_btn.setEnabled(bool(enabled))
        except Exception:
            pass
        try:
            if hasattr(self, "ocr_core_color_dropper_btn"):
                self.ocr_core_color_dropper_btn.setEnabled(bool(enabled))
        except Exception:
            pass
        try:
            if hasattr(self, "ocr_core_color_preview"):
                # 预览仍可显示，但在禁用时做弱化提示
                self.ocr_core_color_preview.setEnabled(bool(enabled))
        except Exception:
            pass

    def _parse_custom_colors(self, raw: str) -> list[str]:
        """解析配置里的自定义颜色列表（逗号分隔 #RRGGBB），最多返回 16 个。"""
        if not raw:
            return []
        items = []
        for part in str(raw).split(","):
            v = self._normalize_hex_color(part)
            if v:
                items.append(v)
            if len(items) >= 16:
                break
        return items

    def _serialize_custom_colors(self, colors: list[str]) -> str:
        """将自定义颜色列表序列化为配置字符串（逗号分隔 #RRGGBB）。"""
        out: list[str] = []
        for c in colors or []:
            v = self._normalize_hex_color(c)
            if v:
                out.append(v)
            if len(out) >= 16:
                break
        return ",".join(out)

    def _apply_qt_custom_colors(self, colors: list[str]) -> None:
        """把自定义颜色写入 Qt 的全局自定义颜色槽位（0~15）。"""
        try:
            for i, hex_c in enumerate(colors[:16]):
                try:
                    QColorDialog.setCustomColor(i, QColor(hex_c))
                except Exception:
                    # 兼容某些 PyQt6 绑定差异
                    pass
        except Exception:
            pass

    def _read_qt_custom_colors(self) -> list[str]:
        """从 Qt 的全局自定义颜色槽位读取 0~15 个颜色，返回 #RRGGBB 列表。"""
        colors: list[str] = []
        try:
            for i in range(16):
                try:
                    qc = QColorDialog.customColor(i)
                except Exception:
                    qc = None
                try:
                    if qc is not None and isinstance(qc, QColor) and qc.isValid():
                        colors.append(qc.name().upper())
                except Exception:
                    pass
        except Exception:
            pass
        return colors

    def _save_ocr_custom_colors(self, colors: list[str]) -> None:
        """保存自定义颜色槽位到配置（ocr.custom_colors）。"""
        try:
            s = self._serialize_custom_colors(colors)
            self.config["ocr_custom_colors"] = s
            if self.config_manager:
                self.config_manager.set("ocr", "custom_colors", s)
        except Exception:
            pass

    def choose_ocr_core_color(self):
        """弹出颜色选择器，设置 OCR 字芯颜色。"""
        current = self._normalize_hex_color(self.ocr_core_color_edit.text()) or "#FFFFFF"
        qcolor = QColor(current)

        # 恢复上次的“自定义颜色”槽位（最多 16 个）
        saved_custom = self._parse_custom_colors(self.config.get("ocr_custom_colors", ""))
        if saved_custom:
            self._apply_qt_custom_colors(saved_custom)

        # 关键：不要用 Windows 原生颜色对话框（它会跟随系统语言，无法由应用强制汉化）
        # 改用 Qt 自带对话框 + Qt 翻译包（见 main.py 里 installTranslator），即可显示中文按钮/标签。
        dlg = QColorDialog(qcolor, self)
        dlg.setWindowTitle("选择字芯颜色")
        try:
            dlg.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
        except Exception:
            pass
        try:
            dlg.setCurrentColor(qcolor)
        except Exception:
            pass

        if dlg.exec():
            picked = dlg.currentColor()
            if not picked or not picked.isValid():
                return

            # 保存当前的“自定义颜色”槽位（用户可能在对话框里点了“添加到自定义颜色”）
            self._save_ocr_custom_colors(self._read_qt_custom_colors())

            hex_color = picked.name().upper()  # #RRGGBB
            self.ocr_core_color_edit.setText(hex_color)
            self._update_ocr_core_color_preview(hex_color)
            self.save_ocr_settings()

    def pick_ocr_core_color_with_eyedropper(self):
        """用吸管从屏幕取色，设置 OCR 字芯颜色。"""
        try:
            if self._eyedropper is None:
                from src.ui.eyedropper import EyedropperOverlay
                self._eyedropper = EyedropperOverlay()
                self._eyedropper.color_picked.connect(self._on_ocr_core_color_picked)
                self._eyedropper.cancelled.connect(lambda: self.log_message("吸管取色已取消"))
            self._eyedropper.start()
        except Exception as e:
            self.log_message(f"吸管启动失败: {e}")
            QMessageBox.warning(self, "吸管失败", f"吸管启动失败: {e}")

    def _on_ocr_core_color_picked(self, hex_color: str):
        hex_color = self._normalize_hex_color(hex_color) or "#FFFFFF"
        self.ocr_core_color_edit.setText(hex_color)
        self._update_ocr_core_color_preview(hex_color)
        self.save_ocr_settings()
        self.log_message(f"OCR字芯颜色已取色: {hex_color}")
            
    def run_shortcut_creator(self):
        """运行快捷方式创建逻辑"""
        installer = getattr(self, "installer", None)
        if installer is None:
            QMessageBox.warning(self, "不可用", "当前版本未启用快捷方式创建功能。")
            return
        try:
            installer.create_all_shortcuts()
            QMessageBox.information(self, "成功", "已在桌面和开始菜单创建快捷方式！")
        except Exception as e:
            QMessageBox.warning(self, "失败", f"创建快捷方式失败: {e}")

    def show_about(self):
        """兼容旧入口：跳转到如何操作"""
        self.show_how_to()
        
    def log_message(self, message):
        """记录日志消息"""
        import datetime
        try:
            logging.info(str(message))
        except Exception:
            pass
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {message}"

        # 检查 log_text 属性是否存在，避免在控件创建之前调用导致错误
        if not hasattr(self, 'log_text'):
            return
        
        # 添加到日志文本框（强制纯文本；允许 message 内部包含换行，显示完整内容）
        try:
            self.log_text.moveCursor(QTextCursor.MoveOperation.End)
            self.log_text.insertPlainText(log_entry + "\n")
        except Exception as e:
            # 兜底：append 可能会按富文本解析，因此只在异常时使用
            try:
                self.log_text.append(log_entry)
            except Exception:
                pass

        # 自动滚动到底部
        try:
            scroll_bar = self.log_text.verticalScrollBar()
            if scroll_bar:
                scroll_bar.setValue(scroll_bar.maximum())
        except Exception:
            pass

    def closeEvent(self, event):
        """窗口关闭事件"""
        if self.is_translating:
            reply = QMessageBox.question(
                self, "确认退出",
                "翻译服务正在运行，确定要退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return
                
        # 停止翻译服务
        if self.is_translating:
            self.toggle_translation()

        try:
            if getattr(self, "_hook_running", False) or (
                getattr(self, "_hook_scan_thread", None) is not None and self._hook_scan_thread.isRunning()
            ):
                self._stop_hook_service()
        except Exception:
            pass

        try:
            self._terminate_hook_agent_process()
        except Exception:
            pass
            
        # 保存配置
        self.config_manager.save_config()
        
        # 关闭时重置临时语言
        self.language_manager.reset_temp_language_on_close()
        
        # 清理OCR临时文件目录
        try:
            ocr_temp_dir = Path(tempfile.gettempdir()) / "screen_translator_ocr"
            if ocr_temp_dir.exists():
                shutil.rmtree(ocr_temp_dir, ignore_errors=True)
        except Exception:
            pass

        try:
            self._unity_memory_scan_stop()
        except Exception:
            pass
        try:
            self._game_stop_realtime_translation()
        except Exception:
            pass

        event.accept()
        
    def showEvent(self, event):
        """窗口显示事件"""
        super().showEvent(event)
        # show 之后 windowHandle 更稳定：安装跨屏监听，并按“窗口实际所在屏幕”应用缩放
        try:
            self._ensure_screen_tracking()
            self._update_scale_factor_for_current_screen(force=True)
            # 每次启动：按当前屏幕重新计算并设置一次初始大小（不依赖上次窗口状态）
            if not self._startup_scale_applied:
                self._startup_scale_applied = True
                self._apply_startup_window_size_for_screen(self._get_current_window_screen())
        except Exception:
            pass

        try:
            if not self._open_animation_played:
                self._open_animation_played = True
                self.setWindowOpacity(0.0)
                anim = QPropertyAnimation(self, b"windowOpacity", self)
                anim.setDuration(240)
                anim.setStartValue(0.0)
                anim.setEndValue(1.0)
                anim.setEasingCurve(QEasingCurve.Type.OutCubic)
                self._open_animation = anim
                anim.start()
        except Exception:
            pass

    def moveEvent(self, event):
        """窗口移动事件：用于拖动跨屏时自动适配缩放（节流）"""
        try:
            if self._scale_apply_debounce is not None:
                # 拖动过程中会非常频繁，这里做个轻量节流
                self._scale_apply_debounce.start(120)
        except Exception:
            pass
        super().moveEvent(event)
