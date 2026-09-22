#!/usr/bin/env python3
"""V2-R10-HOST-VERIFY — host-only. No device, no attempt, no product change.

Binds the R10 design to the product source it rests on. Every check here answers
"is the thing R10 assumes still true of dc94485?". A failure is not a tooling
regression to be patched around: it means an assumption moved, and the affected
design section has to be reopened.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LORIE = HERE.parent.parent / "lorie/src/main/cpp/lorie"
JAVA = HERE.parent.parent / "lorie/src/main/java/com/termux/x11"


def need(cond: bool, tag: str, bad: list[str]) -> None:
    if not cond:
        bad.append(tag)


def main() -> int:
    bad: list[str] = []

    # ---- 1. the frozen probe inventory ----
    inv_path = HERE / "r10-probe-inventory.json"
    need(inv_path.is_file(), "inventory_present", bad)
    inv = json.loads(inv_path.read_text())
    need(inv.get("status") == "R10_PROBE_INVENTORY_FROZEN_V1", "inventory_frozen", bad)
    need(inv.get("product_sha", "").startswith("dc94485"), "inventory_product", bad)
    metrics = inv["metrics"]
    by_status: dict[str, int] = {}
    for m in metrics:
        by_status[m["status"]] = by_status.get(m["status"], 0) + 1
    need(set(by_status) <= set(inv["status_values"]), "inventory_status_values", bad)
    # the null rule is the one thing that must never be relaxed
    need("forbidden" in inv["null_rule"], "inventory_null_rule", bad)
    # the fence-fd gap must stay recorded, not quietly dropped (R-30)
    fence = [m for m in metrics if "fence_fd" in m["metric"]]
    need(len(fence) == 3 and all(m["status"] == "unavailable" for m in fence),
         "inventory_fence_fd_gap_recorded", bad)

    # ---- 2. counter indices, re-derived from the product ----
    sys.path.insert(0, str(HERE.parent / "common"))
    import gatea_counters as C
    lorie_h = (LORIE / "lorie.h").read_text()
    need(C.parse_from_source(lorie_h) == C.COUNTERS, "src_counter_enum", bad)
    need((C.X_REGISTRY_CURRENT, C.RENDERER_REGISTRY_CURRENT, C.LEASE_CURRENT)
         == (18, 19, 20), "src_registry_lease_indices", bad)
    # every counter the inventory names must exist in the enum
    for m in metrics:
        name = m["metric"]
        if name.startswith("gatea.c_"):
            need(name[len("gatea.c_"):].upper() in C.INDEX,
                 f"inventory_counter_exists_{name}", bad)

    # ---- 3. the dump is still the ONLY source of counters ----
    init_c = (LORIE / "InitOutput.c").read_text()
    need('fopen(LORIE_GATEA_SUMMARY_PATH, "w")' in init_c, "src_summary_written", bad)
    need('fopen(LORIE_GATEA_RING_PATH, "w")' in init_c, "src_ring_written", bad)
    need('lorieGateADumpSummary(pvfb->state, "x-close-screen")' in init_c,
         "src_clean_close_dumps", bad)

    # ---- 4. the clean close really is the releasing path ----
    # If this stops retiring every buffer, R10's class-1 criteria are meaningless.
    cg = init_c.rsplit("static void gateACloseGeneration(void)", 1)[-1][:2600]
    need("gateARetireBufferId(ids[i])" in cg, "src_close_retires_all", bad)
    need('gateAXFatal("x-close-registry-not-empty"' in cg, "src_close_asserts_empty", bad)
    need("StoreU64Release(&shared->sessionNonce, 0)" in cg, "src_close_zeroes_nonce", bad)

    renderer = (LORIE / "renderer.cpp").read_text()
    need('gateARendererFatal(st, "r-close-not-empty"' in renderer,
         "src_renderer_asserts_empty_at_close", bad)
    need("lorieGateAUnbindTuple(ctl->nonce, ctl->generation)" in renderer,
         "src_renderer_unbinds_at_close", bad)

    # ---- 5. D-06 REOPEN GUARD ----
    # R10-B exists because the Activity SURVIVES a clean close and dies on every
    # other ending. Both halves are one classifier. If either moves, D-06 and the
    # R9 WARM removals must be reopened and R10-B's premise re-examined.
    hup = (LORIE / "lorie_gatea_hup_class.h").read_text()
    need("if (!bound)" in hup and "LORIE_GATEA_HUP_UNBOUND" in hup,
         "src_hup_unbound_arm", bad)
    need("LORIE_GATEA_HUP_PRESERVE" in hup and "LORIE_GATEA_HUP_R_HUP" in hup,
         "src_hup_bound_arms", bad)
    act = (LORIE / "activity.cpp").read_text()
    need("gateAHupPreserve(published, bn, bg)" in act, "src_preserve_is_fatal", bad)
    need('lorieGateAFatalHalt("r-hup", LORIE_GATEA_FAIL_GENERATION)' in act,
         "src_r_hup_is_fatal", bad)
    gh = act.rsplit("static void gateAHupPreserve", 1)[-1][:700]
    need("_exit(127);" in gh, "src_preserve_exits", bad)
    # and the warm reattach route the surviving Activity uses
    need("static void connect_(" in act, "src_connect_entry", bad)
    cmd = (JAVA / "CmdEntryPoint.java").read_text()
    need("sendBroadcastDelayed" in cmd and "postDelayed(this::sendBroadcastDelayed, 1000)"
         in cmd, "src_action_start_rebroadcast", bad)
    ma = (JAVA / "MainActivity.java").read_text()
    need("LorieView.connect(fd.detachFd())" in ma, "src_activity_accepts_new_fd", bad)

    # ---- 6. the workload's format pair is still the only accelerated one ----
    need("if (op != PictOpOver)" in init_c, "src_only_pictopover", bad)
    need("src->format != PICT_a8r8g8b8" in init_c, "src_src_format", bad)
    need("dst->format != PICT_x8r8g8b8" in init_c, "src_dst_format", bad)
    fixture = (HERE / "p_r10_ledger.c").read_text()
    need("XCB_RENDER_PICT_OP_OVER" in fixture, "fixture_op", bad)
    need("#define PAIR_W 1024" in fixture and "#define PAIR_H 1024" in fixture,
         "fixture_pair_size", bad)
    need("#define N_PAIRS 4" in fixture, "fixture_pair_count", bad)
    need('strcmp(display, ":3") != 0' in fixture, "fixture_stable_redline", bad)

    # ---- 7. the design and its evidence exist and are referenced ----
    root = HERE.parent.parent.parent.parent      # .../GPU加速
    design = root / "evidence/session/gate-a-a1/planning-v2/r10-design/V2-R10-DESIGN.md"
    d06 = root / "evidence/session/gate-a-a1/planning-v2/d06/D-06-DECISION.md"
    need(design.is_file(), "design_present", bad)
    need(d06.is_file(), "d06_present", bad)
    if design.is_file():
        dt = design.read_text()
        for tag in ("R10-A", "R10-B", "R10-C", "V2-R10-NOISE"):
            need(tag in dt, f"design_mentions_{tag}", bad)
        need("1024" in dt, "design_workload_size", bad)

    if bad:
        print("R10_SUPPORT_HOST_FAIL")
        for b in bad:
            print("  " + b)
        return 1
    print("R10_SUPPORT_HOST_STATIC_OK")
    print(f"metrics={len(metrics)} " +
          " ".join(f"{k}={v}" for k, v in sorted(by_status.items())))
    for f in ("r10-probe-inventory.json", "r10_sample.py", "p_r10_ledger.c"):
        print(f"{f} sha256 "
              + hashlib.sha256((HERE / f).read_bytes()).hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main())
