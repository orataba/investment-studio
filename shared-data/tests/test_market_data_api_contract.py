from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from studio_data import contracts
from studio_data.commands import instruments as instrument_routes
from studio_data.cli import execute
from studio_data.services import market_data_ops
from studio_data.services import instrument_store as store_service


def _instrument_record() -> dict[str, object]:
    return {
        "instrument_id": "contract-equity",
        "instrument_name": "Contract Equity",
        "instrument_type": "equity",
        "exchange_code": "XNYS",
        "currency": "USD",
        "identifiers": [
            {
                "identifier_type": "internal",
                "identifier_value": "CONTRACT-EQUITY",
                "is_primary": True,
            }
        ],
        "broker_identifiers": [],
        "latest_market_data": [],
        "quote_selection_policy": {
            "trading": ["close"],
            "valuation": ["close"],
            "total_return": ["adjusted_close"],
            "chart": ["adjusted_close"],
            "reference": ["close"],
        },
        "coverage_state": "complete",
        "source_settings": {},
        "refresh_status": {},
        "lifecycle_state": {},
        "market_data_updated_at": None,
        "corporate_actions": [],
    }


def test_instrument_list_accepts_projection_reconciliation_refresh_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _instrument_record()
    record["refresh_status"] = {
        "status": "refreshed",
        "message": "Projection rebuilt with the current methodology.",
        "requested_at": "2026-07-16T12:00:00Z",
        "requested_by": "pytest",
        "mode": "projection_reconciliation",
        "last_successful_requested_at": "2026-07-16T12:00:00Z",
    }
    monkeypatch.setattr(
        instrument_routes,
        "list_instruments",
        lambda **_kwargs: [record],
    )
    monkeypatch.setattr(
        instrument_routes,
        "instrument_registry_name",
        lambda: "pytest-registry",
    )

    response = instrument_routes.list_instrument_records(include_inactive=True)

    assert response.model_dump(mode="json")["instruments"][0]["refresh_status"]["mode"] == (
        "projection_reconciliation"
    )


def test_market_data_command_derives_contract_and_response_requires_it() -> None:
    payload = contracts.StudioMarketDataUpsertRequest.model_validate(
        {
            "metric_family": "price",
            "quote_basis": "close",
            "as_of_date": "2026-07-15",
            "value": "100",
            "currency": "USD",
            "status": "complete",
        }
    )
    point = contracts.StudioMarketDataPoint.model_validate(
        {
            **payload.model_dump(),
            "price_unit": "per_unit",
            "price_scale": "1",
        }
    )

    assert "price_unit" not in payload.model_dump()
    assert "price_scale" not in payload.model_dump()
    assert point.price_unit == "per_unit"
    assert point.price_scale == Decimal("1")

    with pytest.raises(ValidationError, match="price_unit"):
        contracts.StudioMarketDataPoint.model_validate(payload.model_dump())

    with pytest.raises(
        ValidationError,
        match='quote_basis "close" requires metric_family "price", not "nav"',
    ):
        contracts.StudioMarketDataPoint.model_validate(
            {
                **point.model_dump(),
                "metric_family": "nav",
                "quote_basis": "close",
            }
        )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                "metric_family": "nav",
                "quote_basis": "close",
                "as_of_date": "2026-07-15",
                "value": "100",
                "currency": "USD",
                "status": "complete",
            },
            'requires metric_family "price", not "nav"',
        ),
        (
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": "2026-07-15",
                "value": "100",
                "currency": "USD",
                "price_unit": "per_unit",
                "price_scale": "1",
                "status": "complete",
            },
            "Extra inputs are not permitted",
        ),
    ],
)
def test_market_data_contract_rejects_invalid_price_identity(
    payload: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        contracts.StudioMarketDataUpsertRequest.model_validate(payload)


@pytest.mark.parametrize(
    ("value", "currency", "message"),
    [
        ("0", "HKD", "finite positive decimal"),
        ("-7.8", "HKD", "finite positive decimal"),
        ("7.8", "JPY", "currency must be one of"),
    ],
)
def test_fx_market_data_request_rejects_invalid_rate_contract(
    value: str,
    currency: str,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        contracts.StudioMarketDataUpsertRequest.model_validate(
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-07-15",
                "value": value,
                "currency": currency,
                "status": "complete",
            }
        )

    valid = contracts.StudioMarketDataUpsertRequest.model_validate(
        {
            "metric_family": "fx",
            "quote_basis": "spot",
            "as_of_date": "2026-07-15",
            "value": "7.8",
            "currency": " hkd ",
            "status": "complete",
        }
    )
    assert valid.currency == "HKD"


def test_market_data_api_returns_store_derived_price_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_upsert_market_data(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        record = _instrument_record()
        record["latest_market_data"] = [
            {
                "metric_family": kwargs["metric_family"],
                "quote_basis": kwargs["quote_basis"],
                "as_of_date": kwargs["as_of_date"],
                "value": kwargs["value"],
                "currency": kwargs["currency"],
                "provider": kwargs["provider"],
                "status": kwargs["status"],
                "price_unit": "per_unit",
                "price_scale": Decimal("1"),
            }
        ]
        return record

    monkeypatch.setattr(instrument_routes, "upsert_market_data", fake_upsert_market_data)
    monkeypatch.setattr(
        instrument_routes,
        "notify_market_data_downstream_refresh",
        lambda *_args, **_kwargs: None,
    )

    response = execute(instrument_routes.upsert_instrument_market_data, {
        "instrument_id": "contract-equity",
        "payload": {
            "metric_family": "price",
            "quote_basis": "close",
            "as_of_date": "2026-07-15",
            "value": "100",
            "currency": "USD",
            "provider": "pytest",
            "status": "complete",
        },
    })

    assert "price_unit" not in captured
    assert "price_scale" not in captured
    assert response.model_dump(mode="json")["latest_market_data"][0]["price_unit"] == "per_unit"
    assert response.model_dump(mode="json")["latest_market_data"][0]["price_scale"] == "1"


def test_market_data_cli_rejects_removed_bond_quote_basis() -> None:
    with pytest.raises(ValidationError, match="clean_price"):
        execute(instrument_routes.upsert_instrument_market_data, {
            "instrument_id": "contract-equity",
            "payload": {"metric_family": "price", "quote_basis": "clean_price",
                        "as_of_date": "2026-07-15", "value": "1.25",
                        "currency": "USD", "status": "complete"},
        })

@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        (
            "/api/instruments/contract-equity/nav-import/preview",
            {"raw_text": "not-a-nav-row", "status": "complete"},
        ),
        (
            "/api/instruments/contract-equity/nav-import/file",
            {
                "file_name": "not-a-workbook.xlsx",
                "file_content_base64": "bm90LWEtd29ya2Jvb2s=",
                "status": "complete",
            },
        ),
    ],
)
def test_non_fund_nav_apis_return_400_before_parsing(
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    payload: dict[str, object],
) -> None:
    monkeypatch.setattr(
        market_data_ops,
        "get_instrument",
        lambda instrument_id: {
            "instrument_id": instrument_id,
            "instrument_type": "equity",
        },
    )

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("NAV parser must not run for a non-fund instrument")

    monkeypatch.setattr(market_data_ops, "_parse_nav_rows_from_text", fail_if_called)
    monkeypatch.setattr(market_data_ops, "_parse_nav_rows_from_uploaded_file", fail_if_called)

    handler = (instrument_routes.preview_instrument_nav_history
               if endpoint.endswith("preview")
               else instrument_routes.import_instrument_nav_history_file)
    with pytest.raises(ValueError) as caught:
        execute(handler, {"instrument_id": "contract-equity", "payload": payload})
    assert str(caught.value) == (
        "NAV history import is only supported for public or private fund instruments; "
        '"contract-equity" is equity.'
    )


def test_platform_quote_policy_rejects_removed_bond_quote_basis() -> None:
    with pytest.raises(ValidationError, match="clean_price"):
        contracts.StudioQuoteSelectionPolicyUpdateRequest.model_validate(
            {
                "quote_selection_policy": {
                    "trading": ["clean_price"],
                    "valuation": ["close"],
                    "total_return": ["adjusted_close"],
                    "chart": ["adjusted_close"],
                    "reference": ["close"],
                }
            }
        )


def test_market_data_write_cli_requires_explicit_observation_status() -> None:
    with pytest.raises(ValidationError, match="status"):
        execute(instrument_routes.upsert_instrument_market_data, {
            "instrument_id": "contract-equity",
            "payload": {"metric_family": "price", "quote_basis": "close",
                        "as_of_date": "2026-07-15", "value": "100", "currency": "USD"},
        })

def test_instrument_store_wrapper_delegates_contract_derivation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = object()
    captured: dict[str, object] = {}

    monkeypatch.setattr(store_service, "get_session_factory", lambda: session_factory)

    def fake_upsert_market_data(*args: object, **kwargs: object) -> dict[str, object]:
        captured["args"] = args
        captured.update(kwargs)
        return _instrument_record()

    monkeypatch.setattr(
        store_service.shared_store,
        "upsert_market_data",
        fake_upsert_market_data,
    )

    store_service.upsert_market_data(
        instrument_id="contract-equity",
        metric_family="price",
        quote_basis="close",
        as_of_date=date(2026, 7, 15),
        value="98.5",
        currency="USD",
        provider="pytest",
        status="complete",
    )

    assert captured["args"] == (session_factory,)
    assert "price_unit" not in captured
    assert "price_scale" not in captured
