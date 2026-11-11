"""Command line: ``python -m isodaq_agent serve | pair | clients | revoke NAME | ports``."""
from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import sys
from pathlib import Path

from isodaq_agent import __version__, protocol, security


def _home(args) -> Path:
    return Path(args.config).expanduser() if args.config else security.default_home()


def _addresses() -> list[str]:
    """This machine's LAN addresses, best guess, for the pairing message."""
    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))      # no packet is sent: picks the outgoing interface
            found.append(s.getsockname()[0])
    except OSError:
        pass
    return found or [socket.gethostname()]


def cmd_serve(args) -> int:
    from isodaq_agent.server import AgentServer
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    server = AgentServer(_home(args), host=args.host, port=args.port, name=args.name,
                         extra_ports=tuple(args.allow or ()),
                         lan_only=not args.any_address)
    if args.pair or not server.clients.names():
        code = server.pairing.new_code()
        print(_pair_message(code, server.fingerprint, args.port, args.host), flush=True)
    try:
        asyncio.run(server.serve_forever())
    except KeyboardInterrupt:
        pass
    return 0


def _pair_message(code: str, fp: str, port: int, host: str = "") -> str:
    hosts = [host] if host and host not in ("0.0.0.0", "::") else _addresses()
    where = ", ".join(f"{a}:{port}" for a in hosts)
    return (f"\nPair IsoDAQ with this agent: Device → Pair with agent…, address {where},\n"
            f"code  {code}   (valid {security.PAIR_SECONDS // 60} minutes, "
            f"{security.PAIR_ATTEMPTS} tries)\n"
            f"Agent fingerprint {protocol.show_fingerprint(fp)}\n")


def cmd_pair(args) -> int:
    home = _home(args)
    cert, _ = security.ensure_certificate(home)
    code = security.Pairing(home).new_code()
    print(_pair_message(code, security.certificate_fingerprint(cert), args.port))
    return 0


def cmd_clients(args) -> int:
    clients = security.Clients(_home(args)).names()
    if not clients:
        print("No paired clients.")
    for name, info in clients.items():
        print(f"{name}\tpaired {info.get('paired', '')}\tlast seen {info.get('last_seen') or '—'}")
    return 0


def cmd_revoke(args) -> int:
    if security.Clients(_home(args)).revoke(args.name):
        print(f"Removed {args.name}. It can't connect again until it pairs again.")
        return 0
    print(f"No paired client is called {args.name!r}.", file=sys.stderr)
    return 1


def cmd_ports(_args) -> int:
    from isodaq_agent.server import list_ports
    for p in list_ports():
        print(f"{p['device']}\t{p['description']}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="isodaq-agent",
                                     description="Serve this machine's serial ports to IsoDAQ.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", help="folder for the certificate and paired clients "
                                         f"(default {security.default_home()})")
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the agent")
    s.add_argument("--host", default="0.0.0.0",
                   help="address to listen on (default all local interfaces)")
    s.add_argument("--port", type=int, default=protocol.DEFAULT_PORT)
    s.add_argument("--name", default="", help="name shown in IsoDAQ (default the host name)")
    s.add_argument("--allow", action="append", metavar="PATTERN",
                   help="also allow devices matching this glob, e.g. /dev/serial/by-id/*")
    s.add_argument("--pair", action="store_true", help="print a pairing code on start")
    s.add_argument("--any-address", action="store_true",
                   help="accept clients from public addresses too (default: only local "
                        "networks and VPNs; reach the agent from outside through WireGuard or "
                        "Tailscale rather than an open router port)")
    s.set_defaults(func=cmd_serve)
    p = sub.add_parser("pair", help="make a one-time pairing code")
    p.add_argument("--port", type=int, default=protocol.DEFAULT_PORT,
                   help="the port the agent listens on (only for the message)")
    p.set_defaults(func=cmd_pair)
    sub.add_parser("clients", help="list paired clients").set_defaults(func=cmd_clients)
    r = sub.add_parser("revoke", help="remove a paired client")
    r.add_argument("name")
    r.set_defaults(func=cmd_revoke)
    sub.add_parser("ports", help="list serial ports").set_defaults(func=cmd_ports)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
