from __future__ import annotations

from copy import deepcopy
from datetime import date

import pytest

from portfolio_app.services import (
    calculation_frequency,
    execution_quotes,
    instrument_charts,
    ledger,
    performance,
    research_solver,
    valuation_fx,
)
from portfolio_app.services.market_data import resolve_quote_point, resolve_quote_series


def _point(
    *,
    metric_family: str = "price",
    quote_basis: str = "close",
    as_of_date: str,
    value: str,
    currency: str = "USD",
    price_unit: str = "per_unit",
    price_scale: float = 1.0,
) -> dict[str, object]:
    point: dict[str, object] = {
        "instrument_id": "test-instrument",
        "metric_family": metric_family,
        "quote_basis": quote_basis,
        "as_of_date": as_of_date,
        "value": value,
        "currency": currency,
        "provider": "quote-identity-test",
        "status": "complete",
    }
    point["price_unit"] = price_unit
    point["price_scale"] = price_scale
    return point


def _detail(
    points: list[dict[str, object]],
    *,
    instrument_type: str = "equity",
    currency: str = "USD",
    valuation: list[str] | None = None,
) -> dict[str, object]:
    return {
        "instrument_id": "test-instrument",
        "instrument_name": "Quote Identity Instrument",
        "instrument_type": instrument_type,
        "currency": currency,
        "quote_selection_policy": {
            "trading": list(valuation or ["close"]),
            "valuation": list(valuation or ["close"]),
            "total_return": list(valuation or ["close"]),
            "chart": list(valuation or ["close"]),
            "reference": list(valuation or ["close"]),
        },
        "market_data": points,
        "latest_market_data": deepcopy(points[-1:]),
    }


@pytest.mark.parametrize(
    ("points", "reason"),
    [
        (
            [
                _point(as_of_date="2026-01-01", value="100"),
                _point(as_of_date="2026-01-02", value="780", currency="HKD"),
            ],
            "ambiguous_quote_series_identity",
        ),
        (
            [_point(metric_family="nav", as_of_date="2026-01-02", value="100")],
            "quote_metric_family_mismatch",
        ),
        (
            [_point(as_of_date="2026-01-02", value="780", currency="HKD")],
            "quote_currency_mismatch",
        ),
        (
            [
                _point(as_of_date="2026-01-02", value="100"),
                _point(as_of_date="2026-01-02", value="101"),
            ],
            "duplicate_quote_observation",
        ),
    ],
)
def test_quote_resolver_fails_closed_for_non_unique_series_identity(points, reason):
    resolution = resolve_quote_series(
        _detail(points),
        candidate_bases=["close"],
        end_date=date(2026, 1, 2),
    )

    assert resolution.points == ()
    assert resolution.unavailable_reason == reason


def test_fund_close_cannot_be_relabelled_as_nav() -> None:
    resolution = resolve_quote_series(
        _detail(
            [_point(metric_family="nav", as_of_date="2026-01-02", value="100")],
            instrument_type="fund",
        ),
        candidate_bases=["close"],
        end_date=date(2026, 1, 2),
    )

    assert resolution.points == ()
    assert resolution.unavailable_reason == "quote_metric_family_mismatch"


def test_all_portfolio_quote_consumers_reject_mixed_currency_series():
    detail = _detail(
        [
            _point(as_of_date="2026-01-01", value="100"),
            _point(as_of_date="2026-01-02", value="780", currency="HKD"),
        ]
    )

    assert performance._select_market_point_as_of(
        detail=deepcopy(detail),
        role="valuation",
        as_of_date=date(2026, 1, 2),
    ) is None
    assert ledger._select_quote_value(
        deepcopy(detail),
        role="valuation",
        as_of_date=date(2026, 1, 2),
    ) is None
    execution = execution_quotes.build_execution_quote_from_detail(
        deepcopy(detail),
        instrument_id="test-instrument",
        as_of_date=date(2026, 1, 2),
    )
    assert execution["value"] is None
    assert execution["unavailable_reason"] == "ambiguous_quote_series_identity"
    assert research_solver._selected_price_points(
        deepcopy(detail),
        end_date=date(2026, 1, 2),
    ) == []


def test_direct_fx_lookup_rejects_mixed_currency_spot_identity():
    detail = _detail(
        [
            _point(
                metric_family="fx",
                quote_basis="spot",
                as_of_date="2026-01-01",
                value="7.80",
                currency="HKD",
                price_unit="rate",
                price_scale=1.0,
            ),
            _point(
                metric_family="fx",
                quote_basis="spot",
                as_of_date="2026-01-02",
                value="7.20",
                currency="CNY",
                price_unit="rate",
                price_scale=1.0,
            ),
        ],
        instrument_type="fx",
        currency="HKD",
        valuation=["spot"],
    )

    assert valuation_fx.direct_fx_point_as_of(
        instrument_id="test-instrument",
        as_of_date=date(2026, 1, 2),
        instrument_detail_cache={"test-instrument": detail},
        instrument_detail_loader=lambda _instrument_id: None,
    ) is None
    chart_selection = instrument_charts._select_chart_series(
        deepcopy(detail),
        as_of_date=date(2026, 1, 2),
    )
    assert chart_selection.points == ()
    assert chart_selection.selected_basis is None
    assert calculation_frequency.selected_observation_dates_from_detail(
        deepcopy(detail),
        end_date=date(2026, 1, 2),
    ) == []


def test_direct_fx_lookup_accepts_explicit_rate_scale_contract():
    detail = _detail(
        [
            _point(
                metric_family="fx",
                quote_basis="spot",
                as_of_date="2026-01-02",
                value="7.80",
                currency="HKD",
                price_unit="rate",
                price_scale=1.0,
            )
        ],
        instrument_type="fx",
        currency="HKD",
        valuation=["spot"],
    )

    selected = resolve_quote_point(
        detail,
        candidate_bases=["spot"],
        as_of_date=date(2026, 1, 2),
    )

    assert selected.point is not None
    assert selected.point["value"] == pytest.approx(7.8)
    assert selected.point["price_unit"] == "rate"
    assert selected.point["price_scale"] == pytest.approx(1.0)
    direct_fx = valuation_fx.direct_fx_point_as_of(
        instrument_id="test-instrument",
        as_of_date=date(2026, 1, 2),
        instrument_detail_cache={"test-instrument": detail},
        instrument_detail_loader=lambda _instrument_id: None,
    )
    assert direct_fx is not None
    assert direct_fx["rate"] == pytest.approx(7.8)
    assert direct_fx["as_of_date"] == date(2026, 1, 2)


def test_previous_quote_is_locked_to_selected_series_identity():
    detail = _detail(
        [
            _point(as_of_date="2026-01-01", value="100"),
            _point(as_of_date="2026-01-02", value="101"),
        ]
    )
    selected = resolve_quote_point(
        detail,
        candidate_bases=["close"],
        as_of_date=date(2026, 1, 2),
    )

    assert selected.point is not None
    assert selected.point["value"] == pytest.approx(101.0)
    previous = performance._previous_market_point_for_selected_point(
        detail=detail,
        selected_point=selected.point,
    )
    assert previous is not None
    assert previous["value"] == pytest.approx(100.0)
    assert (
        previous["metric_family"],
        previous["quote_basis"],
        previous["currency"],
    ) == ("price", "close", "USD")


def test_bond_dirty_price_requires_explicit_percent_of_par_scale():
    explicit = _detail(
        [
            _point(
                quote_basis="dirty_price",
                as_of_date="2026-01-02",
                value="98.5",
                price_unit="percent_of_par",
                price_scale=0.01,
            )
        ],
        instrument_type="bond",
        valuation=["dirty_price", "clean_price"],
    )
    selected = resolve_quote_point(
        explicit,
        candidate_bases=["dirty_price", "clean_price"],
        as_of_date=date(2026, 1, 2),
    )

    assert selected.point is not None
    assert selected.point["value"] == pytest.approx(98.5)
    assert selected.point["price_unit"] == "percent_of_par"
    assert selected.point["price_scale"] == pytest.approx(0.01)
    assert valuation_fx.position_market_value(
        quantity=1000,
        last_price=98.5,
        instrument_ref={"instrument_type": "bond"},
        price_scale=0.01,
    ) == pytest.approx(985.0)

    missing_contract = deepcopy(explicit)
    missing_contract["market_data"][0].pop("price_unit")
    missing_contract["market_data"][0].pop("price_scale")
    unavailable = resolve_quote_point(
        missing_contract,
        candidate_bases=["dirty_price"],
        as_of_date=date(2026, 1, 2),
    )
    assert unavailable.point is None
    assert unavailable.unavailable_reason == "bond_price_contract_unavailable"


def test_non_bond_explicit_price_contract_only_allows_per_unit_scale_one():
    valid = _detail(
        [
            _point(
                as_of_date="2026-01-02",
                value="100",
                price_unit="per_unit",
                price_scale=1.0,
            )
        ]
    )
    assert resolve_quote_point(
        valid,
        candidate_bases=["close"],
        as_of_date=date(2026, 1, 2),
    ).point is not None

    missing = deepcopy(valid)
    missing["market_data"][0].pop("price_unit")
    missing["market_data"][0].pop("price_scale")
    unavailable = resolve_quote_point(
        missing,
        candidate_bases=["close"],
        as_of_date=date(2026, 1, 2),
    )
    assert unavailable.point is None
    assert unavailable.unavailable_reason == "non_bond_price_contract_unsupported"

    invalid = _detail(
        [
            _point(
                as_of_date="2026-01-02",
                value="100",
                price_unit="percent_of_par",
                price_scale=0.01,
            )
        ]
    )
    unavailable = resolve_quote_point(
        invalid,
        candidate_bases=["close"],
        as_of_date=date(2026, 1, 2),
    )
    assert unavailable.point is None
    assert unavailable.unavailable_reason == "non_bond_price_contract_unsupported"


def test_bond_clean_price_requires_matching_accrued_interest_component():
    clean = _point(
        quote_basis="clean_price",
        as_of_date="2026-01-02",
        value="98.5",
        price_unit="percent_of_par",
        price_scale=0.01,
    )
    accrued = _point(
        quote_basis="accrued_interest",
        as_of_date="2026-01-02",
        value="1.25",
        price_unit="percent_of_par",
        price_scale=0.01,
    )
    detail = _detail(
        [clean, accrued],
        instrument_type="bond",
        valuation=["clean_price"],
    )

    clean_only_execution = execution_quotes.build_execution_quote_from_detail(
        _detail(
            [clean],
            instrument_type="bond",
            valuation=["clean_price"],
        ),
        instrument_id="test-instrument",
        as_of_date=date(2026, 1, 2),
    )
    assert clean_only_execution["value"] is None
    assert (
        clean_only_execution["unavailable_reason"]
        == "clean_price_requires_matching_accrued_interest"
    )

    selected = resolve_quote_point(
        detail,
        candidate_bases=["clean_price"],
        as_of_date=date(2026, 1, 2),
    )
    assert selected.point is not None
    assert selected.point["value"] == pytest.approx(99.75)
    assert selected.point["quote_basis"] == "dirty_price"
    assert selected.point["source_quote_basis"] == "clean_price"
    assert selected.point["clean_value"] == pytest.approx(98.5)
    assert selected.point["accrued_interest"] == pytest.approx(1.25)
    assert valuation_fx.position_market_value(
        quantity=1000,
        last_price=float(selected.point["value"]),
        instrument_ref={"instrument_type": "bond"},
        price_scale=float(selected.point["price_scale"]),
    ) == pytest.approx(997.5)

    execution = execution_quotes.build_execution_quote_from_detail(
        detail,
        instrument_id="test-instrument",
        as_of_date=date(2026, 1, 2),
    )
    assert execution["quote_basis"] == "dirty_price"
    assert execution["selection_role"] == "trading"
    assert execution["value"] == pytest.approx(99.75)

    for invalid_accrued, expected_reason in (
        (None, "clean_price_requires_matching_accrued_interest"),
        (
            {**accrued, "as_of_date": "2026-01-01"},
            "clean_price_requires_matching_accrued_interest",
        ),
        ({**accrued, "currency": "HKD"}, "accrued_interest_currency_mismatch"),
        (
            {**accrued, "price_scale": 1.0},
            "accrued_interest_price_contract_mismatch",
        ),
    ):
        invalid_points = [clean]
        if invalid_accrued is not None:
            invalid_points.append(invalid_accrued)
        unavailable = resolve_quote_point(
            _detail(
                invalid_points,
                instrument_type="bond",
                valuation=["clean_price"],
            ),
            candidate_bases=["clean_price"],
            as_of_date=date(2026, 1, 2),
        )
        assert unavailable.point is None
        assert unavailable.unavailable_reason == expected_reason


def test_bond_clean_history_fails_when_any_observation_lacks_accrued_interest():
    clean_day_one = _point(
        quote_basis="clean_price",
        as_of_date="2026-01-01",
        value="98.0",
        price_unit="percent_of_par",
        price_scale=0.01,
    )
    clean_day_two = {
        **clean_day_one,
        "as_of_date": "2026-01-02",
        "value": "98.5",
    }
    accrued_day_two = _point(
        quote_basis="accrued_interest",
        as_of_date="2026-01-02",
        value="1.25",
        price_unit="percent_of_par",
        price_scale=0.01,
    )

    resolution = resolve_quote_series(
        _detail(
            [clean_day_one, clean_day_two, accrued_day_two],
            instrument_type="bond",
            valuation=["clean_price"],
        ),
        candidate_bases=["clean_price"],
        end_date=date(2026, 1, 2),
    )

    assert resolution.points == ()
    assert (
        resolution.unavailable_reason
        == "clean_price_requires_matching_accrued_interest"
    )


def test_bond_clean_missing_accrued_falls_back_to_later_dirty_candidate():
    clean = _point(
        quote_basis="clean_price",
        as_of_date="2026-01-02",
        value="98.5",
        price_unit="percent_of_par",
        price_scale=0.01,
    )
    dirty = _point(
        quote_basis="dirty_price",
        as_of_date="2026-01-02",
        value="99.75",
        price_unit="percent_of_par",
        price_scale=0.01,
    )
    detail = _detail(
        [clean, dirty],
        instrument_type="bond",
        valuation=["clean_price", "dirty_price"],
    )

    selected = resolve_quote_point(
        detail,
        candidate_bases=["clean_price", "dirty_price"],
        as_of_date=date(2026, 1, 2),
    )

    assert selected.point is not None
    assert selected.point["quote_basis"] == "dirty_price"
    assert selected.point.get("source_quote_basis") is None
    assert selected.point["value"] == pytest.approx(99.75)


def test_bond_clean_hard_identity_error_does_not_fallback_to_dirty():
    clean_usd = _point(
        quote_basis="clean_price",
        as_of_date="2026-01-02",
        value="98.5",
        price_unit="percent_of_par",
        price_scale=0.01,
    )
    clean_hkd = {**clean_usd, "currency": "HKD", "value": "780"}
    dirty = _point(
        quote_basis="dirty_price",
        as_of_date="2026-01-02",
        value="99.75",
        price_unit="percent_of_par",
        price_scale=0.01,
    )

    unavailable = resolve_quote_point(
        _detail(
            [clean_usd, clean_hkd, dirty],
            instrument_type="bond",
            valuation=["clean_price", "dirty_price"],
        ),
        candidate_bases=["clean_price", "dirty_price"],
        as_of_date=date(2026, 1, 2),
    )

    assert unavailable.point is None
    assert unavailable.unavailable_reason == "ambiguous_quote_series_identity"


def test_bond_clean_accrued_currency_error_does_not_fallback_to_dirty():
    clean = _point(
        quote_basis="clean_price",
        as_of_date="2026-01-02",
        value="98.5",
        price_unit="percent_of_par",
        price_scale=0.01,
    )
    wrong_currency_accrued = _point(
        quote_basis="accrued_interest",
        as_of_date="2026-01-02",
        value="1.25",
        currency="HKD",
        price_unit="percent_of_par",
        price_scale=0.01,
    )
    dirty = _point(
        quote_basis="dirty_price",
        as_of_date="2026-01-02",
        value="99.75",
        price_unit="percent_of_par",
        price_scale=0.01,
    )

    unavailable = resolve_quote_point(
        _detail(
            [clean, wrong_currency_accrued, dirty],
            instrument_type="bond",
            valuation=["clean_price", "dirty_price"],
        ),
        candidate_bases=["clean_price", "dirty_price"],
        as_of_date=date(2026, 1, 2),
    )

    assert unavailable.point is None
    assert unavailable.unavailable_reason == "accrued_interest_currency_mismatch"


def test_unknown_quote_basis_is_explicitly_unsupported():
    unavailable = resolve_quote_point(
        _detail(
            [
                _point(
                    quote_basis="mystery_basis",
                    as_of_date="2026-01-02",
                    value="100",
                )
            ],
            valuation=["mystery_basis"],
        ),
        candidate_bases=["mystery_basis"],
        as_of_date=date(2026, 1, 2),
    )

    assert unavailable.point is None
    assert unavailable.unavailable_reason == "unsupported_quote_basis"
