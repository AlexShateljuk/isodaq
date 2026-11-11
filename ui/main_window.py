"""
ui/main_window.py — Main application window

Layout — IntelliJ-style tool windows (see ui/tool_window.py):

  Menubar
  ┌───────────────────────────────────────────────────────────┐
  │ Connection toolbar (full width, one row)                  │
  ├──┬──────────────┬─────────────────────────┬───────────┬──┤
  │  │              │  Connection tabs        │           │  │
  │L │  left dock   │  Terminal               │ right     │R │
  │st│  Devices /   │  Find bar               │ dock      │st│
  │ri│  Parsing /   │  Command input          │ Graphs /  │ri│
  │pe│  Macros      │                         │ Indicators│pe│
  │  ├──────────────┴─────────────────────────┴───────────┤  │
  │  │ bottom dock — Events / Analytics / Triggers / Log  │  │
  ├──┴────────────────────────────────────────────────────┴──┤
  │ bottom stripe: Events · N | Analytics | Triggers | Log    │
  ├───────────────────────────────────────────────────────────┤
  │ Statusbar                                                 │
  └───────────────────────────────────────────────────────────┘

Every dock collapses to its stripe, so the terminal can own the window.
Each connection (SER3) owns its own terminal, parser, logger, triggers and
display panels; switching the connection tab swaps all of them.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QTimer, Qt, pyqtSlot
from PyQt6.QtGui import QAction, QColor, QFont, QIntValidator, QKeySequence, QShortcut, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTabBar,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.connection import Connection, PALETTE
from core.macros import MacroRunner
from core.quick_plot import quick_parse
import core.signaling as signaling
from core.i18n import current_language, tr
from ui.logger_panel import LoggerPanel
from ui.log_colorizer_dialog import LogColorizerDialog, match_log_color
from ui.macro_panel import MacroPanel
from ui.parse_panel import ParsePanel
from ui.trigger_panel import TriggerPanel
from ui.analytics_panel import AnalyticsPanel
from ui.chart_panel import ChartPanel
from ui.indicator_panel import IndicatorPanel
from ui.trigger_events_panel import TriggerEventsPanel
from ui.tool_window import StripeBar, ToolDock
from ui.port_list import PortDelegate
from ui.themes import build_stylesheet, install_font_fallbacks, install_layout_fixes, theme_colors, THEME_NAMES, key_from_display, set_current_theme, tint_titlebar
from ui.controllers.session_controller import SessionController
from ui.controllers.update_manager import UpdateManager
from ui.controllers.dev_tools import DevTools
from ui.controllers.search_controller import SearchController
from ui.controllers.settings_manager import SettingsManager
from ui.controllers.serial_controller import SerialController
from ui.controllers.trigger_controller import TriggerController
from ui.controllers.flash_controller import FlashController
from ui.controllers.transfer_controller import TransferController
from ui.controllers.tcp_share_controller import TcpShareController
from ui.controllers.agent_controller import AgentController
from ui.controllers.capture_controller import CaptureController
from ui.frame_button import FrameButton
from ui.number_box import NumberBox
from core.periodic import PeriodicSender
from ui.flash_panel import FlashPanel
from ui.modbus_panel import ModbusPanel
from ui.frame_panel import FramePanel
from ui.control_panel import ControlPanel
from core.controls import FREE_KEYS

# ── Colours (updated when theme changes) ──────────────────────────────────────
C_RX  = QColor("#3ecf8e")
C_TX  = QColor("#ff7b54")
C_SYS = QColor("#f59e0b")
C_OK  = QColor("#22c55e")
C_ERR = QColor("#ef4444")
C_DIM = QColor("#3e4460")
C_FG  = QColor("#d4d4d4")


#: Printable ASCII stays, everything else shows as "." in the HEX view's text column.
_HEX_ASCII = bytes(b if 0x20 <= b < 0x7F else 0x2E for b in range(256))


class _FloatWindow(QWidget):
    """Detached tab panel — returns the widget to its original tab on close."""

    def __init__(self, widget: QWidget, title: str,
                 tabs: "QTabWidget", idx: int):
        super().__init__(None, Qt.WindowType.Window)
        self._widget = widget
        self._tabs   = tabs
        self._idx    = idx
        self._title  = title
        self.setWindowTitle(f"IsoDAQ — {title}")
        self.resize(1280, 800)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(widget)
        widget.show()

    def closeEvent(self, event):            # pylint: disable=invalid-name
        self._widget.setParent(None)        # detach before this window dies
        self._tabs.insertTab(self._idx, self._widget, self._title)
        self._tabs.setCurrentIndex(self._idx)
        event.accept()
        self.deleteLater()


class CollapsibleSection(QWidget):
    """
    Accordion-style sidebar panel.
    Click the header to expand / collapse the content widget.
    Pass extra_widgets to add small action buttons on the right of the header.
    """

    def __init__(self, title: str, content: QWidget,
                 extra_widgets: list | None = None,
                 collapsed: bool = False, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._collapsed = collapsed
        self._build(title, content, extra_widgets or [])

    def _build(self, title: str, content: QWidget, extras: list) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Clickable header
        hdr = QWidget()
        hdr.setObjectName("sectionHeader")
        hdr.setCursor(Qt.CursorShape.PointingHandCursor)
        hdr.setMouseTracking(True)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(10, 8, 8, 8)
        hl.setSpacing(7)

        self._arrow = QLabel("▸" if self._collapsed else "▾")
        self._arrow.setObjectName("sectionArrow")
        self._arrow.setFixedWidth(12)
        hl.addWidget(self._arrow)

        ttl = QLabel(title.upper())
        ttl.setObjectName("sectionTitle")
        hl.addWidget(ttl)
        hl.addStretch()

        for w in extras:
            hl.addWidget(w)

        hdr.mousePressEvent = lambda _: self.toggle()
        root.addWidget(hdr)

        self._content_w = content
        root.addWidget(content)
        content.setVisible(not self._collapsed)

    def toggle(self) -> None:
        self._collapsed = not self._collapsed
        self._content_w.setVisible(not self._collapsed)
        self._arrow.setText("▸" if self._collapsed else "▾")

    @property
    def collapsed(self) -> bool:
        return self._collapsed


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        _ver = QApplication.instance().applicationVersion()
        self.setWindowTitle(f"IsoDAQ Studio v{_ver}")
        self.resize(1300, 780)
        self.setMinimumSize(900, 600)

        # Core services — the active Connection owns the per-device data pipeline
        # (parser / trigger engine / logger / last-parsed). MainWindow reaches
        # them via properties that delegate to the active connection (SER3);
        # transport + display widgets stay here for now.
        self._active_conn = Connection(name="Device 1", parent=self)
        self._connections: list[Connection] = [self._active_conn]
        self._log_colorizer_enabled: set[str] = set()
        # Send through the *active* reader so it follows a transport swap
        # (serial → network → ssh); see SerialController._reader_for().
        self._macro_runner = MacroRunner(lambda data: self._reader.send(data), self)
        self._ssh_sessions: list[dict] = []   # saved SSH profiles (name/host/port/user/key/command; no password)
        self._ssh_params: dict = {}           # active profile (+ in-memory password)
        self._net_url: str = ""               # last-used network target (socket://… / rfc2217://…)
        self._ble_target: str = ""            # last-used Bluetooth device ("Name [address]")
        self._mqtt_url: str = ""              # last-used broker (mqtt://host/topic/#?tx=…)
        self._agent_url: str = ""             # last-used agent port (agent://host/dev/ttyUSB0)
        self._modbus_target: str = ""         # last-used Modbus device (serial port or tcp://…)
        self._target_kind: str = "Serial"     # transport the target field is showing
        self._port_options_menu = None        # built lazily by _open_port_options
        self._float_wins: list[_FloatWindow] = []
        self._terminal_font_size: int = 11
        self._scrollback_limit: int = 5000
        self._current_theme: str = "vscode"
        self._right_panel_visible: bool = False   # collapsed by default
        self._user_scrolling: bool = False
        self._mode: str = "advanced"
        self._signaling_url: str = signaling._DEFAULT_URL
        self._language: str = current_language()   # applied at startup in main.py

        # Session sharing (host + viewer) lives in its own controller (OSS6).
        self._session = SessionController(self)
        # In-app update check / banner / tray notification (OSS6).
        self._updates = UpdateManager(self)
        # Element inspector + theme hot-reload (OSS6).
        self._devtools = DevTools(self)
        # In-terminal find bar (F1) + trigger→line jump (F2) (OSS6).
        self._search = SearchController(self)
        # Config persistence + Preferences dialog (OSS6).
        self._settings = SettingsManager(self)
        # Serial connect/send/ports + command history (OSS6).
        self._serial = SerialController(self)
        # Trigger match/actions + analytics sync + save/load (OSS6).
        self._triggers = TriggerController(self)
        self._flash = FlashController(self)
        self._transfer = TransferController(self)
        self._tcp_share = TcpShareController(self)
        self._capture = CaptureController(self)
        self._agents = AgentController(self)

        self._build_ui()
        self._connect_signals()
        self._start_timers()
        self._settings.load()   # restore persisted state before first port scan
        self._triggers.sync_analytics()  # populate analytics with initial trigger list
        self._serial.refresh_ports()
        self._updates.start()
        self._devtools.setup()

        # Backup save — fires even when the process is killed without closeEvent
        QApplication.instance().aboutToQuit.connect(self._settings.save)

        self._log("SYS", "IsoDAQ Studio started.", C_SYS)
        self._log("SYS", "Scanning serial ports…", C_DIM)

    # ═════════════════════════════════════════════════════════════════════════
    # Active-connection delegation (SER3)
    #   The RX pipeline and controllers read these; they resolve to whichever
    #   Connection is active, so multi-connection support is a matter of
    #   switching _active_conn. See SER3_REFACTOR.md.
    # ═════════════════════════════════════════════════════════════════════════

    @property
    def _parser(self):
        return self._active_conn.parser

    @property
    def _engine(self):
        return self._active_conn.engine

    @property
    def _logger(self):
        return self._active_conn.logger

    @property
    def _last_parsed(self):
        return self._active_conn.last_parsed

    @property
    def _reader(self):
        """The active connection's active transport reader."""
        return self._active_conn.reader

    @property
    def _terminal(self):
        """The active connection's terminal widget."""
        return self._terminals[self._active_conn.id]

    @property
    def _parse_panel(self):
        return self._active_conn.parse

    @property
    def _chart_panel(self):
        return self._active_conn.chart

    @property
    def _indicator_panel(self):
        return self._active_conn.indicator

    @property
    def _trigger_panel(self):
        return self._active_conn.trigger

    @property
    def _trigger_events_panel(self):
        return self._active_conn.events

    @property
    def _analytics_panel(self):
        return self._active_conn.analytics

    @property
    def _logger_panel(self):
        return self._active_conn.logger_panel

    # Per-connection scalars — getter+setter so existing `+=`/`=` call sites
    # (RX byte counter, TX byte counter, line-identity in _log) keep working
    # while the state lives on the active connection.
    @property
    def _rx_bytes(self):
        return self._active_conn.rx_bytes

    @_rx_bytes.setter
    def _rx_bytes(self, value):
        self._active_conn.rx_bytes = value

    @property
    def _tx_bytes(self):
        return self._active_conn.tx_bytes

    @_tx_bytes.setter
    def _tx_bytes(self, value):
        self._active_conn.tx_bytes = value

    @property
    def _line_seq(self):
        return self._active_conn.line_seq

    @_line_seq.setter
    def _line_seq(self, value):
        self._active_conn.line_seq = value

    @property
    def _last_rx_line_id(self):
        return self._active_conn.last_rx_line_id

    @_last_rx_line_id.setter
    def _last_rx_line_id(self, value):
        self._active_conn.last_rx_line_id = value

    @property
    def _current_rx_line_id(self):
        return self._active_conn.current_rx_line_id

    @_current_rx_line_id.setter
    def _current_rx_line_id(self, value):
        self._active_conn.current_rx_line_id = value

    # ═════════════════════════════════════════════════════════════════════════
    # UI construction
    # ═════════════════════════════════════════════════════════════════════════

    def _build_ui(self):
        """Assembles the full window: stylesheet, menu, left/right splitter, statusbar."""
        install_font_fallbacks()   # before any widget resolves a font
        install_layout_fixes()     # before any check box is laid out
        # Only if it differs: applying a style sheet restyles every widget that exists.
        app, css = QApplication.instance(), build_stylesheet(self._current_theme)
        if app.styleSheet() != css:
            app.setStyleSheet(css)
        self._build_menu()

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._update_banner = self._updates.build_banner()
        root.addWidget(self._update_banner)

        # Window-wide connection toolbar (IDE pattern): spans the full width
        # above the split, so it never competes with the terminal for room.
        root.addWidget(self._build_port_bar())

        # ── Tool-window shell (IntelliJ pattern) ─────────────────────────────
        #   [left stripe][ left dock | centre | right dock ][right stripe]
        #                [          bottom dock            ]
        #   [            bottom stripe (full width)                      ]
        body = QWidget()
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)

        self._stripe_left = StripeBar("left")
        body_lay.addWidget(self._stripe_left)

        self._vert_splitter = QSplitter(Qt.Orientation.Vertical)
        self._vert_splitter.setHandleWidth(3)

        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._main_splitter.setHandleWidth(3)
        self._dock_left = ToolDock("left")
        self._main_splitter.addWidget(self._dock_left)
        self._main_splitter.addWidget(self._build_left())
        self._dock_right = ToolDock("right")
        self._main_splitter.addWidget(self._dock_right)
        self._main_splitter.setSizes([200, 700, 330])
        self._main_splitter.setStretchFactor(1, 1)

        self._vert_splitter.addWidget(self._main_splitter)
        self._dock_bottom = ToolDock("bottom")
        self._vert_splitter.addWidget(self._dock_bottom)
        self._vert_splitter.setSizes([560, 170])
        self._vert_splitter.setStretchFactor(0, 1)
        body_lay.addWidget(self._vert_splitter, 1)

        self._stripe_right = StripeBar("right")
        body_lay.addWidget(self._stripe_right)
        root.addWidget(body, 1)

        self._stripe_bottom = StripeBar("bottom")
        root.addWidget(self._stripe_bottom)

        self._populate_tool_windows()
        self._build_statusbar()

        # Docks start collapsed; a persisted set is restored in SettingsManager.
        self._apply_right_panel()

    # ── Menu ──────────────────────────────────────────────────────────────────

    def _build_menu(self):
        """Builds the top menu bar: File / Device / View / Settings / Help."""
        mb = self.menuBar()
        file_m = mb.addMenu(tr("File"))
        file_m.addAction(QAction(tr("Save triggers…"), self, triggered=self._triggers.save_triggers))
        file_m.addAction(QAction(tr("Load triggers…"), self, triggered=self._triggers.load_triggers))
        file_m.addSeparator()
        file_m.addAction(QAction(tr("Exit"), self, triggered=self.close))

        device_m = mb.addMenu(tr("Device"))
        device_m.addAction(QAction(tr("Refresh ports"), self, triggered=self._serial.refresh_ports))
        device_m.addAction(QAction(tr("Build && flash"), self, shortcut="F5",
                                   triggered=lambda: self._flash.run()))
        device_m.addAction(QAction(tr("Send file (XMODEM / YMODEM)"), self,
                                   triggered=lambda: self._show_tool("flash")))
        device_m.addAction(QAction(tr("Share port over TCP…"), self,
                                   triggered=lambda: self._tcp_share.open_dialog()))
        device_m.addAction(QAction(tr("Set up a Raspberry Pi…"), self,
                                   triggered=lambda: self._agents.setup_dialog()))
        device_m.addAction(QAction(tr("Remote agents…"), self,
                                   triggered=lambda: self._agents.open_dialog()))
        device_m.addAction(QAction(tr("Pair with agent…"), self,
                                   triggered=lambda: self._agents.pair_dialog()))
        device_m.addAction(QAction(tr("Capture raw bytes to file (start / stop)…"), self,
                                   triggered=lambda: self._capture.toggle()))

        view_m = mb.addMenu(tr("View"))
        view_m.addAction(QAction(tr("Command Palette…"), self, shortcut="Ctrl+Shift+P",
                                 triggered=self._open_palette))
        # Ctrl/Cmd+K too — the other common palette key (Slack, Linear, VS Code).
        from PyQt6.QtGui import QKeySequence, QShortcut
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self._open_palette)
        view_m.addAction(QAction(tr("Find…"), self, shortcut="Ctrl+F",
                                 triggered=self._search.open))
        view_m.addSeparator()
        self._mode_action = QAction(tr("Simple Mode"), self, checkable=True,
                                    shortcut="Ctrl+Shift+M",
                                    triggered=self._toggle_mode)
        view_m.addAction(self._mode_action)
        view_m.addSeparator()
        view_m.addAction(QAction(tr("Right Panel"), self,
                                 triggered=self._toggle_right_panel,
                                 shortcut="Ctrl+Shift+R"))
        view_m.addAction(QAction(tr("Detach Tool Window"), self,
                                 triggered=self._float_current_tab, shortcut="F4"))
        view_m.addSeparator()
        theme_m = view_m.addMenu(tr("Theme"))
        for display_name in THEME_NAMES:
            theme_m.addAction(QAction(display_name, self,
                                      triggered=lambda _=None, n=display_name:
                                      self._apply_theme(key_from_display(n))))

        settings_m = mb.addMenu(tr("Settings"))
        settings_m.addAction(QAction(tr("Log Colorizer…"), self, triggered=self._open_log_colorizer))
        settings_m.addAction(QAction(tr("Preferences…"),   self, triggered=self._settings.open_preferences))

        help_m = mb.addMenu(tr("Help"))
        help_m.addAction(QAction(tr("Check for Updates"), self, triggered=self._updates.check_now))
        help_m.addSeparator()
        help_m.addAction(QAction(tr("About IsoDAQ Studio…"), self, triggered=self._show_about))

    # ── Left panel ────────────────────────────────────────────────────────────

    def _build_left(self) -> QWidget:
        """Left panel (54 %): port settings bar + terminal output + command input."""
        w = QWidget()
        w.setObjectName("leftPanel")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        lay.addWidget(self._build_conn_bar())

        # One terminal widget per connection, shown via a stack (SER3). The
        # active connection's terminal is exposed as `self._terminal`.
        self._terminal_stack = QStackedWidget()
        self._terminals: dict[str, QTextEdit] = {}
        self._add_terminal(self._active_conn)
        lay.addWidget(self._terminal_stack)

        lay.addWidget(self._search.build_bar())
        lay.addWidget(self._build_input_bar())
        return w

    def _build_conn_bar(self) -> QWidget:
        """Connection tab strip (SER3): one tab per connection + an Add button.
        Switching tabs switches the active connection; close ends one."""
        bar = QWidget()
        bar.setObjectName("connBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 4, 8, 0)
        lay.setSpacing(6)

        self._conn_bar = QTabBar()
        self._conn_bar.setObjectName("connTabs")
        self._conn_bar.setExpanding(False)
        self._conn_bar.setDrawBase(False)
        # No eliding: Qt's tab size hint undercounts the close button + QSS
        # padding, so names were cut inside tabs that had room. Labels are short
        # (Connection.short_name); overflow scrolls instead, as in IDE tab strips.
        self._conn_bar.setElideMode(Qt.TextElideMode.ElideNone)
        self._conn_bar.setUsesScrollButtons(True)
        self._conn_bar.currentChanged.connect(self._on_conn_tab_changed)
        lay.addWidget(self._conn_bar, 0)
        # Let the strip shrink below its hint so scroll buttons appear on overflow.
        self._conn_bar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        # "+" sits right after the last tab and the strip is left-aligned, as
        # in IDE editor tabs (macOS would otherwise centre a stretched QTabBar).
        add_btn = QPushButton("+")
        add_btn.setObjectName("addConnBtn")
        add_btn.setFixedSize(22, 22)
        add_btn.setToolTip(tr("Add a connection"))
        add_btn.clicked.connect(self._add_connection)
        lay.addWidget(add_btn)
        lay.addStretch()

        self._add_conn_tab(self._active_conn)
        return bar

    # ── Connection tab strip ──────────────────────────────────────────────────

    def _conn_by_id(self, cid):
        return next((c for c in self._connections if c.id == cid), None)

    def _conn_tab_index(self, cid: str) -> int:
        for i in range(self._conn_bar.count()):
            if self._conn_bar.tabData(i) == cid:
                return i
        return -1

    def _add_conn_tab(self, conn) -> None:
        idx = self._conn_bar.addTab(conn.name)
        self._conn_bar.setTabData(idx, conn.id)
        # A theme-styled close button (avoids the stock red QTabBar ×).
        close = QPushButton("×")
        close.setObjectName("connCloseBtn")
        close.setFixedSize(16, 16)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setToolTip(tr("Close connection"))
        close.clicked.connect(lambda _=False, cid=conn.id: self._close_conn_by_id(cid))
        self._conn_bar.setTabButton(idx, QTabBar.ButtonPosition.RightSide, close)
        self._update_conn_tab(conn)

    def _update_conn_tab(self, conn) -> None:
        """Refresh a tab's label + connected state (shown via text colour:
        the connection's tint when connected, dim when idle)."""
        idx = self._conn_tab_index(conn.id)
        if idx < 0:
            return
        self._conn_bar.setTabText(idx, conn.short_name)
        self._conn_bar.setTabToolTip(idx, conn.name)
        self._conn_bar.setTabTextColor(idx, self._conn_state_color(conn))
        self._refresh_devices_panel()

    @staticmethod
    def _conn_state_color(conn) -> QColor:
        """Connected → the connection's tint; waiting to reconnect → amber;
        failed / link lost → error red; idle → dim."""
        if conn.reconnecting or conn.flash_runner is not None:
            return C_SYS
        if conn.reader.isRunning():
            return conn.color
        return C_ERR if conn.failed else C_DIM

    def _add_connection(self) -> None:
        """Create a new connection (its own terminal + transport) and focus it."""
        n = len(self._connections)
        conn = Connection(name=f"Device {n + 1}",
                          color=PALETTE[n % len(PALETTE)], parent=self)
        self._connections.append(conn)
        self._add_terminal(conn)
        self._make_display_panels(conn)               # its own Parsing/Graphs/Indicators
        self._serial.wire_reader(conn, conn.reader)   # wire its default reader
        self._add_conn_tab(conn)
        self._conn_bar.setCurrentIndex(self._conn_tab_index(conn.id))
        self._refresh_devices_panel()

    def _on_conn_tab_changed(self, index: int) -> None:
        conn = self._conn_by_id(self._conn_bar.tabData(index))
        if conn is not None:
            self._set_active_connection(conn)

    def _close_conn_by_id(self, cid) -> None:
        if len(self._connections) <= 1:
            return   # always keep at least one connection
        conn = self._conn_by_id(cid)
        if conn is None:
            return
        index = self._conn_tab_index(cid)
        self._flash.shutdown(conn)
        self._transfer.shutdown(conn)
        self._serial.set_publishing(conn, False)
        self._serial.stop_repeat(conn)
        self._tcp_share.stop(conn)
        self._capture.stop(conn)
        conn.snapshots.flush()
        self._serial.stop_reconnect(conn, quiet=True)
        for r in conn.readers.values():
            if r.isRunning():
                r.disconnect_port()
        if conn.logger.active:
            conn.logger.stop()
        self._conn_bar.removeTab(index)   # fires currentChanged → new active
        term = self._terminals.pop(conn.id, None)
        if term is not None:
            self._terminal_stack.removeWidget(term)
            term.deleteLater()
        for stack, panel in ((self._parse_stack, conn.parse),
                             (self._chart_stack, conn.chart),
                             (self._indicator_stack, conn.indicator),
                             (self._trigger_stack, conn.trigger),
                             (self._logger_stack, conn.logger_panel),
                             (self._flash_stack, conn.flash_panel),
                             (self._modbus_stack, conn.modbus),
                             (self._frames_stack, conn.frames),
                             (self._events_stack, conn.events),
                             (self._analytics_stack, conn.analytics)):
            stack.removeWidget(panel)
            panel.deleteLater()
        if conn in self._connections:
            self._connections.remove(conn)
        conn.deleteLater()
        self._refresh_devices_panel()

    def _set_active_connection(self, conn) -> None:
        if conn is self._active_conn:
            return
        self._save_toolbar(self._active_conn)
        self._active_conn = conn
        self._restore_toolbar(conn)
        self._terminal_stack.setCurrentWidget(self._terminals[conn.id])
        if self._chk_auto.isChecked():   # show the newest output, not where it was
            if conn.term_pending:
                self._flush_terminal(conn)
            sb =self._terminals[conn.id].verticalScrollBar()
            sb.setValue(sb.maximum())
        # Swap the per-connection right-side panels to this connection's.
        self._chart_stack.setCurrentWidget(conn.chart)
        self._indicator_stack.setCurrentWidget(conn.indicator)
        self._parse_stack.setCurrentWidget(conn.parse)
        self._flash_stack.setCurrentWidget(conn.flash_panel)
        self._modbus_stack.setCurrentWidget(conn.modbus)
        self._frames_stack.setCurrentWidget(conn.frames)
        self._trigger_stack.setCurrentWidget(conn.trigger)
        self._logger_stack.setCurrentWidget(conn.logger_panel)
        self._events_stack.setCurrentWidget(conn.events)
        self._analytics_stack.setCurrentWidget(conn.analytics)
        self._on_events_count(conn, conn.events.count)
        self._refresh_conn_state()
        self._refresh_devices_panel()
        self._serial._show_repeat(conn)
        self._refresh_activity()

    def _save_toolbar(self, conn) -> None:
        """Remember the toolbar selection as *conn*'s own (SER3)."""
        conn.toolbar = {"transport": self._transport_combo.currentText(),
                        "target": self._port_combo.currentText(),
                        "baud": self._baud_combo.currentText()}

    def _restore_toolbar(self, conn) -> None:
        """Show *conn*'s transport / target / baud in the shared toolbar. A new
        connection has none saved yet and inherits the current selection."""
        tb = conn.toolbar
        if not tb:
            return
        self._transport_combo.setCurrentText(tb["transport"])
        if self._port_combo.isEditable():
            self._port_combo.setEditText(tb["target"])
        else:
            i = self._port_combo.findText(tb["target"])
            if i >= 0:
                self._port_combo.setCurrentIndex(i)
        self._baud_combo.setCurrentText(tb["baud"])

    def _refresh_conn_state(self) -> None:
        """Reflect the active connection's reader state on the Connect button +
        status bar (called when switching tabs)."""
        conn = self._active_conn
        running = conn.reader.isRunning()
        busy = running or conn.reconnecting
        self._conn_btn.setText(tr("Stop") if conn.reconnecting
                               else tr("Disconnect") if running else tr("Connect"))
        self._conn_btn.setObjectName("disconnectBtn" if busy else "connectBtn")
        self._repolish(self._conn_btn)
        # A flashing tool or a file transfer has the port.
        self._conn_btn.setEnabled(conn.flash_runner is None and conn.transfer is None)
        if conn.flash_runner is not None:
            self._sb_conn.setText("● " + tr("Flashing…"))
            self._sb_conn.setStyleSheet(f"color:{C_SYS.name()}")
        elif conn.reconnecting:
            self._sb_conn.setText("● " + tr("Reconnecting… {s} s").format(s=conn.reconnect_tries))
            self._sb_conn.setStyleSheet(f"color:{C_SYS.name()}")
        elif running:
            self._sb_conn.setText(f"● {conn.name}")
            self._sb_conn.setStyleSheet(f"color:{C_OK.name()}")
        else:
            self._sb_conn.setText("● " + (tr("Connection failed") if conn.failed
                                          else tr("Disconnected")))
            self._sb_conn.setStyleSheet(f"color:{(C_ERR if conn.failed else C_DIM).name()}")
        self._sb_rx.setText(f"RX: {self._fmt_bytes(conn.rx_bytes)}")
        self._sb_tx.setText(f"TX: {self._fmt_bytes(conn.tx_bytes)}")
        self._refresh_live_stats()

    def _refresh_live_stats(self) -> None:
        """Rate / errors / session clock of the active connection."""
        conn = self._active_conn
        running = conn.reader.isRunning()
        self._sb_rate.setText(tr("Rate: {n} lines/s").format(n=f"{conn.rate:,}")
                              if running else tr("Rate: —"))
        self._sb_err.setText(tr("Errors: {n}").format(n=conn.errors))
        self._sb_err.setStyleSheet(f"color:{C_ERR.name()}" if conn.errors else "")
        t = conn.session_sec
        self._sb_sess.setText(tr("Session: {t}").format(
            t=f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"))

    def _add_terminal(self, conn) -> QTextEdit:
        """Create, configure and register a terminal widget for *conn*."""
        term = QTextEdit()
        term.setObjectName("terminal")
        term.setReadOnly(True)
        term.setFont(QFont("JetBrains Mono", self._terminal_font_size))
        # Per-terminal scroll tracking (pauses autoscroll while the user drags).
        sb = term.verticalScrollBar()
        sb.sliderPressed.connect(lambda: setattr(self, "_user_scrolling", True))
        sb.sliderReleased.connect(lambda: setattr(self, "_user_scrolling", False))
        self._terminals[conn.id] = term
        self._terminal_stack.addWidget(term)
        return term

    def _build_port_bar(self) -> QWidget:
        """Single compact connection toolbar (IDE-style, ~30px).

        Inline: transport, target, rescan, baud, Connect, overflow, then
        Share / Join / panel toggle on the right. Everything you set once —
        data bits, flow control, the view toggles, font size, Clear — lives in
        the ``⋯`` overflow popover, which hosts the *same* widgets so settings
        persistence and the serial controller keep reading them unchanged.
        """
        bar = QWidget()
        bar.setObjectName("portBar")
        # Fixed vertical policy: the root layout must not stretch the toolbar.
        bar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 3, 8, 3)
        lay.setSpacing(4)

        self._transport_combo = QComboBox()
        self._transport_combo.addItems(["Serial", "Network", "Agent", "SSH", "BLE", "MQTT",
                                        "Modbus"])
        self._transport_combo.setFixedWidth(108)   # fits "Network" in the mono font
        self._transport_combo.setToolTip(
            tr("Transport: local serial, TCP/RFC2217 network, SSH, Bluetooth LE, MQTT or Modbus"))
        lay.addWidget(self._transport_combo)

        self._port_combo = QComboBox()
        self._port_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._port_combo.setMinimumWidth(170)
        self._port_combo.activated.connect(self._on_port_activated)
        self._port_combo.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._port_combo.customContextMenuRequested.connect(self._on_port_context_menu)
        self._port_combo.setItemDelegate(PortDelegate(self._port_combo))
        lay.addWidget(self._port_combo, 1)

        self._refresh_btn = QPushButton("↻")
        self._refresh_btn.setFixedWidth(28)
        self._refresh_btn.setStyleSheet("font-size:14px;padding:0;")
        self._refresh_btn.setToolTip(tr("Rescan serial ports"))
        self._refresh_btn.clicked.connect(self._on_refresh_clicked)
        lay.addWidget(self._refresh_btn)

        # Editable (CMP3): any rate the driver accepts — 74880 for ESP8266 boot
        # logs, 250000, 1000000+ for fast USB bridges.
        self._baud_combo = QComboBox()
        self._baud_combo.setEditable(True)
        self._baud_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._baud_combo.addItems(["9600", "19200", "38400", "57600", "74880",
                                   "115200", "230400", "250000", "460800", "500000",
                                   "921600", "1000000", "2000000"])
        self._baud_combo.setCurrentText("115200")
        self._baud_combo.setValidator(QIntValidator(1, 100_000_000, self._baud_combo))
        self._baud_combo.setFixedWidth(104)
        # Editable, but don't grab keyboard focus at startup (click to type).
        self._baud_combo.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._baud_combo.setToolTip(tr("Baud rate — pick one or type any rate"))
        lay.addWidget(self._baud_combo)

        self._conn_btn = QPushButton(tr("Connect"))
        self._conn_btn.setObjectName("connectBtn")
        self._conn_btn.setFixedWidth(104)
        self._conn_btn.clicked.connect(self._serial.toggle_connection)
        lay.addWidget(self._conn_btn)

        self._more_btn = QPushButton("⋯")
        self._more_btn.setObjectName("moreBtn")
        self._more_btn.setFixedWidth(26)
        self._more_btn.setToolTip(tr("Port options and terminal view settings"))
        self._more_btn.clicked.connect(self._open_port_options)
        lay.addWidget(self._more_btn)

        lay.addStretch()

        self._share_btn = QPushButton(tr("Share"))
        self._share_btn.setObjectName("shareBtn")
        self._share_btn.setToolTip(tr("Share this serial session with a colleague"))
        self._share_btn.clicked.connect(self._session.toggle_share)
        lay.addWidget(self._share_btn)

        self._join_btn = QPushButton(tr("Join"))
        self._join_btn.setObjectName("joinBtn")
        self._join_btn.setToolTip(tr("Connect to a shared session"))
        self._join_btn.clicked.connect(self._session.open_join_dialog)
        lay.addWidget(self._join_btn)

        self._panel_toggle_btn = QPushButton("⊞")
        self._panel_toggle_btn.setObjectName("panelToggleBtn")
        self._panel_toggle_btn.setFixedSize(26, 24)
        self._panel_toggle_btn.setToolTip(tr("Show/hide right panel  (Ctrl+Shift+R)"))
        self._panel_toggle_btn.clicked.connect(self._toggle_right_panel)
        lay.addWidget(self._panel_toggle_btn)

        # ── Widgets that moved into the ⋯ popover ────────────────────────────
        # Created here (not in the popover) so they exist for the whole window
        # lifetime; the popover only re-parents them when it opens.
        self._data_combo = QComboBox()
        self._data_combo.addItems(["8N1", "8E1", "8O1", "7N1"])
        self._flow_combo = QComboBox()
        self._flow_combo.addItems(["None", "RTS/CTS", "XON/XOFF"])
        self._chk_ts   = QCheckBox(tr("Timestamp")); self._chk_ts.setChecked(True)
        self._chk_hex  = QCheckBox("HEX")
        self._chk_auto = QCheckBox(tr("Autoscroll")); self._chk_auto.setChecked(True)
        self._chk_echo = QCheckBox(tr("Echo"));       self._chk_echo.setChecked(True)
        # Modem-control lines (CMP2). Asserted is pyserial's default on open.
        self._chk_dtr = QCheckBox("DTR"); self._chk_dtr.setChecked(True)
        self._chk_rts = QCheckBox("RTS"); self._chk_rts.setChecked(True)
        self._chk_dtr.setToolTip(tr("Data Terminal Ready — applied on connect and when toggled"))
        self._chk_rts.setToolTip(tr("Request To Send — applied on connect and when toggled"))
        self._chk_dtr.toggled.connect(lambda on: self._serial.set_line("dtr", on))
        self._chk_rts.toggled.connect(lambda on: self._serial.set_line("rts", on))
        self._chk_reconnect = QCheckBox(tr("Auto-reconnect"))
        self._chk_reconnect.setChecked(True)
        self._chk_reconnect.setToolTip(tr(
            "When the link drops (board reset, cable re-plugged, host down), "
            "keep retrying with the same settings"))
        self._font_size_lbl = QLabel(str(self._terminal_font_size))
        self._font_size_lbl.setFixedWidth(20)
        self._font_size_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._font_size_lbl.setObjectName("dimLabelMono")

        # Wire transport switching last (all widgets now exist).
        self._transport_combo.currentTextChanged.connect(self._on_transport_changed)
        return bar

    def _open_port_options(self) -> None:
        """Popover under ⋯ holding the set-once port options and view toggles.

        Built once and reused: a QWidgetAction owns its default widget, so a
        throwaway menu would destroy the combos/checkboxes that the rest of the
        app reads."""
        from PyQt6.QtWidgets import QGridLayout, QMenu, QWidgetAction

        if getattr(self, "_port_options_menu", None) is not None:
            self._port_options_menu.exec(self._more_btn.mapToGlobal(
                self._more_btn.rect().bottomLeft()))
            return

        menu = QMenu(self)
        self._port_options_menu = menu
        panel = QWidget()
        grid = QGridLayout(panel)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        grid.addWidget(self._lbl(tr("Data"), dim=True), 0, 0)
        grid.addWidget(self._data_combo, 0, 1)
        grid.addWidget(self._lbl(tr("Flow"), dim=True), 1, 0)
        grid.addWidget(self._flow_combo, 1, 1)

        # Lines: DTR / RTS / BREAK and the reset sequences (CMP2).
        grid.addWidget(self._lbl(tr("Lines"), dim=True), 2, 0)
        lines_row = QHBoxLayout()
        lines_row.setSpacing(8)
        lines_row.addWidget(self._chk_dtr)
        lines_row.addWidget(self._chk_rts)
        brk = QPushButton("BREAK")
        brk.setFixedHeight(22)
        brk.setToolTip(tr("Send a 250 ms BREAK"))
        brk.clicked.connect(lambda: self._serial.send_break())
        lines_row.addWidget(brk)
        lines_row.addStretch()
        grid.addLayout(lines_row, 2, 1)

        grid.addWidget(self._lbl(tr("Reset"), dim=True), 3, 0)
        reset_row = QHBoxLayout()
        reset_row.setSpacing(6)
        for text, kind, tip in (
                (tr("Board"), "esp", tr("Pulse RTS → EN (ESP32/ESP8266 dev boards)")),
                (tr("DTR pulse"), "dtr", tr("Drop DTR for 100 ms (Arduino Uno/Nano/Mega)")),
                (tr("Bootloader"), "esp_boot",
                 tr("ESP ROM bootloader: hold GPIO0 low (DTR) while resetting (RTS)"))):
            b = QPushButton(text)
            b.setFixedHeight(22)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=kind: (menu.close(), self._serial.reset_board(k)))
            reset_row.addWidget(b)
        reset_row.addStretch()
        grid.addLayout(reset_row, 3, 1)
        grid.addWidget(self._lbl(tr("Link"), dim=True), 4, 0)
        grid.addWidget(self._chk_reconnect, 4, 1)

        grid.addWidget(self._hline(), 5, 0, 1, 2)

        grid.addWidget(self._chk_ts,   6, 0)
        grid.addWidget(self._chk_hex,  6, 1)
        grid.addWidget(self._chk_auto, 7, 0)
        grid.addWidget(self._chk_echo, 7, 1)

        grid.addWidget(self._hline(), 8, 0, 1, 2)

        font_row = QHBoxLayout()
        font_row.setSpacing(4)
        font_row.addWidget(self._lbl(tr("Font"), dim=True))
        font_dec = QPushButton("−")
        font_dec.setFixedSize(22, 22)
        font_dec.setStyleSheet("padding:0;")
        font_dec.setToolTip(tr("Decrease terminal font size"))
        font_dec.clicked.connect(lambda: self._change_font_size(-1))
        font_row.addWidget(font_dec)
        font_row.addWidget(self._font_size_lbl)
        font_inc = QPushButton("+")
        font_inc.setFixedSize(22, 22)
        font_inc.setStyleSheet("padding:0;")
        font_inc.setToolTip(tr("Increase terminal font size"))
        font_inc.clicked.connect(lambda: self._change_font_size(+1))
        font_row.addWidget(font_inc)
        font_row.addStretch()
        clr = QPushButton(tr("Clear"))
        clr.setFixedHeight(22)
        clr.clicked.connect(lambda: (self._active_conn.term_pending.clear(),
                                     self._terminal.clear(), menu.close()))
        font_row.addWidget(clr)
        grid.addLayout(font_row, 9, 0, 1, 2)

        act = QWidgetAction(menu)
        act.setDefaultWidget(panel)
        menu.addAction(act)
        menu.exec(self._more_btn.mapToGlobal(
            self._more_btn.rect().bottomLeft()))

    @staticmethod
    def _hline() -> QWidget:
        from PyQt6.QtWidgets import QFrame
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setObjectName("sep")
        return line

    #: Transports whose target is typed: where the text is kept between switches.
    _TYPED_TARGETS = {"Network": "_net_url", "Agent": "_agent_url", "BLE": "_ble_target",
                      "MQTT": "_mqtt_url",
                      "Modbus": "_modbus_target"}

    def _on_transport_changed(self, kind: str) -> None:
        """Adapt the target field to the selected transport.

        Serial → port list (rescanned). Network → editable URL field. SSH → a
        read-only summary; the credentials are gathered by a dialog on Connect.
        """
        combo = self._port_combo
        # Remember what was typed before we repurpose the field.
        typed = self._TYPED_TARGETS.get(getattr(self, "_target_kind", ""))
        if typed:
            setattr(self, typed, combo.currentText().strip())
        self._target_kind = kind
        # Baud means nothing to these.
        self._baud_combo.setEnabled(kind not in ("BLE", "MQTT", "SSH"))

        if kind == "Network":
            combo.setEditable(True)
            combo.clear()
            combo.lineEdit().setPlaceholderText(
                "socket://192.168.1.50:23   ·   rfc2217://host:4001")
            combo.setEditText(self._net_url)
            self._refresh_btn.setEnabled(False)
            self._refresh_btn.setText("↻")
        elif kind == "Agent":
            combo.setEditable(True)
            combo.clear()
            combo.lineEdit().setPlaceholderText(
                tr("agent://raspberrypi.local/dev/ttyUSB0   ·   ↻ lists paired agents' ports"))
            self._agents.populate_combo()
            combo.setEditText(self._agent_url)
            self._refresh_btn.setEnabled(True)
            self._refresh_btn.setText("↻")
            self._refresh_btn.setToolTip(tr("List the ports of paired agents"))
        elif kind == "MQTT":
            combo.setEditable(True)
            combo.clear()
            combo.lineEdit().setPlaceholderText(
                "mqtt://broker.local/sensors/#?tx=commands/device")
            combo.setEditText(self._mqtt_url)
            self._refresh_btn.setEnabled(False)
            self._refresh_btn.setText("↻")
        elif kind == "Modbus":
            combo.setEditable(True)
            combo.clear()
            combo.lineEdit().setPlaceholderText(tr("Serial port (RTU), or tcp://192.168.1.10:502"))
            self._serial.populate_modbus_combo(self._modbus_target)
            self._refresh_btn.setEnabled(True)
            self._refresh_btn.setText("↻")
            self._refresh_btn.setToolTip(tr("Rescan serial ports"))
        elif kind == "BLE":
            combo.setEditable(True)
            combo.clear()
            combo.lineEdit().setPlaceholderText(tr("Press ↻ to scan for Bluetooth devices"))
            combo.setEditText(self._ble_target)
            self._serial.populate_ble_combo()
            self._refresh_btn.setEnabled(True)
            self._refresh_btn.setText("↻")
            self._refresh_btn.setToolTip(tr("Scan for Bluetooth devices"))
        elif kind == "SSH":
            combo.setEditable(False)
            self._serial.populate_ssh_combo()
            self._refresh_btn.setEnabled(True)
            self._refresh_btn.setText("✎")
            self._refresh_btn.setToolTip(tr("Edit the selected SSH session"))
        else:  # Serial
            combo.setEditable(False)
            self._refresh_btn.setEnabled(True)
            self._refresh_btn.setText("↻")
            self._refresh_btn.setToolTip(tr("Rescan serial ports"))
            self._serial.refresh_ports()

    def _on_refresh_clicked(self) -> None:
        """⟳ / ✎ button — rescan ports (Serial) or edit the SSH session (SSH)."""
        kind = self._transport_combo.currentText()
        if kind == "SSH":
            self._serial.edit_ssh_session(new=False)
        elif kind == "BLE":
            self._serial.scan_ble()
        elif kind == "Agent":
            self._agents.scan()
        elif kind == "Modbus":
            self._serial.populate_modbus_combo(self._port_combo.currentText().strip())
        else:
            self._serial.refresh_ports()

    def _on_port_activated(self, index: int) -> None:
        """User picked an entry in the target combo. Only SSH acts on it
        (selecting a saved session, or the ＋ Add entry)."""
        if self._transport_combo.currentText() == "SSH":
            self._serial.handle_ssh_activation(index)

    def _on_port_context_menu(self, pos) -> None:
        """Right-click a saved SSH session → Edit… / Delete straight from the list."""
        if self._transport_combo.currentText() != "SSH":
            return
        idx = self._port_combo.currentIndex()
        if idx < 0 or idx >= len(self._ssh_sessions):
            return
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self._port_combo)
        act_edit = menu.addAction(tr("Edit…"))
        act_del = menu.addAction(tr("Delete"))
        chosen = menu.exec(self._port_combo.mapToGlobal(pos))
        if chosen is act_edit:
            self._serial.edit_ssh_session(new=False, index=idx)
        elif chosen is act_del:
            self._serial.delete_ssh_session(idx)

    def _build_input_bar(self) -> QWidget:
        """
        Bottom bar of the left panel: the command line.
        Command line edit + EOL selector + Send button.

        Parsing configuration used to live here as a "parser strip"; it now lives
        entirely in the right-hand **Parsing** sidebar section (channel list +
        editor + default prefix), so all parsing is in one place.
        """
        bar = QWidget()
        bar.setObjectName("inputBar")
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(5)

        # Command row
        cr = QHBoxLayout()
        cr.setSpacing(5)
        self._cmd_edit = QLineEdit()
        self._cmd_edit.setObjectName("cmdEdit")
        self._cmd_edit.setPlaceholderText(tr("Command… (↑↓ history)"))
        self._cmd_edit.installEventFilter(self._serial)
        self._cmd_edit.returnPressed.connect(self._serial.send_command)
        cr.addWidget(self._cmd_edit)
        # One-click HEX view (mirrors the HEX option in the ⋯ popover).
        self._hex_btn = QPushButton("HEX")
        self._hex_btn.setObjectName("hexToggle")
        self._hex_btn.setCheckable(True)
        self._hex_btn.setFixedWidth(44)
        self._hex_btn.setToolTip(tr("Show received data as a hex dump (offset · bytes · ASCII)"))
        self._hex_btn.toggled.connect(self._chk_hex.setChecked)
        self._chk_hex.toggled.connect(self._hex_btn.setChecked)
        cr.addWidget(self._hex_btn)
        self._eol_combo = QComboBox()
        self._eol_combo.addItems(["\\r\\n","\\n","\\r","None"])
        self._eol_combo.setFixedWidth(66)
        cr.addWidget(self._eol_combo)
        self._frame_btn = FrameButton()
        self._frame_btn.changed.connect(self._on_frame_changed)
        cr.addWidget(self._frame_btn)
        self._send_btn = QPushButton(tr("Send"))
        self._send_btn.setObjectName("sendBtn")
        self._send_btn.setFixedWidth(54)
        self._send_btn.clicked.connect(self._serial.send_command)
        cr.addWidget(self._send_btn)
        lay.addLayout(cr)

        return bar

    # ── Tool windows ──────────────────────────────────────────────────────────

    #: tool key → (edge, translated title). Defines the whole dock layout.
    TOOLS = (
        ("devices",   "left",   "Devices"),
        ("parsing",   "left",   "Parsing"),
        ("macros",    "left",   "Macros"),
        ("custom",    "left",   "Custom command"),
        ("graphs",    "right",  "Graphs"),
        ("indicators", "right", "Indicators"),
        ("controls",  "right",  "Controls"),
        ("events",    "bottom", "Events"),
        ("analytics", "bottom", "Analytics"),
        ("triggers",  "bottom", "Triggers"),
        ("logger",    "bottom", "Data Logger"),
        ("flash",     "bottom", "Flash"),
        ("modbus",    "bottom", "Modbus"),
        ("frames",    "bottom", "Binary frames"),
    )

    def _populate_tool_windows(self) -> None:
        """Create every tool's content widget, register it on its dock, and add
        the matching stripe button."""
        # Per-connection stacks (filled by _make_display_panels per connection).
        self._chart_stack     = QStackedWidget()
        self._indicator_stack = QStackedWidget()
        self._events_stack    = QStackedWidget()
        self._analytics_stack = QStackedWidget()
        self._trigger_stack   = QStackedWidget()
        self._logger_stack    = QStackedWidget()
        self._parse_stack     = QStackedWidget()
        self._flash_stack     = QStackedWidget()
        self._modbus_stack    = QStackedWidget()
        self._frames_stack    = QStackedWidget()

        self._macro_panel = MacroPanel(self._macro_runner)
        self._control_panel = ControlPanel(self._serial.send_bytes)
        for key in FREE_KEYS:
            QShortcut(QKeySequence(key), self,
                      activated=lambda k=key: self._control_panel.fire_key(k))
        self._devices_panel = self._build_devices_panel()

        content = {
            "devices":    self._devices_panel,
            "parsing":    self._parse_stack,
            "macros":     self._macro_panel,
            "custom":     self._build_custom_cmd(),
            "flash":      self._flash_stack,
            "modbus":     self._modbus_stack,
            "frames":     self._frames_stack,
            "graphs":     self._chart_stack,
            "indicators": self._indicator_stack,
            "controls":   self._control_panel,
            "events":     self._events_stack,
            "analytics":  self._analytics_stack,
            "triggers":   self._trigger_stack,
            "logger":     self._logger_stack,
        }
        docks   = {"left": self._dock_left, "right": self._dock_right,
                   "bottom": self._dock_bottom}
        stripes = {"left": self._stripe_left, "right": self._stripe_right,
                   "bottom": self._stripe_bottom}

        actions = self._tool_actions()
        self._tool_edge: dict[str, str] = {}
        for key, edge, title in self.TOOLS:
            docks[edge].add_tool(key, tr(title), content[key], actions.get(key))
            stripes[edge].add_tool(key, tr(title))
            self._tool_edge[key] = edge

        for edge, dock in docks.items():
            dock.closed.connect(lambda e=edge: self._close_dock(e))
            stripes[edge].clicked_tool.connect(self._toggle_tool)
            dock.hide()

        # Legacy key kept for SettingsManager's "sections" entry (now unused).
        self._sidebar_sections: dict = {}
        # Now that the stacks exist, build the first connection's panels.
        self._make_display_panels(self._active_conn)
        # The first connection's tab was added before the Devices list
        # existed, so populate it now.
        self._refresh_devices_panel()

    def _toggle_tool(self, key: str) -> None:
        """Stripe click: open the tool, or collapse its dock if already shown."""
        edge = self._tool_edge[key]
        dock, stripe = self._dock_for(edge)
        if dock.isVisible() and dock.current == key:
            self._close_dock(edge)
            return
        dock.show_tool(key)
        dock.show()
        stripe.set_active(key)
        if edge == "right":
            self._right_panel_visible = True
            self._panel_toggle_btn.setText("⊞")

    def _close_dock(self, edge: str) -> None:
        dock, stripe = self._dock_for(edge)
        dock.hide()
        stripe.set_active(None)
        if edge == "right":
            self._right_panel_visible = False
            self._panel_toggle_btn.setText("⊟")

    def _dock_for(self, edge: str):
        return ({"left": self._dock_left, "right": self._dock_right,
                 "bottom": self._dock_bottom}[edge],
                {"left": self._stripe_left, "right": self._stripe_right,
                 "bottom": self._stripe_bottom}[edge])

    def _tool_actions(self) -> dict[str, list[QWidget]]:
        """Per-tool header buttons (what used to sit on the sidebar sections)."""
        def _btn(text, tip, slot, width=None, obj="add"):
            b = QPushButton(text)
            b.setObjectName(obj)
            b.setFixedHeight(18)
            if width:
                b.setFixedWidth(width)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            return b

        return {
            "macros": [
                _btn("📁", tr("Send file directly over serial"),
                     lambda: self._macro_panel._send_file_direct(), 22, "iconAct"),
                _btn(tr("+ Add"), tr("New macro"),
                     lambda: self._macro_panel._new_macro()),
            ],
            "controls": [
                _btn(tr("Import"), tr("Add controls from a file"),
                     lambda: self._control_panel.import_file(), None, "iconAct"),
                _btn(tr("Export"), tr("Save the controls to a file"),
                     lambda: self._control_panel.export_file(), None, "iconAct"),
                _btn(tr("+ Add"), tr("New button, toggle, slider or number box"),
                     lambda: self._control_panel.add()),
            ],
            "modbus": [
                _btn(tr("Import"), tr("Load a register map from a file"),
                     lambda: self._active_conn.modbus.import_file(), None, "iconAct"),
                _btn(tr("Export"), tr("Save the register map to a file"),
                     lambda: self._active_conn.modbus.export_file(), None, "iconAct"),
                _btn(tr("+ Add"), tr("Add a register"),
                     lambda: self._active_conn.modbus.add_point()),
            ],
            "frames": [
                _btn(tr("Import"), tr("Load a frame layout from a file"),
                     lambda: self._active_conn.frames.import_file(), None, "iconAct"),
                _btn(tr("Export"), tr("Save the frame layout to a file"),
                     lambda: self._active_conn.frames.export_file(), None, "iconAct"),
                _btn(tr("+ Add"), tr("Add a field"),
                     lambda: self._active_conn.frames.add_field()),
            ],
            "parsing": [
                _btn(tr("+ Add"), tr("Add a channel"),
                     lambda: self._parse_panel.open_editor()),
            ],
            "events": [
                _btn(tr("Clear"), tr("Clear the event log"),
                     lambda: self._trigger_events_panel.clear(), None, "iconAct"),
            ],
            "logger": [
                _btn("📂", tr("Open log folder"),
                     lambda: self._logger_panel._open_folder(), 22, "iconAct"),
            ],
            "triggers": [
                _btn(tr("Reset"), tr("Clear hit counters"),
                     lambda: self._trigger_panel._clear_hits(), None, "iconAct"),
                _btn(tr("+ Add"), tr("New trigger"),
                     lambda: self._trigger_panel._open_editor()),
            ],
        }

    def _on_events_count(self, conn, n: int) -> None:
        """Show the active connection's event count on the Events stripe."""
        if conn is self._active_conn:
            base = tr("Events")
            self._stripe_bottom.set_badge(
                "events", f"{base} · {n}" if n else base)

    def open_tools(self) -> list[str]:
        """Keys of the currently open tools (one per edge at most)."""
        out = []
        for edge in ("left", "right", "bottom"):
            dock, _ = self._dock_for(edge)
            if dock.isVisible() and dock.current:
                out.append(dock.current)
        return out

    def restore_tools(self, keys: list[str]) -> None:
        """Re-open a persisted set of tools (ignores unknown keys)."""
        for key in keys:
            if key in getattr(self, "_tool_edge", {}):
                self._toggle_tool(key)

    def _build_devices_panel(self) -> QWidget:
        """Connection list — scales past the handful of tabs that fit up top."""
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.setSpacing(0)
        self._devices_list = QVBoxLayout()
        self._devices_list.setContentsMargins(0, 0, 0, 0)
        self._devices_list.setSpacing(0)
        lay.addLayout(self._devices_list)
        lay.addStretch()
        add = QPushButton(tr("+ Add connection"))
        add.setObjectName("iconAct")
        add.setFixedHeight(22)
        add.clicked.connect(self._add_connection)
        lay.addWidget(add)
        return panel

    def _refresh_devices_panel(self) -> None:
        """Rebuild the Devices list from the current connections."""
        if not hasattr(self, "_devices_list"):
            return
        while self._devices_list.count():
            item = self._devices_list.takeAt(0)
            w = item.widget()
            if w is not None:
                # setParent(None) first: deleteLater alone leaves the old row
                # painted at its last geometry until the event loop runs.
                w.setParent(None)
                w.deleteLater()
        for conn in self._connections:
            row = QPushButton(conn.name)
            row.setObjectName("deviceRow")
            row.setCheckable(True)
            row.setChecked(conn is self._active_conn)
            running = conn.reader.isRunning()
            active = conn is self._active_conn
            # Connected → the connection's tint; failed → red; idle → dim, except
            # the selected row, which keeps full contrast on its highlight.
            fg = self._conn_state_color(conn)
            if fg == C_DIM and active:
                fg = C_FG
            mark = ("⚡" if conn.flash_runner is not None
                    else "↻" if conn.reconnecting else "●" if running
                    else "✕" if conn.failed else "○")
            row.setText(f"{mark}  {conn.short_name}")
            row.setToolTip(conn.name)
            row.setStyleSheet(
                f"text-align:left;padding:4px 10px;border:none;"
                f"color:{fg.name()};")
            row.clicked.connect(
                lambda _=False, c=conn: self._conn_bar.setCurrentIndex(
                    self._conn_tab_index(c.id)))
            self._devices_list.addWidget(row)

    def _make_display_panels(self, conn) -> None:
        """Create a connection's own display panels (Parsing / Graphs /
        Indicators / Triggers / Events / Analytics / Logger) and add them to the
        shared stacks (SER3). All signals are wired with the owning connection
        captured, so each connection's data stays independent."""
        conn.parse = ParsePanel(conn.parser)
        conn.chart = ChartPanel()
        conn.chart.apply_theme(theme_colors(self._current_theme))
        conn.indicator = IndicatorPanel()
        conn.trigger = TriggerPanel(conn.engine)
        conn.events = TriggerEventsPanel()
        conn.analytics = AnalyticsPanel()
        conn.analytics.apply_theme(theme_colors(self._current_theme))
        conn.logger_panel = LoggerPanel(conn.logger)

        conn.parse.channel_chart_req.connect(
            lambda name, en, c=conn: self._on_channel_chart_req(c, name, en))
        conn.parse.channel_indicator_req.connect(
            lambda name, en, c=conn: self._on_channel_indicator_req(c, name, en))
        conn.chart.keep_requested.connect(conn.parse.keep_channel)
        conn.logger_panel.publish_toggled.connect(
            lambda on, c=conn: self._serial.set_publishing(c, on))
        conn.flash_panel = FlashPanel()
        self._flash_stack.addWidget(conn.flash_panel)
        self._flash.wire_panel(conn)
        self._transfer.wire_panel(conn)
        conn.modbus = ModbusPanel()
        self._modbus_stack.addWidget(conn.modbus)
        conn.modbus.changed.connect(lambda c=conn: self._serial.sync_modbus(c))
        conn.frames = FramePanel()
        self._frames_stack.addWidget(conn.frames)
        conn.frames.changed.connect(lambda c=conn: self._serial.sync_frames(c))
        conn.events.jump_to_line.connect(self._search.jump_to_line)
        conn.events.set_embedded(True)       # dock header already titles it
        conn.events.count_changed.connect(
            lambda n, c=conn: self._on_events_count(c, n))
        conn.trigger.trigger_changed.connect(
            lambda c=conn: self._triggers.sync_analytics(c))
        # Per-connection triggers "fire always" — each engine's matches route to
        # this connection (events/notifications regardless of which tab is active).
        conn.engine.on_match(
            lambda trig, line, ts, c=conn: self._triggers.on_match_threadsafe(c, trig, line, ts))
        self._triggers.wire_snapshots(conn)

        self._parse_stack.addWidget(conn.parse)
        self._chart_stack.addWidget(conn.chart)
        self._indicator_stack.addWidget(conn.indicator)
        self._trigger_stack.addWidget(conn.trigger)
        self._logger_stack.addWidget(conn.logger_panel)
        self._events_stack.addWidget(conn.events)
        self._analytics_stack.addWidget(conn.analytics)

    def _build_custom_cmd(self) -> QWidget:
        from PyQt6.QtWidgets import QTextEdit as QTE
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)
        self._custom_cmd = QTE()
        self._custom_cmd.setObjectName("customCmd")
        self._custom_cmd.setFixedHeight(64)
        self._custom_cmd.setFont(QFont("JetBrains Mono", 11))
        self._custom_cmd.setPlainText("AT+SAMPLE=100")
        lay.addWidget(self._custom_cmd)
        sb = QPushButton(tr("Send custom"))
        sb.setObjectName("add")
        sb.setFixedHeight(26)
        sb.clicked.connect(self._serial.send_custom)
        lay.addWidget(sb)

        # Repeat it on a timer (CMP8), per connection.
        row = QHBoxLayout()
        row.setSpacing(5)
        self._repeat_ms = NumberBox()
        self._repeat_ms.setDecimals(0)
        self._repeat_ms.setRange(PeriodicSender.MIN_INTERVAL_MS, 86_400_000)
        self._repeat_ms.setValue(1000)
        self._repeat_ms.setFixedSize(64, 24)
        self._repeat_count = NumberBox()
        self._repeat_count.setDecimals(0)
        self._repeat_count.setRange(0, 1_000_000)
        self._repeat_count.setSpecialValueText("∞")
        self._repeat_count.setFixedSize(52, 24)
        self._repeat_count.setToolTip(tr("How many times to send it. 0 (∞) repeats until stopped."))
        row.addWidget(self._lbl(tr("Every"), dim=True))
        row.addWidget(self._repeat_ms)
        row.addWidget(self._lbl(tr("ms"), dim=True))
        row.addSpacing(6)
        row.addWidget(self._lbl(tr("Times"), dim=True))
        row.addWidget(self._repeat_count)
        row.addStretch()
        lay.addSpacing(4)
        lay.addLayout(row)
        self._repeat_btn = QPushButton(tr("Repeat"))
        self._repeat_btn.setObjectName("add")
        self._repeat_btn.setFixedHeight(26)
        self._repeat_btn.setToolTip(tr("Send the command above on a timer, on this device only."))
        self._repeat_btn.clicked.connect(self._serial.toggle_repeat)
        lay.addWidget(self._repeat_btn)
        self._repeat_status = QLabel("")
        self._repeat_status.setObjectName("dimLabel")
        lay.addWidget(self._repeat_status)
        lay.addStretch()   # keep the form at the top of a tall dock
        return w

    def _on_frame_changed(self) -> None:
        """A hex frame is binary: it takes no line ending, and is typed differently."""
        hex_input = self._frame_btn.hex_input
        self._eol_combo.setEnabled(not hex_input)
        self._cmd_edit.setPlaceholderText(
            tr("Hex bytes, e.g. 01 03 00 00 00 02") if hex_input
            else tr("Command… (↑↓ history)"))

    def _refresh_activity(self) -> None:
        """The status bar's note of what the active connection is doing by itself:
        sending on a timer, or open to TCP clients."""
        conn = self._active_conn
        parts = []
        if conn.repeat is not None:
            parts.append(tr("Repeating every {ms} ms").format(ms=conn.repeat.interval_ms))
        share = conn.port_share
        if share is not None:
            parts.append(tr("TCP :{port} ({n} connected)").format(
                port=share.port, n=share.client_count))
        if conn.capture is not None:
            parts.append(tr("Capturing {name}: {size}").format(
                name=conn.capture.path.name, size=self._fmt_bytes(conn.capture.size)))
        self._sb_activity.setText("  ·  ".join(parts))
        self._sb_activity.setVisible(bool(parts))

    # ── Command palette ───────────────────────────────────────────────────────

    def _open_palette(self) -> None:
        from ui.command_palette import CommandPalette
        CommandPalette(self, theme_colors(self._current_theme)).open_with(
            self._palette_commands())

    def _palette_commands(self) -> list:
        """Everything the palette can run, gathered fresh on each open."""
        from ui.command_palette import Command, menu_commands

        def on_off(chk) -> str:
            return tr("on → off") if chk.isChecked() else tr("off → on")

        running = self._reader.isRunning()
        cmds = [
            Command(tr("Disconnect") if running else tr("Connect"), tr("Connection"),
                    self._serial.toggle_connection),
            Command(tr("New connection"), tr("Connection"), self._add_connection),
            Command(tr("Rescan serial ports"), tr("Connection"), self._serial.refresh_ports),
            Command(tr("Scan for Bluetooth devices"), tr("Connection"), self._scan_ble_command),
            Command(tr("Reset board (RTS → EN, ESP32/ESP8266)"), tr("Device"),
                    lambda: self._serial.reset_board("esp")),
            Command(tr("Reset board (DTR pulse, Arduino)"), tr("Device"),
                    lambda: self._serial.reset_board("dtr")),
            Command(tr("Enter ESP bootloader"), tr("Device"),
                    lambda: self._serial.reset_board("esp_boot")),
            Command(tr("Send BREAK"), tr("Device"), self._serial.send_break),
            Command(tr("Share port over TCP…"), tr("Device"), self._tcp_share.open_dialog),
            Command(tr("Set up a Raspberry Pi…"), tr("Device"),
                    lambda: self._agents.setup_dialog()),
            Command(tr("Remote agents…"), tr("Device"), self._agents.open_dialog),
            Command(tr("Pair with agent…"), tr("Device"), lambda: self._agents.pair_dialog()),
            Command(tr("Capture raw bytes to file (start / stop)…"), tr("Device"),
                    self._capture.toggle),
            Command(tr("Repeat custom command (start / stop)"), tr("Device"),
                    self._serial.toggle_repeat),
            Command(f"DTR  ({on_off(self._chk_dtr)})", tr("Device"), self._chk_dtr.toggle),
            Command(f"RTS  ({on_off(self._chk_rts)})", tr("Device"), self._chk_rts.toggle),
            Command(f"{tr('Auto-reconnect')}  ({on_off(self._chk_reconnect)})", tr("Connection"),
                    self._chk_reconnect.toggle),
            Command(tr("Clear terminal"), tr("Terminal"),
                    lambda: (self._active_conn.term_pending.clear(), self._terminal.clear())),
            Command(f"{tr('HEX view')}  ({on_off(self._chk_hex)})", tr("Terminal"),
                    self._chk_hex.toggle),
            Command(f"{tr('Timestamps')}  ({on_off(self._chk_ts)})", tr("Terminal"),
                    self._chk_ts.toggle),
            Command(f"{tr('Autoscroll')}  ({on_off(self._chk_auto)})", tr("Terminal"),
                    self._chk_auto.toggle),
            Command(f"{tr('Echo sent commands')}  ({on_off(self._chk_echo)})", tr("Terminal"),
                    self._chk_echo.toggle),
            Command(tr("Increase font size"), tr("Terminal"), lambda: self._change_font_size(+1)),
            Command(tr("Decrease font size"), tr("Terminal"), lambda: self._change_font_size(-1)),
        ]
        if len(self._connections) > 1:
            cmds.append(Command(tr("Close this connection"), tr("Connection"),
                                lambda: self._close_conn_by_id(self._active_conn.id)))
        for conn in self._connections:
            if conn is not self._active_conn:
                cmds.append(Command(conn.short_name, tr("Switch to"),
                                    lambda c=conn: self._conn_bar.setCurrentIndex(
                                        self._conn_tab_index(c.id))))
        for label, run in self._control_panel.run_commands():
            cmds.append(Command(label, tr("Controls"), run))
        for key, _edge, title in self.TOOLS:
            cmds.append(Command(tr(title), tr("Show tool window"),
                                lambda k=key: self._show_tool(k)))
        cmds += menu_commands(self.menuBar())
        return cmds

    def _scan_ble_command(self) -> None:
        """From the palette: the list being filled has to be on show."""
        self._transport_combo.setCurrentText("BLE")
        self._serial.scan_ble()

    def _show_tool(self, key: str) -> None:
        """Open a tool window (never closes it, unlike a stripe click)."""
        dock, _ = self._dock_for(self._tool_edge[key])
        if not (dock.isVisible() and dock.current == key):
            self._toggle_tool(key)

    def _show_about(self):
        ver = QApplication.instance().applicationVersion()
        QMessageBox.about(
            self,
            tr("About IsoDAQ Studio"),
            f"<b>IsoDAQ Studio</b> v{ver}<br><br>"
            + tr("Serial data acquisition and analysis tool.") + "<br><br>"
            '<a href="https://github.com/AlexShateljuk/isodaq">github.com/AlexShateljuk/isodaq</a>',
        )

    # ── Statusbar ─────────────────────────────────────────────────────────────

    def _build_statusbar(self):
        """Status bar: connection state · RX bytes · TX bytes · rate · errors · session timer."""
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._sb_rx   = QLabel("RX: 0 B")
        self._sb_tx   = QLabel("TX: 0 B")
        self._sb_rate = QLabel(tr("Rate: —"))
        # Room for "Rate: 99,999 lines/s" so the label doesn't clip at high rates.
        self._sb_rate.setMinimumWidth(self._sb_rate.fontMetrics().horizontalAdvance(
            tr("Rate: {n} lines/s").format(n="99,999")) + 20)
        self._sb_err  = QLabel(tr("Errors: 0"))
        self._sb_sess = QLabel(tr("Session: {t}").format(t="00:00:00"))
        self._sb_conn = QLabel("● " + tr("Disconnected"))
        self._sb_conn.setStyleSheet("color:#ef4444")
        for w in (self._sb_conn, self._sb_rx, self._sb_tx, self._sb_rate, self._sb_err):
            sb.addWidget(w)
        self._sb_activity = QLabel("")
        self._sb_activity.hide()
        sb.addWidget(self._sb_activity)

        # Remote session quality indicator — hidden until JOIN is active
        self._sb_ping = QLabel("● — ms")
        self._sb_ping.setToolTip(tr("Remote session latency (round-trip ping)"))
        self._sb_ping.hide()
        sb.addPermanentWidget(self._sb_ping)
        sb.addPermanentWidget(self._sb_sess)

    # ═════════════════════════════════════════════════════════════════════════
    # Signal wiring
    # ═════════════════════════════════════════════════════════════════════════

    def _connect_signals(self):
        """Wires Qt signals from SerialReader, TriggerEngine and MacroRunner to GUI slots."""
        self._chk_auto.stateChanged.connect(self._on_autoscroll_toggled)
        self._chk_hex.toggled.connect(lambda on: self._log(
            "SYS", tr("HEX view on — new data is shown as a hex dump.") if on
            else tr("HEX view off."), C_SYS))
        # Terminal scroll tracking is wired per-terminal in _add_terminal (SER3).
        # The active connection's reader (line_received + state) is wired here,
        # capturing its owning connection so RX routes correctly (SER3).
        self._serial.wire()

        # Trigger matches (per-connection engine.on_match), trigger_changed →
        # analytics, parse → chart/indicator, and events → jump are all wired
        # per connection in _make_display_panels (SER3).

        # Macro runner → terminal log
        self._macro_runner.step_started.connect(
            lambda _, cmd: self._log("MCR", cmd, C_TX))
        self._macro_runner.step_waiting.connect(
            lambda _, pat: self._log("MCR", f"waiting for: {pat}", C_DIM))
        self._macro_runner.finished.connect(
            lambda: self._log("MCR", "Macro finished.", C_OK))
        self._macro_runner.aborted.connect(
            lambda msg: self._log("MCR", f"Macro aborted: {msg}", C_ERR))

    # ═════════════════════════════════════════════════════════════════════════
    # Slots
    # ═════════════════════════════════════════════════════════════════════════

    @pyqtSlot(str, str)
    def _on_line_received(self, line: str, ts: str):
        """Back-compat entry (tools / a single connection): route to the active
        connection's pipeline."""
        self._on_conn_line(self._active_conn, line, ts)

    #: GUI time spent on received lines per drain tick; the rest of the tick is
    #: left for painting/input, so the window never freezes under load.
    RX_BUDGET_S = 0.012
    #: Lines queued longer than this make a connection "overloaded": they are
    #: still logged and trigger-checked, but not drawn (terminal/charts), so the
    #: screen stays close to live instead of drifting seconds behind.
    RX_MAX_LAG_S = 1.0

    def _on_conn_lines(self, conn, batch) -> None:
        """A reader's batch of ``(line, timestamp, arrival)`` (one per read).
        Only queued here; :meth:`_drain_rx` processes it on a time budget."""
        conn.rx_backlog.extend(batch)

    def _drain_rx(self) -> None:
        """Process queued RX lines for every connection, round-robin, within
        :attr:`RX_BUDGET_S`. Whatever does not fit waits for the next tick —
        a burst (or a stall such as a theme switch) can no longer turn into a
        multi-second freeze, because painting happens between ticks."""
        import time as _t
        deadline = _t.perf_counter() + self.RX_BUDGET_S
        busy = [c for c in self._connections if c.rx_backlog]
        for conn in busy:
            self._shed_overload(conn)
        while busy and _t.perf_counter() < deadline:
            for conn in list(busy):
                q = conn.rx_backlog
                for _ in range(min(200, len(q))):
                    line, ts, _arrival, raw = q.popleft()
                    self._on_conn_line(conn, line, ts, raw)
                if not q:
                    busy.remove(conn)
                    if conn.rx_shed:
                        self._log("SYS", f"Display caught up — {conn.rx_shed:,} lines were "
                                  "logged and trigger-checked but not drawn (overload).",
                                  C_SYS, conn=conn)
                        conn.rx_shed = 0
        self._sb_rx.setText(f"RX: {self._fmt_bytes(self._active_conn.rx_bytes)}")

    def _shed_overload(self, conn) -> None:
        """Keep the data, drop the drawing: lines queued longer than
        :attr:`RX_MAX_LAG_S` are written to the logger and checked by triggers
        (cheap), skipping the terminal / parser / charts (expensive)."""
        import time as _t
        q = conn.rx_backlog
        cutoff = _t.monotonic() - self.RX_MAX_LAG_S
        while q and q[0][2] < cutoff:
            line, ts, _arrival, raw = q.popleft()
            conn.hex_offset += len(raw)   # keep HEX offsets true to the stream
            conn.rx_bytes += len(raw)
            conn.rx_lines += 1
            conn.logger.write_line(line, ts)
            conn.snapshots.feed(line, ts)
            conn.engine.check(line, ts)
            conn.rx_shed += 1

    def _on_conn_line(self, conn, line: str, ts: str, raw: bytes | None = None):
        """RX pipeline for a specific source connection (GUI thread, queued).

        Data (log, terminal, line-identity, parse) always runs against *conn*.
        The shared display panels, macro runner and session sharing update only
        when *conn* is the active one. Per-connection triggers ("fire always")
        land in a later step — for now the active engine runs."""
        is_active = conn is self._active_conn

        # What came over the wire, which for a binary frame isn't what is shown.
        conn.rx_bytes += len(raw) if raw is not None else len(line.encode())
        conn.rx_lines += 1

        # Log (non-blocking — goes to queue)
        conn.logger.write_line(line, ts)

        # Display into conn's terminal first — apply log-level color if active.
        # Logging before the trigger check gives this line a stable block id
        # that a trigger event can later jump back to (F2).
        log_color = match_log_color(line, self._log_colorizer_enabled)
        self._log("RX", line, C_RX, ts, text_color=log_color, conn=conn, raw=raw)
        conn.current_rx_line_id = conn.last_rx_line_id

        # Check triggers on the source connection's own engine — "fire always"
        # (events + notifications happen regardless of which tab is active).
        conn.snapshots.feed(line, ts)
        conn.engine.check(line, ts)

        # Parse numeric channels → update this connection's own panels (they are
        # per-connection now, so background connections keep accumulating too).
        parsed = conn.parser.parse(line)
        if parsed:
            conn.last_parsed.update(parsed)
            conn.parse.update_values(parsed)
            conn.chart.update(parsed)
            conn.indicator.update(parsed)
            if conn.publisher is not None:
                conn.publisher.publish_values(parsed)

        # Quick Plot (CMP5/CMP6): data-only lines plot without any setup.
        # Names a Parsing channel already covers follow that channel instead.
        if conn.chart.quick_enabled:
            quick = quick_parse(line)
            if quick:
                if parsed or conn.parser.get_channels():
                    names = conn.parser.channel_names()
                    quick = {k: v for k, v in quick.items() if k not in names}
                if quick:
                    conn.chart.quick_update(quick)
                    if conn.publisher is not None:
                        conn.publisher.publish_values(quick)

        if is_active:
            # Feed macro runner (for wait-for pattern matching)
            self._macro_runner.feed_rx_line(line)
            # Broadcast to any connected session viewers (no-op if not sharing)
            self._session.feed_line(line, "rx")

    @staticmethod
    def _repolish(widget) -> None:
        """Force Qt to re-evaluate the stylesheet after an objectName change."""
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    # ── Parse panel → display panel slots ────────────────────────────────────

    def _float_current_tab(self) -> None:
        """Detach the focused dock's current tool into its own window (F4)."""
        for edge in ("right", "bottom", "left"):
            dock, _ = self._dock_for(edge)
            if dock.isVisible() and dock.current:
                win = dock.float_current()
                if win is None:
                    return
                win.setStyleSheet(QApplication.instance().styleSheet())
                tint_titlebar(win)
                win.destroyed.connect(
                    lambda _=None, w=win: self._float_wins.remove(w)
                    if w in self._float_wins else None)
                self._float_wins.append(win)
                win.show()
                self._close_dock(edge)
                return

    def _on_channel_chart_req(self, conn, name: str, enable: bool) -> None:
        if enable:
            conn.chart.add_channel(name)
            conn.events.register_channel(name)
        else:
            conn.chart.remove_channel(name)
            conn.events.unregister_channel(name)

    def _on_channel_indicator_req(self, conn, name: str, enable: bool) -> None:
        if enable:
            conn.indicator.add_indicator(name)
        else:
            conn.indicator.remove_indicator(name)

    # ── Trigger save/load ─────────────────────────────────────────────────────

    def _apply_theme(self, theme: str) -> None:
        """Switch the application colour theme and refresh all colours."""
        self._current_theme = theme
        c = theme_colors(theme)
        global C_RX, C_TX, C_SYS, C_OK, C_ERR, C_DIM, C_FG
        C_RX  = QColor(c["accent"])
        C_TX  = QColor(c["tx"])
        C_SYS = QColor(c["warn"])
        C_OK  = QColor(c["ok"])
        C_ERR = QColor(c["err"])
        C_DIM = QColor(c["fg_dim"])
        C_FG  = QColor(c["fg"])
        # Set on QApplication so all open dialogs inherit it automatically
        QApplication.instance().setStyleSheet(build_stylesheet(theme))
        # Repaint the stripes/docks — stale paint otherwise lingers on macOS.
        for w in (self._stripe_left, self._stripe_right, self._stripe_bottom,
                  self._dock_left, self._dock_right, self._dock_bottom,
                  self._conn_bar):
            self._repolish(w)
            w.update()
        for conn in self._connections:
            if getattr(conn, "chart", None) is not None:
                conn.chart.apply_theme(c)
            if getattr(conn, "analytics", None) is not None:
                conn.analytics.apply_theme(c)
        set_current_theme(theme)
        tint_titlebar(self)
        for win in self._float_wins:
            tint_titlebar(win)
        self._log("SYS", f"Theme: {theme}", C_SYS)

    def _on_autoscroll_toggled(self, state: int) -> None:
        if state:
            sb = self._terminal.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _apply_right_panel(self) -> None:
        """Sync the data-side tool windows with the toggle + mode.

        Simple mode hides every dock and stripe, leaving just the terminal;
        Advanced restores the right dock when the toggle is on."""
        advanced = self._mode == "advanced"
        for stripe in (self._stripe_left, self._stripe_right, self._stripe_bottom):
            stripe.setVisible(advanced)
        if not advanced:
            for edge in ("left", "right", "bottom"):
                self._dock_for(edge)[0].hide()
        elif self._right_panel_visible:
            if not self._dock_right.isVisible():
                self._toggle_tool(self._dock_right.current or "graphs")
        else:
            self._close_dock("right")
        self._panel_toggle_btn.setText("⊞" if self._right_panel_visible else "⊟")

    def _toggle_right_panel(self) -> None:
        """Show or hide the data side (⊞ button / Ctrl+Shift+R)."""
        self._right_panel_visible = not self._right_panel_visible
        self._apply_right_panel()

    def _toggle_mode(self) -> None:
        self._set_mode("simple" if self._mode == "advanced" else "advanced")

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        simple = mode == "simple"
        self._panel_toggle_btn.setVisible(not simple)
        self._mode_action.setChecked(simple)
        self._apply_right_panel()

    def _change_font_size(self, delta: int) -> None:
        """
        Increases or decreases the terminal font size (range 8–24 pt).
        Applies to all existing text immediately via document().setDefaultFont().
        """
        self._terminal_font_size = max(8, min(24, self._terminal_font_size + delta))
        self._apply_terminal_font()
        self._font_size_lbl.setText(str(self._terminal_font_size))

    def _apply_terminal_font(self) -> None:
        """Apply the current font size to every connection's terminal."""
        font = QFont("JetBrains Mono", self._terminal_font_size)
        for term in self._terminals.values():
            term.setFont(font)
            term.document().setDefaultFont(font)

    def _open_log_colorizer(self):
        dlg = LogColorizerDialog(self._log_colorizer_enabled, self)
        tint_titlebar(dlg)
        if dlg.exec() == LogColorizerDialog.DialogCode.Accepted:
            self._log_colorizer_enabled = dlg.result_enabled()
            if self._log_colorizer_enabled:
                names = ", ".join(sorted(self._log_colorizer_enabled))
                self._log("SYS", f"Log colorizer active: {names}", C_SYS)
            else:
                self._log("SYS", "Log colorizer disabled.", C_DIM)

    # ═════════════════════════════════════════════════════════════════════════
    # Terminal helpers
    # ═════════════════════════════════════════════════════════════════════════

    def _log(self, direction: str, text: str, color: QColor, ts: str | None = None,
             text_color: QColor | None = None, conn=None, raw: bytes | None = None):
        """Queue one terminal line for *conn* (default: the active connection).

        Lines are written in batches by :meth:`_flush_terminals` (~30 fps)
        instead of one QTextDocument edit per line — per-line edits capped the
        whole app at a few hundred lines/s and froze the window at high baud.
        The line's identity (F2 jump target) is assigned here, immediately, so
        trigger events can reference it before it is painted."""
        conn = conn or self._active_conn
        if not ts and self._chk_ts.isChecked():
            from datetime import datetime
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        conn.line_seq += 1
        if direction == "RX":
            conn.last_rx_line_id = conn.line_seq
        offset = -1
        if raw is not None:   # remember the bytes' stream offset (HEX view)
            if direction == "TX":
                offset = conn.tx_hex_offset
                conn.tx_hex_offset += len(raw)
            else:
                offset = conn.hex_offset
                conn.hex_offset += len(raw)
            # Decide the view on arrival, not at paint time — toggling HEX must
            # not re-format lines still waiting for the next batched paint.
            if not self._chk_hex.isChecked():
                raw = None
        conn.term_pending.append(
            ("line", conn.line_seq, ts if self._chk_ts.isChecked() else "",
             direction, text, color, text_color, raw, offset))

    @staticmethod
    def _insert_hex(cursor, raw: bytes, offset: int, indent: str,
                    dim_fmt, hex_fmt, ascii_fmt) -> None:
        """Classic hex dump of one received chunk: ``offset  16 bytes  |ascii|``.
        The first row follows the timestamp/direction prefix; continuation rows
        are indented to line up under it."""
        for row in range(0, len(raw), 16):
            part = raw[row:row + 16]
            if row:
                cursor.insertText("\n" + indent, dim_fmt)
            hx = part[:8].hex(" ").upper()
            if len(part) > 8:
                hx += "  " + part[8:].hex(" ").upper()
            cursor.insertText(f"{offset + row:08X}  ", dim_fmt)
            cursor.insertText(hx.ljust(49), hex_fmt)
            cursor.insertText(" |", dim_fmt)
            cursor.insertText(part.translate(_HEX_ASCII).decode("ascii"), ascii_fmt)
            cursor.insertText("|", dim_fmt)

    def _log_banner(self, conn, text: str, fg: QColor, bg: QColor) -> None:
        """Queue a highlighted banner (trigger flash) in order with the lines."""
        conn.term_pending.append(("banner", text, fg, bg))

    def _flush_terminals(self) -> None:
        for conn in self._connections:
            if conn.term_pending:
                self._flush_terminal(conn)

    def _flush_terminal(self, conn) -> None:
        """Write *conn*'s queued lines into its terminal as one edit block."""
        pending, conn.term_pending = conn.term_pending, []
        # Under overload, lines beyond the scrollback would be trimmed right
        # after being laid out — skip them instead (they are still logged,
        # parsed and trigger-checked; only the on-screen copy is dropped).
        if len(pending) > self._scrollback_limit:
            pending = pending[-self._scrollback_limit:]
        term = self._terminals[conn.id]

        dim_fmt = QTextCharFormat(); dim_fmt.setForeground(C_DIM)
        fg_fmt = QTextCharFormat(); fg_fmt.setForeground(C_FG)
        fmt_cache: dict[tuple, QTextCharFormat] = {}

        def fmt(color: QColor, bold: bool = False, bg: QColor | None = None):
            key = (color.rgba(), bold, bg.rgba() if bg is not None else None)
            f = fmt_cache.get(key)
            if f is None:
                f = fmt_cache[key] = QTextCharFormat()
                f.setForeground(color)
                if bold:
                    f.setFontWeight(700)
                if bg is not None:
                    f.setBackground(bg)
            return f

        cursor = QTextCursor(term.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.beginEditBlock()
        for item in pending:
            if item[0] == "banner":
                _, text, fg, bg = item
                cursor.insertText(f"\n{text}\n", fmt(fg, bg=bg))
                continue
            _, seq, ts, direction, text, color, text_color, raw, offset = item
            if ts:
                cursor.insertText(f"{ts}  ", dim_fmt)
            cursor.insertText(f"{direction:<3}  ", fmt(color, bold=True))
            if raw:   # sent or received while the HEX view was on
                self._insert_hex(cursor, raw, offset, " " * (len(ts) + 7 if ts else 5),
                                 dim_fmt, fg_fmt, fmt(color))
                cursor.block().setUserState(seq)
                cursor.insertText("\n", fg_fmt)
                continue
            if text_color is not None:
                txt_fmt = fmt(text_color)
            else:
                txt_fmt = fg_fmt if direction in ("RX", "SYS", "BLD") else fmt(color)
            cursor.insertText(text, txt_fmt)
            # Tag this line's block with its stable id so events can jump to it (F2)
            cursor.block().setUserState(seq)
            cursor.insertText("\n", txt_fmt)
        cursor.endEditBlock()

        # Limit scrollback — before the scroll-to-bottom so the trim doesn't
        # shift the viewport afterwards.
        doc = term.document()
        excess = doc.blockCount() - self._scrollback_limit
        if excess > 0:
            cur = QTextCursor(doc)
            cur.movePosition(QTextCursor.MoveOperation.Start)
            cur.movePosition(QTextCursor.MoveOperation.Down, QTextCursor.MoveMode.KeepAnchor,
                             excess + self._scrollback_limit // 10)
            cur.removeSelectedText()

        # Autoscroll only the visible (active) terminal.
        if conn is self._active_conn and self._chk_auto.isChecked() and not self._user_scrolling:
            sb = term.verticalScrollBar()
            sb.setValue(sb.maximum())

    # ═════════════════════════════════════════════════════════════════════════
    # Timers
    # ═════════════════════════════════════════════════════════════════════════

    def _start_timers(self):
        """
        Starts background QTimers:
          • 1 s  — session clock tick (_tick_session)
          • 2 s  — trigger hit-count refresh (TriggerPanel.refresh_hits)
        """
        t = QTimer(self)
        t.setInterval(1000)
        t.timeout.connect(self._tick_session)
        t.start()

        # Received-line drain (see _drain_rx): budgeted per tick.
        self._rx_timer = QTimer(self)
        self._rx_timer.setInterval(15)
        self._rx_timer.timeout.connect(self._drain_rx)
        self._rx_timer.start()

        # Terminal batch writer (see _log): ~30 fps.
        self._term_timer = QTimer(self)
        self._term_timer.setInterval(33)
        self._term_timer.timeout.connect(self._flush_terminals)
        self._term_timer.start()

        t2 = QTimer(self)
        t2.setInterval(2000)
        t2.timeout.connect(self._trigger_panel.refresh_hits)
        t2.start()

    def _tick_session(self):
        """Once a second: advance each connected device's session clock and
        line rate, then show the active one's in the status bar."""
        for conn in self._connections:
            mark = getattr(conn, "_rate_mark", 0)
            conn.rate, conn._rate_mark = conn.rx_lines - mark, conn.rx_lines
            if conn.reader.isRunning():
                conn.session_sec += 1
        self._refresh_live_stats()

    # ═════════════════════════════════════════════════════════════════════════
    # Utilities
    # ═════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _fmt_bytes(n: int) -> str:
        for unit in ("B","KB","MB","GB"):
            if n < 1024:
                return f"{n:.0f} {unit}"
            n //= 1024
        return f"{n} TB"

    @staticmethod
    def _lbl(text: str, mono: bool = False, dim: bool = False) -> QLabel:
        lbl = QLabel(text)
        if dim and mono:
            lbl.setObjectName("dimLabelMono")
        elif dim:
            lbl.setObjectName("dimLabel")
        elif mono:
            lbl.setStyleSheet(
                "font-family:'JetBrains Mono';font-size:9px;"
                "text-transform:uppercase;letter-spacing:1px;")
        return lbl

    # ═════════════════════════════════════════════════════════════════════════
    # Settings persistence
    # ═════════════════════════════════════════════════════════════════════════

    _CONFIG_PATH = Path.home() / ".isodaq_studio" / "config.json"

    # ═════════════════════════════════════════════════════════════════════════
    # Close
    # ═════════════════════════════════════════════════════════════════════════

    def closeEvent(self, event):
        # Stop *every* connection's readers and loggers — with several devices
        # open, leaving a background reader running aborts the process on exit
        # ("QThread: Destroyed while thread is still running").
        self._settings.save()
        self._serial.shutdown_scan()
        for conn in self._connections:
            self._flash.shutdown(conn)
            self._transfer.shutdown(conn)
            self._serial.set_publishing(conn, False)
            self._serial.stop_repeat(conn)
            self._tcp_share.stop(conn)
            self._capture.stop(conn)
            conn.snapshots.flush()
            conn.reconnecting = False
            for r in conn.readers.values():
                if r.isRunning():
                    r.disconnect_port()
                    r.wait(2000)
            conn.logger.shutdown()
        event.accept()
