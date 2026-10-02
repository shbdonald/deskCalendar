# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path


def _install_frozen_crash_log() -> None:
    """Write uncaught errors to userdata/crash.log (dev + packaged)."""
    root = _project_root()
    log_dir = root / "userdata"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "crash.log"

    def _hook(exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            log_path.write_text(text, encoding="utf-8")
        except OSError:
            pass
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook


def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _pid_path() -> Path:
    path = _project_root() / "userdata"
    path.mkdir(parents=True, exist_ok=True)
    return path / "instance.pid"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    exit_code = ctypes.c_ulong()
    ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
    kernel32.CloseHandle(handle)
    return bool(ok) and int(exit_code.value) == STILL_ACTIVE


def _kill_pid(pid: int) -> None:
    if sys.platform != "win32" or pid <= 0:
        return
    import ctypes

    PROCESS_TERMINATE = 0x0001
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if handle:
        kernel32.TerminateProcess(handle, 1)
        kernel32.CloseHandle(handle)


def _collect_desktopcalendar_exe_pids(my_pid: int) -> set[int]:
    if sys.platform != "win32":
        return set()
    import ctypes
    from ctypes import wintypes

    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    found: set[int] = set()
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1:
        return found
    pe = PROCESSENTRY32W()
    pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    ok = kernel32.Process32FirstW(snap, ctypes.byref(pe))
    while ok:
        name = (pe.szExeFile or "").lower()
        pid = int(pe.th32ProcessID)
        if pid != my_pid and name == "desktopcalendar.exe":
            found.add(pid)
        ok = kernel32.Process32NextW(snap, ctypes.byref(pe))
    kernel32.CloseHandle(snap)
    return found


def _terminate_other_instances() -> None:
    """End older instances so each test/run starts clean."""
    my_pid = os.getpid()
    victims: set[int] = set()

    lock = _pid_path()
    try:
        if lock.exists():
            old = int((lock.read_text(encoding="utf-8") or "0").strip() or "0")
            if old > 0 and old != my_pid and _pid_alive(old):
                victims.add(old)
    except (OSError, ValueError):
        pass

    victims |= _collect_desktopcalendar_exe_pids(my_pid)

    for pid in victims:
        _kill_pid(pid)
    if victims:
        time.sleep(0.4)

    try:
        lock.write_text(str(my_pid), encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    _install_frozen_crash_log()
    _terminate_other_instances()

    from PySide6.QtWidgets import QApplication

    from app.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("DesktopCalendar")
    app.setOrganizationName("DesktopCalendar")

    window = MainWindow()
    window.startup_show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
