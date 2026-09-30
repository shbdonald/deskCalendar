# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QContextMenuEvent,
    QCursor,
    QGuiApplication,
    QIcon,
    QMouseEvent,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from app.services.calendar_math import get_month_grid, get_week_dates
from app.services.config_store import ConfigStore
from app.services.desktop_embed import send_to_bottom
from app.services.holiday_service import HolidayService
from app.services.layout_metrics import (
    MIN_WEEK,
    cell_size_from_month_window,
    cell_size_from_week_window,
    default_month_window,
    default_week_window,
    max_cell_width,
    month_window_size,
    month_window_from_week_window,
    week_window_size,
    window_width_for_cell_width,
)
from app.services.theme import merge_theme
from app.services.todo_store import TodoStore
from app.widgets.month_view import MonthView
from app.widgets.settings_dialog import SettingsDialog
from app.widgets.todo_dialog import TodoEditDialog
from app.widgets.week_view import WeekView

# Chrome icons (flat unicode)
_ICON_EXPAND = "▼"
_ICON_COLLAPSE = "▲"
_ICON_LOCKED = "🔒"
_ICON_UNLOCKED = "🔓"
_ICON_SETTINGS = "⚙"
_ICON_PREV = "‹"
_ICON_NEXT = "›"
_ICON_MIN = "–"
_ICON_CLOSE = "✕"


# Bit flags for frameless edge resize
_LEFT, _RIGHT, _TOP, _BOTTOM = 1, 2, 4, 8
_EDGE = 6


def _tray_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(56, 132, 220))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(4, 4, 56, 56, 12, 12)
    p.setPen(QColor(255, 255, 255))
    font = p.font()
    font.setBold(True)
    font.setPointSize(18)
    p.setFont(font)
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, str(date.today().day))
    p.end()
    return QIcon(pm)


class MainWindow(QMainWindow):
    MIN_BASE = default_week_window()
    MIN_MONTH = default_month_window()

    def __init__(self) -> None:
        super().__init__()
        self.config = ConfigStore()
        self.todos = TodoStore()
        self.holidays = HolidayService(self)
        self.holidays.updated.connect(self.refresh_views)

        self._ref = date.today()
        self._month = date(self._ref.year, self._ref.month, 1)
        self._today_anchor = date.today()
        self._expanded = bool(self.config.get("expanded", False))
        self._size_locked = bool(self.config.get("size_locked", True))
        self._theme = merge_theme(self.config.get("theme"))
        self._drag_pos: QPoint | None = None
        self._resize_edges = 0
        self._resize_origin: QRect | None = None
        self._resize_mouse: QPoint | None = None

        self.setWindowTitle("桌面日历")
        # Tool window; opacity via setWindowOpacity (WorkerW children break per-pixel alpha).
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)

        root = QWidget()
        root.setObjectName("rootPanel")
        root.setMouseTracking(True)
        self.setCentralWidget(root)
        self._layout = QVBoxLayout(root)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(6)

        self._build_chrome()
        self.week_view = WeekView()
        self.month_view = MonthView()
        self.week_view.bind(
            on_toggle=self._on_todo_toggle,
            on_edit=self._on_todo_edit,
            on_add=self._on_day_add,
        )
        self.month_view.bind(
            on_toggle=self._on_todo_toggle,
            on_edit=self._on_todo_edit,
            on_add=self._on_day_add,
        )
        self._layout.addWidget(self.week_view, 1)
        self._layout.addWidget(self.month_view, 1)

        self._apply_style()
        self.setWindowOpacity(float(self.config.get("opacity", 0.92)))

        self.holidays.set_countries(list(self.config.get("countries", ["CN"])))
        self._ensure_holiday_years()
        # Sync UI flags without resizing — real size applied in startup_show.
        self._apply_expanded(self._expanded, persist=False, apply_size=False)
        self._apply_size_lock(self._size_locked, persist=False, apply_size=False)
        self.refresh_views()
        self._setup_tray()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            app.aboutToQuit.connect(self._on_about_to_quit)

        self._geometry_ready = False
        self._restore_geo: QRect | None = None

        self._bottom_timer = QTimer(self)
        self._bottom_timer.setInterval(1500)
        self._bottom_timer.timeout.connect(self._reinforce_bottom)
        self._bottom_timer.start()

        self._clock = QTimer(self)
        self._clock.setInterval(60_000)
        self._clock.timeout.connect(self._on_clock)
        self._clock.start()

    def startup_show(self) -> None:
        """Apply saved size/pos before first show; re-assert after layout/pin."""
        self._apply_saved_geometry()
        self.show()
        self._geometry_ready = True
        QTimer.singleShot(0, self._finish_startup)

    def _finish_startup(self) -> None:
        self._pin_to_desktop()
        # Layout + Win32 z-order can disturb geometry — restore again from JSON.
        self._apply_saved_geometry()
        self.show()

    def _build_chrome(self) -> None:
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(4)

        self.prev_btn = QPushButton(_ICON_PREV)
        self.next_btn = QPushButton(_ICON_NEXT)
        self.title_label = QLabel("桌面日历")
        self.title_label.setObjectName("titleLabel")
        self.title_label.setMinimumWidth(140)
        self.title_label.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        )

        self.toggle_btn = QPushButton(_ICON_EXPAND)
        self.toggle_btn.setToolTip("展开本月")
        self.lock_btn = QPushButton(_ICON_LOCKED)
        self.lock_btn.setToolTip("锁定后不可移动/缩放/关闭；解锁后可调整，右键退出或最小化")
        self.settings_btn = QPushButton(_ICON_SETTINGS)
        self.settings_btn.setToolTip("设置")
        self.min_btn = QPushButton(_ICON_MIN)
        self.min_btn.setToolTip("最小化")
        self.close_btn = QPushButton(_ICON_CLOSE)
        self.close_btn.setToolTip("关闭到托盘")

        for btn in (
            self.prev_btn,
            self.next_btn,
            self.toggle_btn,
            self.lock_btn,
            self.settings_btn,
            self.min_btn,
            self.close_btn,
        ):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setObjectName("chromeBtn")
            btn.setFixedSize(32, 28)
            btn.setFlat(True)

        bar.addWidget(self.prev_btn)
        bar.addWidget(self.title_label, 1)
        bar.addWidget(self.next_btn)
        bar.addSpacing(6)
        bar.addWidget(self.toggle_btn)
        bar.addWidget(self.lock_btn)
        bar.addWidget(self.settings_btn)
        bar.addWidget(self.min_btn)
        bar.addWidget(self.close_btn)

        self.prev_btn.clicked.connect(self._prev)
        self.next_btn.clicked.connect(self._next)
        self.toggle_btn.clicked.connect(self._toggle_expand)
        self.lock_btn.clicked.connect(self._toggle_size_lock)
        self.settings_btn.clicked.connect(self._open_settings)
        self.min_btn.clicked.connect(self._minimize_to_tray)
        self.close_btn.clicked.connect(self._minimize_to_tray)

        self._ctx_menu = QMenu(self)
        self._act_minimize = QAction("最小化", self)
        self._act_minimize.triggered.connect(self._minimize_to_tray)
        self._act_quit = QAction("退出程序", self)
        self._act_quit.triggered.connect(self._quit)
        self._ctx_menu.addAction(self._act_minimize)
        self._ctx_menu.addAction(self._act_quit)

        self._layout.addLayout(bar)

    def _apply_style(self) -> None:
        t = self._theme
        self.setStyleSheet(
            f"""
            QWidget#rootPanel {{
                background: {t['bg']};
                border: none;
                border-radius: 2px;
            }}
            QLabel#titleLabel {{
                color: {t['text']};
                font-size: 14px;
                font-weight: 600;
                padding-left: 4px;
                border: none;
                background: transparent;
            }}
            QPushButton#chromeBtn {{
                background: {t['btn']};
                color: {t['text']};
                border: none;
                border-radius: 2px;
                font-size: 14px;
                padding: 0;
            }}
            QPushButton#chromeBtn:hover {{
                background: {t['btn_hover']};
            }}
            QPushButton#chromeBtn:flat {{
                background: {t['btn']};
            }}
            """
        )

    def _setup_tray(self) -> None:
        self.tray = QSystemTrayIcon(_tray_icon(), self)
        # Parent the menu to the window so it doesn't keep the process alive alone.
        self._tray_menu = QMenu(self)
        self._tray_show_act = QAction("显示", self)
        self._tray_show_act.triggered.connect(self._show_from_tray)
        self._tray_quit_act = QAction("退出", self)
        self._tray_quit_act.triggered.connect(self._quit)
        self._tray_menu.addAction(self._tray_show_act)
        self._tray_menu.addAction(self._tray_quit_act)
        self.tray.setContextMenu(self._tray_menu)
        self.tray.setToolTip("桌面日历")
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_from_tray()

    def _minimize_to_tray(self) -> None:
        if self._size_locked:
            return
        self._save_config()
        self.hide()

    def _show_from_tray(self) -> None:
        self._apply_saved_geometry()
        self.show()
        self._geometry_ready = True
        self._pin_to_desktop()
        self._apply_saved_geometry()

    def _on_about_to_quit(self) -> None:
        self._save_config()
        self._bottom_timer.stop()
        self._clock.stop()
        self.holidays.shutdown()

    def _quit(self) -> None:
        """Tray/context quit — must fully leave the Qt event loop."""
        self._save_config()
        self._bottom_timer.stop()
        self._clock.stop()
        self.holidays.shutdown()
        self.hide()
        if hasattr(self, "tray") and self.tray is not None:
            self.tray.hide()
            self.tray.setContextMenu(None)
        # Defer quit so Windows can close the tray menu first; otherwise
        # QApplication.quit() often no-ops and the process stays running.
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(0, app.quit)

    def closeEvent(self, event: QCloseEvent) -> None:
        event.ignore()
        if not self._size_locked:
            self._save_config()
            self.hide()

    def contextMenuEvent(self, event) -> None:  # noqa: ANN001
        if self._size_locked:
            event.ignore()
            return
        self._ctx_menu.exec(event.globalPos())
        event.accept()

    def _screen_pos(self) -> QPoint:
        """Absolute screen position even if briefly reparented."""
        return self.mapToGlobal(QPoint(0, 0))

    def _base_size(self) -> QSize:
        """Week baseline from JSON cell_w/h via layout_metrics (expand/collapse)."""
        try:
            cw = int(self.config.get("cell_w", MIN_WEEK[0]))
            ch = int(self.config.get("cell_h", MIN_WEEK[1]))
        except (TypeError, ValueError):
            cw, ch = MIN_WEEK
        min_w, min_h = MIN_WEEK
        return week_window_size(max(min_w, cw), max(min_h, ch))

    def _month_size_from_base(self, base: QSize) -> QSize:
        return month_window_from_week_window(base).expandedTo(self.MIN_MONTH)

    def _cells_from_current(self) -> tuple[int, int]:
        size = self.size()
        if size.width() >= 40 and size.height() >= 40:
            if self._expanded:
                return cell_size_from_month_window(size)
            return cell_size_from_week_window(size)
        try:
            cw = int(self.config.get("cell_w", MIN_WEEK[0]))
            ch = int(self.config.get("cell_h", MIN_WEEK[1]))
            return max(MIN_WEEK[0], cw), max(MIN_WEEK[1], ch)
        except (TypeError, ValueError):
            return MIN_WEEK


    def _display_size(self) -> QSize:
        """Size for current view from JSON cells via layout_metrics (expand/collapse)."""
        base = self._base_size()
        if self._expanded:
            return self._month_size_from_base(base)
        return QSize(base.width(), base.height())

    def _min_size(self) -> QSize:
        return self.MIN_MONTH if self._expanded else self.MIN_BASE

    def _active_screen(self):
        pos = self._screen_pos() if self.isVisible() else QPoint(
            int(self.config.get("x", 80)),
            int(self.config.get("y", 80)),
        )
        return QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()

    def _max_cell_w(self) -> int:
        screen = self._active_screen()
        sw = screen.availableGeometry().width() if screen is not None else 1920
        return max_cell_width(sw)

    def _max_window_width(self) -> int:
        return window_width_for_cell_width(self._max_cell_w())

    def _clamp_size(self, size: QSize) -> QSize:
        """Respect min size and max cell width (≤ display/10)."""
        min_s = self._min_size()
        max_w = self._max_window_width()
        w = min(max(size.width(), min_s.width()), max_w)
        h = max(size.height(), min_s.height())
        return QSize(w, h)

    def _set_window_size(self, size: QSize) -> None:
        """Apply size while respecting lock state."""
        target = self._clamp_size(size)
        max_w = self._max_window_width()
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        if self._size_locked:
            self.resize(target)
            self.setFixedSize(target)
        else:
            self.setMinimumSize(self._min_size())
            self.setMaximumWidth(max_w)
            self.resize(target)
        self._restore_geo = QRect(self.geometry())

    def _clamp_pos(self, x: int, y: int, size: QSize) -> tuple[int, int]:
        screen = QGuiApplication.screenAt(QPoint(x, y)) or QGuiApplication.primaryScreen()
        if screen is None:
            return x, y
        avail = screen.availableGeometry()
        x = min(max(avail.x(), x), avail.x() + max(0, avail.width() - size.width()))
        y = min(max(avail.y(), y), avail.y() + max(0, avail.height() - size.height()))
        return x, y

    def _saved_display_size(self) -> QSize:
        """Always restore from JSON display_w/h; fall back to layout_metrics if missing."""
        try:
            dw = int(self.config.get("display_w", 0))
            dh = int(self.config.get("display_h", 0))
        except (TypeError, ValueError):
            dw, dh = 0, 0
        if dw >= 80 and dh >= 80:
            return self._clamp_size(QSize(dw, dh))
        return self._clamp_size(self._display_size())

    def _apply_saved_geometry(self) -> None:
        """Restore position + size from config.json (first launch and later)."""
        size = self._saved_display_size()
        try:
            x = int(self.config.get("x", 80))
            y = int(self.config.get("y", 80))
        except (TypeError, ValueError):
            x, y = 80, 80
        x, y = self._clamp_pos(x, y, size)
        max_w = self._max_window_width()
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.resize(size)
        self.setGeometry(x, y, size.width(), size.height())
        if self._size_locked:
            self.setFixedSize(self.size())
        else:
            self.setMinimumSize(self._min_size())
            self.setMaximumWidth(max_w)
        self._restore_geo = QRect(self.geometry())


    def _save_config(self) -> None:
        """Persist exact geometry; cell sizes via layout_metrics for later expand/resize."""
        size = self.size()
        if size.width() >= 40 and size.height() >= 40:
            pos = self._screen_pos()
            if self._expanded:
                cell_w, cell_h = cell_size_from_month_window(size)
            else:
                cell_w, cell_h = cell_size_from_week_window(size)
            base = week_window_size(cell_w, cell_h)

            self.config.set("x", int(pos.x()), persist=False)
            self.config.set("y", int(pos.y()), persist=False)
            self.config.set("display_w", int(size.width()), persist=False)
            self.config.set("display_h", int(size.height()), persist=False)
            self.config.set("window_w", int(base.width()), persist=False)
            self.config.set("window_h", int(base.height()), persist=False)
            self.config.set("cell_w", int(cell_w), persist=False)
            self.config.set("cell_h", int(cell_h), persist=False)
            self._restore_geo = QRect(self.geometry())

        self.config.set("expanded", bool(self._expanded), persist=False)
        self.config.set("size_locked", bool(self._size_locked), persist=False)

        opacity = float(self.windowOpacity())
        opacity = min(1.0, max(0.3, opacity))
        self.config.set("opacity", round(opacity, 4), persist=False)
        self.config.set("theme", dict(self._theme), persist=False)
        self.config.set("countries", list(self.holidays.countries()), persist=False)

        self.config.save()

    def _hwnd(self) -> int:
        return int(self.winId())

    def _pin_to_desktop(self) -> None:
        """Z-order only — never let Win32 rewrite Qt geometry (breaks under DPI)."""
        if not self.isVisible():
            self.show()
        _ = self.winId()
        send_to_bottom(self._hwnd())
        if self._restore_geo is not None:
            self.setGeometry(self._restore_geo)
            if self._size_locked:
                self.setFixedSize(self._restore_geo.size())
            else:
                self.setMinimumSize(self._min_size())
        self.show()
        self.update()

    def _reinforce_bottom(self) -> None:
        if self.isVisible() and self._geometry_ready:
            send_to_bottom(self._hwnd())

    def eventFilter(self, obj, event):  # noqa: ANN001
        if not self.isVisible() or self._size_locked:
            return super().eventFilter(obj, event)
        if not isinstance(obj, QWidget) or obj.window() is not self:
            return super().eventFilter(obj, event)

        et = event.type()
        if et == QEvent.Type.MouseMove and isinstance(event, QMouseEvent):
            local = self.mapFromGlobal(event.globalPosition().toPoint())
            if self._resize_edges and self._resize_origin and self._resize_mouse is not None:
                self._perform_resize(event.globalPosition().toPoint())
                return True
            if event.buttons() == Qt.MouseButton.NoButton:
                edges = self._hit_edges(local)
                self.setCursor(QCursor(self._cursor_for_edges(edges)))
            return super().eventFilter(obj, event)

        if et == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
            if event.button() == Qt.MouseButton.RightButton and not self._size_locked:
                self._ctx_menu.exec(event.globalPosition().toPoint())
                return True
            if event.button() == Qt.MouseButton.LeftButton:
                local = self.mapFromGlobal(event.globalPosition().toPoint())
                edges = self._hit_edges(local)
                if edges:
                    self._resize_edges = edges
                    self._resize_origin = QRect(self.geometry())
                    self._resize_mouse = event.globalPosition().toPoint()
                    self._drag_pos = None
                    return True

        if et == QEvent.Type.ContextMenu and not self._size_locked:
            if isinstance(event, QContextMenuEvent):
                self._ctx_menu.exec(event.globalPos())
                return True
            return True

        if et == QEvent.Type.MouseButtonRelease and isinstance(event, QMouseEvent):
            if event.button() == Qt.MouseButton.LeftButton and self._resize_edges:
                self._resize_edges = 0
                self._resize_origin = None
                self._resize_mouse = None
                self._save_config()
                return True

        return super().eventFilter(obj, event)

    def _perform_resize(self, global_pos: QPoint) -> None:
        if not self._resize_origin or self._resize_mouse is None:
            return
        delta = global_pos - self._resize_mouse
        geo = QRect(self._resize_origin)
        min_s = self._min_size()
        max_w = self._max_window_width()
        # Right/bottom: grow from fixed opposite edge, clamp to min/max.
        # Left/top: move that edge but clamp so size stays within [min, max].
        if self._resize_edges & _LEFT:
            new_left = self._resize_origin.left() + delta.x()
            max_left = self._resize_origin.right() - min_s.width() + 1
            min_left = self._resize_origin.right() - max_w + 1
            geo.setLeft(max(min_left, min(new_left, max_left)))
        if self._resize_edges & _RIGHT:
            geo.setWidth(
                min(max_w, max(min_s.width(), self._resize_origin.width() + delta.x()))
            )
        if self._resize_edges & _TOP:
            new_top = self._resize_origin.top() + delta.y()
            max_top = self._resize_origin.bottom() - min_s.height() + 1
            geo.setTop(min(new_top, max_top))
        if self._resize_edges & _BOTTOM:
            geo.setHeight(max(min_s.height(), self._resize_origin.height() + delta.y()))
        self.setGeometry(geo)

    def _hit_edges(self, pos: QPoint) -> int:
        if self._size_locked:
            return 0
        r = self.rect()
        edges = 0
        if pos.x() <= _EDGE:
            edges |= _LEFT
        if pos.x() >= r.width() - _EDGE:
            edges |= _RIGHT
        if pos.y() <= _EDGE:
            edges |= _TOP
        if pos.y() >= r.height() - _EDGE:
            edges |= _BOTTOM
        return edges

    def _cursor_for_edges(self, edges: int) -> Qt.CursorShape:
        if edges in (_TOP | _LEFT, _BOTTOM | _RIGHT):
            return Qt.CursorShape.SizeFDiagCursor
        if edges in (_TOP | _RIGHT, _BOTTOM | _LEFT):
            return Qt.CursorShape.SizeBDiagCursor
        if edges in (_LEFT, _RIGHT):
            return Qt.CursorShape.SizeHorCursor
        if edges in (_TOP, _BOTTOM):
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            if not self._size_locked:
                self._ctx_menu.exec(event.globalPosition().toPoint())
                event.accept()
                return
            event.ignore()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            edges = self._hit_edges(event.position().toPoint())
            if edges:
                self._resize_edges = edges
                self._resize_origin = QRect(self.geometry())
                self._resize_mouse = event.globalPosition().toPoint()
                self._drag_pos = None
                event.accept()
                return
            self._resize_edges = 0
            # Locked: pin in place — no dragging.
            if self._size_locked:
                self._drag_pos = None
                event.accept()
                return
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._resize_edges:
            self._perform_resize(event.globalPosition().toPoint())
            event.accept()
            return
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        edges = self._hit_edges(event.position().toPoint())
        self.setCursor(QCursor(self._cursor_for_edges(edges)))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._resize_edges:
                self._resize_edges = 0
                self._resize_origin = None
                self._resize_mouse = None
                self._save_config()
                event.accept()
                return
            if self._drag_pos is not None:
                self._drag_pos = None
                self._save_config()
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def _apply_size_lock(self, locked: bool, *, persist: bool = True, apply_size: bool = True) -> None:
        self._size_locked = locked
        self.lock_btn.setText(_ICON_LOCKED if locked else _ICON_UNLOCKED)
        self.lock_btn.setToolTip(
            "已锁定：不可移动、缩放、最小化或关闭。点击解锁。"
            if locked
            else "已解锁：可拖动/缩放；右键可最小化或退出。点击锁定。"
        )
        self.min_btn.setVisible(not locked)
        self.close_btn.setVisible(not locked)
        self._drag_pos = None
        self._resize_edges = 0
        self.setCursor(Qt.CursorShape.ArrowCursor)
        if apply_size:
            # Keep current on-screen size; only toggle fixed/min constraints.
            self._set_window_size(QSize(self.width(), self.height()))
        if persist:
            self.config.set("size_locked", locked)
            self._save_config()

    def _toggle_size_lock(self) -> None:
        self._apply_size_lock(not self._size_locked)

    def _apply_expanded(self, expanded: bool, *, persist: bool = True, apply_size: bool = True) -> None:
        """Expand/collapse while preserving current day-cell metrics."""
        if persist and self.isVisible() and apply_size:
            self._save_config()
        cell_w, cell_h = self._cells_from_current()
        self._expanded = expanded
        self.week_view.setVisible(not expanded)
        self.month_view.setVisible(expanded)
        self.toggle_btn.setText(_ICON_COLLAPSE if expanded else _ICON_EXPAND)
        self.toggle_btn.setToolTip("收起本周" if expanded else "展开本月")
        self._update_title()
        if apply_size:
            target = month_window_size(cell_w, cell_h) if expanded else week_window_size(cell_w, cell_h)
            self._set_window_size(target)
            # Keep display_* in sync for next cold start.
            self.config.set("cell_w", cell_w, persist=False)
            self.config.set("cell_h", cell_h, persist=False)
        if persist:
            self.config.set("expanded", expanded)
            self._save_config()

    def _update_title(self) -> None:
        if self._expanded:
            self.title_label.setText(f"{self._month.year}年{self._month.month}月")
        else:
            days = get_week_dates(self._ref)
            self.title_label.setText(
                f"本周  {days[0].month}/{days[0].day}–{days[-1].month}/{days[-1].day}"
            )

    def _ensure_holiday_years(self) -> None:
        years = {self._ref.year, self._month.year}
        for d in get_week_dates(self._ref):
            years.add(d.year)
        for week in get_month_grid(self._month.year, self._month.month):
            for d in week:
                years.add(d.year)
        self.holidays.ensure_years(sorted(years))

    def refresh_views(self) -> None:
        self._update_title()
        self.week_view.rebuild(
            self._ref,
            self.holidays.holidays_for,
            self.todos.for_date,
            theme=self._theme,
        )
        self.month_view.rebuild(
            self._month.year,
            self._month.month,
            self.holidays.holidays_for,
            self.todos.for_date,
            theme=self._theme,
        )

    def _toggle_expand(self) -> None:
        self._apply_expanded(not self._expanded)

    def _prev(self) -> None:
        if self._expanded:
            y, m = self._month.year, self._month.month - 1
            if m < 1:
                y, m = y - 1, 12
            self._month = date(y, m, 1)
        else:
            self._ref = self._ref - timedelta(days=7)
        self._ensure_holiday_years()
        self.refresh_views()

    def _next(self) -> None:
        if self._expanded:
            y, m = self._month.year, self._month.month + 1
            if m > 12:
                y, m = y + 1, 1
            self._month = date(y, m, 1)
        else:
            self._ref = self._ref + timedelta(days=7)
        self._ensure_holiday_years()
        self.refresh_views()

    def _on_clock(self) -> None:
        today = date.today()
        if today == self._today_anchor:
            return
        # Civil day rolled over — jump navigation back to today.
        self._today_anchor = today
        self._ref = today
        self._month = date(today.year, today.month, 1)
        self._ensure_holiday_years()
        self.refresh_views()

    def _on_todo_toggle(self, day: date, item_id: str) -> None:
        try:
            self.todos.toggle(day, item_id)
        except KeyError:
            return
        self.refresh_views()

    def _on_todo_edit(self, day: date, item_id: str) -> None:
        item = next((t for t in self.todos.for_date(day) if t["id"] == item_id), None)
        if not item:
            return
        dlg = TodoEditDialog(day, item=item, parent=self)
        if dlg.exec() != TodoEditDialog.DialogCode.Accepted:
            return
        try:
            if dlg.deleted:
                self.todos.delete(day, item_id)
            else:
                self.todos.update(day, item_id, title=dlg.title_text)
        except ValueError as exc:
            QMessageBox.warning(self, "提示", str(exc))
            return
        self.refresh_views()

    def _on_day_add(self, day: date) -> None:
        dlg = TodoEditDialog(day, parent=self)
        if dlg.exec() != TodoEditDialog.DialogCode.Accepted:
            return
        try:
            self.todos.add(day, dlg.title_text)
        except ValueError as exc:
            QMessageBox.warning(self, "提示", str(exc))
            return
        self.refresh_views()

    def _open_settings(self) -> None:
        prev_opacity = float(self.config.get("opacity", 0.92))
        prev_theme = dict(self._theme)

        def preview_opacity(op: float) -> None:
            self.setWindowOpacity(op)

        def preview_theme(theme: dict) -> None:
            self._theme = merge_theme(theme)
            self._apply_style()
            self.refresh_views()

        dlg = SettingsDialog(
            countries=self.holidays.countries(),
            available=self.holidays.available_countries(),
            opacity=prev_opacity,
            theme=prev_theme,
            on_opacity_preview=preview_opacity,
            on_theme_preview=preview_theme,
            parent=None,
        )
        # Refresh country list in background; dialog already has curated list.
        self.holidays.countries_refreshed.connect(dlg.replace_countries)
        self.holidays.refresh_available_from_api_async()

        if dlg.exec() != SettingsDialog.DialogCode.Accepted:
            self.setWindowOpacity(prev_opacity)
            self._theme = merge_theme(prev_theme)
            self._apply_style()
            self.refresh_views()
            try:
                self.holidays.countries_refreshed.disconnect(dlg.replace_countries)
            except (TypeError, RuntimeError):
                pass
            return
        try:
            self.holidays.countries_refreshed.disconnect(dlg.replace_countries)
        except (TypeError, RuntimeError):
            pass

        countries = dlg.selected_countries() or ["CN"]
        self._theme = merge_theme(dlg.theme())
        self.setWindowOpacity(dlg.opacity())
        self._apply_style()
        self.holidays.set_countries(countries)
        self._ensure_holiday_years()
        self.refresh_views()
        self._save_config()
        self._pin_to_desktop()
        self._apply_saved_geometry()
