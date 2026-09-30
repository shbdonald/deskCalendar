# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

# Base keys every theme must provide.
THEME_KEYS: tuple[str, ...] = (
    "bg",
    "text",
    "muted",
    "accent",
    "holiday",
    "cell",
    "cell_today",
    "btn",
    "btn_hover",
    "border",
)

DEFAULT_THEME: dict[str, str] = {
    "bg": "#1A1F26",
    "text": "#EEF2F6",
    "muted": "#8B97A8",
    "accent": "#3D8BFF",
    "holiday": "#E6B422",
    "cell": "#242B34",
    "cell_today": "#2A3F5C",
    "btn": "#2A313C",
    "btn_hover": "#3A4554",
    "border": "#00000000",
}

# Popular ready-made schemes — pick by id in settings.
THEME_PRESETS: dict[str, dict[str, str]] = {
    "ink_night": {
        **DEFAULT_THEME,
    },
    "graphite": {
        "bg": "#121212",
        "text": "#E8E8E8",
        "muted": "#9A9A9A",
        "accent": "#64B5F6",
        "holiday": "#FFB74D",
        "cell": "#1E1E1E",
        "cell_today": "#263238",
        "btn": "#2C2C2C",
        "btn_hover": "#3A3A3A",
        "border": "#00000000",
    },
    "midnight_teal": {
        "bg": "#0D1B1E",
        "text": "#E6F2F1",
        "muted": "#7FA6A3",
        "accent": "#2DD4BF",
        "holiday": "#FBBF24",
        "cell": "#143033",
        "cell_today": "#1A4A4E",
        "btn": "#1B3A3D",
        "btn_hover": "#245458",
        "border": "#00000000",
    },
    "forest": {
        "bg": "#121A14",
        "text": "#E8F0E9",
        "muted": "#8FA594",
        "accent": "#4ADE80",
        "holiday": "#F59E0B",
        "cell": "#1A261C",
        "cell_today": "#243D2A",
        "btn": "#223028",
        "btn_hover": "#2E4034",
        "border": "#00000000",
    },
    "sakura_night": {
        "bg": "#1A1218",
        "text": "#F8EAF0",
        "muted": "#B08A9C",
        "accent": "#F472B6",
        "holiday": "#FBBF24",
        "cell": "#261820",
        "cell_today": "#3D2434",
        "btn": "#2E1E28",
        "btn_hover": "#3F2A36",
        "border": "#00000000",
    },
    "violet_dusk": {
        "bg": "#16121F",
        "text": "#EDE8F7",
        "muted": "#9B8FB5",
        "accent": "#A78BFA",
        "holiday": "#FBBF24",
        "cell": "#211A2E",
        "cell_today": "#32244A",
        "btn": "#2A2238",
        "btn_hover": "#3A3050",
        "border": "#00000000",
    },
    "amber_ember": {
        "bg": "#1A1410",
        "text": "#F5EDE4",
        "muted": "#B49A82",
        "accent": "#F59E0B",
        "holiday": "#FB7185",
        "cell": "#261C14",
        "cell_today": "#3D2A18",
        "btn": "#2E2218",
        "btn_hover": "#403020",
        "border": "#00000000",
    },
    "ocean": {
        "bg": "#0B1520",
        "text": "#E8F1F8",
        "muted": "#7F9BB0",
        "accent": "#38BDF8",
        "holiday": "#FBBF24",
        "cell": "#122030",
        "cell_today": "#1A3350",
        "btn": "#182838",
        "btn_hover": "#243848",
        "border": "#00000000",
    },
    "paper": {
        "bg": "#F4F1EA",
        "text": "#1C1917",
        "muted": "#78716C",
        "accent": "#2563EB",
        "holiday": "#C2410C",
        "cell": "#FFFFFF",
        "cell_today": "#DBEAFE",
        "btn": "#E7E5E4",
        "btn_hover": "#D6D3D1",
        "border": "#00000000",
    },
    "mint_cream": {
        "bg": "#F0F7F4",
        "text": "#14352C",
        "muted": "#5F7F74",
        "accent": "#0D9488",
        "holiday": "#D97706",
        "cell": "#FFFFFF",
        "cell_today": "#CCFBF1",
        "btn": "#D7EBE3",
        "btn_hover": "#BFE0D4",
        "border": "#00000000",
    },
}

THEME_PRESET_LABELS: dict[str, str] = {
    "ink_night": "墨夜蓝",
    "graphite": "石墨黑",
    "midnight_teal": "深青",
    "forest": "森林绿",
    "sakura_night": "夜樱",
    "violet_dusk": "暮紫",
    "amber_ember": "琥珀",
    "ocean": "海雾蓝",
    "paper": "纸白",
    "mint_cream": "薄荷白",
}


def merge_theme(raw: dict[str, Any] | None) -> dict[str, str]:
    theme = dict(DEFAULT_THEME)
    if isinstance(raw, dict):
        for key in THEME_KEYS:
            val = raw.get(key)
            if isinstance(val, str) and val.strip():
                theme[key] = val.strip()
    return theme


def preset_theme(preset_id: str) -> dict[str, str]:
    base = THEME_PRESETS.get(preset_id) or DEFAULT_THEME
    return merge_theme(base)


def match_preset_id(theme: dict[str, Any] | None) -> str | None:
    """Return preset id if theme colors match a known scheme."""
    current = merge_theme(theme)
    for pid, preset in THEME_PRESETS.items():
        full = merge_theme(preset)
        if all(current.get(k) == full.get(k) for k in THEME_KEYS):
            return pid
    return None


def preset_swatches(preset_id: str) -> list[str]:
    """Few representative colors for a scheme button strip."""
    t = preset_theme(preset_id)
    return [t["bg"], t["cell"], t["accent"], t["holiday"], t["text"]]
