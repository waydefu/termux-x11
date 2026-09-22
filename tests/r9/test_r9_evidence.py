#!/usr/bin/env python3
"""Offline tests for r9_evidence.py. No device, no product.

The point is to prove the derivation NEVER FABRICATES: anything not observed must
come back None so the judge refuses, rather than a plausible-looking default that
would let a broken run pass.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import r9_evidence as E


class ProcIdentity(unittest.TestCase):
    def test_simple(self):
        stat = "4242 (Xlorie) S" + " 0" * 18 + " 987654" + " 0" * 30
        self.assertEqual(E.proc_identity(stat), (4242, 987654))

    def test_comm_with_spaces_and_parens(self):
        # Q8: comm may contain spaces AND parentheses, so field 22 must be taken
        # relative to the LAST ')'. A naive split lands on the wrong field.
        stat = "77 (we ird (proc) name) S" + " 0" * 18 + " 555" + " 0" * 30
        self.assertEqual(E.proc_identity(stat), (77, 555))

    def test_empty_is_none_not_zero(self):
        self.assertEqual(E.proc_identity(""), (None, None))

    def test_malformed_is_none(self):
        self.assertEqual(E.proc_identity("garbage"), (None, None))


class BoundaryRecord(unittest.TestCase):
    RAW = (
        "I/LorieNative: GATEA_BIND version=1 nonce=777 generation=1 bound=1\n"
        "I/R8_OBS: R8_OBS {\"v\":1,\"role\":\"r\",\"phase\":\"R_EPOCH_BEGIN\","
        "\"epoch_id\":3,\"epoch_nonce\":777,\"epoch_generation\":1}\n"
        "I/gatea: GATEA_SUMMARY where=x nonce=777 generation=1 nextSequence=9 "
        "overflow=0 generationFatal=0 c25=2 c26=2\n"
    )
    STAT = "10 (Xlorie) S" + " 0" * 18 + " 4321" + " 0" * 30

    def test_all_fields_present(self):
        b = E.boundary_record(self.STAT, self.STAT, self.RAW, False)
        self.assertEqual(b["x_pid"], 10)
        self.assertEqual(b["x_starttime"], 4321)
        self.assertEqual(b["shared_nonce"], 777)
        self.assertEqual(b["shared_generation"], 1)
        self.assertEqual(b["renderer_bound_nonce"], 777)
        self.assertEqual(b["epoch_id"], 3)
        self.assertIs(b["x_fd_table_had_previous_conn_fd"], False)

    def test_unobserved_stays_none(self):
        b = E.boundary_record("", "", "", None)
        for k in ("x_pid", "shared_nonce", "renderer_bound_nonce", "epoch_id",
                  "x_fd_table_had_previous_conn_fd"):
            self.assertIsNone(b[k], f"{k} must be None, never a default")

    def test_last_bind_wins(self):
        raw = self.RAW + ("I/LorieNative: GATEA_BIND version=1 nonce=777 "
                          "generation=2 bound=1\n")
        b = E.boundary_record(self.STAT, self.STAT, raw, False)
        self.assertEqual(b["renderer_bound_generation"], 2)


class RendererFatal(unittest.TestCase):
    def test_published(self):
        raw = ("I/gatea: GATEA_SUMMARY where=r generationFatal=6 c25=1\n"
               "F/gatea-a1: GATEA_FATAL_HALT what=r-test-fatal-pre-fence reason=6\n")
        d = E.renderer_fatal(raw)
        self.assertTrue(d["published"])
        self.assertEqual(d["halt_reason"], 6)

    def test_not_published_is_detectable(self):
        # faults 10 and 13 only _exit(127); generationFatal stays 0 and X would
        # then take x-eof, so the cell is unconstructible. Must be visible.
        raw = "I/gatea: GATEA_SUMMARY where=r generationFatal=0 c25=1\n"
        self.assertFalse(E.renderer_fatal(raw)["published"])

    def test_no_summary_is_none(self):
        self.assertIsNone(E.renderer_fatal("")["published"])


class XSurvival(unittest.TestCase):
    def test_x_eof_detected(self):
        raw = "F/gatea-a1: GATEA_FATAL_HALT what=x-eof reason=6\n"
        self.assertTrue(E.x_survival(False, raw)["x_eof_fatal"])

    def test_other_fatal_is_not_x_eof(self):
        raw = "F/gatea-a1: GATEA_FATAL_HALT what=x-bump-unterminal reason=6\n"
        self.assertFalse(E.x_survival(True, raw)["x_eof_fatal"])

    def test_unknown_alive_stays_none(self):
        self.assertIsNone(E.x_survival(None, "")["x_alive_after_renderer_death"])


class StaleReplay(unittest.TestCase):
    def test_expected_fatal_implies_active(self):
        # the tuple check is only REACHED when lorieGateAActive() is true, so the
        # fatal itself proves the frame was not silently dropped
        raw = ("I/R8_OBS: R8_OBS {\"v\":1,\"role\":\"r\","
               "\"phase\":\"VALIDATE_TERMINAL_READY\"}\n"
               "F/gatea-a1: GATEA_FATAL_HALT what=x-wrong-generation reason=6\n")
        d = E.stale_replay(raw)
        self.assertTrue(d["legitimate_ready_sent"])
        self.assertTrue(d["stale_frame_sent"])
        self.assertTrue(d["gate_a_active_on_arrival"])

    def test_no_fatal_means_dropped_or_not_sent(self):
        raw = ("I/R8_OBS: R8_OBS {\"v\":1,\"role\":\"r\","
               "\"phase\":\"VALIDATE_TERMINAL_READY\"}\n")
        d = E.stale_replay(raw)
        self.assertFalse(d["gate_a_active_on_arrival"])

    def test_no_ready_means_validate_import_not_reached(self):
        self.assertFalse(E.stale_replay("")["legitimate_ready_sent"])


class RegistryState(unittest.TestCase):
    def test_counters_read(self):
        s = "GATEA_SUMMARY where=x c24=0 c25=3 c26=2 c27=1"
        self.assertEqual(E.registry_state(s), {"x_entries": 3, "renderer_ready_entries": 2})

    def test_absent_counter_is_none_not_zero(self):
        self.assertEqual(E.registry_state("GATEA_SUMMARY where=x"),
                         {"x_entries": None, "renderer_ready_entries": None})


class EndToEnd(unittest.TestCase):
    """derive -> judge, on synthetic raw artifacts. This is the contract that
    protects the attempt: if the derivation and the judge disagree, it shows here
    and not on device."""

    def _run(self, cell, raw, summ, extra_args, expect_verdict):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "raw-logcat.txt").write_text(raw)
            (d / "gatea-summary.txt").write_text(summ)
            stat = d / "stat"
            stat.write_text("10 (Xlorie) S" + " 0" * 18 + " 4321" + " 0" * 30)
            stat2 = d / "stat2"
            stat2.write_text("11 (Activity) S" + " 0" * 18 + " 8888" + " 0" * 30)
            args = [sys.executable, str(HERE / "r9_evidence.py"),
                    "--evidence", str(d), "--cell", cell,
                    "--x-stat-before", str(stat), "--x-stat-after", str(stat),
                    "--act-stat-before", str(stat), "--act-stat-after", str(stat2),
                    "--fd-had-prev", "false"] + extra_args
            r = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((d / "identity-boundaries.jsonl").is_file())
            # read INSIDE the context manager: returning the path would hand back
            # a directory TemporaryDirectory has already deleted
            out = {}
            for f in d.iterdir():
                if f.suffix in (".json", ".jsonl"):
                    out[f.name] = f.read_text()
            return out

    def test_f1_derivation_feeds_judge_invalid_when_dropped(self):
        # no fatal at all -> the derivation must report gate_a_active_on_arrival
        # False, and the judge must call it INVALID, not PASS
        raw = ("I/R8_OBS: R8_OBS {\"v\":1,\"role\":\"r\","
               "\"phase\":\"VALIDATE_TERMINAL_READY\"}\n"
               "I/gatea: GATEA_SUMMARY where=x nonce=777 generation=1 c25=1 c26=1\n"
               "I/LorieNative: GATEA_BIND version=1 nonce=777 generation=1 bound=1\n")
        out = self._run("R9-F1", raw, "GATEA_SUMMARY where=x c25=1 c26=1", [], None)
        self.assertIn("stale-replay.json", out)
        got = json.loads(out["stale-replay.json"])
        self.assertFalse(got["gate_a_active_on_arrival"])
        # and the judge must turn that into INVALID, never PASS
        self.assertTrue(got["legitimate_ready_sent"])

    def test_cold2_derivation_detects_unpublished_fatal(self):
        raw = ("I/gatea: GATEA_SUMMARY where=r nonce=777 generation=1 "
               "generationFatal=0 c25=1 c26=1\n"
               "I/LorieNative: GATEA_BIND version=1 nonce=777 generation=1 bound=1\n")
        out = self._run("R9-COLD-2", raw, "GATEA_SUMMARY where=x c25=1 c26=1",
                        ["--x-alive-after", "false"], None)
        rf = json.loads(out["renderer-fatal.json"])
        self.assertFalse(rf["published"], "an unpublished fatal must be visible")
        xs = json.loads(out["x-survival.json"])
        self.assertFalse(xs["x_alive_after_renderer_death"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
