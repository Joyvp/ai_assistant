#!/usr/bin/env bash
# Make APEXIS work the queue on its own, every five minutes.
#
# This installs a *user* timer. No sudo, nothing system-wide, and it stops
# the moment you tell it to. Undo with: apexis-timer-off
set -euo pipefail

GREEN=$'\033[32m'; DIM=$'\033[2m'; RED=$'\033[31m'; YELLOW=$'\033[33m'
BOLD=$'\033[1m'; CYAN=$'\033[36m'; OFF=$'\033[0m'

echo
echo "${BOLD}Teaching APEXIS to check its own queue${OFF}"
echo

# --- sanity ----------------------------------------------------------------

if ! command -v systemctl > /dev/null 2>&1; then
  echo "${RED}This machine doesn't use systemd, so there's no timer to install.${OFF}"
  echo "${DIM}Leave a terminal running 'apexis watch' instead.${OFF}"
  exit 1
fi

LAUNCHER="$HOME/.local/bin/apexis"
if [ ! -x "$LAUNCHER" ]; then
  echo "${RED}Can't find $LAUNCHER${OFF}"
  echo "${DIM}Run install_command.sh first, then try again.${OFF}"
  exit 1
fi

UNITS="$HOME/.config/systemd/user"
mkdir -p "$UNITS"

# --- the service -----------------------------------------------------------

cat > "$UNITS/apexis-worker.service" <<'UNIT'
[Unit]
Description=APEXIS — work through the queued jobs
After=network-online.target

[Service]
Type=oneshot
ExecStart=%h/.local/bin/apexis watch --once --if-idle
TimeoutStartSec=1800
Nice=15
IOSchedulingClass=idle
NoNewPrivileges=true
UNIT
echo "  ${GREEN}wrote${OFF} $UNITS/apexis-worker.service"

cat > "$UNITS/apexis-worker.timer" <<'UNIT'
[Unit]
Description=APEXIS — check for queued work every few minutes

[Timer]
OnCalendar=*:0/5
Persistent=true
RandomizedDelaySec=30
AccuracySec=1min

[Install]
WantedBy=timers.target
UNIT
echo "  ${GREEN}wrote${OFF} $UNITS/apexis-worker.timer"

# --- turn it on ------------------------------------------------------------

echo
echo "${DIM}starting it...${OFF}"
systemctl --user daemon-reload
systemctl --user enable --now apexis-worker.timer > /dev/null 2>&1

# Keep running when no one is logged in, so a closed laptop still works.
if command -v loginctl > /dev/null 2>&1; then
  loginctl enable-linger "$USER" > /dev/null 2>&1 || true
fi

# --- the off switch --------------------------------------------------------

cat > "$HOME/.local/bin/apexis-timer-off" <<'OFFSCRIPT'
#!/usr/bin/env bash
# Stop APEXIS checking its own queue. Everything else keeps working.
systemctl --user disable --now apexis-worker.timer 2>/dev/null || true
echo
echo "  APEXIS will no longer check the queue on its own."
echo "  Run jobs by hand with:  apexis watch --once"
echo "  Turn it back on with:   systemctl --user enable --now apexis-worker.timer"
echo
OFFSCRIPT
chmod +x "$HOME/.local/bin/apexis-timer-off"
echo "  ${GREEN}wrote${OFF} $HOME/.local/bin/apexis-timer-off"

# --- report ----------------------------------------------------------------

echo
if systemctl --user is-active --quiet apexis-worker.timer; then
  # "active" is not enough. A timer with no NEXT is active and will never
  # fire again, which is exactly how the first version of this file failed.
  NEXT=$(systemctl --user show apexis-worker.timer \
         -p NextElapseUSecRealtime --value 2>/dev/null)
  if [ -n "${NEXT:-}" ] && [ "${NEXT}" != "0" ] && [ "${NEXT}" != "n/a" ]; then
    echo "  ${GREEN}${BOLD}Running.${OFF}"
    echo "  ${DIM}next check: ${NEXT}${OFF}"
  else
    echo "  ${RED}The timer is active but has nothing scheduled.${OFF}"
    echo "  ${DIM}Tell me, and paste: systemctl --user list-timers${OFF}"
  fi
else
  echo "  ${YELLOW}Installed, but not running yet.${OFF}"
  echo "  ${DIM}Start it with: systemctl --user start apexis-worker.timer${OFF}"
fi

echo
echo "${BOLD}What happens now${OFF}"
echo "  ${DIM}Every 5 minutes APEXIS looks at its queue.${OFF}"
echo "  ${DIM}Empty queue costs nothing — it reads a folder and stops.${OFF}"
echo "  ${DIM}It will NOT run while you're using the model.${OFF}"
echo "  ${DIM}It only emails you while you're marked away.${OFF}"
echo
echo "${BOLD}Your routine from here${OFF}"
echo "  ${BOLD}apexis later \"a question\" https://a-link${OFF}"
echo "  ${BOLD}apexis away${OFF}"
echo "  ${DIM}...close the laptop and go. Answers arrive by email.${OFF}"
echo
echo "${DIM}See it:    systemctl --user list-timers apexis-worker.timer${OFF}"
echo "${DIM}Its logs:  journalctl --user -u apexis-worker -n 30${OFF}"
echo "${DIM}Stop it:   apexis-timer-off${OFF}"
echo
