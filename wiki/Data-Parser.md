# Data Parser

The parser turns raw serial lines into **named numeric channels** that feed the
[Live Views](Live-Views) (Graphs / Indicators / Trigger-Events columns). Manage
channels in the **Parsing** sidebar section.

<img src="https://raw.githubusercontent.com/AlexShateljuk/isodaq/master/docs/images/panel-parsing.png" alt="Parser channel editor" width="340" align="right">

## Adding a channel

Click **+ Add** in the Parsing header to open the channel editor:

| Field | Purpose |
|-------|---------|
| **Key** | The token to search for. Dot-notation is fine: `snap.pv_v`, `data.voltage`. |
| **Name** | Display label (defaults to the key). |
| **Prefix** | The line must contain this substring, otherwise it's skipped. Leave empty to match every line. |
| **Unit** | Disambiguates when several values share a key (see below). |
| **×** (scale) | Multiply the extracted number. |
| **+** (offset) | Add after scaling. |
| **Add to Chart** | Show this channel in the **Graphs** tab. |
| **Add to Indicators** | Show this channel in the **Indicators** tab. |

Use the **Test** field to paste a sample line and press **▶** — you'll see the
extracted value (`✓ 52.4`) or a miss (`✗ key not found`) before you save.

<br clear="right">

## Value formats it understands

The extractor auto-detects the common shapes:

| Line looks like | Example | Extracted |
|-----------------|---------|-----------|
| `key: value` | `snap.pv_v: 25691` | `25691` |
| `key: 0xHEX` | `vbat_v: 0x5B64` | `23396` |
| `key=valueUNIT …` | `pv=43608mV 847mA` | see Unit below |
| `key=value` | `rem_cap=500` | `500` |
| JSON object in line | `[ts]{"x":1.2,"y":3.4}` | by dot-path key |

### Using **Unit** to pick a token

When one key is followed by several unit-tagged numbers, the **Unit** field
selects which one:

```
Line:  pv=43608mV 847mA
key=pv  unit=mV  →  43608
key=pv  unit=mA  →  847
```

Leave Unit empty to take the first numeric token after the key.

### Scale & offset

Applied as `value × scale + offset`. Common uses:

- `× 0.001` to convert **mV → V**
- `× 0.1` for a fixed-point sensor
- `+` for a zero-offset calibration

## Parser modes (the parser strip)

The strip above the command box sets defaults for **new** channels (each channel
can still override its own prefix). The mode drop-down covers:

- **KEY=VALUE comma** — `DATA: ch1=1.23,ch2=4.56`
- **JSON** — one JSON object per line: `{"ch1":1.23}` (dot-paths supported)
- **CSV ordered** — values in order mapped to your channel list: `DATA:1.23,4.56`
- **Regex custom** — your own capture groups

## Custom Python snippet

For anything the field-based rules can't express, expand **Custom Snippet** in
the Parsing section and write a small function body:

```python
import re
m = re.findall(r'(\w+)\s*[=:>-]+\s*(-?[\d.]+)', line)
return {k: float(v) for k, v in m}
```

- Input: `line: str`. Output: `dict[str, float]` (or `None` for no match).
- Snippet values **win** on a name collision with a field-based channel.
- A **Test** field validates it against a sample line before you **Apply**.

> ⚠️ **Security:** the snippet is executed (`exec`) — it runs arbitrary Python on
> your machine. Only paste code you understand. See
> [SECURITY.md](https://github.com/AlexShateljuk/isodaq/blob/master/SECURITY.md).

## Where parsed values go

Every parsed value updates, in real time:

- the **live value** shown next to the channel in the Parsing list,
- the **[Graphs](Live-Views#graphs)** tab (if *Add to Chart*),
- the **[Indicators](Live-Views#indicators)** tab (if *Add to Indicators*),
- a per-channel **column** in the [Trigger Events](Live-Views#trigger-events) table.

Channels (and the custom snippet) are saved with your session settings and
restored on next launch.
