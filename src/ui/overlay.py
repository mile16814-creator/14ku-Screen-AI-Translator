"""
悬浮翻译窗 - 显示OCR识别和翻译结果的半透明窗口
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QApplication, QTextEdit, QProgressBar
)
from PyQt6.QtCore import Qt, QEvent, QTimer, QPropertyAnimation, QEasingCurve, pyqtProperty, pyqtSignal, QPoint, QRect
from PyQt6.QtGui import QFont, QColor, QPainter, QBrush, QPen, QCursor


class TranslationOverlay(QWidget):
    """悬浮翻译窗类，显示OCR识别和翻译结果"""
    
    # 定义重译/翻译信号：text, disable_preprocess
    # - OCR 模式：disable_preprocess=False（保留既有“优化/清洗”逻辑）
    # - 文本模式：disable_preprocess=True（用户输入原样送翻译器，不做任何预处理）
    retranslate_requested = pyqtSignal(str, bool)

    def __init__(self):
        super().__init__()
        
        # 窗口属性
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.X11BypassWindowManagerHint
        )
        
        # 设置透明背景
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # 初始化变量
        self.opacity = 0.9
        self.timeout = 10  # 默认显示10秒
        self.auto_hide = True
        # overlay 当前工作模式：ocr / text
        self._mode: str = "ocr"
        # 悬浮窗翻译文字颜色（字芯颜色）
        self.text_color = "#FFFFFF"
        self.hide_timer = QTimer()
        self.hide_timer.timeout.connect(self.hide_overlay)
        
        # 初始化UI
        self.init_ui()
        
        # 设置默认大小和位置
        self.resize(420, 308)
        self.move_to_corner()

        self.setMinimumSize(320, 248)

        self._resize_margin = 8
        self._resizing = False
        self._resize_edges: tuple[bool, bool, bool, bool] = (False, False, False, False)
        self._resize_start_geom = QRect()
        self._resize_start_global = QPoint()

        self.setMouseTracking(True)
        self.installEventFilter(self)
        try:
            for w in self.findChildren(QWidget):
                try:
                    w.setMouseTracking(True)
                except Exception:
                    pass
                try:
                    w.installEventFilter(self)
                except Exception:
                    pass
        except Exception:
            pass
        
    def init_ui(self):
        """初始化用户界面"""
        # 创建主框架
        self.main_frame = QFrame(self)
        self.main_frame.setObjectName("mainFrame")
        self.main_frame.setStyleSheet("""
            QFrame#mainFrame {
                background-color: rgba(30, 30, 30, 230);
                border: 2px solid rgba(100, 100, 100, 200);
                border-radius: 10px;
            }
        """)
        
        # 主布局
        main_layout = QVBoxLayout(self.main_frame)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)
        
        # 1. 标题栏
        title_layout = QHBoxLayout()
        
        self.title_label = QLabel("翻译结果")
        title_font = QFont()
        title_font.setPointSize(12)
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        self.title_label.setStyleSheet("color: #4CAF50;")
        title_layout.addWidget(self.title_label)
        
        # 语言标签
        self.language_label = QLabel("")
        self.language_label.setStyleSheet("color: #888888; font-size: 10px;")
        title_layout.addWidget(self.language_label)
        
        # 关闭按钮
        self.close_button = QPushButton("×")
        self.close_button.setFixedSize(20, 20)
        self.close_button.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 100, 100, 150);
                color: white;
                border: none;
                border-radius: 10px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(255, 50, 50, 200);
            }
        """)
        self.close_button.clicked.connect(self.hide_overlay)
        title_layout.addWidget(self.close_button)
        
        main_layout.addLayout(title_layout)
        
        # 2. 原文区域
        original_group = QFrame()
        original_group.setStyleSheet("""
            QFrame {
                background-color: rgba(40, 40, 40, 180);
                border: 1px solid rgba(80, 80, 80, 150);
                border-radius: 5px;
                padding: 5px;
            }
        """)
        
        original_layout = QVBoxLayout(original_group)
        
        original_title = QLabel("原文")
        original_title.setStyleSheet("color: #888888; font-size: 10px;")
        original_layout.addWidget(original_title)
        
        # 原文文本框（可编辑）
        self.original_text = QTextEdit()
        self.original_text.setReadOnly(False)
        self.original_text.setStyleSheet("""
            QTextEdit {
                background: rgba(30, 30, 30, 100);
                color: #CCCCCC;
                font-size: 11px;
                border: 1px solid rgba(100, 100, 100, 50);
                border-radius: 3px;
            }
            QTextEdit:focus {
                border: 1px solid rgba(76, 175, 80, 150);
            }
        """)
        self.original_text.setMinimumHeight(80)
        original_layout.addWidget(self.original_text)
        
        main_layout.addWidget(original_group)
        
        # 3. 翻译结果区域
        translation_group = QFrame()
        translation_group.setStyleSheet("""
            QFrame {
                background-color: rgba(50, 50, 50, 180);
                border: 1px solid rgba(100, 100, 100, 150);
                border-radius: 5px;
                padding: 5px;
            }
        """)
        
        translation_layout = QVBoxLayout(translation_group)
        
        translation_title = QLabel("翻译")
        translation_title.setStyleSheet("color: #888888; font-size: 10px;")
        translation_layout.addWidget(translation_title)
        
        # 进度条容器（翻译过程中显示）
        self.progress_container = QFrame()
        progress_layout = QVBoxLayout(self.progress_container)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(2)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background: rgba(60, 60, 60, 150);
                border: 1px solid rgba(100, 100, 100, 100);
                border-radius: 3px;
                text-align: center;
                color: #AAAAAA;
                font-size: 10px;
                height: 16px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #4CAF50, stop:1 #8BC34A);
                border-radius: 2px;
            }
        """)
        self.progress_bar.setFixedHeight(16)
        progress_layout.addWidget(self.progress_bar)
        
        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("color: #666666; font-size: 9px;")
        progress_layout.addWidget(self.progress_label)
        
        # 翻译结果文本框（翻译完成后显示）
        self.translation_text = QTextEdit()
        self.translation_text.setReadOnly(True)
        self._apply_translation_text_style()
        self.translation_text.setMinimumHeight(80)
        
        # 初始状态：显示进度条，隐藏翻译结果
        self.translation_text.setVisible(False)
        
        translation_layout.addWidget(self.progress_container)
        translation_layout.addWidget(self.translation_text)
        
        main_layout.addWidget(translation_group)
        
        # 4. 底部按钮
        button_layout = QHBoxLayout()
        
        # 复制按钮
        self.copy_button = QPushButton("复制翻译")
        self.copy_button.setStyleSheet("""
            QPushButton {
                background-color: rgba(76, 175, 80, 150);
                color: white;
                border: none;
                border-radius: 5px;
                padding: 5px 10px;
                font-size: 10px;
            }
            QPushButton:hover {
                background-color: rgba(76, 175, 80, 200);
            }
        """)
        self.copy_button.clicked.connect(self.copy_translation)
        button_layout.addWidget(self.copy_button)
        
        # 固定按钮
        self.pin_button = QPushButton("固定")
        self.pin_button.setCheckable(True)
        self.pin_button.setStyleSheet("""
            QPushButton {
                background-color: rgba(100, 100, 100, 150);
                color: white;
                border: none;
                border-radius: 5px;
                padding: 5px 10px;
                font-size: 10px;
            }
            QPushButton:checked {
                background-color: rgba(33, 150, 243, 200);
            }
            QPushButton:hover {
                background-color: rgba(150, 150, 150, 200);
            }
        """)
        self.pin_button.toggled.connect(self.toggle_pin)
        button_layout.addWidget(self.pin_button)
        
        # 重新翻译按钮
        self.retranslate_button = QPushButton("重新翻译")
        self.retranslate_button.setStyleSheet("""
            QPushButton {
                background-color: rgba(33, 150, 243, 150);
                color: white;
                border: none;
                border-radius: 5px;
                padding: 5px 10px;
                font-size: 10px;
            }
            QPushButton:hover {
                background-color: rgba(33, 150, 243, 200);
            }
            QPushButton:pressed {
                background-color: rgba(25, 118, 210, 200);
            }
        """)
        self.retranslate_button.clicked.connect(self.request_retranslate)
        button_layout.addWidget(self.retranslate_button)
        
        main_layout.addLayout(button_layout)
        
        # 设置主框架布局
        self.setLayout(QVBoxLayout())
        self.layout().addWidget(self.main_frame)
        self.layout().setContentsMargins(0, 0, 0, 0)

    def _apply_translation_text_style(self):
        """根据当前设置应用翻译文本样式（用于动态更新颜色等）。"""
        try:
            c = QColor(self.text_color)
            if not c.isValid():
                c = QColor("#FFFFFF")
        except Exception:
            c = QColor("#FFFFFF")
        self.translation_text.setStyleSheet(f"""
            QTextEdit {{
                background: transparent;
                color: {c.name().upper()};
                font-size: 12px;
                font-weight: bold;
                border: none;
            }}
        """)

    def set_text_color(self, color: str):
        """设置翻译文字颜色（字芯颜色），支持 #RRGGBB。"""
        if not isinstance(color, str):
            return
        v = color.strip()
        try:
            c = QColor(v)
            if not c.isValid():
                return
        except Exception:
            return
        self.text_color = c.name().upper()
        # 若 UI 尚未创建，则等 init_ui 后再应用
        if hasattr(self, "translation_text") and self.translation_text is not None:
            self._apply_translation_text_style()
        
    def move_to_corner(self):
        """将窗口移动到屏幕右下角"""
        # 以当前鼠标所在屏幕为准（多屏更合理）；兜底 primaryScreen
        try:
            from PyQt6.QtGui import QGuiApplication
            screen = QGuiApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        except Exception:
            screen = QApplication.primaryScreen()

        screen_geometry = screen.availableGeometry() if screen is not None else QApplication.primaryScreen().availableGeometry()
        window_width = self.width()
        window_height = self.height()
        
        x = screen_geometry.right() - window_width - 20
        y = screen_geometry.bottom() - window_height - 20
        
        self.move(x, y)
        
    def show_ocr_result(self, original_text, rect):
        """显示OCR识别结果（立即显示，翻译在后台进行）"""
        self._mode = "ocr"
        # 设置原文内容
        self.original_text.setPlainText(original_text)
        
        # 显示进度条，隐藏翻译结果
        self.progress_container.setVisible(True)
        self.translation_text.setVisible(False)
        
        # 设置语言标签为OCR状态
        self.language_label.setText("OCR完成，翻译中...")
        
        # 重置重新翻译按钮状态
        self.retranslate_button.setText("重新翻译")
        self.retranslate_button.setEnabled(False)  # 翻译完成前禁用

        # 调整窗口大小以适应内容
        self.adjust_size()
        
        # 移动到截图区域附近（如果可能）
        self.move_near_rect(rect)
        
        # 显示窗口
        self.show()
        
        # 启动淡入动画
        self.fade_in()
        
        # 如果启用自动隐藏，启动定时器（但翻译完成后会重置）
        if self.auto_hide and not self.pin_button.isChecked():
            self.hide_timer.start(self.timeout * 1000)

    def show_text_mode(self, title_text: str | None = None, hint_text: str | None = None):
        """进入输入模式：用户手动输入/粘贴文本进行翻译（不走 OCR，也不做预处理）。"""
        self._mode = "input"

        # 文案与状态
        try:
            title = str(title_text) if title_text is not None else "输入模式"
            self.title_label.setText(title)
        except Exception:
            pass
        try:
            hint = str(hint_text) if hint_text is not None else "输入模式：输入后点击“翻译”"
            self.language_label.setText(hint)
        except Exception:
            pass

        # 清空内容，给出提示
        self.original_text.setPlainText("")
        
        # 显示翻译结果区域，隐藏进度条
        self.progress_container.setVisible(False)
        self.translation_text.setVisible(True)
        self.translation_text.setPlainText("请输入原文，然后点击“翻译”。")

        # 按钮状态
        self.retranslate_button.setText("翻译")
        self.retranslate_button.setEnabled(True)

        # 位置/显示
        self.adjust_size()
        self.move_to_corner()
        self.show()
        self.fade_in()

        # 文本模式更像一个小工具面板：默认不自动隐藏（避免用户输入时消失）
        if self.hide_timer.isActive():
            self.hide_timer.stop()
            
    def show_translation(self, original_text, translated_text, source_lang, target_lang):
        """显示完整的翻译结果（同步模式）"""
        # 设置文本内容
        self.original_text.setPlainText(original_text)
        
        # 显示翻译结果，隐藏进度条
        self.progress_container.setVisible(False)
        self.translation_text.setVisible(True)
        self.translation_text.setPlainText(translated_text)
        
        # 重置重新翻译按钮状态
        self.retranslate_button.setText("重新翻译")
        self.retranslate_button.setEnabled(True)

        # 设置语言标签
        self.language_label.setText(f"{source_lang} → {target_lang}")
        
        # 调整窗口大小以适应内容
        self.adjust_size()
        
        # 移动到屏幕右下角
        self.move_to_corner()
        
        # 显示窗口
        self.show()
        
        # 启动淡入动画
        self.fade_in()
        
        # 如果启用自动隐藏，启动定时器
        if self.auto_hide and not self.pin_button.isChecked():
            self.hide_timer.start(self.timeout * 1000)
            
    def update_translation_result(self, translated_text):
        """异步更新翻译结果"""
        # 隐藏进度条，显示翻译结果
        self.progress_container.setVisible(False)
        self.translation_text.setVisible(True)
        
        # 更新翻译文本
        self.translation_text.setPlainText(translated_text)
        
        # 启用重新翻译按钮
        self.retranslate_button.setEnabled(True)
        
        # 更新语言标签为完成状态
        if self.language_label.text().startswith("OCR完成"):
            self.language_label.setText("翻译完成")
        
        # 重置自动隐藏定时器
        if self.auto_hide and not self.pin_button.isChecked():
            self.hide_timer.stop()
            self.hide_timer.start(self.timeout * 1000)
            
    def update_translation_progress(self, progress: int, status_text: str):
        """更新翻译进度"""
        # 确保进度条可见，翻译文本隐藏
        self.progress_container.setVisible(True)
        self.translation_text.setVisible(False)
        
        # 更新进度条和标签
        self.progress_bar.setValue(progress)
        self.progress_label.setText(status_text)
            
    def move_near_rect(self, rect):
        """将窗口移动到截图区域附近"""
        # 以截图区域中心所在的屏幕为准（多屏 + 非(0,0)原点）
        try:
            from PyQt6.QtGui import QGuiApplication
            screen = QGuiApplication.screenAt(rect.center()) or QApplication.primaryScreen()
        except Exception:
            screen = QApplication.primaryScreen()

        screen_geometry = screen.availableGeometry() if screen is not None else QApplication.primaryScreen().availableGeometry()
        window_width = self.width()
        window_height = self.height()
        
        # 尝试将窗口放在截图区域右侧，如果放不下则放在下方
        x = rect.right() + 10
        y = rect.top()
        
        # 如果右侧空间不足，放在左侧
        if x + window_width > screen_geometry.right():
            x = rect.left() - window_width - 10
            
        # 如果上下空间不足，调整位置
        if y + window_height > screen_geometry.bottom():
            y = screen_geometry.bottom() - window_height - 20
        elif y < screen_geometry.top():
            y = screen_geometry.top() + 20
            
        # 确保窗口在屏幕内
        x = max(screen_geometry.left() + 20, min(x, screen_geometry.right() - window_width - 20))
        y = max(screen_geometry.top() + 20, min(y, screen_geometry.bottom() - window_height - 20))
        
        self.move(x, y)
            
    def adjust_size(self):
        return
        
    def fade_in(self):
        """淡入动画"""
        self.setWindowOpacity(0)
        self.show()
        
        self.animation = QPropertyAnimation(self, b"windowOpacity")
        self.animation.setDuration(300)  # 300毫秒
        self.animation.setStartValue(0)
        self.animation.setEndValue(self.opacity)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.start()
        
    def fade_out(self):
        """淡出动画"""
        self.animation = QPropertyAnimation(self, b"windowOpacity")
        self.animation.setDuration(300)  # 300毫秒
        self.animation.setStartValue(self.opacity)
        self.animation.setEndValue(0)
        self.animation.setEasingCurve(QEasingCurve.Type.InCubic)
        self.animation.finished.connect(self.hide)
        self.animation.start()
        
    def hide_overlay(self):
        """隐藏悬浮窗"""
        if self.hide_timer.isActive():
            self.hide_timer.stop()
            
        self.fade_out()
        
    def toggle_pin(self, pinned):
        """切换固定状态"""
        if pinned:
            # 固定时停止自动隐藏定时器
            if self.hide_timer.isActive():
                self.hide_timer.stop()
            self.pin_button.setText("已固定")
        else:
            # 取消固定时重新启动定时器（如果启用自动隐藏）
            if self.auto_hide:
                self.hide_timer.start(self.timeout * 1000)
            self.pin_button.setText("固定")
            
    def copy_translation(self):
        """复制翻译文本到剪贴板"""
        clipboard = QApplication.clipboard()
        clipboard.setText(self.translation_text.toPlainText())
        
        # 显示复制成功的反馈
        self.copy_button.setText("已复制!")
        QTimer.singleShot(1000, lambda: self.copy_button.setText("复制翻译"))
        
    def request_retranslate(self):
        """发送重新翻译请求"""
        raw_text = self.original_text.toPlainText()
        # 仅用于判断"是否为空"；不修改用户输入内容
        if isinstance(raw_text, str) and raw_text.strip():
            # 视觉反馈
            self.retranslate_button.setText("正在翻译...")
            self.retranslate_button.setEnabled(False)
            # 显示进度条，隐藏翻译结果
            self.progress_container.setVisible(True)
            self.translation_text.setVisible(False)
            disable_preprocess = (self._mode == "input")
            # 为兼容旧行为：OCR/重译场景仍默认 trim；文本模式严格原样发送
            text_to_send = raw_text if disable_preprocess else raw_text.strip()
            self.retranslate_requested.emit(text_to_send, disable_preprocess)

    def set_opacity(self, opacity):
        """设置窗口透明度"""
        self.opacity = max(0.1, min(1.0, opacity))
        self.setWindowOpacity(self.opacity)
        
    def set_timeout(self, timeout):
        """设置自动隐藏超时时间（秒）"""
        self.timeout = max(1, min(60, timeout))
        
    def set_auto_hide(self, auto_hide):
        """设置是否自动隐藏"""
        self.auto_hide = auto_hide
        
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            local_pos = event.position().toPoint()
            if self._try_begin_resize_from_local_pos(local_pos, event.globalPosition().toPoint()):
                event.accept()
                return
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            
    def mouseMoveEvent(self, event):
        if self._resizing and event.buttons() == Qt.MouseButton.LeftButton:
            self._perform_resize(event.globalPosition().toPoint())
            event.accept()
            return

        if event.buttons() == Qt.MouseButton.LeftButton and hasattr(self, 'drag_position'):
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()
            return

        if event.buttons() == Qt.MouseButton.NoButton:
            self._update_cursor_for_local_pos(event.position().toPoint())
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._resizing:
                self._end_resize()
                event.accept()
                return
            if hasattr(self, 'drag_position'):
                try:
                    delattr(self, 'drag_position')
                except Exception:
                    pass
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def eventFilter(self, watched, event):
        try:
            et = event.type()
        except Exception:
            return False

        if et == QEvent.Type.MouseMove:
            try:
                gp = event.globalPosition().toPoint()
                lp = self.mapFromGlobal(gp)
            except Exception:
                return False
            if self._resizing and getattr(event, "buttons", lambda: Qt.MouseButton.NoButton)() == Qt.MouseButton.LeftButton:
                self._perform_resize(gp)
                return True
            if getattr(event, "buttons", lambda: Qt.MouseButton.NoButton)() == Qt.MouseButton.NoButton:
                self._update_cursor_for_local_pos(lp)
                return False
            return False

        if et == QEvent.Type.MouseButtonPress:
            try:
                if event.button() != Qt.MouseButton.LeftButton:
                    return False
                gp = event.globalPosition().toPoint()
                lp = self.mapFromGlobal(gp)
            except Exception:
                return False
            if self._try_begin_resize_from_local_pos(lp, gp):
                return True
            return False

        if et == QEvent.Type.MouseButtonRelease:
            try:
                if event.button() != Qt.MouseButton.LeftButton:
                    return False
            except Exception:
                return False
            if self._resizing:
                self._end_resize()
                return True
            return False

        return False

    def _hit_test_resize_edges(self, local_pos: QPoint) -> tuple[bool, bool, bool, bool]:
        x = int(local_pos.x())
        y = int(local_pos.y())
        w = int(self.width())
        h = int(self.height())
        m = int(self._resize_margin)
        left = x <= m
        right = x >= (w - m)
        top = y <= m
        bottom = y >= (h - m)
        return left, right, top, bottom

    def _cursor_for_edges(self, edges: tuple[bool, bool, bool, bool]):
        left, right, top, bottom = edges
        if (left and top) or (right and bottom):
            return Qt.CursorShape.SizeFDiagCursor
        if (right and top) or (left and bottom):
            return Qt.CursorShape.SizeBDiagCursor
        if left or right:
            return Qt.CursorShape.SizeHorCursor
        if top or bottom:
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def _update_cursor_for_local_pos(self, local_pos: QPoint) -> None:
        try:
            edges = self._hit_test_resize_edges(local_pos)
            cur = self._cursor_for_edges(edges)
            self.setCursor(QCursor(cur))
        except Exception:
            pass

    def _try_begin_resize_from_local_pos(self, local_pos: QPoint, global_pos: QPoint) -> bool:
        edges = self._hit_test_resize_edges(local_pos)
        if not any(edges):
            return False
        self._resizing = True
        self._resize_edges = edges
        self._resize_start_geom = self.geometry()
        self._resize_start_global = global_pos
        try:
            self.setCursor(QCursor(self._cursor_for_edges(edges)))
        except Exception:
            pass
        return True

    def _perform_resize(self, global_pos: QPoint) -> None:
        if not self._resizing:
            return
        left, right, top, bottom = self._resize_edges
        start = self._resize_start_geom
        dx = int(global_pos.x() - self._resize_start_global.x())
        dy = int(global_pos.y() - self._resize_start_global.y())

        new_left = start.left() + (dx if left else 0)
        new_right = start.right() + (dx if right else 0)
        new_top = start.top() + (dy if top else 0)
        new_bottom = start.bottom() + (dy if bottom else 0)

        min_w = int(self.minimumWidth())
        min_h = int(self.minimumHeight())

        if (new_right - new_left + 1) < min_w:
            if left:
                new_left = new_right - min_w + 1
            else:
                new_right = new_left + min_w - 1

        if (new_bottom - new_top + 1) < min_h:
            if top:
                new_top = new_bottom - min_h + 1
            else:
                new_bottom = new_top + min_h - 1

        try:
            self.setGeometry(QRect(QPoint(new_left, new_top), QPoint(new_right, new_bottom)))
        except Exception:
            pass

    def _end_resize(self) -> None:
        self._resizing = False
        self._resize_edges = (False, False, False, False)
        try:
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        except Exception:
            pass
            
    def enterEvent(self, event):
        """鼠标进入事件，暂停自动隐藏"""
        if self.auto_hide and not self.pin_button.isChecked():
            if self.hide_timer.isActive():
                self.hide_timer.stop()
                
    def leaveEvent(self, event):
        """鼠标离开事件，恢复自动隐藏"""
        if self.auto_hide and not self.pin_button.isChecked():
            if not self.hide_timer.isActive():
                self.hide_timer.start(self.timeout * 1000)
                
    def paintEvent(self, event):
        """绘制事件，添加阴影效果"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # 绘制阴影
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(0, 0, 0, 50)))
        painter.drawRoundedRect(
            self.main_frame.geometry().adjusted(-5, -5, 5, 5),
            15, 15
        )
