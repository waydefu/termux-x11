#!/usr/bin/env python3
"""Static gate: authoritative generationFatal is not rewritten as timeout.

R7-04: RESULT_FATAL + published FAIL_DRAW must preserve, not synthesize
FAIL_TIMEOUT / x-direct-not-success halt. Genuine published==0 timeout
still maps to FAIL_TIMEOUT. RESULT_FATAL never becomes SUCCESS / ACK / Done.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SUCCESS_TOKENS = (
    "lorieExaRepairDestXByteZero",
    "lorieGpuCopyAck",
    "LorieBuffer_gpuCopyPendingDec",
    'p2a2_emit("Gcomp Done")',
    "exaCompDone++",
    "LORIE_GATEA_EVENT_SEMANTIC_SUCCESS",
    "LORIE_GATEA_EVENT_ACK",
    "LORIE_GATEA_EVENT_PENDING_DEC",
    "LORIE_GATEA_EVENT_LEASE_RELEASE",
    "LORIE_GATEA_EVENT_REPAIR",
)


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
    cls_h = repo / "lorie/src/main/cpp/lorie/lorie_gatea_done_class.h"
    init = repo / "lorie/src/main/cpp/lorie/InitOutput.c"
    renderer = repo / "lorie/src/main/cpp/lorie/renderer.cpp"
    host = repo / "scripts/test_gatea_direct_done_class.c"
    need(hdr.is_file(), "hdr:exists", failures)
    need(cls_h.is_file(), "cls:exists", failures)
    need(init.is_file(), "init:exists", failures)
    need(renderer.is_file(), "renderer:exists", failures)
    need(host.is_file(), "host:exists", failures)
    if failures:
        return failures
    h = hdr.read_text()
    c = cls_h.read_text()
    io = init.read_text()
    r = renderer.read_text()
    t = host.read_text()

    need('#include "lorie_gatea_done_class.h"' in h, "hdr:includes-classifier", failures)
    need("lorieGateAClassifyDirectDone" in c, "cls:fn", failures)
    need("LORIE_GATEA_DONE_PRESERVE_FATAL" in c, "cls:preserve", failures)
    need("LORIE_GATEA_DONE_TIMEOUT" in c, "cls:timeout-class", failures)
    need("LORIE_GATEA_DONE_SUCCESS" in c, "cls:success-class", failures)
    need("LORIE_GATEA_FAIL_TIMEOUT" in c, "cls:timeout-reason", failures)

    need(enum_value(h, "LORIE_GATEA_RESULT_SUCCESS") == 1, "abi:result-success", failures)
    need(enum_value(h, "LORIE_GATEA_RESULT_FAILED_QUIESCED") == 2, "abi:result-quiesced", failures)
    need(enum_value(h, "LORIE_GATEA_RESULT_FATAL") == 3, "abi:result-fatal", failures)
    need(enum_value(h, "LORIE_GATEA_FAIL_DRAW") == 2, "abi:fail-draw", failures)
    need(enum_value(h, "LORIE_GATEA_FAIL_TIMEOUT") == 4, "abi:fail-timeout", failures)
    need(enum_value(t, "LORIE_GATEA_RESULT_FATAL") == 3, "host:result-fatal", failures)
    need(enum_value(t, "LORIE_GATEA_FAIL_DRAW") == 2, "host:fail-draw", failures)
    need(enum_value(t, "LORIE_GATEA_FAIL_TIMEOUT") == 4, "host:fail-timeout", failures)

    need('sizeof(struct LorieGateAProtocol) == 40' in h, "abi:protocol-40", failures)
    need("offsetof(struct LorieGateAProtocol, generationFatal) == 4" in h,
         "abi:fatal-off-4", failures)
    need("LORIE_GATEA_PROTOCOL_VERSION" in h, "abi:protocol-version-token", failures)
    need("#define LORIE_GATEA_FENCE_TIMEOUT_NS 2000000000ull" in h, "hdr:fence-2000", failures)
    need("#define LORIE_RENDERER_FRAME_WAIT_NS 8000000L" in h, "hdr:8ms", failures)

    cls_fn = extract_fn(c, "lorieGateAClassifyDirectDone")
    need("LORIE_GATEA_RESULT_SUCCESS" in cls_fn, "cls:success-input", failures)
    need("LORIE_GATEA_RESULT_FAILED_QUIESCED" in cls_fn, "cls:quiesced-input", failures)
    need("publishedFatal != 0" in cls_fn, "cls:published-nonzero", failures)
    need("LORIE_GATEA_DONE_PRESERVE_FATAL" in cls_fn, "cls:preserve-return", failures)
    need("LORIE_GATEA_DONE_TIMEOUT" in cls_fn, "cls:timeout-return", failures)
    need("LORIE_GATEA_DONE_SUCCESS" in cls_fn, "cls:success-return", failures)
    success_ret = cls_fn.find("return LORIE_GATEA_DONE_SUCCESS")
    fatal_ret = cls_fn.find("return LORIE_GATEA_DONE_PRESERVE_FATAL")
    timeout_ret = cls_fn.find("return LORIE_GATEA_DONE_TIMEOUT")
    need(success_ret >= 0 and fatal_ret >= 0 and timeout_ret >= 0
         and success_ret < fatal_ret < timeout_ret,
         "cls:success-then-preserve-then-timeout", failures)
    preserve_at = cls_fn.find("if (publishedFatal != 0)")
    need(preserve_at >= 0, "cls:published-if", failures)
    need("LORIE_GATEA_DONE_SUCCESS" not in cls_fn[preserve_at:],
         "cls:fatal-never-success", failures)

    done = extract_fn(io, "static void gateADoneDirect(PixmapPtr dst)")
    need("lorieGateAClassifyDirectDone" in done, "done:uses-classifier", failures)
    need("LORIE_GATEA_DONE_SUCCESS" in done, "done:success-gate", failures)
    need('gateAXFatal("x-direct-not-success"' in done, "done:quiesced-or-timeout-halt", failures)
    old = '(r == LORIE_GATEA_RESULT_FAILED_QUIESCED)'
    need(old not in done, "done:no-old-timeout-collapse", failures)
    need("(uint32_t)LORIE_GATEA_FAIL_TIMEOUT" not in done, "done:no-local-timeout-synth", failures)
    fail_if = done.find("if (cls != LORIE_GATEA_DONE_SUCCESS)")
    need(fail_if >= 0, "done:non-success-gate", failures)
    fail_block = extract_if_block(done, "if (cls != LORIE_GATEA_DONE_SUCCESS)")
    need("gateAXFatal" in fail_block, "done:fail-calls-xfatal", failures)
    for tok in SUCCESS_TOKENS:
        need(tok not in fail_block, f"done:fail-no-{tok}", failures)
    success_tail = done[fail_if + len(fail_block):]
    need("LORIE_GATEA_EVENT_SEMANTIC_SUCCESS" in success_tail, "done:success-after-gate", failures)
    need("lorieGpuCopyAck" in success_tail, "done:ack-after-success-only", failures)

    published_marker = "uint32_t published = lorieGateAObserveFatal(&st->gateA);"
    pub_at = io.find(published_marker)
    need(pub_at >= 0, "xfatal:observe-marker", failures)
    if pub_at < 0:
        return failures
    sig_at = io.rfind("static void gateAXFatal", 0, pub_at)
    need(sig_at >= 0, "xfatal:definition", failures)
    xfatal = extract_fn(io[sig_at:], "static void gateAXFatal")
    pub = xfatal.find("lorieGateAObserveFatal")
    halt = xfatal.find("lorieGateAFatalHalt")
    publish = xfatal.find("lorieGateAPublishFatal")
    need(pub >= 0 and halt >= 0 and publish >= 0 and pub < publish < halt,
         "xfatal:observe-before-publish-before-halt", failures)
    preserve = extract_if_block(xfatal, "if (published != 0)")
    need("_exit(127)" in preserve, "xfatal:preserve-exit", failures)
    need("lorieGateAFatalHalt" not in preserve, "xfatal:preserve-no-halt", failures)
    need("lorieGateADumpSummary" in preserve, "xfatal:preserve-dump", failures)
    need('"x-observe-fatal"' in preserve, "xfatal:preserve-where", failures)
    remainder = xfatal[xfatal.find(preserve) + len(preserve):]
    need("lorieGateAFatalHalt" in remainder, "xfatal:fresh-fatal-still-halts", failures)
    need("lorieGateAPublishFatal" in remainder, "xfatal:fresh-fatal-publishes", failures)

    wait = extract_fn(io, "static LorieGateAResult gateAWaitTerminal")
    need("elapsed > 2000" in wait, "wait:2000-ms", failures)
    need("elapsed > 3000" not in wait and "elapsed > 5000" not in wait, "wait:not-widened", failures)
    need("lorieGpuCopyWait(serial, 2000)" in io, "init:copy-wait-2000", failures)
    need("lorieGpuCopyWait(serial, 3000)" not in io, "init:not-3000", failures)

    present = extract_fn(io, "void lorieGpuCopyWaitForPresentOrFatal")
    need("lorieGpuCopyWait(serial, 2000)" in present, "present:wait-2000", failures)
    need('gateAXFatal("x-present-copy-wait", LORIE_GATEA_FAIL_TIMEOUT' in present,
         "present:genuine-timeout-kept", failures)

    composite = extract_fn(io, "static void lorieGpuCopyWaitForCompositeOrFatal")
    need("lorieGpuCopyWait(serial, 2000)" in composite, "composite:wait-2000", failures)
    need('gateAXFatal("x-exa-composite-wait", LORIE_GATEA_FAIL_TIMEOUT' in composite,
         "composite:genuine-timeout-kept", failures)

    need("TERMUX_X11_GATEA_TEST_FAULT" in io, "init:fault-env", failures)
    need("TERMUX_X11_GATEA_TEST_ARM" in io, "init:arm-env", failures)
    need('"fbo-incomplete"' in io, "init:r7-04-selector", failures)
    need("LORIE_GATEA_TEST_FBO_INCOMPLETE" in r, "renderer:fbo-selector", failures)
    lookup = extract_fn(r, "int Renderer::consumeGateAComposite")
    need("LORIE_GATEA_TEST_FBO_INCOMPLETE" in lookup, "renderer:fbo-in-lookup", failures)
    need("r-gatea-DIRECT_LOOKUP_FAIL" in r, "renderer:lookup-fail-what", failures)
    need("LORIE_GATEA_FAIL_DRAW" in r, "renderer:fail-draw", failures)
    apply_fn = extract_fn(r, "LorieGateABatchOut Renderer::applyPendingGpuCopiesLocked")
    need('gateARendererFatal(state, "r-gatea-DIRECT_LOOKUP_FAIL"' in apply_fn
         or 'gateARendererFatal(state, "r-gatea-DIRECT_LOOKUP_FAIL",'
            in apply_fn, "renderer:lookup-fail-halt", failures)
    need("LORIE_GATEA_FAIL_DRAW" in apply_fn, "renderer:lookup-fail-reason-2", failures)

    consume = extract_fn(h, "static inline __always_inline int lorieGateATestFaultConsume(")
    need("__atomic_compare_exchange_n(&st->gateATestFault.consumed" in consume,
         "hdr:one-shot-cas", failures)
    need("LORIE_GATEA_EVENT_TEST_FAULT_FIRED" in consume, "hdr:event-35", failures)
    need("getenv" not in consume, "hdr:consume-no-getenv", failures)

    return failures


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_r7_fatal_propagation.py WORKTREE")
        return 2
    failures = verify(Path(sys.argv[1]))
    if failures:
        print("R7_FATAL_PROPAGATION=FAIL")
        for item in failures:
            print(f"FAIL {item}")
        return 1
    print("R7_FATAL_PROPAGATION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
