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

### 3. Session sharing

The public relay (`relay/server.py`, hosted on Railway) is an **unauthenticated,
best-effort** convenience:

- Anyone who knows the 6-digit code can view the shared serial stream. There is
  no PIN/auth yet (tracked as a roadmap item). Do not share sensitive streams
  over the public relay; use direct LAN/VPN address sharing, or self-host the
  relay, for anything confidential.
- The relay enforces abuse limits (session caps, per-IP rate-limiting, body-size
  limits) but makes no confidentiality guarantee.
- Viewers are read-only; they cannot send commands to the host's serial port.
