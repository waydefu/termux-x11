#!/data/data/com.termux/files/usr/bin/sh
# APP-IDLE-01 launcher. Runs OUTSIDE PRoot (adb run-as com.termux, Termux sh): starts a NEW proot instance (stock or
# proot-fast2) with the 42 arguments of the daily desktop proot and runs ONE daily app under it on DISPLAY=:3
# (the experimental X), through app_idle_cmd.sh inside the guest. Writes the proot (= tracer) pid and the guest sid.
#   app_idle_launch.sh <proot-binary> <args-file> <tracer-pid-file(host)> <sid-file(guest)> <app> <run-dir(guest)>
PROOT=$1; ARGS=$2; TPF=$3; SIDF=$4; APP=$5; RUN=$6
cd /data/data/com.termux/files/usr/var/lib/proot-distro/containers/ubuntu/rootfs || exit 1
export PROOT_L2S_DIR=/data/data/com.termux/files/usr/var/lib/proot-distro/containers/ubuntu/rootfs/.l2s
unset LD_PRELOAD   # Termux's bionic libtermux-exec.so must not reach the glibc guest (proot-fast-bench-01)
set --
while IFS= read -r a; do set -- "$@" "$a"; done < "$ARGS"
"$PROOT" "$@" /usr/bin/setsid /bin/bash -c 'echo $$ > "$0"; exec "$@"' "$SIDF" \
  /usr/bin/env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/root LANG=C.UTF-8 DISPLAY=:3 \
  XDG_RUNTIME_DIR="$RUN/runtime" XMODIFIERS=@im=fcitx GTK_IM_MODULE=fcitx QT_IM_MODULE=fcitx \
  /usr/bin/dbus-run-session -- /bin/bash /tmp/app-idle/app_idle_cmd.sh "$APP" "$RUN" &
echo $! > "$TPF"
wait $!
echo "LAUNCH_EXIT rc=$?"
