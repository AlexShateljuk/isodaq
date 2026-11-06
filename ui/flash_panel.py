"""ui/flash_panel.py — the Flash tool window: one connection's build / flash command."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                             QProgressBar, QPushButton, QVBoxLayout, QWidget)

from core.file_transfer import MODES
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
    transfer_requested = pyqtSignal()
    transfer_cancel_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # A compact column in the wide bottom dock, like the Data Logger: wide
        # enough to read a whole command, not stretched edge to edge.
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        form = QWidget()
        form.setMaximumWidth(720)
        outer.addWidget(form, 3)
        outer.addWidget(self._build_transfer(), 2)
        outer.addStretch(1)
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

    def _build_transfer(self) -> QWidget:
        """The other way to get firmware across: over this connection itself, for a
        bootloader waiting for XMODEM or YMODEM."""
        box = QWidget()
        box.setMaximumWidth(460)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)
        lay.addWidget(self._dim(tr("Send a file over this connection")))
        row = QHBoxLayout()
        row.setSpacing(4)
        self._mode = QComboBox()
        self._mode.addItems(MODES)
        row.addWidget(self._mode)
        self._file = QLineEdit()
        self._file.setPlaceholderText(tr("File to send"))
        row.addWidget(self._file, 1)
        pick = QPushButton("…")
        pick.setFixedSize(28, self._file.sizeHint().height())
        pick.setToolTip(tr("Choose a file"))
        pick.clicked.connect(self._pick_file)
        row.addWidget(pick)
        lay.addLayout(row)
        self._send_btn = QPushButton()
        self._send_btn.setFixedHeight(26)
        self._send_btn.clicked.connect(self._on_send_button)
        lay.addWidget(self._send_btn)
        self._progress = QProgressBar()
        self._progress.setFixedHeight(6)
        self._progress.setTextVisible(False)
        self._progress.setRange(0, 100)
        lay.addWidget(self._progress)
        self._transfer_status = QLabel("")
        self._transfer_status.setObjectName("dimLabel")
        self._transfer_status.setWordWrap(True)
        self._transfer_status.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(self._transfer_status)
        hint = QLabel(tr(
            "Tell the device to receive first, from the command bar (for example rx or "
            "loady). The terminal shows nothing while the file is going across."))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lay.addStretch()
        self._sending = False
        self.set_transfer_running(False)
        return box

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("File to send"), self.cwd())
        if path:
            self._file.setText(path)

    def _on_send_button(self) -> None:
        (self.transfer_cancel_requested if self._sending else self.transfer_requested).emit()

    # ── File transfer state ───────────────────────────────────────────────────

    def transfer_mode(self) -> str:
        return self._mode.currentText()

    def transfer_path(self) -> str:
        return self._file.text().strip()

    def set_transfer_config(self, mode: str, path: str) -> None:
        if mode in MODES:
            self._mode.setCurrentText(mode)
        self._file.setText(path)

    def set_transfer_running(self, running: bool) -> None:
        self._sending = running
        self._send_btn.setText(tr("Cancel") if running else tr("Send file"))
        self._send_btn.setObjectName("stop" if running else "start")
        self._send_btn.style().unpolish(self._send_btn)
        self._send_btn.style().polish(self._send_btn)
        self._mode.setEnabled(not running)
        self._file.setEnabled(not running)

    def set_transfer_progress(self, sent: int, total: int) -> None:
        self._progress.setValue(round(100 * sent / total) if total else 0)

    def transfer_progress(self) -> int:
        return self._progress.value()

    def set_transfer_status(self, text: str) -> None:
        self._transfer_status.setText(text)

    def transfer_status(self) -> str:
        return self._transfer_status.text()

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
