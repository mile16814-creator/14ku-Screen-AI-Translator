"""
语言注册表（翻译用）

目标：
- 统一管理“显示名称 ↔ 配置键 ↔ 本地模型(NLLB/M2M100)语言码”的映射
- 兼容旧配置（如 ZH/EN/JA/KO、中文/英语 等）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional


@dataclass(frozen=True)
class Language:
    """一个可选语言（用于翻译）"""

    key: str  # 规范化后的键（建议 BCP47/常见短码，如 en、zh-CN）
    display_name: str  # UI 展示名（简体中文）
    nllb_codes: List[str]  # NLLB/M2M100 目标语言代码候选（优先级从高到低）


# 用户要求的全语言清单（并提供 NLLB/M2M100 映射）
ALL_LANGUAGES: List[Language] = [
    Language("zh-CN", "中文（简体）", ["zho_Hans"]),
    Language("en", "英语", ["eng_Latn"]),
    Language("zh-TW", "中文（繁体）", ["zho_Hant"]),
    Language("ja", "日语", ["jpn_Jpan"]),
    Language("ko", "韩语", ["kor_Hang"]),
    Language("fr", "法语", ["fra_Latn"]),
    Language("de", "德语", ["deu_Latn"]),
    Language("es", "西班牙语", ["spa_Latn"]),
    Language("pt", "葡萄牙语", ["por_Latn"]),
    Language("it", "意大利语", ["ita_Latn"]),
    Language("nl", "荷兰语", ["nld_Latn"]),
    Language("ru", "俄语", ["rus_Cyrl"]),
    Language("uk", "乌克兰语", ["ukr_Cyrl"]),
    Language("pl", "波兰语", ["pol_Latn"]),
    Language("cs", "捷克语", ["ces_Latn"]),
    Language("sk", "斯洛伐克语", ["slk_Latn"]),
    Language("hu", "匈牙利语", ["hun_Latn"]),
    Language("ro", "罗马尼亚语", ["ron_Latn"]),
    Language("bg", "保加利亚语", ["bul_Cyrl"]),
    # 塞尔维亚语常用西里尔/拉丁两种；优先尝试西里尔，失败再回退拉丁
    Language("sr", "塞尔维亚语", ["srp_Cyrl", "srp_Latn"]),
    Language("hr", "克罗地亚语", ["hrv_Latn"]),
    Language("sl", "斯洛文尼亚语", ["slv_Latn"]),
    Language("lt", "立陶宛语", ["lit_Latn"]),
    Language("lv", "拉脱维亚语", ["lav_Latn"]),
    Language("et", "爱沙尼亚语", ["est_Latn"]),
    Language("sv", "瑞典语", ["swe_Latn"]),
    # 挪威语：优先 Bokmål（nob），失败回退 Nynorsk（nno）
    Language("no", "挪威语", ["nob_Latn", "nno_Latn"]),
    Language("da", "丹麦语", ["dan_Latn"]),
    Language("fi", "芬兰语", ["fin_Latn"]),
    Language("is", "冰岛语", ["isl_Latn"]),
    Language("el", "希腊语", ["ell_Grek"]),
    Language("tr", "土耳其语", ["tur_Latn"]),
    Language("he", "希伯来语", ["heb_Hebr"]),
    # 波斯语：优先 pes，失败回退 fas
    Language("hi", "印地语", ["hin_Deva"]),
    Language("bn", "孟加拉语", ["ben_Beng"]),
    Language("ta", "泰米尔语", ["tam_Taml"]),
    Language("te", "泰卢固语", ["tel_Telu"]),
    Language("kn", "卡纳达语", ["kan_Knda"]),
    Language("mr", "马拉地语", ["mar_Deva"]),
    Language("gu", "古吉拉特语", ["guj_Gujr"]),
    Language("pa", "旁遮普语", ["pan_Guru"]),
    Language("th", "泰语", ["tha_Thai"]),
    Language("lo", "老挝语", ["lao_Laoo"]),
    Language("km", "高棉语", ["khm_Khmr"]),
    Language("id", "印尼语", ["ind_Latn"]),
    # 马来语：优先 zsm，失败回退 msa
    Language("ms", "马来语", ["zsm_Latn", "msa_Latn"]),
    Language("fil", "菲律宾语", ["fil_Latn"]),
    Language("sw", "斯瓦希里语", ["swh_Latn"]),
    Language("zu", "祖鲁语", ["zul_Latn"]),
]


_KEY_TO_LANG: Dict[str, Language] = {l.key: l for l in ALL_LANGUAGES}
_DISPLAY_TO_KEY: Dict[str, str] = {l.display_name: l.key for l in ALL_LANGUAGES}

# 旧配置/旧UI/别名兼容
_ALIASES_TO_KEY: Dict[str, str] = {
    # 旧 DeepL 风格码（大写）
    "EN": "en",
    "ZH": "zh-CN",
    "JA": "ja",
    "KO": "ko",
    # 常见短码
    "en": "en",
    "zh": "zh-CN",
    "zh-cn": "zh-CN",
    "zh-tw": "zh-TW",
    "ja": "ja",
    "ko": "ko",
    # OCR / tesseract 别名
    "eng": "en",
    "jpn": "ja",
    "kor": "ko",
    "rus": "ru",
    "chi_sim": "zh-CN",
    "chi_tra": "zh-TW",
    # 旧 UI 显示（无简繁区分时默认简体）
    "中文": "zh-CN",
    "英文": "en",
    "日语": "ja",
    "韩语": "ko",
    "俄语": "ru",
}


def all_language_display_names() -> List[str]:
    """返回全语言的显示名列表（用于 UI）。"""
    return [l.display_name for l in ALL_LANGUAGES]


def display_name_for_key(key: str) -> str:
    """给定规范化 key，返回 UI 展示名。未知则原样返回。"""
    k = normalize_lang_key(key)
    lang = _KEY_TO_LANG.get(k)
    return lang.display_name if lang else (key or "")


def key_for_display_name(display_name: str) -> str:
    """给定 UI 展示名，返回规范化 key。未知则尝试 normalize。"""
    if not display_name:
        return "zh-CN"
    if display_name in _DISPLAY_TO_KEY:
        return _DISPLAY_TO_KEY[display_name]
    return normalize_lang_key(display_name)


def normalize_lang_key(value: str) -> str:
    """
    把各种输入（配置值/旧码/显示名）规范化为 key：
    - en/zh-CN/zh-TW/...
    """
    if not value:
        return "zh-CN"
    raw = str(value).strip()
    if not raw:
        return "zh-CN"

    # 先命中“显示名”
    if raw in _DISPLAY_TO_KEY:
        return _DISPLAY_TO_KEY[raw]

    # 再命中别名（大小写/横线等）
    if raw in _ALIASES_TO_KEY:
        return _ALIASES_TO_KEY[raw]
    low = raw.lower()
    if low in _ALIASES_TO_KEY:
        return _ALIASES_TO_KEY[low]
    up = raw.upper()
    if up in _ALIASES_TO_KEY:
        return _ALIASES_TO_KEY[up]

    # 已是规范 key
    if raw in _KEY_TO_LANG:
        return raw
    if low in _KEY_TO_LANG:
        return low
    # bcp47 兼容（把 zh-cn 这种统一大小写）
    if "-" in raw:
        parts = raw.split("-")
        if len(parts) >= 2:
            norm = parts[0].lower() + "-" + parts[1].upper()
            if norm in _KEY_TO_LANG:
                return norm

    return raw


def nllb_candidates_for_key(key: str) -> List[str]:
    """给定 key，返回 NLLB/M2M100 语言码候选列表。"""
    k = normalize_lang_key(key)
    lang = _KEY_TO_LANG.get(k)
    if not lang:
        return [k]
    return list(lang.nllb_codes)


def normalize_quick_language_keys(keys: Iterable[str], *, desired_len: int = 4) -> List[str]:
    """
    规范化“快捷语言槽位”，保证长度固定、去重、且每项都落在 ALL_LANGUAGES 内（未知会被丢弃）。
    """
    out: List[str] = []
    for k in keys or []:
        nk = normalize_lang_key(k)
        if nk == "auto":
            continue
        if nk not in _KEY_TO_LANG:
            continue
        if nk in out:
            continue
        out.append(nk)
        if len(out) >= desired_len:
            break

    # 默认回填（保持“老4样”）
    defaults = ["en", "zh-CN", "ja", "ko"]
    for d in defaults:
        if len(out) >= desired_len:
            break
        if d not in out:
            out.append(d)
    return out[:desired_len]


