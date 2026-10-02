# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date
from html import escape
from typing import Any

from PySide6.QtCore import QEvent, QTimer, Qt, Signal
from PySide6.QtGui import QHelpEvent, QMouseEvent, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from app.services.layout_metrics import MIN_MONTH, MIN_WEEK
from app.services.theme import DEFAULT_THEME
from app.services.todo_store import REPEAT_LABELS, REPEAT_NONE

_WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"]
# 完成/未完成标记色（与主题无关，保证一眼可辨）
_MARK_DONE_COLOR = "#22C55E"
_MARK_TODO_COLOR = "#EAB308"


class TodoLine(QLabel):
    """格子内计划行：仅双击打开编辑，单击不打卡。"""

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
        self._repeat = str(item.get("repeat", REPEAT_NONE))
        self._calendar_name = str(item.get("calendar_name") or "").strip()
        self._theme = theme or DEFAULT_THEME
        self._compact = compact
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setWordWrap(False)
        self.setIndent(0)
        self.setMargin(0)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setToolTip("")  # 悬浮提示由 DayCell 统一处理
        self._refresh()

    def _short_cal(self) -> str:
        name = self._calendar_name
        if not name:
            return ""
        return name if len(name) <= 4 else name[:3]

    def _mark_char(self) -> str:
        return "✓" if self._done else "○"

    def _mark_html(self) -> str:
        color = _MARK_DONE_COLOR if self._done else _MARK_TODO_COLOR
        return f'<span style="color:{color};font-weight:700;">{self._mark_char()}</span>'

    def _meta_suffix(self) -> str:
        """日历 + 周期（不含完成标记）。"""
        parts: list[str] = []
        short = self._short_cal()
        if short:
            parts.append(f"[{short}]")
        if self._repeat and self._repeat != REPEAT_NONE:
            hint = REPEAT_LABELS.get(self._repeat, "")
            if hint:
                parts.append(f"[{hint}]")
        return " ".join(parts)

    def meta_line(self) -> str:
        """第一行纯文本（用于宽度估算）：完成标记 + 日历 + 周期。"""
        suffix = self._meta_suffix()
        mark = self._mark_char()
        return f"{mark} {suffix}" if suffix else mark

    def _set_display(self, meta_suffix: str, title: str) -> None:
        mark = self._mark_html()
        meta = f"{mark} {escape(meta_suffix)}" if meta_suffix else mark
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setText(f"{meta}<br/>{escape(title)}")

    def apply_elide(self, width: int) -> None:
        fm = self.fontMetrics()
        w = max(24, width)
        mark = self._mark_char()
        mark_w = fm.horizontalAdvance(mark + " ")
        suffix = self._meta_suffix()
        if suffix:
            suffix = fm.elidedText(suffix, Qt.TextElideMode.ElideRight, max(8, w - mark_w))
        title = fm.elidedText(self._title, Qt.TextElideMode.ElideRight, w)
        self._set_display(suffix, title)
        self._sync_height()

    def _sync_height(self) -> None:
        """两行字高钉死，避免格子偏矮时上下被裁切。"""
        fm = self.fontMetrics()
        h = fm.height() * 2 + max(0, fm.leading()) + 2
        self.setMinimumHeight(h)
        self.setMaximumHeight(h)
        self.setFixedHeight(h)

    def _refresh(self) -> None:
        color = self._theme["muted"] if self._done else self._theme["text"]
        deco = "text-decoration: line-through;" if self._done else ""
        size = "11px" if self._compact else "10px"
        self._set_display(self._meta_suffix(), self._title)
        self.setStyleSheet(
            f"font-size:{size}; color:{color}; {deco} border:none; background:transparent;"
            "padding:0; margin:0; qproperty-indent:0;"
        )
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._sync_height()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
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
        self._todos_data: list[dict[str, Any]] = []
        self._holidays_data: list[dict[str, str]] = []
        self._cell_tip = ""
        self._laid_out_h = -1
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(120)
        self._relayout_timer.timeout.connect(self._deferred_relayout)
        self.setObjectName("dayCell")
        mw, mh = MIN_WEEK if compact else MIN_MONTH
        self.setMinimumSize(mw, mh)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        self._layout = QVBoxLayout(self)
        if compact:
            self._layout.setContentsMargins(6, 5, 6, 5)
            self._layout.setSpacing(3)
        else:
            self._layout.setContentsMargins(4, 3, 4, 3)
            self._layout.setSpacing(1)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        # Top row: primary (date / day) + secondary (weekday)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(4)
        self._primary = QLabel()
        self._primary.setObjectName("dayHeader")
        self._primary.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._secondary = QLabel()
        self._secondary.setObjectName("daySub")
        top.addWidget(self._primary, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        top.addStretch(1)
        top.addWidget(self._secondary, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._layout.addLayout(top)

        self._holiday = QLabel()
        self._holiday.setObjectName("holidayLabel")
        self._holiday.setWordWrap(False)
        self._holiday.setIndent(0)
        self._holiday.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._holiday.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._layout.addWidget(self._holiday, 0, Qt.AlignmentFlag.AlignLeft)

        self._todo_box = QVBoxLayout()
        self._todo_box.setSpacing(1 if compact else 0)
        self._todo_box.setContentsMargins(0, 0, 0, 0)
        self._todo_box.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        # 计划区占满剩余高度，便于把 +N 顶到格子底部
        self._layout.addLayout(self._todo_box, 1)

        self._apply_chrome()
        # 子控件悬停也走单元格统一提示
        for w in (self._primary, self._secondary, self._holiday):
            w.installEventFilter(self)

    def apply_day(
        self,
        d: date,
        *,
        in_month: bool = True,
        theme: dict[str, str] | None = None,
    ) -> None:
        """就地更新日期/主题，避免销毁重建整个格子。"""
        theme_changed = theme is not None and theme != self._theme
        day_changed = self.day != d or self.in_month != in_month
        if theme is not None:
            self._theme = theme
        self.day = d
        self.in_month = in_month
        if day_changed:
            self._content_sig = None
        if day_changed or theme_changed:
            if self.compact:
                self._primary.show()
            self._apply_chrome()

    def _accent_inset(self) -> int:
        """今日左边框画在内容区内，需给布局让出同等内边距。"""
        return 3 if self.day == date.today() else 0

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
        inset = self._accent_inset()
        # 左边强调条画在框内，用 layout 左边距让出，避免挡住文字
        base_l, top_m, right_m, bottom_m = (6, 5, 6, 5) if self.compact else (4, 3, 4, 3)
        self._layout.setContentsMargins(base_l + inset, top_m, right_m, bottom_m)
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
        self._sync_header_heights()

    def _sync_header_heights(self) -> None:
        for lab in (self._primary, self._secondary):
            if lab.isVisible() and lab.text():
                h = lab.fontMetrics().height() + 2
                lab.setMinimumHeight(h)
                lab.setMaximumHeight(h)
        if self._holiday.isVisible() and self._holiday_full:
            h = self._holiday.fontMetrics().height() + 2
            self._holiday.setMinimumHeight(h)
            self._holiday.setMaximumHeight(h)
        else:
            self._holiday.setMaximumHeight(16777215)
            self._holiday.setMinimumHeight(0)

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

    def _content_height(self) -> int:
        return max(0, self.contentsRect().height())

    def _header_used_height(self) -> int:
        m = self._layout.contentsMargins()
        used = m.top() + m.bottom() + self._layout.spacing()
        if self._primary.isVisible() and self._primary.text():
            used += self._primary.minimumHeight() or self._primary.fontMetrics().height()
        elif self._secondary.isVisible():
            used += self._secondary.minimumHeight() or self._secondary.fontMetrics().height()
        if self._holiday.isVisible() and self._holiday_full:
            used += self._layout.spacing()
            used += self._holiday.minimumHeight() or self._holiday.fontMetrics().height()
        return used

    def _max_todos_for_height(self) -> int:
        avail = self._content_height() - self._header_used_height()
        pitch = max(1, self._todo_line_pitch())
        soft_cap = 3 if self.compact else 2
        total = len(self._todos_data)
        n_fit = max(0, avail // pitch)
        # 还有更多时先给「+N」留独立空间，避免被计划行挡住
        if total > min(soft_cap, n_fit):
            n_fit = max(0, (avail - self._more_label_height()) // pitch)
        # 有计划但高度紧（假日占位等）时至少挤出 1 条，避免整格空白
        if total > 0 and n_fit <= 0 and avail >= 12:
            n_fit = 1
        return min(soft_cap, n_fit)

    def _todo_line_pitch(self) -> int:
        """单条计划（两行）占用高度含间距。"""
        size_px = 11 if self.compact else 10
        # 与 TodoLine 样式接近的估算；真实高度以实例为准
        fm = self.fontMetrics()
        # 用当前控件字体近似，再按字号比例
        h = max(fm.height(), int(size_px * 1.4))
        line_h = h * 2 + 2
        gap = 1 if self.compact else 0
        return line_h + gap

    def _more_label_height(self) -> int:
        # 各视图统一：仅按字高占位
        return self.fontMetrics().height()

    def _rebuild_todos(self) -> None:
        while self._todo_box.count():
            item = self._todo_box.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        limit = self._max_todos_for_height()
        todos = self._todos_data
        for todo in todos[:limit]:
            line = TodoLine(todo, theme=self._theme, compact=self.compact)
            line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            line.double_clicked.connect(
                lambda _id, tid=todo["id"]: self.todo_edit.emit(self.day, tid)
            )
            self._todo_box.addWidget(
                line, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
            )
            line.installEventFilter(self)
        if len(todos) > limit:
            rest = len(todos) - limit
            more = QLabel(f"+{rest}")
            more.setObjectName("moreTodos")
            more.setAlignment(Qt.AlignmentFlag.AlignCenter)
            t = self._theme
            more.setStyleSheet(
                f"QLabel#moreTodos {{"
                f" color:{t['text']}; background:{t['btn']};"
                f" border:1px solid {t['border']}; border-radius:3px;"
                f" padding:2px 8px; font-size:11px; font-weight:700;"
                f" }}"
            )
            # 按文字算宽高，避免只露出「+」数字被裁掉
            fm = more.fontMetrics()
            text = f"+{rest}"
            tw = fm.horizontalAdvance(text) + 20
            th = max(1, self._more_label_height())
            more.setFixedSize(tw, th)
            more.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            # 弹性空白把 +N 压到格子底部
            self._todo_box.addStretch(1)
            self._todo_box.addWidget(more, 0, Qt.AlignmentFlag.AlignLeft)
            more.installEventFilter(self)
            more.setToolTip("")
        else:
            self._todo_box.addStretch(1)

        self._refresh_elide()

    def set_content(self, holidays: list[dict[str, str]], todos: list[dict[str, Any]]) -> None:
        sig = (
            tuple((str(h.get("country", "")), str(h.get("name", ""))) for h in holidays),
            tuple(
                (
                    str(t.get("id", "")),
                    str(t.get("title", "")),
                    bool(t.get("done")),
                    str(t.get("calendar_name") or ""),
                    str(t.get("repeat") or ""),
                )
                for t in todos
            ),
        )
        if sig == getattr(self, "_content_sig", None):
            return
        self._content_sig = sig
        self._holidays_data = list(holidays)
        self._todos_data = list(todos)
        self.setUpdatesEnabled(False)
        try:
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

            self._sync_header_heights()
            self._rebuild_todos()
            self._apply_cell_tooltip(holidays, todos)
            self._laid_out_h = self._content_height()
        finally:
            self.setUpdatesEnabled(True)
        # 高度尚未稳定时再防抖补一次（避免立即二次 rebuild）
        if self._laid_out_h <= 0:
            self._schedule_relayout()

    def _schedule_relayout(self, *, immediate: bool = False) -> None:
        """重建计划行较重；拖动缩放时用防抖，松手后才稳定重排。"""
        if immediate:
            self._relayout_timer.stop()
            self._deferred_relayout()
            return
        self._relayout_timer.start()

    def _deferred_relayout(self) -> None:
        if not self._todos_data and not self._holiday_full:
            self._laid_out_h = self._content_height()
            return
        self._sync_header_heights()
        self._rebuild_todos()
        self._apply_cell_tooltip(self._holidays_data, self._todos_data)
        self._laid_out_h = self._content_height()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._schedule_relayout()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        # 拖动过程中只更新省略号，避免每帧销毁/重建 TodoLine 导致卡顿和错位
        self._refresh_elide()
        h = self._content_height()
        if h == self._laid_out_h:
            return
        if not self._todos_data and not self._holiday_full:
            self._laid_out_h = h
            return
        # 高度变化会改变可见条数 / +N：防抖后重排
        self._schedule_relayout()

    def _apply_cell_tooltip(
        self,
        holidays: list[dict[str, str]],
        todos: list[dict[str, Any]],
    ) -> None:
        wd = self._weekday_cn()
        lines = [
            escape(f"{self.day.year}-{self.day.month:02d}-{self.day.day:02d} 周{wd}")
        ]
        if holidays:
            lines.append("")
            lines.append("节日")
            for h in holidays:
                lines.append(
                    escape(f"  [{h['country']}] {h['name']}")
                )
        if todos:
            lines.append("")
            lines.append("计划")
            for t in todos:
                if t.get("done"):
                    mark = f'<span style="color:{_MARK_DONE_COLOR};font-weight:700;">✓</span>'
                else:
                    mark = f'<span style="color:{_MARK_TODO_COLOR};font-weight:700;">○</span>'
                meta_bits: list[str] = [f"  {mark}"]
                cname = str(t.get("calendar_name") or "").strip()
                if cname:
                    short = cname if len(cname) <= 4 else cname[:3]
                    meta_bits.append(escape(f"[{short}]"))
                repeat = str(t.get("repeat", REPEAT_NONE))
                hint = REPEAT_LABELS.get(repeat, "")
                if repeat != REPEAT_NONE and hint:
                    meta_bits.append(escape(f"[{hint}]"))
                lines.append(" ".join(meta_bits))
                lines.append(escape(f"    {t.get('title', '')}"))
        if not holidays and not todos:
            lines.append("")
            lines.append("（无节日/计划）")

        # HTML，便于完成/未完成标记着色；空行用 <br/>
        tip_parts: list[str] = []
        for line in lines:
            tip_parts.append(line if line else "&nbsp;")
        self._cell_tip = "<br/>".join(tip_parts)
        # 不用子控件各自的 ToolTip；整格统一弹出，鼠标在格内移动不因跨计划行重启
        self.setToolTip("")
        for w in (self._primary, self._secondary, self._holiday):
            w.setToolTip("")
        for i in range(self._todo_box.count()):
            item = self._todo_box.itemAt(i)
            wid = item.widget() if item else None
            if wid is not None:
                wid.setToolTip("")

    def _show_cell_tooltip(self, global_pos) -> None:  # noqa: ANN001
        if not self._cell_tip:
            return
        QToolTip.showText(global_pos, self._cell_tip, self, self.rect())

    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001
        if event.type() == QEvent.Type.ToolTip and self._cell_tip:
            if isinstance(event, QHelpEvent):
                self._show_cell_tooltip(event.globalPos())
                return True
        return super().eventFilter(obj, event)

    def event(self, event) -> bool:  # noqa: ANN001
        if event.type() == QEvent.Type.ToolTip and self._cell_tip:
            if isinstance(event, QHelpEvent):
                self._show_cell_tooltip(event.globalPos())
                return True
        return super().event(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.day_add.emit(self.day)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
