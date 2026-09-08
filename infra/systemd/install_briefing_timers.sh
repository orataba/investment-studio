#!/usr/bin/env bash
set -euo pipefail

briefing_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
briefing_root="${PROJECT_ROOT:-$(cd "$briefing_script_dir/../.." && pwd -P)}"
briefing_python="${PYTHON_BIN:-$briefing_root/.venv/bin/python}"
briefing_unit_prefix="${UNIT_PREFIX:-investment-studio}"
briefing_daily_calendar="${BRIEFING_DAILY_ON_CALENDAR:-*-*-* 08:30 Asia/Shanghai}"
briefing_weekly_calendar="${BRIEFING_WEEKLY_ON_CALENDAR:-Sat *-*-* 09:00 Asia/Shanghai}"
briefing_start_timers="${START_TIMERS:-true}"
source "$briefing_root/infra/launchd/load_runtime_env.sh"
investment_studio_reject_repository_env_files "$briefing_root"
briefing_env_root="$(investment_studio_resolve_external_env_root "$briefing_root" "${ENV_ROOT:?ENV_ROOT must name the external configuration directory}")"
briefing_env_file="$briefing_env_root/briefing.env"
investment_studio_load_env_file "$briefing_env_file" INVESTMENT_STUDIO_BRIEFING_ INVESTMENT_STUDIO_AUTH_
if [[ "${INVESTMENT_STUDIO_BRIEFING_EDITION_ROLE:-preview}" != "publisher" ]]; then
  echo "Briefing timers can only be installed on the configured formal publisher." >&2
  exit 64
fi
if [[ ! -x "$briefing_python" ]]; then
  echo "PYTHON_BIN is not executable." >&2
  exit 64
fi
if [[ "$briefing_start_timers" != true && "$briefing_start_timers" != false ]]; then
  echo "START_TIMERS must be true or false." >&2
  exit 64
fi
for briefing_calendar in "$briefing_daily_calendar" "$briefing_weekly_calendar"; do
  if [[ "$briefing_calendar" == *$'\n'* || "$briefing_calendar" == *$'\r'* ]]; then
    echo "Each briefing calendar must be one OnCalendar expression." >&2
    exit 64
  fi
done
briefing_unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$briefing_unit_dir"
for briefing_period in daily weekly; do
  briefing_unit="$briefing_unit_prefix-briefing-$briefing_period"
  briefing_calendar="$briefing_daily_calendar"
  [[ "$briefing_period" != weekly ]] || briefing_calendar="$briefing_weekly_calendar"
  cat > "$briefing_unit_dir/$briefing_unit.service" <<EOF
[Unit]
Description=Investment Studio $briefing_period briefing
After=network-online.target $briefing_unit_prefix-briefing-api.service
Wants=network-online.target $briefing_unit_prefix-briefing-api.service

[Service]
Type=oneshot
WorkingDirectory=$briefing_root/apps/briefing/backend
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=$briefing_root/packages/identity:$briefing_root/apps/briefing/backend
EnvironmentFile=$(printf '%q' "$briefing_env_file")
ExecStart=$briefing_python -m briefing_app.cli $briefing_period --scheduled
TimeoutStartSec=70min
StandardOutput=journal
StandardError=journal
EOF
  cat > "$briefing_unit_dir/$briefing_unit.timer" <<EOF
[Unit]
Description=Schedule Investment Studio $briefing_period briefing

[Timer]
OnCalendar=$briefing_calendar
Persistent=false
Unit=$briefing_unit.service

[Install]
WantedBy=timers.target
EOF
  chmod 600 "$briefing_unit_dir/$briefing_unit.service" "$briefing_unit_dir/$briefing_unit.timer"
done
systemctl --user daemon-reload
systemctl --user enable "$briefing_unit_prefix-briefing-daily.timer" "$briefing_unit_prefix-briefing-weekly.timer"
if [[ "$briefing_start_timers" == true ]]; then
  systemctl --user start "$briefing_unit_prefix-briefing-daily.timer" "$briefing_unit_prefix-briefing-weekly.timer"
fi
echo "Briefing schedules installed for the formal publisher; each report retains actual source and market cutoffs."
