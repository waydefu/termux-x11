#!/usr/bin/env python3
"""XFCE-FREEZE-V2 choreography driver. Executes the frozen schedule, records, judges nothing.

    choreo.py --freeze xfce-design-freeze.json --state-dir DIR --log steps.jsonl \
              --term-load term_load.sh

The schedule comes ONLY from the freeze file (p04). Every step is written as one JSON
line with its planned offset, the monotonic and wall-clock instants it actually ran,
its lateness, its return code and what it touched. The judge decides validity from
that log; this script never decides anything except "keep going".

Clock domains (measured 2026-09-23, p2-xfce-probe):
  mono_ns   time.monotonic_ns() == CLOCK_MONOTONIC, the same clock SurfaceFlinger
            --latency reports (same kernel, PRoot does not virtualise it)
  epoch_s   time.time(); PRoot and device share the timezone, so this maps onto
            logcat's local timestamps directly

A failed step does NOT stop the run: the session still has to reach its clean close,
and a run that aborted halfway would leave windows the logout then has to tear down.
Exit 0 iff every step succeeded; the judge reads the log either way.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

XDOTOOL = "xdotool"
TERMINAL = "xfce4-terminal"
# OFFLINE-TEST HOOKS ONLY. The runner launches choreo with a scrubbed environment that
# never carries these, and the T0 record logs all three, so the judge refuses any run
# where one was active (timescale != 1 or a non-default binary).
if os.environ.get("XFCE_CHOREO_TEST_XDOTOOL"):
    XDOTOOL = os.environ["XFCE_CHOREO_TEST_XDOTOOL"]
if os.environ.get("XFCE_CHOREO_TEST_TERMINAL"):
    TERMINAL = os.environ["XFCE_CHOREO_TEST_TERMINAL"]
TIMESCALE = float(os.environ.get("XFCE_CHOREO_TEST_TIMESCALE", "1"))


def now():
    return time.monotonic_ns(), time.time()


def xdo(*args: str, timeout: float = 5.0) -> tuple[int, str]:
    try:
        r = subprocess.run([XDOTOOL, *args], capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def find_window(title: str, timeout: float) -> tuple[list[str], float]:
    """All visible windows whose name is exactly `title`, polled until found."""
    t_end = time.monotonic() + timeout
    t_start = time.monotonic()
    while True:
        rc, out = xdo("search", "--onlyvisible", "--name", f"^{title}$", timeout=3.0)
        ids = [w for w in out.split() if w.isdigit()] if rc == 0 else []
        if ids or time.monotonic() >= t_end:
            return ids, time.monotonic() - t_start
        time.sleep(0.1)


class Driver:
    def __init__(self, freeze: dict, state_dir: Path, log, term_load: str):
        self.f = freeze
        self.sd = state_dir
        self.log = log
        self.term_load = term_load
        self.win = freeze["p04_window_choreography"]["windows"]
        self.lookup_timeout = freeze["p04_window_choreography"]["window_lookup_timeout_s"]
        self.close_timeout = freeze["p04_window_choreography"]["close_exit_timeout_s"]
        self.procs: dict[str, subprocess.Popen] = {}
        self.wids: dict[str, str] = {}
        self.opens: dict[str, int] = {}
        self.ok = True

    def emit(self, rec: dict):
        self.log.write(json.dumps(rec, sort_keys=True) + "\n")
        self.log.flush()
        if not rec.get("ok", True):
            self.ok = False

    def op_open(self, w: str) -> dict:
        n = w[1:]
        k = self.opens.get(w, 0) + 1
        self.opens[w] = k
        flag = self.sd / f"close-{w}"
        if flag.exists():
            return {"ok": False, "why": "stale_close_flag"}
        argv = [TERMINAL, "--disable-server", "--hide-menubar", "--hide-toolbar",
                "--dynamic-title-mode=none", f"--title=XFCE-{w}",
                f"--geometry={self.win[w]['geometry']}",
                "-x", "/bin/bash", self.term_load, n, str(self.sd)]
        out = open(self.sd / f"term-{w}-{k}.log", "w")
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                             start_new_session=False)
        self.procs[w] = p
        ids, waited = find_window(f"XFCE-{w}", self.lookup_timeout)
        if len(ids) != 1:
            return {"ok": False, "why": "window_lookup", "found": ids, "pid": p.pid,
                    "waited_s": round(waited, 3), "argv": argv}
        self.wids[w] = ids[0]
        return {"ok": True, "pid": p.pid, "wid": ids[0], "open_n": k,
                "map_wait_s": round(waited, 3), "argv": argv}

    def op_close(self, w: str) -> dict:
        p = self.procs.get(w)
        if p is None:
            return {"ok": False, "why": "not_open"}
        flag = self.sd / f"close-{w}"
        flag.touch()
        try:
            rc = p.wait(timeout=self.close_timeout)
        except subprocess.TimeoutExpired:
            return {"ok": False, "why": "close_exit_timeout", "pid": p.pid}
        flag.unlink()
        ids, _ = find_window(f"XFCE-{w}", 0.0)
        gone_deadline = time.monotonic() + 5.0
        while ids and time.monotonic() < gone_deadline:
            time.sleep(0.1)
            ids, _ = find_window(f"XFCE-{w}", 0.0)
        del self.procs[w]
        self.wids.pop(w, None)
        return {"ok": rc == 0 and not ids, "pid": p.pid, "rc": rc,
                "window_still_mapped": ids}

    def op_xdo(self, w: str, *args: str) -> dict:
        wid = self.wids.get(w)
        if wid is None:
            return {"ok": False, "why": "no_window"}
        rc, out = xdo(*args[:1], wid, *args[1:])
        return {"ok": rc == 0, "rc": rc, "wid": wid, "out": out[:200]}

    def run(self) -> int:
        sched = self.f["p04_window_choreography"]["schedule"]
        t0_mono, t0_epoch = now()
        self.emit({"step": -1, "op": "T0", "t0_mono_ns": t0_mono, "t0_epoch_s": t0_epoch,
                   "pid": os.getpid(), "xdotool": XDOTOOL, "terminal": TERMINAL,
                   "timescale": TIMESCALE, "ok": True})
        for i, st in enumerate(sched):
            target = t0_mono + int(st["t"] * TIMESCALE * 1e9)
            while True:
                d = target - time.monotonic_ns()
                if d <= 0:
                    break
                time.sleep(min(d / 1e9, 0.5))
            m, e = now()
            op = st["op"]
            if op == "open":
                res = self.op_open(st["win"])
            elif op == "close":
                res = self.op_close(st["win"])
            elif op == "activate":
                res = self.op_xdo(st["win"], "windowactivate")
            elif op == "move":
                res = self.op_xdo(st["win"], "windowmove", str(st["x"]), str(st["y"]))
            elif op == "resize":
                res = self.op_xdo(st["win"], "windowsize", str(st["w"]), str(st["h"]))
            elif op == "end":
                res = {"ok": not self.procs, "still_open": sorted(self.procs)}
            else:
                res = {"ok": False, "why": f"unknown_op {op}"}
            m2, _ = now()
            self.emit({"step": i, "op": op, "win": st.get("win"), "planned_t": st["t"],
                       "actual_t": round((m - t0_mono) / 1e9 / TIMESCALE, 4),
                       "lateness_s": round((m - target) / 1e9, 4),
                       "duration_s": round((m2 - m) / 1e9, 4),
                       "mono_ns": m, "epoch_s": e, **res})
        return 0 if self.ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--freeze", required=True)
    ap.add_argument("--state-dir", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--term-load", required=True)
    a = ap.parse_args()
    freeze = json.loads(Path(a.freeze).read_text())
    if freeze.get("status") != "XFCE_DESIGN_FROZEN_V2":
        print("choreo: freeze status mismatch", file=sys.stderr)
        return 3
    sd = Path(a.state_dir)
    sd.mkdir(parents=True, exist_ok=True)
    with open(a.log, "w") as log:
        return Driver(freeze, sd, log, a.term_load).run()


if __name__ == "__main__":
    sys.exit(main())
