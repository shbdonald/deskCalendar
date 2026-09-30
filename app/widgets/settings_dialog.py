# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from app.services.theme import (
    THEME_PRESET_LABELS,
    THEME_PRESETS,
    match_preset_id,
    merge_theme,
    preset_swatches,
    preset_theme,
)


class SettingsDialog(QDialog):
    def __init__(
        self,
        *,
        countries: list[str],
        available: list[tuple[str, str]],
        opacity: float,
        theme: dict[str, str] | None = None,
        on_opacity_preview: Callable[[float], None] | None = None,
        on_theme_preview: Callable[[dict[str, str]], None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumSize(400, 560)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.Window
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self._on_opacity_preview = on_opacity_preview
        self._on_theme_preview = on_theme_preview
        self._opacity_start = opacity
        self._theme = merge_theme(theme)
        self._theme_start = dict(self._theme)
        self._matched_preset = match_preset_id(self._theme)
        self._preset_id = self._matched_preset or "ink_night"

        root = QVBoxLayout(self)
        root.setSpacing(10)

        root.addWidget(QLabel("节假日国家（可多选）"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._country_holder = QWidget()
        self._country_form = QVBoxLayout(self._country_holder)
        self._checks: dict[str, QCheckBox] = {}
        self._fill_countries(available, {c.upper() for c in countries})
        scroll.setWidget(self._country_holder)
        root.addWidget(scroll, 1)

        root.addWidget(QLabel("配色方案（点击即预览）"))
        scheme_scroll = QScrollArea()
        scheme_scroll.setWidgetResizable(True)
        scheme_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scheme_scroll.setMinimumHeight(220)
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
            # Simple multi-block swatch via nested borders is awkward; use qlineargradient.
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
        root.addWidget(scheme_scroll, 1)

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
        root.addLayout(op_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self._on_reject)
        root.addWidget(buttons)

        self.setStyleSheet(
            """
            QDialog { background:#1A1F26; color:#EEF2F6; }
            QLabel { color:#EEF2F6; }
            QCheckBox, QRadioButton { color:#EEF2F6; spacing:8px; }
            QPushButton {
                background:#2A313C; color:#EEF2F6; border:none; border-radius:2px; padding:6px 10px;
            }
            QPushButton:hover { background:#3A4554; }
            QDialogButtonBox QPushButton {
                background:#2A313C; color:#EEF2F6; border:none; border-radius:2px; padding:6px 10px;
            }
            QScrollArea { border:none; background:transparent; }
            """
        )

    def _fill_countries(self, available: list[tuple[str, str]], selected: set[str]) -> None:
        while self._country_form.count():
            item = self._country_form.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._checks.clear()
        for code, label in available:
            cb = QCheckBox(f"{label}  ·  {code}")
            cb.setChecked(code in selected)
            self._checks[code] = cb
            self._country_form.addWidget(cb)
        self._country_form.addStretch(1)

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

    def _emit_theme_preview(self) -> None:
        if self._on_theme_preview:
            self._on_theme_preview(dict(self._theme))

    def _on_scheme_toggled(self, preset_id: str, checked: bool) -> None:
        if not checked:
            return
        self._preset_id = preset_id
        self._theme = preset_theme(preset_id)
        self._emit_theme_preview()

    def _on_opacity_changed(self, value: int) -> None:
        self.opacity_value.setText(f"{value}%")
        if self._on_opacity_preview:
            self._on_opacity_preview(value / 100.0)

    def _on_reject(self) -> None:
        if self._on_opacity_preview:
            self._on_opacity_preview(self._opacity_start)
        if self._on_theme_preview:
            self._on_theme_preview(dict(self._theme_start))
        self.reject()

    def selected_countries(self) -> list[str]:
        return [code for code, cb in self._checks.items() if cb.isChecked()]

    def opacity(self) -> float:
        return self.opacity_slider.value() / 100.0

    def theme(self) -> dict[str, str]:
        return dict(self._theme)
