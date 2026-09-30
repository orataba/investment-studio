"""Durable automatic retry discovery must not become a new research dispatch."""
from watchlist_app.services import risk_review_state
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from sqlalchemy import func, select
import studio_identity
from studio_identity import Principal, principal_context

from watchlist_app.db.models import InstrumentAttributeValue, InstrumentDetail
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_runner as runner, sector_research as service

NOW = datetime(2026, 9, 27, 1, tzinfo=UTC)  # Sunday, outside the new-research calendar.
IID = 'retry-etf'
RID = 'saved-failed-run'


@pytest.fixture
def saved_retry(client, monkeypatch):
    from watchlist_app.services import shared_instrument_registry
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(service, 'datetime', Clock)
    monkeypatch.setattr(runner, 'datetime', Clock)
    monkeypatch.setattr(shared_instrument_registry, 'list_shared_active_instrument_ids', lambda **kw: [IID])
    monkeypatch.setattr(service, '_research_market', lambda session, iid: 'cn')
    actor = studio_identity.service_principal('watchlist')
    context = {'sector_run': True, 'instrument_ids': [IID], 'research_actor': actor.to_dict(),
        'cutoff': '2026-09-25T08:00:00+00:00', 'input_snapshot_cutoff': '2026-09-25T07:00:00+00:00',
        'submitted_draft': {'reviews': [{'instrument_id': IID, 'summary': 'Bound draft'}]},
        'instrument_inputs': [{'instrument_id': IID, 'retained_value': 7}],
        'execution': {'attempt': 1}, 'runtime_error': {'type': 'TimeoutExpired', 'stage': 'review', 'retryable': True}}
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id=IID, instrument_name='Retry ETF', instrument_type='etf',
            detail_view_type='etf', is_active=True, metadata_json={}))
        session.flush()
        session.add(InstrumentAttributeValue(instrument_id=IID, attribute_key='coverage_status', value_json='Invested', adopted_at=NOW-timedelta(days=3)))
        session.add(ResearchTopic(topic_id='instrument-events:' + IID, title='Research', team_id=actor.team_id,
            visibility='team', instrument_ids=[IID]))
        session.flush()
        session.add(ResearchEntry(entry_id=RID, topic_id='instrument-events:' + IID, team_id=actor.team_id,
            kind='analysis', title='Saved research', status='failed', created_at=NOW-timedelta(days=2),
            completed_at=NOW-timedelta(minutes=2), context_json=context))
        session.commit()
    with principal_context(actor):
        yield context


def test_worker_recovers_cross_day_weekend_original_once_without_new_generation(saved_retry, monkeypatch):
    from watchlist_app.services import research_workbench, risk_officer
    with get_session_factory()() as session:
        assert service.daily_review_groups(session, now=NOW) == []
        assert service.automatic_recovery_runs(session) == {IID: RID}
    monkeypatch.setattr(research_workbench, 'portfolio_options', lambda: {'portfolios': []})
    monkeypatch.setattr(risk_review_state, 'current_scope', lambda session, scope: {'instrument_ids': []})
    calls = []
    def analyze(run_id):
        calls.append(run_id)
        with get_session_factory()() as session:
            run = session.get(ResearchEntry, run_id)
            assert run.context_json['execution']['resume'] is True
            assert run.context_json['execution']['attempt'] == 1  # runner increments on actual launch
            for key in ('cutoff', 'input_snapshot_cutoff', 'instrument_inputs', 'submitted_draft'):
                assert run.context_json[key] == saved_retry[key]
            run.status = 'completed'
            session.commit()
    monkeypatch.setattr(runner, 'run_analysis', analyze)
    service.run_daily_reviews(Event())
    service.run_daily_reviews(Event())
    assert calls == [RID]
    with get_session_factory()() as session:
        assert session.scalar(select(func.count()).select_from(ResearchEntry).where(ResearchEntry.kind == 'analysis')) == 1


@pytest.mark.parametrize('change', ['user', 'other_service', 'exited', 'inactive', 'not_retryable',
    'exhausted', 'first_cooldown', 'second_cooldown', 'completed'])
def test_discovery_and_exact_recovery_recheck_current_eligibility(saved_retry, change):
    with get_session_factory()() as session:
        assert service.automatic_recovery_runs(session) == {IID: RID}
        run = session.get(ResearchEntry, RID)
        context = deepcopy(run.context_json)
        if change == 'user':
            context['research_actor'] = Principal('pm', 'PM', 'default').to_dict()
        elif change == 'other_service':
            context['research_actor']['service_id'] = 'different-service'
        elif change == 'exited':
            session.add(InstrumentAttributeValue(instrument_id=IID, attribute_key='coverage_status', value_json='Exited', adopted_at=NOW))
        elif change == 'inactive':
            session.get(InstrumentDetail, IID).is_active = False
        elif change == 'not_retryable':
            context['runtime_error']['retryable'] = False
        elif change == 'exhausted':
            context['execution']['attempt'] = 3
        elif change == 'first_cooldown':
            run.completed_at = NOW-timedelta(seconds=59)
        elif change == 'second_cooldown':
            context['execution']['attempt'] = 2
            run.completed_at = NOW-timedelta(seconds=299)
        elif change == 'completed':
            run.status = 'completed'
        run.context_json = context
        session.commit()
        assert service.automatic_recovery_runs(session) == {}
        if change == 'inactive':
            with pytest.raises(ValueError):
                service.begin_run(session, [IID], scheduled=True, recovery_run_id=RID)
        else:
            same, created = service.begin_run(session, [IID], scheduled=True, recovery_run_id=RID)
            assert same.entry_id == RID and not created
        assert session.scalar(select(func.count()).select_from(ResearchEntry).where(ResearchEntry.kind == 'analysis')) == 1
        assert session.get(ResearchEntry, RID).status == ('completed' if change == 'completed' else 'failed')


@pytest.mark.parametrize('topic', ['canonical', 'compiled'])
@pytest.mark.parametrize('status', ['queued', 'running', 'completed', 'failed'])
def test_newer_run_or_same_instrument_activity_blocks_old_candidate(saved_retry, topic, status):
    with get_session_factory()() as session:
        assert service.automatic_recovery_runs(session) == {IID: RID}
        topic_id = 'instrument-events:' + IID
        if topic == 'compiled':
            topic_id = 'instrument-events:compiled:default:' + IID
            session.add(ResearchTopic(topic_id=topic_id, title='Another research', team_id='default', visibility='team', instrument_ids=[IID]))
            session.flush()
        session.add(ResearchEntry(entry_id='newer-user-run', topic_id=topic_id, team_id='default', kind='analysis',
            title='Newer research', status=status, created_at=NOW-timedelta(minutes=1),
            completed_at=NOW if status in {'completed', 'failed'} else None,
            context_json={**saved_retry, 'research_actor': Principal('pm', 'PM', 'default').to_dict()}))
        session.commit()
        assert service.automatic_recovery_runs(session) == {}
        same, created = service.begin_run(session, [IID], scheduled=True, recovery_run_id=RID)
        assert same.entry_id == RID and not created and same.status == 'failed'
        assert session.scalar(select(func.count()).select_from(ResearchEntry).where(ResearchEntry.kind == 'analysis')) == 2


def test_exact_recovery_rechecks_authority_and_cannot_dispatch_twice(saved_retry):
    from fastapi import HTTPException
    actor = studio_identity.service_principal('watchlist')
    revoked = Principal(None, 'Revoked', 'default', kind='service', service_id=actor.service_id, scopes=[])
    with get_session_factory()() as session:
        with principal_context(revoked), pytest.raises(HTTPException) as denied:
            service.begin_run(session, [IID], scheduled=True, recovery_run_id=RID)
        assert denied.value.status_code == 403
        assert session.get(ResearchEntry, RID).status == 'failed'
    with get_session_factory()() as session:
        run, created = service.begin_run(session, [IID], scheduled=True, recovery_run_id=RID)
        assert created and run.status == 'queued'
    with get_session_factory()() as session:
        same, created = service.begin_run(session, [IID], scheduled=True, recovery_run_id=RID)
        assert same.entry_id == RID and not created and same.status == 'queued'


def test_worker_never_falls_back_to_new_run_when_discovered_retry_changes(saved_retry, monkeypatch):
    from watchlist_app.services import research_workbench, risk_officer
    original = service.automatic_recovery_runs
    calls = []
    def discover(session, **kwargs):
        result = original(session, **kwargs)
        if not calls:
            run = session.get(ResearchEntry, RID)
            run.context_json = {**run.context_json, 'runtime_error': {'type': 'Rejected', 'retryable': False}}
            session.commit()
        calls.append(result)
        return result
    monkeypatch.setattr(service, 'automatic_recovery_runs', discover)
    monkeypatch.setattr(service, 'daily_review_groups', lambda session: [[IID]])
    monkeypatch.setattr(research_workbench, 'portfolio_options', lambda: {'portfolios': []})
    monkeypatch.setattr(risk_review_state, 'current_scope', lambda session, scope: {'instrument_ids': []})
    monkeypatch.setattr(runner, 'run_analysis', lambda *args: pytest.fail('Stale retry must not dispatch any run'))
    service.run_daily_reviews(Event())
    assert calls[0] == {IID: RID} and all(value == {} for value in calls[1:])
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, RID).status == 'failed'
        assert session.scalar(select(func.count()).select_from(ResearchEntry).where(ResearchEntry.kind == 'analysis')) == 1


def test_retry_invalidated_by_active_research_keeps_dependent_risk_scope_pending(saved_retry, monkeypatch):
    from watchlist_app.services import research_workbench, risk_officer
    original = service.automatic_recovery_runs
    discovered = []
    def discover(session, **kwargs):
        result = original(session, **kwargs)
        if not discovered:
            session.add(ResearchEntry(entry_id='active-competitor', topic_id='instrument-events:' + IID,
                team_id='default', kind='analysis', title='Current researcher', status='running',
                created_at=NOW, context_json={**saved_retry, 'research_actor': Principal('pm', 'PM', 'default').to_dict()}))
            session.commit()
        discovered.append(result)
        return result
    monkeypatch.setattr(service, 'automatic_recovery_runs', discover)
    monkeypatch.setattr(service, 'daily_review_groups', lambda session: [[IID]])
    monkeypatch.setattr(research_workbench, 'portfolio_options', lambda: {'portfolios': [{'portfolio_id': 'held'}]})
    monkeypatch.setattr(risk_review_state, 'current_scope', lambda session, scope: {'instrument_ids': [IID]})
    monkeypatch.setattr(risk_officer, 'begin_run', lambda *args, **kw: pytest.fail('Competing active researcher must block dependent risk'))
    monkeypatch.setattr(runner, 'run_analysis', lambda *args: pytest.fail('No duplicate research dispatch'))
    service.run_daily_reviews(Event())
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, RID).status == 'failed'
        assert session.get(ResearchEntry, 'active-competitor').status == 'running'


def test_pending_theme_keeps_existing_automatic_scope_and_rechecks_its_removal(saved_retry):
    with get_session_factory()() as session:
        session.add(InstrumentAttributeValue(instrument_id=IID, attribute_key='coverage_status', value_json='Watch', adopted_at=NOW))
        theme = ResearchEntry(entry_id='pending-theme', topic_id='instrument-events:' + IID, team_id='default',
            kind='note', title='Pending theme', status='open', context_json={'role': 'research_theme',
            'instrument_id': IID, 'theme_status': 'active', 'baseline_status': 'pending'})
        session.add(theme)
        session.commit()
        assert IID not in service.active_research_ids(session)
        assert service.daily_review_groups(session, now=NOW) == [[IID]]
        assert service.automatic_recovery_runs(session) == {IID: RID}
        theme.context_json = {**theme.context_json, 'theme_status': 'closed'}
        session.commit()
        assert service.automatic_recovery_runs(session) == {}
        same, created = service.begin_run(session, [IID], scheduled=True, recovery_run_id=RID)
        assert same.status == 'failed' and not created


def test_ordinary_due_worker_does_not_revive_failure_superseded_by_compiled_user_run(saved_retry, monkeypatch):
    from watchlist_app.services import research_dossier, research_triggers, research_workbench, risk_officer
    with get_session_factory()() as session:
        old = session.get(ResearchEntry, RID)
        old.context_json = {**old.context_json, 'cutoff': (NOW-timedelta(minutes=4)).isoformat()}
        topic_id = 'instrument-events:compiled:default:' + IID
        session.add(ResearchTopic(topic_id=topic_id, title='Published user research', team_id='default', visibility='team', instrument_ids=[IID]))
        session.flush()
        session.add(ResearchEntry(entry_id='newer-compiled', topic_id=topic_id, kind='analysis', title='Newer report',
            team_id='default', status='completed', created_at=NOW-timedelta(minutes=1), completed_at=NOW,
            context_json={**saved_retry, 'research_actor': Principal('pm', 'PM', 'default').to_dict()}))
        session.commit()
        assert service.automatic_recovery_runs(session) == {}
    monkeypatch.setattr(service, '_research_due', lambda market, now: True)
    monkeypatch.setattr(research_dossier, 'read_dossier', lambda session, iid: {'instrument_id': iid})
    monkeypatch.setattr(research_triggers, 'research_trigger', lambda *args, **kw: None)
    monkeypatch.setattr(research_workbench, 'portfolio_options', lambda: {'portfolios': []})
    monkeypatch.setattr(risk_review_state, 'current_scope', lambda session, scope: {'instrument_ids': []})
    monkeypatch.setattr(runner, 'run_analysis', lambda *args: pytest.fail('Superseded original must not run via the ordinary daily branch'))
    service.run_daily_reviews(Event())
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, RID).status == 'failed'
        assert session.scalar(select(func.count()).select_from(ResearchEntry).where(ResearchEntry.kind == 'analysis')) == 2


def test_user_queued_run_is_not_dispatched_or_failed_by_daily_service(saved_retry, monkeypatch):
    from watchlist_app.services import research_workbench, risk_officer
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, RID)
        run.status = 'queued'
        run.context_json = {**run.context_json, 'research_actor': Principal('pm', 'PM', 'default').to_dict()}
        session.commit()
    monkeypatch.setattr(service, '_research_due', lambda market, now: True)
    monkeypatch.setattr(research_workbench, 'portfolio_options', lambda: {'portfolios': [{'portfolio_id': 'held'}]})
    monkeypatch.setattr(risk_review_state, 'current_scope', lambda session, scope: {'instrument_ids': [IID]})
    monkeypatch.setattr(risk_officer, 'begin_run', lambda *args, **kw: pytest.fail('User research is still pending'))
    monkeypatch.setattr(runner, 'run_analysis', lambda *args: pytest.fail('Worker must not authorize or fail a user task'))
    service.run_daily_reviews(Event())
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, RID).status == 'queued'


def test_unsupported_market_retry_does_not_block_other_recoverable_instruments(saved_retry, monkeypatch):
    from watchlist_app.services import shared_instrument_registry, research_workbench, risk_officer
    other = 'supported-retry'
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id=other, instrument_name='Supported ETF', instrument_type='etf',
            detail_view_type='etf', is_active=True, metadata_json={}))
        session.flush()
        session.add(InstrumentAttributeValue(instrument_id=other, attribute_key='coverage_status', value_json='Invested', adopted_at=NOW))
        session.add(ResearchTopic(topic_id='instrument-events:' + other, title='Other', team_id='default', visibility='team', instrument_ids=[other]))
        session.flush()
        session.add(ResearchEntry(entry_id='other-retry', topic_id='instrument-events:' + other, team_id='default',
            kind='analysis', title='Other retry', status='failed', created_at=NOW-timedelta(days=2), completed_at=NOW-timedelta(minutes=2),
            context_json={**saved_retry, 'instrument_ids': [other]}))
        session.commit()
    monkeypatch.setattr(shared_instrument_registry, 'list_shared_active_instrument_ids', lambda **kw: [IID, other])
    monkeypatch.setattr(service, '_research_market', lambda session, iid: None if iid == IID else 'cn')
    monkeypatch.setattr(research_workbench, 'portfolio_options', lambda: {'portfolios': []})
    monkeypatch.setattr(risk_review_state, 'current_scope', lambda session, scope: {'instrument_ids': []})
    calls = []
    monkeypatch.setattr(runner, 'run_analysis', calls.append)
    with get_session_factory()() as session:
        assert service.automatic_recovery_runs(session) == {other: 'other-retry'}
        same, created = service.begin_run(session, [IID], scheduled=True, recovery_run_id=RID)
        assert same.status == 'failed' and not created
    service.run_daily_reviews(Event())
    assert calls == ['other-retry']


def test_user_queued_risk_is_not_dispatched_or_failed_by_daily_service(saved_retry, monkeypatch):
    from watchlist_app.services import research_workbench, risk_officer
    with get_session_factory()() as session:
        session.get(ResearchEntry, RID).status = 'completed'
        session.add(ResearchTopic(topic_id='risk-scope', title='User risk', team_id='default', visibility='team', instrument_ids=[IID]))
        session.flush()
        session.add(ResearchEntry(entry_id='user-risk', topic_id='risk-scope', team_id='default', kind='analysis',
            title='User risk', status='queued', context_json={'risk_run': True,
            'research_actor': Principal('pm', 'PM', 'default').to_dict()}))
        session.commit()
    monkeypatch.setattr(service, 'daily_review_groups', lambda session: [[IID]])
    monkeypatch.setattr(service, 'begin_run', lambda session, ids, **kw: (session.get(ResearchEntry, RID), False))
    monkeypatch.setattr(research_workbench, 'portfolio_options', lambda: {'portfolios': [{'portfolio_id': 'held'}]})
    monkeypatch.setattr(risk_review_state, 'current_scope', lambda session, scope: {'instrument_ids': [IID]})
    monkeypatch.setattr(risk_officer, 'begin_run', lambda session, **kw: (session.get(ResearchEntry, 'user-risk'), False))
    monkeypatch.setattr(runner, 'run_analysis', lambda *args: pytest.fail('Daily service must leave a user risk task untouched'))
    service.run_daily_reviews(Event())
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, 'user-risk').status == 'queued'


def test_next_market_day_creates_service_run_without_adopting_old_user_failure(saved_retry, monkeypatch):
    from watchlist_app.services import research_workbench
    class Monday(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 28, 1, tzinfo=UTC)
    monkeypatch.setattr(service, 'datetime', Monday)
    monkeypatch.setattr(runner, 'datetime', Monday)
    monkeypatch.setattr(research_workbench, 'portfolio_options', lambda: {'portfolios': []})
    monkeypatch.setattr(risk_review_state, 'current_scope', lambda session, scope: {'instrument_ids': []})
    with get_session_factory()() as session:
        original = session.get(ResearchEntry, RID)
        original.context_json = {**original.context_json, 'research_actor': Principal('pm', 'PM', 'default').to_dict()}
        original_context = deepcopy(original.context_json)
        session.commit()
        assert service.automatic_recovery_runs(session) == {}
    calls = []
    def analyze(run_id):
        assert run_id != RID
        calls.append(run_id)
        with get_session_factory()() as session:
            run = session.get(ResearchEntry, run_id)
            assert run.context_json['research_actor']['kind'] == 'service'
            assert 'submitted_draft' not in run.context_json
            run.status = 'completed'
            session.commit()
    monkeypatch.setattr(runner, 'run_analysis', analyze)
    service.run_daily_reviews(Event())
    service.run_daily_reviews(Event())
    assert len(calls) == 1
    with get_session_factory()() as session:
        original = session.get(ResearchEntry, RID)
        assert original.status == 'failed' and original.context_json == original_context
