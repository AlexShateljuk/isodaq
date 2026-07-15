"""UpdateManager — in-app update check, notification banner and tray message.

Extracted from MainWindow (OSS6). Owns the update banner widget, the tray icon
used for notifications, and the background GitHub release check. MainWindow adds
the banner (from :meth:`build_banner`) to its layout, calls :meth:`start` once at
launch, and routes the Help → "Check for Updates" action to :meth:`check_now`.
"""
from __future__ import annotations

import webbrowser

from PyQt6.QtCore import QObject, QTimer, pyqtSlot
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QWidget,
)

from core.i18n import tr
from core.updater import UpdateChecker


class UpdateManager(QObject):
    """Checks GitHub Releases and surfaces a newer version via banner + tray."""

    def __init__(self, mw):
        super().__init__(mw)
        self._mw = mw
        self._release_url: str = ""
        self._tray: QSystemTrayIcon | None = None
        self._updater: UpdateChecker | None = None
        self._manual_check_running: UpdateChecker | None = None
        self._banner: QWidget | None = None
        self._update_lbl: QLabel | None = None
        self._update_dl_btn: QPushButton | None = None

    # ── Widget ──────────────────────────────────────────────────────────────────

    def build_banner(self) -> QWidget:
        """Create (hidden) update banner. MainWindow adds it to its layout."""
        w = QWidget()
        w.setObjectName("updateBanner")
        w.hide()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(12, 4, 8, 4)
        lay.setSpacing(10)

        self._update_lbl = QLabel("")
        self._update_lbl.setObjectName("updateLabel")
        lay.addWidget(self._update_lbl)
        lay.addStretch()

        self._update_dl_btn = QPushButton(tr("Download"))
        self._update_dl_btn.setObjectName("iconBtn")
        self._update_dl_btn.clicked.connect(self.open_release_page)
        lay.addWidget(self._update_dl_btn)

        dismiss = QPushButton("×")
        dismiss.setObjectName("delBtn")
        dismiss.setFixedSize(20, 20)
        dismiss.clicked.connect(w.hide)
        lay.addWidget(dismiss)

        self._banner = w
        return w

    def _build_tray(self) -> None:
        from PyQt6.QtWidgets import QApplication
        icon = QApplication.instance().windowIcon()
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.messageClicked.connect(self.open_release_page)
        # Don't show in tray — we only use it for notifications
        self._tray.hide()

    # ── Checks ──────────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Kick off the automatic background check shortly after launch."""
        from PyQt6.QtWidgets import QApplication
        self._release_url = ""
        self._build_tray()
        current = QApplication.instance().applicationVersion()
        self._updater = UpdateChecker(current, self)
        self._updater.update_available.connect(self._on_update_available)
        QTimer.singleShot(2000, self._updater.start)

    def check_now(self) -> None:
        """Manual check triggered from Help menu. Runs a fresh checker and reports result."""
        from PyQt6.QtWidgets import QApplication
        current = QApplication.instance().applicationVersion()
        checker = UpdateChecker(current, self)
        checker.update_available.connect(self._on_update_available)
        checker.finished.connect(lambda: self._on_manual_check_done(checker))
        checker.start()
        self._manual_check_running = checker

    def _on_manual_check_done(self, checker: UpdateChecker) -> None:
        if not self._release_url:
            QMessageBox.information(self._mw, tr("No updates"),
                                    tr("You are on the latest version."))

    @pyqtSlot(str, str)
    def _on_update_available(self, version: str, url: str) -> None:
        from PyQt6.QtWidgets import QApplication
        self._release_url = url
        current = QApplication.instance().applicationVersion()
        if self._update_lbl:
            self._update_lbl.setText(
                tr("IsoDAQ Studio v{version} is available — you have v{current}")
                .format(version=version, current=current))
        if self._banner:
            self._banner.show()
        self._notify(version)

    def _notify(self, version: str) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable() or not self._tray:
            return
        self._tray.show()
        self._tray.showMessage(
            tr("IsoDAQ Studio update available"),
            tr("Version {version} is ready to download. Click to open.").format(version=version),
            QSystemTrayIcon.MessageIcon.Information,
            6000,  # ms the notification stays visible
        )

    def open_release_page(self) -> None:
        if self._release_url:
            webbrowser.open(self._release_url)
