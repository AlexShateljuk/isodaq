"""
core/cli_runner.py — Headless CLI serial monitor with keyword triggers.

No Qt dependency — uses plain pyserial + threading, works on headless servers.

Usage examples
--------------
  terminal.exe -com COM8 -baud 115200 -keywords "starting device,ERROR"
  terminal.exe -com COM8 -baud 115200 -keywords "[starting device, ERROR]"
  isodaq --cli --port /dev/ttyUSB0 -b 9600 -k BOOT -k ERROR
  isodaq --cli -p COM3 --exit-on "BOOT OK" --fail-on FAULT --timeout 30 --log run.txt
  isodaq --cli -p COM8 --fail-on ERROR --notify-url https://hooks.example.com/alert

Trigger extras
--------------
  --exit-on  <pattern>   Exit 0 when line contains this (success condition)
  --fail-on  <pattern>   Exit 1 when line contains this (failure condition)
  --timeout  <seconds>   Auto-exit after N seconds (0 = run forever)
  --log      <file>      Append all output to file
  --notify-url <url>     POST to this webhook / Telegram URL on any trigger hit
  --tg-chat  <id>        Telegram chat_id (used with --notify-url)
  --quiet                Only print lines that match a keyword
  --no-color             Disable ANSI colour output
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from datetime import datetime
from typing import IO

# ── ANSI helpers ──────────────────────────────────────────────────────────────

_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_RED    = "\033[91m"
_YELLOW = "\033[93m"
_GREEN  = "\033[92m"
_CYAN   = "\033[96m"
_PURPLE = "\033[95m"
_ORANGE = "\033[38;5;208m"

_KEYWORD_PALETTE = [_YELLOW, _CYAN, _PURPLE, _ORANGE, _GREEN]


def _c(*codes: str, text: str, on: bool) -> str:
    if not on:
        return text
    return "".join(codes) + text + _RESET


def _enable_win_ansi() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleMode(
            ctypes.windll.kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass


def _out(msg: str, f: IO | None = None) -> None:
    print(msg, flush=True)
    if f:
        f.write(msg + "\n")
        f.flush()


# ── Pattern helpers ───────────────────────────────────────────────────────────

def _parse_keywords(raw: str) -> list[str]:
    """Accept 'kw1,kw2' or '[kw1, kw2]' bracket notation."""
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    return [k.strip() for k in raw.split(",") if k.strip()]


def _compile_pattern(text: str):
    """
    Returns (type, compiled) where type is 'contains' or 'regex'.
    Supports '[regex] pattern' prefix from the existing trigger syntax.
    """
    import re
    t = text.strip()
    if t.lower().startswith("[regex]"):
        pat = t[7:].strip()
        return "regex", re.compile(pat, re.IGNORECASE)
    return "contains", t.lower()


def _matches(compiled, line: str) -> bool:
    typ, pat = compiled
    if typ == "contains":
        return pat in line.lower()
    return bool(pat.search(line))


# ── Argument parser ───────────────────────────────────────────────────────────

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="isodaq --cli",
        description="IsoDAQ CLI — headless serial monitor with keyword triggers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  terminal.exe -com COM8 -baud 115200 -keywords "starting device,ERROR"
  isodaq --cli -p /dev/ttyUSB0 -b 9600 -k "BOOT OK" -k ERROR
  isodaq --cli -p COM3 --exit-on "BOOT OK" --fail-on FAULT --timeout 30 --log run.txt
  isodaq --cli -p COM8 --fail-on ERROR --notify-url https://hooks.example.com/alert
        """,
    )

    # ── Connection ────────────────────────────────────────────────────────────
    g = p.add_argument_group("connection")
    g.add_argument("--port", "-p", "-com", "--com",
                   required=True, metavar="PORT",
                   help="Serial port (e.g. COM8, /dev/ttyUSB0)")
    g.add_argument("--baud", "-b", "-baud", "--baud-rate",
                   type=int, default=115200, metavar="BAUD",
                   help="Baud rate (default: 115200)")
    g.add_argument("--bytesize", type=int, default=8, choices=[5, 6, 7, 8],
                   metavar="BITS", help="Data bits (default: 8)")
    g.add_argument("--parity", default="N", choices=["N", "E", "O", "M", "S"],
                   help="Parity: N/E/O/M/S (default: N)")
    g.add_argument("--stopbits", type=float, default=1, choices=[1, 1.5, 2],
                   metavar="STOP", help="Stop bits (default: 1)")

    # ── Keywords / triggers ───────────────────────────────────────────────────
    g = p.add_argument_group("triggers")
    g.add_argument("--keywords", "-keywords", metavar="LIST",
                   help='Comma-separated keywords (or [kw1, kw2] bracket notation)')
    g.add_argument("--keyword", "-k", action="append", default=[], metavar="PATTERN",
                   help="Keyword to highlight (repeatable; prefix [regex] for regex)")
    g.add_argument("--exit-on", action="append", default=[], metavar="PATTERN",
                   help="Exit 0 when a line contains this pattern (success)")
    g.add_argument("--fail-on", action="append", default=[], metavar="PATTERN",
                   help="Exit 1 when a line contains this pattern (failure)")

    # ── Session control ───────────────────────────────────────────────────────
    g = p.add_argument_group("session")
    g.add_argument("--timeout", "-t", type=float, default=0, metavar="SECONDS",
                   help="Auto-exit after N seconds (0 = run forever)")
    g.add_argument("--log", "-l", metavar="FILE",
                   help="Append all output to a log file")
    g.add_argument("--quiet", "-q", action="store_true",
                   help="Only print lines that match a keyword")
    g.add_argument("--no-color", "--no-colour", action="store_true",
                   help="Disable ANSI colour output")

    # ── Notifications ─────────────────────────────────────────────────────────
    g = p.add_argument_group("notifications")
    g.add_argument("--notify-url", metavar="URL",
                   help="POST to this URL on every trigger match (webhook or Telegram)")
    g.add_argument("--tg-chat", metavar="CHAT_ID",
                   help="Telegram chat_id (used with --notify-url)")

    return p


# ── Runner ────────────────────────────────────────────────────────────────────

def run_cli(argv: list[str]) -> int:
    """Entry point for CLI mode. Returns the process exit code."""

    parser = build_arg_parser()
    args = parser.parse_args(argv)

    color = not args.no_color and sys.stdout.isatty()
    if color:
        _enable_win_ansi()

    # Collect and compile all keyword patterns
    all_keywords: list[str] = list(args.keyword)
    if args.keywords:
        all_keywords += _parse_keywords(args.keywords)

    kw_compiled  = [_compile_pattern(k) for k in all_keywords]
    exit_compiled = [_compile_pattern(p) for p in args.exit_on]
    fail_compiled = [_compile_pattern(p) for p in args.fail_on]

    # Print session header
    _out(_c(_BOLD, text=f"IsoDAQ CLI  {args.port} @ {args.baud}", on=color))
    if all_keywords:
        _out(_c(_DIM, text=f"  Watching : {', '.join(all_keywords)}", on=color))
    if args.exit_on:
        _out(_c(_GREEN, text=f"  Exit 0 on: {', '.join(args.exit_on)}", on=color))
    if args.fail_on:
        _out(_c(_RED,   text=f"  Exit 1 on: {', '.join(args.fail_on)}", on=color))
    if args.timeout:
        _out(_c(_DIM,   text=f"  Timeout  : {args.timeout}s", on=color))
    if args.log:
        _out(_c(_DIM,   text=f"  Log file : {args.log}", on=color))
    _out("")

    # Open log file
    log_file: IO | None = None
    if args.log:
        try:
            log_file = open(args.log, "a", encoding="utf-8")
        except OSError as e:
            _out(_c(_RED, text=f"Cannot open log file: {e}", on=color))
            return 1

    # Shared exit state
    exit_code: list[int | None] = [None]
    stop_event = threading.Event()

    def on_line(raw: str, ts: str) -> None:
        ts_str = _c(_DIM, text=f"[{ts}]", on=color)

        # Keyword match → coloured line
        match_idx: int | None = None
        for i, pat in enumerate(kw_compiled):
            if _matches(pat, raw):
                match_idx = i
                break

        line_str = (
            _c(_KEYWORD_PALETTE[match_idx % len(_KEYWORD_PALETTE)], _BOLD,
               text=raw, on=color)
            if match_idx is not None else raw
        )

        if not args.quiet or match_idx is not None:
            _out(f"{ts_str} {line_str}", log_file)

        # Notify on keyword match
        if match_idx is not None and args.notify_url:
            _notify(args, all_keywords[match_idx], raw, ts)

        # Exit-on check (exit 0 — success)
        for i, pat in enumerate(exit_compiled):
            if _matches(pat, raw):
                label = args.exit_on[i]
                _out(_c(_GREEN, _BOLD,
                        text=f"\n  Matched exit-on: '{label}' — exiting 0",
                        on=color), log_file)
                exit_code[0] = 0
                stop_event.set()
                return

        # Fail-on check (exit 1 — failure)
        for i, pat in enumerate(fail_compiled):
            if _matches(pat, raw):
                label = args.fail_on[i]
                _out(_c(_RED, _BOLD,
                        text=f"\n  Matched fail-on: '{label}' — exiting 1",
                        on=color), log_file)
                if args.notify_url:
                    _notify(args, f"FAIL:{label}", raw, ts)
                exit_code[0] = 1
                stop_event.set()
                return

    # Open serial port
    try:
        import serial
        ser = serial.Serial(
            port=args.port,
            baudrate=args.baud,
            bytesize=args.bytesize,
            parity=args.parity,
            stopbits=args.stopbits,
            timeout=0.05,
            write_timeout=1.0,
        )
    except Exception as e:
        _out(_c(_RED, text=f"Cannot open {args.port}: {e}", on=color))
        if log_file:
            log_file.close()
        return 1

    _out(_c(_GREEN, text=f"Connected to {args.port} @ {args.baud} baud", on=color))

    start_time = time.monotonic()
    buf = bytearray()
    buf_ts: float = 0.0
    PARTIAL_FLUSH = 0.25   # seconds; matches SerialReader behaviour

    try:
        while not stop_event.is_set():
            # Timeout check
            if args.timeout and (time.monotonic() - start_time) >= args.timeout:
                _out(_c(_YELLOW,
                        text=f"\nTimeout ({args.timeout}s) reached — exiting 0",
                        on=color), log_file)
                exit_code[0] = 0
                break

            # Read
            try:
                waiting = ser.in_waiting
                chunk = ser.read(waiting if waiting > 0 else 1)
            except Exception as e:
                _out(_c(_RED, text=f"\nSerial error: {e}", on=color), log_file)
                exit_code[0] = 1
                break

            if chunk:
                buf.extend(chunk)
                buf_ts = time.monotonic()

            # Emit complete lines
            while b"\n" in buf:
                idx = buf.index(b"\n")
                raw = buf[:idx].replace(b"\r", b"").decode("utf-8", errors="replace")
                buf = buf[idx + 1:]
                if raw:
                    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    on_line(raw, ts)

            # Flush partial line after silence (mirrors SerialReader)
            if buf and buf_ts > 0 and (time.monotonic() - buf_ts) >= PARTIAL_FLUSH:
                raw = buf.replace(b"\r", b"").decode("utf-8", errors="replace").strip()
                buf = bytearray()
                buf_ts = 0.0
                if raw:
                    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    on_line(raw, ts)

    except KeyboardInterrupt:
        _out(_c(_YELLOW, text="\nInterrupted (Ctrl+C) — exiting 0", on=color),
             log_file)
        exit_code[0] = 0
    finally:
        ser.close()
        if log_file:
            log_file.close()

    return exit_code[0] if exit_code[0] is not None else 0


def _notify(args, trigger_name: str, line: str, ts: str) -> None:
    if not args.notify_url:
        return
    from core.notifier import send_notification
    send_notification(
        args.notify_url,
        args.tg_chat or "",
        trigger_name,
        line,
        ts,
    )
