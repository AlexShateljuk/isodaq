"""
ui/trigger_panel.py — Trigger editor

List view:
  ✓ [contains] Brownout Detector     ● 3   🔴 ✏ ×
  ✓ [regex]    hard fault|HardFault  ● 0   🟡 ✏ ×
  ✓ [python]   lambda line: ...      ● 1   🔵 ✏ ×

Editor:
  Pattern  [_________________________________]
  Name     [______]  Type [contains ▾]  Color [■]
  Actions  ☑ Flash  ☑ Log  ☑ Sound  ☑ Pause log  ☑ Resume log
  [Save]  [Cancel]

Syntax hints update as type changes.
"""
from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from core.triggers import Trigger, TriggerEngine, parse_trigger_line


HINTS = {
    "contains": "Case-insensitive substring.  Example:  Brownout Detector",
    "regex":    "Python re (IGNORECASE).  Example:  hard.?fault|HardFault_Handler|ERR_\\d+",
    "python":   "Lambda returning bool. Case-sensitive — use .lower() for case-insensitive.  Example:  lambda line: 'err' in line.lower()",
}


class _TrigRow(QWidget):
    delete_req = pyqtSignal(object)
    edit_req   = pyqtSignal(object)

    def __init__(self, t: Trigger, parent=None):
        super().__init__(parent)
        self.trigger = t
        self._build()

    def _build(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 1, 0, 1)
        lay.setSpacing(4)

        self._en = QCheckBox()
        self._en.setChecked(self.trigger.enabled)
        self._en.toggled.connect(lambda v: setattr(self.trigger, "enabled", v))
        lay.addWidget(self._en)

        type_lbl = QLabel(f"[{self.trigger.type}]")
        type_lbl.setStyleSheet("font-family:'JetBrains Mono';font-size:9px;color:#3e4460;")
        lay.addWidget(type_lbl)

        self._name = QLabel(self.trigger.name)
        self._name.setStyleSheet("color:#dde1ec;")
        self._name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay.addWidget(self._name)

        self._hits = QLabel("0")
        self._hits.setStyleSheet("font-family:'JetBrains Mono';font-size:10px;color:#3e4460;min-width:18px;")
        lay.addWidget(self._hits)

        dot = QPushButton()
        dot.setFixedSize(11, 11)
        dot.setStyleSheet(f"background:{self.trigger.color};border-radius:5px;border:none;")
        dot.clicked.connect(lambda: self.edit_req.emit(self))
        lay.addWidget(dot)

        edit_btn = QPushButton("✏")
        edit_btn.setObjectName("iconBtn")
        edit_btn.setFixedSize(18, 18)
        edit_btn.clicked.connect(lambda: self.edit_req.emit(self))
        lay.addWidget(edit_btn)

        # edit_btn = QPushButton("✏")
        # edit_btn.setFixedSize(18, 18)
        # edit_btn.setStyleSheet("font-size:10px;border:none;background:transparent;color:#3e4460;")
        # edit_btn.clicked.connect(lambda: self.edit_req.emit(self))
        # lay.addWidget(edit_btn)

        del_btn = QPushButton("×")
        del_btn.setObjectName("delBtn")
        del_btn.setFixedSize(18, 18)
        del_btn.setStyleSheet("font-size:13px;border:none;background:transparent;color:#ef4444;font-weight:700;")
        del_btn.clicked.connect(lambda: self.delete_req.emit(self))
        lay.addWidget(del_btn)

        # del_btn = QPushButton("×")
        # del_btn.setObjectName("delBtn")
        # del_btn.setFixedSize(18, 18)
        # del_btn.clicked.connect(lambda: self.delete_req.emit(self))
        # lay.addWidget(del_btn)

    def refresh(self):
        hits = self.trigger.hit_count
        self._hits.setText(str(hits))
        self._hits.setStyleSheet(
            f"font-family:'JetBrains Mono';font-size:10px;"
            f"color:{'#ef4444' if hits else '#3e4460'};min-width:18px;"
        )


class TriggerPanel(QWidget):
    trigger_changed = pyqtSignal()

    def __init__(self, engine: TriggerEngine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._rows: list[_TrigRow] = []
        self._editing: _TrigRow | None = None
        self._color = "#ef4444"
        self._build()
        self._load_defaults()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # (No header — it lives in CollapsibleSection now)
        # Scroll list
        self._list_w = QWidget()
        self._list_lay = QVBoxLayout(self._list_w)
        self._list_lay.setContentsMargins(8, 3, 8, 3)
        self._list_lay.setSpacing(1)
        self._list_lay.addStretch()
        root.addWidget(self._list_w)

        # Editor
        self._editor = QWidget()
        self._editor.setObjectName("triggerHeader")
        el = QVBoxLayout(self._editor); el.setContentsMargins(10, 8, 10, 8); el.setSpacing(5)

        self._pat = QLineEdit()
        self._pat.setPlaceholderText(
            "Brownout Detector  /  [regex] hard fault  /  [python] lambda line: ...")
        el.addWidget(self._pat)

        # Name row
        r_name = QHBoxLayout(); r_name.setSpacing(5)
        self._name_e = QLineEdit()
        self._name_e.setPlaceholderText("Label (auto-filled if empty)")
        r_name.addWidget(self._name_e)
        el.addLayout(r_name)

        # Type + Color row
        r1 = QHBoxLayout(); r1.setSpacing(5)
        r1.addWidget(QLabel("Type"))
        self._type_c = QComboBox(); self._type_c.addItems(["contains","regex","python"])
        self._type_c.currentTextChanged.connect(self._on_type)
        r1.addWidget(self._type_c)
        r1.addStretch()
        r1.addWidget(QLabel("Color"))
        self._col_btn = QPushButton(); self._col_btn.setFixedSize(22, 22)
        self._col_btn.clicked.connect(self._pick_color); self._upd_color()
        r1.addWidget(self._col_btn)
        el.addLayout(r1)

        # Actions — two compact rows
        r2a = QHBoxLayout(); r2a.setSpacing(6)
        self._a_flash  = QCheckBox("Flash");  self._a_flash.setChecked(True)
        self._a_log    = QCheckBox("Log");    self._a_log.setChecked(True)
        self._a_sound  = QCheckBox("Sound")
        for w in (self._a_flash, self._a_log, self._a_sound):
            r2a.addWidget(w)
        r2a.addStretch()
        el.addLayout(r2a)

        r2b = QHBoxLayout(); r2b.setSpacing(6)
        self._a_pause  = QCheckBox("Pause log")
        self._a_resume = QCheckBox("Resume log")
        for w in (self._a_pause, self._a_resume):
            r2b.addWidget(w)
        r2b.addStretch()
        el.addLayout(r2b)

        self._hint = QLabel(""); self._hint.setObjectName("hint"); self._hint.setWordWrap(True)
        el.addWidget(self._hint)

        # Notifications
        notif_lbl = QLabel("Notifications")
        notif_lbl.setObjectName("sectionTitle")
        el.addWidget(notif_lbl)

        r_url = QHBoxLayout(); r_url.setSpacing(5)
        url_lbl = QLabel("URL")
        url_lbl.setObjectName("dimLabel")
        url_lbl.setFixedWidth(46)
        r_url.addWidget(url_lbl)
        self._notify_url = QLineEdit()
        self._notify_url.setPlaceholderText(
            "https://hooks.example.com/…  or  https://api.telegram.org/bot<TOKEN>/sendMessage")
        self._notify_url.setToolTip(
            "Webhook: POST JSON {trigger, line, ts, text}\n"
            "Telegram: paste the full sendMessage URL and set Chat ID below")
        r_url.addWidget(self._notify_url)
        el.addLayout(r_url)

        r_tg = QHBoxLayout(); r_tg.setSpacing(5)
        tg_lbl = QLabel("Chat ID")
        tg_lbl.setObjectName("dimLabel")
        tg_lbl.setFixedWidth(46)
        r_tg.addWidget(tg_lbl)
        self._notify_tg = QLineEdit()
        self._notify_tg.setFixedWidth(120)
        self._notify_tg.setPlaceholderText("Telegram chat_id")
        r_tg.addWidget(self._notify_tg)
        test_notif_btn = QPushButton("Test ▶")
        test_notif_btn.setFixedHeight(22)
        test_notif_btn.setToolTip("Send a test notification now")
        test_notif_btn.clicked.connect(self._test_notification)
        r_tg.addWidget(test_notif_btn)
        r_tg.addStretch()
        el.addLayout(r_tg)

        r3 = QHBoxLayout(); r3.setSpacing(6)
        sv = QPushButton("Save"); sv.setObjectName("save"); sv.setFixedHeight(24)
        sv.clicked.connect(self._save)
        cn = QPushButton("Cancel"); cn.setFixedHeight(24)
        cn.clicked.connect(self._close_editor)
        r3.addWidget(sv); r3.addWidget(cn)
        el.addLayout(r3)

        self._editor.hide()
        root.addWidget(self._editor)

    # ── Editor ────────────────────────────────────────────────────────────────
    def _open_editor(self, row: _TrigRow | None = None):
        self._editing = row
        if row:
            t = row.trigger
            self._pat.setText(t.pattern)
            self._name_e.setText(t.name)
            self._type_c.setCurrentText(t.type)
            self._color = t.color; self._upd_color()
            self._a_flash.setChecked(t.action_flash)
            self._a_log.setChecked(t.action_log)
            self._a_sound.setChecked(t.action_sound)
            self._a_pause.setChecked(t.action_pause)
            self._a_resume.setChecked(t.action_resume)
            self._notify_url.setText(t.notify_url)
            self._notify_tg.setText(t.notify_tg_chat)
        else:
            self._pat.clear(); self._name_e.clear()
            self._notify_url.clear(); self._notify_tg.clear()
        self._editor.show(); self._pat.setFocus()

    def _close_editor(self):
        self._editor.hide(); self._editing = None

    def _save(self):
        pattern = self._pat.text().strip()
        if not pattern: return
        det_type, clean = parse_trigger_line(pattern)
        chosen = self._type_c.currentText()
        final_type = det_type if det_type != "contains" else chosen
        if pattern.lower().startswith(f"[{det_type}]"):
            pattern = clean
        name = self._name_e.text().strip() or pattern[:28]

        t = Trigger(
            name=name, pattern=pattern, type=final_type,
            enabled=True, color=self._color,
            action_flash=self._a_flash.isChecked(),
            action_log=self._a_log.isChecked(),
            action_sound=self._a_sound.isChecked(),
            action_pause=self._a_pause.isChecked(),
            action_resume=self._a_resume.isChecked(),
            notify_url=self._notify_url.text().strip(),
            notify_tg_chat=self._notify_tg.text().strip(),
        )
        if self._editing:
            idx = self._rows.index(self._editing)
            err = self.engine.update_trigger(idx, t)
        else:
            err = self.engine.add_trigger(t)

        if err:
            QMessageBox.warning(self, "Trigger error", f"Invalid pattern:\n{err}")
            return
        self._rebuild(); self._close_editor(); self.trigger_changed.emit()

    def _test_notification(self) -> None:
        from core.notifier import send_notification
        from datetime import datetime
        url = self._notify_url.text().strip()
        if not url:
            QMessageBox.information(self, "Notification", "Enter a URL first.")
            return
        send_notification(
            url, self._notify_tg.text().strip(),
            "Test trigger", "This is a test notification from IsoDAQ Studio",
            datetime.now().strftime("%H:%M:%S.%f")[:-3],
        )
        QMessageBox.information(self, "Notification", "Test notification sent.")

    # ── List ──────────────────────────────────────────────────────────────────
    def _rebuild(self):
        for r in self._rows:
            self._list_lay.removeWidget(r); r.deleteLater()
        self._rows.clear()
        for t in self.engine.get_triggers():
            row = _TrigRow(t)
            row.delete_req.connect(self._del_row)
            row.edit_req.connect(self._open_editor)
            self._list_lay.insertWidget(self._list_lay.count() - 1, row)
            self._rows.append(row)

    def _del_row(self, row: _TrigRow):
        self.engine.remove_trigger(self._rows.index(row))
        self._rebuild(); self.trigger_changed.emit()

    def _clear_hits(self):
        self.engine.clear_hit_counts(); self.refresh_hits()

    def refresh_hits(self):
        for r in self._rows: r.refresh()

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _on_type(self, t: str):
        self._hint.setText(HINTS.get(t, ""))

    def _pick_color(self):
        c = QColorDialog.getColor(QColor(self._color), self, "Trigger colour")
        if c.isValid(): self._color = c.name(); self._upd_color()

    def _upd_color(self):
        self._col_btn.setStyleSheet(
            f"background:{self._color};border-radius:4px;"
            f"border:1px solid rgba(255,255,255,.15);")

    def _load_defaults(self):
        defaults = [
            Trigger("Brownout",  "Brownout Detector",         "contains", color="#ef4444"),
            Trigger("HardFault", r"hard.?fault|HardFault_Handler", "regex", color="#f59e0b"),
        ]
        for t in defaults:
            self.engine.add_trigger(t)
        self._rebuild()
