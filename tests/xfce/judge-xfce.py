#!/usr/bin/env python3
"""XFCE-FREEZE-V1 judge: xfce-run.json -> verdict. Implements freeze sections
p12_resource_proof, p13_crash_proof, validity and verdict - nothing else.

    judge-xfce.py --run xfce-run.json --freeze xfce-design-freeze.json --out verdict.json

Verdict classes (freeze.verdict):
  BASELINE_VALID   valid, crash proof PASS, every hard resource check PASS
  BASELINE_DEFECT  crash proof FAIL, or a hard resource check FAIL on a judgeable run
  INVALID          the workload did not happen as frozen - no information
The run is BASELINE whatever the class: the freeze has pga_closed=false (plan §11).

Precedence (freeze.verdict.precedence): a crash is never downgraded to INVALID.

Soft checks (fd / maps from the R10 tolerance) produce FINDINGS, never a class change:
R10 established that only the protocol counters are exact.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def chk(name: str, ok, detail=None) -> dict:
    """ok is True / False / None (None = not judgeable, reported, never a pass)."""
    return {"check": name, "ok": ok, "detail": detail}


def judge(run: dict, freeze: dict) -> dict:
    validity, crash, hard, soft = [], [], [], []
    b = freeze["binding"]
    rb = run.get("run_binding") or {}
    validity.append(chk("binding_source_sha", rb.get("source_sha") == b["source_sha"], rb.get("source_sha")))
    validity.append(chk("binding_apk_sha256", rb.get("apk_sha256") == b["apk_sha256"], rb.get("apk_sha256")))
    validity.append(chk("variant_known", rb.get("variant") in freeze["p03_compositor"]["variants"],
                        rb.get("variant")))

    # ---- session start
    sr = run.get("session_ready") or {}
    lim = freeze["p01_duration"]["session_ready_timeout_s"]
    validity.append(chk("session_ready", bool(sr.get("ready")) and (sr.get("waited_s") or 1e9) <= lim,
                        sr))

    # ---- choreography fidelity
    t0 = run.get("t0") or {}
    validity.append(chk("choreo_t0_present", bool(t0), None))
    validity.append(chk("choreo_not_test_mode",
                        t0.get("timescale") == 1.0 and t0.get("xdotool") == "xdotool"
                        and t0.get("terminal") == "xfce4-terminal",
                        {k: t0.get(k) for k in ("timescale", "xdotool", "terminal")}))
    sched = freeze["p04_window_choreography"]["schedule"]
    steps = [s for s in (run.get("steps") or []) if s.get("op") != "T0"]
    late_lim = freeze["p01_duration"]["step_lateness_invalid_s"]
    order_ok = len(steps) == len(sched) and all(
        s.get("step") == i and s.get("op") == sched[i]["op"] and s.get("planned_t") == sched[i]["t"]
        and s.get("win") == sched[i].get("win") for i, s in enumerate(steps))
    validity.append(chk("choreo_all_steps_in_frozen_order", order_ok,
                        {"executed": len(steps), "frozen": len(sched)}))
    failed = [s["step"] for s in steps if not s.get("ok")]
    validity.append(chk("choreo_every_step_ok", not failed and order_ok, {"failed_steps": failed}))
    late = [(s["step"], s.get("lateness_s")) for s in steps
            if (s.get("lateness_s") is None) or s["lateness_s"] > late_lim]
    validity.append(chk("choreo_no_step_late", not late and order_ok, {"late": late, "limit_s": late_lim}))

    # ---- capture integrity
    ce = run.get("capture_end") or {}
    validity.append(chk("logcat_alive_at_end", ce.get("logcat_alive") is True, ce))
    validity.append(chk("logcat_parsed", run.get("logcat_parsed") is True, None))
    lo = run.get("logout") or {}
    validity.append(chk("logout_clean", lo.get("ok") is True, lo))

    # ---- crash proof (p13)
    k = run.get("k") or {}

    def kx(kk, who, field):
        s = (k.get(kk) or {}).get("sample") or {}
        return (s.get(who) or {}).get(field)
    xp = [kx(kk, "x", "pid") for kk in ("K0", "K1", "K2", "K3")]
    xalive = [kx(kk, "x", "alive") for kk in ("K0", "K1", "K2", "K3")]
    crash.append(chk("x_pid_constant_K0_K3", None not in xp and len(set(xp)) == 1
                     and all(a is True for a in xalive), {"pids": xp, "alive": xalive}))
    close = run.get("close") or {}
    summ = run.get("summary") or {}
    ce = run.get("capture_end") or {}
    # No close summary in EITHER source (file, X-pid logcat line):
    #   logcat alive  -> X ended without reaching CloseScreen: crash-proof FAIL
    #   logcat dead   -> we cannot tell; unjudged here, INVALID via logcat_alive_at_end
    if summ.get("where") is None and ce.get("logcat_alive") is not True:
        ends_ok = None
    else:
        ends_ok = (close.get("x_alive_after_terminate") is False
                   and summ.get("where") == "x-close-screen")
    crash.append(chk("x_ends_only_by_terminate", ends_ok,
                     {"close": close, "where": summ.get("where"),
                      "summary_source": run.get("summary_source")}))
    ap = [kx(kk, "activity", "pid") for kk in ("K0", "K1", "K2", "K3", "K4")]
    crash.append(chk("activity_pid_constant_K0_K4", None not in ap and len(set(ap)) == 1, ap))
    fat = run.get("fatal_lines")
    crash.append(chk("no_fatal_lines", fat == [], fat))
    anr = run.get("anr_lines")
    crash.append(chk("no_anr", anr == [], anr))
    c1, c2 = run.get("xfce_clients_K1") or {}, run.get("xfce_clients_K2") or {}
    core = ("xfwm4", "xfce4-panel", "xfdesktop")
    core_ok = all(c1.get(n) and c1.get(n) == c2.get(n) for n in core)
    crash.append(chk("xfce_core_pids_constant_K1_K2", core_ok,
                     {n: [c1.get(n), c2.get(n)] for n in core}))
    closes = [s for s in steps if s.get("op") == "close"]
    term_rc = [s.get("rc") for s in closes]
    crash.append(chk("terminals_exit_0", bool(closes) and all(rc == 0 for rc in term_rc), term_rc))

    # ---- hard resource (p12), from the close summary
    def c(i):
        return summ.get(f"c{i}")
    have = summ.get("where") == "x-close-screen"
    for a, bb, label in ((12, 13, "ahb"), (14, 15, "eglimage"), (16, 17, "texture")):
        hard.append(chk(f"{label}_balanced_c{a}_c{bb}",
                        (c(a) is not None and c(a) == c(bb)) if have else None, [c(a), c(bb)]))
    for i, label in ((18, "x_registry_current"), (19, "renderer_registry_current"),
                     (20, "lease_current"), (21, "fence_timeout"), (22, "fence_error"),
                     (23, "first_failed"), (24, "generation_fatal")):
        hard.append(chk(f"{label}_c{i}_zero", (c(i) == 0) if have else None, c(i)))
    hard.append(chk("generation_close_c27_one", (c(27) == 1) if have else None, c(27)))
    hard.append(chk("summary_no_fatal",
                    (summ.get("generationFatal") == 0 and summ.get("firstFailed") == 0) if have else None,
                    {"generationFatal": summ.get("generationFatal"), "firstFailed": summ.get("firstFailed")}))
    if run.get("events_complete"):
        hard.append(chk("lease_reserved_eq_released",
                        run.get("lease_reserved_run") == run.get("lease_release_run"),
                        [run.get("lease_reserved_run"), run.get("lease_release_run")]))
        forb = {int(e) for e in freeze["p09_expected_gatea_traffic"]["forbidden_events"]}
        seen = {int(e): n for e, n in (run.get("event_hist_run") or {}).items()}
        bad = {e: n for e, n in seen.items() if e in forb}
        hard.append(chk("no_forbidden_events", not bad, bad))
        allowed = set(freeze["p09_expected_gatea_traffic"]["allowed_events"])
        unknown = {e: n for e, n in seen.items() if e not in allowed and e not in forb}
        hard.append(chk("no_unclassified_events", not unknown, unknown))
    else:
        hard.append(chk("lease_reserved_eq_released", None, "events_incomplete"))
        hard.append(chk("no_forbidden_events", None, "events_incomplete"))
    gens = run.get("generations_seen") or []
    hard.append(chk("generation_is_1", gens == [1] if gens else None, gens))
    hard.append(chk("exactly_one_gatea_bind", run.get("gatea_bind_lines") == 1, run.get("gatea_bind_lines")))

    # ---- soft (findings only)
    tol = freeze["p12_resource_proof"]["soft_from_r10_tolerance"]["checks"]
    pairs = {"x.fd_count K3-K0": ("x", "fd_count", "K0", "K3"),
             "activity.fd_count K4-K0": ("activity", "fd_count", "K0", "K4"),
             "activity.maps_count K4-K0": ("activity", "maps_count", "K0", "K4")}
    findings = []
    for key, (who, field, ka, kb) in pairs.items():
        va, vb = kx(ka, who, field), kx(kb, who, field)
        if va is None or vb is None:
            soft.append(chk(key, None, {"a": va, "b": vb}))
            continue
        d = vb - va
        ok = abs(d) <= tol[key]
        soft.append(chk(key, ok, {"a": va, "b": vb, "delta": d, "tolerance": tol[key]}))
        if not ok:
            findings.append(f"{key} delta {d} exceeds R10 tolerance {tol[key]}")

    def failed_of(lst):
        return [x["check"] for x in lst if x["ok"] is False]

    def unjudged_of(lst):
        return [x["check"] for x in lst if x["ok"] is None]
    crash_fail = failed_of(crash)
    valid_fail = failed_of(validity)
    hard_fail = failed_of(hard)
    # a missing summary with no crash to explain it is an INVALID capture, not a defect
    if crash_fail:
        verdict = "BASELINE_DEFECT"
    elif valid_fail:
        verdict = "INVALID"
    elif hard_fail:
        verdict = "BASELINE_DEFECT"
    else:
        verdict = "BASELINE_VALID"
    if verdict == "BASELINE_VALID" and unjudged_of([h for h in hard
                                                    if h["check"] not in ("lease_reserved_eq_released",
                                                                          "no_forbidden_events")]):
        # a hard counter check that could not be evaluated is never a pass
        verdict = "INVALID"
    return {
        "schema": "xfce-verdict/1",
        "freeze_status": freeze["status"],
        "variant": rb.get("variant"),
        "classification": freeze["baseline_vs_final"]["classification"],
        "verdict": verdict,
        "failed": {"crash": crash_fail, "validity": valid_fail, "hard": hard_fail},
        "unjudged": {"hard": unjudged_of(hard), "soft": unjudged_of(soft)},
        "events_complete": run.get("events_complete"),
        "findings": findings,
        "checks": {"validity": validity, "crash": crash, "hard": hard, "soft": soft},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--freeze", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    res = judge(json.loads(Path(a.run).read_text()), json.loads(Path(a.freeze).read_text()))
    Path(a.out).write_text(json.dumps(res, indent=2, sort_keys=True) + "\n")
    print(f"XFCE_{res['verdict']} variant={res['variant']} "
          f"failed={sum(len(v) for v in res['failed'].values())} findings={len(res['findings'])}")
    return {"BASELINE_VALID": 0, "BASELINE_DEFECT": 1, "INVALID": 2}[res["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
