"""
ui/macro_panel.py — Macro command sequencer UI

MacroPanel     — compact sidebar widget (list of macros with run/edit/delete)
MacroEditorDialog — full dialog for creating / editing a single Macro

Each macro step has:
  Command      — text to send
  Delay ms     — pause after this step (before next)
  Wait for     — optional RX substring to wait for before proceeding
  Timeout ms   — max wait time if Wait for is set
"""
from __future__ import annotations

import copy

from PyQt6.QtCore import Qt, pyqtSignal
from ui.themes import tint_titlebar
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.macros import Macro, MacroRunner, MacroStep



# ── Macro row widget (used inside sidebar list) ───────────────────────────────

class _MacroRow(QWidget):
    run_req  = pyqtSignal(object)   # self
    edit_req = pyqtSignal(object)
    del_req  = pyqtSignal(object)

    def __init__(self, macro: Macro, parent=None):
        super().__init__(parent)
        self.macro = macro
        self._build()

    def _build(self) -> None:
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 1, 0, 1)
        lay.setSpacing(3)

        self._run_btn = QPushButton("▶")
        self._run_btn.setObjectName("run")
        self._run_btn.setFixedSize(22, 22)
        self._run_btn.clicked.connect(lambda: self.run_req.emit(self))
        lay.addWidget(self._run_btn)

        self._name_lbl = QLabel(self.macro.name)
        self._name_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay.addWidget(self._name_lbl)

        self._info_lbl = QLabel("")
        self._info_lbl.setObjectName("dimLabelMono")
        self._info_lbl.setFixedWidth(26)
        self._info_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._info_lbl)

        edit_btn = QPushButton("✏")
        edit_btn.setObjectName("iconBtn")
        edit_btn.setFixedSize(18, 18)
        edit_btn.clicked.connect(lambda: self.edit_req.emit(self))
        lay.addWidget(edit_btn)

        del_btn = QPushButton("×")
        del_btn.setObjectName("delBtn")
        del_btn.setFixedSize(18, 18)
        del_btn.clicked.connect(lambda: self.del_req.emit(self))
        lay.addWidget(del_btn)

    # ── State helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _repolish(w) -> None:
        w.style().unpolish(w); w.style().polish(w); w.update()

    def set_running(self, step_idx: int, total: int) -> None:
        self._run_btn.setText("⏹")
        self._run_btn.setObjectName("stop")
        self._repolish(self._run_btn)
        self._info_lbl.setText(f"{step_idx + 1}/{total}")
        self._info_lbl.setObjectName("hint")
        self._repolish(self._info_lbl)

    def set_waiting(self, pattern: str) -> None:
        self._info_lbl.setText("⏳")
        self._info_lbl.setToolTip(f"Waiting for: {pattern}")

    def set_idle(self) -> None:
        self._run_btn.setText("▶")
        self._run_btn.setObjectName("run")
        self._repolish(self._run_btn)
        self._info_lbl.setText("")
        self._info_lbl.setObjectName("dimLabelMono")
        self._repolish(self._info_lbl)
        self._info_lbl.setToolTip("")

    def refresh_name(self) -> None:
        self._name_lbl.setText(self.macro.name)


# ── MacroPanel (sidebar section) ──────────────────────────────────────────────

class MacroPanel(QWidget):
    """Compact sidebar section: list of macros with run / edit / delete."""

    def __init__(self, runner: MacroRunner, parent=None):
        super().__init__(parent)
        self._runner       = runner
        self._macros: list[Macro]     = []
        self._rows:   list[_MacroRow] = []
        self._active_row: _MacroRow | None = None
        self._build()
        self._load_defaults()
        self._connect_runner()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._list_w = QWidget()
        self._list_lay = QVBoxLayout(self._list_w)
        self._list_lay.setContentsMargins(8, 3, 8, 3)
        self._list_lay.setSpacing(1)
        self._list_lay.addStretch()
        root.addWidget(self._list_w)

    def _connect_runner(self) -> None:
        self._runner.step_started.connect(self._on_step_started)
        self._runner.step_waiting.connect(self._on_step_waiting)
        self._runner.finished.connect(self._on_finished)
        self._runner.aborted.connect(self._on_aborted)

    # ── Default macros ────────────────────────────────────────────────────────

    def _load_defaults(self) -> None:
        self._macros = [
            Macro("Version",     steps=[MacroStep("AT+VERSION?")]),
            Macro("Status",      steps=[MacroStep("AT+STATUS?")]),
            Macro("Reset",       steps=[MacroStep("AT+RESET", delay_ms=500)]),
            Macro("Start→Status",steps=[
                MacroStep("AT+START",   delay_ms=300, wait_for="OK", wait_timeout_ms=2000),
                MacroStep("AT+STATUS?", delay_ms=0),
            ]),
            Macro("Calibrate",   steps=[MacroStep("AT+CAL",   delay_ms=200)]),
            Macro("Stop",        steps=[MacroStep("AT+STOP",  delay_ms=0)]),
        ]
        self._rebuild()

    # ── List management ───────────────────────────────────────────────────────

    def _rebuild(self) -> None:
        for r in self._rows:
            self._list_lay.removeWidget(r)
            r.deleteLater()
        self._rows.clear()
        self._active_row = None
        for m in self._macros:
            row = _MacroRow(m)
            row.run_req.connect(self._on_run_req)
            row.edit_req.connect(self._on_edit_req)
            row.del_req.connect(self._on_del_req)
            self._list_lay.insertWidget(self._list_lay.count() - 1, row)
            self._rows.append(row)

    def _send_file_direct(self) -> None:
        """Open file dialog and send the selected file immediately over serial."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Send file over serial", "",
            "All files (*);;Text files (*.txt);;Binary files (*.bin *.hex)")
        if not path:
            return
        err = self._runner.send_file(path)
        if err:
            QMessageBox.warning(self, "Send file", f"Failed to send file:\n{err}")

    def _new_macro(self) -> None:
        m = Macro("New macro", steps=[MacroStep("AT+VERSION?")])
        dlg = MacroEditorDialog(m, self)
        tint_titlebar(dlg)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._macros.append(dlg.result_macro())
            self._rebuild()

    def _on_run_req(self, row: _MacroRow) -> None:
        # Toggle: if this macro is already running, stop it
        if self._runner.running and self._active_row is row:
            self._runner.stop()
            return
        # Stop anything else that's running
        if self._runner.running:
            self._runner.stop()
        self._active_row = row
        self._runner.start(row.macro)

    def _on_edit_req(self, row: _MacroRow) -> None:
        dlg = MacroEditorDialog(copy.deepcopy(row.macro), self)
        tint_titlebar(dlg)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            idx = self._rows.index(row)
            self._macros[idx] = dlg.result_macro()
            self._rebuild()

    def _on_del_req(self, row: _MacroRow) -> None:
        idx = self._rows.index(row)
        self._macros.pop(idx)
        self._rebuild()

    # ── Runner signal handlers ────────────────────────────────────────────────

    def _on_step_started(self, idx: int, _cmd: str) -> None:
        if self._active_row:
            self._active_row.set_running(idx, len(self._active_row.macro.steps))

    def _on_step_waiting(self, _idx: int, pat: str) -> None:
        if self._active_row:
            self._active_row.set_waiting(pat)

    def _on_finished(self) -> None:
        if self._active_row:
            self._active_row.set_idle()
        self._active_row = None

    def _on_aborted(self, _msg: str) -> None:
        if self._active_row:
            self._active_row.set_idle()
        self._active_row = None

    # ── Persistence helpers ───────────────────────────────────────────────────

    def to_dict_list(self) -> list[dict]:
        return [m.to_dict() for m in self._macros]

    def from_dict_list(self, data: list[dict]) -> None:
        self._macros = [Macro.from_dict(d) for d in data]
        self._rebuild()


# ── MacroEditorDialog ─────────────────────────────────────────────────────────

class MacroEditorDialog(QDialog):
    """Full editor for a single Macro — name, EOL, per-step table."""

    def __init__(self, macro: Macro, parent=None):
        super().__init__(parent)
        self._macro = macro
        self.setWindowTitle("Macro Editor")
        self.setMinimumSize(680, 440)
        self.setModal(True)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(10)

        # ── Name + EOL row ────────────────────────────────────────────────────
        nr = QHBoxLayout()
        nr.setSpacing(10)
        name_lbl = QLabel("Name")
        name_lbl.setObjectName("dimLabel")
        nr.addWidget(name_lbl)
        self._name_edit = QLineEdit(self._macro.name)
        nr.addWidget(self._name_edit)
        eol_lbl = QLabel("EOL")
        eol_lbl.setObjectName("dimLabel")
        nr.addWidget(eol_lbl)
        self._eol_combo = QComboBox()
        self._eol_combo.addItems(["\\r\\n", "\\n", "\\r", "None"])
        self._eol_combo.setCurrentText(self._macro.eol)
        self._eol_combo.setFixedWidth(72)
        nr.addWidget(self._eol_combo)
        root.addLayout(nr)

        # ── Hint label ────────────────────────────────────────────────────────
        hint = QLabel(
            "Wait for — optional RX substring before advancing to next step.  "
            "Leave empty to use Delay only.")
        hint.setObjectName("dimLabelMono")
        root.addWidget(hint)

        # ── Step table ────────────────────────────────────────────────────────
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["Command  (or @file:path)", "Delay ms", "Wait for (RX pattern)", "Timeout ms", "📁", ""])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(1, 82)
        self._table.setColumnWidth(3, 92)
        self._table.setColumnWidth(4, 28)
        self._table.setColumnWidth(5, 28)
        self._table.verticalHeader().setDefaultSectionSize(30)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setShowGrid(True)
        root.addWidget(self._table)

        for step in self._macro.steps:
            self._append_row(step)

        # ── Toolbar: add / move ───────────────────────────────────────────────
        tb = QHBoxLayout()
        tb.setSpacing(6)
        add_s = QPushButton("+ Add step")
        add_s.setObjectName("add")
        add_s.setFixedHeight(26)
        add_s.clicked.connect(lambda: self._append_row(MacroStep()))
        tb.addWidget(add_s)
        up_btn = QPushButton("▲ Up")
        up_btn.setFixedHeight(26)
        up_btn.clicked.connect(self._move_up)
        tb.addWidget(up_btn)
        dn_btn = QPushButton("▼ Down")
        dn_btn.setFixedHeight(26)
        dn_btn.clicked.connect(self._move_down)
        tb.addWidget(dn_btn)
        tb.addStretch()
        root.addLayout(tb)

        # ── Dialog buttons ────────────────────────────────────────────────────
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel)
        save_btn = btns.button(QDialogButtonBox.StandardButton.Save)
        save_btn.setObjectName("save")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    # ── Row helpers ───────────────────────────────────────────────────────────

    def _append_row(self, step: MacroStep) -> None:
        r = self._table.rowCount()
        self._table.insertRow(r)

        self._table.setItem(r, 0, QTableWidgetItem(step.command))

        delay_spin = QSpinBox()
        delay_spin.setRange(0, 60000)
        delay_spin.setSingleStep(50)
        delay_spin.setValue(step.delay_ms)
        delay_spin.setSuffix(" ms")
        self._table.setCellWidget(r, 1, delay_spin)

        self._table.setItem(r, 2, QTableWidgetItem(step.wait_for))

        to_spin = QSpinBox()
        to_spin.setRange(100, 60000)
        to_spin.setSingleStep(500)
        to_spin.setValue(step.wait_timeout_ms)
        to_spin.setSuffix(" ms")
        self._table.setCellWidget(r, 3, to_spin)

        file_btn = QPushButton("📁")
        file_btn.setObjectName("iconBtn")
        file_btn.setToolTip("Browse file — sets command to @file:path")
        file_btn.clicked.connect(lambda _, row=r, fb=file_btn: self._browse_file(fb))
        self._table.setCellWidget(r, 4, file_btn)

        del_btn = QPushButton("×")
        del_btn.setObjectName("delBtn")
        del_btn.clicked.connect(lambda _, b=del_btn: self._del_row_by_widget(b))
        self._table.setCellWidget(r, 5, del_btn)

    def _browse_file(self, file_btn: QPushButton) -> None:
        """Open file dialog and set command cell to @file:path."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select file to send", "",
            "All files (*);;Text files (*.txt);;Binary files (*.bin *.hex)")
        if not path:
            return
        # Find which row owns this file button
        for row in range(self._table.rowCount()):
            if self._table.cellWidget(row, 4) is file_btn:
                item = self._table.item(row, 0)
                if item is None:
                    item = QTableWidgetItem()
                    self._table.setItem(row, 0, item)
                item.setText(f"@file:{path}")
                file_btn.setObjectName("hint")  # accent color = file chosen
                file_btn.style().unpolish(file_btn)
                file_btn.style().polish(file_btn)
                return

    def _del_row_by_widget(self, btn: QPushButton) -> None:
        for row in range(self._table.rowCount()):
            if self._table.cellWidget(row, 5) is btn:
                self._table.removeRow(row)
                return

    def _move_up(self) -> None:
        r = self._table.currentRow()
        if r > 0:
            self._swap_rows(r, r - 1)
            self._table.setCurrentCell(r - 1, 0)

    def _move_down(self) -> None:
        r = self._table.currentRow()
        if 0 <= r < self._table.rowCount() - 1:
            self._swap_rows(r, r + 1)
            self._table.setCurrentCell(r + 1, 0)

    def _swap_rows(self, a: int, b: int) -> None:
        for col in (0, 2):
            ia = self._table.item(a, col)
            ib = self._table.item(b, col)
            ta = ia.text() if ia else ""
            tb = ib.text() if ib else ""
            if ia: ia.setText(tb)
            if ib: ib.setText(ta)
        for col in (1, 3):
            wa = self._table.cellWidget(a, col)
            wb = self._table.cellWidget(b, col)
            if isinstance(wa, QSpinBox) and isinstance(wb, QSpinBox):
                va, vb = wa.value(), wb.value()
                wa.setValue(vb)
                wb.setValue(va)
        # file btn tooltip/color swap (col 4) — swap the tooltip text
        fb_a = self._table.cellWidget(a, 4)
        fb_b = self._table.cellWidget(b, 4)
        if fb_a and fb_b:
            ta, tb = fb_a.toolTip(), fb_b.toolTip()
            fb_a.setToolTip(tb)
            fb_b.setToolTip(ta)

    # ── Result ────────────────────────────────────────────────────────────────

    def result_macro(self) -> Macro:
        """Build and return a Macro from the current table contents."""
        steps: list[MacroStep] = []
        for r in range(self._table.rowCount()):
            cmd_item  = self._table.item(r, 0)
            wait_item = self._table.item(r, 2)
            delay_w   = self._table.cellWidget(r, 1)
            to_w      = self._table.cellWidget(r, 3)
            # col 4 = file browse, col 5 = delete (not needed for result)
            cmd  = cmd_item.text().strip()  if cmd_item  else ""
            wait = wait_item.text().strip() if wait_item else ""
            dl   = delay_w.value() if isinstance(delay_w, QSpinBox) else 200
            to   = to_w.value()    if isinstance(to_w,    QSpinBox) else 3000
            if cmd:
                steps.append(MacroStep(cmd, dl, wait, to))
        return Macro(
            name  = self._name_edit.text().strip() or "Macro",
            eol   = self._eol_combo.currentText(),
            steps = steps,
        )
