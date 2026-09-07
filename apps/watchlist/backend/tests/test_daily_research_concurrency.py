from threading import Event, Lock, Thread
from types import SimpleNamespace

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
    monkeypatch.setattr(risk_officer, "read_snapshot", lambda session, **scope: {"instrument_ids": ids[:members]})
    return ids


def test_four_workers_finish_scope_before_risk_then_process_remaining_selected_instruments(client, monkeypatch):
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
            if len(started) == 4:
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
        assert not set(ids[6:]).intersection(started)
        actions.append("risk")
        return SimpleNamespace(entry_id="risk", status="completed"), False

    monkeypatch.setattr(research_runner, "run_analysis", analyze)
    monkeypatch.setattr(risk_officer, "begin_run", risk_run)
    worker = Thread(target=service.run_daily_reviews, args=(stop,))
    worker.start()
    try:
        assert four_started.wait(5)
        assert len(started) == 4
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive()
    assert max(active) == 4 and sorted(started) == ids
    assert actions.index("risk") >= 6
    assert all(actions.index(iid) > actions.index("risk") for iid in ids[6:])


def test_stop_finishes_active_work_without_starting_waiting_members_risk_or_backlog(client, monkeypatch):
    ids = daily_scope(client, monkeypatch)
    release, four_started, stop = Event(), Event(), Event()
    lock = Lock()
    started, risk_calls = [], []

    def analyze(run_id):
        with get_session_factory()() as session:
            iid = session.get(ResearchEntry, run_id).context_json["instrument_ids"][0]
        with lock:
            started.append(iid)
            if len(started) == 4:
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
    assert sorted(started) == ids[:4] and risk_calls == []
    with get_session_factory()() as session:
        from sqlalchemy import select
        attempts = list(session.scalars(select(ResearchEntry).where(ResearchEntry.topic_id.startswith("instrument-events:daily-"))))
        assert len(attempts) == 4 and all(run.status == "completed" for run in attempts)


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
