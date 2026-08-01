"""Tests for the transport family: shared reader interface, the network
(``socket://`` / ``rfc2217://`` / ``loop://``) reader, and the SSH reader's
optional-dependency fallback.

Needs PyQt6 + pyserial (skipped otherwise, like the main-window smoke test). Uses
``loop://`` — pyserial's in-memory loopback — so the network round-trip runs with
no hardware or sockets. A ``QCoreApplication`` event loop is pumped so the
reader thread's queued signals are delivered.
"""
import time

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("serial")

from core.net_reader import NetworkReader           # noqa: E402
from core.serial_reader import SerialReader         # noqa: E402
from core.ssh_reader import SSHReader               # noqa: E402
from core.stream_reader import StreamReader         # noqa: E402

try:
    import paramiko  # noqa: F401
    _HAS_PARAMIKO = True
except ImportError:
    _HAS_PARAMIKO = False


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


def _pump(app, predicate, timeout=3.0) -> bool:
    """Process events until *predicate* is true or *timeout* elapses."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


# ── Interface ──────────────────────────────────────────────────────────────────

def test_readers_share_the_stream_interface():
    # Serial + Network are StreamReader subclasses; SSH stands alone but mirrors
    # the same public surface the controller relies on.
    assert issubclass(SerialReader, StreamReader)
    assert issubclass(NetworkReader, StreamReader)

    for cls in (SerialReader, NetworkReader, SSHReader):
        for sig in ("line_received", "error_occurred", "connected", "disconnected"):
            assert hasattr(cls, sig), f"{cls.__name__} missing signal {sig}"
        for meth in ("configure", "connect_port", "disconnect_port", "send"):
            assert callable(getattr(cls, meth)), f"{cls.__name__} missing {meth}"


def test_network_configure_normalises_bare_hostport():
    r = NetworkReader()
    r.configure(url="192.168.1.50:23")
    assert r._url == "socket://192.168.1.50:23"
    r.configure(url="rfc2217://host:4001")
    assert r._url == "rfc2217://host:4001"


def test_network_connect_without_target_errors():
    r = NetworkReader()
    assert r.connect_port() == "No network target set"


# ── Network round-trip over loop:// ─────────────────────────────────────────────

def test_network_reader_loop_roundtrip(qapp):
    r = NetworkReader()
    r.configure(url="loop://")

    lines: list[str] = []
    connected: list[str] = []
    r.line_received.connect(lambda s, ts: lines.append(s))
    r.connected.connect(lambda label, baud: connected.append(label))

    assert r.connect_port() is None
    try:
        assert _pump(qapp, lambda: bool(connected), 3.0), "never emitted connected"
        assert connected[0] == "loop://"

        assert r.send(b"hello-net\n") is None
        assert _pump(qapp, lambda: "hello-net" in lines, 3.0), f"no echo; got {lines}"
    finally:
        r.disconnect_port()
    assert _pump(qapp, lambda: not r.isRunning(), 2.0)


# ── SSH optional-dependency fallback ────────────────────────────────────────────

@pytest.mark.skipif(_HAS_PARAMIKO, reason="tests the missing-paramiko fallback path")
def test_ssh_reader_reports_missing_paramiko(qapp):
    r = SSHReader()
    r.configure(host="localhost", username="tester")

    errors: list[str] = []
    disc: list[bool] = []
    r.error_occurred.connect(lambda m: errors.append(m))
    r.disconnected.connect(lambda: disc.append(True))

    assert r.connect_port() is None
    assert _pump(qapp, lambda: bool(errors) and bool(disc), 3.0)
    assert "paramiko" in errors[0].lower()


def test_ssh_connect_without_host_errors():
    r = SSHReader()
    assert r.connect_port() == "No SSH host set"
