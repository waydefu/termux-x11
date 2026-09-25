#!/usr/bin/env python3
"""Unit tests for xfce_v6_judge.py (XFCE-V6-BASELINE-01 tool T6). Every case calls the real judge.
Freeze section 3 lists the minimum cases; each one is marked [T6] below.
    python3 test_xfce_v6_judge.py -v
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import xfce_v6_judge as J

ROOT = Path("/root/projects/GPU加速")
QUAL = ROOT / "evidence/session/xfce-v6/tool-qualification"
HIST = ROOT / "evidence/session/gate-a-a1/p2-xfce-runtime/runtime-f592241/xfce-c0-gt-01"
TRACER = 15010
T0 = 1_790_000_000.0


def tracer_json(pid=TRACER, sha=J.V6_SHA, env=None):
    return {"pid": 1, "tracer_pid": pid, "tracer_comm": "proot-fast6", "tracer_exe": "/x",
            "tracer_sha256": sha, "proot_env": {"PROOT_L2S_DIR": "/l2s"} if env is None else env}


def rtt_lines(tracer, t0, n, ms, spikes=()):
    out = [f"TRACER {tracer}"]
    for i in range(n):
        v = ms[i % len(ms)] if isinstance(ms, list) else ms
        out.append(f"RTT {t0 + 0.25 * i:.3f} {150.0 if i in spikes else v:.3f}")
    return "\n".join(out) + "\n"


def w(p: Path, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text if isinstance(text, str) else json.dumps(text))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="xfce-v6-judge-"))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def make_b(self, name, group, p50=0.5, x3=0.35, shared=None, spikes=(), **over):
        cap = self.tmp / name
        w(cap / "runner-exit.txt", "rc=0\n")
        w(cap / "client-tracer.json", over.get("client", tracer_json()))
        w(cap / "xfce-session-tracer.json", over.get("session", tracer_json()))
        w(cap / "touch.json", over.get("touch", {"selftest": True, "events": 0}))
        for n in ("screen-pre.json", "screen-post.json"):
            w(cap / n, {"awake": True, "keyguard": False})
        w(cap / "stable-before.json", {"pid": 4242})
        w(cap / "stable-after.json", {"pid": over.get("stable_after", 4242)})
        env = {"C": "TERMUX_X11_DISABLE_EXA_GPU=1\n", "GT": "TERMUX_X11_GPU_MIN_PIXELS=4097\n",
               "G0": "TERMUX_X11_GPU_MIN_PIXELS=0\n"}[group]
        w(cap / "env-x3.txt", "TERMUX_X11_GATEA_PROTO=1\n" + env)
        n_shared = shared if shared is not None else {"C": 3, "GT": 54, "G0": 2006}[group]
        marker = "E lorie: GPU min pixels 4097 (smaller pixmaps are never promoted)\n" if group != "C" else ""
        w(cap / "raw-logcat.txt", marker + "I LorieNative: Sent shared buffer\n" * n_shared)
        w(cap / "x-root.txt", over.get("root", "x_root=1200x2464 expect=1200x2464\n"))
        w(cap / "steps.jsonl", json.dumps({"t0_epoch_s": T0}) + "\n")
        rca = Path(str(cap) + ".rca")
        w(rca / "rtt.txt", rtt_lines(over.get("traced_tracer", TRACER), T0, 600, p50))
        w(rca / "summary.json", {"x3": 1, "samples": 1, "threads": {}})
        w(rca / "search.jsonl", json.dumps({"epoch": T0 + 1, "dur_s": 0.1, "rc": 0, "found": 1}) + "\n")
        w(rca / "cpu.jsonl", "".join(json.dumps({"epoch": T0 + s, "run_id": "r", "ticks": {
            "9:X3": int(x3 * 100 * s), "8:proot-tracer": int(0.01 * 100 * s)}}) + "\n" for s in (0, 5, 145)))
        v6 = Path(str(cap) + ".v6")
        w(v6 / "untraced-rtt.txt", over.get("untraced", rtt_lines(0, T0, over.get("n", 600), p50, spikes)))
        w(v6 / "traced-lat.txt", "".join(f"TL {T0 + 0.1 * i:.3f} 50.0 0.5\n" for i in range(1500)))
        w(v6 / "tools-v6.json", over.get("tools", {k: J.TOOLS[k] for k in ("x_rtt2.glibc", "x_rtt2.bionic",
                                                                          "traced_lat.glibc")}))
        return cap


# ---------------------------------------------------------------- one capture --
class CaptureB(Base):
    def test_good_capture_is_valid(self):
        r = J.capture_b(self.make_b("gt", "GT"), "GT")
        self.assertTrue(r["valid"], r["why"])
        self.assertEqual(r["metrics"]["untraced"]["n"], 600)
        self.assertEqual(r["metrics"]["x3_cores"], 0.35)

    def test_wrong_tracer_sha_invalid(self):  # [T6]
        r = J.capture_b(self.make_b("c", "C", client=tracer_json(sha=J.STOCK_SHA)), "C")
        self.assertFalse(r["valid"])
        self.assertTrue(any("tracer sha256" in x for x in r["why"]), r["why"])

    def test_off_switches_invalid(self):  # [T6]
        for k, v in J.OFF_SWITCHES.items():
            with self.subTest(k=k):
                r = J.capture_b(self.make_b("c-" + k, "C", client=tracer_json(env={k: v})), "C")
                self.assertFalse(r["valid"])
                self.assertTrue(any(f"{k}={v}" in x for x in r["why"]), r["why"])

    def test_untraced_probe_traced_invalid(self):  # [T6] TRACER 1234
        r = J.capture_b(self.make_b("c", "C", untraced=rtt_lines(1234, T0, 600, 0.5)), "C")
        self.assertFalse(r["valid"])
        self.assertTrue(any("untraced probe: TRACER [1234]" in x for x in r["why"]), r["why"])

    def test_traced_probe_other_tracer_invalid(self):
        r = J.capture_b(self.make_b("c", "C", traced_tracer=999), "C")
        self.assertFalse(r["valid"])

    def test_session_under_other_tracer_invalid(self):
        r = J.capture_b(self.make_b("c", "C", session=tracer_json(pid=777)), "C")
        self.assertFalse(r["valid"])

    def test_c_with_gpu_path_invalid(self):  # [T6] C shared 54
        r = J.capture_b(self.make_b("c", "C", shared=54), "C")
        self.assertFalse(r["valid"])
        self.assertTrue(any("shared buffers 54" in x for x in r["why"]), r["why"])

    def test_gt_without_routing_invalid(self):  # [T6] GT shared 2006
        r = J.capture_b(self.make_b("gt", "GT", shared=2006), "GT")
        self.assertFalse(r["valid"])

    def test_gt_without_marker_invalid(self):
        cap = self.make_b("gt", "GT")
        (cap / "raw-logcat.txt").write_text("Sent shared buffer\n" * 54)
        self.assertFalse(J.capture_b(cap, "GT")["valid"])

    def test_env_group_mismatch_invalid(self):
        cap = self.make_b("c", "C")
        (cap / "env-x3.txt").write_text("TERMUX_X11_GATEA_PROTO=1\n")
        self.assertFalse(J.capture_b(cap, "C")["valid"])

    def test_untraced_n_below_500_invalid(self):  # [T6]
        r = J.capture_b(self.make_b("c", "C", n=499), "C")
        self.assertFalse(r["valid"])
        self.assertTrue(any("untraced n 499" in x for x in r["why"]), r["why"])

    def test_touch_invalid_and_null_is_not_zero(self):  # [T6]
        for t in ({"selftest": True, "events": 3}, {"selftest": True, "events": None},
                  {"selftest": False, "events": 0}):
            with self.subTest(t=t):
                self.assertFalse(J.capture_b(self.make_b("c", "C", touch=t), "C")["valid"])

    def test_touch_null_reason_is_recorder_died(self):
        r = J.capture_b(self.make_b("c", "C", touch={"selftest": True, "events": None}), "C")
        self.assertIn("touch events null (recorder died)", r["why"])

    def test_missing_files_fail_closed(self):
        for f in ("client-tracer.json", "touch.json", "steps.jsonl", "raw-logcat.txt", "runner-exit.txt"):
            with self.subTest(f=f):
                cap = self.make_b("c-" + f, "C")
                (cap / f).unlink()
                self.assertFalse(J.capture_b(cap, "C")["valid"])

    def test_tool_hash_mismatch_invalid(self):
        tools = {k: J.TOOLS[k] for k in ("x_rtt2.glibc", "x_rtt2.bionic", "traced_lat.glibc")}
        tools["x_rtt2.bionic"] = "0" * 64
        self.assertFalse(J.capture_b(self.make_b("c", "C", tools=tools), "C")["valid"])

    def test_stable_pid_change_invalid(self):
        self.assertFalse(J.capture_b(self.make_b("c", "C", stable_after=1), "C")["valid"])

    def test_historical_f592241_capture_invalid(self):  # [T6] missing the new fields fails closed
        if not HIST.is_dir():
            self.skipTest("historical capture not present")
        r = J.capture_b(HIST, "GT")
        self.assertFalse(r["valid"])
        self.assertTrue(any("runner-exit" in x for x in r["why"]))
        self.assertTrue(any("client tracer: no tracer identity" in x for x in r["why"]), r["why"])


# ---------------------------------------------------------------- separation / Part B --
class PartB(Base):
    def series(self, p50s, x3s=None, overs=None, order=None, extra_invalid=()):
        order = order or J.B_ORDER + ["G0"]
        x3s = x3s or {}
        overs = overs or {}
        caps, counts = [], {}
        for i, g in enumerate(order):
            counts[g] = counts.get(g, 0) + 1
            k = counts[g]
            p50 = p50s.get((g, k), 0.5)
            cap = self.make_b(f"{i:02d}-{g}-{k}", g, p50=p50, x3=x3s.get((g, k), 0.35), **overs.get((g, k), {}))
            caps.append({"name": cap.name, "group": g, "dir": str(cap)})
        s = self.tmp / "series.json"
        s.write_text(json.dumps({"captures": caps}))
        return s

    def preflight_ok(self):
        return self.make_pre(self.tmp / "pre", grabs_from_qual=True)

    def make_pre(self, d: Path, grabs_from_qual=True, shift=0.0):
        """Preflight built from the REAL Xvfb qualification data (tool-qualification/t3-*)."""
        w(d / "runner-exit.txt", "rc=0\n")
        w(d / "client-tracer.json", tracer_json())
        w(d / "tools-v6.json", {k: J.TOOLS[k] for k in ("x_rtt2.glibc", "x_rtt2.bionic", "x_grab_stall.glibc")})
        shutil.copy(QUAL / "t3-untraced-rtt.log", d / "untraced-rtt.txt")
        shutil.copy(QUAL / "t3-traced-rtt.log", d / "traced-rtt.txt")
        g = [ln.split() for ln in (QUAL / "t3-grabs-1000ms.log").read_text().splitlines()][:3]
        w(d / "grabs.txt", "".join(f"GRAB {float(a) + shift:.3f} {float(b) + shift:.3f}\n" for _, a, b in g))
        return d

    def test_gt_faster(self):  # [T6] all three GT below all three C
        s = self.series({("GT", k): 0.2 for k in (1, 2, 3)} | {("C", k): 0.6 for k in (1, 2, 3)})
        r = J.part_b(self.preflight_ok(), s)
        self.assertEqual(r["verdict"], "JUDGED", r)
        self.assertEqual(r["B2_p50"], "GT_FASTER")

    def test_cpu_faster(self):  # [T6]
        s = self.series({("GT", k): 1.0 for k in (1, 2, 3)} | {("C", k): 0.4 for k in (1, 2, 3)})
        self.assertEqual(J.part_b(self.preflight_ok(), s)["B2_p50"], "CPU_FASTER")

    def test_interleaved_no_separation(self):  # [T6]
        s = self.series({("GT", 1): 0.3, ("GT", 2): 0.7, ("GT", 3): 0.5, ("C", 1): 0.4, ("C", 2): 0.6, ("C", 3): 0.45})
        self.assertEqual(J.part_b(self.preflight_ok(), s)["B2_p50"], "NO_SEPARATION")

    def test_medians_apart_but_overlapping_is_no_separation(self):
        # GT median 0.3 < C median 0.5, yet GT's 0.7 overlaps C: not complete separation
        s = self.series({("GT", 1): 0.2, ("GT", 2): 0.3, ("GT", 3): 0.7, ("C", 1): 0.4, ("C", 2): 0.5, ("C", 3): 0.6})
        self.assertEqual(J.part_b(self.preflight_ok(), s)["B2_p50"], "NO_SEPARATION")

    def test_x3_cpu_and_tail(self):
        s = self.series({}, x3s={("GT", k): 0.5 for k in (1, 2, 3)} | {("C", k): 0.34 for k in (1, 2, 3)},
                        overs={("GT", 2): {"spikes": (10, 20)}})
        r = J.part_b(self.preflight_ok(), s)
        self.assertEqual(r["B3"], "GT_MORE_X_CPU")
        self.assertEqual(r["B1"], "GT_TAIL_STALLS")
        self.assertEqual(r["B1_over_100ms"], [0, 2, 0])

    def test_too_few_valid_inconclusive(self):  # [T6]
        s = self.series({}, overs={("C", 2): {"touch": {"selftest": True, "events": 1}}})
        r = J.part_b(self.preflight_ok(), s)
        self.assertEqual(r["verdict"], "INCONCLUSIVE")

    def test_replacement_restores_count(self):
        order = J.B_ORDER + ["C", "G0"]
        s = self.series({}, overs={("C", 2): {"touch": {"selftest": True, "events": 1}}}, order=order)
        self.assertEqual(J.part_b(self.preflight_ok(), s)["verdict"], "JUDGED")

    def test_replacement_without_invalid_is_rejected(self):
        s = self.series({}, order=J.B_ORDER + ["GT", "G0"])
        self.assertEqual(J.part_b(self.preflight_ok(), s)["verdict"], "INCONCLUSIVE")

    def test_wrong_order_inconclusive(self):
        s = self.series({}, order=["C", "C", "GT", "GT", "C", "GT", "G0"])
        self.assertEqual(J.part_b(self.preflight_ok(), s)["verdict"], "INCONCLUSIVE")

    def test_x_root_change_inconclusive(self):
        s = self.series({}, overs={("GT", 3): {"root": "x_root=1200x2416 expect=1200x2416\n"}})
        self.assertEqual(J.part_b(self.preflight_ok(), s)["verdict"], "INCONCLUSIVE")

    def test_blocked_without_sensitive_probe(self):
        pre = self.make_pre(self.tmp / "pre-shifted", shift=-8.0)   # grabs moved onto quiet samples
        s = self.series({})
        self.assertEqual(J.part_b(pre, s)["verdict"], "BLOCKED_PROBE_INSENSITIVE")


# ---------------------------------------------------------------- preflight on real data --
class Preflight(PartB):
    def test_real_1000ms_grabs_sensitive(self):
        r = J.preflight(self.preflight_ok())
        self.assertEqual(r["verdict"], "PROBE_SENSITIVE", r)
        self.assertTrue(all(g["untraced_max_ms"] >= 500 and g["traced_max_ms"] >= 500 for g in r["grabs"]))

    def test_real_quiet_period_is_insensitive(self):  # the control that must go red
        r = J.preflight(self.make_pre(self.tmp / "q", shift=-8.0))
        self.assertEqual(r["verdict"], "PROBE_INSENSITIVE", r)

    def test_200ms_grabs_rejected_by_hold(self):   # deviation 1: the old parameters cannot qualify
        d = self.make_pre(self.tmp / "p200")
        shutil.copy(QUAL / "t3-grabs-200ms.log", d / "grabs.txt")
        lines = (d / "grabs.txt").read_text().splitlines()[:3]
        (d / "grabs.txt").write_text("\n".join(lines) + "\n")
        r = J.preflight(d)
        self.assertEqual(r["verdict"], "PREFLIGHT_INVALID")
        self.assertTrue(any("held" in x for x in r["why"]))

    def test_untraced_probe_run_inside_proot_invalid(self):
        d = self.make_pre(self.tmp / "tr")
        shutil.copy(QUAL / "t3-traced-rtt.log", d / "untraced-rtt.txt")   # TRACER 15010
        self.assertEqual(J.preflight(d)["verdict"], "PREFLIGHT_INVALID")


# ---------------------------------------------------------------- Part A --
class PartA(Base):
    def make_a(self, name, kind, s1=0, s2=0, s3=0, **over):
        d = self.tmp / name
        w(d / "sampler-exit.txt", "rc=0\n")
        sha = J.V6_SHA if kind == "v6" else J.STOCK_SHA
        w(d / "binding.json", {"kind": over.get("kind", kind), "client_tracer": over.get("client", tracer_json(sha=sha)),
                               "test_mode": over.get("test_mode", False)})
        w(d / "tools-v6.json", {k: J.TOOLS[k] for k in ("x_rtt2.glibc", "x_rtt2.bionic", "traced_lat.glibc")})
        w(d / "precheck.json", over.get("pre", {"claude_running": True, "cursor_running": False, "hermes_running": False, "electron_others": [], "mem_available_mb": 5100}))
        for n in ("screen-pre.json", "screen-post.json"):
            w(d / n, {"awake": True, "keyguard": False})
        w(d / "touch.json", {"selftest": True, "events": 0})
        w(d / "phases.json", {"baseline_start": T0 - 60, "launch_time": T0, "loaded_end": T0 + 240})
        n = 960
        w(d / "untraced-rtt.txt", rtt_lines(0, T0, n, 0.4, spikes=range(s3)))
        w(d / "traced-rtt.txt", rtt_lines(TRACER, T0, n, 0.5, spikes=range(s2)))
        w(d / "traced-lat.txt", "".join(f"TL {T0 + 0.1 * i:.3f} {200000.0 if i < s1 else 60.0} 0.6\n" for i in range(2400)))
        return d

    def test_gone(self):
        r = J.part_a(self.make_a("a", "v6"), self.make_a("b", "stock", s1=10, s2=2), self.make_a("c", "v6"))
        self.assertEqual((r["A0"], r["A1"], r["A2"]), ("CONTROL_REPRODUCES", "DAILY_STUTTER_GONE", "X_SERVER_NO_STALLS"))

    def test_reduced_and_remains(self):
        r = J.part_a(self.make_a("a", "v6", s1=2), self.make_a("b", "stock", s1=10), self.make_a("c", "v6"))
        self.assertEqual(r["A1"], "DAILY_STUTTER_REDUCED")
        r = J.part_a(self.make_a("d", "v6", s1=5), self.make_a("e", "stock", s1=10), self.make_a("f", "v6"))
        self.assertEqual(r["A1"], "DAILY_STUTTER_REMAINS")

    def test_control_must_reproduce(self):  # the control that must go red
        r = J.part_a(self.make_a("a", "v6"), self.make_a("b", "stock", s1=2), self.make_a("c", "v6"))
        self.assertEqual((r["A0"], r["verdict"]), ("WORKLOAD_DOES_NOT_REPRODUCE", "INCONCLUSIVE"))

    def test_x_server_stalls(self):
        r = J.part_a(self.make_a("a", "v6", s3=1), self.make_a("b", "stock", s1=9), self.make_a("c", "v6"))
        self.assertEqual(r["A2"], "X_SERVER_STALLS_PRESENT")

    def test_s3_does_not_count_in_e(self):
        r = J.part_a(self.make_a("a", "v6", s3=4), self.make_a("b", "stock", s1=9), self.make_a("c", "v6"))
        self.assertEqual(r["A1"], "DAILY_STUTTER_GONE")

    def test_replacement_rescues_one_invalid_slot(self):
        bad = self.make_a("a", "v6", pre={"claude_running": True, "cursor_running": True, "hermes_running": False,
                                           "electron_others": [], "mem_available_mb": 5100})
        r = J.part_a(bad, self.make_a("b", "stock", s1=9), self.make_a("c", "v6"), {"v6-1": self.make_a("a-r1", "v6")})
        self.assertEqual(r["verdict"], "JUDGED", r.get("why"))
        self.assertEqual(r["used"]["v6-1"], "replacement")

    def test_invalid_replacement_ends_part_a(self):
        t = {"selftest": True, "events": 2}
        bad = self.make_a("a", "v6", pre={"claude_running": False, "cursor_running": False, "hermes_running": False,
                                           "electron_others": [], "mem_available_mb": 5100})
        rep = self.make_a("a-r1", "v6", test_mode=True)
        r = J.part_a(bad, self.make_a("b", "stock", s1=9), self.make_a("c", "v6"), {"v6-1": rep})
        self.assertEqual(r["verdict"], "INCONCLUSIVE")

    def test_no_replacing_a_valid_capture(self):   # no cherry-picking
        r = J.part_a(self.make_a("a", "v6"), self.make_a("b", "stock", s1=9), self.make_a("c", "v6"),
                     {"v6-2": self.make_a("c-r1", "v6", s1=0)})
        self.assertEqual(r["verdict"], "INCONCLUSIVE")

    def test_stock_run_under_v6_invalid(self):
        r = J.part_a(self.make_a("a", "v6"), self.make_a("b", "stock", client=tracer_json(sha=J.V6_SHA)),
                     self.make_a("c", "v6"))
        self.assertEqual(r["verdict"], "INCONCLUSIVE")

    def test_apps_already_running_invalid(self):
        pre = {"claude_running": True, "cursor_running": True, "hermes_running": False, "electron_others": [], "mem_available_mb": 5100}
        r = J.part_a(self.make_a("a", "v6", pre=pre), self.make_a("b", "stock", s1=9), self.make_a("c", "v6"))
        self.assertEqual(r["verdict"], "INCONCLUSIVE")

    def test_claude_closed_or_other_electron_invalid(self):
        for pre in ({"claude_running": False, "cursor_running": False, "hermes_running": False, "electron_others": [], "mem_available_mb": 5100},
                    {"claude_running": True, "cursor_running": False, "hermes_running": False, "electron_others": ["chatgpt"], "mem_available_mb": 5100}):
            with self.subTest(pre=pre):
                self.assertFalse(J.capture_a(self.make_a("a", "v6", pre=pre), "v6")["valid"])

    def test_test_mode_capture_never_counts(self):
        r = J.capture_a(self.make_a("a", "v6", test_mode=True), "v6")
        self.assertFalse(r["valid"])
        self.assertTrue(any("test_mode" in x for x in r["why"]), r["why"])

    def test_low_memory_invalid(self):
        pre = {"claude_running": True, "cursor_running": False, "hermes_running": False, "electron_others": [], "mem_available_mb": 4000}
        self.assertFalse(J.capture_a(self.make_a("a", "v6", pre=pre), "v6")["valid"])


if __name__ == "__main__":
    unittest.main()
