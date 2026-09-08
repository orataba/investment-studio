#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VALIDATOR="$REPOSITORY_ROOT/infra/scripts/validate_migration_targets.py"
PYTHON_BIN="${PYTHON_BIN:-$REPOSITORY_ROOT/.venv/bin/python}"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/investment-studio-migration-target-test.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

export INVESTMENT_STUDIO_HOME_DATABASE_URL="postgresql://identity-user@db.internal/investment_studio"
export INVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL="postgresql://registry-user@db.internal/investment_studio"
export INVESTMENT_STUDIO_INSTRUMENT_DATA_ALEMBIC_DATABASE_URL="postgresql+psycopg://registry-migration@db.internal:5432/investment_studio"
export INVESTMENT_STUDIO_DATA_DATABASE_URL="postgresql://platform-user@db.internal/investment_studio"
export INVESTMENT_STUDIO_DATA_ALEMBIC_DATABASE_URL="postgresql://platform-migration@db.internal/investment_studio"
export INVESTMENT_STUDIO_PORTFOLIO_DATABASE_URL="postgresql://portfolio-user@db.internal/investment_studio"
export INVESTMENT_STUDIO_PORTFOLIO_ALEMBIC_DATABASE_URL="postgresql://portfolio-migration@db.internal/investment_studio"
export INVESTMENT_STUDIO_WATCHLIST_DATABASE_URL="postgresql://watchlist-user@db.internal/investment_studio"
export INVESTMENT_STUDIO_WATCHLIST_ALEMBIC_DATABASE_URL="postgresql://watchlist-migration@db.internal/investment_studio"
export INVESTMENT_STUDIO_MARKET_DATABASE_URL="postgresql://market-user@db.internal/investment_studio"
export INVESTMENT_STUDIO_BRIEFING_DATABASE_URL="postgresql://briefing-user@db.internal/investment_studio"
export INVESTMENT_STUDIO_MIGRATION_EXPECTED_DATABASE="investment_studio"

"$PYTHON_BIN" "$VALIDATOR" > "$TEST_ROOT/accepted"
grep -q 'database=investment_studio' "$TEST_ROOT/accepted"
if grep -q 'registry-user\\|migration@\\|platform-user\\|portfolio-user\\|watchlist-user' "$TEST_ROOT/accepted"; then
  echo "Migration target validator exposed URL credentials." >&2
  exit 1
fi

INVESTMENT_STUDIO_WATCHLIST_DATABASE_URL="postgresql://watchlist-user@db.internal/wrong_database" \
  "$PYTHON_BIN" "$VALIDATOR" > "$TEST_ROOT/split-database" 2>&1 && {
    echo "Migration target validator accepted split primary databases." >&2
    exit 1
  }
grep -q 'do not identify one canonical' "$TEST_ROOT/split-database"

INVESTMENT_STUDIO_DATA_ALEMBIC_DATABASE_URL="postgresql://platform-migration@other-host/investment_studio" \
  "$PYTHON_BIN" "$VALIDATOR" > "$TEST_ROOT/alembic-redirect" 2>&1 && {
    echo "Migration target validator accepted an Alembic redirect." >&2
    exit 1
  }
grep -q 'INVESTMENT_STUDIO_DATA_ALEMBIC_DATABASE_URL' "$TEST_ROOT/alembic-redirect"

INVESTMENT_STUDIO_MIGRATION_EXPECTED_DATABASE="another_database" \
  "$PYTHON_BIN" "$VALIDATOR" > "$TEST_ROOT/expected-database" 2>&1 && {
    echo "Migration target validator ignored the expected database name." >&2
    exit 1
  }
grep -q 'INVESTMENT_STUDIO_MIGRATION_EXPECTED_DATABASE' "$TEST_ROOT/expected-database"

echo "Migration target validation tests passed."
