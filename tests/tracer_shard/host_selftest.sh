#!/bin/bash
# host_selftest.sh <outdir> — end-to-end host run of daily_sampler_v3.py for both groups (TRACER-SHARD-01),
# without adb and without the daily :1: the sampler itself runs under a proot-fast7 test tracer (f8-shard with
# F8_SHARD_PROOT), the apps go to Xvfb :99, D-Bus is a private bus under another proot-fast7 tracer.
# Pass = for each group the judge's reasons are ONLY the expected host-mode ones (test_mode, unfrozen shard tools,
# no screen/touch without adb, short windows), i.e. apps-end, tracer identity and both probes check out.
set -uo pipefail
OUT=${1:?outdir}
HERE=$(cd "$(dirname "$0")" && pwd)
B7=/data/data/com.termux/files/home/build/proot-fast/out7/bin/proot-fast7
mkdir -p "$OUT"
XP=""
if ! xdpyinfo -display :99 >/dev/null 2>&1; then
  Xvfb :99 -screen 0 1280x800x24 -nolisten tcp > "$OUT/xvfb.log" 2>&1 &
  XP=$!; sleep 2
fi
SOCK=/tmp/f8-selftest-bus-$$
F8_SHARD_PROOT=$B7 "$HERE/f8-shard" /bin/bash -c "exec dbus-daemon --session --nofork --address=unix:path=$SOCK" > "$OUT/bus.out" 2>&1 &
BP=$!
for i in $(seq 1 50); do [ -S "$SOCK" ] && break; sleep 0.1; done
for g in P S; do
  F8_SHARD_PROOT=$B7 DBUS_SESSION_BUS_ADDRESS=unix:path=$SOCK timeout 300 "$HERE/f8-shard" \
    /usr/bin/python3 "$HERE/daily_sampler_v3.py" --group "$g" --out "$OUT/cap-$g" --test-mode --display :99 \
    --baseline 10 --loaded 40 > "$OUT/sampler-$g.out" 2>&1
  J=$(sed -n 's/.*job=\([^ ]*\).*/\1/p' "$OUT/sampler-$g.out" | head -1)
  [ -n "$J" ] && cp "$J/log" "$OUT/sampler-$g.log" 2>/dev/null
  python3 "$HERE/shard_judge.py" capture "$OUT/cap-$g" "$g" > "$OUT/judge-$g.json"
done
kill -TERM "$BP" 2>/dev/null; wait "$BP" 2>/dev/null; rm -f "$SOCK"
[ -n "$XP" ] && kill -TERM "$XP" 2>/dev/null
python3 - "$OUT" <<'PY'
import json, re, sys
out = sys.argv[1]
expected = re.compile(r"^(test_mode|SHARD_TOOLS not frozen|screen-(pre|post)\.json: not awake|touch recorder self-test failed|"
                      r"(untraced|traced) n \d+ < 768|traced_lat n \d+ < 1920)")
ok = True
for g in ("P", "S"):
    r = json.load(open(f"{out}/judge-{g}.json"))
    other = [w for w in r["why"] if not expected.match(w)]
    apps = json.load(open(f"{out}/cap-{g}/apps-end.json"))
    print(f"group {g}: unexpected reasons {other}")
    print(f"  apps-end {json.dumps({k: {x: v.get(x) for x in ('main_pid', 'tracer_pid', 'renderers', 'shard_tracer_pid')} for k, v in apps.items()})}")
    ok &= not other
print("HOST_SELFTEST_PASS" if ok else "HOST_SELFTEST_FAIL")
PY
