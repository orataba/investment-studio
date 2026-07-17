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
  "$TEST_ROOT/apps/platform/backend/alembic.ini" \
  "$TEST_ROOT/apps/portfolio/backend/alembic.ini" \
  "$TEST_ROOT/apps/watchlist/backend/alembic.ini"

for app in platform portfolio watchlist; do
  upper_app="$(printf '%s' "$app" | tr '[:lower:]' '[:upper:]')"
  printf 'PORTFOLIO_OPS_%s_DATABASE_URL=postgresql://from-env-file/%s\nPORTFOLIO_OPS_%s_ALEMBIC_DATABASE_URL=postgresql://from-env-file/%s-alembic\n' \
    "$upper_app" "$app" "$upper_app" "$app" > "$TEST_ROOT/apps/$app/backend/.env"
done

FAKE_PYTHON="$TEST_ROOT/fake-python"
CAPTURE_PATH="$TEST_ROOT/captured-environment"
COMMAND_CAPTURE_PATH="$TEST_ROOT/captured-commands"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf "%s|%s|%s|%s|%s|%s|%s|%s|%s\n" "$PWD" "${PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL:-}" "${PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL:-}" "${PORTFOLIO_OPS_PLATFORM_DATABASE_URL:-}" "${PORTFOLIO_OPS_PLATFORM_ALEMBIC_DATABASE_URL:-}" "${PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL:-}" "${PORTFOLIO_OPS_PORTFOLIO_ALEMBIC_DATABASE_URL:-}" "${PORTFOLIO_OPS_WATCHLIST_DATABASE_URL:-}" "${PORTFOLIO_OPS_WATCHLIST_ALEMBIC_DATABASE_URL:-}" >> "$CAPTURE_PATH"' \
  'printf "%s|%s\n" "$PWD" "$*" >> "$COMMAND_CAPTURE_PATH"' \
  'if [[ "$PWD" == */infra/instrument_registry && "$*" == "-m alembic current" && -n "${FAKE_REGISTRY_CURRENT:-}" ]]; then printf "%s (head)\n" "$FAKE_REGISTRY_CURRENT"; fi' \
  > "$FAKE_PYTHON"
chmod +x "$FAKE_PYTHON"

export CAPTURE_PATH
export COMMAND_CAPTURE_PATH
export PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL="postgresql://explicit/instrument"
export PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL="postgresql://explicit/instrument-alembic"
export PORTFOLIO_OPS_PLATFORM_DATABASE_URL="postgresql://explicit/platform"
export PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL="postgresql://explicit/portfolio"
export PORTFOLIO_OPS_PORTFOLIO_ALEMBIC_DATABASE_URL="postgresql://explicit/portfolio-alembic"
export PORTFOLIO_OPS_WATCHLIST_DATABASE_URL="postgresql://explicit/watchlist"
unset PORTFOLIO_OPS_WATCHLIST_ALEMBIC_DATABASE_URL

PROJECT_ROOT="$TEST_ROOT" PYTHON_BIN="$FAKE_PYTHON" ENV_ROOT="" \
  "$REPOSITORY_ROOT/infra/scripts/migrate_all.sh"

if grep -q 'from-env-file' "$CAPTURE_PATH"; then
  echo "A .env value overrode the explicit process environment." >&2
  exit 1
fi
grep -q 'postgresql://explicit/instrument' "$CAPTURE_PATH"
grep -q 'postgresql://explicit/instrument-alembic' "$CAPTURE_PATH"
grep -q 'postgresql://explicit/platform' "$CAPTURE_PATH"
grep -q 'postgresql://explicit/portfolio' "$CAPTURE_PATH"
grep -q 'postgresql://explicit/portfolio-alembic' "$CAPTURE_PATH"
grep -q 'postgresql://explicit/watchlist' "$CAPTURE_PATH"

: > "$COMMAND_CAPTURE_PATH"
export FAKE_REGISTRY_CURRENT="20260717_0015"
PROJECT_ROOT="$TEST_ROOT" PYTHON_BIN="$FAKE_PYTHON" ENV_ROOT="" \
  "$REPOSITORY_ROOT/infra/scripts/migrate_all.sh"
unset FAKE_REGISTRY_CURRENT
if grep -q 'upgrade 20260715_0011' "$COMMAND_CAPTURE_PATH"; then
  echo "migrate_all tried to return an already-migrated registry to its prerequisite." >&2
  exit 1
fi
grep -q '/infra/instrument_registry|-m alembic upgrade head' "$COMMAND_CAPTURE_PATH"

captured_line_count="$(wc -l < "$CAPTURE_PATH" | tr -d '[:space:]')"
unset PORTFOLIO_OPS_WATCHLIST_DATABASE_URL
if PROJECT_ROOT="$TEST_ROOT" PYTHON_BIN="$FAKE_PYTHON" ENV_ROOT="" \
  "$REPOSITORY_ROOT/infra/scripts/migrate_all.sh" >/dev/null 2>&1; then
  echo "migrate_all accepted a missing explicit Watchlist database target." >&2
  exit 1
fi
if [[ "$(wc -l < "$CAPTURE_PATH" | tr -d '[:space:]')" != "$captured_line_count" ]]; then
  echo "migrate_all started migrations before validating every database target." >&2
  exit 1
fi

echo "migrate_all explicit-environment precedence test passed."
