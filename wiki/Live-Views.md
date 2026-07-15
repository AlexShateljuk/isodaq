# Live Views

The data side has four tabs that visualise the parsed channels and trigger
activity. Any tab can be **popped out** into its own resizable window with the
**`⤢`** button in the tab-bar corner (close it to dock it back).

Channels appear here once you tick **Add to Chart** / **Add to Indicators** in the
[Data Parser](Data-Parser).

## Graphs

![Graphs tab](https://raw.githubusercontent.com/AlexShateljuk/isodaq/master/docs/images/tab-graphs.png)

Real-time scrolling line chart (pyqtgraph):

- Up to **8 simultaneous channels**, each with a distinct colour.
- **60-second rolling window**, backed by a **20 000-point ring buffer** per
  channel — old points age out, memory stays bounded.
- **Mouse pan and zoom** on both axes.
- A colour **legend strip** below the chart names each channel.
- **PNG** exports the plot as an image; **CSV** exports the buffered data (a
  unified time index with one column per channel).

## Indicators

![Indicators tab](https://raw.githubusercontent.com/AlexShateljuk/isodaq/master/docs/images/tab-indicators.png)

A responsive grid of large value cards — one per channel — updated on every
parsed data point.

**Colour thresholds:** double-click a card to open its threshold editor. Add
`≥ value → colour` rules; the highest matching threshold wins, otherwise the
card's base colour is used. This turns a card red when a reading crosses a limit,
green when it's healthy, and so on.

<img src="https://raw.githubusercontent.com/AlexShateljuk/isodaq/master/docs/images/dialog-thresholds.png" alt="Threshold editor" width="360">

## Trigger Events

![Trigger Events tab](https://raw.githubusercontent.com/AlexShateljuk/isodaq/master/docs/images/tab-events.png)

A session log of every [Trigger](Triggers) match:

- Columns: **Time · Trigger · Raw line**, plus **one column per active parsed
  channel** (so you see the channel values captured at the moment the trigger
  fired).
- Alternating row colours, row selection, non-editable.
- An event counter in the header; **Clear** empties the table.
- **Double-click a row** to jump the terminal to the exact line that fired the
  trigger.

## Analytics

![Analytics tab](https://raw.githubusercontent.com/AlexShateljuk/isodaq/master/docs/images/tab-analytics.png)

A cumulative **hit-count staircase** per trigger: X = elapsed seconds,
Y = total hits. The step shape makes **burst periods and quiet periods** obvious
at a glance — a wall of steps means something is firing repeatedly.

- The trigger list here stays in sync with your [Triggers](Triggers) automatically.
- **PNG** / **CSV** export the chart / the raw `(time, trigger)` hit records.
- **Clear** resets the curves and the time origin.

## Pop-out

Click **`⤢`** to detach the active tab into a standalone window — for example,
put **Graphs** on a second monitor while you keep the terminal in focus. The
window inherits the current theme; closing it returns the tab to its original
slot.
