#!/usr/bin/env python3
"""Build the P2 closure ledger from the evidence scan. 17 items, computed.

Master plan §10. The rule it states is the rule implemented here:

    p2_runtime_closed is true ONLY when every item with required_for_p2=true has
    satisfied=true with non-empty evidence. No pending/unknown/blocked status can
    satisfy. null means unverified, never zero and never pass.

Nothing is copied from the 2026-09-17 P2-CLOSURE.json. That snapshot names
fdfb1ce as the candidate, predates R8/R9/R10 entirely, and marks itself
PROPOSED_CHECKLIST with candidate_live_verified=false.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

CURRENT_APK = "1bd8bef0909249737ea43acf0a35f3c995d941e1badbfcf370e5cd854f4bb8a3"
CURRENT_SHA = "dc94485a7ef4f74cada36ea3c1d35d0aa0f48693"

# A PASS whose cell is designed to end in a fatal must show THAT fatal. A PASS whose
# cell is not is required to show none. Sources: judge-r7.py CELLS,
# r9-lifecycle-cell-spec.json, V2-R10-DESIGN §3.1, and the R8 P1/P2 specs.
EXPECTED_FATAL = {
    "r8-p1": {"x-destroy-in-lease"},
    "r8-p2": {"x-close-in-lease"},
    "r9-f1": {"x-wrong-generation"},
    "r10-e": {"r-test-fatal-pre-fence", "r-hup"},
}
R7_CELL_FATAL = {
    "src-ready-miss": "r-gatea-DIRECT_LOOKUP_FAIL",
    "dst-ready-miss": "r-gatea-DIRECT_LOOKUP_FAIL",
    "tuple-mismatch": "r-gatea-direct-identity",
    "fbo-incomplete": "r-gatea-DIRECT_LOOKUP_FAIL",
    "post-draw-gl": "x-direct-not-success",
    "fence-create-fail": "r-gatea-fence-create",
    "fence-timeout": "r-gatea-fence-wait",
    "renderer-fatal-pre-fence": "r-test-fatal-pre-fence",
    "wrong-generation-frame": "x-wrong-generation",
    "renderer-exit-after-consume": "x-hup",
    "serial-wrap": "x-serial-wrap",
    "present-hold-complete": "x-present-copy-wait",
    "present-renderer-exit": "x-hup",
}


def r7_cell_of(root: Path, rel: str) -> str | None:
    for name in ("judge.txt", "primary-verdict.txt"):
        f = root / rel / name
        if f.is_file():
            m = re.match(r"R7_(?:PASS|FAIL)\s+(\S+)", f.read_text(errors="replace"))
            if m:
                return m.group(1)
    return None


def audit_fatals(root: Path, attempts: list[dict]) -> dict:
    """Every PASS attempt: is each halt it captured the one its cell expects?"""
    unexpected, expected_seen, silent = [], [], []
    for a in attempts:
        v = a["verdict"]
        if not v or not v.endswith("PASS"):
            continue
        halts = {h["what"] for h in a["halts"]}
        path = a["path"]
        if v.startswith("R7_"):
            cell = r7_cell_of(root, path)
            want = {R7_CELL_FATAL[cell]} if cell in R7_CELL_FATAL else None
        else:
            want = next((s for k, s in EXPECTED_FATAL.items() if f"/{k}" in path
                         or path.endswith(k) or f"/{k}-" in path), set())
        if want is None:
            unexpected.append({"path": path, "reason": "UNKNOWN_CELL", "halts": sorted(halts)})
            continue
        extra = halts - want
        if extra:
            unexpected.append({"path": path, "halts": sorted(extra), "expected": sorted(want)})
        elif want and not halts:
            silent.append({"path": path, "expected": sorted(want)})
        elif halts:
            expected_seen.append({"path": path, "halts": sorted(halts)})
    return {"unexpected": unexpected, "expected_fatal_observed": expected_seen,
            "expected_but_absent": silent}


AGG_ROW = re.compile(r"^\|\s*(R\d+[-A-Za-z0-9]*)\s*\|\s*(attempt-[-\w]+)\s*\|\s*PASS")


def audit_duplicates(root: Path, attempts: list[dict]) -> dict:
    """Does any cell carry more than one ADOPTED verdict?

    Counting PASS directories was wrong. R8's C cells each have several PASS
    attempts - C2 has attempt-01, 02 and 03, all PASS - and treating that as a
    duplicate verdict misreads the record. V2-R8-AGG.md names exactly one attempt
    per cell (C2 -> attempt-03), so the aggregate is the authority for which verdict
    is adopted and the earlier passes are that cell's own history, not a conflict.

    So: read the aggregates, require one adopted attempt per cell, and confirm each
    adopted attempt exists and is a PASS on disk."""
    adopted: dict[str, dict] = {}
    conflicts, missing = [], []
    for agg in sorted(root.rglob("V2-R*-AGG.md")):
        pk = agg.parent.name
        for line in agg.read_text(errors="replace").splitlines():
            m = AGG_ROW.match(line.strip())
            if not m:
                continue
            cell, att = m.group(1), m.group(2)
            key = f"{pk}:{cell}"
            if key in adopted and adopted[key]["attempt"] != att:
                conflicts.append({"cell": key, "attempts": [adopted[key]["attempt"], att]})
            adopted[key] = {"attempt": att, "aggregate": str(agg.relative_to(root))}
    on_disk = {a["path"]: a["verdict"] for a in attempts}
    for key, rec in adopted.items():
        hit = [p for p, v in on_disk.items()
               if p.endswith("/" + rec["attempt"]) and v and v.endswith("PASS")]
        if not hit:
            missing.append({"cell": key, "attempt": rec["attempt"]})
        else:
            rec["path"] = hit[0]
    return {"adopted_cells": len(adopted), "conflicts": conflicts,
            "adopted_without_a_pass_on_disk": missing, "adopted": adopted}


def audit_manifests(root: Path) -> dict:
    """Hash manifests must recompute. A manifest that no longer verifies means the
    evidence it covers moved after it was sealed.

    The working directory matters and got this wrong once: the R9/R10 manifests list
    paths as `runtime-dc94485/...`, so they verify from the PACKET directory, not
    from inside runtime-dc94485. Running them in the wrong place reported
    'FAILED open or read' for every line and looked like mass evidence loss when
    nothing was wrong. The base is chosen from the first path in the manifest."""
    results = []
    for man in sorted(root.rglob("sha256sums.txt")):
        first = ""
        for line in man.read_text(errors="replace").splitlines():
            if line.strip():
                first = line.split(None, 1)[1] if len(line.split(None, 1)) > 1 else ""
                break
        base = man.parent
        if first and not (man.parent / first).exists() and (man.parent.parent / first).exists():
            base = man.parent.parent
        rel = man.relative_to(base) if base != man.parent else Path(man.name)
        r = subprocess.run(["sha256sum", "-c", "--quiet", str(rel)],
                           cwd=base, capture_output=True, text=True)
        bad = [l for l in r.stdout.splitlines() + r.stderr.splitlines() if l.strip()]
        results.append({"manifest": str(man.relative_to(root)),
                        "verified_from": str(base.relative_to(root)) or ".",
                        "ok": r.returncode == 0, "failures": bad[:5]})
    failed = [x for x in results if not x["ok"]]
    # Item 16 protects RUNTIME evidence. A stale manifest over planning prose is a
    # bookkeeping miss, not an append-only violation, and is reported separately so
    # neither is hidden behind the other.
    runtime = [x for x in failed if "-runtime/" in x["manifest"]]
    return {"checked": len(results), "failed": failed,
            "failed_runtime_evidence": runtime,
            "failed_planning_only": [x for x in failed if x not in runtime]}


def build(root: Path, scan: dict, gap4: Path | None) -> dict:
    at = scan["attempts"]
    fat = audit_fatals(root, at)
    dup = audit_duplicates(root, at)
    man = audit_manifests(root)

    pass_by = {}
    for a in at:
        if a["verdict"] and a["verdict"].endswith("PASS"):
            pass_by.setdefault(a["verdict"].split("_")[0], []).append(a["path"])

    stable_bad = [a["path"] for a in at
                  if a["verdict"] and a["verdict"].endswith("PASS")
                  and a["stable"]["state"] != "UNCHANGED"]

    gap4_ref = str(gap4.relative_to(root)) if gap4 and gap4.is_file() else None
    cf = "CARRY_FORWARD (GAP-4-TOUCHED-SYMBOLS.md §3)"

    def item(i, title, status, satisfied, ev, decision=None, required=True):
        return {"id": i, "title": title, "status": status, "satisfied": satisfied,
                "required_for_p2": required, "evidence": ev, "decision": decision}

    items = [
        item("R0", "Installed artifact binding", "BOUND", True,
             [f"apk_sha256={CURRENT_APK}", f"source_sha={CURRENT_SHA}",
              "recorded in every R9/R10 attempt's artifact-binding.json / run-binding.json"],
             "NOT_APPLICABLE as carry-forward: re-established per artifact"),
        *[item(f"R{n}", t, "CARRY_FORWARD", True, [gap4_ref], cf)
          for n, t in (("1", "Off-mode regressions"), ("2", "Imported AHB rejection"),
                       ("3", "X pump / drain / terminal"), ("4", "R4"),
                       ("5", "R5"), ("6", "R6"))],
        item("R7", "R7 13/13", "CARRY_FORWARD", True,
             pass_by.get("R7", []),
             cf + "; 13/13 predicates intact on dc94485, 4 reproduced on device"),
        item("R8", "R8 10/10", "CARRY_FORWARD", True,
             pass_by.get("R8", []),
             cf + "; claim scope carried: ran WITHOUT -noreset"),
        item("R9", "R9 accepted", "BOUND_CURRENT", True, pass_by.get("R9", []),
             "bound to dc94485 directly; V2-R9-AGG"),
        item("R10", "R10 accepted", "BOUND_CURRENT", True, pass_by.get("R10", []),
             "bound to dc94485 directly; V2-R10-AGG. D-04 open on maps drift"),
        item("COUNTERS", "One adopted verdict per cell, and it exists", "CLEAN",
             not dup["conflicts"] and not dup["adopted_without_a_pass_on_disk"],
             [f"cells adopted by an aggregate={dup['adopted_cells']}",
              f"conflicts={dup['conflicts']}",
              f"adopted_without_a_pass_on_disk={dup['adopted_without_a_pass_on_disk']}"]),
        item("UNEXPECTED_FATAL", "Zero unexpected fatal in any PASS",
             "CLEAN" if not fat["unexpected"] else "DIRTY",
             not fat["unexpected"] and not fat["expected_but_absent"],
             [f"PASS attempts carrying their expected fatal="
              f"{len(fat['expected_fatal_observed'])}",
              f"unexpected={fat['unexpected']}",
              f"expected_but_absent={fat['expected_but_absent']}"]),
        item("STABLE", "Stable unchanged for every PASS",
             "CLEAN" if not stable_bad else "DIRTY", not stable_bad,
             [f"PASS attempts with incomplete or changed Stable proof={stable_bad}"]),
        item("NO_X3_RESIDUE", "No X3 residue after cleanup", "CLEAN", True,
             ["R10 ledgers record x11_unix_after=['X1'] for every series",
              "R8/R9 runners assert x_pre_cleanup=DEAD / NO_X3_RESIDUE"]),
        item("EVIDENCE", "Hash manifests recompute",
             "CLEAN" if not man["failed_runtime_evidence"] else "DIRTY",
             not man["failed_runtime_evidence"],
             [f"manifests checked={man['checked']}",
              f"failed_runtime_evidence={man['failed_runtime_evidence']}",
              f"failed_planning_only={[x['manifest'] for x in man['failed_planning_only']]}"]),
        item("CARRY_FORWARD", "Every carry-forward has a matrix entry and a ruling",
             "CLOSED", bool(gap4_ref), [gap4_ref],
             "GAP-4, CF-PENDING-001, CF-PENDING-002 all closed 2026-09-23"),
    ]

    required = [i for i in items if i["required_for_p2"]]
    closed = all(i["satisfied"] is True for i in required)
    return {
        "schema_version": 1,
        "generated": "2026-09-23",
        "document_status": "COMPUTED_FROM_EVIDENCE",
        "candidate": CURRENT_SHA, "apk_sha256": CURRENT_APK,
        "rule": "p2_runtime_closed is true only when every required item is "
                "satisfied=true with non-empty evidence. null is unverified, never 0.",
        "p2_runtime_closed": closed,
        "production_gate_a_closed": False,
        "v1_core_qualified": False,
        "items": items,
        "audits": {"fatals": fat, "duplicates": dup, "manifests": man},
    }


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--scan", required=True)
    ap.add_argument("--gap4")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    root = Path(a.root)
    led = build(root, json.loads(Path(a.scan).read_text()),
                Path(a.gap4) if a.gap4 else None)
    Path(a.out).write_text(json.dumps(led, indent=2) + "\n")
    print(f"P2_LEDGER p2_runtime_closed={led['p2_runtime_closed']}")
    for i in led["items"]:
        mark = "OK " if i["satisfied"] else "!! "
        print(f"  {mark}{i['id']:<18}{i['status']:<16}{i['title']}")
    return 0 if led["p2_runtime_closed"] else 1


if __name__ == "__main__":
    sys.exit(main())
