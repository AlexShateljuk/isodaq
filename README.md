# IsoDAQ Studio

A serial terminal and real-time data acquisition frontend for embedded systems development.  
Built with **Python 3 · PyQt6 · pyqtgraph**, runs on **Windows · Linux · macOS**.

[![CI](https://github.com/AlexShateljuk/isodaq/actions/workflows/ci.yml/badge.svg)](https://github.com/AlexShateljuk/isodaq/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

---

## Running from source

```bash
git clone https://github.com/AlexShateljuk/isodaq.git
cd isodaq
pip install -r requirements.txt
python main.py
```

Requirements: Python 3.10+, see `requirements.txt` for package versions.

---

## Pre-built binaries

Download the latest release for your platform from the
[Releases](https://github.com/AlexShateljuk/isodaq/releases) page.

| Platform | File | How to run |
|----------|------|-----------|
| Windows 10/11 x64 | `IsoDAQ-Studio-windows-x64.zip` | Extract → run `IsoDAQ Studio.exe` |
| Linux x64 | `IsoDAQ-Studio-linux-x64.tar.gz` | Extract → `chmod +x "IsoDAQ Studio"` → run `./IsoDAQ\ Studio` |
| macOS 13+ x64 | `IsoDAQ-Studio-macos-x64.zip` | Extract → drag `IsoDAQ Studio.app` to Applications |

> **Linux note:** if the app does not start, install the Qt platform plugin dependencies:
> ```bash
> sudo apt-get install libxcb-cursor0 libxcb-xinerama0 libegl1
> ```

---

## Building from source

Install PyInstaller, then run:

```bash
pip install pyinstaller>=6.0
pyinstaller isodaq.spec --noconfirm
```

The built application is placed in `dist/IsoDAQ Studio/`.

### Automated release builds (GitHub Actions)

Pushing a version tag triggers the CI workflow that builds for all three
platforms and publishes a GitHub Release with attached binaries:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The workflow file is at [`.github/workflows/release.yml`](.github/workflows/release.yml).

---

## Features

### Serial connection

- Port, baud rate (9 600 – 921 600), data bits, parity, stop bits, flow control (None / RTS/CTS / XON/XOFF)
- High-throughput reader: `in_waiting` chunk reads — no per-line latency at high baud rates
- Partial-line flush: incomplete lines without a trailing `\n` are emitted after 250 ms of silence
- Automatic disconnect detection with UI reset
- RX / TX byte counters and session timer in the status bar
- Configurable EOL terminator (`\r\n` / `\n` / `\r` / none)
- Command history navigation (↑ ↓)

### Terminal

- Monospace output with millisecond timestamps
- HEX display mode
- Autoscroll toggle with manual override
- Echo TX back to terminal
- Adjustable font size (8 – 24 pt), persisted across sessions
- Configurable scrollback limit (default 5 000 lines)
- Colour-coded lines: RX · TX · SYS · ERR

### Log Colorizer

Highlights RX lines by log level based on the active platform profile.

| Colour | Level |
|--------|-------|
| Green  | INFO |
| Yellow | WARNING / NOTICE |
| Red    | ERROR / CRITICAL |

Supported platforms: ESP32 (ESP-IDF), Arduino / AVR, STM32 (HAL / printf), Zephyr RTOS,
Linux / syslog, MicroPython / Python logging, FreeRTOS (generic), NuttX RTOS.  
Multiple platforms can be active simultaneously (`Settings → Log Colorizer…`).

### Data logger

- Start / Stop session with one click
- **Dual sink**: CSV file + SQLite database written in parallel via a lock-free queue
- Formats: CSV (`timestamp, trigger, raw`) · JSON lines · raw text
- SQLite: WAL mode + batched `executemany` — no write contention at high data rates
- Configurable filename prefix and output directory
- Live stats: file size and row count, updated every 500 ms
- "Open folder" shortcut opens the log directory in the OS file manager

### Triggers

Alert rules evaluated against every incoming RX line in real time.

| Syntax | Example |
|--------|---------|
| Plain text (case-insensitive) | `Brownout Detector` |
| Regex | `[regex] hard.?fault\|HardFault_Handler\|ERR_\d+` |
| Python lambda | `[python] lambda line: 'ERR' in line and '=' in line` |

Actions per trigger: **Flash** (coloured banner) · **Log** (marker in log file) · **Sound** (system beep) · **Pause log** · **Resume log**.

Hit counters per trigger are updated live and reset on session clear.  
Triggers are saved and loaded as JSON (`File → Save triggers… / Load triggers…`).

### Macros

Configurable command sequences sent over serial:

- Each macro has a name, command string, and optional inter-step delay
- "Run" executes the sequence; "Stop" aborts mid-sequence
- Macros are saved/loaded as part of the session settings

### Data parser

Extracts named numeric channels from RX lines in real time.

| Field | Purpose |
|-------|---------|
| Key | Token to search for (`snap.pv_v`, `vbat`, `data.voltage`) |
| Prefix | Line filter — only lines containing this string are parsed |
| Unit | Disambiguates multiple tokens (`pv=43608mV 847mA` → Unit `mV` → `43608`) |
| × / + | Scale and offset applied after extraction |

Parsed values feed the **Graphs**, **Indicators**, and **Trigger Events** tabs.

### Graphs tab

Real-time scrolling line chart (pyqtgraph):

- Up to 8 simultaneous channels with distinct colours
- 60-second rolling window, 20 000-point ring buffer per channel
- Pan and zoom with mouse
- Colour legend strip below the chart
- Pop-out button (`⤢`) detaches the tab into a resizable standalone window

### Indicators tab

Live value grid — one card per registered channel:

- 3-column responsive grid
- Large monospace value with channel-colour accent
- Updates on every parsed data point

Pop-out supported (`⤢`).

### Trigger Events tab

Session log of every trigger match:

- Columns: **Time · Trigger name · Raw line** + one column per active parsed channel
- Alternating row colours, row selection, non-editable
- Event counter in the header; **Clear** resets the table
- Pop-out supported (`⤢`)

### Session sharing (Share & Join)

Share live serial output with a colleague anywhere in the world — both sides just run
IsoDAQ Studio. No browser, no account, no extra dependencies (pure stdlib on both ends).
One host can stream to **multiple viewers** at once.

There are two ways to join, for two situations:

**1. By address — same network (LAN / VPN).** Direct TCP, lossless, lowest latency.

```
Host  ──────────  direct TCP :9876  ──────────  Viewer
   (192.168.x.x — shown in the Share dialog)
```

**2. By code — over the internet (through any NAT/firewall).** A small hosted
signaling+relay server brokers the connection; no router setup needed.

```
Host                     Signaling + Relay Server                Viewer(s)
  │── register (STUN IP) ───────>│                                  │
  │      → 6-digit code          │<──── look up code ───────────────│
  │── push serial lines ────────>│──── long-poll / deliver ────────>│
  │                              │   (relay forwards the stream)    │
```

1. **Share** — click Share. The app starts the local TCP server, discovers your public
   IP via STUN, registers a **6-digit code**, and enables the relay. The dialog shows the
   code, the LAN address, and a live **Viewers: N** count.
2. **Join** — your colleague clicks Join → **By code** → types the code. Data flows over
   the relay, so it works even behind strict NAT/corporate firewalls.

When you click **Stop**, all viewers are notified and leave automatically.

> **LAN vs relay trade-off:** the LAN/direct-TCP path is lossless and low-latency. The
> relay path is **best-effort** — under sustained very high throughput, or on a network
> hiccup, individual lines may be dropped from the relayed stream. The host's own
> terminal and data logger always capture the complete record regardless.

**Recording a shared session:** a viewer can press **Start** in the Logger panel to record
the received stream to CSV/DB, exactly like a local serial feed.

**Connection quality** is shown in the status bar as a coloured LED + latency (TCP ping in
LAN mode, relay `/health` round-trip in code mode):

| Colour | Latency |
|--------|---------|
| 🟢 Green | ≤ 80 ms |
| 🟡 Yellow | 81 – 250 ms |
| 🔴 Red | > 250 ms or timeout |

A public relay is already configured by default, so sharing works out of the box.

#### Hosting your own relay (optional)

The `relay/` folder contains the server (pure stdlib `http.server`, zero dependencies):

1. Push this repo to GitHub.
2. Create a free project on [railway.app](https://railway.app) → Deploy from GitHub → set **Root Directory** to `relay`.
3. Copy the generated URL (e.g. `https://your-relay.railway.app`).
4. In IsoDAQ Studio → **Edit → Preferences** → paste the URL into **Signaling server URL**.

All users sharing a session must point at the same relay URL. Session codes are held in
memory only (1-hour TTL) and are invalidated if the relay restarts.

### UI modes

Two layout modes switchable via `View → Simple Mode` (`Ctrl+Shift+M`):

| Mode | What's visible |
|------|---------------|
| **Advanced** (default) | Full layout — port bar, terminal, right panel (charts, indicators, triggers, analytics), sidebar |
| **Simple** | Left panel only — port settings, terminal, command input; parser strip hidden |

Mode is persisted across sessions.

### Themes

- **Dark (VS Code)** — default, `#1e1e1e` base with teal accents
- **Light** — off-white base with green accents
- Switchable at runtime via `View → Theme`

### Auto-update check

On startup (2 s after launch) the app silently queries the GitHub Releases API.  
If a newer version tag is found:

- A dismissible **banner** appears at the top of the window with a "Download" button
- An **OS system notification** fires (macOS / Windows) — clicking it opens the release page

`Help → Check for Updates` triggers a manual check at any time.

### Settings persistence

All session state is saved on exit and restored on next launch.  
Config file: `~/.isodaq_studio/config.json`

---

## Architecture

```
SerialReader (QThread)
    │  line_received(line, ts)  ── Qt queued signal → GUI thread
    │
    ├──▶  TriggerEngine.check(line, ts)           thread-safe RLock
    │         └──▶  callbacks → MainWindow._on_trigger_match_gui()
    │
    ├──▶  Logger.write_line(line, ts)             lock-free queue.Queue
    │         └──▶  _LogWriter (daemon thread)
    │                   ├── _FileWriter   → .csv / .json / .txt
    │                   └── _SQLiteWriter → .db WAL
    │
    └──▶  MainWindow._on_line_received()          GUI thread
              ├── DataParser.parse(line)
              ├── ChartPanel / IndicatorPanel update
              ├── SessionServer.feed_line()        → broadcast to viewers
              └── terminal append + log colorizer

SessionClient (QThread)  ← viewer side
    └── line_received → MainWindow._on_remote_line()
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full module map,
threading model, and a "where do I add…?" guide for contributors.

---

## Project structure

```
isodaq/
├── main.py                           # Entry point, version constant
├── requirements.txt
├── relay/                            # Signaling + relay server (deploy once to Railway)
│   ├── server.py                     # HTTP signaling + relay (fan-out, pure stdlib)
│   ├── Procfile                      # Railway/Heroku entry point
│   ├── nixpacks.toml                 # Pin Python build (don't auto-run main.py)
│   ├── railway.toml                  # Railway build/health config
│   ├── requirements.txt              # empty — stdlib only
│   └── README.md                     # Deploy instructions
├── core/
│   ├── serial_reader.py              # QThread serial reader
│   ├── logger.py                     # Async dual-sink logger
│   ├── triggers.py                   # Trigger engine
│   ├── macros.py                     # Macro runner
│   ├── data_parser.py                # Channel extraction engine
│   ├── updater.py                    # GitHub Releases update checker
│   ├── session_server.py             # TCP session server (Share side)
│   ├── session_client.py             # TCP session client (Join side)
│   ├── signaling.py                  # Signaling server register/lookup client
│   ├── stun_helper.py                # STUN public IP discovery (stdlib only)
│   └── notifier.py                   # Telegram / webhook trigger notifier
└── ui/
    ├── main_window.py                # Main window, layout, signal wiring
    ├── chart_panel.py                # pyqtgraph scrolling chart
    ├── indicator_panel.py            # Live value indicator grid
    ├── trigger_events_panel.py       # Trigger match event table
    ├── parse_panel.py                # Parser channel editor
    ├── trigger_panel.py              # Trigger editor
    ├── macro_panel.py                # Macro editor
    ├── logger_panel.py               # Logger controls
    ├── log_colorizer_dialog.py       # Log colorizer settings
    ├── analytics_panel.py            # Trigger hit analytics
    └── themes.py                     # Dark / Light stylesheets
```

---

## Performance

At 921 600 baud continuous ASCII stream (~90 KB/s):

| Component | Strategy | Result |
|-----------|----------|--------|
| Serial read | `in_waiting` chunk reads | ~100 µs / iteration |
| Logger queue | Lock-free enqueue from serial thread | reader never waits for disk |
| File sink | Batch 256 lines → `writelines()` + `flush()` | every 200 ms |
| SQLite sink | `executemany`, WAL, `synchronous=NORMAL` | 512 rows / 500 ms |
| GUI | Queued Qt signal, scrollback capped | 5 000 lines default |

---

## Requirements

```
PyQt6 >= 6.6.0
pyserial >= 3.5
pyqtgraph >= 0.13
numpy >= 1.26
pyqt6-sip
```

---

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for setup and
the checks CI runs (`ruff check .` + `pytest`). Please also read the
[Code of Conduct](CODE_OF_CONDUCT.md). Changes are tracked in
[CHANGELOG.md](CHANGELOG.md).

## Security

Found a vulnerability? Please report it privately — see [SECURITY.md](SECURITY.md).
Note that `[python]` triggers and custom parser snippets execute arbitrary Python
by design; never enable Python rules from a config file you don't trust.

## License

IsoDAQ Studio is licensed under the **Apache License 2.0** — see [LICENSE](LICENSE)
and [NOTICE](NOTICE). You are free to use, modify, and distribute it, including for
commercial purposes, provided you retain the copyright/attribution notices.

Copyright 2026 eSOMtech.
