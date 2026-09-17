#!/usr/bin/env python3
"""Static gate: renderer HUP preserves published fatal; genuine HUP stays r-hup.

R7-05: X publishes generationFatal then fail-stops. Activity must not emit
a second GATEA_FATAL_HALT what=r-hup. Genuine bound published==0 HUP still
halts r-hup/6. Preserve path is noreturn fail-stop and cannot reach normal
cleanup. X-side x-hup is unchanged. No ABI / timeout change.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CLEANUP_TOKENS = (
    "ALooper_removeFd",
    "g_renderer.setSharedState(NULL)",
    "g_renderer.removeAllBuffers()",
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
    cls_h = repo / "lorie/src/main/cpp/lorie/lorie_gatea_hup_class.h"
    activity = repo / "lorie/src/main/cpp/lorie/activity.cpp"
    cmd = repo / "lorie/src/main/cpp/lorie/cmdentrypoint.cpp"
    init = repo / "lorie/src/main/cpp/lorie/InitOutput.c"
    host = repo / "scripts/test_gatea_hup_class.c"
    need(hdr.is_file(), "hdr:exists", failures)
    need(cls_h.is_file(), "cls:exists", failures)
    need(activity.is_file(), "activity:exists", failures)
    need(cmd.is_file(), "cmd:exists", failures)
    need(init.is_file(), "init:exists", failures)
    need(host.is_file(), "host:exists", failures)
    if failures:
        return failures
    h = hdr.read_text()
    c = cls_h.read_text()
    a = activity.read_text()
    x = cmd.read_text()
    io = init.read_text()
    t = host.read_text()

    need("lorieGateAClassifyPeerHup" in c, "cls:fn", failures)
    need("LORIE_GATEA_HUP_UNBOUND" in c, "cls:unbound", failures)
    need("LORIE_GATEA_HUP_PRESERVE" in c, "cls:preserve", failures)
    need("LORIE_GATEA_HUP_R_HUP" in c, "cls:r-hup", failures)
    need("if (!bound)" in c, "cls:unbound-first", failures)
    need("publishedFatal != 0" in c, "cls:published-nonzero", failures)
    cls_fn = extract_fn(c, "lorieGateAClassifyPeerHup")
    unbound_ret = cls_fn.find("return LORIE_GATEA_HUP_UNBOUND")
    preserve_ret = cls_fn.find("return LORIE_GATEA_HUP_PRESERVE")
    rhup_ret = cls_fn.find("return LORIE_GATEA_HUP_R_HUP")
    need(unbound_ret >= 0 and preserve_ret >= 0 and rhup_ret >= 0
         and unbound_ret < preserve_ret < rhup_ret,
         "cls:unbound-then-preserve-then-rhup", failures)
    need("GATEA_FATAL_HALT" not in cls_fn, "cls:no-halt-literal", failures)
    need("_exit" not in cls_fn, "cls:classifier-not-exit", failures)

    need('#include "lorie_gatea_hup_class.h"' in a, "activity:includes-classifier", failures)
    need("gateAMappedState" in a, "activity:keeps-mmap", failures)
    need("lorieGateAObserveFatal(&gateAMappedState->gateA)" in a,
         "activity:observe-before-class", failures)
    need("lorieGateAClassifyPeerHup" in a, "activity:uses-classifier", failures)
    need("GATEA_HUP_PRESERVE" in a, "activity:preserve-marker", failures)
    need("GATEA_FATAL_HALT" not in a.split("GATEA_HUP_PRESERVE")[1].split("\n")[0]
         if "GATEA_HUP_PRESERVE" in a else False,
         "activity:preserve-marker-not-halt", failures)

    need(enum_value(h, "LORIE_GATEA_FAIL_DRAW") == 2, "abi:fail-draw", failures)
    need(enum_value(h, "LORIE_GATEA_FAIL_GENERATION") == 6, "abi:fail-generation", failures)
    need(enum_value(t, "LORIE_GATEA_FAIL_DRAW") == 2, "host:fail-draw", failures)
    need(enum_value(t, "LORIE_GATEA_FAIL_GENERATION") == 6, "host:fail-generation", failures)
    need('sizeof(struct LorieGateAProtocol) == 40' in h, "abi:protocol-40", failures)
    need("offsetof(struct LorieGateAProtocol, generationFatal) == 4" in h,
         "abi:fatal-off-4", failures)
    need("#define LORIE_GATEA_PROTOCOL_VERSION 1u" in h, "abi:protocol-version-1", failures)
    need('LORIE_GATEA_STATIC_ASSERT(LORIE_GATEA_EVENT_MAX == 37' in h, "abi:event-max-37", failures)
    need('LORIE_GATEA_STATIC_ASSERT(LORIE_GATEA_COUNTER_MAX == 28' in h, "abi:counter-max-28", failures)
    need("#define LORIE_GATEA_FENCE_TIMEOUT_NS 2000000000ull" in h, "hdr:fence-2000", failures)
    need("#define LORIE_RENDERER_FRAME_WAIT_NS 8000000L" in h, "hdr:8ms", failures)
    need("elapsed > 2000" in io, "init:wait-2000", failures)
    need("lorieGpuCopyWait(serial, 2000)" in io, "init:copy-wait-2000", failures)

    xcb = extract_fn(a, "static int xcallback(int fd, int events, __unused void* data)")
    hup = extract_if_block(xcb, "if (events & (ALOOPER_EVENT_ERROR | ALOOPER_EVENT_HANGUP))")
    need("lorieGateABoundTuple" in hup, "hup:bound-check", failures)
    need("lorieGateAObserveFatal" in hup, "hup:observe-fatal", failures)
    need("lorieGateAClassifyPeerHup" in hup, "hup:classify", failures)
    obs = hup.find("lorieGateAObserveFatal")
    cls = hup.find("lorieGateAClassifyPeerHup")
    halt = hup.find('lorieGateAFatalHalt("r-hup"')
    need(obs >= 0 and cls >= 0 and halt >= 0 and obs < cls < halt,
         "hup:observe-then-classify-then-rhup", failures)

    preserve_if = extract_if_block(hup, "if (hcls == LORIE_GATEA_HUP_PRESERVE)")
    need("gateAHupPreserve" in preserve_if, "preserve:calls-helper", failures)
    need("lorieGateAFatalHalt" not in preserve_if, "preserve:if-no-fatal-halt", failures)
    for tok in CLEANUP_TOKENS:
        need(tok not in preserve_if, f"preserve-if:no-{tok}", failures)

    helper = extract_fn(a, "static void gateAHupPreserve")
    need("__attribute__((noreturn))" in a[a.find("static void gateAHupPreserve") - 80:
                                         a.find("static void gateAHupPreserve")],
         "preserve:noreturn-attr", failures)
    need("_exit(127)" in helper, "preserve:noreturn-exit", failures)
    need("lorieGateAFatalHalt" not in helper, "preserve:no-fatal-halt", failures)
    need("GATEA_FATAL_HALT" not in helper, "preserve:no-halt-literal", failures)
    need("GATEA_HUP_PRESERVE" in helper, "preserve:diagnostic", failures)
    for tok in CLEANUP_TOKENS:
        need(tok not in helper, f"preserve:no-{tok}", failures)

    rhup_if = extract_if_block(hup, "if (hcls == LORIE_GATEA_HUP_R_HUP)")
    need('lorieGateAFatalHalt("r-hup", LORIE_GATEA_FAIL_GENERATION)' in rhup_if,
         "rhup:halt-reason-6", failures)
    need("_exit" not in rhup_if, "rhup:uses-fatal-halt", failures)
    for tok in CLEANUP_TOKENS:
        need(tok not in rhup_if, f"rhup:no-{tok}", failures)

    legacy = hup[hup.find(rhup_if) + len(rhup_if):]
    need("ALooper_removeFd" in legacy, "unbound:legacy-remove-fd", failures)
    need("g_renderer.setSharedState(NULL)" in legacy, "unbound:legacy-clear-state", failures)
    need("g_renderer.removeAllBuffers()" in legacy, "unbound:legacy-remove-buffers", failures)
    need("lorieGateAFatalHalt" not in legacy, "unbound:no-fatal", failures)

    # Preserve/R_HUP are noreturn so cleanup is textually after, but not
    # reachable. Require the fail-stop calls appear before cleanup tokens.
    first_cleanup = min(hup.find(tok) for tok in CLEANUP_TOKENS if tok in hup)
    need(first_cleanup > halt, "hup:fail-stop-before-cleanup", failures)
    need(a.find("_exit(127)") < a.find("static int xcallback"),
         "hup:preserve-helper-before-callback", failures)
    need(hup.find("gateAHupPreserve") < first_cleanup, "hup:preserve-call-before-cleanup", failures)

    # X-side x-hup must remain the renderer-death containment path.
    need('lorieGateAFatalHalt("x-hup", LORIE_GATEA_FAIL_GENERATION)' in x,
         "x:legacy-x-hup-halt", failures)
    need('gateAFatalFromInput(LORIE_GATEA_FAIL_GENERATION, "x-hup")' in x,
         "x:proto-x-hup", failures)
    need("lorieGateAClassifyPeerHup" not in x, "x:no-renderer-classifier", failures)
    need("GATEA_HUP_PRESERVE" not in x, "x:no-preserve-marker", failures)
    proto = extract_fn(x, "static void handleLorieEventsProto(int fd, int ready)")
    err = extract_if_block(proto, "if (ready & X_NOTIFY_ERROR)")
    need('gateAFatalFromInput(LORIE_GATEA_FAIL_GENERATION, "x-hup")' in err,
         "x-proto:error-still-xhup", failures)
    need("LORIE_GATEA_HUP_PRESERVE" not in err, "x-proto:no-preserve", failures)

    need("lorieGateAClassifyPeerHup" in t, "host:calls-production-fn", failures)
    need("LORIE_GATEA_HUP_PRESERVE" in t, "host:preserve-case", failures)
    need("LORIE_GATEA_HUP_R_HUP" in t, "host:rhup-case", failures)
    need("LORIE_GATEA_HUP_UNBOUND" in t, "host:unbound-case", failures)
    need("LORIE_GATEA_FAIL_DRAW" in t, "host:case-a-draw", failures)
    need("LORIE_GATEA_FAIL_GENERATION" in t, "host:case-b-generation", failures)

    return failures


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_r7_hup_preserve.py WORKTREE")
        return 2
    failures = verify(Path(sys.argv[1]))
    if failures:
        print("R7_HUP_PRESERVE=FAIL")
        for item in failures:
            print(f"FAIL {item}")
        return 1
    print("R7_HUP_PRESERVE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
