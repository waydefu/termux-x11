#!/usr/bin/env python3
"""Offline tests for b3_attribution.py on a synthetic ATTR evidence directory."""
from __future__ import annotations
import datetime as dt, json, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
HERE = Path(__file__).resolve().parent
X, ACT, STABLE = 4100, 4200, 777
B = 1790000000.0          # an epoch in 2026


def lc(epoch, pid, tag, msg):
    t = dt.datetime.fromtimestamp(epoch)
    return f"{t:%m-%d %H:%M:%S}.{t.microsecond // 1000:03d}  {pid}  {pid} I {tag}: {msg}"


class Ev:
    """cells: id -> (batch, event5 lines inside its window). warmup 1 + iters 3 + 1 check
    batch => issued = 5 * batch."""

    def __init__(self, d: Path):
        self.d = d
        d.mkdir(parents=True)
        self.cells = {"c1": (2, 10), "c2": (1, 3), "c3": (4, 0)}
        self.noise = 20           # event 30 lines inside every window (same pids)
        self.foreign5 = 0         # event 5 lines from a pid that is not ours, inside c3
        self.drop = 0
        self.drop_noise = 0       # lose event 30 lines (logd drop of a NON-direct line)
        self.span = 10            # cell i window: [B + span*i, B + span*i + 5]; span 5 = touching
        self.first_failed = 0

    def write(self):
        out, L, seq = [], [], 0
        for i, (cid, (batch, n5)) in enumerate(self.cells.items()):
            b, e = B + self.span * i, B + self.span * i + 5
            out += [f"MARK CELL_BEGIN {cid} {b:.6f}",
                    "CELL " + json.dumps({"id": cid, "batch": batch, "rw": 64, "rh": 64,
                                          "reuse": 1, "residency": "warm", "ratio": "1x",
                                          "readback": "none"}),
                    f"MARK CELL_END {cid} {e:.6f}"]
            for k in range(n5):
                L.append(lc(b + 0.5 + k * 0.01, X, "gatea-telemetry",
                            f"GATEA_EVENT seq={seq} role=1 event=5 generation=1 serial={seq} src=1 dst=2"))
                seq += 1
            for k in range(self.noise):
                L.append(lc(b + 1 + k * 0.01, ACT, "gatea-telemetry",
                            f"GATEA_EVENT seq={seq} role=2 event=30 generation=1 serial={seq} src=1 dst=2"))
                seq += 1
            if cid == "c3":
                for k in range(self.foreign5):
                    L.append(lc(b + 2 + k * 0.01, STABLE, "gatea-telemetry",
                                f"GATEA_EVENT seq={k} role=1 event=5 generation=1 serial=0 src=1 dst=2"))
        c0 = sum(1 for ln in L if " event=5 " in ln and f"  {X} " in ln)
        del L[:self.drop]
        for _ in range(self.drop_noise):
            L.remove(next(ln for ln in L if " event=30 " in ln))
        (self.d / "b3-cells.out").write_text("\n".join(out) + "\n")
        (self.d / "raw-logcat.txt").write_text("\n".join(L) + "\n")
        (self.d / "x3-pid.txt").write_text(f"x3_pid={X}\n")
        (self.d / "activity-pid.txt").write_text(f"activity_pid={ACT}\n")
        (self.d / "gatea-summary.txt").write_text(
            f"GATEA_SUMMARY where=x-close-screen nonce=0 generation=0 nextSequence={seq} overflow=1 "
            f"firstFailed={self.first_failed} generationFatal=0 fatalReason=0 c0={c0}\n")

    def run(self):
        self.write()
        o = self.d / "attr.json"
        r = subprocess.run([sys.executable, str(HERE / "b3_attribution.py"), "--evidence",
                            str(self.d), "--out", str(o)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return json.loads(o.read_text())


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.e = Ev(self.tmp / "e")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_classes(self):
        a = self.e.run()
        self.assertTrue(a["events_complete"])
        c = a["cells"]
        self.assertEqual((c["c1"]["issued"], c["c1"]["event5"], c["c1"]["class"]), (10, 10, "DIRECT"))
        self.assertEqual((c["c2"]["issued"], c["c2"]["event5"], c["c2"]["class"]), (5, 3, "MIXED"))
        self.assertEqual((c["c3"]["issued"], c["c3"]["event5"], c["c3"]["class"]), (20, 0, "NONE"))

    def test_foreign_pid_event5_not_counted(self):
        # Stable (or anything not ours) logging the same tag must not become "direct"
        self.e.foreign5 = 4
        a = self.e.run()
        self.assertEqual(a["cells"]["c3"]["event5"], 0)
        self.assertEqual(a["cells"]["c3"]["class"], "NONE")

    def test_non_direct_line_lost_still_attributes(self):
        self.e.drop_noise = 1
        a = self.e.run()
        self.assertFalse(a["events_complete"])
        self.assertTrue(a["event5_complete"])
        self.assertEqual(a["cells"]["c1"]["class"], "DIRECT")

    def test_touching_windows_null_every_cell(self):
        # back-to-back cells (V1 fixture): a line could be credited to two cells
        self.e.span = 5
        a = self.e.run()
        self.assertFalse(a["windows_disjoint"])
        self.assertTrue(all(v["class"] is None for v in a["cells"].values()))

    def test_fatal_disables_the_c0_rule(self):
        self.e.drop_noise = 1; self.e.first_failed = 3
        a = self.e.run()
        self.assertFalse(a["event5_complete"])

    def test_incomplete_events_null_every_cell(self):
        self.e.drop = 1
        a = self.e.run()
        self.assertFalse(a["events_complete"])
        self.assertTrue(all(v["event5"] is None and v["class"] is None for v in a["cells"].values()))


if __name__ == "__main__":
    unittest.main()
