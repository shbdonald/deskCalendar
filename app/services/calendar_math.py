# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, timedelta

WEEKDAY_LABELS_CN = ["日", "一", "二", "三", "四", "五", "六"]


def monday_offset_sunday_first(d: date) -> int:
    """Python weekday: Mon=0..Sun=6 → Sunday-first index 0..6."""
    return (d.weekday() + 1) % 7


def week_start_sunday(d: date) -> date:
    return d - timedelta(days=monday_offset_sunday_first(d))


def get_week_dates(ref: date | None = None) -> list[date]:
    ref = ref or date.today()
    start = week_start_sunday(ref)
    return [start + timedelta(days=i) for i in range(7)]


def get_month_grid(year: int, month: int) -> list[list[date]]:
    """Return exactly 6 weeks so layout metrics stay cell-size stable."""
    first = date(year, month, 1)
    start = week_start_sunday(first)

    rows: list[list[date]] = []
    cur = start
    for _ in range(6):
        week: list[date] = []
        for _ in range(7):
            week.append(cur)
            cur += timedelta(days=1)
        rows.append(week)
    return rows


def date_key(d: date) -> str:
    return d.isoformat()
