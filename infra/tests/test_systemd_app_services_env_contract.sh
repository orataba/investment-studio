#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-systemd-app-env-test.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

PROJECT_ROOT="$TEST_ROOT/project"
ENV_ROOT="$TEST_ROOT/secure env"
MOCK_BIN="$TEST_ROOT/bin"
SYSTEMCTL_CALLS="$TEST_ROOT/systemctl-calls"
mkdir -p \
  "$PROJECT_ROOT/apps/platform/backend" \
  "$PROJECT_ROOT/apps/watchlist/backend" \
  "$PROJECT_ROOT/apps/portfolio/backend" \
  "$PROJECT_ROOT/apps/platform/frontend/dist" \
  "$PROJECT_ROOT/apps/watchlist/frontend/dist" \
  "$PROJECT_ROOT/apps/portfolio/frontend/dist" \
  "$PROJECT_ROOT/deploy" \
  "$PROJECT_ROOT/infra/launchd" \
  "$PROJECT_ROOT/infra/scripts" \
  "$PROJECT_ROOT/packages/instrument-core/python" \
  "$ENV_ROOT" \
  "$MOCK_BIN"
touch \
  "$PROJECT_ROOT/apps/platform/frontend/dist/index.html" \
  "$PROJECT_ROOT/apps/watchlist/frontend/dist/index.html" \
  "$PROJECT_ROOT/apps/portfolio/frontend/dist/index.html" \
  "$PROJECT_ROOT/deploy/serve_spa_proxy.mjs"
cp "$REPOSITORY_ROOT/infra/launchd/load_runtime_env.sh" \
  "$PROJECT_ROOT/infra/launchd/load_runtime_env.sh"
printf '%s\n' '#!/usr/bin/env bash' 'exit 0' \
  > "$PROJECT_ROOT/infra/scripts/migrate_all.sh"
chmod +x "$PROJECT_ROOT/infra/scripts/migrate_all.sh"

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf "%s\n" "$*" >> "$SYSTEMCTL_CALLS"' \
  'exit 0' \
  > "$MOCK_BIN/systemctl"
chmod +x "$MOCK_BIN/systemctl"
export SYSTEMCTL_CALLS

chmod 700 "$ENV_ROOT"
write_env_files() {
  local platform_url="$1"
  local registry_url="$2"
  local watchlist_url="$3"
  local portfolio_url="$4"
  local platform_alembic_url="${5:-$platform_url}"
  printf '%s\n' \
    "PORTFOLIO_OPS_PLATFORM_DATABASE_URL=$platform_url" \
    "PORTFOLIO_OPS_PLATFORM_ALEMBIC_DATABASE_URL=$platform_alembic_url" \
    "PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL=$registry_url" \
    > "$ENV_ROOT/platform.env"
  printf '%s\n' "PORTFOLIO_OPS_WATCHLIST_DATABASE_URL=$watchlist_url" \
    > "$ENV_ROOT/watchlist.env"
  printf '%s\n' "PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL=$portfolio_url" \
    > "$ENV_ROOT/portfolio.env"
  chmod 600 "$ENV_ROOT/platform.env" "$ENV_ROOT/watchlist.env" "$ENV_ROOT/portfolio.env"
}

CANONICAL_URL='postgresql+psycopg://portfolio_ops@127.0.0.1:5432/portfolio_ops'
write_env_files "$CANONICAL_URL" "$CANONICAL_URL" "$CANONICAL_URL" "$CANONICAL_URL"

run_installer() {
  local config_root="$1"
  local env_root="$2"
  HOME="$TEST_ROOT/home" \
  XDG_CONFIG_HOME="$config_root" \
  PATH="$MOCK_BIN:$PATH" \
  PROJECT_ROOT="$PROJECT_ROOT" \
  PYTHON_BIN="$(command -v python3)" \
  NODE_BIN="/usr/bin/true" \
  ENV_ROOT="$env_root" \
  RUN_MIGRATIONS=false \
  START_SERVICES=false \
    "$REPOSITORY_ROOT/infra/systemd/install_app_services.sh"
}

set +e
run_installer "$TEST_ROOT/config-missing" "" > "$TEST_ROOT/missing.out" 2>&1
missing_status=$?
set -e
if [[ $missing_status -ne 64 ]]; then
  echo "The systemd app installer accepted a missing external environment root." >&2
  exit 1
fi

ln -s "$TEST_ROOT/nonexistent-env" "$PROJECT_ROOT/apps/portfolio/backend/.env"
set +e
run_installer "$TEST_ROOT/config-repository-env" "$ENV_ROOT" \
  > "$TEST_ROOT/repository-env.out" 2>&1
repository_env_status=$?
set -e
rm "$PROJECT_ROOT/apps/portfolio/backend/.env"
if [[ $repository_env_status -eq 0 ]]; then
  echo "The systemd app installer accepted a repository-local .env symlink." >&2
  exit 1
fi
grep -q 'Repository runtime environment files are not allowed for managed services' \
  "$TEST_ROOT/repository-env.out"

MISMATCHED_URL='postgresql+psycopg://portfolio_ops@127.0.0.1:5432/other_database'
write_env_files "$CANONICAL_URL" "$CANONICAL_URL" "$MISMATCHED_URL" "$CANONICAL_URL"
set +e
run_installer "$TEST_ROOT/config-mismatch" "$ENV_ROOT" > "$TEST_ROOT/mismatch.out" 2>&1
mismatch_status=$?
set -e
if [[ $mismatch_status -eq 0 ]]; then
  echo "The systemd app installer accepted different application database targets." >&2
  exit 1
fi
grep -q 'must target the same PostgreSQL' "$TEST_ROOT/mismatch.out"

write_env_files "$CANONICAL_URL" "$CANONICAL_URL" "$CANONICAL_URL" "$CANONICAL_URL" "$MISMATCHED_URL"
set +e
run_installer "$TEST_ROOT/config-platform-migration-mismatch" "$ENV_ROOT" \
  > "$TEST_ROOT/platform-migration-mismatch.out" 2>&1
platform_migration_mismatch_status=$?
set -e
if [[ $platform_migration_mismatch_status -eq 0 ]]; then
  echo "The systemd app installer accepted a different Platform migration target." >&2
  exit 1
fi
grep -q 'PORTFOLIO_OPS_PLATFORM_ALEMBIC_DATABASE_URL' \
  "$TEST_ROOT/platform-migration-mismatch.out"

write_env_files "$CANONICAL_URL" "$CANONICAL_URL" "$CANONICAL_URL" "$CANONICAL_URL"
printf '%s\n' 'PORTFOLIO_OPS_PLATFORM_OPERATIONS_DATABASE_SCHEMA=instrument_registry' \
  >> "$ENV_ROOT/platform.env"
set +e
run_installer "$TEST_ROOT/config-platform-schema-mismatch" "$ENV_ROOT" \
  > "$TEST_ROOT/platform-schema-mismatch.out" 2>&1
platform_schema_mismatch_status=$?
set -e
if [[ $platform_schema_mismatch_status -eq 0 ]]; then
  echo "The systemd app installer accepted an invalid Platform operations schema." >&2
  exit 1
fi
grep -q 'PORTFOLIO_OPS_PLATFORM_OPERATIONS_DATABASE_SCHEMA must be platform' \
  "$TEST_ROOT/platform-schema-mismatch.out"

PASSWORD_URL="${CANONICAL_URL/portfolio_ops@/portfolio_ops:test-password@}"
write_env_files "$PASSWORD_URL" "$CANONICAL_URL" "$CANONICAL_URL" "$CANONICAL_URL"
set +e
run_installer "$TEST_ROOT/config-password" "$ENV_ROOT" > "$TEST_ROOT/password.out" 2>&1
password_status=$?
set -e
if [[ $password_status -eq 0 ]]; then
  echo "The systemd app installer accepted a password-bearing database URL." >&2
  exit 1
fi
grep -q 'must not contain a password' "$TEST_ROOT/password.out"

write_env_files "$CANONICAL_URL" "$CANONICAL_URL" "$CANONICAL_URL" "$CANONICAL_URL"
SUCCESS_CONFIG_ROOT="$TEST_ROOT/config-success"
run_installer "$SUCCESS_CONFIG_ROOT" "$ENV_ROOT"

UNIT_ROOT="$SUCCESS_CONFIG_ROOT/systemd/user"
for app in platform watchlist portfolio; do
  api_unit="$UNIT_ROOT/portfolio-ops-$app-api.service"
  web_unit="$UNIT_ROOT/portfolio-ops-$app-web.service"
  test -f "$api_unit"
  test -f "$web_unit"
  grep -Fq 'EnvironmentFile=' "$api_unit"
  grep -Fq "$app.env" "$api_unit"
  if grep -Fq "/apps/$app/backend/.env" "$api_unit"; then
    echo "Generated unit retained a repository-local environment fallback: $api_unit" >&2
    exit 1
  fi
done
grep -Fxq 'Environment=PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA=instrument_registry' \
  "$UNIT_ROOT/portfolio-ops-platform-api.service"
grep -Fxq 'Environment=PORTFOLIO_OPS_PLATFORM_OPERATIONS_DATABASE_SCHEMA=platform' \
  "$UNIT_ROOT/portfolio-ops-platform-api.service"
grep -Fq 'ExecStart=/usr/bin/env PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA=instrument_registry PORTFOLIO_OPS_PLATFORM_OPERATIONS_DATABASE_SCHEMA=platform ' \
  "$UNIT_ROOT/portfolio-ops-platform-api.service"

echo "systemd app external-environment contract test passed."
