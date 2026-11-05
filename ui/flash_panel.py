"""ui/flash_panel.py — the Flash tool window: one connection's build / flash command."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QVBoxLayout, QWidget)

from core.i18n import tr

#: Starting points offered in the command box; it stays freely editable.
PRESETS = [
    "pio run -t upload --upload-port {port}",
    "idf.py -p {port} flash",
    "arduino-cli compile --upload -p {port} .",
    "esptool.py --port {port} write_flash 0x0 firmware.bin",
    "st-flash --reset write firmware.bin 0x08000000",
    "make flash",
]


class FlashPanel(QWidget):
    run_requested = pyqtSignal()
    stop_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # A compact column in the wide bottom dock, like the Data Logger: wide
        # enough to read a whole command, not stretched edge to edge.
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        form = QWidget()
        form.setMaximumWidth(720)
        outer.addWidget(form)
        outer.addStretch()
        lay = QVBoxLayout(form)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)

        lay.addWidget(self._dim(tr("Command")))
        self._cmd = QComboBox()
        self._cmd.setEditable(True)
        self._cmd.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._cmd.addItems(PRESETS)
        self._cmd.setCurrentText("")
        self._cmd.lineEdit().setPlaceholderText(tr("Pick a preset or type a command"))
        self._cmd.lineEdit().returnPressed.connect(self.run_requested)
        self._cmd.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        lay.addWidget(self._cmd)

        lay.addWidget(self._dim(tr("Project folder")))
        row = QHBoxLayout()
        row.setSpacing(4)
        self._cwd = QLineEdit()
        self._cwd.setPlaceholderText(tr("Folder the command runs in"))
        row.addWidget(self._cwd, 1)
        browse = QPushButton("…")
        browse.setFixedSize(28, self._cwd.sizeHint().height())
        browse.setToolTip(tr("Choose a folder"))
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        lay.addLayout(row)

        self._btn = QPushButton()
        self._btn.setFixedHeight(26)
        self._btn.clicked.connect(self._on_button)
        lay.addWidget(self._btn)

        self._status = QLabel("")
        self._status.setObjectName("dimLabel")
        self._status.setWordWrap(True)
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(self._status)

        hint = QLabel(tr(
            "Runs in your login shell. {port} and {baud} are filled in from this "
            "connection. The port is released while the command runs and "
            "reconnected afterwards; output goes to the terminal."))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lay.addStretch()

        self._running = False
        self.set_running(False)

    @staticmethod
    def _dim(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("dimLabel")
        return lbl

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, tr("Project folder"), self.cwd())
        if path:
            self._cwd.setText(path)

    def _on_button(self) -> None:
        (self.stop_requested if self._running else self.run_requested).emit()

    # ── State ─────────────────────────────────────────────────────────────────

    def command(self) -> str:
        return self._cmd.currentText().strip()

    def cwd(self) -> str:
        return self._cwd.text().strip()

    def set_config(self, command: str, cwd: str) -> None:
        self._cmd.setCurrentText(command)
        self._cwd.setText(cwd)

    def set_running(self, running: bool) -> None:
        self._running = running
        self._btn.setText(tr("Stop") if running else tr("Build && flash"))
        self._btn.setObjectName("stop" if running else "start")
        self._btn.style().unpolish(self._btn)
        self._btn.style().polish(self._btn)
        self._cmd.setEnabled(not running)
        self._cwd.setEnabled(not running)

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    def focus_command(self) -> None:
        self._cmd.setFocus()
