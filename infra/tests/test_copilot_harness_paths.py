import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import pytest


ROOT = Path(__file__).resolve().parents[2]
LOADER = Path('infra/launchd/load_runtime_env.sh')


@pytest.mark.parametrize('app', ['portfolio', 'watchlist'])
def test_relocated_harness_uses_its_project_and_filters_backend_secrets(tmp_path, app):
    if app == 'portfolio':
        runner = Path('apps/portfolio/backend/scripts/run_portfolio_copilot_harness.sh')
        patch_path = Path('apps/portfolio/backend/config/portfolio_copilot_deepseek_harness.patch.yml')
        args = ['portfolio-test', 'batch-test']
        prefix = 'INVESTMENT_STUDIO_PORTFOLIO_COPILOT_'
        api_key = prefix + 'API_BASE_URL'
        api_url = 'http://127.0.0.1:8101/api'
    else:
        runner = Path('apps/watchlist/backend/scripts/run_research_harness.sh')
        patch_path = Path('apps/watchlist/backend/config/research_harness.patch.yml')
        args = ['run-test']
        prefix = 'INVESTMENT_STUDIO_RESEARCH_'
        api_key = 'INVESTMENT_STUDIO_WATCHLIST_RESEARCH_API_BASE_URL'
        api_url = 'http://127.0.0.1:8100/api'
    project = tmp_path / 'relocated studio'
    for relative in (runner, patch_path, LOADER):
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)

    secret = tmp_path / 'portfolio-copilot.env'
    secret.write_text('DEEPSEEK_API_KEY=test-key\n', encoding='utf-8')
    secret.chmod(0o600)
    pnpm = tmp_path / 'pnpm'
    pnpm.write_text(
        f'#!{sys.executable}\n'
        'import json, os, sys\n'
        'print(json.dumps({"env": dict(os.environ), "args": sys.argv[1:]}))\n',
        encoding='utf-8',
    )
    pnpm.chmod(0o700)
    env = {
        **os.environ,
        'INVESTMENT_STUDIO_PORTFOLIO_COPILOT_PNPM': str(pnpm),
        'INVESTMENT_STUDIO_PORTFOLIO_COPILOT_ENV_FILE': str(secret),
        'INVESTMENT_STUDIO_PORTFOLIO_COPILOT_DSH_HOME': str(tmp_path / 'harness'),
        'INVESTMENT_STUDIO_PORTFOLIO_COPILOT_PROJECT_ROOT': '/obsolete/project',
        api_key: api_url,
        'INVESTMENT_STUDIO_PORTFOLIO_DATABASE_URL': 'private-database',
        'FMP_API_KEY': 'private-market-key',
    }
    result = subprocess.run(
        ['bash', str(project / runner), *args],
        env=env, cwd=tmp_path, check=True, capture_output=True, text=True,
    )
    captured = json.loads(result.stdout)
    runtime_env = captured['env']
    assert runtime_env[prefix + 'PROJECT_ROOT'] == str(project)
    assert runtime_env[prefix + 'API_BASE_URL'] == api_url
    if app == 'portfolio':
        assert runtime_env[prefix + 'PORTFOLIO_ID'] == 'portfolio-test'
        assert runtime_env[prefix + 'BATCH_ID'] == 'batch-test'
    else:
        assert runtime_env[prefix + 'RUN_ID'] == 'run-test'
    assert runtime_env['DEEPSEEK_API_KEY'] == 'test-key'
    assert 'INVESTMENT_STUDIO_PORTFOLIO_DATABASE_URL' not in runtime_env
    assert 'FMP_API_KEY' not in runtime_env
    assert str(project / patch_path) in captured['args']
    patch = (project / patch_path).read_text(encoding='utf-8')
    assert f"command: !!js process.env.{prefix}PROJECT_ROOT + '/.venv/bin/python'" in patch
    assert f"cwd: !!js process.env.{prefix}PROJECT_ROOT + '/apps/{app}/backend'" in patch
