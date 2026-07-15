from __future__ import annotations

from copy import deepcopy
from datetime import date

import pytest

from portfolio_app.services import holdings_market_profile, valuation_fx


def _instrument_ref(instrument_id: str = "equity-a") -> dict[str, object]:
    return {
        "instrument_id": instrument_id,
        "instrument_name": "Asset A",
        "instrument_type": "Equity",
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
        "currency": "USD",
        "identifiers": instrument_ref["identifiers"],
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


def test_cash_profile_uses_injected_fx_and_identity_dependencies() -> None:
    def current_fx(**_kwargs):
        return {"rate": "1.20", "as_of_date": "2026-01-03"}

    def previous_fx(**_kwargs):
        return {"rate": "1.00", "as_of_date": "2026-01-02"}

    metric_kwargs = {
        "amount": 100.0,
        "currency": "EUR",
        "base_currency": "USD",
        "as_of_date": date(2026, 1, 3),
        "direct_fx_instruments": {},
        "instrument_detail_cache": {},
    }
    metric = holdings_market_profile.cash_day_change_metrics(
        **metric_kwargs,
        resolve_fx_rate_on=current_fx,
        resolve_previous_fx_rate_before=previous_fx,
    )
    assert metric == pytest.approx((0.20, 20.0))

    def fake_day_change(**_kwargs):
        return 0.25, 25.0

    def fake_cash_id(currency: str) -> str:
        return f"patched:{currency}"

    def fake_cash_ref(currency: str) -> dict[str, object]:
        return {"instrument_id": f"ref:{currency}", "currency": currency}

    row_kwargs = {
        "cash_balances": [
            {
                "currency": "USD",
                "amount": "50",
                "amount_base": "50",
                "account_ids": ["cash-b", "cash-a"],
            },
            {
                "currency": "EUR",
                "amount": 100.0,
                "amount_base": 120.0,
                "account_ids": ["cash-eur"],
            },
            {"currency": "JPY", "amount": 0.0, "amount_base": 0.0},
        ],
        "as_of_date": date(2026, 1, 3),
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
    assert [row["currency"] for row in rows] == ["EUR", "USD"]
    assert rows[1]["account_ids"] == ["cash-a", "cash-b"]


def test_position_lot_aggregations_golden_contract() -> None:
    lots = [
        {
            "account_id": "account-b",
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
        holdings_market_profile.position_buckets_by_account_instrument_from_lots(
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
        None,
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
        normalize_instrument=fake_normalize_instrument,
        build_cash_rows=fake_build_cash_rows,
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
