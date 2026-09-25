#!/bin/bash
# test_dbus_cross.sh <proot-binary> <out> — D-Bus EXTERNAL auth across two proot tracers (TRACER-SHARD, 2026-09-25).
# Two fresh tracers of <proot-binary> (never the daily one): A runs a private dbus-daemon, B connects from a
# different tracer. Also a same-tracer call inside A (must work for every binary).
# Expected: proot-fast6 -> CROSS_FAIL (the control that has to go red: fake_id0 SO_PEERCRED only maps its own
# tracees), proot-fast7 -> CROSS_OK. Prints DBUS_CROSS same=<OK|FAIL> cross=<OK|FAIL>.
set -uo pipefail
BIN=${1:?proot binary}; OUT=${2:?out}
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT"
SOCK=/tmp/f8-dbus-cross-$$
GETID="dbus-send --bus=unix:path=$SOCK --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.GetId"
# A: private session bus + same-tracer call
F8_SHARD_PROOT=$BIN "$HERE/f8-shard" /bin/bash -c "dbus-daemon --session --nofork --address=unix:path=$SOCK & D=\$!;
  for i in \$(seq 1 50); do [ -S $SOCK ] && break; sleep 0.1; done;
  timeout 10 $GETID > $OUT/same.out 2>&1; echo rc=\$? >> $OUT/same.out; wait \$D" > "$OUT/a.out" 2> "$OUT/a.err" &
AP=$!
for i in $(seq 1 100); do grep -q '^rc=' "$OUT/same.out" 2>/dev/null && break; sleep 0.1; done
# B: another tracer, same binary
F8_SHARD_PROOT=$BIN timeout 40 "$HERE/f8-shard" /bin/bash -c "timeout 10 $GETID > $OUT/cross.out 2>&1; echo rc=\$? >> $OUT/cross.out" \
  > "$OUT/b.out" 2> "$OUT/b.err"
for i in $(seq 1 100); do grep -q '^rc=' "$OUT/cross.out" 2>/dev/null && break; sleep 0.1; done
kill -TERM "$AP"; wait "$AP" 2>/dev/null
same=FAIL; grep -q '^rc=0' "$OUT/same.out" 2>/dev/null && same=OK
cross=FAIL; grep -q '^rc=0' "$OUT/cross.out" 2>/dev/null && cross=OK
TA=$(sed -n 's/^F8_SHARD tracer=\([0-9]*\).*/\1/p' "$OUT/a.out"); TB=$(sed -n 's/^F8_SHARD tracer=\([0-9]*\).*/\1/p' "$OUT/b.out")
echo "DBUS_CROSS bin=$(basename "$BIN") sha=$(sha256sum "$BIN" | cut -c1-8) tracerA=$TA tracerB=$TB same=$same cross=$cross" | tee "$OUT/result.txt"
[ -n "$TA" ] && [ -n "$TB" ] && [ "$TA" != "$TB" ] || echo "BROKEN tracers not distinct (A=$TA B=$TB)" | tee -a "$OUT/result.txt"
rm -f "$SOCK"
