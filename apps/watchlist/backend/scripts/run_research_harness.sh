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
copilot_dsh_home="${INVESTMENT_STUDIO_PORTFOLIO_COPILOT_DSH_HOME:-$HOME/.local/share/investment-studio/deepseek-harness}"
research_run_id="$1"
if [[ "${2:-}" == "sector" ]]; then
  copilot_patch="$copilot_project_root/apps/watchlist/backend/config/sector_harness.patch.yml"
elif [[ "${2:-}" == "risk" ]]; then
  copilot_patch="$copilot_project_root/apps/watchlist/backend/config/risk_harness.patch.yml"
fi

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
export INVESTMENT_STUDIO_PORTFOLIO_COPILOT_MODEL_NAME="${INVESTMENT_STUDIO_PORTFOLIO_COPILOT_MODEL_NAME:-deepseek-v4.1-flash}"

copilot_research_persona="$(< "$copilot_project_root/apps/watchlist/backend/config/research_core.md")"
copilot_task="Start with read_research_context overview and read its nonempty page_context/selected-reference sections plus relevant history/evidence. Read relevant instrument overviews and dossier research_plan/mandate/review_agenda/modules sections. Follow next_offset and every deferred.path for selected evidence, repeating the same section/source/version selector. Answer the current question in Chinese with evidence using the same bound dossier and tools as continuing investment research. This conversation is private. Publish a durable research increment only when this user explicitly asks to save it to team research: record that instruction with authorize_team_research before submit_research_review. Never publish portfolio-derived discussion to team research. The application saves your answer and separately reports the common factual review/publication result."
if [[ "${2:-}" == "sector" ]]; then
  copilot_task="Start with read_research_context overview and read_research_instrument overview for every requested instrument_id. Read nonempty incremental_trigger and data_gaps/limitations context sections before investigating or claiming no changes. Read each dossier research_plan/mandate/review_agenda/modules and relevant research sections with read_research_dossier; read shared frameworks and available_modules when refining the method. Read coverage details or catalogue on demand. Follow next_offset and every deferred.path for selected data, preserving the same section/source/version selector; indexes do not count as reading originals. Distinguish an initial instrument investigation from a follow-up review. If no instrument-specific research baseline has ever been established, investigate the retained fundamental, quantitative, pricing and exposure evidence even when no new headline or incremental_trigger exists; generic shared methods, generated mandate metadata and earlier quiet receipts without an instrument-specific judgment or research question do not constitute a completed baseline. Establish an evidence-based initial mandate, a concrete consequential question or a conditional judgment as warranted. If evidence is insufficient, identify the specific decision it limits and the evidence needed; do not wait for the PM to supply your research direction or infer an investment stance from PM workflow status. When a currently applicable module has no saved analysis, investigate its evidence and establish an initial assessment or explicit evidence gap, including when earlier general prose or questions already exist. For an established baseline, examine the reasoning and evidence of the exact prior judgments under review as well as new information; a changed method requires reassessing affected modules, not rewriting every module on every run. A discovered factual error or unsupported pricing/causal inference is a knowledge correction even without new headlines or changed inputs: revise only the affected assertions, keeping historical versions, and state unknowns or conditional hypotheses where needed. Follow the current agenda with suitable tools. Form or revise forward judgments only when warranted. Submit one review per requested instrument through submit_research_review, using change_kind none, knowledge or investment. A quiet check may have empty summary/events and research=null. Research fields are sparse deltas, not mandatory chapters. Correct real validation errors and briefly acknowledge draft acceptance."
elif [[ "${2:-}" == "risk" ]]; then
  copilot_task="Read read_research_context for the scope index, then execute every instrument_overview_pages entry using its tool: read_risk_instruments(offset) for batches or read_risk_instrument(instrument_id) for explicit single entries, reading independent entries in parallel when possible; do not repeat individual overviews. Execute every instrument_detail_reads entry from the scope index using read_risk_instrument. A missing_reads submission error lists exact tools and arguments: read all listed pages, then resubmit the COMPLETE result. Use the overview counts: skip a section if its current and previous counts are both zero. Read all nonempty cases and research_context pages from offset=0 through next_offset=null, including prior-only rows. Read comparisons when needed for performance assessment and follow all pages in that section; unread comparisons cannot support relative-performance conclusions. The research_context contains the complete current PM profile with thesis/counterevidence/monitoring and attribution limits, attributed PM original notes, actively tracked questions/counterevidence and forecasts due for review; distinguish these retained judgments from facts and do not assume a due forecast is fulfilled or wrong. Use sample_dates with comparison_source_id when exact pair-specific alignment is needed. Analyze only these bound retained snapshots. For portfolio scope, also read every portfolio risk module and each derivative holding with read_portfolio_risk. Aggregate researcher risk reports, actual performance and comparisons, quantitative triggers, portfolio allocation/risk/correlation changes and FCN/Option settlement obligations; distinguish unavailable monitoring from safety. Submit the complete concise Chinese risk assessment with submit_risk_review, using shared case IDs and evidence references. Every submission requires summary, priorities and limitations. Correct all tool validation errors by resubmitting the complete result, never only the missing field, then acknowledge acceptance without reprinting JSON. Do not search or trade."
fi

if [[ "${2:-}" != "risk" ]]; then
  copilot_task+=" Numerical tools read_research_numbers/compare_instruments return calculated values and a source_id. Read needed full points/dates/history/input versions through their returned sections/paths with the SAME source_id, following next_offset and all deferred.path. Do not rerun calculations or shorten the sample to continue a page."
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
  DEEPSEEK_SEARCH_URL \
  HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY \
  http_proxy https_proxy all_proxy no_proxy \
  XDG_CACHE_HOME XDG_CONFIG_HOME XDG_DATA_HOME \
  SSL_CERT_FILE SSL_CERT_DIR NODE_EXTRA_CA_CERTS
do
  if [[ -n "${!copilot_optional_env:-}" ]]; then
    copilot_exec_env+=("$copilot_optional_env=${!copilot_optional_env}")
  fi
done

copilot_command=(/usr/bin/env -i "${copilot_exec_env[@]}" \
  "$copilot_project_root/infra/harness/run.sh" \
  --profile headless \
  --patch "$copilot_project_root/infra/config/deepseek_harness.patch.yml" \
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
  # A failed generation has no reviewable result. Preserve its exit and reply;
  # starting the reviewer would charge again and replace the actual failure with
  # a misleading missing-draft error.
  if copilot_reply="$("${copilot_command[@]}")"; then
    printf '%s\n' "$copilot_reply" | copilot_review_output
  else
    copilot_generation_status=$?
    printf '%s\n' "$copilot_reply"
    exit "$copilot_generation_status"
  fi
else
  exec "${copilot_command[@]}"
fi
