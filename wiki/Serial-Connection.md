# Serial Connection

The **connection toolbar** across the top of the window controls the active
connection (the selected [connection tab](Multiple-Connections)).

## Controls

| Control | Options | Notes |
|---------|---------|-------|
| **Transport** | Serial · Network · SSH · BLE · MQTT · [Modbus](Modbus) | what kind of source this connection reads |
| **Port / target** | serial ports · a URL · a saved SSH session | see the transports below |
| **↻** | — | rescan serial ports or scan for Bluetooth devices (in SSH mode it becomes **✎** to edit the session) |
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

### BLE (Bluetooth Low Energy)

For boards with a serial-over-Bluetooth service. Press **↻** to scan for about five
seconds and pick a device: serial devices are listed first, then by signal strength.
You can also type an address.

These services are recognised: **Nordic UART** (the usual ESP32 and nRF choice),
**HM-10** style modules, **Microchip Transparent UART** and **u-blox SPS**. Any other
device with one notifying and one writable characteristic in a service is tried too.
If there's nothing usable, the terminal says so and disconnects.

Long messages are split to fit the link. DTR, RTS and BREAK don't exist over BLE.

On macOS the first scan asks for permission to use Bluetooth. When running from source,
the permission belongs to your terminal or editor. Needs `bleak` (bundled in the release
binaries; `pip install bleak` when running from source).

### MQTT

Uses a broker as the data source. Type a URL into the target field:

```
mqtt://[user@]host[:port]/topic/filter[,other/#][?tx=publish/topic]
```

| URL | Meaning |
|-----|---------|
| `mqtt://broker.local/sensors/#` | everything under `sensors/` (receive only) |
| `mqtt://broker.local/a/#,b/+/c` | several topic filters |
| `mqtt://broker.local/dev/1/#?tx=dev/1/cmd` | what you send is published to `dev/1/cmd` |
| `mqtts://ann@broker.local/dev/#` | TLS (port 8883), as user `ann` |

Each message becomes one terminal line, `topic: payload`, so triggers, the
[parser](Data-Parser), logging and [Quick Plot](Live-Views#quick-plot) work as usual: a
number on `sensors/temp` plots by itself as `sensors/temp`, and a JSON payload can be read
by a Parsing channel.

Passwords aren't part of the URL, because targets are saved in the settings file. If the
URL names a user you're asked once per run, and the password is never written to disk.
Plain `mqtt://` is unencrypted; use `mqtts://` on networks you don't trust. To send data
*to* a broker from any connection, see [Publish to MQTT](Data-Logger#publish-to-mqtt).

Needs `paho-mqtt` (bundled in the release binaries; `pip install paho-mqtt` when running
from source).

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

## Build & flash

The **Flash** tool window (bottom edge) runs your build / upload command without
you having to disconnect first:

1. Pick a preset or type a command, and set the **Project folder**. Each connection
   has its own.

   | Tool | Command |
   |------|---------|
   | PlatformIO | `pio run -t upload --upload-port {port}` |
   | ESP-IDF | `idf.py -p {port} flash` |
   | Arduino CLI | `arduino-cli compile --upload -p {port} .` |
   | esptool | `esptool.py --port {port} write_flash 0x0 firmware.bin` |
   | ST-Link | `st-flash --reset write firmware.bin 0x08000000` |
   | Anything else | `make flash` |

   `{port}` and `{baud}` are filled in from the connection.
2. Press **Build & flash** (or `F5`, or *Build & flash* in the
   [command palette](Interface-Overview#command-palette)).

The port is released, the output appears in the terminal as `BLD` lines, and
afterwards the terminal says how it went. If the connection was connected it
reconnects by itself, waiting for a USB board that re-enumerates; if it wasn't, it
stays disconnected. **Stop** ends the command and everything it started.

The command runs in your login shell, so tools on your `PATH` work even when the
app was started from Finder or a desktop launcher. Anything set up only for
interactive shells has to be part of the command, e.g.
`. ~/esp/esp-idf/export.sh && idf.py -p {port} flash`. See
[SECURITY.md](https://github.com/AlexShateljuk/isodaq/blob/master/SECURITY.md).

## Sending a file (XMODEM / YMODEM)

For bootloaders and firmware that receive uploads over the serial link itself. It's in
the **Flash** tool window, and works on Serial and Network connections.

1. Connect, and tell the device to receive from the command bar (for example `rx`, or
   `loady` in U-Boot).
2. Pick the protocol and the file, then press **Send file**.

| Protocol | Blocks | Notes |
|----------|--------|-------|
| **XMODEM** | 128 bytes | Uses CRC or checksum, whichever the receiver asks for |
| **XMODEM-1K** | 1024 bytes | Falls back to 128 for a checksum-only receiver |
| **YMODEM** | 1024 bytes | Also sends the file name and exact size |

XMODEM pads the last block with `0x1A`; YMODEM tells the receiver the real size. The
transfer waits up to a minute for the receiver to start, and a stray `C` in a device's
banner won't start it by mistake.

While a file is going across, the transfer has the port to itself: received data isn't
shown or logged, and nothing else can send. Afterwards the terminal carries on as before.
Sending only; receiving a file isn't supported yet.

## Sharing a port over TCP

**Device → Share port over TCP…** lets other programs use the connection while IsoDAQ stays
connected to it: a flashing or logging tool, a script, `nc 127.0.0.1 7777`, or another IsoDAQ
with the Network transport and `socket://host:7777`. Bytes pass unchanged in both directions.
Serial, Network, SSH and Bluetooth connections can be shared; MQTT and Modbus can't, as they
aren't byte streams.

- Up to 8 clients. Each gets everything the device sends; what any of them sends goes to the
  device, and shows in the terminal as `[tcp] …`.
- A client that falls more than 1 MB behind is disconnected, and the terminal says so, so that
  it can't hold up the device or the others.
- It stays on through an automatic reconnect or a flash, and ends when you disconnect. It is
  off at every start.
- During a [file transfer](#sending-a-file-xmodem--ymodem) clients get nothing, and what they
  send is refused.

**There is no password and no encryption**, and clients can write to the device. It listens on
this computer only unless you choose **Any computer on the network**; see
[SECURITY.md](https://github.com/AlexShateljuk/isodaq/blob/master/SECURITY.md).

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
