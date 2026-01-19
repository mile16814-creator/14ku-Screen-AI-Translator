"""
语言相关工具函数
"""
import re
from typing import Optional, List


def detect_language(text: str) -> str:
    """
    检测文本的语言类型
    
    Args:
        text: 要检测的文本
        
    Returns:
        检测到的语言代码，例如 'en', 'zh', 'ja', 'ko', 'ru' 等
    """
    if not text.strip():
        return 'auto'
    
    # 基于字符范围检测语言
    japanese_chars = sum(1 for c in text if ('\u3040' <= c <= '\u30ff') or ('\u4e00' <= c <= '\u9fff'))
    korean_chars = sum(1 for c in text if '\uac00' <= c <= '\ud7a3')
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    cyrillic_chars = sum(1 for c in text if '\u0400' <= c <= '\u04ff')  # 俄语等西里尔字母
    greek_chars = sum(1 for c in text if '\u0370' <= c <= '\u03ff')  # 希腊字母
    hebrew_chars = sum(1 for c in text if '\u0590' <= c <= '\u05ff')  # 希伯来字母
    arabic_chars = sum(1 for c in text if '\u0600' <= c <= '\u06ff')  # 阿拉伯字母
    devanagari_chars = sum(1 for c in text if '\u0900' <= c <= '\u097f')  # 天城文（印地语等）
    thai_chars = sum(1 for c in text if '\u0e00' <= c <= '\u0e7f')  # 泰语
    latin_chars = sum(1 for c in text if 'a' <= c.lower() <= 'z')
    
    total_chars = len(text)
    
    if total_chars > 0:
        jp_ratio = japanese_chars / total_chars
        ko_ratio = korean_chars / total_chars
        cn_ratio = chinese_chars / total_chars
        ru_ratio = cyrillic_chars / total_chars  # 俄语比例
        el_ratio = greek_chars / total_chars  # 希腊语
        he_ratio = hebrew_chars / total_chars  # 希伯来语
        ar_ratio = arabic_chars / total_chars  # 阿拉伯语
        hi_ratio = devanagari_chars / total_chars  # 印地语
        th_ratio = thai_chars / total_chars  # 泰语
        en_ratio = latin_chars / total_chars
        
        # 检测优先级：中日韩俄希腊希伯来阿拉伯印地泰语英语
        if jp_ratio > 0.3:
            return 'ja'
        elif ko_ratio > 0.3:
            return 'ko'
        elif cn_ratio > 0.3:
            return 'zh'
        elif ru_ratio > 0.3:  # 俄语检测
            return 'ru'
        elif el_ratio > 0.3:  # 希腊语
            return 'el'
        elif he_ratio > 0.3:  # 希伯来语
            return 'he'
        elif ar_ratio > 0.3:  # 阿拉伯语
            return 'ar'
        elif hi_ratio > 0.3:  # 印地语
            return 'hi'
        elif th_ratio > 0.3:  # 泰语
            return 'th'
        elif en_ratio > 0.5:
            return 'en'
    
    return 'auto'


def is_cjk_language(language: Optional[str]) -> bool:
    """
    判断 language（可能是 'eng+jpn+kor' 这种组合）是否应当按 CJK 处理。
    
    规则：
    - 只有当 language 中的语言代码“全部”属于 CJK 时，才视为 CJK。
    - 混合语言（包含 eng 等非 CJK）不直接判定为 CJK，交给文本内容占比再判断，避免误删空格。
    """
    if not language:
        return False

    lang_codes = [c.strip().lower() for c in str(language).split('+') if str(c).strip()]
    if not lang_codes:
        return False

    return all(is_cjk_lang_code(code) for code in lang_codes)


def is_cjk_lang_code(code: str) -> bool:
    """
    判断单个语言代码是否属于 CJK（中/日/韩）。
    """
    code = (code or "").lower()
    # 注意：这里是“语言代码包含关系”，例如 chi_sim / chi_tra / jpn / kor / zh 等
    return any(x in code for x in ['jpn', 'japanese', 'kor', 'korean', 'chi', 'chinese', 'zh', 'chi_sim', 'chi_tra'])


def normalize_lang_key(key: str) -> str:
    """
    规范化语言代码
    
    Args:
        key: 原始语言代码
        
    Returns:
        规范化后的语言代码
    """
    if not key:
        return 'auto'
    
    key = str(key).strip().lower()
    
    # 常见别名映射
    lang_aliases = {
        'chinese': 'zh',
        'english': 'en',
        'japanese': 'ja',
        'korean': 'ko',
        'russian': 'ru',
        'zh-cn': 'zh',
        'zh-tw': 'zh',
        'chi_sim': 'zh',
        'chi_tra': 'zh',
        'jpn': 'ja',
        'kor': 'ko',
        'rus': 'ru',
        'eng': 'en',
    }
    
    return lang_aliases.get(key, key)


def normalize_quick_language_keys(keys: List[str]) -> List[str]:
    """
    规范化快捷语言列表
    
    Args:
        keys: 原始语言代码列表
        
    Returns:
        规范化后的语言代码列表，保持原始长度和顺序
    """
    normalized = []
    for key in keys:
        normalized_key = normalize_lang_key(key)
        normalized.append(normalized_key)
    return normalized
