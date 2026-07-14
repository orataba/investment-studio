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
  backend-fast [all|platform|portfolio|watchlist]
  frontend [all|platform|portfolio|watchlist]
  infra-portable
  migration-heads
  postgres-integration [all|platform|portfolio|watchlist]
  database-lifecycle

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
  local version_file="$PROJECT_ROOT/.node-version"
  if [[ ! -f "$version_file" ]]; then
    echo "Missing Node.js version file: $version_file" >&2
    exit 1
  fi
  if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    echo "node and npm are required for frontend verification." >&2
    exit 1
  fi
  local expected actual
  expected="$(tr -d '[:space:]' < "$version_file")"
  actual="$(node --version)"
  actual="${actual#v}"
  if [[ "$actual" != "$expected" ]]; then
    echo "Node.js $expected is required; found $actual." >&2
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

run_backend_fast() {
  require_python
  local selected app
  selected="$(selected_apps "${1:-all}")"
  local calculation_core_python="$PROJECT_ROOT/packages/calculation-core/python"
  if [[ "${1:-all}" == "all" || "${1:-all}" == "portfolio" ]]; then
    if [[ -z "${PORTFOLIO_OPS_TEST_POSTGRES_URL:-}" ]]; then
      echo "Set PORTFOLIO_OPS_TEST_POSTGRES_URL explicitly for Portfolio backend tests." >&2
      exit 64
    fi
  fi
  local apps=()
  while IFS= read -r app; do
    apps+=("$app")
  done <<< "$selected"
  if [[ "${1:-all}" == "all" || "${1:-all}" == "portfolio" ]]; then
    echo "Running calculation-core contract tests."
    (
      cd "$calculation_core_python"
      PYTHONPATH="$calculation_core_python${PYTHONPATH:+:$PYTHONPATH}" \
        "$PYTHON_BIN" -m pytest -ra
    )
  fi
  for app in "${apps[@]}"; do
    echo "Running $app backend tests without PostgreSQL integration cases."
    (
      cd "$PROJECT_ROOT/apps/$app/backend"
      PYTHONPATH="$PROJECT_ROOT/apps/$app/backend:$PROJECT_ROOT/packages/instrument-core/python:$calculation_core_python${PYTHONPATH:+:$PYTHONPATH}" \
        "$PYTHON_BIN" -m pytest -m "not postgresql_integration" --strict-markers -ra
    )
  done
}

run_frontend() {
  local selected app
  selected="$(selected_apps "${1:-all}")"
  require_node
  local apps=()
  while IFS= read -r app; do
    apps+=("$app")
  done <<< "$selected"
  for app in "${apps[@]}"; do
    local frontend_root="$PROJECT_ROOT/apps/$app/frontend"
    if [[ ! -d "$frontend_root/node_modules" ]]; then
      echo "Missing node_modules for $app; run npm --prefix $frontend_root ci first." >&2
      exit 1
    fi
    echo "Running $app frontend tests and production build."
    npm --prefix "$frontend_root" test
    npm --prefix "$frontend_root" run build
  done
}

run_infra_portable() {
  require_python
  local tests=(
    infra/tests/test_assert_junit_no_skips.sh
    infra/tests/test_bootstrap_local_database_permissions.sh
    infra/tests/test_launchd_control_local_services.sh
    infra/tests/test_launchd_market_data_refresh_runner.sh
    infra/tests/test_launchd_portfolio_worker_runner.sh
    infra/tests/test_launchd_plist_generation.sh
    infra/tests/test_launchd_runtime_env_loader.sh
    infra/tests/test_migrate_all_env_precedence.sh
    infra/tests/test_runtime_readiness_lifecycle.sh
    infra/tests/test_systemd_app_services.sh
    infra/tests/test_systemd_market_data_refresh_timer.sh
    infra/tests/test_verify_repository_selection.sh
  )
  local test_path
  for test_path in "${tests[@]}"; do
    echo "Running $test_path."
    PYTHON_BIN="$PYTHON_BIN" "$PROJECT_ROOT/$test_path"
  done
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
    cd "$PROJECT_ROOT/infra/calculation_registry"
    PYTHONPATH="$PROJECT_ROOT/packages/calculation-core/python${PYTHONPATH:+:$PYTHONPATH}" \
      "$PYTHON_BIN" -m alembic current --check-heads
  )
  (
    cd "$PROJECT_ROOT/apps/portfolio/backend"
    PYTHONPATH="$PROJECT_ROOT/apps/portfolio/backend:$PROJECT_ROOT/packages/instrument-core/python:$PROJECT_ROOT/packages/calculation-core/python${PYTHONPATH:+:$PYTHONPATH}" \
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
  local selected app report
  selected="$(selected_apps "${1:-all}")"
  local apps=()
  while IFS= read -r app; do
    apps+=("$app")
  done <<< "$selected"
  if [[ -z "${PORTFOLIO_OPS_TEST_POSTGRES_URL:-}" ]]; then
    echo "Set PORTFOLIO_OPS_TEST_POSTGRES_URL explicitly for PostgreSQL integration tests." >&2
    exit 64
  fi
  mkdir -p "$JUNIT_DIR"

  local reports=()
  if [[ "${1:-all}" == "all" || "${1:-all}" == "portfolio" ]]; then
    report="$JUNIT_DIR/calculation-registry-postgres.xml"
    rm -f "$report"
    (
      cd "$PROJECT_ROOT/infra/calculation_registry"
      PYTHONPATH="$PROJECT_ROOT/packages/calculation-core/python${PYTHONPATH:+:$PYTHONPATH}" \
        "$PYTHON_BIN" -m pytest -m postgresql_integration \
          --strict-markers -ra --junitxml="$report"
    )
    reports+=("$report")
  fi
  for app in "${apps[@]}"; do
    report="$JUNIT_DIR/$app-postgres.xml"
    rm -f "$report"
    (
      cd "$PROJECT_ROOT/apps/$app/backend"
      PYTHONPATH="$PROJECT_ROOT/apps/$app/backend:$PROJECT_ROOT/packages/instrument-core/python:$PROJECT_ROOT/packages/calculation-core/python${PYTHONPATH:+:$PYTHONPATH}" \
        "$PYTHON_BIN" -m pytest -m postgresql_integration \
          --strict-markers -ra --junitxml="$report"
    )
    reports+=("$report")
  done

  "$PYTHON_BIN" "$PROJECT_ROOT/infra/scripts/assert_junit_no_skips.py" \
    "${reports[@]}"
}

run_database_lifecycle() {
  require_python
  local test_path
  for test_path in \
    infra/tests/test_release_database_rollback.sh \
    infra/tests/test_restore_project_dump_rollback.sh; do
    echo "Running $test_path."
    PYTHON_BIN="$PYTHON_BIN" "$PROJECT_ROOT/$test_path"
  done
}

command_name="${1:-}"
case "$command_name" in
  backend-fast)
    run_backend_fast "${2:-all}"
    ;;
  frontend)
    run_frontend "${2:-all}"
    ;;
  infra-portable)
    run_infra_portable
    ;;
  migration-heads)
    run_migration_heads
    ;;
  postgres-integration)
    run_postgres_integration "${2:-all}"
    ;;
  database-lifecycle)
    run_database_lifecycle
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 64
    ;;
esac
