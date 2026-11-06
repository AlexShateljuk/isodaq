# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

Report privately via GitHub's [**Report a vulnerability**](https://github.com/AlexShateljuk/isodaq/security/advisories/new)
(Security → Advisories), or email **sashok0127@gmail.com** with the subject
`IsoDAQ SECURITY`. Include steps to reproduce and, if possible, a minimal test
case. You can expect an initial response within a few days.

This is a small volunteer-maintained project — there is no bug-bounty, but your
report is genuinely appreciated and will be credited (unless you prefer
otherwise).

## Supported versions

Only the latest released version receives fixes. Please reproduce on the newest
release before reporting.

## Things you should know (by design)

IsoDAQ Studio runs user-authored code. This is intentional and powerful, but it
means **a configuration file is executable content — treat it like a script, not
data.**

### 1. `[python]` triggers execute arbitrary code

A trigger of type `python` is a Python expression `eval()`'d against every serial
line (`core/triggers.py`). It has full access to the Python runtime.

- Triggers you author in the app, and your own `config.json`, are trusted.
- Triggers loaded from **someone else's file** (`File → Load triggers…`) are
  **blocked**: they load disabled and are never `eval()`'d until you explicitly
  choose *Load & enable*. A warning dialog lists how many executable rules the
  file contains. Only enable them if you trust the author.

### 2. Custom parser snippets execute arbitrary code

The parser "custom snippet" (`core/data_parser.py set_snippet`) is compiled with
`exec()` and run on every line. Snippets are authored only inside the app (there
is no "load snippet from file" path), so the exposure is limited to code you type
yourself — but be aware it is real code with full runtime access.

**Rule of thumb:** never load a trigger/config file from an untrusted source and
enable its Python rules, exactly as you would never `python somebody_elses.py`
without reading it.

### 3. The Flash command runs in your shell

The Flash tool window (`core/flash_runner.py`) passes the command you typed to
your login shell, with your privileges, when you press **Build & flash** or F5.
It is stored in your own `config.json`; only trust a `config.json` from someone
else as far as you would trust a script from them. Nothing received from a
device or a shared session can start it or change it: viewers are read-only.

### 4. MQTT

Plain `mqtt://` sends data, and any password, unencrypted; use `mqtts://` on networks
you don't trust. Passwords are kept in memory for the current run and never saved, and
a URL containing one is refused. **Publish to MQTT** sends device data to the broker
you enter, and is off at every start until you switch it on. Anyone subscribed to a
topic on that broker can read it. Commands you type go to the one topic in `tx=`;
nothing received over MQTT is executed.

### 5. Sharing a port over TCP

**Share port over TCP** has no password and no encryption, and a client can write to the
device, so anything that can reach the port can do what you can do at the terminal. That
includes other programs and users on your computer. It is off at every start and listens
on this computer only by default. **Any computer on the network** warns you first, and the
status bar shows the port and the number of clients for as long as it is on. To reach it
from elsewhere, an SSH tunnel to the local port is safer than opening it to the network.

### 6. Trigger snapshots and notification URLs

A trigger's notification sends the matching line to its URL. With **Send to the URL below**
on, it sends the whole snapshot as well, which can be far more of the device's output.
A trigger file you load can contain a URL, so check the rules in files from others. Loading
a file always switches snapshot sending off, whatever the file says. Snapshots are plain
text in the Data Logger's folder, under `snapshots/`.

### 7. Session sharing

The public relay (`relay/server.py`, hosted on Railway) is an **unauthenticated,
best-effort** convenience:

- Anyone who knows the 6-digit code can view the shared serial stream. There is
  no PIN/auth yet (tracked as a roadmap item). Do not share sensitive streams
  over the public relay; use direct LAN/VPN address sharing, or self-host the
  relay, for anything confidential.
- The relay enforces abuse limits (session caps, per-IP rate-limiting, body-size
  limits) but makes no confidentiality guarantee.
- Viewers are read-only; they cannot send commands to the host's serial port.
