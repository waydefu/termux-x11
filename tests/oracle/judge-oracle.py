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

MARK_RE = re.compile(r"^MARK (\w+) (BEGIN|END) ([\d.]+)$")
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
    rows = COL.parse_logcat(ev / "raw-logcat.txt", year) or []
    ours = {p for p in (x_pid, act) if p}
    fat = [r["msg"][:160] for r in rows if (r["pid"] in ours and any(
        p in r["msg"] for p in COL.FATAL_PATTERNS)) or (r["tag"] == "DEBUG" and r["lvl"] == "F"
        and any(re.search(rf"\bpid: {p},", r["msg"]) for p in ours))]
    chk("correctness", "no_fatal", fat == [], fat[:5])
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
    evs = []
    for r in rows:
        m = COL.EVENT_RE.search(r["msg"])
        if m and r["pid"] in ours:
            evs.append((r["epoch"], int(m.group(1)), int(m.group(3))))
    nxt = summ.get("nextSequence")
    seqs = {e[1] for e in evs}
    complete = nxt is not None and len(evs) == nxt and seqs == set(range(nxt))
    chk("validity", "events_complete", complete, {"lines": len(evs), "nextSequence": nxt})

    # ---- attribution
    def in_phase(ph, event):
        w = orc["marks"].get(ph) or {}
        if "BEGIN" not in w or "END" not in w:
            return None
        # logcat has ms resolution: widen by 5 ms on each side
        return sum(1 for e in evs if e[2] == event and w["BEGIN"] - 0.005 <= e[0] <= w["END"] + 0.005)
    per = orc["phases"].get("persistent") or {}
    d_p = in_phase("persistent", 5) if complete else None
    d_n = in_phase("negative", 5) if complete else None
    d_f = in_phase("fresh", 5) if complete else None
    chk("attribution", "persistent_all_direct",
        None if d_p is None else d_p == per.get("cases"), {"event5": d_p, "cases": per.get("cases")})
    chk("attribution", "negative_no_direct", None if d_n is None else d_n == 0, d_n)

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
