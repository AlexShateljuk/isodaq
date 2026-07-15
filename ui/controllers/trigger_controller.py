"""TriggerController — trigger match handling, actions, analytics sync, save/load.

Extracted from MainWindow (OSS6). The TriggerEngine fires ``on_match_threadsafe``
from the serial reader thread; this controller marshals it onto the GUI thread
(``_on_trigger_match_gui``) and runs the configured actions (flash / log marker /
beep / pause / resume) plus the analytics + events-panel updates. It also owns
the trigger file save/load, including the OSS2 security gate for ``[python]``
rules.

RX-pipeline state it reads from the shell: ``mw._current_rx_line_id`` (the id of
the terminal line that matched, for F2) and ``mw._last_parsed`` (last channel
values, attached to the event row). Colour globals are read via the module at
call time (``_win.C_SYS``).
"""
from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import Q_ARG, QMetaObject, QObject, Qt, pyqtSlot
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import QApplication

import ui.main_window as _win   # colour globals (runtime access only)
from core.i18n import tr
from core.triggers import Trigger, TriggerEngine


class TriggerController(QObject):
    """Handles trigger matches, their actions, analytics sync and file I/O."""

    def __init__(self, mw):
        super().__init__(mw)
        self._mw = mw

    def on_match_threadsafe(self, trigger: Trigger, line: str, ts: str) -> None:
        """Called from the serial reader thread — marshal onto the GUI thread."""
        line_id = self._mw._current_rx_line_id   # id of the terminal line that matched
        QMetaObject.invokeMethod(
            self, "_on_trigger_match_gui",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(object, trigger),
            Q_ARG(str, line),
            Q_ARG(str, ts),
            Q_ARG(int, line_id),
        )

    @pyqtSlot(object, str, str, int)
    def _on_trigger_match_gui(self, trigger: Trigger, line: str, ts: str,
                              line_id: int = -1):
        """Runs in GUI thread. Handles all trigger actions."""
        mw = self._mw
        # ── Flash: highlighted banner in terminal ─────────────────────────────
        if trigger.action_flash:
            cursor = mw._terminal.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            fmt = QTextCharFormat()
            fmt.setBackground(QColor(trigger.color).darker(280))
            fmt.setForeground(QColor(trigger.color))
            cursor.insertText(f"\n⚡ TRIGGER [{trigger.name}]: {line}\n", fmt)
            if mw._chk_auto.isChecked():
                mw._terminal.ensureCursorVisible()

        # ── Log: write trigger marker to active sinks ─────────────────────────
        if trigger.action_log:
            mw._logger.write_trigger_event(trigger.name, line, ts)

        # ── Sound: system beep ────────────────────────────────────────────────
        if trigger.action_sound:
            QApplication.beep()

        # ── Pause log: stop active logging session ────────────────────────────
        if trigger.action_pause and mw._logger.active:
            mw._logger.stop()
            mw._logger_panel._btn.setText("▶  Start Log")
            mw._logger_panel._btn.setObjectName("start")
            mw._repolish(mw._logger_panel._btn)
            mw._log("SYS", f"[TRIGGER:{trigger.name}] Log paused.", _win.C_SYS)

        # ── Resume log: restart logging session ───────────────────────────────
        if trigger.action_resume and not mw._logger.active:
            mw._logger.start()
            mw._logger_panel._btn.setText("⏹  Stop Log")
            mw._logger_panel._btn.setObjectName("stop")
            mw._repolish(mw._logger_panel._btn)
            mw._log("SYS", f"[TRIGGER:{trigger.name}] Log resumed.", _win.C_OK)

        mw._trigger_panel.refresh_hits()
        mw._analytics_panel.record_hit(trigger.name)

        # Always log to trigger events panel (double-click a row to jump — F2)
        mw._trigger_events_panel.add_event(ts, trigger.name, line,
                                           dict(mw._last_parsed), line_id)

    def sync_analytics(self) -> None:
        self._mw._analytics_panel.sync_triggers(self._mw._engine.get_triggers())

    # ── Save / load trigger files ────────────────────────────────────────────────

    def save_triggers(self) -> None:
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(self._mw, tr("Save triggers"), "", "JSON (*.json)")
        if path:
            Path(path).write_text(json.dumps(self._mw._engine.to_dict_list(), indent=2))

    def load_triggers(self) -> None:
        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        mw = self._mw
        path, _ = QFileDialog.getOpenFileName(mw, tr("Load triggers"), "", "JSON (*.json)")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text())
        except Exception as e:
            QMessageBox.warning(mw, tr("Load failed"),
                                tr("Could not read triggers file:\n{err}").format(err=e))
            return

        # Security gate: [python] triggers execute arbitrary code on this machine.
        allow_python = True
        py_count = TriggerEngine.count_python(data)
        if py_count:
            box = QMessageBox(mw)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle(tr("This file runs custom code"))
            box.setText(
                tr("This trigger file contains {n} custom <b>Python</b> rule(s).\n"
                   "Python triggers run arbitrary code on your computer when a serial "
                   "line matches.\n\nOnly enable them if you trust the source of this file.")
                .format(n=py_count))
            b_enable  = box.addButton(tr("Load && enable"), QMessageBox.ButtonRole.AcceptRole)
            b_disable = box.addButton(tr("Load disabled"),  QMessageBox.ButtonRole.DestructiveRole)
            b_cancel  = box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(b_disable)
            box.exec()
            clicked = box.clickedButton()
            if clicked is b_cancel:
                return
            allow_python = clicked is b_enable

        mw._engine.from_dict_list(data, allow_python=allow_python)
        mw._trigger_panel._rebuild_list()
        if py_count and not allow_python:
            mw._log("SYS", f"Loaded {py_count} Python trigger(s) DISABLED — "
                           "open a rule in the editor to review and enable it.", _win.C_SYS)
