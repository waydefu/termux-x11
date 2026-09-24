#!/usr/bin/env bash
# ELECTRON-BENCH-01 cell script (evidence/session/gl/ELECTRON-BENCH-01-FREEZE.md): Cursor with GPU (V2) vs
# today's flags (V0) on the experimental X (:3), run by evidence/session/gl/run-gl-bench.sh KIND=cmd.
#   REP=01|02 SERIAL=<5038-lane serial> DISPLAY=:3 bash electron_bench.sh <cell evidence dir>
#   ELECTRON_BENCH_DRYRUN=1: one V0 run only (harness check, never judged)
# Order: rep 01 V0 -> V2 -> V0', rep 02 V2 -> V0 -> V2' (the repeated variant gives the A/A spread).
# Every run is isolated: fresh HOME (so ~/.cursor/mcp.json is not read: no Serena / Firebase MCP), fresh
# --user-data-dir, its own D-Bus (dbus-run-session) and its own session (setsid). CPU is summed over that
# session id; at the end every process of the session is ended by exact pid and the absence is verified.
# The touchscreen is recorded for the whole run (same detector as gl_bench.sh; any EV_KEY/EV_ABS -> INVALID).
set -uo pipefail
OUT=${1:?evidence dir}
REP=${REP:?REP=01|02}
: "${SERIAL:?SERIAL}"
TB=/data/data/com.termux/files/usr/bin
ADBB="$TB/adb -P 5038 -s $SERIAL"
HERE=$(cd "$(dirname "$0")" && pwd)
DRIVE=$HERE/electron_bench_drive.mjs
BIN=/usr/share/cursor/cursor
BASE=/root/build/electron-bench
WS_FILE=$BASE/ws/bench.cc
NOPCI=/root/build/electron-probe/nopci/libnopci.so
TOUCH_DEV=/dev/input/event7
X3=$(sed -n 's/^x3_pid=//p' "$OUT/x3-pid.txt")
ACT=$(sed -n 's/^activity_pid=//p' "$OUT/activity-pid.txt")
[ -n "$X3" ] && [ -d "/proc/$X3" ] || { echo "ELECTRON_BENCH_ABORT no_x3"; exit 4; }
[ -r "$WS_FILE" ] || { echo "ELECTRON_BENCH_ABORT no_ws_file"; exit 4; }
echo "WS_FILE $(sha256sum "$WS_FILE")"
echo "TOOLS drive=$(sha256sum "$DRIVE" | cut -c1-16) nopci=$(sha256sum "$NOPCI" | cut -c1-16) cursor=$(sha256sum "$BIN" | cut -c1-16)"

COMMON=(--no-sandbox --disable-gpu-sandbox --disable-dev-shm-usage --ozone-platform=x11)
V0_FLAGS=(--disable-gpu)
V2_FLAGS=(--ignore-gpu-blocklist --use-gl=angle --use-angle=vulkan --enable-features=Vulkan,VulkanFromANGLE,DefaultANGLEVulkan)
V2_ENV=(LD_PRELOAD=$NOPCI VK_ICD_FILENAMES=/opt/mesa-kgsl/share/vulkan/icd.d/freedreno_icd.aarch64.json
  "VK_LOADER_LAYERS_DISABLE=*" LD_LIBRARY_PATH=/opt/mesa-kgsl/lib
  __EGL_VENDOR_LIBRARY_FILENAMES=/opt/mesa-kgsl/share/glvnd/egl_vendor.d/50_mesa.json
  __GLX_VENDOR_LIBRARY_NAME=mesa MESA_LOADER_DRIVER_OVERRIDE=zink GALLIUM_DRIVER=zink)

thermal() { $ADBB shell dumpsys thermalservice </dev/null 2>/dev/null | tr -d '\r' | sed -n 's/^Thermal Status: *//p' | head -1; }
LAST_END=0
cooldown() {   # -> "<thermal status at start> <waited s>"
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
touch_start() {   # called directly, never in $(...) (a subshell would lose REC)
  : > "$1"; $ADBB shell getevent "$TOUCH_DEV" </dev/null > "$1" 2>&1 & REC=$!
  sleep 2
  if [ -d "/proc/$REC" ] && ! grep -qiE 'could not|denied|error|no such' "$1"; then SELFTEST=true; else SELFTEST=false; fi
}
touch_stop() {    # -> EV_KEY/EV_ABS count, or null if the recorder died
  local alive=false n
  [ -n "$REC" ] && [ -d "/proc/$REC" ] && alive=true
  if [ -n "$REC" ]; then kill "$REC" 2>/dev/null || true; wait "$REC" 2>/dev/null || true
    echo "touch_recorder_stopped pid=$REC file=${1##*/}" >> "$OUT/electron-bench-kills.txt"; fi
  REC=""; n=$(grep -cE '^000[13] ' "$1" || true)
  $alive && echo "${n:-0}" || echo null
}
session_pids() {  # pids whose session id (stat field 6) is $1
  local p f
  for p in /proc/[0-9]*; do
    f=$(sed 's/.*) //' "$p/stat" 2>/dev/null) || continue
    [ "$(echo "$f" | awk '{print $4}')" = "$1" ] && echo "${p##*/}"
  done
}
end_session() {   # CONSTRUCTION: end exactly the processes of session $1; echo count still alive after KILL
  local sid=$1 name=$2 p left
  for p in $(session_pids "$sid"); do kill -TERM "$p" 2>/dev/null && echo "term $name sid=$sid pid=$p" >> "$OUT/electron-bench-kills.txt"; done
  for _ in $(seq 1 20); do [ -z "$(session_pids "$sid")" ] && break; sleep 0.5; done
  for p in $(session_pids "$sid"); do kill -KILL "$p" 2>/dev/null && echo "kill $name sid=$sid pid=$p" >> "$OUT/electron-bench-kills.txt"; done
  sleep 1; left=$(session_pids "$sid" | wc -l); echo "$left"
}

run_one() {   # name variant(v0|v2) port
  local name=$1 var=$2 port=$3
  local cd th0 waited home prof sidf sid launch rc touches th1 left
  cd=$(cooldown); th0=${cd% *}; waited=${cd#* }
  home=$BASE/home-$name; prof=$BASE/prof-$name; sidf=$OUT/$name.sid
  rm -rf "$home" "$prof"; mkdir -p "$home" "$prof" "$home/runtime"; chmod 700 "$home/runtime"
  local envs=(HOME=$home XDG_RUNTIME_DIR=$home/runtime XDG_CONFIG_HOME=$home/.config XDG_CACHE_HOME=$home/.cache)
  local flags=("${V0_FLAGS[@]}")
  if [ "$var" = v2 ]; then envs+=("${V2_ENV[@]}"); flags=("${V2_FLAGS[@]}"); fi
  touch_start "$OUT/$name.touch"
  launch=$(date +%s%3N)
  # the inner bash becomes the session leader (setsid), records its pid (= session id), then execs the D-Bus
  # wrapper; everything Cursor starts inherits that session id.
  setsid bash -c 'echo $$ > "$0"; exec "$@"' "$sidf" \
    env -u DBUS_SESSION_BUS_ADDRESS "${envs[@]}" dbus-run-session -- \
    "$BIN" "${COMMON[@]}" "${flags[@]}" --user-data-dir="$prof" --remote-debugging-port="$port" \
    --enable-logging=stderr "$WS_FILE" > "$OUT/$name.stderr" 2>&1 < /dev/null &
  for _ in $(seq 1 50); do [ -s "$sidf" ] && break; sleep 0.1; done
  sid=$(cat "$sidf" 2>/dev/null)
  node "$DRIVE" "$port" "${sid:-0}" "$X3" "$ACT" "$SERIAL" "$launch" "$OUT/$name.json" "$OUT/$name.png" > "$OUT/$name.drive.out" 2>&1
  rc=$?
  left=$( [ -n "$sid" ] && end_session "$sid" "$name" || echo null )
  touches=$(touch_stop "$OUT/$name.touch")
  th1=$(thermal); [ -n "$th1" ] || th1=null
  LAST_END=$(date +%s)
  echo "RUN $name variant=$var sid=${sid:-null} drive_rc=$rc json=$name.json touch_selftest=$SELFTEST touch_events=$touches" \
       "thermal_start=$th0 thermal_waited_s=$waited thermal_end=$th1 survivors_after_kill=$left"
}

if [ "${ELECTRON_BENCH_DRYRUN:-0}" = 1 ]; then
  run_one dry-v0 v0 9250; echo ELECTRON_BENCH_DRYRUN_DONE; exit 0
fi
case "$REP" in
  01) run_one r01-1-v0 v0 9251; run_one r01-2-v2 v2 9252; run_one r01-3-v0 v0 9253 ;;
  02) run_one r02-1-v2 v2 9254; run_one r02-2-v0 v0 9255; run_one r02-3-v2 v2 9256 ;;
  *) echo "ELECTRON_BENCH_ABORT bad_rep $REP"; exit 4 ;;
esac
echo "ELECTRON_BENCH_DONE rep=$REP"
