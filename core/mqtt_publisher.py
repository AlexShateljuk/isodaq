"""
core/mqtt_publisher.py — publish a connection's data to a broker (CMP17).

The way out to Grafana, Home Assistant, Node-RED and friends. Under a base topic::

    <base>/temp               21.5                    (parsed channels, Quick Plot)
    <base>/event/Brownout     {"trigger":…, "line":…, "ts":…}   (trigger matches)

A channel goes out at most ``PUBLISH_MS`` apart, always with its latest value, so a
thousand lines a second don't become a thousand messages a second per channel; the
log file is the complete record. Trigger events go out as they happen.

Runs on paho's own network thread with its automatic reconnect: the connection
never blocks the window, and a broker outage only costs the messages sent during it.
"""
from __future__ import annotations

import json
import re
import uuid

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from core.mqtt_reader import parse_mqtt_url

PUBLISH_MS = 100
DEFAULT_BASE_TOPIC = "isodaq"
_NOT_IN_TOPIC = re.compile(r"[+#\s]+")


def topic_part(name: str) -> str:
    """A name as a topic level: wildcards and spaces have no place in a topic."""
    return _NOT_IN_TOPIC.sub("_", name.strip()).strip("/") or "_"


class MqttPublisher(QObject):
    #: Human-readable state, for the panel. May be emitted from paho's thread.
    status = pyqtSignal(str)

    def __init__(self, url: str, password: str = "", parent=None):
        """Raises ``ValueError`` for an unusable URL."""
        super().__init__(parent)
        self._target = parse_mqtt_url(url)
        topics = [t for t in self._target.topics if t != "#"]
        if any("+" in t or "#" in t for t in topics):
            raise ValueError("The base topic to publish under can't contain wildcards.")
        self.base = (topics[0] if topics else DEFAULT_BASE_TOPIC).strip("/")
        self._password = password
        self._client = None
        self._pending: dict[str, float] = {}
        self.sent = 0
        self._timer = QTimer(self, interval=PUBLISH_MS)
        self._timer.timeout.connect(self._flush)

    def start(self) -> None:
        """Connect in the background. Raises ``ImportError`` without paho."""
        import paho.mqtt.client as mqtt
        t = self._target
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                             client_id=f"isodaq-pub-{uuid.uuid4().hex[:10]}")
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.reconnect_delay_set(min_delay=1, max_delay=15)
        if t.username:
            client.username_pw_set(t.username, self._password or None)
        if t.tls:
            client.tls_set()
        self._client = client
        self.status.emit(f"Connecting to {t.host}:{t.port}…")
        client.connect_async(t.host, t.port, keepalive=30)
        client.loop_start()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._flush()
        client, self._client = self._client, None
        if client is not None:
            client.disconnect()
            client.loop_stop()

    def _on_connect(self, _client, _userdata, _flags, reason, _properties=None) -> None:
        if reason.is_failure:
            self.status.emit(f"Broker refused the connection: {reason}")
        else:
            t = self._target
            self.status.emit(f"Publishing to {t.host}:{t.port} under {self.base}/")

    def _on_disconnect(self, _client, _userdata, _flags, _reason, _properties=None) -> None:
        if self._client is not None:
            self.status.emit("Broker unreachable, retrying… (messages are skipped meanwhile)")

    # ── Data (GUI thread) ─────────────────────────────────────────────────────

    def publish_values(self, values: dict[str, float]) -> None:
        self._pending.update(values)

    def publish_event(self, trigger: str, line: str, ts: str) -> None:
        self._publish(f"{self.base}/event/{topic_part(trigger)}",
                      json.dumps({"trigger": trigger, "line": line, "ts": ts}))

    def _flush(self) -> None:
        pending, self._pending = self._pending, {}
        for name, value in pending.items():
            self._publish(f"{self.base}/{topic_part(name)}", f"{value:g}")

    def _publish(self, topic: str, payload: str) -> None:
        client = self._client
        if client is not None and client.publish(topic, payload).rc == 0:
            self.sent += 1
