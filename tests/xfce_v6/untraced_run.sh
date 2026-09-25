#!/usr/bin/env bash
# Run one Termux-side command OUTSIDE the PRoot tracer (XFCE-V6-BASELINE-01, untraced probe).
# Same mechanism as evidence/session/gate-a-a1/p2-runtime-phase1/runner/start-x3-untraced.sh
# (verified TracerPid 0 for X3): Termux's own `am` asks TermuxService to execute the command,
# so it runs as a child of the Termux app process, not of proot. No adb involved.
#
#   untraced_run.sh <log> <program> [args...]
#     <log>      stdout+stderr of the program; must be under /data/data/com.termux/files/
#                (both sides see that tree at the same path)
#     <program>  absolute path under /data/data/com.termux/files/ (a bionic binary)
#   Arguments must be plain tokens: the command travels through --esa, where ',' splits.
# The command returns as soon as the service accepted the request; the program then runs on
# its own for its own duration (x_rtt2 exits by itself), so nothing is left to kill.
set -euo pipefail
LOG=${1:?log}; shift
PROG=${1:?program}
case "$LOG" in /data/data/com.termux/files/*) ;; *) echo "UNTRACED_REFUSE log_path_not_shared $LOG"; exit 3;; esac
case "$PROG" in /data/data/com.termux/files/*) ;; *) echo "UNTRACED_REFUSE program_not_termux $PROG"; exit 3;; esac
for a in "$@"; do
  case "$a" in *[!A-Za-z0-9_.:/-]*|'') echo "UNTRACED_REFUSE arg_not_plain '$a'"; exit 3;; esac
done
CMD="unset BASH_ENV ENV; exec $* >> $LOG 2>&1 < /dev/null"
echo "untraced_cmd=$CMD"
/data/data/com.termux/files/usr/bin/am startservice --user 0 -n com.termux/com.termux.app.TermuxService \
  -a com.termux.service_execute -d com.termux.file:/data/data/com.termux/files/usr/bin/bash \
  --esa com.termux.execute.arguments "-c,$CMD" --ez com.termux.execute.background true
