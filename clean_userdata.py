# -*- coding: utf-8 -*-
"""Prepare a clean tree for version save / share: strip personal userdata.

Usage:
  python clean_userdata.py           # move leftovers from data/, clear userdata/
  python clean_userdata.py --keep    # only migrate data/ → userdata/, do not delete
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
USERDATA = ROOT / "userdata"

USER_FILES = (
    "session.json",
    "todos.json",
    "todolist.json",
    "icloud_caldav.json",
    "icloud_calendars.json",
)


def _strip_config_session() -> None:
    cfg = DATA / "config.json"
    if not cfg.is_file():
        return
    try:
        raw = json.loads(cfg.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(raw, dict):
        return
    defaults = raw.get("defaults") if isinstance(raw.get("defaults"), dict) else {}
    if "session" in raw or "defaults" in raw:
        cfg.write_text(
            json.dumps({"defaults": defaults}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print("config.json → defaults only")


def _migrate_from_data() -> None:
    USERDATA.mkdir(parents=True, exist_ok=True)
    for name in USER_FILES:
        if name == "session.json":
            continue
        src, dst = DATA / name, USERDATA / name
        if src.is_file():
            if not dst.exists():
                shutil.move(str(src), str(dst))
                print(f"moved data/{name} → userdata/{name}")
            else:
                src.unlink()
                print(f"removed duplicate data/{name}")


def _clear_userdata() -> None:
    if not USERDATA.exists():
        USERDATA.mkdir(parents=True, exist_ok=True)
        print("created empty userdata/")
        return
    removed = 0
    for p in USERDATA.iterdir():
        if p.name == ".gitkeep":
            continue
        if p.is_file():
            p.unlink()
            removed += 1
        elif p.is_dir():
            shutil.rmtree(p)
            removed += 1
    print(f"cleared userdata/ ({removed} entries)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean personal data before version/release")
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Migrate out of data/ only; do not delete userdata/",
    )
    args = parser.parse_args()
    _migrate_from_data()
    _strip_config_session()
    if not args.keep:
        _clear_userdata()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
