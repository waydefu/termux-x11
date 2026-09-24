#!/usr/bin/env bash
# DND-PROBE-01 (evidence/session/proot/DND-PROBE-01.md): does dropping a file whose path is non-ASCII close the target
# window / app, and does the locale matter? Run by evidence/session/gl/run-gl-bench.sh KIND=cmd inside the daily PRoot,
# DISPLAY=:3 (experimental X; the daily desktop :1 is not used).
# Matrix: LANG unset (what the daily desktop has: proot-distro login never reads /etc/default/locale) and LANG=C.UTF-8
#   x file ascii-test.txt / 中文測試.txt  x target cursor (Electron, fresh profile) / xfce4-terminal (GTK control).
# Each case: start target + drag source (dnd_source.py) in their own session, drag with xdotool, wait 8 s, record whether
# the target process and its window survived, then end both sessions by exact pid (CONSTRUCTION, logged).
set -uo pipefail
OUT=${1:?evidence dir}
HERE=$(cd "$(dirname "$0")" && pwd)
D=/root/build/dnd-probe
KILLS="$OUT/dnd-kills.txt"
mkdir -p "$D"
printf 'ascii test\n' > "$D/ascii-test.txt"; printf '中文測試\n' > "$D/中文測試.txt"
echo "TOOLS probe=$(sha256sum "$0" | cut -c1-16) source=$(sha256sum "$HERE/dnd_source.py" | cut -c1-16)"

session_pids() { local p; for p in /proc/[0-9]*; do [ "$(sed 's/.*) //' "$p/stat" 2>/dev/null | awk '{print $4}')" = "$1" ] && echo "${p##*/}"; done; }
end_session() {   # sid label
  local p
  for p in $(session_pids "$1"); do kill -TERM "$p" 2>/dev/null && echo "term $2 sid=$1 pid=$p $(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null | cut -c1-80)" >> "$KILLS"; done
  for _ in $(seq 1 20); do [ -z "$(session_pids "$1")" ] && break; sleep 0.5; done
  for p in $(session_pids "$1"); do kill -KILL "$p" 2>/dev/null && echo "kill $2 sid=$1 pid=$p" >> "$KILLS"; done
}
start() {   # sidfile logfile lang cmd...   (setsid: the new session id is written to sidfile)
  local sidf=$1 log=$2 lang=$3; shift 3
  if [ "$lang" = unset ]; then
    setsid bash -c 'echo $$ > "$0"; shift; exec env -u LANG -u LC_ALL -u LC_CTYPE "$@"' "$sidf" _ "$@" > "$log" 2>&1 < /dev/null &
  else
    setsid bash -c 'echo $$ > "$0"; shift; exec env LANG="$1" "${@:2}"' "$sidf" _ "$lang" "$@" > "$log" 2>&1 < /dev/null &
  fi
  for _ in $(seq 1 50); do [ -s "$sidf" ] && break; sleep 0.1; done
}
win() { xdotool search --onlyvisible "$@" 2>/dev/null | head -1; }

one() {   # name lang file target
  local name=$1 lang=$2 file=$3 target=$4 tsid ssid tw sw alive walive title before
  rm -f "$OUT/$name".*
  case $target in
    cursor)   start "$OUT/$name.tsid" "$OUT/$name.target.log" "$lang" dbus-run-session -- /usr/share/cursor/cursor --no-sandbox \
                --disable-gpu-sandbox --disable-dev-shm-usage --ozone-platform=x11 --disable-gpu --user-data-dir="$D/prof-$name" --new-window ;;
    terminal) start "$OUT/$name.tsid" "$OUT/$name.target.log" "$lang" dbus-run-session -- xfce4-terminal --disable-server \
                --title=dnd-target --working-directory=/root/build/dnd-probe ;;
  esac
  tsid=$(cat "$OUT/$name.tsid")
  for _ in $(seq 1 60); do tw=$( [ $target = cursor ] && win --class Cursor || win --name dnd-target ); [ -n "$tw" ] && break; sleep 1; done
  sleep 8   # let the target finish its first paint (Cursor's welcome page)
  start "$OUT/$name.ssid" "$OUT/$name.source.log" "$lang" python3 "$HERE/dnd_source.py" "$D/$file"
  ssid=$(cat "$OUT/$name.ssid")
  for _ in $(seq 1 30); do sw=$(win --name '^dnd-source$'); [ -n "$sw" ] && break; sleep 0.5; done
  if [ -z "$tw" ] || [ -z "$sw" ]; then
    echo "CASE $name lang=$lang file=$file target=$target result=SETUP_FAIL target_win=${tw:-none} source_win=${sw:-none}"
  else
    xdotool windowsize "$tw" 660 1200 windowmove "$tw" 520 300 2>/dev/null
    xdotool windowsize "$sw" 400 400 windowmove "$sw" 40 500 2>/dev/null; sleep 2
    before=$(xdotool getwindowname "$tw" 2>/dev/null)
    xdotool mousemove 240 700; sleep 0.3; xdotool mousedown 1; sleep 0.3
    for i in $(seq 1 12); do xdotool mousemove $((240 + i * 50)) $((700 + i * 18)); sleep 0.08; done
    sleep 0.8; xdotool mouseup 1
    sleep 8
    alive=$( [ -n "$(session_pids "$tsid")" ] && echo true || echo false )
    walive=$(xdotool getwindowname "$tw" >/dev/null 2>&1 && echo true || echo false)
    title=$(xdotool getwindowname "$tw" 2>/dev/null | tr ' ' '_' | cut -c1-80)
    echo "CASE $name lang=$lang file=$file target=$target target_alive=$alive target_window=$walive" \
         "sent=$(grep -c DND_SOURCE_SENT "$OUT/$name.source.log") drag_end=$(grep -c DND_SOURCE_END "$OUT/$name.source.log")" \
         "title_before=$(echo "$before" | tr ' ' '_' | cut -c1-60) title_after=${title:-none}"
  fi
  end_session "$ssid" "source-$name"; end_session "$tsid" "target-$name"
  sleep 2
}

for target in terminal cursor; do
  for lang in unset C.UTF-8; do
    for f in ascii-test.txt 中文測試.txt; do
      one "$target-$([ $lang = unset ] && echo nolang || echo utf8)-$([ "$f" = ascii-test.txt ] && echo ascii || echo cjk)" "$lang" "$f" "$target"
    done
  done
done
echo DND_PROBE_DONE
