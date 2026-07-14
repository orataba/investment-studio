#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-launchd-worker-runner-test.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

MOCK_PYTHON="$TEST_ROOT/python"
MOCK_NODE="$TEST_ROOT/node"
WORKER_RESULT_PATH="$TEST_ROOT/worker-result"
WATCHLIST_WORKER_RESULT_PATH="$TEST_ROOT/watchlist-worker-result"
PLATFORM_OUTBOX_WORKER_RESULT_PATH="$TEST_ROOT/platform-outbox-worker-result"
API_RESULT_PATH="$TEST_ROOT/api-result"
ENV_ROOT="$TEST_ROOT/env"
mkdir -p "$ENV_ROOT"
chmod 700 "$ENV_ROOT"
: > "$ENV_ROOT/portfolio.env"
: > "$ENV_ROOT/watchlist.env"
: > "$ENV_ROOT/platform.env"
chmod 600 "$ENV_ROOT/portfolio.env"
chmod 600 "$ENV_ROOT/watchlist.env"
chmod 600 "$ENV_ROOT/platform.env"

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf "args=%s\npythonpath=%s\n" "$*" "$PYTHONPATH" > "$RESULT_PATH"' \
  > "$MOCK_PYTHON"
printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$MOCK_NODE"
chmod +x "$MOCK_PYTHON" "$MOCK_NODE"

RESULT_PATH="$WORKER_RESULT_PATH" \
  "$REPOSITORY_ROOT/infra/launchd/run_local_service.sh" \
    portfolio-worker \
    "$REPOSITORY_ROOT" \
    "$MOCK_PYTHON" \
    "$MOCK_NODE" \
    "$ENV_ROOT"

grep -Fq 'args=-m portfolio_app.calculations.portfolio_daily.worker --worker-id-prefix local-portfolio-daily' "$WORKER_RESULT_PATH"
grep -Fq "$REPOSITORY_ROOT/apps/portfolio/backend" "$WORKER_RESULT_PATH"
grep -Fq "$REPOSITORY_ROOT/packages/calculation-core/python" "$WORKER_RESULT_PATH"
grep -Fq "$REPOSITORY_ROOT/packages/instrument-core/python" "$WORKER_RESULT_PATH"

RESULT_PATH="$WATCHLIST_WORKER_RESULT_PATH" \
  "$REPOSITORY_ROOT/infra/launchd/run_local_service.sh" \
    watchlist-worker \
    "$REPOSITORY_ROOT" \
    "$MOCK_PYTHON" \
    "$MOCK_NODE" \
    "$ENV_ROOT"

grep -Fq 'args=-m watchlist_app.services.recalc_worker --worker-id-prefix local-watchlist-recalc' "$WATCHLIST_WORKER_RESULT_PATH"
grep -Fq "$REPOSITORY_ROOT/apps/watchlist/backend" "$WATCHLIST_WORKER_RESULT_PATH"
grep -Fq "$REPOSITORY_ROOT/packages/instrument-core/python" "$WATCHLIST_WORKER_RESULT_PATH"
if grep -Fq "$REPOSITORY_ROOT/packages/calculation-core/python" "$WATCHLIST_WORKER_RESULT_PATH"; then
  echo "Watchlist worker unexpectedly depends on calculation-core." >&2
  exit 1
fi

RESULT_PATH="$PLATFORM_OUTBOX_WORKER_RESULT_PATH" \
  "$REPOSITORY_ROOT/infra/launchd/run_local_service.sh" \
    platform-outbox-worker \
    "$REPOSITORY_ROOT" \
    "$MOCK_PYTHON" \
    "$MOCK_NODE" \
    "$ENV_ROOT"

grep -Fq "args=$REPOSITORY_ROOT/apps/platform/backend/scripts/run_market_data_outbox_worker.py --worker-id-prefix local-platform-market-data-outbox" "$PLATFORM_OUTBOX_WORKER_RESULT_PATH"
grep -Fq "$REPOSITORY_ROOT/apps/platform/backend" "$PLATFORM_OUTBOX_WORKER_RESULT_PATH"
grep -Fq "$REPOSITORY_ROOT/packages/instrument-core/python" "$PLATFORM_OUTBOX_WORKER_RESULT_PATH"
if grep -Fq "$REPOSITORY_ROOT/packages/calculation-core/python" "$PLATFORM_OUTBOX_WORKER_RESULT_PATH"; then
  echo "Platform outbox worker unexpectedly depends on calculation-core." >&2
  exit 1
fi

RESULT_PATH="$API_RESULT_PATH" \
  "$REPOSITORY_ROOT/infra/launchd/run_local_service.sh" \
    portfolio-api \
    "$REPOSITORY_ROOT" \
    "$MOCK_PYTHON" \
    "$MOCK_NODE" \
    "$ENV_ROOT"

grep -Fq 'args=-m uvicorn portfolio_app.main:app --host 127.0.0.1 --port 8001' "$API_RESULT_PATH"
grep -Fq "$REPOSITORY_ROOT/apps/portfolio/backend" "$API_RESULT_PATH"
grep -Fq "$REPOSITORY_ROOT/packages/calculation-core/python" "$API_RESULT_PATH"
grep -Fq "$REPOSITORY_ROOT/packages/instrument-core/python" "$API_RESULT_PATH"

echo "launchd worker runtimes test passed."
