#!/usr/bin/env bash
# PROOT-BENCH-01 cell script (evidence/session/proot/PROOT-BENCH-01-FREEZE.md): the same Cursor workload under a
# NEW stock proot (P0) and under proot-fast (PF), on the experimental X (:3). Run by
# evidence/session/gl/run-gl-bench.sh KIND=cmd inside the daily PRoot.
#   REP=01|02 SERIAL=<5038-lane serial> DISPLAY=:3 bash proot_bench.sh <cell evidence dir>
#   PROOT_BENCH_DRYRUN=1: one P0 run and one PF run (harness check, never judged)
# Order: rep 01 P0 -> PF -> P0', rep 02 PF -> P0 -> PF'. Each Cursor runs in its own new proot instance started
# outside PRoot through `adb run-as com.termux` (proot_bench_launch.sh); the driver adds that tracer's CPU
# (EXTRA_PIDS). Touchscreen / thermal / focus handling as in electron_bench.sh.
set -uo pipefail
OUT=${1:?evidence dir}
REP=${REP:?REP=01|02}
: "${SERIAL:?SERIAL}"
TB=/data/data/com.termux/files/usr/bin
ADBB="$TB/adb -P 5038 -s $SERIAL"
HERE=$(cd "$(dirname "$0")" && pwd)
DRIVE=$HERE/../gl/electron_bench_drive.mjs
P0_BIN=/data/data/com.termux/files/usr/bin/proot
PF_BIN=/data/data/com.termux/files/home/build/proot-fast/out/bin/proot-fast
BASE=/root/build/proot-bench
WS_FILE=/root/build/electron-bench/ws/bench.cc
XFER=/tmp/proot-bench            # == $PREFIX/tmp/proot-bench outside PRoot (bind /data/data/com.termux/files/usr/tmp:/tmp)
XFER_HOST=/data/data/com.termux/files/usr/tmp/proot-bench
TOUCH_DEV=/dev/input/event7
X3=$(sed -n 's/^x3_pid=//p' "$OUT/x3-pid.txt")
ACT=$(sed -n 's/^activity_pid=//p' "$OUT/activity-pid.txt")
[ -n "$X3" ] && [ -d "/proc/$X3" ] || { echo "PROOT_BENCH_ABORT no_x3"; exit 4; }
[ -r "$WS_FILE" ] || { echo "PROOT_BENCH_ABORT no_ws_file"; exit 4; }
mkdir -p "$XFER"; cp "$HERE/proot_bench_launch.sh" "$HERE/proot-args.txt" "$XFER/"
echo "WS_FILE $(sha256sum "$WS_FILE")"
echo "TOOLS drive=$(sha256sum "$DRIVE" | cut -c1-16) launch=$(sha256sum "$HERE/proot_bench_launch.sh" | cut -c1-16) args=$(sha256sum "$HERE/proot-args.txt" | cut -c1-16)"
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
session_pids() {
  local p f
  for p in /proc/[0-9]*; do
    f=$(sed 's/.*) //' "$p/stat" 2>/dev/null) || continue
    [ "$(echo "$f" | awk '{print $4}')" = "$1" ] && echo "${p##*/}"
  done
}
end_session() {   # CONSTRUCTION: exact pids of session $1; echo count alive after KILL
  local sid=$1 name=$2 p
  for p in $(session_pids "$sid"); do kill -TERM "$p" 2>/dev/null && echo "term $name sid=$sid pid=$p" >> "$OUT/proot-bench-kills.txt"; done
  for _ in $(seq 1 20); do [ -z "$(session_pids "$sid")" ] && break; sleep 0.5; done
  for p in $(session_pids "$sid"); do kill -KILL "$p" 2>/dev/null && echo "kill $name sid=$sid pid=$p" >> "$OUT/proot-bench-kills.txt"; done
  sleep 1; session_pids "$sid" | wc -l
}

run_one() {   # name variant(p0|pf) port
  local name=$1 var=$2 port=$3 bin
  local cd th0 waited home prof tpf sidf sid tpid targ0 launch rc left tracer_gone touches th1 adbpid
  [ "$var" = pf ] && bin=$PF_BIN || bin=$P0_BIN
  cd=$(cooldown); th0=${cd% *}; waited=${cd#* }
  home=$BASE/home-$name; prof=$BASE/prof-$name
  tpf=$XFER/$name.tracer; sidf=$XFER/$name.sid
  rm -rf "$home" "$prof" "$tpf" "$sidf"; mkdir -p "$home/runtime" "$prof"; chmod 700 "$home/runtime"
  touch_start "$OUT/$name.touch"
  launch=$(date +%s%3N)
  $ADBB shell "run-as com.termux /data/data/com.termux/files/usr/bin/env -i PATH=/data/data/com.termux/files/usr/bin \
HOME=/data/data/com.termux/files/home PREFIX=/data/data/com.termux/files/usr TMPDIR=/data/data/com.termux/files/usr/tmp \
/data/data/com.termux/files/usr/bin/sh $XFER_HOST/proot_bench_launch.sh $bin $XFER_HOST/proot-args.txt \
$XFER_HOST/$name.tracer $XFER/$name.sid $home $prof $port $WS_FILE" </dev/null > "$OUT/$name.launch.out" 2>&1 &
  adbpid=$!
  for _ in $(seq 1 100); do [ -s "$tpf" ] && [ -s "$sidf" ] && break; sleep 0.2; done
  tpid=$(cat "$tpf" 2>/dev/null); sid=$(cat "$sidf" 2>/dev/null)
  targ0=$( [ -n "$tpid" ] && tr '\0' '\n' < "/proc/$tpid/cmdline" 2>/dev/null | head -1 )
  EXTRA_PIDS="${tpid:-0}" node "$DRIVE" "$port" "${sid:-0}" "$X3" "$ACT" "$SERIAL" "$launch" "$OUT/$name.json" "$OUT/$name.png" \
    > "$OUT/$name.drive.out" 2>&1
  rc=$?
  left=$( [ -n "$sid" ] && end_session "$sid" "$name" || echo null )
  tracer_gone=false
  for _ in $(seq 1 20); do [ -n "$tpid" ] && [ -d "/proc/$tpid" ] || { tracer_gone=true; break; }; sleep 0.5; done
  if ! $tracer_gone && [ -n "$tpid" ]; then
    kill -TERM "$tpid" 2>/dev/null && echo "term_tracer $name pid=$tpid" >> "$OUT/proot-bench-kills.txt"; sleep 2
    [ -d "/proc/$tpid" ] || tracer_gone=true
  fi
  if [ -d "/proc/$adbpid" ]; then kill "$adbpid" 2>/dev/null; echo "adb_client_killed $name pid=$adbpid" >> "$OUT/proot-bench-kills.txt"; fi
  wait "$adbpid" 2>/dev/null
  touches=$(touch_stop "$OUT/$name.touch")
  th1=$(thermal); [ -n "$th1" ] || th1=null
  LAST_END=$(date +%s)
  echo "RUN $name variant=$var sid=${sid:-null} tracer_pid=${tpid:-null} tracer_arg0=${targ0:-null} drive_rc=$rc json=$name.json" \
       "touch_selftest=$SELFTEST touch_events=$touches thermal_start=$th0 thermal_waited_s=$waited thermal_end=$th1" \
       "survivors_after_kill=$left tracer_gone=$tracer_gone"
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
