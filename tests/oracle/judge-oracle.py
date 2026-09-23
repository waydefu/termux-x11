#!/usr/bin/env python3
"""V2-QUAL-ORACLE-DIRECT judge. Implements oracle-freeze.json - nothing else.

    judge-oracle.py --evidence <run dir> --freeze oracle-freeze.json --out verdict.json

Reads the fixture's own output (oracle.out: MARK / PHASE_RESULT / RESULT lines), the raw
logcat (GATEA_EVENT attribution by the MARK wall-clock windows), the close summary and
the environment records the runner writes. Verdict precedence (freeze):
FAIL_CORRECTNESS > INVALID > FAIL_ATTRIBUTION > PASS.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


COL = _load("xfce_collect_v3", HERE.parent / "xfce3" / "xfce_collect.py")

MARK_RE = re.compile(r"^MARK ([\w-]+) (BEGIN|END) ([\d.]+)$")
PHASE_RE = re.compile(r"^PHASE_RESULT (\w+) cases=(\d+) fail=(\d+) maxd=(\d+) exact_px=(\d+) xnz_px=(\d+)$")


def jload(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def parse_oracle(text: str) -> dict:
    marks, phases, fails, result = {}, {}, [], None
    for ln in text.splitlines():
        m = MARK_RE.match(ln.strip())
        if m:
            marks.setdefault(m.group(1), {})[m.group(2)] = float(m.group(3))
            continue
        m = PHASE_RE.match(ln.strip())
        if m:
            phases[m.group(1)] = {k: int(v) for k, v in zip(
                ("cases", "fail", "maxd", "exact_px", "xnz_px"), m.groups()[1:])}
            continue
        if ln.startswith("FAIL"):
            fails.append(ln.strip()[:200])
        if ln.startswith("RESULT p_v1_oracle"):
            result = ln.split()[-1]
    return {"marks": marks, "phases": phases, "fail_lines": fails, "result": result}


def judge(ev: Path, freeze: dict) -> dict:
    checks = {"correctness": [], "validity": [], "attribution": []}

    def chk(group, name, ok, detail=None):
        checks[group].append({"check": name, "ok": ok, "detail": detail})

    try:
        orc = parse_oracle((ev / "oracle.out").read_text(errors="replace"))
    except OSError:
        orc = {"marks": {}, "phases": {}, "fail_lines": ["oracle.out missing"], "result": None}
    exp = freeze["expected_cases"]
    # ---- correctness (never downgraded)
    for ph in ("persistent", "fresh", "negative"):
        r = orc["phases"].get(ph)
        chk("correctness", f"{ph}_exact", None if r is None else (r["fail"] == 0 and r["maxd"] == 0), r)
    chk("correctness", "oracle_result_pass", orc["result"] == "PASS" if orc["result"] else None,
        {"result": orc["result"], "fail_lines": orc["fail_lines"][:10]})
    summ = COL.summary_counters((ev / "gatea-summary.txt").read_text(errors="replace")
                                if (ev / "gatea-summary.txt").exists() else "") or {}
    have = summ.get("where") == "x-close-screen"

    def c(i):
        return summ.get(f"c{i}")
    life = have and c(12) == c(13) and c(14) == c(15) and c(16) == c(17) \
        and all(c(i) == 0 for i in (18, 19, 20, 21, 22, 23, 24)) and c(27) == 1 \
        and summ.get("generationFatal") == 0 and summ.get("firstFailed") == 0
    chk("correctness", "lifecycle_balanced", life if have else None,
        {k: summ.get(k) for k in ("where", "c12", "c13", "c18", "c19", "c20", "c24", "c27")})

    x_pid = COL._int(COL.read_kv(ev / "x3-pid.txt").get("x3_pid"))
    act = COL._int(COL.read_kv(ev / "activity-pid.txt").get("activity_pid"))
    act_after = COL._int(COL.read_kv(ev / "activity-pid-after-close.txt").get("activity_pid_after_close"))
    begin = (orc["marks"].get("persistent") or {}).get("BEGIN")
    year = dt.datetime.fromtimestamp(begin).year if begin else dt.datetime.now().year
    ours = {p for p in (x_pid, act) if p}
    names_ours = [re.compile(rf"\bpid: {p},") for p in ours]
    # ONE streaming pass (INCIDENT-20260923: never hold a TELEMETRY=1 capture in memory);
    # keeps only the first fatal lines, the seq bitmap and the epochs of event 5.
    fat, n_fat, ev5 = [], 0, []
    seqs = COL.SeqSet()
    f = COL.open_logcat(ev / "raw-logcat.txt")
    if f is not None:
        with f:
            for r in COL.iter_logcat(f, year):
                msg = r["msg"]
                mine = r["pid"] in ours
                if (mine and any(p in msg for p in COL.FATAL_PATTERNS)) or (
                        r["tag"] == "DEBUG" and r["lvl"] == "F"
                        and any(rx.search(msg) for rx in names_ours)):
                    n_fat += 1
                    if len(fat) < 5:
                        fat.append(msg[:160])
                if mine:
                    m = COL.EVENT_RE.search(msg)
                    if m:
                        seqs.add(int(m.group(1)))
                        if int(m.group(3)) == 5:
                            ev5.append(r["epoch"])
    chk("correctness", "no_fatal", n_fat == 0, fat)
    close = jload(ev / "close.json") or {}
    chk("correctness", "x_ends_only_by_terminate",
        close.get("x_alive_after_terminate") is False and have, close)
    chk("correctness", "activity_survives_close", act is not None and act == act_after,
        [act, act_after])

    # ---- validity
    for ph in ("persistent", "fresh", "negative"):
        r = orc["phases"].get(ph)
        chk("validity", f"{ph}_case_count", r is not None and r["cases"] == exp[ph],
            None if r is None else r["cases"])
    tr = COL.read_kv_colon(ev / "x3-tracer.txt")
    chk("validity", "x3_untraced", COL._int(tr.get("TracerPid")) == 0, tr.get("TracerPid"))
    scr = [jload(ev / "screen-pre.json") or {}, jload(ev / "screen-post.json") or {}]
    chk("validity", "screen_awake_throughout",
        all(s.get("awake") is True and s.get("keyguard") is False for s in scr), scr)
    sb, sa = jload(ev / "stable-before.json"), jload(ev / "stable-after.json")
    chk("validity", "stable_unchanged", bool(sb) and sb == sa, None)
    ce = jload(ev / "capture-end.json") or {}
    chk("validity", "logcat_alive_at_end", ce.get("logcat_alive") is True, ce)
    mk = orc["marks"]
    order = ["persistent", "fresh", "negative"]
    gaps = []
    for a_, b_ in zip(order, order[1:]):
        e_, b2 = (mk.get(a_) or {}).get("END"), (mk.get(b_) or {}).get("BEGIN")
        gaps.append(None if e_ is None or b2 is None else round(b2 - e_, 4))
    negk = [k for k in mk if k.startswith("neg-") and "BEGIN" in mk[k] and "END" in mk[k]]
    need = freeze["fixture"]["quiet_gap_min_s"]
    chk("validity", "phase_gaps_quiet",
        all(g is not None and g >= need for g in gaps) and len(negk) == exp["negative"],
        {"gaps_s": gaps, "min_s": need, "negative_case_marks": len(negk)})
    nxt = summ.get("nextSequence")
    all_complete = seqs.complete(nxt)
    # V3 (ORACLE_FROZEN_V3): attribution needs every event=5 line, not every line. Source
    # (bfb5769): LEASE_GPU_OWNED is traced at ONE site (InitOutput.c:3419) and DIRECT_PUBLISH
    # at ONE site (:3484) of the same function, with only halting paths between them, so in
    # a run with no fatal the number of event=5 equals counter c0 (DIRECT_PUBLISH), which
    # lives in shared memory and cannot be lost by logd. event=5 lines == c0 therefore proves
    # no event=5 line was dropped. oracle-02 and b3-attr-01 each lost exactly one OTHER line
    # (seq 25182 / 100919) with event=5 lines == c0 (1334 / 6790).
    c0 = summ.get("c0")
    no_fatal = (have and summ.get("generationFatal") == 0 and summ.get("firstFailed") == 0
                and n_fat == 0)
    complete = all_complete or (no_fatal and c0 is not None and len(ev5) == c0)
    chk("validity", "event5_stream_complete", complete,
        {"all_events_complete": all_complete, "lines": seqs.count, "nextSequence": nxt,
         "event5_lines": len(ev5), "c0_direct_publish": c0, "no_fatal": no_fatal})

    # ---- attribution
    def in_phase(ph):
        """event 5 (LEASE_GPU_OWNED) lines inside the phase's MARK window.
        V2 window (BEGIN - 1 ms, END]: a logcat stamp is the true time truncated to the
        millisecond, i.e. in (t - 1 ms, t]. V1 widened +-5 ms on both sides while the
        phases were 17 us apart, so boundary events were counted in two phases
        (oracle-01: persistent 1300 for 1298 cases). The fixture now leaves QUIET_MS
        between windows (checked below), so the windows cannot overlap."""
        w = orc["marks"].get(ph) or {}
        if "BEGIN" not in w or "END" not in w:
            return None
        return sum(1 for t in ev5 if w["BEGIN"] - 0.001 < t <= w["END"])
    per = orc["phases"].get("persistent") or {}
    d_p = in_phase("persistent") if complete else None
    d_n = in_phase("negative") if complete else None
    d_f = in_phase("fresh") if complete else None
    chk("attribution", "persistent_all_direct",
        None if d_p is None else d_p == per.get("cases"), {"event5": d_p, "cases": per.get("cases")})
    neg = {k: (in_phase(k) if complete else None) for k in sorted(orc["marks"]) if k.startswith("neg-")}
    chk("attribution", "negative_no_direct", None if d_n is None else d_n == 0,
        {"negative_window": d_n, "per_case": neg})

    def failed(g):
        return [x["check"] for x in checks[g] if x["ok"] is False]

    def unjudged(g):
        return [x["check"] for x in checks[g] if x["ok"] is None]
    if failed("correctness"):
        verdict = "FAIL_CORRECTNESS"
    elif failed("validity") or unjudged("correctness") or unjudged("attribution"):
        verdict = "INVALID"
    elif "negative_no_direct" in failed("attribution"):
        # a direct lease on an op outside the slice is a correctness-class breach of #9
        verdict = "FAIL_CORRECTNESS"
    elif failed("attribution"):
        verdict = "FAIL_ATTRIBUTION"
    else:
        verdict = "PASS"
    return {"schema": "oracle-verdict/1", "freeze_status": freeze["status"], "verdict": verdict,
            "fresh_direct_reported": d_f, "phases": orc["phases"],
            "failed": {g: failed(g) for g in checks}, "unjudged": {g: unjudged(g) for g in checks},
            "checks": checks}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", required=True)
    ap.add_argument("--freeze", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    v = judge(Path(a.evidence), json.loads(Path(a.freeze).read_text()))
    Path(a.out).write_text(json.dumps(v, indent=2, sort_keys=True) + "\n")
    print(f"ORACLE_{v['verdict']} failed={v['failed']} fresh_direct={v['fresh_direct_reported']}")
    return {"PASS": 0, "FAIL_ATTRIBUTION": 1, "FAIL_CORRECTNESS": 1, "INVALID": 2}[v["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
