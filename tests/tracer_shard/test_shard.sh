#!/bin/bash
# test_shard.sh — host test of f8-shard (TRACER-SHARD, 2026-09-25). No Electron, no device, no adb.
# A tiny probe records what it sees; check() decides SHARDED / NOT_SHARDED from the record.
#   case 1 (must be SHARDED):      the probe run through f8-shard
#   case 2 (must be NOT_SHARDED):  the same probe run directly — the control that has to go red
# Also checked for case 1: same tracer binary (sha256), DISPLAY and cwd carried over, rootfs file visible,
# f8-shard forwards SIGTERM (the probe is killed through f8-shard's pid, not its own).
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
SHARD=${SHARD:-$HERE/f8-shard}
OUT=${1:-/tmp/f8-shard-test-$$}
mkdir -p "$OUT"
DAILY=$(awk '/^TracerPid/{print $2}' /proc/self/status)
DAILY_SHA=$(sha256sum "/proc/$DAILY/exe" | awk '{print $1}')
# probe: record status + env + cwd + a rootfs file, then wait to be terminated
PROBE='d=$0; cat /proc/self/status > "$d/status"; printf %s "${DISPLAY-}" > "$d/display"; pwd > "$d/cwd";
  [ -x /root/.local/bin/cursor-gpu ] && echo yes > "$d/rootfs_visible"; trap "echo term > \"$d/term\"; exit 0" TERM;
  for i in $(seq 1 300); do sleep 0.1; done; echo timeout > "$d/term"'
check() {   # check <dir> -> SHARDED | NOT_SHARDED | BROKEN
  local d=$1 tp
  tp=$(awk '/^TracerPid/{print $2}' "$d/status" 2>/dev/null)
  [ -n "$tp" ] || { echo BROKEN; return; }
  if [ "$tp" = 0 ]; then echo BROKEN; elif [ "$tp" = "$DAILY" ]; then echo NOT_SHARDED; else echo SHARDED; fi
}
fail=0
say() { echo "$*" | tee -a "$OUT/result.txt"; }

# case 1: through f8-shard, from a different cwd, with a marker DISPLAY
mkdir -p "$OUT/c1"; cd /usr/share || exit 1
DISPLAY=:77 "$SHARD" /bin/bash -c "$PROBE" "$OUT/c1" > "$OUT/c1/shard.out" 2> "$OUT/c1/shard.err" &
SP=$!
for i in $(seq 1 100); do [ -s "$OUT/c1/status" ] && [ -s "$OUT/c1/shard.out" ] && break; sleep 0.1; done
sleep 0.3
V1=$(check "$OUT/c1"); TP1=$(awk '/^TracerPid/{print $2}' "$OUT/c1/status" 2>/dev/null)
SHA1=$( [ -n "$TP1" ] && sha256sum "/proc/$TP1/exe" 2>/dev/null | awk '{print $1}')
kill -TERM "$SP"; wait "$SP"; RC1=$?
sleep 0.5
say "case1 verdict=$V1 tracer=$TP1 daily=$DAILY shard_out=$(cat "$OUT/c1/shard.out")"
[ "$V1" = SHARDED ] || { say "FAIL case1 not sharded"; fail=1; }
[ "$SHA1" = "$DAILY_SHA" ] && say "ok same tracer sha ${SHA1:0:8}" || { say "FAIL tracer sha ${SHA1:0:8} != ${DAILY_SHA:0:8}"; fail=1; }
[ "$(cat "$OUT/c1/display" 2>/dev/null)" = ":77" ] && say "ok DISPLAY carried" || { say "FAIL DISPLAY '$(cat "$OUT/c1/display" 2>/dev/null)'"; fail=1; }
[ "$(cat "$OUT/c1/cwd" 2>/dev/null)" = "/usr/share" ] && say "ok cwd carried" || { say "FAIL cwd '$(cat "$OUT/c1/cwd" 2>/dev/null)'"; fail=1; }
[ -s "$OUT/c1/rootfs_visible" ] && say "ok rootfs visible" || { say "FAIL rootfs file not visible"; fail=1; }
[ "$(cat "$OUT/c1/term" 2>/dev/null)" = "term" ] && say "ok SIGTERM forwarded (f8-shard rc=$RC1)" || { say "FAIL SIGTERM not forwarded term='$(cat "$OUT/c1/term" 2>/dev/null)'"; fail=1; }
[ -n "$TP1" ] && [ -d "/proc/$TP1" ] && { say "FAIL shard tracer $TP1 still alive after the program ended"; fail=1; } || say "ok shard tracer gone"

# case 2: the same probe run directly — must be NOT_SHARDED
mkdir -p "$OUT/c2"
setsid /bin/bash -c "$PROBE" "$OUT/c2" &
DP=$!
for i in $(seq 1 50); do [ -s "$OUT/c2/status" ] && break; sleep 0.1; done
V2=$(check "$OUT/c2"); kill -TERM "$DP"; wait "$DP" 2>/dev/null
say "case2 verdict=$V2 (control, must be NOT_SHARDED)"
[ "$V2" = NOT_SHARDED ] || { say "FAIL control did not go red"; fail=1; }

[ "$fail" = 0 ] && say "TEST_SHARD_PASS" || say "TEST_SHARD_FAIL"
exit "$fail"
