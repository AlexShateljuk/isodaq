"""
core/stun_helper.py — Minimal STUN Binding Request (RFC 5389).

Discovers the public (NAT-translated) IP address of this machine by
sending a UDP packet to a public STUN server and parsing the response.
No third-party libraries required.
"""
from __future__ import annotations

import random
import socket
import struct
from typing import Optional

_MAGIC = 0x2112A442

_SERVERS = [
    ("stun.l.google.com",   19302),
    ("stun1.l.google.com",  19302),
    ("stun.cloudflare.com", 3478),
]


def get_local_ip() -> str:
    """Best-guess LAN IP by connecting a UDP socket (no data sent)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return ""


def get_public_ip(timeout: float = 3.0) -> Optional[str]:
    """
    Return the machine's public IPv4 address as seen by the internet,
    or None if all STUN servers are unreachable.
    """
    txid = random.randbytes(12)
    # Binding Request: type=0x0001, length=0, magic cookie, transaction id
    request = struct.pack(">HHI12s", 0x0001, 0, _MAGIC, txid)

    for host, port in _SERVERS:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(timeout)
            sock.sendto(request, (host, port))
            data = sock.recv(2048)
            sock.close()

            if len(data) < 20:
                continue

            # Parse attributes starting after the 20-byte header
            offset = 20
            while offset + 4 <= len(data):
                attr_type, attr_len = struct.unpack(">HH", data[offset: offset + 4])
                attr_val = data[offset + 4: offset + 4 + attr_len]
                offset += 4 + ((attr_len + 3) & ~3)  # 4-byte aligned

                if attr_type == 0x0020 and len(attr_val) >= 8:  # XOR-MAPPED-ADDRESS
                    family = attr_val[1]
                    if family != 0x01:   # IPv4 only
                        continue
                    xip = struct.unpack(">I", attr_val[4:8])[0]
                    ip_int = xip ^ _MAGIC
                    return ".".join(
                        str((ip_int >> (24 - i * 8)) & 0xFF) for i in range(4)
                    )

        except Exception:
            continue

    return None
