from __future__ import annotations

import pytest
from pydantic import ValidationError

from studio_data import contracts
from studio_data.commands import instruments as instrument_routes
from studio_data.cli import execute


def _instrument_record(
    source_settings: dict[str, object],
    *,
    instrument_type: str = "equity",
) -> dict[str, object]:
    return {
        "instrument_id": "schedule-equity",
        "instrument_name": "Schedule Equity",
        "instrument_type": instrument_type,
        "currency": "CNY",
        "identifiers": [
            {
                "identifier_type": "ticker",
                "identifier_value": "600000.SH",
                "is_primary": True,
            }
        ],
        "broker_identifiers": [],
        "latest_market_data": [],
        "quote_selection_policy": {
            "trading": ["close"],
            "valuation": ["close"],
            "total_return": ["adjusted_close", "close"],
            "chart": ["adjusted_close", "close"],
            "reference": ["close"],
        },
        "coverage_state": "unavailable",
        "source_settings": source_settings,
        "refresh_status": {},
        "lifecycle_state": {},
        "market_data_updated_at": None,
        "corporate_actions": [],
    }


def test_source_schedule_contract_validates_frequency_calendar_and_release_lag() -> None:
    settings = contracts.StudioSourceSettings.model_validate(
        {
            "source_mode": "api",
            "source_api_profile": "tushare",
            "expected_frequency": "daily",
            "market_calendar": "XSHG",
            "release_lag_days": 1,
        }
    )
    assert settings.expected_frequency == "daily"
    assert settings.market_calendar == "XSHG"
    assert settings.release_lag_days == 1

    with pytest.raises(ValidationError, match="market_calendar"):
        contracts.StudioSourceSettingsUpdateRequest.model_validate(
            {"market_calendar": "   "}
        )
    with pytest.raises(ValidationError, match="release_lag_days"):
        contracts.StudioSourceSettingsUpdateRequest.model_validate(
            {"release_lag_days": -1}
        )
    with pytest.raises(ValidationError, match="expected_frequency"):
        contracts.StudioSourceSettingsUpdateRequest.model_validate(
            {"expected_frequency": "quarterly"}
        )
    with pytest.raises(ValidationError, match="expected_frequency"):
        contracts.StudioSourceSettingsUpdateRequest.model_validate(
            {"expected_frequency": "weekly"}
        )
    with pytest.raises(ValidationError, match="expected_frequency"):
        contracts.StudioSourceSettingsUpdateRequest.model_validate(
            {"expected_frequency": "monthly"}
        )


def test_source_schedule_api_forwards_and_returns_all_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_upsert_source_settings(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return _instrument_record(
            {
                "source_mode": kwargs["source_mode"],
                "source_email": kwargs["source_email"] or "",
                "source_location": kwargs["source_location"] or "Backend CLI",
                "source_api_profile": kwargs["source_api_profile"] or "",
                "source_email_rules": [],
                "expected_frequency": kwargs["expected_frequency"],
                "market_calendar": kwargs["market_calendar"],
                "release_lag_days": kwargs["release_lag_days"],
                "return_semantics": kwargs["return_semantics"],
            },
            instrument_type="index",
        )

    monkeypatch.setattr(
        instrument_routes,
        "upsert_source_settings",
        fake_upsert_source_settings,
    )

    monkeypatch.setattr(instrument_routes, "notify_market_data_downstream_refresh", lambda **kwargs: None)
    response = execute(instrument_routes.update_instrument_source_settings, {
        "instrument_id": "schedule-equity",
        "payload": {
            "source_mode": "api",
            "source_email": "",
            "source_location": "Tushare daily queue",
            "source_api_profile": "tushare",
            "expected_frequency": "daily",
            "market_calendar": "XSHG",
            "release_lag_days": 1,
            "return_semantics": "total_return",
        },
    })
    assert captured["expected_frequency"] == "daily"
    assert captured["market_calendar"] == "XSHG"
    assert captured["release_lag_days"] == 1
    assert captured["return_semantics"] == "total_return"
    assert response.model_dump(mode="json")["source_settings"] == {
        "source_mode": "api",
        "source_email": "",
        "source_location": "Tushare daily queue",
        "source_api_profile": "tushare",
        "source_email_rules": [],
        "expected_frequency": "daily",
        "market_calendar": "XSHG",
        "release_lag_days": 1,
        "return_semantics": "total_return",
    }
