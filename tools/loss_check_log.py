#!/usr/bin/env python3
"""
tools/loss_check_log.py — analyse an IsoDAQ session CSV for relay loss.

Run this against the CSV that IsoDAQ Studio's data logger wrote on the VIEWER
machine while it was joined to a loss-test session. Each test line looks like:

    0000123|A........................................

so the sequence number is the part before '|' in the raw column. Pass the total
count printed by tools/loss_sender.py to detect missing lines exactly.

Usage:
  python tools/loss_check_log.py session.csv --total 16384
  python tools/loss_check_log.py session.csv          # infers total = max seq + 1
"""
from __future__ import annotations

import argparse
import csv
import sys


def _ranges(nums: list[int]) -> str:
    if not nums:
        return ""
    nums = sorted(nums)
    out, start, prev = [], nums[0], nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        out.append(f"{start}-{prev}" if start != prev else f"{start}")
        start = prev = n
    out.append(f"{start}-{prev}" if start != prev else f"{start}")
    return ", ".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_file")
    ap.add_argument("--total", type=int, default=None,
                    help="total messages the sender attempted (from loss_sender.py)")
    args = ap.parse_args()

    seqs: list[int] = []
    with open(args.csv_file, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            raw = row[-1]                       # raw column is last
            if raw == "raw" or "|" not in raw:  # header / non-test line
                continue
            head = raw.split("|", 1)[0].strip().strip('"')
            if head.isdigit():
                seqs.append(int(head))

    if not seqs:
        print("  No sequence-numbered lines found in this CSV.")
        print("  Did you Start logging on the viewer before the blast began?")
        return 1

    seen = set()
    dup = 0
    reorder = 0
    last = -1
    for s in seqs:
        if s in seen:
            dup += 1
        else:
            seen.add(s)
        if s < last:
            reorder += 1
        last = s

    uniq    = len(seen)
    max_seq = max(seen)
    total   = args.total if args.total is not None else max_seq + 1
    missing = [i for i in range(total) if i not in seen]

    print("\n  ── RELAY LOSS REPORT ──────────────────────────")
    print(f"  file                  : {args.csv_file}")
    print(f"  lines logged          : {len(seqs)}")
    print(f"  unique seqs           : {uniq}")
    print(f"  highest seq seen      : {max_seq}")
    print(f"  expected total        : {total}"
          f"{'  (inferred)' if args.total is None else '  (from sender)'}")
    print(f"  duplicates            : {dup}")
    print(f"  out-of-order          : {reorder}")
    pct = (len(missing) / total * 100) if total else 0.0
    print(f"  MISSING               : {len(missing)}  ({pct:.3f}%)")
    if missing:
        rngs = _ranges(missing).split(", ")
        shown = ", ".join(rngs[:25])
        more = f"  … (+{len(rngs) - 25} more ranges)" if len(rngs) > 25 else ""
        print(f"  missing seq ranges    : {shown}{more}")

    print("  ───────────────────────────────────────────────")
    if not missing and dup == 0:
        print("  VERDICT: NO LOSS, NO DUPLICATES ✓\n")
        return 0
    print("  VERDICT: " +
          ("LOSS DETECTED ✗" if missing else "duplicates present ✗") + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
