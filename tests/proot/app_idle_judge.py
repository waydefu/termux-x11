#!/usr/bin/env python3
"""APP-IDLE-01 judge (evidence/session/proot/APP-IDLE-01-FREEZE.md).

  app_idle_judge.py <cell>
  app_idle_judge.py --selftest

Per app, runs ABBA: <app>-1-p0, <app>-2-pf, <app>-3-pf, <app>-4-p0. Idle cores of a run = (session + tracer + X3 ticks
over [t0, t1]) / (t1 - t0), each boundary interpolated on the run-as meter CSV (proot_bench_judge.at).
noise = max(|P0a - P0b| / mean(P0), |PFa - PFb| / mean(PF)).
APP_BENEFIT     mean(PF) <= mean(P0) * (1 - max(0.10, 2*noise))
APP_NO_BENEFIT  valid but not the above
APP_FAIL        every invalid reason of a PF run is functional (no window / app died) while its P0 runs are valid
APP_INVALID     any other invalid run
DAILY_ENABLE    every app APP_BENEFIT and the median reduction across apps >= 30% (the user's "若數據極好直接開啟")
"""
import os, statistics, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proot_bench_judge as pbj  # noqa: E402

CLK = 100
APPS = ("chatgpt", "chatgptweb", "hermes", "cursor")
BINS = {"p0": "/data/data/com.termux/files/usr/bin/proot",
        "pf": os.environ.get("PF_BIN", "/data/data/com.termux/files/home/build/proot-fast/out2/bin/proot-fast2")}
FUNCTIONAL = ("windows=0", "procs_t0", "procs_t1")


def runs(cell):
    out = {}
    for line in open(os.path.join(cell, "cmd.out"), encoding="utf-8", errors="replace"):
        if line.startswith("RUN "):
            p = line.split()
            out[p[1]] = dict(x.split("=", 1) for x in p[2:] if "=" in x)
    return out


def analyse(cell, name, var, r):
    why = []
    if r is None:
        return {"valid": False, "fail": False, "why": [f"{name}: no RUN line"]}
    for k, want in (("touch_selftest", "true"), ("touch_events", "0"), ("thermal_start", "0"), ("survivors_after_kill", "0"),
                    ("tracer_gone", "true"), ("start_awake", "true"), ("end_awake", "true"), ("start_keyguard", "false"),
                    ("end_keyguard", "false"), ("start_focus", "true"), ("end_focus", "true")):
        if r.get(k) != want:
            why.append(f"{name}: {k}={r.get(k)}")
    if r.get("tracer_arg0") != BINS[var]:
        why.append(f"{name}: tracer arg0 {r.get('tracer_arg0')!r} != {BINS[var]}")
    fun = []
    if r.get("windows", "0") in ("0", "", "null"):
        fun.append(f"{name}: windows=0 (app shows no window)")
    for k in ("procs_t0", "procs_t1"):
        if r.get(k) in (None, "", "null", "0"):
            fun.append(f"{name}: {k}={r.get(k)} (app not running)")
    val = None
    try:
        head, rows = pbj.load_meter(os.path.join(cell, r.get("meter", name + ".meter.csv")))
        if head.get("tracer_arg0") != BINS[var]:
            why.append(f"{name}: meter tracer_arg0 {head.get('tracer_arg0')!r}")
        t0, t1 = int(r["t0"]), int(r["t1"])
        a, b = pbj.at(rows, t0), pbj.at(rows, t1)
        for e, v in (("t0", a), ("t1", b)):
            if isinstance(v, str):
                (fun if "session_procs 0" in v else why).append(f"{name}: {e} {v}")
        if not isinstance(a, str) and not isinstance(b, str):
            val = (sum(b) - sum(a)) / CLK / ((t1 - t0) / 1000)
    except Exception as e:
        why.append(f"{name}: meter {type(e).__name__} {e}")
    allw = why + fun
    return {"valid": not allw, "fail": var == "pf" and bool(fun) and not why, "why": allw, "cores": val}


def judge(cell, quiet=False, apps=APPS):
    say = (lambda *x: None) if quiet else print
    rr = runs(cell); verdicts = {}; red = {}
    for app in apps:
        names = [(f"{app}-1-p0", "p0"), (f"{app}-2-pf", "pf"), (f"{app}-3-pf", "pf"), (f"{app}-4-p0", "p0")]
        res = [(n, v, analyse(cell, n, v, rr.get(n))) for n, v in names]
        for n, v, a in res:
            say(f"  {n}: " + (f"idle {a['cores']:.3f} cores" if a["valid"] else ("FAIL " if a["fail"] else "INVALID ") + "; ".join(a["why"])))
        p0 = [a for _, v, a in res if v == "p0"]; pf = [a for _, v, a in res if v == "pf"]
        if any(a["fail"] for a in pf) and all(a["valid"] for a in p0):
            verdicts[app] = "APP_FAIL"
        elif not all(a["valid"] for _, _, a in res):
            verdicts[app] = "APP_INVALID"
        else:
            m0 = statistics.mean(a["cores"] for a in p0); mf = statistics.mean(a["cores"] for a in pf)
            noise = max(abs(p0[0]["cores"] - p0[1]["cores"]) / m0, abs(pf[0]["cores"] - pf[1]["cores"]) / mf)
            need = max(0.10, 2 * noise); red[app] = 1 - mf / m0
            verdicts[app] = "APP_BENEFIT" if mf <= m0 * (1 - need) else "APP_NO_BENEFIT"
            say(f"  {app}: P0 {m0:.3f} PF {mf:.3f} cores ({mf / m0:.3f}x, -{100 * red[app]:.0f}%) noise {noise:.3f} need -{100 * need:.0f}%")
        say(f"VERDICT {app} {verdicts[app]}")
    daily = all(v == "APP_BENEFIT" for v in verdicts.values()) and statistics.median(red.values()) >= 0.30 if len(red) == len(apps) else False
    say(f"DAILY_ENABLE {str(daily).lower()}" + (f" (median reduction {100 * statistics.median(red.values()):.0f}%)" if red else ""))
    return verdicts, daily


# ------------------------------------------------------------------ synthetic self-test
def fake_cell(root, tag, p0, pf, p0b=None, pfb=None, bad=None, on="pf", apps=("chatgpt",)):
    cell = os.path.join(root, tag); os.makedirs(cell); lines = []
    for app in apps:
        for n, var, cores in ((f"{app}-1-p0", "p0", p0), (f"{app}-2-pf", "pf", pf), (f"{app}-3-pf", "pf", pfb or pf),
                              (f"{app}-4-p0", "p0", p0b or p0)):
            t0, t1 = 2_000_000, 2_060_000; hit = bad if var == on else None
            with open(os.path.join(cell, n + ".meter.csv"), "w") as f:
                f.write(f"# sid=1 tracer_pid=1 tracer_arg0={BINS[var]} x3=2 interval=0.2 rescan=5\n")
                f.write("epoch_ms,session_ticks,session_procs,tracer_ticks,x3_ticks\n")
                for t in range(t0 - 5000, t1 + 5001, 200):
                    if hit == "gap" and abs(t - t1) <= 600:
                        continue
                    ticks = int(cores * CLK * max(0, t - t0 + 5000) / 1000)
                    f.write(f"{t},{int(ticks * 0.7)},{0 if hit == 'died' else 9},{int(ticks * 0.3)},0\n")
            line = (f"RUN {n} app={app} variant={var} sid=1 tracer_pid=1 tracer_arg0={BINS[var]} meter={n}.meter.csv t0={t0} t1={t1}"
                    f" windows={0 if hit == 'nowin' else 1} procs_t0={0 if hit == 'died' else 9} procs_t1={0 if hit == 'died' else 9}"
                    f" start_awake=true start_keyguard=false start_focus=true end_awake=true end_keyguard=false end_focus=true"
                    f" touch_selftest=true touch_events={57 if hit == 'touch' else 0} thermal_start=0 thermal_waited_s=0 thermal_end=0"
                    f" mem_avail_mb=5000 mem_waited_s=20 survivors_after_kill={1 if hit == 'survivor' else 0} tracer_gone=true")
            lines.append(line)
    open(os.path.join(cell, "cmd.out"), "w").write("\n".join(lines) + "\n")
    return cell


def selftest():
    cases = [
        ("a_equal_must_be_red", dict(p0=0.50, pf=0.50), {"chatgpt": "APP_NO_BENEFIT"}, False),
        ("b_clear_benefit", dict(p0=0.50, pf=0.20), {"chatgpt": "APP_BENEFIT"}, True),
        ("c_small_gain_must_be_red", dict(p0=0.50, pf=0.47), {"chatgpt": "APP_NO_BENEFIT"}, False),
        ("d_noise_raises_bar", dict(p0=0.50, pf=0.40, p0b=0.35), {"chatgpt": "APP_NO_BENEFIT"}, False),
        ("e_pf_no_window_is_fail", dict(p0=0.50, pf=0.20, bad="nowin"), {"chatgpt": "APP_FAIL"}, False),
        ("f_pf_died_is_fail", dict(p0=0.50, pf=0.20, bad="died"), {"chatgpt": "APP_FAIL"}, False),
        ("g_p0_no_window_is_invalid", dict(p0=0.50, pf=0.20, bad="nowin", on="p0"), {"chatgpt": "APP_INVALID"}, False),
        ("h_touch_is_invalid", dict(p0=0.50, pf=0.20, bad="touch"), {"chatgpt": "APP_INVALID"}, False),
        ("i_meter_gap_is_invalid", dict(p0=0.50, pf=0.20, bad="gap"), {"chatgpt": "APP_INVALID"}, False),
        ("j_survivor_is_invalid", dict(p0=0.50, pf=0.20, bad="survivor", on="p0"), {"chatgpt": "APP_INVALID"}, False),
        ("k_benefit_but_under_30_no_daily", dict(p0=0.50, pf=0.40), {"chatgpt": "APP_BENEFIT"}, False),
    ]
    ok = True
    with tempfile.TemporaryDirectory() as root:
        for name, kw, want, want_daily in cases:
            got, daily = judge(fake_cell(root, name, **kw), quiet=True, apps=("chatgpt",))
            good = got == want and daily == want_daily; ok &= good
            print(f"CASE {name} want {want} daily={want_daily} got {got} daily={daily} {'OK' if good else 'WRONG'}")
    print("SELFTEST_PASS" if ok else "SELFTEST_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["--selftest"]:
        sys.exit(selftest())
    if len(a) == 1:
        judge(a[0]); sys.exit(0)
    print(__doc__); sys.exit(2)
