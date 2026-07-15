# Macros

A macro is a saved **sequence of commands** sent over serial, with optional delays
and response handshakes between steps. Manage them in the **Macros** sidebar
section — each row has **Run (▶)**, **Edit (✏)** and **Delete (×)**.

![Macro editor](https://raw.githubusercontent.com/AlexShateljuk/isodaq/master/docs/images/dialog-macro-editor.png)

## Anatomy of a step

Open the editor (**+ Add** in the Macros header, or ✏ on a row). Each step has:

| Field | Purpose |
|-------|---------|
| **Command** | Text to send. Or `@file:path` to send a file's contents (see below). |
| **Delay ms** | Pause after this step before the next one. |
| **Wait for (RX pattern)** | Optional — hold until a received line contains this substring. |
| **Timeout ms** | Max time to wait for the pattern before giving up. |

Steps run top to bottom; use **▲ Up / ▼ Down** to reorder and **+ Add step** to
grow the sequence. The macro's **EOL** (line terminator) is set once for the whole
sequence.

### Delay vs. Wait-for

- **Delay only** — fire-and-forget with a fixed pause. Good for simple setups.
- **Wait-for** — proper handshaking: send `AT+START`, wait for `OK`, *then* send
  the next command. If the pattern doesn't arrive within **Timeout ms**, the macro
  **aborts** and reports which step failed.

The example above (`Start→Status`) sends `AT+START`, waits up to 2 s for `OK`,
then immediately sends `AT+STATUS?`.

## Running a macro

- Click **▶** on a row to run it. The button becomes **⏹** and a live
  `step/total` progress indicator appears; a **⏳** shows while waiting for a
  pattern.
- Click **⏹** (or run a different macro) to stop mid-sequence.
- Progress and abort/finish messages are echoed to the terminal as `MCR` lines.

## Sending files

Two ways to push a file over the serial link:

- **📁 in the Macros header** — pick a file and send it **immediately**, no macro
  needed.
- **`@file:path` as a step command** (the 📁 button in the editor's step row fills
  this in) — send a file as one step of a larger sequence. Works for text and
  binary (`.bin`, `.hex`).

## Persistence

Macros are saved with your session settings and restored on next launch. A fresh
install ships a few example AT-command macros (Version, Status, Reset,
Start→Status, Calibrate, Stop) to edit or replace.
