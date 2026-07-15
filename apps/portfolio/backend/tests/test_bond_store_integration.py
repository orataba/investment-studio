from __future__ import annotations

from datetime import date

import pytest

from portfolio_app.db.session import get_session_factory
from portfolio_app.services import execution_quotes, instrument_registry, performance, valuation_fx
from portfolio_ops_instrument_core import instrument_store as shared_store


AS_OF_DATE = date(2026, 7, 15)
PRICE_UNIT = "percent_of_par"
PRICE_SCALE = 0.01


def _create_bond(*, ticker: str, quote_basis: str) -> str:
    instrument = shared_store.create_instrument(
        get_session_factory(),
        instrument_name=f"Contract {ticker} Bond",
        instrument_type="bond",
        currency="USD",
        identifiers=[
            {
                "identifier_type": "ticker",
                "identifier_value": ticker,
                "is_primary": True,
            }
        ],
        quote_selection_policy={
            "trading": [quote_basis],
            "valuation": [quote_basis],
            "total_return": [quote_basis],
            "chart": [quote_basis],
            "reference": [quote_basis],
        },
    )
    return str(instrument["instrument_id"])


def _market_point(*, quote_basis: str, value: str) -> dict[str, object]:
    return {
        "metric_family": "price",
        "quote_basis": quote_basis,
        "as_of_date": AS_OF_DATE,
        "value": value,
        "currency": "USD",
        "provider": "bond-contract-test",
        "status": "complete",
    }


def _write_points(instrument_id: str, points: list[dict[str, object]]) -> None:
    changed_count = shared_store.upsert_market_data_points(
        get_session_factory(),
        instrument_id=instrument_id,
        rows=points,
    )
    assert changed_count == len(points)


def _point_by_basis(detail: dict[str, object], quote_basis: str) -> dict[str, object]:
    point = next(
        (
            item
            for item in detail.get("market_data", [])
            if isinstance(item, dict) and item.get("quote_basis") == quote_basis
        ),
        None,
    )
    assert point is not None
    return point


def _assert_price_contract(point: dict[str, object]) -> None:
    assert point["price_unit"] == PRICE_UNIT
    assert float(point["price_scale"]) == pytest.approx(PRICE_SCALE)


def _registry_round_trip(instrument_id: str) -> dict[str, object]:
    stored = shared_store.get_instrument(get_session_factory(), instrument_id)
    assert stored is not None
    registry_detail = instrument_registry.get_registry_instrument_detail(instrument_id)
    assert registry_detail is not None
    assert registry_detail["market_data"] == stored["market_data"]
    return registry_detail


def test_dirty_percent_of_par_survives_shared_store_and_values_through_portfolio() -> None:
    instrument_id = _create_bond(ticker="P1CDIRTY", quote_basis="dirty_price")
    _write_points(
        instrument_id,
        [_market_point(quote_basis="dirty_price", value="98.5")],
    )

    detail = _registry_round_trip(instrument_id)
    dirty_point = _point_by_basis(detail, "dirty_price")
    _assert_price_contract(dirty_point)
    latest_dirty = next(
        point
        for point in detail["latest_market_data"]
        if point["quote_basis"] == "dirty_price"
    )
    _assert_price_contract(latest_dirty)

    execution_quote = execution_quotes.get_execution_quote_on_or_before(
        instrument_id,
        as_of_date=AS_OF_DATE,
    )
    assert execution_quote is not None
    assert execution_quote["value"] == pytest.approx(98.5)
    assert execution_quote["quote_basis"] == "dirty_price"
    _assert_price_contract(execution_quote)

    valuation_point = performance._select_market_point_as_of(
        detail=detail,
        role="valuation",
        as_of_date=AS_OF_DATE,
    )
    assert valuation_point is not None
    _assert_price_contract(valuation_point)
    assert valuation_fx.position_market_value(
        quantity=1000,
        last_price=float(valuation_point["value"]),
        instrument_ref=detail,
        price_scale=float(valuation_point["price_scale"]),
    ) == pytest.approx(985.0)


def test_clean_and_same_day_accrued_round_trip_to_dirty_portfolio_valuation() -> None:
    instrument_id = _create_bond(ticker="P1CCLEAN", quote_basis="clean_price")
    _write_points(
        instrument_id,
        [
            _market_point(quote_basis="clean_price", value="98.5"),
            _market_point(quote_basis="accrued_interest", value="1.25"),
        ],
    )

    detail = _registry_round_trip(instrument_id)
    clean_point = _point_by_basis(detail, "clean_price")
    accrued_point = _point_by_basis(detail, "accrued_interest")
    _assert_price_contract(clean_point)
    _assert_price_contract(accrued_point)
    assert float(accrued_point["value"]) == pytest.approx(1.25)
    assert {
        point["quote_basis"]
        for point in detail["latest_market_data"]
    }.issuperset({"clean_price", "accrued_interest"})

    execution_quote = execution_quotes.get_execution_quote_on_or_before(
        instrument_id,
        as_of_date=AS_OF_DATE,
    )
    assert execution_quote is not None
    assert execution_quote["value"] == pytest.approx(99.75)
    assert execution_quote["quote_basis"] == "dirty_price"
    _assert_price_contract(execution_quote)

    valuation_point = performance._select_market_point_as_of(
        detail=detail,
        role="valuation",
        as_of_date=AS_OF_DATE,
    )
    assert valuation_point is not None
    assert valuation_point["source_quote_basis"] == "clean_price"
    assert valuation_point["clean_value"] == pytest.approx(98.5)
    assert valuation_point["accrued_interest"] == pytest.approx(1.25)
    _assert_price_contract(valuation_point)
    assert valuation_fx.position_market_value(
        quantity=1000,
        last_price=float(valuation_point["value"]),
        instrument_ref=detail,
        price_scale=float(valuation_point["price_scale"]),
    ) == pytest.approx(997.5)
