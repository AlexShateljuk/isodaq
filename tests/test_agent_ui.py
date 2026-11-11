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
