#!/usr/bin/env bash
# APP-IDLE-01 cell script (evidence/session/proot/APP-IDLE-01-FREEZE.md): idle CPU of each daily app under a NEW stock
# proot (P0) and under proot-fast2 (PF), on the experimental X (:3). Run by evidence/session/gl/run-gl-bench.sh KIND=cmd.
#   SERIAL=<5038-lane serial> DISPLAY=:3 bash app_idle.sh <cell evidence dir>
#   APP_IDLE_DRYRUN=1: one app (chatgpt), P0 and PF once (harness check, never judged)
#   APP_IDLE_SANITY=1: one PF run per app (functional sanity of a proot build, never judged)
# Per app ABBA: P0, PF, PF, P0. Launch 60 s (not measured), then a 60 s idle window [t0, t1] with no input.
# CPU = meter CSV on the run-as side (proot_meter.py), as in PROOT-BENCH; end of run = proot_bench_end.py (run-as).
set -uo pipefail
OUT=${1:?evidence dir}
: "${SERIAL:?SERIAL}"
TB=/data/data/com.termux/files/usr/bin
ADBB="$TB/adb -P 5038 -s $SERIAL"
HERE=$(cd "$(dirname "$0")" && pwd)
P0_BIN=/data/data/com.termux/files/usr/bin/proot
PF_BIN=${PF_BIN:-/data/data/com.termux/files/home/build/proot-fast/out2/bin/proot-fast2}
LOAD_S=${LOAD_S:-60}; IDLE_S=${IDLE_S:-60}
XFER=/tmp/app-idle               # == $PREFIX/tmp/app-idle outside PRoot (bind /data/data/com.termux/files/usr/tmp:/tmp)
XFER_HOST=/data/data/com.termux/files/usr/tmp/app-idle
TOUCH_DEV=/dev/input/event7
X3=$(sed -n 's/^x3_pid=//p' "$OUT/x3-pid.txt")
[ -n "$X3" ] && [ -d "/proc/$X3" ] || { echo "APP_IDLE_ABORT no_x3"; exit 4; }
mkdir -p "$XFER"; cp "$HERE/app_idle_launch.sh" "$HERE/app_idle_cmd.sh" "$HERE/proot-args.txt" "$HERE/proot_meter.py" "$HERE/proot_bench_end.py" "$XFER/"
echo "TOOLS launch=$(sha256sum "$HERE/app_idle_launch.sh" | cut -c1-16) cmd=$(sha256sum "$HERE/app_idle_cmd.sh" | cut -c1-16) args=$(sha256sum "$HERE/proot-args.txt" | cut -c1-16)" \
     "meter=$(sha256sum "$HERE/proot_meter.py" | cut -c1-16) end=$(sha256sum "$HERE/proot_bench_end.py" | cut -c1-16)"
echo "PROOTS p0=$(sha256sum "$P0_BIN" | cut -c1-16) pf=$(sha256sum "$PF_BIN" | cut -c1-16)"

thermal() { $ADBB shell dumpsys thermalservice </dev/null 2>/dev/null | tr -d '\r' | sed -n 's/^Thermal Status: *//p' | head -1; }
LAST_END=0
cooldown() {
  local now t waited=0
  now=$(date +%s); [ $((now - LAST_END)) -ge 30 ] || sleep $((30 - (now - LAST_END)))
  while :; do
    t=$(thermal); [ -n "$t" ] || t=null
    [ "$t" = 0 ] && break; [ $waited -ge 300 ] && break
    sleep 10; waited=$((waited + 10))
  done
  echo "$t $waited"
}
REC=""; SELFTEST=""
touch_start() {   # called directly, never in $(...)
  : > "$1"; $ADBB shell getevent "$TOUCH_DEV" </dev/null > "$1" 2>&1 & REC=$!
  sleep 2
  if [ -d "/proc/$REC" ] && ! grep -qiE 'could not|denied|error|no such' "$1"; then SELFTEST=true; else SELFTEST=false; fi
}
touch_stop() {
  local alive=false n
  [ -n "$REC" ] && [ -d "/proc/$REC" ] && alive=true
  if [ -n "$REC" ]; then kill "$REC" 2>/dev/null || true; wait "$REC" 2>/dev/null || true
    echo "touch_recorder_stopped pid=$REC file=${1##*/}" >> "$OUT/app-idle-kills.txt"; fi
  REC=""; n=$(grep -cE '^000[13] ' "$1" || true)
  $alive && echo "${n:-0}" || echo null
}
mem_wait() {   # MemAvailable >= 4600 MB for 20 consecutive s, at most 300 s (dry-run 01: mem-guard 3000 tripped)
  local ok=0 t=0 a
  while [ $ok -lt 20 ] && [ $t -lt 300 ]; do
    a=$(awk '/^MemAvailable:/{print int($2/1024)}' /proc/meminfo)
    if [ "$a" -ge 4600 ]; then ok=$((ok + 1)); else ok=0; fi
    sleep 1; t=$((t + 1))
  done
  [ $ok -ge 20 ] && echo "ok $a $t" || echo "timeout $a $t"
}
runas() {   # Termux-native command in the run-as domain (outside PRoot: not traced, sees the launched processes)
  $ADBB shell "run-as com.termux /data/data/com.termux/files/usr/bin/env -i PATH=/data/data/com.termux/files/usr/bin \
HOME=/data/data/com.termux/files/home PREFIX=/data/data/com.termux/files/usr TMPDIR=/data/data/com.termux/files/usr/tmp $*"
}
wait_client() {   # adb client pid, label: wait up to 10 s, then end it (logged)
  for _ in $(seq 1 20); do [ -d "/proc/$1" ] || break; sleep 0.5; done
  if [ -d "/proc/$1" ]; then kill "$1" 2>/dev/null; echo "adb_client_killed $2 pid=$1" >> "$OUT/app-idle-kills.txt"; fi
  wait "$1" 2>/dev/null
}

devstate() {   # <prefix>: awake / keyguard / focused package, outside the idle window
  local s; s=$($ADBB shell 'dumpsys power | grep -m1 mWakefulness=; dumpsys window | grep -m1 isKeyguardShowing; dumpsys window | grep -m1 mCurrentFocus' </dev/null 2>/dev/null | tr -d '\r')
  printf '%s_awake=%s %s_keyguard=%s %s_focus=%s' "$1" "$(echo "$s" | grep -q 'mWakefulness=Awake' && echo true || echo false)" \
    "$1" "$(echo "$s" | sed -n 's/.*isKeyguardShowing=\([a-z]*\).*/\1/p')" "$1" "$(echo "$s" | grep -q com.waydefu.x11gpu && echo true || echo false)"
}

run_one() {   # name app variant(p0|pf)
  local name=$1 app=$2 var=$3 bin cd th0 waited mw run tpf sidf sid tpid targ0 launch endr left tracer_gone touches th1 adbpid meterpid
  local wins st0 st1 t0 t1 alive0 alive1
  [ "$var" = pf ] && bin=$PF_BIN || bin=$P0_BIN
  cd=$(cooldown); th0=${cd% *}; waited=${cd#* }
  mw=$(mem_wait); set -- $mw
  [ "$1" = ok ] || { echo "APP_IDLE_ABORT mem_wait_timeout run=$name mem_avail_mb=$2 waited_s=$3"; exit 4; }
  local mem_mb=$2 mem_waited=$3
  run=$XFER/run-$name; tpf=$XFER/$name.tracer; sidf=$XFER/$name.sid
  rm -rf "$run" "$tpf" "$sidf" "$XFER/$name.stop" "$XFER/$name.meter.csv" "$XFER/$name.end.log"
  mkdir -p "$run/runtime"; chmod 700 "$run/runtime"
  runas "/data/data/com.termux/files/usr/bin/python $XFER_HOST/proot_meter.py $XFER_HOST/$name.meter.csv $XFER_HOST/$name.stop \
$XFER_HOST/$name.sid $XFER_HOST/$name.tracer $X3" </dev/null > "$OUT/$name.meter.out" 2>&1 &
  meterpid=$!
  touch_start "$OUT/$name.touch"
  launch=$(date +%s%3N)
  runas "/data/data/com.termux/files/usr/bin/sh $XFER_HOST/app_idle_launch.sh $bin $XFER_HOST/proot-args.txt \
$XFER_HOST/$name.tracer $XFER/$name.sid $app $run" </dev/null > "$OUT/$name.launch.out" 2>&1 &
  adbpid=$!
  for _ in $(seq 1 100); do [ -s "$tpf" ] && [ -s "$sidf" ] && break; sleep 0.2; done
  tpid=$(cat "$tpf" 2>/dev/null); sid=$(cat "$sidf" 2>/dev/null)
  sleep "$LOAD_S"
  wins=$(xdotool search --onlyvisible --name . 2>/dev/null | wc -l)
  xdotool search --onlyvisible --name . getwindowname %@ 2>/dev/null | head -5 | tr '\n' '|' > "$OUT/$name.windows"
  alive0=$([ -d "$run" ] && [ -s "$XFER/$name.meter.csv" ] && tail -1 "$XFER/$name.meter.csv" | cut -d, -f3)
  st0=$(devstate start)
  t0=$(date +%s%3N); sleep "$IDLE_S"; t1=$(date +%s%3N)
  st1=$(devstate end)
  alive1=$(tail -1 "$XFER/$name.meter.csv" 2>/dev/null | cut -d, -f3)
  endr=$( [ -n "$sid" ] && [ -n "$tpid" ] && runas "/data/data/com.termux/files/usr/bin/python $XFER_HOST/proot_bench_end.py \
$XFER_HOST/$name.sid $XFER_HOST/$name.tracer $bin $XFER_HOST/$name.end.log" </dev/null 2>&1 | tr -d '\r' | grep '^END ' | tail -1 )
  left=$(echo "$endr" | sed -n 's/.*survivors=\([0-9]*\).*/\1/p'); tracer_gone=$(echo "$endr" | sed -n 's/.*tracer_gone=\([a-z]*\).*/\1/p')
  touch "$XFER/$name.stop"
  wait_client "$meterpid" "meter-$name"; wait_client "$adbpid" "launch-$name"
  cp "$XFER/$name.meter.csv" "$OUT/" 2>/dev/null; cp "$XFER/$name.end.log" "$OUT/" 2>/dev/null
  cat "$OUT/$name.end.log" >> "$OUT/app-idle-kills.txt" 2>/dev/null
  targ0=$(sed -n '1s/.*tracer_arg0=\([^ ]*\).*/\1/p' "$OUT/$name.meter.csv" 2>/dev/null)
  touches=$(touch_stop "$OUT/$name.touch")
  th1=$(thermal); [ -n "$th1" ] || th1=null
  LAST_END=$(date +%s)
  echo "RUN $name app=$app variant=$var sid=${sid:-null} tracer_pid=${tpid:-null} tracer_arg0=${targ0:-null} meter=$name.meter.csv" \
       "t0=$t0 t1=$t1 windows=$wins procs_t0=${alive0:-null} procs_t1=${alive1:-null} $st0 $st1" \
       "touch_selftest=$SELFTEST touch_events=$touches thermal_start=$th0 thermal_waited_s=$waited thermal_end=$th1" \
       "mem_avail_mb=$mem_mb mem_waited_s=$mem_waited survivors_after_kill=${left:-null} tracer_gone=${tracer_gone:-null}"
  [ "${left:-x}" = 0 ] && [ "${tracer_gone:-x}" = true ] || { echo "APP_IDLE_ABORT end_unverified run=$name end='${endr}'"; exit 4; }
}

if [ "${APP_IDLE_SANITY:-0}" = 1 ]; then   # one PF run per app: does it start, show a window, stay alive, end clean?
  for app in ${APPS:-claude chatgpt chatgptweb hermes cursor}; do run_one $app-sanity $app pf; done; echo APP_IDLE_SANITY_DONE; exit 0
fi
if [ "${APP_IDLE_DRYRUN:-0}" = 1 ]; then
  run_one dry-chatgpt-p0 chatgpt p0; run_one dry-chatgpt-pf chatgpt pf; echo APP_IDLE_DRYRUN_DONE; exit 0
fi
for app in ${APPS:-chatgpt chatgptweb hermes cursor}; do
  run_one $app-1-p0 $app p0; run_one $app-2-pf $app pf; run_one $app-3-pf $app pf; run_one $app-4-p0 $app p0
done
echo "APP_IDLE_DONE"
