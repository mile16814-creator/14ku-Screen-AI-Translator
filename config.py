"""
配置文件管理
"""

import os
import json
import configparser
from pathlib import Path
from typing import Dict, Any, Optional


class ConfigManager:
    """配置管理器"""
    
    def __init__(self, app_dir: str):
        self.app_dir = Path(app_dir)
        self.config_dir = self.app_dir / "config"
        self.config_file = self.config_dir / "settings.ini"
        self.config_dir.mkdir(exist_ok=True)
        
        # 默认配置
        self.default_config = {
            'general': {
                'language': 'zh-CN',
                'start_minimized': 'false',
                'auto_start': 'false',
            },
            'hotkey': {
                'screenshot': 'b',
                'toggle_translation': 'ctrl+shift+t',
            },
            'screenshot': {
                'keep_capture_region': 'false',
            },
            'hook': {
                'enabled': 'false',
                'port': '37123',
                'target_process': '',
                'auto_start': 'false',
                'py32': '',
                'prefer_frida_only': 'true',
            },
            'translation': {
                'source_language': 'auto',
                # 统一使用 key（兼容旧值：ZH/zh/中文 等仍可识别）
                'target_language': 'zh-CN',
                # 主界面"4个快捷语言槽位"（默认仍为老4样：英/中(简)/日/韩）
                # 逗号分隔：en,zh-CN,ja,ko
                'quick_languages': 'en,zh-CN,ja,ko',
            },
            'glossary': {
                'entries': '',
            },
            'ocr': {
                'languages': 'eng+jpn+kor',
                'confidence_threshold': '70',
                # 新：字芯颜色（用于颜色分割 OCR 预处理）
                # 格式：#RRGGBB
                'core_color': '#FFFFFF',
                # 新：颜色选择器“自定义颜色”槽位（Qt 标准颜色对话框最多 16 个）
                # 逗号分隔：#RRGGBB,#RRGGBB,... 为空表示不指定（使用 Qt 默认）
                'custom_colors': '',
            },
            # OCR 图像预处理（OpenCV）
            # 预处理（颜色分割链路）
            'ocr_preprocess': {
                'enabled': 'true',
                # 新：小字边缘平滑/去噪（对“白底黑字”合成图做轻度滤波后再二值化）
                # 可选：none / gaussian / bilateral
                'smooth_method': 'gaussian',
                # gaussian: ksize 必须为奇数(>=3)，sigma=0 让 OpenCV 自动估计
                'gaussian_ksize': '3',
                'gaussian_sigma': '0',
                # bilateral: d 为邻域直径；sigmaColor/sigmaSpace 越大越平滑
                'bilateral_d': '5',
                'bilateral_sigma_color': '50',
                'bilateral_sigma_space': '50',
            },
            'overlay': {
                'opacity': '0.9',
                'font_size': '12',
                'background_color': '#2C3E50',
                'text_color': '#ECF0F1',
                'position': 'follow_mouse',
            },
            'auth': {
                # 14ku.date 登录/注册服务地址（可按需修改）
                'base_url': 'https://14ku.date',
                # 接口路径（不知道你服务端具体路径时，可在 settings.ini 里改）
                'login_path': '/api/login',
                'register_path': '/api/register',
                # 新增：字数/扣费/充值接口（同样可在 settings.ini 覆盖）
                'quota_path': '/api/quota',
                'consume_path': '/api/consume',
                'recharge_path': '/api/recharge',
                # 新增：客户端更新检查（每次登录后查询；若发现新版本默认强制锁定旧版）
                'update_path': '/api/client_update',
                # 下载页（服务端也可在接口返回 download_url 覆盖）
                'download_url': 'https://14ku.date/download',
                # 是否强制更新（true 时发现新版本会禁用所有功能）
                'force_update': 'true',
                'timeout': '10',
                # 启动时自动登录（仅使用设备ID，静默请求；失败不影响使用）
                'auto_login': 'true',
            },
        }
        
        self.config = configparser.ConfigParser()
        self.load_config()
    
    def load_config(self) -> None:
        """加载配置文件"""
        if self.config_file.exists():
            self.config.read(self.config_file, encoding='utf-8')
        else:
            # 使用默认配置
            for section, options in self.default_config.items():
                self.config[section] = options
            self.save_config()
    
    def save_config(self) -> None:
        """保存配置文件"""
        with open(self.config_file, 'w', encoding='utf-8') as f:
            self.config.write(f)
    
    def get(self, section: str, key: str, default: Optional[str] = None) -> str:
        """获取配置值"""
        try:
            return self.config.get(section, key)
        except (configparser.NoSectionError, configparser.NoOptionError):
            if default is not None:
                return default
            # 从默认配置中获取
            return self.default_config.get(section, {}).get(key, '')
    
    def set(self, section: str, key: str, value: str) -> None:
        """设置配置值"""
        if section not in self.config:
            self.config[section] = {}
        self.config[section][key] = value
        self.save_config()
    
    def get_bool(self, section: str, key: str, default: bool = False) -> bool:
        """获取布尔值配置"""
        value = self.get(section, key, str(default)).lower()
        return value in ('true', 'yes', '1', 'on')
    
    def get_int(self, section: str, key: str, default: int = 0) -> int:
        """获取整数值配置"""
        try:
            return int(self.get(section, key, str(default)))
        except ValueError:
            return default
    
    def get_float(self, section: str, key: str, default: float = 0.0) -> float:
        """获取浮点数值配置"""
        try:
            return float(self.get(section, key, str(default)))
        except ValueError:
            return default
    
    def get_all(self) -> Dict[str, Dict[str, str]]:
        """获取所有配置"""
        result = {}
        for section in self.config.sections():
            result[section] = dict(self.config.items(section))
        return result


# 全局配置实例
_config_instance: Optional[ConfigManager] = None


def init_config(app_dir: str) -> ConfigManager:
    """初始化全局配置"""
    global _config_instance
    _config_instance = ConfigManager(app_dir)
    return _config_instance


def get_config() -> ConfigManager:
    """获取全局配置实例"""
    if _config_instance is None:
        raise RuntimeError("配置未初始化，请先调用 init_config()")
    return _config_instance


def get_app_directory() -> Path:
    """获取应用目录"""
    # 如果是打包版本，使用可执行文件所在目录
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    # 开发版本使用当前文件所在目录的父目录
    return Path(__file__).parent


# 导入 sys 用于 get_app_directory
import sys
