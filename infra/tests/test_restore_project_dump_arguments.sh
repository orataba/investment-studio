#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RESTORE_SCRIPT="$REPOSITORY_ROOT/infra/postgres/restore_project_dump.sh"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-restore-arguments.XXXXXX")"
DATABASE_URL="postgresql+psycopg://portfolio_ops@127.0.0.1:5432/portfolio_ops"
DATABASE_URL="${DATABASE_URL/portfolio_ops@/portfolio_ops:sensitive-password@}"
trap 'rm -rf "$TEST_ROOT"' EXIT

set +e
"$RESTORE_SCRIPT" >"$TEST_ROOT/missing.out" 2>&1
missing_status=$?
set -e
if [[ $missing_status -eq 0 ]]; then
  echo "Restore accepted a missing dump path." >&2
  exit 1
fi
grep -q '^Usage:' "$TEST_ROOT/missing.out"

set +e
(cd "$TEST_ROOT" && "$RESTORE_SCRIPT" incoming.pgdump) \
  >"$TEST_ROOT/relative.out" 2>&1
relative_status=$?
set -e
if [[ $relative_status -eq 0 ]]; then
  echo "Restore accepted a relative dump path." >&2
  exit 1
fi
grep -q 'Restore dump path must be absolute' "$TEST_ROOT/relative.out"

NO_TARGET_DUMP="$TEST_ROOT/no-target.pgdump"
: > "$NO_TARGET_DUMP"
set +e
CONFIRM_RESTORE=portfolio_ops \
  env -u PORTFOLIO_OPS_LOCAL_DATABASE_URL \
  "$RESTORE_SCRIPT" "$NO_TARGET_DUMP" \
  > "$TEST_ROOT/no-target.out" 2>&1
no_target_status=$?
set -e
if [[ $no_target_status -ne 64 ]]; then
  echo "Restore did not reject a missing explicit database URL." >&2
  exit 1
fi
grep -q 'PORTFOLIO_OPS_LOCAL_DATABASE_URL must explicitly identify' \
  "$TEST_ROOT/no-target.out"

UNVERIFIED_DUMP="$TEST_ROOT/unverified.pgdump"
: > "$UNVERIFIED_DUMP"
set +e
CONFIRM_RESTORE=portfolio_ops \
ALLOW_UNVERIFIED_RESTORE=true \
PORTFOLIO_OPS_LOCAL_DATABASE_URL="$DATABASE_URL" \
  "$RESTORE_SCRIPT" "$UNVERIFIED_DUMP" \
  > "$TEST_ROOT/unverified.out" 2>&1
unverified_status=$?
set -e
if [[ $unverified_status -eq 0 ]]; then
  echo "Restore accepted an incoming dump without a checksum." >&2
  exit 1
fi
grep -q 'Checksum file not found' "$TEST_ROOT/unverified.out"
if grep -q 'restoring without a checksum' "$TEST_ROOT/unverified.out"; then
  echo "Restore retained the deleted unverified-dump bypass." >&2
  exit 1
fi

if grep -q 'ALLOW_ACTIVE_CONNECTIONS\|ALLOW_UNVERIFIED_RESTORE' "$RESTORE_SCRIPT"; then
  echo "Restore retained a deleted destructive bypass." >&2
  exit 1
fi

ACTIVE_DUMP="$TEST_ROOT/active-connections.pgdump"
printf '%s\n' archive > "$ACTIVE_DUMP"
(cd "$TEST_ROOT" && shasum -a 256 "$(basename "$ACTIVE_DUMP")" > active-connections.sha256)
MOCK_BIN="$TEST_ROOT/bin"
EVENT_LOG="$TEST_ROOT/events"
mkdir -p "$MOCK_BIN"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'if [[ "$*" == *sensitive-password* ]]; then printf "credential-in-argv\n" >> "$EVENT_LOG"; fi' \
  'printf "psql\n" >> "$EVENT_LOG"' \
  'if [[ "$*" == *"current_user"* ]]; then' \
  '  printf "%s\n" "portfolio_ops|portfolio_ops"' \
  'elif [[ "$*" == *"SELECT count("* ]]; then' \
  '  printf "%s\n" 1' \
  'fi' \
  > "$MOCK_BIN/psql"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'if [[ "$1" == "--list" ]]; then' \
  '  printf "%s\n" "1; 0 0 SCHEMA - instrument_registry owner" "2; 0 0 SCHEMA - portfolio owner" "3; 0 0 SCHEMA - watchlist owner"' \
  '  exit 0' \
  'fi' \
  'exit 99' \
  > "$MOCK_BIN/pg_restore"
chmod +x "$MOCK_BIN/psql" "$MOCK_BIN/pg_restore"
export EVENT_LOG

set +e
PATH="$MOCK_BIN:$PATH" \
CONFIRM_RESTORE=portfolio_ops \
ALLOW_ACTIVE_CONNECTIONS=true \
PYTHON_BIN="$(command -v python3)" \
PORTFOLIO_OPS_LOCAL_DATABASE_URL="$DATABASE_URL" \
PORTFOLIO_OPS_RESTORE_SERVICE_MANAGER=none \
  "$RESTORE_SCRIPT" "$ACTIVE_DUMP" \
  > "$TEST_ROOT/active-connections.out" 2>&1
active_connections_status=$?
set -e
if [[ $active_connections_status -eq 0 ]]; then
  echo "Restore accepted remaining active connections." >&2
  exit 1
fi
grep -q 'Refusing restore while 1 other database connection(s) remain active' \
  "$TEST_ROOT/active-connections.out"
if [[ "$(grep -c '^psql$' "$EVENT_LOG")" -lt 3 ]]; then
  echo "Active-connection test did not reach the post-termination verification." >&2
  exit 1
fi
if grep -q '^credential-in-argv$' "$EVENT_LOG"; then
  echo "Restore exposed database credentials in a PostgreSQL command argument." >&2
  exit 1
fi

echo "restore dump argument gate test passed."
