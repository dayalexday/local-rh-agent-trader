#!/bin/bash
set -euo pipefail
LABEL="com.local.rh-agent.monthly"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
UID_NUM="$(id -u)"
launchctl bootout "gui/${UID_NUM}" "$PLIST" >/dev/null 2>&1 || true
launchctl bootout "gui/${UID_NUM}/${LABEL}" >/dev/null 2>&1 || true
rm -f "$PLIST"
echo "Removed ${LABEL}."
