#!/usr/bin/env python3
"""RCA instrument for XFCE runs (NOT a judge, NOT a fixture). Runs beside a runner.

    rca_sampler.py --out DIR --x-rtt /path/x_rtt [--duration 600]

Waits for the experimental X (:3), then until it exits:
  every 100 ms  state letter + wchan of every X thread       -> threads.jsonl (aggregated)
  every 250 ms  one X round trip on a persistent connection  -> rtt.txt (x_rtt)
  every 3 s     the exact lookup choreo.py performs:
                xdotool search --onlyvisible --name ^XFCE-T1$ -> search.jsonl
  every 5 s     utime+stime of every process whose environ carries XFCE_RUN_ID,
                and of X                                     -> cpu.jsonl
Writes summary.json at the end. It observes; it changes nothing it measures except
adding one X client (x_rtt) and one search every 3 s, both recorded here.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import subprocess
import time
from pathlib import Path


def x3pid():
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            cmd = (p / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if cmd.startswith("termux-x11gpu com.waydefu.x11gpu :3"):
            return int(p.name)
    return None


def thread_states(pid):
    out = {}
    try:
        tids = os.listdir(f"/proc/{pid}/task")
    except OSError:
        return None
    for t in tids:
        try:
            st = Path(f"/proc/{pid}/task/{t}/stat").read_text()
            comm = st[st.index("(") + 1:st.rindex(")")]
            state = st[st.rindex(")") + 2]
            wchan = Path(f"/proc/{pid}/task/{t}/wchan").read_text().strip() or "0"
        except OSError:
            continue
        out[int(t)] = (comm, state, wchan)
    return out


def ours_cpu(run_id):
    res = {}
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            env = (p / "environ").read_bytes()
        except OSError:
            continue
        if run_id and f"XFCE_RUN_ID={run_id}".encode() in env.split(b"\0"):
            try:
                st = (p / "stat").read_text()
                rest = st[st.rindex(")") + 2:].split()
                res[f"{p.name}:{(p / 'comm').read_text().strip()}"] = int(rest[11]) + int(rest[12])
            except OSError:
                pass
    return res


def find_run_id():
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            env = (p / "environ").read_bytes().split(b"\0")
        except OSError:
            continue
        for e in env:
            if e.startswith(b"XFCE_RUN_ID="):
                return e.split(b"=", 1)[1].decode()
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--x-rtt", required=True)
    ap.add_argument("--duration", type=float, default=900)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    t_end = time.time() + a.duration
    pid = None
    while pid is None and time.time() < t_end:
        pid = x3pid()
        time.sleep(0.2)
    if pid is None:
        (out / "summary.json").write_text(json.dumps({"x3": None}))
        return 1
    # wait until X accepts connections, then keep ONE connection for the RTT probe
    rtt = None
    for _ in range(200):
        if Path("/tmp/.X11-unix/X3").exists():
            break
        time.sleep(0.2)
    # The socket file can exist before X accepts (or be a stale one): x_rtt then prints
    # RTT_CONNECT_FAIL and exits, and xfce-c0-gat-01 (709dfac) lost its whole RTT series that way.
    # Retry until the connection holds; every attempt stays in rtt.txt.
    rtt_f = open(out / "rtt.txt", "w")
    for attempt in range(60):
        rtt = subprocess.Popen([a.x_rtt, ":3", "250", str(int(a.duration))],
                               stdout=rtt_f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        time.sleep(1.0)
        if rtt.poll() is None:
            break
        rtt_f.write(f"RTT_RETRY attempt={attempt + 1} rc={rtt.returncode}\n")
        rtt_f.flush()
        rtt = None
        time.sleep(0.5)
    counts = collections.defaultdict(collections.Counter)   # tid -> Counter(state:wchan)
    comms = {}
    search_f = open(out / "search.jsonl", "w")
    cpu_f = open(out / "cpu.jsonl", "w")
    last_search = last_cpu = 0.0
    run_id = None
    samples = 0
    env = dict(os.environ, DISPLAY=":3")
    while time.time() < t_end and Path(f"/proc/{pid}").exists():
        ts = thread_states(pid)
        if ts is None:
            break
        samples += 1
        for tid, (comm, state, wchan) in ts.items():
            comms[tid] = comm
            counts[tid][f"{state}:{wchan}"] += 1
        now = time.time()
        if now - last_search >= 3.0:
            last_search = now
            t0 = time.monotonic()
            try:
                r = subprocess.run(["xdotool", "search", "--onlyvisible", "--name", "^XFCE-T1$"],
                                   capture_output=True, text=True, timeout=30, env=env,
                                   stdin=subprocess.DEVNULL)
                rc, n = r.returncode, len([x for x in r.stdout.split() if x.isdigit()])
            except subprocess.TimeoutExpired:
                rc, n = 124, 0
            search_f.write(json.dumps({"epoch": now, "dur_s": round(time.monotonic() - t0, 4),
                                       "rc": rc, "found": n}) + "\n")
            search_f.flush()
        if now - last_cpu >= 5.0:
            last_cpu = now
            run_id = run_id or find_run_id()
            c = ours_cpu(run_id)
            try:
                st = Path(f"/proc/{pid}/stat").read_text()
                rest = st[st.rindex(")") + 2:].split()
                c[f"{pid}:X3"] = int(rest[11]) + int(rest[12])
            except OSError:
                pass
            # the PRoot tracer that serves every process in this rootfs (ours included)
            try:
                tp = int(re.search(r"TracerPid:\s+(\d+)", Path("/proc/self/status").read_text()).group(1))
                if tp:
                    st = Path(f"/proc/{tp}/stat").read_text()
                    rest = st[st.rindex(")") + 2:].split()
                    c[f"{tp}:proot-tracer"] = int(rest[11]) + int(rest[12])
            except (OSError, AttributeError, ValueError):
                pass
            cpu_f.write(json.dumps({"epoch": now, "run_id": run_id, "ticks": c}) + "\n")
            cpu_f.flush()
        time.sleep(0.1)
    if rtt:
        rtt.terminate()
    summ = {"x3": pid, "samples": samples, "threads": {}}
    for tid, c in counts.items():
        tot = sum(c.values())
        summ["threads"][str(tid)] = {"comm": comms.get(tid), "samples": tot,
                                     "top": [(k, round(v / tot, 4)) for k, v in c.most_common(6)]}
    (out / "summary.json").write_text(json.dumps(summ, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
