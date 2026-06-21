"""
ui/themes.py — Application colour themes (Light / Dark VS Code)

Usage:
    from ui.themes import build_stylesheet, theme_colors, THEME_NAMES, key_from_display
    app.setStyleSheet(build_stylesheet("vscode"))
    c = theme_colors("vscode")
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

ThemeName = Literal["vscode", "light"]
THEME_NAMES: list[str] = ["Dark (VS Code)", "Light"]
_THEME_KEYS: dict[str, str] = {"Dark (VS Code)": "vscode", "Light": "light"}

_RES = Path(__file__).parent / "resources"

_PALETTES: dict[str, dict[str, str]] = {
    "vscode": {
        "bg":         "#1e1e1e",
        "bg2":        "#252526",
        "bg3":        "#2d2d30",
        "bg4":        "#3e3e42",
        "fg":         "#d4d4d4",
        "fg_dim":     "#6a6a7a",
        "fg_mid":     "#858585",
        "accent":     "#4ec9b0",
        "accent_bg":  "rgba(78,201,176,.15)",
        "accent_brd": "rgba(78,201,176,.35)",
        "err":        "#f44747",
        "warn":       "#ffcc00",
        "ok":         "#4ec994",
        "tx":         "#ce9178",
        "terminal":   "#1e1e1e",
        "border":     "rgba(255,255,255,.09)",
        "border2":    "rgba(255,255,255,.14)",
        "splitter":   "rgba(255,255,255,.06)",
        "splitter_h": "rgba(78,201,176,.3)",
        "arrow_dim":  "#858585",
        "arrow_h":    "#4ec9b0",
        "scrollbar":  "#424242",
        "grid":       "rgba(255,255,255,.05)",
    },
    "light": {
        "bg":         "#f5f6fa",
        "bg2":        "#eceef5",
        "bg3":        "#e4e7f0",
        "bg4":        "#d8dbe8",
        "fg":         "#1e2235",
        "fg_dim":     "#606880",
        "fg_mid":     "#383f58",
        "accent":     "#0fa371",
        "accent_bg":  "rgba(15,163,113,.12)",
        "accent_brd": "rgba(15,163,113,.35)",
        "err":        "#dc2626",
        "warn":       "#d97706",
        "ok":         "#16a34a",
        "tx":         "#e05a2b",
        "terminal":   "#ffffff",
        "border":     "rgba(0,0,0,.12)",
        "border2":    "rgba(0,0,0,.20)",
        "splitter":   "rgba(0,0,0,.08)",
        "splitter_h": "rgba(15,163,113,.30)",
        "arrow_dim":  "#5a6380",
        "arrow_h":    "#0fa371",
        "scrollbar":  "#c8cbd8",
        "grid":       "rgba(0,0,0,.06)",
    },
}


def theme_colors(theme: ThemeName = "vscode") -> dict[str, str]:
    return _PALETTES.get(theme, _PALETTES["vscode"])


def _arrow_svg(color: str) -> str:
    return (
        '<svg width="8" height="5" viewBox="0 0 8 5" '
        'xmlns="http://www.w3.org/2000/svg">'
        f'<path d="M0 0L8 0L4 5Z" fill="{color}"/>'
        '</svg>'
    )


def _ensure_arrows(c: dict[str, str]) -> tuple[str, str]:
    _RES.mkdir(parents=True, exist_ok=True)
    dim_tag   = c["arrow_dim"].lstrip("#")
    hover_tag = c["arrow_h"].lstrip("#")
    p_dim   = _RES / f"arrow_{dim_tag}.svg"
    p_hover = _RES / f"arrow_{hover_tag}.svg"
    if not p_dim.exists():
        p_dim.write_text(_arrow_svg(c["arrow_dim"]), encoding="utf-8")
    if not p_hover.exists():
        p_hover.write_text(_arrow_svg(c["arrow_h"]), encoding="utf-8")
    return p_dim.as_posix(), p_hover.as_posix()


def build_stylesheet(theme: ThemeName = "vscode") -> str:
    c = _PALETTES.get(theme, _PALETTES["vscode"])
    url_dim, url_hover = _ensure_arrows(c)

    return f"""
/* ── Base ───────────────────────────────────────────────── */
QMainWindow, QDialog, QWidget {{
    background: {c['bg']}; color: {c['fg']};
    font-family: 'IBM Plex Sans', 'SF Pro Text', 'Segoe UI', system-ui, sans-serif;
    font-size: 12px;
}}
QLabel {{ background: transparent; }}
QCheckBox {{ background: transparent; }}
QSplitter::handle {{ background: {c['splitter']}; }}
QSplitter::handle:hover {{ background: {c['splitter_h']}; }}

/* ── Menubar ────────────────────────────────────────────── */
QMenuBar {{
    background: {c['bg2']}; color: {c['fg_mid']};
    border-bottom: 1px solid {c['border']};
    padding: 2px 0;
}}
QMenuBar::item {{ padding: 4px 10px; border-radius: 4px; }}
QMenuBar::item:selected {{ background: {c['bg4']}; color: {c['fg']}; }}
QMenu {{
    background: {c['bg3']}; color: {c['fg']};
    border: 1px solid {c['border2']};
    border-radius: 6px; padding: 4px;
}}
QMenu::item {{ padding: 5px 28px 5px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background: {c['bg4']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 8px; }}

/* ── Statusbar ──────────────────────────────────────────── */
QStatusBar {{
    background: {c['bg2']}; color: {c['fg_dim']};
    border-top: 1px solid {c['border']};
    font-family: 'JetBrains Mono', 'SF Mono', 'Cascadia Code', 'Consolas', monospace;
    font-size: 10px;
}}

/* ── Tabs ───────────────────────────────────────────────── */
QWidget#rightPanel {{ background: {c['bg2']}; }}
QTabWidget {{ background: {c['bg2']}; }}
QTabWidget::pane {{
    border: none; border-top: 1px solid {c['border']}; background: {c['bg']};
}}
QTabBar {{
    background: {c['bg2']};
    font-family: 'IBM Plex Sans', 'SF Pro Text', system-ui, sans-serif;
}}
QTabBar::tab {{
    background: {c['bg2']}; color: {c['fg_dim']};
    padding: 7px 14px; border: none;
    border-bottom: 2px solid transparent; font-size: 11px;
    min-width: 64px;
}}
QTabBar::tab:selected {{ color: {c['accent']}; border-bottom-color: {c['accent']}; font-weight: 600; }}
QTabBar::tab:hover:!selected {{ color: {c['fg_mid']}; background: {c['bg3']}; }}

/* ── Scrollbars ─────────────────────────────────────────── */
QScrollBar:vertical {{ background: transparent; width: 6px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: {c['scrollbar']}; border-radius: 3px; min-height: 24px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar:horizontal {{ background: transparent; height: 6px; margin: 2px; }}
QScrollBar::handle:horizontal {{
    background: {c['scrollbar']}; border-radius: 3px; min-width: 24px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
QScrollArea {{ border: none; background: transparent; }}

/* ── ComboBox ───────────────────────────────────────────── */
QComboBox {{
    background: {c['bg3']};
    border: 1px solid {c['border']};
    border-radius: 5px;
    padding: 3px 26px 3px 8px;
    color: {c['fg']};
    font-family: 'JetBrains Mono', 'SF Mono', 'Consolas', monospace;
    font-size: 11px;
    min-height: 24px;
    selection-background-color: {c['bg4']};
}}
QComboBox:focus {{ border-color: {c['accent']}; }}
QComboBox:hover {{ border-color: {c['border2']}; }}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 22px;
    border-left: 1px solid {c['border']};
    border-top-right-radius: 4px;
    border-bottom-right-radius: 4px;
    background: transparent;
}}
QComboBox::down-arrow {{ image: url({url_dim}); width: 8px; height: 5px; }}
QComboBox::down-arrow:hover {{ image: url({url_hover}); }}
QComboBox QAbstractItemView {{
    background: {c['bg3']};
    border: 1px solid {c['border2']};
    border-radius: 4px;
    color: {c['fg']};
    selection-background-color: {c['bg4']};
    outline: none; padding: 2px;
}}

/* ── LineEdit ───────────────────────────────────────────── */
QLineEdit {{
    background: {c['bg3']}; border: 1px solid {c['border']};
    border-radius: 5px; padding: 3px 8px;
    color: {c['fg']}; font-family: 'JetBrains Mono', 'SF Mono', 'Consolas', monospace;
    font-size: 11px; min-height: 24px;
}}
QLineEdit:focus {{ border-color: {c['accent']}; }}
QLineEdit:hover {{ border-color: {c['border2']}; }}

/* ── SpinBox ────────────────────────────────────────────── */
QSpinBox {{
    background: {c['bg3']}; border: 1px solid {c['border']};
    border-radius: 5px; padding: 3px 8px;
    color: {c['fg']}; font-family: 'JetBrains Mono', 'SF Mono', 'Consolas', monospace;
    font-size: 11px; min-height: 24px;
}}
QSpinBox:focus {{ border-color: {c['accent']}; }}
QSpinBox::up-button, QSpinBox::down-button {{
    width: 16px; border: none; background: {c['bg4']}; border-radius: 2px;
}}

/* ── Buttons ────────────────────────────────────────────── */
QPushButton {{
    background: {c['bg3']}; border: 1px solid {c['border']};
    border-radius: 5px; padding: 4px 12px; color: {c['fg_mid']};
    font-size: 12px;
}}
QPushButton:hover {{ border-color: {c['accent']}; color: {c['accent']}; background: {c['bg4']}; }}
QPushButton:pressed {{ background: {c['bg4']}; border-color: {c['accent']}; }}

/* Named semantic buttons */
QPushButton#connectBtn {{
    background: {c['accent_bg']}; color: {c['accent']};
    border-color: {c['accent_brd']}; font-weight: 600;
}}
QPushButton#disconnectBtn {{
    background: rgba(239,68,68,.15); color: {c['err']};
    border-color: rgba(239,68,68,.3); font-weight: 600;
}}
QPushButton#sendBtn {{
    background: {c['accent']}; color: {c['bg']}; border: none; font-weight: 700;
}}
QPushButton#add {{
    background: {c['accent_bg']}; color: {c['accent']};
    border-color: {c['accent_brd']};
}}
QPushButton#run {{
    background: {c['accent_bg']}; color: {c['accent']};
    border-color: {c['accent_brd']}; font-weight: 600;
}}
QPushButton#stop {{
    background: rgba(239,68,68,.15); color: {c['err']};
    border-color: rgba(239,68,68,.3); font-weight: 600;
}}
QPushButton#save {{
    background: {c['accent']}; color: {c['bg']}; border: none; font-weight: 700;
}}
QPushButton#save:hover {{ background: {c['accent']}; opacity: 0.85; }}
QPushButton#start {{
    background: {c['accent_bg']}; color: {c['accent']};
    border-color: {c['accent_brd']}; font-weight: 600;
}}

/* ── Checkboxes ─────────────────────────────────────────── */
QCheckBox {{ color: {c['fg_mid']}; spacing: 5px; }}
QCheckBox::indicator {{
    width: 12px; height: 12px; border-radius: 3px;
    border: 1px solid {c['border2']}; background: {c['bg3']};
}}
QCheckBox::indicator:checked {{
    background: {c['accent']}; border-color: {c['accent']};
}}

/* ── Labels ─────────────────────────────────────────────── */
QLabel#dimLabel     {{ color: {c['fg_dim']}; font-size: 11px; }}
QLabel#dimLabelMono {{
    color: {c['fg_dim']};
    font-family: 'JetBrains Mono', 'SF Mono', 'Consolas', monospace;
    font-size: 10px; letter-spacing: 0.5px;
}}
QLabel#sectionTitle {{
    color: {c['fg_mid']};
    font-family: 'IBM Plex Sans', 'SF Pro Text', system-ui, sans-serif;
    font-size: 10px; font-weight: 600; letter-spacing: 0.8px;
}}
QLabel#sectionArrow {{
    color: {c['accent']};
    font-size: 10px;
}}
QLabel#stat {{ color: {c['accent']}; font-family: 'JetBrains Mono', 'SF Mono', 'Consolas', monospace; font-size: 11px; }}
QLabel#path {{ color: {c['fg_dim']}; font-family: 'JetBrains Mono', 'SF Mono', 'Consolas', monospace; font-size: 10px; }}
QLabel#hint {{ color: {c['accent']}; font-family: 'JetBrains Mono', 'SF Mono', 'Consolas', monospace; font-size: 10px; }}

/* ── Colorizer rows ─────────────────────────────────────── */
/* ── Accordion section header ────────────────────────────── */
QWidget#sectionHeader {{
    background: {c['bg3']};
    border-bottom: 1px solid {c['border']};
    border-top: 1px solid {c['border']};
    min-height: 30px;
}}
QWidget#sectionHeader:hover {{ background: {c['bg4']}; }}

/* ── Indicator cards ─────────────────────────────────────── */
QFrame#indicatorCard {{
    background: {c['bg3']};
    border: 1px solid {c['border2']};
    border-radius: 6px;
}}

/* ── Trigger events table ────────────────────────────────── */
QTableWidget {{
    background: {c['bg2']}; color: {c['fg']};
    border: none; gridline-color: {c['border']};
    font-size: 10px;
}}
QTableWidget::item {{ padding: 2px 4px; }}
QTableWidget::item:alternate {{ background: {c['bg3']}; }}
QHeaderView::section {{
    background: {c['bg3']}; color: {c['fg_dim']};
    border: none; border-right: 1px solid {c['border']};
    padding: 3px 6px;
    font-family: 'JetBrains Mono'; font-size: 9px;
}}

/* ── Colorizer rows ─────────────────────────────────────── */
QWidget#colorizerRow {{
    background: {c['bg3']}; border-radius: 6px;
}}
QWidget#colorizerRow:hover {{ background: {c['bg4']}; }}

/* ── Custom command text area ───────────────────────────── */
QTextEdit#customCmd {{
    background: {c['bg3']};
    border: 1px solid {c['border']};
    border-radius: 4px;
    color: {c['accent']};
    padding: 4px;
}}

/* ── Dividers ────────────────────────────────────────────── */
QFrame[frameShape="4"] {{
    background: {c['border']}; max-height: 1px; border: none;
}}

/* ── Transparent icon buttons ───────────────────────────── */
QPushButton#iconBtn {{
    background: transparent; border: none;
    color: {c['fg_dim']}; padding: 0;
}}
QPushButton#iconBtn:hover {{ color: {c['accent']}; background: transparent; }}
QPushButton#delBtn {{
    background: transparent; border: none;
    color: {c['err']}; padding: 0; font-weight: 700;
}}
QPushButton#delBtn:hover {{ color: {c['err']}; background: transparent; }}

/* ── Share / Join buttons ───────────────────────────────── */
QPushButton#shareBtn {{
    background: {c['bg3']}; border: 1px solid {c['border2']};
    border-radius: 5px; color: {c['fg_mid']}; font-size: 11px;
}}
QPushButton#shareBtn:hover {{
    background: {c['accent_bg']}; border-color: {c['accent_brd']};
    color: {c['accent']};
}}
QPushButton#joinBtn {{
    background: {c['bg3']}; border: 1px solid {c['border2']};
    border-radius: 5px; color: {c['fg_mid']}; font-size: 11px;
}}
QPushButton#joinBtn:hover {{
    background: {c['accent_bg']}; border-color: {c['accent_brd']};
    color: {c['accent']};
}}
QPushButton#stopShareBtn {{
    background: rgba(239,68,68,.15); color: {c['err']};
    border: 1px solid rgba(239,68,68,.3); border-radius: 5px; font-size: 11px;
}}
QPushButton#stopShareBtn:hover {{
    background: rgba(239,68,68,.25);
}}

/* ── Panel toggle button ────────────────────────────────── */
QPushButton#panelToggleBtn {{
    background: {c['bg4']}; border: 1px solid {c['border2']};
    border-radius: 5px; color: {c['fg_mid']};
    font-size: 14px; padding: 0;
}}
QPushButton#panelToggleBtn:hover {{
    background: {c['accent_bg']}; border-color: {c['accent_brd']};
    color: {c['accent']};
}}
QPushButton#panelToggleBtn:pressed {{ background: {c['bg3']}; }}

/* ── Named areas ────────────────────────────────────────── */
QWidget#portBar {{
    background: {c['bg2']}; border-bottom: 1px solid {c['border']};
}}
QWidget#inputBar {{
    background: {c['bg2']}; border-top: 1px solid {c['border']};
}}
QWidget#searchBar {{
    background: {c['bg2']}; border-top: 1px solid {c['border']};
}}
QLineEdit#searchEdit {{
    border: 1px solid {c['accent_brd']};
    background: {c['terminal']};
    font-family: 'JetBrains Mono', 'SF Mono', 'Consolas', monospace; font-size: 11px;
}}
QPushButton#searchNav {{
    padding: 0; font-size: 14px; color: {c['fg']};
}}
QPushButton#searchNav:hover {{ color: {c['accent']}; border-color: {c['accent']}; }}

/* Parser strip container: transparent so it shows inputBar's bg2 (matches portBar)
   instead of the darker default QWidget background */
QWidget#parserStrip {{ background: transparent; }}

/* Parser-strip fields share the search field's background */
QLineEdit#parserField, QComboBox#parserField {{
    background: {c['terminal']};
}}
QWidget#sidebar {{
    background: {c['bg2']}; border-left: 1px solid {c['border']};
}}
QWidget#sidebarSection {{
    background: transparent;
    border-bottom: 1px solid {c['border']};
}}
QWidget#triggerHeader, QWidget#macroHeader {{
    background: {c['bg3']};
    border-bottom: 1px solid {c['border']};
}}
QTextEdit#terminal {{
    background: {c['terminal']}; border: none; padding: 8px 10px;
    font-family: 'JetBrains Mono', 'SF Mono', 'Cascadia Code', 'Consolas', monospace;
}}
QLineEdit#cmdEdit {{
    border: 1px solid {c['accent_brd']};
    color: {c['accent']};
    background: {c['terminal']};
    font-family: 'JetBrains Mono', 'SF Mono', 'Consolas', monospace; font-size: 11px;
}}

/* ── Table (MacroEditor) ────────────────────────────────── */
QTableWidget {{
    background: {c['bg']}; border: none;
    gridline-color: {c['grid']};
    color: {c['fg']}; font-family: 'JetBrains Mono'; font-size: 10px;
}}
QTableWidget::item {{ padding: 2px 5px; }}
QTableWidget::item:selected {{ background: {c['bg4']}; color: {c['fg']}; }}
QHeaderView::section {{
    background: {c['bg2']}; color: {c['fg_dim']};
    font-size: 9px; letter-spacing: 0.5px;
    padding: 3px 5px; border: none;
    border-right: 1px solid {c['border']};
}}

/* ── Update banner ──────────────────────────────────────────── */
QWidget#updateBanner {{
    background: {c['accent_bg']};
    border-bottom: 1px solid {c['accent_brd']};
}}
QLabel#updateLabel {{
    color: {c['accent']};
    font-family: 'JetBrains Mono'; font-size: 11px;
}}
"""


def key_from_display(display_name: str) -> ThemeName:
    return _THEME_KEYS.get(display_name, "vscode")  # type: ignore[return-value]


_current_theme: ThemeName = "vscode"


def set_current_theme(theme: ThemeName) -> None:
    global _current_theme
    _current_theme = theme


def tint_titlebar(widget) -> None:
    """Apply DWM title-bar color matching the active theme (Windows only)."""
    import sys
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes.wintypes import DWORD
    c = _PALETTES.get(_current_theme, _PALETTES["vscode"])
    hwnd = int(widget.winId())
    dwmapi = ctypes.windll.dwmapi
    dark = DWORD(1 if _current_theme == "vscode" else 0)
    dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))
    bg = c["bg"].lstrip("#")
    r, g, b = int(bg[0:2], 16), int(bg[2:4], 16), int(bg[4:6], 16)
    color = DWORD(r | (g << 8) | (b << 16))
    dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(color), ctypes.sizeof(color))
