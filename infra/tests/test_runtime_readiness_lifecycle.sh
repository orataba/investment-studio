#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-runtime-readiness-test.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

MOCK_BIN="$TEST_ROOT/bin"
CURL_CALLS="$TEST_ROOT/curl-calls"
PYTHON_CALLS="$TEST_ROOT/python-calls"
STATE_FILE="$TEST_ROOT/service-state"
mkdir -p "$MOCK_BIN"

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
  'cat >/dev/null' \
  'exit "${PORTFOLIO_READ_SMOKE_EXIT:-0}"' \
  > "$MOCK_BIN/python"
chmod +x "$MOCK_BIN/curl" "$MOCK_BIN/python"
export CURL_CALLS PYTHON_CALLS

source "$REPOSITORY_ROOT/infra/scripts/runtime_readiness.sh"

: > "$STATE_FILE"
PATH="$MOCK_BIN:$PATH" \
  portfolio_ops_wait_for_service_state_readiness \
    "$STATE_FILE" portfolio-ops 1

printf '%s\n' \
  platform-api \
  watchlist-api \
  portfolio-api \
  platform-web \
  watchlist-web \
  portfolio-web \
  > "$STATE_FILE"

expected_launchd_urls="$(printf '%s\n' \
  http://127.0.0.1:8002/api/readiness \
  http://127.0.0.1:8000/api/readiness \
  http://127.0.0.1:8001/api/readiness \
  http://127.0.0.1:5172/ \
  http://127.0.0.1:5173/ \
  http://127.0.0.1:5174/)"
actual_launchd_urls="$(
  portfolio_ops_runtime_urls_for_service_state "$STATE_FILE" portfolio-ops
)"
[[ "$actual_launchd_urls" == "$expected_launchd_urls" ]]

PATH="$MOCK_BIN:$PATH" \
PORTFOLIO_OPS_RUNTIME_HEALTH_POLL_INTERVAL_SECONDS=0 \
  portfolio_ops_wait_for_service_state_readiness \
    "$STATE_FILE" portfolio-ops 1
grep -Fxq 'http://127.0.0.1:8002/api/readiness' "$CURL_CALLS"
grep -Fxq 'http://127.0.0.1:5174/' "$CURL_CALLS"

PATH="$MOCK_BIN:$PATH" \
  portfolio_ops_verify_portfolio_read_contract_for_service_state \
    "$STATE_FILE" portfolio-ops "$MOCK_BIN/python"
grep -Fxq -- '- http://127.0.0.1:8001' "$PYTHON_CALLS"

set +e
PATH="$MOCK_BIN:$PATH" \
PORTFOLIO_OPS_RUNTIME_HEALTH_POLL_INTERVAL_SECONDS=0 \
CURL_FAIL_URL=http://127.0.0.1:8000/api/readiness \
  portfolio_ops_wait_for_service_state_readiness \
    "$STATE_FILE" portfolio-ops 2 \
    > "$TEST_ROOT/readiness-failure.out" 2>&1
readiness_failure_status=$?
set -e
if [[ $readiness_failure_status -eq 0 ]]; then
  echo "Shared runtime gate accepted a failing readiness URL." >&2
  exit 1
fi
grep -Fq 'failed after 2 attempts' "$TEST_ROOT/readiness-failure.out"
grep -Fq 'last failing URL: http://127.0.0.1:8000/api/readiness' \
  "$TEST_ROOT/readiness-failure.out"

printf '%s\n' \
  portfolio-ops-platform-api.service \
  portfolio-ops-watchlist-api.service \
  portfolio-ops-portfolio-api.service \
  portfolio-ops-platform-web.service \
  portfolio-ops-watchlist-web.service \
  portfolio-ops-portfolio-web.service \
  > "$STATE_FILE"
expected_systemd_urls="$(printf '%s\n' \
  http://127.0.0.1:8102/api/readiness \
  http://127.0.0.1:8100/api/readiness \
  http://127.0.0.1:8101/api/readiness \
  http://127.0.0.1:3100/ \
  http://127.0.0.1:3101/ \
  http://127.0.0.1:3102/)"
actual_systemd_urls="$(
  portfolio_ops_runtime_urls_for_service_state "$STATE_FILE" portfolio-ops
)"
[[ "$actual_systemd_urls" == "$expected_systemd_urls" ]]

expected_custom_systemd_urls="$(printf '%s\n' \
  http://127.0.0.1:9102/api/readiness \
  http://127.0.0.1:9100/api/readiness \
  http://127.0.0.1:9101/api/readiness \
  http://127.0.0.1:4100/ \
  http://127.0.0.1:4101/ \
  http://127.0.0.1:4102/)"
actual_custom_systemd_urls="$(
  PLATFORM_API_PORT=9102 \
  WATCHLIST_API_PORT=9100 \
  PORTFOLIO_API_PORT=9101 \
  PLATFORM_WEB_PORT=4100 \
  WATCHLIST_WEB_PORT=4101 \
  PORTFOLIO_WEB_PORT=4102 \
    portfolio_ops_runtime_urls_for_service_state "$STATE_FILE" portfolio-ops
)"
[[ "$actual_custom_systemd_urls" == "$expected_custom_systemd_urls" ]]
expected_specific_bind_urls="$(printf '%s\n' \
  http://192.0.2.10:8102/api/readiness \
  http://192.0.2.10:8100/api/readiness \
  http://192.0.2.10:8101/api/readiness \
  http://192.0.2.20:3100/ \
  http://192.0.2.20:3101/ \
  http://192.0.2.20:3102/)"
actual_specific_bind_urls="$(
  API_HOST=192.0.2.10 \
  WEB_HOST=192.0.2.20 \
    portfolio_ops_runtime_urls_for_service_state "$STATE_FILE" portfolio-ops
)"
[[ "$actual_specific_bind_urls" == "$expected_specific_bind_urls" ]]

expected_wildcard_bind_urls="$expected_systemd_urls"
actual_wildcard_bind_urls="$(
  API_HOST=0.0.0.0 \
  WEB_HOST=:: \
    portfolio_ops_runtime_urls_for_service_state "$STATE_FILE" portfolio-ops
)"
[[ "$actual_wildcard_bind_urls" == "$expected_wildcard_bind_urls" ]]

explicit_health_host_urls="$(
  API_HOST=192.0.2.10 \
  API_HEALTH_HOST=198.51.100.10 \
  WEB_HOST=192.0.2.20 \
  WEB_HEALTH_HOST=198.51.100.20 \
    portfolio_ops_runtime_urls_for_service_state "$STATE_FILE" portfolio-ops
)"
[[ "$explicit_health_host_urls" == "${expected_specific_bind_urls//192.0.2/198.51.100}" ]]
custom_portfolio_base="$(
  PORTFOLIO_API_PORT=9101 \
    portfolio_ops_portfolio_api_base_for_service_state \
      "$STATE_FILE" portfolio-ops
)"
[[ "$custom_portfolio_base" == "http://127.0.0.1:9101" ]]
specific_bind_portfolio_base="$(
  API_HOST=192.0.2.10 \
    portfolio_ops_portfolio_api_base_for_service_state \
      "$STATE_FILE" portfolio-ops
)"
[[ "$specific_bind_portfolio_base" == "http://192.0.2.10:8101" ]]
: > "$PYTHON_CALLS"
PATH="$MOCK_BIN:$PATH" \
PORTFOLIO_API_PORT=9101 \
  portfolio_ops_verify_portfolio_read_contract_for_service_state \
    "$STATE_FILE" portfolio-ops "$MOCK_BIN/python"
grep -Fxq -- '- http://127.0.0.1:9101' "$PYTHON_CALLS"

set +e
PATH="$MOCK_BIN:$PATH" \
PORTFOLIO_READ_SMOKE_EXIT=9 \
  portfolio_ops_verify_portfolio_read_contract_for_service_state \
    "$STATE_FILE" portfolio-ops "$MOCK_BIN/python" \
    > "$TEST_ROOT/read-smoke-failure.out" 2>&1
read_smoke_failure_status=$?
set -e
if [[ $read_smoke_failure_status -eq 0 ]]; then
  echo "Shared runtime gate accepted a failing Portfolio read-contract smoke." >&2
  exit 1
fi

for lifecycle_script in \
  "$REPOSITORY_ROOT/infra/scripts/release_database.sh" \
  "$REPOSITORY_ROOT/infra/postgres/restore_project_dump.sh"; do
  grep -Fq 'source "$PROJECT_ROOT/infra/scripts/runtime_readiness.sh"' \
    "$lifecycle_script"
  grep -Fq 'portfolio_ops_wait_for_service_state_readiness' \
    "$lifecycle_script"
  grep -Fq 'portfolio_ops_verify_portfolio_read_contract_for_service_state' \
    "$lifecycle_script"
  grep -Fq 'market-data-refresh.service" ]]; then' "$lifecycle_script"
  grep -Fq 'was not replayed automatically' "$lifecycle_script"
done
grep -Fq 'stop_started_managed_services' \
  "$REPOSITORY_ROOT/infra/postgres/restore_project_dump.sh"
grep -Fq 'services_may_need_restart="false"' \
  "$REPOSITORY_ROOT/infra/postgres/restore_project_dump.sh"
grep -Fq 'managed_services_are_inactive' \
  "$REPOSITORY_ROOT/infra/scripts/release_database.sh"
grep -Fq 'AUTOMATIC ROLLBACK SKIPPED' \
  "$REPOSITORY_ROOT/infra/scripts/release_database.sh"
grep -Fq 'portfolio_ops_wait_for_runtime_urls' \
  "$REPOSITORY_ROOT/infra/systemd/install_app_services.sh"
grep -Fq 'portfolio_ops_verify_portfolio_read_contract' \
  "$REPOSITORY_ROOT/infra/systemd/install_app_services.sh"

echo "shared runtime readiness lifecycle test passed."
