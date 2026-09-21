#!/usr/bin/env python3
"""Collect R8_OBS producer records. Never fabricate missing records."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from r8_obs_stream import obs_loads

PAT = re.compile(r"R8_OBS\s+(\{.*\})\s*$")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--out-x", required=True)
    ap.add_argument("--out-r", required=True)
    ap.add_argument("--completeness", required=True)
    args = ap.parse_args()
    text = Path(args.raw).read_text(encoding="utf-8", errors="replace")
    xs, rs = [], []
    for line in text.splitlines():
        m = PAT.search(line)
        if not m:
            continue
        try:
            obj = obs_loads(m.group(1))
        except json.JSONDecodeError:
            print("INVALID OBS_JSON", file=sys.stderr)
            return 2
        role = obj.get("role")
        if role == "x":
            xs.append(obj)
        elif role == "r":
            rs.append(obj)
        else:
            print("INVALID OBS_ROLE", file=sys.stderr)
            return 2
    Path(args.out_x).write_text("".join(json.dumps(x) + "\n" for x in xs), encoding="utf-8")
    Path(args.out_r).write_text("".join(json.dumps(x) + "\n" for x in rs), encoding="utf-8")
    Path(args.completeness).write_text(json.dumps({
        "x_count": len(xs), "r_count": len(rs),
        "fabricated": False,
    }, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
