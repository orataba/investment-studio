from __future__ import annotations

from datetime import date

import pytest

from portfolio_app.api.contracts import AccountCreateRequest, TransactionCreateRequest
from portfolio_app.services import performance
from portfolio_app.services.transaction_dates import (
    transaction_economic_date,
    transaction_external_flow_date,
    transaction_ledger_activity_date,
    transaction_performance_effective_date,
    transaction_position_cash_transfer_date,
    transaction_position_effective_date,
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


@pytest.mark.parametrize("currency", ["EUR", "GBP", "CHF"])
def test_european_currencies_are_valid_account_and_transaction_facts(
    currency: str,
) -> None:
    account = AccountCreateRequest.model_validate(
        {
            "account_name": f"{currency} Cash",
            "account_category": "cash",
            "currency": currency,
        }
    )
    transaction = TransactionCreateRequest.model_validate(
        {
            "transaction_type": "deposit",
            "trade_date": "2026-08-21",
            "account_id": f"cash-{currency.lower()}",
            "gross_amount": "100",
            "currency": currency,
        }
    )

    assert account.currency == currency
    assert transaction.currency == currency


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
            "transaction_id": "txn-10001",
            "transaction_sequence": 10001,
            "settlement_date": "2026-07-16",
        },
        {
            **common,
            "marker": "threshold-high",
            "created_at": "2026-07-15T09:30:30Z",
            "transaction_id": "txn-10000",
            "transaction_sequence": 10000,
            "settlement_date": "2026-07-16",
        },
        {
            **common,
            "marker": "threshold-low",
            "created_at": "2026-07-15T09:30:30Z",
            "transaction_id": "txn-9999",
            "transaction_sequence": 9999,
            "settlement_date": "2026-07-17",
        },
    ]

    assert transaction_sort_key(records[0]) == (
        "2026-07-15",
        "2026-07-15T09:30:00Z",
        "2026-07-15T09:31:00Z",
        10001,
        "2026-07-16",
    )
    assert [record["marker"] for record in sorted(records, key=transaction_sort_key)] == [
        "threshold-low",
        "threshold-high",
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


def test_position_effective_date_can_follow_trade_without_rewriting_execution() -> None:
    subscription = {
        "transaction_id": "txn-subscription",
        "transaction_sequence": 1,
        "transaction_type": "buy",
        "trade_date": "2026-07-24",
        "trade_at": "2026-07-24T08:00:00Z",
        "position_effective_date": "2026-07-27",
        "settlement_date": "2026-07-27",
        "instrument_id": "017847-of",
    }

    assert transaction_position_effective_date(subscription) == date(2026, 7, 27)
    assert transaction_economic_date(subscription) == date(2026, 7, 27)
    assert transaction_performance_effective_date(subscription) == date(2026, 7, 27)
    assert transaction_sort_key(subscription)[0] == "2026-07-27"


def test_early_cash_starts_position_bridge_without_moving_economic_date() -> None:
    subscription = {
        "transaction_id": "txn-subscription",
        "transaction_type": "buy",
        "trade_date": "2026-07-24",
        "position_effective_date": "2026-07-27",
        "settlement_date": "2026-07-24",
        "account_id": "broker-cny",
        "settlement_cash_account_id": "cash-cny",
        "instrument_id": "017847-of",
    }

    assert transaction_performance_effective_date(subscription) == date(
        2026, 7, 27
    )
    assert transaction_position_cash_transfer_date(subscription) == date(
        2026, 7, 24
    )
    assert transaction_ledger_activity_date(subscription) == date(2026, 7, 24)


def test_same_day_position_effective_date_is_the_legacy_default() -> None:
    trade = {
        "transaction_type": "buy",
        "trade_date": "2026-07-24",
        "instrument_id": "017847-of",
    }

    assert transaction_position_effective_date(trade) == date(2026, 7, 24)
    assert transaction_economic_date(trade) == date(2026, 7, 24)


def test_position_effective_date_requires_trade_ordering_and_allows_early_cash() -> None:
    with pytest.raises(
        ValueError,
        match="position_effective_date must not be earlier than trade_date",
    ):
        TransactionCreateRequest.model_validate(
            {
                "transaction_type": "buy",
                "trade_date": "2026-07-24",
                "position_effective_date": "2026-07-23",
                "settlement_date": "2026-07-24",
                "account_id": "broker-cny",
                "settlement_cash_account_id": "cash-cny",
                "instrument_id": "017847-of",
                "quantity": "1",
                "price": "1",
                "gross_amount": "1",
                "currency": "CNY",
            }
        )

    early_settled_subscription = TransactionCreateRequest.model_validate(
        {
            "transaction_type": "buy",
            "trade_date": "2026-07-24",
            "position_effective_date": "2026-07-27",
            "settlement_date": "2026-07-24",
            "account_id": "broker-cny",
            "settlement_cash_account_id": "cash-cny",
            "instrument_id": "017847-of",
            "quantity": "1",
            "price": "1",
            "gross_amount": "1",
            "currency": "CNY",
        }
    )
    assert early_settled_subscription.settlement_date == date(2026, 7, 24)
    assert early_settled_subscription.position_effective_date == date(2026, 7, 27)


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
        "/api/portfolios/investment-studio/transactions",
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
        "/api/portfolios/investment-studio/transactions",
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
