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
    QStatusBar,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.logger import Logger
from core.macros import MacroRunner
from core.serial_reader import SerialReader
import core.signaling as signaling
from core.i18n import current_language, tr
from core.triggers import TriggerEngine
from core.data_parser import DataParser
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

        # Core services
        self._logger = Logger()
        self._engine = TriggerEngine()
        self._reader = SerialReader(self)
        self._log_colorizer_enabled: set[str] = set()
        self._macro_runner = MacroRunner(self._reader.send, self)
        self._parser = DataParser()
        self._last_parsed: dict[str, float] = {}
        self._float_wins: list[_FloatWindow] = []
        self._terminal_font_size: int = 11
        self._scrollback_limit: int = 5000
        self._current_theme: str = "vscode"
        self._rx_bytes = 0
        self._tx_bytes = 0
        self._session_sec = 0
        self._right_panel_visible: bool = True
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

        # Per-line identity for "jump to log line" (F2) and in-log search (F1)
        self._line_seq: int = 0
        self._last_rx_line_id: int = -1
        self._current_rx_line_id: int = -1

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

        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._main_splitter.setHandleWidth(3)
        root.addWidget(self._main_splitter)

        self._main_splitter.addWidget(self._build_left())
        self._right_widget = self._build_right()
        self._main_splitter.addWidget(self._right_widget)
        self._main_splitter.setSizes([702, 598])

        self._build_statusbar()

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

        lay.addWidget(self._build_port_bar())

        self._terminal = QTextEdit()
        self._terminal.setObjectName("terminal")
        self._terminal.setReadOnly(True)
        self._terminal.setFont(QFont("JetBrains Mono", 11))
        lay.addWidget(self._terminal)

        lay.addWidget(self._search.build_bar())
        lay.addWidget(self._build_input_bar())
        return w

    def _build_port_bar(self) -> QWidget:
        """
        Top bar of the left panel.
        Row 1 — port selector, refresh, Connect/Disconnect button.
        Row 2 — baud rate, data bits (8N1…), flow control.
        Row 3 — Timestamp / HEX / Autoscroll / Echo checkboxes, font-size control, Clear.
        """
        bar = QWidget()
        bar.setObjectName("portBar")
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(10)

        # Row 1: port + baud + connect
        r1 = QHBoxLayout()
        r1.setSpacing(10)
        r1.addWidget(self._lbl("PORT", mono=True, dim=True))
        self._port_combo = QComboBox()
        self._port_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        r1.addWidget(self._port_combo)
        self._refresh_btn = QPushButton("⟳")
        self._refresh_btn.setFixedWidth(50)
        self._refresh_btn.clicked.connect(self._serial.refresh_ports)
        r1.addWidget(self._refresh_btn)
        self._conn_btn = QPushButton(tr("Connect"))
        self._conn_btn.setObjectName("connectBtn")
        self._conn_btn.setFixedWidth(100)
        self._conn_btn.clicked.connect(self._serial.toggle_connection)
        r1.addWidget(self._conn_btn)
        self._share_btn = QPushButton(tr("Share"))
        self._share_btn.setObjectName("shareBtn")
        self._share_btn.setFixedHeight(28)
        self._share_btn.setToolTip(tr("Share this serial session with a colleague"))
        self._share_btn.clicked.connect(self._session.toggle_share)
        r1.addWidget(self._share_btn)

        self._join_btn = QPushButton(tr("Join"))
        self._join_btn.setObjectName("joinBtn")
        self._join_btn.setFixedHeight(28)
        self._join_btn.setToolTip(tr("Connect to a shared session"))
        self._join_btn.clicked.connect(self._session.open_join_dialog)
        r1.addWidget(self._join_btn)

        self._panel_toggle_btn = QPushButton("⊞")
        self._panel_toggle_btn.setObjectName("panelToggleBtn")
        self._panel_toggle_btn.setFixedSize(28, 28)
        self._panel_toggle_btn.setToolTip(tr("Show/hide right panel  (Ctrl+Shift+R)"))
        self._panel_toggle_btn.clicked.connect(self._toggle_right_panel)
        r1.addWidget(self._panel_toggle_btn)
        lay.addLayout(r1)

        # Row 2: baud + data bits + flow
        r2 = QHBoxLayout()
        r2.setSpacing(6)
        r2.addWidget(self._lbl("BAUD", mono=True, dim=True))
        self._baud_combo = QComboBox()
        self._baud_combo.addItems(["9600","19200","38400","57600","115200","230400","460800","921600"])
        self._baud_combo.setCurrentText("115200")
        self._baud_combo.setFixedWidth(100)
        r2.addWidget(self._baud_combo)
        r2.addWidget(self._lbl(tr("Data"), dim=True))
        self._data_combo = QComboBox()
        self._data_combo.addItems(["8N1","8E1","8O1","7N1"])
        self._data_combo.setFixedWidth(100)
        r2.addWidget(self._data_combo)
        r2.addWidget(self._lbl(tr("Flow"), dim=True))
        self._flow_combo = QComboBox()
        self._flow_combo.addItems(["None","RTS/CTS","XON/XOFF"])
        self._flow_combo.setFixedWidth(120)
        r2.addWidget(self._flow_combo)
        r2.addStretch()
        lay.addLayout(r2)

        # Row 3: checkboxes + font size + clear
        r3 = QHBoxLayout()
        r3.setSpacing(0)
        r3.setContentsMargins(0, 2, 0, 2)
        self._chk_ts    = QCheckBox(tr("Timestamp")); self._chk_ts.setChecked(True)
        self._chk_hex   = QCheckBox("HEX")
        self._chk_auto  = QCheckBox(tr("Autoscroll")); self._chk_auto.setChecked(True)
        self._chk_echo  = QCheckBox(tr("Echo"));       self._chk_echo.setChecked(True)
        for chk in (self._chk_ts, self._chk_hex, self._chk_auto, self._chk_echo):
            r3.addWidget(chk)
            r3.addSpacing(20)
        r3.addStretch()

        # Font size control — separated by a spacer from checkboxes
        r3.addSpacing(6)
        r3.addWidget(self._lbl("A", dim=True))
        r3.addSpacing(4)
        font_dec = QPushButton("−")
        font_dec.setFixedSize(22, 22)
        font_dec.setToolTip(tr("Decrease terminal font size"))
        font_dec.setStyleSheet("font-size:13px;padding:0;")
        font_dec.clicked.connect(lambda: self._change_font_size(-1))
        r3.addWidget(font_dec)
        self._font_size_lbl = QLabel("11")
        self._font_size_lbl.setFixedWidth(22)
        self._font_size_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._font_size_lbl.setObjectName("dimLabelMono")
        r3.addWidget(self._font_size_lbl)
        font_inc = QPushButton("+")
        font_inc.setFixedSize(22, 22)
        font_inc.setToolTip(tr("Increase terminal font size"))
        font_inc.setStyleSheet("font-size:13px;padding:0;")
        font_inc.clicked.connect(lambda: self._change_font_size(+1))
        r3.addWidget(font_inc)
        r3.addSpacing(10)

        clr = QPushButton(tr("Clear"))
        clr.setFixedHeight(22)
        clr.clicked.connect(lambda: self._terminal.clear())
        r3.addWidget(clr)
        lay.addLayout(r3)
        return bar

    def _build_input_bar(self) -> QWidget:
        """
        Bottom bar of the left panel.
        Row 1 — parser type selector + Prefix filter + separator field.
        Row 2 — command line edit + EOL selector + Send button.
        Row 3 — quick-command shortcut buttons (AT commands).
        """
        bar = QWidget()
        bar.setObjectName("inputBar")
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(5)

        # Parser strip (hidden in Simple mode)
        self._parser_strip_w = QWidget()
        self._parser_strip_w.setObjectName("parserStrip")
        ps = QHBoxLayout(self._parser_strip_w)
        ps.setContentsMargins(0, 0, 0, 0)
        ps.setSpacing(7)
        parser_lbl = self._lbl(tr("Parser"), mono=True, dim=True)
        parser_lbl.setToolTip(tr(
            "Selects how incoming RX lines are split into named channels\n"
            "for the Graphs / Indicators tabs.\n\n"
            "KEY=VALUE comma  →  DATA:x=1.2,y=3.4,z=0.0\n"
            "JSON             →  {\"x\":1.2,\"y\":3.4}\n"
            "CSV ordered      →  DATA:1.2,3.4,0.0  (mapped by Channel map)\n"
            "Regex custom     →  user-defined capture groups"
        ))
        ps.addWidget(parser_lbl)
        self._parser_combo = QComboBox()
        self._parser_combo.setObjectName("parserField")
        self._parser_combo.addItems(["KEY=VALUE comma", "JSON", "CSV ordered", "Regex custom"])
        self._parser_combo.setToolTip(tr(
            "KEY=VALUE comma — expects lines like: DATA:ch1=1.23,ch2=4.56\n"
            "JSON            — expects a JSON object per line: {\"ch1\":1.23}\n"
            "CSV ordered     — values in order matching Channel map: DATA:1.23,4.56\n"
            "Regex custom    — define your own capture groups in the Prefix field"
        ))
        ps.addWidget(self._parser_combo)
        prefix_lbl = self._lbl(tr("Prefix"), dim=True)
        prefix_lbl.setToolTip(tr(
            "Default prefix pre-filled when adding a new channel.\n"
            "Each channel can have its own prefix — set it in the channel editor."))
        ps.addWidget(prefix_lbl)
        self._prefix_edit = QLineEdit("DATA:")
        self._prefix_edit.setObjectName("parserField")
        self._prefix_edit.setFixedWidth(62)
        self._prefix_edit.setToolTip(tr("Line prefix filter, e.g. \"DATA:\""))
        ps.addWidget(self._prefix_edit)
        sep_lbl = self._lbl(tr("Sep"), dim=True)
        sep_lbl.setToolTip(tr("Field separator character (CSV / KEY=VALUE mode)"))
        ps.addWidget(sep_lbl)
        self._sep_edit = QLineEdit(",")
        self._sep_edit.setObjectName("parserField")
        self._sep_edit.setFixedWidth(50)
        self._sep_edit.setToolTip(tr("Separator, e.g. \",\" or \";\""))
        ps.addWidget(self._sep_edit)
        ps.addStretch()
        lay.addWidget(self._parser_strip_w)

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

        self._chart_panel = ChartPanel()
        self._tabs.addTab(self._chart_panel, tr("Graphs"))

        self._indicator_panel = IndicatorPanel()
        self._tabs.addTab(self._indicator_panel, tr("Indicators"))

        self._trigger_events_panel = TriggerEventsPanel()
        self._trigger_events_panel.jump_to_line.connect(self._search.jump_to_line)
        self._tabs.addTab(self._trigger_events_panel, tr("Events"))

        self._analytics_panel = AnalyticsPanel()
        self._tabs.addTab(self._analytics_panel, tr("Analytics"))

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
        return w

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
        self._parse_panel = ParsePanel(self._parser, parent=inner)
        _p_add = QPushButton(tr("+ Add"))
        _p_add.setObjectName("add")
        _p_add.setFixedHeight(20)
        _p_add.clicked.connect(
            lambda: self._parse_panel.open_editor(
                default_prefix=self._prefix_edit.text().strip()))
        _cs("parsing", tr("Parsing"), self._parse_panel, extras=[_p_add], collapsed=True)

        # ── Data Logger ───────────────────────────────────────────────────────
        self._logger_panel = LoggerPanel(self._logger)
        _l_folder = QPushButton("📂")
        _l_folder.setFixedSize(22, 20)
        _l_folder.setToolTip(tr("Open log folder"))
        _l_folder.setStyleSheet("font-size:11px;padding:0;")
        _l_folder.clicked.connect(self._logger_panel._open_folder)
        _cs("logger", tr("Data Logger"), self._logger_panel, extras=[_l_folder])

        # ── Triggers ──────────────────────────────────────────────────────────
        self._trigger_panel = TriggerPanel(self._engine)
        _t_reset = QPushButton(tr("Reset"))
        _t_reset.setFixedHeight(20)
        _t_reset.clicked.connect(self._trigger_panel._clear_hits)
        _t_add = QPushButton(tr("+ Add"))
        _t_add.setObjectName("add")
        _t_add.setFixedHeight(20)
        _t_add.clicked.connect(lambda: self._trigger_panel._open_editor())
        _cs("triggers", tr("Triggers"), self._trigger_panel, extras=[_t_reset, _t_add])

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
        _sb = self._terminal.verticalScrollBar()
        _sb.sliderPressed.connect(lambda: setattr(self, "_user_scrolling", True))
        _sb.sliderReleased.connect(lambda: setattr(self, "_user_scrolling", False))
        self._reader.line_received.connect(self._on_line_received)
        self._serial.wire()   # reader connection-state signals → SerialController

        # Trigger matches → GUI highlight + log
        self._engine.on_match(self._triggers.on_match_threadsafe)

        # Trigger list changes → sync analytics panel
        self._trigger_panel.trigger_changed.connect(self._triggers.sync_analytics)

        # Parse panel → chart / indicator panels
        self._parse_panel.channel_chart_req.connect(self._on_channel_chart_req)
        self._parse_panel.channel_indicator_req.connect(self._on_channel_indicator_req)

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
        """Called in GUI thread via queued connection."""
        self._rx_bytes += len(line.encode())
        self._sb_rx.setText(f"RX: {self._fmt_bytes(self._rx_bytes)}")

        # Log (non-blocking — goes to queue)
        self._logger.write_line(line, ts)

        # Display first — apply log-level color if a platform is active.
        # Logging before the trigger check gives this line a stable block id
        # that a trigger event can later jump back to (F2).
        log_color = match_log_color(line, self._log_colorizer_enabled)
        self._log("RX", line, C_RX, ts, text_color=log_color)
        self._current_rx_line_id = self._last_rx_line_id

        # Check triggers (fast path in GUI thread — engine is thread-safe)
        self._engine.check(line, ts)

        # Parse numeric channels → update live values + chart + indicators
        parsed = self._parser.parse(line)
        if parsed:
            self._last_parsed.update(parsed)
            self._parse_panel.update_values(parsed)
            self._chart_panel.update(parsed)
            self._indicator_panel.update(parsed)

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

    def _on_channel_chart_req(self, name: str, enable: bool) -> None:
        if enable:
            self._chart_panel.add_channel(name)
            self._trigger_events_panel.register_channel(name)
        else:
            self._chart_panel.remove_channel(name)
            self._trigger_events_panel.unregister_channel(name)

    @pyqtSlot(str, bool)
    def _on_channel_indicator_req(self, name: str, enable: bool) -> None:
        if enable:
            self._indicator_panel.add_indicator(name)
        else:
            self._indicator_panel.remove_indicator(name)

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
        self._chart_panel.apply_theme(c)
        self._analytics_panel.apply_theme(c)
        set_current_theme(theme)
        tint_titlebar(self)
        for win in self._float_wins:
            tint_titlebar(win)
        self._log("SYS", f"Theme: {theme}", C_SYS)

    def _on_autoscroll_toggled(self, state: int) -> None:
        if state:
            sb = self._terminal.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _toggle_right_panel(self) -> None:
        """Show or hide the right panel; update toggle button label."""
        self._right_panel_visible = not self._right_panel_visible
        self._right_widget.setVisible(self._right_panel_visible)
        self._panel_toggle_btn.setText("⊞" if self._right_panel_visible else "⊟")
        if self._right_panel_visible:
            # Restore a reasonable split when showing again
            total = self._main_splitter.width()
            self._main_splitter.setSizes([int(total * 0.54), int(total * 0.46)])

    def _toggle_mode(self) -> None:
        self._set_mode("simple" if self._mode == "advanced" else "advanced")

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        simple = mode == "simple"
        self._right_widget.setVisible(not simple)
        self._panel_toggle_btn.setVisible(not simple)
        self._parser_strip_w.setVisible(not simple)
        self._mode_action.setChecked(simple)
        if not simple and self._right_panel_visible:
            total = self._main_splitter.width()
            self._main_splitter.setSizes([int(total * 0.46), int(total * 0.54)])

    def _change_font_size(self, delta: int) -> None:
        """
        Increases or decreases the terminal font size (range 8–24 pt).
        Applies to all existing text immediately via document().setDefaultFont().
        """
        self._terminal_font_size = max(8, min(24, self._terminal_font_size + delta))
        font = QFont("JetBrains Mono", self._terminal_font_size)
        self._terminal.setFont(font)
        self._terminal.document().setDefaultFont(font)
        self._font_size_lbl.setText(str(self._terminal_font_size))

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

    def _log(self, direction: str, text: str, color: QColor, ts: str | None = None, text_color: QColor | None = None):
        if not ts and self._chk_ts.isChecked():
            from datetime import datetime
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]

        cursor = self._terminal.textCursor()
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
        self._line_seq += 1
        cursor.block().setUserState(self._line_seq)
        if direction == "RX":
            self._last_rx_line_id = self._line_seq
        cursor.insertText("\n", txt_fmt)

        # Limit scrollback to configured limit — must happen BEFORE ensureCursorVisible
        # so the trim doesn't shift the viewport after the scroll-to-bottom.
        doc = self._terminal.document()
        if doc.lineCount() > self._scrollback_limit:
            cur = QTextCursor(doc)
            cur.movePosition(QTextCursor.MoveOperation.Start)
            cur.movePosition(QTextCursor.MoveOperation.Down, QTextCursor.MoveMode.KeepAnchor,
                             max(1, self._scrollback_limit // 10))
            cur.removeSelectedText()

        if self._chk_auto.isChecked() and not self._user_scrolling:
            sb = self._terminal.verticalScrollBar()
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
