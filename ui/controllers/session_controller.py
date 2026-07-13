"""SessionController — all session-sharing behaviour (host + viewer).

Extracted from MainWindow (OSS6). Owns the SessionServer (host side) and
SessionClient (viewer side), the Share/Join dialogs, and the related status
wiring. MainWindow keeps the Share/Join buttons and the ping status label and
delegates their clicks here; the serial path forwards outgoing/incoming lines
to viewers via :meth:`feed_line`.

The colour constants live as module globals in ``ui.main_window`` and are
re-bound on theme change, so they are read through the module at call time
(``_win.C_SYS``) rather than imported by value.
"""
from __future__ import annotations

import datetime
import json
import threading
import urllib.error
import urllib.request

from PyQt6.QtCore import QObject, Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import core.signaling as signaling
import ui.main_window as _win   # for theme-updated colour globals (runtime access only)
from core.session_client import SessionClient
from core.session_server import SessionServer
from core.stun_helper import get_local_ip, get_public_ip


class SessionController(QObject):
    """Coordinates sharing a serial session (host) and joining one (viewer)."""

    def __init__(self, mw):
        super().__init__(mw)
        self._mw = mw
        self._server: SessionServer | None = None
        self._client: SessionClient | None = None
        self._share_session_code: str = ""
        # Transient Share-dialog widgets (recreated each time the dialog opens).
        self._share_dialog = None
        self._share_code_lbl = None
        self._share_viewers_lbl = None

    # ── serial → viewers bridge ─────────────────────────────────────────────────

    def feed_line(self, text: str, kind: str) -> None:
        """Forward a serial line to connected viewers if a share is active."""
        if self._server:
            self._server.feed_line(text, kind)

    @property
    def sharing(self) -> bool:
        return self._server is not None

    # ── host: share ─────────────────────────────────────────────────────────────

    def toggle_share(self) -> None:
        if self._server:
            self.stop_share()
        else:
            self.start_share()

    def start_share(self) -> None:
        mw = self._mw
        port = SessionServer.DEFAULT_PORT
        self._server = SessionServer(port, mw)
        self._server.client_connected.connect(
            lambda addr: mw._log("SYS", f"[SHARE] {addr} connected", _win.C_SYS))
        self._server.client_disconnected.connect(
            lambda addr: mw._log("SYS", f"[SHARE] {addr} disconnected", _win.C_DIM))
        self._server.error.connect(
            lambda e: mw._log("ERR", f"[SHARE] {e}", _win.C_ERR))
        self._server.viewer_count_changed.connect(self._on_viewer_count)
        self._server.start()

        mw._share_btn.setText("Stop")
        mw._share_btn.setObjectName("stopShareBtn")
        mw._repolish(mw._share_btn)
        mw._log("SYS", f"[SHARE] Session server started on port {port}", _win.C_SYS)

        lan_ip   = get_local_ip()
        lan_addr = f"{lan_ip}:{port}" if lan_ip else f"?:{port}"

        # Build dialog immediately (shows LAN address right away)
        dlg = QDialog(mw)
        dlg.setWindowTitle("Session sharing active")
        dlg.setMinimumWidth(400)
        self._share_dialog = dlg

        form = QFormLayout(dlg)
        form.setContentsMargins(16, 16, 16, 16)
        form.setSpacing(10)

        lan_lbl = QLabel(lan_addr)
        lan_lbl.setObjectName("stat")
        lan_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("LAN address:", lan_lbl)

        self._share_code_lbl = QLabel("Detecting…")
        self._share_code_lbl.setObjectName("stat")
        self._share_code_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("Session code:", self._share_code_lbl)

        self._share_viewers_lbl = QLabel("0 connected")
        self._share_viewers_lbl.setObjectName("stat")
        form.addRow("Viewers:", self._share_viewers_lbl)

        note = QLabel(
            "Your colleague opens IsoDAQ Studio → clicks Join → enters the code above.\n"
            "The code is valid for 1 hour."
        )
        note.setWordWrap(True)
        note.setObjectName("dimLabel")
        form.addRow(note)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setObjectName("dimLabel")
        form.addRow(sep)

        lan_note = QLabel("Same network? Share the LAN address directly.")
        lan_note.setObjectName("dimLabel")
        form.addRow(lan_note)

        fw_note = QLabel(
            "Internet sharing requires port 9876 to be reachable from outside.\n"
            "If the viewer gets 'connection refused', your router is blocking it —\n"
            "forward port 9876 → this machine, or use a VPN."
        )
        fw_note.setWordWrap(True)
        fw_note.setObjectName("dimLabel")
        form.addRow(fw_note)

        btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn.rejected.connect(dlg.accept)
        form.addRow(btn)

        threading.Thread(target=self._discover_and_register,
                         args=(port,), daemon=True).start()
        dlg.exec()
        self._share_dialog = None

    def _discover_and_register(self, port: int) -> None:
        mw = self._mw

        # ── 1. STUN ───────────────────────────────────────────────────────
        pub_ip = None
        try:
            pub_ip = get_public_ip()
        except Exception as e:
            mw._log("SYS", f"[SHARE] STUN error: {e}", _win.C_DIM)
        if pub_ip:
            mw._log("SYS", f"[SHARE] STUN public IP: {pub_ip}", _win.C_DIM)
        else:
            mw._log("SYS", "[SHARE] STUN failed — no public IP (check internet)", _win.C_DIM)

        # ── 2. Signaling URL check ─────────────────────────────────────────
        base = signaling.normalize(mw._signaling_url)
        if not base:
            mw._log("SYS",
                    "[SHARE] Internet sharing unavailable — no signaling server URL.\n"
                    "  → Edit → Preferences → set Signaling server URL", _win.C_DIM)
            if self._share_code_lbl:
                self._share_code_lbl.setText("Not available (no server URL)")
            return

        if not pub_ip:
            if self._share_code_lbl:
                self._share_code_lbl.setText("Not available (STUN failed)")
            return

        # ── 3. Register with signaling server ──────────────────────────────
        try:
            body = json.dumps({"ip": pub_ip, "port": port}).encode()
            req  = urllib.request.Request(f"{base}/register", data=body,
                                          headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=8) as r:
                resp = json.loads(r.read())
            code = str(resp.get("code", ""))
            if not (len(code) == 6 and code.isdigit()):
                raise ValueError(f"unexpected response: {resp}")
            display = f"{code[:3]} {code[3:]}"
            self._share_session_code = code
            mw._log("SYS", f"[SHARE] Code: {display}", _win.C_SYS)
            if self._share_code_lbl:
                self._share_code_lbl.setText(display)
            # Enable relay so internet viewers can connect through NAT
            if self._server:
                self._server.set_relay(base, code)
                mw._log("SYS", "[SHARE] Relay active — internet sharing ready", _win.C_DIM)
        except urllib.error.HTTPError as e:
            mw._log("SYS", f"[SHARE] Signaling server error HTTP {e.code}: {e.reason}", _win.C_DIM)
            if self._share_code_lbl:
                self._share_code_lbl.setText(f"Not available (HTTP {e.code})")
        except Exception as e:
            mw._log("SYS", f"[SHARE] Cannot reach signaling server: {e}", _win.C_DIM)
            if self._share_code_lbl:
                self._share_code_lbl.setText("Not available (server unreachable)")

    def _on_viewer_count(self, n: int) -> None:
        """Relay viewer count changed — update the Share dialog and log it."""
        label = f"{n} connected" if n != 1 else "1 connected"
        if self._share_viewers_lbl:
            try:
                self._share_viewers_lbl.setText(label)
            except RuntimeError:
                pass   # dialog/label already destroyed
        self._mw._log("SYS", f"[SHARE] Viewers: {n}", _win.C_DIM)

    def stop_share(self) -> None:
        mw = self._mw
        if self._server:
            self._server.stop()
            self._server = None
        self._share_session_code = ""
        self._share_viewers_lbl  = None
        mw._share_btn.setText("Share")
        mw._share_btn.setObjectName("shareBtn")
        mw._repolish(mw._share_btn)
        mw._log("SYS", "[SHARE] Session stopped", _win.C_DIM)

    # ── viewer: join ────────────────────────────────────────────────────────────

    def open_join_dialog(self) -> None:
        mw = self._mw

        # If already joined, treat button as Leave
        if self._client:
            client = self._client
            self._client = None
            client.stop()
            mw._sb_ping.hide()
            mw._join_btn.setText("Join")
            mw._join_btn.setObjectName("joinBtn")
            mw._repolish(mw._join_btn)
            mw._log("SYS", "[JOIN] Disconnected", _win.C_DIM)
            return

        dlg = QDialog(mw)
        dlg.setWindowTitle("Join a session")
        dlg.setMinimumWidth(360)

        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        tabs = QTabWidget()
        outer.addWidget(tabs)

        # ── Tab 1: Join by code (internet) ────────────────────────────────
        code_w = QWidget()
        code_form = QFormLayout(code_w)
        code_form.setContentsMargins(12, 12, 12, 0)
        code_form.setSpacing(10)

        code_note = QLabel("Enter the 6-digit code shown on the host's Share dialog.")
        code_note.setWordWrap(True)
        code_note.setObjectName("dimLabel")
        code_form.addRow(code_note)

        code_edit = QLineEdit()
        code_edit.setPlaceholderText("e.g.  481 203")
        code_edit.setMaxLength(7)
        code_form.addRow("Code:", code_edit)

        tabs.addTab(code_w, "By code")

        # ── Tab 2: Join by address (LAN) ───────────────────────────────────
        addr_w = QWidget()
        addr_form = QFormLayout(addr_w)
        addr_form.setContentsMargins(12, 12, 12, 0)
        addr_form.setSpacing(10)

        addr_note = QLabel("Enter the LAN address shown on the host's Share dialog.")
        addr_note.setWordWrap(True)
        addr_note.setObjectName("dimLabel")
        addr_form.addRow(addr_note)

        addr_edit = QLineEdit()
        addr_edit.setPlaceholderText("192.168.x.x:9876")
        addr_form.addRow("Address:", addr_edit)

        tabs.addTab(addr_w, "By address (LAN)")

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        outer.addWidget(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        if tabs.currentIndex() == 0:
            # Code-based join → relay (works through NAT/internet)
            raw_code = code_edit.text().strip()
            if not raw_code:
                return
            base = signaling.normalize(mw._signaling_url)
            if not base:
                QMessageBox.warning(mw, "No signaling server",
                                    "A signaling server URL is required to join by code.\n"
                                    "Go to Edit → Preferences and set the Signaling server URL,\n"
                                    "or ask the host for the LAN address and use By address.")
                return
            # Validate the code exists before connecting to relay
            result = signaling.lookup(raw_code, mw._signaling_url)
            if not result:
                QMessageBox.warning(mw, "Code not found",
                                    "Session not found or expired.\n"
                                    "Ask the host to check their session code.")
                return
            clean = raw_code.strip().replace(" ", "").replace("-", "")
            relay_url = f"{base}/tunnel/{clean}/poll"
            mw._log("SYS", f"[JOIN] Code {raw_code.strip()} → relay", _win.C_DIM)
            self._connect_relay(relay_url)
        else:
            # Address-based join → direct TCP (LAN / VPN)
            raw = addr_edit.text().strip()
            if not raw:
                return
            try:
                host, port_str = raw.rsplit(":", 1)
                port = int(port_str)
            except ValueError:
                QMessageBox.warning(mw, "Invalid address",
                                    "Use the format  host:port  e.g. 192.168.1.5:9876")
                return
            self._connect_to_session(host, port)

    def _connect_to_session(self, host: str, port: int) -> None:
        """Start a direct TCP session (LAN / VPN)."""
        self._start_client(SessionClient(host=host, port=port, parent=self._mw))
        self._mw._sb_ping.setText("● … ms")
        self._mw._sb_ping.setStyleSheet("color:#6a6a7a")

    def _connect_relay(self, relay_url: str) -> None:
        """Start a relay session (works through NAT/internet)."""
        self._start_client(SessionClient(relay_url=relay_url, parent=self._mw))
        self._mw._sb_ping.setText("● relay")
        self._mw._sb_ping.setStyleSheet("color:#6a6a7a")

    def _start_client(self, client: SessionClient) -> None:
        mw = self._mw
        if self._client:
            self._client.stop()
        client.connected.connect(
            lambda addr: mw._log("SYS", f"[JOIN] Connected to {addr}", _win.C_SYS))
        client.disconnected.connect(self._on_session_disconnected)
        client.error.connect(lambda e: mw._log("ERR", f"[JOIN] {e}", _win.C_ERR))
        client.host_closed.connect(self._on_host_closed)
        client.line_received.connect(self._on_remote_line)
        client.latency_updated.connect(self._on_latency_updated)
        client.start()
        self._client = client
        mw._sb_ping.show()
        mw._join_btn.setText("Leave")
        mw._join_btn.setObjectName("stopShareBtn")
        mw._repolish(mw._join_btn)

    def _on_host_closed(self) -> None:
        if self.sender() is not self._client:
            return   # stale client — ignore
        self._mw._log("SYS", "[JOIN] Host closed the session — leaving", _win.C_SYS)

    def _on_session_disconnected(self) -> None:
        # Ignore signals from a superseded client (stale long-poll finishing late)
        if self.sender() is not self._client:
            return
        mw = self._mw
        self._client = None
        mw._log("SYS", "[JOIN] Session ended", _win.C_DIM)
        mw._sb_ping.hide()
        mw._join_btn.setText("Join")
        mw._join_btn.setObjectName("joinBtn")
        mw._repolish(mw._join_btn)

    def _on_latency_updated(self, ms: int) -> None:
        if self.sender() is not self._client:
            return   # stale client — ignore
        ping = self._mw._sb_ping
        if ms < 0:
            ping.setText("● timeout")
            ping.setStyleSheet("color:#ef4444")
        elif ms <= 80:
            ping.setText(f"● {ms} ms")
            ping.setStyleSheet("color:#4ec994")   # green
        elif ms <= 250:
            ping.setText(f"● {ms} ms")
            ping.setStyleSheet("color:#f59e0b")   # yellow
        else:
            ping.setText(f"● {ms} ms")
            ping.setStyleSheet("color:#ef4444")   # red

    def _on_remote_line(self, line: str, ts: float, kind: str) -> None:
        """Handle a line received from a remote session — display, log and parse."""
        if self.sender() is not self._client:
            return   # stale client (superseded long-poll) — drop its lines
        mw = self._mw
        ts_str = datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:12]
        if kind == "tx":
            mw._log("TX", line, _win.C_TX, ts_str)
        else:
            color = _win.C_RX if kind == "rx" else _win.C_SYS
            mw._log("REM", line, color, ts_str)

        # Persist received session data when logging is active (no-op otherwise).
        # Lets a viewer record a shared session to CSV/DB just like a serial feed.
        mw._logger.write_line(line, ts_str)

        parsed = mw._parser.parse(line)
        if parsed:
            mw._chart_panel.update(parsed)
            mw._indicator_panel.update(parsed)
