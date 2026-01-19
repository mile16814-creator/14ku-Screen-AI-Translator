"""
改进的分行分段逻辑方案

主要改进点：
1. OCR阶段：使用统计方法动态调整段落检测阈值
2. 后处理：使用状态机替代正则表达式占位符
3. 翻译阶段：支持段落级翻译以保持上下文
"""

import re
from typing import List, Tuple, Optional
import numpy as np


class ImprovedLineSegmenter:
    """改进的分行分段处理器"""
    
    def __init__(self):
        self.para_token = "__ST_PARA__"
    
    def detect_paragraphs_by_statistics(
        self, 
        line_heights: List[float],
        line_gaps: List[float],
        is_cjk: bool = False
    ) -> List[bool]:
        """
        使用统计方法检测段落分隔
        
        改进点：
        - 不再使用固定阈值，而是基于行间距的统计分布
        - 使用中位数和标准差来动态确定阈值
        - 考虑行高的一致性
        
        Args:
            line_heights: 每行的高度列表
            line_gaps: 每行之间的间距列表
            is_cjk: 是否为CJK语言
            
        Returns:
            布尔列表，True表示该位置是段落分隔
        """
        if not line_gaps or len(line_gaps) < 2:
            return [False] * len(line_gaps)
        
        gaps = np.array(line_gaps)
        heights = np.array(line_heights) if line_heights else np.ones(len(gaps))
        
        # 计算统计量
        median_gap = np.median(gaps[gaps > 0])
        std_gap = np.std(gaps[gaps > 0])
        median_height = np.median(heights[heights > 0])
        
        # 动态阈值：基于统计分布
        # 段落间距应该明显大于正常行间距
        if is_cjk:
            # CJK语言：阈值相对宽松（1.5倍中位数 + 1倍标准差）
            threshold = median_gap * 1.5 + std_gap
            min_threshold = max(median_height * 0.9, 14.0)
        else:
            # 英文：阈值更严格（2倍中位数 + 1.5倍标准差）
            threshold = median_gap * 2.0 + std_gap * 1.5
            min_threshold = max(median_height * 1.6, 24.0)
        
        threshold = max(threshold, min_threshold)
        
        # 检测段落分隔
        is_paragraph = []
        for i, gap in enumerate(gaps):
            # 如果间距明显大于阈值，且大于中位数的2倍，认为是段落分隔
            if gap >= threshold and gap >= median_gap * 2.0:
                is_paragraph.append(True)
            else:
                is_paragraph.append(False)
        
        return is_paragraph
    
    def postprocess_with_state_machine(
        self, 
        text: str, 
        language: str = None,
        is_cjk: bool = False
    ) -> str:
        """
        使用状态机处理文本换行，替代正则表达式占位符方法
        
        改进点：
        - 更清晰的逻辑流程
        - 更好的边界情况处理
        - 支持混合语言场景
        
        Args:
            text: 原始文本
            language: 语言代码
            is_cjk: 是否为CJK语言
            
        Returns:
            处理后的文本
        """
        if not text:
            return ""
        
        # 统一换行符
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        
        # 状态机处理
        lines = text.split('\n')
        result_lines = []
        
        # 状态：0=正常文本, 1=遇到空行（可能是段落分隔）
        state = 0
        consecutive_empty = 0
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            if not stripped:
                # 空行
                consecutive_empty += 1
                if consecutive_empty >= 2:
                    # 连续两个或更多空行 -> 段落分隔
                    if result_lines and result_lines[-1] != "":
                        result_lines.append("")
                    consecutive_empty = 0
                    state = 0
                else:
                    state = 1
            else:
                # 非空行
                if consecutive_empty >= 2:
                    # 之前有段落分隔
                    if result_lines and result_lines[-1] != "":
                        result_lines.append("")
                
                # 处理行内容
                if is_cjk:
                    # CJK：移除行内空格
                    cleaned = re.sub(r'[ \t\f\v]+', '', stripped)
                    result_lines.append(cleaned)
                else:
                    # 非CJK：规范化空格
                    cleaned = re.sub(r'[ \t\f\v]+', ' ', stripped)
                    result_lines.append(cleaned)
                
                consecutive_empty = 0
                state = 0
        
        # 处理末尾空行
        if consecutive_empty >= 2 and result_lines and result_lines[-1] != "":
            result_lines.append("")
        
        result = '\n'.join(result_lines)
        
        # 最终清理：合并连续空行
        result = re.sub(r'\n{3,}', '\n\n', result)
        
        return result.strip()
    
    def smart_line_merge(
        self,
        text: str,
        is_cjk: bool = False
    ) -> str:
        """
        智能合并自动换行
        
        改进点：
        - 考虑行尾标点符号（句号、问号等不应合并）
        - 考虑行首大小写（大写字母可能是新句子）
        - 考虑行长度（短行更可能是自动换行）
        
        Args:
            text: 文本
            is_cjk: 是否为CJK语言
            
        Returns:
            合并后的文本
        """
        lines = text.split('\n')
        if len(lines) <= 1:
            return text
        
        merged_lines = []
        i = 0
        
        while i < len(lines):
            current_line = lines[i].strip()
            
            if not current_line:
                # 空行保留
                merged_lines.append("")
                i += 1
                continue
            
            # 检查是否是段落分隔（双换行）
            if i + 1 < len(lines) and not lines[i + 1].strip():
                if i + 2 < len(lines) and not lines[i + 2].strip():
                    # 连续两个空行 -> 段落分隔
                    merged_lines.append(current_line)
                    merged_lines.append("")
                    i += 3
                    continue
            
            # 尝试合并下一行
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                
                if not next_line:
                    merged_lines.append(current_line)
                    i += 1
                    continue
                
                # 判断是否应该合并
                should_merge = False
                
                if is_cjk:
                    # CJK：单换行通常应该合并
                    # 但行尾有句号、问号、感叹号时可能不合并
                    cjk_end_punct = ['。', '！', '？', '.', '!', '?']
                    if not any(current_line.endswith(p) for p in cjk_end_punct):
                        should_merge = True
                else:
                    # 英文：考虑更多因素
                    # 1. 行尾没有句号等标点
                    # 2. 下一行不是以大写字母开头（可能是新句子）
                    # 3. 当前行较短（可能是自动换行）
                    end_punct = ['.', '!', '?', ':', ';']
                    starts_upper = next_line and next_line[0].isupper()
                    has_end_punct = any(current_line.rstrip().endswith(p) for p in end_punct)
                    is_short_line = len(current_line) < 50
                    
                    if not has_end_punct and (not starts_upper or is_short_line):
                        should_merge = True
                
                if should_merge:
                    # 合并行
                    separator = '' if is_cjk else ' '
                    merged_line = current_line + separator + next_line
                    merged_lines.append(merged_line)
                    i += 2
                else:
                    merged_lines.append(current_line)
                    i += 1
            else:
                merged_lines.append(current_line)
                i += 1
        
        return '\n'.join(merged_lines)
    
    def translate_by_paragraphs(
        self,
        text: str,
        translate_func,
        max_length: int = 512
    ) -> str:
        """
        按段落翻译，保持上下文
        
        改进点：
        - 不是逐行翻译，而是按段落翻译
        - 对于超长段落，智能分割
        - 保持段落结构
        
        Args:
            text: 原文
            translate_func: 翻译函数，接受文本返回翻译结果
            max_length: 最大长度限制
            
        Returns:
            翻译后的文本
        """
        # 按段落分割（双换行）
        paragraphs = re.split(r'\n\n+', text)
        
        translated_paragraphs = []
        
        for para in paragraphs:
            if not para.strip():
                translated_paragraphs.append("")
                continue
            
            # 如果段落太长，需要分割
            if len(para) > max_length:
                # 按句子或行分割
                lines = para.split('\n')
                current_chunk = []
                current_length = 0
                
                for line in lines:
                    line_length = len(line)
                    
                    if current_length + line_length > max_length and current_chunk:
                        # 翻译当前块
                        chunk_text = '\n'.join(current_chunk)
                        translated = translate_func(chunk_text)
                        translated_paragraphs.append(translated)
                        
                        current_chunk = [line]
                        current_length = line_length
                    else:
                        current_chunk.append(line)
                        current_length += line_length + 1  # +1 for newline
                
                # 翻译最后一块
                if current_chunk:
                    chunk_text = '\n'.join(current_chunk)
                    translated = translate_func(chunk_text)
                    translated_paragraphs.append(translated)
            else:
                # 直接翻译整个段落
                translated = translate_func(para)
                translated_paragraphs.append(translated)
        
        # 用双换行连接段落
        return '\n\n'.join(translated_paragraphs)


# 使用示例
if __name__ == "__main__":
    segmenter = ImprovedLineSegmenter()
    
    # 测试状态机处理
    test_text = "第一行\n第二行\n\n第三段\n第四行"
    result = segmenter.postprocess_with_state_machine(test_text, is_cjk=True)
    print("状态机处理结果:", result)
    
    # 测试智能合并
    test_text2 = "这是一个很长的句子\n被自动换行了\n\n这是新段落"
    result2 = segmenter.smart_line_merge(test_text2, is_cjk=True)
    print("智能合并结果:", result2)

