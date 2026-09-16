#!/usr/bin/env python3
"""Static gate: CASE_LOOP renderer idle wait is bounded and EXA 2000 ms is unchanged."""
from __future__ import annotations

import sys
from pathlib import Path


def need(cond: bool, label: str, failures: list[str]) -> None:
    if not cond:
        failures.append(label)


def extract_fn(text: str, sig: str) -> str:
    start = text.find(sig)
    if start < 0:
        raise ValueError(f"missing {sig}")
    brace = text.find("{", start)
    if brace < 0:
        raise ValueError(f"missing body for {sig}")
    depth = 0
    i = brace
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    raise ValueError(f"unbalanced {sig}")


def verify(repo: Path) -> list[str]:
    failures: list[str] = []
    hdr = repo / "lorie/src/main/cpp/lorie/lorie.h"
    cpp = repo / "lorie/src/main/cpp/lorie/renderer.cpp"
    init = repo / "lorie/src/main/cpp/lorie/InitOutput.c"
    need(hdr.is_file(), "hdr:exists", failures)
    need(cpp.is_file(), "cpp:exists", failures)
    need(init.is_file(), "init:exists", failures)
    if failures:
        return failures
    h = hdr.read_text()
    r = cpp.read_text()
    c = init.read_text()

    need("#define LORIE_RENDERER_FRAME_WAIT_NS 8000000L" in h, "hdr:8ms-cap", failures)
    need("#define LORIE_GATEA_FENCE_TIMEOUT_NS 2000000000ull" in h, "hdr:fence-2000-unchanged", failures)
    need("lorieGpuCopyWait(serial, 2000)" in c, "init:wait-2000", failures)
    need("lorieGpuCopyWait(serial, 3000)" not in c, "init:not-3000", failures)
    need("lorieGpuCopyWaitForCompositeOrFatal" in c, "init:helper-kept", failures)

    try:
        idle = extract_fn(r, "void Renderer::waitWhileIdle(bool *waitingForBuffers)")
    except ValueError as exc:
        failures.append(f"idle:extract:{exc}")
        return failures
    need("LORIE_RENDERER_FRAME_WAIT_NS" in idle, "idle:uses-named-cap", failures)
    need("pthread_cond_timedwait(stateCond, &stateLock, &deadline)" in idle,
         "idle:timedwait", failures)
    need("ObserveWriteIndex" in idle, "idle:sticky-writeIndex", failures)
    need("waitForNextFrame" in idle, "idle:only-cap-vsync-wait", failures)
    need("pthread_cond_wait(stateCond, &stateLock)" in idle,
         "idle:infinite-wait-still-for-non-frame", failures)
    need("EGL_FOREVER" not in idle, "idle:no-egl-forever", failures)

    try:
        loop = extract_fn(r, "void Renderer::threadLoop()")
    except ValueError as exc:
        failures.append(f"loop:extract:{exc}")
        return failures
    need("waitWhileIdle(&waitingForBuffers)" in loop, "loop:calls-idle", failures)
    need("pthread_cond_wait(stateCond, &stateLock)" not in loop,
         "loop:no-bare-infinite-wait", failures)
    return failures


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_case_loop_wakeup.py WORKTREE")
        return 2
    failures = verify(Path(sys.argv[1]))
    if failures:
        print("CASE_LOOP_WAKEUP=FAIL")
        for item in failures:
            print(f"FAIL {item}")
        return 1
    print("CASE_LOOP_WAKEUP=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
