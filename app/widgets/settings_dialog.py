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

from app.services.icloud_calendar_sync import DEFAULT_CALENDAR_NAME
from app.services.theme import (
    THEME_PRESET_LABELS,
    THEME_PRESETS,
    match_preset_id,
    merge_theme,
    preset_swatches,
    preset_theme,
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
        on_icloud_set_enabled_calendars: Callable[[list[str], str, list[str]], None]
        | None = None,
        on_clear_plans: Callable[[], None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumSize(480, 560)
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
        self._calendar_names: dict[str, str] = {}
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

        self.todolist_visible = QCheckBox("显示今日待办窗口")
        self.todolist_visible.setChecked(bool(icloud.get("todolist_visible", True)))
        self.todolist_visible.toggled.connect(lambda _c: self._emit_changed())
        lay.addWidget(self.todolist_visible)
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

    def _build_icloud_tab(self, icloud: dict) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(8)

        help_lbl = QLabel(
            "使用 Apple「应用专用密码」。校验通过后勾选要同步的日历；"
            "通讯录「生日」等只读日历不会列出。公共假日仍由 Nager 显示。"
        )
        help_lbl.setWordWrap(True)
        help_lbl.setStyleSheet("color:#A8B0BC; font-size:11px;")
        lay.addWidget(help_lbl)

        # —— 账号区：添加账号 / 输入+校验 / 锁定+修改 ——
        self._add_account_btn = QPushButton("添加账号")
        self._add_account_btn.clicked.connect(self._on_add_account)
        lay.addWidget(self._add_account_btn)

        self._account_form_widget = QWidget()
        account_form = QFormLayout(self._account_form_widget)
        account_form.setContentsMargins(0, 0, 0, 0)

        self.icloud_apple_id = QLineEdit()
        self.icloud_apple_id.setPlaceholderText("Apple ID 邮箱")
        self.icloud_apple_id.setText(str(icloud.get("apple_id", "")))
        account_form.addRow("Apple ID", self.icloud_apple_id)

        self.icloud_password = QLineEdit()
        self.icloud_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.icloud_password.setPlaceholderText("应用专用密码")
        self.icloud_password.setText(str(icloud.get("app_password", "")))
        account_form.addRow("专用密码", self.icloud_password)

        cred_btn_row = QHBoxLayout()
        self._cred_action_btn = QPushButton("校验")
        self._cred_action_btn.clicked.connect(self._on_cred_action)
        self._clear_account_btn = QPushButton("清除账号密码")
        self._clear_account_btn.setToolTip("删除已保存的 Apple ID 与专用密码")
        self._clear_account_btn.clicked.connect(self._on_clear_account)
        self._clear_account_btn.setVisible(False)
        cred_btn_row.addWidget(self._cred_action_btn)
        cred_btn_row.addWidget(self._clear_account_btn)
        cred_btn_row.addStretch(1)
        account_form.addRow("", cred_btn_row)
        lay.addWidget(self._account_form_widget)

        icloud_form = QFormLayout()
        self.icloud_enabled = QCheckBox("启用同步")
        self.icloud_enabled.setChecked(bool(icloud.get("enabled", False)))
        self.icloud_enabled.toggled.connect(lambda _c: self._emit_changed())
        icloud_form.addRow("", self.icloud_enabled)

        self.icloud_poll = QSpinBox()
        self.icloud_poll.setRange(15, 600)
        self.icloud_poll.setSingleStep(1)
        self.icloud_poll.setSuffix(" 秒")
        self.icloud_poll.setValue(int(icloud.get("poll_seconds", 45) or 45))
        self.icloud_poll.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.icloud_poll.setAccelerated(True)
        self.icloud_poll.valueChanged.connect(lambda _v: self._emit_changed())
        poll_row = QHBoxLayout()
        poll_row.setContentsMargins(0, 0, 0, 0)
        poll_row.setSpacing(4)
        poll_row.addWidget(self.icloud_poll, 1)
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
            poll_row.addWidget(btn)
        icloud_form.addRow("轮询间隔", poll_row)
        lay.addLayout(icloud_form)

        cal_header = QHBoxLayout()
        cal_header.addWidget(QLabel("同步日历（可多选）"), 1)
        self._cal_refresh_btn = QPushButton("刷新列表")
        self._cal_refresh_btn.clicked.connect(self._on_refresh_calendars)
        cal_header.addWidget(self._cal_refresh_btn)
        lay.addLayout(cal_header)

        cal_scroll = QScrollArea()
        cal_scroll.setWidgetResizable(True)
        cal_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        cal_scroll.setMinimumHeight(100)
        cal_scroll.setMaximumHeight(180)
        self._cal_holder = QWidget()
        self._cal_form = QVBoxLayout(self._cal_holder)
        self._cal_form.setContentsMargins(0, 0, 4, 0)
        cal_scroll.setWidget(self._cal_holder)
        lay.addWidget(cal_scroll)

        new_row = QHBoxLayout()
        self._new_cal_name = QLineEdit()
        self._new_cal_name.setPlaceholderText("新建日历名称")
        self._new_cal_btn = QPushButton("新建")
        self._new_cal_btn.clicked.connect(self._on_create_calendar)
        new_row.addWidget(self._new_cal_name, 1)
        new_row.addWidget(self._new_cal_btn)
        lay.addLayout(new_row)

        default_row = QFormLayout()
        self.default_calendar = QComboBox()
        self.default_calendar.setMinimumWidth(160)
        self.default_calendar.currentIndexChanged.connect(lambda _i: self._emit_changed())
        default_row.addRow("新建计划默认", self.default_calendar)
        lay.addLayout(default_row)

        enabled = [str(x) for x in (icloud.get("calendars_enabled") or []) if x]
        default_id = str(icloud.get("default_calendar_id") or "")
        calendars = icloud.get("calendars") or []
        if isinstance(calendars, list) and calendars:
            self._fill_calendars(calendars, set(enabled), default_id)
        else:
            empty = QLabel("校验账号后将拉取可同步日历")
            empty.setStyleSheet("color:#A8B0BC; font-size:11px;")
            self._cal_form.addWidget(empty)
            self._cal_form.addStretch(1)

        sync_row = QHBoxLayout()
        self._icloud_sync_btn = QPushButton("立即同步")
        self._icloud_sync_btn.clicked.connect(self._on_sync_now)
        self._icloud_busy_label = QLabel("")
        self._icloud_busy_label.setStyleSheet("color:#FBBF24; font-size:11px;")
        sync_row.addWidget(self._icloud_sync_btn)
        sync_row.addWidget(self._icloud_busy_label, 1)
        lay.addLayout(sync_row)
        lay.addStretch(1)

        has_id = bool(str(icloud.get("apple_id", "")).strip())
        has_pw = bool(str(icloud.get("app_password", "")).strip())
        if has_id and has_pw:
            # 已有账号：视为已校验锁定
            self._set_account_mode("locked", verified=True)
        else:
            self._set_account_mode("empty", verified=False)
        return page

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

        clear_plans = False
        plans_reply = QMessageBox.question(
            self,
            "清除日历计划",
            "是否同时清除本地全部日历计划？\n"
            "选择「是」将删除本机已有计划（不可恢复）；\n"
            "选择「否」仅清除账号，保留本地计划。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if plans_reply == QMessageBox.StandardButton.Yes:
            clear_plans = True

        self.icloud_enabled.setChecked(False)
        self._set_account_mode("empty", verified=False)
        # 清空日历勾选区
        while self._cal_form.count():
            item = self._cal_form.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._calendar_checks.clear()
        self._calendar_names.clear()
        empty = QLabel("校验账号后将拉取可同步日历")
        empty.setStyleSheet("color:#A8B0BC; font-size:11px;")
        self._cal_form.addWidget(empty)
        self._cal_form.addStretch(1)
        self.default_calendar.blockSignals(True)
        self.default_calendar.clear()
        self.default_calendar.blockSignals(False)
        self._emit_changed()
        if clear_plans and self._on_clear_plans:
            self._on_clear_plans()

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
                self._fill_calendars(payload, all_ids)
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
        enabled = [cid for cid, cb in self._calendar_checks.items() if cb.isChecked()]
        enabled_names = [
            self._calendar_names.get(cid, "")
            for cid in enabled
            if self._calendar_names.get(cid)
        ]
        default_id = str(self.default_calendar.currentData() or "")
        if default_id and default_id not in enabled and enabled:
            default_id = enabled[0]
        elif not default_id and enabled:
            default_id = enabled[0]
        default_name = self._calendar_names.get(default_id, self._legacy_calendar_name)
        return {
            "enabled": self.icloud_enabled.isChecked(),
            "apple_id": self.icloud_apple_id.text().strip(),
            "app_password": self.icloud_password.text().strip(),
            "calendar_name": default_name or DEFAULT_CALENDAR_NAME,
            "calendars_enabled": enabled,
            "calendars_enabled_names": enabled_names,
            "default_calendar_id": default_id,
            "poll_seconds": int(self.icloud_poll.value()),
        }

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
        strict: bool = False,
    ) -> None:
        was = self._suppress
        self._suppress = True
        try:
            # 先记下勾选，再拆 UI（deleteLater 后 isChecked 不可靠）
            if strict:
                # 严格模式：只按传入 enabled 勾选（新建后与过滤配置对齐）
                prev_enabled: set[str] = set()
                prev_names: set[str] = set()
            else:
                prev_enabled = self._checked_calendar_ids()
                prev_names = (
                    keep_checked_names
                    if keep_checked_names is not None
                    else self._checked_calendar_names()
                )
                if prev_enabled:
                    enabled = set(enabled) | prev_enabled

            while self._cal_form.count():
                item = self._cal_form.takeAt(0)
                w = item.widget()
                if w:
                    w.deleteLater()
            self._calendar_checks.clear()
            self._calendar_names.clear()
            for item in calendars:
                if not isinstance(item, dict):
                    continue
                cid = str(item.get("id") or "")
                name = str(item.get("name") or "")
                if not cid or not name:
                    continue
                self._calendar_names[cid] = name
                cb = QCheckBox(name)
                if strict:
                    checked = cid in enabled
                else:
                    checked = cid in enabled or name in prev_names
                    if not enabled and not prev_names and name == self._legacy_calendar_name:
                        checked = True
                cb.blockSignals(True)
                cb.setChecked(checked)
                cb.blockSignals(False)
                cb.toggled.connect(self._on_calendar_toggled)
                self._calendar_checks[cid] = cb
                self._cal_form.addWidget(cb)
            if not self._calendar_checks:
                empty = QLabel("未发现可写日历")
                empty.setStyleSheet("color:#A8B0BC; font-size:11px;")
                self._cal_form.addWidget(empty)
            self._cal_form.addStretch(1)
            self._rebuild_default_combo(default_id)
        finally:
            self._suppress = was

    def _on_calendar_toggled(self, _checked: bool = False) -> None:
        if self._suppress:
            return
        self._rebuild_default_combo()
        # 不走完整 _emit_changed（会重样式/断开/重载定时器），只更新过滤
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
        default_id = str(self.default_calendar.currentData() or "")
        if self._on_icloud_set_enabled_calendars:
            self._on_icloud_set_enabled_calendars(enabled, default_id, names)
        else:
            self._emit_changed()

    def _rebuild_default_combo(self, prefer_id: str = "") -> None:
        if not hasattr(self, "default_calendar"):
            return
        current = prefer_id or str(self.default_calendar.currentData() or "")
        self.default_calendar.blockSignals(True)
        self.default_calendar.clear()
        for cid, cb in self._calendar_checks.items():
            if cb.isChecked():
                self.default_calendar.addItem(self._calendar_names.get(cid, cid), cid)
        idx = self.default_calendar.findData(current)
        if idx >= 0:
            self.default_calendar.setCurrentIndex(idx)
        elif self.default_calendar.count() > 0:
            self.default_calendar.setCurrentIndex(0)
        self.default_calendar.blockSignals(False)

    def _set_icloud_busy(self, busy: bool, text: str = "") -> None:
        self._cred_action_btn.setEnabled(not busy)
        self._clear_account_btn.setEnabled(not busy)
        self._add_account_btn.setEnabled(not busy)
        self._icloud_sync_btn.setEnabled(not busy)
        self._cal_refresh_btn.setEnabled(not busy)
        self._new_cal_btn.setEnabled(not busy)
        self._icloud_busy_label.setText(text if busy else "")

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
                enabled = set(keep_ids)
                if not enabled and not keep_names:
                    enabled = {
                        str(it.get("id"))
                        for it in payload
                        if isinstance(it, dict) and it.get("id")
                    }
                self._fill_calendars(payload, enabled, keep_checked_names=keep_names)
                self._emit_changed()

        self._on_icloud_refresh_calendars(self.icloud_settings(), done)

    def _on_create_calendar(self) -> None:
        if not self._creds_verified:
            QMessageBox.information(self, "提示", "请先校验 Apple 账号")
            return
        if not self._on_icloud_create_calendar:
            return
        name = self._new_cal_name.text().strip()
        if not name:
            QMessageBox.information(self, "提示", "请输入日历名称")
            return
        # 同时固化 id 与名称：刷新后 id 可能变，名称用于回填勾选
        keep_ids = list(self._checked_calendar_ids())
        keep_names = [n for n in self._checked_calendar_names() if n]
        default_id = str(self.default_calendar.currentData() or "")
        data = self.icloud_settings()
        data["calendars_enabled"] = keep_ids
        data["calendars_enabled_names"] = keep_names
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
            enabled = {str(x) for x in (payload.get("enabled") or []) if x}
            # 再按名称兜底：防止返回的 enabled 漏了原勾选
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
            self._fill_calendars(
                calendars,
                enabled,
                prefer_default,
                strict=True,
            )
            # 勾选 UI 与过滤配置必须同一份 enabled（按 live id）
            names = [
                str(self._calendar_names.get(cid) or "")
                for cid in enabled
                if self._calendar_names.get(cid)
            ]
            if self._on_icloud_set_enabled_calendars:
                self._on_icloud_set_enabled_calendars(
                    list(enabled), prefer_default, names
                )

        self._on_icloud_create_calendar(data, name, done)

    def _on_sync_now(self) -> None:
        if not self._creds_verified:
            QMessageBox.information(self, "提示", "请先校验 Apple 账号")
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

    def theme(self) -> dict[str, str]:
        return dict(self._theme)

    def show_todolist(self) -> bool:
        return self.todolist_visible.isChecked()

    def closeEvent(self, event) -> None:  # noqa: ANN001
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
