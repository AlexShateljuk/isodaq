"""Install isodaq-agent on a Raspberry Pi over SSH, from IsoDAQ (AGT7).

Copies the agent and ``packaging/agent/install.sh`` to a temporary folder on the Pi,
runs the installer with sudo, and returns the pairing code it prints, so IsoDAQ can
pair straight away. Qt-free; the transport is a small :class:`Session` so the tests
can run the real installer locally.

SSH host keys are checked: a key in ``~/.ssh/known_hosts`` must match, and an unknown
host raises :class:`UnknownHostKey` with its fingerprint for the user to confirm.
The sudo password goes to ``sudo -S`` on stdin, inside the SSH connection; it is
never stored.
"""
from __future__ import annotations

import base64
import hashlib
import re
import shlex
import socket
import sys
import time
from pathlib import Path

from isodaq_agent import protocol

REMOTE_DIR = ".isodaq-agent-setup"
INSTALL_TIMEOUT = 600.0          # apt can take minutes on a Pi Zero
_CODE = re.compile(r"code\s+([A-Z0-9]{4}-[A-Z0-9]{4})")
_NAME = re.compile(r"^[A-Za-z0-9._-]{0,48}$")


class SetupError(Exception):
    """Installing didn't work; the message says why, with the end of the output."""


class UnknownHostKey(SetupError):
    """The SSH host isn't in known_hosts. Confirm *fingerprint*, then try again
    with ``accept_key=fingerprint``."""

    def __init__(self, host: str, key_type: str, fingerprint: str):
        super().__init__(f"{host} isn't a known SSH host. Its {key_type} key is {fingerprint}")
        self.host, self.key_type, self.fingerprint = host, key_type, fingerprint


def _source_root() -> Path:
    """The repository, or the unpacked app bundle (PyInstaller)."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def bundle() -> dict[str, bytes]:
    """The files to copy, by path relative to the setup folder on the Pi."""
    root = _source_root()
    files = {f"isodaq_agent/{p.name}": p.read_bytes()
             for p in sorted((root / "isodaq_agent").glob("*.py"))}
    for name in ("install.sh", "isodaq-agent.service"):
        files[name] = (root / "packaging" / "agent" / name).read_bytes()
    if "isodaq_agent/server.py" not in files:
        raise SetupError("The agent's files aren't part of this copy of IsoDAQ")
    return files


def install_command(agent_port: int, name: str) -> str:
    if not 0 < agent_port < 65536:
        raise SetupError(f"{agent_port} isn't a port number")
    if not _NAME.match(name):
        raise SetupError("The agent name can have letters, digits, '.', '_' and '-'")
    args = ["sh", "install.sh", "--port", str(agent_port)]
    if name:
        args += ["--name", name]
    # -S: password on stdin; -p '': no prompt text mixed into the output.
    return f"cd {REMOTE_DIR} && sudo -S -p '' " + " ".join(shlex.quote(a) for a in args)


def install(session, agent_port: int = protocol.DEFAULT_PORT, name: str = "",
            password: str = "", progress=lambda text: None) -> tuple[str, str]:
    """Install over *session*. Returns ``(pairing code, installer output)``."""
    command = install_command(agent_port, name)
    progress("Copying the agent…")
    session.put_files(REMOTE_DIR, bundle())
    # Only hand sudo the password if it asks for one: with passwordless sudo (the
    # Raspberry Pi OS default) it would reach the installer's stdin instead.
    needs_password = session.run("sudo -n true", timeout=30)[0] != 0
    if needs_password and not password:
        raise SetupError("sudo on the Pi needs this user's password: enter it and try again")
    progress("Installing (this can take a few minutes the first time)…")
    try:
        status, output = session.run(command,
                                     stdin=password + "\n" if needs_password else "",
                                     timeout=INSTALL_TIMEOUT)
    finally:
        try:
            session.run(f"rm -rf {REMOTE_DIR}", timeout=30)
        except Exception:
            pass
    tail = "\n".join(output.strip().splitlines()[-12:])
    if status != 0:
        if "password" in output.lower() and "sudo" in output.lower():
            raise SetupError("sudo needs this user's password (or the password was wrong).\n"
                             + tail)
        raise SetupError(f"The installer stopped (exit {status}):\n{tail}")
    found = _CODE.findall(output)
    if not found:
        raise SetupError(f"The installer didn't print a pairing code:\n{tail}")
    return found[-1], output


# ── SSH ───────────────────────────────────────────────────────────────────────

def key_fingerprint(key) -> str:
    """OpenSSH's form: ``SHA256:…``."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


class SSHSession:
    """A :class:`Session` over paramiko, with host keys checked."""

    def __init__(self, host: str, port: int = 22, username: str = "", password: str = "",
                 key_path: str = "", accept_key: str = "", timeout: float = 15.0):
        import paramiko

        class _Policy(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                fp = key_fingerprint(key)
                if accept_key and fp == accept_key:
                    return               # the user confirmed this key
                raise UnknownHostKey(host, key.get_name(), fp)

        self._client = paramiko.SSHClient()
        try:
            self._client.load_system_host_keys()
        except (OSError, paramiko.SSHException):
            pass
        self._client.set_missing_host_key_policy(_Policy())
        kwargs = {"hostname": host, "port": port, "username": username or None,
                  "timeout": timeout, "banner_timeout": timeout, "auth_timeout": timeout}
        if key_path:
            kwargs["key_filename"] = str(Path(key_path).expanduser())
        if password:
            kwargs["password"] = password
        try:
            self._client.connect(**kwargs)
        except UnknownHostKey:
            raise
        except paramiko.BadHostKeyException as e:
            raise SetupError(
                f"{host}'s SSH key doesn't match the one in ~/.ssh/known_hosts (now "
                f"{key_fingerprint(e.key)}). If the Pi was reinstalled, remove its line "
                "with: ssh-keygen -R " + host) from None
        except paramiko.AuthenticationException:
            raise SetupError(f"{host} refused the user name or password") from None
        except (OSError, paramiko.SSHException) as e:
            raise SetupError(f"Can't connect to {host}:{port} over SSH: {e}") from None

    def put_files(self, folder: str, files: dict[str, bytes]) -> None:
        sftp = self._client.open_sftp()
        try:
            for path in [folder] + sorted({f"{folder}/{p.rsplit('/', 1)[0]}"
                                           for p in files if "/" in p}):
                try:
                    sftp.mkdir(path, 0o700)
                except OSError:
                    pass                  # left over from an earlier attempt
            for rel, data in files.items():
                with sftp.open(f"{folder}/{rel}", "wb") as f:
                    f.write(data)
        finally:
            sftp.close()

    def run(self, command: str, stdin: str = "", timeout: float = 60.0) -> tuple[int, str]:
        chan = self._client.get_transport().open_session()
        chan.set_combine_stderr(True)
        chan.settimeout(timeout)
        chan.exec_command(command)
        if stdin:
            chan.sendall(stdin.encode())
        chan.shutdown_write()
        out = bytearray()
        end = time.monotonic() + timeout
        while True:
            if time.monotonic() > end:
                chan.close()
                raise SetupError(f"No end to '{command.split('&&')[-1].strip()}' after "
                                 f"{int(timeout)} s")
            try:
                data = chan.recv(65536)
            except socket.timeout:
                data = None
            if data is None:
                chan.close()
                raise SetupError(f"No answer from the Pi for {int(timeout)} s")
            if not data:
                break
            out += data
        return chan.recv_exit_status(), out.decode("utf-8", "replace")

    def close(self) -> None:
        self._client.close()
