#!/usr/bin/env python3
"""
tools/loss_sender.py — relay loss-test SENDER (host side).

Registers a relay session, waits for a viewer to connect, then streams a
known, sequence-numbered message stream for ~60 s across four phases with
different delays and burst sizes (trickle → steady → bursty → flood).

Every message carries a contiguous sequence number, so loss is detectable.
The final "_end" marker carries the total count, letting the receiver compute
loss on its own. Writes sent.csv and prints totals.

Usage:
  python tools/loss_sender.py
  python tools/loss_sender.py --duration 60 --base https://your-relay
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request as ur
from pathlib import Path

DEFAULT_BASE = "https://isodaq-production.up.railway.app"


def post(base: str, path: str, obj: dict, timeout: float = 10.0) -> dict:
    req = ur.Request(base + path, data=json.dumps(obj).encode(),
                     headers={"Content-Type": "application/json"})
    with ur.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--out", default="sent.csv")
    ap.add_argument("--wait", type=float, default=360.0,
                    help="max seconds to wait for a viewer")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    code = post(base, "/register", {"ip": "0.0.0.0", "port": 9876})["code"]
    print(f"\n  ┌─────────────────────────────────────────────┐")
    print(f"  │  SESSION CODE:  {code[:3]} {code[3:]}                     │")
    print(f"  └─────────────────────────────────────────────┘")
    print(f"  Relay: {base}\n")
    print(f"  On the SLAVE machine run:")
    print(f"      python tools/loss_receiver.py {code}\n")
    print(f"  Waiting for a viewer to connect ...", flush=True)

    waited = 0.0
    while True:
        try:
            r = post(base, f"/tunnel/{code}/push",
                     {"messages": [{"t": time.time(), "d": "", "k": "_hb"}]})
            if int(r.get("viewers", 0)) >= 1:
                break
        except Exception as e:
            print(f"  (waiting — push error: {e})")
        time.sleep(1.0)
        waited += 1.0
        if waited % 5 == 0:
            print(f"  ... still waiting ({int(waited)}s)")
        if waited >= args.wait:
            print("  No viewer joined in time — aborting.")
            return 1

    print("  Viewer connected — starting data blast.\n", flush=True)

    out = open(args.out, "w")
    out.write("seq,phase,send_t,status\n")

    seq = 0
    sent_ok = 0
    fails = 0

    def make(phase: str):
        nonlocal seq
        s = seq
        seq += 1
        payload = phase + "." * 40
        return s, {"t": time.time(), "d": f"{s:07d}|{payload}", "k": "rx"}

    def flush(batch, phase):
        nonlocal sent_ok, fails
        if not batch:
            return
        msgs = [m for _, m in batch]
        status = "ok"
        try:
            post(base, f"/tunnel/{code}/push", {"messages": msgs})
        except Exception:
            status = "fail"
        for s, m in batch:
            out.write(f"{s},{phase},{m['t']:.3f},{status}\n")
        out.flush()
        if status == "ok":
            sent_ok += len(batch)
        else:
            fails += len(batch)

    t0 = time.time()
    D = args.duration
    #          name        end-fraction   msgs/tick   tick interval
    phases = [("A-trickle", 0.25,           1,          0.25),
              ("B-steady",  0.50,           5,          0.10),
              ("C-bursty",  0.75,          25,          0.05),
              ("D-flood",   1.00,         200,          0.01)]

    for name, fb, n, interval in phases:
        end = t0 + D * fb
        while time.time() < end:
            batch = [make(name) for _ in range(n)]
            flush(batch, name)
            if interval:
                time.sleep(interval)
        print(f"  phase {name:9s} done — seq {seq:6d}  ok {sent_ok:6d}  fail {fails}",
              flush=True)

    total = seq
    time.sleep(1.0)   # let the last batch settle in viewer queues
    try:
        post(base, f"/tunnel/{code}/push",
             {"messages": [{"t": time.time(), "d": str(total), "k": "_end"}]})
    except Exception as e:
        print(f"  WARN: could not send _end marker: {e}")
    out.close()

    elapsed = time.time() - t0
    print("\n  ── SENDER SUMMARY ─────────────────────────────")
    print(f"  attempted (total seq) : {total}")
    print(f"  POST ok               : {sent_ok}")
    print(f"  POST failed (sender)  : {fails}")
    print(f"  duration              : {elapsed:.1f}s")
    print(f"  avg throughput        : {total / elapsed:.0f} msg/s")
    print(f"  bytes (approx)        : {total * 48 / 1024:.0f} KB")
    print(f"  wrote                 : {args.out}")
    print("  ───────────────────────────────────────────────")
    print("  → Now read the receiver's report on the slave machine.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
