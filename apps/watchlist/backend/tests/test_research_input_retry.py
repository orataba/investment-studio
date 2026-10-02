import pytest


def test_portfolio_timeout_retries_without_freezing_invalid_inputs(client, monkeypatch, research_test_actor):
    from datetime import UTC, datetime, timedelta
    from watchlist_app.services import risk_officer, risk_review_state, research_runner, research_access
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.db.models.workbench import ResearchEntry
    monkeypatch.setattr(research_access, 'require_portfolio', lambda pid: {'portfolio_id':pid,'role':'reader'})
    monkeypatch.setattr(risk_review_state, 'current_scope', lambda *a: {})
    monkeypatch.setattr(risk_review_state, 'input_version', lambda *a: None)
    monkeypatch.setattr(risk_officer, 'external_json', lambda *a: (_ for _ in ()).throw(TimeoutError('timed out')))
    monkeypatch.setattr(research_runner, 'resolve_token', lambda *a: research_test_actor)
    monkeypatch.setattr(research_runner.subprocess, 'Popen', lambda *a, **k: pytest.fail('Must not start model without inputs'))
    days={'asset-a':'2026-10-02'}
    with get_session_factory()() as session:
        run, created=risk_officer.begin_run(session,portfolio_id='isolated-timeout-portfolio',scheduled_dates=days)
        assert created
        run_id=run.entry_id
    research_runner._run_analysis(run_id)
    with get_session_factory()() as session:
        run=session.get(ResearchEntry,run_id)
        assert run.status=='failed'
        assert run.context_json['risk_inputs']['scope_available'] is False
        assert run.context_json['runtime_error']['type']=='ResearchInputUnavailable'
        assert 'input_snapshot_cutoff' not in run.context_json
        assert research_runner.automatic_retry_due(run.context_json,run.completed_at,run.body,now=datetime.now(UTC)+timedelta(minutes=10))
        same, created=risk_officer.begin_run(session,portfolio_id='isolated-timeout-portfolio',scheduled_dates=days)
        assert same.entry_id==run_id and not created
