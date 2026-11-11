#!/bin/bash
# test_autoshard_real.sh <out> [app...] — AUTO-SHARD-01 part D (evidence/session/proot/AUTO-SHARD-01-FREEZE.md).
# From the daily tracer: Xvfb :99 and a private D-Bus, then for each app (default: cursor claude) a proot-fast8
# test tracer with PROOT_F8_AUTOSHARD=1 runs autoshard_real_inner.sh. One app at a time; starts only if
# MemAvailable >= 2.5 GB. Never touches :1, Stable, the experimental app, adb or the user's app profiles.
set -uo pipefail
OUT=${1:?out}; shift
APPS=("${@:-cursor}"); [ $# -gt 0 ] || APPS=(cursor claude)
HERE=$(cd "$(dirname "$0")" && pwd)
B8=/data/data/com.termux/files/home/build/proot-fast/out8/bin/proot-fast8
BASE=/data/data/com.termux/files/usr/tmp/f8-shard
DAILY=$(awk '/^TracerPid/{print $2}' /proc/self/status)
mkdir -p "$OUT"
sha256sum "$B8" "$HERE/f8-shard" "$HERE/autoshard_real_inner.sh" "$HERE/test_autoshard_real.sh" > "$OUT/sha256-before-run.txt"
XP=""
if ! xdpyinfo -display :99 >/dev/null 2>&1; then
  Xvfb :99 -screen 0 1280x800x24 -nolisten tcp > "$OUT/xvfb.log" 2>&1 &
  XP=$!; sleep 2
fi
SOCK=/tmp/autoshard-real-bus-$$
dbus-daemon --session --nofork --address=unix:path=$SOCK > "$OUT/bus.log" 2>&1 &
BP=$!
for i in $(seq 1 50); do [ -S "$SOCK" ] && break; sleep 0.1; done
for a in "${APPS[@]}"; do
  m=$(awk '/^MemAvailable/{print int($2/1024)}' /proc/meminfo)
  if [ "$m" -lt 2500 ]; then echo "app=$a NOT_RUN memavail ${m} MB < 2500" | tee "$OUT/$a.result.txt"; continue; fi
  log=$BASE/autoshard-real-$a-$$.log; : > "$log"
  F8_SHARD_PROOT=$B8 F8_SHARD_TRACER_ENV="PROOT_F8_AUTOSHARD=1 PROOT_F8_AUTOSHARD_LOG=$log PROOT_F8_AUTOSHARD_BIN=$HERE/f8-shard" \
    DBUS_SESSION_BUS_ADDRESS=unix:path=$SOCK timeout 240 env -u F8_SHARDED -u F8_SHARD_EXE -u F8_NO_SHARD \
    "$HERE/f8-shard" /bin/bash "$HERE/autoshard_real_inner.sh" "$OUT/$a" "$a" "$DAILY" "$log" > "$OUT/$a.shard.out" 2> "$OUT/$a.shard.err"
  echo $? > "$OUT/$a.shard.rc"
  cp "$log" "$OUT/$a.autoshard.log"
  cp "$OUT/$a/result.txt" "$OUT/$a.result.txt" 2>/dev/null
  tail -1 "$OUT/$a.result.txt"
done
kill -TERM "$BP" 2>/dev/null; wait "$BP" 2>/dev/null; rm -f "$SOCK"
[ -n "$XP" ] && kill -TERM "$XP" 2>/dev/null
v=AUTOSHARD_REAL_PASS
for a in "${APPS[@]}"; do
  r=$(tail -1 "$OUT/$a.result.txt" 2>/dev/null)
  case "$r" in SMOKE_REAL_PASS) ;; SMOKE_REAL_INVALID|*NOT_RUN*) [ "$v" = AUTOSHARD_REAL_PASS ] && v=AUTOSHARD_REAL_INVALID ;; *) v=AUTOSHARD_REAL_FAIL ;; esac
done
echo "$v" | tee "$OUT/verdict.txt"
