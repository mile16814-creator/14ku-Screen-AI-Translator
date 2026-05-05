from __future__ import annotations

from PyQt6.QtCore import Qt, QRect, QSize
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class HowToPage(QWidget):
    def __init__(self, scale_size, close_callback=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scale_size = scale_size
        self._close_callback = close_callback
        self._hotkey = "B"
        self._step2_label: QLabel | None = None
        self._build_ui()
        self._apply_styles()
        self.refresh_content()

    def update_hotkey(self, hotkey: str) -> None:
        self._hotkey = str(hotkey or "B").upper()
        self.refresh_content()

    def refresh_content(self) -> None:
        if self._step2_label is not None:
            self._step2_label.setText(
                f"先在主界面点击“启动翻译”，再按截图快捷键（当前为 {self._hotkey}）。程序会进入截图模式，此时拖动鼠标框选需要翻译的区域。"
            )

    def sizeHint(self) -> QSize:
        return QSize(self._scale_size(900), self._scale_size(680))

    def minimumSizeHint(self) -> QSize:
        return QSize(self._scale_size(860), self._scale_size(640))

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(scroll)

        body = QWidget()
        scroll.setWidget(body)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(
            self._scale_size(28),
            self._scale_size(22),
            self._scale_size(28),
            self._scale_size(22),
        )
        body_layout.setSpacing(self._scale_size(18))

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(self._scale_size(12))

        close_button = QPushButton("关闭页面")
        close_button.setObjectName("howToBackButton")
        if self._close_callback is not None:
            close_button.clicked.connect(self._close_callback)
        top_row.addWidget(close_button, 0, Qt.AlignmentFlag.AlignLeft)
        top_row.addStretch(1)
        body_layout.addLayout(top_row)

        hero = QFrame()
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(0, 0, 0, 0)
        hero_layout.setSpacing(self._scale_size(20))

        hero_icon = QLabel()
        hero_icon.setPixmap(self._make_circle_symbol("i", "#2563EB", "#FFFFFF", self._scale_size(88)))
        hero_icon.setFixedSize(self._scale_size(88), self._scale_size(88))
        hero_layout.addWidget(hero_icon, 0, Qt.AlignmentFlag.AlignTop)

        hero_text_layout = QVBoxLayout()
        hero_text_layout.setContentsMargins(0, self._scale_size(4), 0, 0)
        hero_text_layout.setSpacing(self._scale_size(6))

        title = QLabel("如何操作")
        title.setObjectName("howToHeroTitle")
        hero_text_layout.addWidget(title)

        subtitle = QLabel("使用本软件进行截图翻译及相关设置的操作指南")
        subtitle.setObjectName("howToHeroSubtitle")
        subtitle.setWordWrap(True)
        hero_text_layout.addWidget(subtitle)
        hero_text_layout.addStretch(1)
        hero_layout.addLayout(hero_text_layout, 1)
        body_layout.addWidget(hero)

        card = QFrame()
        card.setObjectName("howToCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(
            self._scale_size(24),
            self._scale_size(18),
            self._scale_size(24),
            self._scale_size(18),
        )
        card_layout.setSpacing(self._scale_size(16))

        self._add_section_title(card_layout, "一、开始前先看这里")
        self._add_step(card_layout, 1, "主界面左上角菜单可以切换到主界面、系统状态、历史记录、翻译词库、智能复用等页面。系统托盘菜单也可以打开主窗口、启动/停止翻译、进入输入模式或退出程序。")
        self._step2_label = self._add_step(card_layout, 2, "")
        self._add_step(card_layout, 3, "如果程序仍在初始化，启动翻译按钮会显示“翻译器正在初始化”或相关提示。此时请先等待，也可以打开“系统状态”查看 OCR、Tesseract、模型和资源占用情况。")
        self._add_step(card_layout, 4, "首次启动截图翻译时，如果本机没有可用的 Tesseract-OCR，程序会提示下载安装。")
        self._add_divider(card_layout)

        self._add_section_title(card_layout, "二、语言设置")
        self._add_step(card_layout, 1, "源语言和目标语言下拉框各自有 6 个槽位：第 1 行是“临时语言”，第 2-5 行是 4 个快捷语言槽位，最后 1 行是“显示更多…”。")
        self._add_step(card_layout, 2, "直接选择快捷语言槽位会保存到配置中，适合常用语言组合。")
        self._add_step(card_layout, 3, "点击“显示更多…”后，可以搜索全部语言。弹窗里点右侧 1-4 按钮可以把该语言直接写入对应快捷槽位；双击或确定则可作为临时语言使用。")
        self._add_step(card_layout, 4, "如果源语言或目标语言停留在“临时语言：未设置”，截图翻译会被禁用，按钮会提示“请先设置语言”。")
        self._add_divider(card_layout)

        self._add_section_title(card_layout, "三、截图与 OCR")
        self._add_step(card_layout, 1, "截图与 OCR 设置区可以修改截图快捷键，例如 b、F1、Ctrl+Shift+S。修改后离开输入框即可生效。")
        self._add_step(card_layout, 2, "“保留框选区域”开启后，框选一次会记住该区域，后续再按快捷键会直接重复抓取同一区域，无需重新框选。关闭该选项后，保留区域会被清除。")
        self._add_step(card_layout, 3, "OCR 识别模式分为“识别文本模式”和“复杂背景模式”。识别文本模式适合干净文字区域；复杂背景模式适合有渐变、花纹、噪点或背景复杂的场景。")
        self._add_step(card_layout, 4, "复杂背景模式下建议设置“字芯颜色”。可以手填 `#RRGGBB`、点击“选择颜色”，也可以使用“吸管”从屏幕直接取色。识别文本模式下不需要设置字芯颜色。")
        self._add_step(card_layout, 5, "进入截图模式后，拖动鼠标框选区域；松开左键或按 Enter 会确认截图；按 Esc 或右上角取消则终止本次截图。选区太小会被忽略。")
        self._add_divider(card_layout)

        self._add_section_title(card_layout, "四、结果显示与输入模式")
        self._add_step(card_layout, 1, "截图成功后，悬浮窗会先显示 OCR 进度，再显示识别原文和翻译结果。识别失败或翻译失败时，也会把失败信息直接显示在悬浮窗里。")
        self._add_step(card_layout, 2, "悬浮窗内可以复制翻译、固定窗口、关闭窗口、手动修改原文后重新翻译。")
        self._add_step(card_layout, 3, "点击主界面的“输入模式”后，可以不经过 OCR，直接在悬浮窗里手动输入或粘贴文本，再点“翻译”。这适合处理纯文本或 Hook 抓到的文本。")
        self._add_step(card_layout, 4, "悬浮窗显示区可以设置透明度、显示时长和是否自动隐藏。固定后可长期停留查看；不固定时会按设置自动隐藏。")
        self._add_divider(card_layout)

        self._add_section_title(card_layout, "五、系统状态、历史记录、词库与智能复用")
        self._add_step(card_layout, 1, "“系统状态”页面会显示翻译服务状态、Tesseract 是否可用、OCR 是否就绪、当前模型是否可用，以及内存、显存、CPU 占用。")
        self._add_step(card_layout, 2, "“历史记录”页面会记录截图、OCR、翻译、Hook 和错误日志，便于排查问题。")
        self._add_step(card_layout, 3, "“翻译词库”支持每行一条规则，例如 `魔王=魔王大人`。支持 `=、:、：、->、=>` 这些分隔方式，保存后会优先套用固定译法。")
        self._add_step(card_layout, 4, "“智能复用”开启后，会缓存源文、译文、语言和更新时间。你可以搜索、查看详情、删除选中记录，或一键清空缓存。")
        self._add_divider(card_layout)

        self._add_section_title(card_layout, "六、Hook 模式")
        self._add_notice(card_layout, "提示：Hook 模式兼容性依赖目标程序类型，部分游戏或引擎可能抓不到文本，或者需要等待一段时间才会开始输出。", "warn")
        self._add_step(card_layout, 1, "进入 Hook 模式后，可以在进程列表里直接选目标进程，也可以手动填写进程名，例如 `game.exe`。")
        self._add_step(card_layout, 2, "点击“刷新进程列表”可以重新扫描当前进程；顶部搜索框可按名称过滤进程。")
        self._add_step(card_layout, 3, "启动 Hook 后，捕获到的文本会进入“拦截文本”列表。开启“实时翻译”时，程序会自动处理新文本；关闭时可以手动选中一条，再点“翻译选中”。(必须出现文本再启动否则崩溃)")
        self._add_step(card_layout, 4, "拦截文本支持内容搜索、双击翻译、清空列表。翻译选中的文本时，会直接打开悬浮窗并按文本模式处理。")
        self._add_step(card_layout, 5, "如果遇到架构不匹配、辅助注入器未找到或 Hook 启动失败，请先查看历史记录中的 Hook 日志。")
        self._add_divider(card_layout)

        self._add_section_title(card_layout, "七、API 服务模式（在线或本地接口）")
        self._add_notice(card_layout, "提示：启用 API 服务后，程序会切换到接口翻译，不再使用本地模型。关闭 API 服务后，会重新初始化本地模型。", "gear")
        api_intro = QLabel("API 服务页面支持服务商预设、BaseURL、APIKey、模型列表、自动获取模型和手动添加模型。常见本地接口地址如下：")
        api_intro.setObjectName("howToStepText")
        card_layout.addWidget(api_intro)

        api_block = QFrame()
        api_block.setObjectName("howToCodeBlock")
        api_layout = QVBoxLayout(api_block)
        api_layout.setContentsMargins(
            self._scale_size(16),
            self._scale_size(12),
            self._scale_size(16),
            self._scale_size(12),
        )
        api_text = QLabel(
            "# LM Studio\n"
            "http://localhost:1234/v1\n"
            "http://localhost:1234/api/v0\n\n"
            "# Ollama\n"
            "http://localhost:11434/api/chat\n"
            "http://localhost:11434/api/generate"
        )
        api_text.setObjectName("howToCodeText")
        api_text.setTextFormat(Qt.TextFormat.PlainText)
        api_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        api_layout.addWidget(api_text)
        card_layout.addWidget(api_block)

        api_model = QLabel("使用步骤建议如下：先选择服务商预设或填写 BaseURL，再填写 APIKey（如需要），然后点击“自动获取模型”或手动“添加模型”，选中模型后再启用 API 服务。模型名称只填写模型名，例如 `llama3.1`、`qwen2.5:14b`、`deepseek-chat`。")
        api_model.setObjectName("howToStepText")
        api_model.setWordWrap(True)
        card_layout.addWidget(api_model)
        self._add_divider(card_layout)

        self._add_section_title(card_layout, "八、其他功能")
        self._add_step(card_layout, 1, "点击“测试截图和翻译”可以快速验证 OCR 和翻译链路是否可用。")
        self._add_step(card_layout, 2, "点击“创建快捷方式”后，程序会在桌面和开始菜单创建快捷方式。")
        self._add_step(card_layout, 3, "程序会检查新版本；如果服务端标记为强制更新，旧版本的翻译功能会被禁用，并提示你前往下载。")
        self._add_divider(card_layout)

        self._add_section_title(card_layout, "九、常见问题")
        for text in (
            "点击“启动翻译”没有反应：通常是 OCR / 模型仍在初始化，或源语言、目标语言还没有设置好。请先看“系统状态”。",
            "提示要安装 Tesseract：说明当前系统没有可用的 OCR 组件，按提示安装即可。",
            "复杂背景识别效果差：请切换到“复杂背景模式”，并重新选择更接近文字主体的字芯颜色。",
            "500 `open .../api/chat/latest`：通常表示模型名写错，或者 BaseURL 不是当前服务实际提供的接口地址。",
            "404 / Unexpected endpoint：通常表示 BaseURL 路径写错，例如把 `/v1`、`/api/chat`、`/api/generate` 填错。",
            "API 连接失败：通常是服务未启动、模型未加载、APIKey 错误，或本机端口地址填写不对。",
            "Hook 没抓到文本：先确认目标进程名是否正确，再看历史记录里的 Hook 日志，必要时尝试重新启动目标程序和 Hook。",
        ):
            bullet = QLabel(f"•  {text}")
            bullet.setObjectName("howToBulletText")
            bullet.setWordWrap(True)
            card_layout.addWidget(bullet)
        body_layout.addWidget(card)

        footer = QFrame()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(self._scale_size(12))

        support_icon = QLabel()
        support_icon.setPixmap(self._make_ring_icon(self._scale_size(30)))
        support_icon.setFixedSize(self._scale_size(30), self._scale_size(30))
        footer_layout.addWidget(support_icon, 0, Qt.AlignmentFlag.AlignVCenter)

        support_text = QLabel('还有问题以及支持我们请到官网 <a href="https://14ku.date/support">https://14ku.date/support</a>')
        support_text.setObjectName("howToFooterText")
        support_text.setOpenExternalLinks(True)
        support_text.setWordWrap(True)
        footer_layout.addWidget(support_text, 1)
        body_layout.addWidget(footer)

        body_layout.addStretch(1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def _add_section_title(self, layout: QVBoxLayout, text: str) -> None:
        label = QLabel(text)
        label.setObjectName("howToSectionTitle")
        layout.addWidget(label)

    def _add_step(self, layout: QVBoxLayout, number: int, text: str) -> QLabel:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(self._scale_size(12))

        badge = QLabel(str(number))
        badge.setObjectName("howToStepBadge")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(self._scale_size(26), self._scale_size(26))
        row_layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)

        content = QLabel(text)
        content.setObjectName("howToStepText")
        content.setWordWrap(True)
        content.setTextFormat(Qt.TextFormat.PlainText)
        row_layout.addWidget(content, 1)
        layout.addWidget(row)
        return content

    def _add_notice(self, layout: QVBoxLayout, text: str, kind: str) -> None:
        row = QFrame()
        row.setObjectName("howToNoticeOrange" if kind == "warn" else "howToNoticeBlue")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(
            self._scale_size(12),
            self._scale_size(9),
            self._scale_size(12),
            self._scale_size(9),
        )
        row_layout.setSpacing(self._scale_size(10))

        icon = QLabel()
        if kind == "warn":
            pixmap = self._make_circle_symbol("!", "#FFF0D8", "#D97706", self._scale_size(22))
        else:
            pixmap = self._make_gear_icon(self._scale_size(22))
        icon.setPixmap(pixmap)
        icon.setFixedSize(self._scale_size(22), self._scale_size(22))
        row_layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)

        text_label = QLabel(text)
        text_label.setObjectName("howToNoticeText")
        text_label.setWordWrap(True)
        row_layout.addWidget(text_label, 1)
        layout.addWidget(row)

    def _add_divider(self, layout: QVBoxLayout) -> None:
        line = QFrame()
        line.setObjectName("howToDivider")
        line.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(line)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: #F7FAFF;
            }
            QPushButton#howToBackButton {
                background: #FFFFFF;
                color: #2563EB;
                border: 1px solid #CFE0FF;
                border-radius: 10px;
                padding: 8px 14px;
                font-size: 14px;
                font-weight: 700;
            }
            QPushButton#howToBackButton:hover {
                background: #F3F8FF;
            }
            QLabel#howToHeroTitle {
                color: #0F172A;
                font-size: 28px;
                font-weight: 800;
            }
            QLabel#howToHeroSubtitle {
                color: #6B7280;
                font-size: 14px;
                font-weight: 600;
            }
            QFrame#howToCard {
                background: rgba(255, 255, 255, 0.98);
                border: 1px solid #DBE6F3;
                border-radius: 18px;
            }
            QFrame#howToDivider {
                color: #E5EDF7;
                background: #E5EDF7;
                min-height: 1px;
                max-height: 1px;
                border: none;
            }
            QLabel#howToSectionTitle {
                color: #2F6BFF;
                font-size: 17px;
                font-weight: 800;
            }
            QLabel#howToStepBadge {
                color: #FFFFFF;
                background: #2F6BFF;
                border-radius: 13px;
                font-size: 13px;
                font-weight: 800;
            }
            QLabel#howToStepText {
                color: #1F2937;
                font-size: 14px;
                font-weight: 600;
                line-height: 1.65;
            }
            QFrame#howToNoticeOrange {
                background: #FFF6EA;
                border: 1px solid #FCE3BF;
                border-radius: 12px;
            }
            QFrame#howToNoticeBlue {
                background: #EEF5FF;
                border: 1px solid #D7E7FF;
                border-radius: 12px;
            }
            QLabel#howToNoticeText {
                color: #8A5A00;
                font-size: 14px;
                font-weight: 700;
            }
            QFrame#howToNoticeBlue QLabel#howToNoticeText {
                color: #5B7DBA;
            }
            QFrame#howToCodeBlock {
                background: #FAFCFF;
                border: 1px solid #D5E1F0;
                border-radius: 14px;
            }
            QLabel#howToCodeText {
                color: #4B5563;
                font-family: Consolas, 'Courier New', monospace;
                font-size: 14px;
                font-weight: 600;
            }
            QLabel#howToBulletText {
                color: #1F2937;
                font-size: 14px;
                font-weight: 600;
                line-height: 1.6;
            }
            QLabel#howToFooterText {
                color: #5F6F85;
                font-size: 14px;
                font-weight: 700;
            }
            QLabel#howToFooterText a {
                color: #2F6BFF;
                text-decoration: underline;
            }
            """
        )

    def _make_circle_symbol(self, symbol: str, bg: str, fg: str, size: int) -> QPixmap:
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(bg))
            painter.drawEllipse(0, 0, size, size)
            painter.setPen(QColor(fg))
            font = QFont("Segoe UI", max(10, int(size * 0.56)))
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, symbol)
        finally:
            painter.end()
        return pix

    def _make_gear_icon(self, size: int) -> QPixmap:
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#E7F0FF"))
            painter.drawEllipse(0, 0, size, size)
            pen = QPen(QColor("#2563EB"), max(2, size // 12))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(int(size * 0.28), int(size * 0.28), int(size * 0.44), int(size * 0.44))
            painter.drawEllipse(int(size * 0.42), int(size * 0.42), int(size * 0.16), int(size * 0.16))
            for x1, y1, x2, y2 in (
                (0.50, 0.10, 0.50, 0.24),
                (0.50, 0.76, 0.50, 0.90),
                (0.10, 0.50, 0.24, 0.50),
                (0.76, 0.50, 0.90, 0.50),
                (0.24, 0.24, 0.33, 0.33),
                (0.67, 0.67, 0.76, 0.76),
                (0.76, 0.24, 0.67, 0.33),
                (0.33, 0.67, 0.24, 0.76),
            ):
                painter.drawLine(int(size * x1), int(size * y1), int(size * x2), int(size * y2))
        finally:
            painter.end()
        return pix

    def _make_ring_icon(self, size: int) -> QPixmap:
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#2F6BFF"))
            painter.drawEllipse(0, 0, size, size)
            pen = QPen(QColor("#FFFFFF"), max(2, size // 11))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(int(size * 0.22), int(size * 0.22), int(size * 0.56), int(size * 0.56))
            painter.drawLine(int(size * 0.29), int(size * 0.29), int(size * 0.18), int(size * 0.18))
            painter.drawLine(int(size * 0.71), int(size * 0.29), int(size * 0.82), int(size * 0.18))
            painter.drawLine(int(size * 0.29), int(size * 0.71), int(size * 0.18), int(size * 0.82))
            painter.drawLine(int(size * 0.71), int(size * 0.71), int(size * 0.82), int(size * 0.82))
        finally:
            painter.end()
        return pix


class HowToWindow(QMainWindow):
    def __init__(self, scale_size, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scale_size = scale_size
        self.setWindowTitle("如何操作")
        self.setMinimumSize(self._scale_size(860), self._scale_size(640))
        self.resize(self._scale_size(900), self._scale_size(680))
        page = HowToPage(scale_size, close_callback=self.close, parent=self)
        self.setCentralWidget(page)
        self.page = page

    def update_hotkey(self, hotkey: str) -> None:
        self.page.update_hotkey(hotkey)
