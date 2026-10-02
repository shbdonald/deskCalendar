# -*- coding: utf-8 -*-
"""App config vs user data.

  data/config.json      — factory defaults only (safe to ship / commit)
  userdata/session.json — last exit state (personal; never ship)
  userdata/*            — todos, credentials, calendar cache
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

# Files that belong under userdata/ (never packaged with releases).
USER_DATA_FILES: tuple[str, ...] = (
    "session.json",
    "todos.json",
    "todolist.json",
    "icloud_caldav.json",
    "icloud_calendars.json",
)


def project_root() -> Path:
    """Project / portable install root (contains data/ and userdata/)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _legacy_appdata_dir() -> Path | None:
    roaming = os.environ.get("APPDATA")
    if roaming:
        return Path(roaming) / "DesktopCalendar"
    return None


def _atomic_write_json(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(prefix=path.stem + "_", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        path.write_text(payload, encoding="utf-8")


def _migrate_from_appdata_if_needed(data_base: Path, user_base: Path) -> None:
    legacy = _legacy_appdata_dir()
    if legacy is None or not legacy.is_dir():
        return
    try:
        src_cfg, dst_cfg = legacy / "config.json", data_base / "config.json"
        if src_cfg.is_file() and not dst_cfg.exists() and not (user_base / "session.json").exists():
            shutil.copy2(src_cfg, dst_cfg)
        for name in ("todos.json",):
            src, dst = legacy / name, user_base / name
            if src.is_file() and not dst.exists():
                shutil.copy2(src, dst)
        leg_hol, loc_hol = legacy / "holidays", data_base / "holidays"
        if leg_hol.is_dir():
            loc_hol.mkdir(parents=True, exist_ok=True)
            for f in leg_hol.glob("*.json"):
                dst = loc_hol / f.name
                if not dst.exists():
                    shutil.copy2(f, dst)
    except OSError:
        pass


def _migrate_userdata_from_data(data_base: Path, user_base: Path) -> None:
    """Move legacy personal files out of data/ into userdata/."""
    if not data_base.is_dir():
        return
    for name in USER_DATA_FILES:
        if name == "session.json":
            continue
        src, dst = data_base / name, user_base / name
        if not src.is_file():
            continue
        if not dst.exists():
            try:
                shutil.move(str(src), str(dst))
            except OSError:
                try:
                    shutil.copy2(src, dst)
                    src.unlink(missing_ok=True)
                except OSError:
                    pass
        else:
            try:
                src.unlink(missing_ok=True)
            except OSError:
                pass

    cfg_path = data_base / "config.json"
    session_path = user_base / "session.json"
    if not cfg_path.is_file():
        return
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(raw, dict):
        return

    if not session_path.exists():
        session_raw = None
        if "defaults" in raw or "session" in raw:
            session_raw = raw.get("session")
        else:
            session_raw = raw
        if isinstance(session_raw, dict) and session_raw:
            try:
                _atomic_write_json(session_path, _normalize_state(session_raw))
            except OSError:
                pass

    if "defaults" in raw or "session" in raw:
        defaults = raw.get("defaults") if isinstance(raw.get("defaults"), dict) else {}
        try:
            _atomic_write_json(cfg_path, {"defaults": defaults})
        except OSError:
            pass
    elif session_path.exists():
        try:
            _atomic_write_json(cfg_path, {"defaults": deepcopy(FACTORY_DEFAULTS)})
        except OSError:
            pass


def app_data_dir() -> Path:
    """Shippable app data (defaults, holiday cache)."""
    base = project_root() / "data"
    base.mkdir(parents=True, exist_ok=True)
    (base / "holidays").mkdir(exist_ok=True)
    user_base = project_root() / "userdata"
    user_base.mkdir(parents=True, exist_ok=True)
    _migrate_from_appdata_if_needed(base, user_base)
    _migrate_userdata_from_data(base, user_base)
    return base


def user_data_dir() -> Path:
    """Personal runtime data — never commit or pack into releases."""
    app_data_dir()
    base = project_root() / "userdata"
    base.mkdir(parents=True, exist_ok=True)
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
    "start_with_windows",
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
    "countries": [],
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
    "start_with_windows": False,
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
        self.session_path = user_data_dir() / "session.json"
        self.defaults: dict[str, Any] = deepcopy(FACTORY_DEFAULTS)
        self.session: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        self.defaults = deepcopy(FACTORY_DEFAULTS)
        self.session = {}
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    if "defaults" in raw or "session" in raw:
                        self.defaults = _backfill_geometry(
                            {**deepcopy(FACTORY_DEFAULTS), **_normalize_state(raw.get("defaults"))}
                        )
                        # Legacy combined file: session may still be inline until migration rewrote it
                        inline = _normalize_state(raw.get("session"))
                        if inline and not self.session_path.exists():
                            self.session = _backfill_geometry(inline)
                    else:
                        # Flat legacy treated as session; defaults stay factory
                        if not self.session_path.exists():
                            self.session = _migrate_flat_legacy(raw)
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                pass

        if self.session_path.exists():
            try:
                raw_s = json.loads(self.session_path.read_text(encoding="utf-8"))
                if isinstance(raw_s, dict):
                    self.session = _backfill_geometry(_normalize_state(raw_s))
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                pass

        self.save()

    def save(self) -> None:
        _atomic_write_json(self.path, {"defaults": self.defaults})
        session_doc = {} if _session_is_empty(self.session) else self.session
        _atomic_write_json(self.session_path, session_doc)

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
