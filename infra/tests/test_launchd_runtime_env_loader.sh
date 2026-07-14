#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-launchd-env-test.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

ENV_ROOT="$TEST_ROOT/secure env"
MOCK_BIN="$TEST_ROOT/bin"
CAPTURE_PATH="$TEST_ROOT/captured-environment"
SENTINEL_PATH="$TEST_ROOT/unsafe-env-executed"
RUNTIME_ROOT="$TEST_ROOT/release/runtime"
STATE_ROOT="$TEST_ROOT/state root"
mkdir -p \
  "$ENV_ROOT" \
  "$MOCK_BIN" \
  "$RUNTIME_ROOT/infra/launchd" \
  "$RUNTIME_ROOT/apps/platform/backend" \
  "$RUNTIME_ROOT/apps/watchlist/backend" \
  "$RUNTIME_ROOT/apps/portfolio/backend" \
  "$RUNTIME_ROOT/packages/instrument-core/python" \
  "$STATE_ROOT/apps/platform/backend" \
  "$STATE_ROOT/apps/watchlist/backend" \
  "$STATE_ROOT/apps/portfolio/backend"
cp "$REPOSITORY_ROOT/infra/launchd/load_runtime_env.sh" \
  "$RUNTIME_ROOT/infra/launchd/load_runtime_env.sh"
: > "$RUNTIME_ROOT/infra/launchd/stage_local_runtime.py"
chmod 700 "$ENV_ROOT"

printf '%s\n' \
  'PORTFOLIO_OPS_PLATFORM_TUSHARE_TOKEN=api-token-loaded' \
  'PORTFOLIO_OPS_PLATFORM_EMAIL_SYNC_ENABLED=true' \
  'PORTFOLIO_OPS_PLATFORM_EMAIL_IMAP_PASSWORD=$(touch "'$SENTINEL_PATH'")' \
  'PORTFOLIO_OPS_PLATFORM_DATABASE_URL=postgresql://must-not-win/from-file' \
  > "$ENV_ROOT/platform.env"
chmod 600 "$ENV_ROOT/platform.env"

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf "%s\n" "${PORTFOLIO_OPS_PLATFORM_TUSHARE_TOKEN:-}" "${PORTFOLIO_OPS_PLATFORM_EMAIL_SYNC_ENABLED:-}" "${PORTFOLIO_OPS_PLATFORM_EMAIL_IMAP_PASSWORD:-}" "${PORTFOLIO_OPS_PLATFORM_DATABASE_URL:-}" "$*" > "$CAPTURE_PATH"' \
  > "$MOCK_BIN/python"
printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$MOCK_BIN/node"
chmod +x "$MOCK_BIN/python" "$MOCK_BIN/node"

export CAPTURE_PATH
PORTFOLIO_OPS_LOCAL_DATABASE_URL="postgresql+psycopg://explicit/local" \
PORTFOLIO_OPS_LOCAL_RELEASE_ID=release-test \
PORTFOLIO_OPS_LOCAL_STATE_ROOT="$STATE_ROOT" \
  "$REPOSITORY_ROOT/infra/launchd/run_local_service.sh" \
  platform-api "$RUNTIME_ROOT" "$MOCK_BIN/python" "$MOCK_BIN/node" "$ENV_ROOT"

if [[ -e "$SENTINEL_PATH" ]]; then
  echo "The API runner executed shell syntax from the Platform environment file." >&2
  exit 1
fi
[[ "$(sed -n '1p' "$CAPTURE_PATH")" == "api-token-loaded" ]]
[[ "$(sed -n '2p' "$CAPTURE_PATH")" == "true" ]]
[[ "$(sed -n '3p' "$CAPTURE_PATH")" == '$(touch "'$SENTINEL_PATH'")' ]]
[[ "$(sed -n '4p' "$CAPTURE_PATH")" == "postgresql+psycopg://explicit/local" ]]
[[ "$(sed -n '5p' "$CAPTURE_PATH")" == "-m uvicorn platform_app.main:app --host 127.0.0.1 --port 8002" ]]

chmod 644 "$ENV_ROOT/platform.env"
set +e
PORTFOLIO_OPS_LOCAL_DATABASE_URL="postgresql+psycopg://explicit/local" \
PORTFOLIO_OPS_LOCAL_RELEASE_ID=release-test \
PORTFOLIO_OPS_LOCAL_STATE_ROOT="$STATE_ROOT" \
  "$REPOSITORY_ROOT/infra/launchd/run_local_service.sh" \
  platform-api "$RUNTIME_ROOT" "$MOCK_BIN/python" "$MOCK_BIN/node" "$ENV_ROOT" \
  > "$TEST_ROOT/insecure.out" 2>&1
insecure_status=$?
set -e
if [[ $insecure_status -eq 0 ]]; then
  echo "The API runner accepted a group/world-readable secret file." >&2
  exit 1
fi
grep -q 'must not be accessible by group or others' "$TEST_ROOT/insecure.out"

chmod 600 "$ENV_ROOT/platform.env"
chmod 755 "$ENV_ROOT"
set +e
PORTFOLIO_OPS_LOCAL_DATABASE_URL="postgresql+psycopg://explicit/local" \
PORTFOLIO_OPS_LOCAL_RELEASE_ID=release-test \
PORTFOLIO_OPS_LOCAL_STATE_ROOT="$STATE_ROOT" \
  "$REPOSITORY_ROOT/infra/launchd/run_local_service.sh" \
  platform-api "$RUNTIME_ROOT" "$MOCK_BIN/python" "$MOCK_BIN/node" "$ENV_ROOT" \
  > "$TEST_ROOT/insecure-directory.out" 2>&1
insecure_directory_status=$?
set -e
if [[ $insecure_directory_status -eq 0 ]]; then
  echo "The API runner accepted a group/world-accessible secret directory." >&2
  exit 1
fi
grep -q 'directory must not be accessible by group or others' "$TEST_ROOT/insecure-directory.out"

ln -s "$TEST_ROOT/missing-secret-file" "$STATE_ROOT/apps/portfolio/backend/.env"
chmod 700 "$ENV_ROOT"
set +e
PORTFOLIO_OPS_LOCAL_DATABASE_URL="postgresql+psycopg://explicit/local" \
PORTFOLIO_OPS_LOCAL_RELEASE_ID=release-test \
PORTFOLIO_OPS_LOCAL_STATE_ROOT="$STATE_ROOT" \
  "$REPOSITORY_ROOT/infra/launchd/run_local_service.sh" \
  platform-api "$RUNTIME_ROOT" "$MOCK_BIN/python" "$MOCK_BIN/node" "$ENV_ROOT" \
  > "$TEST_ROOT/repository-env.out" 2>&1
repository_env_status=$?
set -e
if [[ $repository_env_status -eq 0 ]]; then
  echo "The API runner accepted a repository-local .env symlink." >&2
  exit 1
fi
grep -q 'Repository runtime environment files are not allowed for launchd' "$TEST_ROOT/repository-env.out"
grep -q 'apps/portfolio/backend/.env' "$TEST_ROOT/repository-env.out"

# A legacy plist without an explicit release identity must fail closed instead
# of restarting against a mutable repository checkout.
rm "$STATE_ROOT/apps/portfolio/backend/.env"
set +e
PORTFOLIO_OPS_LOCAL_DATABASE_URL="postgresql+psycopg://explicit/local" \
PORTFOLIO_OPS_LOCAL_STATE_ROOT="$STATE_ROOT" \
  "$REPOSITORY_ROOT/infra/launchd/run_local_service.sh" \
  platform-api "$RUNTIME_ROOT" "$MOCK_BIN/python" "$MOCK_BIN/node" "$ENV_ROOT" \
  > "$TEST_ROOT/missing-release-id.out" 2>&1
missing_release_status=$?
set -e
if [[ $missing_release_status -eq 0 ]]; then
  echo "The API runner accepted a launch without a pinned release id." >&2
  exit 1
fi
grep -q 'PORTFOLIO_OPS_LOCAL_RELEASE_ID is required' "$TEST_ROOT/missing-release-id.out"

echo "launchd safe runtime environment loader test passed."
