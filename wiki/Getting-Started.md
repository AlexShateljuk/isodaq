# Getting Started

## Install

### Option A — pre-built binary (no Python needed)

Download the latest release for your platform from the
[Releases page](https://github.com/AlexShateljuk/isodaq/releases).

| Platform | File | How to run |
|----------|------|-----------|
| Windows 10/11 x64 | `IsoDAQ-Studio-windows-x64.zip` | Extract → run `IsoDAQ Studio.exe` |
| Linux x64 | `IsoDAQ-Studio-linux-x64.tar.gz` | Extract → `chmod +x "IsoDAQ Studio"` → `./IsoDAQ\ Studio` |
| macOS Apple Silicon (M1+) | `IsoDAQ-Studio-macos-arm64.zip` | Extract → drag `IsoDAQ Studio.app` to Applications |
| macOS Intel | *(run from source)* | see Option B |

Releases are **not code-signed**, so the OS shows a one-time warning:

- **macOS** — right-click the app → **Open → Open**. If it says *"damaged"*,
  clear the quarantine flag: `xattr -dr com.apple.quarantine "IsoDAQ Studio.app"`.
- **Windows** — SmartScreen → **More info → Run anyway**.
- **Linux** — if it won't start, install the Qt platform plugins:
  `sudo apt-get install libxcb-cursor0 libxcb-xinerama0 libegl1`.

### Option B — run from source

```bash
git clone https://github.com/AlexShateljuk/isodaq.git
cd isodaq
pip install -r requirements.txt
python main.py
```

Requires **Python 3.10+**. Dependencies: PyQt6, pyserial, pyqtgraph, numpy.

## Your first connection

1. Plug in your device (USB-serial adapter, dev board, etc.).
2. Open IsoDAQ Studio. It scans for ports on launch — press **⟳** to rescan if
   you plugged in afterwards.
3. Pick your **PORT** from the drop-down and set the **BAUD** rate to match your
   firmware (115200 is the common default).
4. Leave framing at **8N1** and flow control at **None** unless your device needs
   otherwise.
5. Click **Connect**. The status bar turns green and shows `● PORT · BAUD`; the
   session timer starts ticking.

Incoming lines now appear in the terminal with millisecond timestamps.
Type a command in the input box at the bottom and press **Enter** (or **Send**);
use **↑ / ↓** to walk your command history.

> Full details: [Serial Connection](Serial-Connection) · [Terminal](Terminal).

## A five-minute tour

Once you see data flowing:

1. **Colour your logs** — `Settings → Log Colorizer…`, tick your platform
   (e.g. *ESP32 (ESP-IDF)*). INFO/WARN/ERROR lines are now green/amber/red.
   → [Log Colorizer](Log-Colorizer)
2. **Pull out a number** — in the **Parsing** sidebar section click **+ Add**,
   set a **Key** (e.g. `temp`), tick **Add to Chart** and **Add to Indicators**,
   and **Save**. → [Data Parser](Data-Parser)
3. **Watch it live** — switch to the **Graphs** and **Indicators** tabs.
   → [Live Views](Live-Views)
4. **Alert on a line** — in **Triggers** click **+ Add**, type
   `Brownout Detector`, pick actions, **Save**. → [Triggers](Triggers)
5. **Record it** — in **Data Logger** click **▶ Start Log** to capture the
   session to CSV + SQLite. → [Data Logger](Data-Logger)

## No hardware handy?

The repo ships a driver-free **virtual serial port** and emulator scenarios under
`emulator/`, used by the test suite. You can point IsoDAQ Studio at a
`socket://127.0.0.1:PORT` URL to exercise the full pipeline without real hardware —
this is exactly how the documentation screenshots are generated
(`python tools/gen_screenshots.py`).

## Where things live

- **Config / persisted settings:** `~/.isodaq_studio/config.json`
- **Default log output:** `~/isodaq_logs/`

Both are configurable — see [Themes, Modes & Settings](Themes-Modes-and-Settings)
and [Data Logger](Data-Logger).
