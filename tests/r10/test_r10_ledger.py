#!/usr/bin/env python3
"""Host tests for r10_ledger.py and r10_tolerance.py. No device, no round consumed."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "common"))
import r10_ledger as L
import r10_tolerance as T
import gatea_counters as C


def ev(role, event, src):
    return (f"I/gatea-telemetry: GATEA_EVENT seq=1 role={role} event={event} "
            f"generation=1 serial=0 src={src} dst=0\n")


def clean_session(ids=(6, 7)):
    """register + lease + release + unregister, both roles, per id."""
    out = ""
    for i in ids:
        for role in (1, 2):
            out += ev(role, L.EVENT_REGISTER_READY, i)
    out += ev(1, L.EVENT_LEASE_RESERVED, 6) + ev(1, L.EVENT_LEASE_GPU_OWNED, 6)
    out += ev(1, L.EVENT_LEASE_RELEASE, 6)
    for i in ids:
        for role in (1, 2):
            out += ev(role, L.EVENT_UNREGISTER_ACK, i)
    return out


class DerivedState(unittest.TestCase):
    def test_clean_session_is_zero_everywhere(self):
        d = L.derived_state(clean_session(), 0)
        self.assertEqual((d["x_registry_current"], d["renderer_registry_current"],
                          d["lease_current"]), (0, 0, 0))
        self.assertEqual(d["direct_success_events"], 1)
        self.assertEqual(d["source"], "ring_events")

    def test_unreleased_registration_is_counted(self):
        raw = ev(1, L.EVENT_REGISTER_READY, 6) + ev(2, L.EVENT_REGISTER_READY, 6)
        d = L.derived_state(raw, 0)
        self.assertEqual((d["x_registry_current"], d["renderer_registry_current"]),
                         (1, 1))

    def test_overflowed_ring_is_null_not_a_number(self):
        """An overflowed ring has lost earlier events, so a difference from what
        survives UNDERCOUNTS what is still held. Undercounting a leak is worse than
        reporting nothing, so it must be null."""
        d = L.derived_state(clean_session(), 3)
        self.assertIsNone(d["x_registry_current"])
        self.assertEqual(d["source"], "ring_overflowed")

    def test_falls_back_to_the_logcat_slice_when_no_dump_exists(self):
        """Mode A never dumps between iterations, so the ring file is empty and the
        live stream is the only source."""
        d = L.derived_state("", None, clean_session())
        self.assertEqual(d["source"], "logcat_events")
        self.assertEqual(d["lease_current"], 0)

    def test_no_events_at_all_is_null(self):
        d = L.derived_state("", None, "")
        self.assertIsNone(d["lease_current"])
        self.assertEqual(d["source"], "no_events")


class Counters(unittest.TestCase):
    def test_reads_by_name_not_by_guessed_index(self):
        s = ("GATEA_SUMMARY where=x-close-screen nonce=0 generation=0 overflow=0 "
             "c12=8 c13=8 c18=0 c19=0 c20=0 c25=8 c26=8 c27=1")
        c = L.counters(s)
        self.assertEqual(c["c_ahb_acquire"], 8)
        self.assertEqual(c["c_x_registry_current"], 0)
        self.assertEqual(c["c_unregister"], 8)      # c25 is UNREGISTER, not registry
        self.assertEqual(c["where"], "x-close-screen")
        self.assertEqual((c["nonce_at_dump"], c["generation_at_dump"]), (0, 0))
        self.assertEqual(c["ring_overflow"], 0)

    def test_missing_counter_is_none(self):
        c = L.counters("GATEA_SUMMARY where=x")
        self.assertIsNone(c["c_lease_current"])

    def test_no_summary_at_all(self):
        c = L.counters("")
        self.assertFalse(c["summary_present"])
        self.assertIsNone(c["ring_overflow"])


class SessionTuple(unittest.TestCase):
    def test_reads_the_live_bind_not_the_zeroed_dump(self):
        raw = ("I/x: GATEA_BIND version=1 nonce=512192170519862193 generation=1 bound=1\n")
        s = L.session_tuple(raw)
        self.assertEqual(s["session_nonce"], 512192170519862193)
        self.assertEqual(s["session_generation"], 1)

    def test_unbound_lines_do_not_count(self):
        raw = "I/x: GATEA_BIND version=1 nonce=0 generation=0 bound=0\n"
        self.assertIsNone(L.session_tuple(raw)["session_nonce"])

    def test_no_slice_means_null_not_another_rounds_tuple(self):
        self.assertIsNone(L.session_tuple("")["session_nonce"])


class Tolerance(unittest.TestCase):
    @staticmethod
    def _noise(values_by_round):
        rounds = []
        for i, v in enumerate(values_by_round, 1):
            rounds.append({"round": i, "samples": {
                "B0": {"x": {"pid": 1, "fd_count": v, "rss_kb": 1000 * v},
                       "activity": {"pid": 2}}}})
        return {"mode": "noise", "rounds": rounds, "halts": [],
                "stable_before": {"a": 1}, "stable_after": {"a": 1},
                "apk_sha256": "x", "source_sha": "y"}

    def test_tolerance_is_twice_the_worst_idle_step(self):
        t = T.tolerances(self._noise([10, 12, 11, 14, 13]))["tolerance"]
        self.assertEqual(t["x.fd_count"], 6)        # worst step 3 -> 6

    def test_counts_have_a_floor_of_one(self):
        t = T.tolerances(self._noise([10, 10, 10, 10, 10]))["tolerance"]
        self.assertEqual(t["x.fd_count"], 1)

    def test_kb_metrics_have_a_floor_of_512(self):
        t = T.tolerances(self._noise([10, 10, 10, 10, 10]))["tolerance"]
        self.assertEqual(t["x.rss_kb"], 512)

    def test_pid_is_not_a_metric(self):
        t = T.tolerances(self._noise([10, 11, 12, 13, 14]))["tolerance"]
        self.assertNotIn("x.pid", t)
        self.assertNotIn("activity.pid", t)

    def test_counters_get_no_tolerance(self):
        """Class 1 is exact. A tolerance on a protocol counter would be the first
        step to explaining away a real imbalance."""
        t = T.tolerances(self._noise([10, 11, 12, 13, 14]))["tolerance"]
        self.assertFalse([k for k in t if k.startswith("gatea.")])


class CounterIndices(unittest.TestCase):
    def test_ledger_uses_the_shared_source_bound_table(self):
        h = HERE.parent.parent / "lorie/src/main/cpp/lorie/lorie.h"
        if not h.is_file():
            self.skipTest("lorie.h not available")
        self.assertEqual(C.parse_from_source(h.read_text(errors="replace")), C.COUNTERS)
        self.assertEqual(C.X_REGISTRY_CURRENT, 18)


if __name__ == "__main__":
    unittest.main()
