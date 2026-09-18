#!/usr/bin/env python3
"""Static gate: R7-P1 Present-target fault arming (qualification validity).

Fault12/13 start unarmed. CopyArea cannot consume. Present schedule arms
nonzero serial+generation before writeIndex. One-shot CAS is unchanged.
No ABI / timeout / judge / protocol / EVENT / CNT / wait-wake change.
Does not authorize device retry or R7-P2.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

JUDGE_SHA256 = (
    "fba3c10f83fc3309151ca62d12dbe9dda4aabde2a040a131dcab116f0bd4cc17"
)
FROZEN_INVALID_RUNNER = "run-r7-p1-8545b26.sh"
FROZEN_CLASSIFIER = "classify_r7_p1_present_hold.py"


def need(cond: bool, label: str, failures: list[str]) -> None:
    if not cond:
        failures.append(label)


def extract_fn(text: str, sig: str) -> str:
    start = 0
    while True:
        pos = text.find(sig, start)
        if pos < 0:
            raise ValueError(f"missing {sig}")
        brace = text.find("{", pos)
        semi = text.find(";", pos)
        if brace < 0:
            raise ValueError(f"missing body for {sig}")
        if semi >= 0 and semi < brace:
            start = semi + 1
            continue
        depth = 0
        i = brace
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return text[pos : i + 1]
            i += 1
        raise ValueError(f"unbalanced {sig}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def verify(repo: Path) -> list[str]:
    failures: list[str] = []
    hdr = repo / "lorie/src/main/cpp/lorie/lorie.h"
    cls = repo / "lorie/src/main/cpp/lorie/lorie_gatea_test_fault_class.h"
    wake = repo / "lorie/src/main/cpp/lorie/lorie_gatea_wait_wake_class.h"
    init = repo / "lorie/src/main/cpp/lorie/InitOutput.c"
    renderer = repo / "lorie/src/main/cpp/lorie/renderer.cpp"
    patch = repo / "lorie/src/main/cpp/patches/xserver.patch"
    host = repo / "scripts/test_gatea_test_fault_class.c"
    wake_host = repo / "scripts/test_gatea_wait_wake_class.c"
    judge_in_tree = repo / "scripts/judge-r7.py"
    frozen_runner = repo / FROZEN_INVALID_RUNNER
    frozen_clf = repo / FROZEN_CLASSIFIER
    evidence_judge = Path(
        "/root/projects/GPU加速/evidence/session/gate-a-a1/p2-r7-design/judge-r7.py"
    )
    hist_runner = Path(
        "/root/projects/GPU加速/evidence/session/gate-a-a1/"
        "p2-r3-xpump-runtime/run-r7-p1-8545b26.sh"
    )
    hist_cell = Path(
        "/root/projects/GPU加速/evidence/session/gate-a-a1/"
        "p2-r3-xpump-runtime/runtime-8545b26/r7-p1"
    )

    need(hdr.is_file(), "hdr:exists", failures)
    need(cls.is_file(), "class:exists", failures)
    need(wake.is_file(), "wake:exists", failures)
    need(init.is_file(), "init:exists", failures)
    need(renderer.is_file(), "renderer:exists", failures)
    need(patch.is_file(), "patch:exists", failures)
    need(host.is_file(), "host:exists", failures)
    need(wake_host.is_file(), "wake-host:exists", failures)
    if failures:
        return failures

    h = hdr.read_text()
    c = cls.read_text()
    w = wake.read_text()
    io = init.read_text()
    r = renderer.read_text()
    p = patch.read_text()
    t = host.read_text()

    need('#include "lorie_gatea_test_fault_class.h"' in h, "hdr:includes-class", failures)
    need("lorieGateATestFaultClassArmPresentTarget" in c, "class:arm-fn", failures)
    need("lorieGateATestFaultClassConsume" in c, "class:consume-fn", failures)
    need("lorieGateATestFaultClassHoldsIncomplete" in c, "class:hold-fn", failures)
    need("lorieGateATestFaultIsPresentBoundCell" in c, "class:present-bound", failures)
    need("LORIE_GATEA_TEST_PRESENT_HOLD_COMPLETE" in c, "class:cell-12", failures)
    need("LORIE_GATEA_TEST_PRESENT_RENDERER_EXIT" in c, "class:cell-13", failures)

    consume = extract_fn(c, "lorieGateATestFaultClassConsume")
    need("compare_exchange_n(&f->consumed" in consume, "class:cas-oneshot", failures)
    need("IsPresentBoundCell(cell)" in consume, "class:consume-present-bound", failures)
    need("targetGen == 0 || targetOrd == 0" in consume, "class:no-wildcard-12-13", failures)
    need("goto" not in consume, "class:consume-no-retry", failures)

    arm = extract_fn(c, "lorieGateATestFaultClassArmPresentTarget")
    need("serial == 0 || generation == 0" in arm, "class:arm-refuse-zero", failures)
    need("consumed != 0" in arm, "class:arm-no-after-consume", failures)
    need("targetGeneration, generation" in arm, "class:store-gen", failures)
    need("targetOrdinal, serial" in arm, "class:store-serial", failures)
    need("armed, 1u" in arm, "class:arm-release", failures)
    need("gotGen == generation && gotOrd == serial" in arm, "class:idempotent-same", failures)
    arm_consumed = arm.find("consumed != 0")
    arm_store = arm.find("targetGeneration")
    need(arm_consumed >= 0 and arm_store > arm_consumed, "class:consumed-before-store", failures)

    need('sizeof(struct LorieGateAProtocol) == 40' in h, "abi:protocol-40", failures)
    need("offsetof(struct LorieGateAProtocol, generationFatal) == 4" in h,
         "abi:fatal-off-4", failures)
    need("#define LORIE_GATEA_PROTOCOL_VERSION 1u" in h, "abi:protocol-version-1", failures)
    need('LORIE_GATEA_STATIC_ASSERT(LORIE_GATEA_EVENT_MAX == 37' in h, "abi:event-max-37", failures)
    need('LORIE_GATEA_STATIC_ASSERT(LORIE_GATEA_COUNTER_MAX == 28' in h, "abi:counter-max-28", failures)
    need('sizeof(struct LorieGateATestFault) == 40' in h, "abi:fault-tail-40", failures)
    need("LORIE_GATEA_TEST_PRESENT_HOLD_COMPLETE = 12" in h, "abi:fault-12", failures)
    need("LORIE_GATEA_TEST_PRESENT_RENDERER_EXIT = 13" in h, "abi:fault-13", failures)
    need("#define LORIE_GATEA_FENCE_TIMEOUT_NS 2000000000ull" in h, "hdr:fence-2000", failures)
    need("#define LORIE_RENDERER_FRAME_WAIT_NS 8000000L" in h, "hdr:8ms", failures)
    need("LORIE_GATEA_EVENT_MAX =" not in c, "class:no-event-enum-change", failures)

    need("lorieGateATestFaultArmPresentTarget" in h, "hdr:arm-wrapper", failures)
    need("lorieGateATestFaultHoldsIncomplete" in h, "hdr:hold-wrapper", failures)
    wrapper = extract_fn(h, "static inline __always_inline int lorieGateATestFaultConsume")
    need("lorieGateATestFaultClassConsume" in wrapper, "hdr:consume-delegates", failures)
    need("LORIE_GATEA_EVENT_TEST_FAULT_FIRED" in wrapper, "hdr:event35-after-cas", failures)
    hold_wrap = extract_fn(h, "static inline __always_inline int lorieGateATestFaultHoldsIncomplete")
    need("lorieGateATestFaultClassHoldsIncomplete" in hold_wrap, "hdr:hold-delegates", failures)

    hold_fn = extract_fn(c, "lorieGateATestFaultClassHoldsIncomplete")
    need("PRESENT_HOLD_COMPLETE" in hold_fn, "class:hold-cell-12-only", failures)
    need("PRESENT_RENDERER_EXIT" not in hold_fn, "class:hold-not-cell-13", failures)
    need("consumed == 0" in hold_fn, "class:hold-requires-consume", failures)
    need("targetOrd != 0 && targetOrd == serial" in hold_fn, "class:hold-serial-exact", failures)
    need("goto" not in hold_fn, "class:hold-no-goto", failures)

    is_done = extract_fn(io, "Bool lorieGpuCopyIsDone")
    need("lorieGateATestFaultHoldsIncomplete" in is_done, "isdone:hold-gate", failures)
    need("ObserveCompleted" in is_done, "isdone:watermark-kept", failures)
    need(">= serial" in is_done, "isdone:ge-watermark", failures)
    hold_at = is_done.find("lorieGateATestFaultHoldsIncomplete")
    ge_at = is_done.find(">= serial")
    need(hold_at >= 0 and ge_at > hold_at, "isdone:hold-before-watermark", failures)
    need("FALSE" in is_done, "isdone:hold-returns-false", failures)
    need("usleep" not in is_done, "isdone:no-sleep", failures)

    blit = extract_fn(io, "static Bool lorieTryScheduleGpuBlit")
    arm_at = blit.find("lorieGateATestFaultArmPresentTarget")
    sync_at = blit.find("__sync_synchronize")
    pub_at = blit.find("lorieGateAPublishWriteIndex")
    need(arm_at >= 0, "blit:arm-present-target", failures)
    need("presentTarget" in blit, "blit:present-flag", failures)
    need(sync_at > arm_at and pub_at > sync_at, "blit:arm-before-writeIndex", failures)
    copy_fn = extract_fn(io, "Bool lorieTryScheduleGpuCopy")
    present_fn = extract_fn(io, "Bool lorieTryScheduleGpuPresentCopy")
    need("presentTarget" not in copy_fn or ", 0," in copy_fn, "copy:presentTarget-0", failures)
    need(", 0," in copy_fn, "copyarea-path:flag-0", failures)
    need(", 1," in present_fn, "present-path:flag-1", failures)

    exa = extract_fn(io, "static void lorieExaCopy")
    need("lorieTryScheduleGpuCopy" in exa, "exa:uses-copy", failures)
    need("lorieTryScheduleGpuPresentCopy" not in exa, "exa:not-present-copy", failures)
    need("LORIE_GPU_OP_COMPOSITE, 0, &serial" in io, "composite:presentTarget-0", failures)
    need("LORIE_GPU_OP_COMPOSITE, 1," not in io, "composite:never-present-arm", failures)

    pub = extract_fn(io, "static void lorieGateATestFaultPublishFromEnv")
    need("IsPresentBoundCell(cell) ? 0u : 1u" in pub, "env:12-13-unarmed", failures)
    need("targetGeneration = 0" in pub, "env:wildcard-zero", failures)
    need("targetOrdinal = 0" in pub, "env:ordinal-zero", failures)

    cb = extract_fn(io, "void lorieGateATraceXCallback")
    need("LORIE_GATEA_XOP_PRESENT" in cb, "callback:present-only-arm", failures)
    need("LORIE_GATEA_XOP_COPYAREA" not in cb, "callback:no-copyarea-arm", failures)

    present_wait = extract_fn(io, "void lorieGpuCopyWaitForPresentOrFatal")
    need("lorieGpuCopyWait(serial, 2000)" in present_wait, "present:wait-2000", failures)
    need('gateAXFatal("x-present-copy-wait", LORIE_GATEA_FAIL_TIMEOUT' in present_wait,
         "present:timeout-4-kept", failures)
    need("elapsed > 3000" not in io and "elapsed > 5000" not in io, "init:wait-not-widened", failures)
    need("usleep(200)" in io, "init:usleep-200", failures)
    need("usleep(2000)" not in io, "init:no-usleep-2000", failures)

    copy_wait = extract_fn(io, "static Bool lorieGpuCopyWait")
    need("lorieGateAClassifyWaitWake" in copy_wait, "copy-wait:uses-wake", failures)
    need('gateAXFatal("x-hup", LORIE_GATEA_FAIL_GENERATION, serial)' in copy_wait,
         "copy-wait:x-hup-6", failures)
    need("LORIE_GATEA_WAIT_WAKE_CONTINUE" in copy_wait, "copy-wait:continue-class", failures)
    need("LORIE_GATEA_WAIT_WAKE_TIMEOUT" in w, "wake:timeout-class-kept", failures)

    need("lorieTryScheduleGpuPresentCopy" in p, "patch:present-copy-call", failures)
    need("lorieTryScheduleGpuCopy(vblank->pixmap" not in p,
         "patch:no-legacy-present-copy-call", failures)
    need("extern Bool lorieTryScheduleGpuPresentCopy" in p, "patch:present-copy-decl", failures)

    apply_fn = extract_fn(r, "LorieGateABatchOut Renderer::applyPendingGpuCopiesLocked")
    hold_apply = extract_fn(r, "void Renderer::applyPendingGpuCopies")
    need("LORIE_GATEA_TEST_PRESENT_HOLD_COMPLETE" in hold_apply, "renderer:hold-apply", failures)
    need("LORIE_GATEA_TEST_PRESENT_HOLD_COMPLETE" in r, "renderer:hold-redraw", failures)
    need("LORIE_GATEA_TEST_PRESENT_RENDERER_EXIT" in apply_fn, "renderer:enum13-copy-op", failures)
    need("PublishCompleted" in hold_apply, "renderer:publish-after-hold-check", failures)
    hold_at = hold_apply.find("LORIE_GATEA_TEST_PRESENT_HOLD_COMPLETE")
    pub_completed = hold_apply.find("lorieGateAPublishCompleted", hold_at)
    need(pub_completed > hold_at, "renderer:withhold-before-completed", failures)
    need("lorieGateATestFaultConsume" in hold_apply, "renderer:consume-hook", failures)

    wake_fn = extract_fn(w, "lorieGateAClassifyWaitWake")
    need("LORIE_GATEA_WAIT_WAKE_X_HUP" in wake_fn, "wake:x-hup", failures)
    need("LORIE_GATEA_FAIL_GENERATION" in wake_fn, "wake:reason-6", failures)
    need("LORIE_GATEA_FAIL_TIMEOUT" in wake_fn, "wake:reason-4", failures)
    need("5000" not in w and "10000" not in w, "wake:no-timeout-inflation", failures)

    need("early CopyArea" in t or "CopyArea serial" in t, "host:early-copyarea", failures)
    need("consumed == 0" in t, "host:no-early-consume", failures)
    need("ArmPresentTarget" in t, "host:arm-api", failures)
    need("Consume" in t, "host:consume-api", failures)
    need("HoldsIncomplete" in t, "host:hold-api", failures)
    need("exactly once" in t or "consumed == 1" in t, "host:oneshot", failures)
    need("different serial" in t, "host:unrelated-copy", failures)
    need("later T=8 must NOT satisfy S" in t or "later T=8" in t, "host:hold-watermark", failures)
    need("unarmed later T>S covers S" in t or "unarmed later" in t, "host:unarmed-watermark", failures)
    need("WAIT_WAKE_X_HUP" in t, "host:r7-10-hup", failures)
    need("WAIT_WAKE_TIMEOUT" in t, "host:live-timeout", failures)
    need("FAIL_GENERATION" in t, "host:reason-6", failures)
    need("FAIL_TIMEOUT" in t, "host:reason-4", failures)
    need("cell 13" in t or "PRESENT_RENDERER_EXIT" in t, "host:enum13-arm", failures)
    need("does not hold Present completion" in t, "host:enum13-no-hold", failures)

    need(not judge_in_tree.is_file(), "judge:not-in-repair-tree", failures)
    need(not frozen_runner.is_file(), "hist-runner:not-copied-into-tree", failures)
    need(not frozen_clf.is_file(), "hist-classifier:not-copied-into-tree", failures)
    need(evidence_judge.is_file(), "judge:frozen-exists", failures)
    if evidence_judge.is_file():
        got = sha256_file(evidence_judge)
        need(got == JUDGE_SHA256, f"judge:sha256-frozen ({got})", failures)
        judge_txt = evidence_judge.read_text()
        need("present-hold-complete" in judge_txt, "judge:p1-cell-kept", failures)
        need("x-present-copy-wait" in judge_txt, "judge:p1-halt-kept", failures)
    need(hist_runner.is_file(), "hist-runner:frozen-exists", failures)
    need(hist_cell.is_dir(), "hist-cell:frozen-exists", failures)
    need("sleep before" not in t.lower(), "host:no-sleep-fix", failures)
    need("InputThreadPreInit" not in io, "init:no-inputthread-preinit", failures)
    return failures


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_r7_p1_present_target_arm.py WORKTREE")
        return 2
    failures = verify(Path(sys.argv[1]))
    if failures:
        print("R7_P1_PRESENT_TARGET_ARM=FAIL")
        for item in failures:
            print(f"FAIL {item}")
        return 1
    print("R7_P1_PRESENT_TARGET_ARM=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
