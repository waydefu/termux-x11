#!/data/data/com.termux/files/usr/bin/sh
# PROOT-BENCH-01 launcher. Runs OUTSIDE PRoot (adb run-as com.termux, Termux sh): starts a NEW proot instance
# (stock or proot-fast) with the exact 42 arguments of the daily desktop proot (proot-args.txt) and runs one
# isolated Cursor under it on DISPLAY=:3. Writes the proot (= tracer) pid and the guest session id.
#   proot_bench_launch.sh <proot-binary> <args-file> <tracer-pid-file(host)> <sid-file(guest)> <home> <profile> <port> <ws-file>
PROOT=$1; ARGS=$2; TPF=$3; SIDF=$4; HOMED=$5; PROF=$6; PORT=$7; WS=$8
cd /data/data/com.termux/files/usr/var/lib/proot-distro/containers/ubuntu/rootfs || exit 1
export PROOT_L2S_DIR=/data/data/com.termux/files/usr/var/lib/proot-distro/containers/ubuntu/rootfs/.l2s
unset LD_PRELOAD   # Termux's bionic libtermux-exec.so must not reach the glibc guest (proot-fast-bench-01)
set --
while IFS= read -r a; do set -- "$@" "$a"; done < "$ARGS"
"$PROOT" "$@" /usr/bin/setsid /bin/bash -c 'echo $$ > "$0"; exec "$@"' "$SIDF" \
  /usr/bin/env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME="$HOMED" \
  XDG_RUNTIME_DIR="$HOMED/runtime" XDG_CONFIG_HOME="$HOMED/.config" XDG_CACHE_HOME="$HOMED/.cache" \
  DISPLAY=:3 LANG=C.UTF-8 /usr/bin/dbus-run-session -- \
  /usr/share/cursor/cursor --no-sandbox --disable-gpu-sandbox --disable-dev-shm-usage --ozone-platform=x11 --disable-gpu \
  --user-data-dir="$PROF" --remote-debugging-port="$PORT" --enable-logging=stderr "$WS" &
echo $! > "$TPF"
wait $!
echo "LAUNCH_EXIT rc=$?"
