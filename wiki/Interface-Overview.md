# Interface Overview

IsoDAQ Studio is a single window split into a **terminal side** (left) and a
**data side** (right), with a menu bar on top and a status bar at the bottom.

![Interface anatomy](https://raw.githubusercontent.com/AlexShateljuk/isodaq/master/docs/images/ui-map.png)

| # | Region | Purpose | More |
|---|--------|---------|------|
| 1 | **Port bar** | Port / baud / framing, Connect, Share & Join, right-panel toggle | [Serial Connection](Serial-Connection) |
| 2 | **Terminal** | Timestamped, colour-coded RX/TX output | [Terminal](Terminal) |
| 3 | **Find bar** | In-terminal search (`Ctrl+F`) | [Terminal](Terminal#search) |
| 4 | **Parser strip** | Default parser type / prefix / separator for new channels | [Data Parser](Data-Parser) |
| 5 | **Command input** | Send line + EOL selector, `↑ ↓` history | [Terminal](Terminal#sending-commands) |
| 6 | **Tabs** | Graphs · Indicators · Events · Analytics | [Live Views](Live-Views) |
| 7 | **Chart area** | Plots / value cards / event log for the active tab | [Live Views](Live-Views) |
| 8 | **Sidebar** | Macros · Parsing · Data Logger · Triggers · Custom command | below |

## The menu bar

| Menu | Contains |
|------|----------|
| **File** | Save / Load triggers, Exit |
| **Device** | Refresh ports |
| **View** | Find… (`Ctrl+F`), Simple Mode (`Ctrl+Shift+M`), Right Panel (`Ctrl+Shift+R`), Theme |
| **Settings** | Log Colorizer…, Preferences… |
| **Help** | Check for Updates, About |

## The sidebar (accordion sections)

The right sidebar is a stack of collapsible sections — click a header to
expand/collapse it:

- **Macros** — command sequences with run/edit/delete and a "send file" button. → [Macros](Macros)
- **Parsing** — channel list + editor + custom Python snippet. → [Data Parser](Data-Parser)
- **Data Logger** — start/stop, format, sinks, live stats. → [Data Logger](Data-Logger)
- **Triggers** — rule list + editor with actions and notifications. → [Triggers](Triggers)
- **Custom command** — a free-text box for a multi-line/ad-hoc command.

## Tabs & pop-out

The data side has four tabs — **Graphs**, **Indicators**, **Events**,
**Analytics**. The **`⤢`** button in the tab-bar corner detaches the current tab
into its own resizable window (useful on a second monitor); closing that window
docks it back. See [Live Views](Live-Views).

## The status bar

Left to right: connection state (`● PORT · BAUD` when connected), **RX** bytes,
**TX** bytes, **Rate**, **Errors**, an optional remote-session latency LED (only
while joined to a shared session), and the **Session** timer.

## Layout modes

`View → Simple Mode` (`Ctrl+Shift+M`) toggles between:

| Mode | What's visible |
|------|----------------|
| **Advanced** (default) | Full layout — port bar, terminal, right panel, sidebar |
| **Simple** | Left panel only — port settings, terminal, command input; parser strip hidden |

You can also hide just the right panel with **`⊞`** / `Ctrl+Shift+R`.
The mode is persisted across sessions. → [Themes, Modes & Settings](Themes-Modes-and-Settings)

## Themes

Two themes, switchable at runtime via `View → Theme`:

| Dark (VS Code) | Light |
|:---:|:---:|
| ![Dark](https://raw.githubusercontent.com/AlexShateljuk/isodaq/master/docs/images/main-window-dark.png) | ![Light](https://raw.githubusercontent.com/AlexShateljuk/isodaq/master/docs/images/main-window-light.png) |
