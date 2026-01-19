"""
统一异常处理模块
"""


class ScreenTranslatorError(Exception):
    """屏幕翻译工具基础异常类"""
    def __init__(self, message: str, error_code: int = 0, details: dict = None):
        """
        初始化异常
        
        Args:
            message: 异常消息
            error_code: 错误代码
            details: 额外的错误详情
        """
        self.error_code = error_code
        self.details = details or {}
        super().__init__(message)
    
    def __str__(self):
        """返回异常字符串表示"""
        return f"[{self.error_code}] {super().__str__()}"


class TranslationError(ScreenTranslatorError):
    """翻译相关异常"""
    def __init__(self, message: str, error_code: int = 100, details: dict = None):
        super().__init__(message, error_code, details)


class OCRError(ScreenTranslatorError):
    """OCR 相关异常"""
    def __init__(self, message: str, error_code: int = 200, details: dict = None):
        super().__init__(message, error_code, details)


class ConfigError(ScreenTranslatorError):
    """配置相关异常"""
    def __init__(self, message: str, error_code: int = 300, details: dict = None):
        super().__init__(message, error_code, details)


class TesseractError(ScreenTranslatorError):
    """Tesseract 相关异常"""
    def __init__(self, message: str, error_code: int = 400, details: dict = None):
        super().__init__(message, error_code, details)


class ModelError(ScreenTranslatorError):
    """模型相关异常"""
    def __init__(self, message: str, error_code: int = 500, details: dict = None):
        super().__init__(message, error_code, details)


class GPUError(ScreenTranslatorError):
    """GPU 相关异常"""
    def __init__(self, message: str, error_code: int = 600, details: dict = None):
        super().__init__(message, error_code, details)


class FileError(ScreenTranslatorError):
    """文件操作相关异常"""
    def __init__(self, message: str, error_code: int = 700, details: dict = None):
        super().__init__(message, error_code, details)


class NetworkError(ScreenTranslatorError):
    """网络相关异常"""
    def __init__(self, message: str, error_code: int = 800, details: dict = None):
        super().__init__(message, error_code, details)


class UIError(ScreenTranslatorError):
    """UI 相关异常"""
    def __init__(self, message: str, error_code: int = 900, details: dict = None):
        super().__init__(message, error_code, details)
