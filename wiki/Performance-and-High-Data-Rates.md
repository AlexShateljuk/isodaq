# Performance & High Data Rates

IsoDAQ Studio is built to keep up with fast links — 921 600 baud, several ports at
once, flash read-outs — without losing data and without freezing the window.

## What was measured

Test streams carried a sequence number in every line, so any lost or reordered
line would be counted. The ports were virtual serial ports opened through the
app's normal serial reader; the logger (CSV + SQLite) and triggers were on.

| Scenario | Result |
|----------|--------|
| 1 port @ 921 600 baud (~1 900 lines/s) | on screen ≈ 25 ms after arrival, no freeze, **0 lines lost** |
| 4 ports @ 921 600 baud (~7 000 lines/s total) | **0 lines lost**, window stays responsive |
| Flash dump: 1–8 KB packets back-to-back on two ports + 4 MB of raw binary on a third, logging on | **0 lines lost** in CSV and SQLite, no crash |
| Everything at once: ≈ 12 Mbit/s over 4 ports, triggers firing, switching tabs, sending, searching, changing theme mid-stream | **0 lines lost** in the logs; screen kept within ~1 s of live |

Numbers are from an Apple-Silicon Mac; slower machines reach their limit sooner,
but the behaviour described below stays the same.

## What happens when data arrives faster than the screen can draw

Received lines go through three steps, and only the last one is optional:

1. **Logged** — written to the [Data Logger](Data-Logger) (if a session is running).
2. **Trigger-checked** — matched against your [Triggers](Triggers) (events,
   notifications, analytics).
3. **Shown** — terminal, parser, charts and indicators.

The window processes received lines in short time slices and repaints in
between, so it never freezes. If the display falls **more than one second
behind**, the oldest lines are **logged and trigger-checked but not drawn**, and
when it catches up the terminal says:

```
SYS  Display caught up — 23,412 lines were logged and trigger-checked but not drawn (overload).
```

Your files and trigger events are complete; only the on-screen copy skipped
those lines.

## Tips for very fast streams

- **Collapse tool windows you don't need** — hidden charts and analytics cost
  almost nothing (they redraw only while visible).
- **Use triggers for the lines that matter** rather than scrolling — they see
  every line even when the screen can't.
- **Flash dumps / binary data**: turn on the [HEX view](Terminal#hex-view). Data
  without line breaks is split into 4 KB pieces, and control bytes show as `·`
  in text mode.
- **Triggers that match thousands of lines** add a flash banner per match to the
  terminal; turn off the trigger's *Flash* action if that gets noisy — the
  Events table still records each hit.
- Switching **theme** with several busy connections takes a second or two; data
  keeps queueing safely meanwhile.

## How it works (for the curious)

- Each connection's reader runs on its own thread, reads whatever the OS has
  buffered in one go, splits it into lines once per read and hands them to the
  window **in batches**.
- The window drains received lines every 15 ms with a budget of 12 ms of work,
  round-robin across connections.
- The terminal paints in batches (~30 times a second); charts and analytics
  redraw on timers only while visible; the Events table keeps the newest 5 000 rows.
- The logger writes from its own thread: CSV in batches every 200 ms, SQLite with
  `executemany` in WAL mode.

Details: [docs/ARCHITECTURE.md](https://github.com/AlexShateljuk/isodaq/blob/master/docs/ARCHITECTURE.md).
