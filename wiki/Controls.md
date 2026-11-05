# Controls

The **Controls** tool window (right edge) holds your own buttons, toggles, sliders
and number boxes. Each one sends a command to the **active connection**, so you can
drive a device without typing.

**+ Add** creates one. Right-click a control to **Edit**, **Move** or **Delete** it.

| Type | What it does | Example command |
|------|--------------|-----------------|
| **Button** | Sends a command when clicked | `AT+RESET` |
| **Toggle** | Sends one command when switched on, another when switched off | `FAN ON` / `FAN OFF` |
| **Slider** | Sends the position while you drag | `SET PWM={value}` |
| **Number** | Sends a typed number on **Send** or `Enter` | `RPM {value}` |

- **`{value}`** is replaced by the number, with as many decimals as the **Step** has
  (a Step of `0.1` sends `3.3`).
- **Escapes:** `\r \n \t \0` and `\xHH` for any byte, so `\x02START` works for
  binary protocols.
- The command bar's **line ending** is added unless you untick it for that control.
- **Sent commands** appear in the terminal as `TX` lines and count towards TX.

## Function keys

A button or toggle can have a shortcut: **F1–F3** or **F6–F12** (F4 and F5 belong to
the app). Buttons and toggles also appear in the
[command palette](Interface-Overview#command-palette).

## When values are sent

Nothing is sent unless you act. Positions are remembered between runs, but a stored
slider or toggle isn't sent on startup or when connecting, so the screen shows
your last setting, which may not be what the device is currently using.

- A **slider** sends at most 10 times a second while you drag, and always sends the
  final position, so a slow link isn't flooded.
- If a **toggle**'s command can't be sent (not connected, for example), it flips back,
  and the terminal shows the error.

## Saving and sharing

Controls are saved with your settings and shared by all connections. **Export** writes
them to a JSON file. **Import** adds a file's controls to yours; if a shortcut is
already in use, your existing control keeps it. Files contain only text to send, so
importing one runs nothing, but check a file from someone else before using it,
because its buttons send whatever it says to your device.

## Controls or macros?

Use a control for one command you want at your fingertips, and a [macro](Macros) for a
sequence with delays and waits.
