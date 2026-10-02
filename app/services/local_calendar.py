# -*- coding: utf-8 -*-
"""Built-in / custom local calendars: offline plans that never sync to iCloud."""
from __future__ import annotations

import hashlib
from typing import Any

LOCAL_CALENDAR_ID = "local"
LOCAL_CALENDAR_NAME = "本地日历"


def is_local_calendar_id(calendar_id: str | None) -> bool:
    cid = str(calendar_id or "").strip()
    if not cid:
        return False
    if cid == LOCAL_CALENDAR_ID:
        return True
    return cid.startswith("local:")


def is_local_plan(plan: dict[str, Any] | None) -> bool:
    if not isinstance(plan, dict):
        return False
    if is_local_calendar_id(plan.get("calendar_id")):
        return True
    name = str(plan.get("calendar_name") or "").strip()
    return name == LOCAL_CALENDAR_NAME and not str(plan.get("calendar_id") or "").startswith(
        "cal_"
    )


def make_local_calendar_id(name: str) -> str:
    """Stable id for a user-created local calendar (by name)."""
    raw = (name or "").strip()
    if not raw:
        raw = "unnamed"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"local:{digest}"


def local_calendar_info() -> dict[str, str]:
    return {"id": LOCAL_CALENDAR_ID, "name": LOCAL_CALENDAR_NAME, "writable": "1"}


def with_local_calendar(
    calendars: list[dict[str, str]] | None,
    *,
    extra_local: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Prepend built-in 本地日历 + custom locals; then iCloud calendars."""
    out: list[dict[str, str]] = [local_calendar_info()]
    seen = {LOCAL_CALENDAR_ID}
    for item in extra_local or []:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not cid or not name or cid in seen:
            continue
        if not is_local_calendar_id(cid):
            continue
        if name == LOCAL_CALENDAR_NAME:
            continue
        seen.add(cid)
        out.append({"id": cid, "name": name, "writable": "1"})
    for item in calendars or []:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not cid or not name:
            continue
        if cid in seen or is_local_calendar_id(cid):
            continue
        if name == LOCAL_CALENDAR_NAME:
            continue
        seen.add(cid)
        out.append({"id": cid, "name": name, "writable": str(item.get("writable") or "1")})
    return out
