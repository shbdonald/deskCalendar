# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QMouseEvent, QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.services.layout_metrics import MIN_MONTH, MIN_WEEK
from app.services.theme import DEFAULT_THEME

_WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"]


class TodoLine(QLabel):
    clicked = Signal(str)
    double_clicked = Signal(str)

    def __init__(
        self,
        item: dict[str, Any],
        theme: dict[str, str] | None = None,
        *,
        compact: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.item_id = item["id"]
        self._done = bool(item.get("done"))
        self._title = str(item.get("title", ""))
        self._theme = theme or DEFAULT_THEME
        self._compact = compact
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setWordWrap(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(220)
        self._click_timer.timeout.connect(self._emit_single_click)
        self._refresh()

    def full_text(self) -> str:
        mark = "✓" if self._done else "·"
        return f"{mark} {self._title}"

    def apply_elide(self, width: int) -> None:
        fm = self.fontMetrics()
        self.setText(fm.elidedText(self.full_text(), Qt.TextElideMode.ElideRight, max(24, width)))

    def _refresh(self) -> None:
        color = self._theme["muted"] if self._done else self._theme["text"]
        deco = "text-decoration: line-through;" if self._done else ""
        size = "11px" if self._compact else "10px"
        self.setText(self.full_text())
        self.setStyleSheet(
            f"font-size:{size}; color:{color}; {deco} border:none; background:transparent;"
            "padding:0; margin:0;"
        )

    def _emit_single_click(self) -> None:
        self.clicked.emit(self.item_id)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._click_timer.isActive():
                self._click_timer.stop()
            else:
                self._click_timer.start()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._click_timer.stop()
            self.double_clicked.emit(self.item_id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class DayCell(QFrame):
    todo_toggled = Signal(object, str)
    todo_edit = Signal(object, str)
    day_add = Signal(object)

    def __init__(
        self,
        d: date,
        *,
        in_month: bool = True,
        compact: bool = False,
        theme: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.day = d
        self.in_month = in_month
        self.compact = compact
        self._theme = theme or dict(DEFAULT_THEME)
        self._holiday_full = ""
        self.setObjectName("dayCell")
        mw, mh = MIN_WEEK if compact else MIN_MONTH
        self.setMinimumSize(mw, mh)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._layout = QVBoxLayout(self)
        if compact:
            self._layout.setContentsMargins(6, 5, 6, 5)
            self._layout.setSpacing(3)
        else:
            self._layout.setContentsMargins(4, 3, 4, 3)
            self._layout.setSpacing(1)

        # Top row: primary (date / day) + secondary (weekday)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(4)
        self._primary = QLabel()
        self._primary.setObjectName("dayHeader")
        self._secondary = QLabel()
        self._secondary.setObjectName("daySub")
        top.addWidget(self._primary, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        top.addStretch(1)
        top.addWidget(self._secondary, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._layout.addLayout(top)

        self._holiday = QLabel()
        self._holiday.setObjectName("holidayLabel")
        self._holiday.setWordWrap(False)
        self._holiday.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._layout.addWidget(self._holiday)

        self._todo_box = QVBoxLayout()
        self._todo_box.setSpacing(1 if compact else 0)
        self._todo_box.setContentsMargins(0, 0, 0, 0)
        self._layout.addLayout(self._todo_box)
        self._layout.addStretch(1)

        self._apply_chrome()

    def _content_width(self) -> int:
        m = self._layout.contentsMargins()
        return max(24, self.width() - m.left() - m.right())

    def _weekday_cn(self) -> str:
        return _WEEKDAYS[(self.day.weekday() + 1) % 7]

    def _apply_chrome(self) -> None:
        t = self._theme
        today = self.day == date.today()
        text = t["text"] if self.in_month else t["muted"]
        muted = t["muted"]
        bg = t["cell_today"] if today else t["cell"]
        border = (
            f"border:none; border-left:3px solid {t['accent']};"
            if today
            else "border:none;"
        )
        wd = self._weekday_cn()

        if self.compact:
            # Week: "3/30" left, "周日" right
            header_css = (
                f"font-size:14px; font-weight:700; color:{text}; "
                "border:none; background:transparent; padding:0;"
            )
            sub_css = (
                f"font-size:11px; font-weight:500; color:{muted}; "
                "border:none; background:transparent; padding:0;"
            )
            holiday_css = (
                f"font-size:10px; color:{t['holiday']}; "
                "border:none; background:transparent; padding:0;"
            )
            self._primary.setText(f"{self.day.month}/{self.day.day}")
            self._secondary.setText(f"周{wd}")
            self._secondary.show()
            self._holiday.setMaximumHeight(16)
            self._holiday.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
        else:
            # Month: day number top-right
            header_css = (
                f"font-size:12px; font-weight:700; color:{text}; "
                "border:none; background:transparent; padding:0;"
            )
            sub_css = "border:none; background:transparent;"
            holiday_css = (
                f"font-size:9px; color:{t['holiday']}; "
                "border:none; background:transparent; padding:0;"
            )
            self._primary.clear()
            self._primary.hide()
            self._secondary.setText(str(self.day.day))
            self._secondary.show()
            self._holiday.setMaximumHeight(14)
            self._holiday.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )

        self.setStyleSheet(
            f"""
            QFrame#dayCell {{
                background: {bg};
                {border}
                border-radius: 2px;
            }}
            QLabel#dayHeader {{ {header_css} }}
            QLabel#daySub {{ {sub_css if self.compact else header_css} }}
            QLabel#holidayLabel {{ {holiday_css} }}
            """
        )

    def _refresh_elide(self) -> None:
        w = self._content_width()
        if self._holiday_full and self._holiday.isVisible():
            fm = self._holiday.fontMetrics()
            self._holiday.setText(
                fm.elidedText(self._holiday_full, Qt.TextElideMode.ElideRight, w)
            )
        for i in range(self._todo_box.count()):
            item = self._todo_box.itemAt(i)
            wid = item.widget() if item else None
            if isinstance(wid, TodoLine):
                wid.apply_elide(w)

    def set_content(self, holidays: list[dict[str, str]], todos: list[dict[str, Any]]) -> None:
        if holidays:
            h0 = holidays[0]
            line = f"{h0['country']}·{h0['name']}"
            if len(holidays) > 1:
                line += f" +{len(holidays) - 1}"
            self._holiday_full = line
            self._holiday.setText(line)
            self._holiday.show()
        else:
            self._holiday_full = ""
            self._holiday.clear()
            self._holiday.hide()

        while self._todo_box.count():
            item = self._todo_box.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        # Week cells are taller → allow more todo lines.
        limit = 4 if self.compact else 3
        for todo in todos[:limit]:
            line = TodoLine(todo, theme=self._theme, compact=self.compact)
            line.clicked.connect(
                lambda _id, tid=todo["id"]: self.todo_toggled.emit(self.day, tid)
            )
            line.double_clicked.connect(
                lambda _id, tid=todo["id"]: self.todo_edit.emit(self.day, tid)
            )
            self._todo_box.addWidget(line)
        if len(todos) > limit:
            more = QLabel(f"+{len(todos) - limit}")
            more.setStyleSheet(
                f"color:{self._theme['muted']}; font-size:9px; border:none; background:transparent;"
            )
            self._todo_box.addWidget(more)

        self._apply_cell_tooltip(holidays, todos)
        self._refresh_elide()

    def _apply_cell_tooltip(
        self,
        holidays: list[dict[str, str]],
        todos: list[dict[str, Any]],
    ) -> None:
        wd = self._weekday_cn()
        lines = [f"{self.day.year}-{self.day.month:02d}-{self.day.day:02d} 周{wd}"]
        if holidays:
            lines.append("")
            lines.append("节日")
            for h in holidays:
                lines.append(f"  [{h['country']}] {h['name']}")
        if todos:
            lines.append("")
            lines.append("待办")
            for t in todos:
                mark = "✓" if t.get("done") else "○"
                lines.append(f"  {mark} {t.get('title', '')}")
        tip = "\n".join(lines)
        if not holidays and not todos:
            tip += "\n（无节日/待办）"

        self.setToolTip(tip)
        self._primary.setToolTip(tip)
        self._secondary.setToolTip(tip)
        self._holiday.setToolTip(tip)
        for i in range(self._todo_box.count()):
            item = self._todo_box.itemAt(i)
            w = item.widget() if item else None
            if w is not None:
                w.setToolTip(tip)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._refresh_elide()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.day_add.emit(self.day)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
