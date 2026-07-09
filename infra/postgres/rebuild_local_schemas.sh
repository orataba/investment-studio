#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PSQL_URL="${PORTFOLIO_OPS_LOCAL_POSTGRES_URL:-postgresql://portfolio_ops:portfolio_ops@127.0.0.1:5432/portfolio_ops}"

echo "Rebuilding local schemas in ${PSQL_URL}"

psql "${PSQL_URL}" <<'SQL'
DROP SCHEMA IF EXISTS watchlist CASCADE;
DROP SCHEMA IF EXISTS portfolio CASCADE;
DROP SCHEMA IF EXISTS instrument_registry CASCADE;
SQL

(
  cd "${ROOT_DIR}/infra/instrument_registry"
  alembic upgrade head
)

(
  cd "${ROOT_DIR}/apps/portfolio/backend"
  PYTHONPATH=. alembic upgrade head
)

(
  cd "${ROOT_DIR}/apps/watchlist/backend"
  PYTHONPATH=. alembic upgrade head
)

echo "Local schemas rebuilt successfully."
