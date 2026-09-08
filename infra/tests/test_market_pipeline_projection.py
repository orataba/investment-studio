"""Exercise the managed pipeline runner without providers, databases or services."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def test_public_arrivals_project_successful_stages_and_keep_external_api_targets(tmp_path):
    root = tmp_path / 'repo'
    for path in ('infra/scripts', 'infra/launchd', 'shared-data/scripts', 'shared-data/studio_data/services', 'shared-data/market/studio_market/numeric'):
        (root / path).mkdir(parents=True)
    for path in ('infra/scripts/run_market_pipeline.sh', 'infra/launchd/load_runtime_env.sh'):
        shutil.copy2(ROOT / path, root / path)
    for path in ('shared-data/studio_data', 'shared-data/studio_data/services', 'shared-data/market/studio_market', 'shared-data/market/studio_market/numeric'):
        (root / path / '__init__.py').write_text('')
    config = tmp_path / 'runtime'
    config.mkdir(mode=0o700)
    for name, content in {
        'market.env': 'INVESTMENT_STUDIO_MARKET_ROLE=replica\n',
        'data.env': 'INVESTMENT_STUDIO_DATA_WATCHLIST_API_URL=http://127.0.0.1:8100\nINVESTMENT_STUDIO_DATA_PORTFOLIO_API_URL=http://127.0.0.1:8101\nINVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL=postgresql://registry@localhost/studio\n',
    }.items():
        (config / name).write_text(content)
        (config / name).chmod(0o600)
    (root / 'shared-data/market/studio_market/config.py').write_text('class MarketSettings:\n @classmethod\n def from_environment(cls): return None\n')
    (root / 'shared-data/market/studio_market/numeric/store.py').write_text('def serializable(value): return value\n')
    (root / 'shared-data/market/studio_market/numeric/collect.py').write_text("def symbol_market(symbol): return 'hk' if symbol.endswith('.HK') else 'us'\n")
    (root / 'shared-data/market/studio_market/pipeline.py').write_text('''
import json, os, sys
from pathlib import Path
def run(settings, action, *, market):
    with Path(os.environ['TEST_EVENTS']).open('a') as output:
        output.write(json.dumps({'step':'pipeline','action':action,'market':market,
            'registry_url':os.environ['INVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL']}) + '\\n')
    return json.loads(os.environ['TEST_PIPELINE_RESULT'])
if __name__ == '__main__':
    run(None, sys.argv[1], market='all')
''')
    (root / 'shared-data/studio_data/services/instrument_store.py').write_text('''
def list_instruments(*, include_inactive):
    assert not include_inactive
    return [{'instrument_id': iid, 'instrument_type': kind, 'source_settings': {'source_api_profile': profile},
             'identifiers': [{'identifier_type': 'provider_symbol', 'identifier_value': 'fmp:' + symbol}]}
            for iid,kind,profile,symbol in [('us','equity','fmp','AAPL'),('hk','etf','fmp','2800.HK'),
                                          ('btcusd','crypto','fmp','BTCUSD'),
                                          ('private','private_fund','fmp','PRIVATE'),('manual','equity','','MANUAL')]]
''')
    (root / 'shared-data/scripts/refresh_market_data_scheduled.py').write_text('''
import json, os, sys
from pathlib import Path
with Path(os.environ['TEST_EVENTS']).open('a') as output:
    output.write(json.dumps({'step':'projection','args':sys.argv[1:],
        'watchlist':os.environ['INVESTMENT_STUDIO_DATA_WATCHLIST_API_URL'],
        'portfolio':os.environ['INVESTMENT_STUDIO_DATA_PORTFOLIO_API_URL']}) + '\\n')
raise SystemExit(int(os.environ.get('TEST_PROJECTION_EXIT', '0')))
''')
    events = tmp_path / 'events.jsonl'
    agents = tmp_path / 'agents'
    agents.mkdir()
    (agents / 'test.regime.auto.catch-up.plist').write_text('installed')
    binary = tmp_path / 'bin'
    binary.mkdir()
    launchctl = binary / 'launchctl'
    launchctl.write_text(f'''#!{sys.executable}
import json, os, sys
from pathlib import Path
with Path(os.environ['TEST_EVENTS']).open('a') as output:
    output.write(json.dumps({{'step':'regime-catch-up','args':sys.argv[1:]}}) + '\\n')
''')
    launchctl.chmod(0o755)
    ready_prices = {'status':'ready','stages':[{'stage':'registered_raw_prices','status':'ready'}]}
    sync_new = {'status':'ready','stages':[{'stage':'numeric_catchup','status':'ready','result':{'receipts':[{'batches':[{'status':'ready'}]}]}}]}
    cases = [
        (['registered-prices','--market','us'], ready_prices, 0, ['market'], [['us']]),
        (['registered-prices','--market','hk'], ready_prices, 0, ['market'], [['hk']]),
        (['registered-prices','--market','cn'], ready_prices, 0, [], []),
        (['registered-prices','--market','eu'], ready_prices, 0, [], []),
        (['weekly'], {'status':'ready','stages':[]}, 0, [], []),
        (['publish'], {'status':'ready','stages':[]}, 0, [], []),
        (['registered-prices','--market','us'], {**ready_prices,'status':'failed'}, 0, ['market'], [['us']]),
        (['sync'], {'status':'ready','stages':[{'stage':'numeric_catchup','status':'ready','result':{'receipts':[]}}]}, 0, ['market','reference'], [['us','hk','btcusd'],['us','hk']]),
        (['sync'], {'status':'ready','stages':[{'stage':'numeric_catchup','status':'ready','result':{'receipts':[{'batches':[{'status':'already_imported'}]}]}}]}, 0, ['market','reference'], [['us','hk','btcusd'],['us','hk']]),
        (['sync'], sync_new, 0, ['market','reference'], [['us','hk','btcusd'],['us','hk']]),
        (['sync'], {**sync_new,'status':'failed','stages':sync_new['stages'] + [{'stage':'mi_text_catchup','status':'failed'}]}, 0, ['market','reference'], [['us','hk','btcusd'],['us','hk']]),
        (['sync'], {'status':'failed','stages':[{'stage':'numeric_catchup','status':'failed'}]}, 0, [], []),
        (['sync'], {'status':'already_running'}, 0, [], []),
        (['daily'], {'status':'ready','stages':[{'stage':'registered_reference','status':'ready'}]}, 0, ['reference'], [['us','hk']]),
        (['daily'], {'status':'ready','stages':[{'stage':'market_series','status':'ready'}, {'stage':'registered_reference','status':'ready'}]}, 0, ['market','reference'], [['btcusd'],['us','hk']]),
        (['daily'], {'status':'ready','stages':[{'stage':'market_series','status':'ready'}]}, 0, ['market'], [['btcusd']]),
        (['crypto'], {'status':'ready','stages':[{'stage':'market_series','status':'ready'}]}, 0, ['market'], [['btcusd']]),
        (['crypto'], {'status':'failed','stages':[{'stage':'market_series','status':'failed'}]}, 0, [], []),
        (['crypto'], {'status':'ready','stages':[{'stage':'market_series','status':'ready'}]}, 1, ['market'], [['btcusd']]),
        (['sync'], sync_new, 9, ['market','reference'], [['us','hk','btcusd'],['us','hk']]),
    ]
    for arguments, result, projection_exit, channels, expected_ids in cases:
        events.write_text('')
        env = dict(os.environ, ENV_ROOT=str(config), PYTHON_BIN=sys.executable, TEST_EVENTS=str(events),
                   REGIME_LAUNCH_AGENT_DIR=str(agents), REGIME_LOCAL_AUTOMATION_LABEL_PREFIX='test.regime.auto',
                   PATH=str(binary)+os.pathsep+os.environ.get('PATH',''),
                   TEST_PIPELINE_RESULT=json.dumps(result), TEST_PROJECTION_EXIT=str(projection_exit))
        completed = subprocess.run([str(root / 'infra/scripts/run_market_pipeline.sh'), *arguments], env=env, capture_output=True, text=True)
        assert completed.returncode == (1 if result['status'] == 'failed' else projection_exit), completed.stderr
        records = [json.loads(row) for row in events.read_text().splitlines()]
        assert records[0]['step'] == 'pipeline'
        assert records[0]['registry_url'] == 'postgresql://registry@localhost/studio'
        catchups = [row for row in records if row['step'] == 'regime-catch-up']
        expected_catchup = arguments == ['sync'] and any(row['stage'] == 'numeric_catchup' and row['status'] == 'ready' for row in result.get('stages', []))
        assert len(catchups) == int(expected_catchup)
        if catchups:
            assert catchups[0]['args'] == ['kickstart', f'gui/{os.getuid()}/test.regime.auto.catch-up']
            assert records[1]['step'] == 'regime-catch-up'
        projections = [row for row in records if row['step'] == 'projection']
        assert [row['args'][row['args'].index('--channel')+1] for row in projections] == channels
        for row, ids in zip(projections, expected_ids):
            assert row['step'] == 'projection'
            assert [value for index,value in enumerate(row['args']) if index and row['args'][index-1] == '--instrument-id'] == ids
            assert '--require-downstream-success' in row['args'] and '--fail-on-item-failure' in row['args']
            assert row['args'][row['args'].index('--lock-wait-seconds') + 1] == '900'
            assert '--market-scope' not in row['args']
            assert row['watchlist'] == 'http://127.0.0.1:8100' and row['portfolio'] == 'http://127.0.0.1:8101'
    # data.env owns only DATA_ and INSTRUMENT_DATA_; other app namespaces remain rejected.
    with (config / 'data.env').open('a') as output:
        output.write('INVESTMENT_STUDIO_PORTFOLIO_DATABASE_URL=postgresql://other@localhost/studio\n')
    rejected = subprocess.run([str(root / 'infra/scripts/run_market_pipeline.sh'), 'publish'], env=env, capture_output=True, text=True)
    assert rejected.returncode != 0
    assert 'outside the allowed namespace: INVESTMENT_STUDIO_PORTFOLIO_DATABASE_URL' in rejected.stderr
