"""
OCR 文字识别模块
"""

import os
import sys
import tempfile
import re
import logging
import subprocess
import shlex
import csv
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image
import pytesseract
import math

# PyQt6 导入
from PyQt6.QtCore import QThread, pyqtSignal


@dataclass
class OCRResult:
    """OCR 识别结果"""
    text: str
    confidence: float
    language: str
    image_path: Optional[str] = None
    error: Optional[str] = None


class OCRProcessor:
    """OCR 处理器"""
    
    def __init__(self, languages: str = "eng+jpn+kor"):
        self.logger = logging.getLogger(__name__)
        self.languages = languages
        self.temp_dir = Path(tempfile.gettempdir()) / "screen_translator_ocr"
        self.temp_dir.mkdir(exist_ok=True)
        
        # 调试模式图像保存目录
        # 获取当前文件所在目录的父目录（screen-translator目录）
        current_file_dir = Path(__file__).parent.parent.parent
        self.debug_image_dir = current_file_dir.resolve() / "调试模式图像"
        # 如果设置了调试模式，创建调试图像目录
        if os.environ.get('SCREEN_TRANSLATOR_DEBUG'):
            self.debug_image_dir.mkdir(exist_ok=True, parents=True)

        # 是否启用预处理（可由配置关闭）
        self.preprocess_enabled = True

        # 新：对“白底黑字合成图”做轻度平滑后再二值化（用于小字边缘更顺滑）
        # 可选：none / gaussian / bilateral
        self.smooth_method: str = "gaussian"
        self.gaussian_ksize: int = 3
        self.gaussian_sigma: float = 0.0
        self.bilateral_d: int = 5
        self.bilateral_sigma_color: float = 50.0
        self.bilateral_sigma_space: float = 50.0
        
        # 语言特定参数 - 使用更稳定的配置
        self.language_params = {
            'jpn': {'psm': '6', 'oem': '3'},  # 日语：单行统一文本块
            'kor': {'psm': '6', 'oem': '3'},  # 韩语：单行统一文本块
            'rus': {'psm': '6', 'oem': '3'},  # 俄语：单行统一文本块
            'chi_sim': {'psm': '6', 'oem': '3'},  # 简体中文：单行统一文本块
            'chi_tra': {'psm': '6', 'oem': '3'},  # 繁体中文：单行统一文本块
            'eng': {'psm': '6', 'oem': '3'},  # 英语：单行统一文本块
            'fra': {'psm': '6', 'oem': '3'},  # 法语：单行统一文本块
            'deu': {'psm': '6', 'oem': '3'},  # 德语：单行统一文本块
            'spa': {'psm': '6', 'oem': '3'},  # 西班牙语：单行统一文本块
            'por': {'psm': '6', 'oem': '3'},  # 葡萄牙语：单行统一文本块
            'ita': {'psm': '6', 'oem': '3'},  # 意大利语：单行统一文本块
            'nld': {'psm': '6', 'oem': '3'},  # 荷兰语：单行统一文本块
            'ukr': {'psm': '6', 'oem': '3'},  # 乌克兰语：单行统一文本块
            'pol': {'psm': '6', 'oem': '3'},  # 波兰语：单行统一文本块
            'ces': {'psm': '6', 'oem': '3'},  # 捷克语：单行统一文本块
            'slk': {'psm': '6', 'oem': '3'},  # 斯洛伐克语：单行统一文本块
            'hun': {'psm': '6', 'oem': '3'},  # 匈牙利语：单行统一文本块
            'ron': {'psm': '6', 'oem': '3'},  # 罗马尼亚语：单行统一文本块
            'bul': {'psm': '6', 'oem': '3'},  # 保加利亚语：单行统一文本块
            'srp': {'psm': '6', 'oem': '3'},  # 塞尔维亚语：单行统一文本块
        }
        
        # 新：字芯颜色（用于颜色分割 OCR）
        # 约定：#RRGGBB
        self.core_color = "#FFFFFF"
        # 分割阈值（经验值；后续如需可做成 UI 可调）
        # - HSV：H 允许环形差值；S/V 允许线性差值
        # 注意：默认偏“严格”，避免把浅色背景/渐变背景误判为文字
        self.core_h_tol = 12
        self.core_s_tol = 55
        self.core_v_tol = 55
        # - Lab：欧氏距离阈值（近似 ΔE）
        self.core_lab_tol = 18.0

        # 调试：保存“文字掩码叠加图”（在 extract_text 中与原图/processed 配对落盘）
        self._last_debug_composited_image: Optional[Image.Image] = None
        # OCR：保留一份叠加图（白底黑字但未做闭运算），用于英文场景额外尝试，提高空格/细笔画识别率
        self._last_composited_for_ocr: Optional[Image.Image] = None

    def _get_available_tesseract_languages_set(self) -> set:
        langs = None
        try:
            langs = pytesseract.get_languages(config="")
        except Exception:
            langs = None

        if langs:
            try:
                return {str(x).strip() for x in langs if str(x).strip()}
            except Exception:
                pass

        tessdata_dir = None
        try:
            prefix = os.environ.get("TESSDATA_PREFIX")
            if prefix:
                tessdata_dir = Path(prefix)
        except Exception:
            tessdata_dir = None

        if tessdata_dir is None:
            try:
                current_dir = Path(__file__).parent.parent.parent
                tessdata_dir = current_dir / "tesseract" / "tessdata"
            except Exception:
                tessdata_dir = None

        out = set()
        try:
            if tessdata_dir and tessdata_dir.exists():
                for p in tessdata_dir.glob("*.traineddata"):
                    out.add(p.stem)
        except Exception:
            pass

        if not out:
            out.add("eng")
        return out

    def _filter_ocr_language_to_available(self, language: str) -> str:
        parts = [p.strip() for p in str(language or "").split("+") if p.strip()]
        if not parts:
            return "eng"

        avail = self._get_available_tesseract_languages_set()
        filtered: List[str] = []
        for p in parts:
            if p in avail and p not in filtered:
                filtered.append(p)

        if filtered:
            return "+".join(filtered)

        if "eng" in avail:
            return "eng"
        try:
            fallback = [p.strip() for p in str(self.languages or "").split("+") if p.strip()]
            for p in fallback:
                if p in avail:
                    return p
        except Exception:
            pass
        return "eng"

    def _decode_tesseract_bytes(self, data: bytes) -> str:
        if not data:
            return ""
        for enc in ("utf-8", "utf-8-sig", "cp936", "mbcs", "latin-1"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
            except Exception:
                break
        return data.decode("utf-8", errors="replace")

    def _run_tesseract_tsv(self, image: Image.Image, config: str) -> dict:
        tesseract_cmd = pytesseract.pytesseract.tesseract_cmd or "tesseract"
        tmp = tempfile.NamedTemporaryFile(suffix=".png", dir=str(self.temp_dir), delete=False)
        tmp_path = Path(tmp.name)
        tmp.close()

        try:
            image.save(tmp_path)
            cfg_args = shlex.split(str(config or ""), posix=False)
            args = [tesseract_cmd, str(tmp_path), "stdout", *cfg_args, "tsv"]

            extra_run_kwargs = {}
            if os.name == "nt":
                try:
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
                    extra_run_kwargs["startupinfo"] = startupinfo
                except Exception:
                    pass
                try:
                    extra_run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                except Exception:
                    pass

            result = subprocess.run(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                **extra_run_kwargs,
            )

            if result.returncode != 0:
                stderr_text = self._decode_tesseract_bytes(result.stderr)
                raise RuntimeError(stderr_text.strip() or f"tesseract 退出码 {result.returncode}")

            tsv_text = self._decode_tesseract_bytes(result.stdout)
            return self._tsv_text_to_data_dict(tsv_text)
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _tsv_text_to_data_dict(self, tsv_text: str) -> dict:
        lines = [ln for ln in (tsv_text or "").splitlines() if ln.strip()]
        if not lines:
            return {"text": [], "conf": [], "line_num": [], "par_num": [], "block_num": [], "left": [], "top": [], "width": [], "height": []}

        reader = csv.DictReader(lines, delimiter="\t")
        out = {"text": [], "conf": [], "line_num": [], "par_num": [], "block_num": [], "left": [], "top": [], "width": [], "height": []}
        for row in reader:
            out["text"].append(row.get("text", "") or "")
            out["conf"].append(row.get("conf", "") or "")
            out["line_num"].append(row.get("line_num", "0") or "0")
            out["par_num"].append(row.get("par_num", "0") or "0")
            out["block_num"].append(row.get("block_num", "0") or "0")
            out["left"].append(row.get("left", "0") or "0")
            out["top"].append(row.get("top", "0") or "0")
            out["width"].append(row.get("width", "0") or "0")
            out["height"].append(row.get("height", "0") or "0")
        return out

    def _safe_image_to_data(self, image: Image.Image, config: str) -> dict:
        try:
            return pytesseract.image_to_data(
                image,
                config=config,
                output_type=pytesseract.Output.DICT,
            )
        except UnicodeDecodeError:
            return self._run_tesseract_tsv(image, config=config)

    def _is_cjk_lang_code(self, code: str) -> bool:
        """判断单个语言代码是否属于 CJK（中/日/韩）。"""
        from src.utils.language_utils import is_cjk_lang_code as shared_is_cjk_lang_code
        return shared_is_cjk_lang_code(code)

    def _is_cjk_language(self, language: Optional[str]) -> bool:
        """
        判断 language（可能是 'eng+jpn+kor' 这种组合）是否应当按 CJK 处理。

        规则：
        - 只有当 language 中的语言代码“全部”属于 CJK 时，才视为 CJK。
        - 混合语言（包含 eng 等非 CJK）不直接判定为 CJK，交给文本内容占比再判断，避免误删空格。
        """
        from src.utils.language_utils import is_cjk_language as shared_is_cjk_language
        return shared_is_cjk_language(language)

    def apply_config(self, config_manager) -> None:
        """
        从 ConfigManager（或兼容接口）应用 OCR/预处理配置。

        约定：config_manager 提供 get/get_bool/get_int/get_float 方法。
        """
        try:
            self.preprocess_enabled = bool(config_manager.get_bool('ocr_preprocess', 'enabled', True))
        except Exception:
            self.preprocess_enabled = True

        # 新：字芯颜色
        try:
            core = str(config_manager.get('ocr', 'core_color', self.core_color) or "").strip()
            self.set_core_color(core)
        except Exception:
            pass

        # 平滑/去噪（作用于“合成白底黑字图”，不影响颜色分割 mask）
        try:
            v = str(config_manager.get('ocr_preprocess', 'smooth_method', self.smooth_method) or "").strip().lower()
            if v in ("none", "gaussian", "bilateral"):
                self.smooth_method = v
        except Exception:
            pass

        try:
            k = int(config_manager.get_int('ocr_preprocess', 'gaussian_ksize', self.gaussian_ksize))
            self.gaussian_ksize = k
        except Exception:
            pass

        try:
            s = float(config_manager.get_float('ocr_preprocess', 'gaussian_sigma', self.gaussian_sigma))
            self.gaussian_sigma = s
        except Exception:
            pass

        try:
            d = int(config_manager.get_int('ocr_preprocess', 'bilateral_d', self.bilateral_d))
            self.bilateral_d = d
        except Exception:
            pass

        try:
            sc = float(config_manager.get_float('ocr_preprocess', 'bilateral_sigma_color', self.bilateral_sigma_color))
            self.bilateral_sigma_color = sc
        except Exception:
            pass

        try:
            ss = float(config_manager.get_float('ocr_preprocess', 'bilateral_sigma_space', self.bilateral_sigma_space))
            self.bilateral_sigma_space = ss
        except Exception:
            pass

    def set_core_color(self, hex_color: str) -> None:
        """设置字芯颜色（#RRGGBB）。不合法则忽略。"""
        v = self._normalize_hex_color(hex_color)
        if v:
            self.core_color = v

    def _normalize_hex_color(self, value: str) -> Optional[str]:
        """把输入规范化为 #RRGGBB（大写）；不合法返回 None。"""
        if not value:
            return None
        v = str(value).strip()
        if re.fullmatch(r"[0-9a-fA-F]{6}", v):
            v = "#" + v
        if re.fullmatch(r"#[0-9a-fA-F]{3}", v):
            v = "#" + "".join([c * 2 for c in v[1:]])
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
            return None
        return v.upper()

    def _postprocess_text(self, text: str, language: str = None) -> str:
        """
        OCR 文本后处理：尽量不"改写语义"，只做清理与规范化。
        
        Args:
            text: 要处理的文本
            language: OCR语言代码，用于决定是否移除空格（日文/韩文/中文不需要单词间空格）
        """
        if not isinstance(text, str):
            return ""
        # 统一换行与空白
        t = text.replace('\r\n', '\n').replace('\r', '\n')
        
        # 对于日文/韩文/中文，移除单词之间的空格（保留换行和段落分隔）。
        # 注意：混合语言（如 'eng+jpn+kor'）不应直接当作 CJK，否则会误删英文空格。
        is_cjk = self._is_cjk_language(language)
        
        # 如果没有指定语言，或者语言判断不明确，尝试检测文本是否包含CJK字符
        if not is_cjk:
            cjk_chars = sum(1 for c in t if ('\u3040' <= c <= '\u30ff') or ('\u4e00' <= c <= '\u9fff') or ('\uac00' <= c <= '\ud7a3'))
            total_chars = len([c for c in t if not c.isspace()])
            if total_chars > 0 and cjk_chars / total_chars > 0.3:
                is_cjk = True
        
        if is_cjk:
            # 对于CJK语言，移除单词之间的空格，但保留换行符
            # 按行处理，移除行内的空格
            lines = t.split('\n')
            cleaned_lines = []
            for line in lines:
                if not line.strip():
                    cleaned_lines.append("")
                    continue
                # 移除行内的空格（日文/韩文/中文不需要单词间空格）
                cleaned_line = re.sub(r'[ \t\f\v]+', '', line)
                cleaned_lines.append(cleaned_line)
            t = '\n'.join(cleaned_lines)
            # 对于CJK语言，清理换行前后的空白
            t = re.sub(r'\n[ \t]+', '\n', t)
            t = re.sub(r'[ \t]+\n', '\n', t)

            # CJK 同样常见“自动换行”：把单换行折叠（直接连接），保留双换行作为段落分隔
            # 例：
            #   "今\n天" -> "今天"
            #   "段1\n\n段2" 保持分段
            t = re.sub(r'\n{3,}', '\n\n', t)
            _para_token = "__ST_PARA__"
            t = t.replace("\n\n", _para_token)
            t = re.sub(r'\s*\n\s*', '', t)
            t = t.replace(_para_token, "\n\n")
        else:
            lang_lower = str(language or "").lower()
            if re.search(r"[\u0400-\u04FF]", t):
                t = t.translate(str.maketrans({
                    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X",
                    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j",
                }))

            if "zul" in lang_lower or "swa" in lang_lower:
                t = t.replace("一", "—")

            # 对于非CJK语言（如英文），保留单词间的空格
            # 只规范化多个连续空格为单个空格，但保留换行前后的空格（可能是单词的一部分）
            t = re.sub(r'[ \t\f\v]+', ' ', t)

            t = re.sub(r'\n{3,}', '\n\n', t)
            _para_token = "__ST_PARA__"
            t = t.replace("\n\n", _para_token)
            t = re.sub(r'\s*\n\s*', ' ', t)
            t = t.replace(_para_token, "\n\n")
            t = re.sub(r'[ \t]+\n', '\n', t)
            t = re.sub(r'\n[ \t]+', '\n', t)

            t = re.sub(r"([^\n\.\!\?;:\]\)\}”\"’'])\n\n(?=[a-z])", r"\1 ", t)

            # OCR 常见误识别：把大写 I 识别成竖线 '|'
            # 仅在英文语言场景、且 '|' 是独立 token 且后面跟小写字母（I went / I felt）时纠正，避免误改真实竖线。
            if 'eng' in lang_lower:
                t = re.sub(r'(^|[\s])\|(?=\s+[a-z])', r'\1I', t)
                t = re.sub(r'(^|[\s])\|(?=\s+i\s)', r'\1I', t, flags=re.IGNORECASE)
        
        # 清理多余的连续换行（保留最多两个连续换行）
        t = re.sub(r'\n{3,}', '\n\n', t)
        return t.strip()

    def _data_to_text_keep_lines(self, data: dict, language: str = None) -> Tuple[str, List[float]]:
        """
        将 pytesseract.image_to_data 的输出重组成"分行文本"。

        - 每个 line_num 输出一行
        - 段落切换（par_num/ block_num 变化）在“垂直间距明显变大”时才插入空行（避免误判）
        - 对于非CJK语言，使用位置信息判断单词间是否应该有空格
        
        Args:
            data: pytesseract 输出的数据字典
            language: OCR语言代码，用于决定是否在单词间添加空格
        """
        try:
            texts = data.get('text') or []
            conf_list = data.get('conf') or []
            line_nums = data.get('line_num') or [0] * len(texts)
            par_nums = data.get('par_num') or [0] * len(texts)
            block_nums = data.get('block_num') or [0] * len(texts)
            lefts = data.get('left') or [0] * len(texts)
            tops = data.get('top') or [0] * len(texts)
            widths = data.get('width') or [0] * len(texts)
            heights = data.get('height') or [0] * len(texts)
        except Exception:
            return "", []

        # 判断是否为CJK语言（日文/韩文/中文）。混合语言不直接当作 CJK，避免误删空格。
        is_cjk = self._is_cjk_language(language)
        lang_lower = str(language or "").lower()

        lines: List[str] = []
        all_confs: List[float] = []

        current_key = None  # (block, par, line)
        current_words: List[str] = []
        current_word_info: List[Tuple[int, int, int, int]] = []  # (left, top, width, height)
        current_confs: List[float] = []
        prev_block_par = None  # (block, par)
        prev_line_bottom: Optional[int] = None
        prev_line_height: Optional[float] = None

        def _flush_line():
            nonlocal current_words, current_word_info, current_confs, prev_line_bottom, prev_line_height
            if current_words:
                if is_cjk:
                    # 对于CJK语言，直接连接单词（不添加空格）
                    lines.append(''.join(current_words))
                else:
                    # 对于非CJK语言，使用位置信息判断单词间是否应该有空格
                    if len(current_words) == 1:
                        lines.append(current_words[0])
                    else:
                        line_text = current_words[0]
                        # 估计该行平均字符宽度（用于空间阈值）
                        try:
                            char_widths = []
                            for idx, word in enumerate(current_words):
                                if idx < len(current_word_info):
                                    w_info = current_word_info[idx][2]
                                    n = max(1, len(word))
                                    if w_info and w_info > 0:
                                        char_widths.append(float(w_info) / float(n))
                            avg_char_w = float(np.median(char_widths)) if char_widths else 0.0
                        except Exception:
                            avg_char_w = 0.0

                        for j in range(1, len(current_words)):
                            prev_info = current_word_info[j-1]
                            curr_info = current_word_info[j]
                            
                            # 计算前一个单词的右边界和后一个单词的左边界
                            prev_right = prev_info[0] + prev_info[2]  # left + width
                            curr_left = curr_info[0]  # left
                            gap = curr_left - prev_right
                            
                            # 计算平均字符宽度（用于判断间距阈值）
                            avg_char_width = 0
                            if prev_info[2] > 0 and len(current_words[j-1]) > 0:
                                avg_char_width = prev_info[2] / len(current_words[j-1])
                            
                            # 空格恢复（更稳）：gap 超过阈值才插入空格，避免乱插导致“空格不准”
                            if gap > 0:
                                # 阈值：与平均字符宽度相关；再加像素下限，适配小字体
                                # 经验：0.35~0.55 倍字符宽度通常对应真正词间空隙
                                base = avg_char_w if avg_char_w > 0 else avg_char_width
                                space_threshold = max(3.0, float(base) * 0.45) if base > 0 else 4.0
                                if gap >= space_threshold:
                                    line_text += ' ' + current_words[j]
                                else:
                                    line_text += current_words[j]
                            else:
                                line_text += current_words[j]
                        
                        lines.append(line_text)
                all_confs.extend(current_confs)

                # 记录上一行的垂直位置（用于判定是否需要插入“段落空行”）
                try:
                    if current_word_info:
                        bottoms_ = [t + h for _, t, _, h in current_word_info if h and h > 0]
                        heights_ = [h for _, _, _, h in current_word_info if h and h > 0]
                        prev_line_bottom = int(max(bottoms_)) if bottoms_ else prev_line_bottom
                        prev_line_height = float(np.mean(heights_)) if heights_ else prev_line_height
                except Exception:
                    # 位置数据只是辅助，不应影响主流程
                    pass
            current_words = []
            current_word_info = []
            current_confs = []

        for i in range(len(texts)):
            w = str(texts[i] or "").strip()
            if not w:
                continue

            key = (int(block_nums[i]), int(par_nums[i]), int(line_nums[i]))

            if current_key is None:
                current_key = key
                prev_block_par = (key[0], key[1])
            elif key != current_key:
                # 新的一行：先输出上一行
                _flush_line()

                # 段落/块发生变化：仅当“垂直间距明显变大”时插入空行，避免把正常换行误判为分段。
                # 英文也需要段落：但阈值更严格，减少误判。
                bp = (key[0], key[1])
                if prev_block_par is not None and bp != prev_block_par:
                    try:
                        curr_top = int(tops[i]) if i < len(tops) else 0
                        if prev_line_bottom is None or prev_line_height is None or float(prev_line_height) <= 0:
                            gap = 0
                        else:
                            gap = curr_top - int(prev_line_bottom)

                        # 阈值：英文更严格（更像“段落间距”才算分段），其他语言相对宽松。
                        base_h = float(prev_line_height) if prev_line_height is not None else 0.0
                        if (not is_cjk) and ('eng' in lang_lower):
                            para_gap_threshold = max(base_h * 1.8, 28.0)
                        else:
                            para_gap_threshold = max(base_h * 1.3, 18.0)

                        if gap >= para_gap_threshold:
                            if not lines or lines[-1] != "":
                                lines.append("")
                    except Exception:
                        # 保守：发生异常时不插入空行，避免误判分段
                        pass
                prev_block_par = bp
                current_key = key

            current_words.append(w)
            try:
                left = int(lefts[i]) if i < len(lefts) else 0
                top = int(tops[i]) if i < len(tops) else 0
                width = int(widths[i]) if i < len(widths) else 0
                height = int(heights[i]) if i < len(heights) else 0
                current_word_info.append((left, top, width, height))
            except Exception:
                current_word_info.append((0, 0, 0, 0))
            
            try:
                c = float(conf_list[i])
                if c >= 0:
                    current_confs.append(c)
            except Exception:
                pass

        _flush_line()

        text = '\n'.join(lines)
        return text, all_confs
    
    def detect_language(self, image: Image.Image) -> str:
        """检测图像中的语言类型"""
        try:
            # 直接使用统一的语言检测工具函数，确保一致性
            from src.utils.language_utils import detect_language as shared_detect_language
            
            # 临时提取文本进行语言检测 - 使用中日韩英4种主要语言，保持检测精度
            detect_langs = 'eng+jpn+kor+chi_sim+chi_tra'
            processed = self.preprocess_image(image)
            config = f'--oem 3 --psm 6 -l {detect_langs}'
            temp_result = self.extract_text_with_config(processed, detect_langs, config)
            text = temp_result.text
            
            if not text.strip():
                return 'auto'
            
            # 使用统一的语言检测函数
            return shared_detect_language(text)
            
        except Exception:
            return 'auto'
    
    def preprocess_image(self, image: Image.Image) -> Image.Image:
        """
        新预处理链路（删除旧逻辑）：
        颜色分割（HSV/Lab，围绕用户指定“字芯颜色”）→ 二值化 → 轻度形态修复 → 输出给 OCR
        """
        cv_bgr = None
        hsv = None
        lab = None
        composited_bgr = None
        try:
            if not self.preprocess_enabled:
                return image

            # 统一为 RGB
            if image.mode != "RGB":
                image = image.convert("RGB")

            cv_bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)

            # 解析目标颜色（#RRGGBB -> BGR/HSV/Lab）
            core_hex = self._normalize_hex_color(self.core_color) or "#FFFFFF"
            r = int(core_hex[1:3], 16)
            g = int(core_hex[3:5], 16)
            b = int(core_hex[5:7], 16)
            core_bgr = np.array([[[b, g, r]]], dtype=np.uint8)
            core_hsv = cv2.cvtColor(core_bgr, cv2.COLOR_BGR2HSV)[0, 0].astype(np.int32)
            core_lab = cv2.cvtColor(core_bgr, cv2.COLOR_BGR2LAB)[0, 0].astype(np.int32)

            # 颜色空间转换
            hsv = cv2.cvtColor(cv_bgr, cv2.COLOR_BGR2HSV).astype(np.int32)
            lab = cv2.cvtColor(cv_bgr, cv2.COLOR_BGR2LAB).astype(np.int32)

            def _make_mask(h_tol: int, s_tol: int, v_tol: int, lab_tol: float) -> np.ndarray:
                # HSV 阈值：H 环形距离 + S/V 线性距离
                h = hsv[:, :, 0]
                s = hsv[:, :, 1]
                v = hsv[:, :, 2]

                dh = np.abs(h - int(core_hsv[0]))
                dh2 = 180 - dh
                dh = np.minimum(dh, dh2)  # Hue 环形
                ds = np.abs(s - int(core_hsv[1]))
                dv = np.abs(v - int(core_hsv[2]))

                # 当字芯颜色饱和度很低（接近白/灰/黑）时，Hue 没意义，HSV 容易“放大误差”吞背景
                core_s = int(core_hsv[1])
                core_v = int(core_hsv[2])

                if core_s <= 18:
                    # 低饱和：只约束 S 较低 + V 接近
                    s_max = min(60, core_s + int(s_tol))
                    mask_hsv = (s <= s_max) & (dv <= int(v_tol))
                else:
                    mask_hsv = (dh <= int(h_tol)) & (ds <= int(s_tol)) & (dv <= int(v_tol))

                # Lab 距离：近似 ΔE
                dl = lab[:, :, 0] - int(core_lab[0])
                da = lab[:, :, 1] - int(core_lab[1])
                db = lab[:, :, 2] - int(core_lab[2])
                dist_lab = np.sqrt(dl * dl + da * da + db * db)
                mask_lab = dist_lab <= float(lab_tol)

                # 严格交集：同时满足 Lab 与 HSV 约束，减少背景误选
                return (mask_hsv & mask_lab).astype(np.uint8) * 255

            def _mask_ratio(m: np.ndarray) -> float:
                try:
                    return float(np.count_nonzero(m)) / float(m.size)
                except Exception:
                    return 0.0

            # 先用“严格”阈值生成种子 mask
            mask_seed = _make_mask(
                int(self.core_h_tol),
                int(self.core_s_tol),
                int(self.core_v_tol),
                float(self.core_lab_tol),
            )
            seed_ratio = _mask_ratio(mask_seed)

            # 再用“放宽”阈值生成候选 mask
            mask_relaxed = _make_mask(
                int(self.core_h_tol * 2),
                int(self.core_s_tol * 2),
                int(self.core_v_tol * 2),
                float(self.core_lab_tol * 1.8),
            )

            # 只在“种子附近”允许放宽
            try:
                neigh_kernel = np.ones((5, 5), np.uint8)
                neigh = cv2.dilate(mask_seed, neigh_kernel, iterations=1)
            except Exception:
                neigh = mask_seed

            mask = mask_seed.copy()
            mask[(neigh == 255) & (mask_relaxed == 255)] = 255

            # 若种子过少，直接用放宽 mask 兜底一次
            if seed_ratio < 0.0002:
                mask = mask_relaxed

            # 若最终 mask 覆盖过大，回退到种子 mask
            if _mask_ratio(mask) > 0.25:
                mask = mask_seed

            # 为了覆盖抗锯齿边缘，轻微膨胀掩码 1 次
            try:
                mask_kernel = np.ones((2, 2), np.uint8)
                mask2 = cv2.dilate(mask, mask_kernel, iterations=1)
            except Exception:
                mask2 = mask

            # 合成图：白底 + 黑字
            composited_bgr = np.full_like(cv_bgr, 255, dtype=np.uint8)
            composited_bgr[mask2 == 255] = (0, 0, 0)

            # 缓存一份 composited 给 OCR 额外尝试（尤其英文空格/细笔画）
            try:
                composited_rgb_for_ocr = cv2.cvtColor(composited_bgr, cv2.COLOR_BGR2RGB)
                if self._last_composited_for_ocr is not None:
                    try:
                        self._last_composited_for_ocr.close()
                    except Exception:
                        pass
                self._last_composited_for_ocr = Image.fromarray(composited_rgb_for_ocr)
            except Exception:
                self._last_composited_for_ocr = None

            # 调试：缓存叠加图，交由 extract_text 统一命名落盘
            if os.environ.get('SCREEN_TRANSLATOR_DEBUG'):
                try:
                    composited_rgb = cv2.cvtColor(composited_bgr, cv2.COLOR_BGR2RGB)
                    if self._last_debug_composited_image is not None:
                        try:
                            self._last_debug_composited_image.close()
                        except Exception:
                            pass
                    self._last_debug_composited_image = Image.fromarray(composited_rgb)
                except Exception:
                    self._last_debug_composited_image = None

            # 二值图：默认直接由 mask 得到（白底黑字）
            binary = np.full(mask2.shape, 255, dtype=np.uint8)
            binary[mask2 == 255] = 0
            try:
                method = (self.smooth_method or "none").strip().lower()
            except Exception:
                method = "none"

            try:
                if method != "none":
                    # composited_bgr 本身是 0/255，滤波后会产生灰度过渡边缘，再阈值回二值
                    comp_gray = cv2.cvtColor(composited_bgr, cv2.COLOR_BGR2GRAY)

                    if method == "gaussian":
                        k = int(self.gaussian_ksize) if int(self.gaussian_ksize) > 0 else 3
                        if k % 2 == 0:
                            k += 1
                        if k < 3:
                            k = 3
                        sigma = float(self.gaussian_sigma) if float(self.gaussian_sigma) >= 0 else 0.0
                        comp_gray = cv2.GaussianBlur(comp_gray, (k, k), sigmaX=sigma, sigmaY=sigma)
                    elif method == "bilateral":
                        d = int(self.bilateral_d) if int(self.bilateral_d) > 0 else 5
                        sc = float(self.bilateral_sigma_color) if float(self.bilateral_sigma_color) > 0 else 50.0
                        ss = float(self.bilateral_sigma_space) if float(self.bilateral_sigma_space) > 0 else 50.0
                        comp_gray = cv2.bilateralFilter(comp_gray, d=d, sigmaColor=sc, sigmaSpace=ss)

                    _, binary2 = cv2.threshold(comp_gray, 127, 255, cv2.THRESH_BINARY)
                    binary = binary2
            except Exception:
                pass

            # 过滤明显的“背景装饰圆点/大块噪声”（仅在覆盖偏大时启用）
            try:
                if _mask_ratio(mask2) > 0.18:
                    inv0 = cv2.bitwise_not(binary)  # 前景=255
                    contours, _ = cv2.findContours(inv0, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                    boxes = []
                    areas = []
                    heights = []
                    for cnt in contours or []:
                        x, y, w, h = cv2.boundingRect(cnt)
                        if w <= 0 or h <= 0:
                            continue
                        a = float(cv2.contourArea(cnt))
                        if a <= 1.0:
                            continue
                        boxes.append((cnt, x, y, w, h, a))
                        areas.append(a)
                        heights.append(float(h))

                    if areas:
                        med_area = float(np.median(areas))
                        med_h = float(np.median(heights)) if heights else 0.0

                        for (cnt, x, y, w, h, a) in boxes:
                            if med_area > 0 and a < med_area * 8.0:
                                continue
                            if med_h > 0 and float(h) < med_h * 1.6:
                                continue

                            peri = float(cv2.arcLength(cnt, True))
                            if peri <= 1.0:
                                continue
                            circularity = 4.0 * math.pi * a / (peri * peri)
                            if circularity >= 0.78:
                                cv2.drawContours(inv0, [cnt], -1, 0, thickness=cv2.FILLED)

                        binary = cv2.bitwise_not(inv0)
            except Exception:
                pass

            # 不跨字处理：按“行/字块ROI”单独做轻度闭运算，避免字间粘连吞空格
            try:
                inv = cv2.bitwise_not(binary)  # 前景=255

                # 先做一轮轻度 opening 去噪
                try:
                    k2 = np.ones((2, 2), np.uint8)
                    inv = cv2.erode(inv, k2, iterations=1)
                    inv = cv2.dilate(inv, k2, iterations=1)
                except Exception:
                    pass

                contours, _ = cv2.findContours(inv, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                boxes = []
                for c in contours or []:
                    x, y, w, h = cv2.boundingRect(c)
                    if w <= 0 or h <= 0:
                        continue
                    if w * h < 6:
                        continue
                    boxes.append((x, y, w, h))

                if boxes:
                    heights = [h for _, _, _, h in boxes]
                    median_h = float(np.median(heights)) if heights else 10.0
                    line_tol = max(6.0, median_h * 0.6)

                    boxes_sorted = sorted(boxes, key=lambda b: (b[1] + b[3] / 2.0, b[0]))
                    lines: list[list[tuple[int, int, int, int]]] = []
                    for b in boxes_sorted:
                        cy = b[1] + b[3] / 2.0
                        placed = False
                        for line in lines:
                            ref = line[0][1] + line[0][3] / 2.0
                            if abs(cy - ref) <= line_tol:
                                line.append(b)
                                placed = True
                                break
                        if not placed:
                            lines.append([b])

                    close_kernel = np.ones((2, 2), np.uint8)
                    H, W = inv.shape[:2]
                    for line in lines:
                        line = sorted(line, key=lambda b: b[0])
                        widths = [w for _, _, w, _ in line]
                        avg_w = float(np.median(widths)) if widths else 12.0
                        gap_threshold = max(4.0, avg_w * 0.6)

                        blocks: list[tuple[int, int, int, int]] = []
                        bx0, by0, bx1, by1 = None, None, None, None
                        prev_right = None
                        for (x, y, w, h) in line:
                            right = x + w
                            bottom = y + h
                            if bx0 is None:
                                bx0, by0, bx1, by1 = x, y, right, bottom
                                prev_right = right
                                continue
                            gap = float(x - (prev_right or x))
                            if gap >= gap_threshold:
                                blocks.append((int(bx0), int(by0), int(bx1), int(by1)))
                                bx0, by0, bx1, by1 = x, y, right, bottom
                            else:
                                bx0 = min(bx0, x)
                                by0 = min(by0, y)
                                bx1 = max(bx1, right)
                                by1 = max(by1, bottom)
                            prev_right = max(prev_right or right, right)
                        if bx0 is not None:
                            blocks.append((int(bx0), int(by0), int(bx1), int(by1)))

                        for (x0, y0, x1, y1) in blocks:
                            pad = 2
                            rx0 = max(0, x0 - pad)
                            ry0 = max(0, y0 - pad)
                            rx1 = min(W, x1 + pad)
                            ry1 = min(H, y1 + pad)
                            roi = inv[ry0:ry1, rx0:rx1]
                            if roi.size == 0:
                                continue
                            roi2 = cv2.morphologyEx(roi, cv2.MORPH_CLOSE, close_kernel, iterations=1)
                            inv[ry0:ry1, rx0:rx1] = roi2

                binary = cv2.bitwise_not(inv)
            except Exception:
                pass

            return Image.fromarray(binary)
        except Exception as e:
            print(f"图像预处理失败(颜色分割链路): {e}")
            return image
        finally:
            try:
                del cv_bgr, hsv, lab, composited_bgr
            except Exception:
                pass
    
    def extract_text_with_config(self, image: Image.Image, language: str, config: str) -> OCRResult:
        """使用指定配置提取文本"""
        try:
            data = self._safe_image_to_data(image, config=config)

            # 重组为分行文本
            full_text, confidences = self._data_to_text_keep_lines(data, language=language)
            full_text = self._postprocess_text(full_text, language=language)

            # 计算平均置信度（忽略 -1 等无效值）
            avg_confidence = float(np.mean(confidences)) if confidences else 0.0
            
            return OCRResult(
                text=full_text.strip(),
                confidence=avg_confidence,
                language=language,
                image_path=None
            )
            
        except Exception as e:
            return OCRResult(
                text='',
                confidence=0.0,
                language=language,
                error=f'OCR 失败: {str(e)}'
            )
    
    def get_ocr_language_from_source_lang(self, source_lang: str) -> str:
        """根据源语言获取OCR语言设置"""
        lang_mapping = {
            "英语": "eng",
            "日语": "jpn",
            "韩语": "kor",
            "俄语": "rus",
            "中文": "chi_sim",
            "中文（简体）": "chi_sim",
            "中文（繁体）": "chi_tra",
            "法语": "fra",
            "德语": "deu",
            "西班牙语": "spa",
            "葡萄牙语": "por",
            "意大利语": "ita",
            "荷兰语": "nld",
            "乌克兰语": "ukr",
            "波兰语": "pol",
            "捷克语": "ces",
            "斯洛伐克语": "slk",
            "匈牙利语": "hun",
            "罗马尼亚语": "ron",
            "保加利亚语": "bul",
            "塞尔维亚语": "srp",
            "克罗地亚语": "hrv",
            "斯洛文尼亚语": "slv",
            "立陶宛语": "lit",
            "拉脱维亚语": "lav",
            "爱沙尼亚语": "est",
            "瑞典语": "swe",
            "挪威语": "nor",
            "丹麦语": "dan",
            "芬兰语": "fin",
            "冰岛语": "isl",
            "希腊语": "ell",
            "土耳其语": "tur",
            "希伯来语": "heb",
            "印地语": "hin",
            "孟加拉语": "ben",
            "泰米尔语": "tam",
            "泰卢固语": "tel",
            "卡纳达语": "kan",
            "马拉地语": "mar",
            "古吉拉特语": "guj",
            "旁遮普语": "pan",
            "泰语": "tha",
            "老挝语": "lao",
            "高棉语": "khm",
            "印尼语": "ind",
            "马来语": "msa",
            "菲律宾语": "fil",
            "斯瓦希里语": "swa",
            "祖鲁语": "zul",
        }
        
        # 如果源语言是中文，优先使用简体中文
        if source_lang == "中文":
            return "chi_sim"
        if source_lang == "中文（繁体）":
            return "chi_tra"
        
        # 对于其他语言，使用映射
        desired = lang_mapping.get(source_lang, "eng+jpn+kor+rus")
        return self._filter_ocr_language_to_available(desired)
    
    def _map_detected_to_ocr_language(self, detected_lang: str) -> str:
        """
        将检测到的语言代码映射为OCR语言代码
        
        Args:
            detected_lang: 检测到的语言代码（如 'ja', 'ko', 'ru'）
            
        Returns:
            OCR语言代码（如 'jpn', 'kor', 'rus'）
        """
        detection_to_ocr_mapping = {
            'ja': 'jpn',      # 日语
            'ko': 'kor',      # 韩语
            'ru': 'rus',      # 俄语
            'zh': 'chi_sim',  # 中文
            'en': 'eng',      # 英语
            'fr': 'fra',      # 法语
            'de': 'deu',      # 德语
            'es': 'spa',      # 西班牙语
            'pt': 'por',      # 葡萄牙语
            'it': 'ita',      # 意大利语
            'nl': 'nld',      # 荷兰语
            'uk': 'ukr',      # 乌克兰语
            'pl': 'pol',      # 波兰语
            'cs': 'ces',      # 捷克语
            'sk': 'slk',      # 斯洛伐克语
            'hu': 'hun',      # 匈牙利语
            'ro': 'ron',      # 罗马尼亚语
            'bg': 'bul',      # 保加利亚语
            'sr': 'srp',      # 塞尔维亚语
            'hr': 'hrv',      # 克罗地亚语
            'sl': 'slv',      # 斯洛文尼亚语
            'lt': 'lit',      # 立陶宛语
            'lv': 'lav',      # 拉脱维亚语
            'et': 'est',      # 爱沙尼亚语
            'sv': 'swe',      # 瑞典语
            'no': 'nor',      # 挪威语
            'da': 'dan',      # 丹麦语
            'fi': 'fin',      # 芬兰语
            'is': 'isl',      # 冰岛语
            'el': 'ell',      # 希腊语
            'tr': 'tur',      # 土耳其语
            'he': 'heb',      # 希伯来语
            'hi': 'hin',      # 印地语
            'bn': 'ben',      # 孟加拉语
            'ta': 'tam',      # 泰米尔语
            'te': 'tel',      # 泰卢固语
            'kn': 'kan',      # 卡纳达语
            'mr': 'mar',      # 马拉地语
            'gu': 'guj',      # 古吉拉特语
            'pa': 'pan',      # 旁遮普语
            'th': 'tha',      # 泰语
            'lo': 'lao',      # 老挝语
            'km': 'khm',      # 高棉语
            'id': 'ind',      # 印尼语
            'ms': 'msa',      # 马来语
            'fil': 'fil',     # 菲律宾语
            'sw': 'swa',      # 斯瓦希里语
            'zu': 'zul',      # 祖鲁语
        }
        
        desired = detection_to_ocr_mapping.get(detected_lang, 'eng+jpn+kor')
        return self._filter_ocr_language_to_available(desired)
    
    def extract_text(self, image: Image.Image, source_lang: str = "自动检测") -> OCRResult:
        """
        从图像中提取文字
        
        Args:
            image: PIL Image 对象
            source_lang: 源语言名称，用于确定OCR语言
        
        Returns:
            OCRResult 对象
        """
        debug_image_path = None
        processed_image = None
        try:
            # 确保 pytesseract 已经知道 tesseract 的位置
            # 如果没有配置 cmd 且本地存在，尝试配置一下（作为双重保险）
            if not pytesseract.pytesseract.tesseract_cmd or pytesseract.pytesseract.tesseract_cmd == 'tesseract':
                from pathlib import Path
                # 尝试查找本地目录
                current_dir = Path(__file__).parent.parent.parent
                local_tesseract = current_dir / "tesseract" / "tesseract.exe"
                if local_tesseract.exists():
                    pytesseract.pytesseract.tesseract_cmd = str(local_tesseract)
                    os.environ['TESSDATA_PREFIX'] = str(current_dir / "tesseract" / "tessdata")

            # 保存原始图像用于调试（带时间戳，便于对比每次截图）
            if os.environ.get('SCREEN_TRANSLATOR_DEBUG'):
                # 确保调试图像目录存在
                if not self.debug_image_dir.exists():
                    self.debug_image_dir.mkdir(exist_ok=True, parents=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                debug_image_path = self.debug_image_dir / f"debug_original_{ts}.png"
                try:
                    image.save(debug_image_path)
                except Exception:
                    debug_image_path = None

            # 预处理图像（OpenCV 缩放/去噪/对比度增强/二值化...）
            processed_image = self.preprocess_image(image)
            
            # 根据源语言确定OCR语言
            ocr_language = self.get_ocr_language_from_source_lang(source_lang)
            


            # 保存预处理后的图像用于调试（与原图同一时间戳）
            if os.environ.get('SCREEN_TRANSLATOR_DEBUG'):
                try:
                    if not self.debug_image_dir.exists():
                        self.debug_image_dir.mkdir(exist_ok=True, parents=True)
                    ts2 = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    if debug_image_path is not None:
                        name = debug_image_path.name.replace("debug_original_", "debug_processed_")
                        debug_processed_path = self.debug_image_dir / name
                    else:
                        debug_processed_path = self.debug_image_dir / f"debug_processed_{ts2}.png"

                    # 额外保存“文字掩码叠加图”（debug_composited_*.png）
                    try:
                        composited_img = getattr(self, "_last_debug_composited_image", None)
                        if composited_img is not None:
                            if debug_image_path is not None:
                                composited_name = debug_image_path.name.replace("debug_original_", "debug_composited_")
                                debug_composited_path = self.debug_image_dir / composited_name
                            else:
                                debug_composited_path = self.debug_image_dir / f"debug_composited_{ts2}.png"
                            composited_img.save(debug_composited_path)
                    except Exception:
                        pass
                    finally:
                        try:
                            if getattr(self, "_last_debug_composited_image", None) is not None:
                                self._last_debug_composited_image.close()
                        except Exception:
                            pass
                        self._last_debug_composited_image = None

                    processed_image.save(debug_processed_path)
                except Exception:
                    pass
            
            # 判断是否为CJK语言（只有当语言列表全部是 CJK 才视为 CJK；否则交给文本内容判断）
            is_cjk = self._is_cjk_language(ocr_language)
            
            # 对于非CJK语言，尝试多种方法获取带空格的文本
            if not is_cjk:
                # 尝试多种 PSM 模式，找到能最好保留空格的模式
                psm_modes = ['3', '4', '6', '11', '13']  # 4=单列文本；对字幕/段落更稳
                lang_config = self.language_params.get(ocr_language.split('+')[0], {'psm': '6', 'oem': '3'})
                oem = lang_config.get('oem', '3')
                
                best_text = None
                best_confidence = 0.0
                best_has_space = False
                
                # 首先尝试预处理后的图像
                images_to_try = [(processed_image, "processed")]
                # 也尝试 composited（白底黑字但未闭运算），通常更利于英文空格/细笔画
                try:
                    comp = getattr(self, "_last_composited_for_ocr", None)
                    if comp is not None:
                        images_to_try.append((comp, "composited"))
                except Exception:
                    pass
                # 也尝试原始图像（预处理可能破坏了空格）
                images_to_try.append((image, "original"))
                
                for img_to_use, img_type in images_to_try:
                    for psm in psm_modes:
                        try:
                            # 强制保留词间空格（对英文非常关键）
                            config = f'--oem {oem} --psm {psm} -l {ocr_language} -c preserve_interword_spaces=1'
                            # 使用 image_to_string 获取原始文本
                            raw_text = pytesseract.image_to_string(img_to_use, config=config)
                            
                            if raw_text and raw_text.strip():
                                has_space = ' ' in raw_text
                                
                                # 如果找到包含空格的文本，优先使用它
                                if has_space and not best_has_space:
                                    # 获取置信度
                                    data = self._safe_image_to_data(img_to_use, config=config)
                                    confidences = [float(c) for c in (data.get('conf') or []) if c and float(c) >= 0]
                                    avg_confidence = float(np.mean(confidences)) if confidences else 0.0
                                    
                                    # 后处理：保留空格，但合并单换行、修正常见误识别
                                    full_text = self._postprocess_text(raw_text, language=ocr_language)
                                    
                                    return OCRResult(
                                        text=full_text,
                                        confidence=avg_confidence,
                                        language=ocr_language,
                                        image_path=str(debug_image_path) if debug_image_path else None
                                    )
                                elif not best_has_space:
                                    # 如果没有找到空格，但文本更长，可能更好
                                    if best_text is None or len(raw_text) > len(best_text):
                                        data = self._safe_image_to_data(img_to_use, config=config)
                                        confidences = [float(c) for c in (data.get('conf') or []) if c and float(c) >= 0]
                                        avg_confidence = float(np.mean(confidences)) if confidences else 0.0
                                        best_text = raw_text
                                        best_confidence = avg_confidence
                        except Exception as e:
                            import logging
                            logger = logging.getLogger(__name__)
                            logger.debug(f"PSM {psm} ({img_type}) 失败: {e}")
                            continue
                
                # 如果所有模式都没有找到空格，使用最佳结果
                if best_text:
                    full_text = self._postprocess_text(best_text, language=ocr_language)
                    
                    return OCRResult(
                        text=full_text,
                        confidence=best_confidence,
                        language=ocr_language,
                        image_path=str(debug_image_path) if debug_image_path else None
                    )
            
            # 使用默认配置（对于CJK语言或所有方法都失败的情况）
            lang_config = self.language_params.get(ocr_language.split('+')[0], {'psm': '6', 'oem': '3'})
            # 非 CJK 同样加 preserve_interword_spaces（不影响 CJK）
            config = f'--oem {lang_config["oem"]} --psm {lang_config["psm"]} -l {ocr_language} -c preserve_interword_spaces=1'
            
            # 提取文本和置信度（使用 image_to_data）
            data = self._safe_image_to_data(processed_image, config=config)

            # 重组为分行文本（按 line_num 还原换行）
            full_text, confidences = self._data_to_text_keep_lines(data, language=ocr_language)
            full_text = self._postprocess_text(full_text, language=ocr_language)

            # 计算平均置信度（忽略 -1 等无效值）
            avg_confidence = float(np.mean(confidences)) if confidences else 0.0
            
            return OCRResult(
                text=full_text.strip(),
                confidence=avg_confidence,
                language=ocr_language,
                image_path=str(debug_image_path) if debug_image_path else None
            )
            
        except pytesseract.TesseractNotFoundError:
            cmd = pytesseract.pytesseract.tesseract_cmd
            return OCRResult(
                text='',
                confidence=0.0,
                language=self.languages,
                error=f'Tesseract-OCR 未找到。尝试使用的路径: "{cmd}"。请确保已正确安装 Tesseract。'
            )
            
        except Exception as e:
            return OCRResult(
                text='',
                confidence=0.0,
                language=self.languages,
                error=f'OCR 识别失败: {str(e)}'
            )
        finally:
            # 释放资源，避免内存泄漏
            resources_to_clean = [
                ("_last_debug_composited_image", self),
                ("_last_composited_for_ocr", self),
            ]
            
            for attr_name, obj in resources_to_clean:
                try:
                    resource = getattr(obj, attr_name, None)
                    if resource is not None:
                        if hasattr(resource, "close"):
                            resource.close()
                        setattr(obj, attr_name, None)
                except Exception as e:
                    self.logger.debug(f"释放资源 {attr_name} 失败: {e}")
            
            # 释放处理后的图像资源
            if processed_image is not None and processed_image is not image:
                try:
                    processed_image.close()
                except Exception as e:
                    self.logger.debug(f"释放处理后的图像失败: {e}")
            
            # 定期清理临时文件（每10次识别清理一次，避免频繁IO）
            import random
            if random.randint(1, 10) == 1:
                try:
                    self._cleanup_old_temp_files()
                except Exception as e:
                    self.logger.debug(f"清理临时文件失败: {e}")
    
    def extract_text_from_file(self, image_path: str, source_lang: str = "自动检测") -> OCRResult:
        """从图像文件中提取文字"""
        try:
            image = Image.open(image_path)
            return self.extract_text(image, source_lang)
        except Exception as e:
            return OCRResult(
                text='',
                confidence=0.0,
                language=self.languages,
                error=f'无法打开图像文件: {str(e)}'
            )
    
    def extract_text_regions(self, image: Image.Image, language: Optional[str] = None) -> List[Tuple[str, Tuple[int, int, int, int]]]:
        """
        提取文本区域及其位置
        
        Returns:
            列表，每个元素为 (文本, (x, y, width, height))
        """
        try:
            # 预处理图像（不传递语言参数，使用默认参数）
            processed_image = self.preprocess_image(image)
            
            # 设置语言
            ocr_language = language if language else self.languages
            
            # 配置参数
            config = f'--oem 3 --psm 6 -l {ocr_language}'
            
            # 获取详细数据
            data = self._safe_image_to_data(processed_image, config=config)
            
            # 提取文本区域
            regions = []
            
            for i in range(len(data['text'])):
                text = data['text'][i].strip()
                if text:  # 只处理非空文本
                    x = data['left'][i]
                    y = data['top'][i]
                    width = data['width'][i]
                    height = data['height'][i]
                    
                    regions.append((text, (x, y, width, height)))
            
            return regions
            
        except Exception as e:
            print(f"提取文本区域失败: {e}")
            return []
    
    def set_languages(self, languages: str):
        """设置 OCR 语言"""
        self.languages = languages
    
    def get_available_languages(self) -> List[str]:
        """获取可用的 Tesseract 语言"""
        try:
            # 获取 Tesseract 支持的语言
            langs = pytesseract.get_languages(config='')
            return langs
        except:
            # 返回默认支持的语言
            return ['eng', 'jpn', 'kor', 'chi_sim', 'chi_tra']
    
    def _cleanup_old_temp_files(self, max_age_hours: int = 24):
        """清理旧的临时文件（保留最近24小时的文件）"""
        try:
            if not self.temp_dir.exists():
                return
            
            import time
            current_time = time.time()
            max_age_seconds = max_age_hours * 3600
            
            for file in self.temp_dir.iterdir():
                try:
                    if file.is_file():
                        file_age = current_time - file.stat().st_mtime
                        if file_age > max_age_seconds:
                            file.unlink()
                except Exception:
                    pass
        except Exception:
            pass
    
    def cleanup(self):
        """清理临时文件"""
        try:
            if self.temp_dir.exists():
                for file in self.temp_dir.iterdir():
                    try:
                        file.unlink()
                    except:
                        pass
        except:
            pass


class OCRThread(QThread):
    """OCR 处理线程（用于后台处理）"""
    
    progress = pyqtSignal(int, str)  # 进度百分比, 状态消息
    result_ready = pyqtSignal(OCRResult)  # OCR 结果
    finished = pyqtSignal(OCRResult)  # 处理完成
    
    def __init__(self, image: Image.Image, source_lang: str = "自动检测"):
        super().__init__()
        self.image = image
        self.source_lang = source_lang
        self.processor = OCRProcessor()
    
    def run(self):
        """线程运行函数"""
        try:
            self.progress.emit(10, "正在预处理图像...")
            
            self.progress.emit(30, "正在识别文字...")
            # 提取文字（extract_text 内部会做预处理）
            result = self.processor.extract_text(self.image, self.source_lang)
            
            if result.error:
                self.progress.emit(100, f"识别失败: {result.error}")
            else:
                self.progress.emit(100, f"识别完成 (置信度: {result.confidence:.1f}%)")
            
            # 发送结果
            self.result_ready.emit(result)
            self.finished.emit(result)
            
        except Exception as e:
            error_result = OCRResult(
                text='',
                confidence=0.0,
                language=self.processor.languages,
                error=f'OCR 处理失败: {str(e)}'
            )
            self.result_ready.emit(error_result)
            self.finished.emit(error_result)
        finally:
            self.processor.cleanup()
