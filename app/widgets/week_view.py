# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date
from typing import Callable

from PySide6.QtWidgets import QGridLayout, QSizePolicy, QWidget

from app.services.calendar_math import get_week_dates
from app.widgets.day_cell import DayCell


class WeekView(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(4)
        self._bind_todo: Callable | None = None
        self._bind_edit: Callable | None = None
        self._bind_add: Callable | None = None

    def bind(
        self,
        *,
        on_toggle: Callable,
        on_edit: Callable,
        on_add: Callable,
    ) -> None:
        self._bind_todo = on_toggle
        self._bind_edit = on_edit
        self._bind_add = on_add

    def rebuild(
        self,
        ref: date,
        holiday_fn: Callable[[date], list],
        todo_fn: Callable[[date], list],
        theme: dict | None = None,
    ) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        for i, d in enumerate(get_week_dates(ref)):
            cell = DayCell(d, in_month=True, compact=True, theme=theme)
            cell.set_content(holiday_fn(d), todo_fn(d))
            if self._bind_todo:
                cell.todo_toggled.connect(self._bind_todo)
            if self._bind_edit:
                cell.todo_edit.connect(self._bind_edit)
            if self._bind_add:
                cell.day_add.connect(self._bind_add)
            self._grid.addWidget(cell, 0, i)
            self._grid.setColumnStretch(i, 1)
        self._grid.setRowStretch(0, 1)
