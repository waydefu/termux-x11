#!/usr/bin/env bash
# TRACER-SHARD-01 series (evidence/session/proot/TRACER-SHARD-01-FREEZE.md section 5). One command runs, in order:
#   P S S P P S   -> each: wait for MemAvailable >= 4600 MB held 20 s (max 10 min) and Cursor/Hermes gone,
#                    then daily_sampler_v3.py --group <g>
#   replacements for INVALID captures (at most 2, same group, in the order they failed)
#   series.json + series-judge.json (shard_judge.py series)
# Sampler rc 3 (refused) stops the series (no silent retry); rc 5 is kept and judged like any capture.
#   SERIAL  live-discovered host:port on lane 5038      OUT  new directory
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
: "${SERIAL:?}"; : "${OUT:?}"
[ -e "$OUT" ] && { echo "SERIES_REFUSE out_exists $OUT"; exit 3; }
mkdir -p "$OUT"
sha256sum "$0" "$HERE"/*.py "$HERE/f8-shard" /data/data/com.termux/files/home/build/proot-fast/shard/shard-run.sh \
  "$HERE"/../xfce_v6/*.py > "$OUT/series-tools.sha256.txt"
LOG=$OUT/series.log
say() { echo "$(date +%T) $*" | tee -a "$LOG"; }
CAPS=()
apps_gone() {   # one python scan (no per-process forks under the daily tracer)
  python3 -c "import sys; sys.path.insert(0, '$HERE/../xfce_v6'); import daily_sampler_v2 as V
m = V.main_processes(); sys.exit(1 if m.get('cursor') or m.get('hermes') else 0)"
}
ready_wait() {   # MemAvailable >= 4600 MB for 20 s in a row (one awk per second, as series-v6.sh), then apps gone
  local ok=0 i m
  for i in $(seq 1 600); do
    m=$(awk '/^MemAvailable/{print int($2/1024)}' /proc/meminfo)
    if [ "$m" -ge 4600 ]; then ok=$((ok + 1)); else ok=0; fi
    if [ "$ok" -ge 20 ]; then apps_gone && return 0; ok=0; fi
    sleep 1
  done
  return 1
}
write_series() {
  python3 - "$OUT/series.json" "${CAPS[@]}" <<'PY'
import json, sys
caps = [dict(zip(("name", "group", "dir"), c.split(" ", 2))) for c in sys.argv[2:]]
json.dump({"captures": caps}, open(sys.argv[1], "w"), indent=2)
PY
}
stop_series() { say "SERIES_STOP $*"; write_series; exit 3; }
capture() {   # capture <name> <group>
  local name=$1 group=$2 d=$OUT/$1 rc
  ready_wait || stop_series "memory/apps never ready for 20 s before $name"
  say "START $name group=$group"
  python3 "$HERE/daily_sampler_v3.py" --group "$group" --out "$d" --serial "$SERIAL" > "$d.sampler.log" 2>&1
  rc=$?
  say "CAPTURE $name rc=$rc $(tail -1 "$d.sampler.log")"
  [ "$rc" = 3 ] && stop_series "sampler refused at $name"
  CAPS+=("$name $group $d")
}
declare -A K=([P]=0 [S]=0)
for g in P S S P P S; do K[$g]=$((K[$g] + 1)); capture "shard-${g,,}-0${K[$g]}" "$g"; done
R=0
for c in "${CAPS[@]}"; do
  read -r name group dir <<< "$c"
  [ "$R" -ge 2 ] && break
  v=$(python3 "$HERE/shard_judge.py" capture "$dir" "$group" | python3 -c "import json,sys;print(json.load(sys.stdin)['valid'])")
  if [ "$v" != True ]; then R=$((R + 1)); say "INVALID $name -> replacement r$R"; capture "shard-${group,,}-r$R" "$group"; fi
done
write_series
python3 "$HERE/shard_judge.py" series "$OUT/series.json" > "$OUT/series-judge.json" 2>&1
say "SHARD_SERIES $(python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(d.get('verdict'),d.get('T0'),d.get('T1'),d.get('T2'),d.get('E'))" "$OUT/series-judge.json" 2>/dev/null)"
echo TRACER_SHARD_SERIES_DONE
