#!/usr/bin/env python3
"""Static gate: redrawLocked has observe-only stall markers.

Claims:
1. SWAP / NEXT_FENCE still wrap eglSwapBuffers and the post-swap
   next-buffer eglClientWaitSync(EGL_FOREVER) without changing those
   calls' arguments, control flow, timeout, or other EGL wait sites.
2. NOTIFY_ENTER / NOTIFY_EXIT wrap the two mutually exclusive
   notifyGpuCopyDone() calls after GPU-copy completion and before
   SWAP_ENTER (legacy in-lock vs Gate A post-unlock). Other notify
   sites stay unwrapped. notifyGpuCopyDone itself is unchanged.
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
    activity = repo / "lorie/src/main/cpp/lorie/activity.cpp"
    need(renderer.is_file(), "renderer:exists", failures)
    need(init.is_file(), "init:exists", failures)
    need(activity.is_file(), "activity:exists", failures)
    if failures:
        return failures

    raw = renderer.read_text()
    init_text = init.read_text()
    activity_text = activity.read_text()

    need("lorieGpuCopyWait(serial, 2000)" in init_text, "wait:timeout-still-2000", failures)
    need("lorieGpuCopyWait(serial, 3000)" not in init_text, "wait:not-3000", failures)

    try:
        body = extract_function(raw, "void Renderer::redrawLocked(bool* waitingForBuffers)")
        notify_fn = extract_function(raw, "static void notifyGpuCopyDone()")
        apply_fn = extract_function(raw, "void Renderer::applyPendingGpuCopies()")
        refresh_fn = extract_function(raw, "void Renderer::refreshContext()")
        log_fn = extract_function(raw, "static void stallPhaseLog(")
    except ValueError as exc:
        failures.append(f"extract:{exc}")
        return failures

    need(
        "lorieEvent e = { .type = EVENT_GPU_COPY_DONE };" in notify_fn
        and "(void)lorieActivitySendLegacyRecord(&e);" in notify_fn
        and "stallPhaseLog" not in notify_fn
        and "O_NONBLOCK" not in notify_fn
        and "fcntl" not in notify_fn,
        "notify:fn-unchanged",
        failures,
    )
    need(notify_fn.count("notifyGpuCopyDone") == 1, "notify:fn-no-recurse", failures)

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

    legacy_enter = body.find('stallPhaseLog(state, "NOTIFY_ENTER", nullptr)')
    legacy_call = body.find("notifyGpuCopyDone();", legacy_enter)
    legacy_exit = body.find('stallPhaseLog(state, "NOTIFY_EXIT", nullptr)', legacy_call)
    unlock = body.find("lorie_mutex_unlock(&state->lock, &state->lockingPid);", legacy_exit)
    gatea_enter = body.find('stallPhaseLog(state, "NOTIFY_ENTER", nullptr)', unlock)
    gatea_call = body.find("notifyGpuCopyDone();", gatea_enter)
    gatea_exit = body.find('stallPhaseLog(state, "NOTIFY_EXIT", nullptr)', gatea_call)

    need(legacy_enter >= 0, "redraw:legacy-NOTIFY_ENTER", failures)
    need(legacy_call >= 0, "redraw:legacy-notify-call", failures)
    need(legacy_exit >= 0, "redraw:legacy-NOTIFY_EXIT", failures)
    need(unlock >= 0, "redraw:unlock-after-legacy-notify", failures)
    need(gatea_enter >= 0, "redraw:gatea-NOTIFY_ENTER", failures)
    need(gatea_call >= 0, "redraw:gatea-notify-call", failures)
    need(gatea_exit >= 0, "redraw:gatea-NOTIFY_EXIT", failures)
    need("if (!gpuCopyOut.gateASeen)" in body[max(0, legacy_enter - 80):legacy_enter],
         "redraw:legacy-guard", failures)
    need("if (gateANotify)" in body[max(0, gatea_enter - 80):gatea_enter],
         "redraw:gatea-guard", failures)
    need("result=" not in body[legacy_enter:legacy_exit + 40],
         "redraw:legacy-notify-omit-result", failures)
    need("result=" not in body[gatea_enter:gatea_exit + 40],
         "redraw:gatea-notify-omit-result", failures)

    need(
        legacy_enter < legacy_call < legacy_exit < unlock < gatea_enter
        < gatea_call < gatea_exit < enter_swap < swap_call < exit_swap
        < err_swap < err_print < enter_fence < fence_call < exit_fence
        < err_fence,
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
    need(raw.count('stallPhaseLog(state, "NOTIFY_ENTER", nullptr)') == 2,
         "string:NOTIFY_ENTER-twice-exclusive", failures)
    need(raw.count('stallPhaseLog(state, "NOTIFY_EXIT", nullptr)') == 2,
         "string:NOTIFY_EXIT-twice-exclusive", failures)
    need(body.count("notifyGpuCopyDone();") == 2, "redraw:notify-two-sites", failures)
    need(apply_fn.count("notifyGpuCopyDone();") == 2, "apply:notify-unwrapped-count", failures)
    need("NOTIFY_" not in apply_fn, "apply:no-notify-markers", failures)
    need(refresh_fn.count("notifyGpuCopyDone();") == 2, "refresh:notify-unwrapped-count", failures)
    need("NOTIFY_" not in refresh_fn, "refresh:no-notify-markers", failures)
    need(raw.count("notifyGpuCopyDone();") == 6, "notify:six-call-sites", failures)
    need("COND_WAIT" not in raw, "no:cond-wait-markers", failures)
    need("O_NONBLOCK" not in raw, "socket:no-nonblock-renderer", failures)
    try:
        write_locked = extract_function(activity_text, "static int lorieActivityWriteLocked(")
        send_legacy = extract_function(activity_text, "int lorieActivitySendLegacyRecord(")
        send_payload = extract_function(activity_text, "int lorieActivitySendLegacyPayload(")
    except ValueError as exc:
        failures.append(f"activity-extract:{exc}")
        return failures

    need("O_NONBLOCK" not in write_locked, "socket:write-locked-no-nonblock", failures)
    need("fcntl" not in write_locked, "socket:write-locked-no-fcntl", failures)
    need("O_NONBLOCK" not in send_legacy, "socket:send-legacy-no-nonblock", failures)
    need("O_NONBLOCK" not in send_payload, "socket:send-payload-no-nonblock", failures)
    need("lorieGateAWriteFull" in write_locked, "socket:write-full-present", failures)
    need("pthread_mutex_lock(&lorieActivityWriterMutex)" in send_payload,
         "socket:writer-mutex-lock", failures)
    need("lorieActivityWriterMutex" in activity_text, "socket:writer-mutex-present", failures)
    need("STALL_PHASE phase=%s" in raw, "string:STALL_PHASE-fmt", failures)
    need(raw.count("STALL_PHASE phase=%s") == 2, "string:STALL_PHASE-fmt-count", failures)
    need("CLOCK_MONOTONIC" in raw, "clock:monotonic", failures)
    need("gettid()" in raw, "tid:gettid", failures)
    need("lorieGateAObserveCompleted" in log_fn, "fields:completedSerial", failures)
    need("lorieGateAObserveReadIndex" in log_fn, "fields:readIndex", failures)
    need("lorieGateAObserveWriteIndex" in log_fn, "fields:writeIndex", failures)
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
