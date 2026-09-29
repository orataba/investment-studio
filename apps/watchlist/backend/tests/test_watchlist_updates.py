from fastapi.testclient import TestClient
from sqlalchemy import select


def test_adding_during_running_calculation_commits_membership_and_selected_status(client: TestClient):
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.db.models.recalc import RecalcJob
    from watchlist_app.services.watchlist_updates import jobs
    from watchlist_app.services.recalc_job_ids import make_recalc_dedupe_key

    watchlist_id = client.post('/api/watchlists', json={'name': 'Concurrent add'}).json()['watchlist_id']
    # Ensure a local identity before simulating a worker with a valid lease.
    assert client.get('/api/watchlists').status_code == 200
    with get_session_factory()() as session:
        jobs.create(session, recalc_job_id='active-test-job', job_type='all', instrument_id='sxv264',
            trigger_type='test', trigger_ref_type=None, trigger_ref_id=None, job_status='running',
            priority=100, dedupe_key=make_recalc_dedupe_key(job_type='all', instrument_id='sxv264'), payload_json={})
        session.commit()

    response = client.post(f'/api/watchlists/{watchlist_id}/items', json={'instrument_ids': ['sxv264'], 'coverage_status': 'Invested'})
    assert response.status_code == 200
    assert response.json()['accepted_count'] == 1
    assert response.json()['pending_recalc_instrument_ids'] == ['sxv264']
    assert client.get('/api/instrument-attributes/instruments/sxv264').json()['values']['coverage_status'] == 'Invested'
    with get_session_factory()() as session:
        records = session.scalars(select(RecalcJob).where(RecalcJob.instrument_id == 'sxv264')).all()
        assert sum(record.job_status == 'running' for record in records) == 1
        assert sum(record.job_status == 'queued' for record in records) == 1

    # Existing membership is still allowed to update the global status and
    # does not enqueue another calculation for an attribute-only change.
    response = client.post(f'/api/watchlists/{watchlist_id}/items', json={'instrument_ids': ['sxv264'], 'coverage_status': 'Proposed'})
    assert response.status_code == 200
    assert response.json()['accepted_count'] == 0
    assert response.json()['pending_recalc_instrument_ids'] == []
    assert client.get('/api/instrument-attributes/instruments/sxv264').json()['values']['coverage_status'] == 'Proposed'
    with get_session_factory()() as session:
        assert len(session.scalars(select(RecalcJob).where(RecalcJob.instrument_id == 'sxv264')).all()) == 2


def test_bulk_status_is_atomic_and_global_across_lists(client: TestClient, monkeypatch):
    from watchlist_app.api.routes.attributes import canonical_recalc_service
    def forbidden(*args, **kwargs):
        raise AssertionError('Manual status must not recalculate market history')
    monkeypatch.setattr(canonical_recalc_service, 'execute_recalc', forbidden)
    first = client.post('/api/watchlists', json={'name': 'First status list'}).json()['watchlist_id']
    second = client.post('/api/watchlists', json={'name': 'Second status list'}).json()['watchlist_id']
    for watchlist_id in [first, second]:
        assert client.post(f'/api/watchlists/{watchlist_id}/items', json={'instrument_ids': ['sxv264']}).status_code == 200
    rejected = client.post(f'/api/watchlists/{first}/items/coverage-status', json={'instrument_ids': ['sxv264', 'missing'], 'coverage_status': 'Invested'})
    assert rejected.status_code == 422
    response = client.post(f'/api/watchlists/{first}/items/coverage-status', json={'instrument_ids': ['sxv264'], 'coverage_status': 'Proposed'})
    assert response.status_code == 200
    assert response.json()['updated_count'] == 1
    for watchlist_id in [first, second]:
        query = client.post('/api/screener/query', json={'watchlist_id': watchlist_id,
            'selected_fields': ['instrument_name', 'attr.coverage_status'],
            'filters': {'attr.coverage_status': ['Proposed']},
            'sort': [{'field': 'attr.coverage_status', 'direction': 'asc'}], 'group_by': 'attr.coverage_status'})
        assert query.status_code == 200
        assert query.json()['total_rows'] == 1
        assert query.json()['rows'][0]['attr.coverage_status'] == 'Proposed'
    rejected = client.post(f'/api/watchlists/{first}/items', json={'instrument_ids': ['sxv264'], 'coverage_status': 'Invalid'})
    assert rejected.status_code == 422

    cleared = client.post('/api/instrument-attributes/instruments/sxv264', json={
        'values': [{'attribute_key': 'coverage_status', 'value': None}]})
    assert cleared.status_code == 200
    assert cleared.json()['recalculated'] is False
    query = client.post('/api/screener/query', json={'watchlist_id': first,
        'selected_fields': ['instrument_name', 'attr.coverage_status'],
        'filters': {'attr.coverage_status': ['Proposed']}, 'group_by': 'none'})
    assert query.json()['total_rows'] == 0
