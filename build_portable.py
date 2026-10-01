# -*- coding: utf-8 -*-
"""Build a portable (green) DesktopCalendar zip under release/.

Never packs userdata/ (session, todos, credentials). Ships defaults-only config.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST_NAME = "DesktopCalendar"
RELEASE = ROOT / "release"
PORTABLE_DIR = RELEASE / DIST_NAME
ZIP_PATH = RELEASE / f"{DIST_NAME}-portable.zip"

# Must stay in sync with app.services.config_store.USER_DATA_FILES
USER_DATA_NAMES = {
    "session.json",
    "todos.json",
    "todolist.json",
    "icloud_caldav.json",
    "icloud_calendars.json",
}


def _write_clean_config(dest: Path) -> None:
    """Write factory defaults only; strip any session / personal fields."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    defaults: dict = {}
    src = ROOT / "data" / "config.json"
    if src.is_file():
        try:
            raw = json.loads(src.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                if isinstance(raw.get("defaults"), dict):
                    defaults = dict(raw["defaults"])
                elif "session" not in raw:
                    # Unexpected flat file — do not ship as defaults
                    defaults = {}
        except (json.JSONDecodeError, OSError):
            defaults = {}
    if not defaults:
        # Import factory after PyInstaller path is fine; for build use code defaults.
        sys.path.insert(0, str(ROOT))
        from app.services.config_store import FACTORY_DEFAULTS  # noqa: WPS433

        defaults = deepcopy(FACTORY_DEFAULTS)
    dest.write_text(
        json.dumps({"defaults": defaults}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _prepare_portable_data() -> None:
    """Ensure portable tree has clean data/ and empty userdata/."""
    data_dst = PORTABLE_DIR / "data"
    if data_dst.exists():
        shutil.rmtree(data_dst)
    data_dst.mkdir(parents=True, exist_ok=True)
    (data_dst / "holidays").mkdir(exist_ok=True)

    # Optional: copy public holiday cache (not personal).
    src_hol = ROOT / "data" / "holidays"
    if src_hol.is_dir():
        for f in src_hol.glob("*.json"):
            shutil.copy2(f, data_dst / "holidays" / f.name)
    src_countries = ROOT / "data" / "countries.json"
    if src_countries.is_file():
        shutil.copy2(src_countries, data_dst / "countries.json")

    _write_clean_config(data_dst / "config.json")

    # Strip any accidental user files under data/
    for name in USER_DATA_NAMES:
        p = data_dst / name
        if p.exists():
            p.unlink()

    userdata = PORTABLE_DIR / "userdata"
    if userdata.exists():
        shutil.rmtree(userdata)
    userdata.mkdir(parents=True, exist_ok=True)
    (userdata / ".gitkeep").write_text("", encoding="utf-8")


def _zip_should_include(rel: Path) -> bool:
    parts = rel.parts
    if not parts:
        return False
    # Never pack personal userdata contents (keep empty folder marker only).
    if parts[0] == DIST_NAME and len(parts) >= 2 and parts[1] == "userdata":
        return len(parts) == 3 and parts[2] == ".gitkeep"
    if parts[0] == DIST_NAME and len(parts) >= 3 and parts[1] == "data":
        if parts[2] in USER_DATA_NAMES:
            return False
    return True


def main() -> int:
    RELEASE.mkdir(parents=True, exist_ok=True)

    if PORTABLE_DIR.exists():
        shutil.rmtree(PORTABLE_DIR)
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--onedir",
        f"--name={DIST_NAME}",
        "--distpath",
        str(RELEASE),
        "--workpath",
        str(RELEASE / "build"),
        "--specpath",
        str(RELEASE),
        str(ROOT / "main.py"),
    ]
    print("Running:", " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(ROOT))

    _prepare_portable_data()

    readme = PORTABLE_DIR / "使用说明.txt"
    readme.write_text(
        "\n".join(
            [
                "桌面日历 · 绿色便携版",
                "",
                "1. 双击 DesktopCalendar.exe 即可运行（无需安装 Python）。",
                "2. 出厂配置在 data\\config.json（仅 defaults）。",
                "3. 个人数据在 userdata\\（窗口状态、计划、iCloud 凭证），勿随版本分享。",
                "4. 托盘图标右键可「退出」结束程序。",
                "5. 可将整个文件夹复制到任意位置使用。",
                "",
            ]
        ),
        encoding="utf-8",
    )

    print("Zipping", ZIP_PATH)
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in PORTABLE_DIR.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(RELEASE)
            if not _zip_should_include(rel):
                print("  skip user data:", rel.as_posix())
                continue
            zf.write(path, rel.as_posix())

    size_mb = ZIP_PATH.stat().st_size / (1024 * 1024)
    print(f"Done: {ZIP_PATH} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
