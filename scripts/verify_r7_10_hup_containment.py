#!/usr/bin/env python3
"""Static gate: direct/present wait contains peer HUP as x-hup/6.

R7-10: renderer _exit after CONSUME_DIRECT with generationFatal==0 must not
become x-direct-not-success/4. Wait observes AF_UNIX HUP via
lorieConnectionAlive and fail-stops x-hup / FAIL_GENERATION. Published
generationFatal still wins. Genuine live-peer timeout stays FAIL_TIMEOUT.
No ABI / timeout / judge / runner / event-enum change.
"""
from __future__ import annotations

import re
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


def extract_if_block(text: str, token: str) -> str:
    start = text.find(token)
    if start < 0:
        raise ValueError(f"missing {token}")
    brace = text.find("{", start)
    if brace < 0:
        raise ValueError(f"missing body for {token}")
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
    raise ValueError(f"unbalanced {token}")


def enum_value(text: str, name: str) -> int | None:
    m = re.search(rf"{re.escape(name)}\s*=\s*(\d+)", text)
    return int(m.group(1)) if m else None


def verify(repo: Path) -> list[str]:
    failures: list[str] = []
    hdr = repo / "lorie/src/main/cpp/lorie/lorie.h"
    wake_h = repo / "lorie/src/main/cpp/lorie/lorie_gatea_wait_wake_class.h"
    done_h = repo / "lorie/src/main/cpp/lorie/lorie_gatea_done_class.h"
    init = repo / "lorie/src/main/cpp/lorie/InitOutput.c"
    renderer = repo / "lorie/src/main/cpp/lorie/renderer.cpp"
    cmd = repo / "lorie/src/main/cpp/lorie/cmdentrypoint.cpp"
    host = repo / "scripts/test_gatea_wait_wake_class.c"
    judge = repo / "scripts/judge-r7.py"
    need(hdr.is_file(), "hdr:exists", failures)
    need(wake_h.is_file(), "wake:exists", failures)
    need(done_h.is_file(), "done:exists", failures)
    need(init.is_file(), "init:exists", failures)
    need(renderer.is_file(), "renderer:exists", failures)
    need(cmd.is_file(), "cmd:exists", failures)
    need(host.is_file(), "host:exists", failures)
    if failures:
        return failures
    h = hdr.read_text()
    w = wake_h.read_text()
    d = done_h.read_text()
    io = init.read_text()
    r = renderer.read_text()
    x = cmd.read_text()
    t = host.read_text()

    need('#include "lorie_gatea_wait_wake_class.h"' in h, "hdr:includes-wake", failures)
    need("lorieGateAClassifyWaitWake" in w, "wake:fn", failures)
    need("LORIE_GATEA_WAIT_WAKE_X_HUP" in w, "wake:x-hup", failures)
    need("LORIE_GATEA_WAIT_WAKE_TIMEOUT" in w, "wake:timeout", failures)
    need("LORIE_GATEA_WAIT_WAKE_PRESERVE_FATAL" in w, "wake:preserve", failures)
    need("LORIE_GATEA_WAIT_WAKE_SURFACE_LOSS" in w, "wake:surface", failures)
    need("LORIE_GATEA_FAIL_GENERATION" in w, "wake:reason-6", failures)
    need("LORIE_GATEA_FAIL_TIMEOUT" in w, "wake:reason-4", failures)
    wake_fn = extract_fn(w, "lorieGateAClassifyWaitWake")
    hup_ret = wake_fn.find("return LORIE_GATEA_WAIT_WAKE_X_HUP")
    timeout_ret = wake_fn.find("return LORIE_GATEA_WAIT_WAKE_TIMEOUT")
    preserve_ret = wake_fn.find("return LORIE_GATEA_WAIT_WAKE_PRESERVE_FATAL")
    surface_ret = wake_fn.find("return LORIE_GATEA_WAIT_WAKE_SURFACE_LOSS")
    need(hup_ret >= 0 and preserve_ret >= 0 and hup_ret > preserve_ret,
         "wake:preserve-before-x-hup", failures)
    need("!connectionAlive" in wake_fn, "wake:connection-first", failures)
    need(timeout_ret > hup_ret, "wake:hup-before-timeout", failures)
    need(surface_ret > hup_ret and surface_ret < timeout_ret,
         "wake:surface-not-hup", failures)
    need("5000" not in w and "10000" not in w, "wake:no-timeout-inflation", failures)

    need(enum_value(h, "LORIE_GATEA_FAIL_TIMEOUT") == 4, "abi:fail-timeout", failures)
    need(enum_value(h, "LORIE_GATEA_FAIL_GENERATION") == 6, "abi:fail-generation", failures)
    need(enum_value(t, "LORIE_GATEA_FAIL_TIMEOUT") == 4, "host:fail-timeout", failures)
    need(enum_value(t, "LORIE_GATEA_FAIL_GENERATION") == 6, "host:fail-generation", failures)
    need('sizeof(struct LorieGateAProtocol) == 40' in h, "abi:protocol-40", failures)
    need("offsetof(struct LorieGateAProtocol, generationFatal) == 4" in h,
         "abi:fatal-off-4", failures)
    need("#define LORIE_GATEA_PROTOCOL_VERSION 1u" in h, "abi:protocol-version-1", failures)
    need('LORIE_GATEA_STATIC_ASSERT(LORIE_GATEA_EVENT_MAX == 37' in h, "abi:event-max-37", failures)
    need('LORIE_GATEA_STATIC_ASSERT(LORIE_GATEA_COUNTER_MAX == 28' in h, "abi:counter-max-28", failures)
    need("LORIE_GATEA_TEST_RENDERER_EXIT_AFTER_CONSUME = 10" in h, "abi:fault-enum-10", failures)
    need("LORIE_GATEA_TEST_SERIAL_WRAP = 11" in h, "abi:fault-enum-tail", failures)
    need("#define LORIE_GATEA_FENCE_TIMEOUT_NS 2000000000ull" in h, "hdr:fence-2000", failures)
    need("#define LORIE_RENDERER_FRAME_WAIT_NS 8000000L" in h, "hdr:8ms", failures)
    need("elapsed > 2000" in io, "init:wait-2000", failures)
    need("elapsed > 3000" not in io and "elapsed > 5000" not in io, "init:wait-not-widened", failures)
    need("lorieGpuCopyWait(serial, 2000)" in io, "init:copy-wait-2000", failures)
    need("lorieGpuCopyWait(serial, 3000)" not in io, "init:not-3000", failures)
    need("usleep(200)" in io, "init:usleep-200", failures)
    need("usleep(2000)" not in io, "init:no-usleep-2000", failures)

    wait = extract_fn(io, "static LorieGateAResult gateAWaitTerminal")
    derive_at = wait.find("lorieGateADeriveResult")
    wake_at = wait.find("lorieGateAClassifyWaitWake")
    none_at = wait.find("if (r != LORIE_GATEA_RESULT_NONE)")
    hup_at = wait.find('gateAXFatal("x-hup"')
    timeout_at = wait.find("elapsed > 2000")
    retry_at = wait.find("goto")
    need(derive_at >= 0 and wake_at >= 0 and derive_at < wake_at,
         "wait:derive-before-wake", failures)
    need(none_at >= 0 and none_at < wake_at, "wait:return-non-none-before-wake", failures)
    need(hup_at >= 0 and hup_at > wake_at, "wait:x-hup-after-wake", failures)
    need("LORIE_GATEA_FAIL_GENERATION" in wait, "wait:hup-reason-6", failures)
    need(timeout_at > wake_at or "elapsed > 2000" in wait, "wait:timeout-still-2000", failures)
    need("LORIE_GATEA_WAIT_WAKE_TIMEOUT" in wait, "wait:timeout-class-used", failures)
    need("LORIE_GATEA_WAIT_WAKE_X_HUP" in wait, "wait:hup-class-used", failures)
    need("LORIE_GATEA_WAIT_WAKE_PRESERVE_FATAL" in wait, "wait:preserve-class-used", failures)
    need(retry_at < 0, "wait:no-goto-retry", failures)
    need(re.search(r'(?<!u)sleep\s*\(', wait) is None, "wait:no-sleep-delay", failures)

    copy_wait = extract_fn(io, "static Bool lorieGpuCopyWait")
    need("lorieGateAClassifyWaitWake" in copy_wait, "copy-wait:uses-wake", failures)
    need('gateAXFatal("x-hup", LORIE_GATEA_FAIL_GENERATION, serial)' in copy_wait,
         "copy-wait:x-hup", failures)
    need("LORIE_GATEA_WAIT_WAKE_PRESERVE_FATAL" in copy_wait, "copy-wait:preserve", failures)
    need("elapsed > timeout_ms" in copy_wait, "copy-wait:timeout-arg", failures)
    need("usleep(200)" in copy_wait, "copy-wait:usleep-200", failures)

    present = extract_fn(io, "void lorieGpuCopyWaitForPresentOrFatal")
    need("lorieGpuCopyWait(serial, 2000)" in present, "present:wait-2000", failures)
    need('gateAXFatal("x-present-copy-wait", LORIE_GATEA_FAIL_TIMEOUT' in present,
         "present:genuine-timeout-kept", failures)

    done = extract_fn(io, "static void gateADoneDirect")
    need('gateAXFatal("x-direct-not-success"' in done, "done:timeout-path-kept", failures)
    need("lorieGateAClassifyDirectDone" in done, "done:still-classifies", failures)
    need("LORIE_GATEA_DONE_SUCCESS" in done or "cls != LORIE_GATEA_DONE_SUCCESS" in done,
         "done:success-only-release", failures)

    need("LORIE_GATEA_DONE_TIMEOUT" in d, "done-class:timeout-kept", failures)
    need("publishedFatal != 0" in d, "done-class:preserve-kept", failures)

    apply_fn = extract_fn(r, "LorieGateABatchOut Renderer::applyPendingGpuCopiesLocked")
    fault_at = apply_fn.find("LORIE_GATEA_TEST_RENDERER_EXIT_AFTER_CONSUME")
    need(fault_at >= 0, "fault:consume-hook", failures)
    if fault_at >= 0:
        exit_at = apply_fn.find("_exit(127)", fault_at)
        need(exit_at > fault_at, "fault:exit-after-hook", failures)
        hook = apply_fn[fault_at:exit_at + len("_exit(127)")]
        need("r-exit-after-consume" in hook, "fault:dump-where", failures)
        need("gateARendererFatal" not in hook, "fault:no-renderer-fatal", failures)
        need("lorieGateAPublishFatal" not in hook, "fault:no-publish-fatal", failures)
        need("completedSerial" not in hook, "fault:no-completed", failures)

    need('gateAFatalFromInput(LORIE_GATEA_FAIL_GENERATION, "x-hup")' in x,
         "x:proto-x-hup-kept", failures)
    need('lorieGateAFatalHalt("x-hup", LORIE_GATEA_FAIL_GENERATION)' in x,
         "x:legacy-x-hup-kept", failures)
    proto = extract_fn(x, "static void handleLorieEventsProto(int fd, int ready)")
    err = extract_if_block(proto, "if (ready & X_NOTIFY_ERROR)")
    need('gateAFatalFromInput(LORIE_GATEA_FAIL_GENERATION, "x-hup")' in err,
         "x-proto:error-still-xhup", failures)

    alive = extract_fn(x, "bool lorieConnectionAlive(void)")
    need("poll(" in alive, "alive:poll", failures)
    need("POLLHUP" in alive and "POLLRDHUP" in alive, "alive:hup-bits", failures)
    need(", 0)" in alive, "alive:timeout-0", failures)

    need("lorieGateAClassifyWaitWake" in t, "host:calls-production-fn", failures)
    need("LORIE_GATEA_WAIT_WAKE_X_HUP" in t, "host:hup-case", failures)
    need("LORIE_GATEA_WAIT_WAKE_PRESERVE_FATAL" in t, "host:preserve-case", failures)
    need("LORIE_GATEA_WAIT_WAKE_TIMEOUT" in t, "host:timeout-case", failures)
    need("LORIE_GATEA_WAIT_WAKE_CONTINUE" in t, "host:continue-case", failures)
    need("LORIE_GATEA_FAIL_GENERATION" in t, "host:reason-6", failures)
    need("LORIE_GATEA_FAIL_TIMEOUT" in t, "host:reason-4", failures)

    need("x-direct-not-success/4" in t or "reason=4" in t or "FAIL_TIMEOUT" in t,
         "host:mentions-timeout-kept", failures)
    need("InputThreadPreInit" not in io, "init:no-inputthread-preinit", failures)
    need("judge-r7.py" not in io, "init:no-judge-embed", failures)
    if judge.is_file():
        failures.append("judge:must-remain-outside-repair-tree")
    return failures


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_r7_10_hup_containment.py WORKTREE")
        return 2
    failures = verify(Path(sys.argv[1]))
    if failures:
        print("R7_10_HUP_CONTAINMENT=FAIL")
        for item in failures:
            print(f"FAIL {item}")
        return 1
    print("R7_10_HUP_CONTAINMENT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
