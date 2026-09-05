#!/usr/bin/env bash
set -euo pipefail

LABEL_PREFIX="${LABEL_PREFIX:-com.orataba.investment-studio}"
LAUNCH_AGENTS_DIR="${LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
domain="gui/$UID"

for service in home-api watchlist-api portfolio-api home-web watchlist-web portfolio-web market-data-refresh; do
  label="$LABEL_PREFIX.$service"
  launchctl bootout "$domain/$label" >/dev/null 2>&1 || true
  rm -f "$LAUNCH_AGENTS_DIR/$label.plist"
done

echo "Investment Studio launchd services and the nightly refresh schedule were removed. Database and logs were preserved."
