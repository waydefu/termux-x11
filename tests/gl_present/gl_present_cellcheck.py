#!/usr/bin/env python3
"""GL-PRESENT cell check - run by the chain right after each main cell, so a doomed attempt stops early
(user rule 2026-09-26: confirm before running, check while running, do not burn whole runs).

    gl_present_cellcheck.py <main cell dir>   -> CELLCHECK_OK | CELLCHECK_FAIL <reasons>; exit 0 | 1

Uses the frozen judge's own segment() / classify rules (imported, not copied), per cell:
  - every segment valid (an invalid segment in one rep already costs its API the A/A pair);
  - the per-cell J0 conditions: on-screen not UNEXPLAINED, off segments hand nothing over and have a
    readable protocol histogram, the sw control is not DRI3.
It decides nothing: verdicts come only from gl_present_judge.py over both reps.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gl_present_judge as j  # noqa: E402


def check(cell):
    why = []
    segs = j.load_cell(cell)
    for name, s in segs.items():
        if not s["valid"]:
            why.append(f"{name}:invalid:{'/'.join(s['why'])}")
    for name in j.ONSCREEN + [j.SW_CONTROL]:
        s = segs[name]
        if s["valid"] and s.get("path") == "UNEXPLAINED":
            why.append(f"{name}:unexplained_frames")
    for name in j.OFFSCREEN:
        s = segs[name]
        if not s["valid"]:
            continue
        if any(s["counts"].get(k, 0) for k in j.HANDOFF_COUNTS):
            why.append(f"{name}:handoff_counted_without_presenting")
        h = s.get("req")
        if h is None:
            why.append(f"{name}:protocol_histogram_missing")
        elif any((r["ext"], r["op"]) in j.HANDOFF_EXT_REQS and r["n"] > 0 for r in h.get("requests", [])) or \
                any(m["major"] in j.PRESENT_CORE_MAJORS and m["n"] > 0 for m in h.get("majors", [])):
            why.append(f"{name}:protocol_handoff_without_presenting")
    sw = segs[j.SW_CONTROL]
    if sw["valid"] and sw.get("path") not in j.NON_DRI3_PATHS:
        why.append(f"{j.SW_CONTROL}:path={sw.get('path')}")
    return why


if __name__ == "__main__":
    reasons = check(sys.argv[1])
    print("CELLCHECK_OK" if not reasons else "CELLCHECK_FAIL " + " ".join(reasons))
    sys.exit(1 if reasons else 0)
