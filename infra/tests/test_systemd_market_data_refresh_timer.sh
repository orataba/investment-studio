#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/investment-studio-systemd-refresh-test.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

PROJECT_ROOT="$TEST_ROOT/project with spaces"
BACKEND_ROOT="$PROJECT_ROOT/shared-data"
ENV_ROOT="$TEST_ROOT/secure env"
MOCK_BIN="$TEST_ROOT/bin"
SYSTEMCTL_CALLS="$TEST_ROOT/systemctl-calls"
mkdir -p "$BACKEND_ROOT/scripts" "$PROJECT_ROOT/infra/launchd" "$PROJECT_ROOT/infra/scripts" "$ENV_ROOT" "$MOCK_BIN"
touch "$BACKEND_ROOT/scripts/refresh_market_data_scheduled.py"
cp "$REPOSITORY_ROOT/infra/scripts/market_close_schedule.py" "$PROJECT_ROOT/infra/scripts/market_close_schedule.py"
cp "$REPOSITORY_ROOT/infra/launchd/load_runtime_env.sh" \
  "$PROJECT_ROOT/infra/launchd/load_runtime_env.sh"
chmod 700 "$ENV_ROOT"
printf '%s\n' \
  'INVESTMENT_STUDIO_DATA_DATABASE_URL=postgresql+psycopg://investment_studio@127.0.0.1:5432/investment_studio' \
  > "$ENV_ROOT/data.env"
chmod 600 "$ENV_ROOT/data.env"

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
  'INVESTMENT_STUDIO_DATA_DATABASE_URL=postgresql+psycopg://investment_studio@127.0.0.1:5432/investment_studio' \
  'INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA=instrument_data' \
  > "$ENV_ROOT/data.env"
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
grep -q 'INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA must be data_ingestion' \
  "$TEST_ROOT/invalid-schema.out"
printf '%s\n' \
  'INVESTMENT_STUDIO_DATA_DATABASE_URL=postgresql+psycopg://investment_studio@127.0.0.1:5432/investment_studio' \
  > "$ENV_ROOT/data.env"
chmod 600 "$ENV_ROOT/data.env"

HOME="$TEST_ROOT/home" \
XDG_CONFIG_HOME="$TEST_ROOT/config" \
PATH="$MOCK_BIN:$PATH" \
PROJECT_ROOT="$PROJECT_ROOT" \
BACKEND_ROOT="$BACKEND_ROOT" \
PYTHON_BIN="$(command -v python3)" \
ENV_ROOT="$ENV_ROOT" \
  "$REPOSITORY_ROOT/infra/systemd/install_market_data_refresh_timer.sh"

SERVICE_FILE="$TEST_ROOT/config/systemd/user/investment-studio-market-data-refresh.service"
TIMER_FILE="$TEST_ROOT/config/systemd/user/investment-studio-market-data-refresh.timer"
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
grep -Fq 'data.env' "$SERVICE_FILE"
grep -Fxq 'Environment=INVESTMENT_STUDIO_DATA_DATABASE_SCHEMA=instrument_data' "$SERVICE_FILE"
grep -Fxq 'Environment=INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA=data_ingestion' "$SERVICE_FILE"
grep -Fq 'INVESTMENT_STUDIO_DATA_DATABASE_SCHEMA=instrument_data INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA=data_ingestion PYTHONPATH=' "$SERVICE_FILE"
if grep -Fq '/shared-data/.env' "$SERVICE_FILE"; then
  echo "The timer unit retained a repository-local environment fallback." >&2
  exit 1
fi
grep -Fq 'enable investment-studio-market-data-refresh.timer' "$SYSTEMCTL_CALLS"
grep -Fq 'start investment-studio-market-data-refresh.timer' "$SYSTEMCTL_CALLS"

for schedule in \
  'market|cn|15:30 Asia/Shanghai' \
  'market|hk|*:30 Asia/Shanghai' \
  'market|us|*:30 America/New_York' \
  'reference|cn-hk|08:00 Asia/Shanghai' \
  'reference|us|08:00 America/New_York'; do
  IFS='|' read -r channel market_scope expected_time <<< "$schedule"
  HOME="$TEST_ROOT/home" \
  XDG_CONFIG_HOME="$TEST_ROOT/config" \
  PATH="$MOCK_BIN:$PATH" \
  PROJECT_ROOT="$PROJECT_ROOT" \
  BACKEND_ROOT="$BACKEND_ROOT" \
  PYTHON_BIN="$(command -v python3)" \
  ENV_ROOT="$ENV_ROOT" CHANNEL="$channel" MARKET_SCOPE="$market_scope" \
    "$REPOSITORY_ROOT/infra/systemd/install_market_data_refresh_timer.sh"
  unit_name="investment-studio-$market_scope-$channel-data-refresh"
  service_file="$TEST_ROOT/config/systemd/user/$unit_name.service"
  timer_file="$TEST_ROOT/config/systemd/user/$unit_name.timer"
  grep -Fq "OnCalendar=*-*-* $expected_time" "$timer_file"
  grep -Fq -- "--channel $channel --market-scope $market_scope" "$service_file"
  grep -Fq -- "$market_scope-$channel-data-refresh-summary.json" "$service_file"
  grep -Fxq 'Restart=no' "$service_file"
  if [[ "$channel" == "market" && ( "$market_scope" == "hk" || "$market_scope" == "us" ) ]]; then
    grep -Fq "ExecCondition=" "$service_file"
    grep -Fq "market_close_schedule.py --market-scope $market_scope" "$service_file"
  fi
  grep -Fq "enable $unit_name.timer" "$SYSTEMCTL_CALLS"
done

echo "systemd market close, pre-open reference and settlement timer tests passed."
