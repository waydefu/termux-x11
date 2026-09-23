#!/usr/bin/env python3
"""Offline tests for the XFCE-FREEZE-V3 collector + judge (and the choreo driver's
bookkeeping). No device, no X server.

Every rule that could silently turn a bad run green has a case that MUST go red.
A control that cannot fail is not a control - P2 closure learned that twice.

    python3 tests/xfce/test_xfce.py
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
FREEZE = json.loads((HERE / "xfce-design-freeze.json").read_text())


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


COL = _load("xfce_collect", "xfce_collect.py")
JDG = _load("judge_xfce", "judge-xfce.py")

X, ACT, STABLE = 697, 16171, 20881
T0_EPOCH = dt.datetime(2026, 9, 23, 12, 0, 0).timestamp()
T0_MONO = 200_000_000_000_000
CLEAN_SUMMARY = ("GATEA_SUMMARY where=x-close-screen nonce=0 generation=0 nextSequence={n} "
                 "overflow=1 firstFailed=0 generationFatal=0 fatalReason=0 "
                 "c0=4 c1=4 c2=4 c3=0 c4=4 c5=4 c6=4 c7=0 c8=8 c9=4 c10=4 c11=8 "
                 "c12=40 c13=40 c14=40 c15=40 c16=40 c17=40 c18=0 c19=0 c20=0 "
                 "c21=0 c22=0 c23=0 c24=0 c25=40 c26=40 c27=1")


def lc(epoch: float, pid: int, tag: str, msg: str, lvl: str = "I") -> str:
    t = dt.datetime.fromtimestamp(epoch)
    return f"{t:%m-%d %H:%M:%S}.{t.microsecond // 1000:03d} {pid:5d} {pid + 1:5d} {lvl} {tag}: {msg}"


def five(frames=10, chk_t=4, chk_f=6, pre_t=4, pre_f=0):
    return (f"{frames} frames in 5.0 seconds = {frames / 5:.1f} FPS, 1/1 present copies offloaded to GPU, "
            f"2/2 EXA copies offloaded (0 CPU rects), exa_solid_prepare=3 exa_solid_gpu=2 "
            f"exa_solid_fallback=1 solid_rects=2 cpu_solid_rects=0 renderer_solid_submits=2 "
            f"renderer_solid_complete=2 xrender_ops={chk_t + chk_f} exa_comp_check={chk_t}/{chk_f} "
            f"prepare={pre_t}/{pre_f} gpu_rects={pre_t} cpu_rects=0 done={pre_t}")


def sample(xpid=X, xalive=True, apid=ACT, xfd=88, afd=170, amaps=4000):
    return {"tag": "K", "x": {"pid": xpid, "alive": xalive, "fd_count": xfd, "maps_count": 3000},
            "activity": {"pid": apid, "alive": apid is not None, "fd_count": afd,
                         "maps_count": amaps}, "gatea": {}}


def stat(pid, ticks):
    return f"{pid} (comm with ) paren) S 1 1 0 0 -1 0 0 0 0 0 {ticks} {ticks // 2} 0 0"


class Run:
    """A synthetic, CLEAN run directory. Tests mutate it to make it fail."""

    def __init__(self, root: Path):
        self.d = root
        d = root
        d.mkdir(parents=True, exist_ok=True)
        (d / "x3-pid.txt").write_text(f"x3_pid={X}\n")
        (d / "activity-pid.txt").write_text(f"activity_pid={ACT}\n")
        (d / "stable-pid.txt").write_text(f"stable_pid={STABLE}\n")
        (d / "x3-tracer.txt").write_text("PPid:\t8415\nTracerPid:\t0\n")
        self.jw("screen-pre.json", {"awake": True, "keyguard": False})
        self.jw("screen-post.json", {"awake": True, "keyguard": False})
        self.jw("run-binding.json", {"source_sha": FREEZE["binding"]["source_sha"],
                                     "apk_sha256": FREEZE["binding"]["apk_sha256"],
                                     "variant": "C1"})
        self.jw("session-ready.json", {"ready": True, "waited_s": 12.3})
        self.jw("logout.json", {"ok": True, "waited_s": 3.1, "leftovers": []})
        self.jw("close.json", {"x_alive_after_terminate": False})
        self.jw("capture-end.json", {"logcat_alive": True})
        cl = {"xfwm4": 100, "xfce4-panel": 101, "xfdesktop": 102}
        self.jw("xfce-clients-K1.json", cl)
        self.jw("xfce-clients-K2.json", dict(cl))
        for k in ("K0", "K1", "K2", "K3"):
            self.jw(f"k-{k}.json", sample())
        self.jw("k-K4.json", sample(xpid=None, xalive=False))
        for k, xt, at in (("K1", 1000, 500), ("K2", 4000, 2000)):
            self.jw(f"k-{k}-extra.json", {"x_stat": stat(X, xt), "act_stat": stat(ACT, at)})
        # steps: the frozen schedule, executed on time
        steps = [{"step": -1, "op": "T0", "t0_mono_ns": T0_MONO, "t0_epoch_s": T0_EPOCH,
                  "timescale": 1.0, "xdotool": "xdotool", "terminal": "xfce4-terminal", "ok": True}]
        for i, st in enumerate(FREEZE["p04_window_choreography"]["schedule"]):
            rec = {"step": i, "op": st["op"], "win": st.get("win"), "planned_t": st["t"],
                   "actual_t": st["t"] + 0.01, "lateness_s": 0.01, "ok": True}
            if st["op"] == "close":
                rec["rc"] = 0
            steps.append(rec)
        self.steps = steps
        self.write_steps()
        # logcat: events 0..N-1 (both roles), a lease pair, 5 s lines from X AND Stable
        self.events = []
        seq = 0
        for ev_id in (1, 1, 2, 3, 4, 5, 6, 10, 11, 14, 17, 22, 23, 29, 30):
            role = 2 if ev_id in (1, 7, 10, 11) else 1
            self.events.append((T0_EPOCH + 10 + seq * 0.01, X if role == 1 else ACT, seq, role, ev_id))
            seq += 1
        self.n = seq
        self.extra_lines: list[str] = []
        self.summary = CLEAN_SUMMARY.format(n=self.n)
        self.sf = [[(T0_MONO + i * 50_000_000, T0_MONO + i * 50_000_000 + 16_000_000,
                     T0_MONO + i * 50_000_000) for i in range(0, 100)]]
        self.write_logcat()

    def jw(self, name, obj):
        (self.d / name).write_text(json.dumps(obj))

    def write_steps(self):
        (self.d / "steps.jsonl").write_text("\n".join(json.dumps(s) for s in self.steps) + "\n")

    def write_logcat(self):
        L = [lc(T0_EPOCH - 5, ACT, "LorieNative", "GATEA_BIND version=1 nonce=1 generation=1 bound=1")]
        for ep, pid, seq, role, ev_id in self.events:
            L.append(lc(ep, pid, "gatea-telemetry",
                        f"GATEA_EVENT seq={seq} role={role} event={ev_id} generation=1 serial=0 src=1 dst=2"))
        for i in range(30):
            L.append(lc(T0_EPOCH + 5 * i + 1, X, "LorieNative", five()))
            L.append(lc(T0_EPOCH + 5 * i + 2, STABLE, "LorieNative", "400 frames in 5.0 seconds = 80.0 FPS"))
            # the adversarial Stable line: today's Stable APK prints the short form above,
            # but a Stable built from a newer tree prints the FULL form under the same tag.
            # Without the pid filter these would be summed into the experimental X.
            L.append(lc(T0_EPOCH + 5 * i + 3, STABLE, "LorieNative",
                        five(frames=500, chk_t=900, chk_f=900, pre_t=900, pre_f=900)))
        for i in range(12):
            # V2: the product prints no stamps; a Stable line that looks like one must
            # neither count as a composite nor trip diag_stamps_absent
            L.append(lc(T0_EPOCH + 20 + i, STABLE, "LorieNative", f"Probe ENTER depth=1 max=1 enter={i} return={i}"))
        L.extend(self.extra_lines)
        L.append(lc(T0_EPOCH + 200, X, "gatea-a1", self.summary))
        (self.d / "raw-logcat.txt").write_text("\n".join(L) + "\n")
        (self.d / "gatea-summary.txt").write_text(self.summary + "\n")
        polls = []
        for rows in self.sf:
            polls.append("=== poll 1\n8333333\n" + "\n".join(f"{a}\t{b}\t{c}" for a, b, c in rows))
        (self.d / "sf-polls.txt").write_text("\n".join(polls) + "\n")

    def judge(self):
        run = COL.collect(self.d, FREEZE)
        return run, JDG.judge(run, FREEZE)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="xfce-test-"))
        self.r = Run(self.tmp / "run")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class Clean(Base):
    def test_clean_is_valid(self):
        run, v = self.r.judge()
        self.assertEqual(v["verdict"], "BASELINE_VALID", v["failed"])
        self.assertTrue(run["events_complete"])
        self.assertEqual(v["classification"], "BASELINE")

    def test_x_pid_filter_excludes_stable(self):
        run, _ = self.r.judge()
        # 30 X lines x 10 frames inside the window; Stable's full-form 500-frame lines
        # and its Probe ENTER lines must NOT be in the X numbers
        self.assertEqual(run["xrender_5s"]["frames"], 300)
        self.assertEqual(run["xrender_5s"]["prepare_false"], 0)
        self.assertEqual(run["composite_total"], 300)      # 30 lines x xrender_ops 10
        self.assertEqual(run["diag_stamp_lines"], 0)
        self.assertTrue(run["stable_background_frames_5s"])
        self.assertTrue(all(f in (400, 500) for f in run["stable_background_frames_5s"]))

    def test_registry_index_is_c18_c19_not_c25_c26(self):
        # the clean summary carries c25=c26=40 (UNREGISTER / RESOURCE_DESTROY); reading
        # those as registry-current would fabricate a 40-entry leak on a clean close
        run, v = self.r.judge()
        self.assertEqual(run["summary"]["c25"], 40)
        self.assertEqual(v["verdict"], "BASELINE_VALID")

    def test_cpu_ticks_parse_after_last_paren(self):
        run, _ = self.r.judge()
        self.assertAlmostEqual(run["x_cpu_s"], (4000 + 2000 - 1000 - 500) / 100.0)

    def test_d0a_staged_from_counters(self):
        run, _ = self.r.judge()
        # prepare_true 30 x 4 = 120 in the window, one event=5 in the window
        self.assertEqual(run["gatea_direct"], 1)
        self.assertEqual(run["d0a_staged"], 119)

    def test_sf_metrics(self):
        run, _ = self.r.judge()
        self.assertTrue(run["sf"]["sf_coverage_complete"])
        self.assertEqual(run["sf"]["frames_sf"], 100)
        self.assertAlmostEqual(run["sf"]["present_lag_ms_p50"], 16.0)
        self.assertEqual(run["sf"]["jank_frames"], 0)


class MustGoRed(Base):
    """Each of these is a real way a run can be bad. Each MUST change the verdict."""

    def test_traced_x_is_invalid(self):
        # RCA-XFCE-2: a traced X is not the product topology
        (self.r.d / "x3-tracer.txt").write_text("PPid:\t20859\nTracerPid:\t20859\n")
        _, v = self.r.judge()
        self.assertEqual(v["verdict"], "INVALID")
        self.assertIn("x3_untraced", v["failed"]["validity"])

    def test_screen_off_mid_run_is_invalid(self):
        self.r.jw("screen-post.json", {"awake": False, "keyguard": True})
        _, v = self.r.judge()
        self.assertEqual(v["verdict"], "INVALID")
        self.assertIn("screen_awake_throughout", v["failed"]["validity"])

    def test_x_pid_stamp_is_invalid(self):
        # a stamp from X means TERMUX_X11_P2A_DIAG was on: the diagnostic build, not the product
        self.r.extra_lines.append(lc(T0_EPOCH + 40, X, "LorieNative", "Sprep pix=0x1 index=0"))
        self.r.write_logcat()
        _, v = self.r.judge()
        self.assertEqual(v["verdict"], "INVALID")
        self.assertIn("diag_stamps_absent", v["failed"]["validity"])

    def test_terminal_never_exited_is_defect(self):
        # the xfce-c1-03 shape: rc None
        for s in self.r.steps:
            if s.get("op") == "close":
                s["rc"] = None
                s["ok"] = False
                s["why"] = "close_exit_timeout"
        self.r.write_steps()
        _, v = self.r.judge()
        self.assertEqual(v["verdict"], "BASELINE_DEFECT")
        self.assertIn("terminals_exited", v["failed"]["crash"])
        self.assertNotIn("terminals_exit_code_0", v["failed"]["crash"])

    def test_counter_imbalance_is_defect(self):
        self.r.summary = self.r.summary.replace("c13=40", "c13=39")
        self.r.write_logcat()
        _, v = self.r.judge()
        self.assertEqual(v["verdict"], "BASELINE_DEFECT")
        self.assertIn("ahb_balanced_c12_c13", v["failed"]["hard"])

    def test_registry_current_nonzero_is_defect(self):
        self.r.summary = self.r.summary.replace("c18=0", "c18=2")
        self.r.write_logcat()
        _, v = self.r.judge()
        self.assertEqual(v["verdict"], "BASELINE_DEFECT")

    def test_fatal_from_x_pid_is_defect(self):
        self.r.extra_lines.append(lc(T0_EPOCH + 50, X, "libc", "Fatal signal 11 (SIGSEGV)", "F"))
        self.r.write_logcat()
        _, v = self.r.judge()
        self.assertEqual(v["verdict"], "BASELINE_DEFECT")
        self.assertIn("no_fatal_lines", v["failed"]["crash"])

    def test_tombstone_naming_our_pid_is_defect(self):
        # crash_dump logs under its OWN pid; the line names ours
        self.r.extra_lines.append(lc(T0_EPOCH + 50, 31337, "DEBUG",
                                     f"pid: {ACT}, tid: {ACT}, name: renderer  >>> x <<<", "F"))
        self.r.write_logcat()
        _, v = self.r.judge()
        self.assertEqual(v["verdict"], "BASELINE_DEFECT")

    def test_fatal_from_stable_is_ignored(self):
        # the control for the two cases above: the same line from Stable's pid is not ours
        self.r.extra_lines.append(lc(T0_EPOCH + 50, STABLE, "libc", "Fatal signal 11 (SIGSEGV)", "F"))
        self.r.write_logcat()
        _, v = self.r.judge()
        self.assertEqual(v["verdict"], "BASELINE_VALID")

    def test_xfwm4_restart_is_defect(self):
        self.r.jw("xfce-clients-K2.json", {"xfwm4": 999, "xfce4-panel": 101, "xfdesktop": 102})
        _, v = self.r.judge()
        self.assertEqual(v["verdict"], "BASELINE_DEFECT")

    def test_activity_pid_change_is_defect(self):
        self.r.jw("k-K2.json", sample(apid=ACT + 1))
        _, v = self.r.judge()
        self.assertEqual(v["verdict"], "BASELINE_DEFECT")

    def test_terminal_nonzero_exit_is_defect(self):
        for s in self.r.steps:
            if s.get("op") == "close":
                s["rc"] = 1
                s["ok"] = False
                break
        self.r.write_steps()
        _, v = self.r.judge()
        self.assertEqual(v["verdict"], "BASELINE_DEFECT")   # crash precedence over INVALID
        self.assertIn("terminals_exit_code_0", v["failed"]["crash"])
        self.assertNotIn("terminals_exited", v["failed"]["crash"])

    def test_late_step_is_invalid(self):
        self.r.steps[10]["lateness_s"] = 2.5
        self.r.write_steps()
        _, v = self.r.judge()
        self.assertEqual(v["verdict"], "INVALID")
        self.assertIn("choreo_no_step_late", v["failed"]["validity"])

    def test_test_mode_timescale_is_invalid(self):
        self.r.steps[0]["timescale"] = 0.01
        self.r.write_steps()
        _, v = self.r.judge()
        self.assertEqual(v["verdict"], "INVALID")

    def test_missing_step_is_invalid(self):
        del self.r.steps[5]
        self.r.write_steps()
        _, v = self.r.judge()
        self.assertEqual(v["verdict"], "INVALID")

    def test_reordered_step_is_invalid(self):
        self.r.steps[4], self.r.steps[5] = self.r.steps[5], self.r.steps[4]
        self.r.write_steps()
        _, v = self.r.judge()
        self.assertEqual(v["verdict"], "INVALID")

    def test_summary_file_missing_but_in_logcat_is_valid(self):
        # the control: a failed file copy must not read as a crash
        (self.r.d / "gatea-summary.txt").write_text("")
        run, v = self.r.judge()
        self.assertEqual(run["summary_source"], "logcat")
        self.assertEqual(v["verdict"], "BASELINE_VALID")

    def test_summary_missing_everywhere_logcat_alive_is_defect(self):
        # X is gone, the capture was alive the whole time, and no dump was ever written:
        # X did not reach CloseScreen
        self.r.summary = "no summary here"
        self.r.write_logcat()
        _, v = self.r.judge()
        self.assertEqual(v["verdict"], "BASELINE_DEFECT")
        self.assertIn("x_ends_only_by_terminate", v["failed"]["crash"])

    def test_summary_missing_logcat_dead_is_invalid(self):
        self.r.summary = "no summary here"
        self.r.write_logcat()
        self.r.jw("capture-end.json", {"logcat_alive": False})
        _, v = self.r.judge()
        self.assertEqual(v["verdict"], "INVALID")

    def test_logout_timeout_is_invalid(self):
        self.r.jw("logout.json", {"ok": False, "waited_s": 45, "leftovers": [1234]})
        _, v = self.r.judge()
        self.assertEqual(v["verdict"], "INVALID")

    def test_session_not_ready_is_invalid(self):
        self.r.jw("session-ready.json", {"ready": False, "waited_s": 90})
        _, v = self.r.judge()
        self.assertEqual(v["verdict"], "INVALID")

    def test_forbidden_event_is_defect(self):
        self.r.events.append((T0_EPOCH + 60, X, self.r.n, 1, 12))   # FENCE_TIMEOUT
        self.r.n += 1
        self.r.summary = CLEAN_SUMMARY.format(n=self.r.n)
        self.r.write_logcat()
        _, v = self.r.judge()
        self.assertEqual(v["verdict"], "BASELINE_DEFECT")
        self.assertIn("no_forbidden_events", v["failed"]["hard"])

    def test_lease_imbalance_is_defect(self):
        self.r.events.append((T0_EPOCH + 60, X, self.r.n, 1, 2))    # extra RESERVED
        self.r.n += 1
        self.r.summary = CLEAN_SUMMARY.format(n=self.r.n)
        self.r.write_logcat()
        _, v = self.r.judge()
        self.assertEqual(v["verdict"], "BASELINE_DEFECT")

    def test_generation_2_is_defect(self):
        ep, pid, seq, role, ev_id = self.r.events[3]
        self.r.events[3] = (ep, pid, seq, role, ev_id)
        self.r.extra_lines.append(lc(T0_EPOCH + 70, X, "gatea-telemetry",
                                     f"GATEA_EVENT seq={self.r.n} role=1 event=30 generation=2 "
                                     "serial=0 src=1 dst=0"))
        self.r.n += 1
        self.r.summary = CLEAN_SUMMARY.format(n=self.r.n)
        self.r.write_logcat()
        _, v = self.r.judge()
        self.assertIn("generation_is_1", v["failed"]["hard"])

    def test_second_bind_is_defect(self):
        self.r.extra_lines.append(lc(T0_EPOCH + 70, ACT, "LorieNative",
                                     "GATEA_BIND version=1 nonce=2 generation=1 bound=1"))
        self.r.write_logcat()
        _, v = self.r.judge()
        self.assertIn("exactly_one_gatea_bind", v["failed"]["hard"])


class Incomplete(Base):
    def test_seq_gap_nulls_event_metrics_but_counters_still_judge(self):
        del self.r.events[4]        # a dropped logcat line
        self.r.write_logcat()
        run, v = self.r.judge()
        self.assertFalse(run["events_complete"])
        self.assertIsNone(run["gatea_direct"])
        self.assertIsNone(run["d0a_staged"])
        self.assertIsNone(run["gatea_hit_rate"])
        self.assertEqual(v["verdict"], "BASELINE_VALID")
        self.assertIn("lease_reserved_eq_released", v["unjudged"]["hard"])

    def test_seq_gap_with_counter_imbalance_still_defect(self):
        # an incomplete event stream must not hide a counter failure
        del self.r.events[4]
        self.r.summary = self.r.summary.replace("c20=0", "c20=1")
        self.r.write_logcat()
        _, v = self.r.judge()
        self.assertEqual(v["verdict"], "BASELINE_DEFECT")

    def test_sf_gap_nulls_frame_metrics(self):
        first = [(T0_MONO + i * 1_000_000, T0_MONO + i * 1_000_000 + 16_000_000, T0_MONO)
                 for i in range(127)]
        later = [(T0_MONO + 10**10 + i * 1_000_000, T0_MONO + 10**10 + i * 1_000_000 + 16_000_000,
                  T0_MONO) for i in range(127)]
        self.r.sf = [first, later]      # second poll FULL and entirely newer: frames lost
        self.r.write_logcat()
        run, v = self.r.judge()
        self.assertFalse(run["sf"]["sf_coverage_complete"])
        self.assertIsNone(run["sf"]["frames_sf"])
        self.assertIsNone(run["sf"]["present_lag_ms_p99"])

    def test_sf_overlap_is_complete(self):
        # control for the case above: overlapping polls must NOT be flagged
        first = [(T0_MONO + i * 1_000_000, T0_MONO + i * 1_000_000 + 16_000_000, T0_MONO)
                 for i in range(127)]
        later = [(a + 50_000_000, b + 50_000_000, c) for a, b, c in first]
        self.r.sf = [first, later]
        self.r.write_logcat()
        run, _ = self.r.judge()
        self.assertTrue(run["sf"]["sf_coverage_complete"])

    def test_unmeasured_is_null_not_zero(self):
        (self.r.d / "gpubusy-polls.txt").unlink(missing_ok=True)
        run, _ = self.r.judge()
        self.assertIsNone(run["gpu_busy_mean"])


class Soft(Base):
    def test_fd_growth_is_finding_not_class_change(self):
        self.r.jw("k-K3.json", sample(xfd=95))
        _, v = self.r.judge()
        self.assertEqual(v["verdict"], "BASELINE_VALID")
        self.assertTrue(any("x.fd_count" in f for f in v["findings"]))


def _reference_rows(text: str, year: int):
    """The pre-streaming parse_logcat body (whole text in memory) - the oracle the
    streaming reader must reproduce row for row."""
    rows = []
    for line in text.splitlines():
        m = COL.LOGCAT_RE.match(line)
        if not m:
            continue
        mo, d, h, mi, s, ms, pid, tid, lvl, tag, msg = m.groups()
        t = dt.datetime(year, int(mo), int(d), int(h), int(mi), int(s), int(ms) * 1000)
        rows.append({"epoch": t.timestamp(), "pid": int(pid), "tid": int(tid),
                     "lvl": lvl, "tag": tag, "msg": msg})
    return rows


class Streaming(unittest.TestCase):
    """INCIDENT-20260923: the collector must stream. Streaming must not change a row."""

    def test_iter_logcat_matches_whole_text_parse(self):
        import random
        rnd = random.Random(20260923)
        seps = ["\n", "\r\n", "\r", "\x0c", "\x1c", "\x85", " ", "\x0b"]
        parts = []
        for i in range(4000):
            ms = rnd.randint(0, 999)
            parts.append(f"09-23 {rnd.randint(0, 23):02d}:{rnd.randint(0, 59):02d}:"
                         f"{rnd.randint(0, 59):02d}.{ms:03d}  {rnd.randint(1, 32000)}  "
                         f"{rnd.randint(1, 32000)} I tag{i % 7}: m{i} "
                         + ("x" * rnd.randint(0, 3000)))
            parts.append(rnd.choice(seps) if i % 11 == 0 else "\n")
            if i % 97 == 0:
                parts.append("garbage \udcff line\n")
        text = "".join(parts)
        raw = text.encode("utf-8", errors="surrogateescape") + b"\xff\xfe tail"
        p = Path(tempfile.mkdtemp(prefix="xfce-stream-")) / "raw-logcat.txt"
        try:
            p.write_bytes(raw)
            want = _reference_rows(raw.decode("utf-8", errors="replace"), 2026)
            f = COL.open_logcat(p)
            with f:
                got = list(COL.iter_logcat(f, 2026))
            self.assertGreater(len(want), 3000)
            self.assertEqual(len(got), len(want))
            self.assertEqual(got, want)
        finally:
            shutil.rmtree(p.parent, ignore_errors=True)

    def test_seqset_matches_set_semantics(self):
        import random
        rnd = random.Random(7)
        for trial in range(3000):
            n = rnd.randint(0, 40)
            seqs = list(range(n))
            rnd.shuffle(seqs)
            k = rnd.randint(0, 3)
            if k == 1 and seqs:
                seqs[rnd.randrange(len(seqs))] = rnd.randint(0, 60)     # gap + dup or out of range
            elif k == 2 and seqs:
                seqs.pop()                                              # missing
            elif k == 3:
                seqs.append(rnd.choice(seqs) if seqs else 0)            # dup
            s = COL.SeqSet()
            for q in seqs:
                s.add(q)
            for nxt in (None, n, n - 1, n + 1, len(seqs)):
                want = nxt is not None and len(seqs) == nxt and set(seqs) == set(range(nxt))
                self.assertEqual(s.complete(nxt), want, (seqs, nxt))
            self.assertEqual(s.count - s.distinct, len(seqs) - len(set(seqs)))


class Choreo(unittest.TestCase):
    """The driver's bookkeeping, against fake xdotool / terminal binaries."""

    def test_schedule_runs_and_logs_every_step(self):
        tmp = Path(tempfile.mkdtemp(prefix="xfce-choreo-"))
        try:
            fake = tmp / "bin"
            fake.mkdir()
            (fake / "xdotool").write_text(
                "#!/bin/bash\n"
                "if [ \"$1\" = search ]; then\n"
                "  t=${@: -1}; t=${t#^}; t=${t%$}\n"
                "  [ -e \"$XS/open-$t\" ] && echo 4242 && exit 0; exit 1\n"
                "fi\nexit 0\n")
            (fake / "term").write_text(
                "#!/bin/bash\n"
                "for a in \"$@\"; do case $a in --title=*) t=${a#--title=};; esac; done\n"
                "touch \"$XS/open-$t\"\n"
                "shift $(( $# - 2 )); n=$1; sd=$2\n"
                "while [ ! -e \"$sd/close-T$n\" ]; do sleep 0.02; done\n"
                "rm -f \"$XS/open-$t\"; exit 0\n")
            for f in fake.iterdir():
                f.chmod(0o755)
            env = dict(os.environ, XS=str(tmp), XFCE_CHOREO_TEST_XDOTOOL=str(fake / "xdotool"),
                       XFCE_CHOREO_TEST_TERMINAL=str(fake / "term"),
                       XFCE_CHOREO_TEST_TIMESCALE="0.02")
            log = tmp / "steps.jsonl"
            r = subprocess.run([sys.executable, str(HERE / "choreo.py"), "--freeze",
                                str(HERE / "xfce-design-freeze.json"), "--state-dir",
                                str(tmp / "st"), "--log", str(log), "--term-load",
                                str(HERE / "term_load.sh")], env=env, timeout=120,
                               capture_output=True, text=True)
            recs = [json.loads(x) for x in log.read_text().splitlines()]
            self.assertEqual(r.returncode, 0, r.stderr + json.dumps([x for x in recs if not x.get("ok")]))
            self.assertEqual(len(recs), 1 + len(FREEZE["p04_window_choreography"]["schedule"]))
            self.assertEqual(recs[0]["timescale"], 0.02)
            # and the judge refuses exactly this log, because it ran in test mode
            run = {"t0": recs[0], "steps": recs}
            v = JDG.judge(run, FREEZE)
            self.assertIn("choreo_not_test_mode", v["failed"]["validity"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=1)
