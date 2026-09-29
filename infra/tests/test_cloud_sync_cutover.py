import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import sync_cloud_to_local as sync


@pytest.fixture
def cutover(tmp_path, monkeypatch):
    directory = tmp_path / 'snapshot'
    directory.mkdir()
    config = {'database_url': 'postgresql://studio@127.0.0.1/studio',
              'admin_url': 'postgresql://admin@127.0.0.1/postgres', 'files': {}}
    for name in ('market', 'documents', 'research'):
        active = tmp_path / (name + '-active')
        active.mkdir()
        (active / 'value').write_text('original')
        staged = directory / name
        staged.mkdir()
        (staged / 'value').write_text('cloud')
        config['files'][name] = {'local': str(active), 'remote': '/unused'}
    databases = {'studio': 'original', 'incoming': 'cloud'}
    connections = {'studio': True, 'incoming': True}

    class Admin:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, query, params=None):
            if query.startswith('SELECT count'):
                return type('Result', (), {'fetchone': lambda self: (0,)})()
            if query == 'SELECT datname FROM pg_database':
                return [(name,) for name in databases]
            return []

    def rename(admin, old, new):
        assert new not in databases
        databases[new] = databases.pop(old)
        connections[new] = connections.pop(old)

    monkeypatch.setattr(sync.psycopg, 'connect', lambda *args, **kwargs: Admin())
    monkeypatch.setattr(sync, 'rename_database', rename)
    monkeypatch.setattr(sync, 'allow_connections', lambda admin, name, allow: connections.update({name: allow}))
    monkeypatch.setattr(sync, 'healthcheck', lambda config: None)
    monkeypatch.setattr(sync, 'refresh_local_credentials', lambda config, stage_name: None)
    return config, directory, databases, connections


def assert_original(config, directory, databases, connections):
    assert databases == {'studio': 'original', 'incoming': 'cloud'}
    assert connections['studio'] is True
    for item in config['files'].values():
        assert (Path(item['local']) / 'value').read_text() == 'original'
    assert json.loads((directory / 'cutover.json').read_text())['phase'] == 'rolled_back'


def test_partial_initial_stop_restores_the_complete_service_manifest(cutover, monkeypatch):
    config, directory, databases, connections = cutover
    actions = []

    def control(action, state):
        actions.append((action, state.name))
        if action == 'stop':
            assert json.loads((directory / 'cutover.json').read_text())['phase'] == 'stopping_services'
            raise RuntimeError('partial initial stop')

    monkeypatch.setattr(sync, 'control_services', control)
    with pytest.raises(RuntimeError, match='partial initial stop'):
        sync.publish(config, directory, 'incoming')
    assert actions == [('stop', 'services.txt'), ('start', 'services.txt')]
    assert_original(config, directory, databases, connections)


def test_partial_replacement_stop_does_not_skip_data_or_service_recovery(cutover, monkeypatch):
    config, directory, databases, connections = cutover
    actions = []

    def control(action, state):
        actions.append((action, state.name))
        if state.name == 'failed-services.txt':
            raise RuntimeError('partial replacement stop')

    monkeypatch.setattr(sync, 'control_services', control)
    monkeypatch.setattr(sync, 'healthcheck', lambda config: (_ for _ in ()).throw(RuntimeError('unhealthy')))
    with pytest.raises(RuntimeError, match='partial replacement stop'):
        sync.publish(config, directory, 'incoming')
    assert actions[-1] == ('start', 'services.txt')
    assert_original(config, directory, databases, connections)


def test_failed_original_service_recovery_blocks_following_sync(cutover, monkeypatch):
    config, directory, databases, connections = cutover

    def control(action, state):
        if action == 'start':
            raise RuntimeError('one service unavailable')

    monkeypatch.setattr(sync, 'control_services', control)
    with pytest.raises(RuntimeError, match='original service'):
        sync.publish(config, directory, 'incoming')
    assert databases['studio'] == 'original'
    journal = json.loads((directory / 'cutover.json').read_text())
    assert journal['phase'] == 'recovery_failed'
    assert 'original service' in journal['recovery_errors'][-1]
    with pytest.raises(RuntimeError, match='unfinished.*cutover.json'):
        sync.check_unfinished_cutovers(directory.parent)


def test_pending_cutover_is_blocked_before_download_even_when_not_due(cutover, monkeypatch):
    config, directory, _, _ = cutover
    sync.write_cutover_journal(directory / 'cutover.json', {}, 'switching_files')
    config['state_root'] = str(directory.parent)
    (directory.parent / 'last-success.json').write_text(json.dumps({'completed_at': sync.datetime.now(sync.timezone.utc).isoformat()}))
    monkeypatch.setattr(sync, 'load_config', lambda path: config)
    monkeypatch.setattr(sync, 'remote_snapshot', lambda *args: pytest.fail('must not download over a pending switch'))
    with pytest.raises(RuntimeError, match='unfinished'):
        sync.main(['--config', 'unused', '--if-due'])
    assert json.loads((directory / 'cutover.json').read_text())['phase'] == 'switching_files'


def test_success_records_recovery_locations_and_final_phase(cutover, monkeypatch):
    config, directory, databases, _ = cutover
    monkeypatch.setattr(sync, 'control_services', lambda *args: None)
    sync.publish(config, directory, 'incoming')
    journal = json.loads((directory / 'cutover.json').read_text())
    assert journal['phase'] == 'published'
    assert databases == {'studio_before_snapshot': 'original', 'studio': 'cloud'}
    assert journal['backup_database'] == 'studio_before_snapshot'
    assert journal['data_roots']['market']['backup'] == str(directory / 'market-before')
    sync.check_unfinished_cutovers(directory.parent)
