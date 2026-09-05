#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/investment-studio-launchd-env-test.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

ENV_ROOT="$TEST_ROOT/secure env"
MOCK_BIN="$TEST_ROOT/bin"
CAPTURE_PATH="$TEST_ROOT/captured-environment"
WEB_CAPTURE_PATH="$TEST_ROOT/captured-web-environment"
SENTINEL_PATH="$TEST_ROOT/unsafe-env-executed"
mkdir -p "$ENV_ROOT" "$MOCK_BIN"
chmod 700 "$ENV_ROOT"

printf '%s\n' \
  'INVESTMENT_STUDIO_HOME_AUTH_USERNAME=api-key-loaded' \
  'INVESTMENT_STUDIO_HOME_AUTH_COOKIE_NAME=true' \
  'INVESTMENT_STUDIO_HOME_AUTH_SESSION_SECRET_FILE=$(touch "'$SENTINEL_PATH'")' \
  > "$ENV_ROOT/home.env"
chmod 600 "$ENV_ROOT/home.env"

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf "%s\n" "${INVESTMENT_STUDIO_HOME_AUTH_USERNAME:-}" "${INVESTMENT_STUDIO_HOME_AUTH_COOKIE_NAME:-}" "${INVESTMENT_STUDIO_HOME_AUTH_SESSION_SECRET_FILE:-}" "${INVESTMENT_STUDIO_DATA_DATABASE_URL:-}" "${INVESTMENT_STUDIO_DATA_DATABASE_SCHEMA:-}" "${INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA:-}" "$*" > "$CAPTURE_PATH"' \
  > "$MOCK_BIN/python"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf "%s\n%s\n" "${INVESTMENT_STUDIO_LOCAL_DATABASE_URL:-}" "$*" > "$WEB_CAPTURE_PATH"' \
  > "$MOCK_BIN/node"
chmod +x "$MOCK_BIN/python" "$MOCK_BIN/node"

export CAPTURE_PATH WEB_CAPTURE_PATH
INVESTMENT_STUDIO_LOCAL_DATABASE_URL="postgresql+psycopg://explicit/local" \
  "$REPOSITORY_ROOT/infra/launchd/run_local_service.sh" \
  home-api "$REPOSITORY_ROOT" "$MOCK_BIN/python" "$MOCK_BIN/node" "$ENV_ROOT"

if [[ -e "$SENTINEL_PATH" ]]; then
  echo "The API runner executed shell syntax from the Home environment file." >&2
  exit 1
fi
[[ "$(sed -n '1p' "$CAPTURE_PATH")" == "api-key-loaded" ]]
[[ "$(sed -n '2p' "$CAPTURE_PATH")" == "true" ]]
[[ "$(sed -n '3p' "$CAPTURE_PATH")" == '$(touch "'$SENTINEL_PATH'")' ]]
[[ -z "$(sed -n '4p' "$CAPTURE_PATH")" ]]
[[ -z "$(sed -n '5p' "$CAPTURE_PATH")" ]]
[[ -z "$(sed -n '6p' "$CAPTURE_PATH")" ]]
[[ "$(sed -n '7p' "$CAPTURE_PATH")" == "-m uvicorn home_api.main:app --host 127.0.0.1 --port 8002" ]]

INVESTMENT_STUDIO_LOCAL_DATABASE_URL="postgresql://explicit/local" \
  "$REPOSITORY_ROOT/infra/launchd/run_local_service.sh" \
  home-api "$REPOSITORY_ROOT" "$MOCK_BIN/python" "$MOCK_BIN/node" "$ENV_ROOT"
[[ -z "$(sed -n '4p' "$CAPTURE_PATH")" ]]

set +e
"$REPOSITORY_ROOT/infra/launchd/run_local_service.sh" \
  watchlist-api "$REPOSITORY_ROOT" "$MOCK_BIN/python" "$MOCK_BIN/node" "$ENV_ROOT" \
  > "$TEST_ROOT/missing-database-url.out" 2>&1
missing_database_status=$?
set -e
if [[ $missing_database_status -ne 64 ]]; then
  echo "The API runner accepted an invocation without an explicit database URL." >&2
  exit 1
fi
grep -q 'INVESTMENT_STUDIO_LOCAL_DATABASE_URL is required' "$TEST_ROOT/missing-database-url.out"

"$REPOSITORY_ROOT/infra/launchd/run_local_service.sh" \
  home-web "$REPOSITORY_ROOT" "$MOCK_BIN/python" "$MOCK_BIN/node" "$ENV_ROOT"
[[ -z "$(sed -n '1p' "$WEB_CAPTURE_PATH")" ]]
[[ "$(sed -n '2p' "$WEB_CAPTURE_PATH")" == *"--port 5172"* ]]

chmod 644 "$ENV_ROOT/home.env"
set +e
INVESTMENT_STUDIO_LOCAL_DATABASE_URL="postgresql+psycopg://explicit/local" \
  "$REPOSITORY_ROOT/infra/launchd/run_local_service.sh" \
  home-api "$REPOSITORY_ROOT" "$MOCK_BIN/python" "$MOCK_BIN/node" "$ENV_ROOT" \
  > "$TEST_ROOT/insecure.out" 2>&1
insecure_status=$?
set -e
if [[ $insecure_status -eq 0 ]]; then
  echo "The API runner accepted a group/world-readable secret file." >&2
  exit 1
fi
grep -q 'must not be accessible by group or others' "$TEST_ROOT/insecure.out"

chmod 600 "$ENV_ROOT/home.env"
chmod 755 "$ENV_ROOT"
set +e
INVESTMENT_STUDIO_LOCAL_DATABASE_URL="postgresql+psycopg://explicit/local" \
  "$REPOSITORY_ROOT/infra/launchd/run_local_service.sh" \
  home-api "$REPOSITORY_ROOT" "$MOCK_BIN/python" "$MOCK_BIN/node" "$ENV_ROOT" \
  > "$TEST_ROOT/insecure-directory.out" 2>&1
insecure_directory_status=$?
set -e
if [[ $insecure_directory_status -eq 0 ]]; then
  echo "The API runner accepted a group/world-accessible secret directory." >&2
  exit 1
fi
grep -q 'directory must not be accessible by group or others' "$TEST_ROOT/insecure-directory.out"

REPOSITORY_ENV_PROJECT="$TEST_ROOT/project-with-repository-env"
mkdir -p \
  "$REPOSITORY_ENV_PROJECT/infra/launchd" \
  "$REPOSITORY_ENV_PROJECT/shared-data" \
  "$REPOSITORY_ENV_PROJECT/apps/watchlist/backend" \
  "$REPOSITORY_ENV_PROJECT/apps/portfolio/backend"
cp "$REPOSITORY_ROOT/infra/launchd/load_runtime_env.sh" \
  "$REPOSITORY_ENV_PROJECT/infra/launchd/load_runtime_env.sh"
ln -s "$TEST_ROOT/missing-secret-file" "$REPOSITORY_ENV_PROJECT/apps/portfolio/backend/.env"
chmod 700 "$ENV_ROOT"
set +e
INVESTMENT_STUDIO_LOCAL_DATABASE_URL="postgresql+psycopg://explicit/local" \
  "$REPOSITORY_ROOT/infra/launchd/run_local_service.sh" \
  home-api "$REPOSITORY_ENV_PROJECT" "$MOCK_BIN/python" "$MOCK_BIN/node" "$ENV_ROOT" \
  > "$TEST_ROOT/repository-env.out" 2>&1
repository_env_status=$?
set -e
if [[ $repository_env_status -eq 0 ]]; then
  echo "The API runner accepted a repository-local .env symlink." >&2
  exit 1
fi
grep -q 'Repository runtime environment files are not allowed for managed services' "$TEST_ROOT/repository-env.out"
grep -q 'apps/portfolio/backend/.env' "$TEST_ROOT/repository-env.out"

set +e
INVESTMENT_STUDIO_LOCAL_DATABASE_URL="postgresql+psycopg://explicit/local" \
  "$REPOSITORY_ROOT/infra/launchd/run_local_service.sh" \
  home-api "$REPOSITORY_ROOT" "$MOCK_BIN/python" "$MOCK_BIN/node" \
  > "$TEST_ROOT/missing-env-root.out" 2>&1
missing_env_root_status=$?
set -e
if [[ $missing_env_root_status -ne 64 ]]; then
  echo "The service runner accepted an invocation without an explicit environment root." >&2
  exit 1
fi
grep -q '<external-env-root>' "$TEST_ROOT/missing-env-root.out"

echo "launchd safe runtime environment loader test passed."
