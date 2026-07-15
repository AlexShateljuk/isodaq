# FAQ & Troubleshooting

## Connection

**My port isn't in the list.**
Press **⟳** to rescan (it only scans on launch and on demand). On Linux, add your
user to the `dialout` group (`sudo usermod -aG dialout $USER`, then re-login). On
macOS, use the `/dev/cu.*` entry, not `/dev/tty.*`.

**I see garbage / mojibake.**
The **baud** rate or **framing** doesn't match the device. Confirm the firmware's
settings and try again (115200 · 8N1 is the common default).

**The device prints but nothing shows up.**
Check the newline convention — some devices emit `\r` only. Also confirm **HEX**
mode is off. A prompt without a trailing newline is flushed after ~250 ms of
silence, so `> ` style prompts should still appear.

**It disconnects on its own.**
IsoDAQ Studio auto-detects a dropped port (cable pulled, board reset) and resets
the UI. Reconnect after the device re-enumerates (press **⟳** if the port name
changed).

## Parsing / views

**My channel shows no value.**
Use the **Test** field in the channel editor with a real sample line. Common
causes: a **Prefix** that the line doesn't contain, the wrong **Key**, or needing
a **Unit** to disambiguate multiple tokens. See [Data Parser](Data-Parser).

**A channel isn't on the chart / indicators.**
Tick **Add to Chart** / **Add to Indicators** in the channel editor. The Graphs
tab supports up to 8 channels. See [Live Views](Live-Views).

**The chart looks flat / one curve dominates.**
Channels with very different magnitudes share one Y axis, so a large one flattens
the rest. Use per-channel **scale** (e.g. `× 0.001`) to bring them into a similar
range, or pop the chart out and zoom.

## Triggers

**A `[python]` trigger doesn't fire after loading a file.**
Python rules loaded from a file are **blocked** for safety until you open the rule
in the editor and enable it. See [Triggers → Security](Triggers#security).

**My regex doesn't match.**
Regex triggers are case-insensitive (`IGNORECASE`). Test the pattern against a
real line; remember to escape special characters.

## Sharing

**"By code" join fails.**
Both sides must use the **same** relay URL (Preferences → Signaling server URL).
Codes expire after 1 hour and are invalidated if the relay restarts. If you host
your own relay, confirm it's reachable. See [Session Sharing](Session-Sharing).

**Lines are missing on the viewer.**
The relay path is **best-effort** — under very high throughput some lines may
drop. The host's terminal and log always have the full record. Use a LAN/direct
connection for lossless viewing.

## App won't launch

**Linux: nothing happens / Qt plugin error.**
Install the Qt platform plugins:
`sudo apt-get install libxcb-cursor0 libxcb-xinerama0 libegl1`.

**macOS: "unidentified developer" / "damaged".**
Releases are unsigned. Right-click the app → **Open → Open**. If it says
*"damaged"*: `xattr -dr com.apple.quarantine "IsoDAQ Studio.app"`.

**Windows: "Windows protected your PC".**
SmartScreen on an unsigned build — **More info → Run anyway**.

## Data & config locations

- **Settings:** `~/.isodaq_studio/config.json`
- **Logs (default):** `~/isodaq_logs/`

Deleting the config file resets IsoDAQ Studio to defaults.

## Still stuck?

Open an issue with your OS, IsoDAQ Studio version (`Help → About`), and the steps
to reproduce: **https://github.com/AlexShateljuk/isodaq/issues**
