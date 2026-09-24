#!/usr/bin/env bash
# PROOT-BENCH-01 cell script (evidence/session/proot/PROOT-BENCH-01-FREEZE.md): the same Cursor workload under a
# NEW stock proot (P0) and under proot-fast (PF), on the experimental X (:3). Run by
# evidence/session/gl/run-gl-bench.sh KIND=cmd inside the daily PRoot.
#   REP=01|02 SERIAL=<5038-lane serial> DISPLAY=:3 bash proot_bench.sh <cell evidence dir>
#   PROOT_BENCH_DRYRUN=1: one P0 run and one PF run (harness check, never judged)
# Order: rep 01 P0 -> PF -> P0', rep 02 PF -> P0 -> PF'. Each Cursor runs in its own new proot instance started
# outside PRoot through `adb run-as com.termux` (proot_bench_launch.sh). The daily PRoot cannot see those processes
# (SELinux runas_app, dry-run 01), so CPU metering (proot_meter.py -> <run>.meter.csv) and the end of the run
# (proot_bench_end.py) also run on the run-as side; the driver only operates Cursor and stamps phase boundaries.
# Touchscreen / thermal / focus handling as in electron_bench.sh.
set -uo pipefail
OUT=${1:?evidence dir}
REP=${REP:?REP=01|02}
: "${SERIAL:?SERIAL}"
TB=/data/data/com.termux/files/usr/bin
ADBB="$TB/adb -P 5038 -s $SERIAL"
HERE=$(cd "$(dirname "$0")" && pwd)
DRIVE=$HERE/../gl/electron_bench_drive.mjs
P0_BIN=/data/data/com.termux/files/usr/bin/proot
PF_BIN=${PF_BIN:-/data/data/com.termux/files/home/build/proot-fast/out/bin/proot-fast}   # PROOT-BENCH-02: out2/bin/proot-fast2
BASE=/root/build/proot-bench
WS_FILE=/root/build/electron-bench/ws/bench.cc
XFER=/tmp/proot-bench            # == $PREFIX/tmp/proot-bench outside PRoot (bind /data/data/com.termux/files/usr/tmp:/tmp)
XFER_HOST=/data/data/com.termux/files/usr/tmp/proot-bench
TOUCH_DEV=/dev/input/event7
X3=$(sed -n 's/^x3_pid=//p' "$OUT/x3-pid.txt")
ACT=$(sed -n 's/^activity_pid=//p' "$OUT/activity-pid.txt")
[ -n "$X3" ] && [ -d "/proc/$X3" ] || { echo "PROOT_BENCH_ABORT no_x3"; exit 4; }
[ -r "$WS_FILE" ] || { echo "PROOT_BENCH_ABORT no_ws_file"; exit 4; }
mkdir -p "$XFER"; cp "$HERE/proot_bench_launch.sh" "$HERE/proot-args.txt" "$HERE/proot_meter.py" "$HERE/proot_bench_end.py" "$XFER/"
echo "WS_FILE $(sha256sum "$WS_FILE")"
echo "TOOLS drive=$(sha256sum "$DRIVE" | cut -c1-16) launch=$(sha256sum "$HERE/proot_bench_launch.sh" | cut -c1-16) args=$(sha256sum "$HERE/proot-args.txt" | cut -c1-16)" \
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
    echo "touch_recorder_stopped pid=$REC file=${1##*/}" >> "$OUT/proot-bench-kills.txt"; fi
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
  if [ -d "/proc/$1" ]; then kill "$1" 2>/dev/null; echo "adb_client_killed $2 pid=$1" >> "$OUT/proot-bench-kills.txt"; fi
  wait "$1" 2>/dev/null
}

run_one() {   # name variant(p0|pf) port
  local name=$1 var=$2 port=$3 bin
  local cd th0 waited mw home prof tpf sidf sid tpid targ0 launch rc endr left tracer_gone touches th1 adbpid meterpid
  [ "$var" = pf ] && bin=$PF_BIN || bin=$P0_BIN
  cd=$(cooldown); th0=${cd% *}; waited=${cd#* }
  mw=$(mem_wait); set -- $mw
  [ "$1" = ok ] || { echo "PROOT_BENCH_ABORT mem_wait_timeout run=$name mem_avail_mb=$2 waited_s=$3"; exit 4; }
  local mem_mb=$2 mem_waited=$3
  home=$BASE/home-$name; prof=$BASE/prof-$name
  tpf=$XFER/$name.tracer; sidf=$XFER/$name.sid
  rm -rf "$home" "$prof" "$tpf" "$sidf" "$XFER/$name.stop" "$XFER/$name.meter.csv" "$XFER/$name.end.log"
  mkdir -p "$home/runtime" "$prof"; chmod 700 "$home/runtime"
  runas "/data/data/com.termux/files/usr/bin/python $XFER_HOST/proot_meter.py $XFER_HOST/$name.meter.csv $XFER_HOST/$name.stop \
$XFER_HOST/$name.sid $XFER_HOST/$name.tracer $X3" </dev/null > "$OUT/$name.meter.out" 2>&1 &
  meterpid=$!
  touch_start "$OUT/$name.touch"
  launch=$(date +%s%3N)
  runas "/data/data/com.termux/files/usr/bin/sh $XFER_HOST/proot_bench_launch.sh $bin $XFER_HOST/proot-args.txt \
$XFER_HOST/$name.tracer $XFER/$name.sid $home $prof $port $WS_FILE" </dev/null > "$OUT/$name.launch.out" 2>&1 &
  adbpid=$!
  for _ in $(seq 1 100); do [ -s "$tpf" ] && [ -s "$sidf" ] && break; sleep 0.2; done
  tpid=$(cat "$tpf" 2>/dev/null); sid=$(cat "$sidf" 2>/dev/null)
  # CPU comes from the run-as meter CSV (the daily PRoot cannot read these processes); the driver only operates
  # Cursor and stamps the phase boundaries.
  node "$DRIVE" "$port" "${sid:-0}" "$X3" "$ACT" "$SERIAL" "$launch" "$OUT/$name.json" "$OUT/$name.png" \
    > "$OUT/$name.drive.out" 2>&1
  rc=$?
  endr=$( [ -n "$sid" ] && [ -n "$tpid" ] && runas "/data/data/com.termux/files/usr/bin/python $XFER_HOST/proot_bench_end.py \
$XFER_HOST/$name.sid $XFER_HOST/$name.tracer $bin $XFER_HOST/$name.end.log" </dev/null 2>&1 | tr -d '\r' | grep '^END ' | tail -1 )
  left=$(echo "$endr" | sed -n 's/.*survivors=\([0-9]*\).*/\1/p'); tracer_gone=$(echo "$endr" | sed -n 's/.*tracer_gone=\([a-z]*\).*/\1/p')
  touch "$XFER/$name.stop"
  wait_client "$meterpid" "meter-$name"; wait_client "$adbpid" "launch-$name"
  cp "$XFER/$name.meter.csv" "$OUT/" 2>/dev/null; cp "$XFER/$name.end.log" "$OUT/" 2>/dev/null
  cat "$OUT/$name.end.log" >> "$OUT/proot-bench-kills.txt" 2>/dev/null
  targ0=$(sed -n '1s/.*tracer_arg0=\([^ ]*\).*/\1/p' "$OUT/$name.meter.csv" 2>/dev/null)
  touches=$(touch_stop "$OUT/$name.touch")
  th1=$(thermal); [ -n "$th1" ] || th1=null
  LAST_END=$(date +%s)
  echo "RUN $name variant=$var sid=${sid:-null} tracer_pid=${tpid:-null} tracer_arg0=${targ0:-null} drive_rc=$rc json=$name.json" \
       "meter=$name.meter.csv touch_selftest=$SELFTEST touch_events=$touches thermal_start=$th0 thermal_waited_s=$waited thermal_end=$th1" \
       "mem_avail_mb=$mem_mb mem_waited_s=$mem_waited survivors_after_kill=${left:-null} tracer_gone=${tracer_gone:-null}"
  # fail closed: an unverified end could leave a Cursor (~1 GB) behind and stack the next run on top of it
  [ "${left:-x}" = 0 ] && [ "${tracer_gone:-x}" = true ] || { echo "PROOT_BENCH_ABORT end_unverified run=$name end='${endr}'"; exit 4; }
}

if [ "${PROOT_BENCH_DRYRUN:-0}" = 1 ]; then
  run_one dry-p0 p0 9260; run_one dry-pf pf 9261; echo PROOT_BENCH_DRYRUN_DONE; exit 0
fi
case "$REP" in
  01) run_one r01-1-p0 p0 9261; run_one r01-2-pf pf 9262; run_one r01-3-p0 p0 9263 ;;
  02) run_one r02-1-pf pf 9264; run_one r02-2-p0 p0 9265; run_one r02-3-pf pf 9266 ;;
  *) echo "PROOT_BENCH_ABORT bad_rep $REP"; exit 4 ;;
esac
echo "PROOT_BENCH_DONE rep=$REP"
