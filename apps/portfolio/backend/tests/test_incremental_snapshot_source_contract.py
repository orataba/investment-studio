from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

from investment_studio_instrument_core.db_models import Instrument
from portfolio_app.db.models import (
    PortfolioTaxonomyStateModel,
    AccountRecordModel,
    PortfolioCalculationStateModel,
    PortfolioDailySnapshotModel,
    PortfolioRecordModel,
    TransactionRecordModel,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services import daily_snapshots
from .test_postgres_instrument_registry_constraints import postgres_portfolio_env


PORTFOLIO_ID = "investment-studio"


def _published():
    with get_session_factory()() as session:
        return [
            (row.as_of_date, row.calculated_at, deepcopy(row.snapshot_json))
            for row in session.query(PortfolioDailySnapshotModel)
            .filter(PortfolioDailySnapshotModel.portfolio_id == PORTFOLIO_ID)
            .order_by(PortfolioDailySnapshotModel.as_of_date).all()
        ]


def _baseline():
    assert daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(PORTFOLIO_ID)
    rows = _published()
    reliable_dates = [day for day, _, payload in rows
                      if payload.get("valuation_coverage_state") == "complete" and payload.get("nav") is not None]
    assert len(reliable_dates) >= 2
    return rows, reliable_dates[-1], reliable_dates[-2]


def _change_source(field):
    with get_session_factory()() as session:
        if field == "taxonomy_configuration_version":
            state = session.get(PortfolioTaxonomyStateModel, PORTFOLIO_ID)
            if state is None:
                session.add(PortfolioTaxonomyStateModel(
                    portfolio_id=PORTFOLIO_ID, current_version=1,
                    updated_at="2099-01-01T00:00:00.000001Z",
                ))
            else:
                state.current_version += 1
        else:
            instrument = session.get(Instrument, "equity-us-abbv")
            assert instrument is not None
            setattr(instrument, field, "2099-01-01T00:00:00.000001Z")
        session.commit()


def _capture_builder(monkeypatch, before_build=None):
    original = daily_snapshots.performance.build_daily_portfolio_snapshots
    starts = []

    def capture(*args, **kwargs):
        starts.append(kwargs.get("start_date"))
        if before_build is not None:
            before_build(len(starts))
        return original(*args, **kwargs)

    monkeypatch.setattr(daily_snapshots.performance, "build_daily_portfolio_snapshots", capture)
    return starts


@pytest.mark.parametrize("field", [
    "market_data_updated_at", "calculation_inputs_updated_at", "taxonomy_configuration_version",
])
@pytest.mark.parametrize("when", ["queued", "claimed"])
def test_missed_source_notification_cannot_reuse_a_transaction_dirty_prefix(monkeypatch, field, when):
    _, dirty_from, _ = _baseline()
    daily_snapshots.mark_portfolio_daily_snapshots_stale(PORTFOLIO_ID, dirty_from=dirty_from)
    claim = daily_snapshots._claim_daily_snapshot_refresh(PORTFOLIO_ID) if when == "claimed" else None
    # Source facts committed independently; no registry enqueue callback arrives.
    _change_source(field)
    events = []
    monkeypatch.setattr(daily_snapshots, "emit", lambda event, **fields: events.append({"event": event, **fields}))
    starts = _capture_builder(monkeypatch)
    if claim is not None:
        assert claim["status"] == "claimed"
        result, superseded = daily_snapshots._recalculate_portfolio_daily_snapshots_once(
            PORTFOLIO_ID, request_id=claim["request_id"],
        )
        assert not superseded
    else:
        result = daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(PORTFOLIO_ID)
    assert result is not None
    assert starts == [None]
    assert events == [{
        "event": "portfolio_snapshot_prefix_rejected", "portfolio_id": PORTFOLIO_ID,
        "reason": "source_generation_mismatch", "source_field": field,
    }]
    generation = result["source_generation_after"]
    assert all(payload["source_generation"] == generation for _, _, payload in _published())


def test_transaction_request_without_source_change_keeps_the_reliable_prefix(monkeypatch):
    original_rows, dirty_from, seed_date = _baseline()
    old_generation = original_rows[-1][2]["source_generation"]
    daily_snapshots.mark_portfolio_daily_snapshots_stale(PORTFOLIO_ID, dirty_from=dirty_from)
    starts = _capture_builder(monkeypatch)
    result = daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(PORTFOLIO_ID)
    assert result is not None
    assert starts == [seed_date]
    assert result["source_generation_after"]["refresh_request_id"] != old_generation["refresh_request_id"]
    assert [row for row in _published() if row[0] < dirty_from] == [row for row in original_rows if row[0] < dirty_from]


def test_normal_full_source_callback_still_overrides_a_partial_transaction_request(monkeypatch):
    _, dirty_from, _ = _baseline()
    daily_snapshots.mark_portfolio_daily_snapshots_stale(PORTFOLIO_ID, dirty_from=dirty_from)
    _change_source("market_data_updated_at")
    daily_snapshots.enqueue_portfolio_daily_snapshot_recalculations_for_instrument_change(
        instrument_ids=["equity-us-abbv"], dirty_from=None,
    )
    starts = _capture_builder(monkeypatch)
    result = daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(PORTFOLIO_ID)
    assert result is not None
    assert starts == [None]
    assert all(payload["source_generation"] == result["source_generation_after"] for _, _, payload in _published())


def test_source_change_after_seed_selection_discards_then_rebuilds_the_prefix(monkeypatch):
    original_rows, dirty_from, seed_date = _baseline()
    daily_snapshots.mark_portfolio_daily_snapshots_stale(PORTFOLIO_ID, dirty_from=dirty_from)

    def change_during_first_build(call_count):
        if call_count == 1:
            _change_source("market_data_updated_at")
        else:
            assert _published() == original_rows

    starts = _capture_builder(monkeypatch, change_during_first_build)
    result = daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(PORTFOLIO_ID)
    assert result is not None
    assert result["source_generation_status"] == "stable_after_retry"
    assert result["discarded_attempt_count"] == 1
    assert starts == [seed_date, None]
    with get_session_factory()() as session:
        state = session.get(PortfolioCalculationStateModel, PORTFOLIO_ID)
        assert state is not None
        assert state.daily_snapshot_status == "current"
        assert state.source_market_data_updated_at == "2099-01-01T00:00:00.000001Z"


@pytest.mark.parametrize("missing", [
    "calculation_version", "source_generation", "market_data_updated_at",
    "calculation_inputs_updated_at", "taxonomy_configuration_version",
])
def test_prefix_with_missing_source_evidence_cannot_seed_incremental_work(monkeypatch, missing):
    _, dirty_from, seed_date = _baseline()
    with get_session_factory()() as session:
        row = session.get(PortfolioDailySnapshotModel, (PORTFOLIO_ID, seed_date))
        assert row is not None
        payload = deepcopy(row.snapshot_json)
        if missing in {"calculation_version", "source_generation"}:
            payload.pop(missing)
        else:
            payload["source_generation"].pop(missing)
        row.snapshot_json = payload
        session.commit()
    daily_snapshots.mark_portfolio_daily_snapshots_stale(PORTFOLIO_ID, dirty_from=dirty_from)
    starts = _capture_builder(monkeypatch)
    assert daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(PORTFOLIO_ID)
    assert starts == [None]


@pytest.mark.postgresql_integration
def test_postgres_seed_rechecks_committed_source_after_a_transaction_claim(postgres_portfolio_env):
    instrument_id = postgres_portfolio_env["instrument_id"]
    seed_day, dirty_day = date(2026, 6, 1), date(2026, 6, 2)
    with get_session_factory()() as session:
        session.add(PortfolioRecordModel(
            portfolio_id=PORTFOLIO_ID, portfolio_name="Prefix source contract",
            base_currency="USD", valuation_timezone="UTC", valuation_cutoff_policy="close",
            inception_date=seed_day,
        ))
        session.add(AccountRecordModel(
            account_id="source-cash", portfolio_id=PORTFOLIO_ID,
            account_name="Source cash", account_type="deposit_account",
            account_category="cash", currency="USD", status="active",
        ))
        session.add(AccountRecordModel(
            account_id="source-security", portfolio_id=PORTFOLIO_ID,
            account_name="Source security", account_type="securities_account",
            default_settlement_cash_account_id="source-cash",
            account_category="security", cost_basis_method="fifo", currency="USD", status="active",
        ))
        session.commit()
        session.add(TransactionRecordModel(
            transaction_id="source-buy", transaction_sequence=1, portfolio_id=PORTFOLIO_ID,
            transaction_type="buy", trade_date=seed_day, trade_time="12:00",
            trade_at="2026-06-01T12:00:00Z", trade_timezone="UTC", settlement_date=seed_day,
            account_id="source-security", instrument_id=instrument_id,
            quantity=1, price=100, gross_amount=100, currency="USD",
        ))
        session.add(PortfolioCalculationStateModel(
            portfolio_id=PORTFOLIO_ID, daily_snapshot_status="running",
            dirty_from=dirty_day, refresh_request_id="new-transaction-request",
        ))
        session.commit()
        generation = daily_snapshots._snapshot_source_generation(session, PORTFOLIO_ID)
        assert generation is not None
        published_generation = {**generation.as_payload(), "refresh_request_id": "previous-publication"}
        session.add(PortfolioDailySnapshotModel(
            portfolio_id=PORTFOLIO_ID, as_of_date=seed_day, coverage_state="complete",
            valuation_coverage_state="complete", return_coverage_state="complete", nav=100,
            snapshot_json={
                "as_of_date": seed_day.isoformat(), "nav": 100,
                "calculation_version": daily_snapshots.DAILY_SNAPSHOT_CALCULATION_VERSION,
                "source_generation": published_generation,
            },
            calculated_at="2026-06-01T12:00:00Z",
        ))
        session.commit()

    def commit_source_change():
        with get_session_factory()() as writer:
            instrument = writer.get(Instrument, instrument_id)
            assert instrument is not None
            instrument.market_data_updated_at = "2099-01-01T00:00:00.000001Z"
            writer.commit()

    with get_session_factory()() as reader:
        assert daily_snapshots._incremental_snapshot_seed(
            reader, PORTFOLIO_ID, dirty_from=dirty_day, source_generation=generation,
        ) is not None
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(commit_source_change).result(timeout=10)
        current = daily_snapshots._snapshot_source_generation(reader, PORTFOLIO_ID)
        assert current is not None and current.market_data_updated_at != generation.market_data_updated_at
        assert daily_snapshots._incremental_snapshot_seed(
            reader, PORTFOLIO_ID, dirty_from=dirty_day, source_generation=current,
        ) is None
        assert reader.get(PortfolioCalculationStateModel, PORTFOLIO_ID).refresh_request_id == "new-transaction-request"
