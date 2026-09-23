#!/usr/bin/env bash
# Run one host-side analysis command so that it cannot push Android into killing com.termux.
#
#   safe-run.sh [--cap-mb N] [--floor-mb N] [--disk-min-gb N --disk-path DIR] -- command [args...]
#   safe-run.sh [--floor-mb N] [--disk-min-gb N --disk-path DIR] --check-only
#                  preflight for a device runner: the same refusals, runs nothing
#
# Why (INCIDENT-20260923-LMK-KILLED-TERMUX): this PRoot lives inside the com.termux app
# process tree. Stable :1, the daily XFCE, Claude Desktop and the adb server live there
# too. A collector that read a 2.2 GB logcat whole drove the phone into memory pressure
# and lmkd killed com.termux - all of it at once. Three guards, all mandatory:
#   1. refuse to start when MemAvailable is below the floor (exit 97, prints why)
#   2. cap the address space (RLIMIT_AS): the tool dies with MemoryError / ENOMEM,
#      never the phone
#   3. lowest CPU priority, so the daily desktop keeps the CPU
# and, for runners, a disk floor: a TELEMETRY=1 XFCE capture is ~0.9 GB and the phone's
# storage is shared with the daily machine.
# MemAvailable here is the phone's own (PRoot shows the real /proc/meminfo).
set -euo pipefail
CAP_MB=${SAFE_RUN_CAP_MB:-1024}
FLOOR_MB=${SAFE_RUN_FLOOR_MB:-3072}
DISK_MIN_GB=""; DISK_PATH=""; CHECK_ONLY=0
while [ $# -gt 0 ]; do
  case $1 in
    --cap-mb) CAP_MB=$2; shift 2 ;;
    --floor-mb) FLOOR_MB=$2; shift 2 ;;
    --disk-min-gb) DISK_MIN_GB=$2; shift 2 ;;
    --disk-path) DISK_PATH=$2; shift 2 ;;
    --check-only) CHECK_ONLY=1; shift ;;
    --) shift; break ;;
    *) echo "SAFE_RUN_USAGE unexpected $1" >&2; exit 2 ;;
  esac
done
[ $# -gt 0 ] || [ "$CHECK_ONLY" = 1 ] || { echo "SAFE_RUN_USAGE no command" >&2; exit 2; }
if [ -n "$DISK_MIN_GB" ]; then
  [ -n "$DISK_PATH" ] || { echo "SAFE_RUN_USAGE --disk-min-gb needs --disk-path" >&2; exit 2; }
  free_gb=$(df -P -BG "$DISK_PATH" | awk 'NR==2 {sub("G","",$4); print $4}')
  if [ -z "$free_gb" ] || [ "$free_gb" -lt "$DISK_MIN_GB" ]; then
    echo "SAFE_RUN_REFUSE disk_free_gb=${free_gb:-unknown} min_gb=$DISK_MIN_GB path=$DISK_PATH" >&2
    exit 97
  fi
fi
avail_kb=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
if [ -z "$avail_kb" ]; then
  echo "SAFE_RUN_REFUSE meminfo_unreadable" >&2
  exit 97
fi
avail_mb=$((avail_kb / 1024))
if [ "$avail_mb" -lt "$FLOOR_MB" ]; then
  echo "SAFE_RUN_REFUSE mem_available_mb=$avail_mb floor_mb=$FLOOR_MB cmd=$1" >&2
  exit 97
fi
if [ "$CHECK_ONLY" = 1 ]; then
  echo "SAFE_RUN_CHECK_OK mem_available_mb=$avail_mb floor_mb=$FLOOR_MB disk_free_gb=${free_gb:-unchecked}" >&2
  exit 0
fi
echo "SAFE_RUN mem_available_mb=$avail_mb floor_mb=$FLOOR_MB cap_mb=$CAP_MB cmd=$1" >&2
# nice is relative: this shell may already run below 0 (it did: -8), so aim at absolute 19
exec prlimit --as=$((CAP_MB * 1024 * 1024)) -- nice -n $((19 - $(nice))) "$@"
