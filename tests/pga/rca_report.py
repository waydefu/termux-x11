#!/usr/bin/env python3
"""Summarise one rca_sampler.py directory against its run's choreography window.

    rca_report.py <run evidence dir>      (reads <dir>.rca/ and <dir>/steps.jsonl)
Prints JSON. Descriptive only - no thresholds.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path


def pct(v, q):
    if not v:
        return None
    s = sorted(v)
    return round(s[max(1, math.ceil(q / 100 * len(s))) - 1], 3)


def main() -> int:
    ev = Path(sys.argv[1])
    rca = Path(str(ev) + ".rca")
    t0 = None
    try:
        t0 = json.loads((ev / "steps.jsonl").read_text().splitlines()[0])["t0_epoch_s"]
    except (OSError, ValueError, IndexError, KeyError):
        pass
    win = (t0, t0 + 150) if t0 else (0, 1e18)
    out = {"run": ev.name, "t0": t0}
    summ = json.loads((rca / "summary.json").read_text())
    thr = sorted(summ["threads"].items(), key=lambda kv: -kv[1]["samples"])
    out["threads"] = {tid: v for tid, v in thr if not v["top"][0][0].startswith("S:do_epoll_wait")
                      or v["comm"] == "main"}
    rtt = []
    rtt_all = []
    for ln in (rca / "rtt.txt").read_text().splitlines():
        p = ln.split()
        if len(p) == 3 and p[0] == "RTT":
            ep, ms = float(p[1]), float(p[2])
            rtt_all.append(ms)
            if win[0] <= ep < win[1]:
                rtt.append(ms)
    out["rtt_ms_window"] = {"n": len(rtt), "p50": pct(rtt, 50), "p90": pct(rtt, 90),
                            "p99": pct(rtt, 99), "max": max(rtt) if rtt else None,
                            "over_100ms": sum(1 for x in rtt if x > 100),
                            "over_1000ms": sum(1 for x in rtt if x > 1000)}
    out["rtt_ms_all"] = {"n": len(rtt_all), "p50": pct(rtt_all, 50), "p99": pct(rtt_all, 99)}
    srch = [json.loads(x) for x in (rca / "search.jsonl").read_text().splitlines() if x.strip()]
    sw = [s for s in srch if win[0] <= s["epoch"] < win[1]]
    d = [s["dur_s"] for s in sw]
    out["xdotool_search_window"] = {"n": len(sw), "p50_s": pct(d, 50), "p90_s": pct(d, 90),
                                    "max_s": max(d) if d else None,
                                    "over_3s": sum(1 for x in d if x > 3.0),
                                    "found": sum(1 for s in sw if s["found"])}
    cpu = [json.loads(x) for x in (rca / "cpu.jsonl").read_text().splitlines() if x.strip()]
    cw = [c for c in cpu if win[0] <= c["epoch"] < win[1]]
    if len(cw) >= 2:
        span = cw[-1]["epoch"] - cw[0]["epoch"]
        first, last = {}, {}
        for c in cw:                      # processes come and go: use each one's own span
            for k, v in c["ticks"].items():
                first.setdefault(k, v)
                last[k] = v
        deltas = {k: round((last[k] - first[k]) / 100.0 / span, 3) for k in last}
        by_comm = {}
        for k, v in deltas.items():
            comm = k.split(":", 1)[1]
            by_comm[comm] = round(by_comm.get(comm, 0) + v, 3)
        out["cpu_cores_window"] = dict(sorted(by_comm.items(), key=lambda kv: -kv[1]))
        out["cpu_span_s"] = round(span, 1)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
