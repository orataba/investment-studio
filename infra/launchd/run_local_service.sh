#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 5 ]]; then
  echo "Usage: $0 <service> <project-root> <python-bin> <node-bin> <external-env-root>" >&2
  exit 64
fi

SERVICE="$1"
PROJECT_ROOT="$(cd "$2" && pwd)"
PYTHON_BIN="$3"
NODE_BIN="$4"
EXTERNAL_ENV_ROOT="$5"
DATABASE_URL=""

case "$SERVICE" in
  watchlist-api|portfolio-api)
    DATABASE_URL="${INVESTMENT_STUDIO_LOCAL_DATABASE_URL:-}"
    if [[ -z "$DATABASE_URL" ]]; then
      echo "INVESTMENT_STUDIO_LOCAL_DATABASE_URL is required for API services." >&2
      exit 64
    fi
    case "$DATABASE_URL" in
      postgresql://*|postgresql+psycopg://*) ;;
      *)
        echo "INVESTMENT_STUDIO_LOCAL_DATABASE_URL must use postgresql:// or postgresql+psycopg://." >&2
        exit 64
        ;;
    esac
    ;;
esac

# Load only namespaced dotenv assignments as data.  This preserves the current
# external secret file without copying secrets into a plist or evaluating shell
# syntax contained in a value.
source "$PROJECT_ROOT/infra/launchd/load_runtime_env.sh"
case "$SERVICE" in
  watchlist-api|portfolio-api)
    investment_studio_require_password_free_database_url "$DATABASE_URL"
    DATABASE_URL="$(investment_studio_sqlalchemy_database_url "$DATABASE_URL")"
    investment_studio_reject_repository_env_files "$PROJECT_ROOT"
    ;;
esac
case "$SERVICE" in
  home-api)
    investment_studio_reject_repository_env_files "$PROJECT_ROOT"
    ENV_FILE="$(investment_studio_runtime_env_file home "$EXTERNAL_ENV_ROOT")"
    investment_studio_load_env_file "$ENV_FILE" INVESTMENT_STUDIO_HOME_
    ;;
  watchlist-api)
    ENV_FILE="$(investment_studio_runtime_env_file watchlist "$EXTERNAL_ENV_ROOT")"
    if [[ -f "$ENV_FILE" ]]; then
      investment_studio_load_env_file "$ENV_FILE" INVESTMENT_STUDIO_WATCHLIST_
    fi
    ;;
  portfolio-api)
    ENV_FILE="$(investment_studio_runtime_env_file portfolio "$EXTERNAL_ENV_ROOT")"
    if [[ -f "$ENV_FILE" ]]; then
      investment_studio_load_env_file "$ENV_FILE" INVESTMENT_STUDIO_PORTFOLIO_
    fi
    ;;
esac

export PYTHONUNBUFFERED=1
export PYTHONNOUSERSITE=1
export PYTHONPATH="$PROJECT_ROOT/shared-data/instruments/python"

case "$SERVICE" in
  home-api)
    export PYTHONPATH="$PROJECT_ROOT/home/backend"
    export INVESTMENT_STUDIO_HOME_ENVIRONMENT=local
    export INVESTMENT_STUDIO_HOME_FRONTEND_URL=http://127.0.0.1:5172
    cd "$PROJECT_ROOT/home/backend"
    exec "$PYTHON_BIN" -m uvicorn home_api.main:app --host 127.0.0.1 --port 8002
    ;;
  watchlist-api)
    export PYTHONPATH="$PROJECT_ROOT/apps/watchlist/backend:$PYTHONPATH"
    export INVESTMENT_STUDIO_WATCHLIST_ENVIRONMENT=local
    export INVESTMENT_STUDIO_WATCHLIST_DATABASE_URL="$DATABASE_URL"
    export INVESTMENT_STUDIO_WATCHLIST_DATABASE_SCHEMA=watchlist
    export INVESTMENT_STUDIO_WATCHLIST_FRONTEND_URL=http://127.0.0.1:5173
    export INVESTMENT_STUDIO_WATCHLIST_CORS_ORIGINS='["http://127.0.0.1:5173","http://localhost:5173"]'
    export INVESTMENT_STUDIO_WATCHLIST_DOCUMENT_STORAGE_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/investment-studio/watchlist-documents"
    cd "$PROJECT_ROOT/apps/watchlist/backend"
    exec "$PYTHON_BIN" -m uvicorn watchlist_app.main:app --host 127.0.0.1 --port 8000
    ;;
  portfolio-api)
    export PYTHONPATH="$PROJECT_ROOT/apps/portfolio/backend:$PYTHONPATH"
    export INVESTMENT_STUDIO_PORTFOLIO_ENVIRONMENT=local
    export INVESTMENT_STUDIO_PORTFOLIO_DATABASE_URL="$DATABASE_URL"
    export INVESTMENT_STUDIO_PORTFOLIO_DATABASE_SCHEMA=portfolio
    export INVESTMENT_STUDIO_PORTFOLIO_FRONTEND_URL=http://127.0.0.1:5174
    export INVESTMENT_STUDIO_PORTFOLIO_CORS_ORIGINS='["http://127.0.0.1:5174","http://localhost:5174"]'
    export INVESTMENT_STUDIO_PORTFOLIO_RESEARCH_OUTPUTS_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/investment-studio/portfolio-research-outputs"
    cd "$PROJECT_ROOT/apps/portfolio/backend"
    exec "$PYTHON_BIN" -m uvicorn portfolio_app.main:app --host 127.0.0.1 --port 8001
    ;;
  home-web)
    exec "$NODE_BIN" "$PROJECT_ROOT/deploy/serve_spa_proxy.mjs" \
      --host 127.0.0.1 --port 5172 \
      --dist "$PROJECT_ROOT/home/frontend/dist" \
      --api-target http://127.0.0.1:8002
    ;;
  watchlist-web)
    exec "$NODE_BIN" "$PROJECT_ROOT/deploy/serve_spa_proxy.mjs" \
      --host 127.0.0.1 --port 5173 \
      --dist "$PROJECT_ROOT/apps/watchlist/frontend/dist" \
      --api-target http://127.0.0.1:8000
    ;;
  portfolio-web)
    exec "$NODE_BIN" "$PROJECT_ROOT/deploy/serve_spa_proxy.mjs" \
      --host 127.0.0.1 --port 5174 \
      --dist "$PROJECT_ROOT/apps/portfolio/frontend/dist" \
      --api-target http://127.0.0.1:8001
    ;;
  *)
    echo "Unknown service: $SERVICE" >&2
    exit 64
    ;;
esac
