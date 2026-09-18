#!/usr/bin/env python3
"""R8 clean-cell orchestration v2.

Does not emit, rewrite, or synthesize producer END records.
Does not invoke the frozen judge. Callers collect producer R8_OBS then ask
whether judge is permitted.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

OBS_PAT = re.compile(r"R8_OBS\s+(\{.*\})\s*$")

STATES = (
    "PRESTART",
    "X_STARTED",
    "FIXTURE_ACTIVE",
    "CLIENT_CONSTRUCTION_COMPLETE",
    "CLEAN_SHUTDOWN_REQUESTED",
    "CLOSESCREEN_OBSERVED",
    "GENERATION_CLOSE_OBSERVED",
    "RENDERER_UNBOUND",
    "PRODUCERS_FINALIZED",
    "RAW_EVIDENCE_FROZEN",
    "JUDGE",
    "CLEANUP",
)

CLASS_A = frozenset({
    "R8-C1", "R8-C3-window", "R8-C3-disconnect", "R8-C4",
    "R8-C5-overflow", "R8-D",
})
CLASS_B = frozenset({"R8-C2", "R8-C5-full"})
CLASS_P = frozenset({"R8-P1", "R8-P2"})
CLEAN_CELLS = CLASS_A | CLASS_B

SHUTDOWN_SOURCE = {
    "control": "LORIE-R8-TEST Terminate opcode 3",
    "handler": "lorie/src/main/cpp/lorie/lorie_r8_test.c ProcLorieR8Terminate",
    "giveup": "lorie/src/main/cpp/xserver/os/utils.c GiveUp sets DE_TERMINATE",
    "close_screen": "lorie/src/main/cpp/xserver/dix/main.c (*CloseScreen) then ddxGiveUp(EXIT_NO_ERROR)",
    "x_end": "InitOutput.c ddxGiveUp: UnlockServer then lorieR8ObsEnd(x) then exit",
    "r_end": "renderer unbind+surface+loop then lorieR8ObsEnd(r)",
    "not": ["SIGKILL", "pkill", "am force-stop Stable", "broad :3 match",
            "runner lorieR8ObsEnd", "SIGTERM as C1 last-client path"],
}


def cell_class(cell: str) -> str:
    if cell in CLASS_A:
        return "A"
    if cell in CLASS_B:
        return "B"
    if cell in CLASS_P:
        return "P"
    raise ValueError(f"unknown_cell {cell}")


def parse_obs_text(text: str) -> tuple[list[dict], list[dict]]:
    xs, rs = [], []
    for line in (text or "").splitlines():
        m = OBS_PAT.search(line)
        if not m:
            continue
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        role = obj.get("role")
        if role == "x":
            xs.append(obj)
        elif role == "r":
            rs.append(obj)
    return xs, rs


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def role_phases(rows: list[dict], role: str) -> list[str]:
    return [r.get("phase") for r in rows if r.get("role") == role]


def _end_ok(rows: list[dict], role: str) -> tuple[bool, str]:
    role_rows = [r for r in rows if r.get("role") == role]
    begins = [r for r in role_rows if r.get("phase") == "BEGIN"]
    ends = [r for r in role_rows if r.get("phase") == "END"]
    recs = [r for r in role_rows if r.get("phase") not in ("BEGIN", "END")]
    if len(begins) != 1:
        return False, f"MISSING_BEGIN_{role}" if not begins else f"MULTI_BEGIN_{role}"
    if len(ends) != 1:
        return False, f"MISSING_END_{role}" if not ends else f"MULTI_END_{role}"
    actual = ends[-1].get("actual_count")
    if actual is None:
        return False, f"END_COUNT_NULL_{role}"
    if int(actual) != len(recs):
        return False, f"END_COUNT_MISMATCH_{role}"
    return True, "ok"


def producer_finalized(xrows: list[dict], rrows: list[dict], cell: str) -> tuple[bool, str]:
    if cell in CLASS_P:
        return True, "fatal_no_clean_end_required"
    ok, reason = _end_ok(xrows, "x")
    if not ok:
        return False, reason
    ok, reason = _end_ok(rrows, "r")
    if not ok:
        return False, reason
    xph = role_phases(xrows, "x")
    rph = role_phases(rrows, "r")
    if "X_CLOSE_ENTER" not in xph:
        return False, "MISSING_X_CLOSE_ENTER"
    if "X_CLOSE_RESULT" not in xph:
        return False, "MISSING_X_CLOSE_RESULT"
    if "R_UNBOUND_FINAL" not in rph:
        return False, "MISSING_R_UNBOUND_FINAL"
    return True, "ok"


def ends_are_producer_only(raw_text: str, jsonl_rows: list[dict]) -> tuple[bool, str]:
    """Reject END rows that are not present as producer R8_OBS in raw text."""
    raw_x, raw_r = parse_obs_text(raw_text)
    raw_ends = [
        (r.get("role"), r.get("producer_seq"), r.get("actual_count"))
        for r in raw_x + raw_r if r.get("phase") == "END"
    ]
    json_ends = [
        (r.get("role"), r.get("producer_seq"), r.get("actual_count"))
        for r in jsonl_rows if r.get("phase") == "END"
    ]
    for item in json_ends:
        if item not in raw_ends:
            return False, "COLLECTOR_SYNTHETIC_END"
    return True, "ok"


def fixture_markers(text: str) -> dict:
    t = text or ""
    return {
        "client_ok": "CLIENT_OK" in t,
        "client_hold": "CLIENT_HOLD" in t,
        "hold_for_term": "HOLD_FOR_TERM" in t,
        "hangup": "X_HANGUP_AFTER_HOLD" in t or "X_ERROR_AFTER_HOLD" in t
            or "X_HANGUP_AFTER_TERMINATE" in t,
        "terminate": "TERMINATE_SENT" in t or "TERMINATE_ACK" in t
            or "X_HANGUP_AFTER_TERMINATE" in t,
        "c5_full": "C5_FULL registered=" in t,
        "pre_term": "CHECKPOINT phase=5" in t,
        "c5_registered": None,
    }


def hold_ready(cell: str, fixture_text: str) -> tuple[bool, str]:
    m = fixture_markers(fixture_text)
    if cell == "R8-C2":
        if not m["client_hold"]:
            return False, "MISSING_CLIENT_HOLD"
        return True, "ok"
    if cell == "R8-C5-full":
        if "C5_FULL registered=" not in (fixture_text or ""):
            return False, "MISSING_C5_FULL"
        if "CHECKPOINT phase=5" not in (fixture_text or ""):
            return False, "MISSING_PRE_TERM"
        if not m["client_hold"]:
            return False, "MISSING_CLIENT_HOLD"
        return True, "ok"
    return False, "not_hold_cell"


def class_a_ready(cell: str, fixture_text: str) -> tuple[bool, str]:
    if cell not in CLASS_A:
        return False, "not_class_a"
    if "CLIENT_OK" not in (fixture_text or ""):
        return False, "MISSING_CLIENT_OK"
    return True, "ok"


def permit_judge(
    cell: str,
    *,
    shutdown_requested: bool,
    xrows: list[dict],
    rrows: list[dict],
    fixture_text: str,
    fixture_alive: bool | None,
    fixture_killed_by_runner: bool,
    x_alive_after_construction: bool | None,
    raw_text: str | None = None,
    jsonl_rows: list[dict] | None = None,
) -> tuple[bool, str]:
    cls = cell_class(cell)
    if fixture_killed_by_runner:
        return False, "HOLD_CLIENT_KILLED_BY_RUNNER"
    if cls == "P":
        return True, "fatal_path"
    if cls == "A":
        ok, reason = class_a_ready(cell, fixture_text)
        if not ok:
            return False, reason
        if x_alive_after_construction is False:
            if not fixture_markers(fixture_text).get("terminate"):
                return False, "X_DIED_BEFORE_SHUTDOWN"
    if cls == "B":
        ok, reason = hold_ready(cell, fixture_text)
        if not ok:
            return False, reason
        if fixture_alive is False and not shutdown_requested:
            return False, "HOLD_CLIENT_DIED_EARLY"
        if fixture_alive is True and not shutdown_requested:
            return False, "HOLD_STILL_ACTIVE"
        if fixture_alive is None:
            return False, "HOLD_FIXTURE_STATE_UNKNOWN"
        if shutdown_requested and fixture_alive is True:
            return False, "HOLD_CLIENT_NO_HANGUP"
    if not shutdown_requested:
        return False, "JUDGE_BEFORE_CLEAN_SHUTDOWN"
    ok, reason = producer_finalized(xrows, rrows, cell)
    if not ok:
        return False, reason
    if raw_text is not None and jsonl_rows is not None:
        ok, reason = ends_are_producer_only(raw_text, jsonl_rows)
        if not ok:
            return False, reason
    return True, "ok"


def append_state(path: Path, state: str, **fields) -> None:
    if state not in STATES:
        raise ValueError(f"unknown_state {state}")
    rec = {"state": state}
    rec.update(fields)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")


def scan_files(raw_paths: list[Path]) -> tuple[list[dict], list[dict], str]:
    blob = ""
    for p in raw_paths:
        if p.is_file():
            blob += p.read_text(encoding="utf-8", errors="replace") + "\n"
    return (*parse_obs_text(blob), blob)


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_emit = sub.add_parser("emit-state")
    p_emit.add_argument("--file", required=True)
    p_emit.add_argument("--state", required=True)
    p_emit.add_argument("--extra", default="{}")
    p_scan = sub.add_parser("scan")
    p_scan.add_argument("--raw", action="append", default=[])
    p_scan.add_argument("--out", required=True)
    p_permit = sub.add_parser("permit-judge")
    p_permit.add_argument("--cell", required=True)
    p_permit.add_argument("--evidence", required=True)
    p_permit.add_argument("--shutdown-requested", type=int, default=0)
    p_permit.add_argument("--fixture-alive", default="unknown")
    p_permit.add_argument("--fixture-killed-by-runner", type=int, default=0)
    p_permit.add_argument("--x-alive-after-construction", default="unknown")
    p_wait = sub.add_parser("wait-finalized")
    p_wait.add_argument("--cell", required=True)
    p_wait.add_argument("--evidence", required=True)
    p_wait.add_argument("--deadline-s", type=float, default=8.0)
    p_wait.add_argument("--interval-s", type=float, default=0.1)
    args = ap.parse_args()
    if args.cmd == "emit-state":
        extra = json.loads(args.extra)
        append_state(Path(args.file), args.state, **extra)
        print(args.state)
        return 0
    if args.cmd == "scan":
        xrows, rrows, blob = scan_files([Path(p) for p in args.raw])
        xph = role_phases(xrows, "x")
        rph = role_phases(rrows, "r")
        out = {
            "x_begin": xph.count("BEGIN"),
            "x_end": xph.count("END"),
            "r_begin": rph.count("BEGIN"),
            "r_end": rph.count("END"),
            "x_close_enter": "X_CLOSE_ENTER" in xph,
            "x_close_result": "X_CLOSE_RESULT" in xph,
            "r_unbound_final": "R_UNBOUND_FINAL" in rph,
            "x_count": len(xrows),
            "r_count": len(rrows),
        }
        Path(args.out).write_text(json.dumps(out, indent=2) + "\n")
        print(json.dumps(out))
        return 0
    if args.cmd == "wait-finalized":
        ev = Path(args.evidence)
        raw_paths = [
            ev / "raw-logcat.txt", ev / "gatea-ring.txt", ev / "gatea-summary.txt",
            ev / "x3-launcher.raw.log",
        ]
        deadline = time.time() + args.deadline_s
        last = "WAITING"
        while time.time() < deadline:
            xrows, rrows, _blob = scan_files(raw_paths)
            ok, reason = producer_finalized(xrows, rrows, args.cell)
            last = reason
            snap = {
                "ok": ok, "reason": reason,
                "x_end": role_phases(xrows, "x").count("END"),
                "r_end": role_phases(rrows, "r").count("END"),
                "x_close_enter": "X_CLOSE_ENTER" in role_phases(xrows, "x"),
                "x_close_result": "X_CLOSE_RESULT" in role_phases(xrows, "x"),
                "r_unbound_final": "R_UNBOUND_FINAL" in role_phases(rrows, "r"),
            }
            (ev / "producer-finalization.json").write_text(
                json.dumps(snap, indent=2) + "\n")
            if ok:
                print("FINALIZED " + reason)
                return 0
            time.sleep(args.interval_s)
        print("NOT_FINALIZED " + last)
        return 2
    ev = Path(args.evidence)
    raw_paths = [
        ev / "raw-logcat.txt", ev / "gatea-ring.txt", ev / "gatea-summary.txt",
        ev / "x3-launcher.raw.log",
    ]
    xrows, rrows, blob = scan_files(raw_paths)
    xrows += load_jsonl(ev / "x-observations.jsonl")
    rrows += load_jsonl(ev / "renderer-observations.jsonl")
    fixture = ""
    for name in ("fixture.stdout", "fixture.jsonl", "fixture.stderr"):
        p = ev / name
        if p.is_file():
            fixture += p.read_text(encoding="utf-8", errors="replace") + "\n"
    fa = args.fixture_alive
    fixture_alive = None if fa == "unknown" else fa in ("1", "true", "yes")
    xa = args.x_alive_after_construction
    x_alive = None if xa == "unknown" else xa in ("1", "true", "yes")
    ok, reason = permit_judge(
        args.cell,
        shutdown_requested=bool(args.shutdown_requested),
        xrows=xrows,
        rrows=rrows,
        fixture_text=fixture,
        fixture_alive=fixture_alive,
        fixture_killed_by_runner=bool(args.fixture_killed_by_runner),
        x_alive_after_construction=x_alive,
        raw_text=blob,
        jsonl_rows=xrows + rrows,
    )
    print(("PERMIT " if ok else "REFUSE ") + reason)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
