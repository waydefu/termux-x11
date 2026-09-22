#!/usr/bin/env python3
"""R9 generation-boundary judge.

Scope: the three cells that V2-R9-FIXTURE proved constructible. All three are
fatal-path cells, because Gate A has no reachable clean same-process generation
boundary (see planning-v2/r9-fixture/COLD1-ROUTE-SEARCH.md).

Verdict codes match R8's so the orchestration is unchanged:
    0 PASS   1 FAIL   2 INVALID   3 BLOCKED

INVALID vs FAIL is the distinction that matters (plan §8.8):
    INVALID  the payload never reached the path being tested. No information.
    FAIL     it reached it and was mishandled. Information.
A cell that silently dropped its own construction is INVALID, never PASS.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "r8"))
from r8_obs_stream import obs_loads  # GAP-8: duplicate "phase" key safe

PASS, FAIL, INVALID, BLOCKED = 0, 1, 2, 3

HALT_PAT = re.compile(r"GATEA_FATAL_HALT what=(\S+) reason=(\d+)")
OBS_PAT = re.compile(r"R8_OBS\s+(\{.*\})\s*$")
RING_EVENT_PAT = re.compile(r"GATEA_EVENT .*\bevent=(\d+)\b")

EVENT_LEASE_GPU_OWNED = 5

IDENTITY_FIELDS = (
    "x_pid", "x_starttime",
    "activity_pid", "activity_starttime",
    "shared_nonce", "shared_generation",
    "renderer_bound_nonce", "renderer_bound_generation",
    "epoch_id",
    "x_fd_table_had_previous_conn_fd",
)


class Verdict(Exception):
    def __init__(self, code: int, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


def load_json(path: Path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return "INVALID_JSON"


def read_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except FileNotFoundError:
        return ""


def load_obs(path: Path) -> list[dict]:
    rows = []
    for line in read_text(path).splitlines():
        if not line.strip():
            continue
        try:
            rows.append(obs_loads(line))
        except json.JSONDecodeError:
            raise Verdict(INVALID, "OBS_JSON_MALFORMED")
    return rows


def phases(rows: list[dict], role: str) -> list[str]:
    return [r.get("phase") for r in rows if r.get("role") == role]


def require_phase(rows: list[dict], role: str, phase: str, reason: str) -> list[dict]:
    hits = [r for r in rows if r.get("role") == role and r.get("phase") == phase]
    if not hits:
        raise Verdict(INVALID, reason)
    return hits


# ---------------------------------------------------------------- common gates

def check_artifact(ev: Path, manifest: dict | None) -> None:
    bind = load_json(ev / "artifact-binding.json")
    if bind in (None, "INVALID_JSON"):
        raise Verdict(INVALID, "ARTIFACT_BINDING_MISSING")
    if manifest:
        for k in ("source_sha", "apk_sha256", "build_id", "signer"):
            if k in manifest and bind.get(k) is not None and bind[k] != manifest[k]:
                raise Verdict(BLOCKED, "ARTIFACT_MISMATCH_PRESTART")


def check_stable(ev: Path) -> None:
    before = load_json(ev / "stable-before.json")
    after = load_json(ev / "stable-after.json")
    if before in (None, "INVALID_JSON") or after in (None, "INVALID_JSON"):
        raise Verdict(INVALID, "STABLE_EVIDENCE_MISSING")
    for k in ("pid", "cmdline", "versionName", "versionCode", "lastUpdateTime"):
        if before.get(k) is None or after.get(k) is None:
            raise Verdict(INVALID, "STABLE_EVIDENCE_MISSING")
        if before[k] != after[k]:
            raise Verdict(FAIL, "STABLE_CHANGED")


def load_boundaries(ev: Path) -> list[dict]:
    """Per-boundary identity records, one JSON object per line.

    r9-identity-contract.json makes every field mandatory. A missing field is
    NOT_OBSERVED and must never be defaulted, so absence is INVALID.
    """
    rows = []
    for line in read_text(ev / "identity-boundaries.jsonl").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            raise Verdict(INVALID, "IDENTITY_JSON_MALFORMED")
    if not rows:
        raise Verdict(INVALID, "IDENTITY_BOUNDARIES_MISSING")
    for r in rows:
        for f in IDENTITY_FIELDS:
            if f not in r or r[f] is None:
                raise Verdict(INVALID, f"IDENTITY_FIELD_MISSING_{f}")
    return rows


def halt(ev: Path) -> tuple[str, int] | None:
    m = HALT_PAT.search(read_text(ev / "raw-logcat.txt"))
    return (m.group(1), int(m.group(2))) if m else None


def require_exact_fatal(ev: Path, what: str, reason: int) -> None:
    h = halt(ev)
    if h is None:
        raise Verdict(INVALID, "EXPECTED_FATAL_ABSENT")
    if h[0] != what or h[1] != reason:
        raise Verdict(FAIL, f"WRONG_FATAL_{h[0]}_{h[1]}")


def ring_events(ev: Path) -> list[int]:
    """Every Gate A telemetry event this attempt produced, from BOTH sinks.

    gatea-ring.txt is a post-mortem snapshot: lorieGateADumpSummary writes it, and
    that only runs on a fatal, a clean close or a terminate. A cell that leaves X
    healthy (R9-F2) produces no file at all, and the file is a bounded ring that can
    overflow even when it does exist. The gatea-telemetry logcat stream carries the
    same GATEA_EVENT lines live and is windowed to this attempt by the runner's
    `logcat -T <since>`, so it is the more complete of the two, not a fallback.
    Union, so neither source alone can make an event disappear."""
    text = read_text(ev / "gatea-ring.txt") + "\n" + read_text(ev / "raw-logcat.txt")
    return [int(m) for m in RING_EVENT_PAT.findall(text)]


# ------------------------------------------------------------------- the cells

# R9-COLD-2 was REMOVED from the runtime packet on 2026-09-22 as
# SOURCE-PROVEN / RUNTIME-NOT-CONSTRUCTIBLE — see
# planning-v2/r9-fixture/COLD2-ROUTE-SEARCH.md. Its expected fatal
# x-bump-unterminal (cmdentrypoint.cpp:391) needs a SECOND
# lorieActivityConnected() with generation != 0, and no X process can reach one:
# X outlives its Activity only when lorieGateAActive() is already false
# (InitOutput.c:620-624), a clean close zeroes sessionNonce too
# (InitOutput.c:3334-3335), and every fatal publisher that would clear the gate
# exits X in the same breath (lorie.h:1414-1416; InitOutput.c:3539 ->
# lorie.h:296-297 -> InitOutput.c:3546 -> :3212-3217).
#
# The judge is kept as a hard refusal rather than deleted: a removed cell must
# never be able to yield PASS, FAIL or INVALID. BLOCKED is the only answer.
REMOVED_CELLS = {
    "R9-COLD-2": "COLD2-ROUTE-SEARCH.md",
    "R9-COLD-1": "COLD1-ROUTE-SEARCH.md",
    "R9-COLD-3": "COLD1-ROUTE-SEARCH.md",
    "R9-WARM-1": "V2-R9-DESIGN.md",
    "R9-WARM-2": "V2-R9-DESIGN.md",
    "R9-WARM-3": "V2-R9-DESIGN.md",
}


def judge_removed(cell: str) -> None:
    raise Verdict(BLOCKED,
                  f"CELL_REMOVED_SOURCE_PROVEN_NOT_CONSTRUCTIBLE_{cell}"
                  f"_see_{REMOVED_CELLS[cell]}")


def judge_f1(ev: Path, xrows: list[dict], rrows: list[dict],
             bounds: list[dict]) -> None:
    # gateAValidateImport must have run at all
    ready = [r for r in rrows if r.get("phase") in ("R_READY_SENT", "VALIDATE_TERMINAL_READY")]
    stale = load_json(ev / "stale-replay.json")
    if stale in (None, "INVALID_JSON"):
        raise Verdict(INVALID, "STALE_REPLAY_EVIDENCE_MISSING")
    if not stale.get("legitimate_ready_sent"):
        raise Verdict(INVALID, "VALIDATE_IMPORT_NOT_REACHED")
    if not stale.get("stale_frame_sent"):
        raise Verdict(INVALID, "STALE_FRAME_NOT_SENT")

    # the silent-drop trap: handleGateARecord returns BEFORE the tuple check when
    # Gate A is inactive (cmdentrypoint.cpp:629-631). A dropped frame proves
    # nothing, so it is INVALID_CONSTRUCTION and never a pass.
    if not stale.get("gate_a_active_on_arrival"):
        raise Verdict(INVALID, "STALE_FRAME_SILENTLY_DROPPED")

    require_exact_fatal(ev, "x-wrong-generation", 6)

    if stale.get("stale_tuple_accepted"):
        raise Verdict(FAIL, "STALE_TUPLE_ACCEPTED")


def judge_f2(ev: Path, xrows: list[dict], rrows: list[dict],
             bounds: list[dict]) -> None:
    pre = load_json(ev / "f1-precondition.json")
    if pre in (None, "INVALID_JSON"):
        raise Verdict(BLOCKED, "F1_PRECONDITION_EVIDENCE_MISSING")
    if not pre.get("f1_expected_fatal_observed"):
        # §8.8: without F1's expected fatal, F2 is BLOCKED. Manufacturing another
        # fatal to satisfy this is forbidden, so this is never INVALID or FAIL.
        raise Verdict(BLOCKED, "F1_EXPECTED_FATAL_ABSENT")

    if len(bounds) < 2:
        raise Verdict(INVALID, "BOUNDARY_RECORDS_INSUFFICIENT")
    a, b = bounds[0], bounds[-1]

    if a["shared_nonce"] == b["shared_nonce"]:
        raise Verdict(FAIL, "NONCE_NOT_REFRESHED")
    if int(b["shared_nonce"]) == 0:
        raise Verdict(FAIL, "SESSION_NONCE_ZEROED")
    if (a["x_pid"], a["x_starttime"]) == (b["x_pid"], b["x_starttime"]):
        raise Verdict(INVALID, "X_DID_NOT_RESTART")
    if (a["activity_pid"], a["activity_starttime"]) == \
       (b["activity_pid"], b["activity_starttime"]):
        raise Verdict(INVALID, "ACTIVITY_DID_NOT_RESTART")

    reg = load_json(ev / "registry-fresh.json")
    if reg in (None, "INVALID_JSON"):
        raise Verdict(INVALID, "REGISTRY_EVIDENCE_MISSING")
    # policy.null_means: NOT OBSERVED, never inferred, never defaulted. The counters
    # come from lorieGateADumpSummary, which only runs on a fatal, a clean close or a
    # terminate; a run that produced no dump has no counters, and reading that
    # absence as "empty" would turn a missing measurement into a pass.
    if reg.get("x_entries") is None or reg.get("renderer_ready_entries") is None:
        raise Verdict(INVALID, "REGISTRY_COUNTERS_NOT_OBSERVED")
    if reg.get("x_entries") or reg.get("renderer_ready_entries"):
        raise Verdict(FAIL, "STALE_RESIDUE_PRESENT")

    if halt(ev) is not None:
        raise Verdict(FAIL, "UNEXPECTED_FATAL_IN_FRESH_SESSION")

    # a clean direct SUCCESS means the GPU path was actually entered
    if EVENT_LEASE_GPU_OWNED not in ring_events(ev):
        raise Verdict(FAIL, "NO_DIRECT_SUCCESS_EVENT5_ABSENT")


DISPATCH = {"R9-F1": judge_f1, "R9-F2": judge_f2}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", required=True)
    ap.add_argument("--manifest")
    ap.add_argument("--spec")
    ap.add_argument("--cell", required=True)
    ap.add_argument("--output")
    args = ap.parse_args()

    ev = Path(args.evidence)
    cell = args.cell
    code, reason = PASS, "ACCEPT"
    try:
        if cell in REMOVED_CELLS:
            judge_removed(cell)
        if cell not in DISPATCH:
            raise Verdict(BLOCKED, f"UNKNOWN_CELL_{cell}")
        manifest = load_json(Path(args.manifest)) if args.manifest else None
        if manifest == "INVALID_JSON":
            raise Verdict(BLOCKED, "ARTIFACT_MISMATCH_PRESTART")
        check_artifact(ev, manifest)
        check_stable(ev)
        xrows = load_obs(ev / "x-observations.jsonl")
        rrows = load_obs(ev / "renderer-observations.jsonl")
        bounds = load_boundaries(ev)
        DISPATCH[cell](ev, xrows, rrows, bounds)
    except Verdict as v:
        code, reason = v.code, v.reason

    name = {PASS: "R9_PASS", FAIL: "R9_FAIL",
            INVALID: "R9_INVALID", BLOCKED: "R9_BLOCKED"}[code]
    print(f"{name} {cell if code == PASS else reason}")
    if args.output:
        Path(args.output).write_text(json.dumps({
            "exit": code, "verdict": name, "cell": cell, "reason": reason,
        }, indent=2) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
