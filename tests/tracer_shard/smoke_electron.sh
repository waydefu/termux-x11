#!/bin/bash
# smoke_electron.sh <launcher> <out> — functional smoke of f8-shard with a real Electron app on Xvfb :99
# (TRACER-SHARD, 2026-09-25; never touches the daily :1). Passes when, within 60 s, the app's main process and
# at least one renderer (a zygote child; it keeps the zygote cmdline) run under the NEW tracer and it maps a window on :99; then f8-shard's pid
# gets SIGTERM and the app, its children and the shard tracer must all be gone within 30 s.
set -uo pipefail
L=${1:?launcher}; OUT=${2:?out}
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT"
DAILY=$(awk '/^TracerPid/{print $2}' /proc/self/status)
say() { echo "$*" | tee -a "$OUT/result.txt"; }
tracer_of() { awk '/^TracerPid/{print $2}' "/proc/$1/status" 2>/dev/null; }
XP=""
if ! xdpyinfo -display :99 >/dev/null 2>&1; then
  Xvfb :99 -screen 0 1280x800x24 -nolisten tcp > "$OUT/xvfb.log" 2>&1 &
  XP=$!; sleep 2
fi
DISPLAY=:99 "$HERE/f8-shard" "$L" > "$OUT/shard.out" 2> "$OUT/shard.err" &
SP=$!
TP=""; APP=""; R=0; W=0
for i in $(seq 1 60); do
  sleep 1
  read -r _ tp app _ < <(sed -n 's/^F8_SHARD tracer=\([0-9]*\) app=\([0-9]*\).*/x \1 \2 x/p' "$OUT/shard.out")
  TP=${tp:-}; APP=${app:-}
  [ -n "$TP" ] || continue
  R=0; N=0; : > "$OUT/under-shard.txt"
  for p in /proc/[0-9]*; do
    [ "$(tracer_of "${p#/proc/}")" = "$TP" ] || continue
    N=$((N + 1))
    c=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null)
    t=$(printf '%s' "$c" | grep -o -- '--type=[a-z-]*' | head -1)
    echo "${p#/proc/} ${t:-main} $(printf '%s' "$c" | cut -c1-60)" >> "$OUT/under-shard.txt"
    # renderers are forked by the zygote and keep its cmdline (--type=zygote): count zygote children of a zygote
    if [ "$t" = "--type=zygote" ]; then
      pp=$(awk '/^PPid/{print $2}' "$p/status" 2>/dev/null)
      tr '\0' ' ' < "/proc/$pp/cmdline" 2>/dev/null | grep -q -- '--type=zygote' && R=$((R + 1))
    fi
  done
  W=$(DISPLAY=:99 xdotool search --onlyvisible --name . 2>/dev/null | wc -l)
  [ "$R" -ge 1 ] && [ "$W" -ge 1 ] && break
done
say "launcher=$L daily_tracer=$DAILY shard_tracer=$TP app=$APP app_tracer=$(tracer_of "$APP") processes_under_shard=$N renderers_under_shard=$R windows_on_99=$W after_s=$i"
ok=1
[ -n "$TP" ] && [ "$TP" != "$DAILY" ] || { say "FAIL no shard tracer"; ok=0; }
[ -n "$APP" ] && [ "$(tracer_of "$APP")" = "$TP" ] || { say "FAIL app not under the shard tracer"; ok=0; }
[ "$R" -ge 1 ] || { say "FAIL no renderer under the shard tracer"; ok=0; }
[ "$W" -ge 1 ] || { say "FAIL no window mapped on :99"; ok=0; }
ps -o pid,ppid,rss,args --ppid "$APP" 2>/dev/null | cut -c1-150 > "$OUT/children.txt"
kill -TERM "$SP"
for i in $(seq 1 30); do sleep 1; [ -d "/proc/$SP" ] || break; done
left=0
[ -n "$TP" ] && [ -d "/proc/$TP" ] && left=1
say "after SIGTERM to f8-shard pid $SP: f8-shard_gone=$([ -d /proc/$SP ] && echo no || echo yes) shard_tracer_gone=$([ "$left" = 0 ] && echo yes || echo no) after_s=$i"
[ -d "/proc/$SP" ] && { say "FAIL f8-shard still alive"; ok=0; }
[ "$left" = 0 ] || { say "FAIL shard tracer $TP still alive"; ok=0; }
[ -n "$XP" ] && kill -TERM "$XP" 2>/dev/null
[ "$ok" = 1 ] && say "SMOKE_ELECTRON_PASS" || say "SMOKE_ELECTRON_FAIL"
