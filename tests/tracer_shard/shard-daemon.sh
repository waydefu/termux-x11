#!/data/data/com.termux/files/usr/bin/bash
# shard-daemon.sh — resident Termux-side helper of f8-shard v2 (AUTO-SHARD, 2026-09-26).
# f8-shard v1 started every shard through `am startservice` (TermuxService): 0.96-1.19 s per call, almost all
# of it the app_process start of `am` itself. Started once that way (f8-shard does it in the background when
# it finds no daemon), this script stays outside every proot tracer, like shard-run.sh, takes job dirs from
# <base>/ctl.fifo and starts shard-run.sh on each at once. Installed next to shard-run.sh (shard2/).
# One instance (flock on <base>/daemon.lock). It holds both ends of the FIFO, so a writer never blocks and the
# daemon never reads EOF; idle it sleeps in read(2). Every 20 jobs it prunes job dirs, keeping the newest 200.
# Fix 3 (2026-09-27): Termux empties $PREFIX/tmp when TermuxService restarts, unlinking daemon.lock and the
# FIFO. A daemon started with `setsid -f` outlives that, so the next f8desk took a lock on a new file and every
# Termux restart left one more daemon asleep on a FIFO no writer can reach (31233, 16178 on 2026-09-27).
# The read now times out every CHECK s and the daemon exits once $BASE/daemon.lock is no longer the file it
# holds. F8_SHARD_BASE / F8_SHARD_DAEMON_CHECK_S are for tests only (f8-shard hard-codes BASE).
# Fix 4 (2026-09-27): shards are started without fd 3 (FIFO) and fd 9 (lock); see the setsid line.
set -u
BASE=${F8_SHARD_BASE:-/data/data/com.termux/files/usr/tmp/f8-shard}
CHECK=${F8_SHARD_DAEMON_CHECK_S:-60}
RUN=$(cd "$(dirname "$0")" && pwd)/shard-run.sh
mkdir -p "$BASE" || exit 3
LOCK=$BASE/daemon.lock
exec 9> "$LOCK"
flock -n 9 || exit 0
FIFO=$BASE/ctl.fifo
[ -p "$FIFO" ] || { rm -f "$FIFO"; mkfifo -m 600 "$FIFO" || exit 3; }
exec 3<> "$FIFO" || exit 3
echo $$ > "$BASE/daemon.pid"
n=0
while :; do
  # A job line is one write(2) of < PIPE_BUF bytes, so a timeout never lands inside a line.
  read -r -t "$CHECK" J <&3; rc=$?
  if [ "$rc" -gt 128 ]; then
    [ /proc/$$/fd/9 -ef "$LOCK" ] || exit 0
    continue
  fi
  [ "$rc" = 0 ] || exit 0
  case "$J" in "$BASE"/2*) ;; *) continue;; esac
  case "$J" in *..*|*' '*) continue;; esac
  [ -d "$J" ] || continue
  # 3>&- 9>&- (fix 4): a shard must not inherit the FIFO or the lock. flock belongs to the open file, so every
  # program of the shard kept holding it: after this daemon died no new one could lock daemon.lock and each
  # shard went through am until the whole shard exited (2026-09-27 10:53, Claude Desktop tree held fd 9).
  setsid "$RUN" "$J" < /dev/null > /dev/null 2>&1 3>&- 9>&- &
  n=$((n + 1))
  if [ $((n % 20)) = 0 ]; then
    ls -1dt "$BASE"/2*/ 2>/dev/null | tail -n +201 | while read -r d; do rm -rf -- "$d"; done
    # proot-fast8 opens the auto-shard log for every line (O_APPEND), so renaming it is safe
    [ "$(stat -c %s "$BASE/autoshard.log" 2>/dev/null || echo 0)" -gt 1048576 ] && mv -f "$BASE/autoshard.log" "$BASE/autoshard.log.1"
  fi
done
