"""Tests for the shared reader line handling (``core.stream_reader``):
line splitting, the line-length cap, raw-byte accounting for the HEX view, and
control-character cleanup — the paths that flash / binary dumps exercise.
"""
import pytest

pytest.importorskip("PyQt6")

from core.stream_reader import LineSplitter, deliver  # noqa: E402


class _FakeReader:
    """Captures what :func:`deliver` emits, without a Qt event loop."""

    def __init__(self):
        self.batches = []
        self.lines_received = self
        self.line_received = object()

    def emit(self, batch):
        self.batches.append(batch)

    def receivers(self, _signal):
        return 0


def test_splits_lines_and_keeps_terminator():
    sp = LineSplitter(4096)
    assert sp.feed(b"a=1\r\nb=2\npart") == [b"a=1\r\n", b"b=2\n"]
    assert sp.feed(b"ial\n") == [b"partial\n"]


def test_long_run_without_newline_is_capped():
    sp = LineSplitter(1024)
    out = sp.feed(b"x" * 5000)
    assert [len(p) for p in out] == [1024] * 4
    assert len(sp.buf) == 5000 - 4096   # remainder waits for more data


def test_partial_line_flushes_after_silence():
    sp = LineSplitter(4096)
    sp.feed(b"crash: no newline")
    assert sp.flush_if_idle(10.0) == []          # not silent long enough yet
    assert sp.flush_if_idle(0.0) == [b"crash: no newline"]
    assert sp.buf == b""


def test_deliver_accounts_for_every_byte():
    r = _FakeReader()
    raw = [b"hello\r\n", b"\r\n", b"\n", b"world\n"]
    deliver(r, raw)
    (batch,) = r.batches
    assert [t for t, *_ in batch] == ["hello", "world"]
    # blank lines are not lines, but their bytes ride along with the next one
    assert b"".join(item[3] for item in batch) == b"".join(raw)


def test_deliver_cleans_control_characters():
    r = _FakeReader()
    deliver(r, [b"A\x00B\x07C\tD\n"])
    assert r.batches[0][0][0] == "A·B·C\tD"   # NUL/BEL replaced, TAB kept
