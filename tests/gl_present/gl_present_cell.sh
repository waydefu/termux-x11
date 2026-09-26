#!/usr/bin/env bash
# GL-PRESENT-01 cell script (evidence/session/gl/GL-PRESENT-01-FREEZE.md), mainline #3 step 1:
# where does the CPU go when a GPU-rendered GL/VK frame from an Ubuntu (glibc) program reaches :3?
#   REP=01|02 SERIAL=<5038-lane serial> GL_PRESENT_SET=main|xdbg|dry DISPLAY=:3 bash gl_present_cell.sh <evidence dir>
# Run by evidence/session/gl/run-gl-bench.sh KIND=cmd MODE=C, which has already started the untraced X3 and
# written x3-pid.txt / activity-pid.txt there. Clients run through /usr/local/bin/f8-gpu (the user's own
# Ubuntu GPU stack: /opt/mesa-kgsl Turnip on KGSL + Zink) with the present_count.so LD_PRELOAD counter.
# Helpers (thermal, state, cooldown, touch recorder, run) are copied from tests/gl/gl_bench.sh (GL-BENCH-01,
# sha256 in the freeze doc); changes: 15 s instead of 30 s between segments, per-thread X3 ticks, wall clock
# around the X3 window, the touch device name check, and the per-segment present_count output.
# Per segment it records:
#   X3 CPU          /proc/<x3>/stat utime+stime and /proc/<x3>/task/*/stat per thread, before/after
#   window          CLOCK_MONOTONIC seconds around the X3 before/after readings, and the same instants as
#                   MM-DDTHH:MM:SS.mmm (logcat time, same device clock) to attribute X3 log lines to a segment
#   client CPU      getrusage(RUSAGE_CHILDREN) of a fresh python wrapper; client stdout in <seg>.log
#   present calls   <seg>.present.jsonl from present_count.so (one line per client process)
#   Activity CPU    /proc/<act>/stat via adb (descriptive only)
#   thermal / focus / awake / keyguard / touches, as in GL-BENCH-01
# One 'SEG' line per segment goes to stdout; gl_present_judge.py reads those plus the files.
set -uo pipefail
OUT=${1:?evidence dir}
REP=${REP:?REP}
SET=${GL_PRESENT_SET:?GL_PRESENT_SET=main|xdbg|dry}
: "${SERIAL:?SERIAL}"
TB=/data/data/com.termux/files/usr/bin
ADBB="$TB/adb -P 5038 -s $SERIAL"
TOOLS=/root/projects/GPU加速/src/f8-ahb-exa-async/tests/gl_present/build
GPU=/usr/local/bin/f8-gpu
TOUCH_DEV=/dev/input/event7   # focaltech_ts (getevent -pl, 2026-09-24); name checked below
EXP_PKG=com.waydefu.x11gpu
X3=$(sed -n 's/^x3_pid=//p' "$OUT/x3-pid.txt")
ACT=$(sed -n 's/^activity_pid=//p' "$OUT/activity-pid.txt")
[ -n "$X3" ] && [ -d "/proc/$X3" ] || { echo "GL_PRESENT_ABORT no_x3"; exit 4; }

x3_ticks() { awk '{print $14+$15}' "/proc/$X3/stat" 2>/dev/null || echo null; }
x3_threads() {   # $1 = file: "tid utime stime comm" per X3 thread (comm last: it may contain spaces)
  python3 - "$X3" > "$1" <<'PY'
import os, sys
pid = sys.argv[1]
for tid in sorted(os.listdir(f"/proc/{pid}/task"), key=int):
    try:
        s = open(f"/proc/{pid}/task/{tid}/stat").read()
    except OSError:
        continue
    comm, rest = s[s.index("(") + 1:s.rindex(")")], s[s.rindex(")") + 2:].split()
    print(tid, rest[11], rest[12], comm)
PY
}
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
  now=$(date +%s); [ $((now - LAST_END)) -ge 15 ] || { sleep $((15 - (now - LAST_END))); }
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
TOUCH_NAME_OK=false
# Called directly, never inside $(...): a command substitution is a subshell, so REC would be lost and
# the recorder orphaned (gl_bench.sh review, 2026-09-24).
touch_start() {   # $1 = file. Starts the recorder; sets REC and SELFTEST=true|false
  : > "$1"
  $ADBB shell getevent "$TOUCH_DEV" </dev/null > "$1" 2>&1 &
  REC=$!
  sleep 2
  if $TOUCH_NAME_OK && [ -d "/proc/$REC" ] && ! grep -qiE 'could not|denied|error|no such' "$1"; then SELFTEST=true; else SELFTEST=false; fi
}
touch_stop() {    # $1 = file -> count of EV_KEY (0001) / EV_ABS (0003) events, or null if the recorder died
  local alive=false n
  [ -n "$REC" ] && [ -d "/proc/$REC" ] && alive=true
  # our own adb client, exact pid; closing it ends the remote getevent
  if [ -n "$REC" ]; then kill "$REC" 2>/dev/null || true; wait "$REC" 2>/dev/null || true
    echo "touch_recorder_stopped pid=$REC file=${1##*/}" >> "$OUT/gl-present-kills.txt"; fi
  REC=""
  n=$(grep -cE '^000[13] ' "$1" || true)
  $alive && $TOUCH_NAME_OK && echo "${n:-0}" || echo null
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
mono() { python3 -c 'import time; print(f"{time.monotonic():.3f}")'; }
seg() {   # name secs cmd...
  local name=$1 secs=$2; shift 2
  local cd th_start waited st_pre st_post selftest x0 x1 a0 a1 m0 m1 d0 d1 res touches th_end
  cd=$(cooldown); th_start=${cd% *}; waited=${cd#* }
  st_pre=$(state)
  touch_start "$OUT/$name.touch"; selftest=$SELFTEST
  a0=$(act_ticks)
  x3_threads "$OUT/$name.x3t0"; d0=$(date '+%m-%dT%H:%M:%S.%3N'); m0=$(mono); x0=$(x3_ticks)
  res=$(run "$name" "$secs" "$@")
  x1=$(x3_ticks); m1=$(mono); d1=$(date '+%m-%dT%H:%M:%S.%3N'); x3_threads "$OUT/$name.x3t1"
  a1=$(act_ticks)
  touches=$(touch_stop "$OUT/$name.touch")
  st_post=$(state); th_end=$(thermal); [ -n "$th_end" ] || th_end=null
  LAST_END=$(date +%s)
  set -- $res; set -- "$@" $st_pre $st_post
  echo "SEG $name wall_s=$1 user_s=$2 sys_s=$3 rc=$4 x3_t0=$x0 x3_t1=$x1 mono0=$m0 mono1=$m1 date0=$d0 date1=$d1 act_t0=$a0 act_t1=$a1" \
       "touch_selftest=$selftest touch_events=$touches thermal_start=$th_start thermal_waited_s=$waited thermal_end=$th_end" \
       "focus_pre=$5 awake_pre=$6 keyguard_pre=$7 focus_post=$8 awake_post=$9 keyguard_post=${10}"
}
PC() {   # segment name -> env words for the counter
  echo "LD_PRELOAD=$TOOLS/present_count.so PRESENT_COUNT_OUT=$OUT/$1.present.jsonl vblank_mode=0"
}

$ADBB shell getevent -pl "$TOUCH_DEV" </dev/null 2>/dev/null | tr -d '\r' | grep -m1 'name:' > "$OUT/touch-device.txt" || true
grep -qi focaltech "$OUT/touch-device.txt" && TOUCH_NAME_OK=true
( cd "$TOOLS" && sha256sum present_count.so vk_present gl_present_egl gl_present_glx ) > "$OUT/gl-present-tools.sha256"
sha256sum "$GPU" "$0" >> "$OUT/gl-present-tools.sha256"
echo "TOOLS $(tr '\n' ' ' < "$OUT/gl-present-tools.sha256" | cut -c1-400)"
echo "SET $SET REP $REP X3 $X3 ACT $ACT touch_name_ok=$TOUCH_NAME_OK"

S=${GL_PRESENT_SECS:-20}
T=$((S + 60))
L=1200x1200
M=600x600
case "$SET" in
  dry)
    seg idle 20 sleep 5
    seg vk-l $T $GPU env $(PC vk-l) $TOOLS/vk_present --size $L --seconds 5
    seg egl-l $T $GPU env $(PC egl-l) $TOOLS/gl_present_egl --size $L --seconds 5
    ;;
  xdbg)   # X3 started with TERMUX_X11_DEBUG=2: its DRI3 import log is the server-side witness of the path
    seg vk-l $T $GPU env $(PC vk-l) $TOOLS/vk_present --size $L --seconds 10
    seg egl-l $T $GPU env $(PC egl-l) $TOOLS/gl_present_egl --size $L --seconds 10
    seg glx-l $T $GPU env $(PC glx-l) $TOOLS/gl_present_glx --size $L --seconds 10
    ;;
  main)
    seg idle $T sleep "$S"
    seg vk-off $T $GPU env $(PC vk-off) $TOOLS/vk_present --size $L --seconds "$S" --offscreen
    seg vk-l $T $GPU env $(PC vk-l) $TOOLS/vk_present --size $L --seconds "$S"
    seg vk-s $T $GPU env $(PC vk-s) $TOOLS/vk_present --size $M --seconds "$S"
    seg egl-off $T $GPU env $(PC egl-off) $TOOLS/gl_present_egl --size $L --seconds "$S" --offscreen
    seg egl-l $T $GPU env $(PC egl-l) $TOOLS/gl_present_egl --size $L --seconds "$S"
    seg egl-s $T $GPU env $(PC egl-s) $TOOLS/gl_present_egl --size $M --seconds "$S"
    seg glx-l $T $GPU env $(PC glx-l) $TOOLS/gl_present_glx --size $L --seconds "$S"
    seg vk-l-sw $T $GPU env MESA_VK_WSI_DEBUG=sw $(PC vk-l-sw) $TOOLS/vk_present --size $L --seconds "$S"
    ;;
  *) echo "GL_PRESENT_ABORT bad_set $SET"; exit 4;;
esac
echo "GL_PRESENT_DONE set=$SET rep=$REP"
