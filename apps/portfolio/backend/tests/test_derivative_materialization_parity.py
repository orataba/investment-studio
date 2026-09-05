from __future__ import annotations

from copy import deepcopy
from datetime import date

import pytest
from sqlalchemy import select

from portfolio_app.db.models import (
    PortfolioDailyContributionSliceModel,
    PortfolioDailyHoldingSnapshotModel,
    PortfolioDailySnapshotModel,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services import daily_snapshots, performance, portfolio_store
from tests.store_fixture import TEST_PORTFOLIO_STORE


def _instrument_ref(
    instrument_id: str,
    instrument_type: str,
    *,
    option_underlying_id: str | None = None,
) -> dict[str, object]:
    ref: dict[str, object] = {
        "instrument_id": instrument_id,
        "instrument_name": instrument_id,
        "instrument_type": instrument_type,
        **({"exchange_code": "XNYS"} if instrument_type in {"equity", "etf"} else {}),
        "currency": "USD",
        "identifiers": [],
        "broker_identifiers": [],
    }
    return ref


def _option_contract(
    derivative_contract_id: str,
    *,
    underlying_instrument_id: str,
    account_id: str = "broker-us-options",
    currency: str = "USD",
) -> dict[str, object]:
    return {
        "derivative_contract_id": derivative_contract_id,
        "portfolio_id": "investment-studio",
        "account_id": account_id,
        "contract_name": "ABBV Dec 2026 Covered Call",
        "contract_type": "option",
        "currency": currency,
        "external_reference": "ABBV-20261218-C-220",
        "terms": {
            "underlying_instrument_id": underlying_instrument_id,
            "option_type": "call",
            "expiry_date": "2026-12-18",
            "strike": "220",
            "contract_multiplier": "100",
        },
        "created_at": "2026-02-11T02:00:00Z",
    }


def _transaction(
    transaction_id: str,
    transaction_type: str,
    trade_date: str,
    *,
    account_id: str,
    settlement_cash_account_id: str | None,
    instrument_ref: dict[str, object] | None = None,
    derivative_contract: dict[str, object] | None = None,
    quantity: float | None = None,
    price: float | None = None,
    gross_amount: float,
    fees: float = 0.0,
    currency: str = "USD",
) -> dict[str, object]:
    return {
        "transaction_id": transaction_id,
        "transaction_sequence": int(transaction_id.rsplit("-", 1)[-1]),
        "portfolio_id": "investment-studio",
        "transaction_type": transaction_type,
        "lifecycle_event_type": None,
        "trade_date": trade_date,
        "trade_time": "10:00",
        "trade_at": f"{trade_date}T10:00:00+08:00",
        "settlement_date": trade_date,
        "position_effective_date": (
            trade_date if transaction_type in {"buy", "sell"} else None
        ),
        "entitlement_date": None,
        "acquisition_date": None,
        "account_id": account_id,
        "settlement_cash_account_id": settlement_cash_account_id,
        "instrument_id": (
            str(instrument_ref.get("instrument_id"))
            if instrument_ref is not None
            else None
        ),
        "instrument_ref": deepcopy(instrument_ref),
        "derivative_contract_id": (
            str(derivative_contract.get("derivative_contract_id"))
            if derivative_contract is not None
            else None
        ),
        "derivative_contract": deepcopy(derivative_contract),
        "quantity": quantity,
        "price": price,
        "gross_amount": gross_amount,
        "counter_amount": None,
        "fx_rate": None,
        "fees": fees,
        "fee_category": "unknown",
        "taxes": 0.0,
        "currency": currency,
        "transfer_scope": None,
        "transfer_object_type": None,
        "transfer_group_id": None,
        "counterparty_account_id": None,
        "source_system": "test",
        "external_reference": transaction_id,
        "note": None,
        "created_at": f"{trade_date}T02:00:00Z",
    }


def test_dynamic_and_materialized_option_asset_and_obligation_are_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    portfolio_id = "investment-studio"
    underlying_id = "equity-us-abbv"
    session_factory = get_session_factory()
    option_id = "option-abbv-20261218-c-220"
    option_contract = _option_contract(
        option_id,
        underlying_instrument_id=underlying_id,
    )
    equity_ref = _instrument_ref(underlying_id, "equity")
    equity_detail = {
        **equity_ref,
        "quote_selection_policy": {
            "trading": ["close"],
            "valuation": ["close"],
            "total_return": ["close"],
            "chart": ["close"],
            "reference": ["close"],
        },
        "market_data": [
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": as_of_date,
                "value": "206.47",
                "currency": "USD",
                "price_unit": "per_unit",
                "price_scale": "1",
                "provider": "test",
                "status": "complete",
            }
            for as_of_date in ("2026-02-10", "2026-02-11")
        ],
    }
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda instrument_id: (
            deepcopy(equity_detail) if instrument_id == underlying_id else None
        ),
    )
    monkeypatch.setattr(
        performance,
        "get_shared_fx_rates",
        lambda: {"supported_currencies": ["USD"], "rates": []},
    )

    store = deepcopy(TEST_PORTFOLIO_STORE)
    store["portfolios"][0]["inception_date"] = "2026-02-10"
    store["portfolios"][0]["as_of_date"] = "2026-02-11"
    store["accounts"].append(
        {
            "account_id": "broker-us-options",
            "portfolio_id": portfolio_id,
            "account_name": "USD Options",
            "account_type": "securities_account",
            "account_category": "option",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "fifo",
            "opened_at": "2026-01-02",
            "status": "active",
        }
    )
    store["transactions"] = [
        _transaction(
            "txn-parity-0001",
            "opening_balance",
            "2026-02-10",
            account_id="cash-usd-main",
            settlement_cash_account_id=None,
            gross_amount=21_662.0,
        ),
        _transaction(
            "txn-parity-0002",
            "buy",
            "2026-02-10",
            account_id="broker-us-options",
            settlement_cash_account_id="cash-usd-main",
            instrument_ref=equity_ref,
            quantity=100.0,
            price=206.47,
            gross_amount=20_647.0,
        ),
        _transaction(
            "txn-parity-0003",
            "buy",
            "2026-02-11",
            account_id="broker-us-options",
            settlement_cash_account_id="cash-usd-main",
            derivative_contract=option_contract,
            quantity=1.0,
            price=500.0,
            gross_amount=500.0,
        ),
        _transaction(
            "txn-parity-0004",
            "option_write",
            "2026-02-11",
            account_id="broker-us-core",
            settlement_cash_account_id="cash-usd-main",
            derivative_contract=option_contract,
            quantity=1.0,
            gross_amount=300.0,
            fees=15.0,
        ),
    ]
    store["derivative_contracts"] = [deepcopy(option_contract)]
    portfolio_store.reset_store(store)

    portfolio = portfolio_store.list_portfolios()[0]
    accounts = portfolio_store.list_accounts(portfolio_id)
    transactions = portfolio_store.list_transactions(portfolio_id)
    dynamic = performance.build_holdings_report(
        portfolio,
        accounts,
        transactions,
        as_of_date=date(2026, 2, 11),
        include_cash_rows=True,
    )
    dynamic_snapshots = performance.build_daily_portfolio_snapshots(
        portfolio,
        accounts,
        transactions,
        start_date=date(2026, 2, 10),
        end_date=date(2026, 2, 11),
        include_materialized_rows=True,
    )
    dynamic_final = dynamic_snapshots[-1]

    refresh = daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(
        portfolio_id
    )
    assert refresh is not None
    assert refresh["source_generation_status"] == "stable"
    materialized = daily_snapshots.build_materialized_holdings_workspace(
        portfolio_id,
        as_of_date=date(2026, 2, 11),
    )
    assert materialized is not None

    dynamic_option_rows = sorted(
        [
            row
            for row in dynamic["positions"]
            if row.get("derivative_contract_id") == option_id
        ],
        key=lambda row: str(row["holding_kind"]),
    )
    materialized_option_rows = sorted(
        [
            row
            for row in materialized["rows"]
            if row.get("derivative_contract_id") == option_id
        ],
        key=lambda row: str(row["holding_kind"]),
    )
    assert [row["holding_kind"] for row in dynamic_option_rows] == [
        "derivative_contract",
        "option_obligation",
    ]
    assert [row["holding_kind"] for row in materialized_option_rows] == [
        "derivative_contract",
        "option_obligation",
    ]
    for dynamic_row, materialized_row in zip(
        dynamic_option_rows,
        materialized_option_rows,
        strict=True,
    ):
        assert materialized_row["quantity"] == pytest.approx(dynamic_row["quantity"])
        assert materialized_row["market_value_base"] == pytest.approx(
            dynamic_row["market_value_base"]
        )
        assert materialized_row["carrying_value_base"] == pytest.approx(
            dynamic_row["carrying_value_base"]
        )
        assert materialized_row["valuation_basis"] == dynamic_row["valuation_basis"]
        assert materialized_row["risk_eligible"] is dynamic_row["risk_eligible"]
        for field_name in (
            "open_contract_quantity", "required_underlying_quantity", "expiry_date",
            "days_to_expiry", "strike", "strike_currency", "option_type",
            "contract_multiplier", "strike_notional", "strike_notional_base",
        ):
            assert materialized_row[field_name] == dynamic_row[field_name]
        assert materialized_row["required_underlying_quantity"] == 100.0
        assert materialized_row["strike_currency"] == "USD"
        assert materialized_row["unrealized_return"] is None
        assert materialized_row["unrealized_return_base"] is None
        if dynamic_row["holding_kind"] == "option_obligation":
            for field_name in (
                "open_contract_quantity",
                "required_underlying_quantity",
                "obligation_status",
                "related_underlying_id",
                "expiry_date",
                "days_to_expiry",
                "strike",
                "option_type",
                "contract_multiplier",
                "strike_notional",
                "strike_notional_base",
                "premium_basis_remaining",
                "liability_value",
                "liability_value_base",
            ):
                assert materialized_row[field_name] == dynamic_row[field_name]

    detail_projection = daily_snapshots.build_materialized_position_holding_projection(
        portfolio_id,
        option_id,
        as_of_date=date(2026, 2, 11),
    )
    assert detail_projection is not None
    assert [row["holding_kind"] for row in detail_projection["rows"]] == [
        "derivative_contract",
        "option_obligation",
    ]
    detail_obligation = next(
        row
        for row in detail_projection["rows"]
        if row["holding_kind"] == "option_obligation"
    )
    materialized_obligation = next(
        row
        for row in materialized_option_rows
        if row["holding_kind"] == "option_obligation"
    )
    for field_name in (
        "open_contract_quantity",
        "required_underlying_quantity",
        "obligation_status",
        "related_underlying_id",
        "expiry_date",
        "days_to_expiry",
        "strike",
        "option_type",
        "contract_multiplier",
        "strike_notional",
        "strike_notional_base",
        "strike_currency",
        "premium_basis_remaining",
        "liability_value",
        "liability_value_base",
    ):
        assert detail_obligation[field_name] == materialized_obligation[field_name]

    assert dynamic["total_nav_base"] == pytest.approx(21_647.0)
    assert materialized["totals"]["nav"] == pytest.approx(dynamic["total_nav_base"])
    assert dynamic_final["ending_nav"] == pytest.approx(dynamic["total_nav_base"])

    with session_factory() as session:
        persisted_holding_kinds = list(
            session.scalars(
                select(PortfolioDailyHoldingSnapshotModel.holding_kind).where(
                    PortfolioDailyHoldingSnapshotModel.portfolio_id == portfolio_id,
                    PortfolioDailyHoldingSnapshotModel.as_of_date
                    == date(2026, 2, 11),
                    PortfolioDailyHoldingSnapshotModel.derivative_contract_id
                    == option_id,
                )
            ).all()
        )
        persisted_snapshot = session.get(
            PortfolioDailySnapshotModel,
            (portfolio_id, date(2026, 2, 11)),
        )
        persisted_option_contribution = session.scalar(
            select(PortfolioDailyContributionSliceModel).where(
                PortfolioDailyContributionSliceModel.portfolio_id == portfolio_id,
                PortfolioDailyContributionSliceModel.as_of_date == date(2026, 2, 11),
                PortfolioDailyContributionSliceModel.axis == "instrument",
                PortfolioDailyContributionSliceModel.group_key == option_id,
            )
        )

    assert sorted(persisted_holding_kinds) == [
        "derivative_contract",
        "option_obligation",
    ]
    assert persisted_snapshot is not None
    assert persisted_snapshot.nav == pytest.approx(dynamic_final["ending_nav"])
    dynamic_option_contribution = next(
        row
        for row in dynamic_final["_contribution_slices"]
        if row["axis"] == "instrument" and row["group_key"] == option_id
    )
    assert persisted_option_contribution is not None
    assert persisted_option_contribution.total_pnl == pytest.approx(
        dynamic_option_contribution["total_pnl"]
    )
    assert persisted_option_contribution.total_pnl == pytest.approx(-15.0)


def test_foreign_option_liability_fx_is_attributed_as_signed_instrument_exposure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    portfolio_id = "investment-studio"
    option_id = "option-hkd-covered-call"
    option_account_id = "broker-hk-options"
    option_contract = _option_contract(
        option_id,
        underlying_instrument_id="equity-hk-underlying",
        account_id=option_account_id,
        currency="HKD",
    )
    fx_detail = {
        "instrument_id": "fx-usd-hkd",
        "instrument_name": "USD/HKD",
        "instrument_type": "fx",
        "currency": "HKD",
        "identifiers": [],
        "quote_selection_policy": {"valuation": ["spot"], "reference": ["spot"]},
        "market_data": [
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": as_of_date,
                "value": value,
                "currency": "HKD",
                "price_unit": "rate",
                "price_scale": 1.0,
                "status": "complete",
            }
            for as_of_date, value in (
                ("2026-02-20", "7.80"),
                ("2026-02-21", "7.50"),
            )
        ],
    }
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda instrument_id: (
            deepcopy(fx_detail) if instrument_id == "fx-usd-hkd" else None
        ),
    )
    monkeypatch.setattr(
        performance,
        "get_shared_fx_rates",
        lambda: {
            "supported_currencies": ["USD", "HKD"],
            "maintained_pairs": ["USD/HKD"],
            "rates": [
                {
                    "base_currency": "USD",
                    "quote_currency": "HKD",
                    "rate": 7.5,
                    "as_of_date": "2026-02-21",
                    "source_kind": "direct",
                    "instrument_id": "fx-usd-hkd",
                    "source_instrument_ids": ["fx-usd-hkd"],
                    "status": "complete",
                }
            ],
        },
    )

    store = deepcopy(TEST_PORTFOLIO_STORE)
    store["portfolios"][0]["inception_date"] = "2026-02-20"
    store["portfolios"][0]["as_of_date"] = "2026-02-21"
    hkd_cash_account = next(
        account
        for account in store["accounts"]
        if account["account_id"] == "cash-hkd-main"
    )
    hkd_cash_account["opened_at"] = "2026-02-20"
    store["accounts"] = [
        hkd_cash_account,
        {
            "account_id": option_account_id,
            "portfolio_id": portfolio_id,
            "account_name": "HKD Options",
            "account_type": "securities_account",
            "account_category": "option",
            "currency": "HKD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-hkd-main",
            "cost_basis_method": "fifo",
            "opened_at": "2026-02-20",
            "status": "active",
        },
    ]
    store["transactions"] = [
        _transaction(
            "txn-option-fx-0001",
            "option_write",
            "2026-02-20",
            account_id=option_account_id,
            settlement_cash_account_id="cash-hkd-main",
            derivative_contract=option_contract,
            quantity=1.0,
            gross_amount=780.0,
            currency="HKD",
        )
    ]
    store["derivative_contracts"] = [deepcopy(option_contract)]
    portfolio_store.reset_store(store)

    snapshots = performance.build_daily_portfolio_snapshots(
        portfolio_store.list_portfolios()[0],
        portfolio_store.list_accounts(portfolio_id),
        portfolio_store.list_transactions(portfolio_id),
        start_date=date(2026, 2, 20),
        end_date=date(2026, 2, 21),
        include_materialized_rows=True,
    )

    final_snapshot = snapshots[-1]
    assert final_snapshot["cash_currency_gains"] == pytest.approx(4.0)
    assert final_snapshot["instrument_currency_gains"] == pytest.approx(-4.0)
    assert final_snapshot["ending_nav"] == pytest.approx(0.0)
    assert final_snapshot["total_pnl"] == pytest.approx(0.0)

    final_slices = [
        row
        for row in final_snapshot["_contribution_slices"]
        if row["as_of_date"] == date(2026, 2, 21)
        and row["axis"] == "instrument"
    ]
    option_slice = next(row for row in final_slices if row["group_key"] == option_id)
    cash_slice = next(row for row in final_slices if row["group_key"] == "cash")
    assert option_slice["instrument_currency_gains"] == pytest.approx(-4.0)
    assert option_slice["total_pnl"] == pytest.approx(-4.0)
    assert cash_slice["cash_currency_gains"] == pytest.approx(4.0)
    assert cash_slice["total_pnl"] == pytest.approx(4.0)
