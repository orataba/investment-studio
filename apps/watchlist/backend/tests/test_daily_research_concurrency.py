from watchlist_app.services import risk_review_state
from threading import Event, Lock, Thread
from types import SimpleNamespace
import pytest

from watchlist_app.db.models import InstrumentDetail
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_runner, research_workbench, risk_officer, sector_research as service


def daily_scope(client, monkeypatch, *, members=6):
    ids = [f"daily-{number}" for number in range(8)]
    with get_session_factory()() as session:
        session.add_all(InstrumentDetail(instrument_id=iid, instrument_type="equity", detail_view_type="equity",
            instrument_name=iid, is_active=True, metadata_json={}) for iid in ids)
        session.commit()
    monkeypatch.setattr(service, "daily_review_groups", lambda session: [[iid] for iid in ids])
    monkeypatch.setattr(service, "_research_market", lambda session, iid: "cn")
    monkeypatch.setattr(research_workbench, "portfolio_options", lambda: {"portfolios": [{"portfolio_id": "held"}]})
    monkeypatch.setattr(risk_review_state, "current_scope", lambda session, scope: {"instrument_ids": ids[:members] if scope.get("portfolio_id") == "held" else []})
    monkeypatch.setattr(risk_officer, "read_snapshot", lambda *args, **kwargs: pytest.fail("Scheduler must use the lightweight scope directory"))
    return ids


@pytest.mark.parametrize("concurrency", [1, 4])
def test_configured_workers_finish_due_instruments_in_fair_order_before_scope_risk(client, monkeypatch, concurrency):
    from watchlist_app.core.settings import get_settings
    monkeypatch.setattr(get_settings(), "research_worker_concurrency", concurrency)
    ids = daily_scope(client, monkeypatch)
    release, four_started, stop = Event(), Event(), Event()
    lock = Lock()
    started, finished, actions, active = [], set(), [], []

    def analyze(run_id):
        with get_session_factory()() as session:
            run = session.get(ResearchEntry, run_id)
            iid = run.context_json["instrument_ids"][0]
        with lock:
            started.append(iid)
            active.append(len(started) - len(finished))
            if len(started) == concurrency:
                four_started.set()
        assert release.wait(5)
        with get_session_factory()() as session:
            session.get(ResearchEntry, run_id).status = "completed"
            session.commit()
        with lock:
            finished.add(iid)
            actions.append(iid)

    def risk_run(session, **scope):
        assert set(ids[:6]).issubset(finished)
        assert set(ids).issubset(finished)
        actions.append("risk")
        return SimpleNamespace(entry_id="risk", status="completed"), False

    monkeypatch.setattr(research_runner, "run_analysis", analyze)
    monkeypatch.setattr(risk_officer, "begin_run", risk_run)
    worker = Thread(target=service.run_daily_reviews, args=(stop,))
    worker.start()
    try:
        assert four_started.wait(5)
        assert len(started) == concurrency
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive()
    assert max(active) == concurrency and sorted(started) == ids
    assert actions.index("risk") == len(ids)


@pytest.mark.parametrize("concurrency", [1, 4])
def test_stop_finishes_active_work_without_starting_waiting_members_risk_or_backlog(client, monkeypatch, concurrency):
    from watchlist_app.core.settings import get_settings
    monkeypatch.setattr(get_settings(), "research_worker_concurrency", concurrency)
    ids = daily_scope(client, monkeypatch)
    release, four_started, stop = Event(), Event(), Event()
    lock = Lock()
    started, risk_calls = [], []

    def analyze(run_id):
        with get_session_factory()() as session:
            iid = session.get(ResearchEntry, run_id).context_json["instrument_ids"][0]
        with lock:
            started.append(iid)
            if len(started) == concurrency:
                four_started.set()
        assert release.wait(5)
        with get_session_factory()() as session:
            session.get(ResearchEntry, run_id).status = "completed"
            session.commit()

    monkeypatch.setattr(research_runner, "run_analysis", analyze)
    monkeypatch.setattr(risk_officer, "begin_run", lambda session, **scope: risk_calls.append(scope))
    worker = Thread(target=service.run_daily_reviews, args=(stop,))
    worker.start()
    try:
        assert four_started.wait(5)
        stop.set()
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive()
    assert sorted(started) == ids[:concurrency] and risk_calls == []
    with get_session_factory()() as session:
        from sqlalchemy import select
        attempts = list(session.scalars(select(ResearchEntry).where(ResearchEntry.topic_id.startswith("instrument-events:daily-"))))
        assert len(attempts) == concurrency and all(run.status == "completed" for run in attempts)


def test_already_running_member_defers_scope_risk_until_a_later_daily_pass(client, monkeypatch):
    ids = daily_scope(client, monkeypatch, members=1)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, ids[:1])
        run.status = "running"
        session.commit()
    calls, risk_calls = [], []

    def analyze(run_id):
        with get_session_factory()() as session:
            run = session.get(ResearchEntry, run_id)
            calls.extend(run.context_json["instrument_ids"])
            run.status = "completed"
            session.commit()

    monkeypatch.setattr(research_runner, "run_analysis", analyze)
    monkeypatch.setattr(risk_officer, "begin_run", lambda session, **scope: risk_calls.append(scope))
    service.run_daily_reviews(Event())
    assert sorted(calls) == ids[1:] and risk_calls == []


def test_free_slot_rechecks_due_boundary_while_another_instrument_is_still_running(client, monkeypatch):
    from datetime import UTC, datetime
    from watchlist_app.core.settings import get_settings
    ids = daily_scope(client, monkeypatch, members=0)
    monkeypatch.setattr(get_settings(), "research_worker_concurrency", 2)
    before_due = datetime(2026, 9, 29, 12, 29, tzinfo=UTC)
    after_due = datetime(2026, 9, 29, 12, 31, tzinfo=UTC)
    class Clock(datetime):
        current = before_due
        @classmethod
        def now(cls, tz=None):
            return cls.current
    monkeypatch.setattr(service, "datetime", Clock)
    long_started, new_started, release = Event(), Event(), Event()
    calls = []
    # The new US member has the oldest prior attempt and belongs ahead of the
    # remaining fund backlog as soon as its exchange-local 08:30 clock is due.
    monkeypatch.setattr(service, "daily_review_groups", lambda session:
        ([[ids[2]]] if service._research_due("us", Clock.current) else []) + [[ids[0]], [ids[1]], [ids[3]]])
    def analyze(run_id):
        with get_session_factory()() as session:
            iid = session.get(ResearchEntry, run_id).context_json["instrument_ids"][0]
        calls.append(iid)
        if iid == ids[0]:
            long_started.set()
            assert release.wait(10)
        elif iid == ids[1]:
            assert long_started.wait(5)
            Clock.current = after_due
        elif iid == ids[2]:
            assert not release.is_set()
            new_started.set()
        with get_session_factory()() as session:
            session.get(ResearchEntry, run_id).status = "completed"
            session.commit()
    monkeypatch.setattr(research_runner, "run_analysis", analyze)
    worker = Thread(target=service.run_daily_reviews, args=(Event(),))
    worker.start()
    try:
        assert new_started.wait(5)
        if ids[3] in calls:
            assert calls.index(ids[2]) < calls.index(ids[3])
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive()
    assert sorted(calls) == ids[:4]


def test_free_capacity_polls_for_newly_due_work_without_waiting_for_long_job(client, monkeypatch):
    from concurrent.futures import wait as actual_wait
    from watchlist_app.core.settings import get_settings
    ids = daily_scope(client, monkeypatch, members=0)
    monkeypatch.setattr(get_settings(), "research_worker_concurrency", 2)
    newly_due, long_started, new_started, release = Event(), Event(), Event(), Event()
    monkeypatch.setattr(service, "daily_review_groups", lambda session: [[ids[0]]] + ([[ids[1]]] if newly_due.is_set() else []))
    def wait_at_boundary(futures, *, timeout, return_when):
        assert long_started.wait(5)
        if not newly_due.is_set():
            assert timeout == 60  # Keep the existing idle worker cadence.
            newly_due.set()
            return set(), set(futures)
        return actual_wait(futures, timeout=timeout, return_when=return_when)
    monkeypatch.setattr(service, "wait", wait_at_boundary)
    def analyze(run_id):
        with get_session_factory()() as session:
            iid = session.get(ResearchEntry, run_id).context_json["instrument_ids"][0]
        if iid == ids[0]:
            long_started.set()
            assert release.wait(10)
        else:
            new_started.set()
        with get_session_factory()() as session:
            session.get(ResearchEntry, run_id).status = "completed"
            session.commit()
    monkeypatch.setattr(research_runner, "run_analysis", analyze)
    worker = Thread(target=service.run_daily_reviews, args=(Event(),))
    worker.start()
    try:
        assert new_started.wait(5)
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive()


def test_manual_member_finishing_during_other_research_unblocks_scope_risk(client, monkeypatch):
    from watchlist_app.core.settings import get_settings
    monkeypatch.setattr(get_settings(), "research_worker_concurrency", 1)
    ids = daily_scope(client, monkeypatch, members=1)
    with get_session_factory()() as session:
        manual, _ = service.begin_run(session, ids[:1])
        manual.status = "running"
        manual_id = manual.entry_id
        session.commit()
    calls = []
    def analyze(run_id):
        assert run_id != manual_id
        with get_session_factory()() as session:
            # The original initiator finishes while the automatic worker is
            # doing unrelated work. Only this fixture simulates that publication.
            session.get(ResearchEntry, manual_id).status = "completed"
            session.get(ResearchEntry, run_id).status = "completed"
            session.commit()
    monkeypatch.setattr(research_runner, "run_analysis", analyze)
    monkeypatch.setattr(risk_officer, "begin_run", lambda session, **scope:
        (calls.append(scope) or SimpleNamespace(entry_id="risk", status="completed"), False))
    service.run_daily_reviews(Event())
    assert len(calls) == 1 and calls[0]["portfolio_id"] == "held"


def test_same_day_completed_front_candidate_does_not_starve_remaining_work(client, monkeypatch):
    from watchlist_app.core.settings import get_settings
    monkeypatch.setattr(get_settings(), "research_worker_concurrency", 1)
    ids = daily_scope(client, monkeypatch, members=0)
    with get_session_factory()() as session:
        first, _ = service.begin_run(session, ids[:1], scheduled=True)
        first.status = "completed"
        session.commit()
    calls = []
    def analyze(run_id):
        with get_session_factory()() as session:
            run = session.get(ResearchEntry, run_id)
            calls.extend(run.context_json["instrument_ids"])
            run.status = "completed"
            session.commit()
    monkeypatch.setattr(research_runner, "run_analysis", analyze)
    service.run_daily_reviews(Event())
    assert calls == ids[1:]


def test_foreign_scope_becomes_ready_after_another_risk_job_completes(client, monkeypatch):
    ids = daily_scope(client, monkeypatch, members=0)
    monkeypatch.setattr(service, 'daily_review_groups', lambda session: [[ids[0]], [ids[1]]])
    monkeypatch.setattr(research_workbench, 'portfolio_options', lambda: {'portfolios': [{'portfolio_id': 'busy'}, {'portfolio_id': 'ready'}]})
    monkeypatch.setattr(risk_review_state, 'current_scope', lambda session, scope:
        {'instrument_ids': [ids[0]] if scope.get('portfolio_id') == 'busy' else [ids[1]] if scope.get('portfolio_id') == 'ready' else []})
    with get_session_factory()() as session:
        manual, _ = service.begin_run(session, ids[:1])
        manual.status = 'running'
        manual_id = manual.entry_id
        finished, _ = service.begin_run(session, [ids[1]], scheduled=True)
        finished.status = 'completed'
        session.commit()
    calls = []
    def begin_risk(session, **scope):
        calls.append(scope['portfolio_id'])
        return SimpleNamespace(entry_id=scope['portfolio_id'], status='queued'), True
    def analyze(run_id):
        assert run_id != manual_id
        assert run_id in {'ready', 'busy'}
        if run_id == 'ready':
            with get_session_factory()() as session:
                session.get(ResearchEntry, manual_id).status = 'completed'
                session.commit()
    monkeypatch.setattr(risk_officer, 'begin_run', begin_risk)
    monkeypatch.setattr(research_runner, 'run_analysis', analyze)
    service.run_daily_reviews(Event())
    assert calls == ['ready', 'busy']
