# -*- coding: utf-8 -*-
"""Current-user Windows Run key for start-with-Windows (no admin)."""
from __future__ import annotations

import sys
import winreg
from pathlib import Path

from app.services.config_store import project_root

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "DesktopCalendar"


def launch_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'
    root = project_root()
    main_py = root / "main.py"
    py = Path(sys.executable).resolve()
    # Prefer pythonw so no console flashes on login.
    pythonw = py.with_name("pythonw.exe")
    exe = pythonw if pythonw.is_file() else py
    return f'"{exe}" "{main_py}"'


def is_registered() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
        return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> None:
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass


def sync_from_preference(enabled: bool) -> None:
    """Re-write or clear the Run value from the saved preference."""
    try:
        set_enabled(bool(enabled))
    except OSError:
        pass
