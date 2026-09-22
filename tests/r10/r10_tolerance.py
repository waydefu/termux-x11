#!/usr/bin/env python3
"""Derive R10 tolerances from a NOISE ledger, and freeze them.

V2-R10-DESIGN §6. The plan requires每個 metric 的雜訊範圍必須事先定義; this makes that
executable instead of asserted. A tolerance is computed from a run with NO workload,
so it measures how much the metric moves on its own.

    tolerance = 2 x max |delta between consecutive samples of the same tag|
    floor       1 for counts, 512 for KB-valued metrics

The floors exist because a noise run that happened to be perfectly still would
otherwise freeze a zero tolerance and make the first ordinary +1 a FAIL.

Counters are NOT given tolerances. They are counts of protocol events, judged
exactly (design §8 class 1); handing them a tolerance would be the first step to
explaining away a real imbalance.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

KB_SUFFIX = "_kb"
COUNT_FLOOR = 1
KB_FLOOR = 512


def series(ledger: dict) -> dict[str, list]:
    """{"<domain>.<metric>@<tag>": [values in round order]} — a metric is only
    comparable against itself at the SAME sampling point."""
    out: dict[str, list] = {}
    for r in ledger["rounds"]:
        for tag, sample in sorted(r["samples"].items()):
            if not sample:
                continue
            for dom in ("x", "activity"):
                for k, v in (sample.get(dom) or {}).items():
                    if isinstance(v, bool) or not isinstance(v, int):
                        continue
                    if k == "pid":
                        continue
                    out.setdefault(f"{dom}.{k}@{tag}", []).append(v)
    return out


def tolerances(ledger: dict) -> dict:
    tol, detail = {}, {}
    for key, vals in series(ledger).items():
        metric = key.split("@")[0]
        if len(vals) < 2:
            detail.setdefault(metric, []).append({"key": key, "values": vals,
                                                  "max_delta": None})
            continue
        deltas = [abs(b - a) for a, b in zip(vals, vals[1:])]
        md = max(deltas)
        detail.setdefault(metric, []).append({"key": key, "values": vals,
                                              "max_delta": md})
        floor = KB_FLOOR if metric.endswith(KB_SUFFIX) else COUNT_FLOOR
        tol[metric] = max(tol.get(metric, 0), max(2 * md, floor))
    # a metric seen only once anywhere still needs a floor, not a missing entry
    for metric in detail:
        if metric not in tol:
            tol[metric] = KB_FLOOR if metric.endswith(KB_SUFFIX) else COUNT_FLOOR
    return {"tolerance": dict(sorted(tol.items())), "detail": detail}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--noise-ledger", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    led = json.loads(Path(a.noise_ledger).read_text())
    if led.get("mode") != "noise":
        print(f"R10_TOLERANCE_REFUSED not_a_noise_ledger mode={led.get('mode')}")
        return 2
    if len(led["rounds"]) < 5:
        print(f"R10_TOLERANCE_REFUSED rounds_below_5 {len(led['rounds'])}")
        return 2
    if led["halts"]:
        print(f"R10_TOLERANCE_REFUSED halts_in_noise_run {led['halts']}")
        return 2
    if led["stable_before"] != led["stable_after"]:
        print("R10_TOLERANCE_REFUSED stable_changed")
        return 2
    t = tolerances(led)
    import hashlib
    out = {
        "schema_version": 1,
        "status": "R10_TOLERANCE_FROZEN_V1",
        "derived_from": str(Path(a.noise_ledger)),
        "noise_ledger_sha256": hashlib.sha256(
            Path(a.noise_ledger).read_bytes()).hexdigest(),
        "source_sha": led.get("source_sha"),
        "apk_sha256": led.get("apk_sha256"),
        "rule": "2 x max|delta between consecutive same-tag samples|, floored at "
                f"{COUNT_FLOOR} for counts and {KB_FLOOR} for *_kb",
        "counters_note": "Gate A counters get NO tolerance: they are counts of "
                         "protocol events and are judged exactly (design §8 class 1).",
        **t,
    }
    Path(a.out).write_text(json.dumps(out, indent=2) + "\n")
    print(f"R10_TOLERANCE_FROZEN metrics={len(out['tolerance'])}")
    for k, v in out["tolerance"].items():
        print(f"  {k:<36}{v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
