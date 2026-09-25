#!/bin/bash
# APP-IDLE-01: the daily launch command of each app (desktop entries on the daily desktop), run inside the guest.
#   app_idle_cmd.sh <app> <run-dir>
case "$1" in
  # chatgpt-f8.desktop (ChatGPT desktop app, includes Codex; real profile ~/.config/Codex)
  chatgpt)    exec /usr/bin/chatgpt --no-sandbox --ozone-platform=x11 --disable-gpu --disable-dev-shm-usage ;;
  # chatgpt-web -> hermes-chrome: Playwright Chromium, real profile ~/.config/chromium, CDP 9222
  chatgptweb) exec /root/.cache/ms-playwright/chromium-1228/chrome-linux/chrome --no-sandbox --disable-dev-shm-usage \
                --disable-gpu --remote-debugging-port=9222 --user-data-dir=/root/.config/chromium --no-first-run \
                --disable-default-apps https://chatgpt.com ;;
  # hermes-f8.desktop (Hermes desktop; real profile ~/.config/Hermes)
  hermes)     exec /usr/local/bin/hermes-desktop-f8 ;;
  # no daily Cursor profile exists (~/.config/Cursor absent): fresh profile per run, flags of PROOT-BENCH
  cursor)     exec /usr/share/cursor/cursor --no-sandbox --disable-gpu-sandbox --disable-dev-shm-usage --ozone-platform=x11 \
                --disable-gpu --user-data-dir="$2/prof-cursor" ;;
  # Claude Desktop with a throwaway profile (sanity only: the daily instance holds the real one)
  claude)     exec /usr/lib/claude-desktop/claude-desktop --no-sandbox --disable-gpu --disable-dev-shm-usage --ozone-platform=x11 \
                --password-store=basic --user-data-dir="$2/prof-claude" ;;
  *) echo "app_idle_cmd: unknown app $1" >&2; exit 2 ;;
esac
