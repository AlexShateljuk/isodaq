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

from PyQt6.QtCore import Q_ARG, QMetaObject, QObject, Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication

import ui.main_window as _win   # colour globals (runtime access only)
from core.i18n import tr
from core.notifier import send_snapshot
from core.triggers import Trigger, TriggerEngine


class TriggerController(QObject):
    """Handles trigger matches, their actions, analytics sync and file I/O."""

    def __init__(self, mw):
        super().__init__(mw)
        self._mw = mw
        # A snapshot whose device went quiet still has to be written (CMP21).
        self._snapshot_timer = QTimer(self)
        self._snapshot_timer.setInterval(1000)
        self._snapshot_timer.timeout.connect(self._poll_snapshots)
        self._snapshot_timer.start()

    def _poll_snapshots(self) -> None:
        for conn in self._mw._connections:
            conn.snapshots.poll()

    def wire_snapshots(self, conn) -> None:
        conn.snapshots.on_saved = lambda trig, path, text, c=conn: self._on_snapshot(
            c, trig, path, text)
        conn.snapshots.on_error = lambda msg, c=conn: self._mw._log(
            "ERR", tr("Snapshot not saved: {err}").format(err=msg), _win.C_ERR, conn=c)

    def _on_snapshot(self, conn, trigger: Trigger, path, text: str) -> None:
        self._mw._log("SYS", tr("Snapshot saved: {path}").format(path=path), _win.C_SYS, conn=conn)
        if trigger.snapshot_notify and trigger.notify_url:
            send_snapshot(trigger.notify_url, trigger.notify_tg_chat, trigger.name,
                          path.name, text)

    def on_match_threadsafe(self, conn, trigger: Trigger, line: str, ts: str) -> None:
        """Called from a connection's reader thread — marshal onto the GUI thread,
        carrying the owning *conn* so the match routes to that connection."""
        line_id = conn.current_rx_line_id   # id of the terminal line that matched
        QMetaObject.invokeMethod(
            self, "_on_trigger_match_gui",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(object, conn),
            Q_ARG(object, trigger),
            Q_ARG(str, line),
            Q_ARG(str, ts),
            Q_ARG(int, line_id),
        )

    @pyqtSlot(object, object, str, str, int)
    def _on_trigger_match_gui(self, conn, trigger: Trigger, line: str, ts: str,
                              line_id: int = -1):
        """Runs in GUI thread. Handles all trigger actions for *conn*. Events,
        logging and notifications fire regardless of which tab is active; the
        flash goes into the matching connection's own terminal."""
        mw = self._mw
        # ── Flash: highlighted banner in the connection's terminal ────────────
        # Queued with the terminal's batched lines so it stays in order.
        if trigger.action_flash:
            c = QColor(trigger.color)
            mw._log_banner(conn, f"⚡ TRIGGER [{trigger.name}]: {line}", c, c.darker(280))

        # ── Log: write trigger marker to this connection's sinks ──────────────
        if trigger.action_log:
            conn.logger.write_trigger_event(trigger.name, line, ts)

        # ── Sound: system beep ────────────────────────────────────────────────
        if trigger.action_sound:
            QApplication.beep()

        # ── Pause log: stop this connection's logging session ─────────────────
        if trigger.action_pause and conn.logger.active:
            conn.logger.stop()
            conn.logger_panel._btn.setText("▶  Start Log")
            conn.logger_panel._btn.setObjectName("start")
            mw._repolish(conn.logger_panel._btn)
            mw._log("SYS", f"[TRIGGER:{trigger.name}] Log paused.", _win.C_SYS, conn=conn)

        # ── Resume log: restart this connection's logging session ─────────────
        if trigger.action_resume and not conn.logger.active:
            conn.logger.start()
            conn.logger_panel._btn.setText("⏹  Stop Log")
            conn.logger_panel._btn.setObjectName("stop")
            mw._repolish(conn.logger_panel._btn)
            mw._log("SYS", f"[TRIGGER:{trigger.name}] Log resumed.", _win.C_OK, conn=conn)

        # ── Reply: auto-response to the device (CMP7) ─────────────────────────
        if trigger.reply:
            self._send_reply(conn, trigger, line)

        if conn.publisher is not None:
            conn.publisher.publish_event(trigger.name, line, ts)

        conn.trigger.refresh_hits()
        conn.analytics.record_hit(trigger.name)

        # Always log to the connection's trigger events panel (F2 double-click)
        conn.events.add_event(ts, trigger.name, line,
                              dict(conn.last_parsed), line_id)

    def _send_reply(self, conn, trigger: Trigger, line: str) -> None:
        """Send *trigger*'s reply on *conn* with the command bar's line ending."""
        mw = self._mw
        if not conn.reader.isRunning():
            return
        if not trigger.reply_due():
            conn.replies_dropped += 1
            if conn.replies_dropped in (1, 100, 1000):
                mw._log("SYS", f"[TRIGGER:{trigger.name}] reply skipped — it fired again "
                               f"within {int(Trigger.REPLY_MIN_INTERVAL * 1000)} ms "
                               f"(a device echoing the reply would loop forever)",
                        _win.C_SYS, conn=conn)
            return
        reply = trigger.render_reply(line)
        data, _note = mw._serial.finish_frame(reply)
        err = conn.reader.send(data)
        if err:
            mw._log("ERR", f"[TRIGGER:{trigger.name}] reply failed: {err}", _win.C_ERR, conn=conn)
            return
        conn.tx_bytes += len(data)
        if conn is mw._active_conn:
            mw._sb_tx.setText(f"TX: {mw._fmt_bytes(conn.tx_bytes)}")
        shown = reply.rstrip(b"\r\n").decode("utf-8", errors="replace")
        mw._log("TX", f"↩ {shown}{_note}   [{trigger.name}]", _win.C_TX, conn=conn)

    def sync_analytics(self, conn=None) -> None:
        conn = conn or self._mw._active_conn
        conn.analytics.sync_triggers(conn.engine.get_triggers())

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

        # Sending snapshots gives a URL far more of the device's output than a
        # notification does, so a file can't switch that on for someone.
        sends = [d for d in data if isinstance(d, dict) and d.get("snapshot_notify")]
        for d in sends:
            d["snapshot_notify"] = False

        mw._engine.from_dict_list(data, allow_python=allow_python)
        mw._trigger_panel._rebuild()
        mw._trigger_panel.trigger_changed.emit()     # analytics lists the new rules
        if py_count and not allow_python:
            mw._log("SYS", f"Loaded {py_count} Python trigger(s) DISABLED — "
                           "open a rule in the editor to review and enable it.", _win.C_SYS)
        if sends:
            mw._log("SYS", tr("{n} loaded rule(s) would send snapshots to a URL. That is "
                              "switched off: check the URL in the rule's editor and switch "
                              "it on there.").format(n=len(sends)), _win.C_SYS)
