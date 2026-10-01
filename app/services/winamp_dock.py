# -*- coding: utf-8 -*-
"""Winamp 风格窗口磁吸：日历为主锚点，待办为副窗贴合/滑动/拉开。"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect

# 贴合间隙（像素）与吸附阈值
GAP = 5
SNAP = 18
BREAK = 36  # 超过此距离视为拉开


@dataclass
class DockState:
    side: str = "bottom"  # bottom|top|left|right
    offset: int = 0  # 沿自由轴相对日历的偏移


def calendar_rect(origin: QPoint, size_w: int, size_h: int) -> QRect:
    return QRect(int(origin.x()), int(origin.y()), int(size_w), int(size_h))


def apply_dock(cal: QRect, todo_size: tuple[int, int], state: DockState) -> QRect:
    w, h = max(240, int(todo_size[0])), max(280, int(todo_size[1]))
    if state.side == "bottom":
        return QRect(cal.x() + state.offset, cal.y() + cal.height() + GAP, w, h)
    if state.side == "top":
        return QRect(cal.x() + state.offset, cal.y() - h - GAP, w, h)
    if state.side == "right":
        return QRect(cal.x() + cal.width() + GAP, cal.y() + state.offset, w, h)
    # left
    return QRect(cal.x() - w - GAP, cal.y() + state.offset, w, h)


def _secondary_x(cal: QRect, todo: QRect) -> int:
    """水平次轴：靠近则吸左/右对齐，否则保持。"""
    x = todo.x()
    if abs(todo.x() - cal.x()) <= SNAP:
        x = cal.x()
    elif abs((todo.x() + todo.width()) - (cal.x() + cal.width())) <= SNAP:
        x = cal.x() + cal.width() - todo.width()
    return x


def _secondary_y(cal: QRect, todo: QRect) -> int:
    y = todo.y()
    if abs(todo.y() - cal.y()) <= SNAP:
        y = cal.y()
    elif abs((todo.y() + todo.height()) - (cal.y() + cal.height())) <= SNAP:
        y = cal.y() + cal.height() - todo.height()
    return y


def try_snap(cal: QRect, todo: QRect) -> DockState | None:
    """若待办靠近日历任一边，返回吸附状态；否则 None。"""
    candidates: list[tuple[int, DockState, QRect]] = []

    # 下缘
    target_y = cal.y() + cal.height() + GAP
    dist = abs(todo.y() - target_y)
    if dist <= SNAP:
        x = _secondary_x(cal, todo)
        geo = QRect(x, target_y, todo.width(), todo.height())
        candidates.append((dist, DockState("bottom", x - cal.x()), geo))

    # 上缘
    target_y = cal.y() - todo.height() - GAP
    dist = abs(todo.y() - target_y)
    if dist <= SNAP:
        x = _secondary_x(cal, todo)
        geo = QRect(x, target_y, todo.width(), todo.height())
        candidates.append((dist, DockState("top", x - cal.x()), geo))

    # 右缘
    target_x = cal.x() + cal.width() + GAP
    dist = abs(todo.x() - target_x)
    if dist <= SNAP:
        y = _secondary_y(cal, todo)
        geo = QRect(target_x, y, todo.width(), todo.height())
        candidates.append((dist, DockState("right", y - cal.y()), geo))

    # 左缘
    target_x = cal.x() - todo.width() - GAP
    dist = abs(todo.x() - target_x)
    if dist <= SNAP:
        y = _secondary_y(cal, todo)
        geo = QRect(target_x, y, todo.width(), todo.height())
        candidates.append((dist, DockState("left", y - cal.y()), geo))

    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    _dist, state, _geo = candidates[0]
    return state


def snap_geometry(cal: QRect, todo: QRect, state: DockState) -> QRect:
    return apply_dock(cal, (todo.width(), todo.height()), state)


def capture_offset(cal: QRect, todo: QRect, side: str) -> int:
    if side in ("bottom", "top"):
        return todo.x() - cal.x()
    return todo.y() - cal.y()


def should_break(cal: QRect, todo: QRect, state: DockState) -> bool:
    """当前几何相对理想贴合位偏离过大 → 视为拉开。"""
    ideal = apply_dock(cal, (todo.width(), todo.height()), state)
    return abs(todo.x() - ideal.x()) > BREAK or abs(todo.y() - ideal.y()) > BREAK


def docked_drag_step(
    cal: QRect,
    todo_size: tuple[int, int],
    state: DockState,
    proposed_tl: QPoint,
) -> tuple[DockState | None, QRect]:
    """贴合拖动一步：按「若自由放置」的左上角 proposed_tl 计算。

    返回 (新 DockState 或 None=已拉开, 应 setGeometry 的矩形)。
    拉开时矩形为自由位置；未拉开时钉住贴合轴、自由轴跟随 proposed。
    """
    w, h = max(240, int(todo_size[0])), max(280, int(todo_size[1]))
    px, py = int(proposed_tl.x()), int(proposed_tl.y())

    if state.side == "bottom":
        target_y = cal.y() + cal.height() + GAP
        if abs(py - target_y) > BREAK:
            return None, QRect(px, py, w, h)
        st = DockState("bottom", px - cal.x())
        return st, apply_dock(cal, (w, h), st)
    if state.side == "top":
        target_y = cal.y() - h - GAP
        if abs(py - target_y) > BREAK:
            return None, QRect(px, py, w, h)
        st = DockState("top", px - cal.x())
        return st, apply_dock(cal, (w, h), st)
    if state.side == "right":
        target_x = cal.x() + cal.width() + GAP
        if abs(px - target_x) > BREAK:
            return None, QRect(px, py, w, h)
        st = DockState("right", py - cal.y())
        return st, apply_dock(cal, (w, h), st)
    # left
    target_x = cal.x() - w - GAP
    if abs(px - target_x) > BREAK:
        return None, QRect(px, py, w, h)
    st = DockState("left", py - cal.y())
    return st, apply_dock(cal, (w, h), st)


def slide_or_break(cal: QRect, todo: QRect, state: DockState) -> DockState | None:
    """兼容旧调用：由当前几何判断是否仍贴合。"""
    st, _geo = docked_drag_step(
        cal, (todo.width(), todo.height()), state, QPoint(todo.x(), todo.y())
    )
    return st
