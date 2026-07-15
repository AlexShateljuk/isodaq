# Terminal

The terminal is the scrolling record of everything sent and received on the port.

![Main window — terminal on the left](https://raw.githubusercontent.com/AlexShateljuk/isodaq/master/docs/images/main-window-dark.png)

## Line colours

Each line is tagged by direction:

| Tag | Meaning | Colour |
|-----|---------|--------|
| `RX` | received from the device | green |
| `TX` | sent by you | orange |
| `SYS` | app messages (connect, theme, …) | amber |
| `ERR` | errors | red |

When the [Log Colorizer](Log-Colorizer) is active, **RX** lines are additionally
recoloured by log level (INFO green / WARN amber / ERROR red) based on your
platform's log format.

## Display toggles (port bar, row 3)

| Toggle | Effect |
|--------|--------|
| **Timestamp** | Prefix every line with `HH:MM:SS.mmm` (millisecond precision) |
| **HEX** | Show incoming bytes as hexadecimal instead of text |
| **Autoscroll** | Keep the newest line in view; turns off automatically while you scroll up, and snaps back when re-enabled |
| **Echo** | Mirror what you send (TX) back into the terminal |

## Font size & scrollback

- **A − / +** steps the terminal font between **8 and 24 pt**; the size is
  persisted across sessions and applies to existing text immediately.
- The scrollback is capped (default **5 000 lines**) so long sessions don't grow
  memory without bound; the oldest lines are trimmed first. The limit is
  configurable in **Preferences**. → [Themes, Modes & Settings](Themes-Modes-and-Settings)
- **Clear** empties the terminal (it does **not** stop logging).

## Sending commands

The input row at the bottom sends a single line:

- **EOL selector** — choose the terminator appended to what you send: `\r\n`,
  `\n`, `\r`, or **None**.
- **History** — **↑ / ↓** cycle through previously sent commands.
- **Custom command** — the sidebar's *Custom command* section holds a free-text
  box for a longer or reused command, sent with its own button.

For scripted, multi-step command flows (with delays and wait-for-response),
use [Macros](Macros).

## Search

Press **`Ctrl+F`** (or `View → Find…`) to open the in-terminal find bar:

- Type to search; **▲ / ▼** jump between matches; a hit counter shows
  `current/total`.
- **Aa** toggles case sensitivity.
- Matches are highlighted in place.

The terminal also supports **jump-to-line** from the [Trigger Events](Live-Views#trigger-events)
table — double-clicking an event row scrolls the terminal to the exact line that
fired the trigger.

## HEX mode

Toggle **HEX** to display raw incoming bytes as hex — useful for binary protocols
or when you suspect non-printable characters. Timestamps still apply; switch back
to text at any time.

## What gets recorded

The terminal is a *view*. To persist the stream to disk (independently of what's
on screen or the scrollback cap), start the [Data Logger](Data-Logger) — it
captures the complete record to CSV + SQLite regardless of terminal trimming.
