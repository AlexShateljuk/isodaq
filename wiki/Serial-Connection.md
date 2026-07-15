# Serial Connection

The **port bar** at the top of the terminal side controls the connection.

## Controls

| Control | Options | Notes |
|---------|---------|-------|
| **PORT** | auto-detected serial ports | press **⟳** to rescan after plugging in |
| **BAUD** | 9600 · 19200 · 38400 · 57600 · 115200 · 230400 · 460800 · 921600 | must match your firmware |
| **Data** | 8N1 · 8E1 · 8O1 · 7N1 | data bits / parity / stop bits |
| **Flow** | None · RTS/CTS · XON/XOFF | hardware / software flow control |
| **Connect** | — | toggles to **Disconnect** once open |

Row three of the port bar carries the terminal toggles (Timestamp, HEX,
Autoscroll, Echo), the font-size stepper and **Clear** — see [Terminal](Terminal).

## Connecting

1. Select **PORT** and **BAUD** (set framing/flow if your device needs it).
2. Click **Connect**.
3. The status bar turns green and reads `● PORT · BAUD`, the session timer starts,
   and a `Connected: PORT @ BAUD` line is written to the terminal.

Click **Disconnect** (same button) to close the port. If the device disappears
(cable pulled, board reset), IsoDAQ Studio **auto-detects the disconnect** and
resets the UI to the disconnected state.

## Under the hood — the high-throughput reader

The serial reader runs on its own `QThread` and is tuned for sustained high baud
rates:

- **Chunked reads** — it reads whatever is in the OS buffer (`in_waiting`) in one
  go rather than byte-by-byte, so there's no per-line latency even at 921 600 baud
  (~90 KB/s). Each read iteration is on the order of ~100 µs.
- **Line assembly** — bytes are buffered and split on the newline; complete lines
  are delivered to the GUI via a **queued Qt signal** (`line_received`), keeping
  the serial thread off the GUI thread.
- **Partial-line flush** — a line that arrives without a trailing `\n` is held
  briefly and then flushed after **250 ms of silence**, so a device that prints a
  prompt without a newline (e.g. `> `) still shows up promptly.

See [Architecture](https://github.com/AlexShateljuk/isodaq/blob/master/docs/ARCHITECTURE.md)
for the full threading model.

## Sending commands

The command box at the bottom sends a line over the port. Pick the **EOL**
terminator (`\r\n`, `\n`, `\r`, or None) next to the Send button; use **↑ / ↓**
for command history. With **Echo** enabled, what you send is mirrored back into
the terminal (TX colour). Details in [Terminal](Terminal#sending-commands).

## Counters

The status bar tracks **RX** and **TX** byte totals and the **Session** time for
the current connection. These reset when you start a new session.

## Troubleshooting

- **Port not listed** — press **⟳**; on Linux make sure your user is in the
  `dialout` group; on macOS use the `/dev/cu.*` entry (not `/dev/tty.*`).
- **Garbage characters** — the baud rate or framing doesn't match the device.
- **Nothing received but device is printing** — check the newline convention and
  the **HEX** toggle; some devices emit `\r` only.

See also [FAQ & Troubleshooting](FAQ-and-Troubleshooting).
