"""Setting up a Pi from IsoDAQ (AGT7): the real installer runs through a local
stand-in for the SSH session, with a fake sudo that wants a password."""
import os
import shutil
import site
import stat
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("serial")
pytest.importorskip("cryptography")

from core import agent_setup                      # noqa: E402
from isodaq_agent import protocol, security       # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform == "win32" or shutil.which("sh") is None,
                                reason="needs a POSIX shell")

FAKE_SUDO = """#!/bin/sh
# Stand-in for sudo: -n fails unless passwordless; -S reads the password from stdin.
if [ "$1" = "-n" ]; then
    [ -n "$PASSWORDLESS" ] && { shift; exec "$@"; }
    echo "sudo: a password is required" >&2; exit 1
fi
[ "$1" = "-S" ] && shift
[ "$1" = "-p" ] && shift 2
if [ -z "$PASSWORDLESS" ]; then
    read -r pw
    [ "$pw" = "raspberry" ] || { echo "sudo: 1 incorrect password attempt" >&2; exit 1; }
fi
# What reaches the command's stdin, so the test can see a password didn't leak.
cat > "$HOME/stdin-seen"
exec "$@" < /dev/null
"""


class LocalSession:
    """put_files / run on this machine: *home* is the Pi user's home folder."""

    def __init__(self, home: Path, root: Path, bin_dir: Path, passwordless=False):
        self.home, self.root, self.bin_dir = home, root, bin_dir
        self.passwordless = passwordless
        self.commands = []

    def close(self):
        pass

    def put_files(self, folder, files):
        for rel, data in files.items():
            path = self.home / folder / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def run(self, command, stdin="", timeout=60):
        self.commands.append(command)
        env = {**os.environ, "HOME": str(self.home), "ISODAQ_AGENT_ROOT": str(self.root),
               # A different HOME would hide packages installed with pip --user.
               "PYTHONUSERBASE": site.getuserbase(),
               "PATH": f"{self.bin_dir}{os.pathsep}{Path(sys.executable).parent}"
                       f"{os.pathsep}{os.environ['PATH']}"}
        if self.passwordless:
            env["PASSWORDLESS"] = "1"
        p = subprocess.run(["sh", "-c", command], cwd=self.home, env=env, input=stdin,
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout + p.stderr


@pytest.fixture
def pi(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sudo = bin_dir / "sudo"
    sudo.write_text(FAKE_SUDO)
    sudo.chmod(sudo.stat().st_mode | stat.S_IEXEC)
    home = tmp_path / "home"
    home.mkdir()
    return home, tmp_path / "root", bin_dir


def _code_is_valid(root: Path, code: str) -> bool:
    state = root / "var/lib/isodaq-agent"
    fp = security.certificate_fingerprint(state / "cert.pem")
    proof = protocol.pair_proof(protocol.code_key(code), fp)
    return security.Pairing(state).verify(proof, fp) == "ok"


def test_install_with_a_sudo_password(pi):
    home, root, bin_dir = pi
    session = LocalSession(home, root, bin_dir)
    steps = []
    code, output = agent_setup.install(session, 7790, "lab-pi", "raspberry", steps.append)
    assert _code_is_valid(root, code)
    assert steps[0].startswith("Copying")
    assert "--port 7790 --name lab-pi" in (root / "etc/systemd/system/"
                                           "isodaq-agent.service").read_text()
    assert (home / "stdin-seen").read_text() == ""      # the password went to sudo only
    assert not (home / agent_setup.REMOTE_DIR).exists()  # cleaned up


def test_passwordless_sudo_gets_no_password(pi):
    home, root, bin_dir = pi
    session = LocalSession(home, root, bin_dir, passwordless=True)
    code, _ = agent_setup.install(session, password="raspberry")
    assert _code_is_valid(root, code)
    assert (home / "stdin-seen").read_text() == ""


def test_wrong_sudo_password(pi):
    home, root, bin_dir = pi
    with pytest.raises(agent_setup.SetupError) as e:
        agent_setup.install(LocalSession(home, root, bin_dir), password="wrong")
    assert "password" in str(e.value)
    assert not (home / agent_setup.REMOTE_DIR).exists()


def test_no_password_when_sudo_wants_one(pi):
    home, root, bin_dir = pi
    session = LocalSession(home, root, bin_dir)
    with pytest.raises(agent_setup.SetupError) as e:
        agent_setup.install(session)
    assert "needs this user's password" in str(e.value)
    assert len(session.commands) == 1                   # nothing was run with sudo -S


@pytest.mark.parametrize("name, port", [("lab pi", 7780), ("x;reboot", 7780),
                                        ("ok", 0), ("ok", 70000)])
def test_bad_name_or_port(pi, name, port):
    home, root, bin_dir = pi
    session = LocalSession(home, root, bin_dir, passwordless=True)
    with pytest.raises(agent_setup.SetupError):
        agent_setup.install(session, port, name)
    assert session.commands == []


def test_bundle_has_everything_the_installer_needs():
    files = agent_setup.bundle()
    assert {"install.sh", "isodaq-agent.service", "isodaq_agent/server.py",
            "isodaq_agent/__main__.py"} <= set(files)


def test_ssh_unknown_host_key_is_reported(monkeypatch):
    paramiko = pytest.importorskip("paramiko")
    key = paramiko.ECDSAKey.generate()
    seen = {}

    def fake_connect(self, **kwargs):
        policy = self._policy
        try:
            policy.missing_host_key(self, kwargs["hostname"], key)
        except agent_setup.UnknownHostKey as e:
            seen["error"] = e
            raise
    monkeypatch.setattr(paramiko.SSHClient, "connect", fake_connect)
    monkeypatch.setattr(paramiko.SSHClient, "load_system_host_keys", lambda self: None)
    with pytest.raises(agent_setup.UnknownHostKey) as e:
        agent_setup.SSHSession("lab-pi.local", username="pi", password="x")
    fp = agent_setup.key_fingerprint(key)
    assert e.value.fingerprint == fp and fp.startswith("SHA256:")
    # Confirmed by the user: accepted.
    agent_setup.SSHSession("lab-pi.local", username="pi", password="x", accept_key=fp)


def test_ssh_changed_host_key_is_refused(monkeypatch):
    paramiko = pytest.importorskip("paramiko")
    key = paramiko.ECDSAKey.generate()

    def fake_connect(self, **kwargs):
        raise paramiko.BadHostKeyException(kwargs["hostname"], key, paramiko.ECDSAKey.generate())
    monkeypatch.setattr(paramiko.SSHClient, "connect", fake_connect)
    with pytest.raises(agent_setup.SetupError) as e:
        agent_setup.SSHSession("lab-pi.local", username="pi")
    assert "doesn't match" in str(e.value) and "ssh-keygen -R lab-pi.local" in str(e.value)
