# -*- coding: utf-8 -*-
"""Z-order only: keep window at the bottom (above wallpaper, clickable).

Never pass Qt geometry into Win32 SetWindowPos — DPI scaling would corrupt size/pos.
"""
from __future__ import annotations

import ctypes
import sys

HWND_BOTTOM = 1
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SW_SHOW = 5
SW_RESTORE = 9


def send_to_bottom(hwnd: int) -> None:
    if sys.platform != "win32" or not hwnd:
        return
    user32 = ctypes.windll.user32
    hwnd = int(hwnd)
    parent = user32.GetParent(hwnd)
    if parent:
        user32.SetParent(hwnd, 0)
    user32.SetWindowPos(
        hwnd,
        HWND_BOTTOM,
        0,
        0,
        0,
        0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
    )


def set_topmost(hwnd: int, enabled: bool = True) -> None:
    """Force a dialog above the desktop shell / other apps."""
    if sys.platform != "win32" or not hwnd:
        return
    user32 = ctypes.windll.user32
    hwnd = int(hwnd)
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetWindowPos(
        hwnd,
        HWND_TOPMOST if enabled else HWND_NOTOPMOST,
        0,
        0,
        0,
        0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
    )
    if enabled:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
