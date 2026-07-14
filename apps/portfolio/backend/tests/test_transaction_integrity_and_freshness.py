from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from threading import Barrier

import pytest
from portfolio_ops_instrument_core.db_models import Instrument

from portfolio_app.db.models import PortfolioCalculationStateModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services import daily_snapshots, ledger, portfolio_store


def _create_deposit(note: str) -> dict[str, object]:
    return portfolio_store.create_transaction(
        portfolio_id="portfolio-ops",
        transaction_type="deposit",
        trade_date=date(2026, 4, 16),
        trade_time=None,
        settlement_date=date(2026, 4, 16),
        entitlement_date=None,
        acquisition_date=None,
        account_id="cash-usd-main",
        settlement_cash_account_id=None,
        instrument_id=None,
        instrument_ref=None,
        quantity=None,
        price=None,
        gross_amount=100.0,
        counter_amount=None,
        fx_rate=None,
        fees=0.0,
        taxes=0.0,
        currency="USD",
        transfer_scope=None,
        transfer_object_type=None,
        transfer_group_id=None,
        counterparty_account_id=None,
        note=note,
    )


def _create_abbv_sale(quantity: float, note: str) -> dict[str, object]:
    source = portfolio_store.get_transaction("portfolio-ops", "txn-0003")
    assert source is not None
    return portfolio_store.create_transaction(
        portfolio_id="portfolio-ops",
        transaction_type="sell",
        trade_date=date(2026, 4, 16),
        trade_time=None,
        settlement_date=date(2026, 4, 16),
        entitlement_date=None,
        acquisition_date=None,
        account_id="broker-us-core",
        settlement_cash_account_id="cash-usd-main",
        instrument_id="equity-us-abbv",
        instrument_ref=deepcopy(source["instrument_ref"]),
        quantity=quantity,
        price=200.0,
        gross_amount=quantity * 200.0,
        counter_amount=None,
        fx_rate=None,
        fees=0.0,
        taxes=0.0,
        currency="USD",
        transfer_scope=None,
        transfer_object_type=None,
        transfer_group_id=None,
        counterparty_account_id=None,
        note=note,
    )


def test_live_portfolio_nav_is_unavailable_when_an_account_cannot_be_valued(monkeypatch) -> None:
    detail_loader = ledger.get_registry_instrument_details
    monkeypatch.setattr(
        ledger,
        "get_registry_instrument_details",
        lambda instrument_ids: {
            instrument_id: (
                None
                if instrument_id == "equity-us-abbv"
                else deepcopy(detail_loader({instrument_id}).get(instrument_id))
            )
            for instrument_id in instrument_ids
        },
    )

    portfolio = portfolio_store.get_portfolio_live_summary("portfolio-ops")

    assert portfolio is not None
    assert portfolio["nav"] is None
    assert portfolio["coverage_state"] == "partial"
    assert "broker-us-core" in portfolio["valuation_coverage"]["missing_account_ids"]


def test_concurrent_position_sales_cannot_oversell() -> None:
    barrier = Barrier(2)

    def sell(index: int) -> tuple[str, object]:
        barrier.wait()
        try:
            return "created", _create_abbv_sale(600.0, f"concurrent sale {index}")
        except ValueError as error:
            return "rejected", error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(sell, range(2)))

    assert sorted(status for status, _result in results) == ["created", "rejected"]
    rejected_error = next(result for status, result in results if status == "rejected")
    assert "exceeds account position" in str(rejected_error)


def test_concurrent_transaction_ids_are_unique() -> None:
    barrier = Barrier(2)

    def deposit(index: int) -> dict[str, object]:
        barrier.wait()
        return _create_deposit(f"concurrent deposit {index}")

    with ThreadPoolExecutor(max_workers=2) as executor:
        created = list(executor.map(deposit, range(2)))

    assert len({str(record["transaction_id"]) for record in created}) == 2


def test_transaction_and_snapshot_invalidation_roll_back_together(monkeypatch) -> None:
    before_ids = {
        str(record["transaction_id"])
        for record in portfolio_store.list_transactions("portfolio-ops")
    }

    def fail_invalidation(*args, **kwargs) -> None:
        del args, kwargs
        raise RuntimeError("stale-state write failed")

    monkeypatch.setattr(
        daily_snapshots,
        "mark_portfolio_daily_snapshots_stale",
        fail_invalidation,
    )

    with pytest.raises(RuntimeError, match="stale-state write failed"):
        _create_deposit("must roll back")

    after_ids = {
        str(record["transaction_id"])
        for record in portfolio_store.list_transactions("portfolio-ops")
    }
    assert after_ids == before_ids


def test_deleting_a_position_source_fact_cannot_invalidate_later_sales() -> None:
    with pytest.raises(ValueError, match="exceeds account position"):
        portfolio_store.delete_transactions(
            "portfolio-ops",
            transaction_ids=["txn-0003"],
        )

    assert portfolio_store.get_transaction("portfolio-ops", "txn-0003") is not None


def test_expired_daily_snapshot_refresh_lease_is_recovered() -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        state = session.get(PortfolioCalculationStateModel, "portfolio-ops")
        if state is None:
            state = PortfolioCalculationStateModel(
                portfolio_id="portfolio-ops",
                daily_snapshot_status="running",
            )
            session.add(state)
        state.daily_snapshot_status = "running"
        state.refresh_request_id = "abandoned-worker"
        state.refresh_started_at = (
            datetime.now(UTC) - timedelta(seconds=daily_snapshots._RUNNING_REFRESH_LEASE_SECONDS + 1)
        ).isoformat()
        session.commit()

    result = daily_snapshots.refresh_portfolio_daily_snapshots("portfolio-ops")

    assert result is not None
    with session_factory() as session:
        state = session.get(PortfolioCalculationStateModel, "portfolio-ops")
        assert state is not None
        assert state.daily_snapshot_status == "current"
        assert state.refresh_request_id is None
        assert state.refresh_completed_at is not None


def test_market_data_watermark_self_invalidates_without_notification() -> None:
    daily_snapshots.refresh_portfolio_daily_snapshots("portfolio-ops")
    session_factory = get_session_factory()
    with session_factory() as session:
        state = session.get(PortfolioCalculationStateModel, "portfolio-ops")
        assert state is not None
        previous_watermark = state.source_market_data_updated_at
        instrument = session.get(Instrument, "equity-us-abbv")
        assert instrument is not None
        instrument.market_data_updated_at = "2099-01-01T00:00:00.000000Z"
        session.commit()

    daily_snapshots.ensure_portfolio_daily_snapshots("portfolio-ops")

    with session_factory() as session:
        state = session.get(PortfolioCalculationStateModel, "portfolio-ops")
        assert state is not None
        assert state.daily_snapshot_status == "current"
        assert state.source_market_data_updated_at == "2099-01-01T00:00:00.000000Z"
        assert state.source_market_data_updated_at != previous_watermark
