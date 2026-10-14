import importlib.util
from pathlib import Path
import plistlib
from unittest.mock import Mock
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/manage_services.py'
spec = importlib.util.spec_from_file_location('manage_services', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_home_restart_does_not_touch_data_or_regime(monkeypatch):
    monkeypatch.setattr(module.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 1000)
    execute = Mock()
    monkeypatch.setattr(module, 'run', execute)
    module.manage('home', 'restart')
    execute.assert_called_once_with([
        'systemctl', '--user', '--no-pager', 'restart',
        'investment-studio-home-api.service', 'investment-studio-home-web.service',
    ], check=True)


def test_database_restore_stops_every_generated_market_writer(tmp_path):
    import os
    import subprocess
    root = module.ROOT
    pipeline_spec = importlib.util.spec_from_file_location('market_pipeline', root / 'infra/scripts/install_market_pipeline.py')
    pipeline = importlib.util.module_from_spec(pipeline_spec)
    pipeline_spec.loader.exec_module(pipeline)
    generated = set(pipeline.definitions('systemd', 'collector', root, tmp_path, Path('/usr/bin/python3'), tmp_path))
    # Evaluate the restore script's actual inventory, stopping before any DB or
    # service operation. A newly installed schedule must be inside this boundary.
    restore = (root / 'infra/postgres/restore_project_dump.sh').read_text().split('PSQL_BIN=""', 1)[0]
    script = tmp_path / 'restore-inventory.sh'
    script.write_text(restore + '\nprintf "%s\\n" "${SYSTEMD_UNITS[@]}"\n')
    result = subprocess.run(['bash', str(script), '/unused.pgdump'],
        env={**os.environ, 'PROJECT_ROOT': str(root)}, capture_output=True, text=True, check=True)
    assert generated <= set(result.stdout.splitlines())
    assert 'investment-studio-market-crypto.timer' in generated
    managed = {f'investment-studio-{name}.{kind}' for name in module.GROUPS['market'] for kind in ('timer', 'service')}
    assert generated == managed


def test_investments_start_schedules_data_without_forcing_refresh(monkeypatch):
    monkeypatch.setattr(module.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(module, 'installed_market_role', lambda: 'collector')
    execute = Mock()
    monkeypatch.setattr(module, 'run', execute)
    module.manage('investments', 'start')
    command = execute.call_args.args[0]
    assert 'investment-studio-market-data-refresh.timer' in command
    assert 'investment-studio-market-data-refresh.service' not in command
    assert 'investment-studio-us-reference-data-refresh.timer' not in command
    assert 'investment-studio-cn-hk-reference-data-refresh.timer' in command
    assert 'investment-studio-us-reference-data-refresh.service' not in command
    assert 'investment-studio-home-api.service' not in command
    assert 'investment-studio-us-market-data-refresh.timer' not in command
    assert 'investment-studio-us-market-data-refresh.service' not in command


@pytest.mark.parametrize('role,has_us_timer', [('collector', False), ('replica', True)])
def test_linux_investments_uses_installed_role_for_us_schedule(monkeypatch, role, has_us_timer):
    monkeypatch.setattr(module.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 1000)
    query = Mock(return_value=Mock(stdout=f'ENV_ROOT=/private/config INVESTMENT_STUDIO_MARKET_ROLE={role}'))
    monkeypatch.setattr(module.subprocess, 'run', query)
    execute = Mock()
    monkeypatch.setattr(module, 'run', execute)
    module.manage('investments', 'restart')
    query.assert_called_once_with([
        'systemctl', '--user', '--no-pager', 'show', 'investment-studio-market-sync.service',
        '--property=Environment', '--value',
    ], capture_output=True, text=True, check=True, timeout=5)
    command = execute.call_args.args[0]
    assert ('investment-studio-us-market-data-refresh.timer' in command) == has_us_timer
    assert 'investment-studio-us-market-data-refresh.service' not in command


def test_linux_investments_does_not_guess_an_unknown_market_role(monkeypatch):
    monkeypatch.setattr(module.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(module.subprocess, 'run', Mock(return_value=Mock(stdout='ENV_ROOT=/private/config')))
    execute = Mock()
    monkeypatch.setattr(module, 'run', execute)
    with pytest.raises(RuntimeError, match='no valid collector/replica role'):
        module.manage('investments', 'start')
    execute.assert_not_called()


def test_market_and_briefing_stop_writers_but_start_only_schedules(monkeypatch):
    monkeypatch.setattr(module.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 1000)
    execute = Mock()
    monkeypatch.setattr(module, 'run', execute)
    for group in ('market', 'briefing'):
        names = module.GROUPS[group] if group == 'market' else ('briefing-daily', 'briefing-weekly')
        module.manage(group, 'stop')
        stopped = execute.call_args.args[0]
        module.manage(group, 'start')
        started = execute.call_args.args[0]
        for name in names:
            assert f'investment-studio-{name}.service' in stopped
            assert f'investment-studio-{name}.timer' in stopped
            assert f'investment-studio-{name}.service' not in started
            assert f'investment-studio-{name}.timer' in started


def test_mac_market_controls_sync_and_does_not_kickstart_a_loaded_schedule(monkeypatch, tmp_path):
    monkeypatch.setenv('ENV_ROOT', str(tmp_path))
    monkeypatch.setattr(module.platform, 'system', lambda: 'Darwin')
    query = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(module.subprocess, 'run', query)
    execute = Mock()
    monkeypatch.setattr(module, 'run', execute)
    module.manage('market', 'restart')
    execute.assert_not_called()
    assert query.call_args.args[0][-1].endswith('.market-sync')
    module.manage('market', 'stop')
    assert execute.call_args.args[0][1] == 'bootout'


def test_mac_full_cloud_sync_replaces_public_only_schedule(monkeypatch, tmp_path):
    monkeypatch.setenv('ENV_ROOT', str(tmp_path))
    (tmp_path / 'cloud-sync.json').write_text('{}')
    monkeypatch.setattr(module.platform, 'system', lambda: 'Darwin')
    query = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(module.subprocess, 'run', query)
    execute = Mock()
    monkeypatch.setattr(module, 'run', execute)
    module.manage('market', 'restart')
    assert query.call_args.args[0][-1].endswith('.cloud-sync')
    execute.assert_not_called()


def test_regime_mac_delegates_its_own_control(monkeypatch):
    monkeypatch.setattr(module.platform, 'system', lambda: 'Darwin')
    execute = Mock()
    monkeypatch.setattr(module, 'run', execute)
    module.manage('regime', 'status')
    execute.assert_called_once_with([str(module.ROOT / 'apps/regime/deploy/launchd/install.sh'), 'status'])


def test_mac_status_reports_failed_restart_state(monkeypatch, capsys):
    monkeypatch.setattr(module.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(module.subprocess, 'run', Mock(return_value=Mock(
        returncode=0, stdout='service = {\n\tstate = spawn scheduled\n\tlast exit code = 3\n}'
    )))
    module.manage('home', 'status')
    assert 'home-api: spawn scheduled' in capsys.readouterr().out


def test_mac_restart_keeps_loaded_services_registered_and_preserves_data_schedule(monkeypatch):
    monkeypatch.setattr(module.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(module, 'local_research_worker_enabled', lambda: True)
    monkeypatch.setattr(module.subprocess, 'run', Mock(return_value=Mock(returncode=0)))
    execute = Mock()
    monkeypatch.setattr(module, 'run', execute)

    module.manage('investments', 'restart')

    commands = [call.args[0] for call in execute.call_args_list]
    assert len(commands) == 5
    assert all(command[:3] == ['launchctl', 'kickstart', '-k'] for command in commands)
    assert {command[-1].rsplit('.', 1)[-1] for command in commands} == {
        'watchlist-api', 'watchlist-web', 'research-worker', 'portfolio-api', 'portfolio-web',
    }


def worker_configuration(tmp_path, monkeypatch, value, *, overrides=None):
    monkeypatch.setattr(module.Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(module.platform, 'system', lambda: 'Darwin')
    environment = tmp_path / 'private-runtime'
    environment.mkdir(mode=0o700)
    env_file = environment / 'watchlist.env'
    env_file.write_text('' if value is None else f'INVESTMENT_STUDIO_WATCHLIST_RESEARCH_WORKER_ENABLED={value}\n')
    env_file.chmod(0o600)
    plist = tmp_path / 'Library/LaunchAgents/com.orataba.investment-studio.research-worker.plist'
    plist.parent.mkdir(parents=True)
    plist.write_bytes(plistlib.dumps({
        'ProgramArguments': ['run_local_service.sh', 'research-worker', str(module.ROOT),
                             '/configured/python', '/configured/node', str(environment)],
        'EnvironmentVariables': overrides or {},
    }))
    return env_file


@pytest.mark.parametrize('action', ['start', 'restart'])
@pytest.mark.parametrize('loaded', [True, False])
def test_mac_group_preserves_worker_disabled_by_actual_runtime_configuration(tmp_path, monkeypatch, action, loaded):
    env_file = worker_configuration(tmp_path, monkeypatch, '"FaLsE"')
    marker = tmp_path / 'must-not-execute'
    with env_file.open('a') as stream:
        stream.write(f'INVESTMENT_STUDIO_WATCHLIST_UNUSED=$(touch "{marker}")\n')
    monkeypatch.setenv('INVESTMENT_STUDIO_WATCHLIST_RESEARCH_WORKER_ENABLED', 'true')
    original = module.subprocess.run
    def query(command, **kwargs):
        return Mock(returncode=0 if loaded else 1) if command[0] == 'launchctl' else original(command, **kwargs)
    monkeypatch.setattr(module.subprocess, 'run', query)
    execute = Mock()
    monkeypatch.setattr(module, 'run', execute)
    module.manage('investments', action)
    commands = [call.args[0] for call in execute.call_args_list]
    assert all('research-worker' not in ' '.join(command) for command in commands)
    if action == 'restart' or not loaded:
        assert any('watchlist-api' in ' '.join(command) for command in commands)
    else:
        assert not commands
    assert not marker.exists()


@pytest.mark.parametrize('value,override,enabled', [
    ('off', None, False), ('0', None, False), ('no', None, False),
    ('true', None, True), (None, None, True), ('false', 'true', True),
])
def test_worker_flag_uses_runner_boolean_and_installed_environment_contract(tmp_path, monkeypatch, value, override, enabled):
    overrides = {'INVESTMENT_STUDIO_WATCHLIST_RESEARCH_WORKER_ENABLED': override} if override else None
    worker_configuration(tmp_path, monkeypatch, value, overrides=overrides)
    assert module.local_research_worker_enabled() is enabled


def test_mac_invalid_worker_flag_fails_before_any_service_action(tmp_path, monkeypatch):
    worker_configuration(tmp_path, monkeypatch, 'unknown')
    execute = Mock()
    monkeypatch.setattr(module, 'run', execute)
    with pytest.raises(ValueError, match='invalid enabled setting'):
        module.manage('investments', 'restart')
    execute.assert_not_called()


def test_registered_us_hk_use_actual_session_conditions_not_fixed_beijing_us_clock(tmp_path):
    spec = importlib.util.spec_from_file_location('market_pipeline', module.ROOT / 'infra/scripts/install_market_pipeline.py')
    pipeline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pipeline)
    result = pipeline.definitions('systemd', 'collector', module.ROOT, tmp_path, Path('/usr/bin/python3'), tmp_path)
    for market, tick in [('us', '*:00 America/New_York'), ('hk', '*:30 Asia/Hong_Kong')]:
        stem = 'investment-studio-market-registered-prices-' + market
        assert 'OnCalendar=*-*-* ' + tick in result[stem + '.timer'].decode()
        service = result[stem + '.service'].decode()
        assert 'StartLimitIntervalSec=0' in service
        assert 'RestartForceExitStatus' not in service
        assert 'ExecCondition=' in service
        assert '--market-scope ' + market + ' --collection' in service
        assert 'registered-prices --market ' + market in service
    daily = result['investment-studio-market-daily.service'].decode()
    assert 'RestartForceExitStatus=75' in daily and 'StartLimitBurst=4' in daily
