#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-launchd-plist-test.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

PROJECT_ROOT="$TEST_ROOT/project with spaces"
PLIST_ROOT="$TEST_ROOT/LaunchAgents"
LOG_ROOT="$TEST_ROOT/logs"
ENV_ROOT="$TEST_ROOT/secure env"
WEB_RELEASE_ROOT="$TEST_ROOT/web release/release-test"
RUNTIME_ROOT="$WEB_RELEASE_ROOT/runtime"
mkdir -p \
  "$PROJECT_ROOT" \
  "$PLIST_ROOT" \
  "$LOG_ROOT" \
  "$ENV_ROOT" \
  "$RUNTIME_ROOT/infra/launchd" \
  "$RUNTIME_ROOT/infra/scripts" \
  "$RUNTIME_ROOT/deploy"
for app in platform watchlist portfolio; do
  mkdir -p "$WEB_RELEASE_ROOT/$app"
  printf '%s\n' '<!doctype html>' > "$WEB_RELEASE_ROOT/$app/index.html"
  printf '%s\n' 'release-test' > "$WEB_RELEASE_ROOT/$app/release-id.txt"
done
printf '%s\n' 'release-test' > "$RUNTIME_ROOT/release-id.txt"
printf '%s\n' '{}' > "$RUNTIME_ROOT/runtime-manifest.json"
for relative_file in \
  infra/launchd/run_local_service.sh \
  infra/launchd/run_market_data_refresh.sh \
  infra/launchd/stage_local_runtime.py \
  infra/scripts/audit_live_data.py \
  deploy/serve_spa_proxy.mjs; do
  : > "$RUNTIME_ROOT/$relative_file"
done
chmod 700 "$ENV_ROOT"
source "$REPOSITORY_ROOT/infra/service_inventory.sh"
EXPECTED_SERVICES="$(printf '%s\n' "${PORTFOLIO_OPS_ALL_SERVICE_NAMES[@]}")"

python3 "$REPOSITORY_ROOT/infra/launchd/generate_local_service_plists.py" \
  --project-root "$PROJECT_ROOT" \
  --runtime-root "$RUNTIME_ROOT" \
  --python-bin "/runtime/python with spaces" \
  --node-bin "/runtime/node with spaces" \
  --database-url "postgresql+psycopg://local/test" \
  --label-prefix "test.portfolio-ops" \
  --launch-agents-dir "$PLIST_ROOT" \
  --log-dir "$LOG_ROOT" \
  --env-root "$ENV_ROOT" \
  --web-release-root "$WEB_RELEASE_ROOT" \
  --release-id "release-test" \
  --refresh-hour 21 \
  --refresh-minute 0

PLIST_ROOT="$PLIST_ROOT" PROJECT_ROOT="$PROJECT_ROOT" RUNTIME_ROOT="$RUNTIME_ROOT" LOG_ROOT="$LOG_ROOT" ENV_ROOT="$ENV_ROOT" WEB_RELEASE_ROOT="$WEB_RELEASE_ROOT" EXPECTED_SERVICES="$EXPECTED_SERVICES" python3 - <<'PY'
import os
import plistlib
from pathlib import Path


plist_root = Path(os.environ["PLIST_ROOT"])
project_root = str(Path(os.environ["PROJECT_ROOT"]).resolve())
runtime_root = str(Path(os.environ["RUNTIME_ROOT"]).resolve())
log_root = str(Path(os.environ["LOG_ROOT"]).resolve())
env_root = str(Path(os.environ["ENV_ROOT"]).resolve())
web_release_root = str(Path(os.environ["WEB_RELEASE_ROOT"]).resolve())
expected_services = set(os.environ["EXPECTED_SERVICES"].splitlines())
generated_services = {
    path.name.removeprefix("test.portfolio-ops.").removesuffix(".plist")
    for path in plist_root.glob("test.portfolio-ops.*.plist")
}
assert generated_services == expected_services

with (plist_root / "test.portfolio-ops.platform-api.plist").open("rb") as source:
    api = plistlib.load(source)
assert api["KeepAlive"] is True
assert api["RunAtLoad"] is True
assert api["Umask"] == 0o077
assert api["ProgramArguments"][0] == f"{runtime_root}/infra/launchd/run_local_service.sh"
assert api["ProgramArguments"][2] == runtime_root
assert api["ProgramArguments"][5] == env_root
assert api["WorkingDirectory"] == runtime_root
assert api["EnvironmentVariables"]["PORTFOLIO_OPS_LOCAL_RELEASE_ID"] == "release-test"
assert api["EnvironmentVariables"]["PORTFOLIO_OPS_LOCAL_STATE_ROOT"] == project_root
assert "PORTFOLIO_OPS_LOCAL_WEB_RELEASE_ROOT" not in api["EnvironmentVariables"]

with (plist_root / "test.portfolio-ops.portfolio-web.plist").open("rb") as source:
    portfolio_web = plistlib.load(source)
assert portfolio_web["EnvironmentVariables"][
    "PORTFOLIO_OPS_LOCAL_WEB_RELEASE_ROOT"
] == web_release_root
assert portfolio_web["EnvironmentVariables"][
    "PORTFOLIO_OPS_LOCAL_RELEASE_ID"
] == "release-test"

with (plist_root / "test.portfolio-ops.portfolio-worker.plist").open("rb") as source:
    worker = plistlib.load(source)
assert worker["KeepAlive"] is True
assert worker["RunAtLoad"] is True
assert worker["ProcessType"] == "Background"
assert worker["ProgramArguments"][1] == "portfolio-worker"
assert worker["StandardOutPath"] == f"{log_root}/portfolio-worker.log"
assert worker["StandardErrorPath"] == f"{log_root}/portfolio-worker.error.log"

with (plist_root / "test.portfolio-ops.watchlist-worker.plist").open("rb") as source:
    watchlist_worker = plistlib.load(source)
assert watchlist_worker["KeepAlive"] is True
assert watchlist_worker["RunAtLoad"] is True
assert watchlist_worker["ProcessType"] == "Background"
assert watchlist_worker["ProgramArguments"][1] == "watchlist-worker"
assert watchlist_worker["StandardOutPath"] == f"{log_root}/watchlist-worker.log"
assert watchlist_worker["StandardErrorPath"] == f"{log_root}/watchlist-worker.error.log"

with (plist_root / "test.portfolio-ops.platform-outbox-worker.plist").open("rb") as source:
    platform_outbox_worker = plistlib.load(source)
assert platform_outbox_worker["KeepAlive"] is True
assert platform_outbox_worker["RunAtLoad"] is True
assert platform_outbox_worker["ProcessType"] == "Background"
assert platform_outbox_worker["ProgramArguments"][1] == "platform-outbox-worker"
assert platform_outbox_worker["StandardOutPath"] == f"{log_root}/platform-outbox-worker.log"
assert platform_outbox_worker["StandardErrorPath"] == f"{log_root}/platform-outbox-worker.error.log"

refresh_path = plist_root / "test.portfolio-ops.market-data-refresh.plist"
with refresh_path.open("rb") as source:
    refresh = plistlib.load(source)
assert refresh["KeepAlive"] is False
assert refresh["RunAtLoad"] is False
assert refresh["StartCalendarInterval"] == {"Hour": 21, "Minute": 0}
assert refresh["ProcessType"] == "Background"
assert refresh["LowPriorityIO"] is True
assert refresh["Umask"] == 0o077
assert refresh["ProgramArguments"] == [
    f"{runtime_root}/infra/launchd/run_market_data_refresh.sh",
    runtime_root,
    "/runtime/python with spaces",
    f"{project_root}/var/market-data-refresh.lock",
    env_root,
]
assert refresh["WorkingDirectory"] == f"{runtime_root}/apps/platform/backend"
assert refresh["EnvironmentVariables"]["PORTFOLIO_OPS_LOCAL_RELEASE_ID"] == "release-test"
assert refresh["EnvironmentVariables"]["PORTFOLIO_OPS_LOCAL_STATE_ROOT"] == project_root
assert refresh["StandardOutPath"] == f"{log_root}/market-data-refresh.log"
assert refresh["StandardErrorPath"] == f"{log_root}/market-data-refresh.error.log"
assert refresh_path.stat().st_mode & 0o777 == 0o600
PY

grep -Fq 'http://127.0.0.1:8002/api/readiness' \
  "$REPOSITORY_ROOT/infra/launchd/install_local_services.sh"
grep -Fq 'http://127.0.0.1:8002/api/readiness' \
  "$REPOSITORY_ROOT/infra/launchd/status_local_services.sh"
grep -Fq 'portfolio_ops_verify_portfolio_read_contract' \
  "$REPOSITORY_ROOT/infra/launchd/install_local_services.sh"
grep -Fq 'stage_local_runtime.py" stage' \
  "$REPOSITORY_ROOT/infra/launchd/install_local_services.sh"
grep -Fq 'PROJECT_ROOT="$runtime_release_dir"' \
  "$REPOSITORY_ROOT/infra/launchd/install_local_services.sh"
if grep -Fq 'local-web-releases/current' \
  "$REPOSITORY_ROOT/infra/launchd/run_local_service.sh"; then
  echo "Managed services must not resolve runtime or web code through current." >&2
  exit 1
fi
if grep -Fq 'frontend/dist"' \
  "$REPOSITORY_ROOT/infra/launchd/run_local_service.sh"; then
  echo "Managed web services must not serve mutable frontend build output." >&2
  exit 1
fi
grep -Fq 'source "$PROJECT_ROOT/infra/service_inventory.sh"' \
  "$REPOSITORY_ROOT/infra/postgres/restore_project_dump.sh"
grep -Fq 'PORTFOLIO_OPS_SYSTEMD_MANAGED_UNIT_SUFFIXES' \
  "$REPOSITORY_ROOT/infra/postgres/restore_project_dump.sh"

echo "launchd plist generation test passed."
