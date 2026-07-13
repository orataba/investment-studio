#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-migrate-env-test.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

mkdir -p \
  "$TEST_ROOT/infra/instrument_registry" \
  "$TEST_ROOT/apps/platform/backend" \
  "$TEST_ROOT/apps/portfolio/backend" \
  "$TEST_ROOT/apps/watchlist/backend" \
  "$TEST_ROOT/packages/instrument-core/python"
touch \
  "$TEST_ROOT/infra/instrument_registry/alembic.ini" \
  "$TEST_ROOT/apps/portfolio/backend/alembic.ini" \
  "$TEST_ROOT/apps/watchlist/backend/alembic.ini"

for app in platform portfolio watchlist; do
  upper_app="$(printf '%s' "$app" | tr '[:lower:]' '[:upper:]')"
  printf 'PORTFOLIO_OPS_%s_DATABASE_URL=postgresql://from-env-file/%s\nPORTFOLIO_OPS_%s_ALEMBIC_DATABASE_URL=postgresql://from-env-file/%s-alembic\nPORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE=must-not-authorize-from-env-file\n' \
    "$upper_app" "$app" "$upper_app" "$app" > "$TEST_ROOT/apps/$app/backend/.env"
done

FAKE_PYTHON="$TEST_ROOT/fake-python"
CAPTURE_PATH="$TEST_ROOT/captured-environment"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf "%s|%s|%s|%s|%s|%s|%s\n" "$PWD" "${PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL:-}" "${PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL:-}" "${PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL:-}" "${PORTFOLIO_OPS_PORTFOLIO_ALEMBIC_DATABASE_URL:-}" "${PORTFOLIO_OPS_WATCHLIST_DATABASE_URL:-}" "${PORTFOLIO_OPS_WATCHLIST_ALEMBIC_DATABASE_URL:-}" >> "$CAPTURE_PATH"' \
  > "$FAKE_PYTHON"
chmod +x "$FAKE_PYTHON"

export CAPTURE_PATH
export PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL="postgresql://explicit/instrument"
export PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL="postgresql://explicit/instrument-alembic"
export PORTFOLIO_OPS_PLATFORM_DATABASE_URL="postgresql://explicit/platform"
export PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL="postgresql://explicit/portfolio"
export PORTFOLIO_OPS_PORTFOLIO_ALEMBIC_DATABASE_URL="postgresql://explicit/portfolio-alembic"
export PORTFOLIO_OPS_WATCHLIST_DATABASE_URL="postgresql://explicit/watchlist"
export PORTFOLIO_OPS_WATCHLIST_ALEMBIC_DATABASE_URL="postgresql://explicit/watchlist-alembic"
export PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE="explicit-target"

PROJECT_ROOT="$TEST_ROOT" PYTHON_BIN="$FAKE_PYTHON" ENV_ROOT="" \
  "$REPOSITORY_ROOT/infra/scripts/migrate_all.sh"

if grep -q 'from-env-file' "$CAPTURE_PATH"; then
  echo "A .env value overrode the explicit process environment." >&2
  exit 1
fi
grep -q 'postgresql://explicit/instrument' "$CAPTURE_PATH"
grep -q 'postgresql://explicit/instrument-alembic' "$CAPTURE_PATH"
grep -q 'postgresql://explicit/portfolio' "$CAPTURE_PATH"
grep -q 'postgresql://explicit/portfolio-alembic' "$CAPTURE_PATH"
grep -q 'postgresql://explicit/watchlist' "$CAPTURE_PATH"
grep -q 'postgresql://explicit/watchlist-alembic' "$CAPTURE_PATH"

unset PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE
if PROJECT_ROOT="$TEST_ROOT" PYTHON_BIN="$FAKE_PYTHON" ENV_ROOT="" \
  "$REPOSITORY_ROOT/infra/scripts/migrate_all.sh" >/dev/null 2>&1; then
  echo "A runtime .env file authorized a migration without caller confirmation." >&2
  exit 1
fi

echo "migrate_all explicit-environment precedence test passed."
