from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from portfolio_ops_instrument_core import fx_rates


def _direct_record(
    quote_currency: str,
    *,
    status: str,
    rate: str,
) -> dict[str, object]:
    instrument_id = f"fx-usd-{quote_currency.lower()}"
    return {
        "base_currency": "USD",
        "quote_currency": quote_currency,
        "rate": Decimal(rate),
        "as_of_date": date(2026, 7, 15),
        "source_kind": "direct",
        "instrument_id": instrument_id,
        "source_instrument_ids": [instrument_id],
        "provider": "pytest",
        "status": status,
    }


@pytest.mark.parametrize(
    ("hkd_status", "cny_status", "expected_status"),
    [
        ("complete", "complete", "complete"),
        ("partial", "complete", "partial"),
        ("partial", "partial", "partial"),
        ("unavailable", "complete", "unavailable"),
        ("unavailable", "partial", "unavailable"),
    ],
)
def test_cross_rate_status_is_worst_leg_status(
    monkeypatch: pytest.MonkeyPatch,
    hkd_status: str,
    cny_status: str,
    expected_status: str,
) -> None:
    records = {
        "HKD": _direct_record("HKD", status=hkd_status, rate="7.8"),
        "CNY": _direct_record("CNY", status=cny_status, rate="7.2"),
    }
    monkeypatch.setattr(
        fx_rates,
        "_direct_rate_record",
        lambda _session_factory, _base_currency, quote_currency: records[quote_currency],
    )

    record = fx_rates._cross_rate_record(object(), "HKD", "CNY")

    assert record is not None
    assert record["status"] == expected_status


def _spot_point(
    *,
    value: str = "7.8",
    currency: str = "HKD",
    as_of_date: str = "2026-07-15",
    status: str = "complete",
) -> dict[str, object]:
    return {
        "metric_family": "fx",
        "quote_basis": "spot",
        "as_of_date": as_of_date,
        "value": value,
        "currency": currency,
        "price_unit": "rate",
        "price_scale": "1",
        "provider": "pytest",
        "status": status,
    }


@pytest.mark.parametrize(
    "invalid_override",
    [
        {"currency": "CNY"},
        {"value": "0"},
        {"value": "-7.8"},
        {"value": "NaN"},
        {"value": "Infinity"},
        {"as_of_date": "not-a-date"},
        {"status": ""},
    ],
)
def test_fx_rate_read_fails_closed_for_invalid_spot(
    monkeypatch: pytest.MonkeyPatch,
    invalid_override: dict[str, str],
) -> None:
    hkd_point = {**_spot_point(), **invalid_override}
    instruments = {
        "fx-usd-hkd": {
            "instrument_id": "fx-usd-hkd",
            "instrument_type": "fx",
            "currency": "HKD",
            "market_data": [hkd_point],
        },
        "fx-usd-cny": {
            "instrument_id": "fx-usd-cny",
            "instrument_type": "fx",
            "currency": "CNY",
            "market_data": [_spot_point(value="7.2", currency="CNY")],
        },
    }
    monkeypatch.setattr(
        fx_rates,
        "get_instrument",
        lambda _session_factory, instrument_id: instruments[instrument_id],
    )

    assert fx_rates._direct_rate_record(object(), "USD", "HKD") is None
    assert fx_rates._inverse_rate_record(object(), "HKD", "USD") is None
    assert fx_rates._cross_rate_record(object(), "HKD", "CNY") is None


@pytest.mark.parametrize("invalid_rate", ["0", "-1", "NaN", "Infinity"])
def test_inverse_rate_fails_closed_for_invalid_direct_rate(
    monkeypatch: pytest.MonkeyPatch,
    invalid_rate: str,
) -> None:
    monkeypatch.setattr(
        fx_rates,
        "_direct_rate_record",
        lambda *_args, **_kwargs: {"rate": invalid_rate},
    )

    assert fx_rates._inverse_rate_record(object(), "HKD", "USD") is None


def test_fx_rate_read_does_not_fall_back_past_corrupt_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instrument = {
        "instrument_id": "fx-usd-hkd",
        "instrument_type": "fx",
        "currency": "HKD",
        "market_data": [
            _spot_point(value="0", as_of_date="2026-07-14"),
            _spot_point(value="7.8", as_of_date="2026-07-15"),
        ],
    }
    monkeypatch.setattr(
        fx_rates,
        "get_instrument",
        lambda _session_factory, _instrument_id: instrument,
    )

    assert fx_rates._direct_rate_record(object(), "USD", "HKD") is None
