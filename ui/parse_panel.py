"""
ui/parse_panel.py — Channel configuration panel for the data parser.
"""
from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSizePolicy, QTextEdit,
    QVBoxLayout, QWidget,
)

from core.data_parser import ChannelConfig, DataParser, parse_single


class _ChRow(QWidget):
    delete_req = pyqtSignal(object)
    edit_req   = pyqtSignal(object)

    def __init__(self, ch: ChannelConfig, parent=None):
        super().__init__(parent)
        self.ch = ch
        self._build()

    def _build(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 1, 0, 1)
        lay.setSpacing(4)

        self._en = QCheckBox()
        self._en.setChecked(self.ch.enabled)
        self._en.toggled.connect(lambda v: setattr(self.ch, "enabled", v))
        lay.addWidget(self._en)

        self._name_lbl = QLabel(self.ch.name)
        self._name_lbl.setStyleSheet("color:#dde1ec;font-size:10px;")
        self._name_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._refresh_tooltip()
        lay.addWidget(self._name_lbl)

        self._val_lbl = QLabel("—")
        self._val_lbl.setStyleSheet(
            "font-family:'JetBrains Mono';font-size:10px;"
            "color:#3e4460;min-width:58px;"
        )
        lay.addWidget(self._val_lbl)

        # Chart / Indicator indicator dots (show state, click opens editor)
        self._chart_dot = QPushButton("▲")
        self._chart_dot.setFixedSize(14, 14)
        self._chart_dot.setToolTip("In chart")
        self._chart_dot.clicked.connect(lambda: self.edit_req.emit(self))
        lay.addWidget(self._chart_dot)

        self._ind_dot = QPushButton("■")
        self._ind_dot.setFixedSize(14, 14)
        self._ind_dot.setToolTip("In indicators")
        self._ind_dot.clicked.connect(lambda: self.edit_req.emit(self))
        lay.addWidget(self._ind_dot)

        self._refresh_dots()

        edit_btn = QPushButton("✏")
        edit_btn.setObjectName("iconBtn")
        edit_btn.setFixedSize(18, 18)
        edit_btn.clicked.connect(lambda: self.edit_req.emit(self))
        lay.addWidget(edit_btn)

        # edit_btn = QPushButton("✏")
        # edit_btn.setFixedSize(18, 18)
        # edit_btn.setStyleSheet(
        #     "font-size:10px;border:none;background:transparent;color:#8891a5;")
        # edit_btn.clicked.connect(lambda: self.edit_req.emit(self))
        # lay.addWidget(edit_btn)

        del_btn = QPushButton("×")
        del_btn.setObjectName("delBtn")
        del_btn.setFixedSize(18, 18)
        del_btn.clicked.connect(lambda: self.delete_req.emit(self))
        lay.addWidget(del_btn)

        # del_btn = QPushButton("×")
        # del_btn.setFixedSize(18, 18)
        # del_btn.setStyleSheet(
        #     "font-size:13px;border:none;background:transparent;"
        #     "color:#ef4444;font-weight:700;")
        # del_btn.clicked.connect(lambda: self.delete_req.emit(self))
        # lay.addWidget(del_btn)

    def _refresh_dots(self):
        on_c  = "border:none;background:transparent;"
        off_c = "border:none;background:transparent;"
        self._chart_dot.setStyleSheet(
            on_c + "color:#3ecf8e;font-size:8px;" if self.ch.show_chart
            else off_c + "color:#3e4460;font-size:8px;")
        self._ind_dot.setStyleSheet(
            on_c + "color:#60a5fa;font-size:8px;" if self.ch.show_indicator
            else off_c + "color:#3e4460;font-size:8px;")

    def _refresh_tooltip(self):
        ch = self.ch
        parts = [f"key: {ch.key}"]
        if ch.unit:
            parts.append(f"unit: {ch.unit}")
        if ch.prefix:
            parts.append(f"prefix: {ch.prefix}")
        parts.append(f"× {ch.scale}  + {ch.offset}")
        self._name_lbl.setToolTip("\n".join(parts))

    def update_value(self, value: float) -> None:
        self._val_lbl.setText(f"{value:.5g}")
        self._val_lbl.setStyleSheet(
            "font-family:'JetBrains Mono';font-size:10px;"
            "color:#3ecf8e;min-width:58px;"
        )


class ParsePanel(QWidget):
    # (name, enable) — MainWindow wires these to ChartPanel / IndicatorPanel
    channel_chart_req     = pyqtSignal(str, bool)
    channel_indicator_req = pyqtSignal(str, bool)

    def __init__(self, parser: DataParser, parent=None):
        super().__init__(parent)
        self._parser = parser
        self._rows: list[_ChRow] = []
        self._editing: _ChRow | None = None
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Channel list ──────────────────────────────────────────────────────
        self._list_w = QWidget()
        self._list_lay = QVBoxLayout(self._list_w)
        self._list_lay.setContentsMargins(8, 3, 8, 3)
        self._list_lay.setSpacing(1)
        self._list_lay.addStretch()
        root.addWidget(self._list_w)

        # ── Editor ────────────────────────────────────────────────────────────
        self._editor = QWidget()
        self._editor.setObjectName("triggerHeader")
        el = QVBoxLayout(self._editor)
        el.setContentsMargins(10, 8, 10, 8)
        el.setSpacing(5)

        # Key
        r_key = QHBoxLayout(); r_key.setSpacing(5)
        r_key.addWidget(_lbl("Key"))
        self._key_e = QLineEdit()
        self._key_e.setPlaceholderText("snap.pv_v  /  pv  /  vbat")
        self._key_e.setToolTip(
            "Token to search for in the line.\n"
            "Dot-notation: snap.pv_v\n"
            "JSON dot-path: data.voltage")
        r_key.addWidget(self._key_e)
        el.addLayout(r_key)

        # Name
        r_name = QHBoxLayout(); r_name.setSpacing(5)
        r_name.addWidget(_lbl("Name"))
        self._name_e = QLineEdit()
        self._name_e.setPlaceholderText("Display label  (empty → use key)")
        r_name.addWidget(self._name_e)
        el.addLayout(r_name)

        # Prefix + Unit
        r_pu = QHBoxLayout(); r_pu.setSpacing(5)
        r_pu.addWidget(_lbl("Prefix"))
        self._prefix_e = QLineEdit()
        self._prefix_e.setPlaceholderText("a1617:  (empty = all lines)")
        self._prefix_e.setToolTip(
            "Line must contain this string.\n"
            "Leave empty to match every line.")
        r_pu.addWidget(self._prefix_e)
        r_pu.addWidget(_lbl("Unit"))
        self._unit_e = QLineEdit()
        self._unit_e.setFixedWidth(46)
        self._unit_e.setPlaceholderText("mV")
        self._unit_e.setToolTip(
            "Unit suffix to pick a specific token.\n"
            "pv=43608mV 847mA  →  Unit=mV  →  43608\n"
            "Leave empty for first numeric token.")
        r_pu.addWidget(self._unit_e)
        el.addLayout(r_pu)

        # Scale + Offset
        r_so = QHBoxLayout(); r_so.setSpacing(5)
        r_so.addWidget(_lbl("×"))
        self._scale_e = QLineEdit("1.0")
        self._scale_e.setFixedWidth(72)
        self._scale_e.setToolTip("Multiply extracted value")
        r_so.addWidget(self._scale_e)
        r_so.addWidget(_lbl("+"))
        self._offset_e = QLineEdit("0.0")
        self._offset_e.setFixedWidth(72)
        self._offset_e.setToolTip("Add after scaling")
        r_so.addWidget(self._offset_e)
        r_so.addStretch()
        el.addLayout(r_so)

        # Test
        r_test = QHBoxLayout(); r_test.setSpacing(5)
        r_test.addWidget(_lbl("Test"))
        self._test_e = QLineEdit()
        self._test_e.setPlaceholderText("Paste a sample line to verify extraction")
        r_test.addWidget(self._test_e)
        test_btn = QPushButton("▶")
        test_btn.setFixedSize(26, 22)
        test_btn.setToolTip("Run extraction on the test line")
        test_btn.clicked.connect(self._run_test)
        r_test.addWidget(test_btn)
        el.addLayout(r_test)

        self._test_result = QLabel("")
        self._test_result.setWordWrap(True)
        self._test_result.setStyleSheet("font-family:'JetBrains Mono';font-size:10px;")
        el.addWidget(self._test_result)

        # Display options
        r_disp = QHBoxLayout(); r_disp.setSpacing(10)
        self._chk_chart = QCheckBox("Add to Chart")
        self._chk_chart.setToolTip("Show channel in the Graphs tab")
        self._chk_indicator = QCheckBox("Add to Indicators")
        self._chk_indicator.setToolTip("Show channel in the Indicators tab")
        r_disp.addWidget(self._chk_chart)
        r_disp.addWidget(self._chk_indicator)
        r_disp.addStretch()
        el.addLayout(r_disp)

        # Save / Cancel
        r_btns = QHBoxLayout(); r_btns.setSpacing(6)
        sv = QPushButton("Save"); sv.setObjectName("save"); sv.setFixedHeight(24)
        sv.clicked.connect(self._save)
        cn = QPushButton("Cancel"); cn.setFixedHeight(24)
        cn.clicked.connect(self._close_editor)
        r_btns.addWidget(sv); r_btns.addWidget(cn)
        el.addLayout(r_btns)

        self._editor.hide()
        root.addWidget(self._editor)

        self._build_snippet_section(root)

    # ── Snippet section ───────────────────────────────────────────────────────

    def _build_snippet_section(self, root: QVBoxLayout) -> None:
        # Toggle header
        self._snip_toggle = QPushButton("▶  Custom Snippet")
        self._snip_toggle.setObjectName("iconBtn")
        self._snip_toggle.setStyleSheet(
            "text-align:left;padding:4px 8px;"
            "font-family:'JetBrains Mono';font-size:10px;")
        self._snip_toggle.clicked.connect(self._toggle_snippet)
        root.addWidget(self._snip_toggle)

        self._snip_w = QWidget()
        self._snip_w.setObjectName("triggerHeader")
        sl = QVBoxLayout(self._snip_w)
        sl.setContentsMargins(10, 8, 10, 8)
        sl.setSpacing(5)

        hint = QLabel(
            "Receives:  line: str\n"
            "Return:    dict[str, float]  or  None\n"
            "Example:   return {'bat': float(line.split('=')[1])}")
        hint.setObjectName("dimLabel")
        hint.setWordWrap(True)
        sl.addWidget(hint)

        self._snip_edit = QTextEdit()
        self._snip_edit.setObjectName("customCmd")
        self._snip_edit.setPlaceholderText(
            "import re\n"
            "m = re.findall(r'(\\w+)\\s*[=:>-]+\\s*(-?[\\d.]+)', line)\n"
            "return {k: float(v) for k, v in m}")
        self._snip_edit.setMinimumHeight(90)
        self._snip_edit.setMaximumHeight(180)
        self._snip_edit.setAcceptRichText(False)
        sl.addWidget(self._snip_edit)

        # Test row
        r_test = QHBoxLayout(); r_test.setSpacing(5)
        r_test.addWidget(_lbl("Test"))
        self._snip_test_e = QLineEdit()
        self._snip_test_e.setPlaceholderText("Paste a sample line")
        r_test.addWidget(self._snip_test_e)
        run_btn = QPushButton("▶")
        run_btn.setFixedSize(26, 22)
        run_btn.clicked.connect(self._run_snippet_test)
        r_test.addWidget(run_btn)
        sl.addLayout(r_test)

        self._snip_result = QLabel("")
        self._snip_result.setWordWrap(True)
        self._snip_result.setObjectName("dimLabel")
        sl.addWidget(self._snip_result)

        # Apply button
        r_apply = QHBoxLayout(); r_apply.setSpacing(6)
        apply_btn = QPushButton("Apply")
        apply_btn.setObjectName("save")
        apply_btn.setFixedHeight(24)
        apply_btn.clicked.connect(self._apply_snippet)
        clear_btn = QPushButton("Clear")
        clear_btn.setFixedHeight(24)
        clear_btn.clicked.connect(self._clear_snippet)
        r_apply.addWidget(apply_btn)
        r_apply.addWidget(clear_btn)
        r_apply.addStretch()
        self._snip_status = QLabel("")
        self._snip_status.setObjectName("dimLabel")
        r_apply.addWidget(self._snip_status)
        sl.addLayout(r_apply)

        self._snip_w.hide()
        root.addWidget(self._snip_w)

    def _toggle_snippet(self) -> None:
        if self._snip_w.isHidden():
            self._snip_w.show()
            self._snip_toggle.setText("▼  Custom Snippet")
        else:
            self._snip_w.hide()
            self._snip_toggle.setText("▶  Custom Snippet")

    def _apply_snippet(self) -> None:
        code = self._snip_edit.toPlainText().strip()
        err = self._parser.set_snippet(code)
        if err:
            self._snip_status.setText(f"✗  {err}")
            self._snip_status.setStyleSheet("color:#ef4444;font-family:'JetBrains Mono';font-size:10px;")
        else:
            label = "Active" if code else "Cleared"
            self._snip_status.setText(f"✓  {label}")
            self._snip_status.setStyleSheet("color:#22c55e;font-family:'JetBrains Mono';font-size:10px;")

    def _clear_snippet(self) -> None:
        self._snip_edit.clear()
        self._parser.set_snippet("")
        self._snip_status.setText("Cleared")
        self._snip_status.setStyleSheet("color:#6a6a7a;font-family:'JetBrains Mono';font-size:10px;")

    def _run_snippet_test(self) -> None:
        code = self._snip_edit.toPlainText().strip()
        line = self._snip_test_e.text().strip()
        if not code:
            self._snip_result.setText("Write a snippet first.")
            return
        if not line:
            self._snip_result.setText("Enter a test line.")
            return
        # Compile a fresh copy for the test
        try:
            indented = "\n".join("    " + ln for ln in code.splitlines())
            ns: dict = {}
            exec(compile(f"def _fn(line):\n{indented}\n", "<test>", "exec"), ns)  # noqa: S102
            result = ns["_fn"](line)
        except Exception as e:
            self._snip_result.setText(f"✗  {e}")
            self._snip_result.setStyleSheet(
                "font-family:'JetBrains Mono';font-size:10px;color:#ef4444;")
            return
        if isinstance(result, dict) and result:
            txt = "  ".join(f"{k}={v}" for k, v in result.items())
            self._snip_result.setText(f"✓  {txt}")
            self._snip_result.setStyleSheet(
                "font-family:'JetBrains Mono';font-size:10px;color:#22c55e;")
        elif result is None or result == {}:
            self._snip_result.setText("✗  No match (returned None or empty dict)")
            self._snip_result.setStyleSheet(
                "font-family:'JetBrains Mono';font-size:10px;color:#f59e0b;")
        else:
            self._snip_result.setText(f"✗  Expected dict, got {type(result).__name__}")
            self._snip_result.setStyleSheet(
                "font-family:'JetBrains Mono';font-size:10px;color:#ef4444;")

    def load_snippet(self, code: str) -> None:
        self._snip_edit.setPlainText(code)
        if code.strip():
            self._snip_status.setText("✓  Active")
            self._snip_status.setStyleSheet(
                "color:#22c55e;font-family:'JetBrains Mono';font-size:10px;")

    # ── Editor ────────────────────────────────────────────────────────────────

    def open_editor(self, row: _ChRow | None = None,
                    default_prefix: str = "") -> None:
        self._editing = row
        self._test_result.setText("")
        if row:
            ch = row.ch
            self._key_e.setText(ch.key)
            self._name_e.setText(ch.name)
            self._prefix_e.setText(ch.prefix)
            self._unit_e.setText(ch.unit)
            self._scale_e.setText(str(ch.scale))
            self._offset_e.setText(str(ch.offset))
            self._chk_chart.setChecked(ch.show_chart)
            self._chk_indicator.setChecked(ch.show_indicator)
        else:
            self._key_e.clear()
            self._name_e.clear()
            self._prefix_e.setText(default_prefix)
            self._unit_e.clear()
            self._scale_e.setText("1.0")
            self._offset_e.setText("0.0")
            self._test_e.clear()
            self._chk_chart.setChecked(False)
            self._chk_indicator.setChecked(False)
        self._editor.show()
        self._key_e.setFocus()

    def _close_editor(self) -> None:
        self._editor.hide()
        self._editing = None

    def _save(self) -> None:
        key = self._key_e.text().strip()
        if not key:
            return
        old_name = self._editing.ch.name if self._editing else None
        ch = ChannelConfig(
            name=self._name_e.text().strip() or key,
            key=key,
            unit=self._unit_e.text().strip(),
            prefix=self._prefix_e.text().strip(),
            scale=_parse_float(self._scale_e.text(), 1.0),
            offset=_parse_float(self._offset_e.text(), 0.0),
            show_chart=self._chk_chart.isChecked(),
            show_indicator=self._chk_indicator.isChecked(),
        )
        if self._editing:
            # If name changed, remove old from display panels
            if old_name and old_name != ch.name:
                self.channel_chart_req.emit(old_name, False)
                self.channel_indicator_req.emit(old_name, False)
            self._parser.update_channel(self._rows.index(self._editing), ch)
        else:
            self._parser.add_channel(ch)
        # Notify display panels
        self.channel_chart_req.emit(ch.name, ch.show_chart)
        self.channel_indicator_req.emit(ch.name, ch.show_indicator)
        self._rebuild()
        self._close_editor()

    def _run_test(self) -> None:
        key = self._key_e.text().strip()
        line = self._test_e.text().strip()
        if not key or not line:
            self._test_result.setText("Enter key and test line first.")
            self._test_result.setStyleSheet(
                "font-family:'JetBrains Mono';font-size:10px;color:#f59e0b;")
            return
        result = parse_single(
            line, key,
            scale=_parse_float(self._scale_e.text(), 1.0),
            offset=_parse_float(self._offset_e.text(), 0.0),
            unit=self._unit_e.text().strip(),
            prefix=self._prefix_e.text().strip(),
        )
        if result is None:
            self._test_result.setText(f'✗  key="{key}" not found')
            self._test_result.setStyleSheet(
                "font-family:'JetBrains Mono';font-size:10px;color:#ef4444;")
        else:
            self._test_result.setText(f"✓  {result:.6g}")
            self._test_result.setStyleSheet(
                "font-family:'JetBrains Mono';font-size:10px;color:#22c55e;")

    # ── List ──────────────────────────────────────────────────────────────────

    def _rebuild(self) -> None:
        for r in self._rows:
            self._list_lay.removeWidget(r)
            r.deleteLater()
        self._rows.clear()
        for ch in self._parser.get_channels():
            row = _ChRow(ch)
            row.delete_req.connect(self._del_row)
            row.edit_req.connect(self.open_editor)
            self._list_lay.insertWidget(self._list_lay.count() - 1, row)
            self._rows.append(row)

    def _del_row(self, row: _ChRow) -> None:
        ch = row.ch
        self.channel_chart_req.emit(ch.name, False)
        self.channel_indicator_req.emit(ch.name, False)
        self._parser.remove_channel(self._rows.index(row))
        self._rebuild()

    # ── Live value update ─────────────────────────────────────────────────────

    def update_values(self, data: dict[str, float]) -> None:
        for row in self._rows:
            if row.ch.name in data:
                row.update_value(data[row.ch.name])

    # ── Sync after settings load ──────────────────────────────────────────────

    def sync_display_panels(self) -> None:
        """Emit chart/indicator signals for all channels. Call after loading settings."""
        for ch in self._parser.get_channels():
            self.channel_chart_req.emit(ch.name, ch.show_chart)
            self.channel_indicator_req.emit(ch.name, ch.show_indicator)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _lbl(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("dimLabel")
    return lbl


def _parse_float(s: str, default: float) -> float:
    try:
        return float(s)
    except ValueError:
        return default
