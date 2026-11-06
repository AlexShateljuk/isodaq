# Binary frames

Many devices send measurements as packed binary instead of text, because it takes less of
the link and less of the firmware's time. The **Binary frames** tool window (bottom edge)
turns those bytes into named values. Each frame becomes an ordinary line in the terminal:

```
temp:21.5 rpm:1500
```

So [Quick Plot](Live-Views), [triggers](Triggers), the [Data Logger](Data-Logger) and
Publish to MQTT all work on it with no further setup. It works on Serial, Network and
Bluetooth connections, and each device tab has its own layout.

## Setting up a layout

Tick **Decode binary frames**, pick a preset or describe your frame, and use **+ Add** for
each value. A frame is laid out as:

```
header | length | fields… | checksum | tail
```

| Part | What to enter |
|------|---------------|
| **Header** | Bytes that start each frame, in hex: `AA 55` |
| **Tail** | Bytes that end each frame: `0D 0A`. A frame needs a header or a tail, to be found |
| **Values** | The byte order of the fields and the length |
| **Length** | A `u8` or `u16` after the header, giving the size of the fields in bytes. **Counts extra** is for devices whose length also counts other bytes |
| **Checksum** | Any of the [checksums](Terminal#hex-commands-and-checksums), and which bytes it covers: the whole frame before it, everything after the header, or the fields only |
| **Fields** | A name, a type (`u8` to `u64`, `i8` to `i64`, `f32`, `f64`) and a scale to multiply by. `skip` ignores one byte |

For a C struct such as this one:

```c
struct __attribute__((packed)) {
    uint8_t  header[2];   // 0xAA, 0x55
    int16_t  temp;        // tenths of a degree
    uint16_t rpm;
    uint8_t  check;       // XOR of everything before it
} frame;
```

set Header to `AA 55`, Checksum to XOR-8 with *Whole frame before it*, and add `temp` as `i16`
with scale `0.1` and `rpm` as `u16`.

To try a layout without a device, paste a frame's bytes into **Try a frame**. It shows the
values, or says what doesn't fit.

## Presets

- **VOFA+ JustFloat** — any number of little-endian `f32`, then `00 00 80 7F`. Channels are
  named `ch1`, `ch2` and so on. The first tail only finds where frames start, so what comes
  before it isn't plotted.
- **SerialPlot** — its framed format: a sync word, a size, the samples, and a Sum-8 of the
  samples. A frame can hold several samples of each channel. Change the sync word, the size
  type and the fields to match your SerialPlot settings.

## What you see

The status line counts frames, the rate, and anything that went wrong:

- **Bad checksum** and **bad length or tail** — bytes that looked like a frame but weren't, or
  a frame damaged on the way. They aren't plotted, and decoding carries on with the next
  frame. A steady count usually means the layout is wrong.
- **Other bytes** — anything that isn't part of a frame. If this climbs while frames stay at 0,
  the header or byte order is probably wrong.

**Show other text** keeps text between frames, such as a boot log, readable in the terminal.
Binary that failed a check is left out rather than filling the terminal with noise.

The [HEX view](Terminal#hex-view) still shows the bytes as they arrived, and
[Share port over TCP](Serial-Connection#sharing-a-port-over-tcp) still passes them on unchanged.
The RX counter counts bytes on the wire, not the text shown.

## Limits

- One layout per connection, so frames with a type byte that selects different layouts aren't
  supported.
- `NaN` and infinity values are left out of the line, since they can't be plotted.
- Not available on SSH, MQTT or Modbus connections.
