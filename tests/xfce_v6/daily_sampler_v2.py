#!/usr/bin/env python3
"""Part A sampler for XFCE-V6-BASELINE-01 (tool T5; freeze section 6, amended by section 9).
Derived from tests/pga/daily_sampler.py v1 (0d74fe2, never run). The user runs it from a terminal
on the DAILY desktop, so it shares the tracer of the daily apps.

    daily_sampler_v2.py --kind v6|stock --out DIR --serial HOST:PORT
        [--baseline 60] [--loaded 240] [--launch CMD ...]
    (--test-mode --display :99: host self-test without adb; the judge rejects such captures)

Refuses (exit 3, nothing created) unless: DIR is new; this process is traced by the tracer the
kind requires (v6 = proot-fast6 without off switches, stock = $PREFIX/bin/proot); Claude Desktop
is running; Cursor, Hermes and any other Electron app are not; MemAvailable >= 4500 MB.
Then: screen + touch recorder (adb) -> the untraced probe (TermuxService), the traced probe and
traced_lat on the display -> baseline -> launches the daily launchers (default cursor-gpu,
hermes-gpu), launch_time = the first launch -> loaded -> stops. Every second: MemAvailable, swap,
cores per group (the tracer found by TracerPid, not by name: v1 matched comm 'proot', which misses
proot-fast6). Stable :1 is only read: two X round-trip probes and /proc via adb. The launched apps
are left running; the user closes them.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tracer_ident  # noqa: E402
import v6_probes as P  # noqa: E402

TADB = "/data/data/com.termux/files/usr/bin/adb"
V6_SHA = "2d5596dce6ae3a978658a66902cf121a34b8062480958b5750caba61feda2eab"
STOCK_SHA = "ea47e17da8e6ff4882c169c6508861e5b4be9227e477c6020f4f14facc85c10d"
OFF = {"PROOT_STAT_AT_ENTER": "0", "PROOT_KOMPAT_FULL": "1", "PROOT_BWRAP_COMPAT": "1"}
TOUCH_DEV = "/dev/input/event7"
HZ = os.sysconf("SC_CLK_TCK")
ELECTRON = {  # main-process markers (cmdline substring) -> name
    "/usr/lib/claude-desktop/claude-desktop": "claude",
    "/usr/share/cursor/cursor": "cursor",
    "linux-arm64-unpacked/Hermes": "hermes",
    "/ChatGPT": "chatgpt",
}


def adb_cmd(serial):
    return ["env", "-u", "ADB_SERVER_SOCKET", "-u", "ANDROID_ADB_SERVER_ADDRESS", "-u", "ANDROID_ADB_SERVER_PORT",
            "HOME=/data/data/com.termux/files/home", "ANDROID_NO_USE_FWMARK_CLIENT=1",
            TADB, "-H", "127.0.0.1", "-P", "5038", "-s", serial]


def adb(serial, *args, timeout=10) -> str:
    try:
        return subprocess.run(adb_cmd(serial) + list(args), capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL).stdout
    except (subprocess.TimeoutExpired, OSError):
        return ""


def screen(serial) -> dict:
    pw = adb(serial, "shell", "dumpsys power")
    wn = adb(serial, "shell", "dumpsys window")
    w = next((ln.split("mWakefulness=")[1].strip() for ln in pw.splitlines() if "mWakefulness=" in ln), "")
    k = next((ln.split("isKeyguardShowing=")[1].strip() for ln in wn.splitlines() if "isKeyguardShowing=" in ln), "")
    return {"wakefulness": w or None, "awake": w == "Awake", "keyguard": None if k == "" else k.startswith("true")}


def main_processes(proc: str = "/proc") -> dict:
    found = {}
    for p in os.listdir(proc):
        if not p.isdigit():
            continue
        try:
            text = P.cmdline_text(open(f"{proc}/{p}/cmdline", "rb").read())
        except OSError:
            continue
        if not text or "--type=" in text:   # renderer / gpu / zygote helpers, not a main process
            continue
        exe = text.split(" ", 1)[0]          # argv[0] may hold the whole command line (see cmdline_text)
        for mark, name in ELECTRON.items():
            if exe.endswith(mark):
                found.setdefault(name, []).append(int(p))
    return found


def mem() -> dict:
    d = {ln.split(":")[0]: int(ln.split()[1]) // 1024 for ln in open("/proc/meminfo")
         if ln.startswith(("MemAvailable", "SwapFree", "SwapTotal"))}
    return {"mem_available_mb": d.get("MemAvailable"), "swap_used_mb": d.get("SwapTotal", 0) - d.get("SwapFree", 0)}


def ticks(pid) -> int | None:
    try:
        st = open(f"/proc/{pid}/stat").read()
        r = st[st.rindex(")") + 2:].split()
        return int(r[11]) + int(r[12])
    except (OSError, ValueError):
        return None


def x1_pid(proc: str = "/proc"):
    return P.find_pid(P.X1_PREFIX, proc)


def refuse(why: str) -> int:
    print(f"DAILY_SAMPLER_REFUSED {why}")
    return 3


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", required=True, choices=("v6", "stock"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--serial")
    ap.add_argument("--display", default=":1")
    ap.add_argument("--baseline", type=float, default=60)
    ap.add_argument("--loaded", type=float, default=240)
    ap.add_argument("--launch", action="append")
    ap.add_argument("--test-mode", action="store_true")
    a = ap.parse_args()
    launch = a.launch or ["/root/.local/bin/cursor-gpu", "/root/.local/bin/hermes-gpu"]
    out = Path(a.out)
    if out.exists():
        return refuse(f"out_exists {out}")
    if not a.test_mode and not a.serial:
        return refuse("serial")
    # ---- preconditions, before anything is created or launched
    ct = tracer_ident.ident(Path("/proc"), os.getpid())
    want = V6_SHA if a.kind == "v6" else STOCK_SHA
    if not ct.get("tracer_pid") or ct.get("tracer_sha256") != want:
        return refuse(f"tracer {ct.get('tracer_comm')} {str(ct.get('tracer_sha256'))[:8]} is not {a.kind}")
    if a.kind == "v6" and any((ct.get("proot_env") or {}).get(k) == v for k, v in OFF.items()):
        return refuse(f"v6 off switch set {ct.get('proot_env')}")
    mp = main_processes()
    m0 = mem()
    pre = {"claude_running": bool(mp.get("claude")), "cursor_running": bool(mp.get("cursor")),
           "hermes_running": bool(mp.get("hermes")),
           "electron_others": sorted(k for k in mp if k not in ("claude", "cursor", "hermes")),
           "main_processes": mp, **m0}
    if not a.test_mode:
        if pre["cursor_running"] or pre["hermes_running"]:
            return refuse(f"close Cursor and Hermes first {mp}")
        if not pre["claude_running"]:
            return refuse("Claude Desktop must be open (idle)")
        if pre["electron_others"]:
            return refuse(f"other Electron apps running {pre['electron_others']}")
        if (m0["mem_available_mb"] or 0) < 4500:
            return refuse(f"MemAvailable {m0['mem_available_mb']} < 4500")
    out.mkdir(parents=True)
    (out / "binding.json").write_text(json.dumps({"kind": a.kind, "client_tracer": ct, "test_mode": a.test_mode,
                                                  "display": a.display, "launch": launch}, indent=2) + "\n")
    (out / "precheck.json").write_text(json.dumps(pre, indent=2) + "\n")
    P.write_tools(out)
    notes = open(out / "sampler.log", "w")
    # ---- screen + touch (adb)
    rec = None
    if not a.test_mode:
        (out / "screen-pre.json").write_text(json.dumps(screen(a.serial)))
        rec = subprocess.Popen(adb_cmd(a.serial) + ["shell", "getevent", TOUCH_DEV], stdin=subprocess.DEVNULL,
                               stdout=open(out / "touch-getevent.txt", "w"), stderr=subprocess.STDOUT)
        time.sleep(2)
        text = (out / "touch-getevent.txt").read_text(errors="replace").lower()
        selftest = rec.poll() is None and not any(w in text for w in ("could not", "denied", "error", "no such"))
    else:
        selftest = False
    # ---- probes
    total = a.baseline + a.loaded
    deadline = time.time() + total + 10
    ut = P.Untraced(a.display, f"daily-{os.getpid()}-{int(time.time())}")
    ut_ok = ut.start(deadline)
    traced = subprocess.Popen([P.TRACED_BIN, a.display, "250", str(int(total + 10))],
                              stdout=open(out / "traced-rtt.txt", "w"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    tl = subprocess.Popen([P.TL_BIN, "100", str(int(total + 10))], stdout=open(out / "traced-lat.txt", "w"),
                          stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    notes.write(f"untraced_started={ut_ok} traced_pid={traced.pid} traced_lat_pid={tl.pid}\n")
    # ---- baseline -> launch -> loaded, one sample per second
    tracer, x1 = ct["tracer_pid"], x1_pid()
    samples = open(out / "samples.jsonl", "w")
    t_start = time.time()
    phases = {"baseline_start": t_start, "launch_time": None, "loaded_end": None}
    prev = {"tracer": ticks(tracer), "x1": ticks(x1) if x1 else None, "t": time.monotonic()}
    reason = "completed"
    while True:
        time.sleep(1.0)
        el = time.time() - t_start
        if phases["launch_time"] is None and el >= a.baseline:
            phases["launch_time"] = time.time()
            for c in launch:
                subprocess.Popen(["setsid", "nohup", "sh", "-c", f"DISPLAY={a.display} exec {c}"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            notes.write(f"launched {launch} at {phases['launch_time']:.3f}\n")
        now = {"tracer": ticks(tracer), "x1": ticks(x1) if x1 else None, "t": time.monotonic()}
        dt = now["t"] - prev["t"]
        cores = {k: (round((now[k] - prev[k]) / HZ / dt, 3) if None not in (now[k], prev[k]) else None)
                 for k in ("tracer", "x1")}
        prev = now
        samples.write(json.dumps({"epoch": time.time(), "phase": "loaded" if phases["launch_time"] else "baseline",
                                  "cores": cores, **mem()}) + "\n")
        samples.flush()
        if (mem()["mem_available_mb"] or 0) < 1500:
            reason = "stopped_low_memory"
            break
        if el >= total:
            break
    phases["loaded_end"] = time.time()
    (out / "phases.json").write_text(json.dumps(phases, indent=2) + "\n")
    # ---- stop own children (exact pids), collect
    for name, pr in (("traced", traced), ("traced_lat", tl)):
        if pr.poll() is None:
            pr.terminate()
            notes.write(f"{name}_sigterm pid={pr.pid} (own child)\n")
        pr.wait()
    events = None
    if rec is not None:
        alive = rec.poll() is None
        rec.terminate()
        rec.wait()
        notes.write(f"touch_recorder_stopped pid={rec.pid}\n")
        n = sum(1 for ln in (out / "touch-getevent.txt").read_text(errors="replace").splitlines()
                if ln.startswith(("0001 ", "0003 ")))
        events = n if alive else None
        (out / "screen-post.json").write_text(json.dumps(screen(a.serial)))
    (out / "touch.json").write_text(json.dumps({"selftest": selftest, "events": events}) + "\n")
    ut.collect(out / "untraced-rtt.txt")
    for n in ut.notes:
        notes.write(n + "\n")
    rc = 0 if reason == "completed" and ut_ok else 5
    (out / "sampler-exit.txt").write_text(f"rc={rc}\n")
    notes.write(f"reason={reason}\n")
    notes.close()
    print(f"DAILY_SAMPLER_DONE kind={a.kind} reason={reason} out={out}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
