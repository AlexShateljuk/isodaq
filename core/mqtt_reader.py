"""
core/mqtt_reader.py — MQTT transport (CMP17): a broker as a data source.

Subscribes to topics and shows each message as one terminal line,
``topic: payload``, so the parser, triggers, logger and Quick Plot all work on it
unchanged (``sensors/temp: 21.5`` plots by itself). What you send is published to
one topic.

Target URL::

    mqtt://[user@]host[:port]/topic/filter[,other/#][?tx=publish/topic]
    mqtts://…                TLS (default port 8883)

Passwords don't go in the URL, because targets are saved in the settings file; the
window asks once per run instead. ``paho-mqtt`` is imported lazily, like paramiko.

Same signals and ``configure`` / ``connect_port`` / ``disconnect_port`` / ``send``
surface as the other readers.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from PyQt6.QtCore import QThread, pyqtSignal

from core.stream_reader import LineSplitter, StreamReader, deliver

CONNECT_TIMEOUT_S = 8


@dataclass
class MqttTarget:
    host: str
    port: int
    tls: bool = False
    username: str = ""
    topics: list[str] = field(default_factory=lambda: ["#"])
    tx_topic: str = ""

    @property
    def label(self) -> str:
        scheme = "mqtts" if self.tls else "mqtt"
        return f"{scheme}://{self.host}:{self.port}/{','.join(self.topics)}"


def parse_mqtt_url(url: str) -> MqttTarget:
    """Parse a target URL; raises ``ValueError`` with a message for the user.
    By hand, because ``#`` (the MQTT wildcard) is a fragment to ``urllib``."""
    text = (url or "").strip()
    if not text:
        raise ValueError("Enter a broker, e.g. mqtt://broker.local/sensors/#")
    scheme, sep, rest = text.partition("://")
    if not sep:
        scheme, rest = "mqtt", text
    if scheme not in ("mqtt", "mqtts"):
        raise ValueError(f"Use mqtt:// or mqtts://, not {scheme}://")
    tls = scheme == "mqtts"

    rest, _, query = rest.partition("?")
    authority, _, filters = rest.partition("/")
    user = ""
    if "@" in authority:
        user, _, authority = authority.rpartition("@")
        if ":" in user:
            raise ValueError("Leave the password out of the URL: it would be saved in "
                             "the settings file. You'll be asked for it when connecting.")
    host, colon, port_text = authority.rpartition(":")
    if not colon:
        host, port_text = authority, ""
    if not host:
        raise ValueError("The MQTT URL has no broker host.")
    if port_text and not (port_text.isdigit() and 0 < int(port_text) < 65536):
        raise ValueError(f"Invalid MQTT port: {port_text}")
    port = int(port_text) if port_text else (8883 if tls else 1883)

    tx_topic = ""
    for part in query.split("&"):
        key, _, value = part.partition("=")
        if key == "tx":
            tx_topic = value
    if "+" in tx_topic or "#" in tx_topic:
        raise ValueError("The publish topic (tx=) can't contain wildcards.")
    topics = [t.strip() for t in filters.split(",") if t.strip()] or ["#"]
    return MqttTarget(host, port, tls, user, topics, tx_topic)


def message_line(topic: str, payload: bytes) -> bytes:
    """One terminal line for a message: pretty-printed JSON stays on one line, so a
    message is one event for triggers and one row in the log."""
    return topic.encode() + b": " + payload.replace(b"\r", b"").replace(b"\n", b" ") + b"\n"


class MQTTReader(QThread):
    line_received  = pyqtSignal(str, str)
    lines_received = pyqtSignal(object)
    error_occurred = pyqtSignal(str)
    connected      = pyqtSignal(str, int)
    disconnected   = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._disconnected_emitted = False
        self.lost = False
        self._target: MqttTarget | None = None
        self._password = ""
        self._client = None
        self._online = False
        self._pending: list[bytes] = []
        self._splitter = LineSplitter(StreamReader.MAX_LINE_BYTES)

    def configure(self, url: str, password: str = "") -> None:
        self._target = parse_mqtt_url(url)
        self._password = password

    # ── Connect / disconnect ──────────────────────────────────────────────────

    def connect_port(self) -> str | None:
        if self._target is None:
            return "No MQTT broker set"
        self._disconnected_emitted = False
        self.lost = False
        self._online = False
        self._running = True
        self.start()                     # the TCP connect blocks: it runs in run()
        return None

    def disconnect_port(self) -> None:
        self._running = False
        self.wait(CONNECT_TIMEOUT_S * 1000 + 1000)
        if not self._disconnected_emitted:
            self.disconnected.emit()
        self._disconnected_emitted = False

    def send(self, data: bytes) -> str | None:
        """Publish *data*. The command bar's line ending is for serial devices:
        in MQTT the message boundary is the message itself, so it is dropped."""
        client = self._client
        if client is None or not self._online:
            return "Not connected"
        if not self._target.tx_topic:
            return "No topic to publish to: add ?tx=your/topic to the MQTT URL"
        info = client.publish(self._target.tx_topic, data.rstrip(b"\r\n"))
        if info.rc != 0:
            return f"Publish failed (code {info.rc})"
        return None

    @staticmethod
    def set_lines(dtr=None, rts=None) -> str | None:   # pylint: disable=unused-argument
        return "DTR/RTS are not available over MQTT"

    @staticmethod
    def set_break(on: bool) -> str | None:   # pylint: disable=unused-argument
        return "BREAK is not available over MQTT"

    # ── Worker thread ─────────────────────────────────────────────────────────

    def run(self) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            self.error_occurred.emit("MQTT needs the 'paho-mqtt' package.\n"
                                     "  Install it:  pip install paho-mqtt")
            self._finish(lost=False)
            return

        t = self._target
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                             client_id=f"isodaq-{uuid.uuid4().hex[:10]}")
        client.connect_timeout = CONNECT_TIMEOUT_S
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        try:
            if t.username:
                client.username_pw_set(t.username, self._password or None)
            if t.tls:
                client.tls_set()
            client.connect(t.host, t.port, keepalive=30)
        except Exception as e:
            self.error_occurred.emit(f"Could not reach the MQTT broker: {e}")
            self._finish(lost=False)
            return

        self._client = client
        rc = 0
        # loop() rather than loop_start(): received messages go to the window as
        # one batch per pass, as the other readers do per read.
        while self._running and rc == 0:
            rc = client.loop(timeout=0.05)
            if self._pending:
                batch, self._pending = self._pending, []
                deliver(self, batch)

        was_online, self._online, self._client = self._online, False, None
        stopped_by_user = not self._running
        try:
            client.disconnect()
        except Exception:
            pass
        if stopped_by_user:
            return                       # disconnect_port() finishes up
        if was_online:
            self.error_occurred.emit("MQTT connection lost")
        self._finish(lost=was_online)

    def _finish(self, lost: bool) -> None:
        self._running = False
        self.lost = lost
        self._disconnected_emitted = True
        self.disconnected.emit()

    # paho callbacks: on this thread, from inside client.loop().

    def _on_connect(self, client, _userdata, _flags, reason, _properties=None) -> None:
        if reason.is_failure:
            self.error_occurred.emit(f"The MQTT broker refused the connection: {reason}")
            client.disconnect()
            self._online = False
            return
        for topic in self._target.topics:
            client.subscribe(topic)
        self._online = True
        self.connected.emit(self._target.label, 0)

    def _on_message(self, _client, _userdata, msg) -> None:
        self._pending.extend(self._splitter.feed(message_line(msg.topic, msg.payload)))
