#!/system/bin/sh
# f8 blackbox - phone-side flight recorder for "the whole desktop froze and I had to reboot".
#
#   adb push bb.sh bb-diff.awk /data/local/tmp/f8-blackbox/
#   adb shell 'cd /data/local/tmp/f8-blackbox && setsid nohup sh bb.sh >/dev/null 2>&1 < /dev/null &'
#   stop:  adb shell touch /data/local/tmp/f8-blackbox/STOP
#
# Why (2026-09-23): twice the desktop froze and the user rebooted the phone
# (sys.boot.reason = reboot,userrequested at 19:09:42 and 19:39:28). A reboot empties logcat
# and every host-side log froze with PRoot, so nothing showed what happened in the minutes
# before. This runs as the adb shell user, OUTSIDE com.termux and outside the PRoot tracer,
# and writes to phone storage, so a PRoot freeze cannot stop it and a reboot cannot erase it.
#
# It only reads. Every PERIOD seconds one line in bb.log:
#   <epoch> avail_mb free_mb cached_mb swap_used_mb busy_cores proot_cores proot_cs_per_s
#           majflt_per_s nprocs termux_procs top=<comm>:<pid>:<cores>,...
# proot_cs_per_s = voluntary context switches of all proot tracers per second: the tracer
# wakes once per ptrace stop it serves, so this is its request rate.
# Kill / low-memory / ANR events stream to events.log (logcat rotating files).
# bb.log rotates at 16 MB (one .1 kept): ~150 bytes x 720/h ~ 0.1 MB/h.
PERIOD=${BB_PERIOD:-5}
D=$(cd "$(dirname "$0")" && pwd)
cd "$D" || exit 1
if [ -f pid ] && kill -0 "$(cat pid)" 2>/dev/null; then echo "BB_ALREADY_RUNNING $(cat pid)"; exit 0; fi
echo $$ > pid
rm -f STOP
HZ=$(getconf CLK_TCK)
echo "# bb start $EPOCHREALTIME pid=$$ period=$PERIOD hz=$HZ" >> bb.log
logcat -b events -b main -b system -v epoch -f "$D/events.log" -r 2048 -n 4 \
  killinfo:I am_kill:I am_proc_died:I am_low_memory:I am_anr:I lowmemorykiller:I lmkd:I '*:S' &
LC=$!
cpu() { head -1 /proc/stat | awk '{b=$2+$3+$4+$7+$8+$9; print b, b+$5+$6}'; }
pcs() { s=0; for p in $(pidof proot); do
          v=$(awk '/^voluntary_ctxt_switches/ {print $2}' /proc/$p/status 2>/dev/null); s=$((s + ${v:-0}))
        done; echo $s; }
cat /proc/[0-9]*/stat > .prev 2>/dev/null
set -- $(cpu); B0=$1; T0=$2; C0=$(pcs); E0=$EPOCHREALTIME
while [ ! -f STOP ]; do
  sleep "$PERIOD"
  cat /proc/[0-9]*/stat > .cur 2>/dev/null
  set -- $(cpu); B1=$1; T1=$2; C1=$(pcs); E1=$EPOCHREALTIME
  DT=$(awk -v a="$E0" -v b="$E1" 'BEGIN {printf "%.3f", b - a}')
  MEM=$(awk '/^MemAvailable:/ {a=$2} /^MemFree:/ {f=$2} /^Cached:/ {c=$2} /^SwapTotal:/ {st=$2} /^SwapFree:/ {sf=$2}
             END {printf "avail_mb=%d free_mb=%d cached_mb=%d swap_used_mb=%d", a/1024, f/1024, c/1024, (st-sf)/1024}' /proc/meminfo)
  BUSY=$(awk -v b="$((B1 - B0))" -v t="$((T1 - T0))" 'BEGIN {printf "%.2f", (t > 0) ? b / t * 8 : -1}')
  CS=$(awk -v c="$((C1 - C0))" -v d="$DT" 'BEGIN {printf "%.0f", (d > 0 && c >= 0) ? c / d : -1}')
  TP=$(pidof com.termux); TU=$( [ -n "$TP" ] && ps -o USER= -p "$TP" )
  TN=$( [ -n "$TU" ] && ps -A -o USER= | grep -c "^$TU\$" || echo -1)
  DIFF=$(awk -v dt="$DT" -v hz="$HZ" -v top=8 -f bb-diff.awk .prev .cur)
  echo "$E1 $MEM busy_cores=$BUSY proot_cs_per_s=$CS termux_procs=$TN $DIFF" >> bb.log
  mv .cur .prev; B0=$B1; T0=$T1; C0=$C1; E0=$E1
  if [ "$(stat -c %s bb.log)" -gt 16777216 ]; then mv bb.log bb.log.1; fi
done
kill "$LC" 2>/dev/null
echo "# bb stop $EPOCHREALTIME" >> bb.log
rm -f pid STOP
