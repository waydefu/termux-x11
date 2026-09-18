#!/usr/bin/env python3
"""Host stream tests for R8 producer-terminal completeness (no truncation)."""
from __future__ import annotations

import json
import sys
from typing import Any


def classify(recs: list[dict[str, Any]]) -> str:
    if not recs:
        return "EMPTY"
    phases = [r.get("phase") for r in recs]
    if phases.count("BEGIN") != 1:
        return "BEGIN_COUNT"
    if phases.count("END") != 1:
        return "END_COUNT"
    if phases[0] != "BEGIN":
        return "BEGIN_NOT_FIRST"
    if phases[-1] != "END":
        return "POST_END"
    mids = [r for r in recs if r.get("phase") not in ("BEGIN", "END")]
    end = recs[-1]
    if end.get("actual_count") != len(mids):
        return "END_COUNT_MISMATCH"
    for rec in mids:
        if rec.get("phase") == "END":
            return "END_NOT_TERMINAL"
    return "OK"


def rec(phase: str, seq: int, **extra: Any) -> dict[str, Any]:
    row = {"v": 1, "role": extra.pop("role", "x"), "producer_seq": seq, "phase": phase}
    row.update(extra)
    return row


def end_row(seq: int, actual: int, role: str = "x") -> dict[str, Any]:
    return rec("END", seq, role=role, actual_count=actual)


def need(cond: bool, label: str, bad: list[str]) -> None:
    if not cond:
        bad.append(label)


def main() -> int:
    bad: list[str] = []

    legal_x = [
        rec("BEGIN", 0),
        rec("X_CLOSE_ENTER", 1),
        rec("X_CLOSE_RESULT", 2),
        rec("X_DESTRUCTOR_ENTER", 3),
        rec("X_DESTRUCTOR_EXIT", 4),
        end_row(4, 4),
    ]
    need(classify(legal_x) == "OK", "legal_x_close_dtor_end", bad)

    # CloseScreen is not X producer quiescence: reset re-enters ScreenInit
    # (CreateRootCursor / GPU_COPY_DONE) before ddxGiveUp.
    legal_x_giveup = [
        rec("BEGIN", 0),
        rec("X_CLOSE_ENTER", 1),
        rec("X_CLOSE_RESULT", 2),
        rec("X_DESTRUCTOR_ENTER", 3),
        rec("X_DESTRUCTOR_EXIT", 4),
        rec("X_DESTRUCTOR_ENTER", 5),
        rec("X_DESTRUCTOR_EXIT", 6),
        rec("DEFER_ENQUEUE", 7),
        rec("X_WAKE_RECEIVED", 8),
        rec("DEFER_DISPATCH", 9),
        rec("RECHECK", 10),
        end_row(10, 10),
    ]
    need(classify(legal_x_giveup) == "OK", "legal_x_reset_then_giveup", bad)

    reset_no_end = [
        rec("BEGIN", 0),
        rec("X_CLOSE_ENTER", 1),
        rec("X_CLOSE_RESULT", 2),
        rec("RECHECK", 3),
    ]
    need(classify(reset_no_end) == "END_COUNT", "reset_path_no_end", bad)

    legal_x_terminate = [
        rec("BEGIN", 0),
        rec("TEST_CONTROL", 1, op="TERMINATE"),
        rec("X_CLOSE_ENTER", 2),
        rec("X_CLOSE_RESULT", 3),
        end_row(3, 3),
    ]
    need(classify(legal_x_terminate) == "OK", "legal_x_terminate_then_giveup", bad)

    end_then_dtor = [
        rec("BEGIN", 0),
        rec("X_CLOSE_ENTER", 1),
        rec("X_CLOSE_RESULT", 2),
        end_row(2, 2),
        rec("X_DESTRUCTOR_ENTER", 3),
    ]
    need(classify(end_then_dtor) == "POST_END", "x_end_then_dtor", bad)

    end_then_any = [
        rec("BEGIN", 0),
        rec("X_CLOSE_RESULT", 1),
        end_row(1, 1),
        rec("DEFER_ENQUEUE", 2),
    ]
    need(classify(end_then_any) == "POST_END", "x_end_then_any", bad)

    unbound_end_surface = [
        rec("BEGIN", 0, role="r"),
        rec("R_UNBOUND_FINAL", 1, role="r"),
        end_row(1, 1, role="r"),
        rec("R_WAKE_SENT", 2, role="r", cause="surface_loss"),
    ]
    need(classify(unbound_end_surface) == "POST_END", "r_unbound_end_then_surface", bad)

    unbound_then_surface_then_end = [
        rec("BEGIN", 0, role="r"),
        rec("R_UNBOUND_FINAL", 1, role="r"),
        rec("R_WAKE_SENT", 2, role="r", cause="surface_loss"),
        rec("R_SURFACE_QUIESCED", 3, role="r"),
        end_row(3, 3, role="r"),
    ]
    need(classify(unbound_then_surface_then_end) == "OK", "r_unbound_surface_end", bad)

    surface_then_unbound_then_end = [
        rec("BEGIN", 0, role="r"),
        rec("R_WAKE_SENT", 1, role="r", cause="surface_loss"),
        rec("R_SURFACE_QUIESCED", 2, role="r"),
        rec("R_UNBOUND_FINAL", 3, role="r"),
        end_row(3, 3, role="r"),
    ]
    need(classify(surface_then_unbound_then_end) == "OK", "r_surface_then_unbound_end", bad)

    # Completeness must not green by dropping post-END records.
    truncated = legal_x[:-1] + [end_row(4, 4)]
    # legal_x already ends with END; this copy with extra dtor must stay red.
    with_extra = list(legal_x) + [rec("X_DESTRUCTOR_ENTER", 5)]
    need(classify(with_extra) != "OK", "no_truncate_to_green", bad)
    need(classify(truncated) == "OK", "legal_still_ok", bad)

    if bad:
        print("FAIL", bad)
        return 1
    print("PASS test_r8_obs_terminal.py")
    print(json.dumps({"vectors": 10, "failures": 0}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
