"""
快捷键管理器 - 监听全局快捷键触发截图
"""
import sys
import threading
import time
from PyQt6.QtCore import QObject, pyqtSignal, QThread
import keyboard
from .screenshot import ScreenshotTool


class HotkeyManager(QObject):
    """快捷键管理器类，监听全局快捷键"""
    
    # 信号：快捷键被触发
    hotkey_triggered = pyqtSignal()
    
    def __init__(self, hotkey='b'):
        super().__init__()
        self.hotkey = hotkey
        self.listening = False
        self.listener_thread = None
        self.screenshot_tool = None
        
    def start_listening(self):
        """开始监听快捷键"""
        if self.listening:
            return
            
        self.listening = True
        
        # 创建监听线程
        self.listener_thread = threading.Thread(target=self._hotkey_listener, daemon=True)
        self.listener_thread.start()
        
    def stop_listening(self):
        """停止监听快捷键"""
        self.listening = False
        
        # 等待线程结束
        if self.listener_thread and self.listener_thread.is_alive():
            self.listener_thread.join(timeout=1)

    # 兼容旧接口，供外部调用 start()/stop()
    def start(self):
        """开始监听快捷键（兼容旧接口）"""
        self.start_listening()

    def stop(self):
        """停止监听快捷键（兼容旧接口）"""
        self.stop_listening()
            
    def _hotkey_listener(self):
        """快捷键监听线程函数"""
        try:
            # 注册热键
            keyboard.add_hotkey(self.hotkey, self._on_hotkey_pressed)
            
            # 保持线程运行
            while self.listening:
                time.sleep(0.1)
                
        except Exception as e:
            print(f"快捷键监听错误: {e}")
        finally:
            # 清理热键
            try:
                keyboard.remove_hotkey(self.hotkey)
            except:
                pass
                
    def _on_hotkey_pressed(self):
        """快捷键按下时的回调函数"""
        if self.listening:
            # 在主线程中发射信号
            self.hotkey_triggered.emit()
            
    def set_hotkey(self, hotkey):
        """设置新的快捷键"""
        # 停止当前监听
        was_listening = self.listening
        if was_listening:
            self.stop_listening()
            
        # 更新快捷键
        self.hotkey = hotkey
        
        # 重新开始监听
        if was_listening:
            self.start_listening()
            
    def get_hotkey(self):
        """获取当前快捷键"""
        return self.hotkey


class HotkeyWorker(QThread):
    """热键工作线程，用于在后台监听快捷键"""
    
    hotkey_triggered = pyqtSignal()
    
    def __init__(self, hotkey='b'):
        super().__init__()
        self.hotkey = hotkey
        self.running = False
        
    def run(self):
        """线程运行函数"""
        self.running = True
        
        try:
            # 注册热键
            keyboard.add_hotkey(self.hotkey, self._on_hotkey_pressed)
            
            # 保持线程运行
            while self.running:
                time.sleep(0.1)
                
        except Exception as e:
            print(f"热键工作线程错误: {e}")
        finally:
            # 清理热键
            try:
                keyboard.remove_hotkey(self.hotkey)
            except:
                pass
                
    def _on_hotkey_pressed(self):
        """快捷键按下时的回调函数"""
        if self.running:
            self.hotkey_triggered.emit()
            
    def stop(self):
        """停止线程"""
        self.running = False
        self.wait()
        
    def set_hotkey(self, hotkey):
        """设置新的快捷键"""
        # 停止当前线程
        was_running = self.running
        if was_running:
            self.stop()
            
        # 更新快捷键
        self.hotkey = hotkey
        
        # 重新启动线程
        if was_running:
            self.start()


def parse_hotkey_string(hotkey_str):
    """
    解析快捷键字符串，转换为keyboard库可识别的格式
    
    支持的格式：
    - 单个键: 'b', 'f1', 'esc'
    - 组合键: 'ctrl+shift+s', 'alt+s', 'win+r'
    """
    if not hotkey_str:
        return 'b'  # 默认快捷键
        
    # 转换为小写并去除空格
    hotkey_str = hotkey_str.lower().strip()
    
    # 检查是否是有效的快捷键
    try:
        # 尝试注册热键来验证格式
        keyboard.add_hotkey(hotkey_str, lambda: None)
        keyboard.remove_hotkey(hotkey_str)
        return hotkey_str
    except:
        # 如果格式无效，返回默认值
        print(f"无效的快捷键格式: {hotkey_str}，使用默认值 'b'")
        return 'b'


def get_available_hotkeys():
    """获取可用的快捷键列表"""
    return [
        'b', 'f1', 'f2', 'f3', 'f4', 'f5', 'f6',
        'ctrl+shift+s', 'alt+s', 'win+s',
        'ctrl+alt+s', 'shift+s', 'ctrl+b'
    ]


if __name__ == "__main__":
    # 测试快捷键管理器
    import signal
    import sys
    
    def signal_handler(sig, frame):
        print("\n程序退出")
        sys.exit(0)
        
    signal.signal(signal.SIGINT, signal_handler)
    
    print("快捷键管理器测试")
    print("按下 'b' 键触发截图（按 Ctrl+C 退出）")
    
    manager = HotkeyManager('b')
    
    def on_hotkey():
        print("快捷键被触发！开始截图...")
        
    manager.hotkey_triggered.connect(on_hotkey)
    manager.start_listening()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        manager.stop_listening()
        print("测试结束")
