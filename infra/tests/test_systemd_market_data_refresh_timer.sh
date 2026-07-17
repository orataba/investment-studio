#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-systemd-refresh-test.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

PROJECT_ROOT="$TEST_ROOT/project with spaces"
BACKEND_ROOT="$PROJECT_ROOT/apps/platform/backend"
ENV_ROOT="$TEST_ROOT/secure env"
MOCK_BIN="$TEST_ROOT/bin"
SYSTEMCTL_CALLS="$TEST_ROOT/systemctl-calls"
mkdir -p "$BACKEND_ROOT/scripts" "$PROJECT_ROOT/infra/launchd" "$ENV_ROOT" "$MOCK_BIN"
touch "$BACKEND_ROOT/scripts/refresh_market_data_scheduled.py"
cp "$REPOSITORY_ROOT/infra/launchd/load_runtime_env.sh" \
  "$PROJECT_ROOT/infra/launchd/load_runtime_env.sh"
chmod 700 "$ENV_ROOT"
printf '%s\n' \
  'PORTFOLIO_OPS_PLATFORM_DATABASE_URL=postgresql+psycopg://portfolio_ops@127.0.0.1:5432/portfolio_ops' \
  > "$ENV_ROOT/platform.env"
chmod 600 "$ENV_ROOT/platform.env"

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf "%s\n" "$*" >> "$SYSTEMCTL_CALLS"' \
  'exit 0' \
  > "$MOCK_BIN/systemctl"
chmod +x "$MOCK_BIN/systemctl"
export SYSTEMCTL_CALLS

set +e
HOME="$TEST_ROOT/home" \
XDG_CONFIG_HOME="$TEST_ROOT/config-missing-env" \
PATH="$MOCK_BIN:$PATH" \
PROJECT_ROOT="$PROJECT_ROOT" \
BACKEND_ROOT="$BACKEND_ROOT" \
PYTHON_BIN="$(command -v python3)" \
ENV_ROOT="" \
  "$REPOSITORY_ROOT/infra/systemd/install_market_data_refresh_timer.sh" \
  > "$TEST_ROOT/missing-env-root.out" 2>&1
missing_env_root_status=$?
set -e
if [[ $missing_env_root_status -ne 64 ]]; then
  echo "The systemd timer installer accepted a missing external environment root." >&2
  exit 1
fi

printf '%s\n' \
  'PORTFOLIO_OPS_PLATFORM_DATABASE_URL=postgresql+psycopg://portfolio_ops@127.0.0.1:5432/portfolio_ops' \
  'PORTFOLIO_OPS_PLATFORM_OPERATIONS_DATABASE_SCHEMA=instrument_registry' \
  > "$ENV_ROOT/platform.env"
set +e
HOME="$TEST_ROOT/home" \
XDG_CONFIG_HOME="$TEST_ROOT/config-invalid-schema" \
PATH="$MOCK_BIN:$PATH" \
PROJECT_ROOT="$PROJECT_ROOT" \
BACKEND_ROOT="$BACKEND_ROOT" \
PYTHON_BIN="$(command -v python3)" \
ENV_ROOT="$ENV_ROOT" \
  "$REPOSITORY_ROOT/infra/systemd/install_market_data_refresh_timer.sh" \
  > "$TEST_ROOT/invalid-schema.out" 2>&1
invalid_schema_status=$?
set -e
if [[ $invalid_schema_status -eq 0 ]]; then
  echo "The systemd timer installer accepted an invalid Platform operations schema." >&2
  exit 1
fi
grep -q 'PORTFOLIO_OPS_PLATFORM_OPERATIONS_DATABASE_SCHEMA must be platform' \
  "$TEST_ROOT/invalid-schema.out"
printf '%s\n' \
  'PORTFOLIO_OPS_PLATFORM_DATABASE_URL=postgresql+psycopg://portfolio_ops@127.0.0.1:5432/portfolio_ops' \
  > "$ENV_ROOT/platform.env"
chmod 600 "$ENV_ROOT/platform.env"

HOME="$TEST_ROOT/home" \
XDG_CONFIG_HOME="$TEST_ROOT/config" \
PATH="$MOCK_BIN:$PATH" \
PROJECT_ROOT="$PROJECT_ROOT" \
BACKEND_ROOT="$BACKEND_ROOT" \
PYTHON_BIN="$(command -v python3)" \
ENV_ROOT="$ENV_ROOT" \
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
grep -Fq 'EnvironmentFile=' "$SERVICE_FILE"
grep -Fq 'platform.env' "$SERVICE_FILE"
grep -Fxq 'Environment=PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA=instrument_registry' "$SERVICE_FILE"
grep -Fxq 'Environment=PORTFOLIO_OPS_PLATFORM_OPERATIONS_DATABASE_SCHEMA=platform' "$SERVICE_FILE"
grep -Fq 'PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA=instrument_registry PORTFOLIO_OPS_PLATFORM_OPERATIONS_DATABASE_SCHEMA=platform PYTHONPATH=' "$SERVICE_FILE"
if grep -Fq '/apps/platform/backend/.env' "$SERVICE_FILE"; then
  echo "The timer unit retained a repository-local environment fallback." >&2
  exit 1
fi
grep -Fq 'enable portfolio-ops-market-data-refresh.timer' "$SYSTEMCTL_CALLS"
grep -Fq 'start portfolio-ops-market-data-refresh.timer' "$SYSTEMCTL_CALLS"

echo "systemd 21:00 market data refresh timer test passed."
