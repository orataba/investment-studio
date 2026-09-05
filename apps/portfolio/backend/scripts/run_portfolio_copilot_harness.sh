#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <portfolio-id> <batch-id>" >&2
  exit 64
fi

copilot_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
copilot_project_root="$(cd "$copilot_script_dir/../../../.." && pwd -P)"
copilot_env_root="${INVESTMENT_STUDIO_SECRET_ROOT:-$HOME/.config/orataba/secrets/investment-studio}"
copilot_env_file="${INVESTMENT_STUDIO_PORTFOLIO_COPILOT_ENV_FILE:-$copilot_env_root/portfolio-copilot.env}"
copilot_patch="$copilot_project_root/apps/portfolio/backend/config/portfolio_copilot_deepseek_harness.patch.yml"
copilot_pnpm="${INVESTMENT_STUDIO_PORTFOLIO_COPILOT_PNPM:-$(command -v pnpm || true)}"
copilot_dsh_home="${INVESTMENT_STUDIO_PORTFOLIO_COPILOT_DSH_HOME:-$HOME/.local/share/investment-studio/deepseek-harness}"
copilot_portfolio_id="$1"
copilot_batch_id="$2"

if [[ -z "$copilot_pnpm" || ! -x "$copilot_pnpm" ]]; then
  echo "pnpm is required to run the pinned DeepSeek Harness runtime." >&2
  exit 1
fi

# launchd intentionally starts services with a minimal PATH.  Once the
# operator supplies the exact pnpm executable, make its sibling Node binary
# available to pnpm's /usr/bin/env shebang without broadening the agent tools.
export PATH="$(dirname "$copilot_pnpm"):$PATH"

source "$copilot_project_root/infra/launchd/load_runtime_env.sh"
investment_studio_reject_repository_env_files "$copilot_project_root"
investment_studio_load_env_file \
  "$copilot_env_file" \
  DEEPSEEK_ \
  INVESTMENT_STUDIO_PORTFOLIO_COPILOT_
: "${DEEPSEEK_API_KEY:?portfolio-copilot.env must set DEEPSEEK_API_KEY}"

mkdir -p "$copilot_dsh_home"
chmod 700 "$copilot_dsh_home"

export DSH_HOME="$copilot_dsh_home"
export DSH_PERMISSION_MODE=read-only
export DSH_TOOLS_MODE=native
export DSH_TELEMETRY_DISABLED=1
export INVESTMENT_STUDIO_PORTFOLIO_COPILOT_PORTFOLIO_ID="$copilot_portfolio_id"
export INVESTMENT_STUDIO_PORTFOLIO_COPILOT_BATCH_ID="$copilot_batch_id"
export INVESTMENT_STUDIO_PORTFOLIO_COPILOT_MODEL_NAME="deepseek-v4-flash-vision-exp"

copilot_task="Analyze the portfolio screenshot batch bound to this restricted MCP process. Follow the review workflow and submit one analysis revision."

# The backend process may hold database and market-data credentials that the
# external harness runtime does not need. Start it with an explicit allowlist.
copilot_exec_env=(
  "HOME=$HOME"
  "PATH=$PATH"
  "TMPDIR=${TMPDIR:-/tmp}"
  "LANG=${LANG:-C.UTF-8}"
  "DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY"
  "DSH_HOME=$DSH_HOME"
  "DSH_PERMISSION_MODE=$DSH_PERMISSION_MODE"
  "DSH_TOOLS_MODE=$DSH_TOOLS_MODE"
  "DSH_TELEMETRY_DISABLED=$DSH_TELEMETRY_DISABLED"
  "INVESTMENT_STUDIO_PORTFOLIO_COPILOT_PROJECT_ROOT=$copilot_project_root"
  "INVESTMENT_STUDIO_PORTFOLIO_COPILOT_API_BASE_URL=${INVESTMENT_STUDIO_PORTFOLIO_COPILOT_API_BASE_URL:-http://127.0.0.1:8001/api}"
  "INVESTMENT_STUDIO_PORTFOLIO_COPILOT_PORTFOLIO_ID=$INVESTMENT_STUDIO_PORTFOLIO_COPILOT_PORTFOLIO_ID"
  "INVESTMENT_STUDIO_PORTFOLIO_COPILOT_BATCH_ID=$INVESTMENT_STUDIO_PORTFOLIO_COPILOT_BATCH_ID"
  "INVESTMENT_STUDIO_PORTFOLIO_COPILOT_MODEL_NAME=$INVESTMENT_STUDIO_PORTFOLIO_COPILOT_MODEL_NAME"
)
for copilot_optional_env in \
  DEEPSEEK_BASE_URL \
  HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY \
  http_proxy https_proxy all_proxy no_proxy \
  NPM_CONFIG_REGISTRY PNPM_HOME \
  XDG_CACHE_HOME XDG_CONFIG_HOME XDG_DATA_HOME \
  SSL_CERT_FILE SSL_CERT_DIR NODE_EXTRA_CA_CERTS
do
  if [[ -n "${!copilot_optional_env:-}" ]]; then
    copilot_exec_env+=("$copilot_optional_env=${!copilot_optional_env}")
  fi
done

exec /usr/bin/env -i "${copilot_exec_env[@]}" \
  "$copilot_pnpm" dlx @deepseek-ai/dsh@0.1.1-rc.2 \
  --profile headless \
  --patch "$copilot_patch" \
  "$copilot_task"
