#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-systemd-refresh-test.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

PROJECT_ROOT="$TEST_ROOT/project with spaces"
BACKEND_ROOT="$PROJECT_ROOT/apps/platform/backend"
MOCK_BIN="$TEST_ROOT/bin"
SYSTEMCTL_CALLS="$TEST_ROOT/systemctl-calls"
CLI_HELP_PATH="$TEST_ROOT/refresh-cli-help.txt"
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

CONTRACT_PYTHON="${PYTHON_BIN:-$REPOSITORY_ROOT/.venv/bin/python}"
if [[ ! -x "$CONTRACT_PYTHON" ]]; then
  echo "Python environment for the refresh CLI contract is missing: $CONTRACT_PYTHON" >&2
  exit 1
fi
PYTHONPATH="$REPOSITORY_ROOT/apps/platform/backend:$REPOSITORY_ROOT/packages/instrument-core/python" \
  "$CONTRACT_PYTHON" \
  "$REPOSITORY_ROOT/apps/platform/backend/scripts/refresh_market_data_scheduled.py" \
  --help > "$CLI_HELP_PATH"

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
grep -Fq -- '--json' "$SERVICE_FILE"
if grep -Fq -- '--require-downstream-success' "$SERVICE_FILE"; then
  echo "systemd timer passed the removed --require-downstream-success option." >&2
  exit 1
fi
for option in \
  --channel \
  --updated-by \
  --retry-failed-attempts \
  --lock-file \
  --summary-file \
  --fail-on-item-failure \
  --json; do
  grep -Fq -- "$option" "$CLI_HELP_PATH"
done
if grep -Fq -- '--require-downstream-success' "$CLI_HELP_PATH"; then
  echo "Refresh CLI unexpectedly advertises removed downstream coordination." >&2
  exit 1
fi
grep -Fq 'enable portfolio-ops-market-data-refresh.timer' "$SYSTEMCTL_CALLS"
grep -Fq 'start portfolio-ops-market-data-refresh.timer' "$SYSTEMCTL_CALLS"

echo "systemd 21:00 market data refresh timer test passed."
