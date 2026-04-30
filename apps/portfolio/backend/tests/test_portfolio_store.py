from __future__ import annotations

from datetime import date

from portfolio_app.db.models import AccountRecordModel, PortfolioRecordModel, TransactionRecordModel
from portfolio_app.services import portfolio_store


def test_reset_store_without_payload_leaves_store_empty() -> None:
    portfolio_store.reset_store()

    assert portfolio_store.list_portfolios() == []
    assert portfolio_store.list_accounts("yungu") == []
    assert portfolio_store.list_transactions("yungu") == []


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
        allowed_asset_types_json=None,
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
            asset_id="sold",
            instrument_ref_json={"asset_id": "sold"},
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
            asset_id="sold",
            instrument_ref_json={"asset_id": "sold"},
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
            asset_id="open",
            instrument_ref_json={"asset_id": "open"},
            quantity=100.0,
            price=1.0,
            gross_amount=100.0,
            fees=0.0,
            taxes=0.0,
            currency="CNY",
        ),
    ]

    def fake_latest_market_date(_session, asset_ids):
        if asset_ids == {"sold", "open"}:
            return date(2026, 4, 29)
        if asset_ids == {"open"}:
            return date(2026, 4, 28)
        return None

    monkeypatch.setattr(portfolio_store, "_latest_market_data_date_for_assets", fake_latest_market_date)
    monkeypatch.setattr(
        portfolio_store,
        "build_position_lots",
        lambda *args, **kwargs: [{"asset_id": "open"}],
    )

    assert portfolio_store._resolve_live_portfolio_as_of_date(
        object(),
        portfolio,
        accounts=[account],
        transactions=transactions,
    ) == date(2026, 4, 28)
