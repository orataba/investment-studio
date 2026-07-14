#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-runtime-snapshot-test.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

PYTHON_BIN="${PYTHON_BIN:-$REPOSITORY_ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi
RUNTIME_ROOT="$TEST_ROOT/release/runtime"
RELEASE_ID=release-snapshot-test

"$PYTHON_BIN" "$REPOSITORY_ROOT/infra/launchd/stage_local_runtime.py" stage \
  --project-root "$REPOSITORY_ROOT" \
  --destination "$RUNTIME_ROOT" \
  --release-id "$RELEASE_ID"

"$PYTHON_BIN" "$RUNTIME_ROOT/infra/launchd/stage_local_runtime.py" verify \
  --runtime-root "$RUNTIME_ROOT" \
  --release-id "$RELEASE_ID"

for required_path in \
  apps/platform/backend/platform_app/main.py \
  apps/watchlist/backend/watchlist_app/main.py \
  apps/portfolio/backend/portfolio_app/main.py \
  apps/portfolio/backend/alembic/versions \
  infra/instrument_registry/alembic/versions \
  infra/scripts/release_database.sh \
  infra/scripts/audit_live_data.py \
  infra/scripts/wait_for_refresh_convergence.py \
  infra/launchd/run_local_service.sh \
  infra/launchd/run_market_data_refresh.sh \
  deploy/serve_spa_proxy.mjs \
  runtime-manifest.json; do
  if [[ ! -e "$RUNTIME_ROOT/$required_path" ]]; then
    echo "Runtime snapshot is missing: $required_path" >&2
    exit 1
  fi
done

if find "$RUNTIME_ROOT" \
  \( -type d \( \
      -name .venv -o \
      -name tests -o \
      -name __pycache__ -o \
      -name .pytest_cache -o \
      -name .ruff_cache -o \
      -name build -o \
      -name '*.egg-info' \
    \) -o -type f \( \
      -name '.env*' -o \
      -name '*.pyc' -o \
      -name '*.pyo' \
    \) -o -type l \) \
  -print -quit | grep -q .; then
  echo "Runtime snapshot contains a cache, test, environment file, or symlink." >&2
  exit 1
fi

# Any code mutation after staging must make the next launch fail closed.
printf '\n# tampered after staging\n' \
  >> "$RUNTIME_ROOT/infra/scripts/wait_for_refresh_convergence.py"
set +e
"$PYTHON_BIN" "$RUNTIME_ROOT/infra/launchd/stage_local_runtime.py" verify \
  --runtime-root "$RUNTIME_ROOT" \
  --release-id "$RELEASE_ID" \
  > "$TEST_ROOT/tamper.out" 2>&1
tamper_status=$?
set -e
if [[ $tamper_status -eq 0 ]]; then
  echo "Runtime verifier accepted a modified refresh convergence waiter." >&2
  exit 1
fi
grep -q 'does not match its recorded file manifest' "$TEST_ROOT/tamper.out"

echo "launchd runtime snapshot test passed."
