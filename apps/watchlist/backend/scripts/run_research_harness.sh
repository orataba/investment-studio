#!/usr/bin/env bash

set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 || ( $# -eq 2 && "$2" != "sector" && "$2" != "risk" ) ]]; then
  echo "Usage: $0 <run-id> [sector|risk]" >&2
  exit 64
fi

copilot_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
copilot_project_root="$(cd "$copilot_script_dir/../../../.." && pwd -P)"
copilot_env_root="${INVESTMENT_STUDIO_SECRET_ROOT:-$HOME/.config/orataba/secrets/investment-studio}"
copilot_env_file="${INVESTMENT_STUDIO_PORTFOLIO_COPILOT_ENV_FILE:-$copilot_env_root/portfolio-copilot.env}"
copilot_patch="$copilot_project_root/apps/watchlist/backend/config/research_harness.patch.yml"
copilot_pnpm="${INVESTMENT_STUDIO_PORTFOLIO_COPILOT_PNPM:-$(command -v pnpm || { [[ -x /opt/homebrew/bin/pnpm ]] && echo /opt/homebrew/bin/pnpm; } || true)}"
copilot_dsh_home="${INVESTMENT_STUDIO_PORTFOLIO_COPILOT_DSH_HOME:-$HOME/.local/share/investment-studio/deepseek-harness}"
research_run_id="$1"
if [[ "${2:-}" == "sector" ]]; then
  copilot_patch="$copilot_project_root/apps/watchlist/backend/config/sector_harness.patch.yml"
elif [[ "${2:-}" == "risk" ]]; then
  copilot_patch="$copilot_project_root/apps/watchlist/backend/config/risk_harness.patch.yml"
fi

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
export INVESTMENT_STUDIO_RESEARCH_RUN_ID="$research_run_id"
export INVESTMENT_STUDIO_PORTFOLIO_COPILOT_MODEL_NAME="deepseek-v4-flash-vision-exp"

copilot_research_persona="$(< "$copilot_project_root/apps/watchlist/backend/config/research_core.md")"
copilot_task="Read the shared research context and answer the current question in Chinese with evidence. Use the same instrument dossier and tools as research tracking. This conversation is private. Publish a durable research increment only when this user explicitly asks to save it to team research: record that instruction with authorize_team_research before submit_research_review. Never publish portfolio-derived discussion to team research. The application saves your answer and separately reports the common factual review/publication result."
if [[ "${2:-}" == "sector" ]]; then
  copilot_task="Read the shared context and bound dossiers for every requested instrument. Investigate meaningful new information and follow the instrument's current research agenda with suitable tools. Form or revise forward judgments only when warranted. Submit one review per requested instrument through submit_research_review, using change_kind none, knowledge or investment. A quiet check may have empty summary/events and research=null. Research fields are sparse deltas, not mandatory chapters. Correct real validation errors and briefly acknowledge draft acceptance."
elif [[ "${2:-}" == "risk" ]]; then
  copilot_task="Read read_research_context for the scope index, then read_risk_instrument for every instrument in that index. Analyze only these bound retained snapshots. For portfolio scope, also read every portfolio risk module and each derivative holding with read_portfolio_risk. Aggregate researcher risk reports, actual performance and comparisons, quantitative triggers, portfolio allocation/risk/correlation changes and FCN/Option settlement obligations; distinguish unavailable monitoring from safety. Submit the complete concise Chinese risk assessment with submit_risk_review, using shared case IDs and evidence references. Correct tool validation errors, then acknowledge acceptance without reprinting JSON. Do not search or trade."
fi

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
  "INVESTMENT_STUDIO_RESEARCH_PROJECT_ROOT=$copilot_project_root"
  "INVESTMENT_STUDIO_RESEARCH_API_BASE_URL=${INVESTMENT_STUDIO_WATCHLIST_RESEARCH_API_BASE_URL:-http://127.0.0.1:8000/api}"
  "INVESTMENT_STUDIO_RESEARCH_RUN_ID=$research_run_id"
  "INVESTMENT_STUDIO_RESEARCH_RUN_TOKEN=${INVESTMENT_STUDIO_RESEARCH_RUN_TOKEN:?research run credential is required}"
  "INVESTMENT_STUDIO_RESEARCH_PERSONA=$copilot_research_persona"
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

copilot_command=(/usr/bin/env -i "${copilot_exec_env[@]}" \
  "$copilot_pnpm" dlx --allow-build=@deepseek-ai/dsh-subprocess-local --allow-build=@google/genai --allow-build=koffi --allow-build=node-pty --allow-build=protobufjs @deepseek-ai/dsh@0.1.1-rc.2 \
  --profile headless \
  --patch "$copilot_patch" \
  "$copilot_task")
if [[ "${2:-}" != "risk" ]]; then
  # macOS /bin/bash 3.2 treats an empty array as unset under set -u.
  # Keep the complete command nonempty for automatic research too.
  copilot_review_command=(/usr/bin/env -i "${copilot_exec_env[@]}"
    "$copilot_project_root/.venv/bin/python" -m watchlist_app.services.sector_fact_review)
  if [[ "${2:-}" != "sector" ]]; then
    copilot_review_command+=(--conversation)
  fi
  copilot_review_output() {
    local copilot_review_status=0
    "${copilot_review_command[@]}" || copilot_review_status=$?
    if [[ "$copilot_review_status" -ne 0 ]]; then
      # A missing executable/module can fail before Python emits its own marker.
      # The runner keeps an earlier, more specific marker when one exists.
      printf '%s\n' 'SECTOR_REVIEW_ERROR {"type":"FactReviewProcessExit","summary":"本地事实核证进程退出，未生成核证结果；请检查研究运行环境。"}' >&2
    fi
    return "$copilot_review_status"
  }
  "${copilot_command[@]}" | copilot_review_output
else
  exec "${copilot_command[@]}"
fi
