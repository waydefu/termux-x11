#!/usr/bin/env python3
"""Offline checks for opstream_decompose.py on hand-built traces with known answers.
Includes cases that must NOT produce a GPU wait (a tool that always finds one is useless)."""
import os
import subprocess
import sys
import tempfile
import zlib
import json

HERE = os.path.dirname(os.path.abspath(__file__))
X, R, IRQ = 5001, 7002, 99

def sw(t, pc, pp, st, nc, np_):
    return (f"   {pc}-{pp}  (  {pp}) [003] d..2. {t:.6f}: sched_switch: prev_comm={pc} prev_pid={pp} "
            f"prev_prio=120 prev_state={st} ==> next_comm={nc} next_pid={np_} next_prio=120")

def wk(t, wc, wp, comm, pid):
    return f"   {wc}-{wp}  (  {wp}) [003] d..3. {t:.6f}: sched_waking: comm={comm} pid={pid} prio=120 target_cpu=003"

def trace_lines():
    L = []
    b = 100.0
    # op 0 (phase 0): full GPU handoff. t0=b+0.000000 t1=b+0.003000
    L += [wk(b + 0.000050, "swapper", 0, "Xlorie", X),
          sw(b + 0.000100, "swapper", 0, "S", "Xlorie", X),           # deliver 100 us
          wk(b + 0.000300, "Xlorie", X, "LorieRendererTh", R),       # x_pre 200 us
          sw(b + 0.000350, "Xlorie", X, "S", "swapper", 0),
          sw(b + 0.000400, "swapper", 0, "S", "LorieRendererTh", R),  # handoff 100 us
          sw(b + 0.000700, "LorieRendererTh", R, "S", "swapper", 0),  # r_work 300 us
          wk(b + 0.001700, "irq/kgsl", IRQ, "LorieRendererTh", R),    # gpu_wait 1000 us
          sw(b + 0.001750, "swapper", 0, "S", "LorieRendererTh", R),
          sw(b + 0.001850, "LorieRendererTh", R, "S", "swapper", 0),  # r_post 100 us
          wk(b + 0.002000, "swapper", 0, "Xlorie", X),
          sw(b + 0.002050, "swapper", 0, "S", "Xlorie", X),           # x_notice 200 us
          sw(b + 0.002150, "Xlorie", X, "S", "swapper", 0)]
    # op 1 (phase 1): nop - X runs, never wakes the renderer
    c = b + 1.0
    L += [sw(c + 0.000080, "swapper", 0, "S", "Xlorie", X),
          sw(c + 0.000180, "Xlorie", X, "S", "swapper", 0)]
    # op 2 (phase 1): renderer woken by X twice (lock ping-pong), never by the GPU
    d = b + 2.0
    L += [sw(d + 0.000100, "swapper", 0, "S", "Xlorie", X),
          wk(d + 0.000200, "Xlorie", X, "LorieRendererTh", R),
          sw(d + 0.000220, "Xlorie", X, "S", "swapper", 0),
          sw(d + 0.000250, "swapper", 0, "S", "LorieRendererTh", R),
          sw(d + 0.000300, "LorieRendererTh", R, "S", "swapper", 0),
          sw(d + 0.000450, "swapper", 0, "S", "Xlorie", X),
          wk(d + 0.000500, "Xlorie", X, "LorieRendererTh", R),
          sw(d + 0.000520, "Xlorie", X, "S", "swapper", 0),
          sw(d + 0.000550, "swapper", 0, "S", "LorieRendererTh", R),
          sw(d + 0.000600, "LorieRendererTh", R, "S", "Xlorie", X),
          sw(d + 0.000700, "Xlorie", X, "S", "swapper", 0)]
    return L

def main():
    tmp = tempfile.mkdtemp()
    body = ("# tracer: nop\n" + "\n".join(trace_lines()) + "\n").encode()
    tz = os.path.join(tmp, "t.z")
    open(tz, "wb").write(b"TRACE:\n" + zlib.compress(body))
    ops = os.path.join(tmp, "ops.txt")
    open(ops, "w").write("PHASE 0 solid 64 single 1 5 0\nOP 0 0 100000000000 100003000000\n"
                         "PHASE 1 nop 64 single 2 5 0\nOP 1 0 101000000000 101001000000\n"
                         "OP 1 1 102000000000 102001000000\n")
    js = os.path.join(tmp, "o.json")
    r = subprocess.run([sys.executable, os.path.join(HERE, "opstream_decompose.py"), "--trace", tz, "--ops", ops,
                        "--x-tid", str(X), "--r-tid", str(R), "--json", js], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    o = {(x["phase"], x["i"]): x for x in json.load(open(js))["ops"]}
    a = o[(0, 0)]
    want = {"kind": "handoff", "deliver_us": 100, "x_pre_us": 200, "handoff_us": 100, "r_work_us": 300,
            "gpu_wait_us": 1050, "r_post_us": 100, "x_notice_us": 200, "tail_us": 950, "gpu_waker": "irq/kgsl"}
    for k, v in want.items():
        got = a[k]
        ok = abs(got - v) < 0.5 if isinstance(v, (int, float)) else got == v
        assert ok, f"op0 {k}: want {v} got {got}"
    assert o[(1, 0)]["kind"] == "no_handoff", o[(1, 0)]
    b = o[(1, 1)]
    assert b["kind"] == "handoff" and b["gpu_wait_us"] is None, f"X-only wakes must not count as GPU: {b}"
    assert abs(b["r_other_us"] - 250) < 0.5, b
    # tid detection: X3 tgid 5000 has a Looper leader (5000) that the client never wakes and a dix
    # thread 5001 it does; the Activity (tgid 7000) has a decoy thread 7003 that X wakes less often.
    def line(t, comm, tid, tg, ev, args):
        return f"   {comm}-{tid}  (  {tg}) [001] d..3. {t:.6f}: {ev}: {args}"
    D = []
    for k in range(3):
        c = 200.0 + k
        D += [line(c + 0.0001, "p_opstream", 6000, 6000, "sched_waking", "comm=main pid=5001 prio=120 target_cpu=001"),
              line(c + 0.0002, "main", 5001, 5000, "sched_waking", "comm=Thread-7 pid=7002 prio=120 target_cpu=002"),
              line(c + 0.0003, "Thread-7", 7002, 7000, "sched_switch", "prev_comm=Thread-7 prev_pid=7002 prev_prio=120 prev_state=S ==> next_comm=swapper next_pid=0 next_prio=120"),
              line(c + 0.0004, "main", 5000, 5000, "sched_switch", "prev_comm=main prev_pid=5000 prev_prio=120 prev_state=S ==> next_comm=swapper next_pid=0 next_prio=120")]
    D += [line(200.0005, "main", 5001, 5000, "sched_waking", "comm=decoy pid=7003 prio=120 target_cpu=002"),
          line(200.0006, "main", 5000, 5000, "sched_waking", "comm=Thread-7 pid=7002 prio=120 target_cpu=002")]
    tz2 = os.path.join(tmp, "d.z")
    open(tz2, "wb").write(b"TRACE:\n" + zlib.compress(("\n".join(D) + "\n").encode()))
    ops2 = os.path.join(tmp, "ops2.txt")
    open(ops2, "w").write("PHASE 0 solid 64 single 3 5 0\n" + "".join(
        f"OP 0 {k} {int((200 + k) * 1e9)} {int((200 + k) * 1e9) + 1000000}\n" for k in range(3)))
    r = subprocess.run([sys.executable, os.path.join(HERE, "opstream_decompose.py"), "--trace", tz2, "--ops", ops2,
                        "--x3-pid", "5000", "--act-pid", "7000"], capture_output=True, text=True)
    assert "DETECT x_tid=5001 r_tid=7002" in r.stdout, r.stdout + r.stderr
    print("TEST_OPSTREAM_DECOMPOSE PASS 3 ops (1 GPU handoff exact, 1 no-handoff, 1 must-not-be-GPU) + tid detection with decoys")

if __name__ == "__main__":
    main()
