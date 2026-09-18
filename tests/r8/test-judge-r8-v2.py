#!/usr/bin/env python3
"""Host vectors for judge-r8-v2.py (observation-stream amendment).

Frozen tests/r8/judge-r8.py remains the historical 53-vector authority.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUDGE = HERE / "judge-r8-v2.py"
VECTORS = json.loads((HERE / "judge-negative-cases.json").read_text())

spec = importlib.util.spec_from_file_location("test_judge_r8", HERE / "test-judge-r8.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def main() -> int:
    orig = mod.JUDGE
    mod.JUDGE = JUDGE
    try:
        cases = VECTORS["cases"]
        if len(cases) != 53:
            print(f"FAIL count={len(cases)} want=53")
            return 1
        failures = []
        for c in cases:
            ok, msg = mod.run_one(
                c["id"], c["cell"], c["expected_verdict"], c["expected_reason"])
            print(("PASS " if ok else "FAIL ") + msg)
            if not ok:
                failures.append(msg)
        print(f"judge_v2_vectors=53 device_cells=10 failures={len(failures)}")
        return 0 if not failures else 1
    finally:
        mod.JUDGE = orig


if __name__ == "__main__":
    sys.exit(main())
