import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import pytest


ROOT = Path(__file__).resolve().parents[2]
LOADER = Path('infra/launchd/load_runtime_env.sh')
PROVIDER_PATCH = Path('infra/config/deepseek_harness.patch.yml')
HARNESS_RUNNER = Path('infra/harness/run.sh')
HARNESS_ENTRY = Path('infra/harness/node_modules/@deepseek-ai/dsh/lib/bin.js')


def test_missing_installed_runtime_stops_without_resolving_packages(tmp_path):
    launcher = tmp_path / HARNESS_RUNNER
    launcher.parent.mkdir(parents=True)
    shutil.copy2(ROOT / HARNESS_RUNNER, launcher)
    invoked = tmp_path / 'package-manager-invoked'
    pnpm = tmp_path / 'pnpm'
    pnpm.write_text(f'#!{sys.executable}\nfrom pathlib import Path\nPath({str(invoked)!r}).touch()\n')
    pnpm.chmod(0o700)
    result = subprocess.run(
        [str(launcher), '--version'], check=False, capture_output=True, text=True,
        env={**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH']},
    )
    assert result.returncode == 78
    assert 'infra/harness/install.sh' in result.stderr
    assert not invoked.exists()


@pytest.mark.parametrize('app,task_mode,reviewer_fails,configured_model', [
    ('portfolio', None, False, None), ('portfolio', None, False, 'test-vision-model'),
    ('watchlist', None, False, None), ('watchlist', 'sector', False, 'deepseek-v4.1-flash'),
    ('watchlist', 'risk', False, None), ('watchlist', 'sector', True, None),
    ('briefing', None, False, None), ('briefing', 'review', False, 'test-configured-model'),
])
def test_relocated_harness_uses_its_project_and_filters_backend_secrets(tmp_path, app, task_mode, reviewer_fails, configured_model):
    if app == 'portfolio':
        runner = Path('apps/portfolio/backend/scripts/run_portfolio_copilot_harness.sh')
        patch_path = Path('apps/portfolio/backend/config/portfolio_copilot_deepseek_harness.patch.yml')
        args = ['portfolio-test', 'batch-test']
        prefix = 'INVESTMENT_STUDIO_PORTFOLIO_COPILOT_'
        api_key = prefix + 'API_BASE_URL'
        api_url = 'http://127.0.0.1:8101/api'
    elif app == 'watchlist':
        runner = Path('apps/watchlist/backend/scripts/run_research_harness.sh')
        patch_path = Path(f'apps/watchlist/backend/config/{task_mode or "research"}_harness.patch.yml')
        args = ['run-test', *([task_mode] if task_mode else [])]
        prefix = 'INVESTMENT_STUDIO_RESEARCH_'
        api_key = 'INVESTMENT_STUDIO_WATCHLIST_RESEARCH_API_BASE_URL'
        api_url = 'http://127.0.0.1:8100/api'
    else:
        runner = Path('apps/briefing/backend/scripts/run_briefing_harness.sh')
        patch_path = Path('apps/briefing/backend/config/briefing_harness.patch.yml')
        args = ['report-test', task_mode or 'write']
        prefix = 'INVESTMENT_STUDIO_BRIEFING_'
        api_key = prefix + 'API_BASE_URL'
        api_url = 'http://127.0.0.1:8110/api/briefing'
    project = tmp_path / 'relocated studio'
    for relative in (runner, patch_path, LOADER, PROVIDER_PATCH, HARNESS_RUNNER):
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)

    if app == 'watchlist':
        core = Path('apps/watchlist/backend/config/research_core.md')
        shutil.copy2(ROOT / core, project / core)
        # The review process consumes the harness reply. This relocation test inspects
        # the launch environment only; publication is covered by Watchlist API tests.
        python = project / '.venv/bin/python'
        python.parent.mkdir(parents=True)
        python.write_text(
            f'#!{sys.executable}\nimport json, sys\n'
            'print("FACT_REVIEW_ARGS " + json.dumps(sys.argv[1:]), file=sys.stderr)\n'
            + ('raise SystemExit(78)\n' if reviewer_fails else 'sys.stdout.write(sys.stdin.read())\n')
        )
        python.chmod(0o700)
    secret = tmp_path / 'portfolio-copilot.env'
    secret.write_text('DEEPSEEK_API_KEY=test-key\nDEEPSEEK_BASE_URL=https://provider.example/v1\n'
        'DEEPSEEK_SEARCH_URL=https://provider.example/v1/messages\n' +
        (f'INVESTMENT_STUDIO_PORTFOLIO_COPILOT_MODEL_NAME={configured_model}\n' if configured_model else ''), encoding='utf-8')
    secret.chmod(0o600)
    entry = project / HARNESS_ENTRY
    entry.parent.mkdir(parents=True)
    entry.write_text('// provisioned fixture')
    node = tmp_path / 'node'
    node.write_text(
        f'#!{sys.executable}\n'
        'import json, os, sys\n'
        'print(json.dumps({"env": dict(os.environ), "args": sys.argv[1:]}))\n',
        encoding='utf-8',
    )
    node.chmod(0o700)
    env = {
        **os.environ,
        'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
        'INVESTMENT_STUDIO_PORTFOLIO_COPILOT_ENV_FILE': str(secret),
        'INVESTMENT_STUDIO_PORTFOLIO_COPILOT_DSH_HOME': str(tmp_path / 'harness'),
        'INVESTMENT_STUDIO_PORTFOLIO_COPILOT_PROJECT_ROOT': '/obsolete/project',
        api_key: api_url,
        prefix + 'RUN_TOKEN': 'scoped-task-token',
        'INVESTMENT_STUDIO_AUTH_SERVICE_TOKEN': 'private-backend-token',
        'INVESTMENT_STUDIO_AUTH_SERVICE_TOKEN_FILE': '/private/backend-token-file',
        'INVESTMENT_STUDIO_PORTFOLIO_DATABASE_URL': 'private-database',
        'FMP_API_KEY': 'private-market-key',
    }
    env.pop('INVESTMENT_STUDIO_PORTFOLIO_COPILOT_MODEL_NAME', None)
    result = subprocess.run(
        ['/bin/bash', str(project / runner), *args],
        env=env, cwd=tmp_path, check=False, capture_output=True, text=True,
    )
    if reviewer_fails:
        assert result.returncode == 78
        markers = [json.loads(line.removeprefix('SECTOR_REVIEW_ERROR ')) for line in result.stderr.splitlines()
                   if line.startswith('SECTOR_REVIEW_ERROR ')]
        assert markers == [{'type': 'FactReviewProcessExit', 'summary': '本地事实核证进程退出，未生成核证结果；请检查研究运行环境。'}]
        return
    assert result.returncode == 0, result.stderr
    captured = json.loads(result.stdout)
    runtime_env = captured['env']
    assert runtime_env[prefix + 'PROJECT_ROOT'] == str(project)
    assert runtime_env[prefix + 'API_BASE_URL'] == api_url
    if app == 'portfolio':
        assert runtime_env[prefix + 'PORTFOLIO_ID'] == 'portfolio-test'
        assert runtime_env[prefix + 'BATCH_ID'] == 'batch-test'
        assert runtime_env[prefix + 'MODEL_NAME'] == (configured_model or 'deepseek-v4.1-flash')
    elif app == 'watchlist':
        assert runtime_env[prefix + 'RUN_ID'] == 'run-test'
        assert runtime_env['INVESTMENT_STUDIO_PORTFOLIO_COPILOT_MODEL_NAME'] == (configured_model or 'deepseek-v4.1-flash')
        assert runtime_env['DEEPSEEK_SEARCH_URL'] == 'https://provider.example/v1/messages'
        assert runtime_env['INVESTMENT_STUDIO_RESEARCH_PERSONA'] == (ROOT / core).read_text().rstrip('\n')
        reviewer_args = [json.loads(line.removeprefix('FACT_REVIEW_ARGS ')) for line in result.stderr.splitlines()
                         if line.startswith('FACT_REVIEW_ARGS ')]
        assert reviewer_args == ([] if task_mode == 'risk' else [
            ['-m', 'watchlist_app.services.sector_fact_review', *([] if task_mode == 'sector' else ['--conversation'])]])
    else:
        assert runtime_env[prefix + 'REPORT_ID'] == 'report-test'
        assert runtime_env[prefix + 'HARNESS_MODE'] == (task_mode or 'write')
        assert runtime_env['INVESTMENT_STUDIO_PORTFOLIO_COPILOT_MODEL_NAME'] == (configured_model or 'deepseek-v4.1-flash')
    assert runtime_env[prefix + 'RUN_TOKEN'] == 'scoped-task-token'
    assert 'INVESTMENT_STUDIO_AUTH_SERVICE_TOKEN' not in runtime_env
    assert 'INVESTMENT_STUDIO_AUTH_SERVICE_TOKEN_FILE' not in runtime_env
    assert runtime_env['DEEPSEEK_API_KEY'] == 'test-key'
    assert runtime_env['DEEPSEEK_BASE_URL'] == 'https://provider.example/v1'
    assert 'INVESTMENT_STUDIO_PORTFOLIO_DATABASE_URL' not in runtime_env
    assert 'FMP_API_KEY' not in runtime_env
    assert captured['args'][0] == str(project / HARNESS_ENTRY)
    assert 'dlx' not in captured['args']
    assert str(project / patch_path) in captured['args']
    assert captured['args'].index(str(project / PROVIDER_PATCH)) < captured['args'].index(str(project / patch_path))
    patch = (project / patch_path).read_text(encoding='utf-8')
    assert f"command: !!js process.env.{prefix}PROJECT_ROOT + '/.venv/bin/python'" in patch
    assert f"cwd: !!js process.env.{prefix}PROJECT_ROOT + '/apps/{app}/backend'" in patch
