#!/usr/bin/env python3
"""Semantic control-flow gate for lorieExaDoneComposite wait-timeout handling.

Claim: if GPU completion is not proven, Composite Done must not become
client-visible success (no repair / ack / Gcomp Done fallthrough).

This is not a whole-file string grep. It extracts lorieExaDoneComposite,
isolates the legacy scheduled path, and checks the wait-false branch.
"""
from __future__ import annotations

import sys
from pathlib import Path

SUCCESS_TOKENS = (
    "lorieExaRepairDestXByteZero",
    "lorieGpuCopyAck",
    "LorieBuffer_gpuCopyPendingDec",
    'p2a2_emit("Gcomp Done")',
    "exaCompDone++",
)

WAIT_CALL = "lorieGpuCopyWait(exaGpuComp.lastSerial, 2000)"
HELPER = "lorieGpuCopyWaitForCompositeOrFatal"
WHAT = '"x-exa-composite-wait"'
PRESENT_WHAT = '"x-present-copy-wait"'


def need(cond: bool, label: str, failures: list[str]) -> None:
    if not cond:
        failures.append(label)


def strip_comments(text: str) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text.startswith("//", i):
            nl = text.find("\n", i)
            i = n if nl < 0 else nl
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def matching_paren(text: str, open_idx: int) -> int:
    depth = 0
    i = open_idx
    while i < len(text):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("unbalanced paren")


def matching_brace(text: str, open_idx: int) -> int:
    depth = 0
    i = open_idx
    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
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
    end = matching_brace(text, brace)
    return text[start : end + 1]


def skip_direct_block(fn: str) -> str:
    marker = "if (exaGpuComp.direct)"
    pos = fn.find(marker)
    if pos < 0:
        return fn
    brace = fn.find("{", pos)
    if brace < 0:
        return fn
    end = matching_brace(fn, brace)
    return fn[:pos] + fn[end + 1 :]


def then_body_of_if(text: str, if_kw_idx: int) -> str:
    """Return the then-body of `if (...)` starting at if_kw_idx."""
    paren_open = text.find("(", if_kw_idx)
    paren_close = matching_paren(text, paren_open)
    i = paren_close + 1
    while i < len(text) and text[i].isspace():
        i += 1
    if i < len(text) and text[i] == "{":
        end = matching_brace(text, i)
        return text[i + 1 : end]
    semi = text.find(";", i)
    if semi < 0:
        raise ValueError("missing then-statement")
    return text[i : semi + 1]


def helper_wait_false_is_fatal(init: str) -> bool:
    try:
        helper = extract_function(
            init, "static void lorieGpuCopyWaitForCompositeOrFatal("
        )
    except ValueError:
        return False
    wait = helper.find("if (!lorieGpuCopyWait(serial, 2000))")
    if wait < 0:
        return False
    then = then_body_of_if(helper, wait)
    if "gateAXFatal(" not in then:
        return False
    if WHAT not in then:
        return False
    if "LORIE_GATEA_FAIL_TIMEOUT" not in then:
        return False
    for tok in SUCCESS_TOKENS:
        if tok in helper:
            return False
    if PRESENT_WHAT in helper:
        return False
    after_if = helper[wait:]
    return "return" not in after_if


def verify(repo: Path) -> list[str]:
    failures: list[str] = []
    init_path = repo / "lorie/src/main/cpp/lorie/InitOutput.c"
    need(init_path.is_file(), "init:exists", failures)
    if failures:
        return failures
    raw = init_path.read_text()
    init = strip_comments(raw)

    need('lorieGpuCopyWait(serial, 2000)' in init, "wait:timeout-still-2000", failures)
    need("lorieGpuCopyWait(serial, 3000)" not in init
         and "lorieGpuCopyWait(serial, 4000)" not in init
         and "lorieGpuCopyWait(serial, 5000)" not in init,
         "wait:timeout-not-increased", failures)
    need('gateAXFatal("x-present-copy-wait", LORIE_GATEA_FAIL_TIMEOUT, serial)' in init,
         "present:wait-or-fatal-unchanged", failures)

    try:
        fn = extract_function(init, "static void lorieExaDoneComposite(PixmapPtr dst)")
    except ValueError as exc:
        failures.append(f"done:extract:{exc}")
        return failures

    legacy = skip_direct_block(fn)
    scheduled_at = legacy.find("if (exaGpuComp.scheduled)")
    need(scheduled_at >= 0, "done:scheduled-block", failures)
    if scheduled_at < 0:
        return failures
    sched_brace = legacy.find("{", scheduled_at)
    need(sched_brace >= 0, "done:scheduled-brace", failures)
    if sched_brace < 0:
        return failures
    sched_end = matching_brace(legacy, sched_brace)
    scheduled = legacy[sched_brace + 1 : sched_end]

    helper_ok = helper_wait_false_is_fatal(init)
    raw_wait = scheduled.find("if (!lorieGpuCopyWait(exaGpuComp.lastSerial, 2000))")
    helper_call = HELPER in scheduled and "exaGpuComp.lastSerial" in scheduled

    if helper_ok and helper_call:
        need(WAIT_CALL not in scheduled, "done:scheduled-no-raw-wait-after-helper", failures)
        need(HELPER in scheduled and "exaGpuComp.lastSerial" in scheduled,
             "done:scheduled-calls-helper", failures)
        # Success tokens may exist after proven wait; they must not appear in helper.
        prefix = scheduled.split(HELPER, 1)[0]
        for tok in SUCCESS_TOKENS:
            need(tok not in prefix, f"done:no-success-before-helper:{tok}", failures)
        return failures

    # Raw wait style: wait-false then-body must fatal-stop.
    need(raw_wait >= 0, "done:wait-false-if-missing", failures)
    if raw_wait < 0:
        return failures
    then = then_body_of_if(scheduled, raw_wait)
    need("gateAXFatal(" in then, "done:wait-false-must-fatal", failures)
    need(WHAT in then, "done:wait-false-what", failures)
    need("LORIE_GATEA_FAIL_TIMEOUT" in then, "done:wait-false-timeout-reason", failures)
    for tok in SUCCESS_TOKENS:
        need(tok not in then, f"done:wait-false-no-success:{tok}", failures)
    return failures


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_exa_composite_wait.py WORKTREE")
        return 2
    repo = Path(sys.argv[1])
    failures = verify(repo)
    if failures:
        print("EXA_COMPOSITE_WAIT=FAIL")
        for item in failures:
            print(f"FAIL {item}")
        return 1
    print("EXA_COMPOSITE_WAIT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
