#!/usr/bin/env python3
"""Judge for TRACER-SHARD-01 (evidence/session/proot/TRACER-SHARD-01-FREEZE.md). Implements sections 4-7, nothing more.

    shard_judge.py capture <dir> <P|S>          one capture: validity + metrics
    shard_judge.py series <series.json>         the whole run: T0 / T1 / T2

The Part A checks (touch, screen, probes, tools, precheck, windows, S1/S2/S3) are the SAME functions as
tests/xfce_v6/xfce_v6_judge.py (imported, not copied); only the client tracer (proot-fast7) and the two
shard-specific checks (freeze section 4 items 2-4) are new. Missing evidence never passes: an absent file
or an unreadable value makes the capture INVALID (null is not 0).

series.json: {"captures": [{"name": ..., "group": "P"|"S", "dir": ...}, ...]} in run order.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "xfce_v6"))
import xfce_v6_judge as J  # noqa: E402

V7_SHA = "4f9d10ad2f78e1eef3907eea1ef761b4b8265c9d7cbd5779df48b4f8cb756058"   # out7/bin/proot-fast7
# freeze section 11 (2026-09-25, before any device data)
SHARD_TOOLS: dict[str, str] = {
    "f8-shard": "b2f11d3dffcb3ac7cfd8c71cfd87d7704b096e7cbf754e9d69fb6ee5c46e5b8e",
    "shard-run.sh": "1b65ac6deab8c5b93a0dbe4b2a6d15b404d6f891ad176e86ce6a7ac270fea912",
}
ORDER = ["P", "S", "S", "P", "P", "S"]
MAX_REPLACEMENTS = 2
APPS = ("cursor", "hermes")


def check_apps(d: Path, group: str, daily_tp, why: list) -> dict | None:
    """freeze section 4 items 3-4, from apps-end.json (taken at the end of the loaded window)."""
    a = J.read_json(d / "apps-end.json")
    if not a:
        why.append("apps-end.json missing")
        return None
    for app in APPS:
        e = a.get(app)
        if not e or not e.get("main_pid"):
            why.append(f"{app}: no main process at the end of the loaded window")
            continue
        tp = e.get("tracer_pid")
        if not tp:
            why.append(f"{app}: main process tracer unknown")
            continue
        if not isinstance(e.get("renderers"), int) or e["renderers"] < 1:
            why.append(f"{app}: no renderer under its tracer ({e.get('renderers')})")
        if group == "P":
            if tp != daily_tp:
                why.append(f"{app}: P but main runs under tracer {tp}, not the daily {daily_tp}")
        else:
            if tp == daily_tp:
                why.append(f"{app}: S but main runs under the daily tracer {daily_tp}")
            if tp != e.get("shard_tracer_pid"):
                why.append(f"{app}: main tracer {tp} != recorded shard tracer {e.get('shard_tracer_pid')}")
            if e.get("tracer_sha256") != V7_SHA:
                why.append(f"{app}: shard tracer sha256 {e.get('tracer_sha256')} != {V7_SHA[:8]}")
    return a


def capture(d: Path, group: str) -> dict:
    why: list = []
    if group not in ("P", "S"):
        return {"valid": False, "why": [f"unknown group {group}"], "metrics": {}}
    if J.exit_rc(d / "sampler-exit.txt") != 0:
        why.append(f"sampler-exit {J.exit_rc(d / 'sampler-exit.txt')}")
    b = J.read_json(d / "binding.json") or {}
    if b.get("kind") != "v7":
        why.append(f"binding kind {b.get('kind')} != v7")
    if b.get("group") != group:
        why.append(f"binding group {b.get('group')} != {group}")
    if b.get("test_mode") is not False:
        why.append(f"test_mode {b.get('test_mode')} (host self-test captures never count)")
    tp = J.check_tracer(b.get("client_tracer"), V7_SHA, why, "client tracer")
    J.check_tools(J.read_json(d / "tools-v6.json"), ("x_rtt2.glibc", "x_rtt2.bionic", "traced_lat.glibc"), why)
    st = J.read_json(d / "tools-shard.json")
    if not SHARD_TOOLS:
        why.append("SHARD_TOOLS not frozen (freeze section 11)")
    elif not st:
        why.append("tools-shard.json missing")
    else:
        for k, v in SHARD_TOOLS.items():
            if st.get(k) != v:
                why.append(f"shard tool {k} sha256 {st.get(k)} != frozen")
    pc = J.read_json(d / "precheck.json")
    if not pc:
        why.append("precheck.json missing")
    else:
        if pc.get("cursor_running") is not False or pc.get("hermes_running") is not False:
            why.append(f"Cursor/Hermes already running before launch ({pc})")
        if pc.get("claude_running") is not True:
            why.append("Claude Desktop not running")
        if pc.get("electron_others") != []:
            why.append(f"other Electron apps {pc.get('electron_others')}")
        if not isinstance(pc.get("mem_available_mb"), (int, float)) or pc["mem_available_mb"] < 4500:
            why.append(f"MemAvailable {pc.get('mem_available_mb')} < 4500")
    J.check_screen_touch(d, why)
    check_apps(d, group, tp, why)
    ph = J.read_json(d / "phases.json") or {}
    lt = ph.get("launch_time")
    if not isinstance(lt, (int, float)):
        why.append("phases.json launch_time missing")
    ut, ur = J.parse_rtt(d / "untraced-rtt.txt")
    tt, tr = J.parse_rtt(d / "traced-rtt.txt")
    J.check_probe(ut, ur, 0, why, "untraced probe")
    J.check_probe(tt, tr, tp, why, "traced probe")
    tl = J.parse_tl(d / "traced-lat.txt")
    if tl is None:
        why.append("traced-lat.txt missing")
    m: dict = {}
    if isinstance(lt, (int, float)):
        win = (lt, lt + J.A_LOADED_S)
        m["untraced"] = J.window_stats(ur or [], win)
        m["traced"] = J.window_stats(tr or [], win)
        fs = [(f, g) for ep, f, g in (tl or []) if win[0] <= ep < win[1]]
        m["traced_lat"] = {"n": len(fs), "over_100ms": sum(1 for f, g in fs if f > J.STALL_MS * 1000),
                           "fstat_p99_us": J.pct([f for f, g in fs], 99),
                           "getppid_p99_us": J.pct([g for f, g in fs], 99)}
        for lab in ("untraced", "traced"):
            if m[lab]["n"] < J.A_MIN_N_RTT:
                why.append(f"{lab} n {m[lab]['n']} < {J.A_MIN_N_RTT}")
        if m["traced_lat"]["n"] < J.A_MIN_N_TL:
            why.append(f"traced_lat n {m['traced_lat']['n']} < {J.A_MIN_N_TL}")
        m["S1"] = m["traced_lat"]["over_100ms"]
        m["S2"] = m["traced"]["over_100ms"]
        m["S3"] = m["untraced"]["over_100ms"]
        m["E"] = m["S1"] + m["S2"]
    return {"valid": not why, "why": why, "metrics": m}


def series(path: Path) -> dict:
    s = J.read_json(path) or {}
    caps = s.get("captures") or []
    out: dict = {"captures": [], "why": []}
    groups = [c.get("group") for c in caps]
    if groups[:len(ORDER)] != ORDER:
        out["why"].append(f"order {groups[:len(ORDER)]} != {ORDER}")
    results = []
    for c in caps:
        r = capture(Path(c["dir"]), c.get("group"))
        results.append((c, r))
        out["captures"].append({"name": c.get("name"), "group": c.get("group"), **r})
    first, extra = results[:len(ORDER)], results[len(ORDER):]
    invalid = [c.get("group") for c, r in first if not r["valid"]]
    if len(extra) > MAX_REPLACEMENTS:
        out["why"].append(f"{len(extra)} replacements > {MAX_REPLACEMENTS}")
    need = list(invalid)
    for c, r in extra:           # a replacement must stand in for an INVALID capture of the same group
        if c.get("group") in need:
            need.remove(c.get("group"))
        else:
            out["why"].append(f"replacement {c.get('name')} ({c.get('group')}) without an INVALID capture of that group")
    valid = {g: [r["metrics"] for c, r in results if c.get("group") == g and r["valid"]] for g in ("P", "S")}
    if out["why"]:
        out["verdict"] = "INCONCLUSIVE"
        return out
    if len(valid["P"]) < 3 or len(valid["S"]) < 3:
        out["why"].append(f"valid P {len(valid['P'])} / S {len(valid['S'])} < 3 / 3")
        out["verdict"] = "INCONCLUSIVE"
        return out
    ep = [m["E"] for m in valid["P"][:3]]
    es = [m["E"] for m in valid["S"][:3]]
    out["E"] = {"P": ep, "S": es}
    out["S3"] = {"P": [m["S3"] for m in valid["P"][:3]], "S": [m["S3"] for m in valid["S"][:3]]}
    if min(ep) < 3:
        out["T0"] = "WORKLOAD_DOES_NOT_REPRODUCE"
        out["verdict"] = "INCONCLUSIVE"
        return out
    out["T0"] = "CONTROL_REPRODUCES"
    if all(e == 0 for e in es):
        out["T1"] = "SHARD_REMOVES_STUTTER"
    elif max(es) <= 0.25 * min(ep):
        out["T1"] = "SHARD_REDUCES_STUTTER"
    else:
        out["T1"] = "SHARD_NO_CLEAR_EFFECT"
    out["T2"] = "X_SERVER_STALLS_PRESENT" if any(x > 0 for x in out["S3"]["S"]) else "X_SERVER_NO_STALLS"
    out["verdict"] = "JUDGED"
    return out


def main(argv) -> int:
    if len(argv) >= 3 and argv[0] == "capture":
        r = capture(Path(argv[1]), argv[2])
    elif len(argv) >= 2 and argv[0] == "series":
        r = series(Path(argv[1]))
    else:
        print(__doc__)
        return 64
    print(json.dumps(r, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
