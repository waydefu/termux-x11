#!/bin/bash
# autoshard_inner.sh <out> <work> <set> — AUTO-SHARD-01 cases, run INSIDE a test tracer that test_autoshard.sh
# started (proot-fast8 with PROOT_F8_AUTOSHARD=1, or proot-fast7 for the R1 control). Every probe records its
# own TracerPid under <out>/cells/<set>-<case>/; the judge decides SHARDED / NOT_SHARDED from that.
set -uo pipefail
OUT=${1:?out}; W=${2:?work}; SET=${3:?set}
C=$OUT/cells
mkdir -p "$C"
TT=$(awk '/^TracerPid/{print $2}' /proc/self/status)
echo "$TT" > "$OUT/$SET.test_tracer"
tr '\0' '\n' < "/proc/$TT/cmdline" > "$OUT/$SET.test_tracer.argv" 2>/dev/null
env | grep -E '^F8_' > "$OUT/$SET.inner_f8_env" || true
export W FAKE=$W/fakeapp/fakeelectron HELPER=$W/fakeapp/helper FAKE2=$W/fakeapp2/fakeelectron2
PLAIN=$W/plain/notelectron
FTERM=$W/fterm/xterm
run() {   # run <case> <cmd...>: stdout/stderr go to files (not pipes)
  local c=$1 s; shift
  s=$(date +%s%N)
  "$@" > "$OUT/$SET-$c.stdout" 2> "$OUT/$SET-$c.stderr"
  echo $? > "$OUT/$SET-$c.rc"
  echo $(( ($(date +%s%N) - s) / 1000000 )) > "$OUT/$SET-$c.ms"
}
tracer_of() { awk '/^TracerPid/{print $2}' "/proc/$1/status" 2>/dev/null; }

case "$SET" in
main)
  SUB1='"$FAKE" "$W/probe.sh" "$d/e8" > "$d/e8.out" 2>&1; "$HELPER" "$W/probe.sh" "$d/e9" > "$d/e9.out" 2>&1;
        "$FAKE2" "$W/probe.sh" "$d/e11" > "$d/e11.out" 2>&1'
  run e1 env SUB="$SUB1" "$FAKE" "$W/probe.sh" "$C/main-e1"
  run e2 "$FAKE" "$W/probe.sh" "$C/main-e2" --type=renderer
  run e3 "$FAKE" "$W/probe.sh" "$C/main-e3" --remote-debugging-pipe
  run e4 env ELECTRON_RUN_AS_NODE=1 "$FAKE" "$W/probe.sh" "$C/main-e4"
  run e5 env F8_NO_SHARD=1 "$FAKE" "$W/probe.sh" "$C/main-e5"
  "$FAKE" "$W/probe.sh" "$C/main-e6" | cat > "$OUT/$SET-e6.stdout"
  "$FAKE" "$W/probe.sh" "$C/main-e7" > "$OUT/$SET-e7.stdout" 2> >(cat > "$OUT/$SET-e7.stderr")
  run e10 env F8_SHARDED=1 "$FAKE" "$W/probe.sh" "$C/main-e10"
  run e12 "$PLAIN" "$W/probe.sh" "$C/main-e12"
  run l1 "$FAKE" "$W/probe.sh" "$C/main-l1"
  run x1 env RC=7 "$FAKE" "$W/probe.sh" "$C/main-x1"
  # T1: a "terminal" that leaves a detached job behind and exits at once
  run t1 "$FTERM" "$W/term_probe.sh" "$C/main-t1"
  d=$C/main-t1; sleep 1
  op=$(cat "$d/orphan.pid" 2>/dev/null); tt1=$(cat "$d/tracer" 2>/dev/null)
  {
    echo "orphan_pid=$op orphan_comm=$(cat /proc/$op/comm 2>/dev/null)"
    echo "orphan_alive_after_1s=$([ -n "$op" ] && [ -d /proc/$op ] && echo yes || echo no)"
    echo "orphan_tracer=$(tracer_of "$op")"
    echo "t1_tracer=$tt1 t1_tracer_alive=$([ -n "$tt1" ] && [ -d /proc/$tt1 ] && echo yes || echo no)"
  } > "$d/orphan.txt"
  [ -n "$op" ] && [ -d "/proc/$op" ] && kill -TERM "$op" && echo "killed_orphan=$op" >> "$d/orphan.txt"
  gone=no
  for i in $(seq 1 50); do [ -n "$tt1" ] && [ -d "/proc/$tt1" ] || { gone=yes; break; }; sleep 0.1; done
  echo "t1_tracer_gone_after_kill=$gone after_ds=$i" >> "$d/orphan.txt"
  ;;
f1|f2)
  run "$SET" env RC=7 "$FAKE" "$W/probe.sh" "$C/$SET-$SET"
  ;;
r1)
  run r1 "$FAKE" "$W/probe.sh" "$C/r1-r1"
  ;;
esac
echo done > "$OUT/$SET.inner_done"
