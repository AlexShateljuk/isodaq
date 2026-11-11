"""The wire protocol between IsoDAQ and ``isodaq-agent``, shared by both ends.

One TLS connection per open port. Everything on it is a frame::

    kind (1 byte) | length (4 bytes, big-endian) | payload

``kind`` is :data:`CONTROL` (a UTF-8 JSON object with an ``"op"``) or :data:`DATA`
(bytes to or from the serial port, passed on exactly as they are).

The first frame from the client must be ``hello`` (with a token from pairing) or
``pair`` (with a proof of the one-time pairing code). Nothing else is accepted
until one of them succeeds. After that: ``list``, ``open``, ``set``, ``close``.

Pairing never sends the code itself. The client sends an HMAC of the agent's TLS
certificate fingerprint, keyed with the code, so a machine in the middle (whose
certificate differs) can't use what it sees, and the agent knows the client saw
its real certificate. After pairing, the client pins that fingerprint and only
sends its token to an agent that presents the same certificate.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import struct

#: Bump when a change would break the other side.
VERSION = 1
DEFAULT_PORT = 7780
#: mDNS service type (for discovery, when zeroconf is installed).
SERVICE_TYPE = "_isodaq._tcp.local."

CONTROL = 1
DATA = 2
_HEADER = struct.Struct(">BI")
#: Bigger frames are refused: a peer can't make the other side buffer without limit.
MAX_FRAME = 1 << 20

#: Pairing codes avoid characters that are easy to misread (0/O, 1/I/L).
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8


class ProtocolError(Exception):
    """The other side sent something that isn't this protocol."""


def encode(kind: int, payload: bytes) -> bytes:
    return _HEADER.pack(kind, len(payload)) + payload


def control(op: str, **fields) -> bytes:
    return encode(CONTROL, json.dumps({"op": op, **fields}).encode())


def data(payload: bytes) -> bytes:
    return encode(DATA, payload)


async def read_frame(reader: asyncio.StreamReader) -> tuple[int, object]:
    """The next frame as ``(CONTROL, dict)`` or ``(DATA, bytes)``. Raises
    ``asyncio.IncompleteReadError`` when the connection closes."""
    head = await reader.readexactly(_HEADER.size)
    kind, length = _HEADER.unpack(head)
    if kind not in (CONTROL, DATA):
        raise ProtocolError(f"unknown frame kind {kind}")
    if length > MAX_FRAME:
        raise ProtocolError(f"frame of {length} bytes is too big")
    payload = await reader.readexactly(length)
    if kind == DATA:
        return DATA, payload
    try:
        message = json.loads(payload)
    except ValueError as e:
        raise ProtocolError("control frame isn't JSON") from e
    if not isinstance(message, dict) or not isinstance(message.get("op"), str):
        raise ProtocolError("control frame has no op")
    return CONTROL, message


def fingerprint(der: bytes) -> str:
    """SHA-256 of a DER certificate, as lowercase hex."""
    return hashlib.sha256(der).hexdigest()


def show_fingerprint(fp: str, groups: int = 8) -> str:
    """``ab12 cd34 …``: the first *groups* groups of four, for people to compare."""
    return " ".join(fp[i:i + 4] for i in range(0, min(len(fp), groups * 4), 4)).upper()


def normalise_code(code: str) -> str:
    """Upper case, no spaces or dashes: ``abcd-efgh`` → ``ABCDEFGH``."""
    return "".join(ch for ch in code.upper() if ch.isalnum())


def show_code(code: str) -> str:
    code = normalise_code(code)
    return f"{code[:4]}-{code[4:]}"


def code_key(code: str) -> bytes:
    """The key both sides derive from a pairing code. The agent stores only this."""
    return hashlib.sha256(b"isodaq-pair-key:" + normalise_code(code).encode()).digest()


def pair_proof(key: bytes, fp: str) -> str:
    """What the client sends when pairing: proves it knows the code and saw *fp*."""
    return hmac.new(key, b"isodaq-pair:" + fp.encode(), hashlib.sha256).hexdigest()


def token_hash(token: str) -> str:
    """The agent stores tokens hashed, so its files don't hold usable credentials."""
    return hashlib.sha256(token.encode()).hexdigest()
