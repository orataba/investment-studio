from watchlist_app.services import risk_review_state
from datetime import UTC, datetime
from investment_studio_instrument_core.db_models import Instrument
from threading import Event, Lock, Thread
from types import SimpleNamespace
import pytest

from watchlist_app.db.models import InstrumentAttributeValue, InstrumentDetail
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_runner, research_workbench, risk_officer, sector_research as service
from .conftest import canonical_quote_policy


def daily_scope(client, monkeypatch, *, members=6):
    from watchlist_app.core.settings import get_settings
    kinds = ["equity", "etf", "index", "equity", "etf", "index"]
    ids = [f"daily-{number}" for number in range(len(kinds))]
    monkeypatch.setattr(get_settings(), "research_automatic_enabled", True)
    monkeypatch.setattr("watchlist_app.services.research_schedule.automatic_research_allowed", lambda now: True)
    with get_session_factory()() as session:
        for iid, kind in zip(ids, kinds):
            session.add(Instrument(instrument_id=iid, instrument_name=iid, instrument_type=kind, currency="USD", exchange_code="XNYS" if kind in {"equity", "etf"} else None,
                quote_selection_policy_json=canonical_quote_policy(kind), source_settings_json={}, lifecycle_state_json={"status": "active"}))
            session.add(InstrumentDetail(instrument_id=iid, instrument_type=kind, detail_view_type=kind,
                instrument_name=iid, is_active=True, metadata_json={}))
        session.flush()
        session.add_all(InstrumentAttributeValue(instrument_id=iid, attribute_key="coverage_status",
            value_json="Invested", adopted_at=datetime.now(UTC)) for iid in ids)
        session.commit()
    monkeypatch.setattr(service, "daily_review_groups", lambda session: [[iid] for iid in ids])
    monkeypatch.setattr(service, "_research_market", lambda session, iid: "cn")
    monkeypatch.setattr(research_workbench, "portfolio_options", lambda: pytest.fail("Automatic research must not enumerate portfolios"))
    monkeypatch.setattr(risk_review_state, "current_scope", lambda session, scope: {"instrument_ids": [scope["instrument_id"]] if scope.get("instrument_id") in ids[:members] else []})
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

    monkeypatch.setattr("watchlist_app.services.research_execution.enqueue_run", lambda principal, run_id, **kwargs: analyze(run_id))
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
    assert actions.index("risk") == len(ids) and actions.count("risk") == len(ids)


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

    monkeypatch.setattr("watchlist_app.services.research_execution.enqueue_run", lambda principal, run_id, **kwargs: analyze(run_id))
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

    monkeypatch.setattr("watchlist_app.services.research_execution.enqueue_run", lambda principal, run_id, **kwargs: analyze(run_id))
    monkeypatch.setattr(risk_officer, "begin_run", lambda session, **scope: risk_calls.append(scope))
    service.run_daily_reviews(Event())
    assert sorted(calls) == ids[1:] and risk_calls == []


def test_free_slot_rechecks_due_boundary_while_another_instrument_is_still_running(client, monkeypatch):
    from datetime import UTC, datetime
    from watchlist_app.core.settings import get_settings
    ids = daily_scope(client, monkeypatch, members=0)
    monkeypatch.setattr(get_settings(), "research_worker_concurrency", 2)
    before_due = datetime(2026, 9, 29, 23, 14, tzinfo=UTC)
    after_due = datetime(2026, 9, 29, 23, 16, tzinfo=UTC)
    class Clock(datetime):
        current = before_due
        @classmethod
        def now(cls, tz=None):
            return cls.current
    monkeypatch.setattr(service, "datetime", Clock)
    long_started, new_started, release = Event(), Event(), Event()
    calls = []
    # The new US member has the oldest prior attempt and belongs ahead of the
    # remaining fund backlog as soon as its post-collection research clock is due.
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
    monkeypatch.setattr("watchlist_app.services.research_execution.enqueue_run", lambda principal, run_id, **kwargs: analyze(run_id))
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
    monkeypatch.setattr("watchlist_app.services.research_execution.enqueue_run", lambda principal, run_id, **kwargs: analyze(run_id))
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
    monkeypatch.setattr("watchlist_app.services.research_execution.enqueue_run", lambda principal, run_id, **kwargs: analyze(run_id))
    monkeypatch.setattr(risk_officer, "begin_run", lambda session, **scope:
        (calls.append(scope) or SimpleNamespace(entry_id="risk", status="completed"), False))
    service.run_daily_reviews(Event())
    assert len(calls) == 1 and calls[0]["instrument_id"] == ids[0]


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
    monkeypatch.setattr("watchlist_app.services.research_execution.enqueue_run", lambda principal, run_id, **kwargs: analyze(run_id))
    service.run_daily_reviews(Event())
    assert calls == ids[1:]


def test_foreign_scope_becomes_ready_after_another_risk_job_completes(client, monkeypatch):
    ids = daily_scope(client, monkeypatch, members=0)
    monkeypatch.setattr(service, 'daily_review_groups', lambda session: [[ids[0]], [ids[1]]])
    monkeypatch.setattr(risk_review_state, 'current_scope', lambda session, scope:
        {'instrument_ids': [scope['instrument_id']] if scope.get('instrument_id') in ids[:2] else []})
    with get_session_factory()() as session:
        manual, _ = service.begin_run(session, ids[:1])
        manual.status = 'running'
        manual_id = manual.entry_id
        finished, _ = service.begin_run(session, [ids[1]], scheduled=True)
        finished.status = 'completed'
        session.commit()
    calls = []
    def begin_risk(session, **scope):
        calls.append(scope['instrument_id'])
        return SimpleNamespace(entry_id='risk:' + scope['instrument_id'], status='queued'), True
    def analyze(run_id):
        assert run_id != manual_id
        assert run_id in {'risk:' + iid for iid in ids[:2]}
        if run_id == 'risk:' + ids[1]:
            with get_session_factory()() as session:
                session.get(ResearchEntry, manual_id).status = 'completed'
                session.commit()
    monkeypatch.setattr(risk_officer, 'begin_run', begin_risk)
    monkeypatch.setattr("watchlist_app.services.research_execution.enqueue_run", lambda principal, run_id, **kwargs: analyze(run_id))
    service.run_daily_reviews(Event())
    assert calls == [ids[1], ids[0]]


@pytest.mark.parametrize("concurrency", [1, 3])
def test_real_durable_queue_holds_scheduler_slots_until_execution_finishes(client, monkeypatch, concurrency):
    from datetime import UTC, datetime
    import time
    from sqlalchemy import select
    from watchlist_app.core.settings import get_settings
    from watchlist_app.db.models.research_execution import ResearchExecution
    from watchlist_app.services import research_execution as queue
    monkeypatch.setattr(get_settings(), "research_worker_concurrency", concurrency)
    ids = daily_scope(client, monkeypatch, members=0)
    stop = Event()
    scheduler = Thread(target=service.run_daily_reviews, args=(stop,))
    scheduler.start()

    def pending():
        with get_session_factory()() as session:
            return list(session.scalars(select(ResearchExecution.run_id).where(
                ResearchExecution.status.in_(["queued", "running"]))))
    def wait_count(expected):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if len(pending()) == expected:
                return
            time.sleep(0.01)
        assert len(pending()) == expected

    try:
        wait_count(concurrency)
        # No synchronous run_analysis mock: committed queue entries remain
        # pending. They must hold all scheduler slots, rather than queue every eligible instrument.
        time.sleep(0.15)
        assert len(pending()) == concurrency
        with get_session_factory()() as session:
            assert len(list(session.scalars(select(ResearchEntry.entry_id).where(
                ResearchEntry.topic_id.startswith("instrument-events:daily-"))))) == concurrency
        claim = queue.claim_next("offline-executor")
        assert claim is not None
        with get_session_factory()() as session:
            run = session.get(ResearchEntry, claim.run_id)
            run.status, run.completed_at = "completed", datetime.now(UTC)
            session.commit()
        queue.finish_claim(claim)
        wait_count(concurrency)
        with get_session_factory()() as session:
            assert len(list(session.scalars(select(ResearchEntry.entry_id).where(
                ResearchEntry.topic_id.startswith("instrument-events:daily-"))))) == concurrency + 1
    finally:
        stop.set()
        scheduler.join(5)
    assert not scheduler.is_alive()
    # Stop releases only the scheduler wait; accepted queue work stays durable.
    assert len(pending()) == concurrency
