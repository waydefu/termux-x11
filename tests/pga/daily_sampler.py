#!/usr/bin/env python3
"""Phase-0 instrument: where does the CPU go on the user's DAILY desktop (:1)? Read-only.

    daily_sampler.py --out DIR --serial S --x-rtt BIN [--baseline 60] [--loaded 240]
                     [--launch "cmd1" --launch "cmd2"]

Nothing of Stable is changed: it reads /proc counters, one GetInputFocus round trip every
250 ms on :1 (x_rtt), the Stable Activity's /proc/<pid>/stat via adb, and Stable's own
'frames in 5.0 seconds' logcat line (tag LorieNative only). After --baseline seconds it
starts each --launch command (the user asked for Cursor + Hermes: "open both and it
starts to stutter"), detached, on DISPLAY=:1, and samples --loaded seconds more.
Every second: busy cores (/proc/stat), CPU cores per group, MemAvailable. Groups:
  x1          termux-x11 com.termux.x11 :1        (the daily X server)
  proot       every process named proot            (the ptrace tracer serving the rootfs)
  claude      /usr/lib/claude-desktop/*            cursor  cmdline has 'cursor'
  hermes      cmdline has 'Hermes' or 'hermes'     sampler  this process + x_rtt
  other       everything else visible here
Stops early (records why) if MemAvailable < 1500 MB. Launched apps are left running.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import time
from pathlib import Path

HZ = os.sysconf("SC_CLK_TCK")
TADB = "/data/data/com.termux/files/usr/bin/adb"


def adb(serial, *args, timeout=10):
    env = {k: v for k, v in os.environ.items()
           if k not in ("ADB_SERVER_SOCKET", "ANDROID_ADB_SERVER_ADDRESS", "ANDROID_ADB_SERVER_PORT")}
    env.update(HOME="/data/data/com.termux/files/home", ANDROID_NO_USE_FWMARK_CLIENT="1")
    try:
        return subprocess.run([TADB, "-H", "127.0.0.1", "-P", "5038", "-s", serial, *args],
                              capture_output=True, text=True, timeout=timeout, env=env,
                              stdin=subprocess.DEVNULL).stdout
    except (subprocess.TimeoutExpired, OSError):
        return ""


def busy_ticks(serial):
    """The PHONE's /proc/stat via adb: PRoot's own /proc/stat is synthetic (it barely moves)."""
    line = adb(serial, "shell", "head -1 /proc/stat", timeout=5).split()
    if len(line) < 5 or line[0] != "cpu":
        return None, None
    v = [int(x) for x in line[1:]]
    idle = v[3] + v[4]
    return sum(v) - idle, sum(v)


def group_of(cmd: str, comm: str, pid: int, mine: set) -> str:
    if pid in mine:
        return "sampler"
    if cmd.startswith("termux-x11 com.termux.x11 :1"):
        return "x1"
    if comm == "proot":
        return "proot"
    if "/claude-desktop" in cmd:
        return "claude"
    if "cursor" in cmd.lower():
        return "cursor"
    if "hermes" in cmd.lower():
        return "hermes"
    return "other"


def proc_ticks(mine: set):
    out, top = {}, {}
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            st = open(f"/proc/{p}/stat").read()
            rest = st[st.rindex(")") + 2:].split()
            t = int(rest[11]) + int(rest[12])
            comm = st[st.index("(") + 1:st.rindex(")")]
            cmd = open(f"/proc/{p}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")
        except (OSError, ValueError):
            continue
        g = group_of(cmd, comm, int(p), mine)
        out[g] = out.get(g, 0) + t
        top[int(p)] = (t, g, cmd[:80] or comm)
    return out, top


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--serial", required=True)
    ap.add_argument("--x-rtt", required=True)
    ap.add_argument("--baseline", type=float, default=60)
    ap.add_argument("--loaded", type=float, default=240)
    ap.add_argument("--launch", action="append", default=[])
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    total = a.baseline + a.loaded
    rtt = subprocess.Popen([a.x_rtt, ":1", "250", str(int(total) + 5)], stdout=open(out / "rtt.txt", "w"),
                           stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    lc = subprocess.Popen(["env", "HOME=/data/data/com.termux/files/home", TADB, "-H", "127.0.0.1", "-P", "5038", "-s", a.serial, "logcat", "-v", "threadtime",
                           "-T", "1", "LorieNative:I", "*:S"], stdout=open(out / "lorienative.txt", "w"),
                          stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    mine = {os.getpid(), rtt.pid, lc.pid}
    act = adb(a.serial, "shell", "pidof", "com.termux.x11").split()
    act = act[0] if act else None
    (out / "meta.json").write_text(json.dumps({"stable_activity_pid": act, "hz": HZ, "launch": a.launch,
                                               "baseline_s": a.baseline, "loaded_s": a.loaded,
                                               "start_epoch": time.time()}))
    f = open(out / "samples.jsonl", "w")
    b0, t0 = busy_ticks(a.serial)
    g0, top0 = proc_ticks(mine)
    first_top = dict(top0)
    a0 = None
    t_start = time.monotonic()
    m_prev = t_start
    launched = False
    reason = "completed"
    while True:
        time.sleep(1.0)
        el = time.monotonic() - t_start
        if not launched and el >= a.baseline:
            for c in a.launch:
                subprocess.Popen(["setsid", "nohup", "sh", "-c", f"DISPLAY=:1 exec {c}"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            launched = True
            f.write(json.dumps({"event": "launch", "t": round(el, 2), "cmds": a.launch}) + "\n")
        b1, t1 = busy_ticks(a.serial)
        g1, top1 = proc_ticks(mine)
        m_now = time.monotonic()
        dt = m_now - m_prev
        m_prev = m_now
        cores = {g: round((g1.get(g, 0) - g0.get(g, 0)) / HZ / dt, 3) for g in set(g0) | set(g1)}
        ast = adb(a.serial, "shell", "cat", f"/proc/{act}/stat", timeout=5) if act and int(el) % 5 == 0 else ""
        act_t = None
        if ast and ")" in ast:
            r = ast[ast.rindex(")") + 2:].split()
            act_t = int(r[11]) + int(r[12])
        mem = {ln.split(":")[0]: int(ln.split()[1]) for ln in open("/proc/meminfo")
               if ln.startswith(("MemAvailable", "SwapFree"))}
        rec = {"t": round(el, 2), "phase": "loaded" if launched else "baseline",
               "busy_cores": (round((b1 - b0) / (t1 - t0) * 8, 3)
                              if None not in (b0, b1, t0, t1) and t1 > t0 else None),   # 8 cores on the phone
               "cores": cores, "mem_available_mb": mem["MemAvailable"] // 1024,
               "swap_free_mb": mem["SwapFree"] // 1024, "stable_activity_ticks": act_t}
        f.write(json.dumps(rec) + "\n")
        f.flush()
        b0, t0, g0, top0 = b1, t1, g1, top1
        if rec["mem_available_mb"] < 1500:
            reason = "stopped_low_memory"
            break
        if el >= total:
            break
    # per-process CPU over the whole run (top consumers)
    per = []
    for pid, (t, g, c) in top0.items():
        per.append((t - first_top.get(pid, (0,))[0], pid, g, c))
    per.sort(reverse=True)
    (out / "top-processes.json").write_text(json.dumps(
        [{"cpu_s": round(d / HZ, 2), "pid": pid, "group": g, "cmd": c} for d, pid, g, c in per[:30]], indent=1))
    rtt.terminate()
    lc.terminate()
    (out / "end.json").write_text(json.dumps({"reason": reason, "end_epoch": time.time()}))
    print("DAILY_SAMPLER", reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
