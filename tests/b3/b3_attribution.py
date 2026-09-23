#!/usr/bin/env python3
"""B.3 path attribution from the ATTR run (G + TELEMETRY=1 + R8 arm, 3 iterations/cell).

    b3_attribution.py --evidence <b3-attr-01> --out b3-attribution.json

Per cell: composites the fixture issued inside its CELL_BEGIN..CELL_END window
  = (warmup + iters) * batch   timed loop
  + batch                       the untimed pixel check
and GATEA_EVENT event=5 (LEASE_GPU_OWNED) in the same window. Classification:
  DIRECT   event5 == composites
  NONE     event5 == 0     (every composite took staging or CPU)
  MIXED    otherwise       (registry pressure: some pairs direct, some refused)
The shape is always Over a8r8g8b8 -> x8r8g8b8 here, so in G mode a non-direct composite is
staging unless its FD clone failed; the analysis reports NONE/MIXED, it does not guess
further. Attribution needs the event stream complete; otherwise every cell is null.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("xc", HERE.parent / "xfce3" / "xfce_collect.py")
COL = importlib.util.module_from_spec(spec)
spec.loader.exec_module(COL)
MARK = re.compile(r"^MARK CELL_(BEGIN|END) (\S+) ([\d.]+)$")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--iters", type=int, default=3)
    ap.add_argument("--warmup", type=int, default=1)
    a = ap.parse_args()
    ev = Path(a.evidence)
    out_lines = (ev / "b3-cells.out").read_text(errors="replace").splitlines()
    windows, cells = [], {}
    cur = {}
    for ln in out_lines:
        m = MARK.match(ln)
        if m:
            kind, cid, t = m.group(1), m.group(2), float(m.group(3))
            if kind == "BEGIN":
                cur[cid] = t
            else:
                windows.append((cid, cur.pop(cid, None), t))
        elif ln.startswith("CELL "):
            d = json.loads(ln[5:])
            cells.setdefault(d["id"], d)
    x_pid = COL._int(COL.read_kv(ev / "x3-pid.txt").get("x3_pid"))
    act = COL._int(COL.read_kv(ev / "activity-pid.txt").get("activity_pid"))
    year = dt.datetime.fromtimestamp(windows[0][1]).year if windows else 2026
    # ONE streaming pass (INCIDENT-20260923): seq bitmap + the epochs of event 5 only
    seqs = COL.SeqSet()
    ev5 = []
    ours = {p for p in (x_pid, act) if p is not None}
    f = COL.open_logcat(ev / "raw-logcat.txt")
    if f is not None:
        with f:
            for r in COL.iter_logcat(f, year):
                if r["pid"] not in ours:
                    continue
                m = COL.EVENT_RE.search(r["msg"])
                if m:
                    seqs.add(int(m.group(1)))
                    if int(m.group(3)) == 5:
                        ev5.append(r["epoch"])
    summ = COL.summary_counters((ev / "gatea-summary.txt").read_text(errors="replace")
                                if (ev / "gatea-summary.txt").exists() else "") or {}
    nxt = summ.get("nextSequence")
    complete = seqs.complete(nxt)
    res = {}
    for cid, b, e in windows:
        d = cells.get(cid)
        if d is None or b is None:
            continue
        issued = (a.warmup + a.iters) * d["batch"] + d["batch"]
        n5 = (sum(1 for t in ev5 if b - 0.005 <= t <= e + 0.005) if complete else None)
        cls = None if n5 is None else "DIRECT" if n5 == issued else "NONE" if n5 == 0 else "MIXED"
        key = cid if cid not in res else f"{cid}#{sum(1 for k in res if k.split('#')[0] == cid)}"
        res[key] = {"issued": issued, "event5": n5, "class": cls, "rect": f"{d['rw']}x{d['rh']}",
                    "reuse": d["reuse"], "residency": d["residency"], "ratio": d["ratio"],
                    "batch": d["batch"], "readback": d["readback"]}
    counts = {}
    for v in res.values():
        counts[str(v["class"])] = counts.get(str(v["class"]), 0) + 1
    out = {"schema": "b3-attribution/1", "events_complete": complete, "nextSequence": nxt,
           "event_lines": seqs.count, "counts": counts, "cells": res}
    Path(a.out).write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    print(f"B3_ATTRIBUTION complete={complete} counts={counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
