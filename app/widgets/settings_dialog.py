# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.services.calendar_math import WEEKDAY_NAMES_CN, normalize_week_starts_on
from app.services.icloud_calendar_sync import DEFAULT_CALENDAR_NAME
from app.services.local_calendar import (
    LOCAL_CALENDAR_ID,
    LOCAL_CALENDAR_NAME,
    is_local_calendar_id,
    with_local_calendar,
)
from app.services.theme import (
    THEME_PRESET_LABELS,
    THEME_PRESETS,
    match_preset_id,
    merge_theme,
    preset_swatches,
    preset_theme,
)

_COL_SHOW_W = 56
_COL_SYNC_W = 56


class _CalendarListRow(QWidget):
    """一整行：名称 + 显示 + 同步；鼠标悬停时整行高亮。"""

    def __init__(self, *, alt: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._alt = alt
        # 自定义 Python 类名不能当 QSS 类型选择器；用 objectName + StyledBackground
        self.setObjectName("calListRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.name_lbl = QLabel()
        self.name_lbl.setWordWrap(False)
        self.name_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.name_lbl.setStyleSheet(
            "QLabel { background:transparent; color:#EEF2F6; padding:7px 8px; border:none; }"
        )
        lay.addWidget(self.name_lbl, 1)

        self.show_cb = QCheckBox()
        self.show_cb.setFixedWidth(_COL_SHOW_W)
        self.show_cb.setStyleSheet(
            "QCheckBox { background:transparent; padding:6px 12px; border:none; }"
        )
        lay.addWidget(self.show_cb, 0, Qt.AlignmentFlag.AlignCenter)

        self.sync_cb = QCheckBox()
        self.sync_cb.setFixedWidth(_COL_SYNC_W)
        self.sync_cb.setStyleSheet(
            "QCheckBox { background:transparent; padding:6px 12px; border:none; }"
        )
        lay.addWidget(self.sync_cb, 0, Qt.AlignmentFlag.AlignCenter)
        self._apply_style()

    def _apply_style(self) -> None:
        bg = "#1E252E" if self._alt else "#181E26"
        # :hover 在子控件上仍生效；勿用 enter/leave（移入勾选框会误 leave）
        self.setStyleSheet(
            f"""
            QWidget#calListRow {{
                background-color: {bg};
                border: 1px solid #2A3340;
            }}
            QWidget#calListRow:hover {{
                background-color: #2A3F5C;
                border: 1px solid #4A9BFF;
            }}
            """
        )


class SettingsDialog(QDialog):
    """多 Tab 设置：改动即时生效，无 OK/Cancel。"""

    def __init__(
        self,
        *,
        countries: list[str],
        available: list[tuple[str, str]],
        opacity: float,
        theme: dict[str, str] | None = None,
        icloud: dict | None = None,
        on_changed: Callable[[], None] | None = None,
        on_icloud_test: Callable[[dict, Callable[[bool, str], None]], None] | None = None,
        on_icloud_sync_now: Callable[[dict, Callable[[bool, str], None]], None] | None = None,
        on_icloud_refresh_calendars: Callable[[dict, Callable[[bool, object], None]], None]
        | None = None,
        on_icloud_create_calendar: Callable[[dict, str, Callable[[bool, object], None]], None]
        | None = None,
        on_icloud_set_enabled_calendars: Callable[
            [list[str], str, list[str], list[str], list[str]], None
        ]
        | None = None,
        on_clear_plans: Callable[[list[str], list[str]], None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumSize(500, 600)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.Window
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self._on_changed = on_changed
        self._on_icloud_test = on_icloud_test
        self._on_icloud_sync_now = on_icloud_sync_now
        self._on_icloud_refresh_calendars = on_icloud_refresh_calendars
        self._on_icloud_create_calendar = on_icloud_create_calendar
        self._on_icloud_set_enabled_calendars = on_icloud_set_enabled_calendars
        self._on_clear_plans = on_clear_plans
        self._theme = merge_theme(theme)
        self._matched_preset = match_preset_id(self._theme)
        self._preset_id = self._matched_preset or "ink_night"
        self._suppress = True
        icloud = icloud or {}
        self._legacy_calendar_name = str(
            icloud.get("calendar_name", DEFAULT_CALENDAR_NAME) or DEFAULT_CALENDAR_NAME
        )
        self._calendar_checks: dict[str, QCheckBox] = {}
        self._calendar_sync_checks: dict[str, QCheckBox] = {}
        # 表头三态循环用：记住「本来勾选」以便从全不选恢复
        self._show_keep_ids: set[str] | None = None
        self._sync_keep_ids: set[str] | None = None
        self._calendar_names: dict[str, str] = {}
        raw_counts = icloud.get("calendar_plan_counts") or {}
        self._calendar_plan_counts: dict[str, int] = {
            str(k): int(v)
            for k, v in (raw_counts.items() if isinstance(raw_counts, dict) else [])
            if str(k) and int(v) >= 0
        }
        self._sync_enabled_ids: set[str] = {
            str(x) for x in (icloud.get("calendars_sync") or []) if x
        }
        self._extra_local_calendars: list[dict[str, str]] = [
            {"id": str(it.get("id")), "name": str(it.get("name"))}
            for it in (icloud.get("local_calendars") or [])
            if isinstance(it, dict) and it.get("id") and it.get("name")
        ]
        self._checks: dict[str, QCheckBox] = {}
        # 账号：empty | editing | locked
        self._account_mode = "empty"
        self._creds_verified = False
        self._close_after_verify_fail = False
        self._verify_in_progress = False
        self._force_close = False

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(350)
        self._debounce.timeout.connect(self._emit_changed)
        # 日历勾选过滤：短防抖，走轻量回调，避免整套 apply 闪屏
        self._cal_filter_debounce = QTimer(self)
        self._cal_filter_debounce.setSingleShot(True)
        self._cal_filter_debounce.setInterval(80)
        self._cal_filter_debounce.timeout.connect(self._flush_calendar_filter)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        tabs = QTabWidget()
        tabs.addTab(self._build_appearance_tab(opacity, icloud), "外观")
        tabs.addTab(self._build_holiday_tab(available, countries), "节假日")
        tabs.addTab(self._build_icloud_tab(icloud), "iCloud")
        root.addWidget(tabs, 1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton("关闭")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.close)
        close_row.addWidget(close_btn)
        root.addLayout(close_row)

        self._apply_dialog_style()
        self._suppress = False

    def set_on_changed(self, callback: Callable[[], None] | None) -> None:
        self._on_changed = callback

    def _build_appearance_tab(self, opacity: float, icloud: dict) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(10)

        lay.addWidget(QLabel("配色方案"))
        scheme_scroll = QScrollArea()
        scheme_scroll.setWidgetResizable(True)
        scheme_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scheme_holder = QWidget()
        scheme_layout = QVBoxLayout(scheme_holder)
        scheme_layout.setSpacing(6)
        scheme_layout.setContentsMargins(0, 0, 4, 0)

        self._scheme_group = QButtonGroup(self)
        self._scheme_group.setExclusive(True)
        self._scheme_radios: dict[str, QRadioButton] = {}

        for pid, label in THEME_PRESET_LABELS.items():
            if pid not in THEME_PRESETS:
                continue
            row = QWidget()
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(4, 2, 4, 2)
            row_lay.setSpacing(8)

            radio = QRadioButton(label)
            radio.setCursor(Qt.CursorShape.PointingHandCursor)
            self._scheme_radios[pid] = radio
            self._scheme_group.addButton(radio)
            radio.toggled.connect(lambda checked, p=pid: self._on_scheme_toggled(p, checked))

            swatch = QLabel()
            swatch.setFixedHeight(22)
            swatch.setMinimumWidth(120)
            colors = preset_swatches(pid)
            stops = " ".join(
                f"stop:{i / max(1, len(colors) - 1):.2f} {c}" for i, c in enumerate(colors)
            )
            swatch.setStyleSheet(
                f"""
                QLabel {{
                    border-radius: 3px;
                    border: 1px solid #445;
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, {stops});
                }}
                """
            )
            row_lay.addWidget(radio, 1)
            row_lay.addWidget(swatch)
            scheme_layout.addWidget(row)
            if self._matched_preset is not None and pid == self._matched_preset:
                radio.setChecked(True)

        scheme_layout.addStretch(1)
        scheme_scroll.setWidget(scheme_holder)
        lay.addWidget(scheme_scroll, 1)

        op_row = QFormLayout()
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(30, 100)
        self.opacity_slider.setValue(int(round(opacity * 100)))
        self.opacity_value = QLabel(f"{self.opacity_slider.value()}%")
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        op_wrap = QHBoxLayout()
        op_wrap.addWidget(self.opacity_slider, 1)
        op_wrap.addWidget(self.opacity_value)
        op_row.addRow("不透明度", op_wrap)
        lay.addLayout(op_row)

        week_row = QFormLayout()
        self.week_starts_on = QComboBox()
        for i, name in enumerate(WEEKDAY_NAMES_CN):
            self.week_starts_on.addItem(name, i)
        cur_start = normalize_week_starts_on(icloud.get("week_starts_on", 6))
        idx = self.week_starts_on.findData(cur_start)
        if idx >= 0:
            self.week_starts_on.setCurrentIndex(idx)
        self.week_starts_on.setToolTip("周视图与月视图表头的一周起点")
        self.week_starts_on.currentIndexChanged.connect(lambda _i: self._emit_changed())
        week_row.addRow("每周第一天", self.week_starts_on)
        lay.addLayout(week_row)

        self.todolist_visible = QCheckBox("显示今日待办窗口")
        self.todolist_visible.setChecked(bool(icloud.get("todolist_visible", True)))
        self.todolist_visible.toggled.connect(lambda _c: self._emit_changed())
        lay.addWidget(self.todolist_visible)

        self.start_with_windows = QCheckBox("开机时自动启动")
        self.start_with_windows.setChecked(bool(icloud.get("start_with_windows", False)))
        self.start_with_windows.toggled.connect(lambda _c: self._emit_changed())
        lay.addWidget(self.start_with_windows)
        return page

    def _build_holiday_tab(
        self,
        available: list[tuple[str, str]],
        countries: list[str],
    ) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(8)
        tip = QLabel("勾选后即时生效；可多选。")
        tip.setStyleSheet("color:#A8B0BC; font-size:11px;")
        lay.addWidget(tip)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._country_holder = QWidget()
        self._country_form = QVBoxLayout(self._country_holder)
        self._fill_countries(available, {c.upper() for c in countries})
        scroll.setWidget(self._country_holder)
        lay.addWidget(scroll, 1)
        return page

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "color:#A8B0BC; font-size:11px; font-weight:600; letter-spacing:0.5px;"
        )
        return lbl

    def _thin_divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        line.setStyleSheet("background:#2A3340; border:none; max-height:1px;")
        return line

    def _build_icloud_tab(self, icloud: dict) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 6, 4, 4)
        lay.setSpacing(10)

        help_lbl = QLabel(
            "使用 Apple「应用专用密码」。通讯录生日等只读日历不会列出；公共假日仍由 Nager 显示。"
        )
        help_lbl.setWordWrap(True)
        help_lbl.setStyleSheet("color:#8B97A8; font-size:11px;")
        lay.addWidget(help_lbl)

        # —— 账号 ——
        lay.addWidget(self._section_label("账号"))
        self._add_account_btn = QPushButton("添加 Apple 账号")
        self._add_account_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_account_btn.clicked.connect(self._on_add_account)
        lay.addWidget(self._add_account_btn)

        self._account_form_widget = QWidget()
        account_form = QFormLayout(self._account_form_widget)
        account_form.setContentsMargins(0, 0, 0, 0)
        account_form.setHorizontalSpacing(12)
        account_form.setVerticalSpacing(8)
        account_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )

        self.icloud_apple_id = QLineEdit()
        self.icloud_apple_id.setPlaceholderText("Apple ID 邮箱")
        self.icloud_apple_id.setText(str(icloud.get("apple_id", "")))
        account_form.addRow("Apple ID", self.icloud_apple_id)

        self.icloud_password = QLineEdit()
        self.icloud_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.icloud_password.setPlaceholderText("应用专用密码（不是登录密码）")
        self.icloud_password.setText(str(icloud.get("app_password", "")))
        account_form.addRow("专用密码", self.icloud_password)

        cred_btn_row = QHBoxLayout()
        cred_btn_row.setContentsMargins(0, 0, 0, 0)
        cred_btn_row.setSpacing(8)
        self._cred_action_btn = QPushButton("校验")
        self._cred_action_btn.clicked.connect(self._on_cred_action)
        self._clear_account_btn = QPushButton("清除账号")
        self._clear_account_btn.setToolTip("删除已保存的 Apple ID 与专用密码")
        self._clear_account_btn.clicked.connect(self._on_clear_account)
        self._clear_account_btn.setVisible(False)
        cred_btn_row.addWidget(self._cred_action_btn)
        cred_btn_row.addWidget(self._clear_account_btn)
        cred_btn_row.addStretch(1)
        account_form.addRow("", cred_btn_row)
        lay.addWidget(self._account_form_widget)

        lay.addWidget(self._thin_divider())

        # —— 同步 ——
        lay.addWidget(self._section_label("同步"))
        sync_bar = QHBoxLayout()
        sync_bar.setContentsMargins(0, 0, 0, 0)
        sync_bar.setSpacing(10)
        self.icloud_enabled = QCheckBox("启用同步")
        self.icloud_enabled.setChecked(bool(icloud.get("enabled", False)))
        self.icloud_enabled.setToolTip(
            "总开关：关闭后不进行任何 iCloud 轮询/推送；\n"
            "打开后仅同步下方勾了「同步」的日历。"
        )
        self.icloud_enabled.toggled.connect(self._on_master_sync_toggled)
        sync_bar.addWidget(self.icloud_enabled)
        sync_bar.addStretch(1)

        poll_lbl = QLabel("轮询间隔")
        poll_lbl.setStyleSheet("color:#A8B0BC; font-size:12px;")
        sync_bar.addWidget(poll_lbl)

        self.icloud_poll = QSpinBox()
        self.icloud_poll.setRange(15, 600)
        self.icloud_poll.setSingleStep(1)
        self.icloud_poll.setSuffix(" 秒")
        self.icloud_poll.setValue(int(icloud.get("poll_seconds", 45) or 45))
        self.icloud_poll.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.icloud_poll.setAccelerated(True)
        self.icloud_poll.setFixedWidth(88)
        self.icloud_poll.valueChanged.connect(lambda _v: self._emit_changed())
        sync_bar.addWidget(self.icloud_poll)
        self._poll_down = QToolButton()
        self._poll_down.setText("−")
        self._poll_down.setToolTip("减少间隔")
        self._poll_down.clicked.connect(self.icloud_poll.stepDown)
        self._poll_up = QToolButton()
        self._poll_up.setText("+")
        self._poll_up.setToolTip("增加间隔")
        self._poll_up.clicked.connect(self.icloud_poll.stepUp)
        for btn in (self._poll_down, self._poll_up):
            btn.setAutoRepeat(True)
            btn.setAutoRepeatDelay(400)
            btn.setAutoRepeatInterval(60)
            btn.setFixedSize(28, 28)
            sync_bar.addWidget(btn)
        lay.addLayout(sync_bar)

        sync_actions = QHBoxLayout()
        sync_actions.setContentsMargins(0, 0, 0, 0)
        sync_actions.setSpacing(8)
        self._icloud_sync_btn = QPushButton("立即同步")
        self._icloud_sync_btn.clicked.connect(self._on_sync_now)
        self._icloud_busy_label = QLabel("")
        self._icloud_busy_label.setStyleSheet("color:#FBBF24; font-size:11px;")
        sync_actions.addWidget(self._icloud_sync_btn)
        sync_actions.addWidget(self._icloud_busy_label, 1)
        lay.addLayout(sync_actions)

        lay.addWidget(self._thin_divider())

        # —— 日历 ——
        lay.addWidget(self._section_label("日历"))
        cal_header = QHBoxLayout()
        cal_header.setContentsMargins(0, 0, 0, 0)
        cal_header.setSpacing(8)
        cal_tip = QLabel("显示 = 主界面可见；同步 = 与 iCloud 双向（本地除外）")
        cal_tip.setStyleSheet("color:#8B97A8; font-size:11px;")
        cal_tip.setWordWrap(True)
        cal_header.addWidget(cal_tip, 1)
        self._cal_refresh_btn = QPushButton("刷新")
        self._cal_refresh_btn.setToolTip("从 iCloud 刷新日历列表")
        self._cal_refresh_btn.clicked.connect(self._on_refresh_calendars)
        cal_header.addWidget(self._cal_refresh_btn)
        lay.addLayout(cal_header)

        cal_scroll = QScrollArea()
        cal_scroll.setWidgetResizable(True)
        cal_scroll.setFrameShape(QScrollArea.Shape.StyledPanel)
        cal_scroll.setStyleSheet(
            "QScrollArea { border:1px solid #445; border-radius:2px; background:#151A20; }"
        )
        cal_scroll.setMinimumHeight(140)
        self._cal_holder = QWidget()
        self._cal_holder.setStyleSheet("background:#151A20;")
        self._cal_form = QVBoxLayout(self._cal_holder)
        self._cal_form.setContentsMargins(6, 4, 6, 4)
        self._cal_form.setSpacing(2)

        hdr = QWidget()
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(0, 0, 0, 0)
        hdr_lay.setSpacing(0)
        name_h = QLabel("名称")
        name_h.setStyleSheet(
            "color:#A8B0BC; font-size:11px; font-weight:600; padding:4px 6px;"
        )
        hdr_lay.addWidget(name_h, 1)
        self._show_all_cb = QCheckBox("显示")
        self._show_all_cb.setFixedWidth(_COL_SHOW_W)
        self._show_all_cb.setTristate(False)
        self._show_all_cb.setToolTip("点击循环：全选 → 全不选 → 恢复原先勾选")
        self._show_all_cb.clicked.connect(self._on_show_all_toggled)
        self._sync_all_cb = QCheckBox("同步")
        self._sync_all_cb.setFixedWidth(_COL_SYNC_W)
        self._sync_all_cb.setTristate(False)
        self._sync_all_cb.setToolTip("点击循环：全选 → 全不选 → 恢复原先勾选（本地日历除外）")
        self._sync_all_cb.clicked.connect(self._on_sync_all_toggled)
        for cb in (self._show_all_cb, self._sync_all_cb):
            cb.setStyleSheet(
                "QCheckBox { color:#A8B0BC; font-size:11px; font-weight:600; spacing:2px; }"
            )
        hdr_lay.addWidget(self._show_all_cb, 0, Qt.AlignmentFlag.AlignCenter)
        hdr_lay.addWidget(self._sync_all_cb, 0, Qt.AlignmentFlag.AlignCenter)
        self._cal_form.addWidget(hdr)

        self._cal_rows = QVBoxLayout()
        self._cal_rows.setContentsMargins(0, 0, 0, 0)
        self._cal_rows.setSpacing(2)
        self._cal_form.addLayout(self._cal_rows)
        self._cal_form.addStretch(1)
        cal_scroll.setWidget(self._cal_holder)
        lay.addWidget(cal_scroll, 1)

        new_row = QHBoxLayout()
        new_row.setContentsMargins(0, 0, 0, 0)
        new_row.setSpacing(8)
        self._new_cal_name = QLineEdit()
        self._new_cal_name.setPlaceholderText("新建日历名称（可不登录）")
        self._new_cal_btn = QPushButton("新建")
        self._new_cal_btn.setFixedWidth(64)
        self._new_cal_btn.clicked.connect(self._on_create_calendar)
        new_row.addWidget(self._new_cal_name, 1)
        new_row.addWidget(self._new_cal_btn)
        lay.addLayout(new_row)

        default_row = QFormLayout()
        default_row.setContentsMargins(0, 0, 0, 0)
        default_row.setHorizontalSpacing(12)
        self.default_calendar = QComboBox()
        self.default_calendar.setMinimumWidth(160)
        self.default_calendar.currentIndexChanged.connect(lambda _i: self._emit_changed())
        default_row.addRow("新建计划默认", self.default_calendar)
        lay.addLayout(default_row)

        enabled = [str(x) for x in (icloud.get("calendars_enabled") or []) if x]
        default_id = str(icloud.get("default_calendar_id") or "")
        calendars = icloud.get("calendars") or []
        if not isinstance(calendars, list):
            calendars = []
        calendars = with_local_calendar(
            calendars, extra_local=self._extra_local_calendars
        )
        self._fill_calendars(calendars, set(enabled), default_id)

        has_id = bool(str(icloud.get("apple_id", "")).strip())
        has_pw = bool(str(icloud.get("app_password", "")).strip())
        if has_id and has_pw:
            self._set_account_mode("locked", verified=True)
        else:
            self._set_account_mode("empty", verified=False)
        self._update_sync_master_ui()
        return page

    def _on_master_sync_toggled(self, checked: bool) -> None:
        if self._suppress:
            return
        if checked and not self._creds_verified:
            QMessageBox.information(
                self,
                "提示",
                "请先添加并校验 Apple 账号，再启用同步。",
            )
            self.icloud_enabled.blockSignals(True)
            self.icloud_enabled.setChecked(False)
            self.icloud_enabled.blockSignals(False)
            self._update_sync_master_ui()
            return
        if checked:
            # 打开总开关且尚未勾选任何「同步」时，默认勾上全部可同步日历
            syncable = [
                cid
                for cid, cb in self._calendar_sync_checks.items()
                if cb.isEnabled() and not is_local_calendar_id(cid)
            ]
            if syncable and not any(
                self._calendar_sync_checks[cid].isChecked() for cid in syncable
            ):
                self._suppress = True
                try:
                    for cid in syncable:
                        cb = self._calendar_sync_checks[cid]
                        cb.blockSignals(True)
                        cb.setChecked(True)
                        cb.blockSignals(False)
                finally:
                    self._suppress = False
                self._refresh_column_header_checks()
                self._cal_filter_debounce.start()
        self._update_sync_master_ui()
        self._emit_changed()

    def _update_sync_master_ui(self) -> None:
        """「启用同步」总开关：关闭时禁用同步列与立即同步。"""
        master_on = bool(self.icloud_enabled.isChecked()) and self._creds_verified
        has_syncable = any(
            not is_local_calendar_id(cid) for cid in self._calendar_sync_checks
        )
        if hasattr(self, "_sync_all_cb"):
            self._sync_all_cb.setEnabled(master_on and has_syncable)
        for cid, cb in self._calendar_sync_checks.items():
            if is_local_calendar_id(cid):
                cb.setEnabled(False)
                continue
            cb.setEnabled(master_on)
        if hasattr(self, "_icloud_sync_btn"):
            if not getattr(self, "_icloud_busy", False):
                self._icloud_sync_btn.setEnabled(master_on)
        if hasattr(self, "icloud_poll"):
            self.icloud_poll.setEnabled(master_on)
            self._poll_down.setEnabled(master_on)
            self._poll_up.setEnabled(master_on)

    def _set_account_mode(self, mode: str, *, verified: bool | None = None) -> None:
        self._account_mode = mode
        if verified is not None:
            self._creds_verified = verified
        if mode == "empty":
            self._add_account_btn.setVisible(True)
            self._account_form_widget.setVisible(False)
            self.icloud_apple_id.clear()
            self.icloud_password.clear()
            self.icloud_apple_id.setReadOnly(False)
            self.icloud_password.setReadOnly(False)
            self._clear_account_btn.setVisible(False)
        elif mode == "editing":
            self._add_account_btn.setVisible(False)
            self._account_form_widget.setVisible(True)
            self.icloud_apple_id.setReadOnly(False)
            self.icloud_password.setReadOnly(False)
            self._cred_action_btn.setText("校验")
            self._clear_account_btn.setVisible(False)
            self._creds_verified = False
        else:  # locked
            self._add_account_btn.setVisible(False)
            self._account_form_widget.setVisible(True)
            self.icloud_apple_id.setReadOnly(True)
            self.icloud_password.setReadOnly(True)
            self._cred_action_btn.setText("修改")
            self._clear_account_btn.setVisible(True)
            self._creds_verified = True
        self._update_sync_master_ui()

    def _on_add_account(self) -> None:
        self._set_account_mode("editing", verified=False)
        self.icloud_apple_id.setFocus()

    def _on_cred_action(self) -> None:
        if self._account_mode == "locked":
            self._set_account_mode("editing", verified=False)
            self.icloud_apple_id.setFocus()
            self.icloud_apple_id.selectAll()
            return
        self._run_credential_verify(reason="manual")

    def _pick_calendars_to_keep(self) -> dict | None:
        """勾选要保留计划的日历。返回 clear/keep 信息；取消返回 None。"""
        items: list[tuple[str, str]] = []
        seen: set[str] = set()
        for cid, name in self._calendar_names.items():
            if not cid or cid in seen:
                continue
            seen.add(cid)
            items.append((cid, name or cid))
        if LOCAL_CALENDAR_ID not in seen:
            items.insert(0, (LOCAL_CALENDAR_ID, LOCAL_CALENDAR_NAME))

        dlg = QDialog(self)
        dlg.setWindowTitle("选择要保留的日历计划")
        dlg.setMinimumWidth(360)
        lay = QVBoxLayout(dlg)
        tip = QLabel(
            "请勾选【要保留】本地计划的日历。\n\n"
            "未勾选的日历：其本地计划将被永久删除（不可恢复）。\n"
            "已勾选的日历：计划会留在本机，并继续在日历上显示。\n\n"
            "若全部不勾选，将清除所有列出日历下的本地计划；\n"
            "若全部勾选，则只清除账号，不删除任何计划。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#EEF2F6; font-size:13px;")
        lay.addWidget(tip)
        checks: dict[str, QCheckBox] = {}
        for cid, name in items:
            label = self._calendar_label(cid, name)
            cb = QCheckBox(f"保留「{label}」")
            # 默认保留本地日历；iCloud 日历默认不勾（即将断开账号）
            cb.setChecked(cid == LOCAL_CALENDAR_ID)
            checks[cid] = cb
            lay.addWidget(cb)
        btns = QHBoxLayout()
        btns.addStretch(1)
        ok_btn = QPushButton("确定")
        cancel_btn = QPushButton("取消")
        ok_btn.clicked.connect(dlg.accept)
        cancel_btn.clicked.connect(dlg.reject)
        btns.addWidget(ok_btn)
        btns.addWidget(cancel_btn)
        lay.addLayout(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        keep_ids = [cid for cid, cb in checks.items() if cb.isChecked()]
        keep_set = set(keep_ids)
        clear_ids = [cid for cid in checks if cid not in keep_set]
        name_of = {
            cid: (
                self._calendar_names.get(cid)
                or (LOCAL_CALENDAR_NAME if cid == LOCAL_CALENDAR_ID else cid)
            )
            for cid in checks
        }
        return {
            "clear_ids": clear_ids,
            "clear_names": [name_of[cid] for cid in clear_ids],
            "keep_ids": keep_ids,
            "keep_names": [name_of[cid] for cid in keep_ids],
            "keep_items": [{"id": cid, "name": name_of[cid]} for cid in keep_ids],
        }

    def _on_clear_account(self) -> None:
        reply = QMessageBox.question(
            self,
            "清除账号",
            "确定清除已保存的 Apple ID 和专用密码？\n同步将停用。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        picked = self._pick_calendars_to_keep()
        if picked is None:
            return
        clear_ids = list(picked.get("clear_ids") or [])
        clear_names = list(picked.get("clear_names") or [])
        keep_ids = [str(x) for x in (picked.get("keep_ids") or []) if x]
        keep_items = [
            it
            for it in (picked.get("keep_items") or [])
            if isinstance(it, dict) and it.get("id")
        ]
        # 至少保留本地日历入口，保证仍可单机新建
        if LOCAL_CALENDAR_ID not in {str(it.get("id")) for it in keep_items}:
            keep_items = [
                {"id": LOCAL_CALENDAR_ID, "name": LOCAL_CALENDAR_NAME},
                *keep_items,
            ]
        enabled = set(keep_ids) | {LOCAL_CALENDAR_ID}

        # 先按选择删计划，再清账号并写回「保留」勾选
        if clear_ids and self._on_clear_plans:
            self._on_clear_plans(clear_ids, clear_names)

        self.icloud_enabled.setChecked(False)
        self._set_account_mode("empty", verified=False)
        default_id = (
            LOCAL_CALENDAR_ID
            if LOCAL_CALENDAR_ID in enabled
            else (keep_ids[0] if keep_ids else LOCAL_CALENDAR_ID)
        )
        self._fill_calendars(keep_items, enabled, default_id, strict=True)
        self._emit_changed()

    def _credentials_filled(self) -> bool:
        return bool(
            self.icloud_apple_id.text().strip() and self.icloud_password.text().strip()
        )

    def _needs_verify_on_close(self) -> bool:
        if self._force_close or self._close_after_verify_fail:
            return False
        if self._account_mode != "editing":
            return False
        return self._credentials_filled() and not self._creds_verified

    def _run_credential_verify(self, *, reason: str) -> None:
        """reason: manual | close"""
        if not self._on_icloud_test:
            return
        if not self._credentials_filled():
            QMessageBox.information(self, "提示", "请填写 Apple ID 和应用专用密码")
            if reason == "close":
                self._force_close = True
                self.close()
            return
        if self._verify_in_progress:
            return
        self._verify_in_progress = True
        self._set_icloud_busy(True, "正在校验账号…")

        def done(ok: bool, msg: str) -> None:
            self._verify_in_progress = False
            self._set_icloud_busy(False)
            if reason == "manual":
                self._on_verify_manual_done(ok, msg)
            else:
                self._on_verify_close_done(ok, msg)

        # 校验时暂不强制启用同步；通过后再写配置
        data = self.icloud_settings()
        self._on_icloud_test(data, done)

    def _on_verify_manual_done(self, ok: bool, msg: str) -> None:
        if not ok:
            QMessageBox.warning(self, "校验失败", msg or "账号或密码不正确")
            return
        self._set_account_mode("locked", verified=True)
        self.icloud_enabled.setChecked(True)
        QMessageBox.information(self, "校验通过", msg or "账号密码可用")
        self._refresh_calendars_select_all(after_msg=None)
        self._update_sync_master_ui()
        self._emit_changed()

    def _on_verify_close_done(self, ok: bool, msg: str) -> None:
        if not ok:
            QMessageBox.warning(
                self,
                "无法同步数据",
                (msg or "账号校验未通过") + "\n无法同步数据。",
            )
            self.icloud_enabled.setChecked(False)
            self._emit_changed()
            self._force_close = True
            self.close()
            return
        self._set_account_mode("locked", verified=True)
        self.icloud_enabled.setChecked(True)
        self._emit_changed()
        QMessageBox.information(
            self,
            "可以同步数据",
            "账号校验通过，可以同步数据。\n请选择要显示/同步的日历（默认已全选）。",
        )
        self._refresh_calendars_select_all(after_msg=None)

    def _refresh_calendars_select_all(self, after_msg: str | None) -> None:
        if not self._on_icloud_refresh_calendars:
            if after_msg:
                QMessageBox.information(self, "提示", after_msg)
            return
        self._set_icloud_busy(True, "正在拉取日历…")

        def done(ok: bool, payload: object) -> None:
            self._set_icloud_busy(False)
            if not ok:
                QMessageBox.warning(self, "刷新失败", str(payload))
                return
            if isinstance(payload, list):
                all_ids = {
                    str(it.get("id"))
                    for it in payload
                    if isinstance(it, dict) and it.get("id")
                }
                # 校验后默认勾选全部 iCloud 显示 + 同步，并保留本地
                all_ids.add(LOCAL_CALENDAR_ID)
                sync_ids = {cid for cid in all_ids if not is_local_calendar_id(cid)}
                self._fill_calendars(payload, all_ids, sync_ids=sync_ids)
                self._emit_changed()
            if after_msg:
                QMessageBox.information(self, "提示", after_msg)

        self._on_icloud_refresh_calendars(self.icloud_settings(), done)

    def _apply_dialog_style(self) -> None:
        self.setStyleSheet(
            """
            QDialog { background:#1A1F26; color:#EEF2F6; }
            QLabel { color:#EEF2F6; }
            QCheckBox, QRadioButton { color:#EEF2F6; spacing:8px; }
            QLineEdit, QSpinBox, QComboBox {
                background:#2A313C; color:#EEF2F6; border:1px solid #445; border-radius:2px; padding:4px;
            }
            QLineEdit:read-only {
                color:#A8B0BC; background:#232830;
            }
            QToolButton {
                background:#2A313C; color:#EEF2F6; border:1px solid #445; border-radius:2px;
                font-size:14px; font-weight:700;
            }
            QToolButton:hover { background:#3A4554; }
            QToolButton:pressed { background:#4A5566; }
            QPushButton {
                background:#2A313C; color:#EEF2F6; border:none; border-radius:2px; padding:6px 10px;
            }
            QPushButton:hover { background:#3A4554; }
            QScrollArea { border:none; background:transparent; }
            QTabWidget::pane {
                border: 1px solid #445; border-radius: 2px; top: -1px; background:#1A1F26;
            }
            QTabBar::tab {
                background:#2A313C; color:#A8B0BC; padding:8px 16px;
                border: 1px solid #445; border-bottom: none; margin-right: 2px;
            }
            QTabBar::tab:selected {
                background:#1A1F26; color:#EEF2F6; font-weight:600;
            }
            QTabBar::tab:hover { color:#EEF2F6; }
            """
        )

    def _schedule_changed(self, *_args) -> None:  # noqa: ANN002
        if self._suppress:
            return
        self._debounce.start()

    def _emit_changed(self) -> None:
        if self._suppress:
            return
        if self._on_changed:
            self._on_changed()

    def icloud_settings(self) -> dict:
        enabled = [
            cid for cid, cb in self._calendar_checks.items() if cb.isChecked()
        ]
        enabled_names = [
            self._calendar_names.get(cid, "")
            for cid in enabled
            if self._calendar_names.get(cid)
        ]
        sync_ids = [
            cid
            for cid, cb in self._calendar_sync_checks.items()
            if cb.isChecked() and not is_local_calendar_id(cid)
        ]
        sync_names = [
            self._calendar_names.get(cid, "")
            for cid in sync_ids
            if self._calendar_names.get(cid)
        ]
        default_id = str(self.default_calendar.currentData() or "")
        if not enabled:
            default_id = LOCAL_CALENDAR_ID
            default_name = LOCAL_CALENDAR_NAME
        else:
            if default_id and default_id not in enabled:
                default_id = enabled[0]
            elif not default_id:
                default_id = enabled[0]
            default_name = self._calendar_names.get(default_id, self._legacy_calendar_name)
        return {
            "enabled": self.icloud_enabled.isChecked(),
            "apple_id": self.icloud_apple_id.text().strip(),
            "app_password": self.icloud_password.text().strip(),
            "calendar_name": default_name or LOCAL_CALENDAR_NAME,
            "calendars_enabled": enabled,
            "calendars_enabled_names": enabled_names,
            "calendars_sync": sync_ids,
            "calendars_sync_names": sync_names,
            "local_calendars": list(self._extra_local_calendars),
            "default_calendar_id": default_id,
            "poll_seconds": int(self.icloud_poll.value()),
        }

    def _plan_count_for(self, cid: str, name: str = "") -> int:
        counts = self._calendar_plan_counts
        n = int(counts.get(cid, 0) or 0)
        if n:
            return n
        if name:
            return int(counts.get(name, 0) or 0)
        return 0

    def _calendar_label(self, cid: str, name: str) -> str:
        n = self._plan_count_for(cid, name)
        return f"{name} · {n} 条"

    def _checked_calendar_ids(self) -> set[str]:
        return {cid for cid, cb in self._calendar_checks.items() if cb.isChecked()}

    def _checked_calendar_names(self) -> set[str]:
        return {
            self._calendar_names.get(cid, "")
            for cid, cb in self._calendar_checks.items()
            if cb.isChecked()
        }

    def _fill_calendars(
        self,
        calendars: list,
        enabled: set[str],
        default_id: str = "",
        *,
        keep_checked_names: set[str] | None = None,
        sync_ids: set[str] | None = None,
        strict: bool = False,
    ) -> None:
        calendars = with_local_calendar(
            [c for c in calendars if isinstance(c, dict)],
            extra_local=self._extra_local_calendars,
        )
        was = self._suppress
        self._suppress = True
        try:
            if sync_ids is None:
                sync_ids = set(self._sync_enabled_ids)
            if strict:
                prev_enabled: set[str] = set()
                prev_names: set[str] = set()
                prev_sync: set[str] = set(sync_ids)
            else:
                prev_enabled = self._checked_calendar_ids()
                prev_names = (
                    keep_checked_names
                    if keep_checked_names is not None
                    else self._checked_calendar_names()
                )
                prev_sync = {
                    cid
                    for cid, cb in self._calendar_sync_checks.items()
                    if cb.isChecked()
                } or set(sync_ids)
                if prev_enabled:
                    enabled = set(enabled) | prev_enabled
                if prev_sync:
                    sync_ids = set(sync_ids) | prev_sync

            # 清空旧数据行
            while self._cal_rows.count():
                item = self._cal_rows.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.deleteLater()
            self._calendar_checks.clear()
            self._calendar_sync_checks.clear()
            self._calendar_names.clear()
            row_i = 0
            for item in calendars:
                if not isinstance(item, dict):
                    continue
                cid = str(item.get("id") or "")
                name = str(item.get("name") or "")
                if not cid or not name:
                    continue
                self._calendar_names[cid] = name

                row = _CalendarListRow(alt=(row_i % 2 == 1))
                row.name_lbl.setText(self._calendar_label(cid, name))
                row.name_lbl.setToolTip(name)

                show_cb = row.show_cb
                show_cb.setToolTip("在主界面显示该日历的计划")
                if strict:
                    show_checked = cid in enabled
                else:
                    show_checked = cid in enabled or name in prev_names
                show_cb.blockSignals(True)
                show_cb.setChecked(show_checked)
                show_cb.blockSignals(False)
                show_cb.toggled.connect(self._on_calendar_toggled)
                self._calendar_checks[cid] = show_cb

                sync_cb = row.sync_cb
                if is_local_calendar_id(cid):
                    sync_cb.setEnabled(False)
                    sync_cb.setChecked(False)
                    sync_cb.setToolTip("本地日历仅保存在本机，不能同步到 iCloud")
                else:
                    sync_cb.setToolTip("登录 Apple 后与 iCloud 同步该日历")
                    sync_cb.blockSignals(True)
                    sync_cb.setChecked(cid in sync_ids)
                    sync_cb.blockSignals(False)
                    sync_cb.toggled.connect(self._on_calendar_toggled)
                self._calendar_sync_checks[cid] = sync_cb

                self._cal_rows.addWidget(row)
                row_i += 1

            if not self._calendar_checks:
                empty = QLabel("暂无日历，可在下方新建")
                empty.setStyleSheet("color:#A8B0BC; font-size:11px; padding:8px;")
                self._cal_rows.addWidget(empty)

            self._sync_enabled_ids = {
                cid
                for cid, cb in self._calendar_sync_checks.items()
                if cb.isChecked()
            }
            self._rebuild_default_combo(default_id)
            self._remember_partial_selections()
            self._refresh_column_header_checks()
            self._update_sync_master_ui()
        finally:
            self._suppress = was

    def _on_show_all_toggled(self, _checked: bool = False) -> None:
        if self._suppress or not self._calendar_checks:
            return
        items = list(self._calendar_checks.items())
        all_on = all(cb.isChecked() for _, cb in items)
        all_off = not any(cb.isChecked() for _, cb in items)
        self._suppress = True
        try:
            if all_on:
                # 全选 → 全不选
                for _, cb in items:
                    cb.blockSignals(True)
                    cb.setChecked(False)
                    cb.blockSignals(False)
            elif all_off:
                # 全不选 → 恢复原先勾选（无记忆则全选）
                keep = self._show_keep_ids
                if keep is None:
                    for _, cb in items:
                        cb.blockSignals(True)
                        cb.setChecked(True)
                        cb.blockSignals(False)
                else:
                    for cid, cb in items:
                        cb.blockSignals(True)
                        cb.setChecked(cid in keep)
                        cb.blockSignals(False)
            else:
                # 原先勾选 → 记住后全选
                self._show_keep_ids = {cid for cid, cb in items if cb.isChecked()}
                for _, cb in items:
                    cb.blockSignals(True)
                    cb.setChecked(True)
                    cb.blockSignals(False)
        finally:
            self._suppress = False
        self._refresh_column_header_checks()
        self._rebuild_default_combo()
        self._cal_filter_debounce.start()

    def _on_sync_all_toggled(self, _checked: bool = False) -> None:
        if self._suppress or not self._calendar_sync_checks:
            return
        items = [
            (cid, cb)
            for cid, cb in self._calendar_sync_checks.items()
            if cb.isEnabled() and not is_local_calendar_id(cid)
        ]
        if not items:
            return
        all_on = all(cb.isChecked() for _, cb in items)
        all_off = not any(cb.isChecked() for _, cb in items)
        self._suppress = True
        try:
            if all_on:
                for _, cb in items:
                    cb.blockSignals(True)
                    cb.setChecked(False)
                    cb.blockSignals(False)
            elif all_off:
                keep = self._sync_keep_ids
                if keep is None:
                    for _, cb in items:
                        cb.blockSignals(True)
                        cb.setChecked(True)
                        cb.blockSignals(False)
                else:
                    for cid, cb in items:
                        cb.blockSignals(True)
                        cb.setChecked(cid in keep)
                        cb.blockSignals(False)
            else:
                self._sync_keep_ids = {cid for cid, cb in items if cb.isChecked()}
                for _, cb in items:
                    cb.blockSignals(True)
                    cb.setChecked(True)
                    cb.blockSignals(False)
        finally:
            self._suppress = False
        self._refresh_column_header_checks()
        self._cal_filter_debounce.start()

    def _remember_partial_selections(self) -> None:
        """手动改行勾选时，把当前半选记为「原先勾选」。"""
        show_items = list(self._calendar_checks.items())
        if show_items:
            n_on = sum(1 for _, cb in show_items if cb.isChecked())
            if 0 < n_on < len(show_items):
                self._show_keep_ids = {cid for cid, cb in show_items if cb.isChecked()}
        sync_items = [
            (cid, cb)
            for cid, cb in self._calendar_sync_checks.items()
            if cb.isEnabled() and not is_local_calendar_id(cid)
        ]
        if sync_items:
            n_on = sum(1 for _, cb in sync_items if cb.isChecked())
            if 0 < n_on < len(sync_items):
                self._sync_keep_ids = {cid for cid, cb in sync_items if cb.isChecked()}

    def _refresh_column_header_checks(self) -> None:
        """表头：勾=全选，空=全不选，半勾=原先/部分勾选。"""
        if not hasattr(self, "_show_all_cb"):
            return
        show_boxes = list(self._calendar_checks.values())
        sync_boxes = [
            cb
            for cid, cb in self._calendar_sync_checks.items()
            if cb.isEnabled() and not is_local_calendar_id(cid)
        ]
        self._show_all_cb.blockSignals(True)
        if not show_boxes:
            self._show_all_cb.setCheckState(Qt.CheckState.Unchecked)
        elif all(cb.isChecked() for cb in show_boxes):
            self._show_all_cb.setCheckState(Qt.CheckState.Checked)
        elif any(cb.isChecked() for cb in show_boxes):
            self._show_all_cb.setCheckState(Qt.CheckState.PartiallyChecked)
        else:
            self._show_all_cb.setCheckState(Qt.CheckState.Unchecked)
        self._show_all_cb.blockSignals(False)

        self._sync_all_cb.blockSignals(True)
        if not sync_boxes:
            self._sync_all_cb.setEnabled(False)
            self._sync_all_cb.setCheckState(Qt.CheckState.Unchecked)
        else:
            self._sync_all_cb.setEnabled(True)
            if all(cb.isChecked() for cb in sync_boxes):
                self._sync_all_cb.setCheckState(Qt.CheckState.Checked)
            elif any(cb.isChecked() for cb in sync_boxes):
                self._sync_all_cb.setCheckState(Qt.CheckState.PartiallyChecked)
            else:
                self._sync_all_cb.setCheckState(Qt.CheckState.Unchecked)
        self._sync_all_cb.blockSignals(False)

    def _on_calendar_toggled(self, _checked: bool = False) -> None:
        if self._suppress:
            return
        self._remember_partial_selections()
        self._refresh_column_header_checks()
        self._rebuild_default_combo()
        self._cal_filter_debounce.start()

    def _flush_calendar_filter(self) -> None:
        if self._suppress:
            return
        enabled = [cid for cid, cb in self._calendar_checks.items() if cb.isChecked()]
        names = [
            self._calendar_names.get(cid, "")
            for cid in enabled
            if self._calendar_names.get(cid)
        ]
        sync_ids = [
            cid
            for cid, cb in self._calendar_sync_checks.items()
            if cb.isChecked() and not is_local_calendar_id(cid)
        ]
        sync_names = [
            self._calendar_names.get(cid, "")
            for cid in sync_ids
            if self._calendar_names.get(cid)
        ]
        self._sync_enabled_ids = set(sync_ids)
        default_id = str(self.default_calendar.currentData() or "")
        if not enabled:
            default_id = LOCAL_CALENDAR_ID
        if self._on_icloud_set_enabled_calendars:
            self._on_icloud_set_enabled_calendars(
                enabled, default_id, names, sync_ids, sync_names
            )
        else:
            self._emit_changed()

    def _rebuild_default_combo(self, prefer_id: str = "") -> None:
        if not hasattr(self, "default_calendar"):
            return
        current = prefer_id or str(self.default_calendar.currentData() or "")
        self.default_calendar.blockSignals(True)
        self.default_calendar.clear()
        checked = self._checked_calendar_ids()
        if not checked:
            self.default_calendar.addItem(
                self._calendar_label(LOCAL_CALENDAR_ID, LOCAL_CALENDAR_NAME),
                LOCAL_CALENDAR_ID,
            )
            self.default_calendar.setCurrentIndex(0)
        else:
            for cid, cb in self._calendar_checks.items():
                if cb.isChecked():
                    name = self._calendar_names.get(cid, cid)
                    self.default_calendar.addItem(self._calendar_label(cid, name), cid)
            idx = self.default_calendar.findData(current)
            if idx >= 0:
                self.default_calendar.setCurrentIndex(idx)
            elif self.default_calendar.count() > 0:
                self.default_calendar.setCurrentIndex(0)
        self.default_calendar.blockSignals(False)

    def _set_icloud_busy(self, busy: bool, text: str = "") -> None:
        self._icloud_busy = bool(busy)
        self._cred_action_btn.setEnabled(not busy)
        self._clear_account_btn.setEnabled(not busy)
        self._add_account_btn.setEnabled(not busy)
        self._cal_refresh_btn.setEnabled(not busy)
        self._new_cal_btn.setEnabled(not busy)
        master_on = bool(self.icloud_enabled.isChecked()) and self._creds_verified
        self._icloud_sync_btn.setEnabled((not busy) and master_on)
        self._icloud_busy_label.setText(text if busy else "")

    def _on_sync_now(self) -> None:
        if not self._creds_verified:
            QMessageBox.information(self, "提示", "请先校验 Apple 账号")
            return
        if not self.icloud_enabled.isChecked():
            QMessageBox.information(
                self,
                "提示",
                "请先勾选「启用同步」，并至少勾选一个日历的「同步」。",
            )
            return
        sync_ids = [
            cid
            for cid, cb in self._calendar_sync_checks.items()
            if cb.isChecked() and not is_local_calendar_id(cid)
        ]
        if not sync_ids:
            QMessageBox.information(
                self,
                "提示",
                "请至少勾选一个日历的「同步」后再立即同步。",
            )
            return
        if not self._on_icloud_sync_now:
            return
        self._emit_changed()
        self._set_icloud_busy(True, "正在同步…")

        def done(ok: bool, msg: str) -> None:
            self._set_icloud_busy(False)
            if ok:
                QMessageBox.information(self, "同步完成", msg)
            else:
                QMessageBox.warning(self, "同步失败", msg)

        self._on_icloud_sync_now(self.icloud_settings(), done)

    def _on_refresh_calendars(self) -> None:
        if not self._creds_verified and not self._credentials_filled():
            QMessageBox.information(self, "提示", "请先添加并校验 Apple 账号")
            return
        if not self._on_icloud_refresh_calendars:
            return
        if self._account_mode == "editing" and not self._creds_verified:
            QMessageBox.information(self, "提示", "请先点击「校验」验证账号密码")
            return
        self._emit_changed()
        keep_ids = self._checked_calendar_ids()
        keep_names = self._checked_calendar_names()
        self._set_icloud_busy(True, "正在拉取日历…")

        def done(ok: bool, payload: object) -> None:
            self._set_icloud_busy(False)
            if not ok:
                QMessageBox.warning(self, "刷新失败", str(payload))
                return
            if isinstance(payload, list):
                # 用户已全部取消勾选时保持空，不要刷新后全选回来
                self._fill_calendars(
                    payload, set(keep_ids), keep_checked_names=keep_names
                )
                self._emit_changed()

        self._on_icloud_refresh_calendars(self.icloud_settings(), done)

    def _on_create_calendar(self) -> None:
        if not self._on_icloud_create_calendar:
            return
        name = self._new_cal_name.text().strip()
        if not name:
            QMessageBox.information(self, "提示", "请输入日历名称")
            return
        if name == LOCAL_CALENDAR_NAME:
            QMessageBox.information(self, "提示", "「本地日历」为内置日历，请换一个名称")
            return
        # 同时固化 id 与名称：刷新后 id 可能变，名称用于回填勾选
        keep_ids = list(self._checked_calendar_ids())
        keep_names = [n for n in self._checked_calendar_names() if n]
        sync_ids = [
            cid
            for cid, cb in self._calendar_sync_checks.items()
            if cb.isChecked() and not is_local_calendar_id(cid)
        ]
        sync_names = [
            self._calendar_names.get(cid, "")
            for cid in sync_ids
            if self._calendar_names.get(cid)
        ]
        default_id = str(self.default_calendar.currentData() or "")
        data = self.icloud_settings()
        data["calendars_enabled"] = keep_ids
        data["calendars_enabled_names"] = keep_names
        data["calendars_sync"] = sync_ids
        data["calendars_sync_names"] = sync_names
        data["create_local"] = not self._creds_verified
        if default_id:
            data["default_calendar_id"] = default_id
        self._set_icloud_busy(True, "正在创建…")

        def done(ok: bool, payload: object) -> None:
            self._set_icloud_busy(False)
            if not ok:
                QMessageBox.warning(self, "创建失败", str(payload))
                return
            self._new_cal_name.clear()
            if not isinstance(payload, dict):
                QMessageBox.warning(self, "创建失败", "创建返回异常")
                return
            calendars = payload.get("calendars") or []
            if not isinstance(calendars, list) or not calendars:
                QMessageBox.warning(self, "创建失败", "已创建但未能刷新日历列表，请点「刷新」")
                return
            extras = payload.get("local_calendars")
            if isinstance(extras, list):
                self._extra_local_calendars = [
                    {"id": str(it.get("id")), "name": str(it.get("name"))}
                    for it in extras
                    if isinstance(it, dict) and it.get("id") and it.get("name")
                ]
            enabled = {str(x) for x in (payload.get("enabled") or []) if x}
            sync_set = {str(x) for x in (payload.get("sync") or sync_ids) if x}
            name_keep = {
                str(n)
                for n in (payload.get("enabled_names") or keep_names)
                if n
            }
            if name.strip():
                name_keep.add(name.strip())
            for it in calendars:
                if not isinstance(it, dict):
                    continue
                cid = str(it.get("id") or "")
                cname = str(it.get("name") or "")
                if cid and cname in name_keep:
                    enabled.add(cid)
            prefer_default = str(payload.get("default_id") or "")
            if prefer_default and prefer_default not in enabled and enabled:
                prefer_default = next(iter(enabled))
            new_id = str(payload.get("new_id") or "")
            if new_id:
                enabled.add(new_id)
                # 云端新建默认勾选同步；本地新建不可同步
                if not is_local_calendar_id(new_id):
                    sync_set.add(new_id)
            self._fill_calendars(
                calendars,
                enabled,
                prefer_default,
                sync_ids=sync_set,
                strict=True,
            )
            names = [
                str(self._calendar_names.get(cid) or "")
                for cid in enabled
                if self._calendar_names.get(cid)
            ]
            sync_out = [
                cid
                for cid in sync_set
                if cid in self._calendar_sync_checks and not is_local_calendar_id(cid)
            ]
            sync_names_out = [
                str(self._calendar_names.get(cid) or "")
                for cid in sync_out
                if self._calendar_names.get(cid)
            ]
            if self._on_icloud_set_enabled_calendars:
                self._on_icloud_set_enabled_calendars(
                    list(enabled), prefer_default, names, sync_out, sync_names_out
                )

        self._on_icloud_create_calendar(data, name, done)

    def _fill_countries(self, available: list[tuple[str, str]], selected: set[str]) -> None:
        was = self._suppress
        self._suppress = True
        try:
            while self._country_form.count():
                item = self._country_form.takeAt(0)
                w = item.widget()
                if w:
                    w.deleteLater()
            self._checks.clear()
            for code, label in available:
                cb = QCheckBox(f"{label}  ·  {code}")
                cb.setChecked(code in selected)
                cb.toggled.connect(lambda _c: self._emit_changed())
                self._checks[code] = cb
                self._country_form.addWidget(cb)
            self._country_form.addStretch(1)
        finally:
            self._suppress = was

    def replace_countries(self, available: object) -> None:
        if not isinstance(available, list):
            return
        selected = {code for code, cb in self._checks.items() if cb.isChecked()}
        pairs: list[tuple[str, str]] = []
        for item in available:
            if isinstance(item, (tuple, list)) and len(item) >= 2:
                pairs.append((str(item[0]), str(item[1])))
        if pairs:
            self._fill_countries(pairs, selected)

    def _on_scheme_toggled(self, preset_id: str, checked: bool) -> None:
        if not checked or self._suppress:
            return
        self._preset_id = preset_id
        self._theme = preset_theme(preset_id)
        self._emit_changed()

    def _on_opacity_changed(self, value: int) -> None:
        self.opacity_value.setText(f"{value}%")
        if not self._suppress:
            self._emit_changed()

    def selected_countries(self) -> list[str]:
        return [code for code, cb in self._checks.items() if cb.isChecked()]

    def opacity(self) -> float:
        return self.opacity_slider.value() / 100.0

    def week_start_day(self) -> int:
        data = self.week_starts_on.currentData()
        return normalize_week_starts_on(data if data is not None else 6)

    def theme(self) -> dict[str, str]:
        return dict(self._theme)

    def show_todolist(self) -> bool:
        return self.todolist_visible.isChecked()

    def start_on_boot(self) -> bool:
        return self.start_with_windows.isChecked()

    def closeEvent(self, event) -> None:  # noqa: ANN001
        if self._cal_filter_debounce.isActive():
            self._cal_filter_debounce.stop()
            self._flush_calendar_filter()
        if self._debounce.isActive():
            self._debounce.stop()
        if self._verify_in_progress:
            event.ignore()
            return
        if self._needs_verify_on_close():
            event.ignore()
            self._run_credential_verify(reason="close")
            return
        if not self._force_close:
            self._emit_changed()
        super().closeEvent(event)
