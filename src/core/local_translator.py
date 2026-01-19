"""
本地AI翻译服务 - 使用M2M100/NLLB模型
"""

import os
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

# 延迟导入transformers，避免启动时立即加载模型
try:
    from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
    TRANSFORMERS_AVAILABLE = True
except ImportError:
    TRANSFORMERS_AVAILABLE = False

try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False


@dataclass
class TranslationResult:
    """翻译结果"""
    original_text: str
    translated_text: str
    source_lang: str
    target_lang: str
    confidence: float = 1.0
    error: Optional[str] = None


class LocalAITranslator:
    """本地AI翻译器 - 使用M2M100/NLLB模型"""
    
    def __init__(self, model_path: Optional[str] = None, load_model_immediately: bool = True):
        """
        初始化本地AI翻译器
        
        Args:
            model_path: 模型路径，如果为None则使用默认的models目录
            load_model_immediately: 是否立即加载模型（默认True，让模型常驻内存）
        """
        from src.exceptions import ModelError, FileError
        
        if not TRANSFORMERS_AVAILABLE:
            raise ModelError("transformers库未安装，请运行: pip install transformers", error_code=501)
        if not TORCH_AVAILABLE:
            raise ModelError("torch库未安装，请运行: pip install torch", error_code=502)
        
        self.logger = logging.getLogger(__name__)
        
        # 确定模型路径
        if model_path is None:
            # 尝试从不同位置找到models目录
            import sys
            current_dir = Path(__file__).parent.parent.parent
            model_path = current_dir / "models"
            
            if not model_path.exists():
                # 如果是打包版本，可能在资源目录
                if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
                    model_path = Path(sys._MEIPASS) / "models"
            
            # 如果仍然找不到，尝试其他常见位置
            if not model_path.exists():
                # 尝试应用根目录
                if getattr(sys, "frozen", False):
                    app_root = Path(sys.executable).parent
                    model_path = app_root / "models"
                else:
                    app_root = Path(__file__).parent.parent.parent
                    model_path = app_root / "models"
        
        # 确保 model_path 不是 None
        if model_path is None:
            raise ModelError("无法找到模型目录，请确保 models 目录存在", error_code=503)
        
        # 转换为 Path 对象
        if isinstance(model_path, str):
            self.model_path = Path(model_path)
        elif isinstance(model_path, Path):
            self.model_path = model_path
        else:
            raise TypeError(f"model_path 必须是字符串或 Path 对象，得到: {type(model_path)}")
        
        if not self.model_path.exists():
            raise FileError(f"模型目录不存在: {self.model_path}", error_code=701)
        
        self.logger.info(f"初始化本地翻译模型: {self.model_path}")
        
        # 初始化模型和tokenizer（将在_load_model中加载）
        self.model = None
        self.tokenizer = None
        
        # 自动检测可用设备
        # 优先使用 CUDA (NVIDIA GPU)，如果没有则使用 CPU
        # 也可以检测 MPS (Apple Silicon)
        if torch.cuda.is_available():
            self.device = "cuda"
            gpu_name = torch.cuda.get_device_name(0) if torch.cuda.device_count() > 0 else "Unknown"
            self.logger.info(f"检测到 NVIDIA GPU: {gpu_name}，使用 CUDA 加速")
        elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
            # Apple Silicon (M1/M2/M3) GPU
            self.device = "mps"
            self.logger.info("检测到 Apple Silicon GPU，使用 MPS 加速")
        else:
            # AMD GPU在Windows上ROCm支持有限，默认使用CPU
            self.device = "cpu"
            self.logger.info("使用 CPU 进行推理（AMD GPU在Windows上建议使用CPU以获得最佳兼容性）")
        
        # 语言代码映射：统一使用 src.core.languages 的 key（如 en/zh-CN），并支持多候选 NLLB 语言码。
        # 说明：
        # - UI/配置层使用 key（简短且稳定）
        # - 模型层使用 nllb_codes（如 eng_Latn / zho_Hans）
        from src.core.languages import ALL_LANGUAGES, normalize_lang_key
        self._normalize_lang_key = normalize_lang_key
        self.language_map: Dict[str, List[str]] = {}
        for lang in ALL_LANGUAGES:
            self.language_map[lang.key] = list(lang.nllb_codes)
        # 旧配置/别名兼容（让旧 settings.ini 继续可用）
        self.language_map.update({
            "ZH": ["zho_Hans"],
            "EN": ["eng_Latn"],
            "JA": ["jpn_Jpan"],
            "KO": ["kor_Hang"],
            "zh": ["zho_Hans"],
            "zh-CN": ["zho_Hans"],
            "zh-TW": ["zho_Hant"],
            "jpn": ["jpn_Jpan"],
            "kor": ["kor_Hang"],
            "chi_sim": ["zho_Hans"],
            "chi_tra": ["zho_Hant"],
            "eng": ["eng_Latn"],
        })
        
        # 立即加载模型（让模型常驻内存）
        if load_model_immediately:
            self.logger.info("正在立即加载模型到内存...")
            self._load_model()
        
    def _load_model(self):
        """加载模型到内存（如果尚未加载）"""
        if self.model is None or self.tokenizer is None:
            try:
                from src.exceptions import ModelError, FileError, ScreenTranslatorError
                import json

                # 确保 model_path 存在且有效
                if self.model_path is None or not self.model_path.exists():
                    raise FileError(f"模型路径无效或不存在: {self.model_path}", error_code=701)

                config_path = self.model_path / "config.json"
                if not config_path.exists():
                    raise FileError(f"模型目录缺少 config.json: {self.model_path}", error_code=702)

                index_path = self.model_path / "pytorch_model.bin.index.json"
                if index_path.exists():
                    try:
                        index_data = json.loads(index_path.read_text(encoding="utf-8"))
                        weight_map = index_data.get("weight_map") if isinstance(index_data, dict) else None
                        shard_names = sorted(set(weight_map.values())) if isinstance(weight_map, dict) else []
                        if shard_names:
                            missing_shards = [n for n in shard_names if not (self.model_path / n).exists()]
                            if missing_shards:
                                raise ModelError(
                                    "检测到分片模型索引文件，但缺少对应的分片权重文件。"
                                    f"缺失: {missing_shards[:5]}{'...' if len(missing_shards) > 5 else ''}。"
                                    "请补齐所有 pytorch_model-0000x-of-0000y.bin 文件，"
                                    "或删除 pytorch_model.bin.index.json 并仅保留单文件 pytorch_model.bin。",
                                    error_code=504,
                                    details={"model_path": str(self.model_path), "missing_shards": missing_shards},
                                )
                    except ScreenTranslatorError:
                        raise
                    except Exception as ex:
                        raise ModelError(
                            f"读取模型索引文件失败: {index_path.name} ({ex})",
                            error_code=505,
                            details={"model_path": str(self.model_path)},
                        )
                
                self.logger.info("正在加载翻译模型，这可能需要一些时间...")
                model_path_str = str(self.model_path)
                
                # 使用 AutoTokenizer 和 AutoModelForSeq2SeqLM 自动检测正确的类型
                # 这样可以兼容 M2M100 和 NLLB 模型
                try:
                    self.tokenizer = AutoTokenizer.from_pretrained(model_path_str, trust_remote_code=True)
                except Exception as ex:
                    raise ModelError(
                        "加载 tokenizer 失败，模型目录可能缺少或混入了不匹配的 tokenizer 文件"
                        "（常见需要: tokenizer.json / sentencepiece.bpe.model / tokenizer_config.json 等）。"
                        f"当前模型目录: {self.model_path}",
                        error_code=507,
                        details={"model_path": str(self.model_path), "original_error": str(ex)},
                    )

                self.model = AutoModelForSeq2SeqLM.from_pretrained(model_path_str, trust_remote_code=True)
                
                # 将模型移动到指定设备
                self.model.to(self.device)
                self.model.eval()  # 设置为评估模式
                
                # 显示设备信息
                if self.device == "cuda":
                    gpu_memory = torch.cuda.get_device_properties(0).total_memory / (1024**3)  # GB
                    self.logger.info(f"模型已加载到 GPU，显存: {gpu_memory:.1f} GB")
                elif self.device == "mps":
                    self.logger.info("模型已加载到 Apple Silicon GPU (MPS)")
                else:
                    self.logger.info("模型已加载到 CPU（如果没有GPU，这是正常的，但翻译速度会较慢）")
                
                # 记录 tokenizer 支持的语言代码信息（用于调试）
                # 尝试多种方法获取语言代码映射
                lang_code_to_id = None
                if hasattr(self.tokenizer, 'lang_code_to_id') and self.tokenizer.lang_code_to_id:
                    lang_code_to_id = self.tokenizer.lang_code_to_id
                else:
                    # 方法1: 从 additional_special_tokens 中提取语言代码
                    if hasattr(self.tokenizer, 'additional_special_tokens') and self.tokenizer.additional_special_tokens:
                        lang_code_to_id = {}
                        for lang_code in self.tokenizer.additional_special_tokens:
                            # NLLB 语言代码格式: zho_Hans, eng_Latn 等（包含下划线，但不是被下划线包围）
                            if '_' in lang_code and len(lang_code) > 3:
                                try:
                                    token_id = self.tokenizer.convert_tokens_to_ids(lang_code)
                                    if token_id is not None and token_id != self.tokenizer.unk_token_id:
                                        lang_code_to_id[lang_code] = token_id
                                except Exception:
                                    pass
                        if lang_code_to_id:
                            self.tokenizer.lang_code_to_id = lang_code_to_id
                    
                    # 方法2: 如果方法1失败，从词汇表中提取
                    if (not lang_code_to_id or len(lang_code_to_id) < 10) and hasattr(self.tokenizer, 'get_vocab'):
                        vocab = self.tokenizer.get_vocab()
                        if not lang_code_to_id:
                            lang_code_to_id = {}
                        for token, token_id in vocab.items():
                            # NLLB 语言代码格式: zho_Hans（包含下划线，长度合理）
                            if '_' in token and 4 <= len(token) <= 15 and not token.startswith('<') and not token.endswith('>'):
                                # 检查是否是有效的语言代码格式（如 zho_Hans, eng_Latn）
                                parts = token.split('_')
                                if len(parts) == 2 and parts[0].isalpha() and parts[1].isalpha():
                                    if token not in lang_code_to_id:
                                        lang_code_to_id[token] = token_id
                        if lang_code_to_id:
                            self.tokenizer.lang_code_to_id = lang_code_to_id
                
                if lang_code_to_id:
                    lang_count = len(lang_code_to_id)
                    # 检查常用语言是否支持
                    common_langs = ['zho_Hans', 'zho_Hant', 'eng_Latn', 'jpn_Jpan', 'kor_Hang']
                    supported_common = [lang for lang in common_langs if lang in lang_code_to_id]
                    self.logger.info(
                        f"翻译模型加载完成 (tokenizer类型: {type(self.tokenizer).__name__}, "
                        f"model类型: {type(self.model).__name__}, "
                        f"支持 {lang_count} 种语言, "
                        f"常用语言支持: {supported_common})"
                    )
                    # 保存到 tokenizer 以便后续使用
                    if not hasattr(self.tokenizer, 'lang_code_to_id') or not self.tokenizer.lang_code_to_id:
                        self.tokenizer.lang_code_to_id = lang_code_to_id
                else:
                    self.logger.warning(
                        f"翻译模型加载完成，但无法获取语言代码映射 "
                        f"(tokenizer类型: {type(self.tokenizer).__name__}, model类型: {type(self.model).__name__})"
                    )
                    self.logger.info("将尝试使用其他方法进行翻译")
            except ValueError as e:
                msg = str(e)
                if "state dictionary" in msg and "corrupted" in msg:
                    raise ModelError(
                        "模型权重无法被正确解析。常见原因是模型目录里的 config/tokenizer/权重文件不是同一个模型版本，"
                        "或权重下载不完整（例如存在分片索引但缺少分片文件）。"
                        f"当前模型目录: {self.model_path}",
                        error_code=506,
                        details={"model_path": str(self.model_path), "original_error": msg},
                    )
                raise
            except Exception as e:
                self.logger.error(f"加载模型失败: {e}", exc_info=True)
                raise
    
    def _map_lang(self, lang: str) -> str:
        """
        将 UI/配置语言代码映射为 NLLB/M2M100 语言码。
        若语言存在多个候选码，会优先选择 tokenizer 真正支持的那一个。
        """
        if not lang:
            return "zho_Hans"

        raw = str(lang).strip()
        try:
            key = self._normalize_lang_key(raw) if hasattr(self, "_normalize_lang_key") else raw
        except Exception:
            key = raw

        candidates = None
        if isinstance(self.language_map, dict):
            candidates = self.language_map.get(key) or self.language_map.get(key.lower()) or self.language_map.get(key.upper())
        if not candidates:
            # 兜底：直接把输入当作 nllb 码
            return key

        # 如果 tokenizer 已经加载并且能拿到 lang_code_to_id，则挑一个可用的
        try:
            lang_code_to_id = getattr(self.tokenizer, "lang_code_to_id", None) if self.tokenizer is not None else None
            if isinstance(lang_code_to_id, dict) and lang_code_to_id:
                for c in candidates:
                    if c in lang_code_to_id:
                        return c
                    # 容错：大小写不敏感
                    for exist in lang_code_to_id.keys():
                        if str(exist).lower() == str(c).lower():
                            return exist
        except Exception:
            pass

        # tokenizer 未加载/无法判断时，返回第一候选
        return str(candidates[0])
    
    def detect_language(self, text: str) -> str:
        """检测文本的语言"""
        from src.utils.language_utils import detect_language as shared_detect_language
        return shared_detect_language(text)
    
    def translate(
        self,
        text: str,
        target_lang: str = 'zh',
        source_lang: str = 'en',
        split_sentences: str = '1',
        preserve_formatting: str = '0',
        formality: str = 'default',
        preprocess: bool = True,
    ) -> TranslationResult:
        """
        翻译文本
        
        Args:
            text: 要翻译的文本
            target_lang: 目标语言代码
            source_lang: 源语言代码
            split_sentences: 分割句子（保留参数以兼容接口，本地模型不使用）
            preserve_formatting: 保留格式（保留参数以兼容接口，本地模型不使用）
            formality: 正式程度（保留参数以兼容接口，本地模型不使用）
        
        Returns:
            TranslationResult 对象
        """
        try:
            # 延迟加载模型
            self._load_model()
            
            if not text or not text.strip():
                return TranslationResult(
                    original_text=text or "",
                    translated_text="",
                    source_lang=source_lang,
                    target_lang=target_lang,
                    error="Empty text"
                )
            

            
            # 映射语言代码
            src_lang_code = self._map_lang(source_lang)
            tgt_lang_code = self._map_lang(target_lang)
            
            if not src_lang_code or not tgt_lang_code:
                return TranslationResult(
                    original_text=text,
                    translated_text="",
                    source_lang=source_lang,
                    target_lang=target_lang,
                    error="源语言或目标语言无效"
                )
            
            # 获取目标语言的BOS token ID（只需要获取一次）
            # NLLB tokenizer 使用 lang_code_to_id 字典
            forced_bos_token_id = None
            try:
                # 方法1: 优先使用 lang_code_to_id（NLLB tokenizer 的标准方法）
                lang_code_to_id = None
                if hasattr(self.tokenizer, 'lang_code_to_id') and self.tokenizer.lang_code_to_id:
                    lang_code_to_id = self.tokenizer.lang_code_to_id
                else:
                    # 如果 lang_code_to_id 为空，尝试从 additional_special_tokens 中提取
                    if hasattr(self.tokenizer, 'additional_special_tokens') and self.tokenizer.additional_special_tokens:
                        lang_code_to_id = {}
                        for lang_code in self.tokenizer.additional_special_tokens:
                            # NLLB 语言代码格式: zho_Hans（包含下划线）
                            if '_' in lang_code and len(lang_code) > 3:
                                try:
                                    token_id = self.tokenizer.convert_tokens_to_ids(lang_code)
                                    if token_id is not None and token_id != self.tokenizer.unk_token_id:
                                        lang_code_to_id[lang_code] = token_id
                                except Exception:
                                    pass
                        if lang_code_to_id:
                            self.tokenizer.lang_code_to_id = lang_code_to_id
                            self.logger.info(f"从 additional_special_tokens 中提取了 {len(lang_code_to_id)} 种语言代码")
                    
                    # 如果还是找不到，从词汇表中提取
                    if (not lang_code_to_id or len(lang_code_to_id) < 10) and hasattr(self.tokenizer, 'get_vocab'):
                        vocab = self.tokenizer.get_vocab()
                        if not lang_code_to_id:
                            lang_code_to_id = {}
                        for token, token_id in vocab.items():
                            # NLLB 语言代码格式: zho_Hans（包含下划线，长度合理）
                            if '_' in token and 4 <= len(token) <= 15 and not token.startswith('<') and not token.endswith('>'):
                                parts = token.split('_')
                                if len(parts) == 2 and parts[0].isalpha() and parts[1].isalpha():
                                    if token not in lang_code_to_id:
                                        lang_code_to_id[token] = token_id
                        if lang_code_to_id:
                            self.tokenizer.lang_code_to_id = lang_code_to_id
                            self.logger.info(f"从词汇表中提取了 {len(lang_code_to_id)} 种语言代码")
                
                if lang_code_to_id:
                    if tgt_lang_code in lang_code_to_id:
                        forced_bos_token_id = lang_code_to_id[tgt_lang_code]
                        self.logger.debug(f"找到目标语言 '{tgt_lang_code}' 的 BOS token ID: {forced_bos_token_id}")
                    else:
                        # 尝试查找相似的语言代码（不区分大小写）
                        tgt_lang_lower = tgt_lang_code.lower()
                        for lang_code, lang_id in lang_code_to_id.items():
                            if lang_code.lower() == tgt_lang_lower:
                                forced_bos_token_id = lang_id
                                self.logger.info(f"找到相似语言代码: {lang_code} -> {tgt_lang_code} (ID: {lang_id})")
                                break
                
                # 方法2: 如果 lang_code_to_id 不存在或为空，尝试直接转换语言代码token
                if forced_bos_token_id is None and hasattr(self.tokenizer, 'convert_tokens_to_ids'):
                    # NLLB 语言代码格式: zho_Hans（直接使用，不需要下划线包围）
                    token_id = self.tokenizer.convert_tokens_to_ids(tgt_lang_code)
                    unk_id = getattr(self.tokenizer, 'unk_token_id', None)
                    if token_id is not None and token_id != unk_id:
                        forced_bos_token_id = token_id
                        self.logger.info(f"通过 convert_tokens_to_ids 找到 '{tgt_lang_code}' 的 token ID: {forced_bos_token_id}")
                
                # 方法3: 尝试 get_lang_id 方法（M2M100）
                if forced_bos_token_id is None and hasattr(self.tokenizer, 'get_lang_id'):
                    try:
                        forced_bos_token_id = self.tokenizer.get_lang_id(tgt_lang_code)
                        if forced_bos_token_id is not None:
                            self.logger.info(f"通过 get_lang_id 找到 '{tgt_lang_code}' 的 token ID: {forced_bos_token_id}")
                    except Exception as e:
                        self.logger.debug(f"get_lang_id 方法失败: {e}")
                
                # 如果还是找不到，记录调试信息
                if forced_bos_token_id is None:
                    available_langs = []
                    if lang_code_to_id:
                        available_langs = list(lang_code_to_id.keys())[:20]  # 只显示前20个
                    elif hasattr(self.tokenizer, 'get_vocab'):
                        # 尝试从词汇表中查找语言代码
                        vocab = self.tokenizer.get_vocab()
                        lang_tokens = [token[2:-2] for token in vocab.keys() 
                                     if token.startswith('__') and token.endswith('__') and len(token) > 4]
                        available_langs = lang_tokens[:20]
                    
                    self.logger.warning(
                        f"未找到目标语言 '{tgt_lang_code}' 的 BOS token ID。"
                        f"可用语言示例: {available_langs}。"
                        f"将使用 decoder_start_token_id 进行翻译。"
                    )
            except Exception as e:
                self.logger.warning(f"获取目标语言BOS token ID时出错 ({tgt_lang_code}): {e}")
                forced_bos_token_id = None
            
            # 自动检测文本语言，决定是否移除单词之间的空格
            # 对于日文/韩文/中文：移除单词之间的空格（避免单词被拆分）
            # 对于英文等其他语言：保留单词之间的空格
            if preprocess:
                import re
                
                # 检测文本中CJK字符和拉丁字符的比例
                cjk_chars = sum(1 for c in text if ('\u3040' <= c <= '\u30ff') or ('\u4e00' <= c <= '\u9fff') or ('\uac00' <= c <= '\ud7a3'))
                latin_chars = sum(1 for c in text if 'a' <= c.lower() <= 'z')
                total_chars = len([c for c in text if not c.isspace()])
                
                # 判断是否为CJK语言
                # 重要：优先根据文本内容判断，而不是根据用户选择的源语言
                # 这样可以避免误判（例如用户选择了中文，但实际识别的是英文）
                is_cjk_text = False
                if total_chars > 0:
                    cjk_ratio = cjk_chars / total_chars
                    latin_ratio = latin_chars / total_chars
                    
                    # 优先根据文本内容判断
                    # 如果CJK字符占比超过30%，认为是CJK文本
                    if cjk_ratio > 0.3:
                        is_cjk_text = True
                    # 如果主要是拉丁字符（英文），保留空格
                    elif latin_ratio > 0.5:
                        is_cjk_text = False
                    # 如果文本内容不明确（混合语言），才参考源语言设置
                    # 但只有当源语言明确是CJK语言，且文本中确实有CJK字符时，才判定为CJK文本
                    elif source_lang in ['ja', 'jpn', 'ko', 'kor', 'zh', 'chi_sim', 'chi_tra'] and cjk_chars > 0:
                        # 即使源语言是CJK，但如果文本中CJK字符很少，也不应该移除空格
                        # 只有当CJK字符占比超过10%时，才考虑移除空格
                        if cjk_ratio > 0.1:
                            is_cjk_text = True
                
                # 对于CJK语言，移除单词之间的空格，但保留换行符
                if is_cjk_text:
                    # 先按换行符分割，处理每一行
                    lines = text.split('\n')
                    cleaned_lines = []
                    for line in lines:
                        if not line.strip():
                            cleaned_lines.append("")
                            continue
                        # 移除行内的空格（日文/韩文/中文不需要单词间空格）
                        cleaned_line = re.sub(r' +', '', line)
                        cleaned_lines.append(cleaned_line)
                    text = '\n'.join(cleaned_lines)
                # 对于英文等其他语言，保留空格（不做处理）
            
            # 保留原文的换行和排版
            # 如果文本包含换行符，按行翻译并保持行结构
            lines = text.split('\n')
            translated_lines = []
            
            # 设置源语言（只需要设置一次）
            if hasattr(self.tokenizer, 'src_lang'):
                self.tokenizer.src_lang = src_lang_code
            
            # 准备生成参数（只需要准备一次）
            if forced_bos_token_id is None:
                # 如果无法获取目标语言的BOS token ID，尝试使用decoder_start_token_id
                if hasattr(self.model.config, 'decoder_start_token_id'):
                    decoder_start_id = self.model.config.decoder_start_token_id
                    if decoder_start_id is not None:
                        forced_bos_token_id = decoder_start_id
                        self.logger.info(f"使用 decoder_start_token_id ({decoder_start_id}) 作为目标语言标记")
            
            # 逐行翻译，保持原文排版
            for line in lines:
                if not line.strip():
                    # 保留空行
                    translated_lines.append("")
                    continue
                
                # 编码输入文本
                encoded = self.tokenizer(line, return_tensors="pt", padding=True, truncation=True, max_length=512)
                encoded = {k: v.to(self.device) for k, v in encoded.items()}
                
                # 生成翻译
                with torch.no_grad():
                    generate_kwargs = {
                        **encoded,
                        'max_length': 512,
                        'num_beams': 5,
                        'early_stopping': True
                    }
                    # NLLB/M2M100 模型需要 forced_bos_token_id 来指定目标语言
                    if forced_bos_token_id is not None:
                        generate_kwargs['forced_bos_token_id'] = forced_bos_token_id
                    
                    generated_tokens = self.model.generate(**generate_kwargs)
                
                # 解码翻译结果
                translated_line = self.tokenizer.batch_decode(generated_tokens, skip_special_tokens=True)[0]
                translated_lines.append(translated_line)
            
            # 用换行符连接所有翻译结果，保持原文排版
            translated_text = '\n'.join(translated_lines)
            
            return TranslationResult(
                original_text=text,
                translated_text=translated_text,
                source_lang=source_lang,
                target_lang=target_lang,
                confidence=1.0
            )
            
        except Exception as e:
            self.logger.error(f"翻译失败: {e}", exc_info=True)
            return TranslationResult(
                original_text=text,
                translated_text="",
                source_lang=source_lang,
                target_lang=target_lang,
                error=f"翻译失败: {str(e)}"
            )
    
    def translate_texts(
        self,
        texts: List[str],
        target_lang: str = 'zh',
        source_lang: str = 'auto',
        split_sentences: str = '1',
        preserve_formatting: str = '0',
        formality: str = 'default',
        timeout: float = 10.0,
    ) -> List[TranslationResult]:
        """
        多文本翻译
        
        Args:
            texts: 要翻译的文本列表
            target_lang: 目标语言代码
            source_lang: 源语言代码（auto 为自动检测）
            split_sentences: 分割句子（保留参数以兼容接口）
            preserve_formatting: 保留格式（保留参数以兼容接口）
            formality: 正式程度（保留参数以兼容接口）
            timeout: 超时时间（保留参数以兼容接口）
        
        Returns:
            TranslationResult 列表
        """
        results = []
        for text in texts:
            result = self.translate(
                text=text,
                target_lang=target_lang,
                source_lang=source_lang,
                split_sentences=split_sentences,
                preserve_formatting=preserve_formatting,
                formality=formality
            )
            results.append(result)
        return results
    
    def translate_batch(
        self,
        texts: List[str],
        target_lang: str = 'zh',
        source_lang: str = 'auto'
    ) -> List[TranslationResult]:
        """批量翻译文本"""
        return self.translate_texts(texts, target_lang=target_lang, source_lang=source_lang)
    
    def get_usage(self) -> Tuple[Optional[int], Optional[int], Optional[str]]:
        """获取使用情况（本地模型不需要API配额）"""
        return None, None, "本地AI模型，无使用限制"
    
    def test_connection(self) -> Tuple[bool, str]:
        """测试模型连接"""
        try:
            self._load_model()
            if self.model is not None and self.tokenizer is not None:
                # 执行一个简单的测试翻译
                test_result = self.translate("Hello", "zh", "en")
                if test_result.error:
                    return False, f"模型测试失败: {test_result.error}"
                elif test_result.translated_text:
                    return True, "本地AI模型加载成功"
                else:
                    return False, "模型测试失败: 无返回结果"
            else:
                return False, "模型未加载"
        except Exception as e:
            return False, f"模型测试失败: {str(e)}"


class LocalTranslationThread(QThread):
    """本地翻译线程（用于后台翻译）"""
    
    progress = pyqtSignal(int, str)  # 进度百分比, 状态消息
    result_ready = pyqtSignal(TranslationResult)  # 单个翻译结果
    finished = pyqtSignal(list)  # 所有翻译完成
    
    def __init__(
        self,
        texts: List[str],
        translator: Optional[LocalAITranslator] = None,  # 复用已加载的翻译器实例
        model_path: Optional[str] = None,  # 仅当translator为None时使用
        target_lang: str = 'zh',
        source_lang: str = 'auto'
    ):
        super().__init__()
        self.texts = texts
        self.target_lang = target_lang
        self.source_lang = source_lang
        # 复用已加载的翻译器实例，避免重复加载模型
        if translator is not None:
            self.translator = translator
        else:
            # 仅在translator未提供时才创建新实例（不推荐，会导致重复加载）
            self.translator = LocalAITranslator(model_path) if model_path else LocalAITranslator()
        self.results: List[TranslationResult] = []
    
    def run(self):
        """线程运行函数"""
        total = len(self.texts or [])
        if total <= 0:
            self.progress.emit(100, "翻译完成")
            self.finished.emit([])
            return

        # 先整体翻译，再逐条 emit，保持旧 UI 兼容
        self.progress.emit(0, f"正在翻译 (0/{total})...")
        batch_results = self.translator.translate_batch(self.texts, self.target_lang, self.source_lang)

        for i, result in enumerate(batch_results):
            progress = int(((i + 1) / total) * 100)
            self.progress.emit(progress, f"正在翻译 ({i+1}/{total})...")
            self.results.append(result)
            self.result_ready.emit(result)
        
        # 完成
        self.progress.emit(100, "翻译完成")
        self.finished.emit(self.results)

