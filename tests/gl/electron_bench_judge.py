#!/usr/bin/env python3
"""ELECTRON-BENCH-01 judge (evidence/session/gl/ELECTRON-BENCH-01-FREEZE.md).

  electron_bench_judge.py <rep01 cell> <rep02 cell>
  electron_bench_judge.py --selftest        synthetic cases; must print SELFTEST_PASS before use

S = CPU seconds of (scroll + type) phases, launch session + X3. Per rep: V0 and V2 are the mean of that rep's runs.
noise = max(|S(V0)-S(V0')|/S(V0) in rep01, |S(V2)-S(V2')|/S(V2) in rep02)
CPU_BETTER        S(V2) <= S(V0) * (1 - max(0.20, 2*noise))
FRAMES_NOT_WORSE  scroll p95(V2) <= p95(V0) * 1.10  and  over50(V2) <= over50(V0) + 3
IDLE_OK           idle cores(V2) <= idle cores(V0) + 0.05
CORRECT           every V2 screenshot: dominant colour < 95 %, >= 16 colours; no GPU-process exit other than the end SIGTERM
"""
import json, os, re, sys, tempfile

CLK = 100
ORDER = {"01": [("r01-1-v0", "v0"), ("r01-2-v2", "v2"), ("r01-3-v0", "v0")],
         "02": [("r02-1-v2", "v2"), ("r02-2-v0", "v0"), ("r02-3-v2", "v2")]}


def run_lines(cell):
    runs = {}
    for line in open(os.path.join(cell, "cmd.out"), encoding="utf-8", errors="replace"):
        if line.startswith("RUN "):
            p = line.split()
            runs[p[1]] = dict(x.split("=", 1) for x in p[2:] if "=" in x)
    return runs


def screenshot_ok(path):
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        im.thumbnail((400, 800))
        cols = im.getcolors(maxcolors=1 << 20) or []
        total = sum(c for c, _ in cols)
        dom = max(c for c, _ in cols) / total if total else 1.0
        return dom < 0.95 and len(cols) >= 16, {"dominant": round(dom, 3), "colors": len(cols)}
    except Exception as e:  # unreadable screenshot -> not correct
        return False, {"error": str(e)}


def gpu_crash_count(stderr_path):
    n = 0
    try:
        for line in open(stderr_path, encoding="utf-8", errors="replace"):
            m = re.search(r"GPU process exited unexpectedly: exit_code=(\d+)", line)
            if m and m.group(1) != "15":   # 15 = the SIGTERM sent at the end of the run
                n += 1
    except FileNotFoundError:
        pass
    return n


def analyse(cell, name, var):
    """-> dict(valid, why[], S, idle_cores, frames, correct, correct_why)"""
    why = []
    r = run_lines(cell).get(name)
    if r is None:
        return {"valid": False, "why": [f"{name}: no RUN line"]}
    for k, want in (("touch_selftest", "true"), ("touch_events", "0"), ("thermal_start", "0"),
                    ("drive_rc", "0"), ("survivors_after_kill", "0")):
        if r.get(k) != want:
            why.append(f"{name}: {k}={r.get(k)}")
    try:
        j = json.load(open(os.path.join(cell, r.get("json", name + ".json"))))
    except Exception as e:
        return {"valid": False, "why": why + [f"{name}: json unreadable {e}"]}
    if j.get("errors"):
        why.append(f"{name}: driver errors {j['errors'][0][:120]}")
    ident = j.get("identity") or {}
    gl = ident.get("glRenderer") or ""
    if var == "v2" and not ("Adreno" in gl and "turnip" in gl.lower() and ident.get("gpu_compositing") == "enabled"):
        why.append(f"{name}: identity {gl!r} compositing={ident.get('gpu_compositing')}")
    if var == "v0" and gl != "Disabled":
        why.append(f"{name}: identity {gl!r} (want Disabled)")
    eb = j.get("editor_before") or {}
    if not eb.get("editor_found") or eb.get("focused") is not True:
        why.append(f"{name}: editor found={eb.get('editor_found')} focused={eb.get('focused')}")
    ph = j.get("phases") or {}
    for p in ("idle", "scroll", "type"):
        if p not in ph:
            why.append(f"{name}: phase {p} missing")
            continue
        for snap in ("start", "end"):
            st = (ph[p].get(snap) or {}).get("state") or {}
            if st.get("focus") is not True or st.get("awake") is not True or st.get("keyguard") is not False:
                why.append(f"{name}: {p}.{snap} state {st}")
            if (ph[p].get(snap) or {}).get("x3_ticks") is None:
                why.append(f"{name}: {p}.{snap} x3_ticks null")
    if "scroll" in ph and ph["scroll"].get("keys_acked") != 300:
        why.append(f"{name}: keys_acked={ph['scroll'].get('keys_acked')}")
    if "type" in ph and ph["type"].get("chars_acked") != 600:
        why.append(f"{name}: chars_acked={ph['type'].get('chars_acked')}")
    # the keys must have had an effect (dry-run 01: acked keys went to an unfocused element and did nothing)
    if "scroll" in ph and ph["scroll"].get("first_line_at_50") in (None, "", "1"):
        why.append(f"{name}: scroll had no effect (first line at key 50 = {ph['scroll'].get('first_line_at_50')!r})")
    if "type" in ph and ph["type"].get("typed_text_visible") is not True:
        why.append(f"{name}: typed text not visible")
    for p in ("scroll", "type"):
        if p in ph and not ((ph[p].get("frames") or {}).get("n") or 0) > 0:
            why.append(f"{name}: {p} rAF samples 0")
    if why:
        return {"valid": False, "why": why}

    def cpu(p):
        a, b = ph[p]["start"], ph[p]["end"]
        return ((b["session_ticks"] - a["session_ticks"]) + (b["x3_ticks"] - a["x3_ticks"])) / CLK

    idle_s = (ph["idle"]["end"]["t"] - ph["idle"]["start"]["t"]) / 1000
    out = {"valid": True, "why": [], "S": cpu("scroll") + cpu("type"), "idle_cores": cpu("idle") / idle_s,
           "p95": ph["scroll"]["frames"]["p95"], "over50": ph["scroll"]["frames"]["over50"],
           "typed_visible": ph["type"].get("typed_text_visible")}
    if var == "v2":
        ok, info = screenshot_ok(os.path.join(cell, name + ".png"))
        crashes = gpu_crash_count(os.path.join(cell, name + ".stderr"))
        out["correct"] = ok and crashes == 0
        out["correct_info"] = {**info, "gpu_crashes": crashes}
    return out


def judge(c1, c2, quiet=False):
    say = (lambda *a: None) if quiet else print
    res = {rep: [(n, v, analyse(cell, n, v)) for n, v in ORDER[rep]] for rep, cell in (("01", c1), ("02", c2))}
    bad = [w for rep in res.values() for _, _, a in rep if not a["valid"] for w in a["why"]]
    for rep, runs in res.items():
        for n, v, a in runs:
            if a["valid"]:
                say(f"  {n} {v}: S={a['S']:.2f}s idle={a['idle_cores']:.3f} cores p95={a['p95']:.1f}ms over50={a['over50']}"
                    + (f" correct={a['correct']} {a['correct_info']}" if v == "v2" else ""))
            else:
                say(f"  {n} {v}: INVALID " + "; ".join(a["why"]))
    if bad:
        say("VERDICT ELECTRON_INVALID")
        return "ELECTRON_INVALID"
    if not all(a["correct"] for rep in res.values() for _, v, a in rep if v == "v2"):
        say("VERDICT ELECTRON_GPU_FAIL")
        return "ELECTRON_GPU_FAIL"
    r1, r2 = [a for _, _, a in res["01"]], [a for _, _, a in res["02"]]
    noise = max(abs(r1[0]["S"] - r1[2]["S"]) / r1[0]["S"], abs(r2[0]["S"] - r2[2]["S"]) / r2[0]["S"])
    need = max(0.20, 2 * noise)
    say(f"  A/A noise={noise:.3f} -> required CPU reduction {need:.3f}")
    fails = []
    for rep, runs in res.items():
        v0 = [a for _, v, a in runs if v == "v0"]
        v2 = [a for _, v, a in runs if v == "v2"]
        m = lambda xs, k: sum(x[k] for x in xs) / len(xs)
        S0, S2 = m(v0, "S"), m(v2, "S")
        cpu_ok = S2 <= S0 * (1 - need)
        fr_ok = m(v2, "p95") <= m(v0, "p95") * 1.10 and m(v2, "over50") <= m(v0, "over50") + 3
        idle_ok = m(v2, "idle_cores") <= m(v0, "idle_cores") + 0.05
        say(f"  rep{rep}: S v0={S0:.2f} v2={S2:.2f} ({S2 / S0:.3f}x) CPU_BETTER={cpu_ok} | p95 v0={m(v0, 'p95'):.1f} "
            f"v2={m(v2, 'p95'):.1f} over50 v0={m(v0, 'over50'):.1f} v2={m(v2, 'over50'):.1f} FRAMES_NOT_WORSE={fr_ok} | "
            f"idle v0={m(v0, 'idle_cores'):.3f} v2={m(v2, 'idle_cores'):.3f} IDLE_OK={idle_ok}")
        fails += [f"rep{rep}:{k}" for k, ok in (("CPU_BETTER", cpu_ok), ("FRAMES_NOT_WORSE", fr_ok), ("IDLE_OK", idle_ok)) if not ok]
    v = "ELECTRON_GPU_BENEFIT" if not fails else "ELECTRON_GPU_NO_BENEFIT"
    say(f"VERDICT {v}" + (f" ({', '.join(fails)})" if fails else ""))
    return v


# ------------------------------------------------------------------ synthetic self-test
def fake_run(cell, name, var, S, idle, p95, over50, touch="0", ident=None, black=False, crash=False, noeffect=False):
    from PIL import Image
    t0 = 1_000_000
    def snap(t, sess, x3):
        return {"t": t, "session_ticks": sess, "x3_ticks": x3, "act_ticks": 0,
                "state": {"focus": True, "awake": True, "keyguard": False}}
    it = int(idle * 60 * CLK)          # idle ticks over 60 s
    half = int(S * CLK / 2)
    ph = {"idle": {"start": snap(t0, 0, 0), "end": snap(t0 + 60000, it, 0)},
          "scroll": {"start": snap(t0 + 61000, it, 0), "end": snap(t0 + 91000, it + half, 0), "keys_acked": 300, "first_line_at_50": "1201",
                     "frames": {"n": 400, "p50": 16.7, "p95": p95, "over50": over50}},
          "type": {"start": snap(t0 + 92000, it + half, 0), "end": snap(t0 + 122000, it + 2 * half, 0), "chars_acked": 600,
                   "frames": {"n": 400, "p50": 16.7, "p95": p95, "over50": over50}, "typed_text_visible": True}}
    gl = ident if ident is not None else ("ANGLE (Qualcomm, Vulkan 1.4.335 (Adreno (TM) 840), turnip Mesa driver)" if var == "v2" else "Disabled")
    j = {"identity": {"glRenderer": gl, "gpu_compositing": "enabled" if var == "v2" else "disabled_software"},
         "editor_before": {"editor_found": True, "focused": True}, "phases": ph, "errors": []}
    if noeffect:
        ph["scroll"]["first_line_at_50"] = "1"
    json.dump(j, open(os.path.join(cell, name + ".json"), "w"))
    im = Image.new("RGB", (120, 240), (0, 0, 0))
    if not black:
        for x in range(120):
            for y in range(0, 240, 3):
                im.putpixel((x, y), (x * 2, y, (x * y) % 255))
    im.save(os.path.join(cell, name + ".png"))
    open(os.path.join(cell, name + ".stderr"), "w").write(
        ("[1:2:ERROR:gpu_process_host.cc] GPU process exited unexpectedly: exit_code=139\n" if crash else "")
        + "[1:2:ERROR:gpu_process_host.cc] GPU process exited unexpectedly: exit_code=15\n")
    return (f"RUN {name} variant={var} sid=1 drive_rc=0 json={name}.json touch_selftest=true touch_events={touch} "
            f"thermal_start=0 thermal_waited_s=0 thermal_end=0 survivors_after_kill=0")


def fake_cells(root, tag, v0, v2, v0b=None, v2b=None, **over):
    cells = []
    for rep in ("01", "02"):
        cell = os.path.join(root, f"{tag}-{rep}"); os.makedirs(cell)
        lines = []
        for i, (n, var) in enumerate(ORDER[rep]):
            base = dict(v0 if var == "v0" else v2)
            if i == 2:   # the repeated run of the rep
                base = dict((v0b or v0) if var == "v0" else (v2b or v2))
            kw = {k: v for k, v in over.items() if k in ("touch", "ident", "black", "crash", "noeffect")} if var == over.get("on", "v2") else {}
            lines.append(fake_run(cell, n, var, **base, **kw))
        open(os.path.join(cell, "cmd.out"), "w").write("\n".join(lines) + "\n")
        cells.append(cell)
    return cells


def selftest():
    V0 = dict(S=40.0, idle=0.10, p95=20.0, over50=2)
    GOOD = dict(S=20.0, idle=0.12, p95=18.0, over50=2)
    cases = [
        ("a_equal_must_be_red", dict(v0=V0, v2=V0), "ELECTRON_GPU_NO_BENEFIT"),
        ("b_clear_benefit", dict(v0=V0, v2=GOOD), "ELECTRON_GPU_BENEFIT"),
        ("c_black_screen", dict(v0=V0, v2=GOOD, black=True), "ELECTRON_GPU_FAIL"),
        ("d_gpu_crash", dict(v0=V0, v2=GOOD, crash=True), "ELECTRON_GPU_FAIL"),
        ("e_wrong_identity", dict(v0=V0, v2=GOOD, ident="ANGLE (Mesa, llvmpipe)"), "ELECTRON_INVALID"),
        ("f_touch", dict(v0=V0, v2=GOOD, touch="5"), "ELECTRON_INVALID"),
        ("j_keys_had_no_effect", dict(v0=V0, v2=GOOD, noeffect=True), "ELECTRON_INVALID"),
        # 25 % less CPU, but V0 itself varied 20 % between its two runs -> need 40 %
        ("g_noise_raises_bar", dict(v0=V0, v2=dict(GOOD, S=30.0), v0b=dict(V0, S=32.0)), "ELECTRON_GPU_NO_BENEFIT"),
        ("h_idle_spin", dict(v0=V0, v2=dict(GOOD, idle=0.40)), "ELECTRON_GPU_NO_BENEFIT"),
        ("i_frames_worse", dict(v0=V0, v2=dict(GOOD, p95=30.0)), "ELECTRON_GPU_NO_BENEFIT"),
    ]
    ok = True
    with tempfile.TemporaryDirectory() as root:
        for name, kw, want in cases:
            c1, c2 = fake_cells(root, name, **kw)
            got = judge(c1, c2, quiet=True)
            good = got == want; ok &= good
            print(f"CASE {name} want {want} got {got} {'OK' if good else 'WRONG'}")
    print("SELFTEST_PASS" if ok else "SELFTEST_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["--selftest"]:
        sys.exit(selftest())
    if len(a) == 2:
        judge(a[0], a[1]); sys.exit(0)
    print(__doc__); sys.exit(2)
