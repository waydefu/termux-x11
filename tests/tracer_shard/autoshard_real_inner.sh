#!/bin/bash
# autoshard_real_inner.sh <out> <app> <daily_tracer> <rule_log> — AUTO-SHARD-01 part D, run INSIDE a proot-fast8
# test tracer with PROOT_F8_AUTOSHARD=1 (test_autoshard_real.sh starts it). Execs the app's Electron binary
# directly (stdout to a file, so the rule applies), on Xvfb :99 with a private D-Bus and an empty HOME.
# Checks: the shard tracer S and main pid that f8-shard reports (<job>/f8-shard.out) — S is neither this tracer
# nor the daily one and main runs under it; the rule log has exactly one "shard electron" line for the app's
# directory (nothing of the app was sharded a second time); at least one renderer under S; a window on :99.
# Then SIGTERM to the f8-shard pid: every process under S whose cmdline names the app binary or has --type=
# must be gone within 30 s; what is left under S is listed (info) and ended by exact pid.
# v2 (after real-01): Chromium's setproctitle rewrites /proc/<pid>/cmdline into one string and leaves
# /proc/<pid>/environ unreadable garbage, so processes are found by tracer, never by environment; cmdlines are
# matched with NUL turned into spaces. Builtins only inside the per-process loops.
# Memory: MemAvailable checked every second, below 1.5 GB the app is stopped and the case is INVALID.
set -uo pipefail
OUT=${1:?out}; APPN=${2:?app}; DAILY=${3:?daily}; RULELOG=${4:?rule log}
mkdir -p "$OUT"
BASE=/data/data/com.termux/files/usr/tmp/f8-shard
TT=$(awk '/^TracerPid/{print $2}' /proc/self/status)
H=/tmp/autoshard-real-home-$APPN-$$
mkdir -p "$H/.config" "$H/.local/share" "$H/.cache"
case "$APPN" in
  cursor) BIN=/usr/share/cursor/cursor
          ARGS=(--no-sandbox --disable-gpu-sandbox --disable-dev-shm-usage "--user-data-dir=$H/cursor-data" --disable-gpu) ;;
  claude) BIN=/usr/lib/claude-desktop/claude-desktop
          ARGS=(--password-store=basic --no-sandbox --disable-gpu --disable-dev-shm-usage --ozone-platform=x11) ;;
  *) echo "unknown app" > "$OUT/result.txt"; exit 2 ;;
esac
APPDIR=${BIN%/*}
say() { echo "$*" | tee -a "$OUT/result.txt"; }
memavail_mb() { local k v _; while read -r k v _; do [ "$k" = MemAvailable: ] && { echo $((v / 1024)); return; }; done < /proc/meminfo; }
tracer_of() { local k v _; while read -r k v _; do [ "$k" = TracerPid: ] && { echo "$v"; return; }; done < "/proc/$1/status" 2>/dev/null; }
say "app=$APPN bin=$BIN test_tracer=$TT daily=$DAILY memavail_mb=$(memavail_mb)"
HOME=$H XDG_CONFIG_HOME=$H/.config XDG_DATA_HOME=$H/.local/share XDG_CACHE_HOME=$H/.cache DISPLAY=:99 \
  "$BIN" "${ARGS[@]}" > "$OUT/app.stdout" 2> "$OUT/app.stderr" &
SP=$!
echo "$SP" > "$OUT/f8-shard.pid"
J=""; S=""; MAIN=""; R=0; WIN=0; N=0; invalid=""
find_job() {   # newest electron job whose f8-shard ran under THIS tracer and reported
  local d
  for d in $(ls -1dt "$BASE"/2*/ 2>/dev/null | head -30); do
    d=${d%/}
    [ "$(cat "$d/parent-tracer.pid" 2>/dev/null)" = "$TT" ] && [ "$(cat "$d/mode" 2>/dev/null)" = electron ] || continue
    grep -q "^F8_SHARD tracer=" "$d/f8-shard.out" 2>/dev/null && { echo "$d"; return; }
  done
}
scan() {   # processes under S -> <out>/procs.txt "pid type cmdline..."; N, R
  local p pid tp c cs t pp pc k v
  N=0; R=0; : > "$OUT/procs.txt"
  [ -n "$S" ] || return 0
  for p in /proc/[0-9]*; do
    pid=${p#/proc/}
    tp=$(tracer_of "$pid")
    [ "$tp" = "$S" ] || continue
    mapfile -d '' c < "$p/cmdline" 2>/dev/null || continue
    cs="${c[*]}"
    t=main; [[ $cs =~ (--type=[a-z-]+) ]] && t=${BASH_REMATCH[1]}
    N=$((N + 1))
    echo "$pid $t ${cs:0:100}" >> "$OUT/procs.txt"
    if [ "$t" = --type=renderer ]; then
      R=$((R + 1))
    elif [ "$t" = --type=zygote ]; then
      pp=""; while read -r k v _; do [ "$k" = PPid: ] && { pp=$v; break; }; done < "$p/status"
      mapfile -d '' pc < "/proc/$pp/cmdline" 2>/dev/null
      [[ "${pc[*]}" == *--type=zygote* ]] && R=$((R + 1))
    fi
  done
}
for i in $(seq 1 60); do
  sleep 1
  m=$(memavail_mb)
  if [ "$m" -lt 1500 ]; then invalid="memavail ${m} MB < 1500"; break; fi
  if [ -z "$S" ]; then
    J=$(find_job)
    [ -n "$J" ] && read -r S MAIN < <(sed -n 's/^F8_SHARD tracer=\([0-9]*\) app=\([0-9]*\).*/\1 \2/p' "$J/f8-shard.out")
  fi
  scan
  WIN=$(DISPLAY=:99 xdotool search --onlyvisible --name . 2>/dev/null | wc -l)
  [ -n "$S" ] && [ "$R" -ge 1 ] && [ "$WIN" -ge 1 ] && break
done
SHARDS=$(grep -c " shard electron $APPDIR/" "$RULELOG" 2>/dev/null)
say "after_s=$i job=$J main=$MAIN main_tracer=$(tracer_of "$MAIN") shard_tracer=$S processes_under_shard=$N renderers=$R windows_on_99=$WIN rule_log_shard_lines=$SHARDS memavail_mb=$(memavail_mb)"
cp "$OUT/procs.txt" "$OUT/procs-up.txt"
ok=1
[ -z "$invalid" ] || { say "INVALID $invalid"; ok=0; }
[ -n "$S" ] && [ "$S" != "$TT" ] && [ "$S" != "$DAILY" ] && [ "$S" != 0 ] || { say "FAIL no shard tracer (S='$S')"; ok=0; }
[ -n "$MAIN" ] && [ "$(tracer_of "$MAIN")" = "$S" ] || { say "FAIL main '$MAIN' not under the shard tracer"; ok=0; }
[ "$SHARDS" = 1 ] || { say "FAIL rule log has $SHARDS 'shard electron' lines for $APPDIR (want exactly 1)"; ok=0; }
[ "$R" -ge 1 ] || { say "FAIL no renderer under the shard tracer"; ok=0; }
[ "$WIN" -ge 1 ] || { say "FAIL no window on :99"; ok=0; }
kill -TERM "$SP" 2>/dev/null
for i in $(seq 1 30); do
  sleep 1; scan
  alive=$(grep -c -e " --type=" -e " main $BIN" "$OUT/procs.txt")
  [ "$alive" = 0 ] && break
done
say "after SIGTERM to f8-shard $SP: app_main_and_type_left=$alive other_left_under_shard=$(( $(grep -c . "$OUT/procs.txt") - alive )) after_s=$i f8-shard_gone=$([ -d /proc/$SP ] && echo no || echo yes)"
[ "$alive" = 0 ] || { say "FAIL app processes still alive"; ok=0; cat "$OUT/procs.txt" >> "$OUT/result.txt"; }
if [ -n "$S" ] && [ -d "/proc/$S" ]; then
  cp "$OUT/procs.txt" "$OUT/leftover.txt"
  say "info: shard tracer $S still alive with $(grep -c . "$OUT/leftover.txt") process(es) under it (leftover.txt); ending them by exact pid"
  while read -r lp _; do kill -TERM "$lp" 2>/dev/null && echo "killed $lp" >> "$OUT/leftover.txt"; done < "$OUT/procs.txt"
  for i in $(seq 1 20); do sleep 0.5; [ -d "/proc/$S" ] || break; done
  say "info: shard tracer gone after cleanup: $([ -d /proc/$S ] && echo no || echo yes)"
else
  say "info: shard tracer gone on its own"
fi
if [ -n "$invalid" ]; then say "SMOKE_REAL_INVALID"; elif [ "$ok" = 1 ]; then say "SMOKE_REAL_PASS"; else say "SMOKE_REAL_FAIL"; fi
rm -rf "$H"
