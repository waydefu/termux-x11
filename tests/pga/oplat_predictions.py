#!/usr/bin/env python3
"""Evaluate the OPLAT predictions frozen in evidence p2-pga-rca/OPLAT-PREDICTIONS.md.

    oplat_predictions.py --g <oplat-g-01> --c <oplat-c-01> --out oplat-predictions.json
    oplat_predictions.py --replication --g <oplat-g-02> --c <oplat-c-02> --out ...

Implements exactly P1-P5 of that document (thresholds copied, never tuned here), and with
--replication exactly R1-R3 of its appendix A.3. A case that is missing or carries X
errors makes the predictions that need it null, not false.

Known defect of the P reading, kept as committed (appendix A.1): 'SOLID_COPY_NOT_THE_SOURCE'
is emitted on P1 false alone, while the frozen row requires P1 false AND G ~ C.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(ev: Path):
    cases = {}
    try:
        text = (ev / "oplat.out").read_text(errors="replace")
    except OSError:
        return None
    for ln in text.splitlines():
        if ln.startswith("OPLAT "):
            d = json.loads(ln[6:])
            cases[(d["op"], d["w"], d["shape"])] = d
    return cases


def p50(cases, op, w, shape):
    d = (cases or {}).get((op, w, shape))
    if d is None or d.get("errors"):
        return None
    return d["p50_us"]


def evaluate(g, c):
    res = {}

    def both(*v):
        return all(x is not None for x in v)
    # P1 solid and copy_win, 64x64 single: G p50 >= 4000 us AND C p50 <= 1000 us (each op)
    p1 = {}
    for op in ("solid", "copy_win"):
        gv, cv = p50(g, op, 64, "single"), p50(c, op, 64, "single")
        p1[op] = {"g_p50_us": gv, "c_p50_us": cv,
                  "holds": (gv >= 4000 and cv <= 1000) if both(gv, cv) else None}
    res["P1"] = {"detail": p1, "holds": None if any(v["holds"] is None for v in p1.values())
                 else all(v["holds"] for v in p1.values())}
    # P2 nop p50 in G <= 1000 us
    gv = p50(g, "nop", 16, "single")
    res["P2"] = {"g_p50_us": gv, "holds": None if gv is None else gv <= 1000}
    # P3 solid and copy_win 64x64: G burst16 p50 >= 8 x G single p50
    p3 = {}
    for op in ("solid", "copy_win"):
        s, b = p50(g, op, 64, "single"), p50(g, op, 64, "burst16")
        p3[op] = {"g_single_p50_us": s, "g_burst16_p50_us": b,
                  "holds": (b >= 8 * s) if both(s, b) else None}
    res["P3"] = {"detail": p3, "holds": None if any(v["holds"] is None for v in p3.values())
                 else all(v["holds"] for v in p3.values())}
    # P4 over_argb 64x64 single: G p50 >= 4000 us
    gv = p50(g, "over_argb", 64, "single")
    res["P4"] = {"g_p50_us": gv, "holds": None if gv is None else gv >= 4000}
    # P5 src_argb 64x64 single: G/C p50 in [0.5, 2]
    gv, cv = p50(g, "src_argb", 64, "single"), p50(c, "src_argb", 64, "single")
    ratio = (gv / cv) if both(gv, cv) and cv > 0 else None
    res["P5"] = {"g_p50_us": gv, "c_p50_us": cv, "ratio": ratio,
                 "holds": None if ratio is None else 0.5 <= ratio <= 2}
    mech = res["P1"]["holds"] and res["P3"]["holds"]
    res["reading"] = ("MECHANISM_CONFIRMED_SYNC_FRAME_COUPLED_SOLID_COPY" if mech
                      else "SOLID_COPY_NOT_THE_SOURCE" if res["P1"]["holds"] is False
                      else "UNDECIDED")
    return res


GPU_SHAPES = ("solid", "copy_pix", "over_argb")
SIZES = (16, 64, 256, 1024)
SHAPES = ("single", "burst16")


def evaluate_replication(g, c):
    """Appendix A.3 R1-R3."""
    def agg(cells):
        if any(v is None for v in cells.values()):
            return None
        return all(cells.values())
    r1 = {}
    for op in GPU_SHAPES:
        for w in SIZES:
            for sh in SHAPES:
                gv, cv = p50(g, op, w, sh), p50(c, op, w, sh)
                r1[f"{op}/{w}/{sh}"] = None if gv is None or cv is None else gv > cv
    r2 = {}
    for op in GPU_SHAPES:
        for w in (16, 64):
            s1, b = p50(g, op, w, "single"), p50(g, op, w, "burst16")
            r2[f"{op}/{w}"] = None if s1 is None or b is None else b >= 8 * s1
    r3 = {}
    for w in SIZES:
        for sh in SHAPES:
            gv, cv = p50(g, "copy_win", w, sh), p50(c, "copy_win", w, sh)
            r3[f"copy_win/{w}/{sh}"] = (None if gv is None or cv is None or cv <= 0
                                        else 0.5 <= gv / cv <= 3)
    res = {"R1": {"cells": r1, "holds": agg(r1)}, "R2": {"cells": r2, "holds": agg(r2)},
           "R3": {"cells": r3, "holds": agg(r3)}}
    res["reading"] = ("O1_O2_CONFIRMED" if res["R1"]["holds"] and res["R2"]["holds"] and res["R3"]["holds"]
                      else "NOT_CONFIRMED")
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--g", required=True)
    ap.add_argument("--c", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--replication", action="store_true")
    a = ap.parse_args()
    g, c = load(Path(a.g)), load(Path(a.c))
    if a.replication:
        res = {"schema": "oplat-replication/1", "g": a.g, "c": a.c,
               "g_loaded": g is not None, "c_loaded": c is not None, **evaluate_replication(g, c)}
        Path(a.out).write_text(json.dumps(res, indent=2, sort_keys=True) + "\n")
        print("OPLAT_REPLICATION", {k: res[k]["holds"] for k in ("R1", "R2", "R3")}, res["reading"])
        return 0
    res = {"schema": "oplat-predictions/1", "g": a.g, "c": a.c,
           "g_loaded": g is not None, "c_loaded": c is not None, **evaluate(g, c)}
    Path(a.out).write_text(json.dumps(res, indent=2, sort_keys=True) + "\n")
    print("OPLAT_PREDICTIONS", {k: res[k]["holds"] for k in ("P1", "P2", "P3", "P4", "P5")},
          res["reading"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
