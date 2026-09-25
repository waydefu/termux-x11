#!/usr/bin/env python3
"""Judge for XFCE-NOTEL-01 (evidence/session/xfce-v6/XFCE-NOTEL-01-FREEZE.md). Implements sections 3-6, nothing more.

    notel_judge.py preflight <dir>
    notel_judge.py capture <dir> <C|GT|CT>
    notel_judge.py series <preflight dir> <series.json>       Q1 (CT vs C) and Q2 (GT vs C)

Every Part B check is the SAME code as tests/xfce_v6/xfce_v6_judge.py (preflight, capture_b, separation, imported):
the only differences are the client tracer (proot-fast7, swapped in for the duration of the call and restored) and
the telemetry switch (freeze section 3): the env must match the group, and the raw logcat must hold 0 gatea-telemetry
lines for C / GT and >= 1000 for CT. CT is checked with capture_b's C rules (the same product settings).
Missing evidence never passes.
"""
from __future__ import annotations

import gzip
import json
import re
import sys
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "xfce_v6"))
import xfce_v6_judge as J  # noqa: E402

V7_SHA = "4f9d10ad2f78e1eef3907eea1ef761b4b8265c9d7cbd5779df48b4f8cb756058"   # out7/bin/proot-fast7
ORDER = ["C", "GT", "CT", "GT", "CT", "C", "CT", "C", "GT"]                    # freeze section 5
MAX_REPLACEMENTS = 2
CT_MIN_TELEMETRY_LINES = 1000
TELEMETRY_LINE = re.compile(rb" [VDIWEF] gatea-telemetry\s*:")   # a logcat record, not the adbd command line


@contextmanager
def client_tracer(sha: str):
    """xfce_v6_judge checks the client tracer against its module constant V6_SHA; this experiment's is proot-fast7."""
    old = J.V6_SHA
    J.V6_SHA = sha
    try:
        yield
    finally:
        J.V6_SHA = old


def telemetry_lines(cap: Path):
    for name in ("raw-logcat.txt", "raw-logcat.txt.gz"):
        p = cap / name
        if p.exists():
            try:
                with (gzip.open(p, "rb") if name.endswith(".gz") else open(p, "rb")) as f:
                    return sum(1 for ln in f if TELEMETRY_LINE.search(ln))
            except OSError:
                return None
    return None


def preflight(d: Path) -> dict:
    with client_tracer(V7_SHA):
        return J.preflight(d)


def capture(cap: Path, group: str) -> dict:
    if group not in ("C", "GT", "CT"):
        return {"valid": False, "why": [f"unknown group {group}"], "metrics": {}}
    with client_tracer(V7_SHA):
        r = J.capture_b(cap, "C" if group == "CT" else group)
    why, m = list(r["why"]), dict(r["metrics"])
    try:
        env = dict(ln.split("=", 1) for ln in (cap / "env-x3.txt").read_text().splitlines() if "=" in ln)
    except OSError:
        env = None          # capture_b already reported the missing file
    if env is not None:
        tel = env.get("TERMUX_X11_GATEA_TELEMETRY")
        if group == "CT" and tel != "1":
            why.append(f"CT but TERMUX_X11_GATEA_TELEMETRY={tel}")
        if group in ("C", "GT") and tel is not None:
            why.append(f"{group} but TERMUX_X11_GATEA_TELEMETRY={tel} (must be unset)")
        for k, v in (("TERMUX_X11_GATEA_PROTO", "1"), ("TERMUX_X11_R8_ARM", "1"), ("TERMUX_X11_R8_CASE", "R8-C1")):
            if env.get(k) != v:
                why.append(f"{k}={env.get(k)} != {v} (freeze section 3, common to all groups)")
    n = telemetry_lines(cap)
    m["telemetry_lines"] = n
    if n is None:
        why.append("raw logcat missing (telemetry lines)")
    elif group == "CT" and n < CT_MIN_TELEMETRY_LINES:
        why.append(f"CT telemetry lines {n} < {CT_MIN_TELEMETRY_LINES} (switch did not take effect)")
    elif group in ("C", "GT") and n != 0:
        why.append(f"{group} telemetry lines {n} != 0 (switch did not take effect)")
    return {"valid": not why, "why": why, "metrics": m}


def series(pre_dir: Path, path: Path) -> dict:
    pf = preflight(pre_dir)
    out: dict = {"preflight": pf}
    if pf["verdict"] != "PROBE_SENSITIVE":
        out["verdict"] = "BLOCKED_" + pf["verdict"]
        return out
    runs = json.loads(path.read_text())["captures"]
    got = [capture(Path(r["dir"]), r["group"]) | {"name": r["name"], "group": r["group"]} for r in runs]
    out["captures"] = got
    why = []
    if [r["group"] for r in runs[:len(ORDER)]] != ORDER:
        why.append(f"first nine groups {[r['group'] for r in runs[:len(ORDER)]]} != {ORDER}")
    extra = runs[len(ORDER):]
    if len(extra) > MAX_REPLACEMENTS:
        why.append(f"{len(extra)} replacements > {MAX_REPLACEMENTS}")
    need = [c["group"] for c in got[:len(ORDER)] if not c["valid"]]
    for r in extra:
        if r["group"] in need:
            need.remove(r["group"])
        else:
            why.append(f"replacement {r['name']} ({r['group']}) without an INVALID capture of that group")
    roots = {c["metrics"].get("x_root") for c in got if c["valid"]}
    if len(roots) > 1:
        why.append(f"X root differs between captures {sorted(map(str, roots))}")
    if why:
        out["verdict"] = "INCONCLUSIVE"
        out["why"] = why
        return out
    valid = {g: [c["metrics"] for c in got if c["group"] == g and c["valid"]][:3] for g in ("C", "GT", "CT")}
    out["valid_counts"] = {g: len([c for c in got if c["group"] == g and c["valid"]]) for g in ("C", "GT", "CT")}
    x3 = {g: [m["x3_cores"] for m in valid[g]] for g in valid}
    # Q1: telemetry cost (CT vs C)
    if len(valid["C"]) == 3 and len(valid["CT"]) == 3:
        out["Q1"] = J.separation(x3["C"], x3["CT"], "TELEMETRY_COSTS_X_CPU", "TELEMETRY_CHEAPER")
        out["Q1_x3"] = {"C": x3["C"], "CT": x3["CT"]}
    else:
        out["Q1"] = "INCONCLUSIVE"
    # Q2: GT vs C, telemetry off (Part B section 5.3 rules)
    if len(valid["C"]) == 3 and len(valid["GT"]) == 3:
        u = lambda cs, k: [c["untraced"][k] for c in cs]  # noqa: E731
        out["N1"] = "GT_NO_TAIL_STALLS" if all(x == 0 for x in u(valid["GT"], "over_100ms")) else "GT_TAIL_STALLS"
        out["N1_over_100ms"] = u(valid["GT"], "over_100ms")
        out["N2_p50"] = J.separation(u(valid["GT"], "p50"), u(valid["C"], "p50"), "GT_FASTER", "CPU_FASTER")
        out["N2_p99"] = J.separation(u(valid["GT"], "p99"), u(valid["C"], "p99"), "GT_FASTER", "CPU_FASTER")
        out["N3"] = J.separation(x3["GT"], x3["C"], "GT_LESS_X_CPU", "GT_MORE_X_CPU")
    else:
        out["Q2"] = "INCONCLUSIVE"
    out["verdict"] = "JUDGED" if out["Q1"] != "INCONCLUSIVE" and "Q2" not in out else "PARTIAL"
    return out


def main(argv) -> int:
    if len(argv) >= 2 and argv[0] == "preflight":
        r = preflight(Path(argv[1]))
    elif len(argv) >= 3 and argv[0] == "capture":
        r = capture(Path(argv[1]), argv[2])
    elif len(argv) >= 3 and argv[0] == "series":
        r = series(Path(argv[1]), Path(argv[2]))
    else:
        print(__doc__)
        return 64
    print(json.dumps(r, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
