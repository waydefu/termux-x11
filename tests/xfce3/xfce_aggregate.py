#!/usr/bin/env python3
"""XFCE baseline aggregate: collect + judge every run directory, then summarise per
variant. Summarises only - no thresholds, no verdict of its own (the per-run verdict
is judge-xfce.py's, applied unchanged).

    xfce_aggregate.py --runtime <runtime-dc94485> --out <dir>

Writes <out>/xfce-run-<name>.json and <out>/xfce-verdict-<name>.json for every run
directory that holds a steps.jsonl (i.e. reached the choreography), and
<out>/xfce-aggregate.json. Run directories that stopped before XFCE (BLOCKED,
X3_STARTUP_CRASH) are listed with their reason and are NOT judged: no XFCE session
existed in them.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
FREEZE = json.loads((HERE / "xfce-design-freeze.json").read_text())


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


COL = _load("xfce_collect", "xfce_collect.py")
JDG = _load("judge_xfce", "judge-xfce.py")

METRICS = [
    ("composite_total", lambda r: r.get("composite_total")),
    ("xrender_ops", lambda r: (r.get("xrender_5s") or {}).get("xrender_ops")),
    ("exa_comp_check_true", lambda r: (r.get("xrender_5s") or {}).get("exa_comp_check_true")),
    ("exa_comp_check_false", lambda r: (r.get("xrender_5s") or {}).get("exa_comp_check_false")),
    ("prepare_true", lambda r: (r.get("xrender_5s") or {}).get("prepare_true")),
    ("prepare_false", lambda r: (r.get("xrender_5s") or {}).get("prepare_false")),
    ("gatea_direct", lambda r: r.get("gatea_direct")),
    ("d0a_staged", lambda r: r.get("d0a_staged")),
    ("cpu_composite", lambda r: r.get("cpu_composite")),
    ("gatea_hit_rate", lambda r: r.get("gatea_hit_rate")),
    ("gpu_composite_share", lambda r: r.get("gpu_composite_share")),
    ("exa_copy_offload", lambda r: (r.get("xrender_5s") or {}).get("exa_copy_offload")),
    ("exa_copy_attempt", lambda r: (r.get("xrender_5s") or {}).get("exa_copy_attempt")),
    ("present_offload", lambda r: (r.get("xrender_5s") or {}).get("present_offload")),
    ("x_frames_5s", lambda r: (r.get("xrender_5s") or {}).get("frames")),
    ("frames_sf", lambda r: (r.get("sf") or {}).get("frames_sf")),
    ("frame_interval_ms_p50", lambda r: (r.get("sf") or {}).get("frame_interval_ms_p50")),
    ("frame_interval_ms_p95", lambda r: (r.get("sf") or {}).get("frame_interval_ms_p95")),
    ("frame_interval_ms_p99", lambda r: (r.get("sf") or {}).get("frame_interval_ms_p99")),
    ("present_lag_ms_p50", lambda r: (r.get("sf") or {}).get("present_lag_ms_p50")),
    ("present_lag_ms_p95", lambda r: (r.get("sf") or {}).get("present_lag_ms_p95")),
    ("present_lag_ms_p99", lambda r: (r.get("sf") or {}).get("present_lag_ms_p99")),
    ("jank_frames", lambda r: (r.get("sf") or {}).get("jank_frames")),
    ("x_cpu_s", lambda r: r.get("x_cpu_s")),
    ("act_cpu_s", lambda r: r.get("act_cpu_s")),
    ("cpu_per_frame_ms", lambda r: r.get("cpu_per_frame_ms")),
    ("wall_per_frame_ms", lambda r: r.get("wall_per_frame_ms")),
    ("gpu_busy_mean", lambda r: r.get("gpu_busy_mean")),
    ("thermal_skin_max", lambda r: (r.get("thermal") or {}).get("skin_max")),
    ("thermal_cpu_max", lambda r: (r.get("thermal") or {}).get("cpu_max")),
    ("thermal_gpu_max", lambda r: (r.get("thermal") or {}).get("gpu_max")),
    ("thermal_status_max", lambda r: (r.get("thermal") or {}).get("status_max")),
    ("events_total", lambda r: r.get("events_total")),
    ("c12_ahb_acquire", lambda r: (r.get("summary") or {}).get("c12")),
    ("c25_unregister", lambda r: (r.get("summary") or {}).get("c25")),
    ("stable_bg_frames_5s_mean", lambda r: (statistics.mean(r["stable_background_frames_5s"])
                                            if r.get("stable_background_frames_5s") else None)),
]


def kval(r, k, who, field):
    return ((((r.get("k") or {}).get(k) or {}).get("sample") or {}).get(who) or {}).get(field)


def summary(vals):
    nums = [v for v in vals if isinstance(v, (int, float))]
    return {"n": len(vals), "n_numeric": len(nums), "values": vals,
            "median": statistics.median(nums) if nums else None,
            "min": min(nums) if nums else None, "max": max(nums) if nums else None,
            "null_in_some_runs": 0 < len(nums) < len(vals)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rt, out = Path(a.runtime), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    runs, skipped = {}, {}
    for d in sorted(p for p in rt.iterdir() if p.is_dir() and p.name.startswith("xfce-")):
        if not (d / "steps.jsonl").exists():
            reason = None
            for f in ("X3-STARTUP-CRASH.txt", "X3-STARTUP-CRASH.md", "BLOCKED.txt", "INVALID-CAPTURE.txt"):
                if (d / f).exists():
                    reason = f"{f}: {(d / f).read_text().strip().splitlines()[0][:160]}"
                    break
            skipped[d.name] = reason or "no steps.jsonl"
            continue
        run = COL.collect(d, FREEZE)
        v = JDG.judge(run, FREEZE)
        (out / f"xfce-run-{d.name}.json").write_text(json.dumps(run, indent=2, sort_keys=True) + "\n")
        (out / f"xfce-verdict-{d.name}.json").write_text(json.dumps(v, indent=2, sort_keys=True) + "\n")
        runs[d.name] = (run, v)
    per_variant: dict = {}
    for name, (run, v) in runs.items():
        var = v.get("variant")
        per_variant.setdefault(var, []).append((name, run, v))
    agg = {"schema": "xfce-aggregate/1", "freeze_status": FREEZE["status"],
           "classification": FREEZE["baseline_vs_final"]["classification"],
           "skipped_not_judged": skipped,
           "runs": {n: {"variant": v["variant"], "verdict": v["verdict"],
                        "failed": v["failed"], "findings": v["findings"],
                        "events_complete": v["events_complete"]} for n, (_, v) in runs.items()},
           "variants": {}}
    for var, lst in sorted(per_variant.items(), key=lambda kv: str(kv[0])):
        valid = [(n, r) for n, r, v in lst if v["verdict"] == "BASELINE_VALID"]
        block = {"runs": [n for n, _, _ in lst], "valid_runs": [n for n, _ in valid], "metrics": {}}
        for mname, fn in METRICS:
            block["metrics"][mname] = summary([fn(r) for _, r in valid])
        for who, field, ka, kb in (("x", "fd_count", "K0", "K3"), ("activity", "fd_count", "K0", "K4"),
                                   ("activity", "maps_count", "K0", "K4"), ("x", "pss_kb", "K0", "K3"),
                                   ("activity", "pss_kb", "K0", "K4"), ("x", "rss_kb", "K1", "K2"),
                                   ("activity", "rss_kb", "K1", "K2")):
            vals = []
            for _, r in valid:
                x, y = kval(r, ka, who, field), kval(r, kb, who, field)
                vals.append(None if x is None or y is None else y - x)
            block["metrics"][f"{who}.{field} {kb}-{ka}"] = summary(vals)
        agg["variants"][str(var)] = block
    (out / "xfce-aggregate.json").write_text(json.dumps(agg, indent=2, sort_keys=True) + "\n")
    for n, (_, v) in runs.items():
        print(f"{n:14s} {v['variant']} {v['verdict']:16s} failed={v['failed']} findings={v['findings']}")
    for n, why in skipped.items():
        print(f"{n:14s} NOT JUDGED ({why})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
