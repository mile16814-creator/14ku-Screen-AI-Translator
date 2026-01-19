"""
截图工具模块
"""

import sys
from typing import Optional, Tuple
from dataclasses import dataclass

from PyQt6.QtCore import Qt, QRect, QPoint, pyqtSignal, QObject, QTimer
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor, QGuiApplication, QCursor, QPixmap
from PyQt6.QtWidgets import QWidget, QApplication


@dataclass
class ScreenshotResult:
    """截图结果"""
    success: bool
    image: Optional[QPixmap] = None
    rect: Optional[QRect] = None
    error: Optional[str] = None


class ScreenshotOverlay(QWidget):
    """截图遮罩层"""
    
    screenshot_taken = pyqtSignal(ScreenshotResult)
    
    def __init__(self):
        super().__init__()
        
        # 设置窗口属性
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        
        # 设置窗口覆盖“虚拟桌面”（多屏幕）
        virtual_geo = QRect()
        for s in QGuiApplication.screens() or []:
            try:
                virtual_geo = virtual_geo.united(s.geometry())
            except Exception:
                pass
        if virtual_geo.isNull():
            screen = QGuiApplication.primaryScreen()
            if screen is not None:
                virtual_geo = screen.geometry()
        if virtual_geo.isNull():
            # 兜底
            virtual_geo = QRect(0, 0, 1920, 1080)
        self.setGeometry(virtual_geo)
        
        # 设置半透明背景
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # 截图相关变量
        self.start_point = QPoint()
        self.end_point = QPoint()
        self.is_selecting = False
        self.selection_rect = QRect()

        # 延迟截图用（避免把遮罩层截进去）
        self._pending_rect: Optional[QRect] = None
        
        # 颜色设置
        self.overlay_color = QColor(0, 0, 0, 100)  # 半透明黑色遮罩
        self.selection_color = QColor(255, 255, 255, 30)  # 半透明白色选区
        self.border_color = QColor(66, 133, 244, 255)  # 蓝色边框
        self.border_width = 2
        
        # 显示鼠标当前位置
        self.show_cursor_pos = True
        
        # 设置鼠标跟踪
        self.setMouseTracking(True)
        
        # 设置光标
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    
    def paintEvent(self, event):
        """绘制事件"""
        painter = QPainter(self)
        
        # 绘制半透明遮罩
        painter.fillRect(self.rect(), self.overlay_color)
        
        # 如果有选区，绘制选区
        if not self.selection_rect.isNull():
            # 绘制选区内部（半透明白色）
            painter.fillRect(self.selection_rect, self.selection_color)
            
            # 绘制选区边框
            pen = QPen(self.border_color)
            pen.setWidth(self.border_width)
            painter.setPen(pen)
            painter.drawRect(self.selection_rect)
            
            # 绘制选区尺寸信息
            if self.selection_rect.width() > 0 and self.selection_rect.height() > 0:
                # 在选区右下角显示尺寸
                text = f"{self.selection_rect.width()} × {self.selection_rect.height()}"
                font = painter.font()
                font.setPointSize(10)
                painter.setFont(font)
                
                # 计算文本位置
                text_rect = painter.fontMetrics().boundingRect(text)
                text_x = self.selection_rect.right() - text_rect.width() - 5
                text_y = self.selection_rect.bottom() - 5
                
                # 绘制文本背景
                painter.fillRect(
                    text_x - 2, text_y - text_rect.height() - 2,
                    text_rect.width() + 4, text_rect.height() + 4,
                    QColor(0, 0, 0, 150)
                )
                
                # 绘制文本
                painter.setPen(QColor(255, 255, 255))
                painter.drawText(text_x, text_y, text)
        
        # 显示鼠标当前位置
        if self.show_cursor_pos and not self.is_selecting:
            cursor_pos = QCursor.pos()
            text = f"{cursor_pos.x()}, {cursor_pos.y()}"
            font = painter.font()
            font.setPointSize(10)
            painter.setFont(font)
            
            text_rect = painter.fontMetrics().boundingRect(text)
            text_x = cursor_pos.x() + 10
            text_y = cursor_pos.y() - 10
            
            # 确保文本在窗口内
            if text_x + text_rect.width() > self.width():
                text_x = cursor_pos.x() - text_rect.width() - 10
            if text_y - text_rect.height() < 0:
                text_y = cursor_pos.y() + text_rect.height() + 10
            
            # 绘制文本背景
            painter.fillRect(
                text_x - 2, text_y - text_rect.height() - 2,
                text_rect.width() + 4, text_rect.height() + 4,
                QColor(0, 0, 0, 150)
            )
            
            # 绘制文本
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(text_x, text_y, text)
    
    def mousePressEvent(self, event):
        """鼠标按下事件"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_point = event.pos()
            self.end_point = event.pos()
            self.is_selecting = True
            self.selection_rect = QRect(self.start_point, self.end_point)
            self.update()
    
    def mouseMoveEvent(self, event):
        """鼠标移动事件"""
        if self.is_selecting:
            self.end_point = event.pos()
            self.selection_rect = QRect(self.start_point, self.end_point).normalized()
            self.update()
        else:
            # 更新光标位置显示
            self.update()
    
    def mouseReleaseEvent(self, event):
        """鼠标释放事件"""
        if event.button() == Qt.MouseButton.LeftButton and self.is_selecting:
            self.end_point = event.pos()
            self.is_selecting = False
            
            # 确保选区有效
            self.selection_rect = QRect(self.start_point, self.end_point).normalized()
            
            # 如果选区太小，忽略
            if self.selection_rect.width() < 10 or self.selection_rect.height() < 10:
                self.selection_rect = QRect()
                self.update()
                return
            
            # 截图并关闭窗口
            self.take_screenshot()
    
    def keyPressEvent(self, event):
        """键盘按下事件"""
        if event.key() == Qt.Key.Key_Escape:
            # ESC 键取消截图
            self.screenshot_taken.emit(ScreenshotResult(
                success=False,
                error="用户取消"
            ))
            self.close()
        elif event.key() == Qt.Key.Key_Enter or event.key() == Qt.Key.Key_Return:
            # Enter 键确认截图
            if not self.selection_rect.isNull():
                self.take_screenshot()
    
    def take_screenshot(self):
        """执行截图"""
        try:
            if self.selection_rect.isNull():
                self.screenshot_taken.emit(ScreenshotResult(success=False, error="截图区域为空"))
                self.close()
                return

            # 关键：先隐藏遮罩层并让事件循环刷新一帧，避免把黑色蒙版/边框一起截进图里
            self._pending_rect = QRect(self.selection_rect)
            self.setWindowOpacity(0.0)
            self.hide()
            QApplication.processEvents()

            # 延迟一点点更稳（不同机器/显卡上 repaint 时机不同）
            QTimer.singleShot(60, self._do_grab_pending_rect)

        except Exception as e:
            self.screenshot_taken.emit(ScreenshotResult(success=False, error=f"截图失败: {str(e)}"))
            self.close()

    def _do_grab_pending_rect(self):
        """真正执行 grabWindow（在遮罩层隐藏后）"""
        try:
            if self._pending_rect is None or self._pending_rect.isNull():
                self.screenshot_taken.emit(ScreenshotResult(success=False, error="截图区域为空"))
                self.close()
                return

            # 选区转为全局坐标（用于悬浮窗定位等）
            global_tl = self.mapToGlobal(self._pending_rect.topLeft())
            global_rect = QRect(global_tl, self._pending_rect.size())

            # 优先使用选区中心所在的屏幕
            screen = QGuiApplication.screenAt(global_rect.center()) or QGuiApplication.primaryScreen()
            if screen is None:
                raise RuntimeError("无法获取屏幕对象")

            # grabWindow(window=0) 的坐标按桌面坐标系传入（Qt 会处理多数 DPI 情况）
            screenshot = screen.grabWindow(
                0,
                global_rect.x(),
                global_rect.y(),
                global_rect.width(),
                global_rect.height(),
            )

            self.screenshot_taken.emit(ScreenshotResult(success=True, image=screenshot, rect=global_rect))
            self.close()

        except Exception as e:
            self.screenshot_taken.emit(ScreenshotResult(success=False, error=f"截图失败: {str(e)}"))
            self.close()


class RegionFrameOverlay(QWidget):
    def __init__(self):
        super().__init__()

        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        try:
            flags |= Qt.WindowType.WindowTransparentForInput
        except Exception:
            pass
        self.setWindowFlags(flags)

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        try:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        except Exception:
            pass

        virtual_geo = QRect()
        for s in QGuiApplication.screens() or []:
            try:
                virtual_geo = virtual_geo.united(s.geometry())
            except Exception:
                pass
        if virtual_geo.isNull():
            screen = QGuiApplication.primaryScreen()
            if screen is not None:
                virtual_geo = screen.geometry()
        if virtual_geo.isNull():
            virtual_geo = QRect(0, 0, 1920, 1080)
        self._virtual_geo = QRect(virtual_geo)
        self.setGeometry(self._virtual_geo)

        self._global_rect = QRect()

    def set_global_rect(self, rect: Optional[QRect]) -> None:
        self._global_rect = QRect(rect) if rect is not None else QRect()
        self.update()

    def paintEvent(self, event):
        if self._global_rect.isNull():
            return

        r = QRect(self._global_rect)
        try:
            r = r.intersected(self._virtual_geo)
        except Exception:
            pass
        if r.isNull():
            return

        local = QRect(r)
        try:
            local.translate(-self._virtual_geo.left(), -self._virtual_geo.top())
        except Exception:
            pass

        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        except Exception:
            pass

        outer = QRect(local).adjusted(-2, -2, 2, 2)
        inner = QRect(local).adjusted(1, 1, -1, -1)

        pen_outer = QPen(QColor(0, 0, 0, 110))
        pen_outer.setWidth(6)
        pen_outer.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen_outer)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(outer, 6, 6)

        pen_inner = QPen(QColor(0, 0, 0, 210))
        pen_inner.setWidth(2)
        pen_inner.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen_inner)
        painter.drawRoundedRect(inner, 5, 5)


class ScreenshotTool(QObject):
    """截图工具（在已有 QApplication 中工作，并通过信号返回截图结果）"""

    # 对外暴露的信号：发送 ScreenshotResult
    screenshot_taken = pyqtSignal(ScreenshotResult)

    def __init__(self):
        super().__init__()
        self.overlay: Optional[ScreenshotOverlay] = None

    def start_capture(self):
        """
        开始一次截图（在主应用的事件循环中工作，不创建新的 QApplication）
        """
        try:
            # 如果已有截图窗口在显示，避免重复打开
            if self.overlay is not None and self.overlay.isVisible():
                return

            # 创建截图遮罩层
            self.overlay = ScreenshotOverlay()
            # 将遮罩层的截图结果转发到本工具的信号
            self.overlay.screenshot_taken.connect(self._on_screenshot_taken)
            self.overlay.show()
            self.overlay.raise_()
            self.overlay.activateWindow()
            self.overlay.setFocus()

        except Exception as e:
            # 出错时发送失败结果
            self.screenshot_taken.emit(ScreenshotResult(
                success=False,
                error=f"启动截图失败: {str(e)}"
            ))

    def _on_screenshot_taken(self, result: ScreenshotResult):
        """接收遮罩层的截图结果并转发，然后清理遮罩层"""
        try:
            self.screenshot_taken.emit(result)
        finally:
            if self.overlay is not None:
                self.overlay.close()
                self.overlay.deleteLater()
                self.overlay = None

    def grab_rect(self, rect: QRect) -> ScreenshotResult:
        try:
            if rect is None or rect.isNull() or rect.width() <= 0 or rect.height() <= 0:
                return ScreenshotResult(success=False, error="截图区域为空")

            screen = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
            if screen is None:
                return ScreenshotResult(success=False, error="无法获取屏幕对象")

            screenshot = screen.grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height())
            return ScreenshotResult(success=True, image=screenshot, rect=QRect(rect))
        except Exception as e:
            return ScreenshotResult(success=False, error=f"截图失败: {str(e)}")
    
    def capture(self) -> ScreenshotResult:
        """
        启动截图
        
        Returns:
            ScreenshotResult 对象
        """
        try:
            # 创建应用程序实例（如果不存在）
            app = QApplication.instance()
            if app is None:
                app = QApplication(sys.argv)
            
            # 创建截图遮罩层
            self.overlay = ScreenshotOverlay()
            self.overlay.show()
            self.overlay.raise_()
            self.overlay.activateWindow()
            self.overlay.setFocus()
            
            # 运行事件循环
            app.exec()
            
            # 这里应该通过信号获取结果，但为了简单起见，我们返回一个占位符
            # 实际实现中应该使用信号槽机制
            return ScreenshotResult(
                success=False,
                error="截图工具已启动，请使用信号槽获取结果"
            )
            
        except Exception as e:
            return ScreenshotResult(
                success=False,
                error=f"启动截图工具失败: {str(e)}"
            )
    
    def capture_full_screen(self) -> ScreenshotResult:
        """截取全屏"""
        try:
            screen = QGuiApplication.primaryScreen()
            screenshot = screen.grabWindow(0)
            
            return ScreenshotResult(
                success=True,
                image=screenshot,
                rect=QRect(0, 0, screenshot.width(), screenshot.height())
            )
            
        except Exception as e:
            return ScreenshotResult(
                success=False,
                error=f"全屏截图失败: {str(e)}"
            )
    
    def capture_active_window(self) -> ScreenshotResult:
        """截取活动窗口"""
        # 在 Windows 上，可以使用 win32gui 获取活动窗口
        # 这里简化实现，返回全屏截图
        return self.capture_full_screen()


def test_screenshot():
    """测试截图功能"""
    app = QApplication(sys.argv)
    
    tool = ScreenshotTool()
    result = tool.capture()
    
    if result.success and result.image:
        # 保存截图
        result.image.save("test_screenshot.png", "PNG")
        print(f"截图成功: {result.rect}")
    else:
        print(f"截图失败: {result.error}")
    
    sys.exit()


if __name__ == "__main__":
    test_screenshot()
