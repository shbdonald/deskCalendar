# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any

from .calendar_math import date_key
from .config_store import app_data_dir


class TodoStore:
    def __init__(self) -> None:
        self.path = app_data_dir() / "todos.json"
        self._data: dict[str, list[dict[str, Any]]] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self._data = {}
            self.save()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._data = raw if isinstance(raw, dict) else {}
        except (json.JSONDecodeError, OSError):
            self._data = {}

    def save(self) -> None:
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def for_date(self, d: date) -> list[dict[str, Any]]:
        return list(self._data.get(date_key(d), []))

    def add(self, d: date, title: str) -> dict[str, Any]:
        title = title.strip()
        if not title:
            raise ValueError("标题不能为空")
        item = {"id": uuid.uuid4().hex, "title": title, "done": False}
        key = date_key(d)
        self._data.setdefault(key, []).append(item)
        self.save()
        return item

    def update(self, d: date, item_id: str, *, title: str) -> None:
        key = date_key(d)
        items = self._data.get(key, [])
        for item in items:
            if item["id"] == item_id:
                t = title.strip()
                if not t:
                    raise ValueError("标题不能为空")
                item["title"] = t
                self.save()
                return
        raise KeyError(item_id)

    def toggle(self, d: date, item_id: str) -> bool:
        key = date_key(d)
        for item in self._data.get(key, []):
            if item["id"] == item_id:
                item["done"] = not item["done"]
                self.save()
                return bool(item["done"])
        raise KeyError(item_id)

    def delete(self, d: date, item_id: str) -> None:
        key = date_key(d)
        items = self._data.get(key, [])
        self._data[key] = [i for i in items if i["id"] != item_id]
        if not self._data[key]:
            del self._data[key]
        self.save()
