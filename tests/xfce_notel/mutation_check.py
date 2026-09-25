#!/usr/bin/env python3
"""Mutation check for notel_judge.py (XFCE-NOTEL-01): every mutant must make test_notel_judge.py fail and the
unmutated copy must pass (the control). Prints one line per case and MUTATION_CHECK_PASS|FAIL.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MUTANTS = [
    ("C/GT telemetry lines ignored", 'elif group in ("C", "GT") and n != 0:', 'elif group in ("C", "GT") and n < 0:'),
    ("CT minimum ignored", 'elif group == "CT" and n < CT_MIN_TELEMETRY_LINES:', 'elif group == "CT" and n < 0:'),
    ("adbd command line counted", r'TELEMETRY_LINE = re.compile(rb" [VDIWEF] gatea-telemetry\s*:")', 'TELEMETRY_LINE = re.compile(rb"gatea-telemetry")'),
    ("client tracer stays v6", '    with client_tracer(V7_SHA):\n        r = J.capture_b', '    with client_tracer(J.V6_SHA):\n        r = J.capture_b'),
    ("CT judged with GT rules", 'J.capture_b(cap, "C" if group == "CT" else group)', 'J.capture_b(cap, "GT" if group == "CT" else group)'),
    ("env telemetry for CT unchecked", 'if group == "CT" and tel != "1":', 'if False:'),
    ("R8 / PROTO env unchecked", 'if env.get(k) != v:', 'if False:'),
    ("Q1 labels swapped", '"TELEMETRY_COSTS_X_CPU", "TELEMETRY_CHEAPER")', '"TELEMETRY_CHEAPER", "TELEMETRY_COSTS_X_CPU")'),
    ("order unchecked", "if [r[\"group\"] for r in runs[:len(ORDER)]] != ORDER:", "if False:"),
    ("cherry-picked replacement accepted", 'why.append(f"replacement {r[\'name\']}', 'pass  # why.append(f"replacement {r[\'name\']}'),
    ("partial reported as judged", 'out["verdict"] = "JUDGED" if out["Q1"] != "INCONCLUSIVE" and "Q2" not in out else "PARTIAL"', 'out["verdict"] = "JUDGED"'),
]


def run(d: Path) -> int:
    return subprocess.run([sys.executable, str(d / "test_notel_judge.py")], cwd=d, capture_output=True, text=True).returncode


def main() -> int:
    src = (HERE / "notel_judge.py").read_text()
    ok = True
    with tempfile.TemporaryDirectory(prefix="notel-mut-") as t:
        tmp = Path(t)
        d = tmp / "xfce_notel"
        d.mkdir()
        (tmp / "xfce_v6").symlink_to(HERE.parent / "xfce_v6")
        (tmp / "pga").symlink_to(HERE.parent / "pga")
        shutil.copy(HERE / "test_notel_judge.py", d)
        (d / "notel_judge.py").write_text(src)
        rc = run(d)
        print(f"control (unmutated) rc={rc} {'OK' if rc == 0 else 'BAD'}")
        ok &= rc == 0
        for name, a, b in MUTANTS:
            if src.count(a) != 1:
                print(f"mutant '{name}': pattern not found exactly once -> BAD")
                ok = False
                continue
            (d / "notel_judge.py").write_text(src.replace(a, b))
            rc = run(d)
            print(f"mutant '{name}': rc={rc} {'caught' if rc != 0 else 'SURVIVED'}")
            ok &= rc != 0
    print("MUTATION_CHECK_PASS" if ok else "MUTATION_CHECK_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
