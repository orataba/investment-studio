#!/usr/bin/env bash
set -euo pipefail

harness_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
harness_entry="$harness_root/node_modules/@deepseek-ai/dsh/lib/bin.js"
harness_node="$(command -v node || true)"
if [[ -z "$harness_node" || ! -r "$harness_entry" ]]; then
  echo "Research runtime is not installed. Run infra/harness/install.sh as the service user before starting research." >&2
  exit 78
fi
# Request execution never resolves or downloads packages. All callers use the
# dependency tree installed for this release and the same restricted environment.
if [[ -n "${INVESTMENT_STUDIO_MODEL_USAGE_PATCH:-}" ]]; then
  exec "$harness_node" "$harness_entry" --patch "$INVESTMENT_STUDIO_MODEL_USAGE_PATCH" "$@"
fi
exec "$harness_node" "$harness_entry" "$@"
