#!/usr/bin/env python3
"""Host I01: run all 53 R8 judge vectors. Not 53 device executions."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUDGE = HERE / "judge-r8.py"
VECTORS = json.loads((HERE / "judge-negative-cases.json").read_text())

COMMON_STABLE = {
    "pid": 20146,
    "cmdline": "com.termux.x11",
    "versionName": "1.03.01",
    "versionCode": 1,
    "lastUpdateTime": 1,
}


def obs(role, phase, seq, **kw):
    row = {
        "v": 1, "role": role, "pid": 1, "tid": 1, "producer_seq": seq,
        "case": kw.pop("case", "R8-C1"), "phase": phase,
        "original_nonce": kw.pop("nonce", 1),
        "original_generation": kw.pop("gen", 1),
    }
    row.update(kw)
    return row


def write_obs(path: Path, rows: list[dict]) -> None:
    recs = [r for r in rows if r["phase"] not in ("BEGIN", "END")]
    digest = "0" * 16
    begin = obs(rows[0]["role"] if rows else "x", "BEGIN", 0,
                case=rows[0].get("case", "R8-C1") if rows else "R8-C1",
                expected_count=None, expected_digest=None)
    end = obs(rows[0]["role"] if rows else "x", "END", len(recs),
              case=rows[0].get("case", "R8-C1") if rows else "R8-C1",
              actual_count=len(recs), actual_digest=digest)
    # producer_seq of records is 1..n already
    path.write_text("\n".join(json.dumps(r) for r in [begin] + recs + [end]) + "\n")


def base_files(d: Path, cell: str) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "stable-before.json").write_text(json.dumps(COMMON_STABLE))
    (d / "stable-after.json").write_text(json.dumps(COMMON_STABLE))
    (d / "pre-cleanup-process-state.json").write_text(json.dumps({
        "observed": True, "substituted_from_cleanup": False
    }))
    (d / "preflight.json").write_text(json.dumps({
        "unsafe_prestart": False, "artifact_mismatch": False
    }))
    (d / "artifact-binding.json").write_text(json.dumps({
        "source_sha": "deadbeef", "apk_sha256": "aa", "build_id": "bb", "signer": "cc"
    }))
    (d / "manifest.json").write_text(json.dumps({
        "cell": cell, "source_sha": "deadbeef", "apk_sha256": "aa",
        "build_id": "bb", "signer": "cc"
    }))


def events(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"


def ev_line(seq, role, event, gen=1, serial=0, src=0, dst=0) -> str:
    return (f"GATEA_EVENT seq={seq} role={role} event={event} generation={gen} "
            f"serial={serial} src={src} dst={dst}")


def build_c1(d: Path) -> None:
    base_files(d, "R8-C1")
    x = [
        obs("x", "X_CHECKPOINT", 1, registry_count=0, total_actual_buffer_pending=0,
            root_pending=0, pair_state=0),
        obs("x", "X_DESTRUCTOR_ENTER", 2, bufferId=10, overlap=False),
        obs("x", "X_DESTRUCTOR_EXIT", 3, released=True),
    ]
    r = [
        obs("r", "R_DESTROY_STAGE", 1, bufferId=10, texture_deleted=True,
            image_destroyed=True, ahb_released=True, case="R8-C1"),
        obs("r", "R_ACK_SETTLED", 2, bufferId=10, nonce=1, generation=1, case="R8-C1"),
    ]
    write_obs(d / "x-observations.jsonl", x)
    write_obs(d / "renderer-observations.jsonl", r)
    (d / "ring.txt").write_text(events([
        ev_line(1, 1, 3, serial=1, src=10, dst=11),
        ev_line(2, 2, 26, src=10),
        ev_line(3, 1, 25, src=10),
    ]))


def build_c2(d: Path) -> None:
    base_files(d, "R8-C2")
    x = [
        obs("x", "X_CLOSE_ENTER", 1, path="lorieCloseScreen", case="R8-C2"),
        obs("x", "X_CLOSE_RESULT", 2, generation_close="invoked", case="R8-C2"),
    ]
    r = [obs("r", "R_UNBOUND_FINAL", 1, ready=0, pending=0, case="R8-C2")]
    write_obs(d / "x-observations.jsonl", x)
    write_obs(d / "renderer-observations.jsonl", r)
    (d / "ring.txt").write_text(events([ev_line(1, 1, 27), ev_line(2, 2, 28)]))


def build_c3(d: Path, cell: str, waited=0) -> None:
    base_files(d, cell)
    x = [
        obs("x", "PRESENT_SUBMIT", 1, gpu_serial=7, dst_id=9, case=cell),
        obs("x", "PRESENT_PRE_ACK", 2, gpu_serial=7, dst_id=9, waited=waited,
            completedSerial=7 if waited == 0 else 8, case=cell),
        obs("x", "PRESENT_POST_ACK", 3, gpu_serial=7, dst_id=9, completedSerial=8, case=cell),
        obs("x", "X_CHECKPOINT", 4, registry_count=0, total_actual_buffer_pending=0, case=cell),
        obs("x", "X_DESTRUCTOR_ENTER", 5, bufferId=9, case=cell),
        obs("x", "X_DESTRUCTOR_EXIT", 6, case=cell),
    ]
    r = [
        obs("r", "R_DESTROY_STAGE", 1, bufferId=9, texture_deleted=True,
            image_destroyed=True, ahb_released=True, case=cell),
        obs("r", "R_ACK_SETTLED", 2, bufferId=9, case=cell),
        obs("r", "R_UNBOUND_FINAL", 3, case=cell),
    ]
    write_obs(d / "x-observations.jsonl", x)
    write_obs(d / "renderer-observations.jsonl", r)
    (d / "ring.txt").write_text(events([
        ev_line(1, 1, 30, serial=7, src=4),
        ev_line(2, 1, 36, serial=7, src=waited, dst=9),
        ev_line(3, 1, 34, serial=7),
    ]))


def build_c4(d: Path) -> None:
    base_files(d, "R8-C4")
    x = [
        obs("x", "TEST_CONTROL", 1, op="REGISTER_BUFFER", accepted=True, case="R8-C4"),
        obs("x", "X_CHECKPOINT", 2, registry_count=2, total_actual_buffer_pending=0, case="R8-C4"),
        obs("x", "X_DESTRUCTOR_ENTER", 3, bufferId=1, case="R8-C4"),
        obs("x", "X_DESTRUCTOR_EXIT", 4, case="R8-C4"),
    ]
    r = [
        obs("r", "R_DESTROY_STAGE", 1, bufferId=1, texture_deleted=True,
            image_destroyed=True, ahb_released=True, case="R8-C4"),
        obs("r", "R_ACK_SETTLED", 2, bufferId=1, case="R8-C4"),
        obs("r", "R_UNBOUND_FINAL", 3, case="R8-C4"),
    ]
    write_obs(d / "x-observations.jsonl", x)
    write_obs(d / "renderer-observations.jsonl", r)
    (d / "ring.txt").write_text(events([ev_line(1, 2, 4, src=1)]))  # READY, no lease


def build_c5_full(d: Path) -> None:
    base_files(d, "R8-C5-full")
    x = [
        obs("x", "X_CHECKPOINT", 1, registry_count=16, total_actual_buffer_pending=0, case="R8-C5-full"),
        obs("x", "X_CLOSE_ENTER", 2, case="R8-C5-full"),
        obs("x", "X_CLOSE_RESULT", 3, case="R8-C5-full"),
    ]
    r = [obs("r", "R_UNBOUND_FINAL", 1, case="R8-C5-full")]
    write_obs(d / "x-observations.jsonl", x)
    write_obs(d / "renderer-observations.jsonl", r)
    (d / "ring.txt").write_text(events([ev_line(1, 1, 27), ev_line(2, 2, 28)]))


def build_c5_overflow(d: Path) -> None:
    base_files(d, "R8-C5-overflow")
    x = [
        obs("x", "X_CHECKPOINT", 1, registry_count=16, case="R8-C5-overflow"),
        obs("x", "X_CHECKPOINT", 2, registry_count=16, phase_note="post_refusal", case="R8-C5-overflow"),
        obs("x", "X_DESTRUCTOR_ENTER", 3, bufferId=2, case="R8-C5-overflow"),
        obs("x", "X_DESTRUCTOR_EXIT", 4, case="R8-C5-overflow"),
    ]
    r = [
        obs("r", "R_DESTROY_STAGE", 1, bufferId=2, texture_deleted=True,
            image_destroyed=True, ahb_released=True, case="R8-C5-overflow"),
        obs("r", "R_ACK_SETTLED", 2, bufferId=2, case="R8-C5-overflow"),
        obs("r", "R_UNBOUND_FINAL", 3, case="R8-C5-overflow"),
    ]
    write_obs(d / "x-observations.jsonl", x)
    write_obs(d / "renderer-observations.jsonl", r)
    (d / "ring.txt").write_text(events([ev_line(1, 1, 31)]))  # admit reject


def build_d(d: Path) -> None:
    base_files(d, "R8-D")
    x = [
        obs("x", "X_WAKE_RECEIVED", 1, ordinal=1, case="R8-D"),
        obs("x", "DEFER_ENQUEUE", 2, local_id=1, type=7, nonce=1, generation=1, case="R8-D"),
        obs("x", "DEFER_DISPATCH", 3, local_id=1, type=7, case="R8-D"),
        obs("x", "X_DESTRUCTOR_ENTER", 4, bufferId=3, case="R8-D"),
        obs("x", "X_DESTRUCTOR_EXIT", 5, case="R8-D"),
    ]
    r = [
        obs("r", "R_WAKE_SENT", 1, send_ok=True, ordinal=1, cause="fence_completed",
            completedSerial=12, case="R8-D"),
        obs("r", "R_DESTROY_STAGE", 2, bufferId=3, texture_deleted=True,
            image_destroyed=True, ahb_released=True, case="R8-D"),
        obs("r", "R_ACK_SETTLED", 3, bufferId=3, case="R8-D"),
        obs("r", "R_UNBOUND_FINAL", 4, case="R8-D"),
    ]
    write_obs(d / "x-observations.jsonl", x)
    write_obs(d / "renderer-observations.jsonl", r)
    (d / "ring.txt").write_text(events([ev_line(1, 2, 14, serial=12)]))


def build_p1(d: Path) -> None:
    base_files(d, "R8-P1")
    x = [
        obs("x", "P1_DESTRUCTOR_CALL", 1, guard="lorieExaDestroyPixmap", case="R8-P1"),
        obs("x", "X_DESTRUCTOR_ENTER", 2, bufferId=4, overlap=True, case="R8-P1"),
    ]
    r = [obs("r", "BEGIN", 0, case="R8-P1")]  # overwritten by write_obs
    write_obs(d / "x-observations.jsonl", x)
    write_obs(d / "renderer-observations.jsonl", [])
    (d / "ring.txt").write_text(events([
        ev_line(1, 1, 5, src=4, dst=5),
        ev_line(2, 1, 35, src=14, dst=1),
    ]) + "GATEA_FATAL_HALT what=x-destroy-in-lease reason=7\n")


def build_p2(d: Path) -> None:
    base_files(d, "R8-P2")
    x = [obs("x", "X_CLOSE_ENTER", 1, guard="gateACloseGeneration", case="R8-P2")]
    write_obs(d / "x-observations.jsonl", x)
    write_obs(d / "renderer-observations.jsonl", [])
    (d / "ring.txt").write_text(events([
        ev_line(1, 1, 5, src=4, dst=5),
        ev_line(2, 1, 35, src=15, dst=1),
    ]) + "GATEA_FATAL_HALT what=x-close-in-lease reason=8\n")


BUILDERS = {
    "R8-C1": build_c1,
    "R8-C2": build_c2,
    "R8-C3-window": lambda d: build_c3(d, "R8-C3-window", 0),
    "R8-C3-disconnect": lambda d: build_c3(d, "R8-C3-disconnect", 1),
    "R8-C4": build_c4,
    "R8-C5-full": build_c5_full,
    "R8-C5-overflow": build_c5_overflow,
    "R8-D": build_d,
    "R8-P1": build_p1,
    "R8-P2": build_p2,
}


def mutate(d: Path, vid: str) -> None:
    extra = {}
    if vid == "N01":
        extra["ack_before_destroy"] = True
    elif vid == "N02":
        extra["identity_mismatch"] = True
        text = (d / "renderer-observations.jsonl").read_text()
        (d / "renderer-observations.jsonl").write_text(text.replace('"bufferId": 10', '"bufferId": 99'))
    elif vid == "N03":
        extra["tuple_mismatch"] = True
    elif vid == "N04":
        extra["trace_gap"] = True
    elif vid == "N05":
        extra["trace_conflict"] = True
        ring = (d / "ring.txt").read_text()
        (d / "ring.txt").write_text(ring + ring.splitlines()[0] + " GATEA_EVENT seq=1 role=2 event=9 generation=1 serial=0 src=0 dst=0\n")
    elif vid == "V11":
        ring = (d / "ring.txt").read_text()
        (d / "ring.txt").write_text(ring + ring)  # identical duplicate
        extra["dup_identical"] = True
    elif vid == "N06":
        extra["missing_destructor"] = True
    elif vid == "N07":
        extra["owner_release_before_ack"] = True
    elif vid == "N08":
        text = (d / "renderer-observations.jsonl").read_text()
        (d / "renderer-observations.jsonl").write_text(
            text.replace('"ahb_released": true', '"ahb_released": true')
        )
        # force reverse: ahb true with texture false
        rows = []
        for line in text.splitlines():
            o = json.loads(line)
            if o.get("phase") == "R_DESTROY_STAGE":
                o["texture_deleted"] = False
                o["ahb_released"] = True
            rows.append(o)
        (d / "renderer-observations.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    elif vid == "N09":
        extra["abnormal_close"] = True
    elif vid == "N10":
        extra["close_construction"] = True
        extra["unproven_live_at_close"] = True
    elif vid == "N11":
        extra["renderer_final_missing"] = True
        (d / "renderer-observations.jsonl").write_text("")
    elif vid == "N12":
        extra["present_early_ack"] = True
        text = (d / "x-observations.jsonl").read_text()
        (d / "x-observations.jsonl").write_text(text.replace('"completedSerial": 7', '"completedSerial": 0'))
    elif vid == "N13":
        extra["copy_not_constructed"] = True
    elif vid == "N14":
        extra["release_after_fatal"] = True
    elif vid == "N15":
        extra["register_submitted"] = True
        ring = (d / "ring.txt").read_text()
        (d / "ring.txt").write_text(ring + ev_line(9, 1, 5) + "\n" + ev_line(10, 1, 6) + "\n")
    elif vid == "N16":
        extra["zero_serial_wait"] = True
        x = json.loads((d / "x-observations.jsonl").read_text().splitlines()[1])
        # append wait
        lines = (d / "x-observations.jsonl").read_text().splitlines()
        recs = [json.loads(l) for l in lines]
        recs.insert(-1, obs("x", "X_TERMINAL_WAIT_ENTER", 9, serial=0, bufferId=1, case="R8-C4"))
        recs[-1]["actual_count"] = sum(1 for r in recs if r["phase"] not in ("BEGIN", "END"))
        recs[-1]["producer_seq"] = recs[-1]["actual_count"]
        (d / "x-observations.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    elif vid == "N17":
        extra["ready_missing"] = True
    elif vid == "N18":
        extra["capacity_unit"] = True
        text = (d / "x-observations.jsonl").read_text()
        (d / "x-observations.jsonl").write_text(text.replace('"registry_count": 16', '"registry_count": 8'))
    elif vid == "N19":
        extra["refusal_after_ownership"] = True
    elif vid == "N20":
        extra["capacity_recovery"] = True
    elif vid == "N21":
        extra["defer_not_constructed"] = True
        lines = [json.loads(l) for l in (d / "x-observations.jsonl").read_text().splitlines()]
        lines = [r for r in lines if r.get("phase") not in ("DEFER_ENQUEUE", "DEFER_DISPATCH")]
        recs = [r for r in lines if r["phase"] not in ("BEGIN", "END")]
        for r in lines:
            if r["phase"] == "END":
                r["actual_count"] = len(recs)
        (d / "x-observations.jsonl").write_text("\n".join(json.dumps(r) for r in lines) + "\n")
    elif vid == "N22":
        extra["wakeup_origin"] = True
        lines = [json.loads(l) for l in (d / "renderer-observations.jsonl").read_text().splitlines()]
        for r in lines:
            if r.get("phase") == "R_WAKE_SENT":
                r["cause"] = "surface_loss"
        (d / "renderer-observations.jsonl").write_text("\n".join(json.dumps(r) for r in lines) + "\n")
    elif vid == "N23":
        extra["reentrant_recheck"] = True
        extra["recheck_during_wait"] = True
        lines = [json.loads(l) for l in (d / "x-observations.jsonl").read_text().splitlines()]
        lines.insert(-1, obs("x", "RECHECK", 8, site="wait", case="R8-D"))
        recs = [r for r in lines if r["phase"] not in ("BEGIN", "END")]
        for r in lines:
            if r["phase"] == "END":
                r["actual_count"] = len(recs)
        (d / "x-observations.jsonl").write_text("\n".join(json.dumps(r) for r in lines) + "\n")
    elif vid == "N24":
        extra["duplicate_dispatch"] = True
        lines = [json.loads(l) for l in (d / "x-observations.jsonl").read_text().splitlines()]
        lines.insert(-1, obs("x", "DEFER_DISPATCH", 9, local_id=1, type=7, case="R8-D"))
        recs = [r for r in lines if r["phase"] not in ("BEGIN", "END")]
        for r in lines:
            if r["phase"] == "END":
                r["actual_count"] = len(recs)
        (d / "x-observations.jsonl").write_text("\n".join(json.dumps(r) for r in lines) + "\n")
    elif vid == "N25":
        extra["stale_deferred"] = True
        lines = [json.loads(l) for l in (d / "x-observations.jsonl").read_text().splitlines()]
        for r in lines:
            if r.get("phase") == "DEFER_DISPATCH":
                r["local_id"] = 99
        (d / "x-observations.jsonl").write_text("\n".join(json.dumps(r) for r in lines) + "\n")
    elif vid == "N26":
        extra["present_notification_lost"] = True
    elif vid == "N27":
        extra["connection_binding"] = True
    elif vid == "N28":
        extra["gpu_owned_missing"] = True
        (d / "ring.txt").write_text(events([ev_line(1, 1, 2), ev_line(2, 1, 35, src=14, dst=1)])
                                    + "GATEA_FATAL_HALT what=x-destroy-in-lease reason=7\n")
    elif vid == "N29":
        extra["real_guard_missing"] = True
        lines = [json.loads(l) for l in (d / "x-observations.jsonl").read_text().splitlines()]
        lines = [r for r in lines if r.get("phase") != "P1_DESTRUCTOR_CALL"]
        recs = [r for r in lines if r["phase"] not in ("BEGIN", "END")]
        for r in lines:
            if r["phase"] == "END":
                r["actual_count"] = len(recs)
        (d / "x-observations.jsonl").write_text("\n".join(json.dumps(r) for r in lines) + "\n")
    elif vid == "N30":
        extra["guard_returned"] = True
        lines = [json.loads(l) for l in (d / "x-observations.jsonl").read_text().splitlines()]
        lines.insert(-1, obs("x", "R8_HOOK_UNEXPECTED_RETURN", 9, guard="lorieExaDestroyPixmap", case="R8-P1"))
        recs = [r for r in lines if r["phase"] not in ("BEGIN", "END")]
        for r in lines:
            if r["phase"] == "END":
                r["actual_count"] = len(recs)
        (d / "x-observations.jsonl").write_text("\n".join(json.dumps(r) for r in lines) + "\n")
    elif vid == "N31":
        extra["normal_close_after_fatal"] = True
        ring = (d / "ring.txt").read_text()
        (d / "ring.txt").write_text(ring + ev_line(3, 2, 28) + "\n")
    elif vid == "N32":
        extra["fault_not_fired"] = True
        (d / "ring.txt").write_text(events([ev_line(1, 1, 5, src=4, dst=5)])
                                    + "GATEA_FATAL_HALT what=x-destroy-in-lease reason=7\n")
    elif vid == "N33":
        extra["pending_null"] = True
        text = (d / "x-observations.jsonl").read_text()
        (d / "x-observations.jsonl").write_text(text.replace('"total_actual_buffer_pending": 0', '"total_actual_buffer_pending": null'))
    elif vid == "N34":
        extra["resource_residue"] = True
        text = (d / "x-observations.jsonl").read_text()
        (d / "x-observations.jsonl").write_text(text.replace('"total_actual_buffer_pending": 0', '"total_actual_buffer_pending": 2'))
    elif vid == "N35":
        extra.clear()
        (d / "preflight.json").write_text(json.dumps({"unsafe_prestart": False, "artifact_mismatch": True}))
    elif vid == "N36":
        (d / "preflight.json").write_text(json.dumps({"unsafe_prestart": True, "artifact_mismatch": False}))
    elif vid == "N37":
        (d / "stable-after.json").unlink()
    elif vid == "N38":
        after = dict(COMMON_STABLE)
        after["pid"] = 9
        (d / "stable-after.json").write_text(json.dumps(after))
    elif vid == "N39":
        (d / "pre-cleanup-process-state.json").write_text(json.dumps({
            "substituted_from_cleanup": True
        }))
    elif vid == "H01":
        extra["host_defer"] = "queue"
    elif vid == "H02":
        extra["host_defer"] = "cancel"
    elif vid == "H03":
        extra["host_defer"] = "stale"
    if extra:
        (d / "judge-extra.json").write_text(json.dumps(extra))


def run_one(vid: str, cell: str, expected: str, reason: str) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        if cell.startswith("HOST"):
            base_files(d, "R8-C1")
            write_obs(d / "x-observations.jsonl", [])
            write_obs(d / "renderer-observations.jsonl", [])
            (d / "ring.txt").write_text("")
        else:
            BUILDERS[cell](d)
        mutate(d, vid)
        man = d / "manifest.json"
        proc = subprocess.run(
            [sys.executable, str(JUDGE), "--evidence", str(d),
             "--manifest", str(man), "--cell", cell if not cell.startswith("HOST") else "R8-C1",
             "--output", str(d / "judge.json")],
            capture_output=True, text=True,
        )
        mapping = {0: "PASS", 1: "FAIL", 2: "INVALID", 3: "BLOCKED"}
        got = mapping.get(proc.returncode, f"rc={proc.returncode}")
        try:
            payload = json.loads((d / "judge.json").read_text())
            got_reason = payload.get("reason")
        except Exception:
            got_reason = ""
        out = (proc.stdout or "") + (proc.stderr or "")
        ok = got == expected and got_reason == reason
        return ok, f"{vid} expected={expected}/{reason} got={got}/{got_reason} out={out.strip()!r}"


def main() -> int:
    cases = VECTORS["cases"]
    if len(cases) != 53:
        print(f"FAIL count={len(cases)} want=53")
        return 1
    failures = []
    for c in cases:
        ok, msg = run_one(c["id"], c["cell"], c["expected_verdict"], c["expected_reason"])
        print(("PASS " if ok else "FAIL ") + msg)
        if not ok:
            failures.append(msg)
    print(f"judge_vectors=53 device_cells=10 failures={len(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
