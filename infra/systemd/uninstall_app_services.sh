#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
UNIT_PREFIX="${UNIT_PREFIX:-portfolio-ops}"
USER_SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

source "$PROJECT_ROOT/infra/service_inventory.sh"
managed_units=()
runtime_units=()
for service in "${PORTFOLIO_OPS_RUNTIME_SERVICE_NAMES[@]}"; do
  runtime_units+=("$UNIT_PREFIX-$service.service")
done
for unit_suffix in "${PORTFOLIO_OPS_SYSTEMD_MANAGED_UNIT_SUFFIXES[@]}"; do
  managed_units+=("$UNIT_PREFIX-$unit_suffix")
done

systemctl --user stop "${managed_units[@]}" >/dev/null 2>&1 || true
systemctl --user disable \
  "${runtime_units[@]}" \
  "$UNIT_PREFIX-market-data-refresh.timer" >/dev/null 2>&1 || true

for unit in "${managed_units[@]}"; do
  rm -f "$USER_SYSTEMD_DIR/$unit"
done
systemctl --user daemon-reload
systemctl --user reset-failed "${managed_units[@]}" >/dev/null 2>&1 || true

echo "Portfolio Operations systemd user services were removed. Database, state, and logs were preserved."
