#!/data/data/com.termux/files/usr/bin/bash
# shard-run.sh <jobdir> — Termux side of f8-shard (TRACER-SHARD, 2026-09-25).
# Started by TermuxService (outside every proot tracer). Re-creates the caller's proot tracer from the job
# files that f8-shard wrote (same binary, same argv, same environment, same rootfs) and runs the guest
# command under it, so the guest gets a tracer of its own. Writes $$ (= the new tracer pid, env and proot
# are exec'd) to <jobdir>/tracer.pid before exec; the guest bash writes its own pid (= the program, it execs)
# to <jobdir>/app.pid, so the caller never has to scan /proc under its own tracer.
# v2 (AUTO-SHARD, 2026-09-26): installed as ~/build/proot-fast/shard2/shard-run.sh, started by shard-daemon.sh
# or TermuxService. It first claims the job (mkdir <jobdir>/claim, atomic): a job f8-shard gave up on, or one
# handed over twice, is never started a second time.
set -u
J=${1:?jobdir}
case "$J" in /data/data/com.termux/files/usr/tmp/f8-shard/*) ;; *) echo "SHARD_RUN_REFUSE jobdir $J"; exit 3;; esac
mkdir "$J/claim" 2>/dev/null || { echo "SHARD_RUN_SKIP already claimed" >> "$J/log"; exit 0; }
for f in tracer.argv tracer.env guest.env guest.argv guest.cwd rootfs; do
  [ -s "$J/$f" ] || { echo "SHARD_RUN_REFUSE missing $f" >> "$J/log"; exit 3; }
done
mapfile -d '' TA < "$J/tracer.argv"
mapfile -d '' TE < "$J/tracer.env"
mapfile -d '' GE < "$J/guest.env"
mapfile -d '' GA < "$J/guest.argv"
GC=$(cat "$J/guest.cwd")
ROOTFS=$(cat "$J/rootfs")
cd "$ROOTFS" || { echo "SHARD_RUN_REFUSE rootfs $ROOTFS" >> "$J/log"; exit 3; }
echo "$$" > "$J/tracer.pid"
# shellcheck disable=SC2016
exec /data/data/com.termux/files/usr/bin/env -i "${TE[@]}" "${TA[@]}" \
  /usr/bin/env -i "${GE[@]}" /bin/bash -c 'echo $$ > "$1/app.pid"; cd -- "$0" 2>/dev/null || cd /root; shift; exec "$@"' "$GC" "$J" "${GA[@]}" \
  >> "$J/log" 2>&1 < /dev/null
