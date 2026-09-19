from datetime import date

from portfolio_app.db.models import PortfolioCalculationStateModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services import daily_snapshots


PORTFOLIO_ID = 'investment-studio'


def test_fact_invalidation_wakes_once_only_after_committed_state_is_readable(monkeypatch):
    wakes = []
    def wake():
        with get_session_factory()() as reader:
            state = reader.get(PortfolioCalculationStateModel, PORTFOLIO_ID)
            wakes.append(state.daily_snapshot_status)
    monkeypatch.setattr(daily_snapshots, '_wake_daily_snapshot_worker', wake)
    with get_session_factory()() as session:
        for day in [date(2026, 4, 15), date(2026, 4, 14)]:
            daily_snapshots.mark_portfolio_daily_snapshots_stale(PORTFOLIO_ID, day, session=session)
        assert wakes == []
        session.commit()
        assert wakes == ['stale']
        session.commit()
        assert wakes == ['stale']


def test_rolled_back_fact_change_never_wakes_worker(monkeypatch):
    wakes = []
    monkeypatch.setattr(daily_snapshots, '_wake_daily_snapshot_worker', lambda: wakes.append(True))
    with get_session_factory()() as session:
        daily_snapshots.mark_portfolio_daily_snapshots_stale(PORTFOLIO_ID, session=session)
        session.rollback()
        session.commit()
    assert wakes == []


def test_savepoint_commit_waits_for_outer_commit_and_savepoint_rollback_discards_its_wake(monkeypatch):
    wakes = []
    monkeypatch.setattr(daily_snapshots, '_wake_daily_snapshot_worker', lambda: wakes.append(True))
    with get_session_factory()() as session:
        with session.begin_nested():
            daily_snapshots.mark_portfolio_daily_snapshots_stale(PORTFOLIO_ID, session=session)
        assert wakes == []
        session.commit()
    assert wakes == [True]
    wakes.clear()
    with get_session_factory()() as session:
        savepoint = session.begin_nested()
        daily_snapshots.mark_portfolio_daily_snapshots_stale(PORTFOLIO_ID, session=session)
        savepoint.rollback()
        session.commit()
    assert wakes == []


def test_savepoint_rollback_preserves_earlier_outer_invalidation(monkeypatch):
    wakes = []
    monkeypatch.setattr(daily_snapshots, '_wake_daily_snapshot_worker', lambda: wakes.append(True))
    with get_session_factory()() as session:
        daily_snapshots.mark_portfolio_daily_snapshots_stale(PORTFOLIO_ID, session=session)
        savepoint = session.begin_nested()
        daily_snapshots.mark_portfolio_daily_snapshots_stale(PORTFOLIO_ID, session=session)
        savepoint.rollback()
        session.commit()
    assert wakes == [True]
