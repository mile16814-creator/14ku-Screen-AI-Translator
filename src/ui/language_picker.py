"""
语言选择器（全语言 + 搜索 + 快捷槽位）

用于主界面"显示更多…"：从全语言里选一个，直接翻译或回填到指定快捷槽位。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class LanguagePickerDialog(QDialog):
    slotClicked = pyqtSignal(int)  # 槽位索引 0-3

    def __init__(
        self,
        *,
        parent=None,
        title: str = "选择语言",
        placeholder: str = "搜索语言（支持中文/代码，如 英语 / en / zh）",
        show_auto: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(550, 520)

        self._show_auto = bool(show_auto)
        self._all_items: List[Tuple[str, str]] = []  # (display, key)
        self._selected_key: Optional[str] = None

        layout = QVBoxLayout(self)

        hint = QLabel("在下方搜索并选择语言，点击右侧数字直接添加到快捷槽位：")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(placeholder)
        self.search_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self.search_edit)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.list_widget, stretch=1)

        info_label = QLabel("提示：点击右侧按钮 1-4 可直接添加到对应快捷槽位，双击或按回车可选择语言后直接翻译")
        info_label.setStyleSheet("color: gray; font-size: 11px;")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        self.ok_btn = QPushButton("确定（选择）")
        self.ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(self.ok_btn)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_btn)

        layout.addLayout(btn_row)

    def set_languages(self, items: List[Tuple[str, str]]) -> None:
        """
        items: [(display_name, key), ...]
        """
        self._all_items = list(items or [])
        
        # 如果不显示自动检测，则过滤掉auto选项
        if not self._show_auto:
            self._all_items = [item for item in self._all_items if item[1] != "auto"]
            
        self._rebuild_list(self._all_items)

    def selected_key(self) -> Optional[str]:
        return self._selected_key

    def selected_slot(self) -> int:
        return getattr(self, '_selected_slot', -1)

    def _create_item_widget(self, display: str, key: str) -> QWidget:
        """为列表项创建自定义 Widget：语言名称 + 4 个数字按钮"""
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(5, 2, 5, 2)
        layout.setSpacing(10)

        label = QLabel(display)
        label.setMinimumWidth(200)
        layout.addWidget(label, stretch=1)

        for slot_idx in range(4):
            btn = QPushButton(str(slot_idx + 1))
            btn.setFixedSize(28, 24)
            btn.setToolTip(f"添加到快捷槽位 {slot_idx + 1}")
            btn.clicked.connect(lambda checked, k=key, s=slot_idx: self._on_slot_clicked(k, s))
            layout.addWidget(btn)

        return container

    def _rebuild_list(self, items: List[Tuple[str, str]]) -> None:
        self.list_widget.clear()
        for display, key in items:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setSizeHint(self._create_item_widget(display, key).sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, self._create_item_widget(display, key))

        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def _on_slot_clicked(self, key: str, slot_index: int) -> None:
        """点击槽位按钮：选择语言并关闭"""
        self._selected_key = key
        self._selected_slot = slot_index
        self.accept()

    def _apply_filter(self) -> None:
        q = (self.search_edit.text() or "").strip().lower()
        if not q:
            self._rebuild_list(self._all_items)
            return

        filtered: List[Tuple[str, str]] = []
        for display, key in self._all_items:
            hay = f"{display} {key}".lower()
            if q in hay:
                filtered.append((display, key))
        self._rebuild_list(filtered)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        if item is None:
            return
        self._selected_key = item.data(Qt.ItemDataRole.UserRole)
        self._selected_slot = -1  # -1 表示直接选择，不添加到槽位
        self.accept()

    def _on_ok(self) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            self._selected_key = None
            self.reject()
            return
        self._selected_key = item.data(Qt.ItemDataRole.UserRole)
        self._selected_slot = -1
        self.accept()


