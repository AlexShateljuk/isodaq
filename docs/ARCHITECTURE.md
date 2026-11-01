# IsoDAQ Studio — Architecture

A map of how the pieces fit together, for contributors. For per-module detail,
read the docstring at the top of each file in `core/`.

## Layers

```
  ui/          Qt widgets & windows. Layout + user interaction only.
  core/        All logic: serial I/O, parsing, triggers, logging, sharing.
  relay/       Standalone signaling + relay HTTP server (deployed separately).
  emulator/    Virtual serial port for testing without hardware.
  tests/       Unit tests for the core.* logic modules.
```

Rule of thumb: **`core/` never imports `ui/`.** Logic is GUI-agnostic and
communicates outward via Qt signals; the UI wires those signals to widgets.

## Connections

A `core/connection.py` **`Connection`** is one device: its transport readers
(Serial / Network / SSH, cached per kind), `DataParser`, `TriggerEngine`,
`Logger`, status figures (RX/TX bytes, line rate, errors, session time, failed
flag), the toolbar selection, and the RX backlog. The UI attaches that
connection's panels (terminal, parse, chart, indicator, trigger, events,
analytics, logger panel). `MainWindow` keeps a list of connections and an
**active** one; properties such as `_parser`, `_reader`, `_terminal` delegate to
the active connection, and switching tabs swaps every per-connection stack.

## Data flow (received data)

```
     device ── bytes ──▶ reader thread (Serial / Network / SSH)
                           │  core/stream_reader.py
                           │  LineSplitter: one split per read, 4 KB line cap,
                           │  partial-line flush after 250 ms of silence
                           │  deliver(): decode, control chars → "·", timestamp
                           ▼
              lines_received([(text, ts, arrival, raw_bytes), …])
                           │  one queued Qt signal per *read*, not per line
                           ▼
   GUI thread:  MainWindow._on_conn_lines  →  connection.rx_backlog
                           │
                           ▼
   MainWindow._drain_rx   (QTimer 15 ms, ≤ 12 ms of work, round-robin)
       │   _shed_overload: lines > 1 s old → logger + triggers only
       ▼
   MainWindow._on_conn_line(conn, text, ts, raw)
       ├── conn.logger.write_line      → queue → _LogWriter thread → .csv/.json/.txt + .db (WAL)
       ├── MainWindow._log             → conn.term_pending → _flush_terminal (~30 fps,
       │                                  text or HEX dump decided on arrival)
       ├── conn.engine.check           → TriggerController (queued) → flash banner,
       │                                  events row, analytics, notifications
       ├── conn.parser.parse           → chart / indicator / parse panel buffers
       │                                  (charts + analytics redraw on timers, only if visible)
       └── (active connection) macro runner wait-for, SessionServer broadcast
```

Design rules that keep this fast (see the 0.4.0 stress tests in
[the wiki](https://github.com/AlexShateljuk/isodaq/wiki/Performance-and-High-Data-Rates)):
never touch a widget per line — buffer and paint on a timer; never call
`setStyleSheet` per sample; never let the GUI thread work for more than one
budget slice before repainting; anything that must see every line (logger,
triggers) stays cheap so it can run even when drawing is skipped.

## Threading model

Everything heavy runs off the GUI thread and reports back via **Qt signals**
(auto-queued across threads, so slots run safely on the GUI thread):

| Thread | Owner | Purpose |
|--------|-------|---------|
| Readers (one per connection) | `SerialReader` / `NetworkReader` (`StreamReader(QThread)`), `SSHReader(QThread)` | read bytes, split lines, timestamp, batch to GUI |
| Log writer | `_LogWriter(threading.Thread)` in `logger.py` | batched file + SQLite writes |
| Session server | `SessionServer` background thread | TCP accept/broadcast + relay push |
| Session client | `SessionClient(QThread)` | TCP read **or** relay long-poll |
| Notifier | daemon thread per send (`notifier.py`) | webhook / Telegram POST |
| Update check | `UpdateChecker(QThread)` | query GitHub Releases once at startup |

Shared state is guarded by `threading.Lock`/`RLock` (see `TriggerEngine._lock`,
`Logger` writer locks, `SessionServer._lock`).

## Session sharing

Two independent transports (see [README](../README.md#session-sharing) for the
user view):

- **Direct (LAN/VPN)** — `SessionServer` runs a TCP server on `:9876`;
  `SessionClient` connects straight to `host:port`. Lossless, lowest latency.
- **Relay (internet/NAT)** — host registers its STUN public IP under a 6-digit
  code via `core/signaling.py` on the hosted `relay/server.py`; the host POSTs
  lines to `/tunnel/{code}/push` and each viewer long-polls
  `/tunnel/{code}/poll`. Best-effort. The relay fans one host out to many
  viewers and enforces abuse limits (session/viewer caps, rate-limit).

`core/stun_helper.py` discovers the public IP (stdlib UDP STUN). The relay is
pure stdlib `http.server` and holds all state in memory (codes die on restart).

## The executable-config surface

Two features run user Python by design — see [SECURITY.md](../SECURITY.md):

- `[python]` **triggers** (`core/triggers.py`) — `eval()`'d per line. Triggers
  loaded from an untrusted file are `_blocked` until explicitly trusted.
- **Parser snippets** (`core/data_parser.py set_snippet`) — `exec()`'d per line;
  authored only in-app.

## Where do I add…?

| To add… | Touch |
|---------|-------|
| a new serial value format | `core/data_parser.py` (`_from_kv` / `_try_json`) + a test |
| a new trigger action | `core/triggers.py` (action flag) + `ui/main_window.py` handler |
| a new log format | `core/logger.py` `_FileWriter._fmt_line` |
| a new tool window | a `ui/*_panel.py` + an entry in `MainWindow.TOOLS` (edge + title) and `_populate_tool_windows` |
| a new transport | a `core/*_reader.py` subclass of `StreamReader` + `SerialController._reader_for` |
| a command-palette entry | `MainWindow._palette_commands` (menu actions are picked up automatically) |
| a sharing/relay change | `core/session_*.py` + `relay/server.py` (keep them in sync) |

## Testing

`tests/` covers the pure-logic modules (triggers, parser, logger, signaling,
line splitting) plus offscreen main-window smoke tests (multi-connection routing,
HEX rendering, command palette); the GUI tests skip when PyQt6 isn't installed. Run `pytest` and
`ruff check .` before a PR (both gate CI). Use `emulator/` +
`python test_vport.py` to exercise the serial path without hardware.
