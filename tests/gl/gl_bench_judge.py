#!/usr/bin/env python3
"""GL-BENCH-01 judge (evidence/session/gl/GL-BENCH-01-FREEZE.md).

  gl_bench_judge.py <rep01 cell> <rep02 cell>      verdicts for GL and VK
  gl_bench_judge.py --aa <rep01 cell> <rep02 cell>  A/A control: CPU run against CPU run, must be NO_BENEFIT
  gl_bench_judge.py --selftest                      synthetic cases; must print SELFTEST_PASS before use

Thresholds (frozen): FASTER score_gpu >= 1.5 * score_cpu; EFFICIENT cpu_ms_per_frame_gpu <= 0.5 * cpu.
Both must hold in both reps. Any invalid segment in either rep -> <API>_INVALID (no information).
"""
import os, re, sys, tempfile

FASTER_X = 1.5
EFFICIENT_X = 0.5
MIN_COMMON = 0.8
CLK_TCK = 100
PAIRS = {"GL": ("gl-gpu", "gl-cpu"), "VK": ("vk-gpu", "vk-cpu")}
IDENT = {  # segment -> (regex for the identity line, substrings that must all appear)
    "gl-cpu": (r"GL_RENDERER:\s*(.*)", ["llvmpipe"]),
    "gl-gpu": (r"GL_RENDERER:\s*(.*)", ["zink", "Adreno"]),
    "gl-gpu-off": (r"GL_RENDERER:\s*(.*)", ["zink", "Adreno"]),
    "vk-cpu": (r"Device Name:\s*(.*)", ["llvmpipe"]),
    "vk-gpu": (r"Device Name:\s*(.*)", ["Adreno"]),
    "vk-gpu-hl": (r"Device Name:\s*(.*)", ["Adreno"]),
}
SCENE = re.compile(r"^\[(?P<scene>[^\]]+)\] (?P<opts>.*?): (?:FPS: (?P<fps>[0-9.]+) FrameTime: [0-9.]+ ms|(?P<uns>Unsupported))\s*$")
DUR = re.compile(r"(?:^|:)duration=([0-9.]+)(?::|$)")


def num(v):
    if v in (None, "", "null", "timeout"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def read_segs(cell):
    segs = {}
    with open(os.path.join(cell, "cmd.out"), encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("SEG "):
                parts = line.split()
                segs[parts[1]] = dict(p.split("=", 1) for p in parts[2:] if "=" in p)
    return segs


def parse_log(path):
    ident, scenes, bad = None, {}, []
    text = open(path, encoding="utf-8", errors="replace").read().replace("\r", "\n")
    for line in text.split("\n"):
        line = line.rstrip()
        if ident is None:
            for rx in (r"GL_RENDERER:\s*(.*)", r"Device Name:\s*(.*)"):
                m = re.search(rx, line)
                if m:
                    ident = m.group(1).strip()
        m = SCENE.match(line.strip())
        if m:
            key = f"[{m['scene']}] {m['opts']}"
            if m["uns"]:
                scenes[key] = None
            else:
                d = DUR.search(m["opts"])
                scenes[key] = (float(m["fps"]), float(d.group(1)) if d else None)
        elif line.strip().startswith("[") and ("FPS" in line or "Unsupported" in line):
            bad.append(line.strip())
    return ident, scenes, bad


def segment(cell, name, check_ident=True):
    """-> dict with metrics and a list of invalid reasons"""
    why = []
    s = read_segs(cell).get(name)
    if s is None:
        return {"why": [f"{name}: no SEG line"]}
    log = os.path.join(cell, f"{name}.log")
    ident, scenes, bad = parse_log(log) if os.path.exists(log) else (None, {}, [])
    if s.get("rc") != "0":
        why.append(f"{name}: rc={s.get('rc')}")
    if check_ident:
        need = IDENT[name][1]
        if ident is None or not all(n in ident for n in need):
            why.append(f"{name}: identity '{ident}' lacks {need}")
    if s.get("thermal_start") != "0":
        why.append(f"{name}: thermal_start={s.get('thermal_start')}")
    if s.get("touch_selftest") != "true":
        why.append(f"{name}: touch recorder self-test {s.get('touch_selftest')}")
    if s.get("touch_events") != "0":
        why.append(f"{name}: touch_events={s.get('touch_events')}")
    for k, want in (("focus_pre", "true"), ("focus_post", "true"), ("awake_pre", "true"),
                    ("awake_post", "true"), ("keyguard_pre", "false"), ("keyguard_post", "false")):
        if s.get(k) != want:
            why.append(f"{name}: {k}={s.get(k)}")
    if bad:
        why.append(f"{name}: {len(bad)} unparsed scene lines")
    user, sys_, x0, x1 = (num(s.get(k)) for k in ("user_s", "sys_s", "x3_t0", "x3_t1"))
    if None in (user, sys_, x0, x1):
        why.append(f"{name}: cpu field null")
        cpu = None
    else:
        cpu = user + sys_ + (x1 - x0) / CLK_TCK
    done = {k: v for k, v in scenes.items() if v is not None}
    if not done:
        why.append(f"{name}: no completed scene")
    if any(v[1] is None for v in done.values()):
        why.append(f"{name}: scene without printed duration")
        frames = None
    else:
        frames = sum(fps * dur for fps, dur in done.values()) if done else None
    cpf = cpu * 1000.0 / frames if (cpu is not None and frames) else None
    a0, a1 = num(s.get("act_t0")), num(s.get("act_t1"))
    return {"why": why, "scenes": scenes, "done": done, "cpu_s": cpu, "frames": frames, "cpf": cpf,
            "ident": ident, "act_cpu_s": (a1 - a0) / CLK_TCK if None not in (a0, a1) else None,
            "wall_s": num(s.get("wall_s"))}


def compare(gpu, cpu):
    """one rep, one API -> (result dict)"""
    why = gpu["why"] + cpu["why"]
    if why:
        return {"valid": False, "why": why}
    common = sorted(set(gpu["done"]) & set(cpu["done"]))
    listed = max(len(gpu["scenes"]), len(cpu["scenes"]))
    only = sorted(set(gpu["done"]) ^ set(cpu["done"]))
    if listed == 0 or len(common) < MIN_COMMON * listed:
        return {"valid": False, "why": [f"common scenes {len(common)}/{listed} < {MIN_COMMON:.0%}"]}
    sg = sum(gpu["done"][k][0] for k in common) / len(common)
    sc = sum(cpu["done"][k][0] for k in common) / len(common)
    faster = sg >= FASTER_X * sc
    efficient = gpu["cpf"] <= EFFICIENT_X * cpu["cpf"]
    return {"valid": True, "score_gpu": sg, "score_cpu": sc, "cpf_gpu": gpu["cpf"], "cpf_cpu": cpu["cpf"],
            "faster": faster, "efficient": efficient, "common": len(common), "listed": listed, "only_one_side": only,
            "cpu_s_gpu": gpu["cpu_s"], "cpu_s_cpu": cpu["cpu_s"], "act_gpu": gpu["act_cpu_s"], "act_cpu": cpu["act_cpu_s"]}


def verdict(api, reps):
    """reps: list of compare() results for rep01, rep02"""
    if not all(r["valid"] for r in reps):
        return f"{api}_INVALID"
    if all(r["faster"] and r["efficient"] for r in reps):
        return f"{api}_GPU_BENEFIT"
    return f"{api}_GPU_NO_BENEFIT"


def report(api, reps):
    v = verdict(api, reps)
    print(f"== {api}: {v}")
    for i, r in enumerate(reps, 1):
        if not r["valid"]:
            print(f"  rep0{i} INVALID: " + "; ".join(r["why"]))
            continue
        print(f"  rep0{i} score gpu {r['score_gpu']:.1f} cpu {r['score_cpu']:.1f} (x{r['score_gpu']/r['score_cpu']:.2f}, "
              f"need >= {FASTER_X}) FASTER={r['faster']} | cpu ms/frame gpu {r['cpf_gpu']:.3f} cpu {r['cpf_cpu']:.3f} "
              f"(x{r['cpf_gpu']/r['cpf_cpu']:.2f}, need <= {EFFICIENT_X}) EFFICIENT={r['efficient']} | "
              f"common {r['common']}/{r['listed']}")
        if r["only_one_side"]:
            print(f"    completed on one side only: {r['only_one_side']}")
        print(f"    describe: client+X3 cpu_s gpu {r['cpu_s_gpu']:.1f} cpu {r['cpu_s_cpu']:.1f}; "
              f"activity cpu_s gpu {r['act_gpu']} cpu {r['act_cpu']}")
    return v


def describe(cell, rep):
    for name in ("gl-gpu-off", "vk-gpu-hl"):
        s = segment(cell, name)
        done = s.get("done") or {}
        score = sum(v[0] for v in done.values()) / len(done) if done else None
        state = "valid" if not s["why"] else "INVALID(" + "; ".join(s["why"]) + ")"
        print(f"  describe rep{rep} {name}: score {score} cpu_ms/frame {s.get('cpf')} {state}")


def judge(c1, c2, aa=False):
    out = {}
    for api, (g, c) in PAIRS.items():
        if aa:  # CPU vs CPU across reps; identity check off because the "gpu" side is llvmpipe by design
            reps = [compare(segment(c1, c, False), segment(c2, c, False)),
                    compare(segment(c2, c, False), segment(c1, c, False))]
            api_name = api + "_AA"
        else:
            reps = [compare(segment(cell, g), segment(cell, c)) for cell in (c1, c2)]
            api_name = api
        out[api] = report(api_name, reps)
    if not aa:
        for rep, cell in (("01", c1), ("02", c2)):
            describe(cell, rep)
    return out


# ---------------------------------------------------------------- synthetic self-test
GL_SCENES = [("build", "use-vbo=false"), ("texture", "texture-filter=nearest"), ("shading", "shading=gouraud"),
             ("bump", "bump-render=high-poly"), ("effect2d", "kernel=0,1,0;1,-4,1;0,1,0;")]
VK_SCENES = [("vertex", "device-local=true"), ("texture", "anisotropy=0"), ("shading", "shading=gouraud"),
             ("cube", ""), ("clear", "color=0.8,0.4,0.2,1.0")]


def fake_log(path, ident_line, scenes, fps):
    with open(path, "w") as f:
        f.write("=" * 55 + "\n" + ident_line + "\n" + "=" * 55 + "\n")
        for sc, opt in scenes:
            opts = f"duration=10.0:{opt}" if opt else "duration=10.0"
            f.write(f"[{sc}] {opts}: FPS: {fps} FrameTime: {1000.0/fps:.3f} ms\n")


def fake_cell(root, name, gl_gpu_ident, gpu_fps, gpu_cpu, touch="0"):
    cell = os.path.join(root, name)
    os.makedirs(cell)
    segs = []
    spec = {"gl-cpu": ("    GL_RENDERER:    llvmpipe (LLVM 21.1.8, 128 bits)", GL_SCENES, 100, 40.0),
            "gl-gpu": (gl_gpu_ident, GL_SCENES, gpu_fps, gpu_cpu),
            "gl-gpu-off": (gl_gpu_ident, GL_SCENES, gpu_fps, gpu_cpu),
            "vk-cpu": ("    Device Name:    llvmpipe (LLVM 21.1.8, 128 bits)", VK_SCENES, 100, 40.0),
            "vk-gpu": ("    Device Name:    Adreno (TM) 840", VK_SCENES, gpu_fps, gpu_cpu),
            "vk-gpu-hl": ("    Device Name:    Adreno (TM) 840", VK_SCENES, gpu_fps, gpu_cpu)}
    for seg, (ident, scenes, fps, cpu) in spec.items():
        fake_log(os.path.join(cell, f"{seg}.log"), ident, scenes, fps)
        t = touch if seg == "gl-gpu" else "0"
        segs.append(f"SEG {seg} wall_s=60 user_s={cpu/2} sys_s={cpu/4} rc=0 x3_t0=0 x3_t1={int(cpu*25)} act_t0=0 act_t1=10 "
                    f"touch_selftest=true touch_events={t} thermal_start=0 thermal_waited_s=0 thermal_end=0 "
                    f"focus_pre=true awake_pre=true keyguard_pre=false focus_post=true awake_post=true keyguard_post=false")
    open(os.path.join(cell, "cmd.out"), "w").write("\n".join(segs) + "\nGL_BENCH_DONE\n")
    return cell


def selftest():
    zink = "    GL_RENDERER:    zink Vulkan 1.4(Adreno (TM) 840 (MESA_TURNIP))"
    cases = [  # name, cell kwargs, expected GL verdict, expected VK verdict
        ("a_equal_must_be_red", dict(gl_gpu_ident=zink, gpu_fps=100, gpu_cpu=40.0), "GL_GPU_NO_BENEFIT", "VK_GPU_NO_BENEFIT"),
        ("b_wrong_driver_invalid", dict(gl_gpu_ident="    GL_RENDERER:    llvmpipe (LLVM 21.1.8, 128 bits)", gpu_fps=300, gpu_cpu=10.0),
         "GL_INVALID", "VK_GPU_BENEFIT"),
        ("c_clear_benefit", dict(gl_gpu_ident=zink, gpu_fps=300, gpu_cpu=10.0), "GL_GPU_BENEFIT", "VK_GPU_BENEFIT"),
        ("d_touch_invalid", dict(gl_gpu_ident=zink, gpu_fps=300, gpu_cpu=10.0, touch="3"), "GL_INVALID", "VK_GPU_BENEFIT"),
        ("e_touch_null_invalid", dict(gl_gpu_ident=zink, gpu_fps=300, gpu_cpu=10.0, touch="null"), "GL_INVALID", "VK_GPU_BENEFIT"),
        # 1.4x faster (< 1.5) with far less CPU: must not pass on EFFICIENT alone
        ("f_faster_below_threshold", dict(gl_gpu_ident=zink, gpu_fps=140, gpu_cpu=5.0), "GL_GPU_NO_BENEFIT", "VK_GPU_NO_BENEFIT"),
    ]
    ok = True
    with tempfile.TemporaryDirectory() as root:
        for name, kw, want_gl, want_vk in cases:
            c1 = fake_cell(root, name + "-01", **kw)
            c2 = fake_cell(root, name + "-02", **kw)
            print(f"--- case {name}")
            got = judge(c1, c2)
            good = got["GL"] == want_gl and got["VK"] == want_vk
            ok &= good
            print(f"CASE {name} want {want_gl}/{want_vk} got {got['GL']}/{got['VK']} {'OK' if good else 'WRONG'}")
        # A/A on a clear-benefit dataset still has to come out NO_BENEFIT (CPU side against CPU side)
        c1 = fake_cell(root, "aa-01", gl_gpu_ident=zink, gpu_fps=300, gpu_cpu=10.0)
        c2 = fake_cell(root, "aa-02", gl_gpu_ident=zink, gpu_fps=300, gpu_cpu=10.0)
        print("--- case g_aa_control")
        got = judge(c1, c2, aa=True)
        good = got["GL"] == "GL_GPU_NO_BENEFIT".replace("GL_", "GL_AA_", 1) and got["VK"] == "VK_AA_GPU_NO_BENEFIT"
        ok &= good
        print(f"CASE g_aa_control want GL_AA_GPU_NO_BENEFIT/VK_AA_GPU_NO_BENEFIT got {got['GL']}/{got['VK']} {'OK' if good else 'WRONG'}")
    print("SELFTEST_PASS" if ok else "SELFTEST_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["--selftest"]:
        sys.exit(selftest())
    if len(a) == 3 and a[0] == "--aa":
        judge(a[1], a[2], aa=True)
    elif len(a) == 2:
        judge(a[0], a[1])
    else:
        print(__doc__)
        sys.exit(2)
