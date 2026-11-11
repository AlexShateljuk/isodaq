"""The Agent transport in IsoDAQ (AGT2): AgentReader on the StreamReader loop, the
port list, pairing from the window, and auto-reconnect giving up when the agent
no longer knows this computer."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("pyqtgraph")
pytest.importorskip("serial")
pytest.importorskip("cryptography")

from core import agent_client as ac               # noqa: E402
from isodaq_agent import server                   # noqa: E402
from test_agent import Board                      # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    app.setApplicationVersion("0.0.0-test")
    yield app


def pump_until(qapp, predicate, sec=3.0) -> bool:
    end = time.monotonic() + sec
    while time.monotonic() < end:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def board():
    b = Board()
    yield b
    b.close()


@pytest.fixture
def agent(tmp_path, board):
    srv = server.AgentServer(tmp_path / "agent", host="127.0.0.1", port=0, name="lab-pi",
                             extra_ports=(board.device,))
    srv.start_in_thread()
    yield srv
    srv.stop()


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "client" / "agents.json"
    monkeypatch.setattr(ac, "default_store_path", lambda: path)
    return ac.TrustStore(path)


@pytest.fixture
def paired(agent, store):
    ac.pair("127.0.0.1", agent.port, agent.pairing.new_code(), store, name="laptop")
    return agent, store


@pytest.fixture
def window(qapp, tmp_path, monkeypatch, store):
    from ui.controllers.update_manager import UpdateManager
    from ui.main_window import MainWindow
    monkeypatch.setattr(MainWindow, "_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(UpdateManager, "start", lambda self: None)
    w = MainWindow()
    w._transport_combo.setCurrentText("Serial")
    yield w
    if w._active_conn.reader.isRunning():
        w._active_conn.reader.disconnect_port()
    w.close()


def test_reader_streams_lines_and_sends(qapp, paired, board):
    from core.agent_reader import AgentReader
    agent, store = paired
    reader = AgentReader(store=store)
    lines, labels = [], []
    reader.lines_received.connect(lambda batch: lines.extend(t for t, *_ in batch))
    reader.connected.connect(lambda label, baud: labels.append((label, baud)))
    reader.configure(ac.make_url("127.0.0.1", agent.port, board.device), baud=57600)
    assert reader.connect_port() is None
    assert pump_until(qapp, lambda: labels)
    assert labels == [(f"lab-pi: {board.device}", 57600)]
    board.send(b"temp:21.5\r\nhum:40\r\n")
    assert pump_until(qapp, lambda: len(lines) >= 2)
    assert lines[:2] == ["temp:21.5", "hum:40"]
    assert reader.send(b"status\n") is None
    assert board.read(7) == b"status\n"
    reader.disconnect_port()
    assert pump_until(qapp, lambda: not agent.open_ports)


def test_reader_not_paired_is_fatal(qapp, store, agent, board):
    from core.agent_reader import AgentReader
    reader = AgentReader(store=store)
    errors = []
    reader.error_occurred.connect(errors.append)
    reader.configure(ac.make_url("127.0.0.1", agent.port, board.device))
    reader.connect_port()
    assert pump_until(qapp, lambda: errors)
    assert "isn't paired" in errors[0] and reader.fatal


def test_transport_and_port_list(qapp, window, paired, board):
    agent, _ = paired
    w = window
    w._transport_combo.setCurrentText("Agent")
    assert w._port_combo.isEditable()
    assert w._refresh_btn.isEnabled()
    w._on_refresh_clicked()
    url = ac.make_url("127.0.0.1", agent.port, board.device)
    assert pump_until(qapp, lambda: w._port_combo.findText(url) >= 0)
    assert w._refresh_btn.isEnabled()


def test_connect_from_the_window(qapp, window, paired, board):
    agent, _ = paired
    w = window
    w._transport_combo.setCurrentText("Agent")
    w._port_combo.setEditText(ac.make_url("127.0.0.1", agent.port, board.device))
    w._serial.toggle_connection()
    conn = w._active_conn
    assert pump_until(qapp, lambda: conn.name.startswith("lab-pi"))
    board.send(b"hello from the pi\n")
    assert pump_until(qapp, lambda: conn.rx_bytes >= 18)
    assert w._reader.send(b"ping\n") is None
    assert board.read(5) == b"ping\n"
    w._serial.toggle_connection()
    assert pump_until(qapp, lambda: not conn.reader.isRunning())


def test_unpaired_target_opens_pairing(qapp, window, agent, board, monkeypatch):
    w = window
    asked = []
    monkeypatch.setattr(w._agents, "pair_dialog",
                        lambda address="", then_connect=False: asked.append(
                            (address, then_connect)))
    w._transport_combo.setCurrentText("Agent")
    w._port_combo.setEditText(ac.make_url("127.0.0.1", agent.port, board.device))
    w._serial.toggle_connection()
    assert asked == [(f"127.0.0.1:{agent.port}", True)]
    assert not w._active_conn.reader.isRunning()


def test_bad_target_is_explained(qapp, window):
    w = window
    w._transport_combo.setCurrentText("Agent")
    w._port_combo.setEditText("agent://pi")
    w._serial.toggle_connection()
    assert pump_until(qapp, lambda: "agent://host/dev/ttyUSB0" in w._terminal.toPlainText())


def test_reconnect_gives_up_when_revoked(qapp, window, paired, board):
    agent, _ = paired
    w = window
    w._chk_reconnect.setChecked(True)
    w._transport_combo.setCurrentText("Agent")
    w._port_combo.setEditText(ac.make_url("127.0.0.1", agent.port, board.device))
    w._serial.toggle_connection()
    conn = w._active_conn
    assert pump_until(qapp, lambda: conn.name.startswith("lab-pi"))
    agent.clients.revoke("laptop")
    # Kick the client off, as if the link dropped.
    loop = agent._loop
    loop.call_soon_threadsafe(lambda: [s.cancel() for s in list(agent._sessions)])
    assert pump_until(qapp, lambda: conn.reconnecting, 3)
    assert pump_until(qapp, lambda: not conn.reconnecting, 8)
    assert pump_until(qapp, lambda: "isn't paired" in w._terminal.toPlainText())


def test_pair_dialog(qapp, window, agent, store):
    from ui.controllers.agent_controller import AgentsDialog, PairDialog
    w = window
    dlg = PairDialog(w._agents, f"127.0.0.1:{agent.port}", w)
    dlg.code.setText("WRONG-CODE")
    dlg._on_pair()
    assert "letters and digits" in dlg.status.text()
    dlg.code.setText("AAAA-AAAA")
    agent.pairing.new_code()
    dlg._on_pair()
    assert pump_until(qapp, lambda: "Wrong pairing code" in dlg.status.text())
    dlg.code.setText(agent.pairing.new_code())
    dlg._on_pair()
    assert pump_until(qapp, lambda: dlg.entry is not None)
    assert store.get("127.0.0.1", agent.port)["name"] == "lab-pi"
    assert pump_until(qapp, lambda: "Paired with agent lab-pi" in w._terminal.toPlainText())

    agents = AgentsDialog(w._agents, w)
    assert agents.table.rowCount() == 1
    assert agents.table.item(0, 0).text() == "lab-pi"
    agents.table.selectRow(0)
    agents._on_forget()
    assert agents.table.rowCount() == 0
    assert store.get("127.0.0.1", agent.port) is None


def test_agent_target_is_saved(qapp, window, tmp_path):
    import json
    w = window
    w._transport_combo.setCurrentText("Agent")
    w._port_combo.setEditText("agent://pi/dev/ttyUSB0")
    w._settings.save()
    data = json.loads((tmp_path / "config.json").read_text())
    assert data["agent_url"] == "agent://pi/dev/ttyUSB0"
    assert data["transport"] == "Agent"


def test_replug_on_the_agent_reconnects(qapp, window, paired, tmp_path):
    from test_device_control import Board as LinkedBoard
    agent, _ = paired
    link = tmp_path / "ttyBOARD"              # like /dev/serial/by-id/…: a stable name
    first = LinkedBoard(link)
    agent.extra_ports = (str(link),)
    w = window
    w._chk_reconnect.setChecked(True)
    w._transport_combo.setCurrentText("Agent")
    w._port_combo.setEditText(ac.make_url("127.0.0.1", agent.port, str(link)))
    w._serial.toggle_connection()
    conn = w._active_conn
    assert pump_until(qapp, lambda: conn.name.startswith("lab-pi"))
    first.unplug()
    assert pump_until(qapp, lambda: conn.reconnecting, 4)
    time.sleep(1.5)
    second = LinkedBoard(link)                # plugged back in
    try:
        assert pump_until(qapp, lambda: not conn.reconnecting and conn.reader.isRunning(), 10)
        second.send(b"back again\n")
        assert pump_until(qapp, lambda: "back again" in w._terminal.toPlainText())
    finally:
        w._serial.toggle_connection()
        pump_until(qapp, lambda: not conn.reader.isRunning())
        second.unplug()


def _local_pi(tmp_path, monkeypatch):
    """SSH replaced by the local stand-in from test_agent_setup; after the real
    installer runs, the installed agent is started the way systemd would."""
    import re
    import socket
    import subprocess

    from core import agent_setup
    from test_agent_setup import FAKE_SUDO, LocalSession
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "sudo").write_text(FAKE_SUDO)
    (bin_dir / "sudo").chmod(0o755)
    home, root = tmp_path / "home", tmp_path / "root"
    home.mkdir()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    seen, procs = {}, []

    def session(host, port_, user, password, key_path, accept_key):
        seen.update(host=host, user=user, accept_key=accept_key)
        return LocalSession(home, root, bin_dir)
    monkeypatch.setattr(agent_setup, "SSHSession", session)
    real_install = agent_setup.install

    def install(sess, agent_port, name, password, progress):
        code, out = real_install(sess, agent_port, name, password, progress)
        p = subprocess.Popen([str(root / "usr/local/bin/isodaq-agent"), "serve", "--host",
                              "127.0.0.1", "--port", str(agent_port)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(p)
        assert re.match(r"[A-Z0-9]{4}-[A-Z0-9]{4}$", code)
        return code, out
    monkeypatch.setattr(agent_setup, "install", install)
    return port, seen, procs


def test_set_up_a_pi_in_one_step(qapp, window, store, tmp_path, monkeypatch):
    from ui.controllers.agent_controller import SetupDialog
    port, seen, procs = _local_pi(tmp_path, monkeypatch)
    w = window
    try:
        dlg = SetupDialog(w._agents, w)
        dlg.host.setText("127.0.0.1")
        dlg.user.setText("pi")
        dlg.password.setText("raspberry")
        dlg.agent_port.setValue(port)
        dlg._start("")
        assert not dlg.go.isEnabled()
        dlg.reject()                                   # can't close mid-install
        assert "Still installing" in dlg.status.text()
        assert pump_until(qapp, lambda: dlg.entry is not None, 30), dlg.status.text()
        assert seen == {"host": "127.0.0.1", "user": "pi", "accept_key": ""}
        assert store.get("127.0.0.1", port)["fingerprint"] == dlg.entry["fingerprint"]
        assert pump_until(qapp, lambda: "Installed isodaq-agent" in w._terminal.toPlainText())
        name, _ = ac.list_ports("127.0.0.1", port, store)
        assert name                                    # paired and answering
    finally:
        for p in procs:
            p.terminate()
            p.wait(10)


def test_set_up_a_pi_wrong_password(qapp, window, tmp_path, monkeypatch):
    from ui.controllers.agent_controller import SetupDialog
    port, _, procs = _local_pi(tmp_path, monkeypatch)
    dlg = SetupDialog(window._agents, window)
    dlg.host.setText("127.0.0.1")
    dlg.user.setText("pi")
    dlg.password.setText("wrong")
    dlg.agent_port.setValue(port)
    dlg._start("")
    assert pump_until(qapp, lambda: dlg.go.isEnabled(), 30)
    assert "password" in dlg.status.text() and dlg.entry is None
    assert procs == []


def test_set_up_asks_about_a_new_ssh_host(qapp, window, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from core import agent_setup
    from ui.controllers.agent_controller import SetupDialog
    tries = []

    def session(host, port, user, password, key_path, accept_key):
        tries.append(accept_key)
        if not accept_key:
            raise agent_setup.UnknownHostKey(host, "ssh-ed25519", "SHA256:abc")
        raise agent_setup.SetupError("stop here")
    monkeypatch.setattr(agent_setup, "SSHSession", session)
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Yes)
    dlg = SetupDialog(window._agents, window)
    dlg.host.setText("lab-pi.local")
    dlg._start("")
    assert pump_until(qapp, lambda: len(tries) == 2 and dlg.go.isEnabled(), 10)
    assert tries == ["", "SHA256:abc"]
    assert "SHA256:abc" in asked[0] and "ssh_host_ed25519_key.pub" in asked[0]
    assert "stop here" in dlg.status.text()


def test_set_up_checks_fields_first(qapp, window):
    from ui.controllers.agent_controller import SetupDialog
    dlg = SetupDialog(window._agents, window)
    dlg._start("")
    assert "address" in dlg.status.text()
    dlg.host.setText("pi")
    dlg.more_btn.setChecked(True)
    dlg.name.setText("lab pi")
    dlg._start("")
    assert "letters, digits" in dlg.status.text()
    assert dlg.go.isEnabled()
