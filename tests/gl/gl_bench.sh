#!/usr/bin/env bash
# GL-BENCH-01 cell script (evidence/session/gl/GL-BENCH-01-FREEZE.md): glmark2 / vkmark with the GPU
# drivers (Zink+Turnip, Turnip) against the CPU drivers (llvmpipe, lavapipe) on the experimental X (:3).
#   REP=01|02 SERIAL=<5038-lane serial> DISPLAY=:3 bash gl_bench.sh <cell evidence dir>
# Run by run-gl-bench.sh KIND=cmd, which has already written x3-pid.txt / activity-pid.txt there.
# Every segment records, besides the tool's own output:
#   client CPU      getrusage(RUSAGE_CHILDREN) of a fresh python wrapper
#   X3 CPU          /proc/<x3>/stat utime+stime ticks before/after
#   Activity CPU    same via adb (descriptive only)
#   thermal         status must be 0 before start (poll 10 s, max 300 s, and >= 30 s after the previous segment)
#   focus / screen  experimental MainActivity focused, awake, no keyguard - before and after
#   touches         getevent on the touchscreen for the whole segment; any EV_KEY/EV_ABS event invalidates it.
#                   Self-test: the recorder must still be running after 2 s with no open/permission error,
#                   and still be running when stopped; otherwise the touch count is null (not 0).
#                   Injecting a harmless EV_SYN was the first idea but `sendevent` is denied to adb shell
#                   (SELinux, 2026-09-24). The detector's positive control is a real touch capture instead:
#                   evidence/session/gl/touch-detector-control-20260924.txt (294 EV_KEY/EV_ABS lines in 3 s).
# One 'SEG' line per segment goes to stdout; gl_bench_judge.py reads those plus the logs.
set -uo pipefail
OUT=${1:?evidence dir}
REP=${REP:?REP=01|02}
: "${SERIAL:?SERIAL}"
TB=/data/data/com.termux/files/usr/bin
ADBB="$TB/adb -P 5038 -s $SERIAL"
TU_ICD=/data/data/com.termux/files/usr/share/vulkan/icd.d/freedreno_icd.aarch64.json
LVP_ICD=/data/data/com.termux/files/usr/share/vulkan/icd.d/lvp_icd.aarch64.json
TOUCH_DEV=/dev/input/event7   # focaltech_ts (getevent -pl, 2026-09-24)
EXP_PKG=com.waydefu.x11gpu
X3=$(sed -n 's/^x3_pid=//p' "$OUT/x3-pid.txt")
ACT=$(sed -n 's/^activity_pid=//p' "$OUT/activity-pid.txt")
[ -n "$X3" ] && [ -d "/proc/$X3" ] || { echo "GL_BENCH_ABORT no_x3"; exit 4; }
export vblank_mode=0
# PRoot's Ubuntu Mesa ships an implicit layer manifest the Termux loader cannot load (GL-FEASIBILITY-01).
NOLAYER="VK_LOADER_LAYERS_DISABLE=*"
GL_CPU_ENV="LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe"
GL_GPU_ENV="$NOLAYER MESA_LOADER_DRIVER_OVERRIDE=zink GALLIUM_DRIVER=zink VK_ICD_FILENAMES=$TU_ICD"

x3_ticks() { awk '{print $14+$15}' "/proc/$X3/stat" 2>/dev/null || echo null; }
act_ticks() { [ -n "$ACT" ] || { echo null; return; }
  $ADBB shell "cat /proc/$ACT/stat" </dev/null 2>/dev/null | tr -d '\r' | awk 'NF>15{print $14+$15; f=1} END{if(!f) print "null"}'; }
thermal() { $ADBB shell dumpsys thermalservice </dev/null 2>/dev/null | tr -d '\r' | sed -n 's/^Thermal Status: *//p' | head -1; }
state() {   # focus_ok awake keyguard  (true/false/null)
  local f w k
  f=$($ADBB shell dumpsys window </dev/null 2>/dev/null | tr -d '\r' | grep -m1 'mCurrentFocus=' || true)
  w=$($ADBB shell dumpsys power </dev/null 2>/dev/null | tr -d '\r' | grep -m1 'mWakefulness=' | sed 's/.*mWakefulness=//' || true)
  k=$($ADBB shell dumpsys window </dev/null 2>/dev/null | tr -d '\r' | grep -m1 'isKeyguardShowing=' | sed 's/.*isKeyguardShowing=//' || true)
  case "$f" in *"$EXP_PKG/"*) f=true;; "") f=null;; *) f=false;; esac
  case "$w" in Awake) w=true;; "") w=null;; *) w=false;; esac
  [ -n "$k" ] || k=null
  echo "$f $w $k"
}
LAST_END=0
cooldown() {   # -> thermal status at start ("null" if never readable), waited seconds
  local now t waited=0
  now=$(date +%s); [ $((now - LAST_END)) -ge 30 ] || { sleep $((30 - (now - LAST_END))); }
  while :; do
    t=$(thermal); [ -n "$t" ] || t=null
    [ "$t" = 0 ] && break
    [ $waited -ge 300 ] && break
    sleep 10; waited=$((waited + 10))
  done
  echo "$t $waited"
}
REC=""
SELFTEST=""
# Called directly, never inside $(...): a command substitution is a subshell, so REC would be lost and
# the recorder orphaned (caught in review before the first cell, 2026-09-24).
touch_start() {   # $1 = file. Starts the recorder; sets REC and SELFTEST=true|false
  : > "$1"
  $ADBB shell getevent "$TOUCH_DEV" </dev/null > "$1" 2>&1 &
  REC=$!
  sleep 2
  if [ -d "/proc/$REC" ] && ! grep -qiE 'could not|denied|error|no such' "$1"; then SELFTEST=true; else SELFTEST=false; fi
}
touch_stop() {    # $1 = file -> count of EV_KEY (0001) / EV_ABS (0003) events, or null if the recorder died
  local alive=false n
  [ -n "$REC" ] && [ -d "/proc/$REC" ] && alive=true
  # our own adb client, exact pid; closing it ends the remote getevent
  if [ -n "$REC" ]; then kill "$REC" 2>/dev/null || true; wait "$REC" 2>/dev/null || true
    echo "touch_recorder_stopped pid=$REC file=${1##*/}" >> "$OUT/gl-bench-kills.txt"; fi
  REC=""
  n=$(grep -cE '^000[13] ' "$1" || true)
  $alive && echo "${n:-0}" || echo null
}
run() {   # name secs cmd...  -> log $OUT/<name>.log ; prints "wall user sys rc"
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
print(f"{time.monotonic()-t0:.2f} {r.ru_utime:.2f} {r.ru_stime:.2f} {rc}")
PY
}
seg() {   # name secs cmd...
  local name=$1 secs=$2; shift 2
  local cd th_start waited st_pre st_post selftest x0 x1 a0 a1 res touches th_end
  cd=$(cooldown); th_start=${cd% *}; waited=${cd#* }
  st_pre=$(state)
  touch_start "$OUT/$name.touch"; selftest=$SELFTEST
  x0=$(x3_ticks); a0=$(act_ticks)
  res=$(run "$name" "$secs" "$@")
  x1=$(x3_ticks); a1=$(act_ticks)
  touches=$(touch_stop "$OUT/$name.touch")
  st_post=$(state); th_end=$(thermal); [ -n "$th_end" ] || th_end=null
  LAST_END=$(date +%s)
  set -- $res; set -- "$@" $st_pre $st_post
  echo "SEG $name wall_s=$1 user_s=$2 sys_s=$3 rc=$4 x3_t0=$x0 x3_t1=$x1 act_t0=$a0 act_t1=$a1" \
       "touch_selftest=$selftest touch_events=$touches thermal_start=$th_start thermal_waited_s=$waited thermal_end=$th_end" \
       "focus_pre=$5 awake_pre=$6 keyguard_pre=$7 focus_post=$8 awake_post=$9 keyguard_post=${10}"
}

# Harness check without any benchmark: one 5 s sleep segment through the full instrumentation.
if [ "${GL_BENCH_DRYRUN:-0}" = 1 ]; then seg dry-sleep 20 sleep 5; echo "GL_BENCH_DRYRUN_DONE"; exit 0; fi

# Vulkan present mode, decided before any benchmark from what both drivers report for an xcb surface
# on :3 (freeze doc: immediate if both support it, else mailbox if both do, else fifo; same for both).
for d in lvp:$LVP_ICD turnip:$TU_ICD; do
  env $NOLAYER VK_ICD_FILENAMES=${d#*:} $TB/vulkaninfo > "$OUT/vulkaninfo-${d%%:*}.txt" 2>&1 || true
done
pm_ok() { grep -q "PRESENT_MODE_$1_KHR" "$OUT/vulkaninfo-lvp.txt" && grep -q "PRESENT_MODE_$1_KHR" "$OUT/vulkaninfo-turnip.txt"; }
if pm_ok IMMEDIATE; then PM=immediate; elif pm_ok MAILBOX; then PM=mailbox; else PM=fifo; fi
echo "PRESENT_MODE $PM"
echo "TOOLS glmark2=$($TB/glmark2 --version 2>&1 | head -1) vkmark_sha256=$(sha256sum $TB/vkmark | cut -c1-16) glmark2_sha256=$(sha256sum $TB/glmark2 | cut -c1-16)"

s_gl_cpu()  { seg gl-cpu 900 env $GL_CPU_ENV $TB/glmark2 --show-all-options; }
s_gl_gpu()  { seg gl-gpu 900 env $GL_GPU_ENV $TB/glmark2 --show-all-options; }
s_vk_cpu()  { seg vk-cpu 600 env $NOLAYER VK_ICD_FILENAMES=$LVP_ICD $TB/vkmark --winsys xcb -p $PM --show-all-options; }
s_vk_gpu()  { seg vk-gpu 600 env $NOLAYER VK_ICD_FILENAMES=$TU_ICD $TB/vkmark --winsys xcb -p $PM --show-all-options; }
s_gl_off()  { seg gl-gpu-off 900 env $GL_GPU_ENV $TB/glmark2 --off-screen --show-all-options; }
s_vk_hl()   { seg vk-gpu-hl 600 env $NOLAYER VK_ICD_FILENAMES=$TU_ICD $TB/vkmark --winsys headless --show-all-options; }
case "$REP" in
  01) s_gl_cpu; s_gl_gpu; s_vk_cpu; s_vk_gpu ;;
  02) s_vk_gpu; s_vk_cpu; s_gl_gpu; s_gl_cpu ;;
  *) echo "GL_BENCH_ABORT bad_rep $REP"; exit 4 ;;
esac
s_gl_off; s_vk_hl
echo "GL_BENCH_DONE rep=$REP"
