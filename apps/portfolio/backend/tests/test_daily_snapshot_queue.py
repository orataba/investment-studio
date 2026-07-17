from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from threading import Barrier, Event, Lock
from time import monotonic, sleep

from portfolio_ops_instrument_core.db_models import Instrument

from portfolio_app.db.models import (
    PortfolioCalculationStateModel,
    PortfolioDailySnapshotModel,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services import daily_snapshot_worker, daily_snapshots


PORTFOLIO_ID = "portfolio-ops"


def _calculation_state() -> dict[str, object]:
    session_factory = get_session_factory()
    with session_factory() as session:
        state = session.get(PortfolioCalculationStateModel, PORTFOLIO_ID)
        assert state is not None
        return {
            "daily_snapshot_status": state.daily_snapshot_status,
            "dirty_from": state.dirty_from,
            "refresh_request_id": state.refresh_request_id,
            "refresh_started_at": state.refresh_started_at,
            "refresh_completed_at": state.refresh_completed_at,
        }


def _published_snapshot_fingerprint() -> list[tuple[object, object, object]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        rows = (
            session.query(PortfolioDailySnapshotModel)
            .filter(PortfolioDailySnapshotModel.portfolio_id == PORTFOLIO_ID)
            .order_by(PortfolioDailySnapshotModel.as_of_date)
            .all()
        )
        return [
            (row.as_of_date, row.nav, deepcopy(row.snapshot_json))
            for row in rows
        ]


def test_concurrent_enqueue_requests_merge_the_earliest_dirty_date() -> None:
    baseline = daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(
        PORTFOLIO_ID
    )
    assert baseline is not None
    initial = daily_snapshots.enqueue_selected_portfolio_daily_snapshot_recalculations(
        [PORTFOLIO_ID],
        dirty_from=date(2026, 4, 10),
    )[0]
    barrier = Barrier(2)

    def enqueue(dirty_from: date) -> dict[str, object]:
        barrier.wait()
        return daily_snapshots.enqueue_selected_portfolio_daily_snapshot_recalculations(
            [PORTFOLIO_ID],
            dirty_from=dirty_from,
        )[0]

    with ThreadPoolExecutor(max_workers=2) as executor:
        accepted = list(
            executor.map(
                enqueue,
                (date(2026, 4, 5), date(2026, 2, 1)),
            )
        )

    state = _calculation_state()
    request_ids = {
        str(initial["refresh_request_id"]),
        *(str(item["refresh_request_id"]) for item in accepted),
    }
    assert len(request_ids) == 3
    assert state["daily_snapshot_status"] == "stale"
    assert state["dirty_from"] == date(2026, 2, 1)
    assert state["refresh_request_id"] in request_ids


def test_full_rebuild_request_cannot_be_downgraded_by_a_later_partial_request() -> None:
    baseline = daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(
        PORTFOLIO_ID
    )
    assert baseline is not None
    partial = daily_snapshots.enqueue_selected_portfolio_daily_snapshot_recalculations(
        [PORTFOLIO_ID],
        dirty_from=date(2026, 4, 10),
    )[0]
    assert partial["dirty_from"] == date(2026, 4, 10)
    full = daily_snapshots.enqueue_selected_portfolio_daily_snapshot_recalculations(
        [PORTFOLIO_ID],
        dirty_from=None,
    )[0]
    later_partial = daily_snapshots.enqueue_selected_portfolio_daily_snapshot_recalculations(
        [PORTFOLIO_ID],
        dirty_from=date(2026, 5, 1),
    )[0]

    state = _calculation_state()
    assert len(
        {
            str(partial["refresh_request_id"]),
            str(full["refresh_request_id"]),
            str(later_partial["refresh_request_id"]),
        }
    ) == 3
    assert state["daily_snapshot_status"] == "stale"
    assert state["dirty_from"] is None
    assert state["refresh_request_id"] == later_partial["refresh_request_id"]


def test_recalculation_api_only_enqueues_and_old_sync_route_is_gone(
    client,
    monkeypatch,
) -> None:
    baseline = daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(
        PORTFOLIO_ID
    )
    assert baseline is not None
    published_before = _published_snapshot_fingerprint()

    def fail_if_calculation_runs(*_args, **_kwargs):
        raise AssertionError("enqueue API must not execute the calculation kernel")

    monkeypatch.setattr(
        daily_snapshots.performance,
        "build_daily_portfolio_snapshots",
        fail_if_calculation_runs,
    )
    response = client.post(
        "/api/portfolios/snapshots/daily/recalculations",
        json={
            "portfolio_ids": [PORTFOLIO_ID],
            "dirty_from": "2026-04-01",
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["portfolio_ids"] == [PORTFOLIO_ID]
    assert payload["accepted"] == [
        {
            "portfolio_id": PORTFOLIO_ID,
            "status": "accepted",
            "daily_snapshot_status": "stale",
            "refresh_request_id": payload["accepted"][0]["refresh_request_id"],
            "dirty_from": "2026-04-01",
        }
    ]
    assert _published_snapshot_fingerprint() == published_before
    assert client.post(
        "/api/portfolios/snapshots/daily/refresh",
        json={"portfolio_ids": [PORTFOLIO_ID]},
    ).status_code == 404
    assert client.post(
        "/api/portfolios/snapshots/daily/recalculations",
        json={
            "portfolio_ids": [PORTFOLIO_ID],
            "instrument_ids": ["equity-us-abbv"],
        },
    ).status_code == 422


def test_running_generation_is_superseded_discarded_and_replayed_without_overlap(
    monkeypatch,
) -> None:
    original_builder = daily_snapshots.performance.build_daily_portfolio_snapshots
    first_build_started = Event()
    release_first_build = Event()
    active_guard = Lock()
    build_calls = 0
    active_builds = 0
    maximum_active_builds = 0

    def controlled_builder(*args, **kwargs):
        nonlocal active_builds, build_calls, maximum_active_builds
        with active_guard:
            build_calls += 1
            call_number = build_calls
            active_builds += 1
            maximum_active_builds = max(maximum_active_builds, active_builds)
        try:
            if call_number == 1:
                first_build_started.set()
                assert release_first_build.wait(timeout=5)
            snapshots = original_builder(*args, **kwargs)
            if call_number == 1 and snapshots:
                snapshots[-1]["nav"] = 987_654_321.0
            return snapshots
        finally:
            with active_guard:
                active_builds -= 1

    monkeypatch.setattr(
        daily_snapshots.performance,
        "build_daily_portfolio_snapshots",
        controlled_builder,
    )
    first_request = (
        daily_snapshots.enqueue_selected_portfolio_daily_snapshot_recalculations(
            [PORTFOLIO_ID],
            dirty_from=date(2026, 4, 1),
        )[0]
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        worker_future = executor.submit(
            daily_snapshot_worker.run_daily_snapshot_recalculation_worker_once
        )
        assert first_build_started.wait(timeout=5)
        running_state = _calculation_state()
        assert running_state["daily_snapshot_status"] == "running"
        assert running_state["refresh_request_id"] == first_request["refresh_request_id"]

        replacement = (
            daily_snapshots.enqueue_selected_portfolio_daily_snapshot_recalculations(
                [PORTFOLIO_ID],
                dirty_from=date(2026, 2, 1),
            )[0]
        )
        assert replacement["daily_snapshot_status"] == "running"
        assert replacement["refresh_request_id"] != first_request["refresh_request_id"]

        # A second worker/process sees the live claim and cannot overlap it.
        assert not daily_snapshot_worker.run_daily_snapshot_recalculation_worker_once()
        release_first_build.set()
        assert worker_future.result(timeout=10)

    state = _calculation_state()
    assert build_calls == 2
    assert maximum_active_builds == 1
    assert state["daily_snapshot_status"] == "current"
    assert state["dirty_from"] is None
    assert state["refresh_request_id"] is None
    assert all(
        nav != 987_654_321.0
        for _snapshot_date, nav, _payload in _published_snapshot_fingerprint()
    )


def test_singleton_worker_recovers_an_expired_database_claim() -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        state = daily_snapshots._state_for_portfolio(session, PORTFOLIO_ID)
        state.daily_snapshot_status = "running"
        state.refresh_request_id = "crashed-worker-generation"
        state.refresh_started_at = (
            datetime.now(UTC)
            - timedelta(
                seconds=daily_snapshots._RUNNING_REFRESH_LEASE_SECONDS + 1
            )
        ).isoformat()
        session.commit()

    first_worker = daily_snapshot_worker.start_daily_snapshot_recalculation_worker(
        poll_seconds=0.01,
        reconciliation_batch_size=2,
    )
    second_worker = daily_snapshot_worker.start_daily_snapshot_recalculation_worker(
        poll_seconds=0.01,
        reconciliation_batch_size=2,
    )
    assert first_worker is second_worker
    try:
        deadline = monotonic() + 10
        while monotonic() < deadline:
            if _calculation_state()["daily_snapshot_status"] == "current":
                break
            sleep(0.01)
        state = _calculation_state()
        assert state["daily_snapshot_status"] == "current"
        assert state["refresh_request_id"] is None
        assert state["refresh_completed_at"] is not None
    finally:
        daily_snapshot_worker.stop_daily_snapshot_recalculation_worker(
            timeout_seconds=5
        )
    assert not first_worker.is_alive


def test_worker_reconciles_a_missed_market_data_notification() -> None:
    baseline = daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(
        PORTFOLIO_ID
    )
    assert baseline is not None
    session_factory = get_session_factory()
    with session_factory() as session:
        state = session.get(PortfolioCalculationStateModel, PORTFOLIO_ID)
        instrument = session.get(Instrument, "equity-us-abbv")
        assert state is not None
        assert instrument is not None
        assert state.daily_snapshot_status == "current"
        previous_generation = state.source_market_data_updated_at
        instrument.market_data_updated_at = "2099-01-01T00:00:00.000001Z"
        session.commit()

    # No enqueue callback was sent: durable state still says current.
    assert _calculation_state()["daily_snapshot_status"] == "current"
    assert daily_snapshot_worker.run_daily_snapshot_recalculation_worker_once()

    with session_factory() as session:
        state = session.get(PortfolioCalculationStateModel, PORTFOLIO_ID)
        rows = (
            session.query(PortfolioDailySnapshotModel)
            .filter(PortfolioDailySnapshotModel.portfolio_id == PORTFOLIO_ID)
            .all()
        )
        assert state is not None
        assert state.daily_snapshot_status == "current"
        assert state.source_market_data_updated_at == (
            "2099-01-01T00:00:00.000001Z"
        )
        assert state.source_market_data_updated_at != previous_generation
        assert rows
        assert all(
            row.snapshot_json["source_generation"]["market_data_updated_at"]
            == "2099-01-01T00:00:00.000001Z"
            for row in rows
        )
