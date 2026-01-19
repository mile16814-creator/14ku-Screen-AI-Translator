"""
语言管理模块 - 处理语言选择、语言列表构建等功能
"""
from PyQt6.QtWidgets import QComboBox
from typing import List, Dict, Optional, Set
from config import ConfigManager


class LanguageManager:
    """
    语言管理器 - 负责处理语言选择、语言列表构建等功能
    """
    
    def __init__(self, config_manager: ConfigManager):
        """
        初始化语言管理器
        
        Args:
            config_manager: 配置管理器实例
        """
        self.config_manager = config_manager
        self._languages = {}
        self._quick_lang_keys_source = []
        self._quick_lang_keys_target = []
        self._updating_lang_combos = False
        self._temp_source_lang = None
        self._temp_target_lang = None
        
        # 加载语言配置
        self._load_language_config()
    
    def _load_language_config(self):
        """
        加载语言配置
        """
        # 从配置中读取语言设置
        raw_quick_src = self.config_manager.get('translation', 'quick_languages_source', '')
        raw_quick_tgt = self.config_manager.get('translation', 'quick_languages_target', '')
        
        # 使用 core 侧的规范化逻辑（长度固定/去重/仅允许 ALL_LANGUAGES 内的 key）
        from src.core.languages import normalize_quick_language_keys

        def split_list(raw: str) -> List[str]:
            return [x.strip() for x in (raw or "").split(",") if x.strip()]

        self._quick_lang_keys_source = normalize_quick_language_keys(split_list(raw_quick_src), desired_len=4)
        self._quick_lang_keys_target = normalize_quick_language_keys(split_list(raw_quick_tgt), desired_len=4)
    
    def rebuild_language_combos(self, source_combo: QComboBox, target_combo: QComboBox, apply_config_selection: bool = True):
        """
        重建语言下拉框
        
        Args:
            source_combo: 源语言下拉框
            target_combo: 目标语言下拉框
            apply_config_selection: 是否应用配置中的选择
        """
        self._updating_lang_combos = True
        
        try:
            # 清空现有选项
            source_combo.clear()
            target_combo.clear()
            
            # 从 src.core.languages 导入 ALL_LANGUAGES
            from src.core.languages import ALL_LANGUAGES, display_name_for_key, key_for_display_name
            
            # 构建快捷语言列表
            quick_langs_source = []
            quick_langs_target = []
            
            # 先添加快捷语言
            for key in self._quick_lang_keys_source:
                display_name = display_name_for_key(key)
                if display_name:
                    quick_langs_source.append((key, display_name))
            
            for key in self._quick_lang_keys_target:
                display_name = display_name_for_key(key)
                if display_name:
                    quick_langs_target.append((key, display_name))
            
            # 添加快捷语言到下拉框
            for key, display_name in quick_langs_source:
                source_combo.addItem(display_name, key)
            
            for key, display_name in quick_langs_target:
                target_combo.addItem(display_name, key)
            
            # 添加分隔符
            source_combo.addSeparator()
            target_combo.addSeparator()
            
            # 添加显示更多选项
            source_combo.addItem("显示更多…", "show_more")
            target_combo.addItem("显示更多…", "show_more")
            
            # 应用配置选择
            if apply_config_selection:
                source_lang = self.config_manager.get('translation', 'source_language', 'en')
                target_lang = self.config_manager.get('translation', 'target_language', 'zh-CN')
                
                # 设置源语言
                for i in range(source_combo.count()):
                    if source_combo.itemData(i) == source_lang:
                        source_combo.setCurrentIndex(i)
                        break
                
                # 设置目标语言
                for i in range(target_combo.count()):
                    if target_combo.itemData(i) == target_lang:
                        target_combo.setCurrentIndex(i)
                        break
        finally:
            self._updating_lang_combos = False
    
    def on_source_lang_combo_changed(self, index: int, combo: QComboBox, main_window):
        """
        源语言下拉框变化处理
        
        Args:
            index: 选中的索引
            combo: 源语言下拉框
            main_window: 主窗口实例
        """
        if self._updating_lang_combos:
            return
        
        try:
            data = combo.currentData()
        except Exception:
            data = None

        # slot 5: 显示更多…
        if data == "show_more":
            if hasattr(main_window, "_open_language_picker"):
                main_window._open_language_picker(for_source=True)
            return

        # slot 0: 临时语言（不写入配置）
        if isinstance(data, str) and data.startswith("temp:"):
            return

        # slot 1-4: 快捷语言（清掉临时语言并保存配置）
        self.reset_temp_language_source()
        if hasattr(main_window, "save_language_settings"):
            main_window.save_language_settings()
    
    def on_target_lang_combo_changed(self, index: int, combo: QComboBox, main_window):
        """
        目标语言下拉框变化处理
        
        Args:
            index: 选中的索引
            combo: 目标语言下拉框
            main_window: 主窗口实例
        """
        if self._updating_lang_combos:
            return
        
        try:
            data = combo.currentData()
        except Exception:
            data = None

        if data == "show_more":
            if hasattr(main_window, "_open_language_picker"):
                main_window._open_language_picker(for_source=False)
            return

        if isinstance(data, str) and data.startswith("temp:"):
            return

        self.reset_temp_language_target()
        if hasattr(main_window, "save_language_settings"):
            main_window.save_language_settings()
    
    def _save_quick_languages_to_config(self, for_source: bool):
        """
        保存快捷语言到配置文件
        
        Args:
            for_source: 是否为源语言
        """
        if for_source:
            self.config_manager.set('translation', 'quick_languages_source', ','.join(self._quick_lang_keys_source))
        else:
            self.config_manager.set('translation', 'quick_languages_target', ','.join(self._quick_lang_keys_target))
    
    def update_quick_languages(self, source_langs: List[str], target_langs: List[str]):
        """
        更新快捷语言列表
        
        Args:
            source_langs: 源语言快捷列表
            target_langs: 目标语言快捷列表
        """
        from src.core.languages import normalize_quick_language_keys
        self._quick_lang_keys_source = normalize_quick_language_keys(source_langs, desired_len=4)
        self._quick_lang_keys_target = normalize_quick_language_keys(target_langs, desired_len=4)
        
        # 保存到配置
        self._save_quick_languages_to_config(for_source=True)
        self._save_quick_languages_to_config(for_source=False)
    
    @property
    def quick_lang_keys_source(self) -> List[str]:
        """获取源语言快捷列表"""
        return self._quick_lang_keys_source.copy()
    
    @property
    def quick_lang_keys_target(self) -> List[str]:
        """获取目标语言快捷列表"""
        return self._quick_lang_keys_target.copy()
    
    def set_temp_language_source(self, lang_key: str):
        """设置临时源语言"""
        self._temp_source_lang = lang_key
    
    def set_temp_language_target(self, lang_key: str):
        """设置临时目标语言"""
        self._temp_target_lang = lang_key
    
    def get_temp_language_source(self) -> Optional[str]:
        """获取临时源语言"""
        return self._temp_source_lang
    
    def get_temp_language_target(self) -> Optional[str]:
        """获取临时目标语言"""
        return self._temp_target_lang
    
    def reset_temp_language_source(self):
        """重置临时源语言"""
        self._temp_source_lang = None
    
    def reset_temp_language_target(self):
        """重置临时目标语言"""
        self._temp_target_lang = None
    
    def swap_quick_languages(self, slot1: int, slot2: int, for_source: bool = True):
        """
        交换两个快捷语言槽位的语言
        
        Args:
            slot1: 第一个槽位索引（0-3）
            slot2: 第二个槽位索引（0-3）
            for_source: 是否为源语言
        """
        if for_source:
            # 直接修改源语言快捷列表
            if 0 <= slot1 < len(self._quick_lang_keys_source) and 0 <= slot2 < len(self._quick_lang_keys_source):
                self._quick_lang_keys_source[slot1], self._quick_lang_keys_source[slot2] = \
                    self._quick_lang_keys_source[slot2], self._quick_lang_keys_source[slot1]
                self._save_quick_languages_to_config(for_source=True)
        else:
            # 直接修改目标语言快捷列表
            if 0 <= slot1 < len(self._quick_lang_keys_target) and 0 <= slot2 < len(self._quick_lang_keys_target):
                self._quick_lang_keys_target[slot1], self._quick_lang_keys_target[slot2] = \
                    self._quick_lang_keys_target[slot2], self._quick_lang_keys_target[slot1]
                self._save_quick_languages_to_config(for_source=False)

    def _should_add_to_quick(self, key: str, valid_keys: Set[str], for_source: bool) -> bool:
        """判断是否需要将 key 添加到快捷语言列表"""
        if not key:
            return False
        if key not in valid_keys:
            return False
        quick_list = self._quick_lang_keys_source if for_source else self._quick_lang_keys_target
        return key not in quick_list

    def _insert_into_quick(self, key: str, for_source: bool):
        """将 key 插入到对应的快捷语言列表首位，并保存配置"""
        from src.core.languages import normalize_quick_language_keys
        if for_source:
            base = self._quick_lang_keys_source
        else:
            base = self._quick_lang_keys_target
        new_quick = normalize_quick_language_keys([key] + base, desired_len=4)
        if for_source:
            self._quick_lang_keys_source = new_quick
        else:
            self._quick_lang_keys_target = new_quick
        self._save_quick_languages_to_config(for_source=for_source)
    
    def rebuild_language_combos_advanced(self, source_combo: QComboBox, target_combo: QComboBox, apply_config_selection: bool = True):
        """
        重建语言下拉框（高级版本，支持自动调整快捷语言槽位）
        
        Args:
            source_combo: 源语言下拉框
            target_combo: 目标语言下拉框
            apply_config_selection: 是否应用配置中的选择
        """
        from src.core.languages import ALL_LANGUAGES, normalize_lang_key, normalize_quick_language_keys
        from src.core.languages import display_name_for_key
        
        self._updating_lang_combos = True
        try:
            # 固定 6 个槽位：
            # 0: 临时语言（仅展示/立即生效，不写入配置）
            # 1-4: 快捷语言槽位（会写入配置）
            # 5: 显示更多…（打开选择器）

            src_key: Optional[str] = None
            tgt_key: Optional[str] = None
            if apply_config_selection:
                src_key = normalize_lang_key(self.config_manager.get("translation", "source_language", "en"))
                tgt_key = normalize_lang_key(self.config_manager.get("translation", "target_language", "zh-CN"))

            # 保证快捷槽位长度为 4（并过滤非法 key）
            self._quick_lang_keys_source = normalize_quick_language_keys(self._quick_lang_keys_source, desired_len=4)
            self._quick_lang_keys_target = normalize_quick_language_keys(self._quick_lang_keys_target, desired_len=4)

            def build_combo(combo: QComboBox, *, temp_key: Optional[str], quick_keys: List[str]) -> None:
                combo.clear()
                # slot 0: temp
                if temp_key:
                    nk = normalize_lang_key(temp_key)
                    combo.addItem(f"临时语言：{display_name_for_key(nk) or nk}", f"temp:{nk}")
                else:
                    combo.addItem("临时语言：未设置", "temp:")

                # slot 1-4: quick
                for k in quick_keys[:4]:
                    combo.addItem(display_name_for_key(k), k)

                # slot 5: show more
                combo.addItem("显示更多…", "show_more")

            build_combo(source_combo, temp_key=self._temp_source_lang, quick_keys=self._quick_lang_keys_source)
            build_combo(target_combo, temp_key=self._temp_target_lang, quick_keys=self._quick_lang_keys_target)

            def apply_selection(combo: QComboBox, *, temp_key: Optional[str], desired_key: Optional[str]) -> None:
                # 有临时语言 -> 选中 slot0；否则按配置 key 在 slot1-4 中选择；再否则默认 slot1
                if temp_key:
                    combo.setCurrentIndex(0)
                    return
                if desired_key:
                    for i in range(1, min(5, combo.count())):  # slot1-4
                        if combo.itemData(i) == desired_key:
                            combo.setCurrentIndex(i)
                            return
                # 默认选快捷槽位1（index=1）
                if combo.count() > 1:
                    combo.setCurrentIndex(1)

            apply_selection(source_combo, temp_key=self._temp_source_lang, desired_key=src_key)
            apply_selection(target_combo, temp_key=self._temp_target_lang, desired_key=tgt_key)
        finally:
            self._updating_lang_combos = False

    def reset_temp_language_on_close(self):
        """
        兼容旧调用：窗口关闭时重置临时语言。
        （MainWindow.closeEvent 会调用这个方法）
        """
        self.reset_temp_language_source()
        self.reset_temp_language_target()
