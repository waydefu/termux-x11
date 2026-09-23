#!/usr/bin/env python3
"""XFCE-FREEZE-V1 collector: one run directory -> xfce-run.json. Derives, never judges.

    xfce_collect.py --evidence <run dir> --freeze xfce-design-freeze.json --out xfce-run.json

Every derived metric is defined in the freeze (p11_metrics.derived); this file only
implements those definitions. Anything that cannot be computed is null - never 0.

Attribution rules that the tests pin (test_xfce.py):
  * X-side lines count ONLY from the X pid of this run. Stable's X (:1) logs the same
    'frames in 5.0 seconds' line under the same tag; counting it would fold the daily
    desktop into the experimental numbers.
  * GATEA_EVENT lines come from both roles (X and renderer). Completeness is judged on
    the global sequence number over the WHOLE run, against nextSequence from the close
    summary; window counts use the logcat timestamp.
  * SurfaceFlinger frames are in CLOCK_MONOTONIC, the same clock choreo.py records T0
    in, so the window is applied in nanoseconds with no conversion.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "common"))
import gatea_counters as C  # noqa: E402

INT64_MAX = 9223372036854775807
LOGCAT_RE = re.compile(
    r"^(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d)\.(\d{3})\s+(\d+)\s+(\d+)\s+([VDIWEFA])\s+(.*?)\s*:\s(.*)$")
FIVE_S_RE = re.compile(
    r"(\d+) frames in 5\.0 seconds = [\d.]+ FPS, (\d+)/(\d+) present copies offloaded to GPU, "
    r"(\d+)/(\d+) EXA copies offloaded \((\d+) CPU rects\), exa_solid_prepare=(\d+) "
    r"exa_solid_gpu=(\d+) exa_solid_fallback=(\d+) solid_rects=(\d+) cpu_solid_rects=(\d+) "
    r"renderer_solid_submits=(\d+) renderer_solid_complete=(\d+) xrender_ops=(\d+) "
    r"exa_comp_check=(\d+)/(\d+) prepare=(\d+)/(\d+) gpu_rects=(\d+) cpu_rects=(\d+) done=(\d+)")
FIVE_S_FIELDS = ("frames", "present_offload", "present_attempt", "exa_copy_offload",
                 "exa_copy_attempt", "exa_copy_cpu_rects", "exa_solid_prepare",
                 "exa_solid_gpu", "exa_solid_fallback", "solid_rects", "cpu_solid_rects",
                 "renderer_solid_submits", "renderer_solid_complete", "xrender_ops",
                 "exa_comp_check_true", "exa_comp_check_false", "prepare_true",
                 "prepare_false", "gpu_rects", "cpu_rects", "done")
STABLE_FRAMES_RE = re.compile(r"^(\d+) frames in 5\.0 seconds = [\d.]+ FPS\s*$")
EVENT_RE = re.compile(r"GATEA_EVENT seq=(\d+) role=(\d+) event=(\d+) generation=(\d+) "
                      r"serial=(\d+) src=(\d+) dst=(\d+)")
FATAL_PATTERNS = ("Fatal signal", "GATEA_FATAL_HALT", "Abort message")


def jload(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def read_kv(p: Path) -> dict:
    out = {}
    try:
        for line in p.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def pct(values: list[float], q: float):
    """Nearest-rank percentile. None for an empty list."""
    if not values:
        return None
    s = sorted(values)
    k = max(1, math.ceil(q / 100.0 * len(s)))
    return s[k - 1]


def parse_logcat(path: Path, year: int):
    rows = []
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        m = LOGCAT_RE.match(line)
        if not m:
            continue
        mo, d, h, mi, s, ms, pid, tid, lvl, tag, msg = m.groups()
        t = dt.datetime(year, int(mo), int(d), int(h), int(mi), int(s), int(ms) * 1000)
        rows.append({"epoch": t.timestamp(), "pid": int(pid), "tid": int(tid),
                     "lvl": lvl, "tag": tag, "msg": msg})
    return rows


def stat_cpu_ticks(text: str | None):
    """utime+stime from /proc/<pid>/stat, parsed after the LAST ')' (comm may hold
    spaces and parentheses - Q8)."""
    if not text or ")" not in text:
        return None
    rest = text[text.rindex(")") + 2:].split()
    try:
        return int(rest[11]) + int(rest[12])      # fields 14, 15 (1-based)
    except (IndexError, ValueError):
        return None


def parse_sf(path: Path):
    polls = []
    cur = None
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("=== poll"):
            cur = {"rows": [], "period": None}
            polls.append(cur)
            continue
        if cur is None:
            continue
        parts = line.split()
        if len(parts) == 1 and parts[0].isdigit() and cur["period"] is None:
            cur["period"] = int(parts[0])
        elif len(parts) == 3 and all(p.isdigit() for p in parts):
            cur["rows"].append(tuple(int(p) for p in parts))
    return polls


def sf_metrics(polls, t0_ns: int, t1_ns: int, jank_ms: float):
    if polls is None:
        return {"sf_available": False, "sf_coverage_complete": None}
    valid_polls = []
    for p in polls:
        rows = [r for r in p["rows"] if r[0] > 0 and 0 < r[1] < INT64_MAX]
        valid_polls.append({"valid": rows, "full": len(p["rows"]) > 0 and len(rows) == len(p["rows"]),
                            "period": p["period"]})
    # coverage: a FULL poll whose oldest frame is newer than the previous poll's newest
    # means frames were overwritten between the two reads
    gaps = 0
    prev_newest = None
    for vp in valid_polls:
        if not vp["valid"]:
            continue
        oldest = min(r[1] for r in vp["valid"])
        newest = max(r[1] for r in vp["valid"])
        if prev_newest is not None and vp["full"] and oldest > prev_newest:
            gaps += 1
        prev_newest = newest if prev_newest is None else max(prev_newest, newest)
    frames = sorted({r for vp in valid_polls for r in vp["valid"]}, key=lambda r: r[1])
    inwin = [r for r in frames if t0_ns <= r[1] < t1_ns]
    complete = gaps == 0 and len(valid_polls) > 0
    out = {"sf_available": True, "sf_polls": len(valid_polls), "sf_poll_gaps": gaps,
           "sf_coverage_complete": complete,
           "sf_refresh_period_ns": next((vp["period"] for vp in valid_polls if vp["period"]), None)}
    if not complete:
        out.update({k: None for k in ("frames_sf", "frame_interval_ms_p50", "frame_interval_ms_p95",
                                      "frame_interval_ms_p99", "present_lag_ms_p50",
                                      "present_lag_ms_p95", "present_lag_ms_p99", "jank_frames")})
        return out
    acts = [r[1] for r in inwin]
    iv = [(b - a) / 1e6 for a, b in zip(acts, acts[1:])]
    lag = [(r[1] - r[0]) / 1e6 for r in inwin]
    out.update({
        "frames_sf": len(inwin),
        "frame_interval_ms_p50": pct(iv, 50), "frame_interval_ms_p95": pct(iv, 95),
        "frame_interval_ms_p99": pct(iv, 99),
        "present_lag_ms_p50": pct(lag, 50), "present_lag_ms_p95": pct(lag, 95),
        "present_lag_ms_p99": pct(lag, 99),
        "jank_frames": sum(1 for x in lag if x > jank_ms),
    })
    return out


def summary_counters(text: str):
    line = next((ln for ln in text.splitlines() if "GATEA_SUMMARY" in ln), "")
    if not line:
        return None
    out = {"where": (re.search(r"where=(\S+)", line) or [None, None])[1]}
    for key in ("nonce", "generation", "nextSequence", "overflow", "firstFailed",
                "generationFatal", "fatalReason"):
        m = re.search(rf"\b{key}=(\d+)\b", line)
        out[key] = int(m.group(1)) if m else None
    for idx in C.COUNTERS:
        out[f"c{idx}"] = C.read(line, idx)
    return out


def collect(ev: Path, freeze: dict) -> dict:
    steps = []
    try:
        steps = [json.loads(ln) for ln in (ev / "steps.jsonl").read_text().splitlines() if ln.strip()]
    except (OSError, ValueError):
        pass
    t0 = next((s for s in steps if s.get("op") == "T0"), None)
    win_s = freeze["p01_duration"]["choreography_window_s"]
    out: dict = {"schema": "xfce-run/1", "evidence": str(ev), "steps": steps,
                 "t0": t0, "window_s": win_s}
    for name in ("run-binding.json", "session-ready.json", "logout.json", "close.json",
                 "xfce-clients-K1.json", "xfce-clients-K2.json", "preflight.json",
                 "capture-end.json"):
        out[name.rsplit(".", 1)[0].replace("-", "_")] = jload(ev / name)
    kp = {}
    for k in ("K0", "K1", "K2", "K3", "K4"):
        kp[k] = {"sample": jload(ev / f"k-{k}.json"), "extra": jload(ev / f"k-{k}-extra.json")}
    out["k"] = kp
    x_pid = _int((read_kv(ev / "x3-pid.txt")).get("x3_pid"))
    act_pid = _int(read_kv(ev / "activity-pid.txt").get("activity_pid"))
    stable_pid = _int(read_kv(ev / "stable-pid.txt").get("stable_pid"))
    out["pids"] = {"x": x_pid, "activity": act_pid, "stable": stable_pid}

    summ_text = ""
    try:
        summ_text = (ev / "gatea-summary.txt").read_text(errors="replace")
    except OSError:
        pass
    out["summary"] = summary_counters(summ_text)
    out["summary_source"] = "file" if out["summary"] else None

    if t0 is None:
        out["window"] = None
        return out
    e0 = float(t0["t0_epoch_s"])
    e1 = e0 + win_s
    n0 = int(t0["t0_mono_ns"])
    n1 = n0 + int(win_s * 1e9)
    out["window"] = {"epoch": [e0, e1], "mono_ns": [n0, n1]}
    year = dt.datetime.fromtimestamp(e0).year

    rows = parse_logcat(ev / "raw-logcat.txt", year)
    out["logcat_parsed"] = rows is not None
    rows = rows or []
    if out["summary"] is None:
        # second, independent source: the dump also goes to logcat from the X pid
        line = next((r["msg"] for r in rows if x_pid is not None and r["pid"] == x_pid
                     and "GATEA_SUMMARY" in r["msg"]), "")
        out["summary"] = summary_counters(line)
        out["summary_source"] = "logcat" if out["summary"] else None
    inwin = [r for r in rows if e0 <= r["epoch"] < e1]

    # ---- X-pid only
    xw = [r for r in inwin if x_pid is not None and r["pid"] == x_pid]
    five = {f: 0 for f in FIVE_S_FIELDS}
    n5 = 0
    for r in xw:
        m = FIVE_S_RE.search(r["msg"])
        if m:
            n5 += 1
            for f, v in zip(FIVE_S_FIELDS, m.groups()):
                five[f] += int(v)
    out["xrender_5s"] = five if n5 else {f: None for f in FIVE_S_FIELDS}
    out["xrender_5s_lines"] = n5
    out["composite_total"] = sum(1 for r in xw if r["msg"].startswith("Probe ENTER"))
    out["d0a_staged"] = sum(1 for r in xw if r["msg"].startswith("Gcomp FDCLONE"))
    out["prepare_true_lines"] = sum(1 for r in xw if r["msg"].startswith("Gcomp Prepare TRUE"))
    out["prepare_false_lines"] = sum(1 for r in xw if r["msg"].startswith("Gcomp Prepare FALSE"))

    # ---- events: completeness over the whole run, counts inside the window
    ev_all = []
    for r in rows:
        m = EVENT_RE.search(r["msg"])
        if m and r["pid"] in (x_pid, act_pid):
            seq, role, event, gen, serial, src, dst = map(int, m.groups())
            ev_all.append({"epoch": r["epoch"], "seq": seq, "role": role, "event": event,
                           "generation": gen})
    nxt = (out["summary"] or {}).get("nextSequence")
    seqs = {e["seq"] for e in ev_all}
    complete = nxt is not None and len(ev_all) == nxt and seqs == set(range(nxt))
    out["events_total"] = len(ev_all)
    out["events_complete"] = complete
    out["events_dup_seq"] = len(ev_all) - len(seqs)
    hist_all: dict[int, int] = {}
    for e in ev_all:
        hist_all[e["event"]] = hist_all.get(e["event"], 0) + 1
    out["event_hist_run"] = {str(k): v for k, v in sorted(hist_all.items())}
    hist_w: dict[int, int] = {}
    for e in ev_all:
        if e0 <= e["epoch"] < e1:
            hist_w[e["event"]] = hist_w.get(e["event"], 0) + 1
    out["event_hist_window"] = {str(k): v for k, v in sorted(hist_w.items())} if complete else None
    out["generations_seen"] = sorted({e["generation"] for e in ev_all})
    out["gatea_direct"] = hist_w.get(5, 0) if complete else None
    out["lease_reserved_run"] = hist_all.get(2, 0) if complete else None
    out["lease_release_run"] = hist_all.get(23, 0) if complete else None

    chk_t = out["xrender_5s"]["exa_comp_check_true"]
    chk_f = out["xrender_5s"]["exa_comp_check_false"]
    pre_t = out["xrender_5s"]["prepare_true"]
    pre_f = out["xrender_5s"]["prepare_false"]
    out["cpu_composite"] = None if chk_f is None or pre_f is None else chk_f + pre_f
    out["gatea_hit_rate"] = (None if out["gatea_direct"] is None or not chk_t
                             else out["gatea_direct"] / chk_t)
    out["gpu_composite_share"] = (None if pre_t is None or not out["composite_total"]
                                  else pre_t / out["composite_total"])

    # ---- GATEA_BIND, fatals, ANR over the WHOLE run
    out["gatea_bind_lines"] = sum(1 for r in rows if "GATEA_BIND" in r["msg"]
                                  and r["pid"] in (x_pid, act_pid))
    ours = {p for p in (x_pid, act_pid) if p is not None}
    # "Fatal signal" is logged by libc INSIDE the crashing process (our pid), but the
    # tombstone lines ("F DEBUG : pid: N, tid: ...", "Abort message") come from the
    # crash_dump helper under ITS OWN pid - so those are matched on the pid they name.
    def names_ours(msg):
        return any(re.search(rf"\bpid: {p},", msg) for p in ours)
    fat = [r for r in rows if
           (r["pid"] in ours and any(p in r["msg"] for p in FATAL_PATTERNS))
           or (r["tag"] == "DEBUG" and r["lvl"] == "F" and names_ours(r["msg"]))]
    out["fatal_lines"] = [f'{r["pid"]} {r["tag"]}: {r["msg"][:200]}' for r in fat][:50]
    out["anr_lines"] = [r["msg"][:200] for r in rows if "ANR in com.waydefu.x11gpu" in r["msg"]][:10]

    # ---- Stable background covariate (passive read of lines Stable already emits)
    sfr = []
    for r in inwin:
        if stable_pid is not None and r["pid"] == stable_pid:
            m = STABLE_FRAMES_RE.match(r["msg"]) or FIVE_S_RE.search(r["msg"])
            if m:
                sfr.append(int(m.group(1)))
    out["stable_background_frames_5s"] = sfr

    # ---- SurfaceFlinger
    jank_ms = 33.33
    out["sf"] = sf_metrics(parse_sf(ev / "sf-polls.txt"), n0, n1, jank_ms)

    # ---- CPU, per frame
    def ticks(k, who):
        return stat_cpu_ticks((kp[k]["extra"] or {}).get(f"{who}_stat"))
    xa, xb = ticks("K1", "x"), ticks("K2", "x")
    aa, ab = ticks("K1", "act"), ticks("K2", "act")
    out["x_cpu_s"] = None if xa is None or xb is None else (xb - xa) / 100.0
    out["act_cpu_s"] = None if aa is None or ab is None else (ab - aa) / 100.0
    fr = out["sf"].get("frames_sf")
    out["cpu_per_frame_ms"] = (None if fr in (None, 0) or out["x_cpu_s"] is None
                               or out["act_cpu_s"] is None
                               else (out["x_cpu_s"] + out["act_cpu_s"]) * 1000.0 / fr)
    out["wall_per_frame_ms"] = None if fr in (None, 0) else win_s * 1000.0 / fr

    # ---- gpubusy
    gb = []
    zero = 0
    try:
        for ln in (ev / "gpubusy-polls.txt").read_text().splitlines():
            p = ln.split()
            if len(p) == 3 and e0 <= float(p[0]) < e1:
                busy, total = int(p[1]), int(p[2])
                if total > 0:
                    gb.append(busy / total)
                else:
                    zero += 1
    except (OSError, ValueError):
        gb = None
    out["gpu_busy_mean"] = (sum(gb) / len(gb)) if gb else None
    out["gpu_busy_polls"] = None if gb is None else len(gb)
    out["gpu_busy_zero_polls"] = zero

    # ---- thermal (HAL section of each poll)
    th = {"skin_max": None, "cpu_max": None, "gpu_max": None, "status_max": None, "polls": 0}
    try:
        cur_hal = False
        for ln in (ev / "thermal-polls.txt").read_text(errors="replace").splitlines():
            if ln.startswith("=== poll"):
                th["polls"] += 1
                cur_hal = False
                continue
            if ln.startswith("Thermal Status:"):
                v = int(ln.split(":")[1])
                th["status_max"] = v if th["status_max"] is None else max(th["status_max"], v)
            if "Current temperatures from HAL" in ln:
                cur_hal = True
                continue
            if cur_hal and ln.strip().startswith("Temperature{"):
                m = re.search(r"mValue=([\d.]+), mType=(\d+), mName=([^,]+),", ln)
                if not m:
                    continue
                val, typ, name = float(m.group(1)), int(m.group(2)), m.group(3)
                key = {3: "skin_max", 0: "cpu_max", 1: "gpu_max"}.get(typ)
                if key:
                    th[key] = val if th[key] is None else max(th[key], val)
            elif cur_hal and ln.strip() and not ln.startswith("\t") and not ln.startswith(" "):
                cur_hal = False
    except OSError:
        th = None
    out["thermal"] = th
    return out


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", required=True)
    ap.add_argument("--freeze", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    freeze = json.loads(Path(a.freeze).read_text())
    res = collect(Path(a.evidence), freeze)
    Path(a.out).write_text(json.dumps(res, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
