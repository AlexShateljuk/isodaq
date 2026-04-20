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
    font-family: 'IBM Plex Sans'; font-size: 12px;
}}
/* Labels and checkboxes must be transparent so parent containers show through */
QLabel {{ background: transparent; }}
QCheckBox {{ background: transparent; }}
QSplitter::handle {{ background: {c['splitter']}; }}
QSplitter::handle:hover {{ background: {c['splitter_h']}; }}

/* ── Menubar ────────────────────────────────────────────── */
QMenuBar {{
    background: {c['bg2']}; color: {c['fg_mid']};
    border-bottom: 1px solid {c['border']};
}}
QMenuBar::item:selected {{ background: {c['bg4']}; color: {c['fg']}; }}
QMenu {{
    background: {c['bg3']}; color: {c['fg']};
    border: 1px solid {c['border2']};
}}
QMenu::item:selected {{ background: {c['bg4']}; }}

/* ── Statusbar ──────────────────────────────────────────── */
QStatusBar {{
    background: {c['bg2']}; color: {c['fg_dim']};
    border-top: 1px solid {c['border']};
    font-family: 'JetBrains Mono'; font-size: 10px;
}}

/* ── Tabs ───────────────────────────────────────────────── */
QTabWidget::pane {{ border: none; background: {c['bg']}; }}
QTabBar::tab {{
    background: {c['bg2']}; color: {c['fg_dim']};
    padding: 6px 16px; border: none;
    border-bottom: 2px solid transparent; font-size: 11px;
}}
QTabBar::tab:selected {{ color: {c['accent']}; border-bottom-color: {c['accent']}; }}
QTabBar::tab:hover {{ color: {c['fg_mid']}; }}

/* ── Scrollbars ─────────────────────────────────────────── */
QScrollBar:vertical {{ background: {c['bg']}; width: 5px; }}
QScrollBar::handle:vertical {{
    background: {c['scrollbar']}; border-radius: 2px; min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollArea {{ border: none; background: transparent; }}

/* ── ComboBox ───────────────────────────────────────────── */
QComboBox {{
    background: {c['bg3']};
    border: 1px solid {c['border']};
    border-radius: 4px;
    padding: 3px 26px 3px 7px;
    color: {c['fg']};
    font-family: 'JetBrains Mono';
    font-size: 11px;
    min-height: 22px;
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
    border-radius: 4px; padding: 3px 7px;
    color: {c['fg']}; font-family: 'JetBrains Mono';
    font-size: 11px; min-height: 22px;
}}
QLineEdit:focus {{ border-color: {c['accent']}; }}

/* ── SpinBox ────────────────────────────────────────────── */
QSpinBox {{
    background: {c['bg3']}; border: 1px solid {c['border']};
    border-radius: 4px; padding: 3px 7px;
    color: {c['fg']}; font-family: 'JetBrains Mono';
    font-size: 11px; min-height: 22px;
}}
QSpinBox:focus {{ border-color: {c['accent']}; }}
QSpinBox::up-button, QSpinBox::down-button {{
    width: 16px; border: none; background: {c['bg4']};
}}

/* ── Buttons ────────────────────────────────────────────── */
QPushButton {{
    background: {c['bg3']}; border: 1px solid {c['border']};
    border-radius: 5px; padding: 4px 11px; color: {c['fg_mid']};
}}
QPushButton:hover {{ border-color: {c['accent']}; color: {c['accent']}; }}
QPushButton:pressed {{ background: {c['bg4']}; }}

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
QLabel#dimLabel     {{ color: {c['fg_dim']}; }}
QLabel#dimLabelMono {{
    color: {c['fg_dim']};
    font-family: 'JetBrains Mono'; font-size: 9px; letter-spacing: 1px;
}}
QLabel#sectionTitle {{
    color: {c['fg_dim']};
    font-family: 'JetBrains Mono'; font-size: 9px; letter-spacing: 1px;
}}
QLabel#stat {{ color: {c['accent']}; font-family: 'JetBrains Mono'; font-size: 11px; }}
QLabel#path {{ color: {c['fg_dim']}; font-family: 'JetBrains Mono'; font-size: 10px; }}
QLabel#hint {{ color: {c['accent']}; font-family: 'JetBrains Mono'; font-size: 10px; }}

/* ── Colorizer rows ─────────────────────────────────────── */
/* ── Accordion section header ────────────────────────────── */
QWidget#sectionHeader {{
    background: {c['bg3']};
    border-bottom: 1px solid {c['border']};
    border-top: 1px solid {c['border']};
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

/* ── Named areas ────────────────────────────────────────── */
QWidget#portBar {{
    background: {c['bg2']}; border-bottom: 1px solid {c['border']};
}}
QWidget#inputBar {{
    background: {c['bg2']}; border-top: 1px solid {c['border']};
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
    background: {c['terminal']}; border: none; padding: 6px;
}}
QLineEdit#cmdEdit {{
    border: 1px solid {c['accent_brd']};
    color: {c['accent']};
    background: {c['terminal']};
    font-family: 'JetBrains Mono'; font-size: 11px;
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
"""


def key_from_display(display_name: str) -> ThemeName:
    return _THEME_KEYS.get(display_name, "vscode")  # type: ignore[return-value]
