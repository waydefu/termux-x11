#!/usr/bin/env python3
"""gap3_requal.py must be able to say PASS, FAIL and INVALID."""
import json, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
HERE = Path(__file__).resolve().parent
X = 4100


def make(d: Path, staged_mb=4096, swap=(4000, 4100), avail=(5000, 4500), maps=(4000, 4010), trip=False):
    d.mkdir(parents=True)
    (d / "x3-pid.txt").write_text(f"x3_pid={X}\n")
    n = staged_mb // 12
    L = [f"09-23 18:00:00.000  {X}  {X} I LorieNative: Sent shared buffer width 1200 stride 1200 height 2621 format 5 type 2 id {i}"
         for i in range(n)]
    L.append(f"09-23 18:00:00.000  999  999 I LorieNative: Sent shared buffer width 30000 stride 30000 height 30000 format 5 type 2 id 1")
    (d / "raw-logcat.txt").write_text("\n".join(L) + "\n")
    g = ["# mem-guard start"] + [f"1.0 mem_available_mb={a} swap_used_mb={s} x3_rss_mb=200 x3_swap_mb=0"
                                 for a, s in zip(avail, swap)]
    (d / "mem-guard.log").write_text("\n".join(g) + "\n")
    if maps is not None:
        (d / "activity-maps.txt").write_text(f"before={maps[0]}\nafter={maps[1]}\n")
    if trip:
        (d / "MEM-GUARD-TRIPPED.txt").write_text("MEM_GUARD_TRIPPED reason=x\n")


def run(d):
    o = d / "out.json"
    subprocess.run([sys.executable, str(HERE / "gap3_requal.py"), "--evidence", str(d), "--out", str(o)],
                   check=True, capture_output=True)
    return json.loads(o.read_text())["verdict"]


class T(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.t, ignore_errors=True)

    def test_flat_memory_passes(self):
        make(self.t / "e"); self.assertEqual(run(self.t / "e"), "GAP3_REQUAL_PASS")

    def test_swap_growth_fails(self):
        make(self.t / "e", swap=(4000, 4700)); self.assertEqual(run(self.t / "e"), "GAP3_REQUAL_FAIL")

    def test_avail_drop_fails(self):
        make(self.t / "e", avail=(5000, 3900)); self.assertEqual(run(self.t / "e"), "GAP3_REQUAL_FAIL")

    def test_maps_growth_fails(self):
        make(self.t / "e", maps=(4000, 4100)); self.assertEqual(run(self.t / "e"), "GAP3_REQUAL_FAIL")

    def test_trip_fails(self):
        make(self.t / "e", trip=True); self.assertEqual(run(self.t / "e"), "GAP3_REQUAL_FAIL")

    def test_too_little_staging_is_invalid(self):
        # a run that did not stage cannot show the leak is gone; foreign pid bytes never count
        make(self.t / "e", staged_mb=1024); self.assertEqual(run(self.t / "e"), "GAP3_REQUAL_INVALID")

    def test_missing_maps_is_invalid(self):
        make(self.t / "e", maps=None); self.assertEqual(run(self.t / "e"), "GAP3_REQUAL_INVALID")


if __name__ == "__main__":
    unittest.main()
