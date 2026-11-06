# Changelog

All notable changes to IsoDAQ Studio are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Binary frames** — a tool window that decodes packed binary from a device into named
  values: header, length, `u8` to `f64` fields with a scale, checksum and tail, in either
  byte order, with presets for VOFA+ JustFloat and SerialPlot. Each frame becomes a
  `name:value` line, so plotting, triggers and logging work on it, and text such as a boot log
  stays readable between frames. Paste a frame to try a layout without a device.
- **Hex commands and checksums** — a button by **Send** switches the command field to hex
  bytes (`01 03 00 00 00 02`) and appends a checksum: Sum-8, XOR-8, LRC-8, CRC-8,
  CRC-8/Maxim, CRC-16 (Modbus, CCITT, XMODEM) or CRC-32, as raw bytes in either order, as
  hex text, or in the NMEA `*HH` form. It also applies to repeated commands, controls and
  trigger replies. A new **checksum** trigger type fires on received lines whose `*HH`
  ending is wrong.
- **Repeat a command** — the Custom command tool window can send its command every N ms, a
  set number of times or until stopped, per device. It stops when the connection closes or
  a send fails.
- **Trigger snapshots** — a trigger can save the lines before and after each match to a
  text file, marked up and in order, and optionally send it to the notification URL. It
  doesn't wait for ever: if the device goes quiet after a fault, the file is written with
  what there is. A trigger that keeps firing can't fill the disk.
- **Share a port over TCP** — Device → Share port over TCP lets other programs, or another
  IsoDAQ with `socket://host:port`, read and write a Serial, Network, SSH or Bluetooth
  connection, byte for byte, while the app stays connected. Off at every start, on this
  computer only unless you choose otherwise, and shown in the status bar while on.
- **Modbus RTU and TCP** — a transport that polls a register map you define in the new
  Modbus tool window: holding, input, coil and discrete tables; u16, i16, u32, i32 and f32
  with swapped word order; scaling. Values arrive as ordinary lines, so plotting, triggers,
  logging and publishing all work on them. `write NAME VALUE` writes back. Problems are
  reported once as `modbus:` lines, and **Show frames** shows the raw bytes.
- **Send files with XMODEM and YMODEM** — in the Flash tool window, over an open Serial or
  Network connection: XMODEM (CRC or checksum), XMODEM-1K and YMODEM, with progress and
  cancel. Sending only.
- **Bluetooth LE transport** — connect to boards with a serial-over-Bluetooth service:
  Nordic UART, HM-10 style modules, Microchip Transparent UART, u-blox SPS, or any
  service with a notifying and a writable characteristic. **↻** scans, serial devices
  first. Needs `bleak`.
- **MQTT transport** — use a broker as a source:
  `mqtt://host/sensors/#?tx=commands/device`. Each message is a `topic: payload` line, so
  triggers, parsing, logging and Quick Plot work as usual. TLS with `mqtts://`; passwords
  are asked for once per run and never saved. Needs `paho-mqtt`.
- **Publish to MQTT** — in the Data Logger: send a connection's parsed values and trigger
  events to a broker for Grafana, Home Assistant or Node-RED. Off at every start.
- **Controls** — a tool window for your own buttons, toggles, sliders and number
  boxes that send commands to the active connection (`SET PWM={value}`). Buttons
  and toggles can have function keys (F1–F3, F6–F12) and appear in the command
  palette. Byte escapes for binary protocols, per-control line ending, JSON import
  and export. Stored positions are never sent by themselves.
- **Build & flash, then resume** — a **Flash** tool window holds a per-connection
  command (presets for PlatformIO, ESP-IDF, arduino-cli, esptool, st-flash and
  make; `{port}` and `{baud}` are filled in). **Build & flash** (or `F5`) releases
  the port, runs the command with its output in that connection's terminal, then
  reconnects if it was connected. **Stop** ends the whole process tree, so nothing
  keeps holding the port.
- **Quick Plot** — lines that are only data are graphed with no setup:
  `12.5, 40, -3` (Arduino Serial Plotter style → `ch1…ch3`), `temp:21.5 hum:40`
  or `temp=21.5, rpm=1500`. The whole line must be data, so ordinary log lines
  are never plotted. Right-click a series in the legend to **keep** it as a
  Parsing channel or hide it; **Auto** in the Graphs footer turns it off.
- **Teleplot input** — `>name:value`, `>name:timestamp:value`, units (`§unit`)
  and multi-point lines are understood by Quick Plot and by Parsing channels, so
  firmware that already uses a Teleplot library works unchanged (text, XY and 3D
  lines are ignored).
- **Auto-reconnect** — when a link drops (board reset, cable re-plugged, network
  bridge restarted), the connection keeps its settings and retries every second
  (network/SSH every 3 s) until the device is back, then carries on in the same
  terminal. A re-plugged USB board is found again by VID:PID + serial number even
  if the OS gives it a new name (`ttyUSB0` → `ttyUSB1`). The status bar shows
  `Reconnecting… N s` and the Connect button becomes **Stop**. On by default;
  `⋯ → Link → Auto-reconnect`.
- **DTR / RTS / BREAK and board reset** — `⋯ → Lines` toggles DTR and RTS
  (applied on connect and live) and sends a 250 ms BREAK; `⋯ → Reset` pulses
  **Board** (RTS → EN, ESP32/ESP8266), **DTR pulse** (Arduino) or
  **Bootloader** (esptool's sequence into the ESP ROM bootloader). Also in the
  command palette.
- **Custom baud rates** — the baud box is editable: type any rate (74880 for
  ESP8266 boot messages, 250000, 1000000 …); 74880, 250000, 500000, 1000000 and
  2000000 are in the list.
- **Port descriptions** — the port list shows what each port is
  ("CP2102 USB to UART Bridge Controller", "STM32 STLink Virtual COM Port"), with
  USB VID:PID and serial number in the tooltip.
- **Trigger auto-reply** — a trigger can send a **Reply** to the device when it
  matches (`login:` → `root`). Regex triggers can insert captured groups
  (`PING (\d+)` → `PONG \1`); escapes `\r \n \t \0 \xHH`. The command bar's
  line ending is appended. Replies to the same trigger closer than 100 ms apart
  are skipped so a device that echoes the reply can't loop forever.
- **Chart view controls** — the Graphs footer gets a time-window selector
  (10 s … 15 min, All), buffer size (2k – 500k points per channel), **Pause**
  (freeze the view while data keeps recording; pan/zoom freely) and **Y lock**.

### Changed
- The RX counter now counts bytes on the wire, including line endings, rather than the text
  shown.

### Fixed
- **File → Load triggers** raised an error after loading: the rules were active but the
  list didn't show them, Analytics didn't list them, and the notice about Python rules
  being loaded disabled never appeared.
- Trigger names were almost invisible in the Light theme, and their type tags and hit
  counts in the Dark theme.
- A chart channel's legend entry was placed between the PNG and CSV buttons.
- The font −/+ buttons in the `⋯` popover showed no text.
- The editable baud box took keyboard focus at startup.
- The selected row of every drop-down list was nearly unreadable in the Light
  theme.
- The Graphs empty-state text was invisible in the Light theme.
- Indicator thresholds could read as a different number: with a comma locale, 50
  showed as `50,000`. They now always use a dot, and were unreadable in Light.
- Fields in Preferences and the SSH dialog were too narrow on macOS, cutting off the
  signaling URL.

## [0.4.0] — 2026-11-01

### Added
- **Multiple simultaneous connections (SER3)** — monitor several devices at once.
  A connection tab strip (with an Add button) sits above the terminal; each
  connection is fully independent — its own transport (serial/network/SSH),
  terminal, data logger, parser + channels, chart, indicators, triggers, events
  and analytics. Switching tabs swaps the whole right-side view to that
  connection; the command line and macros target the active connection.
  **Triggers fire on every connection** (events + Telegram/webhook notifications
  even for background tabs); the visual flash lands in that connection's own
  terminal. Built on a new `core/connection.py` `Connection` model with
  MainWindow delegating to the active connection; see `SER3_REFACTOR.md`.

- **Network & SSH transports (SER1)** — the port bar gains a **transport
  selector** (Serial / Network / SSH) so the terminal can attach to more than a
  local COM/tty port:
  - **Network** — `socket://host:port` (raw TCP: telnet-style log streams,
    `ser2net`/`socat` bridges), `rfc2217://host:port` (the network-serial
    standard: Moxa NPort, Digi, Lantronix, ser2net) and `loop://`, all via
    pyserial's URL handlers — **no new dependency**. The connection opens on the
    reader thread so a dead host never freezes the UI.
  - **SSH** — connect to a remote host and stream a command's output (default
    `cat /dev/ttyUSB0`, e.g. a Raspberry Pi's own serial console or `tail -f`),
    with the command field wired to stdin. Uses **`paramiko`** (now a bundled
    dependency, so SSH works out of the box in the release binaries); it is
    still imported lazily, and a from-source checkout without it gets a friendly
    "`pip install paramiko`" notice instead of failing.
  - **Saved SSH sessions** — a quick-access list of named SSH profiles
    (host/port/user/key/command). In SSH mode the target dropdown lists your
    saved sessions plus a “＋ Add SSH session…” entry; the ⟳ button becomes ✎ to
    edit (or delete) the selected one, and right-clicking a session offers
    **Edit… / Delete** straight from the list (with a confirmation). Sessions
    persist across restarts;
    **passwords are never written to disk** — you're asked once per run (blank
    falls back to a key/agent) and it's cached for the session. The old
    single-session format is migrated automatically.
  - Under the hood: a shared `core/stream_reader.py` base (read loop, `send`,
    connection-state) with `SerialReader`/`NetworkReader`/`SSHReader`; readers
    are swappable and cached per transport. Transport, network URL and SSH
    details persist across restarts (**the SSH password is never saved**). New
    tests in `tests/test_net_reader.py`.

- **IDE-style layout** — IntelliJ-like tool windows: Devices / Parsing / Macros /
  Custom command on the left, Graphs / Indicators on the right, Events /
  Analytics / Triggers / Data Logger at the bottom. Every dock collapses to its
  edge stripe so the terminal can own the window; one window-wide connection
  toolbar.
- **HEX view** — a one-click `HEX` toggle next to the command line shows received
  data as a classic hex dump (`offset · 16 bytes · |ascii|`) with a running
  per-connection byte offset. Every received byte is shown, including CR/LF.
  Parsing, triggers and logging are unaffected.
- **Command palette** — `Ctrl/Cmd+K` or `Ctrl/Cmd+Shift+P` (View → Command
  Palette…): fuzzy-search and run any action — connect, switch connection, open
  a tool window, toggle HEX/timestamps/autoscroll, change theme, every menu item.

### Changed
- **Much faster at high data rates** — the app previously handled ≈250 lines/s in
  total; one port at 921600 baud lagged over a minute and froze the window.
  It now sustains several ports at 921600 baud and flash-dump bursts with no
  lost lines in the CSV/SQLite logs: readers deliver lines in batches, the
  terminal paints in batches (~30 fps), charts/analytics redraw on timers only
  while visible, and received lines are processed on a time budget so the
  window stays responsive. If the display falls more than 1 s behind, the
  excess is still logged and trigger-checked but not drawn (the terminal says
  so when it catches up).
- The connection toolbar, status bar (rate, errors, session time) and the
  Events badge now follow the active connection tab. Failed or lost
  connections are marked with a red ✕ in the Devices list and tab.
- Connection tabs show short names (`127.0.0.1:5000`, `tty.usbmodem1101`) with
  the full name in a tooltip, and scroll instead of truncating.

### Fixed
- **Crash/hang when reading a flash or binary dump** — data without line breaks
  grew into one unbounded "line"; lines are now capped at 4 KB.
- **CSV logs unreadable after binary data** — NUL and other control characters
  are now shown as `·`.
- The terminal rendered in a proportional font when JetBrains Mono isn't
  installed; it now falls back to SF Mono / Menlo / Consolas.
- The HEX option did nothing.
- The status bar's Rate and Errors were never updated.
- Quitting with several live connections aborted with "QThread: Destroyed while
  thread is still running".
- The Devices list was empty until the first connection change.
- Parsing channel names were invisible in the light theme.
- Detaching a tool window had no shortcut after the layout redesign; it is back
  as **View → Detach Tool Window** (`F4`).
- macOS: check boxes overlapped their neighbours' labels (e.g. *Flash / Log /
  Sound* in the trigger editor).

## [0.3.0] — 2026-07-25

### Added
- **Illustrated documentation** — the README now embeds generated screenshots
  next to each major feature, and a full **[wiki](https://github.com/AlexShateljuk/isodaq/wiki)**
  (per-feature pages) was added, authored under `wiki/`. Screenshots are
  reproducible via `tools/gen_screenshots.py`, which drives the real window with
  synthetic data and saves `widget.grab()` PNGs into `docs/images/`.

### Changed
- **Right panel collapsed by default** — a fresh window now opens focused on the
  terminal; reveal the data side (tabs + sidebar) with the `⊞` button or
  `Ctrl+Shift+R`. The panel's open/closed state is now persisted.
- **Parsing consolidated into the sidebar** — removed the left-panel "parser
  strip"; its parser-type and separator controls weren't wired to the parsing
  engine (which auto-detects JSON vs KEY=VALUE per channel). The default
  **New-channel prefix** now lives at the top of the **Parsing** section, so all
  parsing configuration is in one place.
- **i18n coverage expanded** — the whole main window is now translatable, not
  just the menu bar: toolbar buttons, terminal controls, tabs, sidebar sections,
  status bar, the Share/Join dialogs, Preferences, the update banner, and the
  trigger-load security dialog all route through `tr()`. The Ukrainian catalog
  (`translations/uk.json`) grew from 17 to 120 entries. Added a uk-locale smoke
  test that builds the window and asserts translated widgets.

## [0.2.2] — 2026-07-13

This is primarily an open-source-readiness release: licensing, security
hardening, tests + CI, docs, a major internal refactor, and i18n groundwork.

### Added
- **Apache-2.0 license** (`LICENSE`, `NOTICE`).
- Community & project docs: `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`,
  this changelog, and GitHub issue/PR templates.
- Unit test suite (`tests/`, 58 tests) covering triggers, data parser, logger,
  and signaling; `conftest.py`, `requirements-dev.txt`.
- CI workflow (`.github/workflows/ci.yml`): ruff lint + pytest on Python
  3.9/3.11/3.12. Ruff config in `pyproject.toml`.
- `docs/ARCHITECTURE.md` contributor overview; class/method docstrings across
  `core/`.
- `constraints.txt` with the exact tested dependency versions for reproducible
  installs (`pip install -r requirements.txt -c constraints.txt`).
- **Internationalization (i18n)** — lightweight JSON-catalog translations
  (`core/i18n.py`, `translations/`), a Ukrainian catalog, a language picker in
  Preferences, and `ISODAQ_LANG` / system-locale auto-detection. Menu bar is the
  first translated slice; coverage grows incrementally.
- **`ui/main_window.py` decomposition** — split the 2069-line god-object into 7
  focused controllers under `ui/controllers/` (down to 1003 lines); added an
  offscreen construction smoke test and a settings round-trip test.

### Changed
- **Relay hardening**: session cap, per-IP session cap, per-tunnel viewer cap,
  request body-size limit, and per-IP sliding-window rate-limiting on
  `/register` + `/lookup`. Real client IP taken from `X-Forwarded-For`. All caps
  env-tunable for self-hosts; `/health` exposes `sessions`/`limit`.
- Dependencies now have upper bounds (`requirements.txt`) to avoid surprise
  breakage from a future major release.
- `.gitignore` explicitly keeps `ui/resources/**` tracked so bundled assets
  can't be accidentally excluded by the screenshot ignore rules.

### Security
- **Blocked arbitrary code execution from shared trigger configs.** `[python]`
  triggers loaded from an untrusted file now load disabled and are never
  `eval()`'d until explicitly trusted; the load dialog warns and offers
  *Load & enable / Load disabled / Cancel*.

### Fixed
- **macOS release was mislabelled** — the `macos-latest` runner is Apple Silicon
  but the artifact was named `…-x64`, so Intel users got an incompatible binary.
  The macOS build is now Apple-Silicon (`macos-arm64`) with an honest name and an
  ad-hoc signature (so it runs without the "damaged" error). No native Intel
  binary is shipped — GitHub's Intel CI runners queue for hours; Intel Mac users
  run from source. README documents the Gatekeeper/SmartScreen workarounds.
- **Logger dropped the last buffered batch on session stop** — `Logger.stop()`
  closed the sinks before the writer thread flushed its sub-interval buffer, so
  the last ≤0.2 s of lines were silently lost. Added a flush handshake.
- Cleaned up lint findings (unused imports/loop variables).

## [0.2.1] — 2026-06-21
### Changed
- Right-panel layout polish; added design/dev tools (element inspector, live
  theme hot-reload, annotated UI map).

## [0.2.0] — 2026-06-20
### Added
- In-log search with highlight (Ctrl+F) and trigger→log-line jump (F1/F2).
- Internet session sharing: relay mode through NAT, multi-viewer support, viewer
  presence, host-close notification, relay latency indicator; received shared
  lines are logged.
- Application icon; macOS `.app` bundle build.

### Fixed
- Connect/disconnect indicator not always updating (B1).
- Signaling URL normalisation: strip stray paths, upgrade http→https for public
  hosts (fixed a join-failure 404).
- Find-bar visibility/layout, tab strip, and clipped buttons.

## [0.1.0] — 2026-04-21
### Added
- Initial release: serial terminal, real-time charts, trigger engine, custom
  parser snippets, indicator thresholds, analytics, Telegram/webhook
  notifications, chart/CSV export, dual CSV+SQLite logger, CLI/headless mode,
  auto-update check, Simple/Advanced mode, LAN session sharing with STUN +
  signaling server.

[Unreleased]: https://github.com/AlexShateljuk/isodaq/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/AlexShateljuk/isodaq/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/AlexShateljuk/isodaq/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/AlexShateljuk/isodaq/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/AlexShateljuk/isodaq/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/AlexShateljuk/isodaq/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/AlexShateljuk/isodaq/releases/tag/v0.1.0
