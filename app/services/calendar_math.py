# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, timedelta

# Python weekday(): Mon=0 … Sun=6
_WEEKDAY_CN = ("一", "二", "三", "四", "五", "六", "日")
WEEKDAY_NAMES_CN = (
    "星期一",
    "星期二",
    "星期三",
    "星期四",
    "星期五",
    "星期六",
    "星期日",
)

# 默认周日为一周第一天（与历史行为一致）
DEFAULT_WEEK_STARTS_ON = 6


def normalize_week_starts_on(value: object, default: int = DEFAULT_WEEK_STARTS_ON) -> int:
    try:
        return int(value) % 7  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default % 7


def weekday_labels_cn(week_starts_on: int = DEFAULT_WEEK_STARTS_ON) -> list[str]:
    start = normalize_week_starts_on(week_starts_on)
    return [_WEEKDAY_CN[(start + i) % 7] for i in range(7)]


# 兼容旧引用：周日开头
WEEKDAY_LABELS_CN = weekday_labels_cn(DEFAULT_WEEK_STARTS_ON)


def week_start(d: date, week_starts_on: int = DEFAULT_WEEK_STARTS_ON) -> date:
    start = normalize_week_starts_on(week_starts_on)
    return d - timedelta(days=(d.weekday() - start) % 7)


def monday_offset_sunday_first(d: date) -> int:
    """Python weekday: Mon=0..Sun=6 → Sunday-first index 0..6."""
    return (d.weekday() + 1) % 7


def week_start_sunday(d: date) -> date:
    return week_start(d, DEFAULT_WEEK_STARTS_ON)


def get_week_dates(
    ref: date | None = None,
    *,
    week_starts_on: int = DEFAULT_WEEK_STARTS_ON,
) -> list[date]:
    ref = ref or date.today()
    start = week_start(ref, week_starts_on)
    return [start + timedelta(days=i) for i in range(7)]


def get_month_grid(
    year: int,
    month: int,
    *,
    week_starts_on: int = DEFAULT_WEEK_STARTS_ON,
) -> list[list[date]]:
    """Return exactly 6 weeks so layout metrics stay cell-size stable."""
    first = date(year, month, 1)
    start = week_start(first, week_starts_on)

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
