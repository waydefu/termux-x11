#!/usr/bin/env python3
"""V2-B3 analysis. Implements V2-B3-MATRIX-BIND sections 4 and 6 (frozen in b3-freeze.json).

    b3_analyze.py --freeze b3-freeze.json --run G=<g1.out> --run C=<c.out> [--run S=<s.out>]
                  [--run G2=<g2.out>] --out b3-analysis.json

Input: the fixture's stdout (CELL {...} lines). Per cell the statistic is per_op_ns_p50.
A cell with errors or pixel_ok false is EXCLUDED and listed; it is never averaged in.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

CENTER = {"ratio": "1x", "reuse": 1, "residency": "warm", "batch": 1, "readback": "none"}


def load(path: str):
    cells, bad, noise = {}, [], []
    for ln in Path(path).read_text().splitlines():
        if not ln.startswith("CELL "):
            continue
        d = json.loads(ln[5:])
        if d["group"] == "N":
            noise.append(d["per_op_ns_p50"])
            continue
        if d["errors"] or not d["pixel_ok"]:
            bad.append(d["id"])
            continue
        cells[d["id"]] = d
    return cells, bad, noise


def rect_key(d):
    return (d["rw"], d["rh"])


def fit(cells: dict):
    """A[rect,batch,readback] and the three rect-conditioned deltas, in log space."""
    A, dR, dU, dS = {}, {}, {}, {}
    for d in cells.values():
        lt = math.log(d["per_op_ns_p50"])
        if d["group"] == "A":
            A[(rect_key(d), d["batch"], d["readback"])] = lt
    center = {k[0]: v for k, v in A.items() if k[1] == 1 and k[2] == "none"}
    for d in cells.values():
        if d["group"] != "B":
            continue
        r = rect_key(d)
        if r not in center:
            continue
        lt = math.log(d["per_op_ns_p50"]) - center[r]
        if d["ratio"] != "1x":
            dR[(r, d["ratio"])] = lt
        elif d["reuse"] != 1:
            dU[(r, d["reuse"])] = lt
        elif d["residency"] != "warm":
            dS[(r, d["residency"])] = lt
    return A, dR, dU, dS


def predict(model, d):
    A, dR, dU, dS = model
    r = rect_key(d)
    base = A.get((r, d["batch"], d["readback"]))
    if base is None:
        return None
    out = base
    for table, key, center in ((dR, d["ratio"], "1x"), (dU, d["reuse"], 1), (dS, d["residency"], "warm")):
        if key != center:
            v = table.get((r, key))
            if v is None:
                return None
            out += v
    return out


def noise_band(noise):
    return (max(noise) / min(noise)) if len(noise) >= 2 and min(noise) > 0 else None


def winner(r, lo, hi):
    if r is None:
        return None
    if r <= lo:
        return "GPU"
    if r >= hi:
        return "CPU"
    return "TIE"


def analyze(freeze: dict, runs: dict) -> dict:
    th = freeze["thresholds"]
    loaded = {m: load(p) for m, p in runs.items()}
    out = {"schema": "b3-analysis/1", "freeze_status": freeze["status"], "runs": {}}
    for m, (cells, bad, noise) in loaded.items():
        nb = noise_band(noise)
        out["runs"][m] = {"cells": len(cells), "excluded": bad, "noise_center_ns": noise,
                          "noise_band": nb, "judgeable": nb is not None and nb < th["noise_band_max"]}
    if "G" not in loaded or "C" not in loaded:
        out["verdict"] = "INCOMPLETE"
        return out
    g, c = loaded["G"][0], loaded["C"][0]
    judgeable = out["runs"]["G"]["judgeable"] and out["runs"]["C"]["judgeable"]
    band = max(out["runs"]["G"]["noise_band"] or 99, out["runs"]["C"]["noise_band"] or 99)

    # ---- hold-out validation of the pruning (D-11)
    mg, mc = fit(g), fit(c)
    rows, agree, n_decisive, errs = [], 0, 0, []
    for cid, dg in g.items():
        if dg["group"] != "C" or cid not in c:
            continue
        dc = c[cid]
        pg, pc = predict(mg, dg), predict(mc, dc)
        mg_l, mc_l = math.log(dg["per_op_ns_p50"]), math.log(dc["per_op_ns_p50"])
        rec = {"id": cid, "measured_log_ratio": mg_l - mc_l,
               "predicted_log_ratio": None if pg is None or pc is None else pg - pc}
        if pg is not None:
            errs.append(abs(pg - mg_l))
        if pc is not None:
            errs.append(abs(pc - mc_l))
        if rec["predicted_log_ratio"] is not None and abs(rec["measured_log_ratio"]) > math.log(band):
            n_decisive += 1
            same = (rec["measured_log_ratio"] > 0) == (rec["predicted_log_ratio"] > 0)
            agree += same
            rec["winner_agrees"] = same
        rows.append(rec)
    agreement = agree / n_decisive if n_decisive else None
    med_err = statistics.median(errs) if errs else None
    pruning_ok = (agreement is not None and agreement >= th["holdout_winner_agreement_min"]
                  and med_err is not None and med_err <= th["holdout_median_log_error_max"])
    out["holdout"] = {"cells": len(rows), "decisive": n_decisive, "winner_agreement": agreement,
                      "median_abs_log_error": med_err, "pruning_valid": pruning_ok, "rows": rows}

    # ---- winner map over A and B, crossover along rect
    lo, hi = th["gpu_wins_ratio_max"], th["cpu_wins_ratio_min"]
    wmap = {}
    for cid, dg in g.items():
        if dg["group"] not in ("A", "B") or cid not in c:
            continue
        r = dg["per_op_ns_p50"] / c[cid]["per_op_ns_p50"]
        wmap[cid] = {"rect": f"{dg['rw']}x{dg['rh']}", "area": dg["rw"] * dg["rh"],
                     "batch": dg["batch"], "readback": dg["readback"], "ratio": dg["ratio"],
                     "reuse": dg["reuse"], "residency": dg["residency"], "r": r,
                     "winner": winner(r, lo, hi) if judgeable else "UNJUDGED"}
    lines = {}
    for v in wmap.values():
        k = (v["batch"], v["readback"], v["ratio"], v["reuse"], v["residency"])
        lines.setdefault(k, []).append(v)
    cross = {}
    for k, pts in lines.items():
        pts.sort(key=lambda v: v["area"])
        cx = None
        for i in range(len(pts)):
            if all(p["winner"] == "GPU" for p in pts[i:]):
                cx = pts[i]["rect"]
                break
        cross["|".join(map(str, k))] = {"crossover": cx, "n": len(pts),
                                         "winners": [(p["rect"], p["winner"], round(p["r"], 3)) for p in pts]}
    out["winner_map"] = wmap
    out["crossover"] = cross
    counts = {}
    for v in wmap.values():
        counts[v["winner"]] = counts.get(v["winner"], 0) + 1
    out["winner_counts"] = counts

    # ---- stability: the same lines against a second G run
    if "G2" in loaded:
        g2 = loaded["G2"][0]
        diff = []
        for k, pts in lines.items():
            key = "|".join(map(str, k))
            w2 = []
            for p in pts:
                cid = next((i for i, d in g.items() if f"{d['rw']}x{d['rh']}" == p["rect"]
                            and (d["batch"], d["readback"], d["ratio"], d["reuse"], d["residency"]) == k), None)
                if cid in g2 and cid in c:
                    w2.append(winner(g2[cid]["per_op_ns_p50"] / c[cid]["per_op_ns_p50"], lo, hi))
            if w2 != [p["winner"] for p in pts]:
                diff.append(key)
        out["stability_g2"] = {"lines": len(lines), "lines_changed": diff,
                               "judgeable": out["runs"]["G2"]["judgeable"]}
    out["verdict"] = ("UNJUDGED_NOISE" if not judgeable else
                      "PRUNING_VALID" if pruning_ok else "PRUNING_INVALID_EXPAND")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--freeze", required=True)
    ap.add_argument("--run", action="append", default=[])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    runs = dict(x.split("=", 1) for x in a.run)
    res = analyze(json.loads(Path(a.freeze).read_text()), runs)
    Path(a.out).write_text(json.dumps(res, indent=2, sort_keys=True) + "\n")
    print(f"B3_{res['verdict']} counts={res.get('winner_counts')} holdout="
          f"{ {k: v for k, v in res.get('holdout', {}).items() if k != 'rows'} }")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
