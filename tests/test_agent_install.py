"""packaging/agent/install.sh (AGT7), in its test mode: everything goes under one
folder and nothing on the system changes (no user, apt or systemd). Then the
installed `isodaq-agent` command serves, pairs and lists ports for real."""
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("serial")
pytest.importorskip("cryptography")

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "packaging" / "agent" / "install.sh"

pytestmark = pytest.mark.skipif(sys.platform == "win32" or shutil.which("sh") is None,
                                reason="needs a POSIX shell")


def run(root: Path, *args, check=True):
    env = {**os.environ, "ISODAQ_AGENT_ROOT": str(root),
           # The python3 the installer finds must have pyserial + cryptography.
           "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}"}
    return subprocess.run(["sh", str(SCRIPT), *args], env=env, capture_output=True,
                          text=True, check=check, timeout=60)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def root(tmp_path):
    return tmp_path / "pi"


def test_install_layout(root):
    out = run(root, "--port", "7790", "--name", "lab-pi")
    code = re.search(r"code\s+([A-Z0-9]{4}-[A-Z0-9]{4})", out.stdout)
    assert code, out.stdout
    lib = root / "opt/isodaq-agent/isodaq_agent"
    assert sorted(p.name for p in lib.glob("*.py")) == sorted(
        p.name for p in (REPO / "isodaq_agent").glob("*.py"))
    state = root / "var/lib/isodaq-agent"
    assert oct(state.stat().st_mode & 0o777) == "0o700"
    assert (state / "pairing.json").exists() and (state / "cert.pem").exists()
    unit = (root / "etc/systemd/system/isodaq-agent.service").read_text()
    assert not re.search(r"@[A-Z]+@", unit)          # every placeholder filled in
    assert f"--config {state} serve --port 7790 --name lab-pi --allow '/dev/serial/by-id/*'" \
        in unit
    assert f"ReadWritePaths={state}" in unit
    assert os.access(root / "usr/local/bin/isodaq-agent", os.X_OK)


def test_installed_command_serves_and_pairs(root, tmp_path):
    run(root, "--no-code")
    cmd = str(root / "usr/local/bin/isodaq-agent")
    port = free_port()
    agent = subprocess.Popen([cmd, "serve", "--host", "127.0.0.1", "--port", str(port),
                              "--name", "lab-pi"], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
    try:
        code = None
        end = time.monotonic() + 15
        while code is None and time.monotonic() < end:
            line = agent.stdout.readline()
            m = re.search(r"code\s+([A-Z0-9]{4}-[A-Z0-9]{4})", line)
            code = m.group(1) if m else None
        assert code, "the agent printed no pairing code"
        # Wait for it to listen.
        while time.monotonic() < end:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        from core import agent_client as ac
        store = ac.TrustStore(tmp_path / "agents.json")
        entry = ac.pair("127.0.0.1", port, code, store, name="bench-mac")
        assert entry["name"] == "lab-pi"
        assert ac.list_ports("127.0.0.1", port, store)[0] == "lab-pi"
        listed = subprocess.run([cmd, "clients"], capture_output=True, text=True, timeout=30)
        assert "bench-mac" in listed.stdout
        # A code made by the command while the service runs is one it accepts.
        made = subprocess.run([cmd, "pair"], capture_output=True, text=True, timeout=30)
        second = re.search(r"code\s+([A-Z0-9]{4}-[A-Z0-9]{4})", made.stdout).group(1)
        ac.pair("127.0.0.1", port, second, ac.TrustStore(tmp_path / "b.json"), name="other")
    finally:
        agent.terminate()
        agent.wait(10)


def test_reinstall_keeps_pairings(root):
    run(root, "--no-code")
    state = root / "var/lib/isodaq-agent"
    subprocess.run([str(root / "usr/local/bin/isodaq-agent"), "pair"], check=True,
                   capture_output=True, timeout=30)
    cert = (state / "cert.pem").read_text()
    (state / "clients.json").write_text('{"bench": {"token": "x"}}')
    run(root, "--no-code", "--port", "7781")
    assert (state / "cert.pem").read_text() == cert
    assert "bench" in (state / "clients.json").read_text()
    assert "--port 7781" in (root / "etc/systemd/system/isodaq-agent.service").read_text()


def test_uninstall(root):
    run(root, "--no-code")
    out = run(root, "--uninstall")
    assert "kept" in out.stdout
    assert not (root / "opt/isodaq-agent").exists()
    assert not (root / "usr/local/bin/isodaq-agent").exists()
    assert (root / "var/lib/isodaq-agent").exists()
    run(root, "--uninstall", "--purge")
    assert not (root / "var/lib/isodaq-agent").exists()


@pytest.mark.parametrize("args", [["--port", "77x"], ["--name", "lab pi;rm"],
                                  ["--allow", "/dev/x|y"], ["--bogus"]])
def test_bad_options_refused(root, args):
    out = run(root, *args, check=False)
    assert out.returncode == 2
    assert not root.exists()


def test_service_starting_late_keeps_the_installers_code(root, tmp_path):
    # install.sh restarts the service, then runs `pair`. On a slow Pi the service can
    # finish starting after that: it must not replace the code the installer printed.
    out = run(root)
    code = re.search(r"code\s+([A-Z0-9]{4}-[A-Z0-9]{4})", out.stdout).group(1)
    cmd = str(root / "usr/local/bin/isodaq-agent")
    port = free_port()
    agent = subprocess.Popen([cmd, "serve", "--host", "127.0.0.1", "--port", str(port)],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        assert "A pairing code is waiting" in agent.stdout.readline()
        end = time.monotonic() + 15
        while time.monotonic() < end:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        from core import agent_client as ac
        ac.pair("127.0.0.1", port, code, ac.TrustStore(tmp_path / "a.json"))
    finally:
        agent.terminate()
        agent.wait(10)
