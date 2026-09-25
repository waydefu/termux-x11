#!/usr/bin/env bash
# Build the XFCE-V6-BASELINE-01 probes (T1, T3) and print their sha256.
#   glibc (PRoot, traced when run from here)   -> $OUT/x_rtt2.glibc, $OUT/x_grab_stall.glibc
#   bionic (Termux clang; run through TermuxService = untraced) -> $TOUT/x_rtt2.bionic
# The bionic binary lives on a Termux path because the Termux app uid executes it.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${OUT:-/root/build/xfce-v6}
TOUT=${TOUT:-/data/data/com.termux/files/home/xfce-v6}
TCLANG=/data/data/com.termux/files/usr/bin/clang
mkdir -p "$OUT" "$TOUT"
cc -O2 -Wall -o "$OUT/x_rtt2.glibc" "$HERE/x_rtt2.c" -lxcb
cc -O2 -Wall -o "$OUT/x_grab_stall.glibc" "$HERE/x_grab_stall.c" -lxcb
cc -O2 -Wall -o "$OUT/traced_lat.glibc" "$HERE/../pga/traced_lat.c"
"$TCLANG" -O2 -Wall -o "$TOUT/x_rtt2.bionic" "$HERE/x_rtt2.c" -lxcb
sha256sum "$OUT/x_rtt2.glibc" "$OUT/x_grab_stall.glibc" "$OUT/traced_lat.glibc" "$TOUT/x_rtt2.bionic"
