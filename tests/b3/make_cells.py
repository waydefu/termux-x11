#!/usr/bin/env python3
"""V2-B3-MATRIX-BIND cell list (D-11). Deterministic: same code + seed -> same bytes.

    make_cells.py --out cells.tsv [--seed 20260923 --holdout 40]

Columns: id group rw rh sw sh ratio reuse batch residency readback
Groups: A (core plane), B (interaction planes), C (random hold-out), N (noise centre, one row;
the runner re-inserts it every 25 cells). Infeasible combinations are listed in
pruned.tsv with their reason and never measured.
"""
from __future__ import annotations

import argparse
import itertools
import random

RECTS = [("1x1", 1, 1), ("5x24", 5, 24), ("16x16", 16, 16), ("32x32", 32, 32),
         ("64x64", 64, 64), ("128x128", 128, 128), ("256x256", 256, 256),
         ("512x512", 512, 512), ("1024x1024", 1024, 1024),
         ("fs-internal", 1200, 2608), ("fs-external", 3440, 1440)]
RATIOS = ["1x", "4x", "16x", "redirected"]
REUSES = [1, 4, 16, 64]
BATCHES = [1, 4, 8, 16]
RESIDENCIES = ["cold", "warm", "resize", "recreate"]
READBACKS = ["none", "immediate", "delayed"]
CENTER = {"ratio": "1x", "reuse": 1, "residency": "warm", "batch": 1, "readback": "none"}
WINDOW = (1200, 2191)
SRC_CAP = 64 * 1024 * 1024
TOTAL_CAP = 256 * 1024 * 1024


def src_dims(rw, rh, ratio):
    if ratio == "1x":
        return rw, rh
    if ratio == "4x":
        return 2 * rw, 2 * rh
    if ratio == "16x":
        return 4 * rw, 4 * rh
    return max(rw, WINDOW[0]), max(rh, WINDOW[1])          # redirected window-sized source


def feasible(rw, rh, ratio, reuse, residency):
    sw, sh = src_dims(rw, rh, ratio)
    if sw > 32767 or sh > 32767:
        return "x_pixmap_dimension_limit"
    if sw * sh * 4 > SRC_CAP:
        return "src_over_64MB"
    if reuse * (sw * sh + rw * rh) * 4 > TOTAL_CAP:
        return "sets_over_256MB"
    if residency != "warm" and reuse != 1:
        return "reuse_undefined_without_live_sets"   # reuse rotates LIVE sets; cold/resize/recreate have none
    return None


def row(cid, group, rect, ratio, reuse, batch, residency, readback):
    name, rw, rh = rect
    sw, sh = src_dims(rw, rh, ratio)
    return [cid, group, rw, rh, sw, sh, ratio, reuse, batch, residency, readback]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--pruned", required=True)
    ap.add_argument("--seed", type=int, default=20260923)
    ap.add_argument("--holdout", type=int, default=40)
    a = ap.parse_args()
    cells, seen, pruned = [], set(), []

    def key(rect, ratio, reuse, batch, res, rb):
        return (rect[0], ratio, reuse, batch, res, rb)

    def add(group, rect, ratio, reuse, batch, res, rb):
        k = key(rect, ratio, reuse, batch, res, rb)
        if k in seen:
            return
        why = feasible(rect[1], rect[2], ratio, reuse, res)
        if why:
            pruned.append([group, rect[0], ratio, reuse, batch, res, rb, why])
            seen.add(k)
            return
        seen.add(k)
        cells.append(row(f"{group}{len(cells):04d}", group, rect, ratio, reuse, batch, res, rb))

    c = CENTER
    for rect, b, rb in itertools.product(RECTS, BATCHES, READBACKS):
        add("A", rect, c["ratio"], c["reuse"], b, c["residency"], rb)
    for rect in RECTS:
        for ratio in RATIOS:
            add("B", rect, ratio, c["reuse"], c["batch"], c["residency"], c["readback"])
        for reuse in REUSES:
            add("B", rect, c["ratio"], reuse, c["batch"], c["residency"], c["readback"])
        for res in RESIDENCIES:
            add("B", rect, c["ratio"], c["reuse"], c["batch"], res, c["readback"])
    rest = [k for k in itertools.product(RECTS, RATIOS, REUSES, BATCHES, RESIDENCIES, READBACKS)
            if key(*k) not in seen and not feasible(k[0][1], k[0][2], k[1], k[2], k[4])]
    rng = random.Random(a.seed)
    for k in rng.sample(rest, a.holdout):
        add("C", *k)
    centre = row("N0000", "N", RECTS[8], "1x", 1, 1, "warm", "none")
    with open(a.out, "w") as f:
        f.write("id\tgroup\trw\trh\tsw\tsh\tratio\treuse\tbatch\tresidency\treadback\n")
        for r in [centre] + cells:
            f.write("\t".join(map(str, r)) + "\n")
    with open(a.pruned, "w") as f:
        f.write("group\trect\tratio\treuse\tbatch\tresidency\treadback\treason\n")
        for p in pruned:
            f.write("\t".join(map(str, p)) + "\n")
    groups = {}
    for r in cells:
        groups[r[1]] = groups.get(r[1], 0) + 1
    print(f"cells={len(cells)} groups={groups} pruned_in_planes={len(pruned)} "
          f"full_feasible={len(rest) + len(cells)} seed={a.seed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
