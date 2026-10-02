import importlib.util
from pathlib import Path
from unittest.mock import Mock

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
    monkeypatch.setattr(module.subprocess, 'run', Mock(return_value=Mock(returncode=0)))
    execute = Mock()
    monkeypatch.setattr(module, 'run', execute)

    module.manage('investments', 'restart')

    commands = [call.args[0] for call in execute.call_args_list]
    assert len(commands) == 4
    assert all(command[:3] == ['launchctl', 'kickstart', '-k'] for command in commands)
    assert {command[-1].rsplit('.', 1)[-1] for command in commands} == {
        'watchlist-api', 'watchlist-web', 'portfolio-api', 'portfolio-web',
    }
