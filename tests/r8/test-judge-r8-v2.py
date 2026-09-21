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


# D-17 added fields to judge-r8-v2.py's X_CHECKPOINT residue proof that the FROZEN
# builders in test-judge-r8.py do not emit (they predate it). The frozen test and its
# builders are authority for judge-r8.py and MUST NOT be edited, so the amendment's
# own driver supplies the amendment's own preconditions here.
#
# These are exactly the keys judge-r8-v2.py requires non-None, and 0 is what the real
# product emits for a quiesced checkpoint — verified against
# evidence/.../runtime-b984ded/r8-c1/attempt-13/x-observations.jsonl, whose
# X_CHECKPOINT rows carry all of them. total_actual_buffer_pending is deliberately
# NOT supplied: the product hardcodes it null (GAP-8) and the amended judge proves
# residue from the fields the product does populate.
D17_CHECKPOINT_DEFAULTS = {
    "root_pending": 0,
    "readIndex": 0,
    "writeIndex": 0,
    "completedSerial": 0,
    "pair_state": 0,
    "pair_src": 0,
    "pair_dst": 0,
    "generationFatal": 0,
    "firstFailed": 0,
}


def _enrich_checkpoints(d) -> None:
    """Fill only MISSING D-17 keys. Never overwrite a value a builder set, so a
    vector that deliberately mutates one of these still exercises its mutation."""
    from pathlib import Path as _P
    f = _P(d) / "x-observations.jsonl"
    if not f.is_file():
        return
    out = []
    for line in f.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("phase") == "X_CHECKPOINT":
            for k, v in D17_CHECKPOINT_DEFAULTS.items():
                row.setdefault(k, v)
        out.append(json.dumps(row))
    f.write_text("".join(x + "\n" for x in out))


def _wrap_builders() -> dict:
    """Wrap each frozen builder so the D-17 preconditions exist BEFORE mutate()
    runs, leaving every vector's own mutation fully effective."""
    original = dict(mod.BUILDERS)

    def wrap(fn):
        def inner(d, *a, **kw):
            fn(d, *a, **kw)
            _enrich_checkpoints(d)
        return inner

    return {cell: wrap(fn) for cell, fn in original.items()}


# D-17 deliberately removed destroy_before_x_release() from judge_c1: R_DESTROY_STAGE
# and R_ACK_SETTLED only exist once a buffer has been REGISTERED, and C1 registers
# nothing by design (plan §7.1). Requiring it in C1 was one of the defects that made
# 8 of 10 cells structurally unreachable. The helper is still live in judge_c4
# (judge-r8-v2.py:374), which is the cell that does register.
#
# N08 asserts REVERSE_DESTRUCTION_ORDER and is written against C1, so under the
# amendment its C1 form is unreachable BY DESIGN. Re-targeting it to C4 — rather
# than excusing it — keeps REVERSE_DESTRUCTION_ORDER covered for the amended judge;
# no other vector covers it (N15/N16/N17 are the only other C4 vectors and assert
# different reasons). The frozen judge and its 53-vector authority are untouched:
# test-judge-r8.py still runs N08 against C1 and still passes 53/53.
CELL_OVERRIDES = {
    "N08": "R8-C4",
}


def main() -> int:
    orig = mod.JUDGE
    orig_builders = dict(mod.BUILDERS)
    mod.JUDGE = JUDGE
    mod.BUILDERS = _wrap_builders()
    try:
        cases = VECTORS["cases"]
        if len(cases) != 53:
            print(f"FAIL count={len(cases)} want=53")
            return 1
        failures = []
        for c in cases:
            cell = CELL_OVERRIDES.get(c["id"], c["cell"])
            ok, msg = mod.run_one(
                c["id"], cell, c["expected_verdict"], c["expected_reason"])
            print(("PASS " if ok else "FAIL ") + msg)
            if not ok:
                failures.append(msg)
        print(f"judge_v2_vectors=53 device_cells=10 failures={len(failures)}")
        return 0 if not failures else 1
    finally:
        mod.JUDGE = orig
        mod.BUILDERS = orig_builders


if __name__ == "__main__":
    sys.exit(main())
