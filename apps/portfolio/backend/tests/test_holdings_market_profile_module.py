from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

import pytest

from portfolio_app.services import holdings_market_profile, valuation_fx


def _instrument_ref(instrument_id: str = "equity-a") -> dict[str, object]:
    return {
        "instrument_id": instrument_id,
        "instrument_name": "Asset A",
        "instrument_type": "Equity",
        "exchange_code": "XNYS",
        "currency": "usd",
        "identifiers": [
            {
                "identifier_type": "ticker",
                "identifier_value": "AAA",
                "is_primary": True,
            }
        ],
    }


def test_holding_summary_instrument_core_and_cash_identity_golden_contract() -> None:
    rows = [
        {"day_change_value_base": "10.0"},
        {"day_change_value_base": -2.0},
    ]
    assert holdings_market_profile.summarize_holding_day_change(
        rows,
        total_market_value_base="108.0",
    ) == {"day_change_value": 8.0, "day_change_pct": 0.08}
    assert holdings_market_profile.summarize_holding_day_change(
        [{"day_change_value_base": None}],
        total_market_value_base=100.0,
    ) == {"day_change_value": None, "day_change_pct": None}

    instrument_ref = _instrument_ref()
    assert holdings_market_profile.normalize_instrument_core(
        "equity-a", instrument_ref
    ) == {
        "instrument_id": "equity-a",
        "instrument_name": "Asset A",
        "instrument_type": "equity",
        "exchange_code": "XNYS",
        "currency": "USD",
        "identifiers": instrument_ref["identifiers"],
        "broker_identifiers": [],
    }
    invalid_ref = {**instrument_ref, "asset_id": "noncanonical-a"}
    with pytest.raises(ValueError, match="non-canonical fields: asset_id"):
        holdings_market_profile.normalize_instrument_core("equity-a", invalid_ref)

    assert holdings_market_profile.cash_holding_instrument_id(
        " usd "
    ) == "cash:USD"
    assert holdings_market_profile.is_cash_holding_instrument_id(
        " CASH:usd "
    ) is True
    assert holdings_market_profile.cash_holding_instrument_ref("usd")[
        "instrument_id"
    ] == "cash:USD"
    with pytest.raises(ValueError, match="cash currency is required"):
        holdings_market_profile.cash_holding_instrument_id("")


def test_holding_day_change_uses_injected_market_value_calculation() -> None:
    calls: list[dict[str, object]] = []

    def fake_position_market_value(**kwargs):
        calls.append(kwargs)
        return float(kwargs["quantity"]) * float(kwargs["last_price"]) / float(
            kwargs["price_scale"]
        )

    kwargs = {
        "quantity": 2.0,
        "current_price": 110.0,
        "previous_price": 100.0,
        "instrument_ref": _instrument_ref(),
        "current_return_price": 55.0,
        "previous_return_price": 50.0,
        "price_scale": 10.0,
    }

    result = holdings_market_profile.holding_day_change_metrics(
        **kwargs,
        position_market_value=fake_position_market_value,
    )

    assert result[0] == pytest.approx(0.10)
    assert result[1] == pytest.approx(2.0)
    assert len(calls) == 1


def test_holding_day_change_never_mixes_total_return_and_valuation_pairs() -> None:
    change_pct, change_value = holdings_market_profile.holding_day_change_metrics(
        quantity=2.0,
        current_price=110.0,
        previous_price=100.0,
        instrument_ref=_instrument_ref(),
        current_return_price=55.0,
        previous_return_price=None,
    )

    assert change_pct == pytest.approx(0.10)
    assert change_value == pytest.approx(20.0)


def test_cash_profile_uses_injected_fx_and_identity_dependencies() -> None:
    def resolve_fx(**kwargs):
        as_of_date = kwargs["as_of_date"]
        return {
            "rate": "1.20" if as_of_date == date(2026, 1, 3) else "1.00",
            "as_of_date": as_of_date.isoformat(),
            "source_instrument_ids": ["fx-usd-eur"],
            "stale": False,
        }

    metric_kwargs = {
        "amount": 100.0,
        "currency": "EUR",
        "base_currency": "USD",
        "as_of_date": date(2026, 1, 3),
        "previous_as_of_date": date(2026, 1, 2),
        "direct_fx_instruments": {},
        "instrument_detail_cache": {},
    }
    metric = holdings_market_profile.cash_day_change_metrics(
        **metric_kwargs,
        resolve_fx_rate_on=resolve_fx,
    )
    assert metric["day_change_pct"] == pytest.approx(0.20)
    assert metric["day_change_value_base"] == pytest.approx(20.0)
    assert metric["local_day_change_value_base"] == pytest.approx(0.0)
    assert metric["fx_day_change_value_base"] == pytest.approx(20.0)
    assert metric["fx_rate_to_base"] == pytest.approx(1.20)
    assert metric["previous_fx_rate_to_base"] == pytest.approx(1.00)

    def fake_day_change(**_kwargs):
        return {
            "day_change_pct": 0.25,
            "local_day_change_pct": 0.0,
            "local_day_change_value_base": 0.0,
            "fx_day_change_value_base": 25.0,
            "day_change_value_base": 25.0,
        }

    def fake_cash_id(currency: str) -> str:
        return f"patched:{currency}"

    def fake_cash_ref(currency: str) -> dict[str, object]:
        return {"instrument_id": f"ref:{currency}", "currency": currency}

    row_kwargs = {
        "cash_balances": [
            {
                "currency": "USD",
                "amount": "30",
                "amount_base": "30",
                "account_id": "cash-a",
            },
            {
                "currency": "USD",
                "amount": "20",
                "amount_base": "20",
                "account_id": "cash-b",
            },
            {
                "currency": "EUR",
                "amount": 100.0,
                "amount_base": 120.0,
                "account_id": "cash-eur",
            },
            {
                "currency": "JPY",
                "amount": 0.0,
                "amount_base": 0.0,
                "account_id": "cash-jpy",
            },
        ],
        "as_of_date": date(2026, 1, 3),
        "previous_as_of_date": date(2026, 1, 2),
        "base_currency": "USD",
        "direct_fx_instruments": {},
        "instrument_detail_cache": {},
    }
    rows = holdings_market_profile.build_cash_holding_rows(
        **row_kwargs,
        cash_day_change=fake_day_change,
        cash_instrument_id=fake_cash_id,
        cash_instrument_ref=fake_cash_ref,
    )
    assert [row["account_id"] for row in rows] == ["cash-a", "cash-b", "cash-eur"]
    assert [row["line_id"] for row in rows] == [
        "patched:USD:cash-a",
        "patched:USD:cash-b",
        "patched:EUR:cash-eur",
    ]


def test_position_day_change_in_base_separates_local_and_fx_effects() -> None:
    def resolve_fx(**kwargs):
        as_of_date = kwargs["as_of_date"]
        return {
            "rate": 6.65 if as_of_date == date(2026, 1, 3) else 7.0,
            "as_of_date": as_of_date.isoformat(),
            "source_instrument_ids": ["fx-usd-cny"],
            "stale": False,
        }

    metrics = holdings_market_profile.position_day_change_metrics_in_base(
        current_market_value=110.0,
        local_day_change_pct=0.10,
        local_day_change_value=10.0,
        currency="USD",
        base_currency="CNY",
        as_of_date=date(2026, 1, 3),
        previous_as_of_date=date(2026, 1, 2),
        direct_fx_instruments={},
        instrument_detail_cache={},
        resolve_fx_rate_on=resolve_fx,
    )

    assert metrics["local_day_change_value_base"] == pytest.approx(66.5)
    assert metrics["fx_day_change_value_base"] == pytest.approx(-35.0)
    assert metrics["day_change_value_base"] == pytest.approx(31.5)
    assert metrics["day_change_pct"] == pytest.approx(0.045)
    assert metrics["fx_rate_source_instrument_ids"] == ["fx-usd-cny"]


def test_position_unrealized_pnl_reconciles_price_fx_and_total_components() -> None:
    def resolve_fx(**kwargs):
        assert kwargs["as_of_date"] == date(2026, 1, 1)
        return {"rate": 7.8, "stale": False}

    metrics = holdings_market_profile.position_unrealized_metrics(
        cost_basis=100.0,
        cost_basis_base_current_fx=790.0,
        current_fx_stale=True,
        cost_basis_origins=[
            {
                "acquisition_date": "2026-01-01",
                "remaining_cost_basis": 100.0,
            }
        ],
        market_value=110.0,
        market_value_base=869.0,
        currency="USD",
        base_currency="CNY",
        as_of_date=date(2026, 1, 3),
        direct_fx_instruments={},
        instrument_detail_cache={},
        resolve_fx_rate_on=resolve_fx,
    )

    assert metrics["cost_basis_historical_base"] == pytest.approx(780.0)
    assert metrics["cost_basis_current_fx_rate_to_base"] == pytest.approx(7.9)
    assert metrics["cost_basis_fx_rate_to_base"] == pytest.approx(7.8)
    assert metrics["unrealized_price_pnl"] == pytest.approx(10.0)
    assert metrics["unrealized_price_pnl_base"] == pytest.approx(79.0)
    assert metrics["unrealized_fx_pnl_base"] == pytest.approx(10.0)
    assert metrics["unrealized_pnl_base"] == pytest.approx(89.0)
    assert metrics["unrealized_pnl_base"] == pytest.approx(
        metrics["unrealized_price_pnl_base"] + metrics["unrealized_fx_pnl_base"]
    )
    assert metrics["cost_basis_fx_coverage_status"] == "stale"


def test_position_unrealized_pnl_does_not_invent_missing_historical_fx() -> None:
    metrics = holdings_market_profile.position_unrealized_metrics(
        cost_basis=100.0,
        cost_basis_base_current_fx=790.0,
        current_fx_stale=False,
        cost_basis_origins=[
            {
                "acquisition_date": "2026-01-01",
                "remaining_cost_basis": 100.0,
            }
        ],
        market_value=110.0,
        market_value_base=869.0,
        currency="USD",
        base_currency="CNY",
        as_of_date=date(2026, 1, 3),
        direct_fx_instruments={},
        instrument_detail_cache={},
        resolve_fx_rate_on=lambda **_kwargs: None,
    )

    assert metrics["unrealized_price_pnl"] == pytest.approx(10.0)
    assert metrics["unrealized_price_pnl_base"] == pytest.approx(79.0)
    assert metrics["cost_basis_historical_base"] is None
    assert metrics["unrealized_fx_pnl_base"] is None
    assert metrics["unrealized_pnl_base"] is None
    assert metrics["cost_basis_fx_coverage_status"] == "unavailable"


def test_zero_book_cost_position_keeps_complete_base_unrealized_pnl() -> None:
    metrics = holdings_market_profile.position_unrealized_metrics(
        cost_basis=0.0,
        cost_basis_base_current_fx=0.0,
        current_fx_stale=False,
        cost_basis_origins=[],
        market_value=10.0,
        market_value_base=79.0,
        currency="USD",
        base_currency="CNY",
        as_of_date=date(2026, 1, 3),
        direct_fx_instruments={},
        instrument_detail_cache={},
        resolve_fx_rate_on=lambda **_kwargs: None,
    )

    assert metrics["cost_basis_historical_base"] == pytest.approx(0.0)
    assert metrics["unrealized_fx_pnl_base"] == pytest.approx(0.0)
    assert metrics["unrealized_pnl_base"] == pytest.approx(79.0)
    assert metrics["cost_basis_fx_coverage_status"] == "complete"


def test_pending_subscription_is_a_cash_account_receivable_not_cash_or_position() -> None:
    rows = holdings_market_profile.build_pending_monetary_holding_rows(
        pending_balances=[
            {
                "holding_kind": "pending_subscription",
                "account_id": "cash-main",
                "economic_instrument_id": "fund-a",
                "economic_instrument_ref": {
                    "instrument_id": "fund-a",
                    "instrument_name": "Fund A",
                    "instrument_type": "public_fund",
                    "currency": "USD",
                    "identifiers": [],
                },
                "currency": "USD",
                "amount": 250.0,
                "amount_base": 250.0,
                "transaction_ids": ["txn-a"],
            }
        ],
        as_of_date=date(2026, 7, 24),
        previous_as_of_date=date(2026, 7, 23),
        base_currency="USD",
        direct_fx_instruments={},
        instrument_detail_cache={},
        cash_day_change=lambda **_kwargs: {
            "day_change_pct": 0.0,
            "local_day_change_pct": 0.0,
            "local_day_change_value_base": 0.0,
            "fx_day_change_value_base": 0.0,
            "day_change_value_base": 0.0,
        },
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["holding_kind"] == "pending_subscription"
    assert row["account_id"] == "cash-main"
    assert row["economic_instrument_id"] == "fund-a"
    assert row["instrument_ref"]["instrument_type"] == "other"
    assert row["market_value_base"] == pytest.approx(250.0)
    assert row["available_for_trading"] is False
    assert not holdings_market_profile.is_cash_holding_instrument_id(
        row["instrument_id"]
    )
    assert holdings_market_profile.is_pending_monetary_holding(row)


def test_pending_settlement_identity_includes_both_operational_dates() -> None:
    shared = {
        "holding_kind": "settlement_receivable",
        "account_id": "cash-main",
        "economic_instrument_id": "equity-a",
        "economic_instrument_ref": _instrument_ref(),
        "currency": "USD",
        "amount": 100.0,
        "amount_base": 100.0,
    }
    rows = holdings_market_profile.build_pending_monetary_holding_rows(
        pending_balances=[
            {
                **shared,
                "settlement_date": "2026-07-25",
                "pending_until_date": "2026-07-25",
            },
            {
                **shared,
                "settlement_date": "2026-07-28",
                "pending_until_date": "2026-07-28",
            },
        ],
        as_of_date=date(2026, 7, 24),
        previous_as_of_date=date(2026, 7, 23),
        base_currency="USD",
        direct_fx_instruments={},
        instrument_detail_cache={},
        cash_day_change=lambda **_kwargs: {
            "day_change_pct": 0.0,
            "local_day_change_pct": 0.0,
            "local_day_change_value_base": 0.0,
            "fx_day_change_value_base": 0.0,
            "day_change_value_base": 0.0,
        },
    )

    assert len(rows) == 2
    assert len({str(row["instrument_id"]) for row in rows}) == 2


def _option_contract(
    derivative_contract_id: str,
    *,
    expiry_date: str,
    strike: str,
) -> dict[str, object]:
    return {
        "derivative_contract_id": derivative_contract_id,
        "portfolio_id": "portfolio",
        "account_id": "broker",
        "contract_name": derivative_contract_id,
        "contract_type": "option",
        "currency": "USD",
        "external_reference": f"TEST-{derivative_contract_id}",
        "terms": {
            "underlying_instrument_id": "equity-a",
            "option_type": "call",
            "expiry_date": expiry_date,
            "strike": strike,
            "contract_multiplier": "100",
        },
        "created_at": "2026-01-01T00:00:00Z",
    }


def test_short_option_rows_do_not_allocate_or_require_underlying_holdings() -> None:
    obligations = []
    for instrument_id, expiry_date, strike in (
        ("option-near", "2026-01-10", "10"),
        ("option-far", "2026-02-10", "20"),
    ):
        obligations.append(
            {
                "status": "open",
                "account_id": "broker",
                "derivative_contract_id": instrument_id,
                "related_underlying_id": "equity-a",
                "contract_currency": "USD",
                "remaining_quantity": 1.0,
                "open_contract_quantity": 1.0,
                "required_underlying_quantity": 100.0,
                "premium_received_gross": 300.0,
                "premium_basis_remaining": 300.0,
                "carrying_liability": 300.0,
                "expiry_date": expiry_date,
                "strike": strike,
                "option_type": "call",
                "contract_multiplier": 100.0,
                "opened_at": "2026-01-01T10:00:00+08:00",
                "derivative_contract": _option_contract(
                    instrument_id,
                    expiry_date=expiry_date,
                    strike=strike,
                ),
            }
        )

    rows = holdings_market_profile.build_option_obligation_holding_rows(
        obligations,
        as_of_date=date(2026, 1, 1),
        base_currency="USD",
        nav=10_000.0,
        convert_amount_on=lambda amount, **_kwargs: (float(amount) * 2.0, False),
    )

    assert [row["derivative_contract_id"] for row in rows] == [
        "option-near",
        "option-far",
    ]
    assert all(row["instrument_id"] is None for row in rows)
    assert rows[0]["quantity"] == pytest.approx(-1.0)
    assert rows[0]["required_underlying_quantity"] == pytest.approx(100.0)
    assert rows[0]["strike_notional"] == pytest.approx(1_000.0)
    assert rows[0]["strike_notional_base"] == pytest.approx(2_000.0)
    assert rows[1]["quantity"] == pytest.approx(-1.0)
    assert rows[1]["required_underlying_quantity"] == pytest.approx(100.0)
    assert rows[1]["strike_notional"] == pytest.approx(2_000.0)
    assert rows[1]["strike_notional_base"] == pytest.approx(4_000.0)
    assert rows[1]["carrying_value_historical_base"] == pytest.approx(-600.0)
    assert rows[1]["carrying_fx_translation_base"] == pytest.approx(0.0)
    assert rows[1]["carrying_fx_coverage_status"] == "complete"
    assert rows[1]["coverage_status"] == "event-liability"


def test_operational_summary_expiry_boundaries_settlement_net_and_alerts() -> None:
    as_of_date = date(2026, 1, 1)
    obligation_rows = [
        {
            "line_id": f"option-{days}",
            "holding_kind": "option_obligation",
            "expiry_date": (as_of_date + timedelta(days=days)).isoformat(),
            "open_contract_quantity": 1.0,
            "required_underlying_quantity": 100.0,
            "liability_value_base": 25.0,
            "strike_notional_base": 1_000.0,
        }
        for days in (0, 1, 7, 8, 30, 31, 90, 91)
    ]
    settlement_rows = [
        {
            "line_id": "receivable",
            "holding_kind": "settlement_receivable",
            "settlement_date": "2025-12-31",
            "pending_status": "overdue",
            "settlement_amount_base": 120.0,
        },
        {
            "line_id": "payable",
            "holding_kind": "settlement_payable",
            "settlement_date": "2026-01-03",
            "pending_status": "awaiting_settlement",
            "settlement_amount_base": -40.0,
        },
    ]

    result = holdings_market_profile.summarize_holdings_operational_status(
        [*obligation_rows, *settlement_rows],
        as_of_date=as_of_date,
    )
    summary = result["operational_summary"]
    assert [
        (bucket["bucket"], bucket["obligation_count"])
        for bucket in summary["expiry_buckets"]
    ] == [
        ("expired_or_due", 1),
        ("next_7_days", 2),
        ("next_30_days", 2),
        ("next_90_days", 2),
        ("later", 1),
    ]
    assert summary["option_obligation_exposure"]["strike_notional_base"] == pytest.approx(
        8_000.0
    )
    assert summary["settlement_exposure"] == {
        "pending_line_count": 2,
        "receivable_base": 120.0,
        "payable_base": 40.0,
        "net_base": 80.0,
        "earliest_settlement_date": "2025-12-31",
        "overdue_line_count": 1,
        "unavailable_base_line_count": 0,
    }
    alerts_by_code = {
        str(alert["code"]): alert for alert in result["operational_alerts"]
    }
    assert set(alerts_by_code) == {
        "option_expiry_due",
        "option_expiry_next_7_days",
        "pending_settlement_overdue",
    }
    assert alerts_by_code["option_expiry_due"]["related_line_ids"] == [
        "option-0"
    ]
    assert alerts_by_code["option_expiry_next_7_days"]["related_line_ids"] == [
        "option-1",
        "option-7",
    ]

    unavailable = holdings_market_profile.summarize_holdings_operational_status(
        [
            {
                "line_id": "unconverted",
                "holding_kind": "settlement_receivable",
                "settlement_date": "2026-01-03",
                "pending_status": "awaiting_settlement",
                "settlement_amount_base": None,
            }
        ],
        as_of_date=as_of_date,
    )
    assert unavailable["operational_summary"]["settlement_exposure"][
        "net_base"
    ] is None
    assert unavailable["operational_alerts"] == [
        {
            "code": "pending_settlement_fx_unavailable",
            "severity": "warning",
            "title": "Settlement exposure conversion unavailable",
            "message": "At least one pending settlement line cannot be converted to base currency.",
            "related_line_ids": ["unconverted"],
        }
    ]


def test_negative_settled_cash_is_an_explicit_operational_alert() -> None:
    result = holdings_market_profile.summarize_holdings_operational_status(
        [
            {
                "line_id": "cash:USD",
                "holding_kind": "settled_cash",
                "market_value": -25.0,
            }
        ],
        as_of_date=date(2026, 1, 1),
    )

    assert result["operational_alerts"] == [
        {
            "code": "negative_settled_cash",
            "severity": "critical",
            "title": "Negative settled cash",
            "message": (
                "1 settled cash line(s) are negative; record the missing funding "
                "or financing fact."
            ),
            "related_line_ids": ["cash:USD"],
        }
    ]


def test_position_lot_aggregations_golden_contract() -> None:
    lots = [
        {
            "account_id": "account-b",
            "position_reference_id": "equity-a",
            "instrument_id": "equity-a",
            "instrument_ref": _instrument_ref(),
            "currency": "usd",
            "remaining_quantity": "2",
            "remaining_cost_basis": "200",
            "cost_basis_method": "fifo",
            "acquisition_date": "2026-01-03",
        },
        {
            "account_id": "account-a",
            "position_reference_id": "equity-a",
            "instrument_id": "equity-a",
            "instrument_ref": _instrument_ref(),
            "currency": "USD",
            "remaining_quantity": 3.0,
            "remaining_cost_basis": 330.0,
            "cost_basis_method": "moving_average",
            "opened_at": "2026-01-01T09:00:00",
        },
        {
            "account_id": "account-z",
            "position_reference_id": "equity-zero",
            "instrument_id": "equity-zero",
            "instrument_ref": _instrument_ref("equity-zero"),
            "currency": "USD",
            "remaining_quantity": 1.0,
            "remaining_cost_basis": 10.0,
            "cost_basis_method": "fifo",
            "acquisition_date": "2026-01-01",
        },
        {
            "account_id": "account-z",
            "position_reference_id": "equity-zero",
            "instrument_id": "equity-zero",
            "instrument_ref": _instrument_ref("equity-zero"),
            "currency": "USD",
            "remaining_quantity": -1.0,
            "remaining_cost_basis": -10.0,
            "cost_basis_method": "fifo",
            "acquisition_date": "2026-01-02",
        },
    ]

    new_instrument = holdings_market_profile.position_buckets_from_lots(deepcopy(lots))
    new_account = (
        holdings_market_profile.position_buckets_by_account_reference_from_lots(
            deepcopy(lots)
        )
    )

    assert len(new_instrument) == 1
    assert new_instrument[0]["quantity"] == 5.0
    assert new_instrument[0]["cost_basis"] == 530.0
    assert new_instrument[0]["account_ids"] == ["account-a", "account-b"]
    assert new_instrument[0]["cost_basis_method"] == "mixed"
    assert [bucket["holding_start_date"] for bucket in new_account] == [
        date(2026, 1, 3),
        date(2026, 1, 1),
    ]

    missing_currency = [{**lots[0], "currency": None}]
    with pytest.raises(ValueError, match="position-lot currency is required"):
        holdings_market_profile.position_buckets_from_lots(missing_currency)


def test_materialized_holding_market_profile_uses_injected_dependencies() -> None:
    detail = {"instrument_id": "equity-a"}

    def fake_convert_amount(amount, **_kwargs):
        return (None if amount is None else float(amount) * 2.0), False

    def fake_detail_get(instrument_id, _cache):
        return detail if instrument_id == "equity-a" else None

    def fake_select_market_point(*, role, **_kwargs):
        if role == "valuation":
            return {
                "role": role,
                "value": "100",
                "as_of_date": "2026-01-03",
                "metric_family": "price",
                "quote_basis": "close",
                "provider": "golden",
                "status": "complete",
                "price_unit": "minor",
                "price_scale": "10",
            }
        return {"role": role, "value": "55", "quote_basis": "adjusted_close"}

    def fake_previous_market_point(*, selected_point, **_kwargs):
        return {
            "value": "90" if selected_point.get("role") == "valuation" else "50"
        }

    def fake_position_market_value(**kwargs):
        return (
            float(kwargs["quantity"])
            * float(kwargs["last_price"])
            / float(kwargs["price_scale"])
        )

    def fake_holding_day_change(**_kwargs):
        return 0.10, 3.0

    def fake_position_day_change(**_kwargs):
        return {
            "day_change_pct": 0.10,
            "local_day_change_pct": 0.10,
            "local_day_change_value_base": 6.0,
            "fx_day_change_value_base": 0.0,
            "day_change_value_base": 6.0,
            "fx_rate_to_base": 2.0,
            "fx_rate_as_of_date": "2026-01-03",
            "previous_fx_rate_to_base": 2.0,
            "previous_fx_rate_as_of_date": "2026-01-02",
            "fx_rate_source_instrument_ids": [],
            "fx_rate_stale": False,
        }

    def fake_normalize_instrument(instrument_id, _instrument_ref, **_kwargs):
        return {"instrument_id": instrument_id, "normalized": True}

    def fake_build_cash_rows(**_kwargs):
        return [
            {
                "account_id": "cash:USD",
                "instrument_id": "cash:USD",
                "market_value_base": 10.0,
            }
        ]

    def fake_apply_weights(rows, _nav):
        for row in rows:
            row["portfolio_weight"] = 999.0

    kwargs = {
        "account_instrument_buckets": [
            {
                "account_id": "account-z",
                "position_reference_id": "equity-a",
                "instrument_id": "equity-a",
                "instrument_ref": _instrument_ref(),
                "currency": "usd",
                "quantity": "2",
                "cost_basis": "30",
                "cost_basis_method": "fifo",
                "open_position_lot_count": 2,
                "holding_start_date": "2026-01-01",
            },
            {"account_id": "", "instrument_id": "ignored"},
        ],
        "cash_balances": [{"currency": "USD", "amount": 10.0}],
        "as_of_date": date(2026, 1, 3),
        "previous_as_of_date": date(2026, 1, 2),
        "base_currency": "USD",
        "direct_fx_instruments": {},
        "instrument_detail_cache": {},
        "nav": 100.0,
    }

    def safe_float(value):
        return None if value is None else float(value)

    def parse_iso_date(value):
        return date.fromisoformat(str(value)[:10]) if value else None

    new_rows = holdings_market_profile.build_materialized_holding_rows(
        **kwargs,
        normalize_currency=valuation_fx.normalized_currency,
        safe_float=safe_float,
        parse_iso_date=parse_iso_date,
        convert_amount_on=fake_convert_amount,
        instrument_detail_cache_get=fake_detail_get,
        select_market_point_as_of=fake_select_market_point,
        previous_market_point_for_selected_point=fake_previous_market_point,
        position_market_value=fake_position_market_value,
        holding_day_change=fake_holding_day_change,
        position_day_change=fake_position_day_change,
        resolve_fx_rate_on=lambda **_kwargs: {
            "rate": 1.0,
            "as_of_date": "2026-01-03",
            "stale": False,
        },
        normalize_instrument=fake_normalize_instrument,
        build_cash_rows=fake_build_cash_rows,
        build_pending_rows=lambda **_kwargs: [],
        apply_portfolio_weights=fake_apply_weights,
    )

    assert [row["instrument_id"] for row in new_rows] == ["equity-a", "cash:USD"]
    assert new_rows[0]["cost_basis_base"] == 60.0
    assert new_rows[0]["market_value_base"] == 40.0
    assert new_rows[0]["day_change_value_base"] == 6.0
    assert new_rows[0]["instrument_holding_start_date"] == "2026-01-01"
    assert all(row["portfolio_weight"] == 999.0 for row in new_rows)


def test_position_weight_aggregation_golden_contract() -> None:
    positions = [
        {"instrument_id": "a", "market_value_base": "25"},
        {"instrument_id": "b", "market_value_base": 75.0},
        {"instrument_id": "missing", "market_value_base": None},
    ]
    new_positions = deepcopy(positions)

    holdings_market_profile.apply_position_portfolio_weights(new_positions, 100.0)

    assert [position["portfolio_weight"] for position in new_positions] == [
        0.25,
        0.75,
        None,
    ]
