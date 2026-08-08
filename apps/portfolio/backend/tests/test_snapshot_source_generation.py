from __future__ import annotations

from copy import deepcopy

from portfolio_ops_instrument_core.db_models import Instrument

from portfolio_app.db.models import PortfolioCalculationStateModel, PortfolioDailySnapshotModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services import daily_snapshots


def _published_snapshot_fingerprint() -> list[tuple[object, str, dict[str, object]]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        rows = (
            session.query(PortfolioDailySnapshotModel)
            .filter(PortfolioDailySnapshotModel.portfolio_id == "portfolio-ops")
            .order_by(PortfolioDailySnapshotModel.as_of_date)
            .all()
        )
        return [
            (row.as_of_date, row.calculated_at, deepcopy(row.snapshot_json))
            for row in rows
        ]


def _advance_market_data_generation(value: str) -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        instrument = session.get(Instrument, "equity-us-abbv")
        assert instrument is not None
        instrument.market_data_updated_at = value
        session.commit()


def test_stable_snapshot_refresh_persists_its_exact_source_generation() -> None:
    result = daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(
        "portfolio-ops"
    )

    assert result is not None
    assert result["source_generation_status"] == "stable"
    assert result["source_generation_reason"] is None
    assert result["discarded_attempt_count"] == 0
    assert result["source_generation_before"] == result["source_generation_after"]

    captured_generation = result["source_generation_before"]
    assert isinstance(captured_generation, dict)
    assert captured_generation["refresh_request_id"]

    session_factory = get_session_factory()
    with session_factory() as session:
        state = session.get(PortfolioCalculationStateModel, "portfolio-ops")
        rows = (
            session.query(PortfolioDailySnapshotModel)
            .filter(PortfolioDailySnapshotModel.portfolio_id == "portfolio-ops")
            .all()
        )
        assert state is not None
        assert rows
        assert captured_generation["market_data_updated_at"] == state.source_market_data_updated_at
        assert all(row.snapshot_json["source_generation"] == captured_generation for row in rows)


def test_legacy_calculation_version_forces_full_snapshot_rebuild(monkeypatch) -> None:
    result = daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(
        "portfolio-ops"
    )
    assert result is not None

    session_factory = get_session_factory()
    with session_factory() as session:
        state = session.get(PortfolioCalculationStateModel, "portfolio-ops")
        latest = (
            session.query(PortfolioDailySnapshotModel)
            .filter(PortfolioDailySnapshotModel.portfolio_id == "portfolio-ops")
            .order_by(PortfolioDailySnapshotModel.as_of_date.desc())
            .first()
        )
        assert state is not None
        assert latest is not None
        legacy_payload = dict(latest.snapshot_json)
        legacy_payload["calculation_version"] = "legacy-derivative-accounting"
        latest.snapshot_json = legacy_payload
        state.daily_snapshot_status = "current"
        state.dirty_from = latest.as_of_date
        session.commit()

    original_builder = daily_snapshots.performance.build_daily_portfolio_snapshots
    requested_start_dates: list[object] = []

    def capture_rebuild_scope(*args, **kwargs):
        requested_start_dates.append(kwargs.get("start_date"))
        return original_builder(*args, **kwargs)

    monkeypatch.setattr(
        daily_snapshots.performance,
        "build_daily_portfolio_snapshots",
        capture_rebuild_scope,
    )

    daily_snapshots.ensure_portfolio_daily_snapshots("portfolio-ops")

    assert requested_start_dates == [None]
    with session_factory() as session:
        rows = (
            session.query(PortfolioDailySnapshotModel)
            .filter(PortfolioDailySnapshotModel.portfolio_id == "portfolio-ops")
            .all()
        )
        state = session.get(PortfolioCalculationStateModel, "portfolio-ops")
        assert rows
        assert state is not None
        assert state.daily_snapshot_status == "current"
        assert all(
            row.snapshot_json["calculation_version"]
            == daily_snapshots.DAILY_SNAPSHOT_CALCULATION_VERSION
            for row in rows
        )


def test_source_generation_change_discards_first_output_then_replays(monkeypatch) -> None:
    assert (
        daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(
            "portfolio-ops"
        )
        is not None
    )
    published_before = _published_snapshot_fingerprint()
    assert published_before
    daily_snapshots.mark_portfolio_daily_snapshots_stale("portfolio-ops")

    original_builder = daily_snapshots.performance.build_daily_portfolio_snapshots
    build_calls = {"count": 0}

    def build_while_source_changes(*args, **kwargs):
        build_calls["count"] += 1
        snapshots = original_builder(*args, **kwargs)
        assert snapshots
        if build_calls["count"] == 1:
            snapshots[-1]["nav"] = 987_654_321.0
            _advance_market_data_generation("2099-01-01T00:00:00.000001Z")
        else:
            # The first generation was never published, even transiently between retries.
            assert _published_snapshot_fingerprint() == published_before
        return snapshots

    monkeypatch.setattr(
        daily_snapshots.performance,
        "build_daily_portfolio_snapshots",
        build_while_source_changes,
    )

    result = daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(
        "portfolio-ops"
    )

    assert build_calls["count"] == 2
    assert result is not None
    assert result["source_generation_status"] == "stable_after_retry"
    assert result["source_generation_reason"] == "source_generation_changed_during_calculation"
    assert result["discarded_attempt_count"] == 1
    assert result["source_generation_before"] != result["source_generation_after"]

    session_factory = get_session_factory()
    with session_factory() as session:
        state = session.get(PortfolioCalculationStateModel, "portfolio-ops")
        rows = (
            session.query(PortfolioDailySnapshotModel)
            .filter(PortfolioDailySnapshotModel.portfolio_id == "portfolio-ops")
            .all()
        )
        assert state is not None
        assert state.daily_snapshot_status == "current"
        assert state.error_message is None
        assert state.source_market_data_updated_at == "2099-01-01T00:00:00.000001Z"
        assert rows
        assert all(row.nav != 987_654_321.0 for row in rows)
        assert all(
            row.snapshot_json["source_generation"]["market_data_updated_at"]
            == "2099-01-01T00:00:00.000001Z"
            for row in rows
        )


def test_repeated_source_generation_change_returns_explicit_discard_status(
    monkeypatch,
) -> None:
    assert (
        daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(
            "portfolio-ops"
        )
        is not None
    )
    published_before = _published_snapshot_fingerprint()
    original_builder = daily_snapshots.performance.build_daily_portfolio_snapshots

    def build_while_source_changes(*args, **kwargs):
        snapshots = original_builder(*args, **kwargs)
        assert snapshots
        snapshots[-1]["nav"] = 987_654_321.0
        _advance_market_data_generation("2099-01-01T00:00:00.000002Z")
        return snapshots

    monkeypatch.setattr(daily_snapshots, "_SOURCE_GENERATION_MAX_DISCARDS", 1)
    monkeypatch.setattr(
        daily_snapshots.performance,
        "build_daily_portfolio_snapshots",
        build_while_source_changes,
    )

    daily_snapshots.enqueue_selected_portfolio_daily_snapshot_recalculations(
        ["portfolio-ops"]
    )
    payload = daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(
        "portfolio-ops"
    )

    assert payload is not None
    assert payload["source_generation_status"] == "discarded"
    assert payload["source_generation_reason"] == "source_generation_changed_during_calculation"
    assert payload["discarded_attempt_count"] == 1
    assert payload["source_generation_before"] != payload["source_generation_after"]
    assert _published_snapshot_fingerprint() == published_before

    session_factory = get_session_factory()
    with session_factory() as session:
        state = session.get(PortfolioCalculationStateModel, "portfolio-ops")
        assert state is not None
        assert state.daily_snapshot_status == "stale"
        assert state.error_message == "source_generation_changed_during_calculation"
