#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-systemd-refresh-test.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

PROJECT_ROOT="$TEST_ROOT/project with spaces"
BACKEND_ROOT="$PROJECT_ROOT/apps/platform/backend"
MOCK_BIN="$TEST_ROOT/bin"
SYSTEMCTL_CALLS="$TEST_ROOT/systemctl-calls"
mkdir -p "$BACKEND_ROOT/scripts" "$MOCK_BIN"
touch "$BACKEND_ROOT/scripts/refresh_market_data_scheduled.py"

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf "%s\n" "$*" >> "$SYSTEMCTL_CALLS"' \
  'exit 0' \
  > "$MOCK_BIN/systemctl"
chmod +x "$MOCK_BIN/systemctl"
export SYSTEMCTL_CALLS

HOME="$TEST_ROOT/home" \
XDG_CONFIG_HOME="$TEST_ROOT/config" \
PATH="$MOCK_BIN:$PATH" \
PROJECT_ROOT="$PROJECT_ROOT" \
BACKEND_ROOT="$BACKEND_ROOT" \
PYTHON_BIN="$(command -v python3)" \
  "$REPOSITORY_ROOT/infra/systemd/install_market_data_refresh_timer.sh"

SERVICE_FILE="$TEST_ROOT/config/systemd/user/portfolio-ops-market-data-refresh.service"
TIMER_FILE="$TEST_ROOT/config/systemd/user/portfolio-ops-market-data-refresh.timer"
test -f "$SERVICE_FILE"
test -f "$TIMER_FILE"
grep -Fq 'OnCalendar=*-*-* 21:00 Asia/Shanghai' "$TIMER_FILE"
grep -Fq -- '--lock-file' "$SERVICE_FILE"
grep -Fq -- 'market-data-refresh.lock' "$SERVICE_FILE"
grep -Fq -- '--summary-file' "$SERVICE_FILE"
grep -Fq -- 'market-data-refresh-summary.json' "$SERVICE_FILE"
grep -Fq -- '--fail-on-item-failure' "$SERVICE_FILE"
grep -Fq -- '--require-downstream-success' "$SERVICE_FILE"
grep -Fq -- '--json' "$SERVICE_FILE"
grep -Fq 'enable portfolio-ops-market-data-refresh.timer' "$SYSTEMCTL_CALLS"
grep -Fq 'start portfolio-ops-market-data-refresh.timer' "$SYSTEMCTL_CALLS"

echo "systemd 21:00 market data refresh timer test passed."
