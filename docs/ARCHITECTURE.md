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

## Data flow (one received serial line)

```
        serial device
             │  bytes
             ▼
   ┌───────────────────────┐   QThread, own timestamp
   │ core/serial_reader.py │
   │   SerialReader        │
   └───────────┬───────────┘
               │ line_received(line, ts)   ← Qt signal (queued → GUI thread)
               ▼
        ui/main_window.py  ── fans the line out to: ───────────────┐
               │                        │                          │
               ▼                        ▼                          ▼
   ┌────────────────────┐   ┌────────────────────┐   ┌──────────────────────┐
   │ core/data_parser   │   │ core/triggers      │   │ core/logger          │
   │  DataParser.parse  │   │  TriggerEngine.    │   │  Logger.write_line   │
   │  → {chan: value}   │   │  check → callbacks │   │  → queue → writer thr│
   └─────────┬──────────┘   └─────────┬──────────┘   └──────────┬───────────┘
             ▼                        ▼                         ▼
   chart / indicator panels   flash·log·sound·notify·     .csv / .json / .txt
                              analytics·events row        + .db (SQLite WAL)
             │
             └──────────────► if a session is shared, the same line is also
                              handed to core/session_server for broadcast.
```

## Threading model

Everything heavy runs off the GUI thread and reports back via **Qt signals**
(auto-queued across threads, so slots run safely on the GUI thread):

| Thread | Owner | Purpose |
|--------|-------|---------|
| Serial reader | `SerialReader(QThread)` | read bytes, split lines, timestamp |
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
| a new right-panel tab | a new `ui/*_panel.py`, wired in `ui/main_window.py` |
| a sharing/relay change | `core/session_*.py` + `relay/server.py` (keep them in sync) |

## Testing

`tests/` covers the pure-logic modules (triggers, parser, logger, signaling) —
none import PyQt/numpy, so CI runs them without GUI deps. Run `pytest` and
`ruff check .` before a PR (both gate CI). Use `emulator/` +
`python test_vport.py` to exercise the serial path without hardware.
