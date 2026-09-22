#!/usr/bin/env python3
"""Touched-symbol / touched-semantics diff between two product SHAs.

This is the evidence GAP-4 and the CF-PENDING items need: for every line the diff
changes in PRODUCTION source, is that line inside the R8/R9 test-support guard, and
if it is not, does it change a product predicate?

The classification a line can get:

  TEST_GUARD      the line sits inside #ifdef LORIE_ENABLE_R8_TEST_SUPPORT.
                  The experimental APK IS built with -DLORIE_ENABLE_R8_TEST_SUPPORT=ON
                  (lorie/build.gradle), so this does NOT mean "not compiled". It means
                  the line is reachable only through the test-support surface, and the
                  #else branch preserves the product path.
  PRODUCT         the line is compiled unconditionally. Every one of these has to be
                  read and justified individually; the tool refuses to classify them
                  further, because "looks like a refactor" is a judgement and this
                  file only reports facts.

Nothing here decides a carry-forward. It produces the list a decision has to answer.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

GUARD = "LORIE_ENABLE_R8_TEST_SUPPORT"
PRODUCT_PREFIX = "lorie/src/main/cpp/lorie/"
JAVA_PREFIX = "lorie/src/main/java/"


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=True).stdout


def guard_map(text: str) -> list[bool]:
    """For each 1-indexed line: is it inside a GUARD-conditional block?

    Used by the diff classifier to answer "was this addition made under the
    test-support guard". It says nothing about whether the line is compiled.
    """
    inside = [False]
    stack: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if re.match(r"#\s*if(n?def)?\b", s):
            stack.append("guard-if" if (GUARD in s and not s.startswith("#ifndef"))
                         else "other")
        elif re.match(r"#\s*el(se|if)\b", s) and stack:
            if stack[-1] in ("guard-if", "guard-else"):
                stack[-1] = "guard-else"
        elif re.match(r"#\s*endif\b", s) and stack:
            stack.pop()
        inside.append(any(f == "guard-if" for f in stack))
    return inside


def compiled_map(text: str, guard_on: bool = True) -> list[bool]:
    """For each 1-indexed line: does this line EXIST in the built binary?

    This is the question a predicate check actually needs, and it is not the same as
    guard_map. The experimental APK is built with -DLORIE_ENABLE_R8_TEST_SUPPORT=ON
    (lorie/build.gradle), so under that build:

        #ifdef  GUARD   ...   -> compiled
        #else           ...   -> NOT compiled          <- the case guard_map misses
        #ifndef GUARD   ...   -> NOT compiled

    Getting this backwards is how a token that had been moved into an #else branch -
    i.e. removed from the shipped binary - was still being reported as an intact
    predicate, because guard_map correctly calls an #else "not guarded".
    """
    live = [False]
    stack: list[bool] = []     # is this conditional level currently compiled?
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"#\s*if(n?)def\s+(\w+)", s)
        if m:
            neg, name = m.group(1) == "n", m.group(2)
            if name == GUARD:
                stack.append((not guard_on) if neg else guard_on)
            else:
                stack.append(True)          # unrelated conditional: assume taken
        elif re.match(r"#\s*if\b", s):
            stack.append(True)
        elif re.match(r"#\s*else\b", s) and stack:
            stack[-1] = not stack[-1]
        elif re.match(r"#\s*elif\b", s) and stack:
            stack[-1] = True
        elif re.match(r"#\s*endif\b", s) and stack:
            stack.pop()
        live.append(all(stack))
    return live


def changed_lines(repo: Path, old: str, new: str, path: str):
    """(new_lineno, kind) for every added line, and the count of removed ones.

    Added lines can be located in the new file, so they can be classified. Removed
    lines cannot - they no longer exist - so they are counted and reported for
    individual reading rather than auto-classified."""
    diff = git("diff", "-U0", f"{old}..{new}", "--", path, cwd=repo)
    added, removed = [], 0
    newno = 0
    for line in diff.splitlines():
        m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
        if m:
            newno = int(m.group(1))
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added.append(newno)
            newno += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def analyse(repo: Path, old: str, new: str) -> dict:
    names = git("diff", "--name-only", f"{old}..{new}", cwd=repo).split()
    files = []
    for path in names:
        is_product = path.startswith(PRODUCT_PREFIX) or path.startswith(JAVA_PREFIX)
        if not is_product:
            continue
        try:
            text = git("show", f"{new}:{path}", cwd=repo)
        except subprocess.CalledProcessError:
            text = ""                      # deleted in new
        gmap = guard_map(text)
        added, removed = changed_lines(repo, old, new, path)
        in_guard = sum(1 for n in added if n < len(gmap) and gmap[n])
        files.append({
            "path": path,
            "added": len(added),
            "added_in_test_guard": in_guard,
            "added_in_product": len(added) - in_guard,
            "removed_or_modified": removed,
            "whole_file_guarded": text.lstrip().startswith(f"#ifdef {GUARD}"),
        })
    files.sort(key=lambda f: -f["added_in_product"])
    return {
        "old": old, "new": new,
        "product_files_touched": len(files),
        "total_added_in_product": sum(f["added_in_product"] for f in files),
        "total_added_in_test_guard": sum(f["added_in_test_guard"] for f in files),
        "total_removed_or_modified": sum(f["removed_or_modified"] for f in files),
        "files": files,
        "note": "added_in_product lines and every removed_or_modified line must be "
                "read individually. This tool reports where they are, not whether "
                "they are safe.",
    }


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    res = analyse(Path(a.repo), a.old, a.new)
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=2) + "\n")
    print(f"TOUCHED {a.old}..{a.new}  product_files={res['product_files_touched']}  "
          f"added_product={res['total_added_in_product']}  "
          f"added_guarded={res['total_added_in_test_guard']}  "
          f"removed_or_modified={res['total_removed_or_modified']}")
    for f in res["files"]:
        if f["added_in_product"] or f["removed_or_modified"]:
            print(f"  {f['path'].replace(PRODUCT_PREFIX,''):<24}"
                  f"prod+{f['added_in_product']:<5}guard+{f['added_in_test_guard']:<5}"
                  f"-{f['removed_or_modified']:<4}"
                  f"{'  [whole file guarded]' if f['whole_file_guarded'] else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
