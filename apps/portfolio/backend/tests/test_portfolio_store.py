from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest

from portfolio_app.db.models import (
    AccountRecordModel,
    PortfolioCalculationStateModel,
    PortfolioDailySnapshotModel,
    PortfolioRecordModel,
    TransactionCurrentModel,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services import daily_snapshots, portfolio_store
from portfolio_app.services.daily_snapshots import DAILY_SNAPSHOT_CALCULATION_VERSION


def _current_transaction(
    *,
    transaction_id: str,
    transaction_type: str,
    trade_date: date,
    settlement_date: date,
    account_id: str,
    instrument_id: str | None,
    instrument_snapshot: dict[str, object] | None,
    quantity: str | None,
    price: str | None,
    gross_amount: str,
    currency: str,
) -> TransactionCurrentModel:
    trade_at = datetime.combine(trade_date, time(4), tzinfo=UTC)
    return TransactionCurrentModel(
        transaction_id=transaction_id,
        portfolio_id="p1",
        current_revision_id=f"revision-{transaction_id}",
        current_revision_number=1,
        revision_group_id=f"group-{transaction_id}",
        revision_kind="create",
        payload_schema_version="transaction-revision.v1",
        payload_hash=f"sha256:{'0' * 64}",
        created_at=trade_at,
        created_by="test:portfolio-store",
        source_kind="system",
        change_reason="Test current transaction projection",
        actor_type="service",
        actor_id="test:portfolio-store",
        actor_display_name="Portfolio store test",
        actor_source="trusted_service",
        recorded_at=trade_at,
        transaction_type=transaction_type,
        trade_date=trade_date,
        trade_time=time(12),
        trade_at=trade_at,
        trade_timezone="Asia/Shanghai",
        trade_time_is_estimated=True,
        settlement_date=settlement_date,
        account_id=account_id,
        instrument_id=instrument_id,
        instrument_snapshot_json=deepcopy(instrument_snapshot),
        quantity=Decimal(quantity) if quantity is not None else None,
        price=Decimal(price) if price is not None else None,
        gross_amount=Decimal(gross_amount),
        fees=Decimal("0"),
        taxes=Decimal("0"),
        currency=currency,
    )


def test_reset_store_rejects_destructive_reset_after_ledger_initialization() -> None:
    before_ids = {
        item["transaction_id"]
        for item in portfolio_store.list_transactions("portfolio-ops")
    }

    with pytest.raises(
        RuntimeError,
        match="cannot overwrite an initialized append-only transaction ledger",
    ):
        portfolio_store.reset_store()

    assert {
        item["transaction_id"]
        for item in portfolio_store.list_transactions("portfolio-ops")
    } == before_ids


def test_database_load_round_trips_manual_instrument_universe() -> None:
    instrument_ref = {
        "instrument_id": "fund-us-watch",
        "instrument_name": "Watchlist Fund",
        "instrument_type": "fund",
        "currency": "USD",
        "identifiers": [
            {
                "identifier_type": "ticker",
                "identifier_value": "WATCH",
                "is_primary": True,
            }
        ],
    }
    persisted = portfolio_store.upsert_portfolio_instrument_universe_record(
        "portfolio-ops",
        "fund-us-watch",
        instrument_ref=instrument_ref,
    )
    assert persisted is not None

    session_factory = get_session_factory()
    with session_factory() as session:
        loaded = portfolio_store._load_store_from_db(session)
    loaded_row = next(
        row
        for row in loaded["instrument_universe"]
        if row["portfolio_id"] == "portfolio-ops"
        and row["instrument_id"] == "fund-us-watch"
    )
    assert loaded_row["source"] == "manual"
    assert loaded_row["holding_state"] == "not_held"
    assert loaded_row["instrument_ref"]["instrument_name"] == "Watchlist Fund"


def _seed_daily_snapshot(
    *,
    portfolio_id: str,
    as_of_date: date,
    nav: float,
    daily_twr: float | None,
    stale_price_flag: bool = False,
    stale_fx_flag: bool = False,
    twr_state: str = "linked",
    twr_reliability_status: str = "reliable",
    twr_reliability_reasons: list[str] | None = None,
) -> PortfolioDailySnapshotModel:
    beginning_nav = nav / (1 + daily_twr) if daily_twr is not None else None
    absolute_change = nav - beginning_nav if beginning_nav is not None else None
    return PortfolioDailySnapshotModel(
        portfolio_id=portfolio_id,
        as_of_date=as_of_date,
        nav_coverage_state="complete",
        nav_coverage_reason_codes=[],
        book_pnl_coverage_state="complete",
        book_pnl_coverage_reason_codes=[],
        nav=nav,
        beginning_nav=beginning_nav,
        ending_nav=nav,
        daily_twr=daily_twr,
        cumulative_twr=daily_twr,
        drawdown=0.0 if daily_twr is not None else None,
        snapshot_json={
            "portfolio_id": portfolio_id,
            "as_of_date": as_of_date.isoformat(),
            "base_currency": "USD",
            "nav_coverage_state": "complete",
            "nav_coverage_reason_codes": [],
            "book_pnl_coverage_state": "complete",
            "book_pnl_coverage_reason_codes": [],
            "fx_dependency_manifest": {
                "dependencies": [],
                "fingerprint": "0" * 64,
            },
            "stale_price_flag": stale_price_flag,
            "stale_fx_flag": stale_fx_flag,
            "nav": nav,
            "beginning_nav": beginning_nav,
            "ending_nav": nav,
            "external_cash_in": 0.0,
            "external_cash_out": 0.0,
            "net_external_inflow": 0.0,
            "absolute_change": absolute_change,
            "delta": absolute_change,
            "daily_twr": daily_twr,
            "cumulative_twr": daily_twr,
            "drawdown": 0.0,
            "total_position_count": 2,
            "market_observation_count": 2,
            "return_observation_eligible": daily_twr is not None,
            "twr_state": twr_state,
            "twr_reliability_status": twr_reliability_status,
            "twr_reliability_reasons": list(twr_reliability_reasons or []),
            "realized_pnl": 0.0,
            "cash_currency_gains": 0.0,
            "instrument_currency_gains": 0.0,
            "total_pnl": 0.0,
            "calculation_version": DAILY_SNAPSHOT_CALCULATION_VERSION,
        },
        calculated_at="2026-05-21T00:00:00Z",
    )


def test_incremental_snapshot_seed_rejects_existing_twr_boundary() -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        session.add(
            _seed_daily_snapshot(
                portfolio_id="portfolio-ops",
                as_of_date=date(2026, 5, 20),
                nav=100.0,
                daily_twr=0.01,
            )
        )
        session.add(
            _seed_daily_snapshot(
                portfolio_id="portfolio-ops",
                as_of_date=date(2026, 5, 21),
                nav=100.0,
                daily_twr=None,
                twr_state="broken",
                twr_reliability_status="unavailable",
                twr_reliability_reasons=["stale_valuation_on_external_flow"],
            )
        )
        session.flush()

        assert (
            daily_snapshots._incremental_snapshot_seed(
                session,
                "portfolio-ops",
                dirty_from=date(2026, 5, 22),
            )
            is None
        )


def test_incremental_snapshot_rebase_never_links_across_broken_boundary() -> None:
    snapshots = [
        {
            "as_of_date": date(2026, 1, 2),
            "twr_state": "linked",
            "daily_twr": 0.10,
            "cash_currency_gains": 0.0,
            "instrument_currency_gains": 0.0,
            "total_pnl": 10.0,
        },
        {
            "as_of_date": date(2026, 1, 3),
            "twr_state": "broken",
            "daily_twr": None,
            "cash_currency_gains": 0.0,
            "instrument_currency_gains": 0.0,
            "total_pnl": 10.0,
        },
        {
            "as_of_date": date(2026, 1, 4),
            "twr_state": "reanchor",
            "daily_twr": None,
            "cash_currency_gains": 0.0,
            "instrument_currency_gains": 0.0,
            "total_pnl": 10.0,
        },
        {
            "as_of_date": date(2026, 1, 5),
            "twr_state": "linked",
            "daily_twr": 0.05,
            "cash_currency_gains": 0.0,
            "instrument_currency_gains": 0.0,
            "total_pnl": 15.0,
        },
    ]

    daily_snapshots._rebase_incremental_snapshots(
        snapshots,
        seed={
            "seed_date": date(2026, 1, 1),
            "cumulative_twr": 0.02,
            "peak_growth": 1.02,
            "cash_currency_gains": 0.0,
            "instrument_currency_gains": 0.0,
        },
    )

    assert snapshots[0]["cumulative_twr"] == 0.12200000000000011
    assert snapshots[0]["drawdown"] == 0.0
    for snapshot in snapshots[1:]:
        assert snapshot["cumulative_twr"] is None
        assert snapshot["drawdown"] is None


def test_portfolio_summary_prefers_latest_fresh_complete_snapshot(client) -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        session.add(
            _seed_daily_snapshot(
                portfolio_id="portfolio-ops",
                as_of_date=date(2026, 5, 20),
                nav=100.0,
                daily_twr=0.02,
            )
        )
        session.add(
            _seed_daily_snapshot(
                portfolio_id="portfolio-ops",
                as_of_date=date(2026, 5, 21),
                nav=101.0,
                daily_twr=0.01,
                stale_fx_flag=True,
            )
        )
        session.add(
            _seed_daily_snapshot(
                portfolio_id="portfolio-ops",
                as_of_date=date(2026, 5, 22),
                nav=102.0,
                daily_twr=0.01,
                stale_price_flag=True,
            )
        )
        state = session.get(PortfolioCalculationStateModel, "portfolio-ops")
        if state is None:
            state = PortfolioCalculationStateModel(portfolio_id="portfolio-ops", daily_snapshot_status="current")
            session.add(state)
        state.daily_snapshot_status = "current"
        state.refreshed_from = date(2026, 5, 20)
        state.refreshed_to = date(2026, 5, 22)
        state.refreshed_at = "2026-05-21T00:00:00Z"
        state.source_market_data_updated_at = daily_snapshots._source_market_data_watermark(
            session,
            "portfolio-ops",
        )
        state.refresh_request_id = None
        session.commit()

    portfolio = portfolio_store.get_portfolio("portfolio-ops")
    assert portfolio is not None
    assert portfolio["as_of_date"] == "2026-05-21"
    assert portfolio["nav"] == 101.0

    portfolio_rows = portfolio_store.list_portfolios()
    portfolio_ops_row = next(row for row in portfolio_rows if row["portfolio_id"] == "portfolio-ops")
    assert portfolio_ops_row["as_of_date"] == "2026-05-21"

    response = client.get("/api/workspace/summary", params={"portfolio_id": "portfolio-ops"})
    assert response.status_code == 200
    assert response.json()["as_of_date"] == "2026-05-21"

    holdings_response = client.get("/api/workspace/holdings", params={"portfolio_id": "portfolio-ops"})
    assert holdings_response.status_code == 200
    holdings_payload = holdings_response.json()
    assert holdings_payload["as_of_date"] == "2026-05-21"
    assert holdings_payload["risk_policy"]["model_role"] == "production"
    assert holdings_payload["forward_risk"]["status"] in {"ok", "unavailable"}
    assert all("forward_risk_status" in row for row in holdings_payload["rows"])

    performance_response = client.get("/api/portfolios/portfolio-ops/performance", params={"end_date": "2026-05-21"})
    assert performance_response.status_code == 200
    assert performance_response.json()["summary"]["end_date"] == "2026-05-21"


def test_live_portfolio_as_of_uses_current_holding_market_date(monkeypatch) -> None:
    portfolio = PortfolioRecordModel(
        portfolio_id="p1",
        portfolio_name="Portfolio",
        base_currency="CNY",
        valuation_timezone="Asia/Shanghai",
        valuation_cutoff_policy="latest_complete_eod",
        as_of_date=date(2026, 4, 23),
        nav=0.0,
        day_change_value=0.0,
        day_change_pct=0.0,
        securities_count=0,
        sort_order=0,
    )
    account = AccountRecordModel(
        account_id="broker",
        portfolio_id="p1",
        account_name="Broker",
        account_type="securities_account",
        currency="CNY",
        institution=None,
        default_settlement_cash_account_id=None,
        cost_basis_method="fifo",
        allowed_instrument_types_json=None,
        opened_at=None,
        closed_at=None,
        status="active",
    )
    transactions = [
        _current_transaction(
            transaction_id="buy-sold",
            transaction_type="buy",
            trade_date=date(2026, 4, 1),
            settlement_date=date(2026, 4, 1),
            account_id="broker",
            instrument_id="sold",
            instrument_snapshot={
                "instrument_id": "sold",
                "instrument_name": "Sold instrument",
                "instrument_type": "fund",
                "currency": "CNY",
            },
            quantity="100",
            price="1",
            gross_amount="100",
            currency="CNY",
        ),
        _current_transaction(
            transaction_id="sell-sold",
            transaction_type="sell",
            trade_date=date(2026, 4, 20),
            settlement_date=date(2026, 4, 20),
            account_id="broker",
            instrument_id="sold",
            instrument_snapshot={
                "instrument_id": "sold",
                "instrument_name": "Sold instrument",
                "instrument_type": "fund",
                "currency": "CNY",
            },
            quantity="100",
            price="1",
            gross_amount="100",
            currency="CNY",
        ),
        _current_transaction(
            transaction_id="buy-open",
            transaction_type="buy",
            trade_date=date(2026, 4, 24),
            settlement_date=date(2026, 4, 24),
            account_id="broker",
            instrument_id="open",
            instrument_snapshot={
                "instrument_id": "open",
                "instrument_name": "Open instrument",
                "instrument_type": "fund",
                "currency": "CNY",
            },
            quantity="100",
            price="1",
            gross_amount="100",
            currency="CNY",
        ),
    ]

    def fake_latest_market_date(_session, instrument_ids):
        if instrument_ids == {"sold", "open"}:
            return date(2026, 4, 29)
        if instrument_ids == {"open"}:
            return date(2026, 4, 28)
        return None

    monkeypatch.setattr(portfolio_store, "_latest_market_data_date_for_instruments", fake_latest_market_date)
    monkeypatch.setattr(
        portfolio_store,
        "build_position_lots",
        lambda *args, **kwargs: [{"instrument_id": "open"}],
    )

    assert portfolio_store._resolve_live_portfolio_as_of_date(
        object(),
        portfolio,
        accounts=[account],
        transactions=transactions,
    ) == date(2026, 4, 28)


def test_live_portfolio_as_of_uses_settlement_activity_date(monkeypatch) -> None:
    portfolio = PortfolioRecordModel(
        portfolio_id="p1",
        portfolio_name="Portfolio",
        base_currency="CNY",
        valuation_timezone="Asia/Shanghai",
        valuation_cutoff_policy="latest_complete_eod",
        as_of_date=date(2026, 5, 21),
        nav=0.0,
        day_change_value=0.0,
        day_change_pct=0.0,
        securities_count=0,
        sort_order=0,
    )
    account = AccountRecordModel(
        account_id="cash",
        portfolio_id="p1",
        account_name="Cash",
        account_type="deposit_account",
        currency="CNY",
        institution=None,
        default_settlement_cash_account_id=None,
        cost_basis_method=None,
        allowed_instrument_types_json=None,
        opened_at=None,
        closed_at=None,
        status="active",
    )
    transactions = [
        _current_transaction(
            transaction_id="redeem-proceeds",
            transaction_type="deposit",
            trade_date=date(2026, 5, 21),
            settlement_date=date(2026, 5, 26),
            account_id="cash",
            instrument_id=None,
            instrument_snapshot=None,
            quantity=None,
            price=None,
            gross_amount="100",
            currency="CNY",
        ),
    ]

    monkeypatch.setattr(
        portfolio_store,
        "_latest_market_data_date_for_instruments",
        lambda _session, instrument_ids: date(2026, 5, 20) if instrument_ids == {"open"} else None,
    )
    monkeypatch.setattr(
        portfolio_store,
        "build_position_lots",
        lambda *args, **kwargs: [{"instrument_id": "open"}],
    )

    assert portfolio_store._resolve_live_portfolio_as_of_date(
        object(),
        portfolio,
        accounts=[account],
        transactions=transactions,
    ) == date(2026, 5, 26)


def test_live_portfolio_as_of_ignores_stale_cached_portfolio_date(monkeypatch) -> None:
    portfolio = PortfolioRecordModel(
        portfolio_id="p1",
        portfolio_name="Portfolio",
        base_currency="CNY",
        valuation_timezone="Asia/Shanghai",
        valuation_cutoff_policy="latest_complete_eod",
        as_of_date=date(2026, 5, 21),
        nav=0.0,
        day_change_value=0.0,
        day_change_pct=0.0,
        securities_count=0,
        sort_order=0,
    )
    account = AccountRecordModel(
        account_id="broker",
        portfolio_id="p1",
        account_name="Broker",
        account_type="securities_account",
        currency="CNY",
        institution=None,
        default_settlement_cash_account_id=None,
        cost_basis_method="fifo",
        allowed_instrument_types_json=None,
        opened_at=None,
        closed_at=None,
        status="active",
    )
    transactions = [
        _current_transaction(
            transaction_id="buy-open",
            transaction_type="buy",
            trade_date=date(2026, 4, 24),
            settlement_date=date(2026, 4, 24),
            account_id="broker",
            instrument_id="open",
            instrument_snapshot={
                "instrument_id": "open",
                "instrument_name": "Open instrument",
                "instrument_type": "fund",
                "currency": "CNY",
            },
            quantity="100",
            price="1",
            gross_amount="100",
            currency="CNY",
        ),
    ]

    monkeypatch.setattr(
        portfolio_store,
        "_latest_market_data_date_for_instruments",
        lambda _session, instrument_ids: date(2026, 4, 28) if instrument_ids == {"open"} else None,
    )
    monkeypatch.setattr(
        portfolio_store,
        "build_position_lots",
        lambda *args, **kwargs: [{"instrument_id": "open"}],
    )

    assert portfolio_store._resolve_live_portfolio_as_of_date(
        object(),
        portfolio,
        accounts=[account],
        transactions=transactions,
    ) == date(2026, 4, 28)
