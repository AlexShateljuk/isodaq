# IsoDAQ Studio

A serial terminal and real-time data acquisition frontend for embedded systems development.  
Built with **Python 3 · PyQt6 · pyqtgraph**, runs on **Windows · Linux · macOS**.

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

> **Linux serial port:** add your user to the `dialout` group if permission is denied:
> ```bash
> sudo usermod -aG dialout $USER
> ```

---

## CLI mode — headless serial monitor

IsoDAQ can run without a GUI as a lightweight serial monitor.  
Useful for CI pipelines, automated test rigs, and remote/headless systems.

**Auto-detection:** CLI mode activates automatically when `-com`, `--port`, or `-p` is present. No separate binary needed — the same executable covers both modes.

### Basic usage

```bash
# Highlight keywords in the terminal output
terminal.exe -com COM8 -baud 115200 -keywords "starting device,ERROR"

# Bracket notation (equivalent)
terminal.exe -com COM8 -baud 115200 -keywords "[starting device, ERROR]"

# Repeatable -k flags
python main.py -com COM8 -baud 115200 -k "starting device" -k ERROR -k WARN
```

### Exit conditions — CI / automated testing

```bash
# Exit 0 when "BOOT OK" appears, exit 1 when "FAULT" appears, stop after 30 s
python main.py -p COM8 --exit-on "BOOT OK" --fail-on FAULT --timeout 30
```

The process exit code can drive pass/fail in any CI system:

```bash
python main.py -p COM8 --exit-on "BOOT OK" --fail-on FAULT --timeout 30
if [ $? -eq 0 ]; then echo "Device booted OK"; else echo "Boot failed"; fi
```

### All CLI options

| Option | Default | Description |
|--------|---------|-------------|
| `--port` / `-p` / `-com` | *(required)* | Serial port — `COM8`, `/dev/ttyUSB0`, `/dev/cu.usbserial-…` |
| `--baud` / `-b` / `-baud` | `115200` | Baud rate |
| `--bytesize` | `8` | Data bits: 5/6/7/8 |
| `--parity` | `N` | Parity: N/E/O/M/S |
| `--stopbits` | `1` | Stop bits: 1/1.5/2 |
| `--keywords` / `-keywords` | — | Comma-separated list or `[kw1, kw2]` bracket notation |
| `--keyword` / `-k` | — | Single keyword to highlight (repeatable) |
| `--exit-on` | — | Exit code **0** when line contains this pattern (repeatable) |
| `--fail-on` | — | Exit code **1** when line contains this pattern (repeatable) |
| `--timeout` / `-t` | `0` | Auto-exit after N seconds (0 = run forever) |
| `--log` / `-l` | — | Append all output to file |
| `--quiet` / `-q` | off | Only print lines that match a keyword |
| `--no-color` | off | Disable ANSI colour output |
| `--notify-url` | — | POST to this URL on every trigger hit (webhook or Telegram) |
| `--tg-chat` | — | Telegram `chat_id` (used with `--notify-url`) |

### Keyword pattern types

```bash
# Plain text (case-insensitive, default)
-k "starting device"

# Regex (prefix with [regex])
-k "[regex] ERR_\d+|FAULT"

# Multiple types mixed
-k "BOOT OK" -k "[regex] ERR_\d+" --fail-on "[regex] hard.?fault"
```

### Notifications from CLI

```bash
# Telegram alert on any ERROR keyword
python main.py -p COM8 -k ERROR \
  --notify-url "https://api.telegram.org/bot<TOKEN>/sendMessage" \
  --tg-chat "12345678"

# Generic webhook
python main.py -p COM8 --fail-on FAULT --notify-url "https://hooks.example.com/alert"
```

### Logging

```bash
# Log everything to file, only show matching lines in terminal
python main.py -p COM8 -k ERROR -k WARN --log session.txt --quiet
```

### Cross-platform port names

| Platform | Example |
|----------|---------|
| Windows | `COM8`, `COM3` |
| Linux | `/dev/ttyUSB0`, `/dev/ttyACM0` |
| macOS | `/dev/cu.usbserial-0001`, `/dev/cu.usbmodem14201` |

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
- Partial-line flush: incomplete lines without a trailing `\n` are emitted after 250 ms of silence, preventing crash/brownout output from being merged with the following reboot line
- Automatic disconnect detection: serial exceptions in the reader thread emit `disconnected` and reset the UI without requiring a manual reconnect cycle
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

**Actions per trigger:** Flash (coloured banner) · Log (marker in log file) · Sound (system beep) · Pause log · Resume log.

**Notifications per trigger:** each trigger can POST to a Telegram bot or a generic HTTP webhook when it fires — configured inline in the trigger editor with no extra tooling required.

| Notification type | How to configure |
|-------------------|-----------------|
| Telegram | Paste your `https://api.telegram.org/bot<TOKEN>/sendMessage` URL + chat ID |
| Generic webhook | Any HTTPS endpoint — receives JSON `{trigger, line, ts, text}` |

Hit counters per trigger are updated live and reset on session clear.  
Triggers are saved and loaded as JSON (`File → Save triggers… / Load triggers…`).

### Analytics

Cumulative trigger hit chart — one line per trigger, updated in real time:

- Step chart shows how often each trigger fires over the session
- Export as **PNG** (1 920 px wide) or **CSV** for offline analysis
- Accessible from the Analytics tab or `File → Export analytics…`

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

**Custom snippet:** a Python code block compiled at runtime can override or extend the built-in parser for any line format. The snippet receives each raw line and returns a `dict[str, float]` that is merged on top of the built-in channel results.

```python
# Example snippet — parse "T=23.4 H=61" into temp and humidity channels
import re
m = re.search(r'T=([\d.]+).*H=([\d.]+)', line)
if m:
    return {"temp": float(m.group(1)), "humidity": float(m.group(2))}
return {}
```

Parsed values feed the **Graphs**, **Indicators**, and **Trigger Events** tabs.  
Per-channel "Add to Chart" and "Add to Indicators" toggles control which panels receive each channel.  
Channel configuration is persisted and restored between sessions.

### Graphs tab

Real-time scrolling line chart (pyqtgraph):

- Up to 8 simultaneous channels with distinct colours
- 60-second rolling window, 20 000-point ring buffer per channel
- Pan and zoom with mouse
- Colour legend strip below the chart
- **Export:** PNG at 1 920 px width or multi-channel CSV (`File → Export chart…`)
- Pop-out button (`⤢`) detaches the tab into a resizable standalone window; closing the window returns the panel to its original tab position

### Indicators tab

Live value grid — one card per registered channel:

- 3-column responsive grid
- Large monospace value with channel-colour accent
- Updates on every parsed data point
- **Colour thresholds per channel:** double-click any card to open the threshold editor. Each threshold entry has a value and a colour; the card background changes to match the active threshold band in real time.

Pop-out supported (`⤢`).

### Trigger Events tab

Session log of every trigger match:

- Columns: **Time · Trigger name · Raw line** + one column per active parsed channel (snapshot value at match time)
- Alternating row colours, row selection, non-editable
- Event counter in the header; **Clear** resets the table
- Pop-out supported (`⤢`)

### In-app update check

On startup the app queries the GitHub Releases API in a background thread.  
If a newer version is available a dismissible banner appears with a direct link to the release page. No data is sent; the check is a single unauthenticated GET request.

### Themes

- **Dark (VS Code)** — default, `#1e1e1e` base with teal accents
- **Light** — off-white base with green accents
- Switchable at runtime via `Settings → Theme`; all open windows including pop-outs update immediately

### Settings persistence

All session state is saved on exit and restored on next launch:

- Port, baud rate, data format, flow control
- Terminal options (timestamp, HEX, autoscroll, echo, font size, scrollback limit)
- Parser channels with scale, offset, unit, prefix, and display flags
- Active Log Colorizer platforms
- Trigger definitions
- Macro definitions
- Window geometry and splitter positions

Config file: `~/.isodaq_studio/config.json`

---

## Architecture

```
main.py
  ├── CLI mode  (no Qt)  ─▶  core/cli_runner.py
  │       └── pyserial read loop + TriggerEngine + core/notifier.py
  │
  └── GUI mode  ─▶  QApplication + MainWindow
        │
        SerialReader (QThread)
            │  line_received(line, ts)  ── Qt queued signal → GUI thread
            │
            ├──▶  TriggerEngine.check(line, ts)           thread-safe RLock
            │         ├──▶  callbacks → MainWindow._on_trigger_match_gui()
            │         └──▶  core/notifier.send_notification()  daemon thread
            │
            ├──▶  Logger.write_line(line, ts)             lock-free queue.Queue
            │         └──▶  _LogWriter (daemon thread)
            │                   ├── _FileWriter   → .csv / .json / .txt   batch 256 · flush 200 ms
            │                   └── _SQLiteWriter → .db WAL               batch 512 · flush 500 ms
            │
            └──▶  MainWindow._on_line_received()          GUI thread
                      ├── DataParser.parse(line)          → dict[str, float]
                      ├── ChartPanel.update(parsed)
                      ├── IndicatorPanel.update(parsed)
                      ├── AnalyticsPanel.record_hit()
                      ├── terminal append + log colorizer
                      └── _log("RX", ...)
```

---

## Project structure

```
isodaq/
├── main.py                           # Entry point — GUI or CLI routing
├── requirements.txt
├── isodaq.spec                       # PyInstaller spec
├── core/
│   ├── serial_reader.py              # QThread serial reader
│   ├── logger.py                     # Async dual-sink logger (CSV + SQLite)
│   ├── triggers.py                   # Trigger engine (contains / regex / python)
│   ├── macros.py                     # Macro runner
│   ├── data_parser.py                # Channel extraction + custom snippet
│   ├── notifier.py                   # Webhook / Telegram fire-and-forget
│   ├── updater.py                    # GitHub Releases update check (QThread)
│   └── cli_runner.py                 # Headless CLI serial monitor
├── ui/
│   ├── main_window.py                # Main window, layout, signal wiring
│   ├── chart_panel.py                # pyqtgraph scrolling chart + export
│   ├── indicator_panel.py            # Live value indicator grid + thresholds
│   ├── analytics_panel.py            # Trigger hit analytics chart + export
│   ├── trigger_events_panel.py       # Trigger match event table
│   ├── parse_panel.py                # Parser channel editor
│   ├── trigger_panel.py              # Trigger editor + notification config
│   ├── macro_panel.py                # Macro editor
│   ├── logger_panel.py               # Logger controls
│   ├── log_colorizer_dialog.py       # Log colorizer settings
│   └── themes.py                     # Dark / Light stylesheets
└── emulator/
    ├── virtual_port.py               # TCP loopback server (no driver needed on Windows)
    └── scenarios/
        └── base.py                   # Scenario base class — yields (bytes, delay) pairs
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
