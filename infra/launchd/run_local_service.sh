#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 5 ]]; then
  echo "Usage: $0 <service> <runtime-root> <python-bin> <node-bin> <external-env-root>" >&2
  exit 64
fi

SERVICE="$1"
RUNTIME_ROOT="$(cd "$2" && pwd -P)"
PYTHON_BIN="$3"
NODE_BIN="$4"
EXTERNAL_ENV_ROOT="$5"
DATABASE_URL="${PORTFOLIO_OPS_LOCAL_DATABASE_URL:-postgresql+psycopg://portfolio_ops:portfolio_ops@127.0.0.1:5432/portfolio_ops}"
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
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python executable is missing: $PYTHON_BIN" >&2
  exit 1
fi
STATE_ROOT="$(cd "$STATE_ROOT_CONFIGURED" && pwd -P)"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
export PYTHONNOUSERSITE=1
RUNTIME_VERIFIER="$RUNTIME_ROOT/infra/launchd/stage_local_runtime.py"
if [[ ! -f "$RUNTIME_VERIFIER" ]]; then
  echo "Runtime release verifier is missing: $RUNTIME_VERIFIER" >&2
  exit 1
fi
"$PYTHON_BIN" "$RUNTIME_VERIFIER" verify \
  --runtime-root "$RUNTIME_ROOT" \
  --release-id "$RELEASE_ID"

# Load only namespaced dotenv assignments as data.  This preserves the current
# external secret file without copying secrets into a plist or evaluating shell
# syntax contained in a value.
source "$RUNTIME_ROOT/infra/launchd/load_runtime_env.sh"
case "$SERVICE" in
  platform-api|platform-outbox-worker|watchlist-api|watchlist-worker|portfolio-api|portfolio-worker)
    portfolio_ops_reject_repository_env_files "$RUNTIME_ROOT"
    portfolio_ops_reject_repository_env_files "$STATE_ROOT"
    ;;
esac
case "$SERVICE" in
  platform-api|platform-outbox-worker)
    ENV_FILE="$(portfolio_ops_runtime_env_file platform "$EXTERNAL_ENV_ROOT")"
    portfolio_ops_load_env_file "$ENV_FILE" PORTFOLIO_OPS_PLATFORM_
    ;;
  watchlist-api|watchlist-worker)
    ENV_FILE="$(portfolio_ops_runtime_env_file watchlist "$EXTERNAL_ENV_ROOT")"
    if [[ -f "$ENV_FILE" ]]; then
      portfolio_ops_load_env_file "$ENV_FILE" PORTFOLIO_OPS_WATCHLIST_
    fi
    ;;
  portfolio-api|portfolio-worker)
    ENV_FILE="$(portfolio_ops_runtime_env_file portfolio "$EXTERNAL_ENV_ROOT")"
    if [[ -f "$ENV_FILE" ]]; then
      portfolio_ops_load_env_file "$ENV_FILE" PORTFOLIO_OPS_PORTFOLIO_
    fi
    ;;
esac

export PYTHONPATH="$RUNTIME_ROOT/packages/instrument-core/python"

require_pinned_web_release() {
  local app_name="$1" display_name="$2"
  local configured_root="${PORTFOLIO_OPS_LOCAL_WEB_RELEASE_ROOT:-}"
  if [[ -z "$configured_root" || -L "$configured_root" || ! -d "$configured_root" ]]; then
    echo "Pinned $display_name web release is missing or is a symlink." >&2
    exit 1
  fi
  WEB_RELEASE_ROOT="$(cd "$configured_root" && pwd -P)"
  WEB_APP_ROOT="$WEB_RELEASE_ROOT/$app_name"
  if [[ \
    -L "$WEB_APP_ROOT" \
    || -L "$WEB_APP_ROOT/index.html" \
    || ! -f "$WEB_APP_ROOT/index.html" \
    || -L "$WEB_APP_ROOT/release-id.txt" \
    || ! -f "$WEB_APP_ROOT/release-id.txt" \
  ]]; then
    echo "Pinned $display_name web release is incomplete." >&2
    exit 1
  fi
  if [[ "$(<"$WEB_APP_ROOT/release-id.txt")" != "$RELEASE_ID" ]]; then
    echo "$display_name web release id does not match the runtime release." >&2
    exit 1
  fi
}

case "$SERVICE" in
  platform-api)
    export PYTHONPATH="$RUNTIME_ROOT/apps/platform/backend:$PYTHONPATH"
    export PORTFOLIO_OPS_PLATFORM_ENVIRONMENT=local
    export PORTFOLIO_OPS_PLATFORM_DATABASE_URL="$DATABASE_URL"
    export PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA=instrument_registry
    export PORTFOLIO_OPS_PLATFORM_FRONTEND_URL=http://127.0.0.1:5172
    export PORTFOLIO_OPS_PLATFORM_WATCHLIST_URL=http://127.0.0.1:5173
    export PORTFOLIO_OPS_PLATFORM_PORTFOLIO_URL=http://127.0.0.1:5174
    export PORTFOLIO_OPS_PLATFORM_WATCHLIST_API_URL=http://127.0.0.1:8000
    export PORTFOLIO_OPS_PLATFORM_CORS_ORIGINS='["http://127.0.0.1:5172","http://localhost:5172"]'
    cd "$RUNTIME_ROOT/apps/platform/backend"
    exec "$PYTHON_BIN" -m uvicorn platform_app.main:app --host 127.0.0.1 --port 8002
    ;;
  platform-outbox-worker)
    export PYTHONPATH="$RUNTIME_ROOT/apps/platform/backend:$RUNTIME_ROOT/packages/instrument-core/python"
    export PORTFOLIO_OPS_PLATFORM_ENVIRONMENT=local
    export PORTFOLIO_OPS_PLATFORM_DATABASE_URL="$DATABASE_URL"
    export PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA=instrument_registry
    export PORTFOLIO_OPS_PLATFORM_WATCHLIST_API_URL=http://127.0.0.1:8000
    cd "$RUNTIME_ROOT/apps/platform/backend"
    exec "$PYTHON_BIN" \
      "$RUNTIME_ROOT/apps/platform/backend/scripts/run_market_data_outbox_worker.py" \
      --worker-id-prefix local-platform-market-data-outbox
    ;;
  watchlist-api)
    export PYTHONPATH="$RUNTIME_ROOT/apps/watchlist/backend:$PYTHONPATH"
    export PORTFOLIO_OPS_WATCHLIST_ENVIRONMENT=local
    export PORTFOLIO_OPS_WATCHLIST_DATABASE_URL="$DATABASE_URL"
    export PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA=watchlist
    export PORTFOLIO_OPS_WATCHLIST_FRONTEND_URL=http://127.0.0.1:5173
    export PORTFOLIO_OPS_WATCHLIST_CORS_ORIGINS='["http://127.0.0.1:5173","http://localhost:5173"]'
    export PORTFOLIO_OPS_WATCHLIST_DOCUMENT_STORAGE_ROOT="$STATE_ROOT/var/watchlist-documents"
    cd "$RUNTIME_ROOT/apps/watchlist/backend"
    exec "$PYTHON_BIN" -m uvicorn watchlist_app.main:app --host 127.0.0.1 --port 8000
    ;;
  watchlist-worker)
    export PYTHONPATH="$RUNTIME_ROOT/apps/watchlist/backend:$RUNTIME_ROOT/packages/instrument-core/python"
    export PORTFOLIO_OPS_WATCHLIST_ENVIRONMENT=local
    export PORTFOLIO_OPS_WATCHLIST_DATABASE_URL="$DATABASE_URL"
    export PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA=watchlist
    cd "$RUNTIME_ROOT/apps/watchlist/backend"
    exec "$PYTHON_BIN" -m watchlist_app.services.recalc_worker \
      --worker-id-prefix local-watchlist-recalc
    ;;
  portfolio-api)
    export PYTHONPATH="$RUNTIME_ROOT/apps/portfolio/backend:$RUNTIME_ROOT/packages/instrument-core/python:$RUNTIME_ROOT/packages/calculation-core/python"
    export PORTFOLIO_OPS_PORTFOLIO_ENVIRONMENT=local
    export PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL="$DATABASE_URL"
    export PORTFOLIO_OPS_PORTFOLIO_DATABASE_SCHEMA=portfolio
    export PORTFOLIO_OPS_PORTFOLIO_FRONTEND_URL=http://127.0.0.1:5174
    export PORTFOLIO_OPS_PORTFOLIO_CORS_ORIGINS='["http://127.0.0.1:5174","http://localhost:5174"]'
    export PORTFOLIO_OPS_PORTFOLIO_ALLOCATION_RESEARCH_OUTPUTS_ROOT="$STATE_ROOT/var/portfolio-allocation-research-outputs"
    cd "$RUNTIME_ROOT/apps/portfolio/backend"
    exec "$PYTHON_BIN" -m uvicorn portfolio_app.main:app --host 127.0.0.1 --port 8001
    ;;
  portfolio-worker)
    export PYTHONPATH="$RUNTIME_ROOT/apps/portfolio/backend:$RUNTIME_ROOT/packages/instrument-core/python:$RUNTIME_ROOT/packages/calculation-core/python"
    export PORTFOLIO_OPS_PORTFOLIO_ENVIRONMENT=local
    export PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL="$DATABASE_URL"
    export PORTFOLIO_OPS_PORTFOLIO_DATABASE_SCHEMA=portfolio
    cd "$RUNTIME_ROOT/apps/portfolio/backend"
    exec "$PYTHON_BIN" -m portfolio_app.calculations.portfolio_daily.worker \
      --worker-id-prefix local-portfolio-daily
    ;;
  platform-web)
    require_pinned_web_release platform Platform
    exec "$NODE_BIN" "$RUNTIME_ROOT/deploy/serve_spa_proxy.mjs" \
      --host 127.0.0.1 --port 5172 \
      --dist "$WEB_APP_ROOT" \
      --api-target http://127.0.0.1:8002
    ;;
  watchlist-web)
    require_pinned_web_release watchlist Watchlist
    exec "$NODE_BIN" "$RUNTIME_ROOT/deploy/serve_spa_proxy.mjs" \
      --host 127.0.0.1 --port 5173 \
      --dist "$WEB_APP_ROOT" \
      --api-target http://127.0.0.1:8000
    ;;
  portfolio-web)
    require_pinned_web_release portfolio Portfolio
    exec "$NODE_BIN" "$RUNTIME_ROOT/deploy/serve_spa_proxy.mjs" \
      --host 127.0.0.1 --port 5174 \
      --dist "$WEB_APP_ROOT" \
      --api-target http://127.0.0.1:8001
    ;;
  *)
    echo "Unknown service: $SERVICE" >&2
    exit 64
    ;;
esac
