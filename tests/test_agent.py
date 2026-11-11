"""isodaq-agent (AGT1, AGT3) and IsoDAQ's client for it (AGT2), without Qt: a real
agent on localhost, a pty as the board."""
import json
import os
import select
import socket
import ssl
import time
import tty

import pytest

pytest.importorskip("serial")
pytest.importorskip("cryptography")

from core import agent_client as ac                       # noqa: E402
from isodaq_agent import __main__ as cli                  # noqa: E402
from isodaq_agent import protocol, security, server       # noqa: E402


class Board:
    """A pty: the agent opens the slave, the test plays the device on the master."""

    def __init__(self):
        self.master, self.slave = os.openpty()
        tty.setraw(self.slave)
        tty.setraw(self.master)
        self.device = os.ttyname(self.slave)

    def send(self, data: bytes) -> None:
        os.write(self.master, data)

    def read(self, want: int, sec: float = 2.0) -> bytes:
        out, end = b"", time.monotonic() + sec
        while len(out) < want and time.monotonic() < end:
            if select.select([self.master], [], [], 0.05)[0]:
                out += os.read(self.master, 65536)
        return out

    def close(self) -> None:
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass


@pytest.fixture
def board():
    b = Board()
    yield b
    b.close()


@pytest.fixture
def agent(tmp_path, board):
    srv = server.AgentServer(tmp_path / "agent", host="127.0.0.1", port=0, name="lab-pi",
                             extra_ports=(board.device,))
    srv.start_in_thread()
    yield srv
    srv.stop()


@pytest.fixture
def store(tmp_path):
    return ac.TrustStore(tmp_path / "client" / "agents.json")


@pytest.fixture
def paired(agent, store):
    ac.pair("127.0.0.1", agent.port, agent.pairing.new_code(), store, name="laptop")
    return agent, store


def _open(agent, store, device, **settings):
    return ac.AgentSerial("127.0.0.1", agent.port, device, store,
                          {"baud": 115200, **settings})


def _read(ser, want: int, sec: float = 2.0) -> bytes:
    out, end = b"", time.monotonic() + sec
    while len(out) < want and time.monotonic() < end:
        out += ser.read(max(1, ser.in_waiting))
    return out


# ── Targets ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url, expected", [
    ("agent://pi/dev/ttyUSB0", ("pi", 7780, "/dev/ttyUSB0")),
    ("agent://pi.local:9000/dev/serial/by-id/usb-x", ("pi.local", 9000,
                                                       "/dev/serial/by-id/usb-x")),
    ("agent://10.0.0.5/COM5", ("10.0.0.5", 7780, "COM5")),
    ("agent://[fe80::1]:7781/dev/ttyACM0", ("fe80::1", 7781, "/dev/ttyACM0")),
    ("pi:7780/dev/ttyS0", ("pi", 7780, "/dev/ttyS0")),
])
def test_parse_url(url, expected):
    assert ac.parse_url(url) == expected


@pytest.mark.parametrize("url", ["agent://pi", "agent://pi/", "agent://:7780/dev/x",
                                 "agent://pi:99999/dev/x", "agent://pi:abc/dev/x"])
def test_parse_url_refuses(url):
    with pytest.raises(ValueError):
        ac.parse_url(url)


def test_make_url_round_trips():
    for host, port, dev in (("pi", 7780, "/dev/ttyUSB0"), ("fe80::1", 9000, "/dev/x"),
                            ("pc", 7780, "COM3")):
        assert ac.parse_url(ac.make_url(host, port, dev)) == (host, port, dev)


def test_local_addresses_only():
    for ip in ("127.0.0.1", "192.168.1.5", "10.1.2.3", "172.16.0.9", "100.101.102.103",
               "169.254.1.1", "::1", "fe80::1%en0", "::ffff:192.168.1.5", "fd00::1"):
        assert server.local_address(ip), ip
    for ip in ("8.8.8.8", "1.1.1.1", "2001:4860::8888", "::ffff:8.8.8.8", "nonsense"):
        assert not server.local_address(ip), ip


# ── Pairing and tokens ───────────────────────────────────────────────────────

def test_pair_saves_a_private_entry(paired, tmp_path):
    agent, store = paired
    entry = store.get("127.0.0.1", agent.port)
    assert entry["name"] == "lab-pi"
    assert entry["fingerprint"] == agent.fingerprint
    assert entry["client"] == "laptop"
    assert oct(store.path.stat().st_mode & 0o777) == "0o600"
    home = tmp_path / "agent"
    assert oct((home / "key.pem").stat().st_mode & 0o777) == "0o600"
    # The agent keeps only a hash of the token, and the code is gone.
    saved = json.loads((home / "clients.json").read_text())
    assert entry["token"] not in json.dumps(saved)
    assert saved["laptop"]["token"] == protocol.token_hash(entry["token"])
    assert not (home / "pairing.json").exists()


def test_a_code_works_once(agent, store):
    code = agent.pairing.new_code()
    ac.pair("127.0.0.1", agent.port, code, store, name="a")
    with pytest.raises(ac.AgentError) as e:
        ac.pair("127.0.0.1", agent.port, code, store, name="b")
    assert e.value.code == "pair-none"


def test_wrong_codes_use_up_the_attempts(agent, store):
    code = agent.pairing.new_code()
    for _ in range(security.PAIR_ATTEMPTS - 1):
        with pytest.raises(ac.AgentError) as e:
            ac.pair("127.0.0.1", agent.port, "AAAA-AAAA", store)
        assert e.value.code == "pair-wrong"
    with pytest.raises(ac.AgentError):
        ac.pair("127.0.0.1", agent.port, "AAAA-AAAA", store)
    # Used up: even the right code no longer works.
    with pytest.raises(ac.AgentError) as e:
        ac.pair("127.0.0.1", agent.port, code, store)
    assert e.value.code == "pair-none"


def test_expired_code(agent, store):
    code = agent.pairing.new_code(seconds=-1)
    with pytest.raises(ac.AgentError) as e:
        ac.pair("127.0.0.1", agent.port, code, store)
    assert e.value.code == "pair-none"


def test_code_is_case_and_dash_insensitive(agent, store):
    code = agent.pairing.new_code()
    ac.pair("127.0.0.1", agent.port, code.lower().replace("-", " "), store)


def test_proof_is_bound_to_the_certificate():
    # A machine in the middle with its own certificate gets a proof for *its*
    # fingerprint, which the real agent rejects.
    key = protocol.code_key("ABCD-EFGH")
    assert protocol.pair_proof(key, "aa" * 32) != protocol.pair_proof(key, "bb" * 32)


def test_unknown_token_refused(agent, store):
    store.put("127.0.0.1", agent.port, {"fingerprint": agent.fingerprint, "token": "made-up"})
    with pytest.raises(ac.AgentError) as e:
        ac.list_ports("127.0.0.1", agent.port, store)
    assert e.value.code == "auth" and e.value.fatal


def test_revoked_client_refused(paired):
    agent, store = paired
    ac.list_ports("127.0.0.1", agent.port, store)
    assert agent.clients.revoke("laptop")
    with pytest.raises(ac.AgentError) as e:
        ac.list_ports("127.0.0.1", agent.port, store)
    assert e.value.code == "auth"


def test_changed_certificate_is_refused_before_the_token_is_sent(paired, monkeypatch):
    agent, store = paired
    entry = store.get("127.0.0.1", agent.port)
    store.put("127.0.0.1", agent.port, {**entry, "fingerprint": "00" * 32})
    checked = []
    monkeypatch.setattr(agent.clients, "check", lambda t: checked.append(t))
    with pytest.raises(ac.AgentError) as e:
        ac.list_ports("127.0.0.1", agent.port, store)
    assert e.value.code == "fingerprint" and e.value.fatal
    time.sleep(0.2)
    assert checked == []


def test_not_paired():
    with pytest.raises(ac.AgentError) as e:
        ac.list_ports("127.0.0.1", 1, ac.TrustStore("/nonexistent/agents.json"))
    assert e.value.code == "not-paired"


def test_unreachable(store):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()                         # nothing listens there now
    store.put("127.0.0.1", port, {"fingerprint": "00" * 32, "token": "x"})
    with pytest.raises(ac.AgentError) as e:
        ac.list_ports("127.0.0.1", port, store)
    assert e.value.code == "unreachable" and not e.value.fatal


def _raw(agent):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx.wrap_socket(socket.create_connection(("127.0.0.1", agent.port), timeout=3))


def _recv_all(sock) -> bytes:
    out = b""
    try:
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                return out
            out += chunk
    except (OSError, ssl.SSLError):
        return out


def test_data_before_hello_is_refused(agent):
    s = _raw(agent)
    s.sendall(protocol.data(b"reset\n"))
    reply = _recv_all(s)
    assert b'"auth"' in reply and b"welcome" not in reply


def test_oversized_frame_closes(paired):
    agent, store = paired
    s = _raw(agent)
    s.sendall(protocol.control("hello", version=protocol.VERSION,
                               token=store.get("127.0.0.1", agent.port)["token"]))
    s.sendall(bytes([protocol.DATA]) + (protocol.MAX_FRAME + 1).to_bytes(4, "big"))
    assert b"welcome" in _recv_all(s)      # ends: the agent hung up


def test_wrong_protocol_version(agent):
    s = _raw(agent)
    s.sendall(protocol.control("hello", version=99, token="x"))
    assert b'"version"' in _recv_all(s)


def test_repeated_failures_block_the_address(agent, store, monkeypatch):
    monkeypatch.setattr(server, "MAX_FAILURES", 3)
    store.put("127.0.0.1", agent.port, {"fingerprint": agent.fingerprint, "token": "bad"})
    for _ in range(3):
        with pytest.raises(ac.AgentError) as e:
            ac.list_ports("127.0.0.1", agent.port, store)
        assert e.value.code == "auth"
    with pytest.raises(ac.AgentError) as e:
        ac.list_ports("127.0.0.1", agent.port, store)
    assert e.value.code in ("closed", "unreachable")


def test_public_addresses_turned_away(agent, paired, monkeypatch):
    _, store = paired
    monkeypatch.setattr(server, "local_address", lambda ip: False)
    with pytest.raises(ac.AgentError):
        ac.list_ports("127.0.0.1", agent.port, store)


# ── Ports ────────────────────────────────────────────────────────────────────

def test_bytes_both_ways_unchanged(paired, board):
    agent, store = paired
    ser = _open(agent, store, board.device)
    try:
        assert ser.agent == "lab-pi"
        everything = bytes(range(256)) * 4
        ser.write(everything)
        assert board.read(len(everything)) == everything
        board.send(b"boot\r\n\x00\xff")
        assert _read(ser, 8) == b"boot\r\n\x00\xff"
        big = os.urandom(300_000)            # several frames
        import threading
        got = []
        t = threading.Thread(target=lambda: got.append(board.read(len(big), 5)))
        t.start()
        ser.write(big)
        t.join(6)
        assert got[0] == big
    finally:
        ser.close()


def test_lines_and_baud_are_applied(paired, board, monkeypatch):
    agent, store = paired
    seen = []
    real = server.OpenPort.apply

    async def spy(self, msg):
        seen.append({k: v for k, v in msg.items() if k != "op"})
        await real(self, msg)
    monkeypatch.setattr(server.OpenPort, "apply", spy)
    ser = _open(agent, store, board.device, dtr=False, rts=False)
    try:
        ser.dtr = True
        ser.rts = True
        ser.baudrate = 9600
        ser.break_condition = True
        ser.break_condition = False
        end = time.monotonic() + 2
        while len(seen) < 5 and time.monotonic() < end:
            time.sleep(0.02)
        assert seen == [{"dtr": True}, {"rts": True}, {"baud": 9600}, {"break": True},
                        {"break": False}]
        assert ser.baudrate == 9600
        # A pty has no modem lines: the agent's refusal comes back, it isn't lost.
        assert ser.last_error in ("", "[Errno 25] Inappropriate ioctl for device") \
            or "ioctl" in ser.last_error.lower() or "argument" in ser.last_error.lower()
    finally:
        ser.close()


def test_only_serial_ports_can_be_opened(paired):
    agent, store = paired
    for path in ("/etc/passwd", "/dev/null", "../../etc/passwd"):
        with pytest.raises(ac.AgentError) as e:
            _open(agent, store, path)
        assert e.value.code == "open" and not e.value.fatal


def test_bad_settings_refused(paired, board):
    agent, store = paired
    for bad in ({"baud": 0}, {"baud": "fast"}, {"bytesize": 9}, {"parity": "X"},
                {"stopbits": 3}, {"flow": "magic"}):
        with pytest.raises(ac.AgentError) as e:
            _open(agent, store, board.device, **bad)
        assert e.value.code == "open"


def test_one_client_per_port(paired, board):
    agent, store = paired
    first = _open(agent, store, board.device)
    try:
        with pytest.raises(ac.AgentError) as e:
            _open(agent, store, board.device)
        assert e.value.code == "busy" and "laptop" in str(e.value)
        name, ports = ac.list_ports("127.0.0.1", agent.port, store)
        assert name == "lab-pi"
        mine = [p for p in ports if p["device"] == board.device]
        assert mine and mine[0]["busy"] == "laptop"   # --allow matches are listed too
    finally:
        first.close()
    end = time.monotonic() + 2
    while agent.open_ports and time.monotonic() < end:
        time.sleep(0.02)
    second = _open(agent, store, board.device)      # free again once closed
    second.close()


def test_one_board_under_two_names(paired, board, tmp_path):
    agent, store = paired
    link = tmp_path / "usb-CP2102-if00"            # as in /dev/serial/by-id
    link.symlink_to(board.device)
    agent.extra_ports = (board.device, str(link))
    first = _open(agent, store, board.device)
    try:
        with pytest.raises(ac.AgentError) as e:
            _open(agent, store, str(link))
        assert e.value.code == "busy"
    finally:
        first.close()


def test_unplugged_port_ends_the_read(paired):
    agent, store = paired
    b = Board()
    agent.extra_ports = (b.device,)
    ser = _open(agent, store, b.device)
    b.close()                                   # the board goes away
    with pytest.raises(OSError):
        end = time.monotonic() + 3
        while time.monotonic() < end:
            ser.read(1)
    ser.close()
    assert not agent.open_ports


def test_agent_stopping_ends_the_read(paired, board):
    agent, store = paired
    ser = _open(agent, store, board.device)
    agent.stop()
    with pytest.raises(OSError):
        end = time.monotonic() + 3
        while time.monotonic() < end:
            ser.read(1)
    with pytest.raises(OSError):
        ser.write(b"x")


# ── Command line ─────────────────────────────────────────────────────────────

def test_cli_pair_clients_revoke(tmp_path, capsys):
    home = str(tmp_path / "home")
    assert cli.main(["--config", home, "pair"]) == 0
    out = capsys.readouterr().out
    code = out.split("code", 1)[1].split()[0]
    assert len(protocol.normalise_code(code)) == protocol.CODE_LENGTH
    assert security.Pairing(tmp_path / "home").pending()
    security.Clients(tmp_path / "home").add("bench laptop")
    assert cli.main(["--config", home, "clients"]) == 0
    assert "bench laptop" in capsys.readouterr().out
    assert cli.main(["--config", home, "revoke", "bench laptop"]) == 0
    assert cli.main(["--config", home, "revoke", "bench laptop"]) == 1
