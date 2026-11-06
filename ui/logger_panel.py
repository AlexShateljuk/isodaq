"""
ui/logger_panel.py — Logger control sidebar section

Shows:
  ┌─ Data Logger ─────────────────────┐
  │ Prefix  [log_session_________]    │
  │ Format  [csv ▾]  Dir [Browse…]    │
  │ Sink    ☑ File  ☑ SQLite          │
  │ [▶ Start Log]                     │
  │ File:  1.24 MB  DB: 8,432 rows    │
  │ Path: ~/isodaq_logs/log_…csv      │
  │ [Open folder]                     │
  └────────────────────────────────────┘
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QComboBox, QVBoxLayout, QWidget,
)

from core.i18n import tr
from core.logger import Logger



def _fmt(n: int, unit: str = "B") -> str:
    if unit == "rows":
        return f"{n:,} rows"
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {u}"
        n //= 1024
    return f"{n} TB"


class LoggerPanel(QWidget):
    session_started = pyqtSignal(str, str)   # file_path, db_path
    session_stopped = pyqtSignal()
    publish_toggled = pyqtSignal(bool)       # "Publish to MQTT" switched

    def __init__(self, logger: Logger, parent=None):
        super().__init__(parent)
        self._logger = logger
        self._build()
        QTimer(self, interval=500, timeout=self._refresh).start()

    @staticmethod
    def _lbl(text: str, width: int = 0) -> QLabel:
        w = QLabel(text)
        w.setObjectName("dimLabel")
        if width:
            w.setFixedWidth(width)   # aligned label column (IDE settings form)
        return w

    def _build(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        # Cap the form width so it stays a compact column in the wide bottom dock
        # instead of stretching fields edge to edge.
        form = QWidget()
        form.setMaximumWidth(460)
        outer.addWidget(form)
        outer.addStretch()
        lay = QVBoxLayout(form)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)

        # Prefix
        r = QHBoxLayout(); r.setSpacing(6)
        r.addWidget(self._lbl("Prefix", 48))
        self._prefix = QLineEdit("log_session")
        self._prefix.textChanged.connect(self._logger.set_prefix)
        r.addWidget(self._prefix)
        lay.addLayout(r)

        # Format + dir
        r2 = QHBoxLayout(); r2.setSpacing(6)
        r2.addWidget(self._lbl("Format", 48))
        self._fmt = QComboBox()
        self._fmt.addItems(["csv", "json", "txt", "raw"])
        self._fmt.currentTextChanged.connect(self._logger.set_format)
        r2.addWidget(self._fmt, 1)
        dir_btn = QPushButton("Dir…"); dir_btn.setFixedWidth(60)
        dir_btn.setStyleSheet("padding:2px 6px;")
        dir_btn.setToolTip("Choose log output directory")
        dir_btn.clicked.connect(self._pick_dir)
        r2.addWidget(dir_btn)
        lay.addLayout(r2)

        # Sink checkboxes
        r3 = QHBoxLayout(); r3.setSpacing(0)
        self._chk_file = QCheckBox("File"); self._chk_file.setChecked(True)
        self._chk_db   = QCheckBox("SQLite"); self._chk_db.setChecked(True)
        self._chk_file.toggled.connect(self._logger.set_use_file)
        self._chk_db.toggled.connect(self._logger.set_use_db)
        r3.addWidget(self._chk_file)
        r3.addSpacing(16)
        r3.addWidget(self._chk_db)
        r3.addStretch()
        lay.addLayout(r3)

        # Toggle button
        self._btn = QPushButton("▶  Start Log")
        self._btn.setObjectName("start")
        self._btn.setFixedHeight(26)
        self._btn.clicked.connect(self._toggle)
        lay.addWidget(self._btn)

        # Stats row
        r4 = QHBoxLayout(); r4.setSpacing(6)
        r4.addWidget(self._lbl("File:"))
        self._lbl_file = QLabel("—"); self._lbl_file.setObjectName("stat")
        r4.addWidget(self._lbl_file)
        r4.addSpacing(8)
        r4.addWidget(self._lbl("DB:"))
        self._lbl_db = QLabel("—"); self._lbl_db.setObjectName("stat")
        r4.addWidget(self._lbl_db)
        r4.addStretch()
        lay.addLayout(r4)

        # Path label
        self._lbl_path = QLabel("—")
        self._lbl_path.setObjectName("path")
        lay.addWidget(self._lbl_path)

        # Publish to MQTT: another output, independent of the log session.
        r5 = QHBoxLayout(); r5.setSpacing(6)
        self.chk_mqtt = QCheckBox(tr("Publish to MQTT"))
        self.chk_mqtt.setToolTip(tr(
            "Sends parsed values and trigger events to a broker, for Grafana, Home "
            "Assistant or Node-RED. Each channel goes to <base topic>/<name>, at most "
            "10 times a second."))
        self.chk_mqtt.toggled.connect(self.publish_toggled)
        r5.addWidget(self.chk_mqtt)
        self.mqtt_url = QLineEdit()
        self.mqtt_url.setPlaceholderText("mqtt://broker.local/bench1")
        r5.addWidget(self.mqtt_url, 1)
        lay.addLayout(r5)
        self.mqtt_status = QLabel("")
        self.mqtt_status.setObjectName("path")
        self.mqtt_status.setWordWrap(True)
        lay.addWidget(self.mqtt_status)
        lay.addStretch()

    @staticmethod
    def _repolish(w) -> None:
        w.style().unpolish(w); w.style().polish(w); w.update()

    def _toggle(self):
        if self._logger.active:
            self._logger.stop()
            self._btn.setText("▶  Start Log")
            self._btn.setObjectName("start")
            self._repolish(self._btn)
            self.session_stopped.emit()
        else:
            fp, dp = self._logger.start()
            self._btn.setText("⏹  Stop Log")
            self._btn.setObjectName("stop")
            self._repolish(self._btn)
            parts = []
            if fp: parts.append(fp.name)
            if dp: parts.append(dp.name)
            self._lbl_path.setText("\n".join(parts))
            self.session_started.emit(str(fp or ""), str(dp or ""))

    def _refresh(self):
        if not self._logger.active:
            return
        self._lbl_file.setText(_fmt(self._logger.file_bytes))
        self._lbl_db.setText(_fmt(self._logger.db_rows, "rows"))

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select log directory")
        if d:
            self._logger.set_log_dir(d)

    def _open_folder(self):
        folder = (self._logger.current_file or self._logger.current_db
                  or Path.home() / "isodaq_logs").parent
        folder.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            subprocess.Popen(["explorer", str(folder)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
