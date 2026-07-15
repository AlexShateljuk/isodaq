# Triggers

A trigger is a rule tested against **every incoming RX line** in real time. When a
line matches, the trigger fires its configured **actions**. Manage triggers in the
**Triggers** sidebar section.

<img src="https://raw.githubusercontent.com/AlexShateljuk/isodaq/master/docs/images/panel-triggers.png" alt="Trigger editor" width="340" align="right">

## Rule types

| Type | Matches | Example |
|------|---------|---------|
| **contains** | case-insensitive substring | `Brownout Detector` |
| **regex** | Python regular expression (IGNORECASE) | `hard.?fault\|HardFault_Handler\|ERR_\d+` |
| **python** | a `lambda line: …` predicate returning bool | `lambda line: 'ERR' in line and '=' in line` |

You can set the type explicitly in the editor, or **prefix** the pattern and let
the app detect it: `[regex] …`, `[python] …`. Plain text defaults to *contains*.

## Actions

Tick any combination per trigger:

| Action | Effect |
|--------|--------|
| **Flash** | Insert a coloured banner line in the terminal at the match |
| **Log** | Write a `TRIGGER` marker into the active log sinks |
| **Sound** | System beep |
| **Pause log** | Stop the active logging session |
| **Resume log** | Restart a stopped logging session |

Every match also increments the trigger's **hit counter** (shown live in the
list), records a row in the [Trigger Events](Live-Views#trigger-events) table, and
adds a step to the [Analytics](Live-Views#analytics) staircase. **Reset** in the
Triggers header clears the hit counters.

<br clear="right">

## Notifications (webhook / Telegram)

Each trigger can POST a notification when it fires:

- **URL** — a generic webhook (receives JSON `{trigger, line, ts, text}`), **or**
  a full Telegram `sendMessage` URL
  (`https://api.telegram.org/bot<TOKEN>/sendMessage`).
- **Chat ID** — the Telegram `chat_id` (ignored for generic webhooks).
- **Test ▶** — send a test notification immediately to verify the setup.

## Saving & loading

Triggers are stored as JSON:

- `File → Save triggers…` writes the current list to a `.json` file.
- `File → Load triggers…` reads one back.

They're also part of your persisted session settings, so your working set
survives a restart without an explicit save.

## Security

`[python]` triggers **execute arbitrary code** whenever a line matches. To keep
you safe:

- When you **load a trigger file** containing Python rules, IsoDAQ Studio warns
  you and lets you choose **Load & enable**, **Load disabled**, or **Cancel**.
- Rules loaded **disabled** stay *blocked* — their code is never evaluated — until
  you open the rule in the editor and explicitly enable it.

Only enable Python rules from sources you trust. See
[SECURITY.md](https://github.com/AlexShateljuk/isodaq/blob/master/SECURITY.md).

## Examples

| Goal | Pattern | Type |
|------|---------|------|
| Catch a brownout | `Brownout Detector` | contains |
| Any hard fault variant | `hard.?fault\|HardFault_Handler` | regex |
| Error codes | `ERR_\d+` | regex |
| Value over a limit | `lambda line: 'temp=' in line and float(line.split('temp=')[1].split()[0]) > 80` | python |
