#!/usr/bin/env python3
"""The prediction evaluator must be able to say yes, no and don't-know."""
import importlib.util, json, shutil, tempfile, unittest
from pathlib import Path
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("op", HERE / "oplat_predictions.py")
OP = importlib.util.module_from_spec(spec); spec.loader.exec_module(OP)


def world(sync):
    """sync=True: GPU ops wait ~8 ms each in G; otherwise G behaves like C."""
    g, c = {}, {}
    for op in ("nop", "solid", "copy_win", "copy_pix", "over_argb", "src_argb", "putimage"):
        for w in (16, 64, 256, 1024):
            for shape in ("single", "burst16"):
                base = 150 if shape == "single" else 900
                gpu = op in ("solid", "copy_win", "copy_pix", "over_argb")
                gv = (8000 if shape == "single" else 16 * 8000) if (sync and gpu) else base
                g[(op, w, shape)] = {"p50_us": gv, "errors": 0}
                c[(op, w, shape)] = {"p50_us": base, "errors": 0}
    return g, c


class T(unittest.TestCase):
    def test_sync_world_confirms(self):
        r = OP.evaluate(*world(True))
        self.assertEqual([r[p]["holds"] for p in ("P1", "P2", "P3", "P4", "P5")], [True] * 5)
        self.assertEqual(r["reading"], "MECHANISM_CONFIRMED_SYNC_FRAME_COUPLED_SOLID_COPY")

    def test_equal_world_refutes(self):
        r = OP.evaluate(*world(False))
        self.assertFalse(r["P1"]["holds"])
        self.assertFalse(r["P4"]["holds"])
        self.assertFalse(r["P3"]["holds"])       # 16 CPU ops cost 6x one, not >= 8x
        self.assertEqual(r["reading"], "SOLID_COPY_NOT_THE_SOURCE")

    def test_errors_make_null_not_false(self):
        g, c = world(True)
        g[("solid", 64, "single")]["errors"] = 3
        r = OP.evaluate(g, c)
        self.assertIsNone(r["P1"]["holds"])
        self.assertEqual(r["reading"], "UNDECIDED")

    def test_src_slowed_in_g_breaks_p5(self):
        g, c = world(True)
        g[("src_argb", 64, "single")]["p50_us"] = 5 * c[("src_argb", 64, "single")]["p50_us"]
        self.assertFalse(OP.evaluate(g, c)["P5"]["holds"])

    def test_missing_run_is_null(self):
        r = OP.evaluate(None, None)
        self.assertTrue(all(r[p]["holds"] is None for p in ("P1", "P2", "P3", "P4", "P5")))


class R(unittest.TestCase):
    def test_sync_world_confirms(self):
        g, c = world(True)
        for w in (16, 64, 256, 1024):            # copy_win is CPU in both modes (src == dst)
            for sh in ("single", "burst16"):
                g[("copy_win", w, sh)]["p50_us"] = c[("copy_win", w, sh)]["p50_us"]
        r = OP.evaluate_replication(g, c)
        self.assertEqual([r[k]["holds"] for k in ("R1", "R2", "R3")], [True, True, True])
        self.assertEqual(r["reading"], "O1_O2_CONFIRMED")

    def test_one_size_where_gpu_wins_breaks_r1(self):
        g, c = world(True)
        g[("over_argb", 1024, "single")]["p50_us"] = c[("over_argb", 1024, "single")]["p50_us"] - 1
        r = OP.evaluate_replication(g, c)
        self.assertFalse(r["R1"]["holds"])
        self.assertEqual(r["reading"], "NOT_CONFIRMED")

    def test_pipelined_burst_breaks_r2(self):
        g, c = world(True)
        g[("solid", 16, "burst16")]["p50_us"] = 3 * g[("solid", 16, "single")]["p50_us"]
        self.assertFalse(OP.evaluate_replication(g, c)["R2"]["holds"])

    def test_everything_slow_in_g_breaks_control_r3(self):
        g, c = world(True)
        for w in (16, 64, 256, 1024):
            for sh in ("single", "burst16"):
                g[("copy_win", w, sh)]["p50_us"] = 4 * c[("copy_win", w, sh)]["p50_us"]
        r = OP.evaluate_replication(g, c)
        self.assertFalse(r["R3"]["holds"])
        self.assertEqual(r["reading"], "NOT_CONFIRMED")

    def test_error_cell_is_null(self):
        g, c = world(True)
        c[("copy_pix", 256, "single")]["errors"] = 1
        self.assertIsNone(OP.evaluate_replication(g, c)["R1"]["holds"])


if __name__ == "__main__":
    unittest.main()
