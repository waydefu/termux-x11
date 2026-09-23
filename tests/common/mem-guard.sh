#!/usr/bin/env bash
# Memory watchdog for a device run: keeps the phone away from lmkd while the experimental
# X (X3) runs.
#
#   mem-guard.sh --x3-pid PID --log FILE --flag FILE --serial SERIAL
#                [--floor-mb 3000] [--swap-growth-mb 1536] [--x3-max-mb 3072] [--period 0.5]
#
# Why (INCIDENT-20260923-2-LMK-B3-S): X3 is a child of the com.termux app process. In B.3
# mode S it grew by ~250 MB/s; the phone pushed ~7 GB into zram in 30 s, and lmkd killed
# com.termux.x11 (Stable :1, adj 400 while the experimental Activity was on top) and then
# com.termux itself (PRoot, Claude, adb, X3). safe-run.sh only guards host analysis.
#
# Every period it appends one line to --log:
#   <epoch> mem_available_mb=<n> swap_used_mb=<n> x3_rss_mb=<n> x3_swap_mb=<n>
# and TRIPS when any of these holds:
#   MemAvailable < floor · swap used grew by more than swap-growth since start ·
#   X3 VmRSS + VmSwap > x3-max
# A trip is CONSTRUCTION, recorded in --flag with the reason, the target pid and cmdline:
#   SIGTERM X3 (exact pid, cmdline prefix checked) -> am force-stop com.waydefu.x11gpu ->
#   SIGKILL X3 if still alive after 2 s. Then it exits 3. It exits 0 when X3 ends by itself.
# Test hooks (never set by a runner): MEM_GUARD_TEST_PREFIX replaces the cmdline prefix,
# MEM_GUARD_TEST_NO_ADB=1 skips the force-stop.
set -uo pipefail
PREFIX=${MEM_GUARD_TEST_PREFIX:-"termux-x11gpu com.waydefu.x11gpu :3"}
TADB=/data/data/com.termux/files/usr/bin/adb
X3=""; LOG=""; FLAG=""; SERIAL=""
FLOOR_MB=3000; SWAP_GROWTH_MB=1536; X3_MAX_MB=3072; PERIOD=0.5
while [ $# -gt 0 ]; do
  case $1 in
    --x3-pid) X3=$2; shift 2 ;;
    --log) LOG=$2; shift 2 ;;
    --flag) FLAG=$2; shift 2 ;;
    --serial) SERIAL=$2; shift 2 ;;
    --floor-mb) FLOOR_MB=$2; shift 2 ;;
    --swap-growth-mb) SWAP_GROWTH_MB=$2; shift 2 ;;
    --x3-max-mb) X3_MAX_MB=$2; shift 2 ;;
    --period) PERIOD=$2; shift 2 ;;
    *) echo "MEM_GUARD_USAGE unexpected $1" >&2; exit 2 ;;
  esac
done
[ -n "$X3" ] && [ -n "$LOG" ] && [ -n "$FLAG" ] || { echo "MEM_GUARD_USAGE need --x3-pid --log --flag" >&2; exit 2; }

is_x3() {   # the pid still is the experimental X we were given
  local c
  c=$(tr '\0' ' ' 2>/dev/null < "/proc/$X3/cmdline") || return 1
  case "$c" in "$PREFIX"*) return 0 ;; *) return 1 ;; esac
}
meminfo() {  # -> "avail_mb swap_used_mb"
  awk '/^MemAvailable:/ {a=$2} /^SwapTotal:/ {t=$2} /^SwapFree:/ {f=$2}
       END {printf "%d %d\n", a/1024, (t-f)/1024}' /proc/meminfo
}
x3mem() {    # -> "rss_mb swap_mb" (status is readable for the same uid)
  awk '/^VmRSS:/ {r=$2} /^VmSwap:/ {s=$2} END {printf "%d %d\n", r/1024, s/1024}' \
    "/proc/$X3/status" 2>/dev/null || echo "-1 -1"
}

is_x3 || { echo "MEM_GUARD_REFUSE pid $X3 is not X3" >> "$LOG"; exit 2; }
read -r _ SWAP0 < <(meminfo)
echo "# mem-guard start x3=$X3 floor_mb=$FLOOR_MB swap_growth_mb=$SWAP_GROWTH_MB x3_max_mb=$X3_MAX_MB swap_used0_mb=$SWAP0" >> "$LOG"
while is_x3; do
  read -r AVAIL SWAP < <(meminfo)
  read -r RSS XSW < <(x3mem)
  NOW=$(date +%s.%N)
  echo "$NOW mem_available_mb=$AVAIL swap_used_mb=$SWAP x3_rss_mb=$RSS x3_swap_mb=$XSW" >> "$LOG"
  REASON=""
  [ "$AVAIL" -lt "$FLOOR_MB" ] && REASON="mem_available_mb=$AVAIL<$FLOOR_MB"
  [ -z "$REASON" ] && [ $((SWAP - SWAP0)) -gt "$SWAP_GROWTH_MB" ] && REASON="swap_growth_mb=$((SWAP - SWAP0))>$SWAP_GROWTH_MB"
  [ -z "$REASON" ] && [ "$RSS" -ge 0 ] && [ $((RSS + XSW)) -gt "$X3_MAX_MB" ] && REASON="x3_rss_plus_swap_mb=$((RSS + XSW))>$X3_MAX_MB"
  if [ -n "$REASON" ]; then
    CMD=$(tr '\0' ' ' 2>/dev/null < "/proc/$X3/cmdline")
    {
      echo "MEM_GUARD_TRIPPED reason=$REASON at=$NOW"
      echo "construction: SIGTERM target_pid=$X3 cmdline=$CMD"
    } >> "$FLAG"
    echo "$NOW TRIP $REASON" >> "$LOG"
    is_x3 && kill -TERM "$X3" 2>/dev/null
    if [ "${MEM_GUARD_TEST_NO_ADB:-0}" != 1 ] && [ -n "$SERIAL" ]; then
      env -u ADB_SERVER_SOCKET -u ANDROID_ADB_SERVER_ADDRESS -u ANDROID_ADB_SERVER_PORT \
        "$TADB" -H 127.0.0.1 -P 5038 -s "$SERIAL" shell am force-stop com.waydefu.x11gpu \
        </dev/null >/dev/null 2>&1
      echo "construction: am force-stop com.waydefu.x11gpu" >> "$FLAG"
    fi
    for _ in 1 2 3 4; do is_x3 || break; sleep 0.5; done
    if is_x3; then
      kill -KILL "$X3" 2>/dev/null
      echo "construction: SIGKILL target_pid=$X3 (alive 2 s after SIGTERM)" >> "$FLAG"
    fi
    exit 3
  fi
  sleep "$PERIOD"
done
echo "# mem-guard end: x3 $X3 gone" >> "$LOG"
exit 0
