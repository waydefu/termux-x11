#!/usr/bin/env python3
"""PROOT-BENCH-01 judge (evidence/session/proot/PROOT-BENCH-01-FREEZE.md).

  proot_bench_judge.py <rep01 cell> <rep02 cell>
  proot_bench_judge.py --selftest

Reuses electron_bench_judge.analyse() for the shared validity checks (touch, thermal, focus, Cursor identity
'Disabled', keys/typing effective, rAF, survivors) and adds: tracer identity (arg0), tracer CPU readable at every
snapshot, tracer gone at the end. S = CPU s of scroll+type (Cursor session + tracer + X3).
CPU_BETTER  S(PF) <= S(P0) * (1 - max(0.10, 2*noise));  IDLE_OK  idle(PF) <= idle(P0) + 0.05 cores;
FRAMES_NOT_WORSE  p95(PF) <= p95(P0)*1.10 and over50(PF) <= over50(P0)+3.
PF runs whose Cursor did not work (driver errors / keys or typing without effect) -> PROOT_FAST_FAIL.
"""
import json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gl"))
import electron_bench_judge as ej  # noqa: E402

CLK = 100
BINS = {"p0": "/data/data/com.termux/files/usr/bin/proot",
        "pf": "/data/data/com.termux/files/home/build/proot-fast/out/bin/proot-fast"}
ORDER = {"01": [("r01-1-p0", "p0"), ("r01-2-pf", "pf"), ("r01-3-p0", "p0")],
         "02": [("r02-1-pf", "pf"), ("r02-2-p0", "p0"), ("r02-3-pf", "pf")]}
FUNCTIONAL = ("driver errors", "editor found", "scroll had no effect", "typed text not visible", "rAF samples 0", "keys_acked", "chars_acked")


def analyse(cell, name, var):
    a = ej.analyse(cell, name, "v0")          # both variants run Cursor with --disable-gpu
    why = list(a.get("why", []))
    r = ej.run_lines(cell).get(name, {})
    if r.get("tracer_arg0") != BINS[var]:
        why.append(f"{name}: tracer arg0 {r.get('tracer_arg0')!r} != {BINS[var]}")
    if r.get("tracer_gone") != "true":
        why.append(f"{name}: tracer_gone={r.get('tracer_gone')}")
    try:
        ph = json.load(open(os.path.join(cell, r.get("json", name + ".json"))))["phases"]
    except Exception as e:
        return {"valid": False, "fail": False, "why": why + [f"{name}: json {e}"]}
    for p in ("idle", "scroll", "type"):
        for s in ("start", "end"):
            if (ph.get(p, {}).get(s) or {}).get("extra_ticks") is None:
                why.append(f"{name}: {p}.{s} tracer ticks null")
    # a PF run whose Cursor did not work is a failure of proot-fast, not missing information
    functional = [w for w in why if any(k in w for k in FUNCTIONAL)]
    if why:
        return {"valid": False, "fail": var == "pf" and bool(functional) and len(functional) == len(why), "why": why}

    def cpu(p):
        s, e = ph[p]["start"], ph[p]["end"]
        return ((e["session_ticks"] - s["session_ticks"]) + (e["x3_ticks"] - s["x3_ticks"]) + (e["extra_ticks"] - s["extra_ticks"])) / CLK

    def tracer(p):
        return (ph[p]["end"]["extra_ticks"] - ph[p]["start"]["extra_ticks"]) / CLK
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
def fake_cells(root, tag, p0, pf, p0b=None, pfb=None, bad=None, on="pf"):
    cells = []
    for rep in ("01", "02"):
        cell = os.path.join(root, f"{tag}-{rep}"); os.makedirs(cell); lines = []
        for i, (n, var) in enumerate(ORDER[rep]):
            base = dict((p0b or p0) if (i == 2 and var == "p0") else (pfb or pf) if (i == 2 and var == "pf") else (p0 if var == "p0" else pf))
            tracer = base.pop("tracer")
            line = ej.fake_run(cell, n, "v0", **base, **({"noeffect": True} if bad == "noeffect" and var == on else {}))
            j = json.load(open(os.path.join(cell, n + ".json")))
            for p, ph in j["phases"].items():
                dur = (ph["end"]["t"] - ph["start"]["t"]) / 1000
                ph["start"]["extra_ticks"] = 0
                ph["end"]["extra_ticks"] = None if (bad == "tracer_null" and var == on) else int(tracer * dur / 60 * CLK)
            json.dump(j, open(os.path.join(cell, n + ".json"), "w"))
            arg0 = "/usr/bin/wrong-proot" if (bad == "wrong_tracer" and var == on) else BINS[var]
            lines.append(line + f" tracer_pid=1 tracer_arg0={arg0} tracer_gone=true")
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
