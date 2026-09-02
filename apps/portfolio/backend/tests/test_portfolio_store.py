from __future__ import annotations

from copy import deepcopy
from datetime import date

import pytest

from portfolio_app.db.models import (
    AccountRecordModel,
    PortfolioCalculationStateModel,
    PortfolioDailySnapshotModel,
    PortfolioRecordModel,
    TransactionRecordModel,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services import daily_snapshots, portfolio_store
from portfolio_app.services.daily_snapshots import DAILY_SNAPSHOT_CALCULATION_VERSION


def test_reset_store_without_payload_leaves_store_empty() -> None:
    portfolio_store.reset_store()

    assert portfolio_store.list_portfolios() == []
    assert portfolio_store.list_accounts("portfolio-ops") == []
    assert portfolio_store.list_transactions("portfolio-ops") == []


def test_reset_store_requires_portfolio_inception_date() -> None:
    with pytest.raises(ValueError, match="requires a valid inception_date"):
        portfolio_store.reset_store(
            {
                "portfolios": [
                    {
                        "portfolio_id": "missing-inception",
                        "portfolio_name": "Missing Inception",
                        "base_currency": "USD",
                    }
                ]
            }
        )


def test_reset_store_rejects_transaction_before_portfolio_inception() -> None:
    with pytest.raises(ValueError, match="must not predate portfolio inception_date"):
        portfolio_store.reset_store(
            {
                "portfolios": [
                    {
                        "portfolio_id": "p1",
                        "portfolio_name": "Portfolio",
                        "base_currency": "USD",
                        "inception_date": "2026-01-02",
                    }
                ],
                "transactions": [
                    {
                        "transaction_id": "txn-before-inception",
                        "transaction_sequence": 1,
                        "portfolio_id": "p1",
                        "transaction_type": "deposit",
                        "trade_date": "2026-01-01",
                        "settlement_date": "2026-01-01",
                        "account_id": "cash-p1",
                        "gross_amount": 100,
                        "currency": "USD",
                    }
                ],
            }
        )


def test_create_portfolio_requires_and_persists_base_currency(client) -> None:
    missing_currency = client.post("/api/portfolios", json={"name": "Missing Currency"})
    assert missing_currency.status_code == 422

    response = client.post(
        "/api/portfolios",
        json={
            "name": "CNY Portfolio",
            "base_currency": "CNY",
            "inception_date": "2026-01-01",
        },
    )

    assert response.status_code == 200
    assert response.json()["base_currency"] == "CNY"
    assert response.json()["inception_date"] == "2026-01-01"
    assert portfolio_store.get_portfolio("cny-portfolio")["base_currency"] == "CNY"

    future_inception = client.post(
        "/api/portfolios",
        json={
            "name": "Future Portfolio",
            "base_currency": "USD",
            "inception_date": "2999-01-01",
        },
    )
    assert future_inception.status_code == 422


def test_updating_base_currency_invalidates_all_derived_snapshots(client) -> None:
    response = client.post(
        "/api/portfolios",
        json={
            "name": "Base Currency Change",
            "base_currency": "USD",
            "inception_date": "2026-01-01",
        },
    )
    assert response.status_code == 200
    portfolio_id = response.json()["portfolio_id"]

    session_factory = get_session_factory()
    with session_factory() as session:
        session.add(
            _seed_daily_snapshot(
                portfolio_id=portfolio_id,
                as_of_date=date(2026, 1, 2),
                nav=100.0,
                daily_twr=0.01,
            )
        )
        session.add(
            PortfolioCalculationStateModel(
                portfolio_id=portfolio_id,
                daily_snapshot_status="current",
                refreshed_from=date(2026, 1, 1),
                refreshed_to=date(2026, 1, 2),
            )
        )
        session.commit()

    update_response = client.patch(
        f"/api/portfolios/{portfolio_id}",
        json={"base_currency": "CNY"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["base_currency"] == "CNY"
    assert update_response.json()["nav"] is None

    with session_factory() as session:
        portfolio = session.get(PortfolioRecordModel, portfolio_id)
        state = session.get(PortfolioCalculationStateModel, portfolio_id)
        assert portfolio is not None and portfolio.base_currency == "CNY"
        assert state is not None and state.daily_snapshot_status == "stale"
        assert state.dirty_from is None
        assert (
            session.get(
                PortfolioDailySnapshotModel,
                (portfolio_id, date(2026, 1, 2)),
            )
            is None
        )

    missing_response = client.patch(
        "/api/portfolios/missing-portfolio",
        json={"base_currency": "CNY"},
    )
    assert missing_response.status_code == 404


def test_reset_store_rejects_missing_or_duplicate_transaction_sequence() -> None:
    transaction = {
        "transaction_id": "txn-sequence-contract",
        "portfolio_id": "portfolio-sequence-contract",
        "transaction_type": "deposit",
        "trade_date": "2026-01-01",
        "settlement_date": "2026-01-01",
        "account_id": "cash-sequence-contract",
        "gross_amount": 1.0,
        "fees": 0.0,
        "taxes": 0.0,
        "currency": "USD",
        "created_at": "2026-01-01T00:00:00Z",
    }

    with pytest.raises(ValueError, match="requires a positive transaction_sequence"):
        portfolio_store.reset_store({"transactions": [transaction]})

    with pytest.raises(ValueError, match="sequence '1' is duplicated"):
        portfolio_store.reset_store(
            {
                "transactions": [
                    {**transaction, "transaction_sequence": 1},
                    {
                        **transaction,
                        "transaction_id": "txn-sequence-contract-2",
                        "transaction_sequence": 1,
                    },
                ]
            }
        )


def test_reset_store_round_trips_manual_instrument_universe() -> None:
    payload = {
        "portfolios": [
            {
                "portfolio_id": "p1",
                "portfolio_name": "Portfolio",
                "base_currency": "USD",
                "valuation_timezone": "Asia/Shanghai",
                "valuation_cutoff_policy": "latest_complete_eod",
                "inception_date": "2026-01-01",
                "as_of_date": "2026-05-21",
                "nav": 0.0,
                "day_change_value": 0.0,
                "day_change_pct": 0.0,
                "securities_count": 0,
                "sort_order": 0,
            }
        ],
        "instrument_universe": [
            {
                "portfolio_id": "p1",
                "instrument_id": "fund-us-watch",
                "instrument_ref": {
                    "instrument_id": "fund-us-watch",
                    "instrument_name": "Watchlist Fund",
                    "instrument_type": "etf",
                    "currency": "USD",
                    "exchange_code": "XNAS",
                    "identifiers": [
                        {
                            "identifier_type": "ticker",
                            "identifier_value": "WATCH",
                            "is_primary": True,
                        }
                    ],
                },
                "source": "manual",
                "holding_state": "not_held",
                "first_transaction_date": None,
                "last_transaction_date": None,
                "transaction_count": 0,
                "status": "active",
                "created_at": "2026-05-21T00:00:00Z",
                "updated_at": "2026-05-21T00:00:00Z",
            }
        ],
    }

    portfolio_store.reset_store(deepcopy(payload))
    session_factory = get_session_factory()
    with session_factory() as session:
        loaded = portfolio_store._load_store_from_db(session)
    portfolio_store.reset_store(loaded)

    rows = portfolio_store.list_portfolio_instrument_universe("p1")
    assert [row["instrument_id"] for row in rows] == ["fund-us-watch"]
    assert rows[0]["source"] == "manual"
    assert rows[0]["holding_state"] == "not_held"
    assert rows[0]["instrument_ref"]["instrument_name"] == "Watchlist Fund"


def _seed_daily_snapshot(
    *,
    portfolio_id: str,
    as_of_date: date,
    nav: float,
    daily_twr: float,
    stale_price_flag: bool = False,
    stale_fx_flag: bool = False,
) -> PortfolioDailySnapshotModel:
    beginning_nav = nav / (1 + daily_twr)
    absolute_change = nav - beginning_nav
    return PortfolioDailySnapshotModel(
        portfolio_id=portfolio_id,
        as_of_date=as_of_date,
        coverage_state="complete",
        valuation_coverage_state="complete",
        return_coverage_state="complete",
        book_pnl_coverage_state="complete",
        attribution_coverage_state="complete",
        nav=nav,
        beginning_nav=beginning_nav,
        ending_nav=nav,
        daily_twr=daily_twr,
        cumulative_twr=daily_twr,
        drawdown=0.0,
        snapshot_json={
            "as_of_date": as_of_date.isoformat(),
            "coverage_state": "complete",
            "valuation_coverage_state": "complete",
            "return_coverage_state": "complete",
            "book_pnl_coverage_state": "complete",
            "attribution_coverage_state": "complete",
            "return_chain_continuous": True,
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
            "return_observation_eligible": True,
            "calculation_version": DAILY_SNAPSHOT_CALCULATION_VERSION,
        },
        calculated_at="2026-05-21T00:00:00Z",
    )


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
    assert portfolio["as_of_date"] == "2026-05-20"
    assert portfolio["nav"] == 100.0

    portfolio_rows = portfolio_store.list_portfolios()
    portfolio_ops_row = next(row for row in portfolio_rows if row["portfolio_id"] == "portfolio-ops")
    assert portfolio_ops_row["as_of_date"] == "2026-05-20"

    response = client.get("/api/workspace/summary", params={"portfolio_id": "portfolio-ops"})
    assert response.status_code == 200
    assert response.json()["as_of_date"] == "2026-05-20"

    holdings_response = client.get("/api/workspace/holdings", params={"portfolio_id": "portfolio-ops"})
    assert holdings_response.status_code == 200
    holdings_payload = holdings_response.json()
    assert holdings_payload["as_of_date"] == "2026-05-20"
    assert holdings_payload["risk_policy"]["model_role"] == "production"
    assert holdings_payload["forward_risk"]["status"] in {"ok", "unavailable"}
    assert all("forward_risk_status" in row for row in holdings_payload["rows"])

    performance_response = client.get("/api/portfolios/portfolio-ops/performance", params={"end_date": "2026-05-20"})
    assert performance_response.status_code == 200
    assert performance_response.json()["summary"]["end_date"] == "2026-05-20"


def test_live_portfolio_as_of_uses_current_holding_market_date(monkeypatch) -> None:
    portfolio = PortfolioRecordModel(
        portfolio_id="p1",
        portfolio_name="Portfolio",
        base_currency="CNY",
        valuation_timezone="Asia/Shanghai",
        valuation_cutoff_policy="latest_complete_eod",
        inception_date=date(2026, 1, 1),
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
        account_category="security",
        currency="CNY",
        institution=None,
        default_settlement_cash_account_id=None,
        cost_basis_method="fifo",
        opened_at=None,
        closed_at=None,
        status="active",
    )
    transactions = [
        TransactionRecordModel(
            transaction_id="buy-sold",
            portfolio_id="p1",
            transaction_type="buy",
            trade_date=date(2026, 4, 1),
            trade_time="12:00",
            trade_at="2026-04-01T04:00:00Z",
            trade_timezone="Asia/Shanghai",
            trade_time_is_estimated=True,
            settlement_date=date(2026, 4, 1),
            account_id="broker",
            instrument_id="sold",
            instrument_ref_json={"instrument_id": "sold"},
            quantity=100.0,
            price=1.0,
            gross_amount=100.0,
            fees=0.0,
            taxes=0.0,
            currency="CNY",
        ),
        TransactionRecordModel(
            transaction_id="sell-sold",
            portfolio_id="p1",
            transaction_type="sell",
            trade_date=date(2026, 4, 20),
            trade_time="12:00",
            trade_at="2026-04-20T04:00:00Z",
            trade_timezone="Asia/Shanghai",
            trade_time_is_estimated=True,
            settlement_date=date(2026, 4, 20),
            account_id="broker",
            instrument_id="sold",
            instrument_ref_json={"instrument_id": "sold"},
            quantity=100.0,
            price=1.0,
            gross_amount=100.0,
            fees=0.0,
            taxes=0.0,
            currency="CNY",
        ),
        TransactionRecordModel(
            transaction_id="buy-open",
            portfolio_id="p1",
            transaction_type="buy",
            trade_date=date(2026, 4, 24),
            trade_time="12:00",
            trade_at="2026-04-24T04:00:00Z",
            trade_timezone="Asia/Shanghai",
            trade_time_is_estimated=True,
            settlement_date=date(2026, 4, 24),
            account_id="broker",
            instrument_id="open",
            instrument_ref_json={"instrument_id": "open"},
            quantity=100.0,
            price=1.0,
            gross_amount=100.0,
            fees=0.0,
            taxes=0.0,
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


def test_live_portfolio_as_of_caps_future_settlement_at_valuation_today(monkeypatch) -> None:
    portfolio = PortfolioRecordModel(
        portfolio_id="p1",
        portfolio_name="Portfolio",
        base_currency="CNY",
        valuation_timezone="Asia/Shanghai",
        valuation_cutoff_policy="latest_complete_eod",
        inception_date=date(2026, 1, 1),
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
        account_category="cash",
        currency="CNY",
        institution=None,
        default_settlement_cash_account_id=None,
        cost_basis_method=None,
        opened_at=None,
        closed_at=None,
        status="active",
    )
    transactions = [
        TransactionRecordModel(
            transaction_id="redeem-proceeds",
            portfolio_id="p1",
            transaction_type="deposit",
            trade_date=date(2026, 5, 21),
            trade_time="12:00",
            trade_at="2026-05-21T04:00:00Z",
            trade_timezone="Asia/Shanghai",
            trade_time_is_estimated=True,
            settlement_date=date(2026, 5, 26),
            account_id="cash",
            instrument_id=None,
            instrument_ref_json=None,
            quantity=None,
            price=None,
            gross_amount=100.0,
            fees=0.0,
            taxes=0.0,
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
    monkeypatch.setattr(
        portfolio_store,
        "portfolio_valuation_today",
        lambda _valuation_timezone: date(2026, 5, 22),
    )

    assert portfolio_store._resolve_live_portfolio_as_of_date(
        object(),
        portfolio,
        accounts=[account],
        transactions=transactions,
    ) == date(2026, 5, 22)


def test_live_portfolio_as_of_ignores_stale_cached_portfolio_date(monkeypatch) -> None:
    portfolio = PortfolioRecordModel(
        portfolio_id="p1",
        portfolio_name="Portfolio",
        base_currency="CNY",
        valuation_timezone="Asia/Shanghai",
        valuation_cutoff_policy="latest_complete_eod",
        inception_date=date(2026, 1, 1),
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
        account_category="security",
        currency="CNY",
        institution=None,
        default_settlement_cash_account_id=None,
        cost_basis_method="fifo",
        opened_at=None,
        closed_at=None,
        status="active",
    )
    transactions = [
        TransactionRecordModel(
            transaction_id="buy-open",
            portfolio_id="p1",
            transaction_type="buy",
            trade_date=date(2026, 4, 24),
            trade_time="12:00",
            trade_at="2026-04-24T04:00:00Z",
            trade_timezone="Asia/Shanghai",
            trade_time_is_estimated=True,
            settlement_date=date(2026, 4, 24),
            account_id="broker",
            instrument_id="open",
            instrument_ref_json={"instrument_id": "open"},
            quantity=100.0,
            price=1.0,
            gross_amount=100.0,
            fees=0.0,
            taxes=0.0,
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
