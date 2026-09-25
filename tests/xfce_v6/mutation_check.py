#!/usr/bin/env python3
"""Verify the verifier: break one rule of xfce_v6_judge.py at a time and require the unit tests
to go red. A mutant that survives means the tests do not guard that rule.
    python3 mutation_check.py      -> MUTATION_CHECK_PASS (every mutant killed) or _FAIL
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MUTANTS = [   # (name, exact source text, replacement) or (name, [(old, new), ...])
    ("untraced TRACER not checked", 'why.append(f"{label}: TRACER {sorted(tracers)} != {want}")', "pass"),
    ("tracer sha not checked", "if d.get(\"tracer_sha256\") != want_sha:", "if False:"),
    ("off switches ignored", "if env.get(k) == bad:", "if False:"),
    ("shared-buffer range ignored", "if shared < lo or (hi is not None and shared > hi):", "if False:"),
    ("null touch counted as 0", [('elif t.get("events") is None:', "elif False:"),
                                 ('elif t["events"] != 0:', 'elif (t["events"] or 0) != 0:')]),
    ("probe hit threshold 0", "GRAB_HIT_MS, GRAB_PRE_S, GRAB_POST_S = 3, 0.95, 500.0, 0.5, 1.0",
     "GRAB_HIT_MS, GRAB_PRE_S, GRAB_POST_S = 3, 0.95, 0.0, 0.5, 1.0"),
    ("grab hold not checked", "if g1 - g0 < GRAB_MIN_HOLD_S:", "if False:"),
    ("separation allows overlap", "if max(a) < min(b):", "if sorted(a)[1] < sorted(b)[1]:"),
    ("B n floor removed", 'if m["untraced"]["n"] < B_MIN_N:', "if False:"),
    ("A control not required", "if e_stock < 3:", "if False:"),
    ("S3 counted in E", 'm["E"] = m["S1"] + m["S2"]', 'm["E"] = m["S1"] + m["S2"] + m["S3"]'),
    ("apps-running precheck ignored",
     'if pc.get("cursor_running") is not False or pc.get("hermes_running") is not False:', "if False:"),
    ("test_mode captures accepted", 'if b.get("test_mode") is not False:', "if False:"),
    ("Claude-open precheck ignored", 'if pc.get("claude_running") is not True:', "if False:"),
    ("replacement accepted for a valid capture",
     'why.append(f"{slot}: replacement given although the original is valid")', "pass"),
    ("invalid replacement accepted", 'if rc["valid"]:\n                used[slot] = rc', 'if True:\n                used[slot] = rc'),
    ("too few valid captures still judged",
     'if why or len(valid["C"]) < 3 or len(valid["GT"]) < 3:', "if why:"),
]


# process matching (v6_probes.py), guarded by test_proc_match.py
PROC_MUTANTS = [
    ("NUL-separated cmdline match (the bug the host self-test found)",
     'if t == prefix or t.startswith(prefix + " "):', 'if raw_nul.startswith(prefix.replace(" ", "\\0").encode()):'),
    ("':3' also matches ':30'", 'if t == prefix or t.startswith(prefix + " "):', "if t.startswith(prefix):"),
]


def run_suite(src: str, target: str = "xfce_v6_judge.py", suite: str = "test_xfce_v6_judge.py") -> int:
    """Run one unit-test suite against a copy of tests/xfce_v6 (+ tests/pga, which the judge imports)
    with <target> replaced by <src>."""
    with tempfile.TemporaryDirectory(prefix="xfce-v6-mut-") as t:
        t = Path(t)
        shutil.copytree(HERE.parent / "pga", t / "pga", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(HERE, t / "xfce_v6", ignore=shutil.ignore_patterns("__pycache__"))
        (t / "xfce_v6" / target).write_text(src)
        return subprocess.run([sys.executable, suite], cwd=t / "xfce_v6",
                              capture_output=True, text=True).returncode


def main() -> int:
    src = (HERE / "xfce_v6_judge.py").read_text()
    # control that must stay green: the unmutated copy passes, so a red mutant means the rule bit
    if run_suite(src) != 0:
        print("MUTATION_CHECK_BROKEN unmutated copy fails - kills below would mean nothing")
        return 2
    print("CONTROL  unmutated copy passes")
    survived = []
    for name, *rest in MUTANTS:
        pairs = rest[0] if len(rest) == 1 else [tuple(rest)]
        mutated = src
        if any(src.count(old) != 1 for old, _ in pairs):
            print(f"MUTANT_SOURCE_NOT_FOUND {name!r}")
            survived.append(name)
            continue
        for old, new in pairs:
            mutated = mutated.replace(old, new)
        killed = run_suite(mutated) != 0
        print(f"{'KILLED  ' if killed else 'SURVIVED'} {name}")
        if not killed:
            survived.append(name)
    psrc = (HERE / "v6_probes.py").read_text()
    if run_suite(psrc, "v6_probes.py", "test_proc_match.py") != 0:
        print("MUTATION_CHECK_BROKEN unmutated test_proc_match fails")
        return 2
    for name, old, new in PROC_MUTANTS:
        if psrc.count(old) != 1:
            print(f"MUTANT_SOURCE_NOT_FOUND {name!r}")
            survived.append(name)
            continue
        mutated = psrc.replace(old, new).replace(
            't = cmdline_text(open(f"{proc}/{p}/cmdline", "rb").read())',
            'raw_nul = open(f"{proc}/{p}/cmdline", "rb").read(); t = cmdline_text(raw_nul)')
        killed = run_suite(mutated, "v6_probes.py", "test_proc_match.py") != 0
        print(f"{'KILLED  ' if killed else 'SURVIVED'} {name}")
        if not killed:
            survived.append(name)
    print("MUTATION_CHECK_PASS" if not survived else f"MUTATION_CHECK_FAIL {survived}")
    return 0 if not survived else 1


if __name__ == "__main__":
    sys.exit(main())
