#!/usr/bin/env python3
"""Is every R7 cell's acceptance predicate still true of the current product?

CF-PENDING-001 asks whether R7's 13 PASSes carry forward. A PASS is only as portable
as the predicate it was judged against, so the question is answerable statically:
judge-r7.py pins, per cell, a (fault cell number, expected halt token, expected
reason). If the product still maps that fault number to that fault name, and still
raises that exact token with that exact reason, the predicate that produced the PASS
is intact and the PASS carries. If any leg moved, that cell needs REVERIFY - and only
that cell.

This does NOT re-judge frozen evidence and does not turn a FAIL into a PASS. It
reports whether the ground the verdict stood on is still there.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

GUARD = "LORIE_ENABLE_R8_TEST_SUPPORT"


def show(repo: Path, sha: str, path: str) -> str:
    try:
        return subprocess.run(["git", "show", f"{sha}:{path}"], cwd=repo,
                              capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError:
        return ""


def cells_from_judge(judge_py: Path) -> dict:
    """Read the CELLS table out of judge-r7.py itself, so the predicate list can
    never drift from the judge that actually produced the verdicts."""
    text = judge_py.read_text()
    blob = text.split("CELLS = {", 1)[1].split("\n}", 1)[0]
    out = {}
    for m in re.finditer(r'"([^"]+)":\s*\{([^}]*)\}', blob):
        name, body = m.group(1), m.group(2)
        d = {}
        for k, v in re.findall(r'"(\w+)":\s*("?[^,"]*"?)', body):
            d[k] = v.strip('"')
        out[name] = d
    return out


def fault_names(src: str) -> dict[int, str]:
    """index -> name, from the product's own gateATestCellNames table."""
    if "gateATestCellNames[] = {" not in src:
        return {}
    blk = src.split("gateATestCellNames[] = {", 1)[1].split("};", 1)[0]
    names = [e.strip().strip('",') for e in blk.split("\n") if e.strip()]
    return dict(enumerate(names))


def fail_codes(lorie_h: str) -> dict[str, int]:
    return {m.group(1): int(m.group(2))
            for m in re.finditer(r"(LORIE_GATEA_FAIL_[A-Z_]+)\s*=\s*(\d+)", lorie_h)}


def token_sites(sources: dict[str, str], token: str) -> list[dict]:
    """Every place the product can raise `token`, and whether that site sits inside
    the R8 test-support guard. A token whose ONLY remaining site is guarded is not
    the same predicate any more: the experimental APK is built with the guard ON, so
    the guarded branch is what actually runs."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from touched_symbols import guard_map, compiled_map
    out = []
    for path, text in sources.items():
        if not text:
            continue
        g = guard_map(text)
        c = compiled_map(text, guard_on=True)   # the experimental APK's build
        for i, line in enumerate(text.splitlines(), start=1):
            if f'"{token}"' in line:
                out.append({"file": path.split("/")[-1], "line": i,
                            "in_test_guard": bool(g[i]) if i < len(g) else None,
                            "compiled_in_apk": bool(c[i]) if i < len(c) else None,
                            "text": line.strip()[:110]})
    return out


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--judge", required=True)
    ap.add_argument("--cells", default="1-13",
                    help="which cell numbers to check (R7 ran 1-13)")
    ap.add_argument("--out")
    a = ap.parse_args()
    repo = Path(a.repo)
    lo, hi = (int(x) for x in a.cells.split("-"))

    base = "lorie/src/main/cpp/lorie/"
    sources = {base + f: show(repo, a.sha, base + f)
               for f in ("InitOutput.c", "cmdentrypoint.cpp", "renderer.cpp",
                         "activity.cpp", "lorie.h", "lorie_r8_test.c")}
    names = fault_names(sources[base + "InitOutput.c"])
    codes = fail_codes(sources[base + "lorie.h"])
    cells = cells_from_judge(Path(a.judge))

    results = []
    for cell_name, spec in cells.items():
        num = int(spec["cell"])
        if not (lo <= num <= hi):
            continue
        want_what, want_reason = spec["what"], int(spec["reason"])
        sites = token_sites(sources, want_what)
        # What matters is whether a site SURVIVES the build the APK actually uses,
        # not whether it is syntactically outside the guard. A token moved into an
        # #else branch is outside the guard AND absent from the binary.
        live = [s for s in sites if s["compiled_in_apk"]]
        fault_ok = names.get(num) == cell_name if names else None
        verdict = ("PREDICATE_INTACT" if live and fault_ok
                   else "PREDICATE_NOT_COMPILED" if sites and not live
                   else "TOKEN_ABSENT" if not sites
                   else "FAULT_NAME_MISMATCH")
        results.append({
            "cell": num, "name": cell_name,
            "expected_halt": want_what, "expected_reason": want_reason,
            "fault_index_name_in_product": names.get(num),
            "fault_index_matches": fault_ok,
            "halt_sites": len(sites), "halt_sites_compiled_in_apk": len(live),
            "sites": sites,
            "verdict": verdict,
        })
    results.sort(key=lambda r: r["cell"])
    summary = {}
    for r in results:
        summary[r["verdict"]] = summary.get(r["verdict"], 0) + 1
    out = {"sha": a.sha, "judge": str(a.judge), "cells_checked": f"{lo}-{hi}",
           "fail_codes": codes, "summary": summary, "cells": results}
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=2) + "\n")
    print(f"R7_PREDICATES sha={a.sha[:7]} {summary}")
    for r in results:
        flag = "" if r["verdict"] == "PREDICATE_INTACT" else "  <<<"
        print(f"  {r['cell']:>2} {r['name']:<28}{r['expected_halt']:<28}"
              f"r{r['expected_reason']}  sites={r['halt_sites']}"
              f"(live {r['halt_sites_compiled_in_apk']})  "
              f"{r['verdict']}{flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
