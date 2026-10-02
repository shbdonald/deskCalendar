# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QContextMenuEvent,
    QCursor,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QIcon,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from app.services import autostart
from app.services.calendar_math import (
    get_month_grid,
    get_week_dates,
    normalize_week_starts_on,
)
from app.services.config_store import ConfigStore
from app.services.desktop_embed import send_to_bottom, set_topmost
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
from app.services.todo_store import REPEAT_NONE, TodoStore, plan_needs_push
from app.services.winamp_dock import (
    SNAP,
    DockState,
    apply_dock,
    capture_offset,
    docked_drag_step,
    should_break,
    try_snap,
)
from app.services.icloud_calendar_sync import DEFAULT_CALENDAR_NAME, ICloudCalendarSync
from app.services.local_calendar import (
    LOCAL_CALENDAR_ID,
    LOCAL_CALENDAR_NAME,
    is_local_calendar_id,
    is_local_plan,
    make_local_calendar_id,
    with_local_calendar,
)
from app.widgets.month_view import MonthView
from app.widgets.settings_dialog import SettingsDialog
from app.widgets.todo_dialog import (
    DELETE_OCCURRENCE,
    NOTE_MODE_DAY,
    NOTE_MODE_SERIES,
    TodoEditDialog,
)
from app.widgets.todo_list_window import TodoListWindow
from app.widgets.week_view import WeekView

# Chrome icons (flat unicode)
_ICON_EXPAND = "▼"
_ICON_COLLAPSE = "▲"
_ICON_LOCKED = "🔒"
_ICON_UNLOCKED = "🔓"
_ICON_SYNC = "🔄"
_ICON_SETTINGS = "⚙"
_ICON_MIN = "–"
_ICON_CLOSE = "✕"

# Windows / Edge 浏览器工具栏返回键字形（Segoe MDL2 / Fluent Icons）
_CHROME_BACK = "\uE0A6"
_SEGOE_ICON_FONTS = ("Segoe Fluent Icons", "Segoe MDL2 Assets")


def _segoe_icon_font(pixel_size: int) -> QFont | None:
    families = set(QFontDatabase.families())
    for name in _SEGOE_ICON_FONTS:
        if name in families:
            font = QFont(name)
            font.setPixelSize(pixel_size)
            return font
    return None


def _browser_nav_icon(*, forward: bool, color: str, size: int = 32) -> QIcon:
    """绘制与 Windows 常用浏览器一致的前进/后退图标。"""
    pm = QPixmap(size, size)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    col = QColor(color)
    font = _segoe_icon_font(max(14, size // 2))
    if font is not None:
        # ChromeBack；前进 = 水平镜像，与 Edge 工具栏一致
        if forward:
            p.translate(size, 0)
            p.scale(-1.0, 1.0)
        p.setFont(font)
        p.setPen(col)
        p.drawText(
            0,
            0,
            size,
            size,
            int(Qt.AlignmentFlag.AlignCenter),
            _CHROME_BACK,
        )
    else:
        # 无 Segoe 字体时：画浏览器风格带杆箭头
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        s = float(size)
        path = QPainterPath()
        # 箭头头部（朝左）+ 水平杆
        path.moveTo(s * 0.42, s * 0.22)
        path.lineTo(s * 0.18, s * 0.50)
        path.lineTo(s * 0.42, s * 0.78)
        path.lineTo(s * 0.42, s * 0.62)
        path.lineTo(s * 0.78, s * 0.62)
        path.lineTo(s * 0.78, s * 0.38)
        path.lineTo(s * 0.42, s * 0.38)
        path.closeSubpath()
        if forward:
            p.translate(size, 0)
            p.scale(-1.0, 1.0)
        p.drawPath(path)
    p.end()
    icon = QIcon()
    icon.addPixmap(pm)
    return icon


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


class _ICloudJob(QThread):
    """在后台线程执行 iCloud 同步，避免卡住界面。"""

    finished_ok = Signal(object)
    finished_err = Signal(str)

    def __init__(self, fn, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn()
            self.finished_ok.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.finished_err.emit(str(exc))


class MainWindow(QMainWindow):
    MIN_BASE = default_week_window()
    MIN_MONTH = default_month_window()

    def __init__(self) -> None:
        super().__init__()
        self.config = ConfigStore()
        self.todos = TodoStore()
        self.icloud = ICloudCalendarSync()
        self._ensure_local_calendar_config()
        self._icloud_jobs: list[_ICloudJob] = []
        self._todolist_syncing_vis = False
        self._todo_docked = bool(self.config.get("todolist_docked", True))
        side = str(self.config.get("todolist_dock_side", "bottom") or "bottom")
        if side not in ("bottom", "top", "left", "right"):
            side = "bottom"
        try:
            dock_off = int(self.config.get("todolist_dock_offset", 0) or 0)
        except (TypeError, ValueError):
            dock_off = 0
        self._todo_dock = DockState(side=side, offset=dock_off)
        self._todo_group_guard = False
        self._todo_drag_last: QPoint | None = None
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
            on_edit=self._on_todo_edit,
            on_add=self._on_day_add,
        )
        self.month_view.bind(
            on_edit=self._on_todo_edit,
            on_add=self._on_day_add,
        )
        self._layout.addWidget(self.week_view, 1)
        self._layout.addWidget(self.month_view, 1)

        self._apply_style()
        self.setWindowOpacity(float(self.config.get("opacity", 0.92)))

        self.holidays.set_countries(list(self.config.get("countries", [])))
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

        self._icloud_timer = QTimer(self)
        self._icloud_timer.timeout.connect(self._icloud_poll)
        self._reload_icloud_timer()

        # Refresh Run key path if preference is on (e.g. portable folder moved).
        if bool(self.config.get("start_with_windows", False)):
            autostart.sync_from_preference(True)

        self._init_todolist_window()

    def _init_todolist_window(self) -> None:
        # 副窗口：以日历为主窗 owner，跟随主窗生命周期
        self.todo_list_win = TodoListWindow(self)
        self.todo_list_win.apply_theme(self._theme)
        self.todo_list_win.setWindowOpacity(float(self.windowOpacity()))
        self.todo_list_win.set_interactive(not self._size_locked)
        self.todo_list_win.set_items(self._todos_for_date(date.today()))
        self.todo_list_win.add_requested.connect(self._on_todolist_add)
        self.todo_list_win.done_changed.connect(self._on_todolist_done)
        self.todo_list_win.title_changed.connect(self._on_todolist_title)
        self.todo_list_win.delete_requested.connect(self._on_todolist_delete)
        self.todo_list_win.edit_requested.connect(self._on_todolist_edit)
        self.todo_list_win.visibility_changed.connect(self._on_todolist_visibility)
        self.todo_list_win.geometry_changed.connect(self._persist_todolist_geometry)
        self.todo_list_win.geometry_moving.connect(self._on_todolist_moving)
        self.todo_list_win.undock_requested.connect(self._undock_todolist)
        self._place_todolist(force_dock=self._size_locked or self._todo_docked)
        if bool(self.config.get("todolist_visible", True)):
            self._set_todolist_visible(True, persist=False)

    def _todolist_size(self) -> tuple[int, int]:
        try:
            w = int(self.config.get("todolist_w", 0))
        except (TypeError, ValueError):
            w = 0
        try:
            h = int(self.config.get("todolist_h", 0))
        except (TypeError, ValueError):
            h = 0
        if w < 240:
            w = max(240, int(self.width()) if self.width() >= 240 else 300)
        if h < 280:
            h = 320
        return w, h

    def _calendar_screen_rect(self) -> QRect:
        origin = self.mapToGlobal(QPoint(0, 0))
        return QRect(origin.x(), origin.y(), int(self.width()), int(self.height()))

    def _default_bottom_dock(self) -> DockState:
        """默认贴日历正下方、右对齐。"""
        cal = self._calendar_screen_rect()
        w, _h = self._todolist_size()
        return DockState("bottom", max(0, cal.width() - w))

    def _clamp_todo_dock_offset(self, *, size: tuple[int, int] | None = None) -> None:
        """避免旧偏移把待办推出屏幕 / 日历右缘。"""
        cal = self._calendar_screen_rect()
        w, h = size if size is not None else self._todolist_size()
        side = self._todo_dock.side
        off = int(self._todo_dock.offset)
        if side in ("bottom", "top"):
            max_off = max(0, cal.width() - w)
            off = min(max(0, off), max_off)
        elif side in ("left", "right"):
            max_off = max(0, cal.height() - h)
            off = min(max(0, off), max_off)
        self._todo_dock.offset = off

    def _apply_todo_dock(self, *, size: tuple[int, int] | None = None) -> None:
        if not hasattr(self, "todo_list_win"):
            return
        if not (self._todo_docked or self._size_locked):
            return
        w, h = size if size is not None else self._todolist_size()
        w, h = max(240, int(w)), max(280, int(h))
        self._clamp_todo_dock_offset(size=(w, h))
        cal = self._calendar_screen_rect()
        geo = apply_dock(cal, (w, h), self._todo_dock)
        screen = QGuiApplication.screenAt(geo.center()) or QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            x = min(max(avail.x(), geo.x()), avail.x() + max(0, avail.width() - geo.width()))
            y = min(max(avail.y(), geo.y()), avail.y() + max(0, avail.height() - geo.height()))
            geo = QRect(x, y, geo.width(), geo.height())
        self._todo_group_guard = True
        try:
            self.todo_list_win.setGeometry(geo)
        finally:
            self._todo_group_guard = False
        self.todo_list_win.setWindowOpacity(float(self.windowOpacity()))
        # 解锁且贴合时：拖动走「仅主窗算几何」路径，避免抖动
        self.todo_list_win.set_dock_slide(bool(self._todo_docked and not self._size_locked))

    def _place_todolist(self, *, force_dock: bool = False) -> None:
        if not hasattr(self, "todo_list_win"):
            return
        self.todo_list_win.setWindowOpacity(float(self.windowOpacity()))
        w, h = self._todolist_size()
        if force_dock or self._size_locked:
            if not self._todo_docked:
                self._todo_docked = True
                self._todo_dock = self._default_bottom_dock()
            self._apply_todo_dock(size=(w, h))
            return
        if self._todo_docked:
            self._apply_todo_dock(size=(w, h))
            return
        try:
            x = int(self.config.get("todolist_x", 80))
            y = int(self.config.get("todolist_y", 80))
        except (TypeError, ValueError):
            x, y = 80, 80
        self.todo_list_win.setGeometry(x, y, w, h)

    def _dock_todolist(self) -> None:
        if not hasattr(self, "todo_list_win"):
            return
        self.todo_list_win.setWindowOpacity(float(self.windowOpacity()))
        if self._size_locked:
            self._todo_docked = True
            if self._todo_dock.side not in ("bottom", "top", "left", "right"):
                self._todo_dock = self._default_bottom_dock()
            self._apply_todo_dock()
        elif self._todo_docked:
            self._apply_todo_dock()

    def _remember_todo_dock(self, state: DockState) -> None:
        self._todo_docked = True
        self._todo_dock = state
        if hasattr(self, "todo_list_win"):
            self.todo_list_win.set_dock_slide(True)
        self.config.set('todolist_docked', True, persist=False)
        self.config.set('todolist_dock_side', state.side, persist=False)
        self.config.set('todolist_dock_offset', int(state.offset), persist=False)

    def _undock_todolist(self) -> None:
        if self._size_locked:
            return
        self._todo_docked = False
        self._todo_drag_last = None
        if hasattr(self, "todo_list_win"):
            self.todo_list_win.set_dock_slide(False)
        self.config.set('todolist_docked', False, persist=False)
        self.config.save()

    def _on_todolist_moving(self, mode: str) -> None:
        """副窗移动/缩放；主窗（日历）永不被副窗拖走。"""
        if self._todo_group_guard or not hasattr(self, "todo_list_win"):
            return
        cal = self._calendar_screen_rect()
        todo = self.todo_list_win.geometry()
        cursor = QCursor.pos()

        if mode == "drag" and self._todo_docked and not self._size_locked:
            grab = self.todo_list_win.drag_grab()
            if grab is None:
                return
            proposed = cursor - grab
            st, geo = docked_drag_step(
                cal, (todo.width(), todo.height()), self._todo_dock, proposed
            )
            if st is None:
                # 拉开：放到自由位置，退出贴合滑动
                self._todo_docked = False
                self.todo_list_win.set_dock_slide(False)
                self._todo_drag_last = QPoint(geo.x(), geo.y())
                self._todo_group_guard = True
                try:
                    self.todo_list_win.setGeometry(geo)
                finally:
                    self._todo_group_guard = False
                self.todo_list_win.resync_drag_grab(cursor)
                return
            self._remember_todo_dock(st)
            self._todo_group_guard = True
            try:
                self.todo_list_win.setGeometry(geo)
            finally:
                self._todo_group_guard = False
            return

        if mode == "drag":
            # 未贴合：自由移动，靠近日历则吸上
            self._todo_drag_last = QPoint(todo.x(), todo.y())
            state = try_snap(cal, todo)
            if state is not None:
                self._remember_todo_dock(state)
                self._apply_todo_dock(size=(todo.width(), todo.height()))
                self.todo_list_win.resync_drag_grab(cursor)
            return

        # resize：贴合时保留用户拖出的宽高，只更新偏移再贴回
        if self._todo_docked:
            if should_break(cal, todo, self._todo_dock):
                self._todo_docked = False
                self.todo_list_win.set_dock_slide(False)
            else:
                self._todo_dock.offset = capture_offset(cal, todo, self._todo_dock.side)
                self._apply_todo_dock(size=(todo.width(), todo.height()))
                return
        state = try_snap(cal, todo)
        if state is not None:
            self._remember_todo_dock(state)
            self._apply_todo_dock(size=(todo.width(), todo.height()))

    def _persist_todolist_geometry(self) -> None:
        if not hasattr(self, "todo_list_win"):
            return
        self._todo_drag_last = None
        geo = self.todo_list_win.geometry()
        if geo.width() < 40 or geo.height() < 40:
            return
        # 松手时：若仍贴合，做一次左右/上下对齐吸附（拖动中不做，避免抖）
        if self._todo_docked or self._size_locked:
            cal = self._calendar_screen_rect()
            side = self._todo_dock.side
            if side in ("bottom", "top"):
                x = geo.x()
                if abs(geo.x() - cal.x()) <= SNAP:
                    x = cal.x()
                elif abs((geo.x() + geo.width()) - (cal.x() + cal.width())) <= SNAP:
                    x = cal.x() + cal.width() - geo.width()
                self._todo_dock.offset = x - cal.x()
            else:
                y = geo.y()
                if abs(geo.y() - cal.y()) <= SNAP:
                    y = cal.y()
                elif abs((geo.y() + geo.height()) - (cal.y() + cal.height())) <= SNAP:
                    y = cal.y() + cal.height() - geo.height()
                self._todo_dock.offset = y - cal.y()
            self._apply_todo_dock(size=(geo.width(), geo.height()))
            geo = self.todo_list_win.geometry()
        self.config.set("todolist_x", int(geo.x()), persist=False)
        self.config.set("todolist_y", int(geo.y()), persist=False)
        self.config.set("todolist_w", int(geo.width()), persist=False)
        self.config.set("todolist_h", int(geo.height()), persist=False)
        self.config.set("todolist_docked", bool(self._todo_docked), persist=False)
        self.config.set("todolist_dock_side", self._todo_dock.side, persist=False)
        self.config.set("todolist_dock_offset", int(self._todo_dock.offset), persist=False)
        self.config.save()

    def _set_todolist_visible(self, visible: bool, *, persist: bool = True) -> None:
        # 锁定时不允许关闭今日待办
        if not visible and self._size_locked:
            visible = True
        self._todolist_syncing_vis = True
        try:
            if visible:
                self.todo_list_win.apply_theme(self._theme)
                self.todo_list_win.set_items(self._todos_for_date(date.today()))
                self._place_todolist(force_dock=self._size_locked or self._todo_docked)
                self.todo_list_win.show()
                self.todo_list_win.raise_()
            else:
                self.todo_list_win.force_hide()
            if persist:
                self.config.set("todolist_visible", bool(visible))
                self.config.save()
        finally:
            self._todolist_syncing_vis = False

    def _on_todolist_visibility(self, visible: bool) -> None:
        if self._todolist_syncing_vis:
            return
        self.config.set("todolist_visible", bool(visible))
        self.config.save()

    def _refresh_todolist_ui(self) -> None:
        if hasattr(self, "todo_list_win"):
            self.todo_list_win.set_items(self._todos_for_date(date.today()))

    def _on_todolist_add(self, title: str) -> None:
        today = date.today()
        default_id = self._effective_default_calendar_id()
        cals = self._calendars_for_todo_dialog()
        default_name = LOCAL_CALENDAR_NAME if is_local_calendar_id(default_id) else None
        if not default_name:
            for c in cals:
                if c.get("id") == default_id:
                    default_name = c.get("name")
                    break
        if not default_name:
            default_name = self._icloud_calendar_name()
        try:
            plan = self.todos.add(
                today,
                title,
                repeat=REPEAT_NONE,
                calendar_id=default_id,
                calendar_name=default_name,
            )
        except ValueError as exc:
            QMessageBox.warning(self.todo_list_win, "提示", str(exc))
            return
        self._ensure_plan_calendar_visible(default_id, default_name)
        self.refresh_views()
        self._icloud_push_plan(plan["id"])

    def _on_todolist_title(self, item_id: str, title: str) -> None:
        plan = self.todos.get_plan(item_id)
        if not plan:
            self._refresh_todolist_ui()
            return
        try:
            self.todos.update(
                item_id,
                title=title,
                start=date.fromisoformat(str(plan["start"])),
                repeat=str(plan.get("repeat") or REPEAT_NONE),
                calendar_id=plan.get("calendar_id"),
                calendar_name=plan.get("calendar_name"),
            )
        except (ValueError, KeyError) as exc:
            QMessageBox.warning(self.todo_list_win, "提示", str(exc))
            self._refresh_todolist_ui()
            return
        self.refresh_views()
        self._icloud_push_plan(item_id)

    def _on_todolist_edit(self, item_id: str) -> None:
        self._on_todo_edit(date.today(), item_id)

    def _on_todolist_delete(self, item_id: str) -> None:
        """TodoList 删除：仅删今日（重复用 exceptions；单次整条删）。"""
        today = date.today()
        plan = self.todos.get_plan(item_id)
        if not plan:
            self._refresh_todolist_ui()
            return
        repeat = str(plan.get("repeat") or REPEAT_NONE)
        if repeat == REPEAT_NONE:
            uid = str(plan.get("caldav_uid") or "")
            cal_id = str(plan.get("calendar_id") or "") or None
            try:
                self.todos.delete(item_id)
            except KeyError:
                return
            if uid:
                self._icloud_delete_uid(uid, calendar_id=cal_id)
        else:
            try:
                self.todos.delete_occurrence(item_id, today)
            except KeyError:
                return
            self._icloud_push_plan(item_id)
        self.refresh_views()

    def _ask_complete_note(
        self,
        title: str,
        *,
        recurring: bool,
        parent=None,
        initial: str = "",
        series_note: str = "",
    ) -> tuple[str, str, bool]:
        """返回 (note_mode, text, ok)。取消时 ok=False。

        周期计划：选「系列备注」预填系列备注；选「当日备注」清空备注栏。
        """
        from PySide6.QtWidgets import (
            QDialog,
            QDialogButtonBox,
            QHBoxLayout,
            QLabel,
            QRadioButton,
            QTextEdit,
            QVBoxLayout,
        )

        dlg = QDialog(parent or self)
        dlg.setWindowTitle("完成情况")
        dlg.setMinimumWidth(360)
        root = QVBoxLayout(dlg)
        root.addWidget(QLabel(f"「{title}」"))

        mode = NOTE_MODE_DAY
        series_radio = None
        day_radio = None
        series_text = str(series_note or "").strip()
        if recurring:
            row = QHBoxLayout()
            row.addWidget(QLabel("备注范围"))
            series_radio = QRadioButton("系列备注")
            day_radio = QRadioButton("当日备注")
            day_radio.setChecked(True)
            row.addWidget(series_radio)
            row.addWidget(day_radio)
            row.addStretch(1)
            root.addLayout(row)
            tip = QLabel(
                "系列：写进整条计划，当日仅打卡。\n"
                "当日：新建「今日单日」已完成任务，系列跳过今天。"
            )
            tip.setWordWrap(True)
            root.addWidget(tip)
        else:
            root.addWidget(QLabel("当日备注（可选）"))

        edit = QTextEdit()
        edit.setAcceptRichText(False)
        edit.setMinimumHeight(80)
        if recurring:
            edit.clear()  # 默认当日备注：空白
        else:
            edit.setPlainText(initial)
        root.addWidget(edit)

        if recurring and series_radio is not None and day_radio is not None:

            def _on_series(checked: bool) -> None:
                if checked:
                    edit.setPlainText(series_text)

            def _on_day(checked: bool) -> None:
                if checked:
                    edit.clear()

            series_radio.toggled.connect(_on_series)
            day_radio.toggled.connect(_on_day)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        root.addWidget(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return NOTE_MODE_DAY, "", False
        if recurring and series_radio is not None and series_radio.isChecked():
            mode = NOTE_MODE_SERIES
        else:
            mode = NOTE_MODE_DAY
        return mode, edit.toPlainText().strip(), True

    def _on_todolist_done(self, item_id: str, done: bool) -> None:
        today = date.today()
        item = next((t for t in self._todos_for_date(today) if t["id"] == item_id), None)
        if not item:
            self._refresh_todolist_ui()
            return
        if bool(item.get("done")) == bool(done):
            return

        if done:
            plan = self.todos.get_plan(item_id)
            if not plan:
                return
            repeat = str(plan.get("repeat") or REPEAT_NONE)
            title = str(item.get("title") or "")
            if repeat != REPEAT_NONE:
                note_mode, text, ok = self._ask_complete_note(
                    title,
                    recurring=True,
                    parent=self.todo_list_win,
                    series_note=str(plan.get("detail") or ""),
                )
                if not ok:
                    self._refresh_todolist_ui()
                    return
                if note_mode == NOTE_MODE_DAY:
                    try:
                        series_id, new_id = self.todos.complete_recurring_day_as_standalone(
                            item_id, today, detail=text
                        )
                    except (KeyError, ValueError):
                        self._refresh_todolist_ui()
                        return
                    self.refresh_views()
                    self._icloud_push_plan(series_id)
                    self._icloud_push_plan(new_id)
                    return
                # 系列备注：写进系列 detail，当日打卡，不拆单日
                try:
                    if text:
                        self.todos.update(
                            item_id,
                            title=str(plan.get("title") or ""),
                            start=date.fromisoformat(str(plan["start"])),
                            repeat=repeat,
                            calendar_id=plan.get("calendar_id"),
                            calendar_name=plan.get("calendar_name"),
                            detail=text,
                        )
                    self.todos.toggle(today, item_id)
                except (KeyError, ValueError):
                    self._refresh_todolist_ui()
                    return
                self.refresh_views()
                self._icloud_push_plan(item_id)
                return
            # 单日：当日备注写入 detail，再打卡
            _mode, text, ok = self._ask_complete_note(
                title,
                recurring=False,
                parent=self.todo_list_win,
                initial=str(plan.get("detail") or ""),
            )
            if not ok:
                self._refresh_todolist_ui()
                return
            try:
                if text != str(plan.get("detail") or "").strip():
                    self.todos.update(
                        item_id,
                        title=str(plan.get("title") or ""),
                        start=date.fromisoformat(str(plan["start"])),
                        repeat=REPEAT_NONE,
                        calendar_id=plan.get("calendar_id"),
                        calendar_name=plan.get("calendar_name"),
                        detail=text,
                    )
                self.todos.toggle(today, item_id)
            except (KeyError, ValueError):
                return
            self.refresh_views()
            self._icloud_push_plan(item_id)
            return

        # 取消完成：仅取消打卡（若是拆出的单日任务，系列仍跳过该日）
        try:
            self.todos.toggle(today, item_id)
        except KeyError:
            return
        self.refresh_views()
        self._icloud_push_plan(item_id)

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
        self._place_todolist(force_dock=self._size_locked)
        if bool(self.config.get("todolist_visible", True)):
            self._set_todolist_visible(True, persist=False)
        self.show()

    def _build_chrome(self) -> None:
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(4)

        self.prev_btn = QPushButton()
        self.next_btn = QPushButton()
        self.prev_btn.setToolTip("上一周期")
        self.next_btn.setToolTip("下一周期")
        self.today_btn = QPushButton("今")
        self.today_btn.setToolTip("回到当日")
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
        self.sync_btn = QPushButton(_ICON_SYNC)
        self.sync_btn.setToolTip("立即同步 iCloud")
        self.settings_btn = QPushButton(_ICON_SETTINGS)
        self.settings_btn.setToolTip("设置")
        self.min_btn = QPushButton(_ICON_MIN)
        self.min_btn.setToolTip("最小化")
        self.close_btn = QPushButton(_ICON_CLOSE)
        self.close_btn.setToolTip("关闭到托盘")

        for btn in (
            self.prev_btn,
            self.next_btn,
            self.today_btn,
            self.toggle_btn,
            self.lock_btn,
            self.sync_btn,
            self.settings_btn,
            self.min_btn,
            self.close_btn,
        ):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setObjectName("chromeBtn")
            btn.setFixedSize(32, 28)
            btn.setFlat(True)

        self.prev_btn.setIconSize(QSize(16, 16))
        self.next_btn.setIconSize(QSize(16, 16))
        self._refresh_nav_icons()

        bar.addWidget(self.prev_btn)
        bar.addWidget(self.title_label, 1)
        bar.addWidget(self.next_btn)
        bar.addWidget(self.today_btn)
        bar.addSpacing(6)
        bar.addWidget(self.toggle_btn)
        bar.addWidget(self.lock_btn)
        bar.addWidget(self.sync_btn)
        bar.addWidget(self.settings_btn)
        bar.addWidget(self.min_btn)
        bar.addWidget(self.close_btn)

        self.prev_btn.clicked.connect(self._prev)
        self.next_btn.clicked.connect(self._next)
        self.today_btn.clicked.connect(self._go_today)
        self.toggle_btn.clicked.connect(self._toggle_expand)
        self.lock_btn.clicked.connect(self._toggle_size_lock)
        self.sync_btn.clicked.connect(self._on_chrome_sync)
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

    def _refresh_nav_icons(self) -> None:
        color = self._theme.get("text", "#EEF2F6")
        self.prev_btn.setIcon(_browser_nav_icon(forward=False, color=color))
        self.next_btn.setIcon(_browser_nav_icon(forward=True, color=color))

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
        if hasattr(self, "prev_btn"):
            self._refresh_nav_icons()
        if hasattr(self, "todo_list_win"):
            self.todo_list_win.apply_theme(self._theme)

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
        # 隐藏待办但不改「显示今日待办」偏好
        if hasattr(self, "todo_list_win"):
            self._todolist_syncing_vis = True
            try:
                self.todo_list_win.force_hide()
            finally:
                self._todolist_syncing_vis = False
        self.hide()

    def _show_from_tray(self) -> None:
        self._apply_saved_geometry()
        self.show()
        self._geometry_ready = True
        self._pin_to_desktop()
        self._apply_saved_geometry()
        self._place_todolist(force_dock=self._size_locked)
        if bool(self.config.get("todolist_visible", True)):
            self._set_todolist_visible(True, persist=False)

    def _on_about_to_quit(self) -> None:
        self._save_config()
        self._bottom_timer.stop()
        self._clock.stop()
        self._icloud_timer.stop()
        self.holidays.shutdown()
        if hasattr(self, "todo_list_win"):
            self.todo_list_win.force_hide()

    def _quit(self) -> None:
        """Tray/context quit — must fully leave the Qt event loop."""
        self._save_config()
        self._bottom_timer.stop()
        self._clock.stop()
        self._icloud_timer.stop()
        self.holidays.shutdown()
        self.hide()
        if hasattr(self, "todo_list_win"):
            self.todo_list_win.force_hide()
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
        self._dock_todolist()


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

        visible = bool(self.config.get("todolist_visible", True))
        self.config.set("todolist_visible", visible, persist=False)
        if hasattr(self, "todo_list_win"):
            if self._todo_docked or self._size_locked:
                self._apply_todo_dock()
            geo = self.todo_list_win.geometry()
            if geo.width() >= 40 and geo.height() >= 40:
                self.config.set("todolist_x", int(geo.x()), persist=False)
                self.config.set("todolist_y", int(geo.y()), persist=False)
                self.config.set("todolist_w", int(geo.width()), persist=False)
                self.config.set("todolist_h", int(geo.height()), persist=False)
            self.config.set("todolist_docked", bool(self._todo_docked), persist=False)
            self.config.set("todolist_dock_side", self._todo_dock.side, persist=False)
            self.config.set("todolist_dock_offset", int(self._todo_dock.offset), persist=False)

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
            self._dock_todolist()
            event.accept()
            return
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            self._dock_todolist()
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
                self._dock_todolist()
                event.accept()
                return
            if self._drag_pos is not None:
                self._drag_pos = None
                self._save_config()
                self._dock_todolist()
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def _apply_size_lock(self, locked: bool, *, persist: bool = True, apply_size: bool = True) -> None:
        self._size_locked = locked
        self.lock_btn.setText(_ICON_LOCKED if locked else _ICON_UNLOCKED)
        self.lock_btn.setToolTip(
            "已锁定：不可移动、缩放、最小化或关闭；今日待办不可关闭。点击解锁。"
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
        if hasattr(self, "todo_list_win"):
            self.todo_list_win.set_interactive(not locked)
            if locked:
                geo = self.todo_list_win.geometry()
                self.config.set("todolist_w", max(240, int(geo.width())), persist=False)
                self.config.set("todolist_h", max(280, int(geo.height())), persist=False)
                if not self._todo_docked:
                    self._todo_dock = self._default_bottom_dock()
                self._todo_docked = True
                self._dock_todolist()
                # 锁定时保持今日待办可见且不可关
                self.config.set("todolist_visible", True, persist=False)
                self._set_todolist_visible(True, persist=False)
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
        was = self._expanded
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
        if apply_size:
            self._dock_todolist()
        # 切到另一视图时补刷一次（平时只刷新可见视图）
        if was != expanded:
            self.refresh_views()

    def _week_starts_on(self) -> int:
        return normalize_week_starts_on(self.config.get("week_starts_on", 6))

    def _update_title(self) -> None:
        if self._expanded:
            self.title_label.setText(f"{self._month.year}年{self._month.month}月")
        else:
            days = get_week_dates(self._ref, week_starts_on=self._week_starts_on())
            self.title_label.setText(
                f"本周  {days[0].month}/{days[0].day}–{days[-1].month}/{days[-1].day}"
            )
        if hasattr(self, "today_btn"):
            self.today_btn.setEnabled(not self._is_viewing_today())

    def _is_viewing_today(self) -> bool:
        today = date.today()
        if self._expanded:
            return self._month.year == today.year and self._month.month == today.month
        return today in get_week_dates(self._ref, week_starts_on=self._week_starts_on())

    def _ensure_holiday_years(self) -> None:
        years = {self._ref.year, self._month.year}
        week_start = self._week_starts_on()
        for d in get_week_dates(self._ref, week_starts_on=week_start):
            years.add(d.year)
        for week in get_month_grid(
            self._month.year, self._month.month, week_starts_on=week_start
        ):
            for d in week:
                years.add(d.year)
        self.holidays.ensure_years(sorted(years))

    def refresh_views(self) -> None:
        self._update_title()
        # 批量更新，减少勾选过滤时整窗闪烁
        self.setUpdatesEnabled(False)
        week_start = self._week_starts_on()
        try:
            if self._expanded:
                self.month_view.refresh(
                    self._month.year,
                    self._month.month,
                    self.holidays.holidays_for,
                    self._todos_for_date,
                    theme=self._theme,
                    week_starts_on=week_start,
                )
            else:
                self.week_view.refresh(
                    self._ref,
                    self.holidays.holidays_for,
                    self._todos_for_date,
                    theme=self._theme,
                    week_starts_on=week_start,
                )
            self._refresh_todolist_ui()
        finally:
            self.setUpdatesEnabled(True)

    def _todos_for_date(self, day: date):
        """按设置中勾选的日历过滤显示。

        与「是否启用 iCloud 同步」无关：取消勾选即隐藏该日历下的计划；
        无 calendar_id/name 的本地计划始终显示。
        """
        items = self.todos.for_date(day)
        enabled_ids = set(self._icloud_enabled_calendar_ids())
        enabled_names = set(self._icloud_enabled_calendar_names())
        # 用当前日历缓存把启用 id 映射成名称，避免 id 漂移后过滤失效
        for info in self.icloud.list_calendars(use_cache=True):
            if info.id in enabled_ids and info.name:
                enabled_names.add(info.name)
        out = []
        for t in items:
            cid = str(t.get("calendar_id") or "")
            cname = str(t.get("calendar_name") or "").strip()
            if not cid and not cname:
                out.append(t)
            elif cid and cid in enabled_ids:
                out.append(t)
            elif cname and cname in enabled_names:
                out.append(t)
        return out

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

    def _go_today(self) -> None:
        today = date.today()
        self._today_anchor = today
        self._ref = today
        self._month = date(today.year, today.month, 1)
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
        self._icloud_push_plan(item_id)

    def _on_todo_edit(self, day: date, item_id: str) -> None:
        plan = self.todos.get_plan(item_id)
        if not plan:
            return
        item = dict(plan)
        item["series_detail"] = str(plan.get("detail") or "")
        item["day_detail"] = self.todos.day_detail(item_id, day)
        for t in self.todos.for_date(day):
            if t["id"] == item_id:
                item["done"] = t.get("done")
                break
        dlg = TodoEditDialog(
            day,
            item=item,
            calendars=self._calendars_for_todo_dialog(plan=item),
            default_calendar_id=self._effective_default_calendar_id(),
            on_create_calendar=self._create_calendar_sync,
            parent=None,
        )
        if not self._run_todo_dialog(dlg):
            return
        try:
            if dlg.deleted:
                if dlg.delete_mode == DELETE_OCCURRENCE:
                    self.todos.delete_occurrence(item_id, day)
                    self._icloud_push_plan(item_id)
                else:
                    plan = self.todos.get_plan(item_id)
                    uid = str((plan or {}).get("caldav_uid") or "")
                    cal_id = str((plan or {}).get("calendar_id") or "") or None
                    self.todos.delete(item_id)
                    if uid:
                        self._icloud_delete_uid(uid, calendar_id=cal_id)
            else:
                # 先更新标题/日期/重复/日历
                series_detail = None
                if dlg.repeat_mode == REPEAT_NONE:
                    # 单日：备注直接写入 detail
                    series_detail = dlg.day_detail_text
                elif dlg.note_mode == NOTE_MODE_SERIES:
                    series_detail = dlg.detail_text

                self.todos.update(
                    item_id,
                    title=dlg.title_text,
                    start=dlg.plan_date,
                    repeat=dlg.repeat_mode,
                    calendar_id=dlg.calendar_id,
                    calendar_name=dlg.calendar_name,
                    detail=series_detail if series_detail is not None else None,
                )

                if (
                    dlg.repeat_mode != REPEAT_NONE
                    and dlg.note_mode == NOTE_MODE_DAY
                ):
                    # 周期 + 当日备注：拆出当日已完成单日任务，系列跳过该日
                    series_id, new_id = self.todos.complete_recurring_day_as_standalone(
                        item_id, day, detail=dlg.day_detail_text
                    )
                    self.refresh_views()
                    self._icloud_push_plan(series_id)
                    self._icloud_push_plan(new_id)
                    return

                self._icloud_push_plan(item_id)
        except ValueError as exc:
            QMessageBox.warning(self, "提示", str(exc))
            return
        except KeyError:
            return
        self.refresh_views()

    def _checked_calendar_items(self) -> list[dict[str, str]]:
        """当前勾选用于显示过滤的日历（含本地与自定义本地）。"""
        by_id = {c.id: c.name for c in self.icloud.list_calendars(use_cache=True)}
        for info in self._local_calendars_list():
            by_id[info["id"]] = info["name"]
        by_id[LOCAL_CALENDAR_ID] = LOCAL_CALENDAR_NAME
        out: list[dict[str, str]] = []
        seen: set[str] = set()
        for cid in self._icloud_enabled_calendar_ids():
            if cid in seen:
                continue
            seen.add(cid)
            if cid in by_id:
                out.append({"id": cid, "name": by_id[cid]})
            elif is_local_calendar_id(cid):
                out.append({"id": cid, "name": cid})
        return out

    def _effective_default_calendar_id(self) -> str:
        """新建计划默认：在勾选内；全不选时落到本地日历。"""
        enabled = self._icloud_enabled_calendar_ids()
        if not enabled:
            return LOCAL_CALENDAR_ID
        prefer = self._icloud_default_calendar_id()
        if prefer in enabled:
            return prefer
        return enabled[0]

    def _ensure_plan_calendar_visible(
        self,
        calendar_id: str | None,
        calendar_name: str | None = None,
    ) -> None:
        """新建后若该日历未勾选显示，则自动勾上以便主界面可见。"""
        cid = str(calendar_id or "").strip()
        if not cid:
            return
        enabled = list(self._icloud_enabled_calendar_ids())
        if cid in enabled:
            return
        names = list(self._icloud_enabled_calendar_names())
        if is_local_calendar_id(cid):
            if cid == LOCAL_CALENDAR_ID:
                cname = LOCAL_CALENDAR_NAME
            else:
                cname = str(calendar_name or "").strip()
                if not cname:
                    for info in self._local_calendars_list():
                        if info["id"] == cid:
                            cname = info["name"]
                            break
                if not cname:
                    cname = cid
        else:
            cname = str(calendar_name or "").strip()
            if not cname:
                for info in self.icloud.list_calendars(use_cache=True):
                    if info.id == cid:
                        cname = info.name
                        break
        enabled.append(cid)
        if cname and cname not in names:
            names.append(cname)
        self._icloud_set_enabled_calendars(
            enabled,
            default_id=self._effective_default_calendar_id(),
            enabled_names=names,
            refresh=True,
        )

    def _calendars_for_todo_dialog(
        self, *, plan: dict | None = None
    ) -> list[dict[str, str]]:
        """新建/编辑对话框可选日历。全不选时新建仅本地；有勾选时仅已勾选。"""
        if plan is None:
            if not self._icloud_enabled_calendar_ids():
                return [{"id": LOCAL_CALENDAR_ID, "name": LOCAL_CALENDAR_NAME}]
            return self._checked_calendar_items()
        out = self._checked_calendar_items()
        seen = {x["id"] for x in out}
        pcid = str(plan.get("calendar_id") or "")
        pname = str(plan.get("calendar_name") or "").strip()
        if pcid and pcid not in seen:
            if is_local_calendar_id(pcid):
                out.append({"id": LOCAL_CALENDAR_ID, "name": LOCAL_CALENDAR_NAME})
            elif pname:
                out.append({"id": pcid, "name": pname})
        if not out:
            if is_local_calendar_id(pcid):
                return [{"id": LOCAL_CALENDAR_ID, "name": LOCAL_CALENDAR_NAME}]
            if pcid and pname:
                return [{"id": pcid, "name": pname}]
            return [{"id": LOCAL_CALENDAR_ID, "name": LOCAL_CALENDAR_NAME}]
        return out

    def _run_todo_dialog(self, dlg: TodoEditDialog) -> bool:
        """打开计划对话框（置顶，避免嵌桌面后模态框被挡住像卡死）。"""
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        if hasattr(self, "_bottom_timer"):
            self._bottom_timer.stop()
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            ag = screen.availableGeometry()
            dlg.adjustSize()
            geo = dlg.frameGeometry()
            geo.moveCenter(ag.center())
            dlg.move(geo.topLeft())
        dlg.show()
        _ = dlg.winId()
        try:
            set_topmost(int(dlg.winId()), True)
        except Exception:  # noqa: BLE001
            pass
        dlg.raise_()
        dlg.activateWindow()
        accepted = dlg.exec() == TodoEditDialog.DialogCode.Accepted
        try:
            set_topmost(int(dlg.winId()), False)
        except Exception:  # noqa: BLE001
            pass
        if hasattr(self, "_bottom_timer") and self.isVisible():
            self._bottom_timer.start()
            self._pin_to_desktop()
        return accepted

    def _on_day_add(self, day: date) -> None:
        try:
            cals = self._calendars_for_todo_dialog()
            if not cals:
                box = QMessageBox(self)
                box.setWindowTitle("提示")
                box.setText(
                    "当前没有可用日历。\n"
                    "请先在设置 → iCloud 中校验账号，并勾选至少一个日历"
                    "（或保留「新建计划默认」）。"
                )
                box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
                box.show()
                try:
                    set_topmost(int(box.winId()), True)
                except Exception:  # noqa: BLE001
                    pass
                box.exec()
                return
            dlg = TodoEditDialog(
                day,
                calendars=cals,
                default_calendar_id=self._effective_default_calendar_id(),
                on_create_calendar=self._create_calendar_sync,
                parent=None,
            )
            if not self._run_todo_dialog(dlg):
                return
            if dlg.repeat_mode == REPEAT_NONE:
                detail = dlg.day_detail_text
            else:
                detail = dlg.detail_text
            plan = self.todos.add(
                dlg.plan_date,
                dlg.title_text,
                repeat=dlg.repeat_mode,
                calendar_id=dlg.calendar_id,
                calendar_name=dlg.calendar_name,
                detail=detail,
            )
            self._ensure_plan_calendar_visible(dlg.calendar_id, dlg.calendar_name)
            if (
                dlg.repeat_mode != REPEAT_NONE
                and dlg.note_mode == NOTE_MODE_DAY
            ):
                series_id, new_id = self.todos.complete_recurring_day_as_standalone(
                    plan["id"], dlg.plan_date, detail=dlg.day_detail_text
                )
                self.refresh_views()
                self._icloud_push_plan(series_id)
                self._icloud_push_plan(new_id)
                return
            self.refresh_views()
            self._icloud_push_plan(plan["id"])
        except Exception as exc:  # noqa: BLE001
            import traceback
            from pathlib import Path

            try:
                Path("userdata/crash.log").write_text(
                    traceback.format_exc(), encoding="utf-8"
                )
            except OSError:
                pass
            box = QMessageBox(None)
            box.setWindowTitle("添加计划失败")
            box.setText(str(exc))
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            box.show()
            try:
                set_topmost(int(box.winId()), True)
            except Exception:  # noqa: BLE001
                pass
            box.exec()

    def _on_chrome_sync(self) -> None:
        if not self._icloud_enabled():
            QMessageBox.information(
                self,
                "同步",
                "请先在设置中启用 iCloud 同步，并填写 Apple ID 与应用专用密码。",
            )
            return
        creds = self.icloud.load_credentials()
        if not creds.get("apple_id") or not creds.get("app_password"):
            QMessageBox.information(self, "同步", "未找到 iCloud 凭证，请先在设置中连接。")
            return
        self.sync_btn.setEnabled(False)
        self.sync_btn.setToolTip("同步中…")

        def done(ok: bool, msg: str) -> None:
            self.sync_btn.setEnabled(True)
            self.sync_btn.setToolTip("立即同步 iCloud")
            if ok:
                QMessageBox.information(self, "同步完成", msg)
            else:
                QMessageBox.warning(self, "同步失败", msg)

        self._icloud_sync_now_from_dialog(
            {
                "enabled": True,
                "apple_id": creds.get("apple_id", ""),
                "app_password": creds.get("app_password", ""),
                "calendar_name": self._icloud_calendar_name(),
                "calendars_enabled": self._icloud_enabled_calendar_ids(),
                "default_calendar_id": self._icloud_default_calendar_id(),
                "poll_seconds": int(self.config.get("icloud_poll_seconds", 45) or 45),
            },
            done,
        )

    def _ensure_local_calendar_config(self) -> None:
        """保证「本地日历」可用：无默认时落到本地；无 Apple 且未勾选任何日历时默认勾选本地。"""
        changed = False
        enabled = self._icloud_enabled_calendar_ids()
        names = self._icloud_enabled_calendar_names()
        default_id = self._icloud_default_calendar_id()
        creds = self.icloud.load_credentials()
        has_apple = bool(
            str(creds.get("apple_id", "")).strip()
            and str(creds.get("app_password", "")).strip()
        )
        if not default_id:
            self.config.set("icloud_default_calendar_id", LOCAL_CALENDAR_ID, persist=False)
            changed = True
            default_id = LOCAL_CALENDAR_ID
        cal_name = str(self.config.get("icloud_calendar_name", "") or "").strip()
        if not cal_name or (
            default_id == LOCAL_CALENDAR_ID and cal_name == DEFAULT_CALENDAR_NAME
        ):
            self.config.set("icloud_calendar_name", LOCAL_CALENDAR_NAME, persist=False)
            changed = True
        if not enabled and not has_apple:
            self.config.set(
                "icloud_calendars_enabled", [LOCAL_CALENDAR_ID], persist=False
            )
            self.config.set(
                "icloud_calendars_enabled_names", [LOCAL_CALENDAR_NAME], persist=False
            )
            self.config.set("icloud_default_calendar_id", LOCAL_CALENDAR_ID, persist=False)
            self.config.set("icloud_calendar_name", LOCAL_CALENDAR_NAME, persist=False)
            changed = True
        elif LOCAL_CALENDAR_ID in enabled and LOCAL_CALENDAR_NAME not in names:
            names = list(names) + [LOCAL_CALENDAR_NAME]
            self.config.set("icloud_calendars_enabled_names", names, persist=False)
            changed = True
        # 升级：若尚无「同步」列表，把已勾选的非本地日历视为默认同步
        if "icloud_calendars_sync" not in self.config.session:
            sync_ids = [
                cid
                for cid in self._icloud_enabled_calendar_ids()
                if not is_local_calendar_id(cid)
            ]
            sync_names = []
            live = {c.id: c.name for c in self.icloud.list_calendars(use_cache=True)}
            en_names = self._icloud_enabled_calendar_names()
            en_ids = self._icloud_enabled_calendar_ids()
            for cid in sync_ids:
                if cid in live:
                    sync_names.append(live[cid])
                else:
                    try:
                        sync_names.append(en_names[en_ids.index(cid)])
                    except (ValueError, IndexError):
                        sync_names.append(cid)
            self.config.set("icloud_calendars_sync", sync_ids, persist=False)
            self.config.set("icloud_calendars_sync_names", sync_names, persist=False)
            changed = True
        if changed:
            self.config.save()

    def _icloud_enabled(self) -> bool:
        return bool(self.config.get("icloud_sync_enabled", False))

    def _icloud_calendar_name(self) -> str:
        name = str(self.config.get("icloud_calendar_name", LOCAL_CALENDAR_NAME) or "")
        return name.strip() or LOCAL_CALENDAR_NAME

    def _icloud_default_calendar_id(self) -> str:
        return str(
            self.config.get("icloud_default_calendar_id", LOCAL_CALENDAR_ID) or ""
        ) or LOCAL_CALENDAR_ID

    def _icloud_enabled_calendar_ids(self) -> list[str]:
        raw = self.config.get("icloud_calendars_enabled", [])
        if isinstance(raw, list):
            return [str(x) for x in raw if x]
        return []

    def _icloud_enabled_calendar_names(self) -> list[str]:
        raw = self.config.get("icloud_calendars_enabled_names", [])
        if isinstance(raw, list):
            return [str(x) for x in raw if x]
        return []

    def _icloud_sync_calendar_ids(self) -> list[str]:
        raw = self.config.get("icloud_calendars_sync", [])
        if isinstance(raw, list):
            return [str(x) for x in raw if x and not is_local_calendar_id(str(x))]
        return []

    def _icloud_sync_calendar_names(self) -> list[str]:
        raw = self.config.get("icloud_calendars_sync_names", [])
        if isinstance(raw, list):
            return [str(x) for x in raw if x]
        return []

    def _local_calendars_list(self) -> list[dict[str, str]]:
        raw = self.config.get("local_calendars", [])
        out: list[dict[str, str]] = []
        if not isinstance(raw, list):
            return out
        for it in raw:
            if not isinstance(it, dict):
                continue
            cid = str(it.get("id") or "").strip()
            name = str(it.get("name") or "").strip()
            if cid and name and is_local_calendar_id(cid) and cid != LOCAL_CALENDAR_ID:
                out.append({"id": cid, "name": name})
        return out

    def _icloud_set_enabled_calendars(
        self,
        enabled_ids: list[str],
        *,
        default_id: str = "",
        enabled_names: list[str] | None = None,
        sync_ids: list[str] | None = None,
        sync_names: list[str] | None = None,
        refresh: bool = True,
    ) -> None:
        """写入显示勾选与同步勾选，并刷新视图。"""
        cleaned = [str(x) for x in enabled_ids if x]
        names = [str(n) for n in (enabled_names or []) if n]
        if not names:
            live = {c.id: c.name for c in self.icloud.list_calendars(use_cache=True)}
            for info in self._local_calendars_list():
                live[info["id"]] = info["name"]
            live[LOCAL_CALENDAR_ID] = LOCAL_CALENDAR_NAME
            names = [live[cid] for cid in cleaned if cid in live and live[cid]]
        self.config.set("icloud_calendars_enabled", cleaned, persist=False)
        self.config.set("icloud_calendars_enabled_names", names, persist=False)
        if sync_ids is not None:
            sync_clean = [
                str(x) for x in sync_ids if x and not is_local_calendar_id(str(x))
            ]
            snames = [str(n) for n in (sync_names or []) if n]
            if not snames:
                live = {c.id: c.name for c in self.icloud.list_calendars(use_cache=True)}
                snames = [live[cid] for cid in sync_clean if cid in live and live[cid]]
            self.config.set("icloud_calendars_sync", sync_clean, persist=False)
            self.config.set("icloud_calendars_sync_names", snames, persist=False)
        prefer = str(default_id or "")
        if cleaned:
            # 有显示勾选时：默认日历必须落在勾选内
            if prefer and prefer not in cleaned:
                prefer = cleaned[0]
            elif not prefer:
                prefer = cleaned[0]
            self.config.set("icloud_default_calendar_id", prefer, persist=False)
            name = next(
                (n for cid, n in zip(cleaned, names) if cid == prefer and n),
                "",
            )
            if not name:
                for info in self.icloud.list_calendars(use_cache=True):
                    if info.id == prefer:
                        name = info.name
                        break
                if not name:
                    for info in self._local_calendars_list():
                        if info["id"] == prefer:
                            name = info["name"]
                            break
                if prefer == LOCAL_CALENDAR_ID:
                    name = LOCAL_CALENDAR_NAME
            if name:
                self.config.set("icloud_calendar_name", name, persist=False)
        else:
            # 显示过滤全空：新建默认固定为本地日历
            if not prefer or not is_local_calendar_id(prefer):
                prefer = LOCAL_CALENDAR_ID
            self.config.set("icloud_default_calendar_id", prefer, persist=False)
            self.config.set("icloud_calendar_name", LOCAL_CALENDAR_NAME, persist=False)
        self.config.save()
        self._remap_plans_calendar_ids_by_name()
        if refresh:
            self.refresh_views()

    def _remap_plans_calendar_ids_by_name(self) -> None:
        """按日历显示名把本地计划的 calendar_id 对齐到当前 live id。"""
        live_by_name = {
            c.name: c for c in self.icloud.list_calendars(use_cache=True) if c.name
        }
        if not live_by_name:
            return
        for plan in self.todos.all_plans():
            if is_local_plan(plan):
                continue
            cname = str(plan.get("calendar_name") or "").strip()
            if not cname or cname not in live_by_name:
                continue
            if cname == LOCAL_CALENDAR_NAME:
                continue
            info = live_by_name[cname]
            if str(plan.get("calendar_id") or "") == info.id:
                continue
            try:
                self.todos.set_calendar(
                    str(plan["id"]),
                    calendar_id=info.id,
                    calendar_name=info.name,
                    touch=False,
                )
            except KeyError:
                continue

    def _realign_enabled_ids_to_live(
        self,
        live_ids: set[str],
        *,
        names_hint: set[str] | None = None,
    ) -> list[str]:
        """刷新后把启用列表对齐到 live id（保留名称勾选语义）。"""
        by_id = {
            c.id: c
            for c in self.icloud.list_calendars(use_cache=True)
            if not live_ids or c.id in live_ids
        }
        by_name = {c.name: c for c in by_id.values() if c.name}
        enabled = self._icloud_enabled_calendar_ids()
        names = set(self._icloud_enabled_calendar_names())
        if names_hint:
            names |= {str(n) for n in names_hint if n}
        out: list[str] = []
        seen: set[str] = set()
        for cid in enabled:
            if is_local_calendar_id(cid):
                if cid not in seen:
                    out.append(cid)
                    seen.add(cid)
                    names.add(LOCAL_CALENDAR_NAME)
                continue
            if cid in by_id and cid not in seen:
                out.append(cid)
                seen.add(cid)
                if by_id[cid].name:
                    names.add(by_id[cid].name)
        for n in list(names):
            if n == LOCAL_CALENDAR_NAME:
                if LOCAL_CALENDAR_ID not in seen:
                    out.append(LOCAL_CALENDAR_ID)
                    seen.add(LOCAL_CALENDAR_ID)
                continue
            info = by_name.get(n)
            if info and info.id not in seen:
                out.append(info.id)
                seen.add(info.id)
        # 对不上任何 live 时绝不能写成空（否则视图会像「全没勾选」）
        if enabled and not out:
            return enabled
        name_list = []
        for cid in out:
            if is_local_calendar_id(cid):
                name_list.append(LOCAL_CALENDAR_NAME)
            elif cid in by_id and by_id[cid].name:
                name_list.append(by_id[cid].name)
        if not name_list:
            name_list = sorted(names)
        if out != enabled or name_list != self._icloud_enabled_calendar_names():
            self._icloud_set_enabled_calendars(
                out,
                default_id=self._icloud_default_calendar_id(),
                enabled_names=name_list,
                refresh=False,
            )
        return out

    def _create_calendar_sync(self, name: str) -> dict[str, str]:
        """在计划对话框中创建日历：无 Apple 时建本地；有账号时建 iCloud。"""
        name = (name or "").strip()
        if not name or name == LOCAL_CALENDAR_NAME or is_local_calendar_id(name):
            return {"id": LOCAL_CALENDAR_ID, "name": LOCAL_CALENDAR_NAME}
        creds = self.icloud.load_credentials()
        has_apple = bool(
            str(creds.get("apple_id", "")).strip()
            and str(creds.get("app_password", "")).strip()
        )
        if not has_apple:
            new_id = make_local_calendar_id(name)
            extras = self._local_calendars_list()
            if not any(x["id"] == new_id for x in extras):
                extras.append({"id": new_id, "name": name})
                self.config.set("local_calendars", extras, persist=False)
            enabled = self._icloud_enabled_calendar_ids()
            if new_id not in enabled:
                enabled.append(new_id)
                names = self._icloud_enabled_calendar_names()
                if name not in names:
                    names.append(name)
                self.config.set("icloud_calendars_enabled", enabled, persist=False)
                self.config.set("icloud_calendars_enabled_names", names, persist=False)
            self.config.save()
            return {"id": new_id, "name": name}
        self.icloud.ensure_connected()
        info = self.icloud.ensure_calendar(name)
        enabled = self._icloud_enabled_calendar_ids()
        if info.id not in enabled:
            enabled.append(info.id)
            self.config.set("icloud_calendars_enabled", enabled, persist=False)
            if not self._icloud_default_calendar_id():
                self.config.set("icloud_default_calendar_id", info.id, persist=False)
            sync = self._icloud_sync_calendar_ids()
            if info.id not in sync:
                sync.append(info.id)
                self.config.set("icloud_calendars_sync", sync, persist=False)
            self.config.save()
        return {"id": info.id, "name": info.name}

    def _reload_icloud_timer(self) -> None:
        self._icloud_timer.stop()
        if not self._icloud_enabled():
            return
        seconds = int(self.config.get("icloud_poll_seconds", 45) or 45)
        seconds = max(15, min(600, seconds))
        self._icloud_timer.setInterval(seconds * 1000)
        self._icloud_timer.start()
        QTimer.singleShot(2500, self._icloud_poll)

    def _run_icloud_job(self, fn, on_ok=None, on_err=None) -> None:  # noqa: ANN001
        job = _ICloudJob(fn, self)

        def _ok(result: object) -> None:
            if job in self._icloud_jobs:
                self._icloud_jobs.remove(job)
            if on_ok:
                on_ok(result)

        def _err(msg: str) -> None:
            if job in self._icloud_jobs:
                self._icloud_jobs.remove(job)
            if on_err:
                on_err(msg)

        job.finished_ok.connect(_ok)
        job.finished_err.connect(_err)
        self._icloud_jobs.append(job)
        job.start()

    def _prepare_plan_for_push(self, plan: dict) -> dict:
        """补全默认日历字段后返回副本。本地日历计划原样返回，不改写。"""
        p = dict(plan)
        if is_local_plan(p):
            p["calendar_id"] = LOCAL_CALENDAR_ID
            p["calendar_name"] = LOCAL_CALENDAR_NAME
            return p
        if not p.get("calendar_id") and not p.get("calendar_name"):
            default_id = self._icloud_default_calendar_id()
            if default_id:
                p["calendar_id"] = default_id
            p["calendar_name"] = self._icloud_calendar_name()
        return p

    def _icloud_push_plan(self, item_id: str) -> None:
        if not self._icloud_enabled():
            return
        plan = self.todos.get_plan(item_id)
        if not plan or is_local_plan(plan):
            return
        plan = self._prepare_plan_for_push(plan)
        if is_local_plan(plan):
            return
        sync_ids = set(self._icloud_sync_calendar_ids())
        sync_names = set(self._icloud_sync_calendar_names())
        cid = str(plan.get("calendar_id") or "")
        cname = str(plan.get("calendar_name") or "").strip()
        if sync_ids or sync_names:
            if cid and cid not in sync_ids and cname not in sync_names:
                return
            if not cid and cname and cname not in sync_names:
                return

        def work():
            self.icloud.ensure_connected()
            try:
                uid, etag = self.icloud.upsert_plan(plan)
            except RuntimeError:
                # 目标日历已在 iCloud 删除：不自动新建
                return None
            return item_id, uid, etag, plan.get("calendar_id"), plan.get("calendar_name")

        def ok(result: object) -> None:
            if not isinstance(result, tuple) or len(result) != 5:
                return
            pid, uid, etag, cid, cname = result
            try:
                self.todos.set_caldav_meta(str(pid), caldav_uid=str(uid), caldav_etag=etag)
                if cid:
                    self.todos.set_calendar(
                        str(pid),
                        calendar_id=str(cid),
                        calendar_name=str(cname) if cname else None,
                        touch=False,
                    )
            except KeyError:
                return

        self._run_icloud_job(work, on_ok=ok)

    def _icloud_delete_uid(self, uid: str, *, calendar_id: str | None = None) -> None:
        if not self._icloud_enabled() or not uid:
            return
        if is_local_calendar_id(calendar_id):
            return

        def work():
            self.icloud.ensure_connected()
            self.icloud.delete_event(uid, calendar_id=calendar_id)
            return True

        self._run_icloud_job(work)

    def _prune_enabled_calendars(self, live_ids: set[str]) -> list[str]:
        """去掉 iCloud 上已不存在的启用日历，避免反复按名重建。保留本地日历。"""
        enabled = self._icloud_enabled_calendar_ids()
        # 刷新失败得到空集合时绝不清空本地勾选
        if not live_ids:
            return enabled
        pruned = [
            cid
            for cid in enabled
            if is_local_calendar_id(cid) or cid in live_ids
        ]
        # 若一个都匹配不上，更可能是临时异常，保留原勾选
        if enabled and not pruned:
            return enabled
        changed = pruned != enabled
        if changed:
            self.config.set("icloud_calendars_enabled", pruned, persist=False)
        default_id = str(self.config.get("icloud_default_calendar_id") or "")
        if default_id and not is_local_calendar_id(default_id) and default_id not in live_ids:
            self.config.set(
                "icloud_default_calendar_id",
                pruned[0] if pruned else LOCAL_CALENDAR_ID,
                persist=False,
            )
            changed = True
        if changed:
            self.config.save()
        return pruned

    def _icloud_poll(self) -> None:
        if not self._icloud_enabled():
            return
        enabled_ids = self._icloud_sync_calendar_ids()
        enabled_names = self._icloud_sync_calendar_names()
        plans_to_push = [
            self._prepare_plan_for_push(p)
            for p in self.todos.all_plans()
            if plan_needs_push(p) and not is_local_plan(p)
        ]

        def work():
            self.icloud.ensure_connected()
            cals = self.icloud.refresh_calendars()
            live_ids = {c.id for c in cals}
            by_name = {c.name: c.id for c in cals if c.name}
            # 按 id，再按名称补齐（防止 id 漂移后 active 变空触发误删）
            # 本地日历不参与 CalDAV active 范围
            active: list[str] = []
            seen: set[str] = set()
            for cid in enabled_ids:
                if is_local_calendar_id(cid):
                    continue
                if cid in live_ids and cid not in seen:
                    active.append(cid)
                    seen.add(cid)
            for n in enabled_names:
                if str(n) == LOCAL_CALENDAR_NAME:
                    continue
                cid = by_name.get(str(n))
                if cid and cid not in seen:
                    active.append(cid)
                    seen.add(cid)
            meta: list[tuple[str, str, str | None, str | None, str | None]] = []
            for plan in plans_to_push:
                if is_local_plan(plan):
                    continue
                cid = str(plan.get("calendar_id") or "")
                if is_local_calendar_id(cid):
                    continue
                if active and cid and cid not in active:
                    continue
                if active and cid and cid not in live_ids:
                    continue
                try:
                    uid, etag = self.icloud.upsert_plan(plan)
                except RuntimeError:
                    # 所属日历已删：跳过，不自动新建
                    continue
                meta.append(
                    (
                        str(plan["id"]),
                        str(uid),
                        etag,
                        plan.get("calendar_id"),
                        plan.get("calendar_name"),
                    )
                )
            remotes = []
            scope: set[str] = set()
            for cid in active:
                try:
                    remotes.extend(self.icloud.list_events(cid))
                    scope.add(cid)
                except Exception:  # noqa: BLE001
                    continue
            return meta, remotes, scope, live_ids

        def ok(result: object) -> None:
            if not isinstance(result, tuple) or len(result) != 4:
                return
            meta, remotes, scope, live_ids = result
            live_set = set(live_ids) if isinstance(live_ids, set) else set(live_ids or [])
            self._prune_enabled_calendars(live_set)
            self._realign_enabled_ids_to_live(live_set)
            for row in meta:
                pid, uid, etag, cid, cname = row
                try:
                    self.todos.set_caldav_meta(pid, caldav_uid=uid, caldav_etag=etag)
                    if cid:
                        self.todos.set_calendar(
                            pid,
                            calendar_id=str(cid),
                            calendar_name=str(cname) if cname else None,
                            touch=False,
                        )
                except KeyError:
                    continue
            scope_ids = scope if isinstance(scope, set) else set(scope or [])
            self.icloud.reconcile_with_remotes(
                self.todos, remotes, scope_calendar_ids=scope_ids
            )
            # prune/realign 也可能改了启用列表，统一刷新显示
            self.refresh_views()

        self._run_icloud_job(work, on_ok=ok)

    def _icloud_apply_settings_dict(self, data: dict) -> None:
        apple_id = str(data.get("apple_id", "")).strip()
        password = str(data.get("app_password", "")).strip()
        old = self.icloud.load_credentials()
        creds_changed = (
            str(old.get("apple_id", "")).strip() != apple_id
            or str(old.get("app_password", "")).strip() != password
        )
        if apple_id and password:
            self.icloud.save_credentials(apple_id=apple_id, app_password=password)
        else:
            self.icloud.clear_credentials()
            creds_changed = True
        # 仅账号变更时断开；勾选日历变化不断开，避免刷新后 id 漂移把启用列表写空
        if creds_changed:
            self.icloud.disconnect()
        self.config.set("icloud_sync_enabled", bool(data.get("enabled", False)), persist=False)
        self.config.set(
            "icloud_calendar_name",
            str(data.get("calendar_name", DEFAULT_CALENDAR_NAME) or DEFAULT_CALENDAR_NAME),
            persist=False,
        )
        enabled = data.get("calendars_enabled")
        if isinstance(enabled, list):
            cleaned = [str(x) for x in enabled if x]
            # UI 传入的列表一律采信（含故意清空）；勿因「空」跳过而留下旧勾选
            names_from_ui = [
                str(n)
                for n in (data.get("calendars_enabled_names") or [])
                if n
            ]
            live = {c.id: c.name for c in self.icloud.list_calendars(use_cache=True)}
            names = names_from_ui or [
                live[cid] for cid in cleaned if cid in live and live[cid]
            ]
            # 名称仍空时保留旧名称，供后续按名 realign（仅在仍有 id 时）
            if not names and cleaned:
                names = list(self._icloud_enabled_calendar_names())
            # 补齐本地日历名称
            if LOCAL_CALENDAR_ID in cleaned and LOCAL_CALENDAR_NAME not in names:
                names = list(names) + [LOCAL_CALENDAR_NAME]
            self.config.set("icloud_calendars_enabled", cleaned, persist=False)
            self.config.set("icloud_calendars_enabled_names", names, persist=False)
            self._remap_plans_calendar_ids_by_name()
        sync_raw = data.get("calendars_sync")
        if isinstance(sync_raw, list):
            sync_clean = [
                str(x) for x in sync_raw if x and not is_local_calendar_id(str(x))
            ]
            sync_names = [
                str(n) for n in (data.get("calendars_sync_names") or []) if n
            ]
            if not sync_names:
                live = {c.id: c.name for c in self.icloud.list_calendars(use_cache=True)}
                sync_names = [
                    live[cid] for cid in sync_clean if cid in live and live[cid]
                ]
            self.config.set("icloud_calendars_sync", sync_clean, persist=False)
            self.config.set("icloud_calendars_sync_names", sync_names, persist=False)
        locals_raw = data.get("local_calendars")
        if isinstance(locals_raw, list):
            self.config.set(
                "local_calendars",
                [
                    {"id": str(it.get("id")), "name": str(it.get("name"))}
                    for it in locals_raw
                    if isinstance(it, dict) and it.get("id") and it.get("name")
                ],
                persist=False,
            )
        cleaned_enabled = [
            str(x) for x in (data.get("calendars_enabled") or []) if x
        ]
        default_id = str(data.get("default_calendar_id") or "")
        if not cleaned_enabled:
            self.config.set("icloud_default_calendar_id", LOCAL_CALENDAR_ID, persist=False)
            self.config.set("icloud_calendar_name", LOCAL_CALENDAR_NAME, persist=False)
        elif default_id:
            self.config.set("icloud_default_calendar_id", default_id, persist=False)
        elif not self._icloud_default_calendar_id():
            self.config.set("icloud_default_calendar_id", "", persist=False)
        self.config.set(
            "icloud_poll_seconds",
            int(data.get("poll_seconds", 45) or 45),
            persist=False,
        )
        # 无账号时：日历列表缓存只保留当前勾选（不含本地），避免设置里仍显示全部 iCloud 名
        if not apple_id or not password:
            slim: list[dict[str, str]] = []
            ids = self._icloud_enabled_calendar_ids()
            names = self._icloud_enabled_calendar_names()
            name_by_id = {
                c.id: c.name for c in self.icloud.list_calendars(use_cache=True)
            }
            for i, cid in enumerate(ids):
                if is_local_calendar_id(cid):
                    continue
                cname = ""
                if i < len(names):
                    cname = str(names[i] or "").strip()
                if not cname:
                    cname = name_by_id.get(cid, "") or cid
                if cname == LOCAL_CALENDAR_NAME and not is_local_calendar_id(cid):
                    # 名称列表错位时再用缓存名
                    cname = name_by_id.get(cid, cname)
                slim.append({"id": cid, "name": cname, "writable": "1"})
            self.icloud.replace_calendars_cache(slim)
        self.config.save()
        self._reload_icloud_timer()

    def _calendars_for_settings_ui(self) -> list[dict[str, str]]:
        """设置页日历列表：已登录显示缓存全部；未登录只显示本地+当前勾选。"""
        creds = self.icloud.load_credentials()
        has_apple = bool(
            str(creds.get("apple_id", "")).strip()
            and str(creds.get("app_password", "")).strip()
        )
        extras = self._local_calendars_list()
        try:
            cached = [
                {"id": c.id, "name": c.name, "writable": str(int(bool(c.writable)))}
                for c in self.icloud.list_calendars(use_cache=True)
            ]
        except Exception:  # noqa: BLE001
            cached = []
        if has_apple:
            return with_local_calendar(cached, extra_local=extras)
        enabled_ids = self._icloud_enabled_calendar_ids()
        enabled_names = self._icloud_enabled_calendar_names()
        by_id = {str(c.get("id")): c for c in cached if c.get("id")}
        out: list[dict[str, str]] = []
        seen: set[str] = set()
        for i, cid in enumerate(enabled_ids):
            if is_local_calendar_id(cid) or cid in seen:
                continue
            seen.add(cid)
            if cid in by_id:
                out.append(by_id[cid])
                continue
            cname = enabled_names[i] if i < len(enabled_names) else cid
            out.append({"id": cid, "name": str(cname or cid), "writable": "1"})
        return with_local_calendar(out, extra_local=extras)

    def _icloud_test_from_dialog(self, data: dict, done) -> None:  # noqa: ANN001
        self._icloud_apply_settings_dict({**data, "enabled": True})

        def work():
            return self.icloud.test_connection()

        def ok(result: object) -> None:
            done(True, str(result))

        def err(msg: str) -> None:
            done(False, msg)

        self._run_icloud_job(work, on_ok=ok, on_err=err)

    def _icloud_refresh_calendars_from_dialog(self, data: dict, done) -> None:  # noqa: ANN001
        self._icloud_apply_settings_dict({**data, "enabled": data.get("enabled", True)})

        def work():
            cals = self.icloud.refresh_calendars()
            live_ids = {c.id for c in cals}
            enabled = [str(x) for x in (data.get("calendars_enabled") or []) if x]
            # 去掉已在 iCloud 删除的；保留本地日历勾选
            enabled = [
                cid
                for cid in enabled
                if is_local_calendar_id(cid) or cid in live_ids
            ]
            return [
                {"id": c.id, "name": c.name, "writable": c.writable} for c in cals
            ], enabled, live_ids

        def ok(result: object) -> None:
            if not isinstance(result, tuple) or len(result) < 2:
                done(False, "刷新返回异常")
                return
            cals, enabled = result[0], result[1]
            live_ids = result[2] if len(result) > 2 else {
                str(x.get("id")) for x in cals if isinstance(x, dict) and x.get("id")
            }
            self._prune_enabled_calendars(set(live_ids))
            self._realign_enabled_ids_to_live(set(live_ids) if isinstance(live_ids, set) else set(live_ids or []))
            done(True, cals)

        def err(msg: str) -> None:
            done(False, msg)

        self._run_icloud_job(work, on_ok=ok, on_err=err)

    def _icloud_create_calendar_from_dialog(self, data: dict, name: str, done) -> None:  # noqa: ANN001
        """新建日历：无 Apple 时建本地日历；有账号时建 iCloud 日历。"""
        name = (name or "").strip()
        preserved = [str(x) for x in (data.get("calendars_enabled") or []) if x]
        preserved_names = {
            str(n).strip()
            for n in (data.get("calendars_enabled_names") or [])
            if str(n).strip()
        }
        sync_preserved = [
            str(x)
            for x in (data.get("calendars_sync") or [])
            if x and not is_local_calendar_id(str(x))
        ]
        if not preserved:
            preserved = self._icloud_enabled_calendar_ids()
        if preserved and not preserved_names:
            for info in self.icloud.list_calendars(use_cache=True):
                if info.id in preserved and info.name:
                    preserved_names.add(info.name)
            for info in self._local_calendars_list():
                if info["id"] in preserved:
                    preserved_names.add(info["name"])
        default_id = str(data.get("default_calendar_id") or "") or self._icloud_default_calendar_id()
        apple_id = str(data.get("apple_id", "")).strip()
        password = str(data.get("app_password", "")).strip()
        create_local = bool(data.get("create_local")) or not (apple_id and password)

        if create_local:
            new_id = make_local_calendar_id(name)
            extras = self._local_calendars_list()
            if not any(x["id"] == new_id for x in extras):
                extras.append({"id": new_id, "name": name})
                self.config.set("local_calendars", extras, persist=False)
                self.config.save()
            enabled = list(dict.fromkeys([*preserved, new_id, LOCAL_CALENDAR_ID]))
            names_out = sorted(preserved_names | {name, LOCAL_CALENDAR_NAME})
            cached = self._calendars_for_settings_ui()
            done(
                True,
                {
                    "calendars": cached,
                    "local_calendars": extras,
                    "enabled": enabled,
                    "enabled_names": names_out,
                    "sync": sync_preserved,
                    "new_id": new_id,
                    "new_name": name,
                    "default_id": new_id,
                },
            )
            return

        if apple_id and password:
            self.icloud.save_credentials(apple_id=apple_id, app_password=password)

        def work():
            self.icloud.ensure_connected()
            self.icloud.ensure_calendar(name)
            cals = self.icloud.refresh_calendars()
            live = {c.id: c for c in cals}
            by_name = {c.name: c for c in cals}
            new_info = by_name.get(name.strip())
            new_id = new_info.id if new_info else ""
            new_name = new_info.name if new_info else name.strip()

            enabled: list[str] = []
            seen: set[str] = set()
            names_out = set(preserved_names)
            for cid in preserved:
                if is_local_calendar_id(cid):
                    if cid not in seen:
                        enabled.append(cid)
                        seen.add(cid)
                        names_out.add(
                            LOCAL_CALENDAR_NAME
                            if cid == LOCAL_CALENDAR_ID
                            else next(
                                (
                                    x["name"]
                                    for x in self._local_calendars_list()
                                    if x["id"] == cid
                                ),
                                cid,
                            )
                        )
                    continue
                if cid in live and cid not in seen:
                    enabled.append(cid)
                    seen.add(cid)
                    names_out.add(live[cid].name)
            for cname in list(preserved_names):
                info = by_name.get(cname)
                if info and info.id not in seen:
                    enabled.append(info.id)
                    seen.add(info.id)
                    names_out.add(info.name)
            if new_id and new_id not in seen:
                enabled.append(new_id)
                seen.add(new_id)
            if new_name:
                names_out.add(new_name)

            prefer = default_id if default_id in seen else ""
            if not prefer and enabled:
                prefer = enabled[0]
            sync_out = list(sync_preserved)
            if new_id and new_id not in sync_out:
                sync_out.append(new_id)

            cal_list = [
                {"id": c.id, "name": c.name, "writable": c.writable} for c in cals
            ]
            return {
                "calendars": with_local_calendar(
                    cal_list, extra_local=self._local_calendars_list()
                ),
                "local_calendars": self._local_calendars_list(),
                "enabled": enabled,
                "enabled_names": sorted(names_out),
                "sync": sync_out,
                "new_id": new_id,
                "new_name": new_name,
                "default_id": prefer,
            }

        def ok(result: object) -> None:
            if not isinstance(result, dict):
                done(False, "创建返回异常")
                return
            enabled = [str(x) for x in (result.get("enabled") or []) if x]
            prefer = str(result.get("default_id") or "")
            names = [str(n) for n in (result.get("enabled_names") or []) if n]
            sync_ids = [str(x) for x in (result.get("sync") or []) if x]
            self._icloud_set_enabled_calendars(
                enabled,
                default_id=prefer,
                enabled_names=names,
                sync_ids=sync_ids,
                refresh=True,
            )
            self.config.set("icloud_sync_enabled", True, persist=False)
            self.config.save()
            done(True, result)

        def err(msg: str) -> None:
            done(False, msg)

        self._run_icloud_job(work, on_ok=ok, on_err=err)

    def _icloud_sync_now_from_dialog(self, data: dict, done) -> None:  # noqa: ANN001
        # 尊重「启用同步」总开关，不再强行打开
        if not bool(data.get("enabled", False)):
            done(False, "未启用同步。请先勾选「启用同步」。")
            return
        self._icloud_apply_settings_dict(data)
        enabled_ids = [str(x) for x in (data.get("calendars_sync") or []) if x]
        if not enabled_ids:
            enabled_ids = self._icloud_sync_calendar_ids()
        if not enabled_ids:
            done(False, "没有勾选需要同步的日历。")
            return
        enabled_names = [str(x) for x in (data.get("calendars_sync_names") or []) if x]
        if not enabled_names:
            enabled_names = self._icloud_sync_calendar_names()
        plans = [
            self._prepare_plan_for_push(p)
            for p in self.todos.all_plans()
            if plan_needs_push(p) and not is_local_plan(p)
        ]

        def work():
            self.icloud.ensure_connected()
            cals = self.icloud.refresh_calendars()
            live_ids = {c.id for c in cals}
            by_name = {c.name: c.id for c in cals if c.name}
            active: list[str] = []
            seen: set[str] = set()
            for cid in enabled_ids:
                if is_local_calendar_id(cid):
                    continue
                if cid in live_ids and cid not in seen:
                    active.append(cid)
                    seen.add(cid)
            for n in enabled_names:
                if str(n) == LOCAL_CALENDAR_NAME:
                    continue
                cid = by_name.get(str(n))
                if cid and cid not in seen:
                    active.append(cid)
                    seen.add(cid)
            meta: list[tuple[str, str, str | None, str | None, str | None]] = []
            for plan in plans:
                if is_local_plan(plan):
                    continue
                cid = str(plan.get("calendar_id") or "")
                if is_local_calendar_id(cid):
                    continue
                if active and cid and cid not in active:
                    continue
                try:
                    uid, etag = self.icloud.upsert_plan(plan)
                except RuntimeError:
                    continue
                meta.append(
                    (
                        str(plan["id"]),
                        str(uid),
                        etag,
                        plan.get("calendar_id"),
                        plan.get("calendar_name"),
                    )
                )
            remotes = []
            scope: set[str] = set()
            for cid in active:
                try:
                    remotes.extend(self.icloud.list_events(cid))
                    scope.add(cid)
                except Exception:  # noqa: BLE001
                    continue
            return meta, remotes, scope, live_ids

        def ok(result: object) -> None:
            if not isinstance(result, tuple) or len(result) != 4:
                done(False, "同步返回异常")
                return
            meta, remotes, scope, live_ids = result
            live_set = set(live_ids) if isinstance(live_ids, set) else set(live_ids or [])
            pruned = self._prune_enabled_calendars(live_set)
            self._realign_enabled_ids_to_live(live_set)
            for row in meta:
                pid, uid, etag, cid, cname = row
                try:
                    self.todos.set_caldav_meta(pid, caldav_uid=uid, caldav_etag=etag)
                    if cid:
                        self.todos.set_calendar(
                            pid,
                            calendar_id=str(cid),
                            calendar_name=str(cname) if cname else None,
                            touch=False,
                        )
                except KeyError:
                    continue
            scope_ids = scope if isinstance(scope, set) else set(scope or [])
            changed = self.icloud.reconcile_with_remotes(
                self.todos, remotes, scope_calendar_ids=scope_ids
            )
            self.refresh_views()
            parts = ["已与 iCloud 对账完成"]
            parts.append("（有计划更新）" if changed else "（计划无变更）")
            if enabled_ids and len(pruned) < len(enabled_ids):
                parts.append(
                    f"；已移除 {len(enabled_ids) - len(pruned)} 个在 iCloud 上已删除的日历"
                )
            done(True, "".join(parts))

        def err(msg: str) -> None:
            done(False, msg)

        self._run_icloud_job(work, on_ok=ok, on_err=err)

    def _clear_local_plans_from_dialog(
        self,
        calendar_ids: list[str] | None = None,
        calendar_names: list[str] | None = None,
    ) -> None:
        ids = [str(x) for x in (calendar_ids or []) if x]
        names = [str(x) for x in (calendar_names or []) if x]
        if ids or names:
            n = self.todos.clear_by_calendars(ids, names)
        else:
            n = 0
        self.refresh_views()
        self._refresh_todolist_ui()
        QMessageBox.information(
            self,
            "已清除计划",
            f"已删除本地计划 {n} 条。" if n else "所选日历下没有可清除的计划。",
        )

    def _apply_start_with_windows(self, enabled: bool) -> None:
        enabled = bool(enabled)
        self.config.set("start_with_windows", enabled, persist=False)
        try:
            autostart.set_enabled(enabled)
        except OSError as exc:
            QMessageBox.warning(self, "开机启动", f"无法写入开机启动项：{exc}")

    def _open_settings(self) -> None:
        existing = getattr(self, "_settings_dlg", None)
        if existing is not None:
            try:
                if existing.isVisible():
                    existing.raise_()
                    existing.activateWindow()
                    return
            except RuntimeError:
                self._settings_dlg = None

        creds = self.icloud.load_credentials()
        cached = self._calendars_for_settings_ui()

        dlg = SettingsDialog(
            countries=self.holidays.countries(),
            available=self.holidays.available_countries(),
            opacity=float(self.config.get("opacity", 0.92)),
            theme=dict(self._theme),
            icloud={
                "enabled": self._icloud_enabled(),
                "apple_id": creds.get("apple_id", ""),
                "app_password": creds.get("app_password", ""),
                "calendar_name": self._icloud_calendar_name(),
                "calendars_enabled": self._icloud_enabled_calendar_ids(),
                "default_calendar_id": self._icloud_default_calendar_id(),
                "calendars": cached,
                "calendars_sync": self._icloud_sync_calendar_ids(),
                "local_calendars": self._local_calendars_list(),
                "calendar_plan_counts": self.todos.plan_counts_by_calendar(),
                "poll_seconds": int(self.config.get("icloud_poll_seconds", 45) or 45),
                "todolist_visible": bool(self.config.get("todolist_visible", True)),
                "start_with_windows": bool(self.config.get("start_with_windows", False)),
                "week_starts_on": self._week_starts_on(),
            },
            on_icloud_test=self._icloud_test_from_dialog,
            on_icloud_sync_now=self._icloud_sync_now_from_dialog,
            on_icloud_refresh_calendars=self._icloud_refresh_calendars_from_dialog,
            on_icloud_create_calendar=self._icloud_create_calendar_from_dialog,
            on_icloud_set_enabled_calendars=lambda ids, default_id, names, sync_ids=None, sync_names=None: self._icloud_set_enabled_calendars(
                list(ids),
                default_id=str(default_id or ""),
                enabled_names=list(names or []),
                sync_ids=list(sync_ids or []),
                sync_names=list(sync_names or []),
                refresh=True,
            ),
            on_clear_plans=self._clear_local_plans_from_dialog,
            parent=None,
        )
        dlg.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._settings_dlg = dlg
        self.settings_btn.setEnabled(False)

        def _raise_settings() -> None:
            if not dlg.isVisible():
                return
            dlg.raise_()
            dlg.activateWindow()
            set_topmost(int(dlg.winId()), True)

        def apply_now() -> None:
            countries = list(dlg.selected_countries())
            self._theme = merge_theme(dlg.theme())
            op = dlg.opacity()
            self.setWindowOpacity(op)
            if hasattr(self, "todo_list_win"):
                self.todo_list_win.setWindowOpacity(op)
            self._apply_style()
            self.holidays.set_countries(countries)
            self._ensure_holiday_years()
            self._icloud_apply_settings_dict(dlg.icloud_settings())
            self._set_todolist_visible(dlg.show_todolist(), persist=True)
            self._apply_start_with_windows(dlg.start_on_boot())
            self.config.set("week_starts_on", dlg.week_start_day(), persist=False)
            self.refresh_views()
            self._save_config()
            # Don't pin calendar while settings is open — keeps dialog visible.
            self._dock_todolist()
            _raise_settings()

        dlg.set_on_changed(apply_now)
        self.holidays.countries_refreshed.connect(dlg.replace_countries)
        self.holidays.refresh_available_from_api_async()

        def on_finished(_code: int = 0) -> None:
            if getattr(self, "_settings_dlg", None) is dlg:
                self._settings_dlg = None
            self.settings_btn.setEnabled(True)
            try:
                self.holidays.countries_refreshed.disconnect(dlg.replace_countries)
            except (TypeError, RuntimeError):
                pass
            try:
                set_topmost(int(dlg.winId()), False)
            except Exception:  # noqa: BLE001
                pass

        dlg.finished.connect(on_finished)

        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            ag = screen.availableGeometry()
            dlg.resize(520, 600)
            geo = dlg.frameGeometry()
            geo.moveCenter(ag.center())
            dlg.move(geo.topLeft())

        # Pause HWND_BOTTOM reinforce so the dialog is not buried.
        if hasattr(self, "_bottom_timer"):
            self._bottom_timer.stop()
        dlg.show()
        _ = dlg.winId()
        _raise_settings()
        QTimer.singleShot(0, _raise_settings)
        QTimer.singleShot(50, _raise_settings)
        dlg.exec()
        if hasattr(self, "_bottom_timer") and self.isVisible():
            self._bottom_timer.start()
            self._pin_to_desktop()
