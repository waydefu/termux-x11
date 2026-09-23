#!/usr/bin/env bash
# Offline tests for mem-guard.sh against a FAKE X3 (sleep renamed with exec -a). No device.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
G=$HERE/mem-guard.sh
T=$(mktemp -d)
export MEM_GUARD_TEST_PREFIX="memguardfake :3" MEM_GUARD_TEST_NO_ADB=1
fail=0
fake() { bash -c "${1:-}exec -a 'memguardfake :3 test' sleep 60" >/dev/null 2>&1 & echo $!; }
check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }

# 1 no trip: X3 ends by itself -> exit 0, samples logged, no flag
P=$(fake); sleep 0.2
"$G" --x3-pid "$P" --log "$T/1.log" --flag "$T/1.flag" --floor-mb 0 --swap-growth-mb 999999 --x3-max-mb 999999 --period 0.1 &
M=$!; sleep 0.6; kill "$P"; wait "$M"; rc=$?
check no_trip_exit0 '[ $rc -eq 0 ]'
check no_trip_samples '[ "$(grep -c mem_available_mb= "$T/1.log")" -ge 3 ]'
check no_trip_no_flag '[ ! -e "$T/1.flag" ]'

# 2 floor trip: SIGTERM the fake, exit 3, flag names reason + pid + cmdline
P=$(fake); sleep 0.2
"$G" --x3-pid "$P" --log "$T/2.log" --flag "$T/2.flag" --floor-mb 99999999 --period 0.1; rc=$?
sleep 0.2
check floor_trip_exit3 '[ $rc -eq 3 ]'
check floor_trip_killed '[ ! -d /proc/$P ] || grep -q zombie /proc/$P/status 2>/dev/null || ! tr "\0" " " </proc/$P/cmdline | grep -q memguardfake'
check floor_trip_flag 'grep -q "reason=mem_available_mb=" "$T/2.flag" && grep -q "target_pid=$P cmdline=memguardfake :3 test" "$T/2.flag"'

# 3 x3 size trip
P=$(fake); sleep 0.2
"$G" --x3-pid "$P" --log "$T/3.log" --flag "$T/3.flag" --floor-mb 0 --swap-growth-mb 999999 --x3-max-mb -1 --period 0.1; rc=$?
check x3max_trip '[ $rc -eq 3 ] && grep -q "reason=x3_rss_plus_swap_mb=" "$T/3.flag"'

# 4 SIGTERM ignored -> SIGKILL after 2 s, recorded
P=$(fake 'trap "" TERM; '); sleep 0.2
"$G" --x3-pid "$P" --log "$T/4.log" --flag "$T/4.flag" --floor-mb 99999999 --period 0.1; rc=$?
sleep 0.3
check sigkill_escalation '[ $rc -eq 3 ] && grep -q "SIGKILL target_pid=$P" "$T/4.flag"'
check sigkill_dead '! tr "\0" " " 2>/dev/null </proc/$P/cmdline | grep -q memguardfake'

# 5 refuse a pid that is not X3 (never kill a stranger)
sleep 60 & S=$!
"$G" --x3-pid "$S" --log "$T/5.log" --flag "$T/5.flag" --floor-mb 99999999 --period 0.1; rc=$?
check refuse_stranger '[ $rc -eq 2 ] && [ -d /proc/$S ] && [ ! -e "$T/5.flag" ]'
kill "$S" 2>/dev/null
wait 2>/dev/null
rm -rf "$T"
[ $fail -eq 0 ] && echo "MEM_GUARD_TESTS PASS" || echo "MEM_GUARD_TESTS FAIL"
exit $fail
