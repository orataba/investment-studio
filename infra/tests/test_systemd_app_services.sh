#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-systemd-services-test.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

MOCK_BIN="$TEST_ROOT/bin"
ENV_ROOT="$TEST_ROOT/env"
SYSTEMCTL_CALLS="$TEST_ROOT/systemctl-calls"
CURL_CALLS="$TEST_ROOT/curl-calls"
PYTHON_CALLS="$TEST_ROOT/python-calls"
mkdir -p "$MOCK_BIN" "$ENV_ROOT"
for app in platform watchlist portfolio; do
  : > "$ENV_ROOT/$app.env"
done

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf "%s\n" "$*" >> "$SYSTEMCTL_CALLS"' \
  'exit 0' \
  > "$MOCK_BIN/systemctl"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'url=""' \
  'for argument in "$@"; do url="$argument"; done' \
  'printf "%s\n" "$url" >> "$CURL_CALLS"' \
  'if [[ -n "${CURL_FAIL_URL:-}" && "$url" == "$CURL_FAIL_URL" ]]; then exit 22; fi' \
  'exit 0' \
  > "$MOCK_BIN/curl"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf "%s\n" "$*" >> "$PYTHON_CALLS"' \
  'if [[ "${1:-}" == "-" ]]; then cat >/dev/null; fi' \
  'exit "${PORTFOLIO_READ_SMOKE_EXIT:-0}"' \
  > "$MOCK_BIN/python"
chmod +x "$MOCK_BIN/systemctl" "$MOCK_BIN/curl" "$MOCK_BIN/python"
export SYSTEMCTL_CALLS CURL_CALLS PYTHON_CALLS

HOME="$TEST_ROOT/home" \
XDG_CONFIG_HOME="$TEST_ROOT/config" \
PATH="$MOCK_BIN:$PATH" \
PROJECT_ROOT="$REPOSITORY_ROOT" \
ENV_ROOT="$ENV_ROOT" \
PYTHON_BIN="$MOCK_BIN/python" \
NODE_BIN="$(command -v node)" \
RUN_MIGRATIONS=false \
START_SERVICES=true \
  "$REPOSITORY_ROOT/infra/systemd/install_app_services.sh"

UNIT_ROOT="$TEST_ROOT/config/systemd/user"
expected_units=(
  portfolio-ops-platform-api.service
  portfolio-ops-watchlist-api.service
  portfolio-ops-watchlist-worker.service
  portfolio-ops-platform-outbox-worker.service
  portfolio-ops-portfolio-api.service
  portfolio-ops-portfolio-worker.service
  portfolio-ops-platform-web.service
  portfolio-ops-watchlist-web.service
  portfolio-ops-portfolio-web.service
)
for unit in "${expected_units[@]}"; do
  test -f "$UNIT_ROOT/$unit"
done

CALCULATION_CORE="$REPOSITORY_ROOT/packages/calculation-core/python"
PORTFOLIO_API_UNIT="$UNIT_ROOT/portfolio-ops-portfolio-api.service"
WORKER_UNIT="$UNIT_ROOT/portfolio-ops-portfolio-worker.service"
WATCHLIST_API_UNIT="$UNIT_ROOT/portfolio-ops-watchlist-api.service"
WATCHLIST_WORKER_UNIT="$UNIT_ROOT/portfolio-ops-watchlist-worker.service"
PLATFORM_API_UNIT="$UNIT_ROOT/portfolio-ops-platform-api.service"
PLATFORM_OUTBOX_WORKER_UNIT="$UNIT_ROOT/portfolio-ops-platform-outbox-worker.service"
grep -Fq "Environment=PYTHONPATH=$REPOSITORY_ROOT/apps/portfolio/backend:$REPOSITORY_ROOT/packages/instrument-core/python:$CALCULATION_CORE" "$PORTFOLIO_API_UNIT"
grep -Fq "Environment=PYTHONPATH=$REPOSITORY_ROOT/apps/portfolio/backend:$REPOSITORY_ROOT/packages/instrument-core/python:$CALCULATION_CORE" "$WORKER_UNIT"
grep -Fq 'ExecStart=' "$WORKER_UNIT"
grep -Fq -- '-m portfolio_app.calculations.portfolio_daily.worker --worker-id-prefix systemd-portfolio-daily' "$WORKER_UNIT"
grep -Fq 'Restart=always' "$WORKER_UNIT"
grep -Fq 'TimeoutStopSec=30' "$WORKER_UNIT"
grep -Fq "Environment=PYTHONPATH=$REPOSITORY_ROOT/apps/watchlist/backend:$REPOSITORY_ROOT/packages/instrument-core/python" "$WATCHLIST_WORKER_UNIT"
grep -Fq -- '-m watchlist_app.services.recalc_worker --worker-id-prefix systemd-watchlist-recalc' "$WATCHLIST_WORKER_UNIT"
grep -Fq 'Restart=always' "$WATCHLIST_WORKER_UNIT"
grep -Fq 'TimeoutStopSec=30' "$WATCHLIST_WORKER_UNIT"
grep -Fq 'After=network-online.target portfolio-ops-watchlist-worker.service' "$WATCHLIST_API_UNIT"
grep -Fq 'Wants=network-online.target portfolio-ops-watchlist-worker.service' "$WATCHLIST_API_UNIT"
grep -Fq "Environment=PYTHONPATH=$REPOSITORY_ROOT/apps/platform/backend:$REPOSITORY_ROOT/packages/instrument-core/python" "$PLATFORM_OUTBOX_WORKER_UNIT"
grep -Fq -- "$REPOSITORY_ROOT/apps/platform/backend/scripts/run_market_data_outbox_worker.py --worker-id-prefix systemd-platform-market-data-outbox" "$PLATFORM_OUTBOX_WORKER_UNIT"
grep -Fq 'Environment=PORTFOLIO_OPS_PLATFORM_WATCHLIST_API_URL=http://127.0.0.1:8100' "$PLATFORM_OUTBOX_WORKER_UNIT"
grep -Fq 'After=network-online.target portfolio-ops-watchlist-api.service' "$PLATFORM_OUTBOX_WORKER_UNIT"
grep -Fq 'Requires=portfolio-ops-watchlist-api.service' "$PLATFORM_OUTBOX_WORKER_UNIT"
grep -Fq 'Restart=always' "$PLATFORM_OUTBOX_WORKER_UNIT"
grep -Fq 'TimeoutStopSec=30' "$PLATFORM_OUTBOX_WORKER_UNIT"
grep -Fq 'After=network-online.target portfolio-ops-platform-outbox-worker.service' "$PLATFORM_API_UNIT"
grep -Fq 'Wants=network-online.target portfolio-ops-platform-outbox-worker.service' "$PLATFORM_API_UNIT"
grep -Fq 'portfolio-ops-watchlist-worker.service' "$SYSTEMCTL_CALLS"
grep -Fq 'portfolio-ops-portfolio-worker.service' "$SYSTEMCTL_CALLS"
grep -Fq 'portfolio-ops-platform-outbox-worker.service' "$SYSTEMCTL_CALLS"
grep -Fq 'restart portfolio-ops-platform-api.service portfolio-ops-watchlist-api.service portfolio-ops-watchlist-worker.service portfolio-ops-platform-outbox-worker.service portfolio-ops-portfolio-api.service portfolio-ops-portfolio-worker.service portfolio-ops-platform-web.service portfolio-ops-watchlist-web.service portfolio-ops-portfolio-web.service' "$SYSTEMCTL_CALLS"
for url in \
  http://127.0.0.1:8102/api/readiness \
  http://127.0.0.1:8100/api/readiness \
  http://127.0.0.1:8101/api/readiness \
  http://127.0.0.1:3100/ \
  http://127.0.0.1:3101/ \
  http://127.0.0.1:3102/; do
  grep -Fxq "$url" "$CURL_CALLS"
done
grep -Fxq -- '- http://127.0.0.1:8101' "$PYTHON_CALLS"
grep -Fq 'source "$PROJECT_ROOT/infra/scripts/runtime_readiness.sh"' "$REPOSITORY_ROOT/infra/scripts/release_database.sh"
grep -Fq 'portfolio_ops_wait_for_service_state_readiness' "$REPOSITORY_ROOT/infra/scripts/release_database.sh"
grep -Fq 'portfolio_ops_verify_portfolio_read_contract_for_service_state' "$REPOSITORY_ROOT/infra/scripts/release_database.sh"

: > "$CURL_CALLS"
: > "$PYTHON_CALLS"
HOME="$TEST_ROOT/home" \
XDG_CONFIG_HOME="$TEST_ROOT/config" \
PATH="$MOCK_BIN:$PATH" \
PROJECT_ROOT="$REPOSITORY_ROOT" \
ENV_ROOT="$ENV_ROOT" \
PYTHON_BIN="$MOCK_BIN/python" \
NODE_BIN="$(command -v node)" \
RUN_MIGRATIONS=false \
START_SERVICES=true \
API_HOST=192.0.2.10 \
WEB_HOST=192.0.2.20 \
  "$REPOSITORY_ROOT/infra/systemd/install_app_services.sh"
for url in \
  http://192.0.2.10:8102/api/readiness \
  http://192.0.2.10:8100/api/readiness \
  http://192.0.2.10:8101/api/readiness \
  http://192.0.2.20:3100/ \
  http://192.0.2.20:3101/ \
  http://192.0.2.20:3102/; do
  grep -Fxq "$url" "$CURL_CALLS"
done
grep -Fxq -- '- http://192.0.2.10:8101' "$PYTHON_CALLS"

: > "$SYSTEMCTL_CALLS"
set +e
HOME="$TEST_ROOT/home" \
XDG_CONFIG_HOME="$TEST_ROOT/config" \
PATH="$MOCK_BIN:$PATH" \
PROJECT_ROOT="$REPOSITORY_ROOT" \
ENV_ROOT="$ENV_ROOT" \
PYTHON_BIN="$MOCK_BIN/python" \
NODE_BIN="$(command -v node)" \
RUN_MIGRATIONS=false \
START_SERVICES=true \
PORTFOLIO_OPS_SYSTEMD_HEALTH_ATTEMPTS=2 \
PORTFOLIO_OPS_RUNTIME_HEALTH_POLL_INTERVAL_SECONDS=0 \
CURL_FAIL_URL=http://127.0.0.1:8102/api/readiness \
  "$REPOSITORY_ROOT/infra/systemd/install_app_services.sh" \
  > "$TEST_ROOT/readiness-failure.out" 2>&1
readiness_failure_status=$?
set -e
if [[ $readiness_failure_status -eq 0 ]]; then
  echo "systemd install accepted a failed Platform readiness check." >&2
  exit 1
fi
grep -Fq 'last failing URL: http://127.0.0.1:8102/api/readiness' "$TEST_ROOT/readiness-failure.out"
grep -Fq 'stop portfolio-ops-platform-api.service' "$SYSTEMCTL_CALLS"

: > "$SYSTEMCTL_CALLS"
set +e
HOME="$TEST_ROOT/home" \
XDG_CONFIG_HOME="$TEST_ROOT/config" \
PATH="$MOCK_BIN:$PATH" \
PROJECT_ROOT="$REPOSITORY_ROOT" \
ENV_ROOT="$ENV_ROOT" \
PYTHON_BIN="$MOCK_BIN/python" \
NODE_BIN="$(command -v node)" \
RUN_MIGRATIONS=false \
START_SERVICES=true \
PORTFOLIO_OPS_SYSTEMD_HEALTH_ATTEMPTS=1 \
PORTFOLIO_READ_SMOKE_EXIT=9 \
  "$REPOSITORY_ROOT/infra/systemd/install_app_services.sh" \
  > "$TEST_ROOT/read-smoke-failure.out" 2>&1
read_smoke_failure_status=$?
set -e
if [[ $read_smoke_failure_status -eq 0 ]]; then
  echo "systemd install accepted a failed Portfolio read-contract smoke." >&2
  exit 1
fi
grep -Fq 'Managed services failed post-start runtime validation.' "$TEST_ROOT/read-smoke-failure.out"
grep -Fq 'stop portfolio-ops-platform-api.service' "$SYSTEMCTL_CALLS"

HOME="$TEST_ROOT/home" \
XDG_CONFIG_HOME="$TEST_ROOT/config" \
PATH="$MOCK_BIN:$PATH" \
PROJECT_ROOT="$REPOSITORY_ROOT" \
  "$REPOSITORY_ROOT/infra/systemd/uninstall_app_services.sh"
for unit in "${expected_units[@]}"; do
  test ! -e "$UNIT_ROOT/$unit"
done
grep -Fq 'stop portfolio-ops-platform-api.service portfolio-ops-watchlist-api.service portfolio-ops-watchlist-worker.service portfolio-ops-platform-outbox-worker.service portfolio-ops-portfolio-api.service portfolio-ops-portfolio-worker.service' "$SYSTEMCTL_CALLS"
grep -Fq 'disable portfolio-ops-platform-api.service portfolio-ops-watchlist-api.service portfolio-ops-watchlist-worker.service portfolio-ops-platform-outbox-worker.service portfolio-ops-portfolio-api.service portfolio-ops-portfolio-worker.service' "$SYSTEMCTL_CALLS"

echo "systemd app and worker services test passed."
