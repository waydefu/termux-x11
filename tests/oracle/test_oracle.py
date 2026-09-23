#!/usr/bin/env python3
"""Offline tests for judge-oracle.py. Every rule has a case that must go red."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
FREEZE = json.loads((HERE / "oracle-freeze.json").read_text())
spec = importlib.util.spec_from_file_location("judge_oracle", HERE / "judge-oracle.py")
J = importlib.util.module_from_spec(spec)
spec.loader.exec_module(J)
X, ACT = 500, 600
B = dt.datetime(2026, 9, 24, 10, 0, 0).timestamp()


def lc(ep, pid, tag, msg):
    t = dt.datetime.fromtimestamp(ep)
    return f"{t:%m-%d %H:%M:%S}.{t.microsecond // 1000:03d} {pid:5d} {pid:5d} I {tag}: {msg}"


class Run:
    def __init__(self, d: Path):
        self.d = d
        d.mkdir(parents=True)
        self.phases = {"persistent": (1298, 0, 0), "fresh": (36, 0, 0), "negative": (7, 0, 0)}
        self.marks = {"persistent": (B, B + 100), "fresh": (B + 101, B + 110), "negative": (B + 111, B + 112)}
        self.direct = {"persistent": 1298, "fresh": 30, "negative": 0}
        self.summary = ("GATEA_SUMMARY where=x-close-screen nonce=0 generation=0 nextSequence={n} overflow=1 "
                        "firstFailed=0 generationFatal=0 fatalReason=0 c0={c0} c12=9 c13=9 c14=9 c15=9 c16=9 c17=9 "
                        "c18=0 c19=0 c20=0 c21=0 c22=0 c23=0 c24=0 c25=9 c26=9 c27=1")
        self.drop = 0
        self.extra = []
        self.noise = 0          # non-direct GATEA_EVENT lines per phase window (event 30)
        # V2: one MARK window per negative case, QUIET_MS-like gaps inside the negative phase
        self.neg_kinds = ["src-op", "mask-a8", "dst-argb", "bilinear", "repeat", "transform",
                          "component-alpha"]
        self.neg_direct = {}    # kind -> event=5 lines inside that case's window
        self.at = []            # extra event=5 lines at these exact epochs (boundary tests)
        self.drop_noise = 0     # lose this many event=30 lines (logd drop of a NON-direct line)
        (d / "x3-pid.txt").write_text(f"x3_pid={X}\n")
        (d / "activity-pid.txt").write_text(f"activity_pid={ACT}\n")
        (d / "activity-pid-after-close.txt").write_text(f"activity_pid_after_close={ACT}\n")
        (d / "x3-tracer.txt").write_text("PPid:\t8415\nTracerPid:\t0\n")
        for k in ("screen-pre.json", "screen-post.json"):
            (d / k).write_text(json.dumps({"awake": True, "keyguard": False}))
        for k in ("stable-before.json", "stable-after.json"):
            (d / k).write_text(json.dumps({"pid": 1, "versionName": "x"}))
        (d / "close.json").write_text(json.dumps({"x_alive_after_terminate": False}))
        (d / "capture-end.json").write_text(json.dumps({"logcat_alive": True}))
        self.write()

    def write(self):
        o = []
        for ph in ("persistent", "fresh", "negative"):
            b, e = self.marks[ph]
            cases, fail, maxd = self.phases[ph]
            o += [f"MARK {ph} BEGIN {b:.6f}"]
            if ph == "negative":
                for k, (nb, ne) in self.neg_windows().items():
                    o += [f"MARK neg-{k} BEGIN {nb:.6f}", f"MARK neg-{k} END {ne:.6f}"]
            o += [f"MARK {ph} END {e:.6f}",
                  f"PHASE_RESULT {ph} cases={cases} fail={fail} maxd={maxd} exact_px=1 xnz_px=0"]
        fail_any = any(v[1] for v in self.phases.values())
        o.append(f"RESULT p_v1_oracle {'FAIL' if fail_any else 'PASS'}")
        (self.d / "oracle.out").write_text("\n".join(o) + "\n")
        L, seq = [], 0
        for ph in ("persistent", "fresh", "negative"):
            b, e = self.marks[ph]
            n = self.direct[ph]
            for i in range(n):
                L.append(lc(b + (e - b) * (i + 0.5) / max(n, 1), X, "gatea-telemetry",
                            f"GATEA_EVENT seq={seq} role=1 event=5 generation=1 serial={seq} src=1 dst=2"))
                seq += 1
            for i in range(self.noise):
                L.append(lc(b + (e - b) * (i + 0.25) / max(self.noise, 1), X, "gatea-telemetry",
                            f"GATEA_EVENT seq={seq} role=1 event=30 generation=1 serial={seq} src=1 dst=2"))
                seq += 1
        for k, n in self.neg_direct.items():
            nb, ne = self.neg_windows()[k]
            for i in range(n):
                L.append(lc((nb + ne) / 2, X, "gatea-telemetry",
                            f"GATEA_EVENT seq={seq} role=1 event=5 generation=1 serial={seq} src=1 dst=2"))
                seq += 1
        for t in self.at:
            L.append(lc(t, X, "gatea-telemetry",
                        f"GATEA_EVENT seq={seq} role=1 event=5 generation=1 serial={seq} src=1 dst=2"))
            seq += 1
        n5 = sum(1 for ln in L if " event=5 " in ln)       # what X emitted: c0 == event=5 count
        for i in range(self.drop):
            L.pop(0)
        for i in range(self.drop_noise):
            L.remove(next(ln for ln in L if " event=30 " in ln))
        L += self.extra
        (self.d / "raw-logcat.txt").write_text("\n".join(L) + "\n")
        (self.d / "gatea-summary.txt").write_text(self.summary.format(n=seq, c0=n5) + "\n")

    def neg_windows(self):
        b = self.marks["negative"][0]
        return {k: (b + 0.05 + i * 0.13, b + 0.11 + i * 0.13) for i, k in enumerate(self.neg_kinds)}

    def judge(self):
        return J.judge(self.d, FREEZE)


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.r = Run(self.tmp / "e")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def v(self):
        return self.r.judge()["verdict"]

    def test_clean_pass(self):
        self.assertEqual(self.v(), "PASS")

    def test_pixel_mismatch(self):
        self.r.phases["persistent"] = (1298, 1, 3); self.r.write()
        self.assertEqual(self.v(), "FAIL_CORRECTNESS")

    def test_phase_fail_with_result_pass_line(self):
        # the per-phase check must stand on its own, not lean on the RESULT line
        self.r.phases["fresh"] = (36, 1, 2); self.r.write()
        out = (self.r.d / "oracle.out").read_text().replace("RESULT p_v1_oracle FAIL", "RESULT p_v1_oracle PASS")
        (self.r.d / "oracle.out").write_text(out)
        self.assertEqual(self.v(), "FAIL_CORRECTNESS")

    def test_negative_pixel_mismatch(self):
        self.r.phases["negative"] = (7, 1, 255); self.r.write()
        self.assertEqual(self.v(), "FAIL_CORRECTNESS")

    def test_not_all_direct_is_attribution(self):
        self.r.direct["persistent"] = 1290; self.r.write()
        self.assertEqual(self.v(), "FAIL_ATTRIBUTION")

    def test_other_events_in_window_are_not_direct(self):
        # only event 5 (LEASE_GPU_OWNED) is a direct composite; the far more frequent
        # event 30 lines in the same windows must not be counted
        self.r.noise = 50; self.r.write()
        self.assertEqual(self.v(), "PASS")

    def test_boundary_event_counted_once(self):
        # V2 window (BEGIN - 1 ms, END]. Three extra event=5 lines:
        #   at persistent END exactly            -> persistent only
        #   true time 0.2 ms after fresh BEGIN, whose ms-truncated stamp falls BEFORE BEGIN
        #                                        -> fresh (the 1 ms allowance), once
        #   3 ms before fresh BEGIN (quiet gap)  -> no phase (V1's +-5 ms credited it to fresh)
        self.r.direct["persistent"] = 1297
        self.r.direct["fresh"] = 30
        pe = self.r.marks["persistent"][1]
        fb = pe + 0.0605                       # BEGIN not on a millisecond boundary
        self.r.marks["fresh"] = (fb, self.r.marks["fresh"][1])
        self.r.at = [pe, fb + 0.0002, fb - 0.003]
        self.r.write()
        v = self.r.judge()
        det = {c["check"]: c["detail"] for c in v["checks"]["attribution"]}
        self.assertEqual(det["persistent_all_direct"]["event5"], 1298)
        self.assertEqual(v["fresh_direct_reported"], 31)
        self.assertEqual(v["verdict"], "PASS")

    def test_direct_in_named_negative_case(self):
        self.r.neg_direct = {"repeat": 1}; self.r.write()
        v = self.r.judge()
        self.assertEqual(v["verdict"], "FAIL_CORRECTNESS")
        det = {c["check"]: c["detail"] for c in v["checks"]["attribution"]}
        self.assertEqual(det["negative_no_direct"]["per_case"]["neg-repeat"], 1)
        self.assertEqual(det["negative_no_direct"]["per_case"]["neg-src-op"], 0)

    def test_back_to_back_phases_are_invalid(self):
        # the V1 construction: no quiet gap -> windows can overlap -> not a V2 run
        pe = self.r.marks["persistent"][1]
        self.r.marks["fresh"] = (pe + 0.00002, self.r.marks["fresh"][1]); self.r.write()
        v = self.r.judge()
        self.assertIn("phase_gaps_quiet", v["failed"]["validity"])
        self.assertEqual(v["verdict"], "INVALID")

    def test_missing_negative_case_marks_is_invalid(self):
        self.r.neg_kinds = self.r.neg_kinds[:6]; self.r.write()
        self.assertIn("phase_gaps_quiet", self.r.judge()["failed"]["validity"])

    def test_direct_in_negative_is_correctness(self):
        self.r.direct["negative"] = 1; self.r.write()
        self.assertEqual(self.v(), "FAIL_CORRECTNESS")

    def test_fresh_shortfall_is_only_reported(self):
        self.r.direct["fresh"] = 0; self.r.write()
        self.assertEqual(self.v(), "PASS")

    def test_non_direct_line_lost_is_still_valid(self):
        # V3: one event=30 line lost (logd), every event=5 line present == c0 -> attributable
        self.r.noise = 20; self.r.drop_noise = 1; self.r.write()
        v = self.r.judge()
        det = {c["check"]: c for c in v["checks"]["validity"]}["event5_stream_complete"]
        self.assertTrue(det["ok"])
        self.assertFalse(det["detail"]["all_events_complete"])
        self.assertEqual(v["verdict"], "PASS")

    def test_fatal_disables_the_c0_rule(self):
        # the c0 equality is only proven for runs without a fatal
        self.r.noise = 20; self.r.drop_noise = 1
        self.r.summary = self.r.summary.replace("firstFailed=0", "firstFailed=7"); self.r.write()
        v = self.r.judge()
        det = {c["check"]: c for c in v["checks"]["validity"]}["event5_stream_complete"]
        self.assertFalse(det["ok"])

    def test_events_incomplete_is_invalid(self):
        self.r.drop = 1; self.r.write()
        self.assertEqual(self.v(), "INVALID")

    def test_case_count_wrong_is_invalid(self):
        self.r.phases["persistent"] = (1297, 0, 0); self.r.direct["persistent"] = 1297; self.r.write()
        self.assertEqual(self.v(), "INVALID")

    def test_traced_is_invalid(self):
        (self.r.d / "x3-tracer.txt").write_text("TracerPid:\t20859\n")
        self.assertEqual(self.v(), "INVALID")

    def test_screen_off_is_invalid(self):
        (self.r.d / "screen-post.json").write_text(json.dumps({"awake": False, "keyguard": True}))
        self.assertEqual(self.v(), "INVALID")

    def test_stable_changed_is_invalid(self):
        (self.r.d / "stable-after.json").write_text(json.dumps({"pid": 2, "versionName": "x"}))
        self.assertEqual(self.v(), "INVALID")

    def test_lifecycle_imbalance(self):
        self.r.summary = self.r.summary.replace("c13=9", "c13=8"); self.r.write()
        self.assertEqual(self.v(), "FAIL_CORRECTNESS")

    def test_fatal_line(self):
        self.r.extra = [lc(B + 50, X, "libc", "Fatal signal 11 (SIGSEGV)")]; self.r.write()
        self.assertEqual(self.v(), "FAIL_CORRECTNESS")

    def test_correctness_beats_invalid(self):
        self.r.phases["fresh"] = (36, 2, 9); self.r.write()
        (self.r.d / "x3-tracer.txt").write_text("TracerPid:\t20859\n")
        self.assertEqual(self.v(), "FAIL_CORRECTNESS")

    def test_activity_died(self):
        (self.r.d / "activity-pid-after-close.txt").write_text("activity_pid_after_close=\n")
        self.assertEqual(self.v(), "FAIL_CORRECTNESS")


if __name__ == "__main__":
    unittest.main(verbosity=1)
