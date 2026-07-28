#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VALIDATOR="$REPOSITORY_ROOT/infra/scripts/validate_migration_targets.py"
PYTHON_BIN="${PYTHON_BIN:-$REPOSITORY_ROOT/.venv/bin/python}"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-migration-target-test.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

export PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL="postgresql://registry-user@db.internal/portfolio_ops"
export PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL="postgresql+psycopg://registry-migration@db.internal:5432/portfolio_ops"
export PORTFOLIO_OPS_PLATFORM_DATABASE_URL="postgresql://platform-user@db.internal/portfolio_ops"
export PORTFOLIO_OPS_PLATFORM_ALEMBIC_DATABASE_URL="postgresql://platform-migration@db.internal/portfolio_ops"
export PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL="postgresql://portfolio-user@db.internal/portfolio_ops"
export PORTFOLIO_OPS_PORTFOLIO_ALEMBIC_DATABASE_URL="postgresql://portfolio-migration@db.internal/portfolio_ops"
export PORTFOLIO_OPS_WATCHLIST_DATABASE_URL="postgresql://watchlist-user@db.internal/portfolio_ops"
export PORTFOLIO_OPS_WATCHLIST_ALEMBIC_DATABASE_URL="postgresql://watchlist-migration@db.internal/portfolio_ops"
export PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE="portfolio_ops"

"$PYTHON_BIN" "$VALIDATOR" > "$TEST_ROOT/accepted"
grep -q 'database=portfolio_ops' "$TEST_ROOT/accepted"
if grep -q 'registry-user\\|migration@\\|platform-user\\|portfolio-user\\|watchlist-user' "$TEST_ROOT/accepted"; then
  echo "Migration target validator exposed URL credentials." >&2
  exit 1
fi

PORTFOLIO_OPS_WATCHLIST_DATABASE_URL="postgresql://watchlist-user@db.internal/wrong_database" \
  "$PYTHON_BIN" "$VALIDATOR" > "$TEST_ROOT/split-database" 2>&1 && {
    echo "Migration target validator accepted split primary databases." >&2
    exit 1
  }
grep -q 'do not identify one canonical' "$TEST_ROOT/split-database"

PORTFOLIO_OPS_PLATFORM_ALEMBIC_DATABASE_URL="postgresql://platform-migration@other-host/portfolio_ops" \
  "$PYTHON_BIN" "$VALIDATOR" > "$TEST_ROOT/alembic-redirect" 2>&1 && {
    echo "Migration target validator accepted an Alembic redirect." >&2
    exit 1
  }
grep -q 'PORTFOLIO_OPS_PLATFORM_ALEMBIC_DATABASE_URL' "$TEST_ROOT/alembic-redirect"

PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE="another_database" \
  "$PYTHON_BIN" "$VALIDATOR" > "$TEST_ROOT/expected-database" 2>&1 && {
    echo "Migration target validator ignored the expected database name." >&2
    exit 1
  }
grep -q 'PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE' "$TEST_ROOT/expected-database"

echo "Migration target validation tests passed."
