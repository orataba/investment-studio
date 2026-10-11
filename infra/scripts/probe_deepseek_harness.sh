#!/usr/bin/env bash
# Explicit, small live probe. Never called by the ordinary test suite or installer.
set -euo pipefail
probe_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
probe_secret_root="${INVESTMENT_STUDIO_SECRET_ROOT:-$HOME/.config/orataba/secrets/investment-studio}"
probe_env_file="${INVESTMENT_STUDIO_PORTFOLIO_COPILOT_ENV_FILE:-$probe_secret_root/portfolio-copilot.env}"
source "$probe_root/infra/launchd/load_runtime_env.sh"
investment_studio_reject_repository_env_files "$probe_root"
investment_studio_load_env_file "$probe_env_file" DEEPSEEK_ INVESTMENT_STUDIO_PORTFOLIO_COPILOT_
exec "$probe_root/.venv/bin/python" "$probe_root/infra/scripts/probe_deepseek_harness.py"
