#!/usr/bin/env python3
"""Scan the evidence tree and report what is actually there, per attempt.

This is the INPUT to the P2 closure ledger, not the ledger itself. It reads; it does
not decide. Every field is what was found, and a field that was not found is null.

The master plan's closure principle is that a historical pass is not automatically
valid: R0-R7 were taken on older PRODUCT_SHAs and must either be retaken on the
current one or carried forward by an explicit decision. A scan cannot make that
decision, but it can say exactly which artifact each attempt is bound to, which is
the fact the decision needs.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HALT = re.compile(r"GATEA_FATAL_HALT what=(\S+) reason=(\d+)")
VERDICT_TOKEN = re.compile(r"\b(R8|R9|R10|R7|R6)_(PASS|FAIL|INVALID|BLOCKED)\b")


def read(p: Path) -> str:
    try:
        return p.read_text(errors="replace")
    except OSError:
        return ""


def jload(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def verdict_of(d: Path):
    """Verdict token and where it came from. Three formats exist in this tree, and
    the newest is not always present, so the source is recorded with the value."""
    j = jload(d / "judge.json")
    if isinstance(j, dict) and j.get("verdict"):
        return j["verdict"], "judge.json"
    # The R7-era layout predates judge.json: its verdict is a bare token in
    # judge.txt / primary-verdict.txt, written by judge-r7.py. Reading only the
    # newer format silently drops every R7 attempt from the scan, which is how this
    # scanner first reported 61 attempts and no R7 at all.
    for name in ("judge.stdout", "VERDICT.txt", "judge.txt", "primary-verdict.txt"):
        m = VERDICT_TOKEN.search(read(d / name))
        if m:
            return m.group(0), name
    return None, None


def artifact_of(d: Path):
    """Which installed artifact this attempt is bound to. apk_sha256 is the only
    identifier that cannot be edited into agreement after the fact."""
    for name in ("artifact-binding.json", "run-binding.json"):
        j = jload(d / name)
        if isinstance(j, dict):
            apk = j.get("apk_sha256")
            src = j.get("source_sha")
            if apk or src:
                return {"apk_sha256": apk, "source_sha": src, "from": name}
    m = re.search(r"installed_apk_sha256=([0-9a-f]{64})",
                  read(d / "installed-apk.sha256.txt"))
    if m:
        return {"apk_sha256": m.group(1), "source_sha": None,
                "from": "installed-apk.sha256.txt"}
    for name in ("artifact-binding.txt", "installed-package.txt"):
        txt = read(d / name)
        m = re.search(r"\b([0-9a-f]{64})\b", txt)
        if m:
            return {"apk_sha256": m.group(1), "source_sha": None, "from": name}
        # the R7-era layout records the versionName, which carries the source sha
        v = re.search(r"versionName=(\S*?-([0-9a-f]{7})-\S*)", txt)
        if v:
            return {"apk_sha256": None, "source_sha": v.group(2),
                    "version_name": v.group(1), "from": name}
    return {"apk_sha256": None, "source_sha": None, "from": None}


R7_STABLE_PRE = ("stable-pre.txt", "stable-pre-live.txt", "stable-pre-local.txt",
                 "stable-snapshot.txt")
R7_STABLE_AFTER = ("stable-after.txt", "stable-after-live.txt",
                   "stable-package-after.txt", "stable-package-after-full.txt",
                   "stable-package-after-live2.txt", "stable-x3-after.txt")
R7_STABLE_PKG_PRE = ("stable-package-pre.txt", "stable-package-pre-live.txt")


def _r7_identity(text: str) -> dict:
    """Normalise an R7-era stable capture to the facts it asserts.

    The field NAMES differ between the pre and after files - `stable_pre_pid` versus
    `stable_after_pid` - so comparing the raw text reports CHANGED for a capture that
    says the identical thing. That is how this scanner first flagged
    runtime-fdfb1ce/r7-04-requalification-01 as CHANGED when both files record pid
    24999 and the same cmdline.
    """
    out = {}
    m = re.search(r"stable_(?:pre|after)_pid=(\d+)", text)
    if m:
        out["pid"] = int(m.group(1))
    m = re.search(r"stable_(?:pre|after)_cmd=(.*)", text)
    if m:
        out["cmd"] = m.group(1).strip()
    m = re.search(r"versionName=(\S+)", text)
    if m:
        out["versionName"] = m.group(1)
    m = re.search(r"lastUpdateTime=(.+)", text)
    if m:
        out["lastUpdateTime"] = m.group(1).strip()
    return out


def stable_of(d: Path):
    """before == after. Missing 'after' is NOT 'unchanged' - it is a proof that was
    never completed, and it is reported as such.

    Two layouts exist. The R8+ one is a pair of JSON files. The R7-era one is plain
    text under any of thirteen different names, with the field name encoding which
    side it is, so it is normalised to (pid, cmd, versionName, lastUpdateTime)
    before comparison. Only the keys present on BOTH sides are compared: a package
    dump and a process snapshot assert different things, and demanding that a
    process capture carry a versionName would report a difference that does not
    exist."""
    b, a = d / "stable-before.json", d / "stable-after.json"
    if b.is_file():
        jb = jload(b)
        if not a.is_file():
            return {"state": "NO_AFTER", "pid": (jb or {}).get("pid"),
                    "format": "json"}
        ja = jload(a)
        return {"state": "UNCHANGED" if jb == ja else "CHANGED",
                "pid": (jb or {}).get("pid"), "format": "json"}

    pre_files = [d / n for n in R7_STABLE_PRE + R7_STABLE_PKG_PRE if (d / n).is_file()]
    aft_files = [d / n for n in R7_STABLE_AFTER if (d / n).is_file()]
    if not pre_files:
        return {"state": "NO_BEFORE", "pid": None}
    pre = {}
    for f in pre_files:
        pre.update(_r7_identity(read(f)))
    if not aft_files:
        return {"state": "NO_AFTER", "pid": pre.get("pid"), "format": "r7_text"}
    aft = {}
    for f in aft_files:
        aft.update(_r7_identity(read(f)))
    shared = set(pre) & set(aft)
    if not shared:
        return {"state": "NOT_COMPARABLE", "pid": pre.get("pid"),
                "format": "r7_text",
                "note": "pre and after capture disjoint facts"}
    same = all(pre[k] == aft[k] for k in shared)
    return {"state": "UNCHANGED" if same else "CHANGED", "pid": pre.get("pid"),
            "format": "r7_text", "compared_keys": sorted(shared),
            "diff": None if same else {k: [pre[k], aft[k]] for k in shared
                                       if pre[k] != aft[k]}}


def halts_of(d: Path):
    out = []
    for name in ("raw-logcat.txt", "collect-input.txt", "judge.stdout"):
        for m in HALT.finditer(read(d / name)):
            out.append({"what": m.group(1), "reason": int(m.group(2))})
        if out:
            break
    return out


def is_attempt(d: Path) -> bool:
    """An attempt directory is one that carries at least one of the artefacts a
    device run produces. Planning and design directories are not attempts."""
    markers = ("judge.json", "judge.stdout", "VERDICT.txt", "artifact-binding.json",
               "artifact-binding.txt", "installed-apk.sha256.txt", "run-binding.json",
               "judge.txt", "primary-verdict.txt", "installed-package.txt")
    return any((d / m).is_file() for m in markers)


def scan(root: Path) -> dict:
    attempts = []
    for d in sorted(p for p in root.rglob("*") if p.is_dir()):
        if not is_attempt(d):
            continue
        rel = d.relative_to(root)
        v, vsrc = verdict_of(d)
        attempts.append({
            "path": str(rel),
            "packet": rel.parts[0] if rel.parts else None,
            "artifact_group": next((p for p in rel.parts if p.startswith("runtime-")), None),
            "verdict": v, "verdict_source": vsrc,
            "artifact": artifact_of(d),
            "stable": stable_of(d),
            "halts": halts_of(d),
            "has_manifest": (d / "sha256sums.txt").is_file()
                            or (d / "full-evidence-sha256sums.txt").is_file(),
            "frozen_note": sorted(p.name for p in d.glob("*FROZEN*")),
        })
    return {"root": str(root), "attempts": attempts}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    res = scan(Path(a.root))
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=2) + "\n")
    at = res["attempts"]
    print(f"P2_SCAN attempts={len(at)}")
    from collections import Counter
    print("  verdicts :", dict(Counter(x["verdict"] for x in at)))
    print("  artifacts:", dict(Counter(x["artifact_group"] for x in at)))
    print("  stable   :", dict(Counter(x["stable"]["state"] for x in at)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
