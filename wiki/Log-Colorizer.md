# Log Colorizer

The Log Colorizer highlights **RX** lines in the terminal by **log level**, using
the log format of your platform. Open it from `Settings → Log Colorizer…`.

![Log Colorizer settings](https://raw.githubusercontent.com/AlexShateljuk/isodaq/master/docs/images/dialog-log-colorizer.png)

## Colours

| Colour | Level |
|--------|-------|
| 🟢 Green | INFO |
| 🟡 Amber | WARNING / NOTICE |
| 🔴 Red | ERROR / CRITICAL |

When several enabled rules could match, the colouriser **escalates**:
ERROR beats WARNING beats INFO.

## Supported platforms

Tick one or more frameworks — matching lines are coloured automatically. Each
row shows the pattern it looks for:

| Platform | Recognises |
|----------|-----------|
| **ESP32 (ESP-IDF)** | `I (tick) tag: msg` / `W (…)` / `E (…)` |
| **Arduino / AVR** | `[INFO]` / `[WARN]` / `[ERROR]` anywhere in the line |
| **STM32 (HAL / printf)** | `INFO:` / `WARN:` / `ERROR:` at line start |
| **Zephyr RTOS** | `<inf> module: msg` / `<wrn>` / `<err>` |
| **Linux / syslog** | `INFO` / `WARNING` / `NOTICE` / `ERROR` / `CRITICAL` (whole word) |
| **MicroPython / Python logging** | `INFO:module:msg` / `WARNING:…` at line start |
| **FreeRTOS (generic)** | `[I]` / `[W]` / `[E]` at line start |
| **NuttX RTOS** | `nx_info` / `nx_warn` / `nx_err` function prefix |

Multiple platforms can be active at once — handy when a log stream mixes sources.
**Select all** / **Select none** are shortcuts; **Apply** commits your choice,
**Cancel** discards it.

## How it works

For every received line, each enabled platform's rules are tested in order and the
first match wins for that platform; across platforms the highest severity wins.
Matching only recolours the line's text — it does not alter, filter or drop
anything, and it's independent of [Triggers](Triggers) (which *act* on lines
rather than merely colouring them).

## Tips

- Colourising is purely visual and has no effect on parsing, logging, or sharing.
- If your firmware uses a custom format none of the profiles match, use a
  [Trigger](Triggers) with a `regex` rule and a **Flash** action to make specific
  lines stand out, or a [custom parser snippet](Data-Parser#custom-python-snippet)
  if you need to extract data from them.
