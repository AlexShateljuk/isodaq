"""
ui/log_colorizer_dialog.py — Advanced Log Colorizer Settings

Allows the user to select one or more embedded/OS logging frameworks.
Matching RX lines in the terminal are highlighted:
  INFO  → green   (#22c55e)
  WARN  → yellow  (#f59e0b)
  ERROR → red     (#ef4444)
"""
from __future__ import annotations

import re

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

# ── Log-level colors ──────────────────────────────────────────────────────────
C_LOG_INFO = QColor("#22c55e")
C_LOG_WARN = QColor("#f59e0b")
C_LOG_ERR  = QColor("#ef4444")

# ── Platform definitions ──────────────────────────────────────────────────────
# Each entry: name → {"description": str, "rules": [(compiled_re, QColor), ...]}
PLATFORMS: dict[str, dict] = {
    "ESP32 (ESP-IDF)": {
        "description": 'I (tick) tag: msg  /  W (tick)  /  E (tick)',
        "rules": [
            (re.compile(r"^\s*I\s*[\(\s]"),  C_LOG_INFO),
            (re.compile(r"^\s*W\s*[\(\s]"),  C_LOG_WARN),
            (re.compile(r"^\s*E\s*[\(\s]"),  C_LOG_ERR),
        ],
    },
    "Arduino / AVR": {
        "description": "[INFO] / [WARN] / [ERROR]  anywhere in line",
        "rules": [
            (re.compile(r"\[INFO\]",          re.IGNORECASE), C_LOG_INFO),
            (re.compile(r"\[WARN(?:ING)?\]",  re.IGNORECASE), C_LOG_WARN),
            (re.compile(r"\[ERR(?:OR)?\]",    re.IGNORECASE), C_LOG_ERR),
        ],
    },
    "STM32 (HAL / printf)": {
        "description": "INFO: / WARN: / ERROR:  at line start",
        "rules": [
            (re.compile(r"^\s*INFO\s*:",         re.IGNORECASE), C_LOG_INFO),
            (re.compile(r"^\s*WARN(?:ING)?\s*:", re.IGNORECASE), C_LOG_WARN),
            (re.compile(r"^\s*ERR(?:OR)?\s*:",   re.IGNORECASE), C_LOG_ERR),
        ],
    },
    "Zephyr RTOS": {
        "description": "<inf> module: msg  /  <wrn>  /  <err>",
        "rules": [
            (re.compile(r"<inf>", re.IGNORECASE), C_LOG_INFO),
            (re.compile(r"<wrn>", re.IGNORECASE), C_LOG_WARN),
            (re.compile(r"<err>", re.IGNORECASE), C_LOG_ERR),
        ],
    },
    "Linux / syslog": {
        "description": "INFO / WARNING / NOTICE / ERROR / CRITICAL  (whole word)",
        "rules": [
            (re.compile(r"\bINFO\b",                          re.IGNORECASE), C_LOG_INFO),
            (re.compile(r"\b(?:WARN(?:ING)?|NOTICE)\b",       re.IGNORECASE), C_LOG_WARN),
            (re.compile(r"\b(?:ERR(?:OR)?|CRIT(?:ICAL)?)\b",  re.IGNORECASE), C_LOG_ERR),
        ],
    },
    "MicroPython / Python logging": {
        "description": "INFO:module:msg  /  WARNING:module:msg  at line start",
        "rules": [
            (re.compile(r"^INFO:",               re.IGNORECASE), C_LOG_INFO),
            (re.compile(r"^WARNING:",            re.IGNORECASE), C_LOG_WARN),
            (re.compile(r"^(?:ERROR|CRITICAL):", re.IGNORECASE), C_LOG_ERR),
        ],
    },
    "FreeRTOS (generic)": {
        "description": "[I] / [W] / [E]  at line start",
        "rules": [
            (re.compile(r"^\s*\[I\]"), C_LOG_INFO),
            (re.compile(r"^\s*\[W\]"), C_LOG_WARN),
            (re.compile(r"^\s*\[E\]"), C_LOG_ERR),
        ],
    },
    "NuttX RTOS": {
        "description": "nx_info / nx_warn / nx_err  function prefix",
        "rules": [
            (re.compile(r"\bnx_info\b",  re.IGNORECASE), C_LOG_INFO),
            (re.compile(r"\bnx_warn\b",  re.IGNORECASE), C_LOG_WARN),
            (re.compile(r"\bnx_err\b",   re.IGNORECASE), C_LOG_ERR),
        ],
    },
}


# ── Public helper ─────────────────────────────────────────────────────────────

def match_log_color(line: str, enabled: set[str]) -> QColor | None:
    """
    Test *line* against every enabled platform's rules.
    Returns the first matching QColor (INFO/WARN/ERR), or None.
    Priority: ERR > WARN > INFO (evaluated in rule order within each platform).
    """
    best: QColor | None = None
    for name, meta in PLATFORMS.items():
        if name not in enabled:
            continue
        for pattern, color in meta["rules"]:
            if pattern.search(line):
                # Escalate: err beats warn beats info
                if best is None:
                    best = color
                elif color == C_LOG_ERR:
                    best = color
                elif color == C_LOG_WARN and best == C_LOG_INFO:
                    best = color
                break  # first matching rule per platform wins
    return best


# ── Dialog ────────────────────────────────────────────────────────────────────

class LogColorizerDialog(QDialog):
    """Advanced Settings — Log Colorizer."""

    def __init__(self, enabled: set[str], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Advanced  —  Log Colorizer")
        self.setMinimumWidth(560)
        self.setMinimumHeight(480)
        self.setModal(True)
        self._checks: dict[str, QCheckBox] = {}
        self._build(enabled)

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self, enabled: set[str]) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(16, 16, 16, 14)

        # Header
        title = QLabel("Log Colorizer")
        title.setStyleSheet("font-size:15px;font-weight:700;font-family:'IBM Plex Sans';")
        root.addWidget(title)

        sub = QLabel(
            "Select the logging framework(s) your device uses. "
            "Matching RX lines will be highlighted in the terminal automatically."
        )
        sub.setWordWrap(True)
        sub.setObjectName("dimLabel")
        root.addWidget(sub)

        # Color legend — keep semantic colors (INFO=green, WARN=amber, ERR=red)
        legend = QHBoxLayout()
        legend.setSpacing(4)
        for label, hex_col in [("INFO", "#22c55e"), ("WARNING", "#f59e0b"), ("ERROR", "#ef4444")]:
            dot = QLabel("●")
            dot.setStyleSheet(f"color:{hex_col};font-size:13px;")
            lbl = QLabel(label)
            lbl.setStyleSheet(
                f"color:{hex_col};font-size:11px;font-weight:600;"
                "font-family:'JetBrains Mono';"
            )
            legend.addWidget(dot)
            legend.addWidget(lbl)
            legend.addSpacing(14)
        legend.addStretch()
        root.addLayout(legend)

        # Divider
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)

        # Scrollable platform list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setContentsMargins(0, 2, 6, 2)
        inner_lay.setSpacing(4)

        for name, meta in PLATFORMS.items():
            inner_lay.addWidget(self._make_row(name, meta, enabled))

        inner_lay.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll)

        # Bottom buttons
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep2)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        all_btn = QPushButton("Select all")
        all_btn.setFixedHeight(26)
        all_btn.clicked.connect(lambda: self._set_all(True))
        none_btn = QPushButton("Select none")
        none_btn.setFixedHeight(26)
        none_btn.clicked.connect(lambda: self._set_all(False))
        btn_row.addWidget(all_btn)
        btn_row.addWidget(none_btn)
        btn_row.addStretch()

        cancel = QPushButton("Cancel")
        cancel.setFixedHeight(28)
        cancel.clicked.connect(self.reject)

        apply = QPushButton("Apply")
        apply.setObjectName("save")
        apply.setFixedHeight(28)
        apply.clicked.connect(self.accept)

        btn_row.addWidget(cancel)
        btn_row.addWidget(apply)
        root.addLayout(btn_row)

    def _make_row(self, name: str, meta: dict, enabled: set[str]) -> QWidget:
        row = QWidget()
        row.setObjectName("colorizerRow")

        rl = QHBoxLayout(row)
        rl.setContentsMargins(10, 8, 10, 8)
        rl.setSpacing(10)

        chk = QCheckBox()
        chk.setChecked(name in enabled)
        self._checks[name] = chk
        rl.addWidget(chk)

        col = QVBoxLayout()
        col.setSpacing(2)
        nm = QLabel(name)
        nm.setStyleSheet("font-weight:600;font-size:12px;font-family:'IBM Plex Sans';")
        col.addWidget(nm)
        dp = QLabel(meta["description"])
        dp.setObjectName("dimLabelMono")
        col.addWidget(dp)
        rl.addLayout(col)
        rl.addStretch()

        # Color swatches (INFO / WARN / ERR)
        for hex_col in ("#22c55e", "#f59e0b", "#ef4444"):
            sw = QLabel("■")
            sw.setStyleSheet(f"color:{hex_col};font-size:12px;")
            rl.addWidget(sw)

        # Click anywhere on row toggles checkbox
        def _toggle(_, c=chk):
            c.setChecked(not c.isChecked())
        row.mousePressEvent = _toggle

        return row

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_all(self, state: bool) -> None:
        for chk in self._checks.values():
            chk.setChecked(state)

    def result_enabled(self) -> set[str]:
        """Return the set of enabled platform names (call after accept())."""
        return {name for name, chk in self._checks.items() if chk.isChecked()}
