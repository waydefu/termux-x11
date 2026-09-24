#!/usr/bin/env bash
# GL / Vulkan renderer feasibility on the experimental X (:3). NOT a benchmark: only the two demo
# programs that are already installed (glxgears, vkcube). Answers: which renderer does a GL app get,
# does Zink-on-Turnip (GPU) run at all here, and how does it compare with llvmpipe / lavapipe (CPU).
#   DISPLAY=:3 bash gl_feasibility.sh <evidence dir>
# Every client is a Termux-native binary (Mesa 26.0.6 with Turnip's KGSL backend) started from PRoot.
# CPU time of each client comes from python's resource.getrusage(RUSAGE_CHILDREN).
set -uo pipefail
OUT=${1:?evidence dir}
TB=/data/data/com.termux/files/usr/bin
TU_ICD=/data/data/com.termux/files/usr/share/vulkan/icd.d/freedreno_icd.aarch64.json
LVP_ICD=/data/data/com.termux/files/usr/share/vulkan/icd.d/lvp_icd.aarch64.json
run() {   # name secs cmd...  -> RUN <name> wall_s user_s sys_s rc   (output in $OUT/<name>.log)
  local name=$1 secs=$2; shift 2
  python3 - "$OUT/$name.log" "$secs" "$@" <<'PY'
import resource, subprocess, sys, time
log, secs, cmd = sys.argv[1], float(sys.argv[2]), sys.argv[3:]
t0 = time.monotonic()
with open(log, "w") as f:
    try:
        rc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, timeout=secs).returncode
    except subprocess.TimeoutExpired:
        rc = "timeout"
r = resource.getrusage(resource.RUSAGE_CHILDREN)
print(f"RUN {sys.argv[1].rsplit('/',1)[1][:-4]} wall_s={time.monotonic()-t0:.2f} user_s={r.ru_utime:.2f} sys_s={r.ru_stime:.2f} rc={rc}")
PY
}
export vblank_mode=0
# PRoot's Ubuntu Mesa installs an implicit layer manifest (VkLayer_MESA_device_select.json) that the
# Termux loader finds but cannot dlopen; Zink then fails vkCreateInstance with LAYER_NOT_PRESENT
# (gl-feas-g-01). Disable implicit layers for every Vulkan-backed run.
NOLAYER="VK_LOADER_LAYERS_DISABLE=*"
echo "== renderer strings"
env LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe $TB/glxinfo -B > "$OUT/glxinfo-llvmpipe.txt" 2>&1
env $NOLAYER MESA_LOADER_DRIVER_OVERRIDE=zink GALLIUM_DRIVER=zink VK_ICD_FILENAMES=$TU_ICD $TB/glxinfo -B > "$OUT/glxinfo-zink.txt" 2>&1
env $NOLAYER VK_ICD_FILENAMES=$TU_ICD $TB/glxinfo -B > "$OUT/glxinfo-default.txt" 2>&1
for f in llvmpipe zink default; do echo "$f: $(grep -m1 'OpenGL renderer' "$OUT/glxinfo-$f.txt")"; done
echo "== glxgears 12 s (vblank_mode=0)"
run gears-llvmpipe 12 env LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe $TB/glxgears
run gears-zink 12 env $NOLAYER MESA_LOADER_DRIVER_OVERRIDE=zink GALLIUM_DRIVER=zink VK_ICD_FILENAMES=$TU_ICD $TB/glxgears
for f in llvmpipe zink; do echo "gears-$f: $(grep -E 'frames in' "$OUT/gears-$f.log" | tail -2 | tr '\n' ' ')"; done
echo "== vkcube 600 frames"
run vkcube-turnip 60 env $NOLAYER VK_ICD_FILENAMES=$TU_ICD $TB/vkcube --c 600
run vkcube-lavapipe 120 env $NOLAYER VK_ICD_FILENAMES=$LVP_ICD $TB/vkcube --c 600
for f in turnip lavapipe; do echo "vkcube-$f: $(tail -2 "$OUT/vkcube-$f.log" | tr '\n' ' ' | cut -c1-160)"; done
echo GL_FEASIBILITY_DONE
