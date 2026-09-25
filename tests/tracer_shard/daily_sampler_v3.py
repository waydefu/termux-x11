#!/usr/bin/env python3
"""TRACER-SHARD-01 sampler (evidence/session/proot/TRACER-SHARD-01-FREEZE.md, sections 4-6).
The Part A protocol of tests/xfce_v6/daily_sampler_v2.py (its functions are imported, v2 itself is unchanged),
with the client tracer proot-fast7 and two launch groups:
    P  /root/.local/bin/cursor-gpu, /root/.local/bin/hermes-gpu            -> they share this shell's tracer
    S  the same two through tests/tracer_shard/f8-shard                     -> each gets a tracer of its own

    daily_sampler_v3.py --group P|S --out DIR --serial HOST:PORT [--baseline 60] [--loaded 240]
    (--test-mode --display :99: host self-test without adb; the judge rejects such captures)

Added to v2: tools-shard.json (sha256 of f8-shard, the installed shard-run.sh, this file, the judge);
per second the cores of every shard tracer; apps-end.json at the end of the loaded window, before anything is
stopped (per app: main pid, its TracerPid and that tracer's exe sha256, renderers = zygote children of a zygote
under that tracer, and for S the tracer recorded by f8-shard); closing: P exactly as v2 --close-launched,
S by SIGTERM to the recorded f8-shard pid (cmdline must contain /f8-shard ), which forwards it to the app.
Both are construction steps recorded in closed.json.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "xfce_v6"))
import daily_sampler_v2 as V2  # noqa: E402
import tracer_ident  # noqa: E402
import v6_probes as P  # noqa: E402

V7_SHA = "4f9d10ad2f78e1eef3907eea1ef761b4b8265c9d7cbd5779df48b4f8cb756058"
F8_SHARD = HERE / "f8-shard"
SHARD_RUN = Path("/data/data/com.termux/files/home/build/proot-fast/shard/shard-run.sh")
JOBS = "/data/data/com.termux/files/usr/tmp/f8-shard"
APPS = {"cursor": "/root/.local/bin/cursor-gpu", "hermes": "/root/.local/bin/hermes-gpu"}


def sha256(p) -> str | None:
    try:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    except OSError:
        return None


def status_field(pid, key) -> str | None:
    try:
        for ln in open(f"/proc/{pid}/status"):
            if ln.startswith(key + ":"):
                return ln.split()[1]
    except OSError:
        pass
    return None


def cmd(pid) -> str:
    try:
        return P.cmdline_text(open(f"/proc/{pid}/cmdline", "rb").read())
    except OSError:
        return ""


def job_pid(f8_pid: int, name: str) -> int | None:
    """a pid f8-shard recorded in its job dir <JOBS>/<date>-<f8-shard pid>/: tracer.pid or app.pid"""
    for j in glob.glob(f"{JOBS}/*-{f8_pid}"):
        try:
            return int(Path(j, name).read_text().split()[0])
        except (OSError, ValueError, IndexError):
            return None
    return None


def shard_tracer_of(f8_pid: int) -> int | None:
    return job_pid(f8_pid, "tracer.pid")


def apps_end(group: str, launched: list) -> dict:
    """freeze section 4 items 3-4, read once at the end of the loaded window."""
    procs = {}
    for p in os.listdir("/proc"):
        if p.isdigit():
            procs[int(p)] = (status_field(p, "TracerPid"), status_field(p, "PPid"), cmd(p))
    mains = V2.main_processes()
    marks = {v: k for k, v in V2.ELECTRON.items()}
    out = {}
    for name in APPS:
        # the main process is the one that was launched, not a guess from cmdlines: VS Code-type apps run
        # helpers from the same binary without --type= (ELECTRON_RUN_AS_NODE). P: the launched pid (sh execs
        # through the launcher into the app); S: app.pid in the f8-shard job dir (the guest bash execs likewise).
        item = next((x for x in launched if x["app"] == name), None)
        mp = None
        if item:
            mp = item["pid"] if group == "P" else job_pid(item["pid"], "app.pid")
        c = cmd(mp) if mp else ""
        ok = bool(c) and c.split(" ", 1)[0].endswith(marks[name])
        e = {"main_pids_by_cmdline": mains.get(name) or [], "launched_pid": mp,
             "launched_cmdline": c[:160], "main_pid": mp if ok else None}
        if e["main_pid"]:
            tp = procs.get(e["main_pid"], (None,))[0]
            e["tracer_pid"] = int(tp) if tp and tp.isdigit() else None
            e["tracer_sha256"] = sha256(f"/proc/{e['tracer_pid']}/exe") if e["tracer_pid"] else None
            ren = 0
            for pid, (t, pp, c) in procs.items():
                if t is None or not t.isdigit() or int(t) != e["tracer_pid"] or "--type=zygote" not in c:
                    continue
                if pp and pp.isdigit() and "--type=zygote" in procs.get(int(pp), (None, None, ""))[2]:
                    ren += 1
            e["renderers"] = ren
        if group == "S":
            e["f8_shard_pid"] = item["pid"] if item else None
            e["shard_tracer_pid"] = shard_tracer_of(item["pid"]) if item else None
        out[name] = e
    return out


def close_shard(out: Path, launched: list, procs: dict, notes) -> None:
    rec = []
    for item in launched:
        pid, pr = item["pid"], procs.get(item["pid"])
        if pr is not None and pr.poll() is not None:
            rec.append({**item, "cmdline": None, "action": "already_gone", "rc": pr.returncode})
            continue
        text = cmd(pid)
        if "/f8-shard " not in text + " ":
            rec.append({**item, "cmdline": text[:200], "action": "skipped_cmdline_mismatch"})
            continue
        os.kill(pid, 15)
        notes.write(f"close_shard sigterm f8-shard pid={pid} cmdline={text[:160]}\n")
        t0 = time.time()
        while time.time() - t0 < 20 and pr is not None and pr.poll() is None:
            time.sleep(0.5)
        gone = pr is not None and pr.poll() is not None
        rec.append({**item, "cmdline": text[:200], "action": "sigterm_f8_shard",
                    "gone_after_s": round(time.time() - t0, 1) if gone else None})
    (out / "closed.json").write_text(json.dumps({"closed": rec, "electron_left": V2.main_processes()}, indent=2) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", required=True, choices=("P", "S"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--serial")
    ap.add_argument("--display", default=":1")
    ap.add_argument("--baseline", type=float, default=60)
    ap.add_argument("--loaded", type=float, default=240)
    ap.add_argument("--test-mode", action="store_true")
    a = ap.parse_args()
    pre_cmd = [str(F8_SHARD) + " "] if a.group == "S" else [""]
    launch = [{"app": n, "cmd": pre_cmd[0] + c} for n, c in APPS.items()]
    out = Path(a.out)
    if out.exists():
        return V2.refuse(f"out_exists {out}")
    if not a.test_mode and not a.serial:
        return V2.refuse("serial")
    ct = tracer_ident.ident(Path("/proc"), os.getpid())
    if not ct.get("tracer_pid") or ct.get("tracer_sha256") != V7_SHA:
        return V2.refuse(f"tracer {ct.get('tracer_comm')} {str(ct.get('tracer_sha256'))[:8]} is not proot-fast7")
    if any((ct.get("proot_env") or {}).get(k) == v for k, v in V2.OFF.items()):
        return V2.refuse(f"off switch set {ct.get('proot_env')}")
    if a.group == "S" and not (os.access(F8_SHARD, os.X_OK) and SHARD_RUN.is_file()):
        return V2.refuse("f8-shard / shard-run.sh missing")
    mp = V2.main_processes()
    m0 = V2.mem()
    pre = {"claude_running": bool(mp.get("claude")), "cursor_running": bool(mp.get("cursor")),
           "hermes_running": bool(mp.get("hermes")),
           "electron_others": sorted(k for k in mp if k not in ("claude", "cursor", "hermes")),
           "main_processes": mp, **m0}
    if not a.test_mode:
        if pre["cursor_running"] or pre["hermes_running"]:
            return V2.refuse(f"close Cursor and Hermes first {mp}")
        if not pre["claude_running"]:
            return V2.refuse("Claude Desktop must be open")
        if pre["electron_others"]:
            return V2.refuse(f"other Electron apps running {pre['electron_others']}")
        if (m0["mem_available_mb"] or 0) < 4500:
            return V2.refuse(f"MemAvailable {m0['mem_available_mb']} < 4500")
    out.mkdir(parents=True)
    (out / "binding.json").write_text(json.dumps({"kind": "v7", "group": a.group, "client_tracer": ct,
                                                  "test_mode": a.test_mode, "display": a.display,
                                                  "launch": launch}, indent=2) + "\n")
    (out / "precheck.json").write_text(json.dumps(pre, indent=2) + "\n")
    (out / "tools-shard.json").write_text(json.dumps({
        "f8-shard": sha256(F8_SHARD), "shard-run.sh": sha256(SHARD_RUN),
        "daily_sampler_v3.py": sha256(__file__), "shard_judge.py": sha256(HERE / "shard_judge.py")}, indent=2) + "\n")
    P.write_tools(out)
    notes = open(out / "sampler.log", "w")
    rec = None
    if not a.test_mode:
        (out / "screen-pre.json").write_text(json.dumps(V2.screen(a.serial)))
        rec = subprocess.Popen(V2.adb_cmd(a.serial) + ["shell", "getevent", V2.TOUCH_DEV], stdin=subprocess.DEVNULL,
                               stdout=open(out / "touch-getevent.txt", "w"), stderr=subprocess.STDOUT)
        time.sleep(2)
        text = (out / "touch-getevent.txt").read_text(errors="replace").lower()
        selftest = rec.poll() is None and not any(w in text for w in ("could not", "denied", "error", "no such"))
    else:
        selftest = False
    total = a.baseline + a.loaded
    deadline = time.time() + total + 10
    ut = P.Untraced(a.display, f"shard-{os.getpid()}-{int(time.time())}")
    ut_ok = ut.start(deadline)
    traced = subprocess.Popen([P.TRACED_BIN, a.display, "250", str(int(total + 10))],
                              stdout=open(out / "traced-rtt.txt", "w"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    tl = subprocess.Popen([P.TL_BIN, "100", str(int(total + 10))], stdout=open(out / "traced-lat.txt", "w"),
                          stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    notes.write(f"untraced_started={ut_ok} traced_pid={traced.pid} traced_lat_pid={tl.pid}\n")
    tracer, x1 = ct["tracer_pid"], V2.x1_pid()
    samples = open(out / "samples.jsonl", "w")
    t_start = time.time()
    phases = {"baseline_start": t_start, "launch_time": None, "loaded_end": None}
    prev = {"tracer": V2.ticks(tracer), "x1": V2.ticks(x1) if x1 else None, "t": time.monotonic()}
    shard_prev: dict = {}
    reason = "completed"
    launched: list = []
    procs: dict = {}
    apps = None
    while True:
        time.sleep(1.0)
        el = time.time() - t_start
        if phases["launch_time"] is None and el >= a.baseline:
            phases["launch_time"] = time.time()
            for item in launch:
                lp = subprocess.Popen(["setsid", "nohup", "sh", "-c", f"DISPLAY={a.display} exec {item['cmd']}"],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
                launched.append({**item, "pid": lp.pid})
                procs[lp.pid] = lp
            (out / "launched.json").write_text(json.dumps(launched, indent=2) + "\n")
            notes.write(f"launched {[x['cmd'] for x in launch]} at {phases['launch_time']:.3f}\n")
        now = {"tracer": V2.ticks(tracer), "x1": V2.ticks(x1) if x1 else None, "t": time.monotonic()}
        dt = now["t"] - prev["t"]
        cores = {k: (round((now[k] - prev[k]) / V2.HZ / dt, 3) if None not in (now[k], prev[k]) else None)
                 for k in ("tracer", "x1")}
        prev = now
        shard = {}
        if a.group == "S":
            for item in launched:
                st = shard_tracer_of(item["pid"])
                if not st:
                    continue
                t = V2.ticks(st)
                if st in shard_prev and None not in (t, shard_prev[st]):
                    shard[item["app"]] = round((t - shard_prev[st]) / V2.HZ / dt, 3)
                shard_prev[st] = t
        samples.write(json.dumps({"epoch": time.time(), "phase": "loaded" if phases["launch_time"] else "baseline",
                                  "cores": {**cores, "shard": shard}, **V2.mem()}) + "\n")
        samples.flush()
        if (V2.mem()["mem_available_mb"] or 0) < 1500:
            reason = "stopped_low_memory"
            break
        if el >= total:
            apps = apps_end(a.group, launched)
            break
    phases["loaded_end"] = time.time()
    (out / "phases.json").write_text(json.dumps(phases, indent=2) + "\n")
    (out / "apps-end.json").write_text(json.dumps(apps, indent=2) + "\n")
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
        (out / "screen-post.json").write_text(json.dumps(V2.screen(a.serial)))
    (out / "touch.json").write_text(json.dumps({"selftest": selftest, "events": events}) + "\n")
    ut.collect(out / "untraced-rtt.txt")
    for n in ut.notes:
        notes.write(n + "\n")
    if a.group == "P":
        V2.close_launched(out, [{"cmd": x["cmd"], "pid": x["pid"]} for x in launched], procs, notes)
    else:
        close_shard(out, launched, procs, notes)
    rc = 0 if reason == "completed" and ut_ok else 5
    (out / "sampler-exit.txt").write_text(f"rc={rc}\n")
    notes.write(f"reason={reason}\n")
    notes.close()
    print(f"SHARD_SAMPLER_DONE group={a.group} reason={reason} out={out}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
