#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "Usage: $0 <runtime-root> <python-bin> <lock-file> <external-env-root>" >&2
  exit 64
fi

RUNTIME_ROOT="$(cd "$1" && pwd -P)"
PYTHON_BIN="$2"
LOCK_FILE="$3"
EXTERNAL_ENV_ROOT="$4"
RELEASE_ID="${PORTFOLIO_OPS_LOCAL_RELEASE_ID:-}"
STATE_ROOT_CONFIGURED="${PORTFOLIO_OPS_LOCAL_STATE_ROOT:-}"
if [[ -z "$RELEASE_ID" ]]; then
  echo "PORTFOLIO_OPS_LOCAL_RELEASE_ID is required." >&2
  exit 64
fi
if [[ -z "$STATE_ROOT_CONFIGURED" || ! -d "$STATE_ROOT_CONFIGURED" ]]; then
  echo "PORTFOLIO_OPS_LOCAL_STATE_ROOT must name the persistent state root." >&2
  exit 64
fi
STATE_ROOT="$(cd "$STATE_ROOT_CONFIGURED" && pwd -P)"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
export PYTHONNOUSERSITE=1
SUMMARY_FILE="$STATE_ROOT/var/market-data-refresh-summary.json"
BACKEND_ROOT="$RUNTIME_ROOT/apps/platform/backend"
REFRESH_SCRIPT="$BACKEND_ROOT/scripts/refresh_market_data_scheduled.py"
WATCHLIST_BACKEND_ROOT="$RUNTIME_ROOT/apps/watchlist/backend"
WATCHLIST_REBUILD_SCRIPT="$WATCHLIST_BACKEND_ROOT/scripts/rebuild_watchlist_derived_state.py"
PORTFOLIO_BACKEND_ROOT="$RUNTIME_ROOT/apps/portfolio/backend"
PORTFOLIO_PUBLISH_SCRIPT="$PORTFOLIO_BACKEND_ROOT/scripts/publish_portfolio_daily.py"
CONVERGENCE_WAIT_SCRIPT="$RUNTIME_ROOT/infra/scripts/wait_for_refresh_convergence.py"
AUDIT_SCRIPT="$RUNTIME_ROOT/infra/scripts/audit_live_data.py"
DATABASE_URL="${PORTFOLIO_OPS_LOCAL_DATABASE_URL:-postgresql+psycopg://portfolio_ops:portfolio_ops@127.0.0.1:5432/portfolio_ops}"
CHANNEL="${PORTFOLIO_OPS_LOCAL_REFRESH_CHANNEL:-all}"
RETRY_FAILED_ATTEMPTS="${PORTFOLIO_OPS_LOCAL_REFRESH_RETRY_FAILED_ATTEMPTS:-2}"
FAIL_ON_ITEM_FAILURE="${PORTFOLIO_OPS_LOCAL_REFRESH_FAIL_ON_ITEM_FAILURE:-true}"
CONVERGENCE_TIMEOUT_SECONDS="${PORTFOLIO_OPS_LOCAL_REFRESH_CONVERGENCE_TIMEOUT_SECONDS:-900}"
CONVERGENCE_POLL_INTERVAL_SECONDS="${PORTFOLIO_OPS_LOCAL_REFRESH_CONVERGENCE_POLL_INTERVAL_SECONDS:-1}"
CONVERGENCE_STABLE_SAMPLES="${PORTFOLIO_OPS_LOCAL_REFRESH_CONVERGENCE_STABLE_SAMPLES:-2}"
PORTFOLIO_DRAIN_TIMEOUT_SECONDS="${PORTFOLIO_OPS_LOCAL_REFRESH_PORTFOLIO_DRAIN_TIMEOUT_SECONDS:-$CONVERGENCE_TIMEOUT_SECONDS}"
VALUATION_DATE="${PORTFOLIO_OPS_LOCAL_REFRESH_VALUATION_DATE:-$(date '+%F')}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python executable is missing: $PYTHON_BIN" >&2
  exit 1
fi
RUNTIME_VERIFIER="$RUNTIME_ROOT/infra/launchd/stage_local_runtime.py"
if [[ ! -f "$RUNTIME_VERIFIER" ]]; then
  echo "Runtime release verifier is missing: $RUNTIME_VERIFIER" >&2
  exit 1
fi
"$PYTHON_BIN" "$RUNTIME_VERIFIER" verify \
  --runtime-root "$RUNTIME_ROOT" \
  --release-id "$RELEASE_ID"

source "$RUNTIME_ROOT/infra/launchd/load_runtime_env.sh"
portfolio_ops_reject_repository_env_files "$RUNTIME_ROOT"
portfolio_ops_reject_repository_env_files "$STATE_ROOT"
ENV_FILE="$(portfolio_ops_runtime_env_file platform "$EXTERNAL_ENV_ROOT")"
portfolio_ops_load_env_file "$ENV_FILE" PORTFOLIO_OPS_PLATFORM_
WATCHLIST_ENV_FILE="$(portfolio_ops_runtime_env_file watchlist "$EXTERNAL_ENV_ROOT")"
if [[ -f "$WATCHLIST_ENV_FILE" ]]; then
  portfolio_ops_load_env_file "$WATCHLIST_ENV_FILE" PORTFOLIO_OPS_WATCHLIST_
fi
PORTFOLIO_ENV_FILE="$(portfolio_ops_runtime_env_file portfolio "$EXTERNAL_ENV_ROOT")"
if [[ -f "$PORTFOLIO_ENV_FILE" ]]; then
  portfolio_ops_load_env_file "$PORTFOLIO_ENV_FILE" PORTFOLIO_OPS_PORTFOLIO_
fi

if [[ ! -f "$REFRESH_SCRIPT" ]]; then
  echo "Scheduled refresh script is missing: $REFRESH_SCRIPT" >&2
  exit 1
fi
if [[ ! -f "$CONVERGENCE_WAIT_SCRIPT" ]]; then
  echo "Refresh convergence waiter is missing: $CONVERGENCE_WAIT_SCRIPT" >&2
  exit 1
fi
if [[ ! -f "$WATCHLIST_REBUILD_SCRIPT" ]]; then
  echo "Watchlist rebuild script is missing: $WATCHLIST_REBUILD_SCRIPT" >&2
  exit 1
fi
if [[ ! -f "$PORTFOLIO_PUBLISH_SCRIPT" ]]; then
  echo "Portfolio publication script is missing: $PORTFOLIO_PUBLISH_SCRIPT" >&2
  exit 1
fi
if [[ ! -f "$AUDIT_SCRIPT" ]]; then
  echo "Post-refresh data audit script is missing: $AUDIT_SCRIPT" >&2
  exit 1
fi
case "$CHANNEL" in
  all|email|tushare) ;;
  *)
    echo "Invalid refresh channel: $CHANNEL" >&2
    exit 64
    ;;
esac
if [[ ! "$RETRY_FAILED_ATTEMPTS" =~ ^[0-9]+$ ]]; then
  echo "Refresh retry count must be a non-negative integer: $RETRY_FAILED_ATTEMPTS" >&2
  exit 64
fi
case "$FAIL_ON_ITEM_FAILURE" in
  true|false) ;;
  *)
    echo "PORTFOLIO_OPS_LOCAL_REFRESH_FAIL_ON_ITEM_FAILURE must be true or false." >&2
    exit 64
    ;;
esac
if [[ ! "$CONVERGENCE_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
  echo "Refresh convergence timeout must be a positive integer: $CONVERGENCE_TIMEOUT_SECONDS" >&2
  exit 64
fi
if [[ ! "$CONVERGENCE_POLL_INTERVAL_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
  echo "Refresh convergence poll interval must be a positive integer: $CONVERGENCE_POLL_INTERVAL_SECONDS" >&2
  exit 64
fi
if [[ ! "$CONVERGENCE_STABLE_SAMPLES" =~ ^[1-9][0-9]*$ ]]; then
  echo "Refresh convergence stable sample count must be a positive integer: $CONVERGENCE_STABLE_SAMPLES" >&2
  exit 64
fi
if [[ ! "$PORTFOLIO_DRAIN_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
  echo "Portfolio publication drain timeout must be a positive integer: $PORTFOLIO_DRAIN_TIMEOUT_SECONDS" >&2
  exit 64
fi
if [[ ! "$VALUATION_DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "Watchlist rebuild valuation date must be canonical YYYY-MM-DD: $VALUATION_DATE" >&2
  exit 64
fi
"$PYTHON_BIN" - "$VALUATION_DATE" <<'PY'
from datetime import date
import sys


try:
    parsed = date.fromisoformat(sys.argv[1])
except ValueError as exc:
    raise SystemExit(f"Invalid Watchlist rebuild valuation date: {sys.argv[1]}") from exc
if parsed.isoformat() != sys.argv[1]:
    raise SystemExit(
        f"Watchlist rebuild valuation date must be canonical YYYY-MM-DD: {sys.argv[1]}"
    )
PY

mkdir -p "$(dirname "$LOCK_FILE")"

export PYTHONPATH="$BACKEND_ROOT:$RUNTIME_ROOT/packages/instrument-core/python"
export PORTFOLIO_OPS_LOCAL_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_PLATFORM_ENVIRONMENT=local
export PORTFOLIO_OPS_PLATFORM_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA=instrument_registry
export PORTFOLIO_OPS_PLATFORM_FRONTEND_URL=http://127.0.0.1:5172
export PORTFOLIO_OPS_PLATFORM_WATCHLIST_URL=http://127.0.0.1:5173
export PORTFOLIO_OPS_PLATFORM_PORTFOLIO_URL=http://127.0.0.1:5174
export PORTFOLIO_OPS_PLATFORM_WATCHLIST_API_URL=http://127.0.0.1:8000

refresh_arguments=(
  --channel "$CHANNEL"
  --updated-by launchd-scheduler
  --retry-failed-attempts "$RETRY_FAILED_ATTEMPTS"
  --lock-file "$LOCK_FILE"
  --summary-file "$SUMMARY_FILE"
  --json
)
if [[ "$FAIL_ON_ITEM_FAILURE" == "true" ]]; then
  refresh_arguments+=(--fail-on-item-failure)
fi

cd "$BACKEND_ROOT"
"$PYTHON_BIN" "$REFRESH_SCRIPT" "${refresh_arguments[@]}"
"$PYTHON_BIN" "$CONVERGENCE_WAIT_SCRIPT" \
  --phase targeted \
  --timeout-seconds "$CONVERGENCE_TIMEOUT_SECONDS" \
  --poll-interval-seconds "$CONVERGENCE_POLL_INTERVAL_SECONDS" \
  --stable-samples "$CONVERGENCE_STABLE_SAMPLES" \
  --summary-file "$SUMMARY_FILE" \
  --json

export PYTHONPATH="$PORTFOLIO_BACKEND_ROOT:$RUNTIME_ROOT/packages/instrument-core/python:$RUNTIME_ROOT/packages/calculation-core/python"
export PORTFOLIO_OPS_PORTFOLIO_ENVIRONMENT=local
export PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_PORTFOLIO_DATABASE_SCHEMA=portfolio
export PORTFOLIO_OPS_PORTFOLIO_ALLOCATION_RESEARCH_OUTPUTS_ROOT="$STATE_ROOT/var/portfolio-allocation-research-outputs"
cd "$PORTFOLIO_BACKEND_ROOT"
"$PYTHON_BIN" "$PORTFOLIO_PUBLISH_SCRIPT" \
  --as-of-date "$VALUATION_DATE" \
  --timeout-seconds "$PORTFOLIO_DRAIN_TIMEOUT_SECONDS"

export PYTHONPATH="$WATCHLIST_BACKEND_ROOT:$RUNTIME_ROOT/packages/instrument-core/python"
export PORTFOLIO_OPS_WATCHLIST_ENVIRONMENT=local
export PORTFOLIO_OPS_WATCHLIST_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA=watchlist
cd "$WATCHLIST_BACKEND_ROOT"
"$PYTHON_BIN" "$WATCHLIST_REBUILD_SCRIPT" \
  --valuation-date "$VALUATION_DATE" \
  --all-active \
  --rounds 2 \
  --continue-on-error

"$PYTHON_BIN" "$CONVERGENCE_WAIT_SCRIPT" \
  --phase final \
  --required-portfolio-as-of-date "$VALUATION_DATE" \
  --timeout-seconds "$CONVERGENCE_TIMEOUT_SECONDS" \
  --poll-interval-seconds "$CONVERGENCE_POLL_INTERVAL_SECONDS" \
  --stable-samples "$CONVERGENCE_STABLE_SAMPLES" \
  --summary-file "$SUMMARY_FILE" \
  --json
"$PYTHON_BIN" "$AUDIT_SCRIPT" --json --fail-on-warning
