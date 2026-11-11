#!/bin/bash
# test_autoshard.sh <out> — AUTO-SHARD-01 parts A/B/C (evidence/session/proot/AUTO-SHARD-01-FREEZE.md).
# Run from the daily desktop tracer. Each case set runs inside its own TEST tracer, started through f8-shard's
# F8_SHARD_PROOT / F8_SHARD_TRACER_ENV hooks: main, f1 (broken f8-shard), f2 (missing f8-shard) on proot-fast8
# with PROOT_F8_AUTOSHARD=1, r1 on proot-fast7 (no rule: the control that has to go red). Fake apps are copies
# of /bin/bash under /tmp; the only processes started are those copies, sleep and the tracers. Never touches
# :1, Stable, the experimental app or adb.
set -uo pipefail
OUT=${1:?out}
HERE=$(cd "$(dirname "$0")" && pwd)
B8=/data/data/com.termux/files/home/build/proot-fast/out8/bin/proot-fast8
B7=/data/data/com.termux/files/home/build/proot-fast/out7/bin/proot-fast7
BASE=/data/data/com.termux/files/usr/tmp/f8-shard
TS=$(date +%Y%m%d-%H%M%S)
W=/tmp/autoshard-work-$TS
mkdir -p "$OUT" "$W"/fakeapp "$W"/fakeapp2 "$W"/plain "$W"/fterm "$BASE"
cp /bin/bash "$W/fakeapp/fakeelectron"; cp /bin/bash "$W/fakeapp/helper"; : > "$W/fakeapp/v8_context_snapshot.bin"
cp /bin/bash "$W/fakeapp2/fakeelectron2"; : > "$W/fakeapp2/v8_context_snapshot.bin"
cp /bin/bash "$W/plain/notelectron"
cp /bin/bash "$W/fterm/xterm"
cat > "$W/probe.sh" <<'EOF'
d=$1; mkdir -p "$d"
cat /proc/self/status > "$d/status"
printf '%s' "${F8_SHARD_EXE-}" > "$d/shard_exe"; printf '%s' "${F8_SHARDED-}" > "$d/sharded"
tp=$(awk '/^TracerPid/{print $2}' /proc/self/status); echo "$tp" > "$d/tracer"
tr '\0' '\n' < "/proc/$tp/cmdline" > "$d/tracer.argv" 2>/dev/null
S=${SUB-}; unset SUB
[ -n "$S" ] && eval "$S"
exit "${RC:-0}"
EOF
cat > "$W/term_probe.sh" <<'EOF'
d=$1; mkdir -p "$d"
cat /proc/self/status > "$d/status"
tp=$(awk '/^TracerPid/{print $2}' /proc/self/status); echo "$tp" > "$d/tracer"
tr '\0' '\n' < "/proc/$tp/cmdline" > "$d/tracer.argv" 2>/dev/null
setsid nohup sleep 20 > /dev/null 2>&1 < /dev/null &
echo $! > "$d/orphan.pid"
exit 0
EOF
sed 's|^RUN=.*|RUN=/nonexistent/shard-run.sh|' "$HERE/f8-shard" > "$W/f8-shard-broken"; chmod 755 "$W/f8-shard-broken"
{
  echo "ts=$TS work=$W"
  sha256sum "$B8" "$B7" "$HERE/f8-shard" "$HERE/shard-run.sh" "$HERE/shard-daemon.sh" "$HERE/autoshard_inner.sh" \
    "$HERE/test_autoshard.sh" "$HERE/autoshard_judge.py" \
    /data/data/com.termux/files/home/build/proot-fast/shard2/shard-run.sh \
    /data/data/com.termux/files/home/build/proot-fast/shard2/shard-daemon.sh
} > "$OUT/sha256-before-run.txt"
start() {   # start <set> <tracer binary> <extra tracer env>
  local log=$BASE/autoshard-test-$TS-$1.log
  : > "$log"
  F8_SHARD_PROOT=$2 F8_SHARD_TRACER_ENV="PROOT_F8_AUTOSHARD=1 PROOT_F8_AUTOSHARD_LOG=$log $3" \
    timeout 180 env -u F8_SHARDED -u F8_SHARD_EXE -u F8_NO_SHARD \
    "$HERE/f8-shard" /bin/bash "$HERE/autoshard_inner.sh" "$OUT" "$W" "$1" > "$OUT/$1.shard.out" 2> "$OUT/$1.shard.err"
  echo $? > "$OUT/$1.shard.rc"
  cp "$log" "$OUT/$1.autoshard.log"
}
start main "$B8" "PROOT_F8_AUTOSHARD_BIN=$HERE/f8-shard"
start f1 "$B8" "PROOT_F8_AUTOSHARD_BIN=$W/f8-shard-broken"
start f2 "$B8" "PROOT_F8_AUTOSHARD_BIN=/nonexistent/f8-shard"
start r1 "$B7" "PROOT_F8_AUTOSHARD_BIN=$HERE/f8-shard"
echo "$W" > "$OUT/work"
python3 "$HERE/autoshard_judge.py" "$OUT" | tee "$OUT/judge.txt"
