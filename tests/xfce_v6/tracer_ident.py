#!/usr/bin/env python3
"""Who is ptrace-tracing a process? (XFCE-V6-BASELINE-01 section 4, items 1-2)

    tracer_ident.py [--pid PID] [--proc-root /proc] [--out FILE]

Prints / writes JSON for PID (default: this process):
  {"pid", "tracer_pid", "tracer_comm", "tracer_exe", "tracer_sha256", "proot_env"}
tracer_pid 0 means untraced; then the tracer_* fields are null. proot_env holds every PROOT_*
variable of the tracer (the v5/v6 off switches live there). Any read failure is recorded as
null, never guessed. --proc-root exists for the unit tests.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path


def tracer_of(proc: Path, pid: int | str) -> int | None:
    try:
        m = re.search(r"^TracerPid:\s+(\d+)", (proc / str(pid) / "status").read_text(), re.M)
    except OSError:
        return None
    return int(m.group(1)) if m else None


def sha256_file(path: Path) -> str | None:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def ident(proc: Path, pid: int | str) -> dict:
    tp = tracer_of(proc, pid)
    out = {"pid": int(pid), "tracer_pid": tp, "tracer_comm": None, "tracer_exe": None,
           "tracer_sha256": None, "proot_env": None}
    if not tp:
        return out
    base = proc / str(tp)
    try:
        out["tracer_comm"] = (base / "comm").read_text().strip()
    except OSError:
        pass
    try:
        out["tracer_exe"] = os.readlink(base / "exe")
    except OSError:
        pass
    out["tracer_sha256"] = sha256_file(base / "exe")
    try:
        env = (base / "environ").read_bytes().split(b"\0")
        out["proot_env"] = dict(sorted(e.decode(errors="replace").split("=", 1) for e in env
                                       if e.startswith(b"PROOT_") and b"=" in e))
    except OSError:
        pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", default=str(os.getpid()))
    ap.add_argument("--proc-root", default="/proc")
    ap.add_argument("--out")
    a = ap.parse_args()
    d = ident(Path(a.proc_root), a.pid)
    text = json.dumps(d, indent=2, sort_keys=True)
    if a.out:
        Path(a.out).write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
