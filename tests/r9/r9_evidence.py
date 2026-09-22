#!/usr/bin/env python3
"""Derive the judge-facing R9 evidence files from captured raw artifacts.

Separated from the shell runner on purpose: this is where every judgement-relevant
inference lives, so it can be unit-tested offline with synthetic input. The runner
only orchestrates the device and captures raw artifacts; it makes no inferences.

NEVER fabricate. A value that was not observed is None, and the judge treats None as
NOT OBSERVED and refuses. Every function here returns what it actually saw.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "r8"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
from r8_obs_stream import obs_loads
import gatea_counters as C

HALT_PAT = re.compile(r"GATEA_FATAL_HALT what=(\S+) reason=(\d+)")
OBS_PAT = re.compile(r"R8_OBS\s+(\{.*\})\s*$")
SUMMARY_PAT = re.compile(r"GATEA_SUMMARY .*?\bnonce=(\d+)\b.*?\bgeneration=(\d+)\b")
# the renderer's own bind log line, which is the ONLY place the latched tuple appears
BIND_PAT = re.compile(
    r"GATEA_BIND version=\d+ nonce=(\d+) generation=(\d+) bound=(\d+)")
EPOCH_PAT = re.compile(r'"phase":\s*"R_EPOCH_BEGIN".*?"epoch_id":\s*(\d+)')
# The renderer's per-stage import log. This is a LorieNative line, NOT an R8_OBS row:
# gateAValidateStage writes "GATEA_VALIDATE version=.. generation=.. bufferId=..
# stage=<NAME> result=<n> errno=.. elapsed_us=.." (renderer.cpp:369). Measured on
# r9-f1/attempt-02: the stages are there and the R8_OBS stream does not carry them,
# so a derivation that only reads obs rows reports legitimate_ready_sent=false on a
# run whose READY demonstrably succeeded.
VALIDATE_PAT = re.compile(r"GATEA_VALIDATE\b.*?\bstage=(\S+).*?\bresult=(-?\d+)")
# LORIE_GATEA_EVENT_TEST_FAULT_FIRED = 35; lorieGateATrace puts the CELL number in
# src and the role in dst (lorie.h, lorieGateATestFaultConsume). The event name never
# appears in the log, only the number.
TEST_FAULT_FIRED_PAT = re.compile(
    r"GATEA_EVENT\b.*?\bevent=35\b.*?\bsrc=(\d+)\b.*?\bdst=(\d+)\b")
LORIE_GATEA_TEST_STALE_READY_REPLAY = 16
# Frozen event numbers (lorie.h, "Numbers 1..28 stay frozen").
EVENT_REGISTER_READY = 1
EVENT_UNREGISTER_ACK = 25
ROLE_X = 1
ROLE_RENDERER = 2
RING_ROW_PAT = re.compile(
    r"GATEA_EVENT\b.*?\brole=(\d+)\b.*?\bevent=(\d+)\b.*?\bsrc=(\d+)\b")


def obs_rows(raw: str) -> list[dict]:
    rows = []
    for line in raw.splitlines():
        m = OBS_PAT.search(line)
        if m:
            try:
                rows.append(obs_loads(m.group(1)))
            except json.JSONDecodeError:
                continue
    return rows


def halt_of(raw: str):
    m = HALT_PAT.search(raw)
    return (m.group(1), int(m.group(2))) if m else None


def proc_identity(stat_text: str):
    """(pid, starttime) from /proc/<pid>/stat. Field 22 is taken relative to the
    LAST ')' because comm may contain spaces and parentheses (Q8)."""
    if not stat_text.strip():
        return None, None
    try:
        pid = int(stat_text.split(" ", 1)[0])
        tail = stat_text[stat_text.rindex(")") + 2:].split()
        return pid, int(tail[19])
    except (ValueError, IndexError):
        return None, None


def validate_stages(raw: str) -> list[tuple[str, int]]:
    return [(m.group(1), int(m.group(2))) for m in VALIDATE_PAT.finditer(raw)]


def test_fault_fired(raw: str, cell: int) -> bool:
    return any(int(m.group(1)) == cell for m in TEST_FAULT_FIRED_PAT.finditer(raw))


def binds_observed(raw: str):
    """How many times the renderer latched a tuple in this capture. None when the obs
    stream is absent, because 'no rows' and 'no capture' are different facts."""
    rows = list(EPOCH_PAT.finditer(raw))
    if not rows and "R8_OBS" not in raw:
        return None
    return len(rows)


def fd_previous_conn(fdlist: str, binds):
    """Q9-F1: did X's fd table still hold the PREVIOUS conn_fd at this boundary?
    getXConnection neither closes nor unregisters the old fd, so after a rebind two
    can be registered at once, and nothing in the product reports it.

    Honest answers only:
      * no fd listing captured              -> None. NOT OBSERVED.
      * listing captured, at most ONE bind  -> False. Not a default: with a single
        bind there was never a previous connection, so there is no previous fd that
        could have been retained. The listing is still required, because 'X was alive
        and its table was read' is the part that has to be observed.
      * listing captured, MORE than one bind -> None. Answering then needs per-fd peer
        identification, which nothing captures today. No cell in the current packet
        (F1, F2) crosses a rebind; the WARM cells that did are removed. A future warm
        cell must capture the peer identity rather than inherit this False."""
    if not fdlist.strip():
        return None
    if binds is None:
        return None
    return False if binds <= 1 else None


def boundary_record(x_stat: str, act_stat: str, raw: str, fd_had_prev) -> dict:
    """One per-boundary identity record. Every field is mandatory for the judge;
    anything not observed stays None so the judge refuses rather than guesses."""
    xp, xs = proc_identity(x_stat)
    ap, ast_ = proc_identity(act_stat)
    sm = SUMMARY_PAT.search(raw)
    bm = None
    for bm in BIND_PAT.finditer(raw):
        pass  # the LAST bind is the current latch
    ep = None
    for m in EPOCH_PAT.finditer(raw):
        ep = int(m.group(1))

    # Two sources for the X-authority tuple, and they are not interchangeable.
    #
    # GATEA_SUMMARY is the shared state read by X itself, but lorieGateADumpSummary
    # only runs on a fatal, a clean close or a terminate. A healthy session (R9-F2)
    # never dumps, so on its own this field would be None and the cell would be
    # INVALID for evidence that was never going to exist. Measured: r9-f2/attempt-01.
    #
    # GATEA_BIND is the fallback and it is a real read of the SAME words:
    # gateABindFromState does
    #     nonce      = lorieGateALoadU64Acquire(&state->gateA.sessionNonce)
    #     generation = lorieGateALoadU64Acquire(&state->gateA.generation)
    # (activity.cpp:202-203) and logs those, BEFORE latching them into
    # gateABoundNonce/Generation (:219-220). So the printed value is the shared
    # state at bind time, not the renderer's private copy.
    #
    # What it is NOT: a reading of "now". The two halves have different lifetimes -
    # the latch is a copy and X can move the shared words underneath it - so this
    # field means "the shared tuple as of the last bind". That is exactly what
    # new_session_nonce asks (is this a different session from the previous one), and
    # it is NOT a substitute for renderer_bound_*, which stays separately recorded.
    if sm:
        nonce, gen, src = int(sm.group(1)), int(sm.group(2)), "summary_dump"
    elif bm:
        nonce, gen, src = int(bm.group(1)), int(bm.group(2)), "gatea_bind_shared_read"
    else:
        nonce = gen = None
        src = None
    return {
        "x_pid": xp, "x_starttime": xs,
        "activity_pid": ap, "activity_starttime": ast_,
        "shared_nonce": nonce,
        "shared_generation": gen,
        "shared_tuple_source": src,
        "renderer_bound_nonce": int(bm.group(1)) if bm else None,
        "renderer_bound_generation": int(bm.group(2)) if bm else None,
        "epoch_id": ep,
        "x_fd_table_had_previous_conn_fd": fd_had_prev,
    }


def renderer_fatal(raw: str) -> dict:
    """Did the renderer PUBLISH before dying? That is what lets X survive (the COLD
    trilemma). A renderer that only _exit(127)s leaves generationFatal 0, so X takes
    x-eof and the cell is unconstructible."""
    h = halt_of(raw)
    published = None
    m = re.search(r"GATEA_SUMMARY .*?\bgenerationFatal=(\d+)\b", raw)
    if m:
        published = int(m.group(1)) != 0
    return {"published": published,
            "halt_what": h[0] if h else None,
            "halt_reason": h[1] if h else None}


def x_survival(x_alive_after: bool | None, raw: str) -> dict:
    h = halt_of(raw)
    return {"x_alive_after_renderer_death": x_alive_after,
            "x_eof_fatal": (h[0] == "x-eof") if h else False}


def ring_rows(raw: str) -> list[tuple[int, int, int]]:
    """(role, event, srcId) for every GATEA_EVENT line, from the live telemetry."""
    return [(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            for m in RING_ROW_PAT.finditer(raw)]


def registry_state(summary_text: str, raw: str = "") -> dict:
    """How many buffers each side still holds registered.

    Preferred source is the LIVE event stream, not the summary counters. c25/c26 are
    only ever printed by lorieGateADumpSummary, which runs on a fatal, a clean close
    or a terminate - so a healthy session produces no counters at all, and reading
    that absence as "empty" would turn a missing measurement into a pass. The events
    are unconditional: REGISTER_READY (1) per id per role on the way in,
    UNREGISTER_ACK (25) per id per role on the way out. What is still held is the
    difference, per role, which is exactly empty_registry_both_sides.

    Measured on r9-f2/attempt-01: ids 6 and 7 each took event 1 then event 25 on BOTH
    roles, while gatea-summary.txt was empty because nothing dumped. Trying to force a
    dump with SIGTERM did not reach CloseScreen and fatalled the renderer with r-hup.

    Counters are still read when a dump did happen, and are used only if the event
    stream is absent entirely (then both are None: NOT OBSERVED, never 0)."""
    # DEFECT FOUND 2026-09-22, fixed here: this read c25/c26, which are UNREGISTER
    # and RESOURCE_DESTROY. The registry-CURRENT counters are c18 and c19
    # (tests/common/gatea_counters.py, pinned against lorie.h by
    # verify-r9-support.py). No R9 verdict depended on the wrong pair - the only
    # PASS that reads a registry file is r9-f2/attempt-02 with
    # source=telemetry_events, and r9-f1/attempt-03 has no registry file - but R10
    # is built entirely on these counters, so it had to be corrected before R10.
    def c(n):
        return C.read(summary_text, n)

    rows = ring_rows(raw)
    if not rows:
        xr = c(C.X_REGISTRY_CURRENT)
        return {"x_entries": xr,
                "renderer_ready_entries": c(C.RENDERER_REGISTRY_CURRENT),
                "source": "summary_counters" if xr is not None else "none"}

    def held(role):
        ready = {sid for r, e, sid in rows if r == role and e == EVENT_REGISTER_READY}
        acked = {sid for r, e, sid in rows if r == role and e == EVENT_UNREGISTER_ACK}
        return len(ready - acked)

    return {"x_entries": held(ROLE_X),
            "renderer_ready_entries": held(ROLE_RENDERER),
            "source": "telemetry_events",
            "counter_x_registry_current": c(C.X_REGISTRY_CURRENT),
            "counter_renderer_registry_current": c(C.RENDERER_REGISTRY_CURRENT)}


def stale_replay(raw: str) -> dict:
    """F1 evidence, read from the two places the product actually writes it.

    legitimate_ready_sent  GATEA_VALIDATE stage=READY_SEND_RETURN result=1. That is
                           the return of the LEGITIMATE gateASendReady, immediately
                           before the fault site (renderer.cpp:670-679), so it is the
                           precondition the cell needs: gateAValidateImport ran and
                           got as far as a successful READY.
    stale_frame_sent       the TEST_FAULT_FIRED telemetry for cell 16, which
                           lorieGateATestFaultConsume emits on the CAS that lets the
                           injection run. Deliberately NOT derived from the expected
                           fatal: taking the fatal as proof that the frame was sent,
                           and then requiring that same fatal for the verdict, would
                           be circular.
    gate_a_active_on_arrival
                           the silent-drop trap. handleGateARecord returns BEFORE the
                           tuple check when Gate A is inactive (cmdentrypoint.cpp:631
                           -632), so a dropped frame proves nothing and must come out
                           INVALID, never PASS. x-wrong-generation is only reachable
                           past that gate, so the fatal proves active. No fatal at all
                           after a fired fault means it was dropped -> False. Any
                           OTHER fatal -> None, because the frame's fate is unknown.
    """
    stages = validate_stages(raw)
    rows = obs_rows(raw)
    legit = any(st == "READY_SEND_RETURN" and rc == 1 for st, rc in stages) or any(
        r.get("phase") in ("VALIDATE_TERMINAL_READY", "R_READY_SENT")
        for r in rows if r.get("role") == "r")
    sent = test_fault_fired(raw, LORIE_GATEA_TEST_STALE_READY_REPLAY)
    h = halt_of(raw)
    if h is not None and h[0] == "x-wrong-generation":
        active = True
        accepted = False
    elif h is None:
        active = False if sent else None
        accepted = None
    else:
        active = None
        accepted = None
    return {"legitimate_ready_sent": legit,
            "stale_frame_sent": sent,
            "gate_a_active_on_arrival": active,
            "stale_tuple_accepted": accepted}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", required=True)
    ap.add_argument("--cell", required=True)
    ap.add_argument("--x-stat-before", default="")
    ap.add_argument("--x-stat-after", default="")
    ap.add_argument("--act-stat-before", default="")
    ap.add_argument("--act-stat-after", default="")
    # F2's boundary is the F1 -> F2 boundary, so its "before" half comes from the
    # PREVIOUS attempt's own captures, not from this one. Passed explicitly as files;
    # this module never goes looking through the evidence tree by itself.
    ap.add_argument("--x-fdlist-before", default="")
    ap.add_argument("--x-fdlist-after", default="")
    ap.add_argument("--prev-x-fdlist", default="")
    ap.add_argument("--prev-x-stat", default="")
    ap.add_argument("--prev-act-stat", default="")
    ap.add_argument("--prev-raw", default="")
    ap.add_argument("--x-alive-after", choices=("true", "false", "unknown"),
                    default="unknown")
    ap.add_argument("--fd-had-prev", choices=("true", "false", "unknown"),
                    default="unknown")
    a = ap.parse_args()

    ev = Path(a.evidence)
    raw = (ev / "raw-logcat.txt").read_text(errors="replace") if (ev / "raw-logcat.txt").is_file() else ""
    summ = (ev / "gatea-summary.txt").read_text(errors="replace") if (ev / "gatea-summary.txt").is_file() else ""

    def rd(p):
        return Path(p).read_text(errors="replace") if p and Path(p).is_file() else ""

    tri = {"true": True, "false": False, "unknown": None}
    # An explicit --fd-had-prev still wins, so a cell that genuinely observes the fd
    # table can say so. "unknown" is not an answer, it is the absence of one, so it
    # hands the question to fd_previous_conn and its captures.
    override = tri[a.fd_had_prev]

    def fdp_for(fdlist_path: str, raw_text: str):
        if override is not None:
            return override
        return fd_previous_conn(rd(fdlist_path), binds_observed(raw_text))

    # How many identity boundaries a cell actually has is a property of the cell,
    # not a constant. Writing a second record that is all None because the process it
    # would describe is gone would be fabrication in the other direction: the judge
    # would refuse a correct run for missing evidence that never existed.
    if a.cell == "R9-F1":
        # ONE session, no process boundary. X halting on x-wrong-generation IS the
        # cell's expected outcome (spec: fatal.what = x-wrong-generation), so there is
        # no live "after" identity to observe. That X died is proved by the halt token
        # in raw-logcat, which is what judge_f1 requires; it is not proved by an empty
        # /proc read, which is indistinguishable from a failed capture.
        bounds = [boundary_record(rd(a.x_stat_before), rd(a.act_stat_before), raw,
                                  fdp_for(a.x_fdlist_before, raw))]
    elif a.cell == "R9-F2":
        # The boundary being judged is F1 -> F2: a NEW X process and a NEW Activity
        # process, with a NEW sessionNonce. Both halves are real captures. F1's half
        # is its BEFORE snapshot, because F1's own after-snapshot is empty by design
        # (see above).
        prev_raw = rd(a.prev_raw)
        bounds = [boundary_record(rd(a.prev_x_stat), rd(a.prev_act_stat), prev_raw,
                                  fdp_for(a.prev_x_fdlist, prev_raw)),
                  boundary_record(rd(a.x_stat_after), rd(a.act_stat_after), raw,
                                  fdp_for(a.x_fdlist_after, raw))]
    else:
        bounds = [boundary_record(rd(a.x_stat_before), rd(a.act_stat_before), raw,
                                  fdp_for(a.x_fdlist_before, raw)),
                  boundary_record(rd(a.x_stat_after), rd(a.act_stat_after), raw,
                                  fdp_for(a.x_fdlist_after, raw))]
    (ev / "identity-boundaries.jsonl").write_text(
        "".join(json.dumps(b) + "\n" for b in bounds))

    if a.cell == "R9-COLD-2":
        (ev / "renderer-fatal.json").write_text(json.dumps(renderer_fatal(raw), indent=2) + "\n")
        (ev / "x-survival.json").write_text(
            json.dumps(x_survival(tri[a.x_alive_after], raw), indent=2) + "\n")
        reg = registry_state(summ, raw)
        (ev / "registry-at-bump.json").write_text(json.dumps(
            {"pending_count": None, "last_submitted_serial": None, **reg}, indent=2) + "\n")
    elif a.cell == "R9-F1":
        (ev / "stale-replay.json").write_text(json.dumps(stale_replay(raw), indent=2) + "\n")
    elif a.cell == "R9-F2":
        (ev / "registry-fresh.json").write_text(
            json.dumps(registry_state(summ, raw), indent=2) + "\n")

    print(f"R9_EVIDENCE_DERIVED {a.cell}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
