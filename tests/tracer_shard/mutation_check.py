#!/usr/bin/env python3
"""Mutation check for shard_judge.py (TRACER-SHARD-01): every mutant must make test_shard_judge.py fail,
and the unmutated copy must pass (the control). Prints one line per case and MUTATION_CHECK_PASS|FAIL.
    python3 mutation_check.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MUTANTS = [
    ("S apps on the daily tracer accepted", 'if tp == daily_tp:\n                why.append(f"{app}: S but main', 'if False:\n                why.append(f"{app}: S but main'),
    ("P apps elsewhere accepted", "if tp != daily_tp:\n                why.append(f\"{app}: P but", "if False:\n                why.append(f\"{app}: P but"),
    ("renderer check dropped", 'e["renderers"] < 1', 'e["renderers"] < 0'),
    ("missing app accepted", 'why.append(f"{app}: no main process at the end of the loaded window")\n            continue', 'continue'),
    ("client tracer = v6 accepted", 'J.check_tracer(b.get("client_tracer"), V7_SHA', 'J.check_tracer(b.get("client_tracer"), b.get("client_tracer", {}).get("tracer_sha256") or V7_SHA'),
    ("unfrozen tools accepted", 'if not SHARD_TOOLS:\n        why.append', 'if False:\n        why.append'),
    ("threshold 0.25 -> 0.5", "max(es) <= 0.25 * min(ep)", "max(es) <= 0.5 * min(ep)"),
    ("control not required", "if min(ep) < 3:", "if min(ep) < 0:"),
    ("cherry-picked replacement accepted", 'out["why"].append(f"replacement {c.get', 'pass  # out["why"].append(f"replacement {c.get'),
    ("E counts S3", 'm["E"] = m["S1"] + m["S2"]', 'm["E"] = m["S1"] + m["S2"] + m["S3"]'),
]


def run(tmp: Path) -> int:
    return subprocess.run([sys.executable, str(tmp / "test_shard_judge.py")], cwd=tmp,
                          capture_output=True, text=True).returncode


def main() -> int:
    src = (HERE / "shard_judge.py").read_text()
    ok = True
    with tempfile.TemporaryDirectory(prefix="shard-mut-") as t:
        tmp = Path(t)
        # the copies import xfce_v6 through HERE.parent / "xfce_v6": mirror that layout
        (tmp / "tracer_shard").mkdir()
        (tmp / "xfce_v6").symlink_to(HERE.parent / "xfce_v6")
        d = tmp / "tracer_shard"
        shutil.copy(HERE / "test_shard_judge.py", d)
        (d / "shard_judge.py").write_text(src)
        rc = run(d)
        print(f"control (unmutated) rc={rc} {'OK' if rc == 0 else 'BAD'}")
        ok &= rc == 0
        for name, a, b in MUTANTS:
            if src.count(a) != 1:
                print(f"mutant '{name}': pattern not found exactly once -> BAD")
                ok = False
                continue
            (d / "shard_judge.py").write_text(src.replace(a, b))
            rc = run(d)
            print(f"mutant '{name}': rc={rc} {'caught' if rc != 0 else 'SURVIVED'}")
            ok &= rc != 0
    print("MUTATION_CHECK_PASS" if ok else "MUTATION_CHECK_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
