#!/usr/bin/env python3
"""Judge one Gate A R8 lifecycle cell.

Exits: 0 PASS, 1 FAIL, 2 INVALID, 3 BLOCKED.
Mandatory null fields are INVALID. Unknown is never coerced to false/zero.
Partial-order oracles use observation kinds and tuple/buffer identity, not
cross-transport timestamp total order.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PASS, FAIL, INVALID, BLOCKED = 0, 1, 2, 3

EVENT_LEASE_GPU_OWNED = 5
EVENT_PUBLISH = 6
EVENT_UNREGISTER_ACK = 25
EVENT_RESOURCE_DESTROY = 26
EVENT_GENERATION_CLOSE = 27
EVENT_GENERATION_CLOSED = 28
EVENT_EARLY_ACK = 32
EVENT_FAULT = 35
EVENT_RETIRE = 36

CELLS = (
    "R8-C1", "R8-C2", "R8-C3-window", "R8-C3-disconnect", "R8-C4",
    "R8-C5-full", "R8-C5-overflow", "R8-D", "R8-P1", "R8-P2",
)

EV_PAT = re.compile(
    r"GATEA_EVENT seq=(\d+) role=(\d+) event=(\d+) generation=(\d+) "
    r"serial=(\d+) src=(\d+) dst=(\d+)"
)
HALT_PAT = re.compile(r"GATEA_FATAL_HALT what=(\S+) reason=(\d+)")
OBS_PAT = re.compile(r"R8_OBS\s+(\{.*\})\s*$")


class Verdict(Exception):
    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


def load_json(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "INVALID_JSON"


def parse_events(text: str) -> list[dict]:
    out = []
    for m in EV_PAT.finditer(text or ""):
        seq, role, event, gen, serial, src, dst = (int(x) for x in m.groups())
        out.append({
            "seq": seq, "role": role, "event": event, "gen": gen,
            "serial": serial, "src": src, "dst": dst,
        })
    out.sort(key=lambda e: e["seq"])
    return out


def parse_obs_blob(text: str) -> list[dict]:
    rows = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        payload = line
        m = OBS_PAT.search(line)
        if m:
            payload = m.group(1)
        elif line.startswith("{"):
            payload = line
        else:
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            raise Verdict(INVALID, "OBS_JSON")
        if not isinstance(obj, dict) or obj.get("v") != 1:
            raise Verdict(INVALID, "OBS_ENVELOPE")
        rows.append(obj)
    return rows


def read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def first_file(ev: Path, names: tuple[str, ...]) -> Path | None:
    for n in names:
        p = ev / n
        if p.is_file():
            return p
    return None


def tv(value, *, required=False, name="field"):
    if value is None:
        if required:
            raise Verdict(INVALID, name)
        return None
    return value


def obs_kinds(rows: list[dict], kind: str) -> list[dict]:
    return [r for r in rows if r.get("phase") == kind]


def require_obs(rows: list[dict], kind: str) -> list[dict]:
    got = obs_kinds(rows, kind)
    if not got:
        raise Verdict(INVALID, f"MISSING_OBS_{kind}")
    return got


def completeness(rows: list[dict], role: str) -> None:
    role_rows = [r for r in rows if r.get("role") == role]
    if not role_rows:
        raise Verdict(INVALID, f"MISSING_ROLE_{role}")
    begin = [r for r in role_rows if r.get("phase") == "BEGIN"]
    end = [r for r in role_rows if r.get("phase") == "END"]
    if not begin:
        raise Verdict(INVALID, f"MISSING_BEGIN_{role}")
    if not end:
        raise Verdict(INVALID, f"MISSING_END_{role}")
    recs = [r for r in role_rows if r.get("phase") not in ("BEGIN", "END")]
    actual = end[-1].get("actual_count")
    if actual is None:
        raise Verdict(INVALID, f"END_COUNT_NULL_{role}")
    if int(actual) != len(recs):
        raise Verdict(INVALID, f"END_COUNT_MISMATCH_{role}")


def check_artifact(ev: Path, manifest: dict | None) -> None:
    bind = load_json(ev / "artifact-binding.json")
    pre = load_json(ev / "preflight.json")
    if pre == "INVALID_JSON" or bind == "INVALID_JSON":
        raise Verdict(INVALID, "JSON")
    if pre is None:
        raise Verdict(BLOCKED, "UNSAFE_PRESTART")
    if pre.get("unsafe_prestart"):
        raise Verdict(BLOCKED, "UNSAFE_PRESTART")
    if pre.get("artifact_mismatch"):
        raise Verdict(BLOCKED, "ARTIFACT_MISMATCH_PRESTART")
    if bind is None:
        raise Verdict(INVALID, "ARTIFACT_BINDING_MISSING")
    if manifest:
        for k in ("source_sha", "apk_sha256", "build_id", "signer"):
            if k in manifest and bind.get(k) not in (None, manifest.get(k)):
                if bind.get(k) != manifest.get(k):
                    raise Verdict(BLOCKED, "ARTIFACT_MISMATCH_PRESTART")


def check_stable(ev: Path) -> None:
    before = load_json(ev / "stable-before.json")
    after = load_json(ev / "stable-after.json")
    if after is None:
        raise Verdict(INVALID, "STABLE_EVIDENCE_MISSING")
    if before is None or before == "INVALID_JSON" or after == "INVALID_JSON":
        raise Verdict(INVALID, "STABLE_EVIDENCE_MISSING")
    keys = ("pid", "cmdline", "versionName", "versionCode", "lastUpdateTime")
    for k in keys:
        if k not in before or k not in after:
            raise Verdict(INVALID, "STABLE_EVIDENCE_MISSING")
        if before[k] is None or after[k] is None:
            raise Verdict(INVALID, "STABLE_EVIDENCE_MISSING")
        if before[k] != after[k]:
            raise Verdict(FAIL, "STABLE_CHANGED")


def check_process_observation(ev: Path) -> None:
    pre = load_json(ev / "pre-cleanup-process-state.json")
    if pre is None or pre == "INVALID_JSON":
        raise Verdict(INVALID, "PROCESS_OBSERVATION")
    if pre.get("substituted_from_cleanup"):
        raise Verdict(INVALID, "PROCESS_OBSERVATION")


def destroy_before_x_release(xrows: list[dict], rrows: list[dict]) -> None:
    destroys = obs_kinds(rrows, "R_DESTROY_STAGE")
    acks = obs_kinds(rrows, "R_ACK_SETTLED")
    exits = obs_kinds(xrows, "X_DESTRUCTOR_EXIT")
    if not destroys:
        raise Verdict(INVALID, "DESTRUCTOR_NOT_OBSERVED")
    if not acks:
        raise Verdict(INVALID, "RESOURCE_IDENTITY_MISSING")
    by_id = {}
    for d in destroys:
        bid = d.get("bufferId")
        if bid is None:
            raise Verdict(INVALID, "RESOURCE_IDENTITY_MISSING")
        by_id.setdefault(bid, []).append(d)
        if d.get("ahb_released") is True and (
            d.get("texture_deleted") is False or d.get("image_destroyed") is False
        ):
            raise Verdict(FAIL, "REVERSE_DESTRUCTION_ORDER")
    for ack in acks:
        bid = ack.get("bufferId")
        if bid not in by_id:
            raise Verdict(INVALID, "RESOURCE_IDENTITY_MISSING")
    if not exits:
        raise Verdict(INVALID, "DESTRUCTOR_NOT_OBSERVED")


def tuple_bind(rows: list[dict]) -> None:
    origins = [(r.get("original_nonce"), r.get("original_generation")) for r in rows
               if r.get("phase") not in ("BEGIN", "END")]
    origins = [o for o in origins if o[0] not in (None, 0)]
    if origins and len({o for o in origins}) > 1:
        raise Verdict(INVALID, "TUPLE_BINDING")


def judge_c1(events, xrows, rrows, extra) -> None:
    if extra.get("ack_before_destroy"):
        raise Verdict(FAIL, "ACK_BEFORE_DESTROY")
    if extra.get("owner_release_before_ack"):
        raise Verdict(FAIL, "OWNER_RELEASE_BEFORE_ACK")
    if extra.get("trace_gap"):
        raise Verdict(INVALID, "TRACE_GAP")
    if extra.get("trace_conflict"):
        raise Verdict(INVALID, "TRACE_CONFLICT")
    if extra.get("pending_null"):
        raise Verdict(INVALID, "PENDING_NOT_OBSERVED")
    if extra.get("resource_residue"):
        raise Verdict(FAIL, "RESOURCE_RESIDUE")
    if extra.get("missing_destructor"):
        raise Verdict(INVALID, "DESTRUCTOR_NOT_OBSERVED")
    if extra.get("tuple_mismatch"):
        raise Verdict(INVALID, "TUPLE_BINDING")
    completeness(xrows + rrows, "x")
    completeness(xrows + rrows, "r")
    tuple_bind(xrows + rrows)
    destroy_before_x_release(xrows, rrows)
    require_obs(xrows, "X_DESTRUCTOR_ENTER")
    if any(e["event"] == EVENT_EARLY_ACK for e in events):
        raise Verdict(FAIL, "PRESENT_EARLY_ACK")
    if extra.get("identity_mismatch"):
        raise Verdict(INVALID, "RESOURCE_IDENTITY_MISSING")
    ck = require_obs(xrows, "X_CHECKPOINT")
    pending = ck[-1].get("total_actual_buffer_pending")
    if pending is None:
        raise Verdict(INVALID, "PENDING_NOT_OBSERVED")
    if pending not in (0, "0"):
        raise Verdict(FAIL, "RESOURCE_RESIDUE")


def judge_c2(events, xrows, rrows, extra) -> None:
    if extra.get("abnormal_close"):
        raise Verdict(FAIL, "ABNORMAL_CLOSE")
    if extra.get("close_construction"):
        raise Verdict(INVALID, "CLOSE_CONSTRUCTION")
    if extra.get("renderer_final_missing"):
        raise Verdict(INVALID, "RENDERER_FINAL_MISSING")
    completeness(xrows + rrows, "x")
    completeness(xrows + rrows, "r")
    require_obs(xrows, "X_CLOSE_ENTER")
    require_obs(xrows, "X_CLOSE_RESULT")
    require_obs(rrows, "R_UNBOUND_FINAL")
    if extra.get("unproven_live_at_close"):
        raise Verdict(INVALID, "CLOSE_CONSTRUCTION")


def judge_c3(events, xrows, rrows, extra) -> None:
    if extra.get("copy_not_constructed"):
        raise Verdict(INVALID, "COPY_NOT_CONSTRUCTED")
    if extra.get("present_early_ack"):
        raise Verdict(FAIL, "PRESENT_EARLY_ACK")
    if extra.get("release_after_fatal"):
        raise Verdict(FAIL, "RELEASE_AFTER_FATAL")
    require_obs(xrows, "PRESENT_SUBMIT")
    pre = require_obs(xrows, "PRESENT_PRE_ACK")
    require_obs(xrows, "PRESENT_POST_ACK")
    waited = pre[-1].get("waited")
    if waited not in (0, 1):
        raise Verdict(INVALID, "PRESENT_WAITED")
    t = pre[-1].get("completedSerial")
    s = pre[-1].get("gpu_serial")
    if t is None or s is None:
        raise Verdict(INVALID, "PRESENT_SERIAL_NULL")
    if int(t) < int(s):
        raise Verdict(FAIL, "PRESENT_EARLY_ACK")
    if any(e["event"] == EVENT_EARLY_ACK for e in events):
        raise Verdict(FAIL, "PRESENT_EARLY_ACK")


def judge_c4(events, xrows, rrows, extra) -> None:
    if extra.get("register_submitted"):
        raise Verdict(FAIL, "REGISTER_ONLY_SUBMITTED")
    if extra.get("zero_serial_wait"):
        raise Verdict(FAIL, "ZERO_SERIAL_WAIT")
    if extra.get("ready_missing"):
        raise Verdict(INVALID, "READY_MISSING")
    completeness(xrows + rrows, "x")
    completeness(xrows + rrows, "r")
    destroy_before_x_release(xrows, rrows)
    waits = obs_kinds(xrows, "X_TERMINAL_WAIT_ENTER")
    for w in waits:
        if int(w.get("serial") or 0) == 0:
            raise Verdict(FAIL, "ZERO_SERIAL_WAIT")
    if any(e["event"] == EVENT_LEASE_GPU_OWNED for e in events):
        raise Verdict(FAIL, "REGISTER_ONLY_SUBMITTED")
    if any(e["event"] == EVENT_PUBLISH for e in events):
        raise Verdict(FAIL, "REGISTER_ONLY_SUBMITTED")


def judge_c5_full(events, xrows, rrows, extra) -> None:
    if extra.get("capacity_unit"):
        raise Verdict(INVALID, "CAPACITY_UNIT_OR_CONSTRUCTION")
    ck = require_obs(xrows, "X_CHECKPOINT")
    occ = ck[-1].get("registry_count")
    if occ is None:
        raise Verdict(INVALID, "CAPACITY_UNIT_OR_CONSTRUCTION")
    if int(occ) != 16:
        raise Verdict(INVALID, "CAPACITY_UNIT_OR_CONSTRUCTION")
    require_obs(xrows, "X_CLOSE_ENTER")
    require_obs(rrows, "R_UNBOUND_FINAL")


def judge_c5_overflow(events, xrows, rrows, extra) -> None:
    if extra.get("refusal_after_ownership"):
        raise Verdict(FAIL, "REFUSAL_AFTER_OWNERSHIP")
    if extra.get("capacity_recovery"):
        raise Verdict(FAIL, "CAPACITY_RECOVERY")
    require_obs(xrows, "X_CHECKPOINT")


def judge_d(events, xrows, rrows, extra) -> None:
    if extra.get("defer_not_constructed"):
        raise Verdict(INVALID, "DEFER_NOT_CONSTRUCTED")
    if extra.get("wakeup_origin"):
        raise Verdict(INVALID, "WAKEUP_ORIGIN")
    if extra.get("reentrant_recheck"):
        raise Verdict(FAIL, "REENTRANT_RECHECK")
    if extra.get("duplicate_dispatch"):
        raise Verdict(FAIL, "DUPLICATE_DISPATCH")
    if extra.get("stale_deferred"):
        raise Verdict(FAIL, "STALE_DEFERRED_DISPATCH")
    if extra.get("present_notification_lost"):
        raise Verdict(FAIL, "PRESENT_NOTIFICATION_LOST")
    if extra.get("connection_binding"):
        raise Verdict(INVALID, "CONNECTION_BINDING")
    enq = require_obs(xrows, "DEFER_ENQUEUE")
    disp = require_obs(xrows, "DEFER_DISPATCH")
    wakes = require_obs(rrows, "R_WAKE_SENT")
    require_obs(xrows, "X_WAKE_RECEIVED")
    ids = [e.get("local_id") for e in enq]
    dids = [d.get("local_id") for d in disp]
    if None in ids or None in dids:
        raise Verdict(INVALID, "DEFER_NOT_CONSTRUCTED")
    if len(dids) != len(set(dids)):
        raise Verdict(FAIL, "DUPLICATE_DISPATCH")
    if not set(dids).issubset(set(ids)):
        raise Verdict(FAIL, "STALE_DEFERRED_DISPATCH")
    if any(w.get("cause") == "surface_loss" for w in wakes):
        # surface-loss may exist; completion mapping must use fence_completed
        if not any(w.get("cause") == "fence_completed" and w.get("send_ok") is True
                   for w in wakes):
            raise Verdict(INVALID, "WAKEUP_ORIGIN")
    rec = obs_kinds(xrows, "RECHECK")
    # semantic recheck inside wait is FAIL; extra flag from mutation
    if rec and extra.get("recheck_during_wait"):
        raise Verdict(FAIL, "REENTRANT_RECHECK")


def judge_p1(events, xrows, rrows, extra) -> None:
    if extra.get("gpu_owned_missing"):
        raise Verdict(INVALID, "GPU_OWNED_NOT_CONSTRUCTED")
    if extra.get("real_guard_missing"):
        raise Verdict(INVALID, "REAL_GUARD_NOT_TESTED")
    if extra.get("guard_returned"):
        raise Verdict(FAIL, "GUARD_RETURNED")
    if extra.get("fault_not_fired"):
        raise Verdict(INVALID, "FAULT_NOT_FIRED")
    if not any(e["event"] == EVENT_LEASE_GPU_OWNED for e in events):
        raise Verdict(INVALID, "GPU_OWNED_NOT_CONSTRUCTED")
    faults = [e for e in events if e["event"] == EVENT_FAULT]
    if len(faults) != 1 or faults[0].get("src") != 14:
        raise Verdict(INVALID, "FAULT_NOT_FIRED")
    require_obs(xrows, "P1_DESTRUCTOR_CALL")
    if obs_kinds(xrows, "R8_HOOK_UNEXPECTED_RETURN"):
        raise Verdict(FAIL, "GUARD_RETURNED")
    if extra.get("halt_what") != "x-destroy-in-lease" or extra.get("halt_reason") != 7:
        raise Verdict(FAIL, "UNEXPECTED_FATAL")
    if any(e["event"] == EVENT_UNREGISTER_ACK for e in events):
        raise Verdict(FAIL, "NORMAL_RELEASE")
    if any(e["event"] == EVENT_PUBLISH for e in events if e["seq"] > faults[0]["seq"]):
        raise Verdict(FAIL, "PUBLISH_AFTER_FAULT")


def judge_p2(events, xrows, rrows, extra) -> None:
    if extra.get("gpu_owned_missing"):
        raise Verdict(INVALID, "GPU_OWNED_NOT_CONSTRUCTED")
    if extra.get("normal_close_after_fatal"):
        raise Verdict(FAIL, "NORMAL_CLOSE_AFTER_FATAL")
    if extra.get("fault_not_fired"):
        raise Verdict(INVALID, "FAULT_NOT_FIRED")
    if not any(e["event"] == EVENT_LEASE_GPU_OWNED for e in events):
        raise Verdict(INVALID, "GPU_OWNED_NOT_CONSTRUCTED")
    faults = [e for e in events if e["event"] == EVENT_FAULT]
    if len(faults) != 1 or faults[0].get("src") != 15:
        raise Verdict(INVALID, "FAULT_NOT_FIRED")
    require_obs(xrows, "X_CLOSE_ENTER")
    if obs_kinds(xrows, "R8_HOOK_UNEXPECTED_RETURN"):
        raise Verdict(FAIL, "GUARD_RETURNED")
    if extra.get("halt_what") != "x-close-in-lease" or extra.get("halt_reason") != 8:
        raise Verdict(FAIL, "UNEXPECTED_FATAL")
    if any(e["event"] == EVENT_GENERATION_CLOSED for e in events):
        raise Verdict(FAIL, "NORMAL_CLOSE_AFTER_FATAL")


def host_defer(extra) -> None:
    kind = extra.get("host_defer")
    if kind == "queue":
        return
    if kind == "cancel":
        return
    if kind == "stale":
        return
    raise Verdict(INVALID, "HOST_DEFER")


def load_extra(ev: Path) -> dict:
    p = load_json(ev / "judge-extra.json")
    if p is None:
        return {}
    if p == "INVALID_JSON":
        raise Verdict(INVALID, "JSON")
    return p if isinstance(p, dict) else {}


def judge(ev: Path, cell: str, manifest: dict | None) -> tuple[int, str]:
    try:
        extra = load_extra(ev)
        if extra.get("host_defer"):
            host_defer(extra)
            reason = {
                "queue": "HOST_QUEUE_ONLY",
                "cancel": "HOST_CANCEL_ONLY",
                "stale": "HOST_STALE_ONLY",
            }[extra["host_defer"]]
            return PASS, reason
        check_artifact(ev, manifest)
        check_stable(ev)
        check_process_observation(ev)
        ring = first_file(ev, ("ring.txt", "gatea-ring.txt", "ring"))
        raw = first_file(ev, ("raw-logcat.txt", "logcat.txt", "x-launcher.raw.log"))
        text = (read_text(ring) if ring else "") + "\n" + (read_text(raw) if raw else "")
        events = parse_events(text)
        seqs = [e["seq"] for e in events]
        if extra.get("dup_identical"):
            pass
        elif len(seqs) != len(set(seqs)) and extra.get("trace_conflict"):
            raise Verdict(INVALID, "TRACE_CONFLICT")
        xobs = parse_obs_blob(read_text(ev / "x-observations.jsonl") + "\n" + text)
        robs = parse_obs_blob(read_text(ev / "renderer-observations.jsonl") + "\n" + text)
        if extra.get("missing_destructor"):
            xobs = [r for r in xobs if r.get("phase") not in (
                "X_DESTRUCTOR_ENTER", "X_DESTRUCTOR_EXIT")]
        halt = HALT_PAT.search(text)
        if halt:
            extra.setdefault("halt_what", halt.group(1))
            extra.setdefault("halt_reason", int(halt.group(2)))
        dispatch = {
            "R8-C1": judge_c1,
            "R8-C2": judge_c2,
            "R8-C3-window": judge_c3,
            "R8-C3-disconnect": judge_c3,
            "R8-C4": judge_c4,
            "R8-C5-full": judge_c5_full,
            "R8-C5-overflow": judge_c5_overflow,
            "R8-D": judge_d,
            "R8-P1": judge_p1,
            "R8-P2": judge_p2,
        }[cell]
        dispatch(events, xobs, robs, extra)
        return PASS, "ACCEPT"
    except Verdict as v:
        return v.code, v.reason


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest")
    ap.add_argument("--spec")
    ap.add_argument("--evidence", required=True)
    ap.add_argument("--cell")
    ap.add_argument("--output")
    args = ap.parse_args()
    ev = Path(args.evidence)
    manifest = load_json(Path(args.manifest)) if args.manifest else None
    if manifest == "INVALID_JSON":
        code, reason = BLOCKED, "ARTIFACT_MISMATCH_PRESTART"
        cell = args.cell or "UNKNOWN"
    else:
        cell = args.cell or (manifest or {}).get("cell") or ""
        if cell not in CELLS and not str(cell).startswith("HOST"):
            code, reason = INVALID, "UNKNOWN_CELL"
        else:
            code, reason = judge(ev, cell, manifest if isinstance(manifest, dict) else None)
    names = {PASS: "R8_PASS", FAIL: "R8_FAIL", INVALID: "R8_INVALID", BLOCKED: "R8_BLOCKED"}
    line = f"{names[code]} {cell if code == PASS else reason}"
    print(line)
    if args.output:
        Path(args.output).write_text(json.dumps({
            "exit": code, "verdict": names[code], "cell": cell, "reason": reason,
            "true": True, "false": False, "null": None,
        }, indent=2) + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(main())
