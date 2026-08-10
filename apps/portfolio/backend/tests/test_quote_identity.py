from __future__ import annotations

from copy import deepcopy
from datetime import date

import pytest

from portfolio_app.services import (
    calculation_frequency,
    execution_quotes,
    instrument_charts,
    ledger,
    market_data,
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


def test_fund_analytical_consumers_do_not_fallback_to_unit_nav() -> None:
    detail = {
        "instrument_id": "fund-unit-nav-only",
        "instrument_name": "Unit NAV Only Fund",
        "instrument_type": "fund",
        "currency": "USD",
        "quote_selection_policy": {
            "trading": ["official_nav"],
            "valuation": ["official_nav"],
            "total_return": ["total_return_nav"],
            "chart": ["total_return_nav"],
            "reference": ["official_nav"],
        },
        "market_data": [
            _point(
                metric_family="nav",
                quote_basis="official_nav",
                as_of_date="2026-01-01",
                value="1.00",
            ),
            _point(
                metric_family="nav",
                quote_basis="official_nav",
                as_of_date="2026-01-02",
                value="1.01",
            ),
        ],
    }

    assert market_data.analytical_return_quote_bases(detail) == ["total_return_nav"]
    assert research_solver._selected_price_points(
        deepcopy(detail),
        end_date=date(2026, 1, 2),
    ) == []
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

    valuation_point = performance._select_market_point_as_of(
        detail=deepcopy(detail),
        role="valuation",
        as_of_date=date(2026, 1, 2),
    )
    assert valuation_point is not None
    assert valuation_point["quote_basis"] == "official_nav"


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


def test_explicit_price_contract_only_allows_canonical_unit_and_scale():
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
    assert unavailable.unavailable_reason == "price_contract_unsupported"

    invalid = _detail(
        [
            _point(
                as_of_date="2026-01-02",
                value="100",
                price_unit="rate",
                price_scale=1.0,
            )
        ]
    )
    unavailable = resolve_quote_point(
        invalid,
        candidate_bases=["close"],
        as_of_date=date(2026, 1, 2),
    )
    assert unavailable.point is None
    assert unavailable.unavailable_reason == "price_contract_unsupported"


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
