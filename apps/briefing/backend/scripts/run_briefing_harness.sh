#!/usr/bin/env bash
set -euo pipefail

if [[ $# != 2 || ( "$2" != write && "$2" != review ) ]]; then
  echo "Usage: $0 <report-id> <write|review>" >&2
  exit 64
fi
briefing_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
briefing_root="$(cd "$briefing_script_dir/../../../.." && pwd -P)"
briefing_secret_root="${INVESTMENT_STUDIO_SECRET_ROOT:-$HOME/.config/orataba/secrets/investment-studio}"
briefing_env_file="${INVESTMENT_STUDIO_PORTFOLIO_COPILOT_ENV_FILE:-$briefing_secret_root/portfolio-copilot.env}"
briefing_pnpm="${INVESTMENT_STUDIO_PORTFOLIO_COPILOT_PNPM:-$(command -v pnpm || true)}"
if [[ -z "$briefing_pnpm" || ! -x "$briefing_pnpm" ]]; then
  echo "pnpm is required for the pinned DeepSeek Harness." >&2
  exit 1
fi
export PATH="$(dirname "$briefing_pnpm"):$PATH"
source "$briefing_root/infra/launchd/load_runtime_env.sh"
investment_studio_reject_repository_env_files "$briefing_root"
investment_studio_load_env_file "$briefing_env_file" DEEPSEEK_ INVESTMENT_STUDIO_PORTFOLIO_COPILOT_
: "${DEEPSEEK_API_KEY:?DeepSeek credential is not configured}"

briefing_dsh_home="${INVESTMENT_STUDIO_BRIEFING_DSH_HOME:-$HOME/.local/share/investment-studio/briefing-harness}"
mkdir -p "$briefing_dsh_home"
chmod 700 "$briefing_dsh_home"
: "${INVESTMENT_STUDIO_BRIEFING_RUN_TOKEN:?Report task identity is required}"
briefing_env=(
  "HOME=$HOME" "PATH=$PATH" "TMPDIR=${TMPDIR:-/tmp}" "LANG=${LANG:-C.UTF-8}"
  "DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY" "DSH_HOME=$briefing_dsh_home"
  "DSH_PERMISSION_MODE=read-only" "DSH_TOOLS_MODE=native" "DSH_TELEMETRY_DISABLED=1"
  "INVESTMENT_STUDIO_BRIEFING_PROJECT_ROOT=$briefing_root"
  "INVESTMENT_STUDIO_BRIEFING_API_BASE_URL=${INVESTMENT_STUDIO_BRIEFING_API_BASE_URL:-http://127.0.0.1:8010/api/briefing}"
  "INVESTMENT_STUDIO_BRIEFING_REPORT_ID=$1"
  "INVESTMENT_STUDIO_BRIEFING_RUN_TOKEN=$INVESTMENT_STUDIO_BRIEFING_RUN_TOKEN"
  "INVESTMENT_STUDIO_BRIEFING_HARNESS_MODE=$2"
)
for briefing_key in DEEPSEEK_BASE_URL HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy NPM_CONFIG_REGISTRY PNPM_HOME SSL_CERT_FILE SSL_CERT_DIR NODE_EXTRA_CA_CERTS; do
  if [[ -n "${!briefing_key:-}" ]]; then
    briefing_env+=("$briefing_key=${!briefing_key}")
  fi
done
if [[ "$2" == review ]]; then
  briefing_task="Act as a fresh independent editor. Read read_briefing_context and its draft_json first. Keep the existing topics and structure; do not add topics from the headline index. Read every cited source, including every retained page, again. Treat all original draft fields as unverified model writing. Correct factual meaning, subjects, attribution, conditional thresholds, event dates, relative weeks, financial directions, currencies and numerical scale in every field, including analysis and outlook. Where retained evidence conflicts or does not establish the claim, state the uncertainty or remove that unsupported claim. Submit the complete corrected report with submit_briefing_report even if no edits are needed, resolve validation errors, and acknowledge acceptance."
else
  briefing_task="Read read_briefing_context including its complete current-period headline index. Select relevant retained originals and read every page of those originals before citing them. Search late_received historical context when relevant, keeping its original dates. Compose the Chinese daily or weekly briefing using only bound evidence and program-calculated tables. Submit the complete structured report with submit_briefing_report, correct validation errors, then briefly acknowledge acceptance."
fi
exec /usr/bin/env -i "${briefing_env[@]}" "$briefing_pnpm" dlx \
  --allow-build=@deepseek-ai/dsh-subprocess-local --allow-build=@google/genai \
  --allow-build=koffi --allow-build=node-pty --allow-build=protobufjs \
  @deepseek-ai/dsh@0.1.1-rc.2 \
  --profile headless --patch "$briefing_root/infra/config/deepseek_harness.patch.yml" \
  --patch "$briefing_root/apps/briefing/backend/config/briefing_harness.patch.yml" \
  "$briefing_task"
