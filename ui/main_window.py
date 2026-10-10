"""
ui/main_window.py — Main application window

Layout:
  Titlebar
  Menubar
  ┌──────────────────────┬────────────────────────────────┐
  │   LEFT (46%)         │   RIGHT (54%)                  │
  │                      │   ┌──────────────┬──────────┐  │
  │  Port settings       │   │ Tabs+Charts  │ Sidebar  │  │
  │  Terminal output     │   │              │ Parsing  │  │
  │  Parser strip        │   │              │ Logger   │  │
  │  Command input       │   │              │ Triggers │  │
  └──────────────────────┴───┴──────────────┴──────────┘
  Statusbar
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QTimer, Qt, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor, QAction
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
    QScrollArea,
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
from ui.themes import build_stylesheet, theme_colors, THEME_NAMES, key_from_display, set_current_theme, tint_titlebar
from ui.controllers.session_controller import SessionController
from ui.controllers.update_manager import UpdateManager
from ui.controllers.dev_tools import DevTools
from ui.controllers.search_controller import SearchController
from ui.controllers.settings_manager import SettingsManager
from ui.controllers.serial_controller import SerialController
from ui.controllers.trigger_controller import TriggerController

# ── Colours (updated when theme changes) ──────────────────────────────────────
C_RX  = QColor("#3ecf8e")
C_TX  = QColor("#ff7b54")
C_SYS = QColor("#f59e0b")
C_OK  = QColor("#22c55e")
C_ERR = QColor("#ef4444")
C_DIM = QColor("#3e4460")
C_FG  = QColor("#d4d4d4")


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
        self._port_options_menu = None        # built lazily by _open_port_options
        self._float_wins: list[_FloatWindow] = []
        self._terminal_font_size: int = 11
        self._scrollback_limit: int = 5000
        self._current_theme: str = "vscode"
        self._session_sec = 0
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
        QApplication.instance().setStyleSheet(build_stylesheet(self._current_theme))
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

        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._main_splitter.setHandleWidth(3)
        root.addWidget(self._main_splitter)

        self._main_splitter.addWidget(self._build_left())
        self._right_widget = self._build_right()
        self._main_splitter.addWidget(self._right_widget)
        self._main_splitter.setSizes([702, 598])

        self._build_statusbar()

        # Right panel starts collapsed by default (Advanced mode); a persisted
        # preference is restored later in SettingsManager.load().
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

        view_m = mb.addMenu(tr("View"))
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
        self._conn_bar.setElideMode(Qt.TextElideMode.ElideRight)
        self._conn_bar.currentChanged.connect(self._on_conn_tab_changed)
        lay.addWidget(self._conn_bar, 1)

        add_btn = QPushButton("+")
        add_btn.setObjectName("addConnBtn")
        add_btn.setFixedSize(26, 26)
        add_btn.setToolTip(tr("Add a connection"))
        add_btn.clicked.connect(self._add_connection)
        lay.addWidget(add_btn)

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
        running = conn.reader.isRunning()
        self._conn_bar.setTabText(idx, conn.name)
        self._conn_bar.setTabTextColor(idx, conn.color if running else C_DIM)

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
                             (self._events_stack, conn.events),
                             (self._analytics_stack, conn.analytics)):
            stack.removeWidget(panel)
            panel.deleteLater()
        if conn in self._connections:
            self._connections.remove(conn)
        conn.deleteLater()

    def _set_active_connection(self, conn) -> None:
        if conn is self._active_conn:
            return
        self._active_conn = conn
        self._terminal_stack.setCurrentWidget(self._terminals[conn.id])
        # Swap the per-connection right-side panels to this connection's.
        self._chart_stack.setCurrentWidget(conn.chart)
        self._indicator_stack.setCurrentWidget(conn.indicator)
        self._parse_stack.setCurrentWidget(conn.parse)
        self._trigger_stack.setCurrentWidget(conn.trigger)
        self._logger_stack.setCurrentWidget(conn.logger_panel)
        self._events_stack.setCurrentWidget(conn.events)
        self._analytics_stack.setCurrentWidget(conn.analytics)
        self._refresh_conn_state()

    def _refresh_conn_state(self) -> None:
        """Reflect the active connection's reader state on the Connect button +
        status bar (called when switching tabs)."""
        conn = self._active_conn
        running = conn.reader.isRunning()
        self._conn_btn.setText(tr("Disconnect") if running else tr("Connect"))
        self._conn_btn.setObjectName("disconnectBtn" if running else "connectBtn")
        self._repolish(self._conn_btn)
        if running:
            self._sb_conn.setText(f"● {conn.name}")
            self._sb_conn.setStyleSheet(f"color:{C_OK.name()}")
        else:
            self._sb_conn.setText("● " + tr("Disconnected"))
            self._sb_conn.setStyleSheet(f"color:{C_ERR.name()}")
        self._sb_rx.setText(f"RX: {self._fmt_bytes(conn.rx_bytes)}")
        self._sb_tx.setText(f"TX: {self._fmt_bytes(conn.tx_bytes)}")

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
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(5)

        self._transport_combo = QComboBox()
        self._transport_combo.addItems(["Serial", "Network", "SSH"])
        self._transport_combo.setFixedWidth(94)
        self._transport_combo.setToolTip(
            tr("Transport: local serial, TCP/RFC2217 network, or SSH"))
        lay.addWidget(self._transport_combo)

        self._port_combo = QComboBox()
        self._port_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._port_combo.setMinimumWidth(170)
        self._port_combo.activated.connect(self._on_port_activated)
        self._port_combo.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._port_combo.customContextMenuRequested.connect(self._on_port_context_menu)
        lay.addWidget(self._port_combo, 1)

        self._refresh_btn = QPushButton("⟳")
        self._refresh_btn.setFixedWidth(34)
        self._refresh_btn.setToolTip(tr("Rescan serial ports"))
        self._refresh_btn.clicked.connect(self._on_refresh_clicked)
        lay.addWidget(self._refresh_btn)

        self._baud_combo = QComboBox()
        self._baud_combo.addItems(["9600", "19200", "38400", "57600",
                                   "115200", "230400", "460800", "921600"])
        self._baud_combo.setCurrentText("115200")
        self._baud_combo.setFixedWidth(104)
        self._baud_combo.setToolTip(tr("Baud rate"))
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

        grid.addWidget(self._hline(), 2, 0, 1, 2)

        grid.addWidget(self._chk_ts,   3, 0)
        grid.addWidget(self._chk_hex,  3, 1)
        grid.addWidget(self._chk_auto, 4, 0)
        grid.addWidget(self._chk_echo, 4, 1)

        grid.addWidget(self._hline(), 5, 0, 1, 2)

        font_row = QHBoxLayout()
        font_row.setSpacing(4)
        font_row.addWidget(self._lbl(tr("Font"), dim=True))
        font_dec = QPushButton("−")
        font_dec.setFixedSize(22, 22)
        font_dec.setToolTip(tr("Decrease terminal font size"))
        font_dec.clicked.connect(lambda: self._change_font_size(-1))
        font_row.addWidget(font_dec)
        font_row.addWidget(self._font_size_lbl)
        font_inc = QPushButton("+")
        font_inc.setFixedSize(22, 22)
        font_inc.setToolTip(tr("Increase terminal font size"))
        font_inc.clicked.connect(lambda: self._change_font_size(+1))
        font_row.addWidget(font_inc)
        font_row.addStretch()
        clr = QPushButton(tr("Clear"))
        clr.setFixedHeight(22)
        clr.clicked.connect(lambda: (self._terminal.clear(), menu.close()))
        font_row.addWidget(clr)
        grid.addLayout(font_row, 6, 0, 1, 2)

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

    def _on_transport_changed(self, kind: str) -> None:
        """Adapt the target field to the selected transport.

        Serial → port list (rescanned). Network → editable URL field. SSH → a
        read-only summary; the credentials are gathered by a dialog on Connect.
        """
        combo = self._port_combo
        # Remember a typed network URL before we repurpose the field.
        if combo.isEditable():
            self._net_url = combo.currentText().strip()

        if kind == "Network":
            combo.setEditable(True)
            combo.clear()
            combo.lineEdit().setPlaceholderText(
                "socket://192.168.1.50:23   ·   rfc2217://host:4001")
            combo.setEditText(self._net_url)
            self._refresh_btn.setEnabled(False)
            self._refresh_btn.setText("⟳")
        elif kind == "SSH":
            combo.setEditable(False)
            self._serial.populate_ssh_combo()
            self._refresh_btn.setEnabled(True)
            self._refresh_btn.setText("✎")
            self._refresh_btn.setToolTip(tr("Edit the selected SSH session"))
        else:  # Serial
            combo.setEditable(False)
            self._refresh_btn.setEnabled(True)
            self._refresh_btn.setText("⟳")
            self._refresh_btn.setToolTip(tr("Rescan serial ports"))
            self._serial.refresh_ports()

    def _on_refresh_clicked(self) -> None:
        """⟳ / ✎ button — rescan ports (Serial) or edit the SSH session (SSH)."""
        if self._transport_combo.currentText() == "SSH":
            self._serial.edit_ssh_session(new=False)
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
        self._eol_combo = QComboBox()
        self._eol_combo.addItems(["\\r\\n","\\n","\\r","None"])
        self._eol_combo.setFixedWidth(54)
        cr.addWidget(self._eol_combo)
        self._send_btn = QPushButton(tr("Send"))
        self._send_btn.setObjectName("sendBtn")
        self._send_btn.setFixedWidth(54)
        self._send_btn.clicked.connect(self._serial.send_command)
        cr.addWidget(self._send_btn)
        lay.addLayout(cr)

        return bar

    # ── Right panel ───────────────────────────────────────────────────────────

    def _build_right(self) -> QWidget:
        """Right panel (46 %): tabbed chart area (Graphs / Indicators / FFT) + sidebar."""
        w = QWidget()
        w.setObjectName("rightPanel")   # bg2 so the tab-row gap fills (not dark bg)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(False)        # documentMode drew a stray base line
        self._tabs.tabBar().setDrawBase(False)   # no native base line under the tabs
        lay.addWidget(self._tabs)

        # Graphs + Indicators are per-connection (SER3): each tab holds a stack
        # of that connection's panel; switching connections swaps the page.
        self._chart_stack = QStackedWidget()
        self._tabs.addTab(self._chart_stack, tr("Graphs"))

        self._indicator_stack = QStackedWidget()
        self._tabs.addTab(self._indicator_stack, tr("Indicators"))

        self._events_stack = QStackedWidget()
        self._tabs.addTab(self._events_stack, tr("Events"))

        self._analytics_stack = QStackedWidget()
        self._tabs.addTab(self._analytics_stack, tr("Analytics"))

        # Pop-out button in the tab-bar corner
        _float_btn = QPushButton("⤢")
        _float_btn.setObjectName("floatBtn")
        _float_btn.setFixedSize(26, 22)
        _float_btn.setToolTip(tr("Open in separate window"))
        _float_btn.setStyleSheet(
            "border:none;background:transparent;color:#6b7280;"
            "font-size:14px;padding:0;")
        _float_btn.clicked.connect(lambda: self._float_current_tab())
        self._tabs.setCornerWidget(_float_btn)

        lay.addWidget(self._build_sidebar())
        # Now that all three stacks exist, build the active connection's panels.
        self._make_display_panels(self._active_conn)
        return w

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
        conn.events.jump_to_line.connect(self._search.jump_to_line)
        conn.trigger.trigger_changed.connect(
            lambda c=conn: self._triggers.sync_analytics(c))
        # Per-connection triggers "fire always" — each engine's matches route to
        # this connection (events/notifications regardless of which tab is active).
        conn.engine.on_match(
            lambda trig, line, ts, c=conn: self._triggers.on_match_threadsafe(c, trig, line, ts))

        self._parse_stack.addWidget(conn.parse)
        self._chart_stack.addWidget(conn.chart)
        self._indicator_stack.addWidget(conn.indicator)
        self._trigger_stack.addWidget(conn.trigger)
        self._logger_stack.addWidget(conn.logger_panel)
        self._events_stack.addWidget(conn.events)
        self._analytics_stack.addWidget(conn.analytics)

    def _build_sidebar(self) -> QWidget:
        """
        Scrollable right sidebar.
        Every section is a CollapsibleSection accordion panel.
        """
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(245)
        lay = QVBoxLayout(sidebar)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        from PyQt6.QtWidgets import QFrame
        scroll.setFrameShape(QFrame.Shape.NoFrame)   # no inset border/gap above Macros

        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setContentsMargins(0, 0, 0, 0)
        inner_lay.setSpacing(0)

        self._sidebar_sections: dict[str, CollapsibleSection] = {}

        def _cs(key: str, title: str, content: QWidget,
                extras: list | None = None, collapsed: bool = False):
            cs = CollapsibleSection(title, content, extra_widgets=extras,
                                    collapsed=collapsed)
            self._sidebar_sections[key] = cs
            inner_lay.addWidget(cs)

        # ── Macros ────────────────────────────────────────────────────────────
        self._macro_panel = MacroPanel(self._macro_runner, parent=inner)
        _m_file = QPushButton("📁")
        _m_file.setFixedSize(22, 20)
        _m_file.setToolTip(tr("Send file directly over serial"))
        _m_file.setStyleSheet("font-size:11px;padding:0;")
        _m_file.clicked.connect(self._macro_panel._send_file_direct)
        _m_add = QPushButton(tr("+ Add"))
        _m_add.setObjectName("add")
        _m_add.setFixedHeight(20)
        _m_add.clicked.connect(self._macro_panel._new_macro)
        _cs("macros", tr("Macros"), self._macro_panel, extras=[_m_file, _m_add])

        # ── Parsing ───────────────────────────────────────────────────────────
        # Per-connection parse panels live in a stack (SER3); the active one is
        # exposed as self._parse_panel. Populated by _make_display_panels.
        self._parse_stack = QStackedWidget()
        _p_add = QPushButton(tr("+ Add"))
        _p_add.setObjectName("add")
        _p_add.setFixedHeight(20)
        _p_add.clicked.connect(lambda: self._parse_panel.open_editor())
        _cs("parsing", tr("Parsing"), self._parse_stack, extras=[_p_add], collapsed=True)

        # ── Data Logger ───────────────────────────────────────────────────────
        # Per-connection (SER3): each connection logs to its own files.
        self._logger_stack = QStackedWidget()
        _l_folder = QPushButton("📂")
        _l_folder.setFixedSize(22, 20)
        _l_folder.setToolTip(tr("Open log folder"))
        _l_folder.setStyleSheet("font-size:11px;padding:0;")
        _l_folder.clicked.connect(lambda: self._logger_panel._open_folder())
        _cs("logger", tr("Data Logger"), self._logger_stack, extras=[_l_folder])

        # ── Triggers ──────────────────────────────────────────────────────────
        # Per-connection (SER3): each connection has its own trigger set + hits.
        self._trigger_stack = QStackedWidget()
        _t_reset = QPushButton(tr("Reset"))
        _t_reset.setFixedHeight(20)
        _t_reset.clicked.connect(lambda: self._trigger_panel._clear_hits())
        _t_add = QPushButton(tr("+ Add"))
        _t_add.setObjectName("add")
        _t_add.setFixedHeight(20)
        _t_add.clicked.connect(lambda: self._trigger_panel._open_editor())
        _cs("triggers", tr("Triggers"), self._trigger_stack, extras=[_t_reset, _t_add])

        # ── Custom command ────────────────────────────────────────────────────
        _cs("custom", tr("Custom command"), self._build_custom_cmd(), collapsed=True)

        inner_lay.addStretch()
        scroll.setWidget(inner)
        lay.addWidget(scroll)
        return sidebar


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
        return w

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
        self._sb_err  = QLabel(tr("Errors: 0"))
        self._sb_sess = QLabel(tr("Session: {t}").format(t="00:00:00"))
        self._sb_conn = QLabel("● " + tr("Disconnected"))
        self._sb_conn.setStyleSheet("color:#ef4444")
        for w in (self._sb_conn, self._sb_rx, self._sb_tx, self._sb_rate, self._sb_err):
            sb.addWidget(w)

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

    def _on_conn_line(self, conn, line: str, ts: str):
        """RX pipeline for a specific source connection (GUI thread, queued).

        Data (log, terminal, line-identity, parse) always runs against *conn*.
        The shared display panels, macro runner and session sharing update only
        when *conn* is the active one. Per-connection triggers ("fire always")
        land in a later step — for now the active engine runs."""
        is_active = conn is self._active_conn

        conn.rx_bytes += len(line.encode())
        if is_active:
            self._sb_rx.setText(f"RX: {self._fmt_bytes(conn.rx_bytes)}")

        # Log (non-blocking — goes to queue)
        conn.logger.write_line(line, ts)

        # Display into conn's terminal first — apply log-level color if active.
        # Logging before the trigger check gives this line a stable block id
        # that a trigger event can later jump back to (F2).
        log_color = match_log_color(line, self._log_colorizer_enabled)
        self._log("RX", line, C_RX, ts, text_color=log_color, conn=conn)
        conn.current_rx_line_id = conn.last_rx_line_id

        # Check triggers on the source connection's own engine — "fire always"
        # (events + notifications happen regardless of which tab is active).
        conn.engine.check(line, ts)

        # Parse numeric channels → update this connection's own panels (they are
        # per-connection now, so background connections keep accumulating too).
        parsed = conn.parser.parse(line)
        if parsed:
            conn.last_parsed.update(parsed)
            conn.parse.update_values(parsed)
            conn.chart.update(parsed)
            conn.indicator.update(parsed)

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

    @pyqtSlot(str, bool)
    def _float_current_tab(self) -> None:
        """Detach the active tab panel into its own resizable window."""
        idx = self._tabs.currentIndex()
        if idx < 0:
            return
        widget = self._tabs.widget(idx)
        title  = self._tabs.tabText(idx)
        self._tabs.removeTab(idx)
        win = _FloatWindow(widget, title, self._tabs, idx)
        win.setStyleSheet(QApplication.instance().styleSheet())
        tint_titlebar(win)
        win.destroyed.connect(lambda: self._float_wins.remove(win) if win in self._float_wins else None)
        self._float_wins.append(win)
        win.show()

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
        # Force the tab bar to repaint — on macOS the empty tab-bar area can keep
        # stale paint from the previous theme (the "dark rectangle after Analytics").
        self._repolish(self._tabs)
        self._repolish(self._tabs.tabBar())
        if self._tabs.cornerWidget():
            self._repolish(self._tabs.cornerWidget())
        self._tabs.update()
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
        """Show/hide the right panel per the current toggle + mode and sync the
        toggle button. The right panel is only ever shown in Advanced mode."""
        show = self._right_panel_visible and self._mode == "advanced"
        self._right_widget.setVisible(show)
        self._panel_toggle_btn.setText("⊞" if self._right_panel_visible else "⊟")
        if show:
            total = self._main_splitter.width()
            self._main_splitter.setSizes([int(total * 0.54), int(total * 0.46)])

    def _toggle_right_panel(self) -> None:
        """Show or hide the right panel (⊞ button / Ctrl+Shift+R)."""
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
             text_color: QColor | None = None, conn=None):
        # Route to a specific connection's terminal + line-identity; default is
        # the active connection (SER3). SYS/ERR/TX callers pass no conn → active.
        conn = conn or self._active_conn
        term = self._terminals[conn.id]

        if not ts and self._chk_ts.isChecked():
            from datetime import datetime
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]

        cursor = term.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        dim_fmt = QTextCharFormat()
        dim_fmt.setForeground(C_DIM)

        dir_fmt = QTextCharFormat()
        dir_fmt.setForeground(color)
        dir_fmt.setFontWeight(700)

        txt_fmt = QTextCharFormat()
        if text_color is not None:
            txt_fmt.setForeground(text_color)
        else:
            txt_fmt.setForeground(C_FG if direction in ("RX", "SYS") else color)

        if self._chk_ts.isChecked() and ts:
            cursor.insertText(f"{ts}  ", dim_fmt)
        cursor.insertText(f"{direction:<3}  ", dir_fmt)
        cursor.insertText(text, txt_fmt)
        # Tag this line's block with a stable id so events can jump to it (F2)
        conn.line_seq += 1
        cursor.block().setUserState(conn.line_seq)
        if direction == "RX":
            conn.last_rx_line_id = conn.line_seq
        cursor.insertText("\n", txt_fmt)

        # Limit scrollback to configured limit — must happen BEFORE ensureCursorVisible
        # so the trim doesn't shift the viewport after the scroll-to-bottom.
        doc = term.document()
        if doc.lineCount() > self._scrollback_limit:
            cur = QTextCursor(doc)
            cur.movePosition(QTextCursor.MoveOperation.Start)
            cur.movePosition(QTextCursor.MoveOperation.Down, QTextCursor.MoveMode.KeepAnchor,
                             max(1, self._scrollback_limit // 10))
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

        t2 = QTimer(self)
        t2.setInterval(2000)
        t2.timeout.connect(self._trigger_panel.refresh_hits)
        t2.start()

    def _tick_session(self):
        """Increments the session clock every second while the port is connected."""
        if self._reader.isRunning():
            self._session_sec += 1
            h = self._session_sec // 3600
            m = (self._session_sec % 3600) // 60
            s = self._session_sec % 60
            self._sb_sess.setText(tr("Session: {t}").format(t=f"{h:02d}:{m:02d}:{s:02d}"))

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
        if self._reader.isRunning():
            self._reader.disconnect_port()
        self._settings.save()
        self._logger.shutdown()
        event.accept()
