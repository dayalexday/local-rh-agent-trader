#!/bin/bash
# Install a Monday 9:45 local-time job. The agent itself skips unless it is
# the first open Monday of the month. Does not fire the job now.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
LABEL="com.local.rh-agent.monthly"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

if [[ ! -x "$PY" ]]; then
  echo "Missing $PY. Run ./scripts/setup.sh first." >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/runs"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>WorkingDirectory</key>
  <string>${ROOT}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PY}</string>
    <string>-m</string>
    <string>local_rh_agent</string>
    <string>monthly</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key>
    <integer>1</integer>
    <key>Hour</key>
    <integer>9</integer>
    <key>Minute</key>
    <integer>45</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>${ROOT}/runs/launchd.log</string>
  <key>StandardErrorPath</key>
  <string>${ROOT}/runs/launchd.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>LOCAL_RH_ROOT</key>
    <string>${ROOT}</string>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
EOF

UID_NUM="$(id -u)"
launchctl bootout "gui/${UID_NUM}" "$PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/${UID_NUM}" "$PLIST"
launchctl enable "gui/${UID_NUM}/${LABEL}"

echo "Installed ${LABEL}."
echo "Fires Mondays at 9:45 in this Mac's timezone. Set the Mac to US Eastern."
echo "The process no-ops unless that Monday is the first open session of the month."
echo "Log: ${ROOT}/runs/launchd.log"
echo "Disable with ./scripts/uninstall_launchd.sh"
