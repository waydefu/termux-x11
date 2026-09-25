#!/usr/bin/env python3
"""Unit tests for notel_judge.py (XFCE-NOTEL-01). Every case calls the real judge; controls that must go red: [RED].
Captures are built with the Part B fixture of tests/xfce_v6/test_xfce_v6_judge.py (make_b), then given this
experiment's env and telemetry lines.
    python3 test_notel_judge.py -v
"""
from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "xfce_v6"))
import notel_judge as N  # noqa: E402
import test_xfce_v6_judge as TB  # noqa: E402
import xfce_v6_judge as J  # noqa: E402

V7 = TB.tracer_json(sha=N.V7_SHA)
ADB_LINE = ("09-26 01:00:00.000  3072  3072 I adbd    : adbd service requested 'shell,v2:exec logcat "
            "'gatea-telemetry:V' 'gatea-a1:V''\n")
TEL_LINE = "09-26 01:00:00.001 16159 16321 I gatea-telemetry: GATEA_EVENT seq=0 role=1 event=30\n"


class Base(TB.Base):
    make_pre = TB.PartB.make_pre      # the preflight built from the real Xvfb qualification data

    def make(self, name, group, tel_lines=None, env_extra=None, drop=(), **over):
        over.setdefault("client", V7)
        over.setdefault("session", V7)
        cap = self.make_b(name, "C" if group == "CT" else group, **over)
        env = {"TERMUX_X11_GATEA_PROTO": "1", "TERMUX_X11_R8_ARM": "1", "TERMUX_X11_R8_CASE": "R8-C1"}
        env.update({"C": {"TERMUX_X11_DISABLE_EXA_GPU": "1"}, "GT": {"TERMUX_X11_GPU_MIN_PIXELS": "4097"},
                    "CT": {"TERMUX_X11_DISABLE_EXA_GPU": "1", "TERMUX_X11_GATEA_TELEMETRY": "1"}}[group])
        env.update(env_extra or {})
        for k in drop:
            env.pop(k, None)
        (cap / "env-x3.txt").write_text("".join(f"{k}={v}\n" for k, v in sorted(env.items())))
        n = tel_lines if tel_lines is not None else (5000 if group == "CT" else 0)
        with open(cap / "raw-logcat.txt", "a") as f:
            f.write(ADB_LINE + TEL_LINE * n)
        return cap

    def pre(self):
        d = self.make_pre(self.tmp / "pre", grabs_from_qual=True)
        TB.w(d / "client-tracer.json", V7)
        return d

    def series(self, x3=None, p50=None, order=None, bad=(), extra=()):
        x3 = x3 or {}
        p50 = p50 or {}
        order = order or N.ORDER
        caps, k = [], {}
        for i, g in enumerate(list(order) + [g for g, _ in extra]):
            k[g] = k.get(g, 0) + 1
            over = {"touch": {"selftest": True, "events": 2}} if i in bad else {}
            cap = self.make(f"{i:02d}-{g}-{k[g]}", g, x3=x3.get((g, k[g]), 0.5), p50=p50.get((g, k[g]), 0.5), **over)
            caps.append({"name": cap.name, "group": g, "dir": str(cap)})
        s = self.tmp / "series.json"
        s.write_text(json.dumps({"captures": caps}))
        return N.series(self.pre(), s)


class Capture(Base):
    def test_good_groups_valid(self):
        for g in ("C", "GT", "CT"):
            with self.subTest(g=g):
                r = N.capture(self.make("a" + g, g), g)
                self.assertTrue(r["valid"], r["why"])

    def test_c_with_telemetry_lines_invalid(self):  # [RED] the switch did not take effect
        self.assertFalse(N.capture(self.make("a", "C", tel_lines=3), "C")["valid"])
        self.assertFalse(N.capture(self.make("b", "GT", tel_lines=1), "GT")["valid"])

    def test_ct_without_telemetry_lines_invalid(self):  # [RED]
        self.assertFalse(N.capture(self.make("a", "CT", tel_lines=0), "CT")["valid"])
        self.assertFalse(N.capture(self.make("b", "CT", tel_lines=999), "CT")["valid"])

    def test_adbd_command_line_is_not_telemetry(self):
        r = N.capture(self.make("a", "C", tel_lines=0), "C")
        self.assertEqual(r["metrics"]["telemetry_lines"], 0)
        self.assertTrue(r["valid"], r["why"])

    def test_env_must_match_group(self):
        self.assertFalse(N.capture(self.make("a", "C", env_extra={"TERMUX_X11_GATEA_TELEMETRY": "1"}), "C")["valid"])
        self.assertFalse(N.capture(self.make("b", "CT", drop=("TERMUX_X11_GATEA_TELEMETRY",)), "CT")["valid"])
        self.assertFalse(N.capture(self.make("c", "GT", drop=("TERMUX_X11_R8_ARM",)), "GT")["valid"])

    def test_v6_client_tracer_invalid(self):  # [RED] run before the daily switch to proot-fast7
        v6 = TB.tracer_json(sha=J.V6_SHA)
        self.assertFalse(N.capture(self.make("a", "C", client=v6, session=v6), "C")["valid"])

    def test_tracer_constant_restored(self):
        before = J.V6_SHA
        N.capture(self.make("a", "C"), "C")
        N.preflight(self.pre())
        self.assertEqual(J.V6_SHA, before)

    def test_ct_uses_c_path_rules(self):  # CT with the GPU path on is not CT
        self.assertFalse(N.capture(self.make("a", "CT", shared=54), "CT")["valid"])

    def test_touch_and_mem_guard_invalid(self):
        self.assertFalse(N.capture(self.make("a", "C", touch={"selftest": True, "events": 1}), "C")["valid"])
        cap = self.make("b", "GT")
        (cap / "MEM-GUARD-TRIPPED.txt").write_text("x")
        self.assertFalse(N.capture(cap, "GT")["valid"])

    def test_missing_logcat_fails_closed(self):
        cap = self.make("a", "C")
        (cap / "raw-logcat.txt").unlink()
        r = N.capture(cap, "C")
        self.assertFalse(r["valid"])
        self.assertIsNone(r["metrics"]["telemetry_lines"])


class Series(Base):
    def test_telemetry_costs_and_gt_less_cpu(self):
        x3 = {("C", i): 0.40 for i in (1, 2, 3)} | {("CT", i): 0.60 for i in (1, 2, 3)} | {("GT", i): 0.30 for i in (1, 2, 3)}
        r = self.series(x3=x3)
        self.assertEqual((r["verdict"], r["Q1"], r["N3"]), ("JUDGED", "TELEMETRY_COSTS_X_CPU", "GT_LESS_X_CPU"), r.get("why"))

    def test_interleaved_no_separation(self):  # [RED]
        x3 = {("C", 1): 0.4, ("C", 2): 0.6, ("C", 3): 0.5, ("CT", 1): 0.55, ("CT", 2): 0.45, ("CT", 3): 0.7}
        r = self.series(x3=x3)
        self.assertEqual(r["Q1"], "NO_SEPARATION")

    def test_gt_more_cpu_and_cpu_faster(self):
        x3 = {("C", i): 0.40 for i in (1, 2, 3)} | {("GT", i): 0.50 for i in (1, 2, 3)}
        p50 = {("C", i): 0.5 for i in (1, 2, 3)} | {("GT", i): 1.5 for i in (1, 2, 3)}
        r = self.series(x3=x3, p50=p50)
        self.assertEqual((r["N3"], r["N2_p50"]), ("GT_MORE_X_CPU", "CPU_FASTER"))

    def test_wrong_order_inconclusive(self):  # [RED]
        r = self.series(order=["C", "C", "C", "GT", "GT", "GT", "CT", "CT", "CT"])
        self.assertEqual(r["verdict"], "INCONCLUSIVE")

    def test_replacement_restores_q1(self):
        r = self.series(bad=(2,), extra=[("CT", None)])
        self.assertEqual(r["verdict"], "JUDGED", r.get("why"))

    def test_cherry_picked_replacement_rejected(self):  # [RED]
        r = self.series(extra=[("GT", None)])
        self.assertEqual(r["verdict"], "INCONCLUSIVE")

    def test_one_question_short_is_partial(self):
        r = self.series(bad=(2,))              # one CT INVALID, no replacement
        self.assertEqual((r["verdict"], r["Q1"]), ("PARTIAL", "INCONCLUSIVE"))
        self.assertIn("N3", r)

    def test_insensitive_preflight_blocks(self):  # [RED]
        x = self.tmp / "pre"
        pre = self.pre()
        (pre / "grabs.txt").write_text("GRAB 1.000 1.001\nGRAB 2.000 2.001\nGRAB 3.000 3.001\n")
        caps = [{"name": "c", "group": "C", "dir": str(self.make("c", "C"))}]
        s = self.tmp / "s.json"
        s.write_text(json.dumps({"captures": caps}))
        self.assertTrue(N.series(x, s)["verdict"].startswith("BLOCKED"))


if __name__ == "__main__":
    unittest.main()
