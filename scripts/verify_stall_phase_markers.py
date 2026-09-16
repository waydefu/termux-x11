#!/usr/bin/env python3
"""Static gate: stall-phase markers with function-body NOTIFY coverage.

Claims:
1. SWAP / NEXT_FENCE still wrap eglSwapBuffers and the post-swap
   next-buffer eglClientWaitSync(EGL_FOREVER) without changing those
   calls' arguments, control flow, timeout, or other EGL wait sites.
2. NOTIFY_ENTER / NOTIFY_EXIT live inside notifyGpuCopyDone() and
   surround the existing EVENT_GPU_COPY_DONE / SendLegacyRecord body.
   Every source caller reaches that one function. Call-site wrapping
   is forbidden (one logical pair per real invocation).
3. Socket/write/mutex/timeout/ABI semantics are unchanged.
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
    start = 0
    while True:
        found = text.find(sig, start)
        if found < 0:
            raise ValueError(f"missing {sig}")
        brace = text.find("{", found)
        if brace < 0:
            raise ValueError(f"missing body for {sig}")
        semi = text.find(";", found)
        if 0 <= semi < brace:
            start = semi + 1
            continue
        return text[found : matching_brace(text, brace) + 1]


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
        notify_fn = extract_function(raw, "static void notifyGpuCopyDone(")
        apply_fn = extract_function(raw, "void Renderer::applyPendingGpuCopies()")
        refresh_fn = extract_function(raw, "void Renderer::refreshContext()")
        log_fn = extract_function(raw, "static void stallPhaseLog(")
    except ValueError as exc:
        failures.append(f"extract:{exc}")
        return failures

    if "NOTIFY_ENTER" not in notify_fn or "NOTIFY_EXIT" not in notify_fn:
        failures.append("notify:function-body-not-instrumented")
        return failures

    enter = notify_fn.find('stallPhaseLog(st, "NOTIFY_ENTER", nullptr)')
    event = notify_fn.find("lorieEvent e = { .type = EVENT_GPU_COPY_DONE };")
    send = notify_fn.find("(void)lorieActivitySendLegacyRecord(&e);")
    exit_m = notify_fn.find('stallPhaseLog(st, "NOTIFY_EXIT", nullptr)')
    need(enter >= 0, "notify:function-body-NOTIFY_ENTER", failures)
    need(event >= 0, "notify:body-event", failures)
    need(send >= 0, "notify:body-send-legacy", failures)
    need(exit_m >= 0, "notify:function-body-NOTIFY_EXIT", failures)
    need(
        0 <= enter < event < send < exit_m,
        "notify:function-body-surround",
        failures,
    )
    need(notify_fn.count("NOTIFY_ENTER") == 1, "notify:one-ENTER", failures)
    need(notify_fn.count("NOTIFY_EXIT") == 1, "notify:one-EXIT", failures)
    need("O_NONBLOCK" not in notify_fn, "notify:no-nonblock", failures)
    need("fcntl" not in notify_fn, "notify:no-fcntl", failures)
    need("sleep" not in notify_fn, "notify:no-sleep", failures)
    need("pthread_mutex_lock" not in notify_fn, "notify:no-extra-mutex", failures)
    need(notify_fn.count("notifyGpuCopyDone") == 1, "notify:fn-no-recurse", failures)
    need(
        "struct lorie_shared_server_state *st" in notify_fn.split("{", 1)[0],
        "notify:st-arg-observe-only",
        failures,
    )

    enter_swap = body.find('stallPhaseLog(state, "SWAP_ENTER", nullptr)')
    swap_call = body.find("swap_result = eglSwapBuffers(egl_display, sfc);")
    exit_swap = body.find('stallPhaseLog(state, "SWAP_EXIT", &swap_result_i)')
    err_swap = body.find("if (swap_result != EGL_TRUE)")
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

    legacy_guard = body.find("if (!gpuCopyOut.gateASeen)")
    legacy_call = body.find("notifyGpuCopyDone(state);", legacy_guard)
    unlock = body.find("lorie_mutex_unlock(&state->lock, &state->lockingPid);", legacy_call)
    gatea_guard = body.find("if (gateANotify)", unlock)
    gatea_call = body.find("notifyGpuCopyDone(state);", gatea_guard)

    need(legacy_guard >= 0, "redraw:legacy-guard", failures)
    need(legacy_call >= 0, "redraw:legacy-notify-call", failures)
    need(unlock >= 0, "redraw:unlock-after-legacy-notify", failures)
    need(gatea_guard >= 0, "redraw:gatea-guard", failures)
    need(gatea_call >= 0, "redraw:gatea-notify-call", failures)
    need("NOTIFY_" not in body, "redraw:no-callsite-NOTIFY", failures)
    need(
        legacy_guard < legacy_call < unlock < gatea_guard < gatea_call
        < enter_swap < swap_call < exit_swap < err_swap < err_print
        < enter_fence < fence_call < exit_fence < err_fence,
        "redraw:marker-order",
        failures,
    )

    need(raw.count("eglSwapBuffers(egl_display, sfc)") == 2, "swap:sfc-sites", failures)
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
    need(raw.count('stallPhaseLog(st, "NOTIFY_ENTER", nullptr)') == 1,
         "string:NOTIFY_ENTER-once-in-fn", failures)
    need(raw.count('stallPhaseLog(st, "NOTIFY_EXIT", nullptr)') == 1,
         "string:NOTIFY_EXIT-once-in-fn", failures)
    need(raw.count('stallPhaseLog(state, "NOTIFY_ENTER"') == 0,
         "string:no-caller-NOTIFY_ENTER", failures)
    need(raw.count('stallPhaseLog(state, "NOTIFY_EXIT"') == 0,
         "string:no-caller-NOTIFY_EXIT", failures)
    need(body.count("notifyGpuCopyDone(state);") == 2, "redraw:notify-two-sites", failures)
    need(apply_fn.count("notifyGpuCopyDone(state);") == 2, "apply:notify-two-sites", failures)
    need("NOTIFY_" not in apply_fn, "apply:no-notify-markers", failures)
    need(refresh_fn.count("notifyGpuCopyDone(state);") == 2, "refresh:notify-two-sites", failures)
    need("NOTIFY_" not in refresh_fn, "refresh:no-notify-markers", failures)
    need(raw.count("notifyGpuCopyDone(state);") == 6, "notify:six-call-sites", failures)
    need(raw.count("static void notifyGpuCopyDone(") == 1, "notify:one-definition", failures)
    need("notifyGpuCopyDone();" not in raw, "notify:no-zero-arg-calls", failures)
    need("if (!win)" in refresh_fn, "refresh:no-win-guard", failures)
    need("eglMakeCurrent failed" in refresh_fn, "refresh:makecurrent-fail-path", failures)
    need("if (gateANotify)" in apply_fn, "apply:gatea-guard", failures)
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
    need("lorieActivitySendLegacyRecord" in send_legacy, "socket:send-legacy-body", failures)
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
