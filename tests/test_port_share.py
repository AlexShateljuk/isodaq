"""Sharing a connection over TCP (CMP22): real sockets, raw bytes both ways."""
import os
import socket
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt  # noqa: E402

from core.port_share import LOCAL_HOST, MAX_CLIENT_BACKLOG, MAX_CLIENTS, PortShare  # noqa: E402


def wait_for(cond, sec=3.0):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return cond()


def client(share):
    c = socket.create_connection((LOCAL_HOST, share.port), timeout=3)
    c.settimeout(3)
    return c


def read_exactly(sock, n):
    out = b""
    while len(out) < n:
        got = sock.recv(n - len(out))
        assert got, "closed early"
        out += got
    return out


@pytest.fixture
def rig():
    written = []

    def write(data):
        written.append(data)

    share = PortShare(write)
    assert share.start(LOCAL_HOST, 0) is None
    yield share, written
    share.stop()


def test_every_byte_value_passes_unchanged_both_ways(rig):
    share, written = rig
    c = client(share)
    assert wait_for(lambda: share.client_count == 1)
    every = bytes(range(256)) * 40
    share.feed(every)
    assert read_exactly(c, len(every)) == every
    c.sendall(every)
    assert wait_for(lambda: sum(map(len, written)) == len(every))
    assert b"".join(written) == every
    c.close()


def test_all_clients_get_everything(rig):
    share, _ = rig
    a, b = client(share), client(share)
    assert wait_for(lambda: share.client_count == 2)
    share.feed(b"one\n")
    share.feed(b"two\n")
    assert read_exactly(a, 8) == read_exactly(b, 8) == b"one\ntwo\n"
    a.close()
    b.close()


def test_a_closed_client_leaves_the_others_running(rig):
    share, _ = rig
    a, b = client(share), client(share)
    assert wait_for(lambda: share.client_count == 2)
    a.close()
    assert wait_for(lambda: share.client_count == 1)
    share.feed(b"x")
    assert read_exactly(b, 1) == b"x"
    b.close()


def test_client_limit(rig):
    share, _ = rig
    keep = [client(share) for _ in range(MAX_CLIENTS)]
    assert wait_for(lambda: share.client_count == MAX_CLIENTS)
    extra = client(share)
    assert extra.recv(1) == b""          # closed by the server
    assert share.client_count == MAX_CLIENTS
    for c in keep:
        c.close()


def test_a_stalled_client_is_dropped_and_feed_never_blocks(rig):
    share, _ = rig
    drops = []
    share.client_dropped.connect(drops.append, Qt.ConnectionType.DirectConnection)
    stalled, healthy = client(share), client(share)
    assert wait_for(lambda: share.client_count == 2)
    received = bytearray()
    stop = threading.Event()

    def drain():
        healthy.settimeout(0.05)
        while not stop.is_set():
            try:
                received.extend(healthy.recv(65536))
            except socket.timeout:
                pass

    t = threading.Thread(target=drain)
    t.start()
    chunk = b"z" * 65536
    total = MAX_CLIENT_BACKLOG * 3
    fed = 0
    worst = 0.0
    while fed < total:                   # at the pace the healthy client can read
        started = time.monotonic()
        share.feed(chunk)
        worst = max(worst, time.monotonic() - started)
        fed += len(chunk)
        assert wait_for(lambda want=fed: len(received) >= want, 5)
    assert worst < 0.5
    assert wait_for(lambda: share.client_count == 1, 5)
    assert wait_for(lambda: bool(drops))
    stop.set()
    t.join()
    stalled.close()
    healthy.close()


def test_device_stuck_in_write_doesnt_hold_up_what_clients_receive():
    release = threading.Event()

    def stuck(_data):
        release.wait(10)

    share = PortShare(stuck)
    assert share.start(LOCAL_HOST, 0) is None
    c = client(share)
    assert wait_for(lambda: share.client_count == 1)
    c.settimeout(0.5)
    with pytest.raises(socket.timeout):  # TCP slows the client down: memory stays bounded
        c.sendall(b"x" * (1 << 26))
    c.settimeout(3)
    assert share._to_device.qsize() <= share._to_device.maxsize
    share.feed(b"still alive")
    assert read_exactly(c, 11) == b"still alive"
    release.set()
    share.stop()
    c.close()


def test_write_errors_are_reported_not_fatal():
    share = PortShare(lambda _d: "Not connected")
    assert share.start(LOCAL_HOST, 0) is None
    c = client(share)
    c.sendall(b"x")
    assert wait_for(lambda: share._error == "Not connected")
    count, _preview, err = share.take_sent()
    assert (count, err) == (0, "Not connected")
    share.stop()
    c.close()


def test_take_sent_counts_all_and_previews_the_start(rig):
    share, _ = rig
    c = client(share)
    c.sendall(b"y" * 5000)
    assert wait_for(lambda: share._sent == 5000)
    count, preview, err = share.take_sent()
    assert count == 5000 and 0 < len(preview) <= 200 and not err
    assert share.take_sent() == (0, b"", "")
    c.close()


def test_port_in_use_is_an_error_message(rig):
    share, _ = rig
    other = PortShare(lambda _d: None)
    assert other.start(LOCAL_HOST, share.port)
    assert not other.running


def test_stop_closes_clients_and_frees_the_port():
    share = PortShare(lambda _d: None)
    assert share.start(LOCAL_HOST, 0) is None
    port = share.port
    c = client(share)
    assert wait_for(lambda: share.client_count == 1)
    share.stop()
    assert c.recv(1) == b""
    assert share.client_count == 0
    share.feed(b"after stop")            # the reader may still be mid-chunk
    share.stop()                         # twice is fine
    c.close()
    if sys.platform != "win32":
        again = PortShare(lambda _d: None)
        assert again.start(LOCAL_HOST, port) is None
        again.stop()


def test_listens_on_this_computer_only_by_default():
    share = PortShare(lambda _d: None)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    assert share.start(port=free) is None
    assert share._listener.getsockname() == ("127.0.0.1", free)
    share.stop()
