#!/usr/bin/env python3
"""PGA-GAP-3 host test (source level): the per-transaction staging upload is unregistered
from the renderer when its composite transaction ends.

    python3 tests/pga/test_gap3_unregister.py [path/to/InitOutput.c]

Rules (planning-v2/pga/PGA-GAP-3-DESIGN.md), checked on lorieExaDoneComposite:
  R1  the non-direct part calls lorieUnregisterBuffer(upload)
  R2  guarded so the D0a cache buffer (d0aStagingCache) keeps its registration
  R3  AFTER the completion wait (lorieGpuCopyWaitForCompositeOrFatal) - the renderer is done
  R4  BEFORE the first LorieBuffer_release(upload) of the non-direct part
The control: bfb5769 (the unpatched base) must FAIL R1 - a test that passes on the defect
is not a test. `--expect-red` asserts exactly that.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT = HERE.parent.parent / "lorie/src/main/cpp/lorie/InitOutput.c"


def function_body(src: str, name: str) -> str | None:
    m = re.search(rf"^static void {name}\([^)]*\)\s*\{{", src, re.M)
    if not m:
        return None
    i, depth = m.end(), 1
    while depth and i < len(src):
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        i += 1
    return src[m.end():i - 1]


def check(src: str) -> dict:
    body = function_body(src, "lorieExaDoneComposite")
    res = {"function_found": body is not None}
    if body is None:
        return res
    # the direct branch returns early; the rest is the staging / legacy part
    d = re.search(r"if \(exaGpuComp\.direct\) \{.*?\n        return;\n    \}", body, re.S)
    rest = body[d.end():] if d else body
    res["direct_branch_found"] = d is not None
    call = re.search(r"if \(upload && upload != d0aStagingCache\)\s*\n\s*lorieUnregisterBuffer\(upload\);", rest)
    any_call = "lorieUnregisterBuffer(upload)" in rest
    wait = rest.find("lorieGpuCopyWaitForCompositeOrFatal(")
    rel = rest.find("LorieBuffer_release(upload)")
    res["R1_unregister_called"] = any_call
    res["R2_cache_excluded"] = call is not None
    pos = call.start() if call else (rest.find("lorieUnregisterBuffer(upload)") if any_call else -1)
    res["R3_after_wait"] = any_call and wait >= 0 and pos > wait
    res["R4_before_release"] = any_call and rel >= 0 and pos < rel
    res["pass"] = all(res[k] for k in ("function_found", "direct_branch_found", "R1_unregister_called",
                                       "R2_cache_excluded", "R3_after_wait", "R4_before_release"))
    return res


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = Path(args[0]) if args else DEFAULT
    res = check(path.read_text(errors="replace"))
    print(f"GAP3_UNREGISTER {'PASS' if res.get('pass') else 'FAIL'} {res}")
    if "--expect-red" in sys.argv:
        return 0 if not res.get("pass") and res.get("function_found") and not res.get("R1_unregister_called") else 1
    return 0 if res.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
