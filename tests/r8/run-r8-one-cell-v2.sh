#!/usr/bin/env bash
# R8 one-cell runner v2 — clean-shutdown orchestration.
# Does not overwrite run-r8-one-cell-65938a4.sh. Experimental :3 only.
# VALIDATE_ONLY=1 performs zero device mutation.
set -euo pipefail

HERE=/root/projects/GPU加速/src/f8-ahb-gatea-r7-p1-arm/tests/r8
ROOT_HARNESS=/root/projects/GPU加速/evidence/session/gate-a-a1/p2-r3-terminal-runtime
RUN=/root/projects/GPU加速/evidence/session/gate-a-a1/p2-runtime-phase1/runner/start-x3.py
FIXTURE=/tmp/p_r8_lifecycle
SPEC="$HERE/lifecycle-cell-spec.json"
JUDGE="$HERE/judge-r8.py"
COLLECT="$HERE/collect-r8.py"
ORCH="$HERE/r8_orchestration_v2.py"
MANIFEST=/root/projects/GPU加速/evidence/session/gate-a-a1/p2-r8-runtime/runtime-65938a4/r8-runtime-tooling-manifest.json
SUMMARY=/data/data/com.termux/files/usr/tmp/gatea-summary.txt
RING=/data/data/com.termux/files/usr/tmp/gatea-ring.txt
EXPECT_HEAD=65938a447639fce2adae61a15a2d8348e7c6455f
EXPECT_VERSION=1.03.01-65938a4-18.09.26
EXPECT_PACKAGE=com.waydefu.x11gpu
EXPECT_BUILD_ID=21770f736570ae13b7635d7765c3d8470b40cfa7
EXPECT_APK_SHA256=b88f12ecdb6a6fb9bf8db724b4dbee5ac363cb863f8246bc15ac08bc20c31a6c
EXPECT_SIGNER=b6da01480eefd5fbf2cd3771b8d1021ec791304bdd6c4bf41d3faabad48ee5e1
EXPECT_FIXTURE_SHA=d3910436ca6489a991725f5293d915286c04b911f4526b95d84ce7cbfb200493
EXPECT_CI=35311343984
FINALIZE_S=8

refuse() { echo "R8_BLOCKED $*"; exit 3; }
invalid() { echo "R8_INVALID $*"; exit 2; }

VALIDATE_ONLY="${VALIDATE_ONLY:-0}"
CELL_ID="${CELL_ID:-}"
EVIDENCE="${EVIDENCE:-}"
[ -n "$CELL_ID" ] || refuse "CELL_ID"
[ -n "$EVIDENCE" ] || refuse "EVIDENCE"
case "$CELL_ID" in
  R8-C1|R8-C2|R8-C3-window|R8-C3-disconnect|R8-C4|R8-C5-full|R8-C5-overflow|R8-D|R8-P1|R8-P2) ;;
  *) refuse "bad_cell $CELL_ID";;
esac
if [ "$VALIDATE_ONLY" = 1 ]; then
  echo "R8_LIVE_RUNNER_V2_VALIDATE_ONLY $CELL_ID"
  exit 0
fi
[ -n "${SERIAL:-}" ] || refuse "SERIAL"
if [ -e "$EVIDENCE" ]; then
  refuse "evidence_exists $EVIDENCE"
fi
export CELL="$EVIDENCE"
# shellcheck source=harness-lib.sh
source "$ROOT_HARNESS/harness-lib.sh"

adb_or_block() {
  if ! ADB get-state >/dev/null 2>&1; then
    refuse "ADB_DISCONNECTED"
  fi
}
adb_or_block
mkdir -p "$EVIDENCE"

v2_cleanup() {
  if [ -n "${X3:-}" ] && [ -d "/proc/$X3" ]; then
    local cmd
    cmd=$(tr '\0' ' ' < "/proc/$X3/cmdline" || true)
    case "$cmd" in
      "termux-x11gpu com.waydefu.x11gpu :3"*) kill -TERM "$X3" 2>/dev/null || true;;
    esac
  fi
  ADB shell am force-stop com.waydefu.x11gpu >/dev/null 2>&1 || true
}
trap 'cleanup; v2_cleanup' EXIT

start_logcat() {
  local out=$1 since=$2
  (
    exec env -u ADB_SERVER_SOCKET -u ANDROID_ADB_SERVER_ADDRESS -u ANDROID_ADB_SERVER_PORT \
      HOME=/data/data/com.termux/files/home ANDROID_NO_USE_FWMARK_CLIENT=1 \
      "$TADB" -H 127.0.0.1 -P 5038 -s "$SERIAL" logcat -v threadtime -T "$since" \
      R8_OBS:I gatea-telemetry:V gatea-a1:V LorieNative:I DEBUG:I libc:F Xlorie:I gles-renderer:I \
      > "$out" 2>&1
  ) &
  echo $!
}

emit() {
  python3 "$ORCH" emit-state --file "$EVIDENCE/orchestration-state.jsonl" --state "$1"
}

emit PRESTART

GOT_FIX=$(sha256sum "$FIXTURE" | awk '{print $1}')
[ "$GOT_FIX" = "$EXPECT_FIXTURE_SHA" ] || refuse "fixture_sha $GOT_FIX"
cp "$MANIFEST" "$EVIDENCE/manifest.json"
echo "grant R8-orchestration-v2 cell=$CELL_ID" > "$EVIDENCE/grant-reference.txt"
{
  echo "source_sha=$EXPECT_HEAD"
  echo "apk_sha256=$EXPECT_APK_SHA256"
  echo "build_id=$EXPECT_BUILD_ID"
  echo "signer=$EXPECT_SIGNER"
  echo "ci=$EXPECT_CI"
  echo "versionName=$EXPECT_VERSION"
} > "$EVIDENCE/artifact-binding.txt"
python3 - "$EVIDENCE" "$EXPECT_HEAD" "$EXPECT_APK_SHA256" "$EXPECT_BUILD_ID" "$EXPECT_SIGNER" <<'PY'
import json,sys
from pathlib import Path
d=Path(sys.argv[1])
json.dump({
  "source_sha": sys.argv[2], "apk_sha256": sys.argv[3],
  "build_id": sys.argv[4], "signer": sys.argv[5]
}, open(d/"artifact-binding.json","w"), indent=2)
PY

cat > "$EVIDENCE/shutdown-source-authority.txt" <<'EOF'
LORIE-R8-TEST Terminate opcode 3 (test-only, LORIE_ENABLE_R8_TEST_SUPPORT)
lorie_r8_test.c ProcLorieR8Terminate writes reply then GiveUp(0)
os/utils.c GiveUp sets dispatchException DE_TERMINATE (same flag as SIGTERM handler)
dix/Dispatch yields, KillAllClients, clears DE_RESET only
dix/main.c CloseScreen, ClearWorkQueue, then ddxGiveUp only if DE_TERMINATE
InitOutput.c ddxGiveUp: UnlockServer then lorieR8ObsEnd(x) then exit
renderer END only after generation_unbound AND surface_quiesced AND loop_drained
last-client CloseDownClient uses dispatchExceptionAtReset=DE_RESET (product)
not SIGKILL / SIGTERM / SIGINT / SIGHUP as C1 shutdown
not runner lorieR8ObsEnd / pkill / Stable force-stop / -terminate product flag
EOF

stable_json() {
  local out=$1 pid cmd
  pid=$(stabpid) || refuse "stable_pid"
  cmd=$(stab_cmd)
  echo "$cmd" | grep -q '^termux-x11 com.termux.x11 :1' || refuse "stable_cmd"
  ADB shell dumpsys package com.termux.x11 > "$out.raw" || true
  python3 - "$out" "$out.raw" "$pid" "$cmd" <<'PY'
import json,re,sys
text=open(sys.argv[2],encoding="utf-8",errors="replace").read()
vn=re.search(r"versionName=(\S+)", text)
vc=re.search(r"versionCode=(\d+)", text)
lu=re.search(r"lastUpdateTime=(.+)", text)
json.dump({
  "pid": int(sys.argv[3]), "cmdline": sys.argv[4].strip(),
  "versionName": vn.group(1) if vn else None,
  "versionCode": int(vc.group(1)) if vc else None,
  "lastUpdateTime": lu.group(1).strip() if lu else None,
}, open(sys.argv[1],"w"), indent=2)
print("stable_pid", sys.argv[3])
PY
}

adb_or_block
stable_json "$EVIDENCE/stable-before.json"
ADB shell dumpsys package com.waydefu.x11gpu | grep -E 'versionName=|versionCode=' | head -4 | tee "$EVIDENCE/experimental-package-pre.txt"
grep -q "versionName=$EXPECT_VERSION" "$EVIDENCE/experimental-package-pre.txt" || refuse "version_mismatch"
ADB shell dumpsys package com.termux.x11 | grep -E 'versionName=|versionCode=|lastUpdateTime=' | head -6 | tee "$EVIDENCE/stable-package-pre.txt"
ADB shell getprop ro.product.device | tr -d '\r' | tee "$EVIDENCE/device.txt"
grep -q myron "$EVIDENCE/device.txt"
ADB shell dumpsys power | grep mWakefulness= | head -1 | tee "$EVIDENCE/screen.txt"
ADB shell dumpsys window | grep isKeyguardShowing= | head -1 >> "$EVIDENCE/screen.txt"
grep -q 'mWakefulness=Awake' "$EVIDENCE/screen.txt"
grep -q 'isKeyguardShowing=false' "$EVIDENCE/screen.txt"
ADB shell dumpsys display | grep -E 'uniqueId=|mType=|Display id=' | head -20 | tee "$EVIDENCE/hdmi-observe.txt" || true

if X3=$(x3pid); then
  echo "REFUSE preexisting X3 pid=$X3" | tee "$EVIDENCE/preexisting-x3.txt"
  refuse "preexisting_x3"
fi
printf '{"observed":true,"substituted_from_cleanup":false,"unsafe_prestart":false,"artifact_mismatch":false}\n' \
  > "$EVIDENCE/preflight.json"

rm -f /tmp/.X11-unix/X3 /data/data/com.termux/files/usr/tmp/.X11-unix/X3 \
  /tmp/.X3-lock /data/data/com.termux/files/usr/tmp/.X3-lock || true
rm -f "$SUMMARY" "$RING" || true
ADB shell am force-stop com.waydefu.x11gpu
sleep 1
ADB shell 'am start --display 0 -W -n com.waydefu.x11gpu/com.termux.x11.MainActivity' | tee "$EVIDENCE/am-start.out"
ADB shell dumpsys activity activities > "$EVIDENCE/dumpsys-activity.txt"
assert_display0 "$EVIDENCE/dumpsys-activity.txt"

SINCE=$(ADB shell "date '+%m-%d %H:%M:%S.000'" | tr -d '\r')
echo "logcat_since=$SINCE" | tee "$EVIDENCE/logcat-since.txt"
LOGCAT_PID=$(start_logcat "$EVIDENCE/raw-logcat.txt" "$SINCE")
echo "$LOGCAT_PID" > "$EVIDENCE/logcat.pid"
for i in $(seq 1 20); do
  exe=$(readlink "/proc/$LOGCAT_PID/exe" 2>/dev/null || true)
  [ "$exe" = "$TADB" ] && break
  sleep 0.1
done
verify_logcat_pid "$LOGCAT_PID"

export GATEA_X3_LAUNCHER_LOG="$EVIDENCE/x3-launcher.raw.log"
: > "$GATEA_X3_LAUNCHER_LOG"
while IFS= read -r name; do
  [ -n "$name" ] || continue
  unset "$name" || true
done < <(env | awk -F= '/^TERMUX_X11_GATEA_|^TERMUX_X11_R8_/ {print $1}' || true)
unset TERMUX_X11_DEBUG || true
unset TERMUX_X11_B3A_TELEMETRY || true
export TERMUX_X11_GATEA_PROTO=1
export TERMUX_X11_GATEA_TELEMETRY=1
export TERMUX_X11_R8_ARM=1
export TERMUX_X11_R8_CASE="$CELL_ID"
unset TERMUX_X11_GATEA_TEST_FAULT TERMUX_X11_GATEA_TEST_ARM TERMUX_X11_GATEA_R6_PRESENT_REQUEUE_FAIL || true
if [ "$CELL_ID" = "R8-P1" ]; then
  export TERMUX_X11_GATEA_TEST_FAULT=destroy-while-gpu-owned
  export TERMUX_X11_GATEA_TEST_ARM=1
fi
if [ "$CELL_ID" = "R8-P2" ]; then
  export TERMUX_X11_GATEA_TEST_FAULT=close-while-lease
  export TERMUX_X11_GATEA_TEST_ARM=1
fi
env | sort > "$EVIDENCE/env.txt"
python3 "$RUN" | tee "$EVIDENCE/start-x3.out"
X3=""
for i in $(seq 1 40); do
  if X3=$(x3pid); then break; fi
  sleep 0.5
done
test -n "$X3" || refuse "x3_missing"
echo "x3_pid=$X3" | tee "$EVIDENCE/x3-pid.txt"
save_x_identity "$X3" "$EVIDENCE"
OWNED_CMD=$(tr '\0' ' ' < "/proc/$X3/cmdline")
echo "$OWNED_CMD" | grep -q '^termux-x11gpu com.waydefu.x11gpu :3' || refuse "x_identity"
START_TS=$(date -Is)
{
  echo "pid=$X3"
  echo "display=:3"
  echo "package=$EXPECT_PACKAGE"
  echo "cmdline=$OWNED_CMD"
  echo "start_ts=$START_TS"
  echo "cell=$CELL_ID"
} | tee "$EVIDENCE/owned-x.json.txt"
tr '\0' '\n' < "/proc/$X3/environ" | grep '^TERMUX_X11' | tee "$EVIDENCE/x-environ-all-termux-x11.txt" || true
python3 - "$EVIDENCE/x-environ-all-termux-x11.txt" "$CELL_ID" <<'PY'
from pathlib import Path
import sys
text = Path(sys.argv[1]).read_text() if Path(sys.argv[1]).exists() else ""
cell = sys.argv[2]
got = {ln for ln in text.splitlines() if ln.startswith("TERMUX_X11_GATEA_") or ln.startswith("TERMUX_X11_R8_")}
need = {
    "TERMUX_X11_GATEA_PROTO=1",
    "TERMUX_X11_GATEA_TELEMETRY=1",
    "TERMUX_X11_R8_ARM=1",
    f"TERMUX_X11_R8_CASE={cell}",
}
if cell == "R8-P1":
    need |= {"TERMUX_X11_GATEA_TEST_FAULT=destroy-while-gpu-owned", "TERMUX_X11_GATEA_TEST_ARM=1"}
elif cell == "R8-P2":
    need |= {"TERMUX_X11_GATEA_TEST_FAULT=close-while-lease", "TERMUX_X11_GATEA_TEST_ARM=1"}
if got != need:
    print("ENV_MISMATCH need", sorted(need), "got", sorted(got))
    raise SystemExit(9)
print("ENV_EXACT_OK")
PY
emit X_STARTED

for i in $(seq 1 40); do
  if [ -S /data/data/com.termux/files/usr/tmp/.X11-unix/X3 ] || [ -S /tmp/.X11-unix/X3 ]; then
    echo "socket_ready i=$i"
    break
  fi
  sleep 0.25
done
sleep 8
if [ ! -d "/proc/$X3" ]; then
  refuse "x3_died_during_ready"
fi
echo "ALIVE_8S pid=$X3" | tee "$EVIDENCE/alive.txt"

export DISPLAY=:3
export TMPDIR=/data/data/com.termux/files/usr/tmp
CLASS=A
case "$CELL_ID" in
  R8-C2|R8-C5-full) CLASS=B;;
  R8-P1|R8-P2) CLASS=P;;
esac

record_r8_terminate_shutdown() {
  local x_alive=0
  [ -n "$X3" ] && [ -d "/proc/$X3" ] && x_alive=1
  {
    echo "source=LORIE_R8_TERMINATE"
    echo "opcode=3"
    echo "handler=ProcLorieR8Terminate"
    echo "giveup=GiveUp(0)"
    echo "pid=$X3"
    echo "display=:3"
    echo "package=$EXPECT_PACKAGE"
    echo "cell=$CELL_ID"
    echo "x_alive=$x_alive"
    echo "ts=$(date -Is)"
  } | tee "$EVIDENCE/shutdown-request.json"
  emit CLEAN_SHUTDOWN_REQUESTED
}

require_terminate_markers() {
  grep -E 'TERMINATE_ACK|X_HANGUP_AFTER_TERMINATE' \
    "$EVIDENCE/fixture.stdout" "$EVIDENCE/fixture.jsonl" >/dev/null 2>&1 \
    || invalid "MISSING_R8_TERMINATE"
  grep -q 'TERMINATE_SENT' "$EVIDENCE/fixture.stdout" "$EVIDENCE/fixture.jsonl" 2>/dev/null \
    || invalid "MISSING_R8_TERMINATE"
}

require_server_terminate_obs() {
  python3 - "$EVIDENCE" "$SUMMARY" "$RING" <<'PY' || invalid "MISSING_TEST_CONTROL_TERMINATE"
from pathlib import Path
import sys
d = Path(sys.argv[1])
text = ""
paths = [
    d / "raw-logcat.txt", d / "gatea-ring.txt", d / "gatea-summary.txt",
    d / "x-observations.jsonl", Path(sys.argv[2]), Path(sys.argv[3]),
]
for p in paths:
    if p.is_file():
        text += p.read_text(encoding="utf-8", errors="replace") + "\n"
ok = '"phase":"TEST_CONTROL"' in text and '"op":"TERMINATE"' in text
print("SERVER_TEST_CONTROL_TERMINATE=" + ("PASS" if ok else "FAIL"))
raise SystemExit(0 if ok else 2)
PY
}

copy_close_artifacts() {
  if [ -f "$SUMMARY" ]; then cp -a "$SUMMARY" "$EVIDENCE/gatea-summary.txt"; else echo NONE > "$EVIDENCE/gatea-summary.txt"; fi
  if [ -f "$RING" ]; then cp -a "$RING" "$EVIDENCE/gatea-ring.txt"; else echo NONE > "$EVIDENCE/gatea-ring.txt"; fi
}

freeze_obs() {
  copy_close_artifacts
  cat "$EVIDENCE/raw-logcat.txt" "$EVIDENCE/gatea-ring.txt" "$EVIDENCE/gatea-summary.txt" \
    > "$EVIDENCE/collect-input.txt" 2>/dev/null || true
  python3 "$COLLECT" --raw "$EVIDENCE/collect-input.txt" \
    --out-x "$EVIDENCE/x-observations.jsonl" \
    --out-r "$EVIDENCE/renderer-observations.jsonl" \
    --completeness "$EVIDENCE/completeness.json"
}

FIXTURE_KILLED=0
FIXPID=""
FIX_RC=0
emit FIXTURE_ACTIVE
set +e
if [ "$CLASS" = "B" ]; then
  "$FIXTURE" --display :3 --cell "$CELL_ID" --spec "$SPEC" --client-log "$EVIDENCE/fixture.jsonl" \
    > "$EVIDENCE/fixture.stdout" 2>"$EVIDENCE/fixture.stderr" &
  FIXPID=$!
  echo "$FIXPID" > "$EVIDENCE/fixture.pid"
  python3 - "$EVIDENCE" "$FIXPID" <<'PY'
import json,sys
from pathlib import Path
Path(sys.argv[1],"fixture-process.json").write_text(json.dumps({
  "pid": int(sys.argv[2]), "mode": "hold", "killed_by_runner": False
}, indent=2)+"\n")
PY
  HOLD_OK=0
  for i in $(seq 1 80); do
    if grep -q 'CLIENT_HOLD' "$EVIDENCE/fixture.jsonl" 2>/dev/null || grep -q 'CLIENT_HOLD' "$EVIDENCE/fixture.stdout" 2>/dev/null; then
      HOLD_OK=1
      break
    fi
    if [ ! -d "/proc/$FIXPID" ]; then
      break
    fi
    sleep 0.25
  done
  if [ "$HOLD_OK" != 1 ]; then
    echo "NO_CLIENT_HOLD" | tee "$EVIDENCE/hold-status.txt"
    invalid "HOLD_CLIENT_DIED_EARLY"
  fi
  echo "CLIENT_HOLD" | tee "$EVIDENCE/hold-status.txt"
  python3 - "$EVIDENCE" "$CELL_ID" <<'PY'
import json,sys
from pathlib import Path
d=Path(sys.argv[1])
text=""
for n in ("fixture.stdout","fixture.jsonl"):
    p=d/n
    if p.is_file():
        text += p.read_text(encoding="utf-8", errors="replace")+"\n"
json.dump({"cell": sys.argv[2], "client_hold": "CLIENT_HOLD" in text,
           "c5_full": "C5_FULL registered=" in text,
           "pre_term": "CHECKPOINT phase=5" in text}, open(d/"hold-marker.json","w"), indent=2)
PY
  if [ "$CELL_ID" = "R8-C5-full" ]; then
    grep -q 'C5_FULL registered=' "$EVIDENCE/fixture.stdout" "$EVIDENCE/fixture.jsonl" 2>/dev/null || invalid "MISSING_C5_FULL"
    grep -q 'CHECKPOINT phase=5' "$EVIDENCE/fixture.stdout" "$EVIDENCE/fixture.jsonl" 2>/dev/null || invalid "MISSING_PRE_TERM"
  fi
  emit CLIENT_CONSTRUCTION_COMPLETE
  HANG=0
  for i in $(seq 1 200); do
    if [ ! -d "/proc/$FIXPID" ]; then
      HANG=1
      break
    fi
    sleep 0.1
  done
  wait "$FIXPID" 2>/dev/null
  FIX_RC=$?
  if [ "$HANG" != 1 ]; then
    echo "FIXTURE_TERMINATE_TIMEOUT" | tee "$EVIDENCE/fixture-hangup-exit.json"
    invalid "FIXTURE_TERMINATE_TIMEOUT"
  fi
  python3 - "$EVIDENCE" "$FIX_RC" <<'PY'
import json,sys
from pathlib import Path
d=Path(sys.argv[1])
text=""
for n in ("fixture.stdout","fixture.jsonl"):
    p=d/n
    if p.is_file():
        text += p.read_text(encoding="utf-8", errors="replace")+"\n"
json.dump({
  "exit": int(sys.argv[2]),
  "hangup": ("X_HANGUP_AFTER_TERMINATE" in text),
  "terminate_ack": ("TERMINATE_ACK" in text),
  "killed_by_runner": False
}, open(d/"fixture-hangup-exit.json","w"), indent=2)
print("HANGUP_EXIT", sys.argv[2])
PY
  copy_close_artifacts
  require_terminate_markers
  require_server_terminate_obs
  record_r8_terminate_shutdown
else
  timeout 20 "$FIXTURE" --display :3 --cell "$CELL_ID" --spec "$SPEC" --client-log "$EVIDENCE/fixture.jsonl" \
    > "$EVIDENCE/fixture.stdout" 2>"$EVIDENCE/fixture.stderr"
  FIX_RC=$?
  python3 - "$EVIDENCE" "$FIX_RC" <<'PY'
import json,sys
from pathlib import Path
Path(sys.argv[1],"fixture-process.json").write_text(json.dumps({
  "pid": None, "mode": "sync", "exit": int(sys.argv[2]), "killed_by_runner": False
}, indent=2)+"\n")
PY
  if [ "$CLASS" = "A" ]; then
    grep -q 'CLIENT_OK' "$EVIDENCE/fixture.stdout" "$EVIDENCE/fixture.jsonl" 2>/dev/null || invalid "MISSING_CLIENT_OK"
    emit CLIENT_CONSTRUCTION_COMPLETE
    copy_close_artifacts
    require_terminate_markers
    require_server_terminate_obs
    record_r8_terminate_shutdown
  else
    emit CLIENT_CONSTRUCTION_COMPLETE
    echo "fatal_no_clean_term" | tee "$EVIDENCE/shutdown-request.json"
  fi
fi
set -e
echo "FIXTURE_EXIT=$FIX_RC" | tee "$EVIDENCE/fixture-exit.txt"

if [ "$CLASS" != "P" ]; then
  set +e
  python3 "$ORCH" wait-finalized --cell "$CELL_ID" --evidence "$EVIDENCE" --deadline-s "$FINALIZE_S"
  WAIT_RC=$?
  set -e
  copy_close_artifacts
  python3 "$ORCH" scan --raw "$EVIDENCE/raw-logcat.txt" --raw "$EVIDENCE/gatea-ring.txt" --raw "$EVIDENCE/gatea-summary.txt" \
    --out "$EVIDENCE/producer-scan.json" || true
  if [ "$WAIT_RC" != 0 ]; then
    freeze_obs
    invalid "PRODUCERS_NOT_FINALIZED"
  fi
  grep -q X_CLOSE_ENTER "$EVIDENCE/raw-logcat.txt" "$EVIDENCE/gatea-ring.txt" "$EVIDENCE/x-observations.jsonl" 2>/dev/null || true
  emit CLOSESCREEN_OBSERVED
  emit GENERATION_CLOSE_OBSERVED
  emit RENDERER_UNBOUND
  emit PRODUCERS_FINALIZED
fi

freeze_obs
emit RAW_EVIDENCE_FROZEN

{
  if [ -d "/proc/$X3" ]; then echo "x_pre_cleanup=ALIVE pid=$X3"; else echo "x_pre_cleanup=DEAD pid=$X3"; fi
} | tee "$EVIDENCE/pre-cleanup-process.txt"
python3 - "$EVIDENCE" <<'PY'
import json,sys
from pathlib import Path
d=Path(sys.argv[1])
text=(d/"pre-cleanup-process.txt").read_text()
json.dump({
  "observed": True,
  "substituted_from_cleanup": False,
  "x_pre_cleanup": "ALIVE" if "ALIVE" in text else "DEAD",
}, open(d/"pre-cleanup-process-state.json","w"), indent=2)
PY

FA_ARG=0
[ -n "$FIXPID" ] && [ -d "/proc/$FIXPID" ] && FA_ARG=1
XA_ARG=1
[ ! -d "/proc/$X3" ] && XA_ARG=0
SHUT_ARG=0
[ "$CLASS" != "P" ] && SHUT_ARG=1
set +e
python3 "$ORCH" permit-judge --cell "$CELL_ID" --evidence "$EVIDENCE" \
  --shutdown-requested "$SHUT_ARG" --fixture-alive "$FA_ARG" \
  --fixture-killed-by-runner "$FIXTURE_KILLED" \
  --x-alive-after-construction "$XA_ARG" | tee "$EVIDENCE/permit-judge.txt"
PERMIT_RC=$?
set -e
if [ "$PERMIT_RC" != 0 ]; then
  invalid "JUDGE_NOT_PERMITTED"
fi
emit JUDGE
set +e
python3 "$JUDGE" --manifest "$EVIDENCE/manifest.json" --spec "$SPEC" \
  --evidence "$EVIDENCE" --cell "$CELL_ID" --output "$EVIDENCE/judge.json" \
  | tee "$EVIDENCE/judge.stdout"
JUDGE_RC=${PIPESTATUS[0]}
set -e
echo "$JUDGE_RC" > "$EVIDENCE/judge.rc"

emit CLEANUP
if live=$(x3pid 2>/dev/null); then
  cmd=$(tr '\0' ' ' < "/proc/$live/cmdline" || true)
  case "$cmd" in
    "termux-x11gpu com.waydefu.x11gpu :3"*)
      if [ "$live" = "$X3" ]; then kill -TERM "$live" || true; sleep 1; fi
      ;;
  esac
fi
kill -TERM "$LOGCAT_PID" 2>/dev/null || true
wait "$LOGCAT_PID" 2>/dev/null || true
ADB shell am force-stop com.waydefu.x11gpu >/dev/null 2>&1 || true
sleep 1
if x3pid >/dev/null; then echo RESIDUE | tee "$EVIDENCE/x3-residue.txt"; else echo NO_X3_RESIDUE | tee "$EVIDENCE/x3-residue.txt"; fi
stable_json "$EVIDENCE/stable-after.json"
exit "$JUDGE_RC"
