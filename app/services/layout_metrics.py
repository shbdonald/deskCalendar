# -*- coding: utf-8 -*-
"""Window/cell size math for resize and expand — not for cold-start load.

Cold start restores x/y/display_w/h from data/config.json.
This module seeds FACTORY_DEFAULTS and converts cell ↔ window when resizing
or toggling week/month views.
"""
from __future__ import annotations

from PySide6.QtCore import QSize

# Keep in sync with DayCell.setMinimumSize usage.
MIN_WEEK = (75, 105)
MIN_MONTH = (75, 105)

# Must match MainWindow / WeekView / MonthView layout constants.
MARGIN = 8
CHROME_H = 28
MAIN_SPACING = 6
GRID_SPACING = 4
WEEKDAY_HEADER_H = 20


def week_window_size(cell_w: int, cell_h: int) -> QSize:
    w = 2 * MARGIN + 7 * cell_w + 6 * GRID_SPACING
    h = 2 * MARGIN + CHROME_H + MAIN_SPACING + cell_h
    return QSize(w, h)


def month_window_size(cell_w: int, cell_h: int) -> QSize:
    w = 2 * MARGIN + 7 * cell_w + 6 * GRID_SPACING
    h = (
        2 * MARGIN
        + CHROME_H
        + MAIN_SPACING
        + WEEKDAY_HEADER_H
        + 6 * cell_h
        + 6 * GRID_SPACING
    )
    return QSize(w, h)


def cell_size_from_week_window(window: QSize) -> tuple[int, int]:
    cell_w = (window.width() - 2 * MARGIN - 6 * GRID_SPACING) // 7
    cell_h = window.height() - 2 * MARGIN - CHROME_H - MAIN_SPACING
    mw, mh = MIN_WEEK
    return max(mw, cell_w), max(mh, cell_h)


def cell_size_from_month_window(window: QSize) -> tuple[int, int]:
    cell_w = (window.width() - 2 * MARGIN - 6 * GRID_SPACING) // 7
    inner_h = (
        window.height()
        - 2 * MARGIN
        - CHROME_H
        - MAIN_SPACING
        - WEEKDAY_HEADER_H
        - 6 * GRID_SPACING
    )
    cell_h = inner_h // 6
    mw, mh = MIN_MONTH
    return max(mw, cell_w), max(mh, cell_h)


def default_week_window() -> QSize:
    """First-launch size: MIN_WEEK cells + chrome."""
    cw, ch = MIN_WEEK
    return week_window_size(cw, ch)


def default_month_window() -> QSize:
    cw, ch = MIN_MONTH
    week = default_week_window()
    month = month_window_size(cw, ch)
    return QSize(max(week.width(), month.width()), month.height())


def week_window_from_month_window(month: QSize) -> QSize:
    cw, ch = cell_size_from_month_window(month)
    return week_window_size(cw, ch)


def month_window_from_week_window(week: QSize) -> QSize:
    cw, ch = cell_size_from_week_window(week)
    return month_window_size(cw, ch)


def max_cell_width(screen_width: int) -> int:
    """Hard cap: cell width ≤ 1/10 of the current display width."""
    return max(MIN_WEEK[0], int(screen_width) // 10)


def window_width_for_cell_width(cell_w: int) -> int:
    """Outer window width for 7 day-columns at the given cell width."""
    return 2 * MARGIN + 7 * int(cell_w) + 6 * GRID_SPACING
