#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
VENV_ROOT="${VENV_ROOT:-$PROJECT_ROOT/.venv}"
PYTHON_VERSION_FILE="$PROJECT_ROOT/.python-version"
UV_VERSION_FILE="$PROJECT_ROOT/uv.toml"
UV_BIN="${UV_BIN:-$(command -v uv || true)}"

if [[ ! -f "$PYTHON_VERSION_FILE" || ! -f "$UV_VERSION_FILE" ]]; then
  echo "Missing .python-version or uv.toml at repository root." >&2
  exit 1
fi
PYTHON_VERSION="$(tr -d '[:space:]' < "$PYTHON_VERSION_FILE")"

if [[ -z "$UV_BIN" || ! -x "$UV_BIN" ]]; then
  echo "uv is required to reproduce the locked Python environment." >&2
  exit 1
fi
expected_uv_version="$(sed -nE 's/^required-version[[:space:]]*=[[:space:]]*"==([^\"]+)"[[:space:]]*$/\1/p' "$UV_VERSION_FILE")"
if [[ -z "$expected_uv_version" ]]; then
  echo "uv.toml must contain one exact required-version." >&2
  exit 1
fi
actual_uv_version="$("$UV_BIN" --version | awk '{print $2}')"
if [[ "$actual_uv_version" != "$expected_uv_version" ]]; then
  echo "uv $expected_uv_version is required; found $actual_uv_version." >&2
  exit 1
fi
if [[ ! -f "$PROJECT_ROOT/requirements/python.lock" ]]; then
  echo "Missing Python lock file: $PROJECT_ROOT/requirements/python.lock" >&2
  exit 1
fi

if [[ ! -x "$VENV_ROOT/bin/python" ]]; then
  "$UV_BIN" venv --python "$PYTHON_VERSION" "$VENV_ROOT"
fi
actual_python="$($VENV_ROOT/bin/python -c 'import platform; print(f"{platform.python_implementation()} {platform.python_version()}")')"
if [[ "$actual_python" != "CPython $PYTHON_VERSION" ]]; then
  echo "CPython $PYTHON_VERSION is required; existing environment uses $actual_python." >&2
  echo "Remove $VENV_ROOT and rerun this script to rebuild it." >&2
  exit 1
fi

"$UV_BIN" pip sync \
  --python "$VENV_ROOT/bin/python" \
  --require-hashes \
  "$PROJECT_ROOT/requirements/python.lock"

for package_root in \
  "$PROJECT_ROOT/packages/instrument-core/python" \
  "$PROJECT_ROOT/packages/calculation-core/python" \
  "$PROJECT_ROOT/apps/platform/backend" \
  "$PROJECT_ROOT/apps/watchlist/backend" \
  "$PROJECT_ROOT/apps/portfolio/backend"; do
  "$UV_BIN" pip install \
    --python "$VENV_ROOT/bin/python" \
    --no-build-isolation \
    --no-deps \
    --editable "$package_root"
done

echo "Locked Python environment is ready at $VENV_ROOT."
