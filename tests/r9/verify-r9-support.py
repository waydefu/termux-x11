#!/usr/bin/env python3
"""V2-R9-HOST-VERIFY. No device, no product build, no attempts.

Three jobs:
  1. the spec is well-formed and frozen
  2. the judge's vectors pass
  3. SOURCE BINDING - every fault number, fatal token and reason code the spec
     names must actually exist at the cited site in the product source. A spec
     that drifts from the product silently produces INVALID_CONSTRUCTION on
     device, which costs an attempt and yields nothing.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent.parent
LORIE = SRC / "lorie/src/main/cpp/lorie"


def need(cond, label, bad):
    if not cond:
        bad.append(label)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    bad: list[str] = []

    # ---- 1. spec ----
    spec_path = HERE / "r9-lifecycle-cell-spec.json"
    need(spec_path.is_file(), "spec_present", bad)
    spec = json.loads(spec_path.read_text())
    need(spec.get("status") == "R9_CELL_SPEC_FROZEN_V1", "spec_frozen", bad)
    need(spec["cell_order"] == ["R9-COLD-2", "R9-F1", "R9-F2"], "spec_cell_order", bad)
    need(len(spec["cells"]) == 3, "spec_cell_count", bad)
    need("-noreset" in spec["policy"]["x_launch_flags"], "spec_noreset_required", bad)
    need(spec["policy"]["experimental_display"] == ":3", "spec_display_pinned", bad)
    need(spec["policy"]["stable_policy"] == "never touched", "spec_stable_redline", bad)
    need(spec["runtime_authorization"].startswith("NOT GRANTED"),
         "spec_no_implicit_authorization", bad)
    # the six removals must stay recorded with their reopen conditions
    need(len(spec["removed_cells"]) >= 7, "spec_removals_recorded", bad)
    for k in ("R9-WARM-1", "R9-WARM-2", "R9-WARM-3", "R9-COLD-1", "R9-COLD-3",
              "V2-R9-RESET"):
        need(k in spec["removed_cells"], f"spec_removal_{k}", bad)

    cells = {c["id"]: c for c in spec["cells"]}

    # ---- 2. judge vectors ----
    r = subprocess.run([sys.executable, str(HERE / "test-judge-r9.py")],
                       capture_output=True, text=True)
    need(r.returncode == 0 and "failures=0" in r.stdout,
         f"judge_vectors_r9:{r.stdout[-200:]}", bad)

    # ---- 2b. evidence derivation ----
    # Every judgement-relevant inference lives in r9_evidence.py precisely so it can
    # be tested without a device. If the derivation and the judge ever disagree, it
    # must show here and not by burning an attempt.
    need((HERE / "r9_evidence.py").is_file(), "evidence_module_present", bad)
    r = subprocess.run([sys.executable, str(HERE / "test_r9_evidence.py")],
                       capture_output=True, text=True)
    need(r.returncode == 0, f"evidence_tests:{(r.stderr or r.stdout)[-200:]}", bad)
    ev_src = (HERE / "r9_evidence.py").read_text()
    # the parser must key on the LAST ')' - a naive split lands on the wrong field
    # when comm contains spaces or parentheses (Q8)
    need("rindex(\")\")" in ev_src, "evidence_proc_parse_last_paren", bad)
    # NEVER FABRICATE: unobserved values must stay None so the judge refuses
    need("NEVER fabricate" in ev_src, "evidence_no_fabrication_contract", bad)

    # ---- 3. SOURCE BINDING ----
    lorie_h = (LORIE / "lorie.h").read_text()
    renderer = (LORIE / "renderer.cpp").read_text()
    cmdentry = (LORIE / "cmdentrypoint.cpp").read_text()

    # reason code 6 must still be FAIL_GENERATION
    need("LORIE_GATEA_FAIL_GENERATION = 6," in lorie_h, "src_reason6_is_generation", bad)

    # COLD-2 depends on fault 8 publishing. If it ever stops publishing, X takes
    # x-eof and the cell becomes unconstructible (COLD1-ROUTE-SEARCH.md).
    need("LORIE_GATEA_TEST_RENDERER_FATAL_PRE_FENCE = 8," in lorie_h,
         "src_fault8_number", bad)
    need(cells["R9-COLD-2"]["test_fault"] == 8, "spec_cold2_fault8", bad)
    i = renderer.find("LORIE_GATEA_TEST_RENDERER_FATAL_PRE_FENCE")
    need(i > 0, "src_fault8_consumed", bad)
    need("gateARendererFatal" in renderer[i:i + 400], "src_fault8_publishes", bad)

    # F1 depends on fault 16 sending a second READY with a decremented tuple
    need("LORIE_GATEA_TEST_STALE_READY_REPLAY = 16," in lorie_h, "src_fault16_number", bad)
    need(cells["R9-F1"]["test_fault"] == 16, "spec_f1_fault16", bad)
    j = renderer.find("LORIE_GATEA_TEST_STALE_READY_REPLAY")
    need(j > 0, "src_fault16_consumed", bad)
    blk = renderer[j:j + 400]
    need("generation - 1" in blk and "nonce - 1" in blk, "src_fault16_stale_tuple", bad)
    need("gateASendReady" in blk, "src_fault16_sends_ready", bad)

    # the arming env var takes a NAME, not a number. A numeric value resolves to
    # cell 0 and the X server fatal-halts with x-test-fault-env before the cell
    # runs - an attempt spent on nothing. Pin each name to its index in the
    # product's own table.
    init_c = (LORIE / "InitOutput.c").read_text()
    tbl = init_c.split("gateATestCellNames[] = {", 1)[-1].split("};", 1)[0]
    entries = [e.strip().strip('",') for e in tbl.split("\n") if e.strip()]
    for cid, idx, nm in (("R9-COLD-2", 8, "renderer-fatal-pre-fence"),
                         ("R9-F1", 16, "stale-ready-replay")):
        need(cells[cid].get("test_fault_env_name") == nm, f"spec_arming_name_{cid}", bad)
        need(idx < len(entries) and entries[idx] == nm,
             f"src_fault_name_index_{idx}", bad)
        arm = cells[cid].get("arming", {})
        need(arm.get("TERMUX_X11_GATEA_TEST_FAULT") == nm, f"spec_arming_env_{cid}", bad)
        need(arm.get("TERMUX_X11_GATEA_TEST_ARM") == "1", f"spec_arming_flag_{cid}", bad)
    need("gateATestCellFromName" in init_c, "src_fault_name_lookup", bad)

    # the two expected fatals must exist at the cited sites
    need('"x-bump-unterminal", LORIE_GATEA_FAIL_GENERATION' in cmdentry,
         "src_fatal_x_bump_unterminal", bad)
    need('LORIE_GATEA_FAIL_GENERATION, "x-wrong-generation"' in cmdentry,
         "src_fatal_x_wrong_generation", bad)
    for cid, what in (("R9-COLD-2", "x-bump-unterminal"), ("R9-F1", "x-wrong-generation")):
        f = cells[cid]["fatal"]
        need(f["what"] == what and f["reason"] == 6, f"spec_fatal_{cid}", bad)

    # F1's silent-drop trap must still be the shape the spec warns about
    k = cmdentry.find("static void handleGateARecord")
    need(k > 0 and "lorieGateAActive" in cmdentry[k:k + 300],
         "src_f1_silent_drop_precedes_tuple_check", bad)

    # D-01: the product default must still be DE_RESET, which is WHY -noreset is
    # mandatory. If upstream ever changes it, the policy needs re-deriving.
    dispatch_c = (SRC / "lorie/src/main/cpp/xserver/dix/dispatch.c").read_text()
    need("char dispatchExceptionAtReset = DE_RESET;" in dispatch_c,
         "src_reset_default_unchanged", bad)

    # D-02: multi-epoch observation must still be present, or R9 cannot be observed
    need("lorieR8ObsEpoch" in (LORIE / "lorie_r8_obs.h").read_text(), "src_epoch_api", bad)
    need("r8RendererRunFinalizeSeen" in renderer, "src_finalize_latch", bad)

    if bad:
        print("R9_SUPPORT_HOST_FAIL")
        for b in bad:
            print("  " + b)
        return 1
    print("R9_SUPPORT_HOST_STATIC_OK")
    print(f"r9_cells={len(spec['cells'])} removed={len(spec['removed_cells']) - 1}")
    print("spec_sha " + sha(spec_path))
    print("judge_sha " + sha(HERE / "judge-r9.py"))
    print("evidence_sha " + sha(HERE / "r9_evidence.py"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
