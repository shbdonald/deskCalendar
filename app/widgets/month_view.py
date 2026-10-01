# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QLabel, QSizePolicy, QWidget

from app.services.calendar_math import WEEKDAY_LABELS_CN, get_month_grid
from app.services.layout_metrics import WEEKDAY_HEADER_H
from app.widgets.day_cell import DayCell


class MonthView(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(4)
        self._bind_todo: Callable | None = None
        self._bind_edit: Callable | None = None
        self._bind_add: Callable | None = None
        self._headers: list[QLabel] = []
        self._cells: list[DayCell] = []  # 6×7
        self._month_key: tuple[int, int] | None = None

    def bind(
        self,
        *,
        on_toggle: Callable | None = None,
        on_edit: Callable,
        on_add: Callable,
    ) -> None:
        self._bind_todo = on_toggle
        self._bind_edit = on_edit
        self._bind_add = on_add

    def _ensure_structure(self, theme: dict | None) -> None:
        if len(self._cells) == 42 and len(self._headers) == 7:
            return
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._headers.clear()
        self._cells.clear()

        muted = (theme or {}).get("muted", "#8B97A8")
        for i, label in enumerate(WEEKDAY_LABELS_CN):
            hdr = QLabel(label)
            hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hdr.setFixedHeight(WEEKDAY_HEADER_H)
            hdr.setStyleSheet(
                f"color:{muted}; font-size:11px; font-weight:600; border:none; background:transparent;"
            )
            hdr.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._grid.addWidget(hdr, 0, i)
            self._grid.setColumnStretch(i, 1)
            self._headers.append(hdr)

        for r in range(6):
            for c in range(7):
                cell = DayCell(date.today(), in_month=True, compact=False, theme=theme)
                if self._bind_todo:
                    cell.todo_toggled.connect(self._bind_todo)
                if self._bind_edit:
                    cell.todo_edit.connect(self._bind_edit)
                if self._bind_add:
                    cell.day_add.connect(self._bind_add)
                self._grid.addWidget(cell, r + 1, c)
                self._cells.append(cell)
            self._grid.setRowStretch(r + 1, 1)

    def refresh(
        self,
        year: int,
        month: int,
        holiday_fn: Callable[[date], list],
        todo_fn: Callable[[date], list],
        theme: dict | None = None,
    ) -> None:
        self._ensure_structure(theme)
        muted = (theme or {}).get("muted", "#8B97A8")
        for hdr in self._headers:
            hdr.setStyleSheet(
                f"color:{muted}; font-size:11px; font-weight:600; border:none; background:transparent;"
            )

        rows = get_month_grid(year, month)
        idx = 0
        for week in rows:
            for d in week:
                cell = self._cells[idx]
                cell.apply_day(d, in_month=(d.month == month), theme=theme)
                cell.set_content(holiday_fn(d), todo_fn(d))
                idx += 1
        self._month_key = (year, month)
