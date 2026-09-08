#!/usr/bin/env bash
set -euo pipefail

LABEL_PREFIX="${LABEL_PREFIX:-com.orataba.investment-studio}"
LAUNCH_AGENTS_DIR="${LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
domain="gui/$UID"

for service in home-api watchlist-api portfolio-api briefing-api home-web watchlist-web portfolio-web briefing-web market-data-refresh cn-market-data-refresh hk-market-data-refresh us-market-data-refresh cn-hk-reference-data-refresh us-reference-data-refresh market-sync; do
  label="$LABEL_PREFIX.$service"
  launchctl bootout "$domain/$label" >/dev/null 2>&1 || true
  rm -f "$LAUNCH_AGENTS_DIR/$label.plist"
done

echo "Investment Studio launchd services and refresh schedules were removed. Database and logs were preserved."
