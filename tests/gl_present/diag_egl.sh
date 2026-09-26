#!/usr/bin/env bash
# DIAGNOSTIC cell script (NOT judged; GL-PRESENT-01 tool repair after dry-01-r2): what does the EGL/Zink
# client send to :3, and do its frames reach the X framebuffer? Run by run-gl-bench.sh KIND=cmd MODE=C.
#   For egl-l, glx-l, vk-l (5 s, 1200x1200): present_count.so + req_count.so (libc-level request histogram)
#   and px_probe samples of root pixel (600,600) at ~2 s into the run. Also the :3 extension major opcodes.
set -uo pipefail
OUT=${1:?evidence dir}
TOOLS=/root/projects/GPU加速/src/f8-ahb-exa-async/tests/gl_present/build
GPU=/usr/local/bin/f8-gpu
( cd "$TOOLS" && sha256sum present_count.so req_count.so px_probe vk_present gl_present_egl gl_present_glx ) > "$OUT/diag-tools.sha256"
xdpyinfo -queryExtensions > "$OUT/xdpyinfo-ext.txt" 2>&1
"$TOOLS/px_probe" 600 600 1 0 > "$OUT/px-before.txt" 2>&1
for c in "egl-l gl_present_egl" "glx-l gl_present_glx" "vk-l vk_present"; do
  set -- $c
  $GPU env LD_PRELOAD="$TOOLS/present_count.so $TOOLS/req_count.so" PRESENT_COUNT_OUT="$OUT/$1.present.jsonl" \
    REQ_COUNT_OUT="$OUT/$1.req.jsonl" vblank_mode=0 "$TOOLS/$2" --size 1200x1200 --seconds 5 > "$OUT/$1.log" 2>&1 &
  CP=$!
  sleep 2.5
  "$TOOLS/px_probe" 600 600 4 150 > "$OUT/$1.px.txt" 2>&1
  wait "$CP"; echo "DIAG $1 rc=$? $(tail -1 "$OUT/$1.log" | cut -c1-120) px=$(awk '{print $3}' "$OUT/$1.px.txt" | tr '\n' ',')"
  sleep 3
done
"$TOOLS/px_probe" 600 600 1 0 > "$OUT/px-after.txt" 2>&1
echo "DIAG_DONE"
