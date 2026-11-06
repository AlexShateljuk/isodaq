# Modbus

Poll registers on Modbus devices (PLCs, drives, energy meters, sensors) and work with
the values like any other data. RTU over a serial port and Modbus TCP are both
supported, with nothing extra to install.

## Connecting

Choose the **Modbus** transport and enter the target:

| Target | Meaning |
|--------|---------|
| a serial port, e.g. `/dev/ttyUSB0` or `COM5` | Modbus RTU, using the toolbar's **Baud** and `⋯ → Data` |
| `tcp://192.168.1.10` | Modbus TCP on port 502 |
| `tcp://192.168.1.10:1502` | Modbus TCP on another port |

Many RTU devices default to even parity: set `⋯ → Data` to `8E1` if yours does.

## The register map

Add registers in the **Modbus** tool window (bottom edge). Each row is one value:

| Column | Meaning |
|--------|---------|
| **Name** | What it's called everywhere else. No spaces, `:` or `=`. |
| **Unit** | The device's unit ID on the bus |
| **Table** | `holding`, `input`, `coil` or `discrete` |
| **Address** | Counted **from 0**: holding register 40001 in a manual is address 0 |
| **Format** | `u16`, `i16`, `u32`, `i32`, `f32`. *Swapped* variants are for devices that send the low word first |
| **Scale** | Multiplies the value: `0.1` turns 215 into 21.5 |
| **Value** | The latest reading |

You can change the map while connected. A row that isn't valid is marked `invalid`
and skipped, and the others keep working. **Export** and **Import** save the map to a
JSON file. Adjacent registers are read in one request; addresses with a gap between
them are read separately, because reading across a gap can hit an address the device
doesn't have, which makes it refuse the whole request.

## Where the values go

Each poll writes ordinary lines to the terminal:

```
temp:21.5 hum:40 rpm:1500
```

So they plot by themselves with [Quick Plot](Live-Views#quick-plot), and work with
[triggers](Triggers), the [Data Logger](Data-Logger) and
[Publish to MQTT](Data-Logger#publish-to-mqtt).

## Writing

Send `write NAME VALUE` from the command bar, a [macro](Macros) or a
[control](Controls), for example a slider with the command `write pwm {value}`.

- The value is in scaled units: with a scale of `0.1`, `write temp 21.5` writes 215.
- Input registers and discrete inputs are read-only, and values that don't fit the
  format are refused.

> Writes act on real equipment immediately, with no confirmation.

## When something goes wrong

Problems appear as `modbus:` lines, so a trigger can watch for them:

| Line | Meaning |
|------|---------|
| `modbus: unit 1 holding 0: no response` | Nothing answered: check the wiring, unit ID, baud and parity |
| `modbus: unit 1 holding 500: illegal data address` | The device doesn't have that register |
| `modbus: unit 1 holding 0: CRC error` | The answer was damaged: often noise or wrong framing |
| `modbus: responding again` | It recovered |

Each problem is reported once, not at every poll, and the tool window keeps count. A
device that stops answering doesn't disconnect you. A lost serial port or TCP connection
does, and [auto-reconnect](Serial-Connection#auto-reconnect) takes over.

**Show frames** prints every request and answer as hex (`modbus>` and `modbus<`), for
comparing against a device manual.
