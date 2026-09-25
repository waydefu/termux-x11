#!/usr/bin/env python3
"""Probe-sensitivity preflight body (XFCE-V6-BASELINE-01 section 5.2 as amended by section 9,
deviation 1). Called by run-xfce-v4-v6.sh MODE=preflight once X3 is up, with no XFCE session.

    preflight_probe.py --out DIR [--display :3]

Writes DIR/untraced-rtt.txt, traced-rtt.txt, grabs.txt, tools-v6.json. The two probes run
together; after 3 s, x_grab_stall holds GrabServer 3 x 1000 ms, 10 s apart. Judged by
xfce_v6_judge.py preflight. Only its own traced-probe child is ever signalled.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path

import v6_probes as P

GRABS, HOLD_MS, GAP_S = 3, 1000, 10


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--display", default=":3")
    a = ap.parse_args()
    out = Path(a.out)
    P.write_tools(out, with_grab=True)
    span = 3 + GRABS * (GAP_S + HOLD_MS / 1000) + 6          # both probes outlive the last grab
    deadline = time.time() + span
    ut = P.Untraced(a.display, f"pf-{os.getpid()}-{int(time.time())}")
    ok = ut.start(deadline)
    traced = subprocess.Popen([P.TRACED_BIN, a.display, "250", str(int(span))],
                              stdout=open(out / "traced-rtt.txt", "w"), stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL)
    time.sleep(3)
    with open(out / "grabs.txt", "w") as g:
        rc = subprocess.run([P.GRAB_BIN, a.display, str(GRABS), str(HOLD_MS), str(GAP_S)],
                            stdout=g, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL).returncode
    traced.wait(timeout=span + 30)                            # exits by itself at its duration
    ut.collect(out / "untraced-rtt.txt")
    (out / "preflight-probe.log").write_text(
        f"untraced_started={ok} grab_rc={rc} traced_rc={traced.returncode}\n" + "\n".join(ut.notes) + "\n")
    return 0 if ok and rc == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
