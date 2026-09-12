#!/usr/bin/env bash
set -euo pipefail

harness_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$harness_root"
# Provision as the runtime user during release preparation, before switching
# services. --offline can reuse a previously populated official-registry store.
pnpm install --frozen-lockfile "$@"
exec "$harness_root/run.sh" --version
