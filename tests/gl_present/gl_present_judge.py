#!/usr/bin/env python3
"""GL-PRESENT-01 judge (evidence/session/gl/GL-PRESENT-01-FREEZE.md), mainline #3 step 1.

    gl_present_judge.py --rep <main cell dir> --rep <main cell dir> [--xdbg <xdbg cell dir>] [--json out]
    gl_present_judge.py --self-test

Reads the SEG lines in <cell>/cmd.out plus <seg>.log, <seg>.present.jsonl, <seg>.req.jsonl, <seg>.px.txt,
<seg>.x3t0/.x3t1 and (xdbg) raw-logcat.txt.
Amendment 1 (2026-09-26, before any judged cell): the EGL/Zink client presents nothing on :3 (dry-01-r2,
diag-egl-01, not judged), so J2/J3 are judged for vk + glx, and a NOT_PRESENTED path class exists that needs
both the protocol-level request histogram and the root-pixel probe. No threshold value changed.
GL-PRESENT-02 (2026-09-26, after GL-PRESENT-01 was judged INVALID on these two defects; thresholds unchanged):
  - J0 rule 2 counts only frame hand-offs in off segments (HANDOFF_*): Zink kopper builds its swapchain at
    glXMakeCurrent, so glx-off imports 3 DRI3 buffers and never presents - that is not presenting.
  - J1x is X_WITNESS_NULL when J1 is not a path class (it said CONTRADICTS when J1 was PATH_INVALID).
GL-PRESENT-03 (2026-09-26, after GL-PRESENT-02 was judged INVALID; thresholds unchanged):
  - an off segment fails the protocol check only when its histogram is missing/unreadable; an EMPTY histogram
    is "measured, sent nothing" (vk_present --offscreen never connects to X). 02 failed rep1 vk-off on this.
  - replayed on the real 01/02 cells before freezing (tool check only; 01/02 verdicts stay INVALID).
Every threshold is a constant below and is quoted verbatim in the freeze doc.
A value that could not be measured is None and makes its segment INVALID; it is never read as 0.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile

CLK_TCK = 100                 # /proc/<pid>/stat utime/stime unit on this kernel (USER_HZ)
COVER = 0.90                  # a path counter must account for >= 90% of the client's frames
ON_MIN_CORES = 0.10           # J2: X3 cores in the on-screen -l segment, lower bound for COST
ON_OVER_OFF = 3.0             # J2: on-l X3 cores >= 3 x max(off, idle, FLOOR_CORES)
FLOOR_CORES = 0.01
OFF_CTRL_MARGIN = 0.05        # J2 control: off X3 cores <= idle X3 cores + 0.05, else CONTROL_FAILED
CHEAP_MAX = 0.05              # J2: on-l X3 cores < 0.05 in both reps -> X3_PRESENT_CHEAP
SCALE_PER_PIXEL = 2.0         # J3: r = X3 ms/frame (1200x1200) / (600x600) >= 2.0 in both reps
SCALE_FIXED = 1.5             # J3: r <= 1.5 in both reps
ONSCREEN = ["vk-l", "vk-s", "glx-l", "glx-s", "egl-l"]
OFFSCREEN = ["vk-off", "glx-off"]
JUDGED_APIS = ("vk", "glx")      # amendment 1 (was vk, egl)
CLIENT_COLOURS = {0x0080ff, 0xff8000}   # the two clear colours of every client, as 0xRRGGBB
# requests that hand a frame to the server: (extension, minor) at xcb level and core majors at libc level
PRESENT_EXT_REQS = {("Present", 1), ("MIT-SHM", 3), ("MIT-SHM", 5), ("DRI3", 2), ("DRI3", 7)}
PRESENT_CORE_MAJORS = {72, 62, 63}      # PutImage, CopyArea, CopyPlane
# GL-PRESENT-02: what counts as handing a frame over in an off segment (swapchain set-up imports do not)
HANDOFF_COUNTS = ["present_pixmap", "shm_put_image", "put_image", "x_put_image", "x_shm_put_image"]
HANDOFF_EXT_REQS = {("Present", 1), ("MIT-SHM", 3)}
SW_CONTROL = "vk-l-sw"
NON_DRI3_PATHS = {"SHM_PIXMAP_PRESENT", "SHM_PUT", "CORE_PUT"}
PRESENT_KEYS = ["dri3_open", "pixmap_from_buffer", "pixmap_from_buffers", "present_pixmap", "shm_put_image",
                "put_image", "x_put_image", "x_shm_put_image", "shm_create_pixmap", "shm_attach"]


def api_of(seg):
    return seg.split("-")[0]


def parse_seg_lines(path):
    segs = {}
    for line in open(path, errors="replace"):
        if not line.startswith("SEG "):
            continue
        parts = line.split()
        kv = dict(p.split("=", 1) for p in parts[2:] if "=" in p)
        segs[parts[1]] = kv
    return segs


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def client_result(cell, seg):
    """-> (frames, identity_ok, detail) from the client's own stdout."""
    try:
        text = open(os.path.join(cell, f"{seg}.log"), errors="replace").read()
    except OSError:
        return None, False, "no_log"
    api = api_of(seg)
    if api == "vk":
        m = re.search(r'^VK_DEVICE name="([^"]*)" driver="([^"]*)"', text, re.M)
        ident = bool(m) and "adreno" in m.group(1).lower() and "turnip" in m.group(2).lower()
        r = re.search(r"^VK_PRESENT frames=(\d+) secs=([\d.]+)", text, re.M)
    else:
        m = re.search(r'^GL_RENDERER "([^"]*)"', text, re.M)
        ident = bool(m) and "zink" in m.group(1).lower() and "adreno" in m.group(1).lower()
        r = re.search(r"^GL_PRESENT api=\w+ frames=(\d+) secs=([\d.]+)", text, re.M)
    frames = int(r.group(1)) if r else None
    return frames, ident, (m.group(0) if m else "no_identity")


def shim(cell, seg):
    """-> the single present_count JSON object, or None (missing/duplicated = not measured)."""
    try:
        lines = [l for l in open(os.path.join(cell, f"{seg}.present.jsonl")) if l.strip()]
    except OSError:
        return None
    if len(lines) != 1:
        return None
    try:
        return json.loads(lines[0])
    except ValueError:
        return None


def classify(c, frames):
    """Which way the frames reached the server. UNEXPLAINED = no counter accounts for >= COVER of them."""
    need = COVER * frames
    imports = c["pixmap_from_buffer"] + c["pixmap_from_buffers"]
    shm_pix = c["shm_create_pixmap"]
    puts = c["put_image"] + c["x_put_image"]
    shm_puts = c["shm_put_image"] + c["x_shm_put_image"]
    if imports >= 1 and c["present_pixmap"] >= need and shm_pix == 0 and puts == 0 and shm_puts == 0:
        return "DRI3"
    if shm_pix >= 1 and c["present_pixmap"] >= need and imports == 0 and puts == 0:
        return "SHM_PIXMAP_PRESENT"
    if shm_puts >= need and imports == 0 and c["present_pixmap"] == 0 and puts == 0:
        return "SHM_PUT"
    if puts >= need and imports == 0 and c["present_pixmap"] == 0 and shm_puts == 0:
        return "CORE_PUT"
    return "UNEXPLAINED"


def req_hist(cell, seg):
    """-> the single req_count JSON object, or None."""
    try:
        lines = [l for l in open(os.path.join(cell, f"{seg}.req.jsonl")) if l.strip()]
        return json.loads(lines[0]) if len(lines) == 1 else None
    except (OSError, ValueError):
        return None


def px_samples(cell, seg):
    """-> list of 0xRRGGBB ints (4 expected) or None if the probe did not produce 4 readable samples."""
    try:
        vals = [l.split()[2] for l in open(os.path.join(cell, f"{seg}.px.txt")) if l.startswith("PX ")]
    except (OSError, IndexError):
        return None
    if len(vals) != 4 or any(not v.startswith("0x") for v in vals):
        return None
    return [int(v, 16) & 0xffffff for v in vals]


def not_presented(req, px):
    """True only when the protocol stream shows no frame hand-off AND the window pixel never shows a client
    colour. Unknown (None) when either witness is missing."""
    if req is None or px is None or not req.get("majors"):
        return None
    if any((r["ext"], r["op"]) in PRESENT_EXT_REQS and r["n"] > 0 for r in req.get("requests", [])):
        return False
    if any(m["major"] in PRESENT_CORE_MAJORS and m["n"] > 0 for m in req["majors"]):
        return False
    return not any(v in CLIENT_COLOURS for v in px)


def thread_deltas(cell, seg):
    def load(p):
        d = {}
        for line in open(p):
            f = line.split(None, 3)
            if len(f) >= 3:
                d[f[0]] = (int(f[1]) + int(f[2]), f[3].strip() if len(f) > 3 else "")
        return d
    try:
        a, b = load(os.path.join(cell, f"{seg}.x3t0")), load(os.path.join(cell, f"{seg}.x3t1"))
    except OSError:
        return None
    out = [(b[t][0] - a.get(t, (0, ""))[0], t, b[t][1]) for t in b]
    out.sort(reverse=True)
    return out


def segment(cell, seg, kv):
    """-> dict with valid flag, reasons and metrics."""
    r = {"seg": seg, "valid": True, "why": []}

    def bad(why):
        r["valid"] = False
        r["why"].append(why)
    if kv is None:
        bad("no_seg_line")
        return r
    if kv.get("rc") != "0":
        bad(f"rc={kv.get('rc')}")
    if kv.get("touch_selftest") != "true":
        bad("touch_selftest")
    if kv.get("touch_events") != "0":
        bad(f"touch_events={kv.get('touch_events')}")
    for k, want in (("focus_pre", "true"), ("focus_post", "true"), ("awake_pre", "true"), ("awake_post", "true"),
                    ("keyguard_pre", "false"), ("keyguard_post", "false"), ("thermal_start", "0")):
        if kv.get(k) != want:
            bad(f"{k}={kv.get(k)}")
    x0, x1, m0, m1 = (num(kv.get(k)) for k in ("x3_t0", "x3_t1", "mono0", "mono1"))
    if None in (x0, x1, m0, m1) or m1 <= m0:
        bad("x3_or_window_null")
        return r
    r["window_s"] = m1 - m0
    r["x3_ticks"] = x1 - x0
    r["x3_cores"] = (x1 - x0) / CLK_TCK / (m1 - m0)
    u, s = num(kv.get("user_s")), num(kv.get("sys_s"))
    r["client_cpu_s"] = None if None in (u, s) else u + s
    th = thread_deltas(cell, seg)
    if th and r["x3_ticks"] > 0:
        r["x3_top_thread"] = {"tid": th[0][1], "comm": th[0][2], "share": round(th[0][0] / r["x3_ticks"], 3)}
    if seg == "idle":
        return r
    frames, ident, detail = client_result(cell, seg)
    r["identity"] = detail
    if not ident:
        bad("driver_identity")
    if not frames:
        bad("no_frames")
        return r
    r["frames"] = frames
    r["x3_ms_per_frame"] = r["x3_ticks"] * 1000.0 / CLK_TCK / frames
    if r["client_cpu_s"] is not None:
        r["client_ms_per_frame"] = r["client_cpu_s"] * 1000.0 / frames
    c = shim(cell, seg)
    if c is None:
        bad("present_count_missing")
        return r
    r["counts"] = {k: c.get(k, 0) for k in PRESENT_KEYS}
    r["modifiers"] = sorted({str(i.get("modifier")) for i in c.get("imports", [])})
    r["path"] = "NONE" if seg in OFFSCREEN else classify(r["counts"], frames)
    if seg in OFFSCREEN:
        r["req"] = req_hist(cell, seg)
    px = px_samples(cell, seg)
    r["px_client_colour"] = None if px is None else any(v in CLIENT_COLOURS for v in px)
    if r["path"] == "UNEXPLAINED" and not_presented(req_hist(cell, seg), px) is True:
        r["path"] = "NOT_PRESENTED"
    return r


def load_cell(cell):
    try:
        kvs = parse_seg_lines(os.path.join(cell, "cmd.out"))
    except OSError:
        kvs = {}
    return {seg: segment(cell, seg, kvs.get(seg)) for seg in ["idle"] + OFFSCREEN + ONSCREEN + [SW_CONTROL]}


def xdbg_witness(cell):
    """-> {api: count of 'DRI3: imported' X3 log lines inside that api's segment window} or None."""
    if not cell:
        return None
    try:
        kvs = parse_seg_lines(os.path.join(cell, "cmd.out"))
        log = open(os.path.join(cell, "raw-logcat.txt"), errors="replace").read().splitlines()
    except OSError:
        return None
    out = {}
    for seg in ("vk-l", "glx-l", "egl-l"):
        kv = kvs.get(seg)
        if not kv or "date0" not in kv or kv.get("rc") != "0":
            out[api_of(seg)] = None
            continue
        n, mods = 0, set()
        for line in log:
            if "DRI3: imported" not in line:
                continue
            t = line[:18].replace(" ", "T")
            if kv["date0"] <= t <= kv["date1"]:
                n += 1
                m = re.search(r"modifier (\d+)", line)
                if m:
                    mods.add(m.group(1))
        out[api_of(seg)] = {"lines": n, "modifiers": sorted(mods)}
    return out


def judge(reps, xdbg=None):
    cells = [load_cell(c) for c in reps]
    res = {"reps": reps, "segments": cells, "verdicts": {}}
    V = res["verdicts"]
    # J0 tool validity
    why = []
    for i, cell in enumerate(cells):
        for seg in ONSCREEN + [SW_CONTROL]:
            s = cell[seg]
            if s["valid"] and s.get("path") == "UNEXPLAINED":
                why.append(f"rep{i + 1}:{seg}:unexplained_frames")
        for seg in OFFSCREEN:
            s = cell[seg]
            if not s["valid"]:
                continue
            if any(s["counts"].get(k, 0) for k in HANDOFF_COUNTS):
                why.append(f"rep{i + 1}:{seg}:handoff_counted_without_presenting")
            h = s.get("req")
            if h is None:
                why.append(f"rep{i + 1}:{seg}:protocol_histogram_missing")
            elif any((r["ext"], r["op"]) in HANDOFF_EXT_REQS and r["n"] > 0 for r in h.get("requests", [])) or \
                    any(m["major"] in PRESENT_CORE_MAJORS and m["n"] > 0 for m in h.get("majors", [])):
                why.append(f"rep{i + 1}:{seg}:protocol_handoff_without_presenting")
    sw = [cell[SW_CONTROL] for cell in cells if cell[SW_CONTROL]["valid"]]
    if not sw:
        why.append("sw_control_never_valid")
    for s in sw:
        if s.get("path") not in NON_DRI3_PATHS:
            why.append(f"sw_control_path={s.get('path')}")
    V["J0"] = "GL_PRESENT_TOOL_VALID" if not why else "GL_PRESENT_TOOL_INVALID"
    res["J0_why"] = why
    tool_ok = not why
    # J1 path per api
    for api in ("vk", "egl", "glx"):
        paths = [cell[s]["path"] for cell in cells for s in ONSCREEN if api_of(s) == api and cell[s]["valid"]]
        if not tool_ok:
            V[f"J1_{api}"] = "PATH_INVALID"
        elif not paths:
            V[f"J1_{api}"] = "PATH_INVALID"
        elif len(set(paths)) == 1:
            V[f"J1_{api}"] = f"PATH_{paths[0]}"
        else:
            V[f"J1_{api}"] = "PATH_UNSTABLE"
    wit = xdbg_witness(xdbg)
    res["xdbg_witness"] = wit
    for api in ("vk", "egl", "glx"):
        w = None if wit is None else wit.get(api)
        if w is None or not V[f"J1_{api}"].startswith("PATH_") or V[f"J1_{api}"] in ("PATH_INVALID", "PATH_UNSTABLE"):
            V[f"J1x_{api}"] = "X_WITNESS_NULL"
        elif V[f"J1_{api}"] == "PATH_DRI3":
            V[f"J1x_{api}"] = "X_WITNESS_CONFIRMS" if w["lines"] >= 1 else "X_WITNESS_CONTRADICTS"
        else:
            V[f"J1x_{api}"] = "X_WITNESS_CONTRADICTS" if w["lines"] >= 1 else "X_WITNESS_CONSISTENT"
    # J2 X3 present cost, J3 scaling
    for api in JUDGED_APIS:
        rows, ctrl_fail = [], False
        for cell in cells:
            idle, off, on = cell["idle"], cell[f"{api}-off"], cell[f"{api}-l"]
            if not (idle["valid"] and off["valid"] and on["valid"]):
                continue
            if off["x3_cores"] > idle["x3_cores"] + OFF_CTRL_MARGIN:
                ctrl_fail = True
            rows.append((on["x3_cores"], max(off["x3_cores"], idle["x3_cores"], FLOOR_CORES)))
        res[f"J2_{api}_rows"] = rows
        if not tool_ok:
            V[f"J2_{api}"] = "INVALID_TOOL"
        elif ctrl_fail:
            V[f"J2_{api}"] = "CONTROL_FAILED"
        elif len(rows) < 2:
            V[f"J2_{api}"] = "INSUFFICIENT_REPS"
        elif all(on >= ON_MIN_CORES and on >= ON_OVER_OFF * base for on, base in rows):
            V[f"J2_{api}"] = "X3_PRESENT_COST"
        elif all(on < CHEAP_MAX for on, _ in rows):
            V[f"J2_{api}"] = "X3_PRESENT_CHEAP"
        else:
            V[f"J2_{api}"] = "INCONCLUSIVE"
        ratios = []
        for cell in cells:
            l, s = cell[f"{api}-l"], cell[f"{api}-s"]
            if l["valid"] and s["valid"] and s["x3_ms_per_frame"] > 0:
                ratios.append(l["x3_ms_per_frame"] / s["x3_ms_per_frame"])
        res[f"J3_{api}_ratios"] = ratios
        if not tool_ok:
            V[f"J3_{api}"] = "INVALID_TOOL"
        elif len(ratios) < 2:
            V[f"J3_{api}"] = "INSUFFICIENT_REPS"
        elif all(r >= SCALE_PER_PIXEL for r in ratios):
            V[f"J3_{api}"] = "X3_COST_PER_PIXEL"
        elif all(r <= SCALE_FIXED for r in ratios):
            V[f"J3_{api}"] = "X3_COST_PER_FRAME_FIXED"
        else:
            V[f"J3_{api}"] = "INCONCLUSIVE"
    if not tool_ok:
        V["STEP1"] = "GL_PRESENT_INVALID"
    elif all(V[f"J2_{a}"] == "X3_PRESENT_COST" and V[f"J3_{a}"] == "X3_COST_PER_PIXEL" for a in JUDGED_APIS):
        V["STEP1"] = "PRESENT_COST_IS_X3_PER_PIXEL_COPY"
    elif all(V[f"J2_{a}"] == "X3_PRESENT_CHEAP" for a in JUDGED_APIS):
        V["STEP1"] = "PRESENT_COST_NOT_IN_X3"
    elif any(V[f"J2_{a}"] in ("CONTROL_FAILED", "INSUFFICIENT_REPS", "INVALID_TOOL") for a in JUDGED_APIS):
        V["STEP1"] = "GL_PRESENT_INVALID"
    else:
        V["STEP1"] = "PRESENT_COST_MIXED"
    return res


# ----------------------------------------------------------------------------------------------- self-test
def _write_cell(d, over=None):
    """Synthetic main cell that should judge PRESENT_COST_IS_X3_PER_PIXEL_COPY; `over` mutates it."""
    over = over or {}
    os.makedirs(d, exist_ok=True)
    base = dict(rc="0", user_s="1.0", sys_s="1.0", mono0="100.0", mono1="120.0", date0="09-26T11:00:00.000",
                date1="09-26T11:00:20.000", touch_selftest="true", touch_events="0", thermal_start="0",
                focus_pre="true", awake_pre="true", keyguard_pre="false", focus_post="true", awake_post="true",
                keyguard_post="false")
    # X3 ticks over 20 s: idle 10 (0.005 core), off 20 (0.01), on-l 1600 (0.8), on-s 800 (0.4)
    plan = {"idle": 10, "vk-off": 20, "glx-off": 20, "vk-l": 1600, "vk-s": 800, "glx-l": 1600, "glx-s": 800,
            "egl-l": 30, "vk-l-sw": 1800}
    frames = {"vk-l": 2000, "vk-s": 4000, "glx-l": 2000, "glx-s": 4000, "egl-l": 4000, "vk-l-sw": 1500,
              "vk-off": 9000, "glx-off": 9000}
    lines = []
    for seg, ticks in plan.items():
        kv = dict(base, x3_t0="1000", x3_t1=str(1000 + over.get(("ticks", seg), ticks)))
        kv.update(over.get(("kv", seg), {}))
        lines.append("SEG " + seg + " " + " ".join(f"{k}={v}" for k, v in kv.items()))
        with open(os.path.join(d, f"{seg}.x3t0"), "w") as f:
            f.write("100 500 0 termux-x11gpu\n101 0 0 main\n")
        with open(os.path.join(d, f"{seg}.x3t1"), "w") as f:
            f.write(f"100 {500 + ticks - 5} 0 termux-x11gpu\n101 5 0 main\n")
        if seg == "idle":
            continue
        n = over.get(("frames", seg), frames[seg])
        with open(os.path.join(d, f"{seg}.log"), "w") as f:
            if seg.startswith("vk"):
                f.write('VK_DEVICE name="Adreno (TM) 840" driver="turnip Mesa driver" info="Mesa 26.0.6"\n')
                f.write(f"VK_PRESENT frames={n} secs=20.000 fps=100.0 size=1200x1200 offscreen=0 mode=immediate\n")
            else:
                f.write('GL_RENDERER "zink Vulkan 1.4(Adreno (TM) 840 (MESA_TURNIP))" GL_VERSION "x"\n')
                f.write(f"GL_PRESENT api={seg.split('-')[0]} frames={n} secs=20.000 fps=100.0 size=1200x1200\n")
        c = {k: 0 for k in PRESENT_KEYS}
        c["imports"] = []
        req = {"requests": [{"ext": "DRI3", "op": 0, "n": 1}], "majors": [{"major": 14, "n": n}]}
        if seg == "vk-off":    # GL-PRESENT-02 observation: offscreen Vulkan never connects to X
            req = {"requests": [], "majors": []}
        px = "0x00000000"
        if seg in ONSCREEN and seg != "egl-l":   # egl-l: the observed EGL/Zink behaviour, nothing handed over
            c.update(pixmap_from_buffers=3, present_pixmap=n, dri3_open=1)
            c["imports"] = [{"modifier": 0}] * 3
            req["requests"] += [{"ext": "Present", "op": 1, "n": n}, {"ext": "DRI3", "op": 7, "n": 3}]
            px = "0xffff8000"
        elif seg == SW_CONTROL:
            c.update(shm_create_pixmap=3, shm_attach=3, present_pixmap=n)
            req["requests"] += [{"ext": "Present", "op": 1, "n": n}, {"ext": "MIT-SHM", "op": 5, "n": 3}]
            px = "0xff0080ff"
        req.update(over.get(("req", seg), {}))
        px = over.get(("px", seg), px)
        if seg == "glx-off":   # GL-PRESENT-01 observation: kopper swapchain built at MakeCurrent, never presented
            c.update(dri3_open=1, pixmap_from_buffers=3)
            c["imports"] = [{"modifier": 0}] * 3
            req["requests"] += [{"ext": "DRI3", "op": 7, "n": 3}, {"ext": "Present", "op": 3, "n": 3}]
            c.update(over.get(("counts", seg), {}))
        if (seg in ONSCREEN or seg in OFFSCREEN or seg == SW_CONTROL) and not over.get(("no_req", seg)):
            with open(os.path.join(d, f"{seg}.req.jsonl"), "w") as f:
                f.write(json.dumps(req) + "\n")
            if px is not None:
                with open(os.path.join(d, f"{seg}.px.txt"), "w") as f:
                    f.write("".join(f"PX t={i * 150} {px}\n" for i in range(4)))
        c.update(over.get(("counts", seg), {}))
        if over.get(("no_shim", seg)):
            continue
        with open(os.path.join(d, f"{seg}.present.jsonl"), "w") as f:
            f.write(json.dumps(c) + "\n")
    with open(os.path.join(d, "cmd.out"), "w") as f:
        f.write("\n".join(lines) + "\nGL_PRESENT_DONE\n")


def _write_xdbg(d):
    """Synthetic xdbg cell: vk-l / glx-l each with one X3 'DRI3: imported' line inside its window, egl-l none."""
    os.makedirs(d, exist_ok=True)
    wins = {"vk-l": ("09-26T11:00:00.000", "09-26T11:00:10.000"), "glx-l": ("09-26T11:00:20.000", "09-26T11:00:30.000"),
            "egl-l": ("09-26T11:00:40.000", "09-26T11:00:50.000")}
    with open(os.path.join(d, "cmd.out"), "w") as f:
        for seg, (a, b) in wins.items():
            f.write(f"SEG {seg} rc=0 date0={a} date1={b}\n")
    with open(os.path.join(d, "raw-logcat.txt"), "w") as f:
        f.write("09-26 11:00:02.000  1  2 I LorieNative: DRI3: imported raw fd, modifier 0, 1200x1200 stride 4864\n")
        f.write("09-26 11:00:22.000  1  2 I LorieNative: DRI3: imported raw fd, modifier 0, 1200x1200 stride 4864\n")


def self_test():
    cases = [
        ("baseline", {}, {"J0": "GL_PRESENT_TOOL_VALID", "J1_vk": "PATH_DRI3", "J1_glx": "PATH_DRI3",
                          "J1_egl": "PATH_NOT_PRESENTED", "J2_vk": "X3_PRESENT_COST", "J3_vk": "X3_COST_PER_PIXEL",
                          "J2_glx": "X3_PRESENT_COST", "STEP1": "PRESENT_COST_IS_X3_PER_PIXEL_COPY"}),
        ("shim_blind_to_path", {("counts", "vk-l"): {"pixmap_from_buffers": 0, "present_pixmap": 0}},
         {"J0": "GL_PRESENT_TOOL_INVALID", "STEP1": "GL_PRESENT_INVALID"}),
        ("blind_but_pixels_show_frames", {("px", "egl-l"): "0xffff8000"}, {"J0": "GL_PRESENT_TOOL_INVALID"}),
        ("blind_protocol_shows_present", {("req", "egl-l"): {"requests": [{"ext": "Present", "op": 1, "n": 50}]}},
         {"J0": "GL_PRESENT_TOOL_INVALID"}),
        ("not_presented_needs_pixels", {("px", "egl-l"): None}, {"J0": "GL_PRESENT_TOOL_INVALID"}),
        ("sw_control_did_not_flip", {("counts", "vk-l-sw"): {"shm_create_pixmap": 0, "pixmap_from_buffers": 3}},
         {"J0": "GL_PRESENT_TOOL_INVALID"}),
        ("offscreen_counts_presents", {("counts", "glx-off"): {"present_pixmap": 50}}, {"J0": "GL_PRESENT_TOOL_INVALID"}),
        ("off_protocol_handoff", {("req", "vk-off"): {"requests": [{"ext": "Present", "op": 1, "n": 9}]}},
         {"J0": "GL_PRESENT_TOOL_INVALID"}),
        ("off_protocol_file_missing", {("no_req", "glx-off"): True}, {"J0": "GL_PRESENT_TOOL_INVALID"}),
        ("j1x_null_when_tool_invalid", {("counts", "vk-l"): {"pixmap_from_buffers": 0, "present_pixmap": 0}, "xdbg": 1},
         {"J0": "GL_PRESENT_TOOL_INVALID", "J1x_vk": "X_WITNESS_NULL", "J1x_glx": "X_WITNESS_NULL"}),
        ("xdbg_confirms", {"xdbg": 1}, {"J1x_vk": "X_WITNESS_CONFIRMS", "J1x_glx": "X_WITNESS_CONFIRMS",
                                        "J1x_egl": "X_WITNESS_CONSISTENT"}),
        ("off_control_busy", {("ticks", "vk-off"): 400}, {"J2_vk": "CONTROL_FAILED", "STEP1": "GL_PRESENT_INVALID"}),
        ("touch_invalidates_on", {("kv", "glx-l"): {"touch_events": "4"}}, {"J2_glx": "INSUFFICIENT_REPS"}),
        ("missing_shim_is_null", {("no_shim", "vk-s"): True}, {"J3_vk": "INSUFFICIENT_REPS"}),
        ("fixed_cost_not_pixels", {("ticks", "vk-s"): 1600, ("frames", "vk-s"): 2000}, {"J3_vk": "X3_COST_PER_FRAME_FIXED"}),
        ("cheap_x3", {("ticks", s): 40 for s in ("vk-l", "glx-l", "vk-s", "glx-s")},
         {"J2_vk": "X3_PRESENT_CHEAP", "J2_glx": "X3_PRESENT_CHEAP", "STEP1": "PRESENT_COST_NOT_IN_X3"}),
        ("wrong_driver", {}, {}),   # expectations set below
        ("shm_path_classified", {("counts", "glx-l"): {"pixmap_from_buffers": 0, "shm_create_pixmap": 3},
                                 ("counts", "glx-s"): {"pixmap_from_buffers": 0, "shm_create_pixmap": 3}},
         {"J1_glx": "PATH_SHM_PIXMAP_PRESENT"}),
    ]
    tmp = tempfile.mkdtemp(prefix="glp-selftest-")
    fails = 0
    try:
        for name, over, want in cases:
            dirs = []
            for rep in (1, 2):
                d = os.path.join(tmp, f"{name}-{rep}")
                _write_cell(d, over)
                dirs.append(d)
            if name == "wrong_driver":   # llvmpipe instead of zink on both reps -> glx-l invalid
                for d in dirs:
                    p = os.path.join(d, "glx-l.log")
                    open(p, "w").write('GL_RENDERER "llvmpipe (LLVM 20)" GL_VERSION "x"\nGL_PRESENT api=glx frames=2000 secs=20.000\n')
                want = {"J2_glx": "INSUFFICIENT_REPS"}
            xd = None
            if over.get("xdbg"):
                xd = os.path.join(tmp, f"{name}-xdbg")
                _write_xdbg(xd)
            got = judge(dirs, xd)["verdicts"]
            bad = {k: (got.get(k), v) for k, v in want.items() if got.get(k) != v}
            print(f"SELFTEST {name}: {'ok' if not bad else 'FAIL ' + str(bad)}")
            fails += bool(bad)
    finally:
        shutil.rmtree(tmp)
    print(f"SELFTEST_{'PASS' if not fails else 'FAIL'} cases={len(cases)} failed={fails}")
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rep", action="append", default=[])
    ap.add_argument("--xdbg")
    ap.add_argument("--json")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if len(a.rep) != 2:
        ap.error("exactly two --rep cells")
    res = judge(a.rep, a.xdbg)
    if a.json:
        json.dump(res, open(a.json, "w"), indent=1, default=str)
    for i, cell in enumerate(res["segments"]):
        for seg, s in cell.items():
            keep = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.items()
                    if k in ("valid", "why", "x3_cores", "x3_ms_per_frame", "frames", "path", "modifiers",
                             "client_ms_per_frame", "x3_top_thread", "px_client_colour")}
            print(f"rep{i + 1} {seg:8s} {json.dumps(keep)}")
    print("J0_why", res["J0_why"])
    print("xdbg_witness", json.dumps(res["xdbg_witness"]))
    for api in JUDGED_APIS:
        print(f"J2_{api}_rows", res[f"J2_{api}_rows"], f"J3_{api}_ratios", [round(r, 3) for r in res[f"J3_{api}_ratios"]])
    for k, v in res["verdicts"].items():
        print(f"VERDICT {k} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
