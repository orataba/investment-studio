#!/usr/bin/env bash
set -euo pipefail

market_project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
market_env_root="${ENV_ROOT:?ENV_ROOT must name the external runtime configuration directory}"
market_python="${PYTHON_BIN:-$market_project_root/.venv/bin/python}"
source "$market_project_root/infra/launchd/load_runtime_env.sh"
investment_studio_reject_repository_env_files "$market_project_root"
market_env_root="$(investment_studio_resolve_external_env_root "$market_project_root" "$market_env_root")"
investment_studio_load_env_file "$market_env_root/market.env" INVESTMENT_STUDIO_MARKET_
investment_studio_load_env_file "$market_env_root/data.env" INVESTMENT_STUDIO_DATA_ INVESTMENT_STUDIO_INSTRUMENT_DATA_ INVESTMENT_STUDIO_AUTH_
export PYTHONPATH="$market_project_root/packages/identity:$market_project_root/shared-data/market:$market_project_root/shared-data:$market_project_root/shared-data/instruments/python${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUNBUFFERED=1
export PYTHONNOUSERSITE=1
case "${1:-}" in
  daily|crypto|registered-prices|sync) ;;
  *) exec "$market_python" -m studio_market.pipeline "$@" ;;
esac

exec "$market_python" - "$market_project_root" "$@" <<'PY'
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from studio_market.config import MarketSettings
from studio_market.numeric.collect import symbol_market
from studio_market.numeric.store import serializable
from studio_market.pipeline import run

root = Path(sys.argv[1])
parser = argparse.ArgumentParser()
parser.add_argument('action', choices=['daily', 'crypto', 'registered-prices', 'sync'])
parser.add_argument('--market', choices=['cn', 'hk', 'us', 'eu', 'all'], default='all')
args = parser.parse_args(sys.argv[2:])
result = run(MarketSettings.from_environment(), args.action, market=args.market)
print(json.dumps(serializable(result), ensure_ascii=False, indent=2), flush=True)
if result['status'] == 'already_running':
    raise SystemExit(0)
exit_code = 1 if result['status'] == 'failed' else 0
stages = {item['stage']: item for item in result['stages']}
if args.action == 'sync' and stages.get('numeric_catchup', {}).get('status') == 'ready':
    # Regime's scheduled read can precede cloud publication and local delivery.
    # Reuse its existing catch-up agent after arrival; launchd keeps one instance.
    label = os.environ.get('REGIME_LOCAL_AUTOMATION_LABEL_PREFIX', 'com.orataba.asset-regime-dashboard.auto') + '.catch-up'
    agents = Path(os.environ.get('REGIME_LAUNCH_AGENT_DIR', Path.home() / 'Library/LaunchAgents'))
    if (agents / (label + '.plist')).is_file():
        completed = subprocess.run(['launchctl', 'kickstart', f'gui/{os.getuid()}/{label}'])
        exit_code = exit_code or completed.returncode
channels = []
if args.action == 'registered-prices' and stages.get('registered_raw_prices', {}).get('status') == 'ready':
    channels = ['market']
elif args.action in {'daily', 'crypto'}:
    if stages.get('market_series', {}).get('status') == 'ready':
        channels.append('market')
    if stages.get('registered_reference', {}).get('status') == 'ready':
        channels.append('reference')
elif args.action == 'sync' and stages.get('numeric_catchup', {}).get('status') == 'ready':
    # A previous arrival can lose the shared projection lock to settlement.
    # Every scheduled sync rechecks the public projections, even without a new pack.
    channels = ['market', 'reference']
if not channels:
    raise SystemExit(exit_code)

from studio_data.services.instrument_store import list_instruments

# Arrivals may occur after midnight or on holidays. Choose the public identities
# directly; the scheduled projection keeps its own price and accounting dates.
registered = list_instruments(include_inactive=False)
for channel in channels:
    instruments = []
    for item in registered:
        kind = item['instrument_type']
        if kind not in {'equity', 'etf', 'public_fund', 'index', 'crypto'} or item.get('source_settings', {}).get('source_api_profile') != 'fmp':
            continue
        # Crypto uses the UTC market series, never listed raw prices or equity
        # reference data. Daily listed prices have their own closing jobs.
        if kind == 'crypto' and (channel != 'market' or args.action == 'registered-prices'):
            continue
        if args.action in {'daily', 'crypto'} and channel == 'market' and kind != 'crypto':
            continue
        symbols = [row['identifier_value'][4:] for row in item.get('identifiers', [])
                   if row.get('identifier_type') == 'provider_symbol' and row.get('identifier_value', '').startswith('fmp:')]
        if args.action != 'registered-prices' or args.market == 'all' or any(symbol_market(symbol) == args.market for symbol in symbols):
            instruments.append(str(item['instrument_id']))
    if not instruments:
        continue
    command = [sys.executable, str(root / 'shared-data/scripts/refresh_market_data_scheduled.py'),
               '--channel', channel, '--updated-by', 'public-market-pipeline',
               '--lock-wait-seconds', '900',
               '--fail-on-item-failure', '--require-downstream-success', '--json']
    for instrument_id in instruments:
        command.extend(['--instrument-id', instrument_id])
    completed = subprocess.run(command)
    exit_code = exit_code or completed.returncode
raise SystemExit(exit_code)
PY
