#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INSTALLER="$REPOSITORY_ROOT/infra/launchd/install_local_services.sh"

python3 - "$INSTALLER" <<'PY'
from pathlib import Path
import sys


source = Path(sys.argv[1]).read_text(encoding="utf-8")
ordered_fragments = (
    'stage_local_runtime.py" stage',
    'runtime_release_dir="$web_release_dir/runtime"',
    'stage_local_runtime.py" verify',
    'source "$runtime_release_dir/infra/service_inventory.sh"',
    'services_stopped=true',
    'PROJECT_ROOT="$runtime_release_dir" PYTHON_BIN="$PYTHON_BIN"',
    '"$runtime_release_dir/infra/scripts/release_database.sh"',
    '"$runtime_release_dir/infra/launchd/generate_local_service_plists.py"',
    '--runtime-root "$runtime_release_dir"',
    'for service in "${backend_services[@]}"',
    'for service in "${web_services[@]}"',
    'services_stopped=false',
)
position = -1
for fragment in ordered_fragments:
    next_position = source.find(fragment, position + 1)
    assert next_position >= 0, f"installer is missing release phase: {fragment}"
    assert next_position > position, f"installer release phase is out of order: {fragment}"
    position = next_position

assert 'PROJECT_ROOT="$PROJECT_ROOT" PYTHON_BIN="$PYTHON_BIN"' not in source
assert '\n"$PYTHON_BIN" "$SCRIPT_DIR/generate_local_service_plists.py"' not in source
assert 'PYTHONDONTWRITEBYTECODE=1' in source
assert 'NODE_VERSION_FILE="$PROJECT_ROOT/.node-version"' in source
assert 'actual_node_version="$("$NODE_BIN" --version)"' in source
assert 'export PATH="$NODE_BIN_DIR:$PATH"' in source

failure_handler_start = source.index("leave_services_stopped_on_failure()")
failure_handler_end = source.index("trap leave_services_stopped_on_failure EXIT")
failure_handler = source[failure_handler_start:failure_handler_end]
assert '"$services_stopped" == "true"' in failure_handler
assert 'launchctl bootout' in failure_handler
assert "control_local_services.sh\" start" not in failure_handler
assert "rm -rf \"$web_release_dir\"" not in failure_handler

stop_position = source.index('"$SCRIPT_DIR/control_local_services.sh" stop')
stage_position = source.index('stage_local_runtime.py" stage')
assert stage_position < stop_position

release_position = source.index('"$runtime_release_dir/infra/scripts/release_database.sh"')
generator_position = source.index(
    '"$runtime_release_dir/infra/launchd/generate_local_service_plists.py"'
)
assert release_position < generator_position
PY

echo "launchd immutable release flow test passed."
