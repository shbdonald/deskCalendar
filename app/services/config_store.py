# -*- coding: utf-8 -*-
"""config.json = defaults + session.

  defaults — editable factory defaults (seeded from layout_metrics + theme)
  session  — last exit state; empty {} → load uses defaults
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRect, QSize

from app.services.layout_metrics import (
    MIN_WEEK,
    cell_size_from_week_window,
    default_week_window,
    month_window_size,
    week_window_from_month_window,
    week_window_size,
)
from app.services.theme import DEFAULT_THEME
from app.services.winamp_dock import DockState, apply_dock


def project_root() -> Path:
    """Project / portable install root (contains data/)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _legacy_appdata_dir() -> Path | None:
    roaming = os.environ.get("APPDATA")
    if roaming:
        return Path(roaming) / "DesktopCalendar"
    return None


def _migrate_from_appdata_if_needed(base: Path) -> None:
    legacy = _legacy_appdata_dir()
    if legacy is None or not legacy.is_dir() or (base / "config.json").exists():
        return
    try:
        for name in ("config.json", "todos.json"):
            src, dst = legacy / name, base / name
            if src.is_file() and not dst.exists():
                shutil.copy2(src, dst)
        leg_hol, loc_hol = legacy / "holidays", base / "holidays"
        if leg_hol.is_dir():
            loc_hol.mkdir(parents=True, exist_ok=True)
            for f in leg_hol.glob("*.json"):
                dst = loc_hol / f.name
                if not dst.exists():
                    shutil.copy2(f, dst)
    except OSError:
        pass


def app_data_dir() -> Path:
    base = project_root() / "data"
    base.mkdir(parents=True, exist_ok=True)
    (base / "holidays").mkdir(exist_ok=True)
    _migrate_from_appdata_if_needed(base)
    return base


_size = default_week_window()
_dw, _dh = _size.width(), _size.height()
_dcw, _dch = MIN_WEEK
_EDGE_MARGIN = 5


def top_right_origin(width: int, height: int, *, margin: int = _EDGE_MARGIN) -> tuple[int, int]:
    """默认态：贴屏幕可用区右上角，上/右各留 margin 像素。"""
    try:
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            g = screen.availableGeometry()
            x = int(g.x() + g.width() - width - margin)
            y = int(g.y() + margin)
            return x, y
    except Exception:  # noqa: BLE001
        pass
    return 80, margin


_ox, _oy = top_right_origin(_dw, _dh)

# 今日待办：默认贴日历下方右侧对齐，可自由缩放（不强制与日历同宽）
_TW = 300
_TH = 320
_todo_off = max(0, _dw - _TW)
_todo_geo = apply_dock(
    QRect(_ox, _oy, _dw, _dh),
    (_TW, _TH),
    DockState("bottom", _todo_off),
)

STATE_KEYS: tuple[str, ...] = (
    "x",
    "y",
    "countries",
    "expanded",
    "opacity",
    "size_locked",
    "display_w",
    "display_h",
    "window_w",
    "window_h",
    "cell_w",
    "cell_h",
    "theme",
    "icloud_sync_enabled",
    "icloud_calendar_name",
    "icloud_poll_seconds",
    "icloud_calendars_enabled",
    "icloud_calendars_enabled_names",
    "icloud_default_calendar_id",
    "todolist_visible",
    "todolist_x",
    "todolist_y",
    "todolist_w",
    "todolist_h",
    "todolist_docked",
    "todolist_dock_side",
    "todolist_dock_offset",
)

FACTORY_DEFAULTS: dict[str, Any] = {
    "x": _ox,
    "y": _oy,
    "countries": ["CN"],
    "expanded": False,
    "opacity": 0.92,
    "size_locked": True,
    "display_w": _dw,
    "display_h": _dh,
    "window_w": _dw,
    "window_h": _dh,
    "cell_w": _dcw,
    "cell_h": _dch,
    "theme": dict(DEFAULT_THEME),
    "icloud_sync_enabled": False,
    "icloud_calendar_name": "桌面计划",
    "icloud_poll_seconds": 45,
    "icloud_calendars_enabled": [],
    "icloud_calendars_enabled_names": [],
    "icloud_default_calendar_id": "",
    "todolist_visible": True,
    "todolist_x": int(_todo_geo.x()),
    "todolist_y": int(_todo_geo.y()),
    "todolist_w": _TW,
    "todolist_h": _TH,
    "todolist_docked": True,
    "todolist_dock_side": "bottom",
    "todolist_dock_offset": _todo_off,
}


def _normalize_state(raw: dict[str, Any] | None) -> dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    for key in STATE_KEYS:
        if key not in src or src[key] is None:
            continue
        val = src[key]
        if key == "theme":
            if isinstance(val, dict) and val:
                out[key] = dict(val)
        elif key == "countries":
            if isinstance(val, list):
                out[key] = [str(c).upper() for c in val if c]
        elif key == "icloud_calendars_enabled":
            if isinstance(val, list):
                out[key] = [str(c) for c in val if c]
        elif key == "icloud_calendars_enabled_names":
            if isinstance(val, list):
                out[key] = [str(c) for c in val if c]
        else:
            out[key] = val
    return out


def _session_is_empty(session: dict[str, Any] | None) -> bool:
    return not session or not isinstance(session, dict) or len(_normalize_state(session)) == 0


def _backfill_geometry(state: dict[str, Any]) -> dict[str, Any]:
    merged = dict(state)
    if "cell_w" not in merged or "cell_h" not in merged:
        try:
            cw, ch = cell_size_from_week_window(
                QSize(int(merged.get("window_w", _dw)), int(merged.get("window_h", _dh)))
            )
            merged["cell_w"], merged["cell_h"] = cw, ch
        except (TypeError, ValueError):
            merged["cell_w"], merged["cell_h"] = MIN_WEEK
    if "display_w" not in merged or "display_h" not in merged:
        try:
            cw, ch = int(merged.get("cell_w", _dcw)), int(merged.get("cell_h", _dch))
            disp = (
                month_window_size(cw, ch)
                if bool(merged.get("expanded", False))
                else week_window_size(cw, ch)
            )
            merged["display_w"], merged["display_h"] = disp.width(), disp.height()
        except (TypeError, ValueError):
            merged["display_w"], merged["display_h"] = _dw, _dh
    return merged


def _migrate_flat_legacy(raw: dict[str, Any]) -> dict[str, Any]:
    flat = {k: v for k, v in raw.items() if k not in (
        "week_w", "week_h", "month_w", "month_h", "always_on_top", "geometry_saved",
    )}
    if "window_w" not in flat and "week_w" in raw:
        flat["window_w"] = int(raw["week_w"])
    if "window_h" not in flat and "week_h" in raw:
        flat["window_h"] = int(raw["week_h"])
    if "window_w" not in flat and "month_w" in raw:
        flat["window_w"] = int(raw["month_w"])
    if "window_h" not in flat and "month_h" in raw:
        week = week_window_from_month_window(
            QSize(int(raw.get("month_w", _dw)), int(raw["month_h"]))
        )
        flat["window_w"], flat["window_h"] = week.width(), week.height()
    return _backfill_geometry(_normalize_state(flat))


class ConfigStore:
    def __init__(self) -> None:
        self.path = app_data_dir() / "config.json"
        self.defaults: dict[str, Any] = deepcopy(FACTORY_DEFAULTS)
        self.session: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.defaults = deepcopy(FACTORY_DEFAULTS)
            self.session = {}
            self.save()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("config root must be object")
            if "defaults" in raw or "session" in raw:
                self.defaults = _backfill_geometry(
                    {**deepcopy(FACTORY_DEFAULTS), **_normalize_state(raw.get("defaults"))}
                )
                self.session = _normalize_state(raw.get("session"))
                if self.session:
                    self.session = _backfill_geometry(self.session)
            else:
                self.defaults = deepcopy(FACTORY_DEFAULTS)
                self.session = _migrate_flat_legacy(raw)
            self.save()
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self.defaults = deepcopy(FACTORY_DEFAULTS)
            self.session = {}
            self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "defaults": self.defaults,
            "session": {} if _session_is_empty(self.session) else self.session,
        }
        payload = json.dumps(document, ensure_ascii=False, indent=2)
        fd, tmp_name = tempfile.mkstemp(prefix="config_", suffix=".json", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.path)
        except OSError:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            self.path.write_text(payload, encoding="utf-8")

    def get(self, key: str, default: Any = None) -> Any:
        if not _session_is_empty(self.session) and key in self.session:
            val = self.session[key]
            if val is not None and not (key == "theme" and (not isinstance(val, dict) or not val)):
                return val
        if key in self.defaults:
            return self.defaults[key]
        return default

    def set(self, key: str, value: Any, *, persist: bool = True) -> None:
        self.session[key] = value
        if persist:
            self.save()
