#!/usr/bin/env bash
set -euo pipefail

LABEL_PREFIX="${LABEL_PREFIX:-com.orataba.portfolio-ops}"
LAUNCH_AGENTS_DIR="${LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
domain="gui/$UID"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../service_inventory.sh"

for service in "${PORTFOLIO_OPS_ALL_SERVICE_NAMES[@]}"; do
  label="$LABEL_PREFIX.$service"
  launchctl bootout "$domain/$label" >/dev/null 2>&1 || true
  rm -f "$LAUNCH_AGENTS_DIR/$label.plist"
done

echo "Portfolio Operations launchd services and the nightly refresh schedule were removed. Database and logs were preserved."
