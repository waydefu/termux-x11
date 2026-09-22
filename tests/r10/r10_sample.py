#!/usr/bin/env python3
"""R10 sampler — one resource sample, as JSON, from both Gate A processes.

Two measurement domains, and they are NOT symmetric:

  X server   runs in this PRoot rootfs, so /proc/<x_pid>/* is read directly.
  Activity   runs on the device. Plain `adb shell` can read only stat / status /
             statm; fd, maps and smaps_rollup are Permission denied. `run-as
             <package>` drops to the app's own uid and CAN read all three
             (measured: p2-r10-probe/probe-01). dumpsys meminfo adds the GPU
             attribution (EGL mtrack / GL mtrack / Gfx dev) that /proc cannot give.

NEVER FABRICATE. A value that could not be read is null. It is never 0, never "",
never "unknown". A metric that is null in one sample and a number in another is a
probe regression and the judge must say so rather than average over it.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ADB = "/data/data/com.termux/files/usr/bin/adb"
ADB_ENV_STRIP = ("ADB_SERVER_SOCKET", "ANDROID_ADB_SERVER_ADDRESS",
                 "ANDROID_ADB_SERVER_PORT")


def adb(serial: str, *args: str, timeout: int = 30) -> str | None:
    """stdout of one adb call, or None if it failed. Lane 5038 only."""
    import os
    env = {k: v for k, v in os.environ.items() if k not in ADB_ENV_STRIP}
    env["HOME"] = "/data/data/com.termux/files/home"
    env["ANDROID_NO_USE_FWMARK_CLIENT"] = "1"
    cmd = [ADB, "-H", "127.0.0.1", "-P", "5038", "-s", serial, *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.replace("\r", "")


def _int(text: str | None):
    if text is None:
        return None
    t = text.strip().split()
    return int(t[0]) if t and t[0].lstrip("-").isdigit() else None


# ----------------------------------------------------------------- X (local) --

def x_sample(pid: int | None) -> dict:
    def read(rel):
        if pid is None:
            return None
        try:
            return Path(f"/proc/{pid}/{rel}").read_text(errors="replace")
        except OSError:
            return None

    def status_field(name):
        s = read("status")
        if s is None:
            return None
        m = re.search(rf"^{name}:\s+(\d+)", s, re.M)
        return int(m.group(1)) if m else None

    def rollup(name):
        s = read("smaps_rollup")
        if s is None:
            return None
        m = re.search(rf"^{name}:\s+(\d+) kB", s, re.M)
        return int(m.group(1)) if m else None

    try:
        fd = len(list(Path(f"/proc/{pid}/fd").iterdir())) if pid else None
    except OSError:
        fd = None
    maps = read("maps")
    return {
        "pid": pid,
        "alive": bool(pid) and Path(f"/proc/{pid}").is_dir(),
        "fd_count": fd,
        "fd_table_size": status_field("FDSize"),
        "maps_count": len(maps.splitlines()) if maps is not None else None,
        "threads": status_field("Threads"),
        "rss_kb": status_field("VmRSS"),
        "vm_size_kb": status_field("VmSize"),
        "pss_kb": rollup("Pss"),
        "pss_anon_kb": rollup("Pss_Anon"),
        "pss_shmem_kb": rollup("Pss_Shmem"),
    }


# ------------------------------------------------------------ Activity (adb) --

MEMINFO_ROWS = {
    "native_heap_pss_kb": "Native Heap",
    "gfx_dev_pss_kb": "Gfx dev",
    "egl_mtrack_pss_kb": "EGL mtrack",
    "gl_mtrack_pss_kb": "GL mtrack",
}


def act_sample(serial: str, pkg: str, pid: int | None) -> dict:
    out = {"pid": pid, "alive": pid is not None}
    if pid is None:
        return {**out, **{k: None for k in (
            "fd_count", "fd_table_size", "maps_count", "threads", "rss_kb",
            "vm_size_kb", "pss_kb", *MEMINFO_ROWS)}}

    status = adb(serial, "shell", f"cat /proc/{pid}/status")

    def status_field(name):
        if not status:
            return None
        m = re.search(rf"^{name}:\s+(\d+)", status, re.M)
        return int(m.group(1)) if m else None

    # run-as: the ONLY way to reach fd / maps / smaps_rollup without root.
    # Never use a shell redirect here - it is evaluated by the OUTER shell, which
    # does not have permission, and silently yields an empty value.
    out["fd_count"] = _int(adb(serial, "shell",
                               f"run-as {pkg} ls /proc/{pid}/fd | wc -l"))
    out["maps_count"] = _int(adb(serial, "shell",
                                 f"run-as {pkg} wc -l /proc/{pid}/maps"))
    roll = adb(serial, "shell", f"run-as {pkg} cat /proc/{pid}/smaps_rollup")
    m = re.search(r"^Pss:\s+(\d+) kB", roll, re.M) if roll else None
    out["pss_kb"] = int(m.group(1)) if m else None
    out["fd_table_size"] = status_field("FDSize")
    out["threads"] = status_field("Threads")
    out["rss_kb"] = status_field("VmRSS")
    out["vm_size_kb"] = status_field("VmSize")

    mem = adb(serial, "shell", f"dumpsys meminfo {pkg}", timeout=60)
    for key, row in MEMINFO_ROWS.items():
        val = None
        if mem:
            mm = re.search(rf"^\s*{re.escape(row)}\s+(\d+)", mem, re.M)
            if mm:
                val = int(mm.group(1))
        out[key] = val
    return out


# ------------------------------------------------------------------- Gate A ---

def gatea_sample(summary_path: str, ring_path: str) -> dict:
    """Counters exist ONLY when lorieGateADumpSummary has run, which needs a fatal,
    a clean close or a terminate. At any other sampling point they are null, and
    that is a fact about the product, not a capture failure."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
    import gatea_counters as C
    try:
        summ = Path(summary_path).read_text(errors="replace")
    except OSError:
        summ = ""
    out = {"summary_present": bool(summ.strip())}
    for idx, name in C.COUNTERS.items():
        out["c_" + name.lower()] = C.read(summ, idx)
    m = re.search(r"\bnonce=(\d+)\b.*?\bgeneration=(\d+)\b", summ)
    out["session_nonce"] = int(m.group(1)) if m else None
    out["generation"] = int(m.group(2)) if m else None
    try:
        out["ring_lines"] = len(Path(ring_path).read_text(errors="replace").splitlines())
    except OSError:
        out["ring_lines"] = None
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--serial", required=True)
    ap.add_argument("--package", default="com.waydefu.x11gpu")
    ap.add_argument("--x-pid", type=int)
    ap.add_argument("--act-pid", type=int)
    ap.add_argument("--summary",
                    default="/data/data/com.termux/files/usr/tmp/gatea-summary.txt")
    ap.add_argument("--ring",
                    default="/data/data/com.termux/files/usr/tmp/gatea-ring.txt")
    ap.add_argument("--out")
    a = ap.parse_args()

    sample = {
        "tag": a.tag,
        "x": x_sample(a.x_pid),
        "activity": act_sample(a.serial, a.package, a.act_pid),
        "gatea": gatea_sample(a.summary, a.ring),
    }
    text = json.dumps(sample, indent=2) + "\n"
    if a.out:
        Path(a.out).write_text(text)
    print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
