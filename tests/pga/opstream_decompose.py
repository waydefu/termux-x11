#!/usr/bin/env python3
"""Split every p_opstream op into scheduler-visible parts (PGA-GAP-2 step 2). Descriptive, not a judge.

    opstream_decompose.py --trace atrace.z --ops opstream.out --x-tid N --r-tid N [--json OUT]

Inputs: an `atrace -z` capture (sched events, trace_clock=boot), the fixture's OP lines
(CLOCK_BOOTTIME windows [t0, t1]), the X3 main thread tid and the renderer thread tid
(LorieRendererTh in the Activity). The trace is streamed; only events that touch the two
threads are kept.

Segments, frozen before any device capture was analysed (all per op, inside [t0, t1]):
  deliver    t0 -> first time X runs                  (client request reaches X)
  x_pre      X's first run -> X wakes the renderer     (X-side prepare + publish; X CPU)
  handoff    X wakes renderer -> renderer runs          (cross-process futex wake + scheduling)
  r_work     renderer running time until it blocks on the wait that the GPU ends
  gpu_wait   renderer sleep that ends with a wake NOT from X (GPU/fence completion)
  r_other    every other renderer sleep in the window   (lock / driver waits)
  r_post     renderer running time after gpu_wait until its last switch-out
  x_notice   renderer's last switch-out -> X's next run (X polls with usleep(200))
  tail       X notice -> t1                             (reply + client wake)
An op with no X->renderer wake is 'no_handoff' (expected for nop and in mode C) and only
gets deliver / x_cpu / total. x_wakeups = how many times X was switched in within [t0, t1]
(the usleep(200) poll loop shows up as many short runs).
Unmatched structure is counted, never forced into a segment.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import zlib

LINE = re.compile(r"^\s*(.+?)-(\d+)\s+\(\s*[-\d]*\)\s+\[\d+\]\s+\S+\s+([\d.]+): (\w+): (.*)$")
SW = re.compile(r"prev_comm=(.*) prev_pid=(\d+) prev_prio=\d+ prev_state=(\S+) ==> next_comm=(.*) next_pid=(\d+) next_prio")
WK = re.compile(r"comm=(.*) pid=(\d+) prio=")


def lines_of(path):
    """atrace -z writes 'TRACE:\\n' then one zlib stream; plain text is accepted too."""
    raw = open(path, "rb")
    head = raw.read(4096)
    k = head.find(b"TRACE:\n")
    if k >= 0 and head[k + 7:k + 8] == b"\x78":           # zlib header
        d = zlib.decompressobj()
        buf = d.decompress(head[k + 7:])
        while True:
            *full, buf = buf.split(b"\n")
            for ln in full:
                yield ln.decode(errors="replace")
            chunk = raw.read(1 << 20)
            if not chunk:
                break
            buf += d.decompress(chunk)
        buf += d.flush()
        for ln in buf.split(b"\n"):
            if ln:
                yield ln.decode(errors="replace")
    else:
        rest = head + raw.read()
        yield from rest.decode(errors="replace").splitlines()


def load_events(path, xt, rt):
    """-> list of (ts_ns, kind, a, b): kind in on/off (thread switched in/out, b=prev_state)
    and wake (a=wakee, b=waker comm-tid)."""
    ev = []
    for ln in lines_of(path):
        m = LINE.match(ln)
        if not m:
            continue
        comm, tid, ts, name, args = m.groups()
        t = int(round(float(ts) * 1e9))
        if name == "sched_switch":
            s = SW.match(args)
            if not s:
                continue
            pp, ps, np_ = int(s.group(2)), s.group(3), int(s.group(5))
            if pp in (xt, rt):
                ev.append((t, "off", pp, ps))
            if np_ in (xt, rt):
                ev.append((t, "on", np_, ""))
        elif name == "sched_waking":
            s = WK.match(args)
            if s and int(s.group(2)) in (xt, rt):
                ev.append((t, "wake", int(s.group(2)), f"{comm}-{tid}"))
    ev.sort(key=lambda e: e[0])
    return ev


def detect_tids(path, ops, x3_pid, act_pid, client_comm="p_opstream"):
    """Thread identities from the trace itself (opstream-g-02: guessing them from logcat was wrong -
    'gles-renderer: Initialized EGL' is X's own EGL init, and the X3 thread-group leader is the Android
    Looper running Choreographer callbacks, not the dix thread).
      X        = the X3 thread (tgid x3_pid) the client wakes most often inside op windows
      renderer = the Activity thread (tgid act_pid) that X wakes most often inside op windows
    Returns (x_tid, r_tid, evidence dict)."""
    wins = [(t0, t1) for _, _, t0, t1 in ops]
    lo, hi = wins[0][0], wins[-1][1]
    tg = re.compile(r"^\s*(.+?)-(\d+)\s+\(\s*([-\d]*)\)")
    wake_by_client, wakes = {}, []
    k = 0
    for ln in lines_of(path):
        if "sched_waking" not in ln:
            continue
        m = LINE.match(ln)
        if not m:
            continue
        t = int(round(float(m.group(3)) * 1e9))
        if t < lo:
            continue
        if t > hi:
            break
        while k < len(wins) and wins[k][1] < t:
            k += 1
        if k == len(wins) or t < wins[k][0]:
            continue
        s = WK.match(m.group(5))
        if not s:
            continue
        wakes.append((m.group(1), int(m.group(2)), int(s.group(2))))
    # tgid of every tid seen as a line owner
    tgid = {}
    for ln in lines_of(path):
        g = tg.match(ln)
        if g and g.group(3).isdigit():
            tgid.setdefault(int(g.group(2)), int(g.group(3)))
    for comm, waker, wakee in wakes:
        if comm == client_comm and tgid.get(wakee) == x3_pid:
            wake_by_client[wakee] = wake_by_client.get(wakee, 0) + 1
    if not wake_by_client:
        return None, None, {"error": "client never woke an X3 thread"}
    xt = max(wake_by_client, key=wake_by_client.get)
    by_x = {}
    for comm, waker, wakee in wakes:
        if waker == xt and tgid.get(wakee) == act_pid:
            by_x[wakee] = by_x.get(wakee, 0) + 1
    rt = max(by_x, key=by_x.get) if by_x else None
    return xt, rt, {"client_wakes_x3_threads": wake_by_client, "x_wakes_activity_threads": by_x}


def load_ops(path):
    ops, phases = [], {}
    for ln in open(path):
        p = ln.split()
        if p[:1] == ["PHASE"]:
            phases[int(p[1])] = {"op": p[2], "size": int(p[3]), "shape": p[4], "count": int(p[5]), "gap_ms": int(p[6])}
        elif p[:1] == ["OP"]:
            ops.append((int(p[1]), int(p[2]), int(p[3]), int(p[4])))
    return ops, phases


def runs(ev, tid, lo, hi):
    """Running intervals of tid clipped to [lo, hi], from on/off events (state before lo inferred)."""
    out, start = [], None
    for t, k, a, _ in ev:
        if a != tid or k == "wake":
            continue
        if t > hi:
            break
        if k == "on":
            start = t
        elif k == "off":
            if start is None:
                start = lo if t >= lo else None
            if start is not None and t >= lo:
                out.append((max(start, lo), t))
            start = None
    if start is not None and start <= hi:
        out.append((max(start, lo), hi))
    return out


def decompose(ev, ops, xt, rt):
    res = []
    idx_lo = 0
    xs = f"-{xt}"
    for phase, i, t0, t1 in ops:
        while idx_lo < len(ev) and ev[idx_lo][0] < t0 - 50_000_000:
            idx_lo += 1
        win = [e for e in ev[idx_lo:] if e[0] <= t1 + 1_000_000]
        win = [e for e in win if e[0] >= t0 - 50_000_000]
        xr = runs(win, xt, t0, t1)
        rec = {"phase": phase, "i": i, "total_us": (t1 - t0) / 1e3,
               "x_wakeups": len(xr), "x_cpu_us": sum(b - a for a, b in xr) / 1e3}
        if not xr:
            rec["kind"] = "x_not_seen"
            res.append(rec)
            continue
        x_first = xr[0][0]
        rec["deliver_us"] = (x_first - t0) / 1e3
        sig = next((e for e in win if e[1] == "wake" and e[2] == rt and e[3].endswith(xs) and x_first <= e[0] <= t1), None)
        if not sig:
            rec["kind"] = "no_handoff"
            res.append(rec)
            continue
        ts = sig[0]
        rr = runs(win, rt, ts, t1)
        if not rr:
            rec["kind"] = "renderer_not_seen"
            res.append(rec)
            continue
        rec["kind"] = "handoff"
        rec["x_pre_us"] = (ts - x_first) / 1e3
        rec["handoff_us"] = (rr[0][0] - ts) / 1e3
        # renderer sleeps between runs, and who ended each one
        wakes = [e for e in win if e[1] == "wake" and e[2] == rt and ts < e[0] <= t1]
        sleeps = []
        for (a0, a1), (b0, _) in zip(rr, rr[1:]):
            w = next((e for e in wakes if a1 <= e[0] <= b0), None)
            sleeps.append((a1, b0, w[3] if w else None))
        gpu = [s for s in sleeps if s[2] is not None and not s[2].endswith(xs)]
        g = max(gpu, key=lambda s: s[1] - s[0]) if gpu else None
        rec["r_wakers"] = [s[2] for s in sleeps]
        if g:
            rec["gpu_wait_us"] = (g[1] - g[0]) / 1e3
            rec["gpu_waker"] = re.sub(r"-\d+$", "", g[2])
            rec["r_work_us"] = sum(min(b, g[0]) - a for a, b in rr if a < g[0]) / 1e3
            rec["r_post_us"] = sum(b - max(a, g[1]) for a, b in rr if b > g[1]) / 1e3
        else:
            rec["gpu_wait_us"] = None                    # null, not 0: no GPU-ended sleep was seen
            rec["r_work_us"] = sum(b - a for a, b in rr) / 1e3
        rec["r_other_us"] = sum(s[1] - s[0] for s in sleeps if s is not g) / 1e3
        r_last = rr[-1][1]
        xn = next((a for a, b in xr if a >= r_last), None)
        rec["x_notice_us"] = (xn - r_last) / 1e3 if xn is not None else None
        rec["tail_us"] = (t1 - xn) / 1e3 if xn is not None else None
        res.append(rec)
    return res


def summarize(res, phases):
    keys = ["total_us", "deliver_us", "x_pre_us", "handoff_us", "r_work_us", "gpu_wait_us", "r_other_us",
            "r_post_us", "x_notice_us", "tail_us", "x_cpu_us", "x_wakeups"]
    out = []
    for p in sorted(phases):
        rows = [r for r in res if r["phase"] == p]
        s = {"phase": p, **phases[p], "n": len(rows), "kinds": {}}
        for r in rows:
            s["kinds"][r["kind"]] = s["kinds"].get(r["kind"], 0) + 1
        for k in keys:
            v = sorted(r[k] for r in rows if r.get(k) is not None)
            s[k] = {"n": len(v), "p50": round(statistics.median(v), 1),
                    "p90": round(v[int(0.9 * (len(v) - 1))], 1)} if v else None
        wk = {}
        for r in rows:
            if r.get("gpu_waker"):
                wk[r["gpu_waker"]] = wk.get(r["gpu_waker"], 0) + 1
        s["gpu_wakers"] = wk
        out.append(s)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", required=True)
    ap.add_argument("--ops", required=True)
    ap.add_argument("--x-tid", type=int)
    ap.add_argument("--r-tid", type=int)
    ap.add_argument("--x3-pid", type=int, help="with --act-pid: detect both tids from the trace")
    ap.add_argument("--act-pid", type=int)
    ap.add_argument("--json")
    a = ap.parse_args()
    ops, phases = load_ops(a.ops)
    detect = None
    if a.x3_pid and a.act_pid:
        a.x_tid, a.r_tid, detect = detect_tids(a.trace, ops, a.x3_pid, a.act_pid)
        print(f"DETECT x_tid={a.x_tid} r_tid={a.r_tid} {json.dumps(detect)}")
        if a.x_tid is None or a.r_tid is None:
            print("DECOMPOSE_TIDS_NOT_FOUND")
            return 2
    if a.x_tid is None or a.r_tid is None:
        ap.error("need --x-tid/--r-tid or --x3-pid/--act-pid")
    ev = load_events(a.trace, a.x_tid, a.r_tid)
    if not ev:
        print("DECOMPOSE_NO_EVENTS")
        return 2
    cover = (ev[0][0], ev[-1][0])
    lost = sum(1 for _, _, t0, t1 in ops if t0 < cover[0] or t1 > cover[1])
    res = decompose(ev, ops, a.x_tid, a.r_tid)
    summ = summarize(res, phases)
    doc = {"x_tid": a.x_tid, "r_tid": a.r_tid, "detect": detect, "events": len(ev), "trace_cover_ns": cover, "ops_outside_trace": lost, "phases": summ, "ops": res}
    if a.json:
        json.dump(doc, open(a.json, "w"), indent=1)
    print(f"DECOMPOSE events={len(ev)} ops={len(ops)} ops_outside_trace={lost}")
    for s in summ:
        f = lambda k: "null" if s[k] is None else f"{s[k]['p50']}/{s[k]['p90']}"
        print(f"{s['phase']} {s['op']}:{s['size']}:{s['shape']}:gap{s['gap_ms']} n={s['n']} kinds={s['kinds']}"
              f" | total {f('total_us')} deliver {f('deliver_us')} x_pre {f('x_pre_us')} handoff {f('handoff_us')}"
              f" r_work {f('r_work_us')} gpu_wait {f('gpu_wait_us')} r_other {f('r_other_us')} r_post {f('r_post_us')}"
              f" x_notice {f('x_notice_us')} tail {f('tail_us')} | x_cpu {f('x_cpu_us')} x_wakeups {f('x_wakeups')}"
              f" gpu_wakers={s['gpu_wakers']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
