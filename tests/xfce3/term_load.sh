#!/bin/bash
# XFCE-FREEZE-V3 terminal workload. Runs INSIDE xfce4-terminal (-x).
#   term_load.sh <n> <state_dir>
# Prints one fixed-width line every 100 ms (10 lines/s) and exits 0 as soon as
# <state_dir>/close-T<n> exists. The exit closes the window from the CLIENT side,
# which is the clean destroy path the choreography wants - no WM kill, no signal.
# Deterministic content: the only variable field is the line counter.
n=$1; sd=$2
[ -n "$n" ] && [ -d "$sd" ] || exit 64
i=0
pad='abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJ'
while [ ! -e "$sd/close-T$n" ]; do
  printf 'XFCE-T%s %06d %s\n' "$n" "$i" "$pad"
  i=$((i + 1))
  sleep 0.1
done
echo "$i" > "$sd/lines-T$n.$$"
exit 0
