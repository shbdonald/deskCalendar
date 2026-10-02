# -*- coding: utf-8 -*-
"""今日计划操作台（投影自 TodoStore 当日计划）。"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QCloseEvent, QCursor, QMouseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.services.theme import DEFAULT_THEME, merge_theme

_ICON_CLOSE = "✕"
_EDGE = 8
_MIN_W = 240
_MIN_H = 280


class _TodoRow(QFrame):
    toggled = Signal(str, bool)
    edited = Signal(str, str)
    deleted = Signal(str)
    edit_requested = Signal(str)

    def __init__(
        self,
        item: dict[str, Any],
        theme: dict[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.item_id = str(item["id"])
        self._theme = theme
        self._orig_title = str(item.get("title", ""))
        self.setObjectName("todoRow")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 4, 2, 4)
        lay.setSpacing(4)
        lay.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.check = QCheckBox()
        self.check.setChecked(bool(item.get("done")))
        self.check.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.check.toggled.connect(self._on_toggled)
        lay.addWidget(self.check, 0, Qt.AlignmentFlag.AlignLeft)

        self.title = QLineEdit()
        self.title.setObjectName("todoTitleEdit")
        self.title.setText(self._orig_title)
        self.title.setCursorPosition(0)
        self.title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.title.setReadOnly(True)
        self.title.setFrame(False)
        self.title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.title.returnPressed.connect(self._commit_edit)
        self.title.editingFinished.connect(self._commit_edit)
        self.title.mouseDoubleClickEvent = self._on_title_dbl  # type: ignore[method-assign]
        lay.addWidget(self.title, 1)

        edit_btn = QPushButton("…")
        edit_btn.setObjectName("rowDel")
        edit_btn.setFixedSize(24, 24)
        edit_btn.setFlat(True)
        edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        edit_btn.setToolTip("编辑（含详情）")
        edit_btn.clicked.connect(lambda: self.edit_requested.emit(self.item_id))
        lay.addWidget(edit_btn)

        del_btn = QPushButton("×")
        del_btn.setObjectName("rowDel")
        del_btn.setFixedSize(24, 24)
        del_btn.setFlat(True)
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.setToolTip("仅删除今日")
        del_btn.clicked.connect(lambda: self.deleted.emit(self.item_id))
        lay.addWidget(del_btn)

        self._apply_done_style(bool(item.get("done")))
        tip_parts = []
        cal = str(item.get("calendar_name") or "").strip()
        if cal:
            tip_parts.append(cal)
        detail = str(item.get("detail") or "").strip()
        if detail:
            tip_parts.append(detail)
        if tip_parts:
            tip = "\n".join(tip_parts)
            self.setToolTip(tip)
            self.title.setToolTip(tip)

    def _on_title_dbl(self, event: QMouseEvent) -> None:  # noqa: N802
        self.title.setReadOnly(False)
        self.title.setFocus()
        self.title.selectAll()
        event.accept()

    def _commit_edit(self) -> None:
        if self.title.isReadOnly():
            return
        self.title.setReadOnly(True)
        text = self.title.text().strip()
        if text:
            self._orig_title = text
            self.edited.emit(self.item_id, text)
        else:
            self.title.setText(self._orig_title)
        self.title.setCursorPosition(0)

    def _on_toggled(self, checked: bool) -> None:
        self._apply_done_style(checked)
        self.toggled.emit(self.item_id, checked)

    def _apply_done_style(self, done: bool) -> None:
        color = self._theme["muted"] if done else self._theme["text"]
        deco = "text-decoration: line-through;" if done else ""
        self.title.setStyleSheet(
            f"QLineEdit#todoTitleEdit {{"
            f" color:{color}; background:transparent; border:none; padding:0 2px;"
            f" {deco} }}"
        )
        self.title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.title.setCursorPosition(0)

    def apply_theme(self, theme: dict[str, str]) -> None:
        self._theme = theme
        self._apply_done_style(self.check.isChecked())

    def apply_item(self, item: dict[str, Any]) -> None:
        """就地更新一行，避免整表重建闪动。"""
        self.item_id = str(item["id"])
        title = str(item.get("title", ""))
        done = bool(item.get("done"))
        self._orig_title = title
        if self.title.text() != title:
            self.title.setText(title)
            self.title.setCursorPosition(0)
        if self.check.isChecked() != done:
            self.check.blockSignals(True)
            self.check.setChecked(done)
            self.check.blockSignals(False)
        self._apply_done_style(done)
        tip_parts = []
        cal = str(item.get("calendar_name") or "").strip()
        if cal:
            tip_parts.append(cal)
        detail = str(item.get("detail") or "").strip()
        if detail:
            tip_parts.append(detail)
        tip = "\n".join(tip_parts) if tip_parts else ""
        self.setToolTip(tip)
        self.title.setToolTip(tip)


class TodoListWindow(QWidget):
    """今日计划窗；关闭 = 隐藏。锁定时由主窗磁贴；解锁时可拖动/缩放。"""

    visibility_changed = Signal(bool)
    geometry_changed = Signal()
    geometry_moving = Signal(str)  # "drag" | "resize"
    undock_requested = Signal()
    add_requested = Signal(str)
    done_changed = Signal(str, bool)
    title_changed = Signal(str, str)
    delete_requested = Signal(str)
    edit_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("今日待办")
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(300, 320)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)

        self._theme = dict(DEFAULT_THEME)
        self._suppress_toggle = False
        self._interactive = False
        self._closable = True
        self._allow_programmatic_hide = False
        self._dock_slide = False  # 贴合滑动：不自行 move，由主窗算几何
        self._drag_pos: QPoint | None = None
        self._resize_edges = 0  # bitflags: 1L 2R 4T 8B
        self._resize_origin: QRect | None = None
        self._resize_mouse: QPoint | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        chrome = QHBoxLayout()
        self._title = QLabel("今日待办")
        self._title.setObjectName("todoTitle")
        self._title.setToolTip(
            "副窗口：贴合后随日历移动；可沿贴合边滑动。"
            "拉开或双击标题取消贴合；Ctrl+拖动也可取消。"
        )
        chrome.addWidget(self._title, 1)
        self._close_btn = QPushButton(_ICON_CLOSE)
        self._close_btn.setObjectName("chromeBtn")
        self._close_btn.setFixedSize(28, 24)
        self._close_btn.setFlat(True)
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setToolTip("隐藏今日待办")
        self._close_btn.clicked.connect(self._on_close_clicked)
        chrome.addWidget(self._close_btn)
        root.addLayout(chrome)

        add_row = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setPlaceholderText("添加今日计划…")
        self._input.returnPressed.connect(self._on_add)
        self._add_btn = QPushButton("添加")
        self._add_btn.setObjectName("addBtn")
        self._add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_btn.clicked.connect(self._on_add)
        add_row.addWidget(self._input, 1)
        add_row.addWidget(self._add_btn)
        root.addLayout(add_row)

        self._pending_label = QLabel("未完成")
        self._pending_label.setObjectName("sectionLabel")
        root.addWidget(self._pending_label)

        self._pending_scroll = QScrollArea()
        self._pending_scroll.setWidgetResizable(True)
        self._pending_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._pending_scroll.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self._pending_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._pending_holder = QWidget()
        self._pending_holder.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        self._pending_box = QVBoxLayout(self._pending_holder)
        self._pending_box.setContentsMargins(0, 0, 0, 0)
        self._pending_box.setSpacing(2)
        self._pending_box.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._pending_box.addStretch(1)
        self._pending_scroll.setWidget(self._pending_holder)
        root.addWidget(self._pending_scroll, 2)

        self._done_label = QLabel("已完成")
        self._done_label.setObjectName("sectionLabel")
        root.addWidget(self._done_label)

        self._done_scroll = QScrollArea()
        self._done_scroll.setWidgetResizable(True)
        self._done_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._done_scroll.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self._done_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._done_holder = QWidget()
        self._done_holder.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        self._done_box = QVBoxLayout(self._done_holder)
        self._done_box.setContentsMargins(0, 0, 0, 0)
        self._done_box.setSpacing(2)
        self._done_box.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._done_box.addStretch(1)
        self._done_scroll.setWidget(self._done_holder)
        root.addWidget(self._done_scroll, 1)

        self._rows: dict[str, _TodoRow] = {}
        self.apply_theme(self._theme)

    def set_interactive(self, enabled: bool) -> None:
        """解锁后可拖动与缩放、可关闭；锁定后磁贴且不可关闭。"""
        self._interactive = bool(enabled)
        self._closable = bool(enabled)
        self._drag_pos = None
        self._resize_edges = 0
        self._resize_origin = None
        self._resize_mouse = None
        if not enabled:
            self._dock_slide = False
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._close_btn.setVisible(self._closable)
        self._close_btn.setEnabled(self._closable)
        self._close_btn.setToolTip(
            "隐藏今日待办" if self._closable else "锁定时不可关闭今日待办"
        )

    def set_dock_slide(self, enabled: bool) -> None:
        """贴合滑动中：拖动事件只通知主窗，避免 move 与 apply_dock 互抢。"""
        self._dock_slide = bool(enabled)

    def drag_grab(self) -> QPoint | None:
        return QPoint(self._drag_pos) if self._drag_pos is not None else None

    def resync_drag_grab(self, global_pos: QPoint) -> None:
        """外部 setGeometry（贴合）后同步拖拽抓点，避免下一帧与吸附抢位置抖动。"""
        if self._drag_pos is not None:
            self._drag_pos = global_pos - self.frameGeometry().topLeft()

    def _on_close_clicked(self) -> None:
        if not self._closable:
            return
        self.hide()

    def apply_theme(self, theme: dict[str, str] | None) -> None:
        self._theme = merge_theme(theme)
        t = self._theme
        self.setStyleSheet(
            f"""
            TodoListWindow {{
                background: {t["bg"]};
                color: {t["text"]};
                border: 1px solid {t["border"]};
                border-radius: 4px;
            }}
            QLabel#todoTitle {{
                color: {t["text"]}; font-size: 14px; font-weight: 600;
            }}
            QLabel#sectionLabel {{
                color: {t["muted"]}; font-size: 11px;
            }}
            QLineEdit {{
                background: {t["btn"]};
                color: {t["text"]};
                border: 1px solid #445;
                border-radius: 2px;
                padding: 4px 6px;
            }}
            QLineEdit#todoTitleEdit {{
                background: transparent;
                border: none;
                padding: 0 2px;
            }}
            QPushButton#chromeBtn, QPushButton#rowDel {{
                background: transparent; color: {t["muted"]}; border: none;
            }}
            QPushButton#chromeBtn:hover, QPushButton#rowDel:hover {{
                color: {t["text"]}; background: {t["btn_hover"]};
            }}
            QPushButton#addBtn {{
                background: {t["btn"]}; color: {t["text"]};
                border: none; border-radius: 2px; padding: 4px 10px;
            }}
            QPushButton#addBtn:hover {{ background: {t["btn_hover"]}; }}
            QCheckBox {{ color: {t["text"]}; spacing: 6px; }}
            QScrollArea {{ background: transparent; border: none; }}
            QFrame#todoRow {{
                background: transparent; border-radius: 2px;
            }}
            QFrame#todoRow:hover {{ background: {t["cell"]}; }}
            """
        )
        for row in self._rows.values():
            row.apply_theme(self._theme)

    def set_items(self, items: list[dict[str, Any]]) -> None:
        pending = [it for it in items if not it.get("done")]
        done = [it for it in items if it.get("done")]
        keep_ids = {str(it["id"]) for it in items}
        self._sync_box(self._pending_box, pending)
        self._sync_box(self._done_box, done)
        # 回收两端都不需要的行
        for iid in list(self._rows):
            if iid not in keep_ids:
                row = self._rows.pop(iid)
                row.deleteLater()
        self._pending_label.setText(f"未完成（{len(pending)}）")
        self._done_label.setText(f"已完成（{len(done)}）")
        self._title.setText(f"今日待办（{len(done)}/{len(items)}）")

    def _sync_box(self, box: QVBoxLayout, items: list[dict[str, Any]]) -> None:
        """按 id 复用行控件，只增删变化项。"""
        while box.count():
            item = box.itemAt(box.count() - 1)
            if item is None:
                break
            if item.spacerItem() is not None:
                box.takeAt(box.count() - 1)
                continue
            if item.widget() is None:
                box.takeAt(box.count() - 1)
                continue
            break

        desired_set = {str(it["id"]) for it in items}

        current: list[_TodoRow] = []
        for i in range(box.count()):
            w = box.itemAt(i).widget()
            if isinstance(w, _TodoRow):
                current.append(w)

        # 本栏不需要的只拆下来，留给另一栏复用（不 delete）
        for row in current:
            if row.item_id not in desired_set:
                box.removeWidget(row)
                row.setParent(None)

        def _detach(row: _TodoRow) -> None:
            for lay in (self._pending_box, self._done_box):
                if lay.indexOf(row) >= 0:
                    lay.removeWidget(row)
                    break
            row.setParent(None)

        for idx, it in enumerate(items):
            iid = str(it["id"])
            row = self._rows.get(iid)
            if row is None:
                row = _TodoRow(it, self._theme)
                row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                row.toggled.connect(self._on_row_toggled)
                row.edited.connect(self.title_changed.emit)
                row.deleted.connect(self.delete_requested.emit)
                row.edit_requested.connect(self.edit_requested.emit)
                self._rows[iid] = row
                box.insertWidget(idx, row, 0, Qt.AlignmentFlag.AlignTop)
            else:
                row.apply_item(it)
                at = box.indexOf(row)
                if at < 0:
                    _detach(row)
                    box.insertWidget(idx, row, 0, Qt.AlignmentFlag.AlignTop)
                elif at != idx:
                    box.removeWidget(row)
                    box.insertWidget(idx, row, 0, Qt.AlignmentFlag.AlignTop)
                row.show()

        box.addStretch(1)

    def _on_row_toggled(self, item_id: str, done: bool) -> None:
        if self._suppress_toggle:
            return
        self.done_changed.emit(item_id, done)

    def _on_add(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        self.add_requested.emit(text)

    def _hit_edges(self, local: QPoint) -> int:
        edges = 0
        if local.x() <= _EDGE:
            edges |= 1
        if local.x() >= self.width() - _EDGE:
            edges |= 2
        if local.y() <= _EDGE:
            edges |= 4
        if local.y() >= self.height() - _EDGE:
            edges |= 8
        return edges

    @staticmethod
    def _cursor_for_edges(edges: int) -> Qt.CursorShape:
        if edges in (1 | 4, 2 | 8):
            return Qt.CursorShape.SizeFDiagCursor
        if edges in (2 | 4, 1 | 8):
            return Qt.CursorShape.SizeBDiagCursor
        if edges & (1 | 2):
            return Qt.CursorShape.SizeHorCursor
        if edges & (4 | 8):
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._interactive:
            local = event.position().toPoint()
            edges = self._hit_edges(local)
            if edges:
                self._resize_edges = edges
                self._resize_origin = QRect(self.geometry())
                self._resize_mouse = event.globalPosition().toPoint()
                self._drag_pos = None
                event.accept()
                return
            if local.y() < 36:
                # Ctrl+拖动：请求取消贴合后自由移动
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    self.undock_requested.emit()
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                self._resize_edges = 0
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 36:
            self.undock_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._interactive and self._resize_edges and self._resize_origin and self._resize_mouse is not None:
            self._perform_resize(event.globalPosition().toPoint())
            self.geometry_moving.emit("resize")
            event.accept()
            return
        if self._interactive and self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if self._dock_slide:
                # 几何完全由主窗根据光标+抓点计算
                self.geometry_moving.emit("drag")
            else:
                self.move(event.globalPosition().toPoint() - self._drag_pos)
                self.geometry_moving.emit("drag")
            event.accept()
            return
        if self._interactive and event.buttons() == Qt.MouseButton.NoButton:
            edges = self._hit_edges(event.position().toPoint())
            self.setCursor(QCursor(self._cursor_for_edges(edges)))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._resize_edges or self._drag_pos is not None:
                self._resize_edges = 0
                self._resize_origin = None
                self._resize_mouse = None
                self._drag_pos = None
                self.geometry_changed.emit()
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def _perform_resize(self, global_pos: QPoint) -> None:
        """按边缘缩放；触达最小宽高时钉住对边，不回弹到按下时的尺寸。"""
        if self._resize_origin is None or self._resize_mouse is None:
            return
        delta = global_pos - self._resize_mouse
        o = self._resize_origin
        x, y, w, h = o.x(), o.y(), o.width(), o.height()
        right = x + w
        bottom = y + h

        if self._resize_edges & 1:  # left：钉住右边
            new_x = o.x() + delta.x()
            new_w = right - new_x
            if new_w < _MIN_W:
                new_w = _MIN_W
                new_x = right - _MIN_W
            x, w = new_x, new_w
        if self._resize_edges & 2:  # right：钉住左边
            new_w = o.width() + delta.x()
            if new_w < _MIN_W:
                new_w = _MIN_W
            w = new_w
        if self._resize_edges & 4:  # top：钉住底边
            new_y = o.y() + delta.y()
            new_h = bottom - new_y
            if new_h < _MIN_H:
                new_h = _MIN_H
                new_y = bottom - _MIN_H
            y, h = new_y, new_h
        if self._resize_edges & 8:  # bottom：钉住顶边
            new_h = o.height() + delta.y()
            if new_h < _MIN_H:
                new_h = _MIN_H
            h = new_h

        self.setGeometry(x, y, w, h)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._closable and not self._allow_programmatic_hide:
            event.ignore()
            return
        self.geometry_changed.emit()
        event.ignore()
        self.hide()

    def hideEvent(self, event) -> None:  # noqa: ANN001
        super().hideEvent(event)
        # 仅用户点「×」隐藏时持久化；退出/托盘/程序 force_hide 不算「用户关闭待办」
        if self.isHidden() and not self._allow_programmatic_hide:
            self.visibility_changed.emit(False)

    def hide(self) -> None:
        if not self._closable and not self._allow_programmatic_hide:
            return
        super().hide()

    def force_hide(self) -> None:
        """程序化隐藏（托盘、退出），不受锁定限制。"""
        self._allow_programmatic_hide = True
        try:
            super().hide()
        finally:
            self._allow_programmatic_hide = False

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        self.visibility_changed.emit(True)
