#!/usr/bin/env python3
"""autoshard_judge.py <out> — AUTO-SHARD-01 parts A/B/C verdict (evidence/session/proot/AUTO-SHARD-01-FREEZE.md).

Reads what autoshard_inner.sh recorded under <out> and the per-set autoshard logs. Prints one line per case
and a final verdict: AUTOSHARD_RULES_PASS / AUTOSHARD_RULES_FAIL / AUTOSHARD_INVALID (R1 or R2 not red)."""
import collections
import json
import os
import sys


def read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def verdict_of(out, set_, case):
    """SHARDED / NOT_SHARDED / BROKEN for cells/<set>-<case> (or a nested path under it)."""
    tt = read(f"{out}/{set_}.test_tracer")
    tp = read(f"{out}/cells/{set_}-{case}/tracer") if "/" not in case else read(f"{out}/cells/{case}/tracer")
    if not tt or not tp or tp == "0":
        return "BROKEN", tp
    return ("NOT_SHARDED" if tp == tt else "SHARDED"), tp


def log_multiset(out, set_):
    """{(decision, basename of exe): count} from <out>/<set>.autoshard.log"""
    counts = collections.Counter()
    for line in (read(f"{out}/{set_}.autoshard.log", "") or "").splitlines():
        parts = line.split(" ", 4)            # ts pid=N shard|skip what exe
        if len(parts) == 5:
            counts[(f"{parts[2]} {parts[3]}", os.path.basename(parts[4]))] += 1
    return counts


EXPECTED_LOG = {
    # every shard that starts adds one "skip same_app" line: the program's own execve inside the new tracer
    # (found by the dry run of 2026-09-26 16:07, before the judged run)
    "main": {("shard electron", "fakeelectron"): 3,          # e1, l1, x1
             ("skip chromium_child", "fakeelectron"): 1,     # e2
             ("skip debugging_pipe", "fakeelectron"): 1,     # e3
             ("skip electron_run_as_node", "fakeelectron"): 1,
             ("skip f8_no_shard", "fakeelectron"): 1,        # e5
             ("skip stdio_pipe", "fakeelectron"): 2,         # e6, e7
             ("skip same_app", "fakeelectron"): 4,           # e8 + in-shard exec of e1, l1, x1
             ("skip same_app", "helper"): 1,                 # e9
             ("skip explicit_shard", "fakeelectron"): 1,     # e10
             ("shard electron", "fakeelectron2"): 1,         # e11
             ("skip same_app", "fakeelectron2"): 1,          # in-shard exec of e11
             ("shard terminal", "xterm"): 1,                 # t1
             ("skip same_app", "xterm"): 1},                 # in-shard exec of t1
    "f1": {("shard electron", "fakeelectron"): 1, ("skip f8_no_shard", "fakeelectron"): 1},
    "f2": {("skip no_f8_shard", "fakeelectron"): 1},
    "r1": {},
}


def judge(out):
    rows, fail, invalid = [], [], []

    def expect(name, ok, detail):
        rows.append(f"{'ok  ' if ok else 'FAIL'} {name}: {detail}")
        if not ok:
            fail.append(name)

    work = read(f"{out}/work", "")
    for s in ("main", "f1", "f2", "r1"):
        if read(f"{out}/{s}.inner_done") != "done":
            expect(f"{s}.inner", False, f"inner harness did not finish (shard rc {read(f'{out}/{s}.shard.rc')})")
        env = read(f"{out}/{s}.inner_f8_env", "")
        expect(f"{s}.clean_env", env == "", f"F8_* in the inner harness: {env!r}")

    # A
    v1, tp1 = verdict_of(out, "main", "e1")
    d1 = f"{out}/cells/main-e1"
    expect("E1", v1 == "SHARDED", f"{v1} tracer={tp1}")
    expect("E1.shard_exe", read(f"{d1}/shard_exe") == f"{work}/fakeapp/fakeelectron", f"F8_SHARD_EXE={read(f'{d1}/shard_exe')!r}")
    expect("E1.sharded", read(f"{d1}/sharded") == "1", f"F8_SHARDED={read(f'{d1}/sharded')!r}")
    argv = (read(f"{d1}/tracer.argv", "") or "").splitlines()
    expect("E1.no_kill_on_exit", bool(argv) and "--kill-on-exit" not in argv, f"tracer argv has {len(argv)} words, kill-on-exit={'--kill-on-exit' in argv}")
    for c in ("e2", "e3", "e4", "e5", "e6", "e7", "e10", "e12"):
        v, tp = verdict_of(out, "main", c)
        expect(c.upper(), v == "NOT_SHARDED", f"{v} tracer={tp}")
    for c in ("e8", "e9"):
        v, tp = verdict_of(out, "main", f"main-e1/{c}")
        expect(c.upper(), tp is not None and tp == tp1, f"tracer={tp} (E1 shard tracer {tp1})")
    v11, tp11 = verdict_of(out, "main", "main-e1/e11")
    expect("E11", v11 == "SHARDED" and tp11 != tp1, f"{v11} tracer={tp11} (E1 shard tracer {tp1})")
    vt, tpt = verdict_of(out, "main", "t1")
    expect("T1", vt == "SHARDED", f"{vt} tracer={tpt}")
    o = dict(kv.split("=", 1) for line in (read(f"{out}/cells/main-t1/orphan.txt", "") or "").splitlines()
             for kv in line.split(" ") if "=" in kv)
    expect("T1.orphan", o.get("orphan_alive_after_1s") == "yes" and o.get("orphan_comm") == "sleep"
           and o.get("orphan_tracer") == tpt and o.get("t1_tracer_alive") == "yes",
           f"alive={o.get('orphan_alive_after_1s')} comm={o.get('orphan_comm')} orphan_tracer={o.get('orphan_tracer')} t1_tracer_alive={o.get('t1_tracer_alive')}")
    expect("T1.tracer_ends", o.get("t1_tracer_gone_after_kill") == "yes", f"gone={o.get('t1_tracer_gone_after_kill')} after_ds={o.get('after_ds')}")

    # B
    for s in ("f1", "f2"):
        v, tp = verdict_of(out, s, s)
        rc = read(f"{out}/{s}-{s}.rc")
        expect(s.upper(), v == "NOT_SHARDED" and rc == "7", f"{v} tracer={tp} rc={rc}")
    expect("F1.refuse_logged", "F8_SHARD_REFUSE" in (read(f"{out}/f1-f1.stderr", "") or ""), "stderr has F8_SHARD_REFUSE")

    # logs
    for s, exp in EXPECTED_LOG.items():
        got = log_multiset(out, s)
        expect(f"{s}.log", dict(got) == exp, "exact" if dict(got) == exp else f"got {dict(got)} want {exp}")

    # C
    vr, tpr = verdict_of(out, "r1", "r1")
    if vr != "NOT_SHARDED":
        invalid.append(f"R1 control not red: {vr} tracer={tpr}")
    rows.append(f"{'ok  ' if vr == 'NOT_SHARDED' else 'RED?'} R1 (proot-fast7, must be NOT_SHARDED): {vr} tracer={tpr}")
    r2_mismatch = v1 != "NOT_SHARDED"   # E1's record compared with the wrong expectation must not match
    if not r2_mismatch:
        invalid.append("R2 comparator did not flag E1 against NOT_SHARDED")
    rows.append(f"{'ok  ' if r2_mismatch else 'RED?'} R2 (comparator flags E1 vs NOT_SHARDED): mismatch={r2_mismatch}")

    info = {c: read(f"{out}/main-{c}.ms") for c in ("l1", "x1", "e1", "t1")}
    rows.append(f"info ms {json.dumps(info)} x1_rc={read(f'{out}/main-x1.rc')} (known limitation: sharded exit code is 0)")
    verdict = "AUTOSHARD_INVALID" if invalid else ("AUTOSHARD_RULES_FAIL" if fail else "AUTOSHARD_RULES_PASS")
    return rows, verdict, fail, invalid


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: autoshard_judge.py <out>")
    rows, verdict, fail, invalid = judge(sys.argv[1])
    print("\n".join(rows))
    print(f"fail={fail} invalid={invalid}")
    print(verdict)


if __name__ == "__main__":
    main()
