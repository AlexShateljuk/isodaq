"""Placeholder expansion and the environment of the build / flash command (CMP12)."""
import os
import sys

import pytest

pytest.importorskip("PyQt6")

from core import flash_runner
from core.flash_runner import child_env, expand_command, login_shell_argv


def test_placeholders_filled_and_other_braces_kept():
    assert expand_command("idf.py -p {port} -b {baud} flash", "/dev/ttyUSB0", 921600) == \
        "idf.py -p /dev/ttyUSB0 -b 921600 flash"
    assert expand_command("awk '{print $1}' x") == "awk '{print $1}' x"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX quoting")
def test_port_is_quoted_for_the_shell():
    assert expand_command("t {port}", "/dev/my port; rm x") == "t '/dev/my port; rm x'"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shells")
def test_login_shell_selection(monkeypatch, tmp_path):
    monkeypatch.setenv("SHELL", "/bin/sh")
    assert login_shell_argv("x") == ["/bin/sh", "-lc", "x"]
    csh = tmp_path / "csh"                 # rejects -lc → plain sh
    csh.write_text("")
    monkeypatch.setenv("SHELL", str(csh))
    assert login_shell_argv("x") == ["/bin/sh", "-c", "x"]


def test_frozen_build_restores_library_path(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/local/lib")
    monkeypatch.setenv("DYLD_LIBRARY_PATH", "/bundle")
    monkeypatch.delenv("DYLD_LIBRARY_PATH_ORIG", raising=False)
    env = child_env()
    assert env["LD_LIBRARY_PATH"] == "/usr/local/lib"
    assert "DYLD_LIBRARY_PATH" not in env
    assert env["PYTHONUNBUFFERED"] == "1"


def test_unfrozen_environment_is_left_alone(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/mine")
    assert child_env()["LD_LIBRARY_PATH"] == "/mine"
    assert os.environ["LD_LIBRARY_PATH"] == "/mine"


def test_output_lines_split_on_cr_and_strip_colour():
    runner = flash_runner.FlashRunner("true")
    got = []
    runner.output.connect(got.extend)
    buf = bytearray(b"\x1b[32mok\x1b[0m\r\n10%\r20%\rpartial")
    runner._emit_lines(buf, partial=False)
    assert got == ["ok", "10%", "20%"] and bytes(buf) == b"partial"
    runner._emit_lines(buf, partial=True)
    assert got[-1] == "partial" and not buf
