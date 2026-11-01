# Serial Connection

The **connection toolbar** across the top of the window controls the active
connection (the selected [connection tab](Multiple-Connections)).

## Controls

| Control | Options | Notes |
|---------|---------|-------|
| **Transport** | Serial · Network · SSH | what kind of source this connection reads |
| **Port / target** | serial ports · a URL · a saved SSH session | see the transports below |
| **↻** | — | rescan serial ports (in SSH mode it becomes **✎** to edit the session) |
| **Baud** | 9600 … 74880 · 115200 … 921600 · 1000000 · 2000000 — or **type any rate** | must match your firmware; custom rates need driver support (real USB adapters have it, virtual ports often don't) |
| **Connect** | — | toggles to **Disconnect** once open |
| **`⋯`** | Data (8N1 · 8E1 · 8O1 · 7N1), Flow (None · RTS/CTS · XON/XOFF), **Lines** (DTR · RTS · BREAK), **Reset** (Board · DTR pulse · Bootloader), **Auto-reconnect**, Timestamp / HEX / Autoscroll / Echo, font size, Clear | set-once options |

The toolbar always shows the **active tab's** transport, target and baud;
switching tabs switches what it shows.

## Transports

### Serial

A local COM / tty port. Pick it from the list (press **↻** after plugging a
device in). Each entry shows what the OS knows about the port — e.g.
`/dev/cu.usbserial-0001   CP2102 USB to UART Bridge Controller` — with the USB
VID:PID and serial number in the tooltip. On macOS prefer the `/dev/cu.*` entry.

### Network

Type a URL into the target field:

| URL | Use for |
|-----|---------|
| `socket://192.168.1.50:23` | raw TCP — telnet-style log streams, `ser2net` / `socat` bridges |
| `rfc2217://host:4001` | network serial servers (Moxa NPort, Digi, Lantronix, ser2net in RFC 2217 mode) — baud/framing are applied remotely |
| `loop://` | in-memory loopback, handy for testing |

The connection opens on the reader thread, so an unreachable host never freezes
the window.

### SSH

Streams the output of a command on a remote machine — e.g. `cat /dev/ttyUSB0` on
a Raspberry Pi wired to your board, or `tail -f /var/log/app.log`. The command
line sends to the remote command's stdin.

- **Saved sessions** — the target list shows your saved SSH profiles
  (name, host, port, user, key, command) plus **＋ Add SSH session…**. **✎** edits
  the selected one; right-click a session for **Edit… / Delete**.
- **Passwords are never written to disk** — you're asked once per run (leave it
  blank to use a key or ssh-agent).
- Needs `paramiko` (bundled in the release binaries; `pip install paramiko` when
  running from source).

## Connecting

1. Choose the transport and target, and the **Baud** (set framing/flow under `⋯`
   if your device needs it).
2. Click **Connect**.
3. The status bar turns green and reads `● PORT · BAUD`, the session timer starts,
   and a `Connected: …` line is written to the terminal.

Click **Disconnect** (same button) to close it.

### Auto-reconnect

If the link drops — cable pulled, board reset, network bridge restarted —
IsoDAQ Studio keeps the connection's settings and **waits for the device to come
back**:

- the status bar shows `● Reconnecting… N s` in amber, the tab and the
  [Devices](Multiple-Connections) entry turn amber with **↻**, and the Connect
  button becomes **Stop** (click it to give up);
- serial ports are retried every second; a re-plugged USB board is found again
  by its VID:PID + serial number even if it comes back under a new name
  (`ttyUSB0` → `ttyUSB1`); network and SSH targets are retried every 3 s;
- when it is back the terminal says `Reconnected: … (after N s)` and data flows
  into the same terminal, log and charts.

A disconnect you make yourself never reconnects. Turn the behaviour off under
`⋯ → Link → Auto-reconnect`; then a lost link shows `● Connection failed` in
red, a red **✕** in the Devices list and counts under **Errors** until the next
successful connect.

### DTR, RTS, BREAK and resetting the board

`⋯ → Lines`:

- **DTR / RTS** — the modem-control lines, applied when connecting and
  immediately when toggled. Both are on by default (the usual driver default).
  Untick one if your board's auto-reset circuit holds it in reset while
  connected.
- **BREAK** — holds the line in BREAK for 250 ms (some bootloaders and Linux
  consoles use it as a "magic" key).

`⋯ → Reset` (also in the [command palette](Interface-Overview#command-palette)):

| Button | Sequence | For |
|--------|----------|-----|
| **Board** | RTS on 100 ms with DTR off → release | ESP32 / ESP8266 dev boards (RTS drives EN) |
| **DTR pulse** | DTR off 100 ms → on | Arduino Uno / Nano / Mega (reset through the DTR capacitor) |
| **Bootloader** | esptool's sequence: hold GPIO0 low (DTR) while pulsing EN (RTS) | ESP chips — enter the ROM bootloader |

Afterwards the lines return to the DTR/RTS checkboxes. Network (`rfc2217://`)
connections forward the lines; raw `socket://` and SSH can't, and say so.

Need more than one device? Click **`+`** next to the tabs — see
[Multiple Connections](Multiple-Connections).

## Under the hood — the high-throughput reader

Each connection's reader runs on its own thread and is tuned for sustained high
data rates:

- **Chunked reads** — whatever is in the OS buffer (`in_waiting`) is read in one
  go, not byte by byte.
- **Line assembly** — one split per read; lines are handed to the window **in
  batches** (one signal per read), not one by one.
- **Partial-line flush** — a line without a trailing `\n` is flushed after
  **250 ms of silence**, so a prompt like `> ` still shows up promptly.
- **Line-length cap** — a run of more than 4 KB without a newline (binary data,
  flash dumps) is emitted in 4 KB pieces instead of growing without limit.
- **Control characters** (NUL, BEL, …) are shown as `·` so they can't break the
  CSV log; use the [HEX view](Terminal#hex-view) to see the exact bytes.

→ [Performance & High Data Rates](Performance-and-High-Data-Rates) ·
[Architecture](https://github.com/AlexShateljuk/isodaq/blob/master/docs/ARCHITECTURE.md)

## Sending commands

The command box at the bottom sends a line over the active connection. Pick the
**EOL** terminator (`\r\n`, `\n`, `\r`, or None) next to the Send button; use
**↑ / ↓** for command history. With **Echo** enabled, what you send is mirrored
into the terminal (TX colour). Details in [Terminal](Terminal#sending-commands).

## Counters

The status bar shows, for the active connection: **RX** and **TX** bytes,
**Rate** (lines per second), **Errors**, and **Session** (time connected).

## Troubleshooting

- **Port not listed** — press **↻**; on Linux make sure your user is in the
  `dialout` group; on macOS use the `/dev/cu.*` entry (not `/dev/tty.*`).
- **Garbage characters** — the baud rate or framing doesn't match the device.
- **Nothing received but the device is printing** — check the newline convention
  (some devices emit `\r` only) and try the **HEX** view to see what arrives.
- **Network: connection refused** — check host/port and that the bridge
  (`ser2net`, NPort…) is listening; the status bar shows `Connection failed`.

See also [FAQ & Troubleshooting](FAQ-and-Troubleshooting).
