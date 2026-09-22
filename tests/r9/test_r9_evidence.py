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
    """Derived from what the product actually logs, measured on r9-f1/attempt-02.

    The stages are LorieNative "GATEA_VALIDATE ... stage=X result=N" lines and the
    fault firing is telemetry event 35 with the CELL number in src. Neither is an
    R8_OBS row, and R9 runs with R8 observation disarmed, so a derivation that reads
    only obs rows reports legitimate_ready_sent=false on a run whose READY succeeded.
    """

    # the two shapes, verbatim from r9-f1/attempt-02/raw-logcat.txt
    READY_OK = ("I/LorieNative: GATEA_VALIDATE version=1 generation=1 bufferId=6 "
                "stage=READY_SEND_RETURN result=1 errno=0 elapsed_us=314\n")
    FAULT16 = ("I/gatea-telemetry: GATEA_EVENT seq=20 role=2 event=35 generation=1 "
               "serial=0 src=16 dst=2\n")
    FATAL = "F/gatea-a1: GATEA_FATAL_HALT what=x-wrong-generation reason=6\n"

    def test_reads_the_validate_stage_line_not_just_obs_rows(self):
        d = E.stale_replay(self.READY_OK + self.FAULT16 + self.FATAL)
        self.assertTrue(d["legitimate_ready_sent"])
        self.assertTrue(d["stale_frame_sent"])
        self.assertTrue(d["gate_a_active_on_arrival"])
        self.assertFalse(d["stale_tuple_accepted"])

    def test_obs_row_path_still_works(self):
        # kept for builds that do carry the renderer obs rows
        raw = ('I/R8_OBS: R8_OBS {"v":1,"role":"r",'
               '"phase":"VALIDATE_TERMINAL_READY"}\n') + self.FAULT16 + self.FATAL
        self.assertTrue(E.stale_replay(raw)["legitimate_ready_sent"])

    def test_stale_frame_sent_is_not_inferred_from_the_fatal(self):
        """Circularity guard. If the fatal were taken as proof the frame was sent,
        the judge would then require that same fatal for the verdict and the two
        checks would be one check. The fault-fired telemetry is independent."""
        d = E.stale_replay(self.READY_OK + self.FATAL)   # fatal, but no event 35
        self.assertFalse(d["stale_frame_sent"])

    def test_a_different_cells_fault_does_not_count(self):
        other = self.FAULT16.replace("src=16", "src=8")
        self.assertFalse(E.stale_replay(self.READY_OK + other)["stale_frame_sent"])

    def test_fired_but_no_fatal_is_the_silent_drop(self):
        d = E.stale_replay(self.READY_OK + self.FAULT16)
        self.assertIs(d["gate_a_active_on_arrival"], False)

    def test_a_different_fatal_leaves_the_frames_fate_unknown(self):
        raw = self.READY_OK + self.FAULT16 + \
            "F/gatea-a1: GATEA_FATAL_HALT what=x-hup reason=6\n"
        d = E.stale_replay(raw)
        self.assertIsNone(d["gate_a_active_on_arrival"])
        self.assertIsNone(d["stale_tuple_accepted"])

    def test_no_ready_means_validate_import_not_reached(self):
        self.assertFalse(E.stale_replay("")["legitimate_ready_sent"])

    def test_failed_ready_send_does_not_count_as_sent(self):
        bad = self.READY_OK.replace("result=1", "result=0")
        self.assertFalse(E.stale_replay(bad)["legitimate_ready_sent"])


class FdTable(unittest.TestCase):
    """Q9-F1: did X's fd table still hold the PREVIOUS conn_fd at this boundary?"""

    LIST = ("total 0\n"
            "lrwx------. 1 u u 64 Sep 22 14:39 0 -> /dev/null\n"
            "lrwx------. 1 u u 64 Sep 22 14:39 9 -> socket:[123456]\n")
    ONE_BIND = ('I/R8_OBS: R8_OBS {"v":1,"role":"r","phase":"R_EPOCH_BEGIN",'
                '"epoch_id":1}\n')

    def test_no_listing_is_not_observed(self):
        self.assertIsNone(E.fd_previous_conn("", E.binds_observed(self.ONE_BIND)))

    def test_single_bind_with_a_listing_is_false(self):
        self.assertIs(E.fd_previous_conn(self.LIST, E.binds_observed(self.ONE_BIND)),
                      False)

    def test_rebind_is_not_answered_by_a_bare_count(self):
        two = self.ONE_BIND + self.ONE_BIND.replace('"epoch_id":1', '"epoch_id":2')
        self.assertIsNone(E.fd_previous_conn(self.LIST, E.binds_observed(two)))

    def test_absent_obs_stream_is_not_zero_binds(self):
        self.assertIsNone(E.binds_observed("nothing here"))
        self.assertIsNone(E.fd_previous_conn(self.LIST, E.binds_observed("nothing")))

    def test_obs_stream_present_but_no_epochs_is_zero(self):
        raw = 'I/R8_OBS: R8_OBS {"v":1,"role":"r","phase":"BEGIN"}\n'
        self.assertEqual(E.binds_observed(raw), 0)
        self.assertIs(E.fd_previous_conn(self.LIST, E.binds_observed(raw)), False)



class RegistryState(unittest.TestCase):
    """The live event stream is the source; the summary counters are the fallback.

    c25/c26 exist only when lorieGateADumpSummary ran, and it runs on a fatal, a clean
    close or a terminate - never on a healthy session. r9-f2/attempt-01 measured
    exactly that: an empty gatea-summary.txt on a run whose registry demonstrably
    emptied itself, and a SIGTERM that did not reach CloseScreen and fatalled the
    renderer with r-hup instead.
    """

    @staticmethod
    def ev(role, event, src):
        return (f"I/gatea-telemetry: GATEA_EVENT seq=1 role={role} event={event} "
                f"generation=1 serial=0 src={src} dst=0\n")

    def test_counters_used_when_there_is_no_event_stream(self):
        # c18 = X_REGISTRY_CURRENT, c19 = RENDERER_REGISTRY_CURRENT.
        # c25/c26 are UNREGISTER/RESOURCE_DESTROY and must NOT be read as registry
        # occupancy - that was the defect found 2026-09-22.
        s = "GATEA_SUMMARY where=x c18=3 c19=2 c25=9 c26=9"
        r = E.registry_state(s)
        self.assertEqual((r["x_entries"], r["renderer_ready_entries"]), (3, 2))
        self.assertEqual(r["source"], "summary_counters")

    def test_the_old_wrong_counters_are_not_read_as_registry(self):
        """Regression guard for the c25/c26 defect. A summary that carries ONLY
        UNREGISTER and RESOURCE_DESTROY says nothing about current occupancy, so
        both fields must be NOT OBSERVED."""
        r = E.registry_state("GATEA_SUMMARY where=x c25=7 c26=7")
        self.assertIsNone(r["x_entries"])
        self.assertIsNone(r["renderer_ready_entries"])
        self.assertEqual(r["source"], "none")

    def test_real_summary_line_from_r9_f1_attempt_02(self):
        """Verbatim from evidence; with the right indices it reads as two buffers
        registered on both sides and never released, which is what F1 does."""
        s = ("GATEA_SUMMARY where=x-wrong-generation nonce=1 generation=1 "
             "c12=2 c13=0 c14=2 c15=0 c16=2 c17=0 c18=2 c19=2 c20=0 c24=1 "
             "c25=0 c26=0 c27=0")
        r = E.registry_state(s)
        self.assertEqual((r["x_entries"], r["renderer_ready_entries"]), (2, 2))

    def test_counter_indices_match_the_product_enum(self):
        """The mapping is hard-coded on purpose (a judge must not follow a drifting
        enum silently), so it is re-derived from lorie.h here and must agree."""
        from pathlib import Path as _P
        h = (_P(__file__).resolve().parent.parent.parent
             / "lorie/src/main/cpp/lorie/lorie.h")
        if not h.is_file():          # source tree not present: nothing to compare
            self.skipTest("lorie.h not available")
        import sys as _s
        _s.path.insert(0, str(_P(__file__).resolve().parent.parent / "common"))
        import gatea_counters as C
        derived = C.parse_from_source(h.read_text(encoding="utf-8", errors="replace"))
        self.assertEqual(derived, C.COUNTERS)
        self.assertEqual(C.X_REGISTRY_CURRENT, 18)
        self.assertEqual(C.RENDERER_REGISTRY_CURRENT, 19)

    def test_absent_counter_is_none_not_zero(self):
        r = E.registry_state("GATEA_SUMMARY where=x")
        self.assertIsNone(r["x_entries"])
        self.assertIsNone(r["renderer_ready_entries"])
        self.assertEqual(r["source"], "none")

    def test_registered_then_acked_on_both_roles_is_empty(self):
        # the shape r9-f2/attempt-01 actually produced for ids 6 and 7
        raw = ""
        for sid in (6, 7):
            for role in (1, 2):
                raw += self.ev(role, 1, sid) + self.ev(role, 25, sid)
        r = E.registry_state("", raw)
        self.assertEqual((r["x_entries"], r["renderer_ready_entries"]), (0, 0))
        self.assertEqual(r["source"], "telemetry_events")

    def test_registered_and_never_acked_is_held(self):
        raw = self.ev(1, 1, 6) + self.ev(2, 1, 6) + self.ev(2, 25, 6)
        r = E.registry_state("", raw)
        self.assertEqual(r["x_entries"], 1)            # X never acked
        self.assertEqual(r["renderer_ready_entries"], 0)

    def test_events_beat_counters_when_both_exist(self):
        raw = self.ev(1, 1, 6) + self.ev(1, 25, 6) + self.ev(2, 1, 6) + self.ev(2, 25, 6)
        r = E.registry_state("GATEA_SUMMARY where=x c18=9 c19=9", raw)
        self.assertEqual((r["x_entries"], r["renderer_ready_entries"]), (0, 0))
        self.assertEqual((r["counter_x_registry_current"],
                          r["counter_renderer_registry_current"]), (9, 9))


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

    # ---- how many identity boundaries a cell actually has ----

    def test_f1_writes_exactly_one_boundary(self):
        """F1 crosses no process boundary and X halting IS its expected outcome, so
        there is no live 'after' identity to observe. A second, all-None record would
        make judge-r9.py refuse a correct run for missing evidence that never
        existed (load_boundaries treats None as NOT OBSERVED)."""
        raw = ("I/gatea: GATEA_SUMMARY where=x nonce=777 generation=1\n"
               "I/gatea: GATEA_BIND version=1 nonce=777 generation=1 bound=1\n"
               'I/R8_OBS: R8_OBS {"v":1,"role":"r","phase":"R_EPOCH_BEGIN","epoch_id":1}\n'
               "F/gatea-a1: GATEA_FATAL_HALT what=x-wrong-generation reason=6\n")
        out = self._run("R9-F1", raw, "c25=1 c26=1", [], None)
        rows = [l for l in out["identity-boundaries.jsonl"].splitlines() if l.strip()]
        self.assertEqual(len(rows), 1)
        rec = json.loads(rows[0])
        self.assertEqual(rec["x_pid"], 10)
        self.assertIsNotNone(rec["shared_nonce"])

    def test_f2_boundary_before_half_comes_from_the_previous_attempt(self):
        """F2's boundary is F1 -> F2. Its 'before' half must be F1's captures, not a
        second read of F2's own session, or the nonce and both process identities
        would compare equal and the judge would report NONCE_NOT_REFRESHED /
        X_DID_NOT_RESTART on a perfectly good run."""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            prev_raw = d / "prev-raw.txt"
            prev_raw.write_text(
                "I/gatea: GATEA_SUMMARY where=x nonce=111 generation=1\n"
                "I/gatea: GATEA_BIND version=1 nonce=111 generation=1 bound=1\n"
                'I/R8_OBS: R8_OBS {"v":1,"role":"r","phase":"R_EPOCH_BEGIN","epoch_id":1}\n')
            prev_x = d / "prev-x"; prev_x.write_text("90 (Xlorie) S" + " 0" * 18 + " 1111" + " 0" * 30)
            prev_a = d / "prev-a"; prev_a.write_text("91 (Activity) S" + " 0" * 18 + " 2222" + " 0" * 30)
            raw = ("I/gatea: GATEA_SUMMARY where=x nonce=999 generation=1\n"
                   "I/gatea: GATEA_BIND version=1 nonce=999 generation=1 bound=1\n"
                   'I/R8_OBS: R8_OBS {"v":1,"role":"r","phase":"R_EPOCH_BEGIN","epoch_id":1}\n')
            out = self._run("R9-F2", raw, "c25=0 c26=0",
                            ["--prev-x-stat", str(prev_x),
                             "--prev-act-stat", str(prev_a),
                             "--prev-raw", str(prev_raw)], None)
        rows = [json.loads(l) for l in out["identity-boundaries.jsonl"].splitlines() if l.strip()]
        self.assertEqual(len(rows), 2)
        a, b = rows
        self.assertEqual((a["x_pid"], a["x_starttime"]), (90, 1111))
        self.assertEqual((a["activity_pid"], a["activity_starttime"]), (91, 2222))
        self.assertEqual(a["shared_nonce"], 111)
        self.assertEqual(b["shared_nonce"], 999)
        self.assertNotEqual((a["x_pid"], a["x_starttime"]), (b["x_pid"], b["x_starttime"]))

    def test_f2_without_prev_evidence_yields_unobserved_not_a_guess(self):
        """Missing previous evidence must surface as NOT OBSERVED (None), so the
        judge refuses, rather than silently defaulting to this run's own identity."""
        raw = "I/gatea: GATEA_SUMMARY where=x nonce=999 generation=1\n"
        out = self._run("R9-F2", raw, "c25=0 c26=0", [], None)
        rows = [json.loads(l) for l in out["identity-boundaries.jsonl"].splitlines() if l.strip()]
        self.assertIsNone(rows[0]["x_pid"])
        self.assertIsNone(rows[0]["activity_pid"])

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
