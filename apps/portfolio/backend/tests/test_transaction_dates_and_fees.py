from __future__ import annotations

from datetime import date

import pytest

from portfolio_app.api.contracts import TransactionCreateRequest
from portfolio_app.services import performance
from portfolio_app.services.transaction_dates import (
    transaction_economic_date,
    transaction_external_flow_date,
    transaction_performance_effective_date,
    transaction_sort_key,
)


def _deposit() -> dict[str, object]:
    return {
        "transaction_type": "deposit",
        "trade_date": "2026-07-14",
        "settlement_date": "2026-07-15",
        "gross_amount": 100.0,
        "fees": 0.0,
        "taxes": 0.0,
        "currency": "USD",
    }


def test_transaction_sort_key_keeps_same_time_transactions_deterministic() -> None:
    common = {
        "trade_date": "2026-07-15",
        "trade_at": "2026-07-15T09:30:00Z",
    }
    records: list[dict[str, object]] = [
        {
            **common,
            "marker": "created-later",
            "created_at": "2026-07-15T09:31:00Z",
            "transaction_id": "txn-001",
            "settlement_date": "2026-07-16",
        },
        {
            **common,
            "marker": "id-later",
            "created_at": "2026-07-15T09:30:30Z",
            "transaction_id": "txn-002",
            "settlement_date": "2026-07-16",
        },
        {
            **common,
            "marker": "settles-later",
            "created_at": "2026-07-15T09:30:30Z",
            "transaction_id": "txn-001",
            "settlement_date": "2026-07-17",
        },
        {
            **common,
            "marker": "settles-earlier",
            "created_at": "2026-07-15T09:30:30Z",
            "transaction_id": "txn-001",
            "settlement_date": "2026-07-16",
        },
    ]

    assert transaction_sort_key(records[0]) == (
        "2026-07-15",
        "2026-07-15T09:30:00Z",
        "2026-07-15T09:31:00Z",
        "txn-001",
        "2026-07-16",
    )
    assert [record["marker"] for record in sorted(records, key=transaction_sort_key)] == [
        "settles-earlier",
        "settles-later",
        "id-later",
        "created-later",
    ]


def test_external_flow_date_is_distinct_from_economic_trade_date() -> None:
    deposit = _deposit()

    assert transaction_economic_date(deposit) == date(2026, 7, 14)
    assert transaction_external_flow_date(deposit) == date(2026, 7, 15)
    assert transaction_performance_effective_date(deposit) == date(2026, 7, 15)

    coupon = {
        "transaction_type": "coupon",
        "trade_date": "2026-07-15",
        "entitlement_date": "2026-07-10",
        "settlement_date": "2026-07-15",
    }
    assert transaction_economic_date(coupon) == date(2026, 7, 10)
    assert transaction_external_flow_date(coupon) is None
    assert transaction_performance_effective_date(coupon) == date(2026, 7, 10)


def test_daily_external_flow_is_neutralized_on_settlement_not_trade_date() -> None:
    deposit = _deposit()
    common = {
        "base_currency": "USD",
        "direct_fx_instruments": {},
        "instrument_detail_cache": {},
    }

    trade_day = performance._daily_external_flow_breakdown(
        [deposit],
        date(2026, 7, 14),
        **common,
    )
    settlement_day = performance._daily_external_flow_breakdown(
        [deposit],
        date(2026, 7, 15),
        **common,
    )

    assert trade_day["external_cash_in"] == pytest.approx(0.0)
    assert trade_day["net_external_inflow"] == pytest.approx(0.0)
    assert settlement_day["external_cash_in"] == pytest.approx(100.0)
    assert settlement_day["net_external_inflow"] == pytest.approx(100.0)


def test_fee_category_is_never_inferred_and_requires_an_actual_fee() -> None:
    unclassified = TransactionCreateRequest.model_validate(
        {
            "transaction_type": "fee",
            "trade_date": "2026-07-15",
            "account_id": "cash-usd-main",
            "gross_amount": 25,
            "currency": "USD",
        }
    )
    assert unclassified.fee_category == "unknown"

    classified = TransactionCreateRequest.model_validate(
        {
            "transaction_type": "fee",
            "trade_date": "2026-07-15",
            "account_id": "cash-usd-main",
            "gross_amount": 25,
            "currency": "USD",
            "fee_category": "management_fee",
        }
    )
    assert classified.fee_category == "management_fee"

    with pytest.raises(ValueError, match="fee_category requires"):
        TransactionCreateRequest.model_validate(
            {
                "transaction_type": "buy",
                "trade_date": "2026-07-15",
                "account_id": "brokerage",
                "instrument_id": "equity",
                "quantity": 1,
                "price": 100,
                "gross_amount": 100,
                "currency": "USD",
                "fee_category": "transaction_cost",
            }
        )


def test_transaction_api_round_trips_dates_and_fee_category(client) -> None:
    fee_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json={
            "transaction_type": "fee",
            "trade_date": "2026-04-16",
            "settlement_date": "2026-04-16",
            "account_id": "cash-usd-main",
            "gross_amount": 25,
            "currency": "USD",
            "fee_category": "management_fee",
        },
    )
    assert fee_response.status_code == 200, fee_response.text
    fee = fee_response.json()
    assert fee["economic_date"] == "2026-04-16"
    assert fee["external_flow_date"] is None
    assert fee["fee_category"] == "management_fee"

    deposit_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json={
            "transaction_type": "deposit",
            "trade_date": "2026-04-16",
            "settlement_date": "2026-04-17",
            "account_id": "cash-usd-main",
            "gross_amount": 100,
            "currency": "USD",
        },
    )
    assert deposit_response.status_code == 200, deposit_response.text
    deposit = deposit_response.json()
    assert deposit["economic_date"] == "2026-04-16"
    assert deposit["external_flow_date"] == "2026-04-17"
    assert deposit["fee_category"] == "unknown"
