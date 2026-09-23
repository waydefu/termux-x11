#!/bin/bash
# XFCE-FREEZE-V2: runs as the child of dbus-run-session. Records the session bus
# address (xfce4-session-logout needs it to reach THIS session and no other) and the
# session pid, then becomes xfce4-session. Nothing else.
set -eu
: "${XFCE_STATE:?}"
printf '%s\n' "$DBUS_SESSION_BUS_ADDRESS" > "$XFCE_STATE/dbus-address"
echo $$ > "$XFCE_STATE/xfce4-session.pid"
exec xfce4-session
