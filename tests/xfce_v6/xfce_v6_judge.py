#!/usr/bin/env python3
"""Judge for XFCE-V6-BASELINE-01 (tool T6). Implements the frozen rules of
evidence/session/xfce-v6/XFCE-V6-BASELINE-01-FREEZE.md sections 4, 5, 6 and 9, nothing more.

    xfce_v6_judge.py preflight <dir>                     section 5.2 (+ deviation 1)
    xfce_v6_judge.py capture-b <dir> <C|GT|G0>           one Part B capture: validity + metrics
    xfce_v6_judge.py part-b <preflight dir> <series.json>
    xfce_v6_judge.py part-a <v6-1 dir> <stock-1 dir> <v6-2 dir>

Missing evidence never passes: an absent file, an unreadable value or a null count makes the
capture INVALID (null is not 0).

File contract (written by run-xfce-v4-v6.sh / v6_probes.py / daily_sampler_v2.py):
  Part B capture <cap>/ (plus the existing runner files: screen-*.json, stable-*.json,
  env-x3.txt, x-root.txt, steps.jsonl, raw-logcat.txt[.gz], MEM-GUARD-TRIPPED.txt if tripped)
    runner-exit.txt            "rc=<int>"   (0 = XFCE_CAPTURED)
    client-tracer.json         tracer_ident.py of the runner shell
    xfce-session-tracer.json   tracer_ident.py of the :3 xfce4-session
    touch.json                 {"selftest": bool, "events": int|null}
  <cap>.rca/                   rca_sampler.py, run with x_rtt2.glibc (the traced probe)
  <cap>.v6/                    untraced-rtt.txt, traced-lat.txt, tools-v6.json
  Preflight <dir>/: runner-exit.txt, client-tracer.json, grabs.txt, untraced-rtt.txt,
    traced-rtt.txt, tools-v6.json
  Part A <dir>/: sampler-exit.txt, binding.json {"kind", "client_tracer"}, precheck.json,
    phases.json {"launch_time"}, untraced-rtt.txt, traced-rtt.txt, traced-lat.txt,
    touch.json, screen-pre.json, screen-post.json, tools-v6.json
"""
from __future__ import annotations

import gzip
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "pga"))
from rca_report import pct  # noqa: E402  the same nearest-rank percentile as every earlier XFCE round

# ---------------------------------------------------------------- frozen bindings --
V6_SHA = "2d5596dce6ae3a978658a66902cf121a34b8062480958b5750caba61feda2eab"      # proot-fast6
STOCK_SHA = "ea47e17da8e6ff4882c169c6508861e5b4be9227e477c6020f4f14facc85c10d"   # $PREFIX/bin/proot
OFF_SWITCHES = {"PROOT_STAT_AT_ENTER": "0", "PROOT_KOMPAT_FULL": "1", "PROOT_BWRAP_COMPAT": "1"}
TOOLS = {  # section 9, supplement 6 (built 2026-09-25 11:04)
    "x_rtt2.glibc": "17700d91b426c973bb2582fca88cf7131d9a1ca7ded0971ee7834ad9017948a3",
    "x_grab_stall.glibc": "1004aa87eade6bcffabc48d1dbc641d1597d11a4f4c93e0ce9274e99986fe6f5",
    "traced_lat.glibc": "44ea9efbd1d45cc6a06ad6c71850bef262775080e9c3aeebc23bbe6a94822268",
    "x_rtt2.bionic": "61f51ff371795e112f3b501d76281a416765710523e0bb3e691b2f80251277a0",
}
B_WINDOW_S = 150.0
B_MIN_N = 500                 # untraced probe, 150 s window
A_LOADED_S = 240.0
A_MIN_N_RTT = 768             # 80 % of 960
A_MIN_N_TL = 1920             # 80 % of 2400
STALL_MS = 100.0
GRAB_COUNT, GRAB_MIN_HOLD_S, GRAB_HIT_MS, GRAB_PRE_S, GRAB_POST_S = 3, 0.95, 500.0, 0.5, 1.0
SHARED = {"C": (0, 5), "GT": (20, 200), "G0": (1000, None)}
B_ORDER = ["C", "GT", "GT", "C", "C", "GT"]


# ---------------------------------------------------------------- parsing --
def read_json(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def exit_rc(p: Path):
    try:
        t = p.read_text().strip()
    except OSError:
        return None
    return int(t[3:]) if t.startswith("rc=") and t[3:].lstrip("-").isdigit() else None


def parse_rtt(p: Path):
    """-> (set of TRACER values, [(epoch, ms)]) or (None, None) when the file is missing."""
    try:
        lines = p.read_text().splitlines()
    except OSError:
        return None, None
    tracers, rows = set(), []
    for ln in lines:
        f = ln.split()
        if len(f) == 2 and f[0] == "TRACER":
            tracers.add(int(f[1]))
        elif len(f) == 3 and f[0] == "RTT":
            rows.append((float(f[1]), float(f[2])))
    return tracers, rows


def parse_tl(p: Path):
    try:
        lines = p.read_text().splitlines()
    except OSError:
        return None
    return [(float(f[1]), float(f[2]), float(f[3])) for f in (ln.split() for ln in lines)
            if len(f) == 4 and f[0] == "TL"]


def parse_grabs(p: Path):
    try:
        lines = p.read_text().splitlines()
    except OSError:
        return None
    return [(float(f[1]), float(f[2])) for f in (ln.split() for ln in lines) if len(f) == 3 and f[0] == "GRAB"]


def window_stats(rows, win):
    v = [ms for ep, ms in rows if win[0] <= ep < win[1]]
    return {"n": len(v), "p50": pct(v, 50), "p99": pct(v, 99), "max": max(v) if v else None,
            "over_100ms": sum(1 for x in v if x > STALL_MS)}


def logcat_text(cap: Path):
    for name in ("raw-logcat.txt", "raw-logcat.txt.gz"):
        p = cap / name
        if p.exists():
            try:
                return gzip.open(p, "rt", errors="replace").read() if name.endswith(".gz") \
                    else p.read_text(errors="replace")
            except OSError:
                return None
    return None


# ---------------------------------------------------------------- shared checks --
def check_tracer(d, want_sha, why, label):
    if not d or not d.get("tracer_pid"):
        why.append(f"{label}: no tracer identity")
        return None
    if d.get("tracer_sha256") != want_sha:
        why.append(f"{label}: tracer sha256 {d.get('tracer_sha256')} != {want_sha[:8]}")
    env = d.get("proot_env")
    if env is None:
        why.append(f"{label}: tracer environment unreadable")
    else:
        for k, bad in OFF_SWITCHES.items():
            if env.get(k) == bad:
                why.append(f"{label}: tracer has {k}={bad}")
    return d["tracer_pid"]


def check_tools(tools, needed, why):
    if not tools:
        why.append("tools-v6.json missing")
        return
    for name in needed:
        if tools.get(name) != TOOLS[name]:
            why.append(f"tool {name} sha256 {tools.get(name)} != frozen")


def check_probe(tracers, rows, want, why, label):
    if tracers is None:
        why.append(f"{label}: file missing")
        return
    if not tracers:
        why.append(f"{label}: no TRACER line")
    elif tracers != {want}:
        why.append(f"{label}: TRACER {sorted(tracers)} != {want}")


def check_screen_touch(d: Path, why):
    for n in ("screen-pre.json", "screen-post.json"):
        s = read_json(d / n)
        if not s or s.get("awake") is not True or s.get("keyguard") is not False:
            why.append(f"{n}: not awake/unlocked ({s})")
    t = read_json(d / "touch.json")
    if not t or t.get("selftest") is not True:
        why.append(f"touch recorder self-test failed ({t})")
    elif t.get("events") is None:
        why.append("touch events null (recorder died)")
    elif t["events"] != 0:
        why.append(f"touch events {t['events']}")


def separation(a, b, a_wins: str, b_wins: str):
    """a, b: three values each, lower is better. Complete separation or nothing (freeze 5.3)."""
    if len(a) != 3 or len(b) != 3 or any(x is None for x in a + b):
        return None
    if max(a) < min(b):
        return a_wins
    if min(a) > max(b):
        return b_wins
    return "NO_SEPARATION"


# ---------------------------------------------------------------- preflight (5.2 + deviation 1) --
def preflight(d: Path) -> dict:
    why = []
    if exit_rc(d / "runner-exit.txt") != 0:
        why.append(f"runner-exit {exit_rc(d / 'runner-exit.txt')}")
    tp = check_tracer(read_json(d / "client-tracer.json"), V6_SHA, why, "client tracer")
    check_tools(read_json(d / "tools-v6.json"), ("x_rtt2.glibc", "x_rtt2.bionic", "x_grab_stall.glibc"), why)
    ut, ur = parse_rtt(d / "untraced-rtt.txt")
    tt, tr = parse_rtt(d / "traced-rtt.txt")
    check_probe(ut, ur, 0, why, "untraced probe")
    check_probe(tt, tr, tp, why, "traced probe")
    grabs = parse_grabs(d / "grabs.txt")
    per = []
    if grabs is None:
        why.append("grabs.txt missing")
    elif len(grabs) != GRAB_COUNT:
        why.append(f"{len(grabs)} grabs, want {GRAB_COUNT}")
    else:
        for g0, g1 in grabs:
            row = {"hold_s": round(g1 - g0, 3)}
            if g1 - g0 < GRAB_MIN_HOLD_S:
                why.append(f"grab held {g1 - g0:.3f} s < {GRAB_MIN_HOLD_S}")
            for label, rows in (("untraced", ur), ("traced", tr)):
                hit = max([ms for ep, ms in (rows or []) if g0 - GRAB_PRE_S <= ep <= g1 + GRAB_POST_S] or [None],
                          key=lambda x: -1 if x is None else x)
                row[label + "_max_ms"] = hit
            per.append(row)
    invalid = bool(why)
    sensitive = (not invalid) and all(r["untraced_max_ms"] is not None and r["untraced_max_ms"] >= GRAB_HIT_MS
                                      and r["traced_max_ms"] is not None and r["traced_max_ms"] >= GRAB_HIT_MS
                                      for r in per)
    verdict = "PREFLIGHT_INVALID" if invalid else ("PROBE_SENSITIVE" if sensitive else "PROBE_INSENSITIVE")
    return {"verdict": verdict, "why": why, "grabs": per}


# ---------------------------------------------------------------- Part B --
def rca_summary(cap: Path):
    try:
        r = subprocess.run([sys.executable, str(HERE.parent / "pga" / "rca_report.py"), str(cap)],
                           capture_output=True, text=True, timeout=120)
        return json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def capture_b(cap: Path, group: str) -> dict:
    why = []
    if group not in SHARED:
        return {"valid": False, "why": [f"unknown group {group}"], "metrics": {}}
    if exit_rc(cap / "runner-exit.txt") != 0:
        why.append(f"runner-exit {exit_rc(cap / 'runner-exit.txt')}")
    tp = check_tracer(read_json(cap / "client-tracer.json"), V6_SHA, why, "client tracer")
    xs = read_json(cap / "xfce-session-tracer.json")
    if not xs or xs.get("tracer_pid") != tp:
        why.append(f"xfce4-session tracer {None if not xs else xs.get('tracer_pid')} != runner tracer {tp}")
    v6 = Path(str(cap) + ".v6")
    rca = Path(str(cap) + ".rca")
    check_tools(read_json(v6 / "tools-v6.json"), ("x_rtt2.glibc", "x_rtt2.bionic", "traced_lat.glibc"), why)
    check_screen_touch(cap, why)
    b, a = read_json(cap / "stable-before.json"), read_json(cap / "stable-after.json")
    if not b or not a or b.get("pid") is None or b.get("pid") != a.get("pid"):
        why.append("Stable pid changed or unrecorded")
    if (cap / "MEM-GUARD-TRIPPED.txt").exists():
        why.append("mem-guard tripped")
    # the group switch: setup (env) and path evidence (shared buffers / marker), section 5.1
    try:
        env = dict(ln.split("=", 1) for ln in (cap / "env-x3.txt").read_text().splitlines() if "=" in ln)
    except OSError:
        env = None
        why.append("env-x3.txt missing")
    if env is not None:
        dis, mp = env.get("TERMUX_X11_DISABLE_EXA_GPU"), env.get("TERMUX_X11_GPU_MIN_PIXELS")
        ok = {"C": dis == "1", "GT": dis is None and mp in (None, "4097"), "G0": dis is None and mp == "0"}[group]
        if not ok:
            why.append(f"env does not set group {group}: DISABLE_EXA_GPU={dis} GPU_MIN_PIXELS={mp}")
    lc = logcat_text(cap)
    shared = None
    if lc is None:
        why.append("raw logcat missing")
    else:
        shared = lc.count("Sent shared buffer")
        lo, hi = SHARED[group]
        if shared < lo or (hi is not None and shared > hi):
            why.append(f"shared buffers {shared} outside {lo}..{hi} for {group}")
        if group == "GT" and "GPU min pixels 4097" not in lc:
            why.append("GT marker 'GPU min pixels 4097' absent")
    try:
        root = (cap / "x-root.txt").read_text().split()[0].split("=", 1)[1]
    except (OSError, IndexError):
        root = None
        why.append("x-root.txt missing")
    try:
        t0 = json.loads((cap / "steps.jsonl").read_text().splitlines()[0])["t0_epoch_s"]
    except (OSError, ValueError, IndexError, KeyError):
        t0 = None
        why.append("no choreography t0 (steps.jsonl)")
    ut, ur = parse_rtt(v6 / "untraced-rtt.txt")
    tt, tr = parse_rtt(rca / "rtt.txt")
    check_probe(ut, ur, 0, why, "untraced probe")
    check_probe(tt, tr, tp, why, "traced probe")
    m = {"shared_buffers": shared, "x_root": root}
    if t0 is not None and ur is not None:
        win = (t0, t0 + B_WINDOW_S)
        m["untraced"] = window_stats(ur, win)
        if m["untraced"]["n"] < B_MIN_N:
            why.append(f"untraced n {m['untraced']['n']} < {B_MIN_N}")
        m["traced"] = window_stats(tr or [], win)
        tl = parse_tl(v6 / "traced-lat.txt") or []
        fs = [f for ep, f, g in tl if win[0] <= ep < win[1]]
        m["traced_lat_us"] = {"n": len(fs), "p50": pct(fs, 50), "p99": pct(fs, 99), "max": max(fs) if fs else None}
    summ = rca_summary(cap)
    cores = (summ or {}).get("cpu_cores_window", {})
    m["x3_cores"] = cores.get("X3")
    m["tracer_cores"] = cores.get("proot-tracer")
    m["search"] = (summ or {}).get("xdotool_search_window")
    if m["x3_cores"] is None:
        why.append("X3 cores unavailable (rca cpu window)")
    return {"valid": not why, "why": why, "metrics": m}


def part_b(pre_dir: Path, series: Path) -> dict:
    pf = preflight(pre_dir)
    out = {"preflight": pf}
    if pf["verdict"] != "PROBE_SENSITIVE":
        out["verdict"] = "BLOCKED_" + pf["verdict"]
        return out
    runs = json.loads(series.read_text())["captures"]      # [{"name", "group", "dir"}], in run order
    got = [capture_b(Path(r["dir"]), r["group"]) | {"name": r["name"], "group": r["group"]} for r in runs]
    out["captures"] = got
    why = []
    if [r["group"] for r in runs[:6]] != B_ORDER:
        why.append(f"first six groups {[r['group'] for r in runs[:6]]} != {B_ORDER}")
    tail = runs[6:]
    if not tail or tail[-1]["group"] != "G0":
        why.append("series must end with G0")
    extra = tail[:-1]
    if len(extra) > 2:
        why.append(f"{len(extra)} replacements > 2")
    n_invalid = {g: sum(1 for c in got[:6] if c["group"] == g and not c["valid"]) for g in ("C", "GT")}
    for g in ("C", "GT"):
        if sum(1 for r in extra if r["group"] == g) > n_invalid[g]:
            why.append(f"replacement for {g} without an INVALID {g} capture")
    roots = {c["metrics"].get("x_root") for c in got if c["valid"]}
    if len(roots) > 1:
        why.append(f"X root differs between captures {sorted(map(str, roots))}")
    valid = {g: [c for c in got if c["group"] == g and c["valid"]] for g in ("C", "GT", "G0")}
    if why or len(valid["C"]) < 3 or len(valid["GT"]) < 3:
        out["verdict"] = "INCONCLUSIVE"
        out["why"] = why + [f"valid C {len(valid['C'])}, GT {len(valid['GT'])} (need 3 each)"]
        return out
    C, GT = valid["C"][:3], valid["GT"][:3]
    u = lambda cs, k: [c["metrics"]["untraced"][k] for c in cs]  # noqa: E731
    out["B1"] = "GT_NO_TAIL_STALLS" if all(x == 0 for x in u(GT, "over_100ms")) else "GT_TAIL_STALLS"
    out["B1_over_100ms"] = u(GT, "over_100ms")
    out["B2_p50"] = separation(u(GT, "p50"), u(C, "p50"), "GT_FASTER", "CPU_FASTER")
    out["B2_p99"] = separation(u(GT, "p99"), u(C, "p99"), "GT_FASTER", "CPU_FASTER")
    out["B3"] = separation([c["metrics"]["x3_cores"] for c in GT], [c["metrics"]["x3_cores"] for c in C],
                           "GT_LESS_X_CPU", "GT_MORE_X_CPU")
    out["verdict"] = "JUDGED"
    return out


# ---------------------------------------------------------------- Part A --
def capture_a(d: Path, kind: str) -> dict:
    why = []
    if exit_rc(d / "sampler-exit.txt") != 0:
        why.append(f"sampler-exit {exit_rc(d / 'sampler-exit.txt')}")
    b = read_json(d / "binding.json") or {}
    if b.get("kind") != kind:
        why.append(f"binding kind {b.get('kind')} != {kind}")
    if b.get("test_mode") is not False:
        why.append(f"test_mode {b.get('test_mode')} (host self-test captures never count)")
    tp = check_tracer(b.get("client_tracer"), V6_SHA if kind == "v6" else STOCK_SHA, why, "client tracer")
    if kind == "stock":   # the off switches only matter for v6; stock has none of the v5/v6 code
        why[:] = [w for w in why if " has PROOT_" not in w]
    check_tools(read_json(d / "tools-v6.json"), ("x_rtt2.glibc", "x_rtt2.bionic", "traced_lat.glibc"), why)
    pc = read_json(d / "precheck.json")
    if not pc:
        why.append("precheck.json missing")
    else:
        if pc.get("cursor_running") is not False or pc.get("hermes_running") is not False:
            why.append(f"Cursor/Hermes already running before launch ({pc})")
        if pc.get("claude_running") is not True:
            why.append("Claude Desktop not running (freeze 6.1: open and idle)")
        if pc.get("electron_others") != []:
            why.append(f"other Electron apps {pc.get('electron_others')}")
        if not isinstance(pc.get("mem_available_mb"), (int, float)) or pc["mem_available_mb"] < 4500:
            why.append(f"MemAvailable {pc.get('mem_available_mb')} < 4500")
    check_screen_touch(d, why)
    ph = read_json(d / "phases.json") or {}
    lt = ph.get("launch_time")
    if not isinstance(lt, (int, float)):
        why.append("phases.json launch_time missing")
    ut, ur = parse_rtt(d / "untraced-rtt.txt")
    tt, tr = parse_rtt(d / "traced-rtt.txt")
    check_probe(ut, ur, 0, why, "untraced probe")
    check_probe(tt, tr, tp, why, "traced probe")
    tl = parse_tl(d / "traced-lat.txt")
    if tl is None:
        why.append("traced-lat.txt missing")
    m = {}
    if isinstance(lt, (int, float)):
        win = (lt, lt + A_LOADED_S)
        m["untraced"] = window_stats(ur or [], win)
        m["traced"] = window_stats(tr or [], win)
        fs = [(f, g) for ep, f, g in (tl or []) if win[0] <= ep < win[1]]
        m["traced_lat"] = {"n": len(fs), "over_100ms": sum(1 for f, g in fs if f > STALL_MS * 1000),
                           "fstat_p99_us": pct([f for f, g in fs], 99),
                           "getppid_p99_us": pct([g for f, g in fs], 99)}
        for lab in ("untraced", "traced"):
            if m[lab]["n"] < A_MIN_N_RTT:
                why.append(f"{lab} n {m[lab]['n']} < {A_MIN_N_RTT}")
        if m["traced_lat"]["n"] < A_MIN_N_TL:
            why.append(f"traced_lat n {m['traced_lat']['n']} < {A_MIN_N_TL}")
        m["S1"] = m["traced_lat"]["over_100ms"]
        m["S2"] = m["traced"]["over_100ms"]
        m["S3"] = m["untraced"]["over_100ms"]
        m["E"] = m["S1"] + m["S2"]
    return {"valid": not why, "why": why, "metrics": m}


def part_a(v6_1: Path, stock_1: Path, v6_2: Path) -> dict:
    caps = {"v6-1": capture_a(v6_1, "v6"), "stock-1": capture_a(stock_1, "stock"), "v6-2": capture_a(v6_2, "v6")}
    out = {"captures": caps}
    bad = [k for k, c in caps.items() if not c["valid"]]
    if bad:   # the freeze defines no replacement for Part A
        out["verdict"] = "INCONCLUSIVE"
        out["why"] = [f"{k} INVALID" for k in bad]
        return out
    e_stock = caps["stock-1"]["metrics"]["E"]
    e_v6 = [caps["v6-1"]["metrics"]["E"], caps["v6-2"]["metrics"]["E"]]
    if e_stock < 3:
        out["A0"] = "WORKLOAD_DOES_NOT_REPRODUCE"
        out["verdict"] = "INCONCLUSIVE"
        return out
    out["A0"] = "CONTROL_REPRODUCES"
    if all(e == 0 for e in e_v6):
        out["A1"] = "DAILY_STUTTER_GONE"
    elif max(e_v6) <= 0.25 * e_stock:
        out["A1"] = "DAILY_STUTTER_REDUCED"
    else:
        out["A1"] = "DAILY_STUTTER_REMAINS"
    s3 = [caps["v6-1"]["metrics"]["S3"], caps["v6-2"]["metrics"]["S3"]]
    out["A2"] = "X_SERVER_STALLS_PRESENT" if any(x > 0 for x in s3) else "X_SERVER_NO_STALLS"
    out["E"] = {"stock-1": e_stock, "v6-1": e_v6[0], "v6-2": e_v6[1]}
    out["verdict"] = "JUDGED"
    return out


def main(argv) -> int:
    if len(argv) >= 2 and argv[0] == "preflight":
        r = preflight(Path(argv[1]))
    elif len(argv) >= 3 and argv[0] == "capture-b":
        r = capture_b(Path(argv[1]), argv[2])
    elif len(argv) >= 3 and argv[0] == "part-b":
        r = part_b(Path(argv[1]), Path(argv[2]))
    elif len(argv) >= 4 and argv[0] == "part-a":
        r = part_a(Path(argv[1]), Path(argv[2]), Path(argv[3]))
    else:
        print(__doc__)
        return 64
    print(json.dumps(r, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
