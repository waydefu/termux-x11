#!/usr/bin/env python3
"""Build ledger.json from an R10 evidence directory.

All judgement-relevant inference lives here, not in the runner, so it can be tested
offline against synthetic input. NEVER FABRICATE: a value that was not observed is
null. Writing 0 for an unobserved metric is permanently forbidden (plan §9.5) —
0 means "measured, and it was zero", and using it for "not measured" is exactly the
substitution that turns a leak into no-leak.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
import gatea_counters as C

RING_ROW = re.compile(
    r"GATEA_EVENT\b.*?\brole=(\d+)\b.*?\bevent=(\d+)\b.*?\bsrc=(\d+)\b")
BIND = re.compile(r"GATEA_BIND version=\d+ nonce=(\d+) generation=(\d+) bound=(\d+)")
HALT = re.compile(r"GATEA_FATAL_HALT what=(\S+) reason=(\d+)")
OVERFLOW = re.compile(r"\boverflow=(\d+)\b")

EVENT_REGISTER_READY = 1
EVENT_LEASE_RESERVED = 2
EVENT_LEASE_GPU_OWNED = 5
EVENT_LEASE_RELEASE = 23
EVENT_UNREGISTER_ACK = 25
ROLE_X, ROLE_RENDERER = 1, 2


def read(p: Path) -> str:
    try:
        return p.read_text(errors="replace")
    except OSError:
        return ""


def derived_state(ring_text: str, overflow: int | None,
                  logcat_slice_text: str = "") -> dict:
    """Registry and lease occupancy from the traced events.

    The ring is a bounded circular buffer. If it overflowed, earlier events are gone
    and a difference computed from what survives would UNDERCOUNT what is still
    held — it would understate a leak. So an overflowed ring yields null, not a
    number. `overflow` comes from the summary line; if that is missing too we cannot
    even tell, and the answer is null again."""
    # Two sources, and the ring is preferred only when it is known intact.
    #   ring file        one dump per session, authoritative, but BOUNDED - an
    #                    overflowed ring has lost earlier events, so a difference
    #                    computed from what survives would UNDERCOUNT what is still
    #                    held, i.e. understate a leak. Overflowed -> null.
    #   logcat slice     the same GATEA_EVENT lines, live and unbounded, windowed to
    #                    this round by the runner's offsets. It is the ONLY source at
    #                    a sampling point with no dump, which is every point in mode A.
    source = None
    if ring_text.strip() and overflow == 0:
        rows = [(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                for m in RING_ROW.finditer(ring_text)]
        source = "ring_events"
    elif ring_text.strip() and overflow != 0:
        return {"x_registry_current": None, "renderer_registry_current": None,
                "lease_current": None, "direct_success_events": None,
                "source": "ring_overflowed"}
    else:
        rows = [(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                for m in RING_ROW.finditer(logcat_slice_text)]
        source = "logcat_events"
    if not rows:
        return {"x_registry_current": None, "renderer_registry_current": None,
                "lease_current": None, "direct_success_events": None,
                "source": "no_events"}

    def held(role):
        ready = {s for r, e, s in rows if r == role and e == EVENT_REGISTER_READY}
        acked = {s for r, e, s in rows if r == role and e == EVENT_UNREGISTER_ACK}
        return len(ready - acked)

    res = sum(1 for r, e, _ in rows if r == ROLE_X and e == EVENT_LEASE_RESERVED)
    rel = sum(1 for r, e, _ in rows if r == ROLE_X and e == EVENT_LEASE_RELEASE)
    return {
        "x_registry_current": held(ROLE_X),
        "renderer_registry_current": held(ROLE_RENDERER),
        "lease_current": res - rel,
        "direct_success_events": sum(
            1 for r, e, _ in rows if e == EVENT_LEASE_GPU_OWNED),
        "source": source,
    }


def counters(summary_text: str) -> dict:
    out = {"summary_present": bool(summary_text.strip())}
    for idx, name in C.COUNTERS.items():
        out["c_" + name.lower()] = C.read(summary_text, idx)
    m = OVERFLOW.search(summary_text)
    out["ring_overflow"] = int(m.group(1)) if m else None
    m = re.search(r"\bwhere=(\S+)", summary_text)
    out["where"] = m.group(1) if m else None
    m = re.search(r"\bnonce=(\d+)\b.*?\bgeneration=(\d+)\b", summary_text)
    out["nonce_at_dump"] = int(m.group(1)) if m else None
    out["generation_at_dump"] = int(m.group(2)) if m else None
    return out


def session_tuple(logcat_slice: str) -> dict:
    """The tuple the renderer latched for this session, read from GATEA_BIND.

    It cannot come from the summary: a clean close zeroes both halves before the
    dump, so `nonce=0 generation=0` there is the CLOSE's signature, not the
    session's identity."""
    binds = [(int(m.group(1)), int(m.group(2)), int(m.group(3)))
             for m in BIND.finditer(logcat_slice)]
    live = [b for b in binds if b[2] == 1]
    if not live:
        return {"session_nonce": None, "session_generation": None, "binds_seen": len(binds)}
    n, g, _ = live[-1]
    return {"session_nonce": n, "session_generation": g, "binds_seen": len(binds)}


def logcat_slice(raw: str, rdir: Path) -> str:
    """The lines belonging to this round, if the runner recorded offsets. Without
    them the slice is empty and the tuple comes back null rather than being taken
    from some other round's bind."""
    b = read(rdir / "logcat-offset-begin.txt").strip()
    e = read(rdir / "logcat-offset-end.txt").strip()
    if not b.isdigit() or not e.isdigit():
        return ""
    lines = raw.splitlines()
    return "\n".join(lines[int(b):int(e)])


def build(ev: Path) -> dict:
    binding = json.loads(read(ev / "run-binding.json") or "{}")
    raw = read(ev / "raw-logcat.txt")
    rounds = sorted((d for d in ev.glob("r*") if d.is_dir() and d.name[1:].isdigit()),
                    key=lambda d: int(d.name[1:]))
    out_rounds = []
    for rdir in rounds:
        summ = read(rdir / "gatea-summary.txt")
        cnt = counters(summ)
        ring = read(rdir / "gatea-ring.txt")
        slice_text = logcat_slice(raw, rdir)
        rec = {
            "round": int(rdir.name[1:]),
            "samples": {},
            "counters_at_close": cnt,
            "derived": derived_state(ring, cnt["ring_overflow"], slice_text),
            "session": session_tuple(slice_text),
            "client_rc": _rc(read(rdir / "client-rc.txt")),
            "x_alive_after_terminate": _bool(read(rdir / "x-close.txt")),
            # activity-pid.txt is written by the B/C branches only. The same
            # observation is already inside every sample (activity.pid), captured by
            # the same adb call, so fall back to it rather than reporting NOT
            # OBSERVED for a value that WAS observed. Preference order matters: the
            # file is the round-scoped record, the sample is the per-point one.
            "activity_pid": _kv(read(rdir / "activity-pid.txt"), "activity_pid"),
            # mode E only: each round's own ending, read from its own capture.
            "halts_in_round": [{"what": m.group(1), "reason": int(m.group(2))}
                               for m in HALT.finditer(slice_text)],
            "x_alive_after_ending": _bool(read(rdir / "ending.txt")),
            "activity_alive_after_ending": _act_alive(read(rdir / "ending.txt")),
        }
        if rec["activity_pid"] is None:
            for s_ in sorted(rdir.glob("sample-*.json")):
                try:
                    pid = (json.loads(s_.read_text()).get("activity") or {}).get("pid")
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(pid, int):
                    rec["activity_pid"] = pid
                    rec["activity_pid_source"] = "sample"
                    break
        for s in sorted(rdir.glob("sample-*.json")):
            tag = s.stem[len("sample-"):]
            try:
                rec["samples"][tag] = json.loads(s.read_text())
            except (OSError, json.JSONDecodeError):
                rec["samples"][tag] = None
        out_rounds.append(rec)

    halts = [{"what": m.group(1), "reason": int(m.group(2))}
             for m in HALT.finditer(raw)]
    return {
        "schema_version": 1,
        "mode": binding.get("mode"),
        "rounds_declared": binding.get("rounds"),
        "source_sha": binding.get("source_sha"),
        "apk_sha256": binding.get("apk_sha256"),
        "stable_before": _json(ev / "stable-before.json"),
        "stable_after": _json(ev / "stable-after.json"),
        "halts": halts,
        "x11_unix_after": read(ev / "x11-unix-after.txt").split(),
        "rounds": out_rounds,
    }


def _json(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _rc(text: str):
    m = re.search(r"client_rc=(\d+)", text)
    return int(m.group(1)) if m else None


def _bool(text: str):
    m = re.search(r"=(true|false)", text)
    return {"true": True, "false": False}[m.group(1)] if m else None


def _act_alive(text: str):
    """`activity_pid_after=` with nothing after it is an OBSERVED absence - the pid
    was looked for and was not there. Absence of the line entirely is null."""
    m = re.search(r"activity_pid_after=(\S*)", text)
    if not m:
        return None
    return bool(m.group(1).strip())


def _kv(text: str, key: str):
    m = re.search(rf"{key}=(\S+)", text)
    return int(m.group(1)) if m and m.group(1).isdigit() else None


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    ev = Path(a.evidence)
    led = build(ev)
    text = json.dumps(led, indent=2) + "\n"
    Path(a.out or (ev / "ledger.json")).write_text(text)
    print(f"R10_LEDGER_BUILT mode={led['mode']} rounds={len(led['rounds'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
