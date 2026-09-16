#!/usr/bin/env python3
"""Static gate: redrawLocked has observe-only swap/next-buffer stall markers.

Claim: the four STALL_PHASE boundaries wrap eglSwapBuffers and the post-swap
next-buffer eglClientWaitSync(EGL_FOREVER) without changing those calls'
arguments, control flow, timeout, or other EGL wait sites.
"""
from __future__ import annotations

import sys
from pathlib import Path


def need(cond: bool, label: str, failures: list[str]) -> None:
    if not cond:
        failures.append(label)


def matching_brace(text: str, open_idx: int) -> int:
    depth = 0
    i = open_idx
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("unbalanced brace")


def extract_function(text: str, sig: str) -> str:
    start = text.find(sig)
    if start < 0:
        raise ValueError(f"missing {sig}")
    brace = text.find("{", start)
    if brace < 0:
        raise ValueError(f"missing body for {sig}")
    return text[start : matching_brace(text, brace) + 1]


def verify(repo: Path) -> list[str]:
    failures: list[str] = []
    renderer = repo / "lorie/src/main/cpp/lorie/renderer.cpp"
    init = repo / "lorie/src/main/cpp/lorie/InitOutput.c"
    need(renderer.is_file(), "renderer:exists", failures)
    need(init.is_file(), "init:exists", failures)
    if failures:
        return failures

    raw = renderer.read_text()
    init_text = init.read_text()

    need("lorieGpuCopyWait(serial, 2000)" in init_text, "wait:timeout-still-2000", failures)
    need("lorieGpuCopyWait(serial, 3000)" not in init_text, "wait:not-3000", failures)

    try:
        body = extract_function(raw, "void Renderer::redrawLocked(bool* waitingForBuffers)")
    except ValueError as exc:
        failures.append(f"redraw:extract:{exc}")
        return failures

    enter_swap = body.find('stallPhaseLog(state, "SWAP_ENTER", nullptr)')
    swap_call = body.find("swap_result = eglSwapBuffers(egl_display, sfc);")
    exit_swap = body.find('stallPhaseLog(state, "SWAP_EXIT", &swap_result_i)')
    err_swap = body.find('if (swap_result != EGL_TRUE)')
    err_print = body.find('printEglError("Failed to swap buffers"')
    enter_fence = body.find('stallPhaseLog(state, "NEXT_FENCE_ENTER", nullptr)')
    fence_call = body.find(
        "wait_result = lorieEglClientWaitSyncKHR(egl_display, fence, "
        "EGL_SYNC_FLUSH_COMMANDS_BIT_KHR, EGL_FOREVER);"
    )
    exit_fence = body.find('stallPhaseLog(state, "NEXT_FENCE_EXIT", &wait_result_i)')
    err_fence = body.find("if (wait_result != EGL_CONDITION_SATISFIED_KHR)", fence_call)

    need(enter_swap >= 0, "redraw:SWAP_ENTER", failures)
    need(swap_call >= 0, "redraw:swap-call", failures)
    need(exit_swap >= 0, "redraw:SWAP_EXIT", failures)
    need(err_swap >= 0 and err_print >= 0, "redraw:swap-error-path", failures)
    need(enter_fence >= 0, "redraw:NEXT_FENCE_ENTER", failures)
    need(fence_call >= 0, "redraw:next-fence-call-forever", failures)
    need(exit_fence >= 0, "redraw:NEXT_FENCE_EXIT", failures)
    need(err_fence >= 0, "redraw:next-fence-error-path", failures)
    need(
        enter_swap < swap_call < exit_swap < err_swap < err_print
        < enter_fence < fence_call < exit_fence < err_fence,
        "redraw:marker-order",
        failures,
    )

    # Only this site is wrapped. Other swaps and the GPU-copy fence stay bare.
    need(raw.count('eglSwapBuffers(egl_display, sfc)') == 2, "swap:sfc-sites", failures)
    need(raw.count("eglSwapBuffers(egl_display, *esfc)") == 1, "swap:release-unwrapped", failures)
    need(
        raw.count("lorieEglClientWaitSyncKHR(egl_display, fence, 0, EGL_FOREVER)") == 1,
        "copy-fence:forever-unwrapped",
        failures,
    )
    need(raw.count('stallPhaseLog(state, "SWAP_ENTER", nullptr)') == 1,
         "string:SWAP_ENTER-once", failures)
    need(raw.count('stallPhaseLog(state, "SWAP_EXIT", &swap_result_i)') == 1,
         "string:SWAP_EXIT-once", failures)
    need(raw.count('stallPhaseLog(state, "NEXT_FENCE_ENTER", nullptr)') == 1,
         "string:NEXT_FENCE_ENTER-once", failures)
    need(raw.count('stallPhaseLog(state, "NEXT_FENCE_EXIT", &wait_result_i)') == 1,
         "string:NEXT_FENCE_EXIT-once", failures)
    need(raw.count("STALL_PHASE phase=%s") == 2, "string:STALL_PHASE-fmt", failures)
    need("CLOCK_MONOTONIC" in raw, "clock:monotonic", failures)
    need("gettid()" in raw, "tid:gettid", failures)
    need("lorieGateAObserveCompleted" in extract_function(
        raw, "static void stallPhaseLog("
    ), "fields:completedSerial", failures)
    return failures


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_stall_phase_markers.py WORKTREE")
        return 2
    failures = verify(Path(sys.argv[1]))
    if failures:
        print("STALL_PHASE_MARKERS=FAIL")
        for item in failures:
            print(f"FAIL {item}")
        return 1
    print("STALL_PHASE_MARKERS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
