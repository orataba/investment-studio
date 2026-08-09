from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace


def test_reconciliation_target_loader_keyset_pages_and_fails_closed(
    monkeypatch,
) -> None:
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from watchlist_app.services import read_model_freshness

    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE instrument_detail ("
                "instrument_id TEXT PRIMARY KEY, is_active BOOLEAN NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE instrument_chart_read_model ("
                "instrument_id TEXT PRIMARY KEY, source_cutoff_at DATETIME, "
                "materialization_version TEXT NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE watchlist_row_read_model ("
                "watchlist_id TEXT NOT NULL, instrument_id TEXT NOT NULL, "
                "last_nav_date DATE, last_fact_update_at DATETIME, "
                "materialization_version TEXT NOT NULL, "
                "PRIMARY KEY (watchlist_id, instrument_id))"
            )
        )
        connection.execute(
            text(
                "INSERT INTO instrument_detail (instrument_id, is_active) VALUES "
                "('fund-a', 1), ('fund-b', 1), ('fund-c', 1), ('fund-z', 0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO instrument_chart_read_model "
                "(instrument_id, source_cutoff_at, materialization_version) VALUES "
                "('fund-a', '2026-07-15 09:00:00', 'watchlist-materialization/v3'), "
                "('fund-b', '2026-07-15 09:00:00', 'watchlist-materialization/v3'), "
                "('fund-c', '2026-07-15 10:00:00', 'watchlist-materialization/v3')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO watchlist_row_read_model "
                "(watchlist_id, instrument_id, last_nav_date, last_fact_update_at, "
                "materialization_version) "
                "VALUES "
                "('wl-1', 'fund-a', '2026-07-14', '2026-07-15 08:00:00', "
                "'watchlist-materialization/v3'), "
                "('wl-2', 'fund-a', '2026-07-15', '2026-07-15 09:00:00', "
                "'watchlist-materialization/v3'), "
                "('wl-1', 'fund-b', '2026-07-15', NULL, 'unversioned')"
            )
        )

    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(
        read_model_freshness,
        "get_session_factory",
        lambda: session_factory,
    )

    first_targets, first_cursor, first_completed = (
        read_model_freshness._load_reconciliation_targets(
            limit=2,
            after_instrument_id=None,
        )
    )
    second_targets, second_cursor, second_completed = (
        read_model_freshness._load_reconciliation_targets(
            limit=2,
            after_instrument_id=first_cursor,
        )
    )

    assert [target["instrument_id"] for target in first_targets] == [
        "fund-a",
        "fund-b",
    ]
    assert first_targets[0]["local_latest_date"].isoformat() == "2026-07-15"
    assert first_targets[0]["local_source_cutoff_at"] == datetime(
        2026,
        7,
        15,
        8,
        tzinfo=UTC,
    )
    assert first_targets[0]["local_materialization_version"] == (
        "watchlist-materialization/v3"
    )
    assert first_targets[1]["local_source_cutoff_at"] is None
    assert first_targets[1]["local_materialization_version"] is None
    assert first_cursor == "fund-b"
    assert first_completed is False
    assert [target["instrument_id"] for target in second_targets] == ["fund-c"]
    assert second_targets[0]["local_source_cutoff_at"] == datetime(
        2026,
        7,
        15,
        10,
        tzinfo=UTC,
    )
    assert second_targets[0]["local_materialization_version"] == (
        "watchlist-materialization/v3"
    )
    assert second_cursor is None
    assert second_completed is True


def test_local_source_cutoff_fails_closed_for_partial_materialization() -> None:
    from watchlist_app.services.read_model_freshness import (
        local_materialization_source_cutoff,
    )

    older = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
    newer = datetime(2026, 7, 15, 9, 0, tzinfo=UTC)

    assert local_materialization_source_cutoff(newer, older) == older
    assert local_materialization_source_cutoff(newer, None) is None
    assert local_materialization_source_cutoff() is None


def test_source_generation_ref_preserves_same_date_revision_identity() -> None:
    from watchlist_app.services.read_model_freshness import _source_generation_ref

    source_generation = datetime(2026, 7, 15, 9, 30, tzinfo=UTC)

    assert _source_generation_ref(
        shared_updated_at=source_generation,
        shared_latest_date=source_generation.date(),
    ) == "2026-07-15T09:30:00Z"


def test_legacy_source_date_converges_when_selected_series_is_unavailable() -> None:
    from watchlist_app.services.read_model_freshness import (
        _source_data_is_materialized,
    )

    assert _source_data_is_materialized(
        shared_updated_at=None,
        shared_latest_date=date(2026, 4, 28),
        local_source_cutoff_at=datetime(2026, 7, 20, 8, 0, tzinfo=UTC),
        local_latest_date=None,
    ) is True
    assert _source_data_is_materialized(
        shared_updated_at=None,
        shared_latest_date=date(2026, 7, 21),
        local_source_cutoff_at=datetime(2026, 7, 20, 8, 0, tzinfo=UTC),
        local_latest_date=None,
    ) is False


def test_single_instrument_legacy_source_date_does_not_requeue_forever(
    monkeypatch,
) -> None:
    from watchlist_app.services import read_model_freshness
    from watchlist_app.services.materialization_policy import (
        WATCHLIST_MATERIALIZATION_VERSION,
    )

    shared = {
        "instrument_id": "legacy-fund",
        "instrument_name": "Legacy Fund",
        "instrument_type": "fund",
        "identifiers": [],
        "market_data_updated_at": None,
        "latest_market_data": [{"as_of_date": "2026-04-28"}],
    }
    monkeypatch.setattr(
        read_model_freshness,
        "get_shared_instrument",
        lambda _instrument_id: shared,
    )
    monkeypatch.setattr(
        read_model_freshness,
        "_local_instrument_metadata_drift",
        lambda **_kwargs: False,
    )
    monkeypatch.setattr(
        read_model_freshness,
        "_enqueue_stale_recalc_job",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("already-materialized legacy data must not requeue")
        ),
    )

    assert read_model_freshness.schedule_instrument_refresh_if_stale(
        instrument_id="legacy-fund",
        local_latest_date=None,
        local_source_cutoff_at=datetime(2026, 7, 20, 8, 0, tzinfo=UTC),
        local_materialization_version=WATCHLIST_MATERIALIZATION_VERSION,
        trigger_ref_type="detail_read",
    ) is False


def test_stale_generation_creates_one_durable_per_instrument_job(
    monkeypatch,
) -> None:
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from watchlist_app.db.models.instruments import InstrumentDetail
    from watchlist_app.db.models.recalc import RecalcJob
    from watchlist_app.services import read_model_freshness

    engine = create_engine("sqlite+pysqlite:///:memory:")
    InstrumentDetail.__table__.create(engine)
    RecalcJob.__table__.create(engine)
    session_factory = sessionmaker(bind=engine)
    now = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
    with session_factory() as session:
        session.add(
            InstrumentDetail(
                instrument_id="fund-a",
                instrument_type="fund",
                detail_view_type="fund",
                instrument_name="Fund A",
                primary_identifier_type="internal",
                primary_identifier_value="FUND-A",
                is_active=True,
                metadata_json={},
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    shared_summary = {
        "instrument_id": "fund-a",
        "instrument_name": "Fund A",
        "instrument_type": "fund",
        "identifiers": [
            {
                "identifier_type": "internal",
                "identifier_value": "FUND-A",
                "is_primary": True,
            }
        ],
        "market_data_updated_at": "2026-07-15T09:30:00.000001Z",
        "latest_market_data": [
            {
                "as_of_date": "2026-07-15",
                "metric_family": "nav",
                "quote_basis": "official_nav",
            }
        ],
    }
    monkeypatch.setattr(
        read_model_freshness,
        "get_session_factory",
        lambda: session_factory,
    )
    monkeypatch.setattr(
        read_model_freshness,
        "get_shared_instrument_summaries",
        lambda instrument_ids: {
            instrument_id: shared_summary if instrument_id == "fund-a" else None
            for instrument_id in instrument_ids
        },
    )
    monkeypatch.setattr(
        read_model_freshness,
        "get_settings",
        lambda: SimpleNamespace(recalc_worker_running_job_timeout_seconds=300.0),
    )
    target = {
        "instrument_id": "fund-a",
        "local_latest_date": "2026-07-15",
        "local_source_cutoff_at": "2026-07-15T09:00:00Z",
        "local_materialization_version": "watchlist-materialization/v3",
    }

    first = read_model_freshness.schedule_instrument_refreshes_if_stale(
        targets=[target],
        trigger_ref_type="worker_reconcile",
        raise_on_error=True,
    )
    second = read_model_freshness.schedule_instrument_refreshes_if_stale(
        targets=[target],
        trigger_ref_type="worker_reconcile",
        raise_on_error=True,
    )

    with session_factory() as session:
        claimed = read_model_freshness.recalc_repository.claim_next_queued(
            session,
            running_timeout_seconds=300.0,
        )
        assert claimed is not None
        session.commit()
    while_running = read_model_freshness.schedule_instrument_refreshes_if_stale(
        targets=[target],
        trigger_ref_type="worker_reconcile",
        raise_on_error=True,
    )

    with session_factory() as session:
        jobs = list(session.scalars(select(RecalcJob)).all())
    assert first == 1
    assert second == 1
    assert while_running == 1
    assert len(jobs) == 1
    assert jobs[0].job_status == "running"
    assert jobs[0].trigger_ref_id == "2026-07-15T09:30:00.000001Z"
    assert jobs[0].payload_json["target_source_generation"] == (
        "2026-07-15T09:30:00.000001Z"
    )
    assert jobs[0].payload_json["target_materialization_version"] == (
        read_model_freshness.WATCHLIST_MATERIALIZATION_VERSION
    )


def test_same_source_generation_with_old_materialization_version_is_stale(
    monkeypatch,
) -> None:
    from watchlist_app.services import read_model_freshness

    shared = {
        "instrument_id": "fund-a",
        "instrument_name": "Fund A",
        "instrument_type": "fund",
        "identifiers": [
            {
                "identifier_type": "internal",
                "identifier_value": "FUND-A",
                "is_primary": True,
            }
        ],
        "market_data_updated_at": "2026-07-15T09:30:00Z",
        "latest_market_data": [{"as_of_date": "2026-07-15"}],
    }
    queued: list[dict[str, object]] = []
    monkeypatch.setattr(
        read_model_freshness,
        "get_shared_instrument",
        lambda _instrument_id: shared,
    )
    monkeypatch.setattr(
        read_model_freshness,
        "_local_instrument_metadata_drift",
        lambda **_kwargs: False,
    )
    monkeypatch.setattr(
        read_model_freshness,
        "_enqueue_stale_recalc_job",
        lambda **kwargs: queued.append(kwargs) or True,
    )

    assert read_model_freshness.schedule_instrument_refresh_if_stale(
        instrument_id="fund-a",
        local_latest_date=date(2026, 7, 15),
        local_source_cutoff_at=datetime(2026, 7, 15, 9, 30, tzinfo=UTC),
        local_materialization_version="unversioned",
        trigger_ref_type="version_test",
    ) is True
    assert len(queued) == 1


def test_reconciliation_pages_cover_all_targets_without_exceeding_batch(
    monkeypatch,
) -> None:
    from watchlist_app.services import read_model_freshness

    loaded_cursors: list[str | None] = []
    scheduled_batches: list[list[str]] = []

    def load_batch(*, limit: int, after_instrument_id: str | None):
        assert limit == 2
        loaded_cursors.append(after_instrument_id)
        if after_instrument_id is None:
            return (
                [
                    {"instrument_id": "fund-a"},
                    {"instrument_id": "fund-b"},
                ],
                "fund-b",
                False,
            )
        assert after_instrument_id == "fund-b"
        return ([{"instrument_id": "fund-c"}], None, True)

    def schedule(*, targets, **_kwargs):
        instrument_ids = [str(target["instrument_id"]) for target in targets]
        scheduled_batches.append(instrument_ids)
        return len(instrument_ids)

    monkeypatch.setattr(
        read_model_freshness,
        "_load_reconciliation_targets",
        load_batch,
    )
    monkeypatch.setattr(
        read_model_freshness,
        "schedule_instrument_refreshes_if_stale",
        schedule,
    )

    first = read_model_freshness.reconcile_stale_instrument_read_models(limit=2)
    second = read_model_freshness.reconcile_stale_instrument_read_models(
        limit=2,
        after_instrument_id=first.next_cursor,
    )

    assert loaded_cursors == [None, "fund-b"]
    assert scheduled_batches == [["fund-a", "fund-b"], ["fund-c"]]
    assert first.scanned_count == 2
    assert first.scheduled_count == 2
    assert first.next_cursor == "fund-b"
    assert first.cycle_completed is False
    assert second.scanned_count == 1
    assert second.scheduled_count == 1
    assert second.next_cursor is None
    assert second.cycle_completed is True


def test_worker_advances_reconciliation_cursor_and_never_zero_waits(
    monkeypatch,
) -> None:
    from watchlist_app.services import recalc_worker
    from watchlist_app.services.read_model_freshness import ReconciliationBatchResult

    settings = SimpleNamespace(
        recalc_worker_poll_interval_seconds=0.5,
        recalc_worker_reconcile_interval_seconds=60.0,
        recalc_worker_reconcile_batch_size=2,
    )
    cursor_calls: list[str | None] = []
    clock = [100.0]

    def reconcile(*, limit: int, after_instrument_id: str | None):
        assert limit == 2
        cursor_calls.append(after_instrument_id)
        if len(cursor_calls) == 1:
            return ReconciliationBatchResult(2, 0, "fund-b", False)
        return ReconciliationBatchResult(1, 0, None, True)

    class ControlledStopEvent:
        def __init__(self) -> None:
            self.waits: list[float] = []

        def is_set(self) -> bool:
            return len(self.waits) >= 2

        def wait(self, seconds: float) -> bool:
            assert seconds > 0
            self.waits.append(seconds)
            clock[0] += seconds
            return self.is_set()

    stop_event = ControlledStopEvent()
    monkeypatch.setattr(recalc_worker, "get_settings", lambda: settings)
    monkeypatch.setattr(
        recalc_worker,
        "reconcile_stale_instrument_read_models",
        reconcile,
    )
    monkeypatch.setattr(recalc_worker, "process_next_recalc_job", lambda: False)
    monkeypatch.setattr(recalc_worker.time, "monotonic", lambda: clock[0])

    recalc_worker.run_recalc_worker_loop(
        stop_event=stop_event,
        poll_interval_seconds=0.5,
    )

    assert cursor_calls == [None, "fund-b"]
    assert stop_event.waits == [0.5, 0.5]


def test_worker_error_backoff_is_bounded_and_logs_at_sparse_intervals() -> None:
    from watchlist_app.services import recalc_worker

    delays = [
        recalc_worker._worker_error_backoff_seconds(
            failures,
            poll_interval_seconds=0.5,
        )
        for failures in range(1, 10)
    ]

    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0, 60.0]
    assert [
        failures
        for failures in range(1, 18)
        if recalc_worker._should_log_worker_error(failures)
    ] == [1, 2, 4, 8, 16]


def test_worker_retries_same_page_after_registry_failure_without_spinning(
    monkeypatch,
) -> None:
    from watchlist_app.services import recalc_worker
    from watchlist_app.services.read_model_freshness import ReconciliationBatchResult

    settings = SimpleNamespace(
        recalc_worker_poll_interval_seconds=60.0,
        recalc_worker_reconcile_interval_seconds=60.0,
        recalc_worker_reconcile_batch_size=2,
    )
    cursor_calls: list[str | None] = []
    clock = [100.0]

    class ControlledStopEvent:
        def __init__(self) -> None:
            self.done = False
            self.waits: list[float] = []

        def is_set(self) -> bool:
            return self.done

        def wait(self, seconds: float) -> bool:
            if self.done:
                return True
            assert seconds > 0
            self.waits.append(seconds)
            clock[0] += seconds
            return False

    stop_event = ControlledStopEvent()

    def reconcile(*, limit: int, after_instrument_id: str | None):
        assert limit == 2
        cursor_calls.append(after_instrument_id)
        if len(cursor_calls) == 1:
            raise RuntimeError("registry unavailable")
        stop_event.done = True
        return ReconciliationBatchResult(1, 0, None, True)

    monkeypatch.setattr(recalc_worker, "get_settings", lambda: settings)
    monkeypatch.setattr(
        recalc_worker,
        "reconcile_stale_instrument_read_models",
        reconcile,
    )
    monkeypatch.setattr(recalc_worker, "process_next_recalc_job", lambda: False)
    monkeypatch.setattr(recalc_worker.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(recalc_worker.logger, "exception", lambda *_args: None)

    recalc_worker.run_recalc_worker_loop(
        stop_event=stop_event,
        poll_interval_seconds=60.0,
    )

    assert cursor_calls == [None, None]
    assert stop_event.waits == [60.0]
