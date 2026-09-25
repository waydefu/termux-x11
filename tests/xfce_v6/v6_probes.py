#!/usr/bin/env python3
"""Companion probes for one XFCE-V6-BASELINE-01 capture (tool T4 helper; also imported by T5).

    v6_probes.py --out <cap>.v6 [--display :3] [--duration 900]

Started next to run-xfce-v4-v6.sh the way rca_sampler.py is. It
  1. writes tools-v6.json (sha256 of the probe binaries actually used),
  2. waits for the experimental X3 and its socket,
  3. starts the UNTRACED probe (x_rtt2.bionic through TermuxService, untraced_run.sh), retrying
     while X is not yet accepting (every attempt kept, as rca_sampler does for the traced one),
  4. runs traced_lat inside PRoot (its own child),
  5. stops when X3 is gone or the duration ends, and writes untraced-rtt.txt / traced-lat.txt.
The traced probe is rca_sampler.py started with --x-rtt x_rtt2.glibc; it is not duplicated here.
Kills only its own traced_lat child, by exact pid, and records it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TERMUX_TMP = Path("/data/data/com.termux/files/usr/tmp")
UNTRACED_BIN = "/data/data/com.termux/files/home/xfce-v6/x_rtt2.bionic"
TRACED_BIN = "/root/build/xfce-v6/x_rtt2.glibc"
TL_BIN = "/root/build/xfce-v6/traced_lat.glibc"
GRAB_BIN = "/root/build/xfce-v6/x_grab_stall.glibc"
UNTRACED_RUN = HERE / "untraced_run.sh"


def sha256(path) -> str | None:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def write_tools(out: Path, with_grab=False) -> dict:
    t = {"x_rtt2.bionic": sha256(UNTRACED_BIN), "x_rtt2.glibc": sha256(TRACED_BIN),
         "traced_lat.glibc": sha256(TL_BIN)}
    if with_grab:
        t["x_grab_stall.glibc"] = sha256(GRAB_BIN)
    (out / "tools-v6.json").write_text(json.dumps(t, indent=2, sort_keys=True) + "\n")
    return t


class Untraced:
    """The untraced probe on <display>, with connect retries; logs live on the shared Termux path."""

    def __init__(self, display: str, tag: str):
        self.display, self.tag = display, tag
        self.logs: list[Path] = []
        self.notes: list[str] = []

    def _launch(self, remaining: float) -> Path:
        log = TERMUX_TMP / f"xfce-v6-{self.tag}-untraced-{len(self.logs) + 1}.log"
        log.unlink(missing_ok=True)
        r = subprocess.run(["bash", str(UNTRACED_RUN), str(log), UNTRACED_BIN, self.display, "250",
                            str(int(max(remaining, 1)))], capture_output=True, text=True, timeout=30)
        self.notes.append(f"launch attempt={len(self.logs) + 1} rc={r.returncode} {r.stdout.strip()[-200:]}")
        self.logs.append(log)
        return log

    def start(self, deadline: float, attempts: int = 60) -> bool:
        for _ in range(attempts):
            log = self._launch(deadline - time.time())
            for _ in range(30):                           # up to 3 s for the first line(s)
                time.sleep(0.1)
                text = log.read_text(errors="replace") if log.exists() else ""
                if "RTT " in text:
                    return True
                if "RTT_CONNECT_FAIL" in text:
                    break
            self.notes.append(f"attempt={len(self.logs)} no RTT yet: {text.strip()[-120:]!r}")
            time.sleep(0.5)
            if time.time() >= deadline:
                break
        return False

    def collect(self, dest: Path, settle_s: float = 5.0) -> None:
        """Wait until the last log stops growing (the probe exits on RTT_DEAD), then concatenate."""
        last, stable_since = -1, time.time()
        while self.logs and time.time() - stable_since < settle_s:
            size = self.logs[-1].stat().st_size if self.logs[-1].exists() else 0
            if size != last:
                last, stable_since = size, time.time()
            time.sleep(0.5)
        with open(dest, "w") as f:
            for i, log in enumerate(self.logs, 1):
                if i > 1:
                    f.write(f"RTT_RETRY attempt={i}\n")
                f.write(log.read_text(errors="replace") if log.exists() else "RTT_LOG_MISSING\n")


def cmdline_text(raw: bytes) -> str:
    """NUL -> space, as the runners' `tr '\\0' ' '`. termux-x11 (Stable and experimental) and Claude
    Desktop rewrite argv[0] to the whole command line with SPACES, then NUL padding, so matching
    on NUL-separated arguments never finds them (caught by the host self-test, 2026-09-25)."""
    return raw.replace(b"\0", b" ").decode(errors="replace").strip()


def find_pid(prefix: str, proc: str = "/proc"):
    for p in os.listdir(proc):
        if p.isdigit():
            try:
                t = cmdline_text(open(f"{proc}/{p}/cmdline", "rb").read())
                if t == prefix or t.startswith(prefix + " "):     # ':3' must not match ':30'
                    return int(p)
            except OSError:
                pass
    return None


X3_PREFIX = "termux-x11gpu com.waydefu.x11gpu :3"
X1_PREFIX = "termux-x11 com.termux.x11 :1"


def x3_pid(proc: str = "/proc"):
    return find_pid(X3_PREFIX, proc)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--display", default=":3")
    ap.add_argument("--duration", type=float, default=900)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    notes = open(out / "v6-probes.log", "w")
    write_tools(out)
    t_end = time.time() + a.duration
    pid = None
    while pid is None and time.time() < t_end:
        pid = x3_pid()
        time.sleep(0.2)
    if pid is None:
        notes.write("NO_X3\n")
        return 1
    sock = Path(f"/tmp/.X11-unix/X{a.display.lstrip(':')}")
    while not sock.exists() and time.time() < t_end:
        time.sleep(0.2)
    tag = f"{os.getpid()}-{int(time.time())}"
    ut = Untraced(a.display, tag)
    ok = ut.start(t_end)
    notes.write(f"x3={pid} untraced_started={ok}\n")
    tl = subprocess.Popen([TL_BIN, "100", str(int(max(t_end - time.time(), 1)))],
                          stdout=open(out / "traced-lat.txt", "w"), stderr=subprocess.STDOUT,
                          stdin=subprocess.DEVNULL)
    notes.write(f"traced_lat pid={tl.pid}\n")
    while time.time() < t_end and Path(f"/proc/{pid}").exists():
        time.sleep(0.5)
    if tl.poll() is None:
        tl.terminate()
        notes.write(f"traced_lat_sigterm pid={tl.pid} (own child, X3 gone)\n")
    tl.wait()
    ut.collect(out / "untraced-rtt.txt")
    for n in ut.notes:
        notes.write(n + "\n")
    notes.close()
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
