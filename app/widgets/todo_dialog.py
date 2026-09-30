# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date
from typing import Any

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)


class TodoEditDialog(QDialog):
    def __init__(
        self,
        day: date,
        *,
        item: dict[str, Any] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.day = day
        self.item = item
        self.setWindowTitle("编辑待办" if item else "添加待办")
        self.setMinimumWidth(320)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"日期：{day.isoformat()}"))

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("待办内容")
        if item:
            self.title_edit.setText(str(item.get("title", "")))
        layout.addWidget(self.title_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if item:
            row = QHBoxLayout()
            delete_btn = QPushButton("删除")
            delete_btn.clicked.connect(self._delete)
            row.addWidget(delete_btn)
            row.addStretch(1)
            layout.addLayout(row)

        self._deleted = False
        self.title_edit.setFocus()

    def _delete(self) -> None:
        if QMessageBox.question(self, "确认", "删除这条待办？") == QMessageBox.StandardButton.Yes:
            self._deleted = True
            self.accept()

    @property
    def deleted(self) -> bool:
        return self._deleted

    @property
    def title_text(self) -> str:
        return self.title_edit.text().strip()
