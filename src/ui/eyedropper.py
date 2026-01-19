"""
吸管取色工具：全屏透明层，点击屏幕任意位置取该像素颜色。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QPoint
from PyQt6.QtGui import QColor, QGuiApplication, QCursor, QPainter
from PyQt6.QtWidgets import QWidget, QApplication


class EyedropperOverlay(QWidget):
    """全屏吸管取色层：左键取色，ESC/右键取消。"""

    color_picked = pyqtSignal(str)  # "#RRGGBB"
    cancelled = pyqtSignal()

    def __init__(self):
        super().__init__()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

        # 覆盖“虚拟桌面”（多屏幕）
        virtual_geo = None
        for s in QGuiApplication.screens() or []:
            try:
                g = s.geometry()
                virtual_geo = g if virtual_geo is None else virtual_geo.united(g)
            except Exception:
                pass
        if virtual_geo is None:
            screen = QGuiApplication.primaryScreen()
            virtual_geo = screen.geometry() if screen is not None else None
        if virtual_geo is None:
            # 兜底
            self.setGeometry(0, 0, 1920, 1080)
        else:
            self.setGeometry(virtual_geo)

    def start(self):
        """开始取色模式。"""
        self.show()
        self.raise_()
        self.activateWindow()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._cancel()
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        pos = QCursor.pos()

        # 先隐藏自身，避免被截图抓进去（尽量）
        self.setVisible(False)
        try:
            QApplication.processEvents()
        except Exception:
            pass

        color = self._pick_color_at_global_pos(pos)
        if color is not None:
            self.color_picked.emit(color.name().upper())
        else:
            self.cancelled.emit()
        self.close()

    def paintEvent(self, event):
        # 轻微暗化，提示“正在取色”
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 40))

    def _cancel(self):
        self.cancelled.emit()
        self.close()

    def _pick_color_at_global_pos(self, global_pos: QPoint) -> QColor | None:
        """从屏幕抓取并采样指定全局坐标像素颜色（尽量兼容高 DPI / 多屏）。"""
        try:
            screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
            if screen is None:
                return None

            geo = screen.geometry()
            x = global_pos.x() - geo.x()
            y = global_pos.y() - geo.y()

            # 抓取整屏（一次性），再按比例映射到实际像素坐标，兼容 DPI 缩放
            pix = screen.grabWindow(0)
            if pix.isNull():
                return None

            img = pix.toImage()
            if img.isNull():
                return None

            # 计算缩放比例（pixmap 可能是 device pixels，而 geo/pos 是逻辑坐标）
            scale_x = img.width() / max(1, screen.size().width())
            scale_y = img.height() / max(1, screen.size().height())

            px = int(x * scale_x)
            py = int(y * scale_y)

            if px < 0 or py < 0 or px >= img.width() or py >= img.height():
                return None

            return img.pixelColor(px, py)
        except Exception:
            return None


