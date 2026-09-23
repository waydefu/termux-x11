#!/usr/bin/env python3
"""Offline tests for b3_analyze.py, on synthetic fixture output generated from cells.tsv."""
from __future__ import annotations
import importlib.util, json, math, shutil, tempfile, unittest
from pathlib import Path
HERE = Path(__file__).resolve().parent
FREEZE = json.loads((HERE / "b3-freeze.json").read_text())
spec = importlib.util.spec_from_file_location("b3a", HERE / "b3_analyze.py")
B = importlib.util.module_from_spec(spec); spec.loader.exec_module(B)
CELLS = [l.split("\t") for l in (HERE / "cells.tsv").read_text().splitlines()[1:]]


def cost(mode, row, interaction=0.0, flip=None):
    cid, grp, rw, rh, sw, sh, ratio, reuse, batch, res, rb = row
    if flip and mode == "G" and (int(rw), int(rh)) == flip:
        return 10 ** 9                    # one rect where the GPU path is terrible
    area = int(rw) * int(rh)
    if mode == "C":                       # CPU: linear in area
        lt = math.log(2000 + 0.5 * area)
    else:                                 # GPU: big fixed cost, cheap per pixel -> crossover
        lt = math.log(60000 + 0.02 * area)
    lt += {"1": 0, "4": -0.1, "8": -0.15, "16": -0.2}[batch]
    lt += {"none": 0, "immediate": 0.3, "delayed": 0.1}[rb]
    lt += {"1x": 0, "4x": 0.05, "16x": 0.2, "redirected": 0.1}[ratio] * (1 if mode == "C" else 0.5)
    lt += {"1": 0, "4": 0.02, "16": 0.3, "64": 0.4}[reuse] * (1 if mode != "C" else 0.1)
    lt += {"warm": 0, "cold": 0.5, "resize": 0.4, "recreate": 0.8}[res]
    if grp == "C":
        lt += interaction                 # a hidden interaction the A+B model cannot see
    return int(math.exp(lt))


def write(path, mode, noise=(1000, 1010, 1005), interaction=0.0, bad_ids=(), flip=None):
    L = []
    for i, row in enumerate(CELLS):
        if row[1] == "N":
            continue
        d = {"seq": i, "id": row[0], "group": row[1], "rw": int(row[2]), "rh": int(row[3]),
             "sw": int(row[4]), "sh": int(row[5]), "ratio": row[6], "reuse": int(row[7]),
             "batch": int(row[8]), "residency": row[9], "readback": row[10],
             "per_op_ns_p50": cost(mode, row, interaction if mode == "G" else 0.0, flip),
             "errors": 0, "pixel_ok": row[0] not in bad_ids}
        L.append("CELL " + json.dumps(d))
    for n in noise:
        L.append("CELL " + json.dumps({"id": "N0000", "group": "N", "per_op_ns_p50": n,
                                       "errors": 0, "pixel_ok": True}))
    Path(path).write_text("\n".join(L) + "\n")


class T(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def run_(self, **kw):
        write(self.d / "g", "G", **kw)
        write(self.d / "c", "C", noise=kw.get("noise", (1000, 1010, 1005)))
        return B.analyze(FREEZE, {"G": str(self.d / "g"), "C": str(self.d / "c")})

    def test_additive_world_validates_pruning(self):
        r = self.run_()
        self.assertEqual(r["verdict"], "PRUNING_VALID", r["holdout"])

    def test_hidden_interaction_rejects_pruning(self):
        # the control that must fail: C cells carry an effect A+B never saw
        r = self.run_(interaction=1.2)
        self.assertEqual(r["verdict"], "PRUNING_INVALID_EXPAND")

    def test_noise_band_blocks_judgement(self):
        r = self.run_(noise=(1000, 1400))
        self.assertEqual(r["verdict"], "UNJUDGED_NOISE")
        self.assertTrue(all(v["winner"] == "UNJUDGED" for v in r["winner_map"].values()))

    def test_crossover_exists_on_center_line(self):
        r = self.run_()
        line = r["crossover"]["1|none|1x|1|warm"]
        self.assertIsNotNone(line["crossover"])
        self.assertEqual(line["winners"][0][1], "CPU")     # 1x1: CPU wins in this world

    def test_crossover_is_after_the_last_non_gpu_point(self):
        # a GPU win at 512 followed by a loss at 1024 is NOT a crossover at 512
        r = self.run_(flip=(1024, 1024))
        line = r["crossover"]["1|none|1x|1|warm"]
        rects = [w[0] for w in line["winners"]]
        self.assertEqual(line["winners"][rects.index("512x512")][1], "GPU")
        self.assertGreater(rects.index(line["crossover"]), rects.index("1024x1024"))

    def test_bad_pixel_cell_excluded_and_listed(self):
        r = self.run_(bad_ids=("A0001",))
        self.assertIn("A0001", r["runs"]["G"]["excluded"])
        self.assertNotIn("A0001", r["winner_map"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
