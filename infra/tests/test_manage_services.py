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


def test_investments_start_schedules_data_without_forcing_refresh(monkeypatch):
    monkeypatch.setattr(module.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 1000)
    execute = Mock()
    monkeypatch.setattr(module, 'run', execute)
    module.manage('investments', 'start')
    command = execute.call_args.args[0]
    assert 'investment-studio-market-data-refresh.timer' in command
    assert 'investment-studio-market-data-refresh.service' not in command
    assert 'investment-studio-us-reference-data-refresh.timer' in command
    assert 'investment-studio-us-reference-data-refresh.service' not in command
    assert 'investment-studio-home-api.service' not in command


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
