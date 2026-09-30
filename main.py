# -*- coding: utf-8 -*-
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("DesktopCalendar")
    app.setOrganizationName("DesktopCalendar")

    window = MainWindow()
    window.startup_show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
