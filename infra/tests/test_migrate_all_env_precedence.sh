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
  'if [[ "$1" == */alembic_revision_is_descendant.py ]]; then case "${FAKE_REGISTRY_CURRENT:-}" in 20260715_0012|20260716_0013|20260717_0014|20260717_0015|20260731_0016|20260804_0017|20260806_0018|20260807_0019) exit 0 ;; *) exit 1 ;; esac; fi' \
  > "$FAKE_PYTHON"
chmod +x "$FAKE_PYTHON"

export CAPTURE_PATH
export COMMAND_CAPTURE_PATH
export PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL="postgresql://instrument@explicit/portfolio_ops"
export PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL="postgresql://instrument-alembic@explicit/portfolio_ops"
export PORTFOLIO_OPS_PLATFORM_DATABASE_URL="postgresql://platform@explicit/portfolio_ops"
export PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL="postgresql://portfolio@explicit/portfolio_ops"
export PORTFOLIO_OPS_PORTFOLIO_ALEMBIC_DATABASE_URL="postgresql://portfolio-alembic@explicit/portfolio_ops"
export PORTFOLIO_OPS_WATCHLIST_DATABASE_URL="postgresql://watchlist@explicit/portfolio_ops"
unset PORTFOLIO_OPS_WATCHLIST_ALEMBIC_DATABASE_URL

PROJECT_ROOT="$TEST_ROOT" PYTHON_BIN="$FAKE_PYTHON" ENV_ROOT="" \
  "$REPOSITORY_ROOT/infra/scripts/migrate_all.sh"

if grep -q 'from-env-file' "$CAPTURE_PATH"; then
  echo "A .env value overrode the explicit process environment." >&2
  exit 1
fi
grep -q 'postgresql://instrument@explicit/portfolio_ops' "$CAPTURE_PATH"
grep -q 'postgresql://instrument-alembic@explicit/portfolio_ops' "$CAPTURE_PATH"
grep -q 'postgresql://platform@explicit/portfolio_ops' "$CAPTURE_PATH"
grep -q 'postgresql://portfolio@explicit/portfolio_ops' "$CAPTURE_PATH"
grep -q 'postgresql://portfolio-alembic@explicit/portfolio_ops' "$CAPTURE_PATH"
grep -q 'postgresql://watchlist@explicit/portfolio_ops' "$CAPTURE_PATH"

: > "$COMMAND_CAPTURE_PATH"
for registry_revision in 20260804_0017 20260806_0018 20260807_0019; do
  : > "$COMMAND_CAPTURE_PATH"
  export FAKE_REGISTRY_CURRENT="$registry_revision"
  PROJECT_ROOT="$TEST_ROOT" PYTHON_BIN="$FAKE_PYTHON" ENV_ROOT="" \
    "$REPOSITORY_ROOT/infra/scripts/migrate_all.sh"
  if grep -q 'upgrade 20260715_0011' "$COMMAND_CAPTURE_PATH"; then
    echo "migrate_all tried to return an already-migrated registry to its prerequisite: $registry_revision" >&2
    exit 1
  fi
  grep -q '/infra/instrument_registry|-m alembic upgrade head' "$COMMAND_CAPTURE_PATH"
done
unset FAKE_REGISTRY_CURRENT

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
