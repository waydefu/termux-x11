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
from r8_obs_stream import obs_loads

HALT_PAT = re.compile(r"GATEA_FATAL_HALT what=(\S+) reason=(\d+)")
OBS_PAT = re.compile(r"R8_OBS\s+(\{.*\})\s*$")
SUMMARY_PAT = re.compile(r"GATEA_SUMMARY .*?\bnonce=(\d+)\b.*?\bgeneration=(\d+)\b")
# the renderer's own bind log line, which is the ONLY place the latched tuple appears
BIND_PAT = re.compile(
    r"GATEA_BIND version=\d+ nonce=(\d+) generation=(\d+) bound=(\d+)")
EPOCH_PAT = re.compile(r'"phase":\s*"R_EPOCH_BEGIN".*?"epoch_id":\s*(\d+)')


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
    return {
        "x_pid": xp, "x_starttime": xs,
        "activity_pid": ap, "activity_starttime": ast_,
        "shared_nonce": int(sm.group(1)) if sm else None,
        "shared_generation": int(sm.group(2)) if sm else None,
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


def registry_state(summary_text: str) -> dict:
    """Registry occupancy read from the GATEA_SUMMARY counters. c25/c26 are the
    registry-current counters; absence is None, never 0."""
    def c(n):
        m = re.search(rf"\bc{n}=(\d+)\b", summary_text)
        return int(m.group(1)) if m else None
    return {"x_entries": c(25), "renderer_ready_entries": c(26)}


def stale_replay(raw: str) -> dict:
    """F1 evidence. The silent-drop trap is the field that matters: if Gate A was
    inactive when the stale frame arrived, handleGateARecord returned BEFORE the
    tuple check and the frame proves nothing (INVALID_CONSTRUCTION, not a pass)."""
    rows = obs_rows(raw)
    legit = any(r.get("phase") in ("VALIDATE_TERMINAL_READY", "R_READY_SENT")
                for r in rows if r.get("role") == "r")
    h = halt_of(raw)
    # the stale frame is only observable indirectly: the expected fatal names it
    sent = bool(re.search(r"\bTEST_FAULT_FIRED\b", raw)) or (
        h is not None and h[0] == "x-wrong-generation")
    active = None
    if h is not None and h[0] == "x-wrong-generation":
        # the fatal itself proves the frame passed the lorieGateAActive() gate,
        # because the tuple check is only reached when active
        active = True
    elif h is None:
        active = False  # no fatal and no drop marker: it was dropped or never sent
    return {"legitimate_ready_sent": legit,
            "stale_frame_sent": sent,
            "gate_a_active_on_arrival": active,
            "stale_tuple_accepted": False if h else None}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", required=True)
    ap.add_argument("--cell", required=True)
    ap.add_argument("--x-stat-before", default="")
    ap.add_argument("--x-stat-after", default="")
    ap.add_argument("--act-stat-before", default="")
    ap.add_argument("--act-stat-after", default="")
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
    fdp = tri[a.fd_had_prev]

    b0 = boundary_record(rd(a.x_stat_before), rd(a.act_stat_before), raw, fdp)
    b1 = boundary_record(rd(a.x_stat_after), rd(a.act_stat_after), raw, fdp)
    (ev / "identity-boundaries.jsonl").write_text(
        json.dumps(b0) + "\n" + json.dumps(b1) + "\n")

    if a.cell == "R9-COLD-2":
        (ev / "renderer-fatal.json").write_text(json.dumps(renderer_fatal(raw), indent=2) + "\n")
        (ev / "x-survival.json").write_text(
            json.dumps(x_survival(tri[a.x_alive_after], raw), indent=2) + "\n")
        reg = registry_state(summ)
        (ev / "registry-at-bump.json").write_text(json.dumps(
            {"pending_count": None, "last_submitted_serial": None, **reg}, indent=2) + "\n")
    elif a.cell == "R9-F1":
        (ev / "stale-replay.json").write_text(json.dumps(stale_replay(raw), indent=2) + "\n")
    elif a.cell == "R9-F2":
        (ev / "registry-fresh.json").write_text(json.dumps(registry_state(summ), indent=2) + "\n")

    print(f"R9_EVIDENCE_DERIVED {a.cell}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
