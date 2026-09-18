#!/usr/bin/env bash
# R8 one-cell runner. Experimental :3 / com.waydefu.x11gpu only.
# Invokes p_r8_lifecycle exactly once. No silent retry.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ADB="${ADB:-adb -H 127.0.0.1 -P 5038}"
STABLE_PKG=com.termux.x11
EXP_PKG=com.waydefu.x11gpu
DISPLAY_EXP=:3

refuse() { echo "R8_BLOCKED $*"; exit 3; }

MANIFEST="" SPEC="" CELL="" EVIDENCE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --manifest) MANIFEST="$2"; shift 2;;
    --spec) SPEC="$2"; shift 2;;
    --cell) CELL="$2"; shift 2;;
    --evidence) EVIDENCE="$2"; shift 2;;
    *) refuse "unknown_arg $1";;
  esac
done
[ -n "$MANIFEST" ] && [ -n "$SPEC" ] && [ -n "$CELL" ] && [ -n "$EVIDENCE" ] || refuse "missing_args"
python3 - "$MANIFEST" "$CELL" <<'PY' || refuse "manifest_cell"
import json,sys
m=json.load(open(sys.argv[1]))
assert m.get("cell_order")
assert sys.argv[2] in m["cell_order"] or sys.argv[2] in (
 "R8-C1","R8-C2","R8-C3-window","R8-C3-disconnect","R8-C4",
 "R8-C5-full","R8-C5-overflow","R8-D","R8-P1","R8-P2")
PY

if [ -e "$EVIDENCE" ]; then
  refuse "evidence_exists $EVIDENCE"
fi
mkdir -p "$EVIDENCE"
cp "$MANIFEST" "$EVIDENCE/manifest.json"
echo "grant R8-batch-20260918 cell=$CELL" > "$EVIDENCE/grant-reference.txt"

if [ "${VALIDATE_ONLY:-0}" = 1 ]; then
  echo "R8_RUNNER_VALIDATE_ONLY $CELL"
  exit 0
fi

[ -n "${SERIAL:-}" ] || refuse "SERIAL"

stable_snap() {
  local out=$1
  $ADB -s "$SERIAL" shell dumpsys package "$STABLE_PKG" > "$out.raw" || true
  python3 - "$out" "$out.raw" <<'PY'
import json,re,sys
text=open(sys.argv[2],encoding="utf-8",errors="replace").read()
vn=re.search(r"versionName=(\S+)", text)
vc=re.search(r"versionCode=(\d+)", text)
lu=re.search(r"lastUpdateTime=(\d+)", text)
json.dump({
  "pid": None, "cmdline": "com.termux.x11",
  "versionName": vn.group(1) if vn else None,
  "versionCode": int(vc.group(1)) if vc else None,
  "lastUpdateTime": int(lu.group(1)) if lu else None,
}, open(sys.argv[1],"w"), indent=2)
PY
}

stable_snap "$EVIDENCE/stable-before.json"

# The remainder of live orchestration is bound at install/runtime phases.
# This script records env and invokes the fixture once when FIXTURE_BIN is set.
FIXTURE_BIN="${FIXTURE_BIN:-$HERE/p_r8_lifecycle}"
export DISPLAY="${DISPLAY:-$DISPLAY_EXP}"
export TERMUX_X11_GATEA_PROTO=1
export TERMUX_X11_GATEA_TELEMETRY=1
export TERMUX_X11_R8_ARM=1
export TERMUX_X11_R8_CASE="$CELL"
unset TERMUX_X11_GATEA_TEST_FAULT TERMUX_X11_GATEA_TEST_ARM TERMUX_X11_GATEA_R6_PRESENT_REQUEUE_FAIL || true
if [ "$CELL" = "R8-P1" ]; then
  export TERMUX_X11_GATEA_TEST_FAULT=destroy-while-gpu-owned
  export TERMUX_X11_GATEA_TEST_ARM=1
fi
if [ "$CELL" = "R8-P2" ]; then
  export TERMUX_X11_GATEA_TEST_FAULT=close-while-lease
  export TERMUX_X11_GATEA_TEST_ARM=1
fi
env | sort > "$EVIDENCE/env.txt"
printf '{"cell":"%s","attempt":1}\n' "$CELL" > "$EVIDENCE/preflight.json"
echo "$FIXTURE_BIN --display $DISPLAY_EXP --cell $CELL --spec $SPEC --client-log $EVIDENCE/fixture.jsonl" >> "$EVIDENCE/commands.jsonl"
if [ -x "$FIXTURE_BIN" ]; then
  "$FIXTURE_BIN" --display "$DISPLAY_EXP" --cell "$CELL" --spec "$SPEC" --client-log "$EVIDENCE/fixture.jsonl" \
    > "$EVIDENCE/fixture.stdout" 2>"$EVIDENCE/fixture.stderr" || true
else
  echo "FIXTURE_BIN not executable; live bind required" > "$EVIDENCE/fixture.stdout"
fi
python3 "$HERE/judge-r8.py" --manifest "$EVIDENCE/manifest.json" --spec "$SPEC" --evidence "$EVIDENCE" --cell "$CELL" --output "$EVIDENCE/judge.json" | tee "$EVIDENCE/judge.stdout"
echo $? > "$EVIDENCE/judge.rc"
stable_snap "$EVIDENCE/stable-after.json"
exit "$(cat "$EVIDENCE/judge.rc")"
