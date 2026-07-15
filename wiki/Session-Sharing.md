# Session Sharing (Share & Join)

Stream your live serial output to a colleague anywhere in the world — both sides
just run IsoDAQ Studio. No browser, no account, no extra dependencies (pure
stdlib on both ends). One host can stream to **multiple viewers** at once.

## Two ways to connect

### 1. By address — same network (LAN / VPN)

Direct TCP: lossless and lowest latency.

```
Host  ──────────  direct TCP :9876  ──────────  Viewer
   (192.168.x.x — shown in the Share dialog)
```

### 2. By code — over the internet (through any NAT/firewall)

A small hosted **signaling + relay** server brokers the connection — no router
setup needed.

```
Host                     Signaling + Relay Server                Viewer(s)
  │── register (STUN IP) ───────>│                                  │
  │      → 6-digit code          │<──── look up code ───────────────│
  │── push serial lines ────────>│──── long-poll / deliver ────────>│
  │                              │   (relay forwards the stream)    │
```

## How to share

1. Click **Share**. IsoDAQ Studio starts the local TCP server, discovers your
   public IP via **STUN**, registers a **6-digit code** with the relay, and shows:
   the code, the LAN address, and a live **Viewers: N** count.
2. Your colleague clicks **Join**:
   - **By code** → type the 6-digit code (works through NAT/firewalls), or
   - **By address** → type your LAN `IP:port` (same network only).
3. Data flows. Click **Stop** to end — all viewers are notified and leave
   automatically.

## Connection quality

While joined, the status bar shows a coloured LED + latency (TCP ping in LAN
mode, relay `/health` round-trip in code mode):

| Colour | Latency |
|--------|---------|
| 🟢 Green | ≤ 80 ms |
| 🟡 Yellow | 81 – 250 ms |
| 🔴 Red | > 250 ms or timeout |

## LAN vs relay trade-off

- **LAN / direct TCP** — lossless and low-latency.
- **Relay (by code)** — **best-effort**: under sustained very high throughput or a
  network hiccup, individual lines may be dropped from the relayed stream.

Either way, the **host's own terminal and data logger always capture the complete
record** — sharing never affects local fidelity. A viewer can also press **▶ Start
Log** to record what they receive. → [Data Logger](Data-Logger#recording-a-shared-session)

## Hosting your own relay

A public relay is configured by default, so sharing works out of the box. If you'd
rather run your own, the `relay/` folder contains the server (pure-stdlib
`http.server`, zero dependencies):

1. Push this repo to GitHub.
2. Create a free project on [railway.app](https://railway.app) → **Deploy from
   GitHub** → set **Root Directory** to `relay`.
3. Copy the generated URL (e.g. `https://your-relay.railway.app`).
4. In IsoDAQ Studio → **Edit → Preferences** → paste it into **Signaling server
   URL**.

All users sharing a session must point at the **same** relay URL. Session codes
live in memory only (1-hour TTL) and are invalidated if the relay restarts. See
[`relay/README.md`](https://github.com/AlexShateljuk/isodaq/blob/master/relay/README.md)
for deploy details.

## Privacy note

Sharing transmits your serial lines to the connected viewers (and, in code mode,
through the relay you've configured). Don't share sessions carrying secrets you
wouldn't want a viewer or relay operator to see.
