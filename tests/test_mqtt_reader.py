"""MQTT transport against a real-protocol broker on localhost."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("paho.mqtt")

from fake_broker import FakeBroker

from core.mqtt_reader import MQTTReader, message_line, parse_mqtt_url


# ── URL ─────────────────────────────────────────────────────────────────────────

def test_parse_full_url():
    t = parse_mqtt_url("mqtts://ann@broker.local:1999/a/#,b/+/c?tx=cmd/x")
    assert (t.host, t.port, t.tls, t.username) == ("broker.local", 1999, True, "ann")
    assert t.topics == ["a/#", "b/+/c"] and t.tx_topic == "cmd/x"


def test_parse_defaults():
    t = parse_mqtt_url("broker.local")
    assert (t.host, t.port, t.tls, t.topics, t.tx_topic) == ("broker.local", 1883, False, ["#"], "")
    assert parse_mqtt_url("mqtts://h").port == 8883
    assert parse_mqtt_url("mqtt://h/sensors/#").topics == ["sensors/#"]    # not a URL fragment


@pytest.mark.parametrize("url, words", [
    ("", "Enter a broker"),
    ("http://h/x", "mqtt://"),
    ("mqtt://ann:secret@h/x", "password"),
    ("mqtt:///x", "no broker host"),
    ("mqtt://h:99999/x", "port"),
    ("mqtt://h/x?tx=a/#", "wildcards"),
])
def test_parse_rejects(url, words):
    with pytest.raises(ValueError, match=words):
        parse_mqtt_url(url)


def test_message_line_is_one_line():
    assert message_line("a/b", b'{\r\n  "x": 1\n}') == b'a/b: {   "x": 1 }\n'


# ── Live ────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


def wait(qapp, cond, sec=5.0):
    end = time.monotonic() + sec
    while time.monotonic() < end and not cond():
        qapp.processEvents()
        time.sleep(0.005)
    assert cond()


class Session:
    def __init__(self, url, password=""):
        self.reader = MQTTReader()
        self.lines, self.errors, self.up, self.down = [], [], [], []
        self.reader.lines_received.connect(lambda b: self.lines.extend(x[0] for x in b))
        self.reader.error_occurred.connect(self.errors.append)
        self.reader.connected.connect(lambda label, _b: self.up.append(label))
        self.reader.disconnected.connect(lambda: self.down.append(1))
        self.reader.configure(url, password)
        assert self.reader.connect_port() is None


@pytest.fixture
def broker():
    b = FakeBroker()
    yield b
    b.stop()


def test_receive_and_publish(qapp, broker):
    s = Session(f"mqtt://127.0.0.1:{broker.port}/sensors/#?tx=cmd/dev")
    wait(qapp, lambda: s.up and broker.subscriptions == ["sensors/#"])
    assert s.up == [f"mqtt://127.0.0.1:{broker.port}/sensors/#"]

    broker.publish("sensors/temp", b"21.5")
    broker.publish("other/ignored", b"x")
    broker.publish("sensors/json", b'{"a": 1}')
    wait(qapp, lambda: len(s.lines) == 2)
    assert s.lines == ["sensors/temp: 21.5", 'sensors/json: {"a": 1}']

    assert s.reader.send(b"RESET\r\n") is None
    wait(qapp, lambda: broker.published)
    assert broker.published == [("cmd/dev", b"RESET")]   # no serial line ending

    s.reader.disconnect_port()
    qapp.processEvents()
    assert s.down == [1] and not s.reader.lost and not s.reader.isRunning()


def test_send_without_a_publish_topic_explains(qapp, broker):
    s = Session(f"mqtt://127.0.0.1:{broker.port}/x")
    wait(qapp, lambda: s.up)
    assert "?tx=" in s.reader.send(b"hi")
    s.reader.disconnect_port()


def test_lost_connection_is_flagged(qapp, broker):
    s = Session(f"mqtt://127.0.0.1:{broker.port}/x")
    wait(qapp, lambda: s.up)
    broker.kick_clients()
    wait(qapp, lambda: s.down)
    assert s.reader.lost and "lost" in s.errors[-1]
    wait(qapp, lambda: not s.reader.isRunning())


def test_wrong_password_is_refused_not_lost(qapp):
    b = FakeBroker(password="right")
    try:
        s = Session(f"mqtt://ann@127.0.0.1:{b.port}/x", "wrong")
        wait(qapp, lambda: s.down)
        assert not s.up and not s.reader.lost
        assert any("refused" in e for e in s.errors)
        assert s.reader.send(b"x") == "Not connected"

        ok = Session(f"mqtt://ann@127.0.0.1:{b.port}/x", "right")
        wait(qapp, lambda: ok.up)
        ok.reader.disconnect_port()
    finally:
        b.stop()


def test_unreachable_broker_is_an_error(qapp):
    b = FakeBroker()
    port = b.port
    b.stop()
    s = Session(f"mqtt://127.0.0.1:{port}/x")
    wait(qapp, lambda: s.down)
    assert not s.reader.lost and "reach" in s.errors[-1]


# ── Through the real window ─────────────────────────────────────────────────────

@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    pytest.importorskip("pyqtgraph")
    from ui.controllers.update_manager import UpdateManager
    from ui.main_window import MainWindow
    monkeypatch.setattr(MainWindow, "_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(UpdateManager, "start", lambda self: None)
    w = MainWindow()
    yield w
    w.close()


def _term(win):
    win._flush_terminals()
    return win._terminal.toPlainText()


def test_window_end_to_end(qapp, win, broker):
    conn = win._active_conn
    win._transport_combo.setCurrentText("MQTT")
    url = f"mqtt://127.0.0.1:{broker.port}/sensors/#?tx=cmd/dev"
    win._port_combo.setEditText(url)
    win._serial.toggle_connection()
    wait(qapp, lambda: broker.subscriptions)
    wait(qapp, lambda: "Connected" in _term(win))
    assert conn.name == f"mqtt://127.0.0.1:{broker.port}/sensors/#"
    assert not conn.short_name.startswith("mqtt") and len(conn.short_name) <= 24
    assert not win._baud_combo.isEnabled()

    for i in range(5):
        broker.publish("sensors/temp", str(20 + i).encode())
    broker.publish("sensors/state", b"running")
    wait(qapp, lambda: "sensors/state: running" in _term(win))
    assert list(conn.chart._channels["sensors/temp"]["buf_v"]) == [20, 21, 22, 23, 24]  # Quick Plot
    assert "sensors/state" not in conn.chart._channels                                  # text

    win._cmd_edit.setText("RESET")
    win._serial.send_command()
    wait(qapp, lambda: broker.published)
    assert broker.published == [("cmd/dev", b"RESET")]
    win._serial.toggle_connection()
    wait(qapp, lambda: not conn.reader.isRunning())


def test_auto_reconnect_to_a_restarted_broker(qapp, win):
    b = FakeBroker()
    port = b.port
    conn = win._active_conn
    win._transport_combo.setCurrentText("MQTT")
    win._port_combo.setEditText(f"mqtt://127.0.0.1:{port}/x")
    win._serial.toggle_connection()
    wait(qapp, lambda: b.subscriptions)
    b.stop()
    wait(qapp, lambda: conn.reconnecting)
    errors = conn.errors
    b = FakeBroker(port=port)
    try:
        wait(qapp, lambda: b.subscriptions and not conn.reconnecting, sec=12)
        assert conn.errors == errors       # failed retries don't count as errors
        assert "Reconnected" in _term(win)
    finally:
        win._serial.toggle_connection()
        wait(qapp, lambda: not conn.reader.isRunning())
        b.stop()


def test_password_is_prompted_once_and_never_saved(qapp, win, monkeypatch):
    import json
    b = FakeBroker(password="s3cret")
    asked = []
    monkeypatch.setattr(win._serial, "_prompt_ssh_password",
                        lambda p: asked.append(p) or "s3cret")
    conn = win._active_conn
    win._transport_combo.setCurrentText("MQTT")
    win._port_combo.setEditText(f"mqtt://ann@127.0.0.1:{b.port}/x")
    try:
        for _ in range(2):
            win._serial.toggle_connection()
            wait(qapp, lambda: "Connected" in _term(win))
            win._serial.toggle_connection()
            wait(qapp, lambda: not conn.reader.isRunning())
            win._terminal.clear()
            conn.term_pending.clear()
        assert len(asked) == 1
        win._settings.save()
        saved = win._CONFIG_PATH.read_text()
        assert "s3cret" not in saved
        assert json.loads(saved)["mqtt_url"].startswith("mqtt://ann@")
    finally:
        b.stop()


def test_bad_url_is_reported_and_not_connected(qapp, win):
    win._transport_combo.setCurrentText("MQTT")
    win._port_combo.setEditText("mqtt://ann:pw@h/x")
    win._serial.toggle_connection()
    assert not win._active_conn.reader.isRunning() and "password" in _term(win)


def test_typed_targets_survive_switching_transports(qapp, win):
    win._transport_combo.setCurrentText("Network")
    win._port_combo.setEditText("socket://1.2.3.4:5")
    win._transport_combo.setCurrentText("MQTT")
    assert win._port_combo.currentText() == ""
    win._port_combo.setEditText("mqtt://h/x")
    win._transport_combo.setCurrentText("BLE")
    win._port_combo.setEditText("Board [AA:BB]")
    win._transport_combo.setCurrentText("Network")
    assert win._port_combo.currentText() == "socket://1.2.3.4:5"
    win._transport_combo.setCurrentText("MQTT")
    assert win._port_combo.currentText() == "mqtt://h/x"
    win._transport_combo.setCurrentText("BLE")
    assert win._port_combo.currentText() == "Board [AA:BB]"
    win._settings.save()


# ── Publishing a connection's data ──────────────────────────────────────────────

def test_publisher_unit(qapp, broker):
    import json

    from core.mqtt_publisher import MqttPublisher, topic_part
    assert topic_part(" a b+c#/ ") == "a_b_c_"
    statuses = []
    pub = MqttPublisher(f"mqtt://127.0.0.1:{broker.port}/bench1")
    pub.status.connect(statuses.append)
    assert pub.base == "bench1"
    pub.start()
    wait(qapp, lambda: any("Publishing" in s for s in statuses))

    for v in range(100):                   # a burst: only the latest goes out
        pub.publish_values({"temp": float(v), "my rpm": 7.5})
    pub.publish_event("Brown out", "E: vdd=2.7", "10:00:00.000")
    wait(qapp, lambda: len(broker.published) >= 3)
    got = dict(broker.published)
    assert got["bench1/temp"] == b"99" and got["bench1/my_rpm"] == b"7.5"
    assert sum(t == "bench1/temp" for t, _ in broker.published) == 1
    assert json.loads(got["bench1/event/Brown_out"]) == {
        "trigger": "Brown out", "line": "E: vdd=2.7", "ts": "10:00:00.000"}

    pub.publish_values({"last": 1.0})      # stop() sends what is waiting
    pub.stop()
    wait(qapp, lambda: ("bench1/last", b"1") in broker.published)


@pytest.mark.parametrize("url", ["mqtt://h/a/+/b", "mqtt://ann:pw@h/a", ""])
def test_publisher_rejects_bad_urls(qapp, url):
    from core.mqtt_publisher import MqttPublisher
    with pytest.raises(ValueError):
        MqttPublisher(url)


def test_publisher_default_base_and_unreachable_broker(qapp):
    from core.mqtt_publisher import MqttPublisher
    b = FakeBroker()
    port = b.port
    b.stop()
    pub = MqttPublisher(f"mqtt://127.0.0.1:{port}")
    assert pub.base == "isodaq"
    pub.start()                            # doesn't block or raise
    pub.publish_values({"x": 1.0})
    t0 = time.monotonic()
    while time.monotonic() - t0 < 0.3:
        qapp.processEvents()
    pub.stop()


def test_window_publishes_channels_and_trigger_events(qapp, win, broker):
    from core.data_parser import ChannelConfig
    from core.triggers import Trigger
    conn = win._active_conn
    conn.parser.add_channel(ChannelConfig(name="temp", key="temp"))
    conn.engine.add_trigger(Trigger("Fault", "FAULT", action_flash=False))
    panel = conn.logger_panel

    win._on_conn_line(conn, "temp=1", "00:00:00.000")   # off: nothing goes out
    panel.mqtt_url.setText(f"mqtt://127.0.0.1:{broker.port}/lab")
    panel.chk_mqtt.setChecked(True)
    assert conn.publisher is not None and not panel.mqtt_url.isEnabled()
    wait(qapp, lambda: "Publishing" in panel.mqtt_status.text())
    assert broker.published == []

    win._on_conn_line(conn, "temp=21.5", "00:00:01.000")
    win._on_conn_line(conn, "rpm:1500 volts:3.3", "00:00:01.100")   # Quick Plot values too
    win._on_conn_line(conn, "FAULT detected", "00:00:01.200")
    wait(qapp, lambda: len(broker.published) >= 4)
    got = dict(broker.published)
    assert got["lab/temp"] == b"21.5" and got["lab/rpm"] == b"1500" and got["lab/volts"] == b"3.3"
    assert b"FAULT detected" in got["lab/event/Fault"]

    win._settings.save()
    saved = win._CONFIG_PATH.read_text()
    assert f"127.0.0.1:{broker.port}/lab" in saved
    panel.chk_mqtt.setChecked(False)
    assert conn.publisher is None and panel.mqtt_url.isEnabled()


def test_window_bad_publish_url_unticks_and_explains(qapp, win):
    panel = win._active_conn.logger_panel
    panel.mqtt_url.setText("mqtt://ann:pw@h/lab")
    panel.chk_mqtt.setChecked(True)
    assert not panel.chk_mqtt.isChecked() and win._active_conn.publisher is None
    assert "password" in panel.mqtt_status.text()


def test_publishing_doesnt_restart_by_itself(qapp, tmp_path, monkeypatch):
    pytest.importorskip("pyqtgraph")
    from ui.controllers.update_manager import UpdateManager
    from ui.main_window import MainWindow
    monkeypatch.setattr(MainWindow, "_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(UpdateManager, "start", lambda self: None)
    (tmp_path / "config.json").write_text('{"mqtt_publish_url": "mqtt://h/lab"}')
    w = MainWindow()
    try:
        panel = w._active_conn.logger_panel
        assert panel.mqtt_url.text() == "mqtt://h/lab"
        assert not panel.chk_mqtt.isChecked() and w._active_conn.publisher is None
    finally:
        w.close()
