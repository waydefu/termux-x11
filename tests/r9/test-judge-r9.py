#!/usr/bin/env python3
"""Host vectors for judge-r9.py. No device, no product, no attempts.

Every negative pins exactly ONE assertion, so a failure names the rule that broke.
The INVALID/FAIL split is tested deliberately: a construction that never reached the
path under test must be INVALID, never PASS and never FAIL (plan §8.8).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUDGE = HERE / "judge-r9.py"

MANIFEST = {
    "source_sha": "dc94485a7ef4f74cada36ea3c1d35d0aa0f48693",
    "apk_sha256": "1bd8bef0909249737ea43acf0a35f3c995d941e1badbfcf370e5cd854f4bb8a3",
    "build_id": "bc993eee0c4420b9721801b06d7183ed53de487b",
    "signer": "b6da01480eefd5fbf2cd3771b8d1021ec791304bdd6c4bf41d3faabad48ee5e1",
}

def boundary(**over):
    b = {
        "x_pid": 100, "x_starttime": 1000,
        "activity_pid": 200, "activity_starttime": 2000,
        "shared_nonce": 777, "shared_generation": 1,
        "renderer_bound_nonce": 777, "renderer_bound_generation": 1,
        "epoch_id": 1,
        "x_fd_table_had_previous_conn_fd": False,
    }
    b.update(over)
    return b


def base(d: Path, cell: str, halt: tuple[str, int] | None, bounds: list[dict],
         ring_events=(1, 2, 3, 4, 5)) -> None:
    (d / "artifact-binding.json").write_text(json.dumps(MANIFEST))
    (d / "manifest.json").write_text(json.dumps(MANIFEST))
    st = {"pid": 5, "cmdline": "x", "versionName": "v", "versionCode": 1,
          "lastUpdateTime": 9}
    (d / "stable-before.json").write_text(json.dumps(st))
    (d / "stable-after.json").write_text(json.dumps(st))
    (d / "x-observations.jsonl").write_text(
        json.dumps({"v": 1, "role": "x", "phase": "BEGIN", "producer_seq": 0}) + "\n")
    (d / "renderer-observations.jsonl").write_text(
        json.dumps({"v": 1, "role": "r", "phase": "BEGIN", "producer_seq": 0}) + "\n")
    (d / "identity-boundaries.jsonl").write_text(
        "".join(json.dumps(b) + "\n" for b in bounds))
    raw = ""
    if halt:
        raw = f"F/gatea-a1: GATEA_FATAL_HALT what={halt[0]} reason={halt[1]}\n"
    (d / "raw-logcat.txt").write_text(raw)
    (d / "gatea-ring.txt").write_text(
        "".join(f"GATEA_EVENT seq={i} role=1 event={e} generation=1 serial=0 src=0 dst=0\n"
                for i, e in enumerate(ring_events)))


def build_cold2(d: Path, **mut) -> None:
    b0 = boundary()
    b1 = boundary(activity_pid=mut.get("act_pid", 201),
                  activity_starttime=mut.get("act_start", 2001),
                  shared_nonce=mut.get("nonce2", 777),
                  shared_generation=mut.get("gen2", 1))
    base(d, "R9-COLD-2", mut.get("halt", ("x-bump-unterminal", 6)), [b0, b1])
    (d / "renderer-fatal.json").write_text(json.dumps(
        {"published": mut.get("published", True)}))
    (d / "x-survival.json").write_text(json.dumps(
        {"x_alive_after_renderer_death": mut.get("x_alive", True),
         "x_eof_fatal": mut.get("x_eof", False)}))
    (d / "registry-at-bump.json").write_text(json.dumps(
        {"pending_count": mut.get("pending", 1), "last_submitted_serial": 0}))


def build_f1(d: Path, **mut) -> None:
    base(d, "R9-F1", mut.get("halt", ("x-wrong-generation", 6)),
         [boundary(), boundary()])
    (d / "stale-replay.json").write_text(json.dumps({
        "legitimate_ready_sent": mut.get("legit", True),
        "stale_frame_sent": mut.get("sent", True),
        "gate_a_active_on_arrival": mut.get("active", True),
        "stale_tuple_accepted": mut.get("accepted", False),
    }))


def build_f2(d: Path, **mut) -> None:
    b0 = boundary()
    b1 = boundary(x_pid=101, x_starttime=1001, activity_pid=201,
                  activity_starttime=2001, shared_nonce=mut.get("nonce2", 888))
    if mut.get("same_x"):
        b1["x_pid"], b1["x_starttime"] = 100, 1000
    if mut.get("same_act"):
        b1["activity_pid"], b1["activity_starttime"] = 200, 2000
    base(d, "R9-F2", mut.get("halt"), [b0, b1],
         ring_events=mut.get("ring", (1, 2, 3, 4, 5)))
    if not mut.get("no_pre"):
        (d / "f1-precondition.json").write_text(json.dumps(
            {"f1_expected_fatal_observed": mut.get("pre_ok", True)}))
    (d / "registry-fresh.json").write_text(json.dumps(
        {"x_entries": mut.get("x_entries", 0),
         "renderer_ready_entries": mut.get("r_entries", 0)}))


CASES = [
    # ---- R9-COLD-2 ----
    ("V01", "R9-COLD-2", build_cold2, {}, "R9_PASS", "ACCEPT"),
    ("N01", "R9-COLD-2", build_cold2, {"published": False}, "R9_INVALID", "RENDERER_FATAL_NOT_PUBLISHED"),
    ("N02", "R9-COLD-2", build_cold2, {"x_alive": False}, "R9_FAIL", "X_DID_NOT_SURVIVE"),
    ("N03", "R9-COLD-2", build_cold2, {"x_eof": True}, "R9_FAIL", "X_TOOK_X_EOF"),
    ("N04", "R9-COLD-2", build_cold2, {"nonce2": 999}, "R9_FAIL", "SESSION_NONCE_CHANGED"),
    ("N05", "R9-COLD-2", build_cold2, {"act_pid": 200, "act_start": 2000}, "R9_INVALID", "ACTIVITY_DID_NOT_RESTART"),
    ("N06", "R9-COLD-2", build_cold2, {"pending": 0}, "R9_INVALID", "REGISTRY_WAS_TERMINAL"),
    ("N07", "R9-COLD-2", build_cold2, {"halt": ("x-eof", 6)}, "R9_FAIL", "WRONG_FATAL_x-eof_6"),
    ("N08", "R9-COLD-2", build_cold2, {"halt": None}, "R9_INVALID", "EXPECTED_FATAL_ABSENT"),
    ("N09", "R9-COLD-2", build_cold2, {"gen2": 2}, "R9_FAIL", "GENERATION_OPENED_OVER_NONTERMINAL_REGISTRY"),
    # ---- R9-F1 ----
    ("V02", "R9-F1", build_f1, {}, "R9_PASS", "ACCEPT"),
    ("N10", "R9-F1", build_f1, {"legit": False}, "R9_INVALID", "VALIDATE_IMPORT_NOT_REACHED"),
    ("N11", "R9-F1", build_f1, {"sent": False}, "R9_INVALID", "STALE_FRAME_NOT_SENT"),
    ("N12", "R9-F1", build_f1, {"active": False}, "R9_INVALID", "STALE_FRAME_SILENTLY_DROPPED"),
    ("N13", "R9-F1", build_f1, {"halt": ("x-bump-unterminal", 6)}, "R9_FAIL", "WRONG_FATAL_x-bump-unterminal_6"),
    ("N14", "R9-F1", build_f1, {"accepted": True}, "R9_FAIL", "STALE_TUPLE_ACCEPTED"),
    # ---- R9-F2 ----
    ("V03", "R9-F2", build_f2, {}, "R9_PASS", "ACCEPT"),
    ("N15", "R9-F2", build_f2, {"no_pre": True}, "R9_BLOCKED", "F1_PRECONDITION_EVIDENCE_MISSING"),
    ("N16", "R9-F2", build_f2, {"pre_ok": False}, "R9_BLOCKED", "F1_EXPECTED_FATAL_ABSENT"),
    ("N17", "R9-F2", build_f2, {"nonce2": 777}, "R9_FAIL", "NONCE_NOT_REFRESHED"),
    ("N18", "R9-F2", build_f2, {"same_x": True}, "R9_INVALID", "X_DID_NOT_RESTART"),
    ("N19", "R9-F2", build_f2, {"same_act": True}, "R9_INVALID", "ACTIVITY_DID_NOT_RESTART"),
    ("N20", "R9-F2", build_f2, {"x_entries": 1}, "R9_FAIL", "STALE_RESIDUE_PRESENT"),
    ("N21", "R9-F2", build_f2, {"ring": (1, 2, 3)}, "R9_FAIL", "NO_DIRECT_SUCCESS_EVENT5_ABSENT"),
    ("N22", "R9-F2", build_f2, {"halt": ("x-anything", 6)}, "R9_FAIL", "UNEXPECTED_FATAL_IN_FRESH_SESSION"),
]


def mutate_common(d: Path, which: str) -> None:
    if which == "artifact":
        b = json.loads((d / "artifact-binding.json").read_text())
        b["apk_sha256"] = "deadbeef"
        (d / "artifact-binding.json").write_text(json.dumps(b))
    elif which == "stable":
        a = json.loads((d / "stable-after.json").read_text())
        a["pid"] = 6
        (d / "stable-after.json").write_text(json.dumps(a))
    elif which == "identity":
        rows = [json.loads(l) for l in
                (d / "identity-boundaries.jsonl").read_text().splitlines() if l.strip()]
        rows[0].pop("epoch_id")
        (d / "identity-boundaries.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows))


COMMON = [
    ("C01", "R9-F1", "artifact", "R9_BLOCKED", "ARTIFACT_MISMATCH_PRESTART"),
    ("C02", "R9-F1", "stable", "R9_FAIL", "STABLE_CHANGED"),
    ("C03", "R9-F1", "identity", "R9_INVALID", "IDENTITY_FIELD_MISSING_epoch_id"),
]


def run(d: Path, cell: str) -> tuple[str, str]:
    p = subprocess.run(
        [sys.executable, str(JUDGE), "--evidence", str(d), "--cell", cell,
         "--manifest", str(d / "manifest.json"), "--output", str(d / "j.json")],
        capture_output=True, text=True)
    out = (p.stdout or "").strip().split(" ", 1)
    got = json.loads((d / "j.json").read_text())
    return out[0] if out else "?", got.get("reason", "")


def main() -> int:
    failures = []
    for vid, cell, builder, mut, want_v, want_r in CASES:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            builder(d, **mut)
            v, r = run(d, cell)
            ok = (v == want_v and r == want_r)
            print(("PASS " if ok else "FAIL ") +
                  f"{vid} {cell} expected={want_v}/{want_r} got={v}/{r}")
            if not ok:
                failures.append(vid)
    for vid, cell, which, want_v, want_r in COMMON:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            build_f1(d)
            mutate_common(d, which)
            v, r = run(d, cell)
            ok = (v == want_v and r == want_r)
            print(("PASS " if ok else "FAIL ") +
                  f"{vid} {cell} expected={want_v}/{want_r} got={v}/{r}")
            if not ok:
                failures.append(vid)
    total = len(CASES) + len(COMMON)
    print(f"r9_judge_vectors={total} device_cells=3 failures={len(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
