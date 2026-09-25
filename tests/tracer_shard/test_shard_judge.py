#!/usr/bin/env python3
"""Unit tests for shard_judge.py (TRACER-SHARD-01). Every case calls the real judge; the controls that must go
red are marked [RED].
    python3 test_shard_judge.py -v
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "xfce_v6"))
import shard_judge as SJ  # noqa: E402
import xfce_v6_judge as J  # noqa: E402

DAILY = 27459
T0 = 1_790_000_000.0
TOOLS = {"f8-shard": "a" * 64, "shard-run.sh": "b" * 64}


def tracer_json(pid=DAILY, sha=SJ.V7_SHA, env=None):
    return {"pid": 1, "tracer_pid": pid, "tracer_comm": "proot-fast7", "tracer_exe": "/x",
            "tracer_sha256": sha, "proot_env": {"PROOT_L2S_DIR": "/l2s"} if env is None else env}


def rtt_lines(tracer, t0, n, ms, spikes=()):
    out = [f"TRACER {tracer}"]
    for i in range(n):
        out.append(f"RTT {t0 + 0.25 * i:.3f} {150.0 if i in spikes else ms:.3f}")
    return "\n".join(out) + "\n"


def w(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj))


def apps(group, cursor_tp=None, hermes_tp=None, renderers=1, sha=SJ.V7_SHA, missing=()):
    out = {}
    for i, name in enumerate(("cursor", "hermes")):
        if name in missing:
            out[name] = {"main_pids": [], "main_pid": None}
            continue
        default = DAILY if group == "P" else 30000 + i
        tp = {"cursor": cursor_tp, "hermes": hermes_tp}[name] or default
        e = {"main_pids": [100 + i], "main_pid": 100 + i, "tracer_pid": tp, "tracer_sha256": sha, "renderers": renderers}
        if group == "S":
            e.update({"f8_shard_pid": 200 + i, "shard_tracer_pid": 30000 + i})
        out[name] = e
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="shard-judge-"))
        self._tools = dict(SJ.SHARD_TOOLS)
        SJ.SHARD_TOOLS.clear()
        SJ.SHARD_TOOLS.update(TOOLS)

    def tearDown(self):
        SJ.SHARD_TOOLS.clear()
        SJ.SHARD_TOOLS.update(self._tools)
        shutil.rmtree(self.tmp)

    def make(self, name, group, s1=0, s2=0, s3=0, **over):
        d = self.tmp / name
        w(d / "sampler-exit.txt", "rc=0\n")
        w(d / "binding.json", {"kind": over.get("kind", "v7"), "group": over.get("bgroup", group),
                               "client_tracer": over.get("client", tracer_json()), "test_mode": over.get("test_mode", False)})
        w(d / "tools-v6.json", {k: J.TOOLS[k] for k in ("x_rtt2.glibc", "x_rtt2.bionic", "traced_lat.glibc")})
        w(d / "tools-shard.json", over.get("tools", TOOLS))
        w(d / "precheck.json", over.get("pre", {"claude_running": True, "cursor_running": False, "hermes_running": False,
                                                "electron_others": [], "mem_available_mb": 5100}))
        for n in ("screen-pre.json", "screen-post.json"):
            w(d / n, {"awake": True, "keyguard": False})
        w(d / "touch.json", over.get("touch", {"selftest": True, "events": 0}))
        w(d / "phases.json", {"baseline_start": T0 - 60, "launch_time": T0, "loaded_end": T0 + 240})
        w(d / "apps-end.json", over.get("apps", apps(group)))
        n = 960
        w(d / "untraced-rtt.txt", rtt_lines(0, T0, n, 0.4, spikes=range(s3)))
        w(d / "traced-rtt.txt", rtt_lines(DAILY, T0, n, 0.5, spikes=range(s2)))
        w(d / "traced-lat.txt", "".join(f"TL {T0 + 0.1 * i:.3f} {200000.0 if i < s1 else 60.0} 0.6\n" for i in range(2400)))
        return d

    def series(self, es_p, es_s, order=SJ.ORDER, extra=(), bad=()):
        ip, is_ = iter(es_p), iter(es_s)
        caps = []
        for k, g in enumerate(order):
            e = next(ip) if g == "P" else next(is_)
            over = {"touch": {"selftest": True, "events": 3}} if k in bad else {}
            caps.append({"name": f"c{k}", "group": g, "dir": str(self.make(f"c{k}", g, s1=e, **over))})
        for k, (g, e) in enumerate(extra):
            caps.append({"name": f"r{k}", "group": g, "dir": str(self.make(f"r{k}", g, s1=e))})
        p = self.tmp / "series.json"
        w(p, {"captures": caps})
        return SJ.series(p)


class Capture(Base):
    def test_good_p_and_s_valid(self):
        for g in ("P", "S"):
            with self.subTest(g=g):
                r = SJ.capture(self.make("a" + g, g, s1=4, s2=1, s3=1), g)
                self.assertTrue(r["valid"], r["why"])
                self.assertEqual((r["metrics"]["S1"], r["metrics"]["S2"], r["metrics"]["S3"], r["metrics"]["E"]), (4, 1, 1, 5))

    def test_s_but_apps_on_daily_tracer_invalid(self):  # [RED] the shard did not happen
        r = SJ.capture(self.make("a", "S", apps=apps("S", cursor_tp=DAILY)), "S")
        self.assertFalse(r["valid"])
        self.assertTrue(any("daily tracer" in x for x in r["why"]), r["why"])

    def test_s_main_tracer_not_the_recorded_shard_invalid(self):
        r = SJ.capture(self.make("a", "S", apps=apps("S", hermes_tp=31234)), "S")
        self.assertFalse(r["valid"])

    def test_p_but_apps_sharded_invalid(self):  # [RED]
        r = SJ.capture(self.make("a", "P", apps=apps("P", cursor_tp=30000)), "P")
        self.assertFalse(r["valid"])

    def test_app_not_launched_invalid(self):  # [RED] "no stutter because nothing ran" must not pass
        for g in ("P", "S"):
            with self.subTest(g=g):
                self.assertFalse(SJ.capture(self.make("m" + g, g, apps=apps(g, missing=("hermes",))), g)["valid"])
                self.assertFalse(SJ.capture(self.make("r" + g, g, apps=apps(g, renderers=0)), g)["valid"])

    def test_apps_end_missing_fails_closed(self):
        d = self.make("a", "S")
        (d / "apps-end.json").unlink()
        self.assertFalse(SJ.capture(d, "S")["valid"])

    def test_v6_client_tracer_invalid(self):  # [RED] daily not switched to proot-fast7
        r = SJ.capture(self.make("a", "P", client=tracer_json(sha=J.V6_SHA)), "P")
        self.assertFalse(r["valid"])

    def test_shard_tracer_wrong_binary_invalid(self):
        r = SJ.capture(self.make("a", "S", apps=apps("S", sha=J.V6_SHA)), "S")
        self.assertFalse(r["valid"])

    def test_off_switch_invalid(self):
        r = SJ.capture(self.make("a", "P", client=tracer_json(env={"PROOT_KOMPAT_FULL": "1"})), "P")
        self.assertFalse(r["valid"])

    def test_shard_tools_mismatch_invalid(self):
        r = SJ.capture(self.make("a", "S", tools={"f8-shard": "c" * 64, "shard-run.sh": "b" * 64}), "S")
        self.assertFalse(r["valid"])

    def test_unfrozen_shard_tools_invalid(self):  # [RED] nothing counts before freeze section 11
        SJ.SHARD_TOOLS.clear()
        self.assertFalse(SJ.capture(self.make("a", "P"), "P")["valid"])

    def test_group_mismatch_invalid(self):
        self.assertFalse(SJ.capture(self.make("a", "S", bgroup="P"), "S")["valid"])

    def test_touch_and_null_touch_invalid(self):
        self.assertFalse(SJ.capture(self.make("a", "P", touch={"selftest": True, "events": 1}), "P")["valid"])
        self.assertFalse(SJ.capture(self.make("b", "P", touch={"selftest": True, "events": None}), "P")["valid"])

    def test_test_mode_never_counts(self):
        self.assertFalse(SJ.capture(self.make("a", "P", test_mode=True), "P")["valid"])

    def test_precheck_apps_running_invalid(self):
        pre = {"claude_running": True, "cursor_running": True, "hermes_running": False, "electron_others": [], "mem_available_mb": 5100}
        self.assertFalse(SJ.capture(self.make("a", "P", pre=pre), "P")["valid"])

    def test_part_a_capture_is_not_a_shard_capture(self):  # [RED] historical Part A data cannot be reused
        pa = Path("/root/projects/GPU加速/evidence/session/xfce-v6/part-a/v6-1")
        if pa.is_dir():
            self.assertFalse(SJ.capture(pa, "P")["valid"])


class Series(Base):
    def test_removes(self):
        r = self.series([20, 15, 30], [0, 0, 0])
        self.assertEqual((r["verdict"], r["T0"], r["T1"]), ("JUDGED", "CONTROL_REPRODUCES", "SHARD_REMOVES_STUTTER"))

    def test_reduces(self):
        r = self.series([20, 16, 30], [1, 4, 0])       # 4 <= 0.25 * 16
        self.assertEqual(r["T1"], "SHARD_REDUCES_STUTTER")

    def test_no_clear_effect(self):  # [RED]
        r = self.series([20, 16, 30], [1, 5, 0])       # 5 > 4
        self.assertEqual(r["T1"], "SHARD_NO_CLEAR_EFFECT")

    def test_control_must_reproduce(self):  # [RED]
        r = self.series([20, 2, 30], [0, 0, 0])
        self.assertEqual((r["T0"], r["verdict"]), ("WORKLOAD_DOES_NOT_REPRODUCE", "INCONCLUSIVE"))

    def test_x_server_stalls(self):
        caps = []
        for k, g in enumerate(SJ.ORDER):
            caps.append({"name": f"c{k}", "group": g, "dir": str(self.make(f"c{k}", g, s1=9 if g == "P" else 0,
                                                                           s3=1 if k == 2 else 0))})
        p = self.tmp / "s.json"
        w(p, {"captures": caps})
        self.assertEqual(SJ.series(p)["T2"], "X_SERVER_STALLS_PRESENT")

    def test_replacement_restores_count(self):
        r = self.series([20, 15, 30], [0, 0, 0], bad=(1,), extra=[("S", 0)])
        self.assertEqual(r["verdict"], "JUDGED", r.get("why"))

    def test_replacement_without_invalid_rejected(self):  # [RED] no cherry-picking
        r = self.series([20, 15, 30], [5, 5, 5], extra=[("S", 0)])
        self.assertEqual(r["verdict"], "INCONCLUSIVE")

    def test_too_many_replacements(self):
        r = self.series([20, 15, 30], [0, 0, 0], bad=(1, 2, 5), extra=[("S", 0), ("S", 0), ("S", 0)])
        self.assertEqual(r["verdict"], "INCONCLUSIVE")

    def test_too_few_valid_inconclusive(self):
        r = self.series([20, 15, 30], [0, 0, 0], bad=(1,))
        self.assertEqual(r["verdict"], "INCONCLUSIVE")

    def test_wrong_order_inconclusive(self):
        r = self.series([20, 15, 30], [0, 0, 0], order=["P", "P", "P", "S", "S", "S"])
        self.assertEqual(r["verdict"], "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
