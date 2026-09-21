#!/usr/bin/env python3
"""Host regressions for R8 semantic-vs-provenance observation ingestion.

Zero device mutation. Does not rewrite frozen attempt-08 verdicts.
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

from r8_obs_stream import collector_provenance, load_semantic_obs, parse_raw_obs
from r8_orchestration_v2 import (
    ends_are_producer_only,
    parse_obs_text,
    permit_judge,
    producer_finalized,
    scan_files,
)

ATTEMPT08 = Path(
    "/root/projects/GPU加速/evidence/session/gate-a-a1/p2-r8-runtime"
    "/runtime-b984ded/r8-c1/attempt-08-xcb-sender"
)
JUDGE_V2 = HERE / "judge-r8-v2.py"
FROZEN_JUDGE = HERE / "judge-r8.py"
FROZEN_JUDGE_SHA = (
    "f021048da3c1b729c6f9bf560eba52609b2f980336dd4fab77ad1700438c0e31"
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
    return [
        obs("x", "BEGIN", 0, expected_count=None, expected_digest=None),
        obs("x", "X_CLOSE_ENTER", 1),
        obs("x", "X_CLOSE_RESULT", 2),
        obs("x", "END", n, actual_count=n, actual_digest="00"),
    ]


def closed_r(n=1):
    return [
        obs("r", "BEGIN", 0, expected_count=None, expected_digest=None, case=None),
        obs("r", "R_UNBOUND_FINAL", 1),
        obs("r", "END", n, actual_count=n, actual_digest="00"),
    ]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def write_raw(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(raw_line(r) for r in rows) + "\n", encoding="utf-8")


def c1_permit_kwargs(**kw):
    base = dict(
        cell="R8-C1",
        shutdown_requested=True,
        fixture_text="RESULT p_r8_lifecycle C1 CLIENT_OK",
        fixture_alive=False,
        fixture_killed_by_runner=False,
        x_alive_after_construction=True,
    )
    base.update(kw)
    return base


class IngestionContract(unittest.TestCase):
    def test_same_obs_in_raw_and_collector_counts_once(self):
        x, r = closed_x(), closed_r()
        raw = "\n".join(raw_line(o) for o in x + r)
        xraw, rraw = parse_obs_text(raw)
        concat_x = xraw + x
        concat_r = rraw + r
        ok, reason = producer_finalized(concat_x, concat_r, "R8-C1")
        self.assertFalse(ok)
        self.assertEqual(reason, "MULTI_BEGIN_x")
        ok, reason = permit_judge(
            **c1_permit_kwargs(xrows=x, rrows=r, raw_text=raw, jsonl_rows=x + r)
        )
        self.assertTrue(ok, reason)
        ok, reason = producer_finalized(x, r, "R8-C1")
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "ok")

    def test_valid_begin_records_end_pass(self):
        x, r = closed_x(), closed_r()
        raw = "\n".join(raw_line(o) for o in x + r)
        ok, reason = permit_judge(
            **c1_permit_kwargs(xrows=x, rrows=r, raw_text=raw, jsonl_rows=x + r)
        )
        self.assertTrue(ok, reason)

    def test_duplicate_begin_inside_collector_multi_begin(self):
        x = closed_x()
        x = [x[0], x[0]] + x[1:]
        r = closed_r()
        raw = "\n".join(raw_line(o) for o in closed_x() + r)
        ok, reason = producer_finalized(x, r, "R8-C1")
        self.assertFalse(ok)
        self.assertEqual(reason, "MULTI_BEGIN_x")
        ok, reason = permit_judge(
            **c1_permit_kwargs(xrows=x, rrows=r, raw_text=raw, jsonl_rows=x + r)
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "MULTI_BEGIN_x")

    def test_duplicate_end_inside_collector_invalid(self):
        x = closed_x()
        x = x + [x[-1]]
        r = closed_r()
        ok, reason = producer_finalized(x, r, "R8-C1")
        self.assertFalse(ok)
        self.assertEqual(reason, "MULTI_END_x")

    def test_synthetic_collector_end_absent_from_raw(self):
        real_x = [
            obs("x", "BEGIN", 0, expected_count=None, expected_digest=None),
            obs("x", "X_CLOSE_ENTER", 1),
            obs("x", "X_CLOSE_RESULT", 2),
        ]
        fake_end = obs("x", "END", 99, actual_count=2, actual_digest="ff")
        x = real_x + [fake_end]
        r = closed_r()
        raw = "\n".join(raw_line(o) for o in real_x + r)
        ok, reason = ends_are_producer_only(raw, x + r)
        self.assertFalse(ok)
        self.assertEqual(reason, "COLLECTOR_SYNTHETIC_END")
        ok, reason = permit_judge(
            **c1_permit_kwargs(xrows=x, rrows=r, raw_text=raw, jsonl_rows=x + r)
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "COLLECTOR_SYNTHETIC_END")

    def test_provenance_mismatch_fail_closed(self):
        x, r = closed_x(), closed_r()
        mismatched = dict(x[1])
        mismatched["producer_seq"] = 99
        x2 = [x[0], mismatched, x[2], x[3]]
        raw = "\n".join(raw_line(o) for o in x + r)
        ok, reason = collector_provenance(raw, x2 + r)
        self.assertFalse(ok)
        self.assertEqual(reason, "PROVENANCE_MISMATCH")
        ok, reason = permit_judge(
            **c1_permit_kwargs(xrows=x2, rrows=r, raw_text=raw, jsonl_rows=x2 + r)
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "PROVENANCE_MISMATCH")

    def test_permit_cli_jsonl_only_not_concat(self):
        with tempfile.TemporaryDirectory() as td:
            ev = Path(td)
            x, r = closed_x(), closed_r()
            write_jsonl(ev / "x-observations.jsonl", x)
            write_jsonl(ev / "renderer-observations.jsonl", r)
            write_raw(ev / "raw-logcat.txt", x + r)
            (ev / "fixture.stdout").write_text(
                "RESULT p_r8_lifecycle C1 CLIENT_OK\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(HERE / "r8_orchestration_v2.py"),
                 "permit-judge", "--cell", "R8-C1", "--evidence", str(ev),
                 "--shutdown-requested", "1", "--fixture-alive", "0",
                 "--fixture-killed-by-runner", "0",
                 "--x-alive-after-construction", "1"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("PERMIT ok", proc.stdout)

    def test_wait_finalized_source_raw_only(self):
        text = (HERE / "r8_orchestration_v2.py").read_text(encoding="utf-8")
        wait = text.split('if args.cmd == "wait-finalized":', 1)[1]
        wait = wait.split("ev = Path(args.evidence)", 1)[1]
        wait = wait.split("ev = Path(args.evidence)", 1)[0]
        self.assertIn("scan_files(raw_paths)", wait)
        self.assertNotIn("load_semantic_obs", wait)
        self.assertNotIn("load_jsonl", wait)
        self.assertNotIn("x-observations.jsonl", wait)

    def test_class_b_c2_c5_full_still_require_terminate(self):
        x, r = closed_x(), closed_r()
        ok, reason = permit_judge(
            "R8-C2", shutdown_requested=True, xrows=x, rrows=r,
            fixture_text="RESULT p_r8_lifecycle C2 CLIENT_HOLD\nTERMINATE_SENT\n",
            fixture_alive=False, fixture_killed_by_runner=False,
            x_alive_after_construction=True,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "MISSING_R8_TERMINATE")
        text = (
            "C5_FULL registered=16\nCHECKPOINT phase=5\n"
            "RESULT p_r8_lifecycle C5-full CLIENT_HOLD\n"
            "TERMINATE_SENT\nTERMINATE_ACK\nX_HANGUP_AFTER_TERMINATE\n"
        )
        ok, reason = permit_judge(
            "R8-C5-full", shutdown_requested=True, xrows=x, rrows=r,
            fixture_text=text, fixture_alive=False,
            fixture_killed_by_runner=False, x_alive_after_construction=False,
        )
        self.assertTrue(ok, reason)


class JudgeV2Ingestion(unittest.TestCase):
    def _base_files(self, d: Path) -> None:
        stable = {
            "pid": 20146, "cmdline": "com.termux.x11", "versionName": "1.03.01",
            "versionCode": 1, "lastUpdateTime": 1,
        }
        (d / "stable-before.json").write_text(json.dumps(stable))
        (d / "stable-after.json").write_text(json.dumps(stable))
        (d / "pre-cleanup-process-state.json").write_text(json.dumps({
            "observed": True, "substituted_from_cleanup": False
        }))
        (d / "preflight.json").write_text(json.dumps({
            "unsafe_prestart": False, "artifact_mismatch": False
        }))
        (d / "artifact-binding.json").write_text(json.dumps({
            "source_sha": "deadbeef", "apk_sha256": "aa",
            "build_id": "bb", "signer": "cc"
        }))
        (d / "manifest.json").write_text(json.dumps({
            "cell": "R8-C1", "source_sha": "deadbeef", "apk_sha256": "aa",
            "build_id": "bb", "signer": "cc"
        }))

    def _c1_rows(self):
        x = [
            obs("x", "BEGIN", 0, expected_count=None, expected_digest=None),
            # D-17 widened judge-r8-v2.py's C1 residue proof beyond root_pending:
            # it now requires readIndex / writeIndex / completedSerial / pair_src /
            # pair_dst / generationFatal / firstFailed to be non-None, and proves
            # zero residue from those instead of from total_actual_buffer_pending,
            # which product b984ded hardcodes null (GAP-8). This fixture predated
            # that and therefore tripped PENDING_NOT_OBSERVED. The values match a
            # quiesced checkpoint in the real corpus
            # (runtime-b984ded/r8-c1/attempt-13/x-observations.jsonl).
            obs("x", "X_CHECKPOINT", 1, registry_count=0,
                total_actual_buffer_pending=0, root_pending=0, pair_state=0,
                pair_src=0, pair_dst=0, readIndex=0, writeIndex=0,
                completedSerial=0, generationFatal=0, firstFailed=0),
            obs("x", "X_DESTRUCTOR_ENTER", 2, bufferId=10, overlap=False),
            obs("x", "X_DESTRUCTOR_EXIT", 3, released=True),
            obs("x", "END", 3, actual_count=3, actual_digest="00"),
        ]
        r = [
            obs("r", "BEGIN", 0, expected_count=None, expected_digest=None),
            obs("r", "R_DESTROY_STAGE", 1, bufferId=10, texture_deleted=True,
                image_destroyed=True, ahb_released=True, case="R8-C1"),
            obs("r", "R_ACK_SETTLED", 2, bufferId=10, nonce=1, generation=1,
                case="R8-C1"),
            obs("r", "END", 2, actual_count=2, actual_digest="00"),
        ]
        return x, r

    def _run_judge(self, d: Path, judge: Path) -> tuple[int, str]:
        proc = subprocess.run(
            [sys.executable, str(judge), "--evidence", str(d),
             "--manifest", str(d / "manifest.json"), "--cell", "R8-C1",
             "--output", str(d / "judge.json")],
            capture_output=True, text=True, check=False,
        )
        reason = ""
        jp = d / "judge.json"
        if jp.is_file():
            try:
                reason = json.loads(jp.read_text()).get("reason", "")
            except json.JSONDecodeError:
                reason = ""
        return proc.returncode, reason

    def test_valid_jsonl_with_raw_duplicate_source_pass(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self._base_files(d)
            x, r = self._c1_rows()
            write_jsonl(d / "x-observations.jsonl", x)
            write_jsonl(d / "renderer-observations.jsonl", r)
            write_raw(d / "raw-logcat.txt", x + r)
            (d / "ring.txt").write_text(
                "GATEA_EVENT seq=1 role=1 event=3 generation=1 serial=1 src=10 dst=11\n"
                "GATEA_EVENT seq=2 role=2 event=26 generation=1 serial=0 src=10 dst=0\n"
                "GATEA_EVENT seq=3 role=1 event=25 generation=1 serial=0 src=10 dst=0\n"
            )
            rc, reason = self._run_judge(d, JUDGE_V2)
            self.assertEqual(rc, 0, reason)
            self.assertEqual(reason, "ACCEPT")

    def test_duplicate_begin_in_collector_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self._base_files(d)
            x, r = self._c1_rows()
            x = [x[0], x[0]] + x[1:]
            write_jsonl(d / "x-observations.jsonl", x)
            write_jsonl(d / "renderer-observations.jsonl", r)
            write_raw(d / "raw-logcat.txt", x + r)
            (d / "ring.txt").write_text(
                "GATEA_EVENT seq=1 role=1 event=3 generation=1 serial=1 src=10 dst=11\n"
                "GATEA_EVENT seq=2 role=2 event=26 generation=1 serial=0 src=10 dst=0\n"
                "GATEA_EVENT seq=3 role=1 event=25 generation=1 serial=0 src=10 dst=0\n"
            )
            rc, reason = self._run_judge(d, JUDGE_V2)
            self.assertEqual(rc, 2)
            self.assertEqual(reason, "MULTI_BEGIN_x")

    def test_duplicate_end_in_collector_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self._base_files(d)
            x, r = self._c1_rows()
            x = x + [x[-1]]
            write_jsonl(d / "x-observations.jsonl", x)
            write_jsonl(d / "renderer-observations.jsonl", r)
            write_raw(d / "raw-logcat.txt", x + r)
            (d / "ring.txt").write_text(
                "GATEA_EVENT seq=1 role=1 event=3 generation=1 serial=1 src=10 dst=11\n"
                "GATEA_EVENT seq=2 role=2 event=26 generation=1 serial=0 src=10 dst=0\n"
                "GATEA_EVENT seq=3 role=1 event=25 generation=1 serial=0 src=10 dst=0\n"
            )
            rc, reason = self._run_judge(d, JUDGE_V2)
            self.assertEqual(rc, 2)
            self.assertEqual(reason, "MULTI_END_x")

    def test_synthetic_end_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self._base_files(d)
            x, r = self._c1_rows()
            raw_x = [row for row in x if row["phase"] != "END"]
            write_jsonl(d / "x-observations.jsonl", x)
            write_jsonl(d / "renderer-observations.jsonl", r)
            write_raw(d / "raw-logcat.txt", raw_x + r)
            (d / "ring.txt").write_text(
                "GATEA_EVENT seq=1 role=1 event=3 generation=1 serial=1 src=10 dst=11\n"
                "GATEA_EVENT seq=2 role=2 event=26 generation=1 serial=0 src=10 dst=0\n"
                "GATEA_EVENT seq=3 role=1 event=25 generation=1 serial=0 src=10 dst=0\n"
            )
            rc, reason = self._run_judge(d, JUDGE_V2)
            self.assertEqual(rc, 2)
            self.assertEqual(reason, "COLLECTOR_SYNTHETIC_END")


class Attempt08ReplayNonAuthority(unittest.TestCase):
    def test_frozen_verdict_unchanged_and_old_concat_was_ingestion(self):
        self.assertTrue(ATTEMPT08.is_dir(), f"missing {ATTEMPT08}")
        verdict_path = ATTEMPT08 / "VERDICT.txt"
        before = verdict_path.read_text(encoding="utf-8")
        self.assertIn("R8_INVALID", before)
        self.assertIn("JUDGE_NOT_PERMITTED", before)
        self.assertIn("MULTI_BEGIN_x", before)
        frozen_sha = __import__("hashlib").sha256(FROZEN_JUDGE.read_bytes()).hexdigest()
        self.assertEqual(frozen_sha, FROZEN_JUDGE_SHA)

        raw_paths = [
            ATTEMPT08 / "raw-logcat.txt",
            ATTEMPT08 / "gatea-ring.txt",
            ATTEMPT08 / "gatea-summary.txt",
            ATTEMPT08 / "x3-launcher.raw.log",
        ]
        xraw, rraw, blob = scan_files(raw_paths)
        xjson, rjson = load_semantic_obs(ATTEMPT08)
        ok_old, reason_old = producer_finalized(xraw + xjson, rraw + rjson, "R8-C1")
        self.assertFalse(ok_old)
        self.assertEqual(reason_old, "MULTI_BEGIN_x")
        ok_new, reason_new = producer_finalized(xjson, rjson, "R8-C1")
        self.assertTrue(ok_new, reason_new)
        ok_prov, reason_prov = collector_provenance(blob, xjson + rjson)
        self.assertTrue(ok_prov, reason_prov)

        proc = subprocess.run(
            [sys.executable, str(HERE / "r8_orchestration_v2.py"),
             "permit-judge", "--cell", "R8-C1", "--evidence", str(ATTEMPT08),
             "--shutdown-requested", "1", "--fixture-alive", "0",
             "--fixture-killed-by-runner", "0",
             "--x-alive-after-construction", "0"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PERMIT ok", proc.stdout)

        after = verdict_path.read_text(encoding="utf-8")
        self.assertEqual(after, before)
        self.assertFalse((ATTEMPT08 / "judge.json").exists())
        permit_txt = (ATTEMPT08 / "permit-judge.txt").read_text(encoding="utf-8")
        self.assertEqual(permit_txt.strip(), "REFUSE MULTI_BEGIN_x")


if __name__ == "__main__":
    unittest.main()
