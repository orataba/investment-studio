#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
PYTHON_BIN="${PYTHON_BIN:-$PROJECT_ROOT/.venv/bin/python}"
JUNIT_DIR="${JUNIT_DIR:-${RUNNER_TEMP:-$PROJECT_ROOT/var/test-results}}"

usage() {
  cat <<'EOF'
Usage: verify_repository.sh COMMAND [APP]

Commands:
  static
  backend [all|platform|portfolio|watchlist]
  frontend [all|platform|portfolio|watchlist]
  infra [all|portable|postgresql]
  migration-heads
  postgres-integration [all|platform|portfolio|watchlist]
  all-local

Dependency installation is intentionally separate. Run
infra/scripts/sync_python_env.sh and npm ci before the relevant command.
EOF
}

require_python() {
  if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "PYTHON_BIN is not executable: $PYTHON_BIN" >&2
    exit 1
  fi
}

require_node() {
  if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    echo "node and npm are required for frontend verification." >&2
    exit 1
  fi
}

selected_apps() {
  local selection="${1:-all}"
  case "$selection" in
    all)
      printf '%s\n' platform portfolio watchlist
      ;;
    platform|portfolio|watchlist)
      printf '%s\n' "$selection"
      ;;
    *)
      echo "Unknown app selection: $selection" >&2
      exit 64
      ;;
  esac
}

run_static() {
  require_python
  git -C "$PROJECT_ROOT" diff --check HEAD
  "$PYTHON_BIN" "$PROJECT_ROOT/infra/scripts/check_markdown_links.py" "$PROJECT_ROOT"

  local forbidden
  forbidden="$(
    git -C "$PROJECT_ROOT" ls-files |
      grep -E '(^|/)(\.env|__pycache__|\.pytest_cache|node_modules|dist)(/|$)|\.(db|sqlite|sqlite3|pyc)$' ||
      true
  )"
  if [[ -n "$forbidden" ]]; then
    echo "Tracked runtime or secret-shaped artifacts are forbidden:" >&2
    printf '%s\n' "$forbidden" >&2
    exit 1
  fi
  echo "Static repository checks passed."
}

run_backend() {
  require_python
  local selected app_name
  selected="$(selected_apps "${1:-all}")"
  while IFS= read -r app_name; do
    echo "Running $app_name backend tests without PostgreSQL integration cases."
    (
      cd "$PROJECT_ROOT/apps/$app_name/backend"
      PYTHONPATH="$PROJECT_ROOT/apps/$app_name/backend:$PROJECT_ROOT/packages/instrument-core/python${PYTHONPATH:+:$PYTHONPATH}" \
        "$PYTHON_BIN" -m pytest -m "not postgresql_integration" --strict-markers -ra
    )
  done <<< "$selected"
}

run_frontend() {
  local selected app_name
  selected="$(selected_apps "${1:-all}")"
  require_node
  while IFS= read -r app_name; do
    local frontend_root="$PROJECT_ROOT/apps/$app_name/frontend"
    if [[ ! -d "$frontend_root/node_modules" ]]; then
      echo "Missing node_modules for $app_name; run npm --prefix $frontend_root ci first." >&2
      exit 1
    fi
    echo "Running $app_name frontend tests and production build."
    npm --prefix "$frontend_root" test
    npm --prefix "$frontend_root" run build
  done <<< "$selected"
}

run_infra() {
  require_python
  local selection="${1:-all}"
  local test_path
  case "$selection" in
    all|portable|postgresql)
      ;;
    *)
      echo "Unknown infra selection: $selection" >&2
      exit 64
      ;;
  esac

  if [[ "$selection" != "postgresql" ]] \
    && find "$PROJECT_ROOT/infra/tests" -maxdepth 1 -type f -name 'test_*.py' \
      | grep -q .; then
    echo "Running Python infrastructure tests."
    "$PYTHON_BIN" -m pytest "$PROJECT_ROOT/infra/tests" -ra
  fi
  while IFS= read -r test_path; do
    if [[ "$selection" == "portable" && "$(basename "$test_path")" == test_postgresql_* ]]; then
      continue
    fi
    if [[ "$selection" == "postgresql" && "$(basename "$test_path")" != test_postgresql_* ]]; then
      continue
    fi
    echo "Running ${test_path#"$PROJECT_ROOT/"}."
    PYTHON_BIN="$PYTHON_BIN" "$test_path"
  done < <(find "$PROJECT_ROOT/infra/tests" -maxdepth 1 -type f -name 'test_*.sh' | sort)
}

run_migration_heads() {
  require_python
  "$PROJECT_ROOT/infra/scripts/migrate_all.sh"

  echo "Verifying every Alembic chain is at all heads."
  (
    cd "$PROJECT_ROOT/infra/instrument_registry"
    PYTHONPATH="$PROJECT_ROOT/packages/instrument-core/python${PYTHONPATH:+:$PYTHONPATH}" \
      "$PYTHON_BIN" -m alembic current --check-heads
  )
  (
    cd "$PROJECT_ROOT/apps/platform/backend"
    PYTHONPATH="$PROJECT_ROOT/apps/platform/backend:$PROJECT_ROOT/packages/instrument-core/python${PYTHONPATH:+:$PYTHONPATH}" \
      "$PYTHON_BIN" -m alembic current --check-heads
  )
  (
    cd "$PROJECT_ROOT/apps/portfolio/backend"
    PYTHONPATH="$PROJECT_ROOT/apps/portfolio/backend:$PROJECT_ROOT/packages/instrument-core/python${PYTHONPATH:+:$PYTHONPATH}" \
      "$PYTHON_BIN" -m alembic current --check-heads
  )
  (
    cd "$PROJECT_ROOT/apps/watchlist/backend"
    PYTHONPATH="$PROJECT_ROOT/apps/watchlist/backend:$PROJECT_ROOT/packages/instrument-core/python${PYTHONPATH:+:$PYTHONPATH}" \
      "$PYTHON_BIN" -m alembic current --check-heads
  )
}

run_postgres_integration() {
  require_python
  local selected app_name report
  selected="$(selected_apps "${1:-all}")"
  if [[ -z "${PORTFOLIO_OPS_TEST_POSTGRES_URL:-}" ]]; then
    echo "Set PORTFOLIO_OPS_TEST_POSTGRES_URL explicitly for PostgreSQL integration tests." >&2
    exit 64
  fi
  mkdir -p "$JUNIT_DIR"

  local reports=()
  while IFS= read -r app_name; do
    report="$JUNIT_DIR/$app_name-postgres.xml"
    rm -f "$report"
    (
      cd "$PROJECT_ROOT/apps/$app_name/backend"
      PYTHONPATH="$PROJECT_ROOT/apps/$app_name/backend:$PROJECT_ROOT/packages/instrument-core/python${PYTHONPATH:+:$PYTHONPATH}" \
        "$PYTHON_BIN" -m pytest -m postgresql_integration \
          --strict-markers -ra --junitxml="$report"
    )
    reports+=("$report")
  done <<< "$selected"

  "$PYTHON_BIN" "$PROJECT_ROOT/infra/scripts/assert_junit_no_skips.py" \
    "${reports[@]}"
}

run_all_local() {
  run_static
  run_backend all
  run_frontend all
  run_infra
}

command_name="${1:-}"
case "$command_name" in
  static)
    run_static
    ;;
  backend)
    run_backend "${2:-all}"
    ;;
  frontend)
    run_frontend "${2:-all}"
    ;;
  infra)
    run_infra "${2:-all}"
    ;;
  migration-heads)
    run_migration_heads
    ;;
  postgres-integration)
    run_postgres_integration "${2:-all}"
    ;;
  all-local)
    run_all_local
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 64
    ;;
esac
