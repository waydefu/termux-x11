#!/usr/bin/env python3
"""PROOT-BENCH-01 judge (evidence/session/proot/PROOT-BENCH-01-FREEZE.md).

  proot_bench_judge.py <rep01 cell> <rep02 cell>
  proot_bench_judge.py --selftest

Reuses electron_bench_judge.analyse() for the shared validity checks (touch, thermal, focus, Cursor identity
'Disabled', keys/typing effective, rAF, survivors) and adds: tracer identity (arg0 in the meter CSV header and the RUN
line), tracer gone at the end. CPU comes from the run-as meter CSV (<run>.meter.csv, proot_meter.py), not from the
driver JSON: the daily PRoot cannot read the processes launched through `adb run-as` (dry-run 01). Each phase boundary
(driver `t`) is linearly interpolated between the two CSV samples around it; both must be within 0.5 s of it, have
tracer and X3 ticks, and see >= 1 session process. S = CPU s of scroll+type (Cursor session + tracer + X3).
CPU_BETTER  S(PF) <= S(P0) * (1 - max(0.10, 2*noise));  IDLE_OK  idle(PF) <= idle(P0) + 0.05 cores;
FRAMES_NOT_WORSE  p95(PF) <= p95(P0)*1.10 and over50(PF) <= over50(P0)+3.
PF runs whose Cursor did not work (driver errors / keys or typing without effect) -> PROOT_FAST_FAIL.
"""
import json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gl"))
import electron_bench_judge as ej  # noqa: E402

CLK = 100
BINS = {"p0": "/data/data/com.termux/files/usr/bin/proot",
        "pf": os.environ.get("PF_BIN", "/data/data/com.termux/files/home/build/proot-fast/out/bin/proot-fast")}
# PROOT-BENCH-02 judges proot-fast v2 with PF_BIN=.../out2/bin/proot-fast2 (same thresholds as 01)
ORDER = {"01": [("r01-1-p0", "p0"), ("r01-2-pf", "pf"), ("r01-3-p0", "p0")],
         "02": [("r02-1-pf", "pf"), ("r02-2-p0", "p0"), ("r02-3-pf", "pf")]}
FUNCTIONAL = ("driver errors", "editor found", "scroll had no effect", "typed text not visible", "rAF samples 0", "keys_acked", "chars_acked")


GAP_MS = 500


def load_meter(path):
    """-> (header dict, rows [(t, session, procs, tracer|None, x3|None)]) or raises"""
    with open(path) as f:
        head = dict(kv.split("=", 1) for kv in f.readline().lstrip("# ").split() if "=" in kv)
        f.readline()
        rows = []
        for line in f:
            t, se, n, tr, x = line.strip().split(",")
            rows.append((int(t), int(se), int(n), int(tr) if tr else None, int(x) if x else None))
    return head, rows


def at(rows, t):
    """interpolated (session, tracer, x3) at t, or a reason string"""
    for (t0, s0, n0, r0, x0), (t1, s1, n1, r1, x1) in zip(rows, rows[1:]):
        if t0 <= t <= t1:
            if t - t0 > GAP_MS or t1 - t > GAP_MS:
                return f"meter gap {t0}..{t1} around {t}"
            if None in (r0, r1, x0, x1):
                return "tracer/x3 ticks null"
            if n0 == 0 or n1 == 0:
                return "session_procs 0"
            w = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            return tuple(u + (v - u) * w for u, v in ((s0, s1), (r0, r1), (x0, x1)))
    return f"meter does not cover {t}"


def analyse(cell, name, var):
    a = ej.analyse(cell, name, "v0")          # both variants run Cursor with --disable-gpu
    why = list(a.get("why", []))
    r = ej.run_lines(cell).get(name, {})
    if r.get("tracer_arg0") != BINS[var]:
        why.append(f"{name}: tracer arg0 {r.get('tracer_arg0')!r} != {BINS[var]}")
    if r.get("tracer_gone") != "true":
        why.append(f"{name}: tracer_gone={r.get('tracer_gone')}")
    pts = {}
    try:
        ph = json.load(open(os.path.join(cell, r.get("json", name + ".json"))))["phases"]
        head, rows = load_meter(os.path.join(cell, r.get("meter", name + ".meter.csv")))
        if head.get("tracer_arg0") != BINS[var]:
            why.append(f"{name}: meter tracer_arg0 {head.get('tracer_arg0')!r} != {BINS[var]}")
        for p in ("idle", "scroll", "type"):
            for e in ("start", "end"):
                v = at(rows, ph[p][e]["t"])
                if isinstance(v, str):
                    why.append(f"{name}: {p}.{e} {v}")
                else:
                    pts[(p, e)] = v
    except Exception as e:
        why.append(f"{name}: json/meter {type(e).__name__} {e}")
    # a PF run whose Cursor did not work is a failure of proot-fast, not missing information
    functional = [w for w in why if any(k in w for k in FUNCTIONAL)]
    if why:
        return {"valid": False, "fail": var == "pf" and bool(functional) and len(functional) == len(why), "why": why}

    def cpu(p):
        return sum(pts[(p, "end")]) / CLK - sum(pts[(p, "start")]) / CLK

    def tracer(p):
        return (pts[(p, "end")][1] - pts[(p, "start")][1]) / CLK
    idle_s = (ph["idle"]["end"]["t"] - ph["idle"]["start"]["t"]) / 1000
    return {"valid": True, "fail": False, "why": [], "S": cpu("scroll") + cpu("type"), "idle": cpu("idle") / idle_s,
            "tracer_S": tracer("scroll") + tracer("type"), "p95": a["p95"], "over50": a["over50"]}


def judge(c1, c2, quiet=False):
    say = (lambda *x: None) if quiet else print
    res = {rep: [(n, v, analyse(cell, n, v)) for n, v in ORDER[rep]] for rep, cell in (("01", c1), ("02", c2))}
    for runs in res.values():
        for n, v, a in runs:
            say(f"  {n} {v}: " + (f"S={a['S']:.2f}s (tracer {a['tracer_S']:.2f}s) idle={a['idle']:.3f} cores p95={a['p95']:.1f} over50={a['over50']}"
                                 if a["valid"] else ("FAIL " if a["fail"] else "INVALID ") + "; ".join(a["why"])))
    if any(a["fail"] for runs in res.values() for _, _, a in runs):
        say("VERDICT PROOT_FAST_FAIL"); return "PROOT_FAST_FAIL"
    if not all(a["valid"] for runs in res.values() for _, _, a in runs):
        say("VERDICT PROOT_INVALID"); return "PROOT_INVALID"
    r1, r2 = [a for _, _, a in res["01"]], [a for _, _, a in res["02"]]
    noise = max(abs(r1[0]["S"] - r1[2]["S"]) / r1[0]["S"], abs(r2[0]["S"] - r2[2]["S"]) / r2[0]["S"])
    need = max(0.10, 2 * noise)
    say(f"  A/A noise={noise:.3f} -> required reduction {need:.3f}")
    fails = []
    m = lambda xs, k: sum(x[k] for x in xs) / len(xs)
    for rep, runs in res.items():
        p0 = [a for _, v, a in runs if v == "p0"]; pf = [a for _, v, a in runs if v == "pf"]
        S0, SF = m(p0, "S"), m(pf, "S")
        ok_cpu = SF <= S0 * (1 - need)
        ok_idle = m(pf, "idle") <= m(p0, "idle") + 0.05
        ok_fr = m(pf, "p95") <= m(p0, "p95") * 1.10 and m(pf, "over50") <= m(p0, "over50") + 3
        say(f"  rep{rep}: S p0={S0:.2f} pf={SF:.2f} ({SF / S0:.3f}x) CPU_BETTER={ok_cpu} | tracer p0={m(p0, 'tracer_S'):.2f} "
            f"pf={m(pf, 'tracer_S'):.2f} | idle p0={m(p0, 'idle'):.3f} pf={m(pf, 'idle'):.3f} IDLE_OK={ok_idle} | FRAMES_NOT_WORSE={ok_fr}")
        fails += [f"rep{rep}:{k}" for k, ok in (("CPU_BETTER", ok_cpu), ("IDLE_OK", ok_idle), ("FRAMES_NOT_WORSE", ok_fr)) if not ok]
    v = "PROOT_FAST_BENEFIT" if not fails else "PROOT_FAST_NO_BENEFIT"
    say(f"VERDICT {v}" + (f" ({', '.join(fails)})" if fails else "")); return v


# ------------------------------------------------------------------ synthetic self-test
def fake_meter(path, j, tracer, arg0, bad):
    """CSV sampled every 200 ms whose curves pass through the JSON boundary values (session) and a tracer that
    runs at `tracer` CPU s per 60 s inside the phases"""
    ph = j["phases"]
    b = [(ph[p][e]["t"], ph[p][e]["session_ticks"]) for p in ("idle", "scroll", "type") for e in ("start", "end")]
    rate = tracer / 60 * CLK / 1000                       # ticks per ms inside a phase
    tb, acc = [], 0.0
    for p in ("idle", "scroll", "type"):
        tb.append((ph[p]["start"]["t"], acc)); acc += rate * (ph[p]["end"]["t"] - ph[p]["start"]["t"]); tb.append((ph[p]["end"]["t"], acc))

    def lin(pts, t):
        if t <= pts[0][0]:
            return pts[0][1]
        for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
            if t0 <= t <= t1:
                return v0 if t1 == t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)
        return pts[-1][1]
    gap = ph["scroll"]["start"]["t"]
    with open(path, "w") as f:
        f.write(f"# sid=1 tracer_pid=1 tracer_arg0={arg0} x3=2 interval=0.2 rescan=5\n")
        f.write("epoch_ms,session_ticks,session_procs,tracer_ticks,x3_ticks\n")
        for t in range(b[0][0] - 1000, b[-1][0] + 1001, 200):
            if bad == "meter_gap" and abs(t - gap) <= 600:
                continue
            tr = "" if bad == "tracer_null" else int(lin(tb, t))
            f.write(f"{t},{int(lin(b, t))},{0 if bad == 'procs_zero' else 12},{tr},0\n")


def fake_cells(root, tag, p0, pf, p0b=None, pfb=None, bad=None, on="pf"):
    cells = []
    for rep in ("01", "02"):
        cell = os.path.join(root, f"{tag}-{rep}"); os.makedirs(cell); lines = []
        for i, (n, var) in enumerate(ORDER[rep]):
            base = dict((p0b or p0) if (i == 2 and var == "p0") else (pfb or pf) if (i == 2 and var == "pf") else (p0 if var == "p0" else pf))
            tracer = base.pop("tracer")
            hit = bad if var == on else None
            line = ej.fake_run(cell, n, "v0", **base, **({"noeffect": True} if hit == "noeffect" else {}))
            j = json.load(open(os.path.join(cell, n + ".json")))
            arg0 = "/usr/bin/wrong-proot" if hit == "wrong_tracer" else BINS[var]
            if hit != "meter_missing":
                fake_meter(os.path.join(cell, n + ".meter.csv"), j, tracer, arg0, hit)
            if hit == "survivor":
                line = line.replace("survivors_after_kill=0", "survivors_after_kill=1")
            lines.append(line + f" tracer_pid=1 tracer_arg0={arg0} meter={n}.meter.csv tracer_gone=true")
        open(os.path.join(cell, "cmd.out"), "w").write("\n".join(lines) + "\n")
        cells.append(cell)
    return cells


def selftest():
    P0 = dict(S=60.0, idle=0.30, p95=25.0, over50=3, tracer=10.0)
    GOOD = dict(S=45.0, idle=0.25, p95=25.0, over50=3, tracer=3.0)
    cases = [
        ("a_equal_must_be_red", dict(p0=P0, pf=P0), "PROOT_FAST_NO_BENEFIT"),
        ("b_clear_benefit", dict(p0=P0, pf=GOOD), "PROOT_FAST_BENEFIT"),
        ("c_wrong_tracer", dict(p0=P0, pf=GOOD, bad="wrong_tracer"), "PROOT_INVALID"),
        ("d_tracer_null", dict(p0=P0, pf=GOOD, bad="tracer_null"), "PROOT_INVALID"),
        ("e_pf_not_working", dict(p0=P0, pf=GOOD, bad="noeffect"), "PROOT_FAST_FAIL"),
        ("f_p0_not_working_is_invalid", dict(p0=P0, pf=GOOD, bad="noeffect", on="p0"), "PROOT_INVALID"),
        ("g_noise_raises_bar", dict(p0=P0, pf=dict(GOOD, S=51.0), p0b=dict(P0, S=48.0)), "PROOT_FAST_NO_BENEFIT"),
        # idle counts the tracer too: keep PF's tracer load equal to P0's so only the Cursor idle is worse
        # (first version lowered PF's tracer as well, which made this must-be-red case pass - fixture error)
        ("h_idle_worse", dict(p0=P0, pf=dict(GOOD, idle=0.40, tracer=10.0)), "PROOT_FAST_NO_BENEFIT"),
        # run-as meter (dry-run 01: the daily PRoot saw session_procs=0 and null tracer ticks, and ended nothing)
        ("i_meter_gap_at_boundary", dict(p0=P0, pf=GOOD, bad="meter_gap"), "PROOT_INVALID"),
        ("j_session_procs_zero", dict(p0=P0, pf=GOOD, bad="procs_zero"), "PROOT_INVALID"),
        ("k_meter_missing", dict(p0=P0, pf=GOOD, bad="meter_missing"), "PROOT_INVALID"),
        ("l_survivor_after_end", dict(p0=P0, pf=GOOD, bad="survivor", on="p0"), "PROOT_INVALID"),
    ]
    ok = True
    with tempfile.TemporaryDirectory() as root:
        for name, kw, want in cases:
            got = judge(*fake_cells(root, name, **kw), quiet=True)
            good = got == want; ok &= good
            print(f"CASE {name} want {want} got {got} {'OK' if good else 'WRONG'}")
    print("SELFTEST_PASS" if ok else "SELFTEST_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["--selftest"]:
        sys.exit(selftest())
    if len(a) == 2:
        judge(a[0], a[1]); sys.exit(0)
    print(__doc__); sys.exit(2)
