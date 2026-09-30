# -*- coding: utf-8 -*-
"""Build a portable (green) DesktopCalendar zip under release/."""
from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST_NAME = "DesktopCalendar"
RELEASE = ROOT / "release"
PORTABLE_DIR = RELEASE / DIST_NAME
ZIP_PATH = RELEASE / f"{DIST_NAME}-portable.zip"


def main() -> int:
    RELEASE.mkdir(parents=True, exist_ok=True)

    # Clean previous portable output only (keep other release artifacts if any).
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

    # Write a simple launcher note next to the exe.
    readme = PORTABLE_DIR / "使用说明.txt"
    readme.write_text(
        "\n".join(
            [
                "桌面日历 · 绿色便携版",
                "",
                "1. 双击 DesktopCalendar.exe 即可运行（无需安装 Python）。",
                "2. 配置与待办保存在本目录下的 data\\ 文件夹。",
                "3. 托盘图标右键可「退出」结束程序。",
                "4. 可将整个文件夹复制到任意位置使用。",
                "",
            ]
        ),
        encoding="utf-8",
    )

    # Zip folder contents under DesktopCalendar/
    print("Zipping", ZIP_PATH)
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in PORTABLE_DIR.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(RELEASE).as_posix())

    size_mb = ZIP_PATH.stat().st_size / (1024 * 1024)
    print(f"Done: {ZIP_PATH} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
