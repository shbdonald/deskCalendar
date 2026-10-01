# -*- coding: utf-8 -*-
"""独立待办清单存储（与日历计划 TodoStore 分离）。"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from typing import Any

from .calendar_math import date_key
from .config_store import app_data_dir

STORE_FILE = "todolist.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class TodoListStore:
    def __init__(self) -> None:
        self._path = app_data_dir() / STORE_FILE
        self._items: list[dict[str, Any]] = []
        self.load()

    def load(self) -> None:
        if not self._path.exists():
            self._items = []
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._items = []
            return
        items = raw.get("items") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            self._items = []
            return
        self._items = [self._normalize(it) for it in items if isinstance(it, dict)]

    def save(self) -> None:
        payload = {"items": self._items}
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _normalize(self, item: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        created = str(item.get("created_at") or "").strip() or now
        updated = str(item.get("updated_at") or "").strip() or created
        done = bool(item.get("done"))
        completed_at = str(item.get("completed_at") or "").strip() or None
        if done and not completed_at:
            completed_at = date_key(date.today())
        if not done:
            completed_at = None
        return {
            "id": str(item.get("id") or uuid.uuid4().hex),
            "title": str(item.get("title", "")).strip(),
            "done": done,
            "created_at": created,
            "updated_at": updated,
            "completed_at": completed_at,
            "plan_id": str(item.get("plan_id") or "") or None,
            "caldav_uid": str(item.get("caldav_uid") or "") or None,
        }

    def _find(self, item_id: str) -> dict[str, Any] | None:
        for it in self._items:
            if it["id"] == item_id:
                return it
        return None

    def all(self) -> list[dict[str, Any]]:
        return [dict(it) for it in self._items]

    def pending(self) -> list[dict[str, Any]]:
        return [dict(it) for it in self._items if not it.get("done")]

    def completed(self) -> list[dict[str, Any]]:
        return [dict(it) for it in self._items if it.get("done")]

    def get(self, item_id: str) -> dict[str, Any] | None:
        it = self._find(item_id)
        return dict(it) if it else None

    def add(self, title: str) -> dict[str, Any]:
        title = title.strip()
        if not title:
            raise ValueError("标题不能为空")
        stamp = utc_now_iso()
        item = self._normalize(
            {
                "id": uuid.uuid4().hex,
                "title": title,
                "done": False,
                "created_at": stamp,
                "updated_at": stamp,
            }
        )
        self._items.insert(0, item)
        self.save()
        return dict(item)

    def update_title(self, item_id: str, title: str) -> None:
        it = self._find(item_id)
        if it is None:
            raise KeyError(item_id)
        t = title.strip()
        if not t:
            raise ValueError("标题不能为空")
        it["title"] = t
        it["updated_at"] = utc_now_iso()
        self.save()

    def set_done(
        self,
        item_id: str,
        done: bool,
        *,
        completed_at: date | None = None,
    ) -> dict[str, Any]:
        it = self._find(item_id)
        if it is None:
            raise KeyError(item_id)
        it["done"] = bool(done)
        if done:
            day = completed_at or date.today()
            it["completed_at"] = date_key(day)
        else:
            it["completed_at"] = None
        it["updated_at"] = utc_now_iso()
        self.save()
        return dict(it)

    def set_plan_link(
        self,
        item_id: str,
        *,
        plan_id: str | None,
        caldav_uid: str | None = None,
    ) -> None:
        it = self._find(item_id)
        if it is None:
            raise KeyError(item_id)
        it["plan_id"] = plan_id or None
        if caldav_uid is not None:
            it["caldav_uid"] = caldav_uid or None
        self.save()

    def clear_plan_link(self, item_id: str) -> None:
        it = self._find(item_id)
        if it is None:
            raise KeyError(item_id)
        it["plan_id"] = None
        it["caldav_uid"] = None
        self.save()

    def delete(self, item_id: str) -> dict[str, Any] | None:
        it = self._find(item_id)
        if it is None:
            raise KeyError(item_id)
        removed = dict(it)
        self._items = [x for x in self._items if x["id"] != item_id]
        self.save()
        return removed
