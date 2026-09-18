#!/usr/bin/env python3
"""Host tests for R8 orchestration v2. Zero device mutation."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from r8_orchestration_v2 import (
    STATES,
    append_state,
    cell_class,
    ends_are_producer_only,
    hold_ready,
    parse_obs_text,
    permit_judge,
    producer_finalized,
)


def obs(role, phase, seq, **kw):
    row = {
        "v": 1, "role": role, "pid": 1, "tid": 1, "producer_seq": seq,
        "case": "R8-C1", "phase": phase,
        "original_nonce": 1, "original_generation": 1,
    }
    row.update(kw)
    return row


def raw_line(obj: dict) -> str:
    return "I R8_OBS : R8_OBS " + json.dumps(obj, separators=(",", ":"))


def closed_x(n=2):
    recs = [
        obs("x", "BEGIN", 0, expected_count=None, expected_digest=None),
        obs("x", "X_CLOSE_ENTER", 1),
        obs("x", "X_CLOSE_RESULT", 2),
        obs("x", "END", n, actual_count=n, actual_digest="00"),
    ]
    return recs


def closed_r(n=1):
    return [
        obs("r", "BEGIN", 0, expected_count=None, expected_digest=None, case=None),
        obs("r", "R_UNBOUND_FINAL", 1),
        obs("r", "END", n, actual_count=n, actual_digest="00"),
    ]


class OrchestrationV2(unittest.TestCase):
    def test_states_complete(self):
        self.assertEqual(STATES[0], "PRESTART")
        self.assertEqual(STATES[-1], "CLEANUP")
        self.assertIn("CLEAN_SHUTDOWN_REQUESTED", STATES)
        self.assertIn("PRODUCERS_FINALIZED", STATES)
        self.assertIn("JUDGE", STATES)

    def test_classes(self):
        self.assertEqual(cell_class("R8-C1"), "A")
        self.assertEqual(cell_class("R8-C2"), "B")
        self.assertEqual(cell_class("R8-C5-full"), "B")
        self.assertEqual(cell_class("R8-P1"), "P")

    def test_c1_judge_before_end_refused(self):
        x = [obs("x", "BEGIN", 0), obs("x", "X_DESTRUCTOR_ENTER", 1)]
        r = [obs("r", "BEGIN", 0)]
        ok, reason = permit_judge(
            "R8-C1", shutdown_requested=False, xrows=x, rrows=r,
            fixture_text="RESULT p_r8_lifecycle C1 CLIENT_OK",
            fixture_alive=False, fixture_killed_by_runner=False,
            x_alive_after_construction=True,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "JUDGE_BEFORE_CLEAN_SHUTDOWN")

    def test_c1_x_dead_without_terminate_invalid(self):
        x, r = closed_x(), closed_r()
        ok, reason = permit_judge(
            "R8-C1", shutdown_requested=True, xrows=x, rrows=r,
            fixture_text="RESULT p_r8_lifecycle C1 CLIENT_OK",
            fixture_alive=False, fixture_killed_by_runner=False,
            x_alive_after_construction=False,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "X_DIED_BEFORE_SHUTDOWN")

    def test_c1_terminate_allows_x_already_dead(self):
        x, r = closed_x(), closed_r()
        text = ("RESULT p_r8_lifecycle C1 CLIENT_OK\n"
                "TERMINATE_SENT\nTERMINATE_ACK\nX_HANGUP_AFTER_TERMINATE\n")
        ok, reason = permit_judge(
            "R8-C1", shutdown_requested=True, xrows=x, rrows=r,
            fixture_text=text, fixture_alive=False,
            fixture_killed_by_runner=False, x_alive_after_construction=False,
        )
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "ok")

    def test_c1_clean_shutdown_with_ends_permits(self):
        x, r = closed_x(), closed_r()
        raw = "\n".join(raw_line(o) for o in x + r)
        ok, reason = permit_judge(
            "R8-C1", shutdown_requested=True, xrows=x, rrows=r,
            fixture_text="RESULT p_r8_lifecycle C1 CLIENT_OK",
            fixture_alive=False, fixture_killed_by_runner=False,
            x_alive_after_construction=True, raw_text=raw, jsonl_rows=x + r,
        )
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "ok")

    def test_c2_hold_then_hangup_permits(self):
        x, r = closed_x(), closed_r()
        text = "RESULT p_r8_lifecycle C2 CLIENT_HOLD\nX_HANGUP_AFTER_HOLD\n"
        ok, reason = permit_judge(
            "R8-C2", shutdown_requested=True, xrows=x, rrows=r,
            fixture_text=text, fixture_alive=False,
            fixture_killed_by_runner=False, x_alive_after_construction=True,
        )
        self.assertTrue(ok, reason)

    def test_c2_fixture_killed_invalid(self):
        ok, reason = permit_judge(
            "R8-C2", shutdown_requested=True, xrows=closed_x(), rrows=closed_r(),
            fixture_text="RESULT p_r8_lifecycle C2 CLIENT_HOLD",
            fixture_alive=False, fixture_killed_by_runner=True,
            x_alive_after_construction=True,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "HOLD_CLIENT_KILLED_BY_RUNNER")

    def test_c2_died_early(self):
        ok, reason = permit_judge(
            "R8-C2", shutdown_requested=False, xrows=[], rrows=[],
            fixture_text="RESULT p_r8_lifecycle C2 CLIENT_HOLD",
            fixture_alive=False, fixture_killed_by_runner=False,
            x_alive_after_construction=True,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "HOLD_CLIENT_DIED_EARLY")

    def test_c5_full_requires_pre_term_and_hold(self):
        ok, reason = hold_ready("R8-C5-full", "RESULT p_r8_lifecycle C5-full CLIENT_HOLD")
        self.assertFalse(ok)
        text = "C5_FULL registered=16\nCHECKPOINT phase=5\nRESULT p_r8_lifecycle C5-full CLIENT_HOLD\n"
        ok, reason = hold_ready("R8-C5-full", text)
        self.assertTrue(ok, reason)

    def test_missing_x_end(self):
        x = [obs("x", "BEGIN", 0), obs("x", "X_CLOSE_ENTER", 1), obs("x", "X_CLOSE_RESULT", 2)]
        ok, reason = producer_finalized(x, closed_r(), "R8-C1")
        self.assertFalse(ok)
        self.assertEqual(reason, "MISSING_END_x")

    def test_missing_renderer_end(self):
        r = [obs("r", "BEGIN", 0), obs("r", "R_UNBOUND_FINAL", 1)]
        ok, reason = producer_finalized(closed_x(), r, "R8-C1")
        self.assertFalse(ok)
        self.assertEqual(reason, "MISSING_END_r")

    def test_collector_fake_end_rejected(self):
        real = [obs("x", "BEGIN", 0), obs("x", "X_CLOSE_ENTER", 1)]
        fake_end = obs("x", "END", 99, actual_count=1, actual_digest="ff")
        raw = "\n".join(raw_line(o) for o in real)
        ok, reason = ends_are_producer_only(raw, real + [fake_end])
        self.assertFalse(ok)
        self.assertEqual(reason, "COLLECTOR_SYNTHETIC_END")
        ok, reason = permit_judge(
            "R8-C1", shutdown_requested=True,
            xrows=closed_x(), rrows=closed_r(),
            fixture_text="CLIENT_OK", fixture_alive=False,
            fixture_killed_by_runner=False, x_alive_after_construction=True,
            raw_text=raw, jsonl_rows=real + [fake_end] + closed_r(),
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "COLLECTOR_SYNTHETIC_END")

    def test_p1_p2_no_clean_end_not_refused(self):
        x = [obs("x", "BEGIN", 0), obs("x", "P1_DESTRUCTOR_CALL", 1)]
        r = [obs("r", "BEGIN", 0)]
        ok, reason = permit_judge(
            "R8-P1", shutdown_requested=False, xrows=x, rrows=r,
            fixture_text="", fixture_alive=False,
            fixture_killed_by_runner=False, x_alive_after_construction=False,
        )
        self.assertTrue(ok, reason)
        ok, reason = permit_judge(
            "R8-P2", shutdown_requested=False, xrows=x, rrows=r,
            fixture_text="", fixture_alive=False,
            fixture_killed_by_runner=False, x_alive_after_construction=False,
        )
        self.assertTrue(ok, reason)

    def test_parse_obs_no_fabricate(self):
        text = "noise\n" + raw_line(obs("x", "BEGIN", 0)) + "\n"
        xs, rs = parse_obs_text(text)
        self.assertEqual(len(xs), 1)
        self.assertEqual(len(rs), 0)
        self.assertEqual(xs[0]["phase"], "BEGIN")

    def test_append_state_rejects_skip(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "orchestration-state.jsonl"
            append_state(p, "PRESTART")
            append_state(p, "X_STARTED")
            with self.assertRaises(ValueError):
                append_state(p, "SKIPPED")
            lines = p.read_text().splitlines()
            self.assertEqual(len(lines), 2)


if __name__ == "__main__":
    unittest.main()
