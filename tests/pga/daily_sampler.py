#!/usr/bin/env python3
"""Phase-0 instrument v2: is the PRoot tracer what makes the DAILY desktop (:1) stutter?

    daily_sampler.py --out DIR --serial S --x-rtt BIN --traced-lat BIN
                     [--baseline 60] [--loaded 240] [--floor-mb 2000]
                     [--launch "cmd1" --launch "cmd2"] [--close-launched]

Stable is only observed. Everything is written straight into DIR (on disk, not /tmp, so a
Termux kill cannot take the evidence with it).

Why v2 (v1 = commit 0d74fe2): v1 walked PRoot's /proc every second and spawned one adb per
second. Inside PRoot every open() of a path is a trapped syscall, so the sampler itself
loaded the one tracer thread it was measuring. v2 moves process accounting to the PHONE
side (one long-lived `adb shell` loop; the phone shell is not traced) and keeps only
socket / already-open-fd work in PRoot:
  phone.txt     every 1 s: phone /proc/stat cpu line, tracer /proc/<pid>/stat and its
                voluntary_ctxt_switches (one wakeup per ptrace stop it serves), :1 X and
                the Stable Activity stat; every 5 s: /proc/[0-9]*/stat of EVERY process
  events.txt    logcat (events+main+system) kill / lmk / ANR / Stable frame lines, streamed
  rtt.txt       x_rtt :1 every 250 ms (X :1 is not traced; its own dispatch latency)
  tl.txt        traced_lat every 100 ms: fstatat (trapped -> waits for the tracer) vs
                getppid (not trapped: the CPU-contention control)
  host.jsonl    every 1 s: MemAvailable / SwapFree (meminfo kept open: no path lookup)
After --baseline seconds the --launch commands start (setsid, DISPLAY=:1). If MemAvailable
drops below --floor-mb the launched process groups get SIGTERM (construction, recorded in
end.json with pgid + cmd) and the run stops. --close-launched does the same at the end.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path

TADB = "/data/data/com.termux/files/usr/bin/adb"
X1_PREFIX = "termux-x11 com.termux.x11 :1"

PHONE_LOOP = r"""
T={tracer}; X={x1}; A={act}; N={n}; i=0
echo "HZ $(getconf CLK_TCK)"
while [ $i -lt $N ]; do
  echo "S $EPOCHREALTIME"
  head -1 /proc/stat
  cat /proc/$T/stat /proc/$X/stat /proc/$A/stat 2>/dev/null
  grep -E '^(non)?voluntary_ctxt_switches' /proc/$T/status
  if [ $((i % 5)) -eq 0 ]; then echo "FULL $EPOCHREALTIME"; cat /proc/[0-9]*/stat 2>/dev/null; echo ENDFULL; fi
  i=$((i+1)); sleep 1
done
echo PHONE_LOOP_END
"""

LOGCAT_FILTER = ("killinfo:I am_kill:I am_proc_died:I am_low_memory:I am_anr:I "
                 "lowmemorykiller:I lmkd:I LorieNative:I *:S")


def adb_argv(serial, *args):
    return ["env", "-u", "ADB_SERVER_SOCKET", "-u", "ANDROID_ADB_SERVER_ADDRESS", "-u",
            "ANDROID_ADB_SERVER_PORT", "HOME=/data/data/com.termux/files/home",
            "ANDROID_NO_USE_FWMARK_CLIENT=1", TADB, "-H", "127.0.0.1", "-P", "5038",
            "-s", serial, *args]


def adb(serial, *args, timeout=15):
    try:
        return subprocess.run(adb_argv(serial, *args), capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL).stdout
    except (subprocess.TimeoutExpired, OSError):
        return ""


def find_pid(pred):
    """One /proc walk at setup time only (not during sampling)."""
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            comm = open(f"/proc/{p}/comm").read().strip()
            cmd = open(f"/proc/{p}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if pred(comm, cmd):
            return int(p), cmd
    return None, None


def meminfo(fh):
    fh.seek(0)
    m = {}
    for ln in fh.read().splitlines():
        k, v = ln.split(":", 1)
        if k in ("MemAvailable", "SwapFree", "SwapTotal"):
            m[k] = int(v.split()[0]) // 1024
    return m


def stop_groups(launched, why):
    done = []
    for g in launched:
        try:
            os.killpg(g["pgid"], signal.SIGTERM)
            done.append({"signal": "SIGTERM", "pgid": g["pgid"], "cmd": g["cmd"], "why": why})
        except ProcessLookupError:
            done.append({"signal": None, "pgid": g["pgid"], "cmd": g["cmd"], "why": "already_gone"})
    return done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--serial", required=True)
    ap.add_argument("--x-rtt", required=True)
    ap.add_argument("--traced-lat", required=True)
    ap.add_argument("--baseline", type=float, default=60)
    ap.add_argument("--loaded", type=float, default=240)
    ap.add_argument("--floor-mb", type=int, default=2000)
    ap.add_argument("--launch", action="append", default=[])
    ap.add_argument("--close-launched", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=False)          # never write into an old run
    total = a.baseline + a.loaded

    tracer, tcmd = find_pid(lambda comm, cmd: comm == "proot")
    x1, _ = find_pid(lambda comm, cmd: cmd.startswith(X1_PREFIX))
    act = (adb(a.serial, "shell", "pidof", "com.termux.x11").split() or [None])[0]
    screen = adb(a.serial, "shell", "dumpsys power | grep -E 'mWakefulness='; "
                 "dumpsys window | grep -m1 isKeyguardShowing")
    meta = {"tracer_pid": tracer, "tracer_cmd": tcmd, "x1_pid": x1, "stable_activity_pid": act,
            "screen": screen.split(), "launch": a.launch, "baseline_s": a.baseline,
            "loaded_s": a.loaded, "floor_mb": a.floor_mb, "sampler_pid": os.getpid(),
            "start_epoch": time.time()}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    if not (tracer and x1 and act):
        (out / "end.json").write_text(json.dumps({"reason": "preflight_missing_pid", **meta}))
        print("DAILY_SAMPLER preflight_missing_pid")
        return 2

    n = int(total) + 10
    procs = {
        "phone": subprocess.Popen(adb_argv(a.serial, "shell", PHONE_LOOP.format(tracer=tracer, x1=x1, act=act, n=n)),
                                  stdout=open(out / "phone.txt", "w"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL),
        "events": subprocess.Popen(adb_argv(a.serial, "shell", f"timeout {n} logcat -b events -b main -b system "
                                            f"-v epoch -T 1 {LOGCAT_FILTER}"),
                                   stdout=open(out / "events.txt", "w"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL),
        "rtt": subprocess.Popen([a.x_rtt, ":1", "250", str(n)], stdout=open(out / "rtt.txt", "w"),
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL),
        "tl": subprocess.Popen([a.traced_lat, "100", str(n)], stdout=open(out / "tl.txt", "w"),
                               stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL),
    }
    meta["helper_pids"] = {k: p.pid for k, p in procs.items()}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))

    mfh = open("/proc/meminfo")
    f = open(out / "host.jsonl", "w")
    launched, construction = [], []
    reason = "completed"
    t0 = time.monotonic()
    while True:
        time.sleep(1.0)
        el = time.monotonic() - t0
        if a.launch and not launched and el >= a.baseline:
            for c in a.launch:
                p = subprocess.Popen(["setsid", "sh", "-c", f"DISPLAY=:1 exec {c}"],
                                     stdout=open(out / f"launch-{len(launched)}.log", "w"),
                                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
                launched.append({"pgid": p.pid, "cmd": c, "t": round(el, 2), "epoch": time.time()})
            f.write(json.dumps({"event": "launch", "t": round(el, 2), "epoch": time.time(),
                                "launched": launched}) + "\n")
        m = meminfo(mfh)
        f.write(json.dumps({"t": round(el, 2), "epoch": round(time.time(), 3),
                            "phase": "loaded" if launched else "baseline",
                            "mem_available_mb": m["MemAvailable"],
                            "swap_used_mb": m["SwapTotal"] - m["SwapFree"]}) + "\n")
        f.flush()
        if m["MemAvailable"] < a.floor_mb:
            reason = "stopped_low_memory"
            construction += stop_groups(launched, f"mem_available_mb={m['MemAvailable']}<{a.floor_mb}")
            break
        dead = [k for k, p in procs.items() if p.poll() is not None]
        if dead:
            reason = "helper_exited:" + ",".join(dead)
            break
        if el >= total:
            break
    if a.close_launched and reason == "completed":
        construction += stop_groups(launched, "close_launched_at_end")
    for p in procs.values():
        if p.poll() is None:
            p.terminate()
    for p in procs.values():
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
    (out / "end.json").write_text(json.dumps({"reason": reason, "end_epoch": time.time(),
                                              "launched": launched, "construction": construction}, indent=1))
    print("DAILY_SAMPLER", reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
