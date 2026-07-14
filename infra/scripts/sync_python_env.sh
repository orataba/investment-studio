#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
VENV_ROOT="${VENV_ROOT:-$PROJECT_ROOT/.venv}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
UV_BIN="${UV_BIN:-$(command -v uv || true)}"

if [[ -z "$UV_BIN" || ! -x "$UV_BIN" ]]; then
  echo "uv is required to reproduce the locked Python environment." >&2
  exit 1
fi
if [[ ! -f "$PROJECT_ROOT/requirements/python.lock" ]]; then
  echo "Missing Python lock file: $PROJECT_ROOT/requirements/python.lock" >&2
  exit 1
fi

if [[ ! -x "$VENV_ROOT/bin/python" ]]; then
  "$UV_BIN" venv --python "$PYTHON_VERSION" "$VENV_ROOT"
fi

"$UV_BIN" pip sync \
  --python "$VENV_ROOT/bin/python" \
  "$PROJECT_ROOT/requirements/python.lock"

for package_root in \
  "$PROJECT_ROOT/packages/instrument-core/python" \
  "$PROJECT_ROOT/apps/platform/backend" \
  "$PROJECT_ROOT/apps/watchlist/backend" \
  "$PROJECT_ROOT/apps/portfolio/backend"; do
  "$UV_BIN" pip install --python "$VENV_ROOT/bin/python" --no-deps --editable "$package_root"
done

echo "Locked Python environment is ready at $VENV_ROOT."
