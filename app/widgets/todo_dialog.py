# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date
from typing import Any

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.services.todo_store import (
    REPEAT_CHOICES,
    REPEAT_LABELS,
    REPEAT_NONE,
)

NEW_CALENDAR_SENTINEL = "__new__"

DELETE_NONE = ""
DELETE_OCCURRENCE = "occurrence"
DELETE_SERIES = "series"

NOTE_MODE_SERIES = "series"
NOTE_MODE_DAY = "day"


class TodoEditDialog(QDialog):
    def __init__(
        self,
        day: date,
        *,
        item: dict[str, Any] | None = None,
        calendars: list[dict[str, str]] | None = None,
        default_calendar_id: str | None = None,
        on_create_calendar=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.day = day
        self.item = item
        self._on_create_calendar = on_create_calendar
        self.setWindowTitle("编辑计划" if item else "添加计划")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("计划内容")
        if item:
            self.title_edit.setText(str(item.get("title", "")))
        form.addRow("内容", self.title_edit)

        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        start = day
        if item and item.get("start"):
            try:
                start = date.fromisoformat(str(item["start"]))
            except ValueError:
                start = day
        self.date_edit.setDate(QDate(start.year, start.month, start.day))
        form.addRow("日期", self.date_edit)

        self.repeat_combo = QComboBox()
        for key in REPEAT_CHOICES:
            self.repeat_combo.addItem(REPEAT_LABELS[key], key)
        repeat = str(item.get("repeat", REPEAT_NONE)) if item else REPEAT_NONE
        idx = self.repeat_combo.findData(repeat)
        self.repeat_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.repeat_combo.currentIndexChanged.connect(self._sync_note_ui)
        form.addRow("重复", self.repeat_combo)

        self.calendar_combo = QComboBox()
        self._calendars = list(calendars or [])
        self._fill_calendars(
            prefer_id=str((item or {}).get("calendar_id") or default_calendar_id or "")
        )
        self.calendar_combo.activated.connect(self._on_calendar_activated)
        form.addRow("日历", self.calendar_combo)

        layout.addLayout(form)

        # 备注区：单日仅当日备注；周期可选系列 / 当日
        note_box = QVBoxLayout()
        note_box.setSpacing(6)

        self.note_mode_row = QWidget()
        mode_row = QHBoxLayout(self.note_mode_row)
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.addWidget(QLabel("备注范围"))
        self.note_series_radio = QRadioButton("系列备注")
        self.note_day_radio = QRadioButton("当日备注")
        self._note_group = QButtonGroup(self)
        self._note_group.addButton(self.note_series_radio)
        self._note_group.addButton(self.note_day_radio)
        mode_row.addWidget(self.note_series_radio)
        mode_row.addWidget(self.note_day_radio)
        mode_row.addStretch(1)
        note_box.addWidget(self.note_mode_row)

        self.note_label = QLabel("当日备注")
        note_box.addWidget(self.note_label)

        self.note_edit = QTextEdit()
        self.note_edit.setAcceptRichText(False)
        self.note_edit.setMinimumHeight(72)
        self.note_edit.setMaximumHeight(120)
        note_box.addWidget(self.note_edit)

        self._series_note_cache = ""
        self._day_note_cache = ""
        if item:
            self._series_note_cache = str(
                item.get("series_detail") or item.get("detail") or ""
            ).strip()
            day_note = str(item.get("day_detail") or "").strip()
            # 单日计划：detail 即当日备注
            if str(item.get("repeat") or REPEAT_NONE) == REPEAT_NONE:
                day_note = day_note or self._series_note_cache
                self._series_note_cache = ""
            self._day_note_cache = day_note

        # 默认：有当日备注时优先选当日，否则系列
        if self._day_note_cache and not self._series_note_cache:
            self.note_day_radio.setChecked(True)
        else:
            self.note_series_radio.setChecked(True)

        self._shown_note_mode = NOTE_MODE_DAY
        self.note_series_radio.toggled.connect(self._on_note_mode_toggled)
        layout.addLayout(note_box)

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
        self._delete_mode = DELETE_NONE
        self._apply_note_field()
        self.title_edit.setFocus()

    def _is_recurring(self) -> bool:
        data = self.repeat_combo.currentData()
        return str(data or REPEAT_NONE) != REPEAT_NONE

    def _active_note_mode(self) -> str:
        if not self._is_recurring():
            return NOTE_MODE_DAY
        return (
            NOTE_MODE_SERIES
            if self.note_series_radio.isChecked()
            else NOTE_MODE_DAY
        )

    def _cache_shown_note(self) -> None:
        text = self.note_edit.toPlainText()
        if self._shown_note_mode == NOTE_MODE_SERIES:
            self._series_note_cache = text
        else:
            self._day_note_cache = text

    def _apply_note_field(self) -> None:
        recurring = self._is_recurring()
        self.note_mode_row.setVisible(recurring)
        mode = self._active_note_mode()
        if mode == NOTE_MODE_SERIES:
            self.note_label.setText("系列备注")
            self.note_edit.setPlaceholderText("整条周期计划共用（可选）")
            self.note_edit.setPlainText(self._series_note_cache)
        else:
            self.note_label.setText("当日备注")
            day_hint = self.day.isoformat()
            if recurring:
                self.note_edit.setPlaceholderText(
                    f"仅 {day_hint}：将拆成当日已完成单日任务，系列跳过该日"
                )
            else:
                self.note_edit.setPlaceholderText(f"{day_hint} 的备注（可选）")
            self.note_edit.setPlainText(self._day_note_cache)
        self._shown_note_mode = mode

    def _sync_note_ui(self) -> None:
        self._cache_shown_note()
        self._apply_note_field()

    def _on_note_mode_toggled(self, checked: bool) -> None:
        if not checked:
            return
        self._cache_shown_note()
        self._apply_note_field()

    def _fill_calendars(self, prefer_id: str = "") -> None:
        self.calendar_combo.blockSignals(True)
        self.calendar_combo.clear()
        for cal in self._calendars:
            cid = str(cal.get("id") or "")
            name = str(cal.get("name") or "")
            if cid and name:
                self.calendar_combo.addItem(name, cid)
        self.calendar_combo.addItem("新建日历…", NEW_CALENDAR_SENTINEL)
        idx = self.calendar_combo.findData(prefer_id)
        if idx >= 0:
            self.calendar_combo.setCurrentIndex(idx)
        elif self.calendar_combo.count() > 1:
            self.calendar_combo.setCurrentIndex(0)
        self.calendar_combo.blockSignals(False)

    def _on_calendar_activated(self, _index: int) -> None:
        if self.calendar_combo.currentData() != NEW_CALENDAR_SENTINEL:
            return
        name, ok = QInputDialog.getText(self, "新建日历", "日历名称：")
        if not ok or not str(name).strip():
            if self.calendar_combo.count() > 1:
                self.calendar_combo.setCurrentIndex(0)
            return
        name = str(name).strip()
        if self._on_create_calendar:
            try:
                info = self._on_create_calendar(name)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "创建失败", str(exc))
                if self.calendar_combo.count() > 1:
                    self.calendar_combo.setCurrentIndex(0)
                return
            if isinstance(info, dict) and info.get("id"):
                self._calendars.append(
                    {"id": str(info["id"]), "name": str(info.get("name") or name)}
                )
                self._fill_calendars(prefer_id=str(info["id"]))
                return
        fake_id = f"local:{name}"
        self._calendars.append({"id": fake_id, "name": name})
        self._fill_calendars(prefer_id=fake_id)

    def _delete(self) -> None:
        repeat = str((self.item or {}).get("repeat", REPEAT_NONE))
        title = str((self.item or {}).get("title", "") or "计划")
        if repeat != REPEAT_NONE:
            box = QMessageBox(self)
            box.setWindowTitle("删除重复计划")
            label = REPEAT_LABELS.get(repeat, "重复")
            box.setText(
                f"「{title}」是{label}计划（当前日 {self.day.isoformat()}）。\n"
                "请选择删除范围："
            )
            only_btn = box.addButton("仅删除此日", QMessageBox.ButtonRole.AcceptRole)
            all_btn = box.addButton("删除全部周期", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked is only_btn:
                self._deleted = True
                self._delete_mode = DELETE_OCCURRENCE
                self.accept()
            elif clicked is all_btn:
                self._deleted = True
                self._delete_mode = DELETE_SERIES
                self.accept()
            return
        if (
            QMessageBox.question(self, "确认", "删除这条计划？")
            == QMessageBox.StandardButton.Yes
        ):
            self._deleted = True
            self._delete_mode = DELETE_SERIES
            self.accept()

    def accept(self) -> None:
        self._cache_shown_note()
        super().accept()

    @property
    def deleted(self) -> bool:
        return self._deleted

    @property
    def delete_mode(self) -> str:
        return self._delete_mode

    @property
    def title_text(self) -> str:
        return self.title_edit.text().strip()

    @property
    def note_mode(self) -> str:
        return self._active_note_mode()

    @property
    def detail_text(self) -> str:
        """系列备注（周期 + 选系列时）；单日为空。"""
        if self.note_mode == NOTE_MODE_SERIES:
            return self._series_note_cache.strip()
        return ""

    @property
    def day_detail_text(self) -> str:
        """当日备注（单日，或周期选当日时）。"""
        if self.note_mode == NOTE_MODE_DAY:
            return self._day_note_cache.strip()
        return ""

    @property
    def plan_date(self) -> date:
        qd = self.date_edit.date()
        return date(qd.year(), qd.month(), qd.day())

    @property
    def repeat_mode(self) -> str:
        data = self.repeat_combo.currentData()
        return str(data) if data else REPEAT_NONE

    @property
    def calendar_id(self) -> str | None:
        data = self.calendar_combo.currentData()
        if data is None or data == NEW_CALENDAR_SENTINEL:
            return None
        return str(data)

    @property
    def calendar_name(self) -> str | None:
        data = self.calendar_combo.currentData()
        if data is None or data == NEW_CALENDAR_SENTINEL:
            return None
        return self.calendar_combo.currentText().strip() or None
