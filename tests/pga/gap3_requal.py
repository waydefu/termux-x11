#!/usr/bin/env python3
"""PGA-GAP-3 requal predicate (frozen in evidence planning-v2/pga/PGA-GAP-3-DESIGN.md).

    gap3_requal.py --evidence <gap3-requal-NN> --out gap3-requal.json

PASS iff ALL of:
  staged      X logged "Sent shared buffer ... type 2" bytes >= 3 GiB during the run
              (the workload really staged; otherwise the run proves nothing -> INVALID)
  no_trip     mem-guard never tripped (no MEM-GUARD-TRIPPED.txt)
  swap        max(swap used) - swap used at guard start < 512 MB
  avail       MemAvailable at guard start - min(MemAvailable) < 1024 MB
  maps        Activity maps_count after - before <= 16
A missing measurement is null and makes the verdict INVALID, never PASS.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

GIB = 1024 ** 3


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    ev = Path(a.evidence)
    res = {"schema": "gap3-requal/1"}
    try:
        x = int((ev / "x3-pid.txt").read_text().split("=")[1])
    except (OSError, ValueError, IndexError):
        x = None
    staged = None
    if x is not None and (ev / "raw-logcat.txt").exists():
        staged = 0
        pat = re.compile(r"Sent shared buffer width (\d+) stride \d+ height (\d+) format \d+ type 2 ")
        with open(ev / "raw-logcat.txt", encoding="utf-8", errors="replace") as f:
            for ln in f:                      # streamed (INCIDENT-20260923)
                if f" {x} " in ln[18:40]:
                    m = pat.search(ln)
                    if m:
                        staged += int(m.group(1)) * int(m.group(2)) * 4
    res["staged_bytes"] = staged
    tripped = (ev / "MEM-GUARD-TRIPPED.txt").exists()
    res["mem_guard_tripped"] = tripped
    avail, swap = [], []
    try:
        for ln in (ev / "mem-guard.log").read_text().splitlines():
            m = re.search(r"mem_available_mb=(\d+) swap_used_mb=(\d+)", ln)
            if m:
                avail.append(int(m.group(1))); swap.append(int(m.group(2)))
    except OSError:
        pass
    res["swap_growth_mb"] = (max(swap) - swap[0]) if swap else None
    res["avail_drop_mb"] = (avail[0] - min(avail)) if avail else None
    res["guard_samples"] = len(avail)
    def kv(name, key):
        try:
            return int(re.search(rf"{key}=(\d+)", (ev / name).read_text()).group(1))
        except (OSError, AttributeError, ValueError):
            return None
    mb, ma = kv("activity-maps.txt", "before"), kv("activity-maps.txt", "after")
    res["maps_before"], res["maps_after"] = mb, ma
    res["maps_delta"] = None if mb is None or ma is None else ma - mb
    checks = {
        "staged": None if staged is None else staged >= 3 * GIB,
        "no_trip": not tripped,
        "swap": None if res["swap_growth_mb"] is None else res["swap_growth_mb"] < 512,
        "avail": None if res["avail_drop_mb"] is None else res["avail_drop_mb"] < 1024,
        "maps": None if res["maps_delta"] is None else res["maps_delta"] <= 16,
    }
    res["checks"] = checks
    if checks["staged"] is False or any(v is None for v in checks.values()):
        res["verdict"] = "GAP3_REQUAL_INVALID"
    elif all(checks.values()):
        res["verdict"] = "GAP3_REQUAL_PASS"
    else:
        res["verdict"] = "GAP3_REQUAL_FAIL"
    Path(a.out).write_text(json.dumps(res, indent=2) + "\n")
    print(res["verdict"], checks, {k: res[k] for k in ("staged_bytes", "swap_growth_mb", "avail_drop_mb", "maps_delta")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
