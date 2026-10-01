# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from typing import Any

from .calendar_math import date_key
from .config_store import app_data_dir

REPEAT_NONE = "none"
REPEAT_DAILY = "daily"
REPEAT_WEEKLY = "weekly"
REPEAT_MONTHLY = "monthly"
REPEAT_YEARLY = "yearly"
REPEAT_CHOICES: tuple[str, ...] = (
    REPEAT_NONE,
    REPEAT_DAILY,
    REPEAT_WEEKLY,
    REPEAT_MONTHLY,
    REPEAT_YEARLY,
)
REPEAT_LABELS: dict[str, str] = {
    REPEAT_NONE: "不重复",
    REPEAT_DAILY: "每天",
    REPEAT_WEEKLY: "每周",
    REPEAT_MONTHLY: "每月",
    REPEAT_YEARLY: "每年",
}


def occurs_on(plan: dict[str, Any], d: date) -> bool:
    start = _parse_date(plan.get("start"))
    if start is None or d < start:
        return False
    key = date_key(d)
    exceptions = plan.get("exceptions") or []
    if isinstance(exceptions, list) and key in {str(x) for x in exceptions}:
        return False
    repeat = str(plan.get("repeat", REPEAT_NONE))
    if repeat == REPEAT_NONE:
        return d == start
    if repeat == REPEAT_DAILY:
        return True
    if repeat == REPEAT_WEEKLY:
        return d.weekday() == start.weekday()
    if repeat == REPEAT_MONTHLY:
        return d.day == start.day
    if repeat == REPEAT_YEARLY:
        return d.month == start.month and d.day == start.day
    return d == start


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def plan_needs_push(plan: dict[str, Any]) -> bool:
    """本地比上次成功同步更新，或尚未关联远端 → 需要推送。"""
    if not str(plan.get("caldav_uid") or "").strip():
        return True
    updated = parse_ts(plan.get("updated_at"))
    synced = parse_ts(plan.get("synced_at"))
    if updated is None:
        return True
    if synced is None:
        return True
    return updated > synced


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


class TodoStore:
    """Plans with optional recurrence; file kept as todos.json for compatibility."""

    def __init__(self) -> None:
        self.path = app_data_dir() / "todos.json"
        self._plans: list[dict[str, Any]] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self._plans = []
            self.save()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._plans = []
            return
        self._plans = self._migrate(raw)
        # Persist migrated shape so next load is clean.
        if not isinstance(raw, dict) or "plans" not in raw:
            self.save()

    def save(self) -> None:
        payload = {"plans": [self._normalize_plan(p) for p in self._plans]}
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _migrate(self, raw: Any) -> list[dict[str, Any]]:
        if isinstance(raw, dict) and isinstance(raw.get("plans"), list):
            return [self._normalize_plan(p) for p in raw["plans"] if isinstance(p, dict)]

        # Legacy: { "YYYY-MM-DD": [ {id, title, done}, ... ] }
        if isinstance(raw, dict):
            plans: list[dict[str, Any]] = []
            for key, items in raw.items():
                if key == "plans" or not isinstance(items, list):
                    continue
                start = _parse_date(key)
                if start is None:
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    completions: list[str] = []
                    if item.get("done"):
                        completions.append(date_key(start))
                    plans.append(
                        self._normalize_plan(
                            {
                                "id": str(item.get("id") or uuid.uuid4().hex),
                                "title": str(item.get("title", "")),
                                "start": date_key(start),
                                "repeat": REPEAT_NONE,
                                "completions": completions,
                            }
                        )
                    )
            return plans
        return []

    def _normalize_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        start = _parse_date(plan.get("start")) or date.today()
        repeat = str(plan.get("repeat", REPEAT_NONE))
        if repeat not in REPEAT_CHOICES:
            repeat = REPEAT_NONE
        completions: list[str] = []
        raw_comp = plan.get("completions", [])
        if isinstance(raw_comp, list):
            for c in raw_comp:
                dd = _parse_date(c)
                if dd is not None and dd >= start:
                    completions.append(date_key(dd))
        seen: set[str] = set()
        uniq: list[str] = []
        for c in completions:
            if c not in seen:
                seen.add(c)
                uniq.append(c)
        exceptions: list[str] = []
        raw_ex = plan.get("exceptions", [])
        if isinstance(raw_ex, list):
            for c in raw_ex:
                dd = _parse_date(c)
                if dd is not None and dd >= start:
                    exceptions.append(date_key(dd))
        seen_ex: set[str] = set()
        uniq_ex: list[str] = []
        for c in exceptions:
            if c not in seen_ex:
                seen_ex.add(c)
                uniq_ex.append(c)
        occ_details: dict[str, str] = {}
        raw_occ = plan.get("occurrence_details")
        if isinstance(raw_occ, dict):
            for k, v in raw_occ.items():
                dd = _parse_date(k)
                text = str(v or "").strip()
                if dd is not None and text:
                    occ_details[date_key(dd)] = text
        now = utc_now_iso()
        created = str(plan.get("created_at") or "").strip() or now
        updated = str(plan.get("updated_at") or "").strip() or created
        synced = str(plan.get("synced_at") or "").strip() or None
        return {
            "id": str(plan.get("id") or uuid.uuid4().hex),
            "title": str(plan.get("title", "")).strip(),
            "detail": str(plan.get("detail") or "").strip(),
            "occurrence_details": occ_details,
            "start": date_key(start),
            "repeat": repeat,
            "completions": uniq,
            "exceptions": uniq_ex,
            "created_at": created,
            "updated_at": updated,
            "synced_at": synced,
            "calendar_id": str(plan.get("calendar_id") or "") or None,
            "calendar_name": str(plan.get("calendar_name") or "").strip() or None,
            "caldav_uid": str(plan.get("caldav_uid") or "") or None,
            "caldav_etag": str(plan.get("caldav_etag") or "") or None,
            "split_from_id": str(plan.get("split_from_id") or "") or None,
        }

    def _touch(self, plan: dict[str, Any]) -> None:
        plan["updated_at"] = utc_now_iso()
        if not plan.get("created_at"):
            plan["created_at"] = plan["updated_at"]

    def all_plans(self) -> list[dict[str, Any]]:
        return list(self._plans)

    def get_plan(self, item_id: str) -> dict[str, Any] | None:
        plan = self._find(item_id)
        return dict(plan) if plan else None

    def set_caldav_meta(
        self,
        item_id: str,
        *,
        caldav_uid: str | None,
        caldav_etag: str | None = None,
        mark_synced: bool = True,
    ) -> None:
        plan = self._find(item_id)
        if plan is None:
            raise KeyError(item_id)
        plan["caldav_uid"] = caldav_uid or None
        if caldav_etag is not None:
            plan["caldav_etag"] = caldav_etag or None
        if mark_synced:
            # 推送成功：对齐 synced_at 与当前 updated_at
            plan["synced_at"] = str(plan.get("updated_at") or utc_now_iso())
        self.save()

    def set_calendar(
        self,
        item_id: str,
        *,
        calendar_id: str | None,
        calendar_name: str | None,
        touch: bool = True,
    ) -> None:
        plan = self._find(item_id)
        if plan is None:
            raise KeyError(item_id)
        plan["calendar_id"] = calendar_id or None
        plan["calendar_name"] = (calendar_name or "").strip() or None
        if touch:
            self._touch(plan)
        self.save()

    def apply_remote(
        self,
        item_id: str,
        *,
        title: str,
        start: date,
        repeat: str,
        completions: list[str],
        caldav_etag: str | None = None,
        remote_updated_at: str | None = None,
        calendar_id: str | None = None,
        calendar_name: str | None = None,
        exceptions: list[str] | None = None,
        detail: str | None = None,
        occurrence_details: dict[str, str] | None = None,
    ) -> None:
        plan = self._find(item_id)
        if plan is None:
            raise KeyError(item_id)
        plan["title"] = title.strip() or plan["title"]
        if detail is not None:
            plan["detail"] = str(detail).strip()
        if occurrence_details is not None:
            plan["occurrence_details"] = dict(occurrence_details)
        plan["start"] = date_key(start)
        if repeat in REPEAT_CHOICES:
            plan["repeat"] = repeat
        plan["completions"] = list(completions)
        if exceptions is not None:
            plan["exceptions"] = list(exceptions)
        if caldav_etag is not None:
            plan["caldav_etag"] = caldav_etag or None
        if calendar_id is not None:
            plan["calendar_id"] = calendar_id or None
        if calendar_name is not None:
            plan["calendar_name"] = (calendar_name or "").strip() or None
        stamp = remote_updated_at or utc_now_iso()
        plan["updated_at"] = stamp
        plan["synced_at"] = stamp
        if not plan.get("created_at"):
            plan["created_at"] = stamp
        self._plans = [self._normalize_plan(p) for p in self._plans]
        self.save()

    def add_from_remote(
        self,
        *,
        title: str,
        start: date,
        repeat: str = REPEAT_NONE,
        completions: list[str] | None = None,
        caldav_uid: str,
        caldav_etag: str | None = None,
        remote_updated_at: str | None = None,
        calendar_id: str | None = None,
        calendar_name: str | None = None,
        exceptions: list[str] | None = None,
        detail: str | None = None,
        occurrence_details: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        stamp = remote_updated_at or utc_now_iso()
        plan = self._normalize_plan(
            {
                "id": uuid.uuid4().hex,
                "title": title,
                "detail": detail or "",
                "occurrence_details": dict(occurrence_details or {}),
                "start": date_key(start),
                "repeat": repeat if repeat in REPEAT_CHOICES else REPEAT_NONE,
                "completions": list(completions or []),
                "exceptions": list(exceptions or []),
                "caldav_uid": caldav_uid,
                "caldav_etag": caldav_etag,
                "created_at": stamp,
                "updated_at": stamp,
                "synced_at": stamp,
                "calendar_id": calendar_id,
                "calendar_name": calendar_name,
            }
        )
        self._plans.append(plan)
        self.save()
        return plan

    def _find(self, item_id: str) -> dict[str, Any] | None:
        for plan in self._plans:
            if plan["id"] == item_id:
                return plan
        return None

    def for_date(self, d: date) -> list[dict[str, Any]]:
        key = date_key(d)
        out: list[dict[str, Any]] = []
        for plan in self._plans:
            if not occurs_on(plan, d):
                continue
            occ = plan.get("occurrence_details") or {}
            day_detail = str(occ.get(key) or "").strip()
            series_detail = str(plan.get("detail") or "").strip()
            repeat = str(plan.get("repeat") or REPEAT_NONE)
            if repeat == REPEAT_NONE:
                # 单日：detail 即当日备注，不展示系列备注
                shown = series_detail
                day_detail = series_detail
                series_detail = ""
            else:
                shown = day_detail or series_detail
            out.append(
                {
                    "id": plan["id"],
                    "title": plan["title"],
                    "detail": shown,
                    "day_detail": day_detail,
                    "series_detail": series_detail,
                    "done": key in plan.get("completions", []),
                    "start": plan["start"],
                    "repeat": plan["repeat"],
                    "caldav_uid": plan.get("caldav_uid"),
                    "calendar_id": plan.get("calendar_id"),
                    "calendar_name": plan.get("calendar_name"),
                    "created_at": plan.get("created_at"),
                    "updated_at": plan.get("updated_at"),
                }
            )
        return out

    def add(
        self,
        d: date,
        title: str,
        *,
        repeat: str = REPEAT_NONE,
        calendar_id: str | None = None,
        calendar_name: str | None = None,
        detail: str = "",
    ) -> dict[str, Any]:
        title = title.strip()
        if not title:
            raise ValueError("标题不能为空")
        if repeat not in REPEAT_CHOICES:
            repeat = REPEAT_NONE
        stamp = utc_now_iso()
        plan = self._normalize_plan(
            {
                "id": uuid.uuid4().hex,
                "title": title,
                "detail": detail,
                "start": date_key(d),
                "repeat": repeat,
                "completions": [],
                "created_at": stamp,
                "updated_at": stamp,
                "synced_at": None,
                "calendar_id": calendar_id,
                "calendar_name": calendar_name,
            }
        )
        self._plans.append(plan)
        self.save()
        return plan

    def update(
        self,
        item_id: str,
        *,
        title: str,
        start: date,
        repeat: str = REPEAT_NONE,
        calendar_id: str | None = None,
        calendar_name: str | None = None,
        detail: str | None = None,
    ) -> None:
        plan = self._find(item_id)
        if plan is None:
            raise KeyError(item_id)
        t = title.strip()
        if not t:
            raise ValueError("标题不能为空")
        if repeat not in REPEAT_CHOICES:
            repeat = REPEAT_NONE
        plan["title"] = t
        if detail is not None:
            plan["detail"] = str(detail).strip()
        plan["start"] = date_key(start)
        plan["repeat"] = repeat
        if calendar_id is not None or calendar_name is not None:
            plan["calendar_id"] = calendar_id or None
            plan["calendar_name"] = (calendar_name or "").strip() or None
        kept: list[str] = []
        for c in plan.get("completions", []):
            dd = _parse_date(c)
            if dd is not None and dd >= start:
                kept.append(date_key(dd))
        plan["completions"] = kept
        kept_ex: list[str] = []
        for c in plan.get("exceptions", []):
            dd = _parse_date(c)
            if dd is not None and dd >= start:
                kept_ex.append(date_key(dd))
        plan["exceptions"] = kept_ex
        # 清理早于新 start 的当日备注
        occ = dict(plan.get("occurrence_details") or {})
        plan["occurrence_details"] = {
            k: v for k, v in occ.items() if _parse_date(k) is not None and _parse_date(k) >= start
        }
        self._touch(plan)
        self._plans = [self._normalize_plan(p) for p in self._plans]
        self.save()

    def set_day_detail(self, item_id: str, d: date, text: str) -> None:
        """写入某一日的完成备注（周期计划按日独立）。"""
        plan = self._find(item_id)
        if plan is None:
            raise KeyError(item_id)
        key = date_key(d)
        occ = dict(plan.get("occurrence_details") or {})
        note = str(text or "").strip()
        if note:
            occ[key] = note
        else:
            occ.pop(key, None)
        plan["occurrence_details"] = occ
        self._touch(plan)
        self.save()

    def day_detail(self, item_id: str, d: date) -> str:
        plan = self._find(item_id)
        if plan is None:
            return ""
        occ = plan.get("occurrence_details") or {}
        return str(occ.get(date_key(d)) or "").strip()

    def complete_recurring_day_as_standalone(
        self,
        item_id: str,
        d: date,
        *,
        detail: str = "",
    ) -> tuple[str, str]:
        """将周期计划的某一天拆成独立已完成单日计划；系列跳过该日。

        返回 (series_id, new_plan_id)。
        """
        plan = self._find(item_id)
        if plan is None:
            raise KeyError(item_id)
        repeat = str(plan.get("repeat", REPEAT_NONE))
        if repeat == REPEAT_NONE:
            raise ValueError("非周期计划无需拆分")
        title = str(plan.get("title") or "").strip() or "计划"
        note = str(detail or "").strip()
        # 系列跳过当日
        self.delete_occurrence(item_id, d)
        stamp = utc_now_iso()
        new_plan = self._normalize_plan(
            {
                "id": uuid.uuid4().hex,
                "title": title,
                "detail": note,
                "start": date_key(d),
                "repeat": REPEAT_NONE,
                "completions": [date_key(d)],
                "created_at": stamp,
                "updated_at": stamp,
                "synced_at": None,
                "calendar_id": plan.get("calendar_id"),
                "calendar_name": plan.get("calendar_name"),
                "split_from_id": item_id,
            }
        )
        self._plans.append(new_plan)
        self.save()
        return item_id, str(new_plan["id"])

    def toggle(self, d: date, item_id: str) -> bool:
        plan = self._find(item_id)
        if plan is None:
            raise KeyError(item_id)
        if not occurs_on(plan, d):
            raise KeyError(item_id)
        key = date_key(d)
        comps: list[str] = list(plan.get("completions", []))
        if key in comps:
            comps = [c for c in comps if c != key]
            done = False
        else:
            comps.append(key)
            done = True
        plan["completions"] = comps
        self._touch(plan)
        self.save()
        return done

    def delete_occurrence(self, item_id: str, d: date) -> None:
        """仅跳过某一日的重复出现（写入 exceptions）。"""
        plan = self._find(item_id)
        if plan is None:
            raise KeyError(item_id)
        if str(plan.get("repeat", REPEAT_NONE)) == REPEAT_NONE:
            self.delete(item_id)
            return
        key = date_key(d)
        ex = [str(x) for x in plan.get("exceptions", []) if x]
        if key not in ex:
            ex.append(key)
        plan["exceptions"] = ex
        # 该日若已打卡也一并清掉，避免脏数据
        comps = [c for c in plan.get("completions", []) if c != key]
        plan["completions"] = comps
        self._touch(plan)
        self.save()

    def delete(self, item_id: str) -> None:
        before = len(self._plans)
        self._plans = [p for p in self._plans if p["id"] != item_id]
        if len(self._plans) == before:
            raise KeyError(item_id)
        self.save()

    def clear_all(self) -> int:
        """清空全部本地计划，返回删除条数。"""
        n = len(self._plans)
        if n:
            self._plans = []
            self.save()
        return n
