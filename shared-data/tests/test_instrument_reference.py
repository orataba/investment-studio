from __future__ import annotations

from copy import deepcopy
from datetime import datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from investment_studio_instrument_core.db_models import Instrument, InstrumentReferenceObservation, InstrumentReferenceSnapshot, InstrumentRegistryBase

from studio_data.services import instrument_reference


@pytest.mark.parametrize("instrument_type,section", [("etf", "holdings"), ("equity", "financials")])
def test_each_collection_is_retained_without_overwriting_history_and_commit_is_atomic(monkeypatch, instrument_type, section):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    InstrumentRegistryBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        session.add(Instrument(instrument_id="test", instrument_name="Reference test", instrument_type=instrument_type,
                               currency="USD", exchange_code="XNYS", quote_selection_policy_json={}))
        session.commit()
    monkeypatch.setattr(instrument_reference, "get_session_factory", lambda: factory)
    record = {"instrument_id": "test", "instrument_type": instrument_type, "provider": "fmp", "provider_symbol": "TEST",
              "fetched_at": "2026-09-05T08:00:00Z", "source": {"provider_updated_at": "2026-09-04"},
              "sections": {section: [{"value": 100, "date": "2026-06-30", "collected_at": "2026-09-05T07:59:00Z"}]},
              "section_errors": {}}
    first = deepcopy(record)
    monkeypatch.setattr(instrument_reference, "get_instrument_reference_data", lambda _: deepcopy(record))
    instrument_reference.refresh_instrument_reference_data("test")
    record["fetched_at"] = "2026-09-06T08:00:00Z"
    record["sections"][section][0].update(value=110, collected_at="2026-09-06T07:59:00Z")
    second = deepcopy(record)
    instrument_reference.refresh_instrument_reference_data("test")
    with factory() as session:
        observations = list(session.scalars(select(InstrumentReferenceObservation).order_by(InstrumentReferenceObservation.collected_at)))
        assert [row.value_json for row in observations] == [first, second]
        assert observations[0].collected_at == datetime(2026, 9, 5, 8)
        assert session.get(InstrumentReferenceSnapshot, "test").value_json == second
        previous_id = observations[0].observation_id
    # If retaining an observation fails, its latest view must not advance either.
    monkeypatch.setattr(instrument_reference, "uuid4", lambda: previous_id)
    record["fetched_at"] = "2026-09-07T08:00:00Z"
    record["sections"][section][0]["value"] = 999
    with pytest.raises(IntegrityError):
        instrument_reference.refresh_instrument_reference_data("test")
    with factory() as session:
        assert session.get(InstrumentReferenceSnapshot, "test").value_json == second
        assert [row.value_json for row in session.scalars(select(InstrumentReferenceObservation).order_by(InstrumentReferenceObservation.collected_at))] == [first, second]


def _instrument(
    instrument_type: str,
    *,
    source_profile: str,
    identifier: str,
) -> dict[str, object]:
    prefix = "fmp:" if source_profile == "fmp" else "tushare:"
    return {
        "instrument_id": f"test-{instrument_type}",
        "instrument_type": instrument_type,
        "source_settings": {
            "source_mode": "api",
            "source_location": source_profile,
            "source_api_profile": source_profile,
            "expected_frequency": "daily",
        },
        "refresh_status": {"status": "refreshed", "message": "ok"},
        "identifiers": [
            {
                "identifier_type": "provider_symbol",
                "identifier_value": f"{prefix}{identifier}",
                "is_primary": False,
            }
        ],
    }


class FakeFmpClient:
    def profile(self, symbol: str) -> dict[str, object]:
        return {"symbol": symbol, "companyName": "Apple Inc."}

    def income_statements(self, symbol: str, *, limit: int) -> list[dict[str, object]]:
        return [{"symbol": symbol, "revenue": 10, "limit": limit}]

    def key_metrics(self, symbol: str, *, limit: int) -> list[dict[str, object]]:
        return [{"symbol": symbol, "returnOnEquity": 0.5, "limit": limit}]

    def financial_ratios(self, symbol: str, *, limit: int) -> list[dict[str, object]]:
        return [{"symbol": symbol, "priceToEarningsRatio": 20, "limit": limit}]

    def dividends(self, symbol: str, *, limit: int) -> list[dict[str, object]]:
        return [{"symbol": symbol, "dividend": 0.25, "limit": limit}]

    def splits(self, symbol: str, *, limit: int) -> list[dict[str, object]]:
        return [{"symbol": symbol, "numerator": 4, "denominator": 1, "limit": limit}]

    def index_info(self, symbol: str) -> dict[str, object]:
        return {"symbol": symbol, "name": "S&P 500", "exchange": "SNP", "currency": "USD"}

    def fund_info(self, symbol: str) -> dict[str, object]:
        return {"symbol": symbol, "name": "CSI 300 ETF"}

    def fund_holdings(self, symbol: str) -> list[dict[str, object]]:
        return [{"symbol": "600519.SS", "weightPercentage": 5.2}]

    def fund_sector_weights(self, symbol: str) -> list[dict[str, object]]:
        return [{"sector": "Consumer Defensive", "weightPercentage": 12.3}]

    def fund_country_weights(self, symbol: str) -> list[dict[str, object]]:
        return [{"country": "China", "weightPercentage": 100}]


def test_fmp_equity_reference_keeps_sections_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        instrument_reference,
        "get_instrument",
        lambda _instrument_id: _instrument(
            "equity",
            source_profile="fmp",
            identifier="AAPL",
        ),
    )

    result = instrument_reference.get_instrument_reference_data(
        "test-equity",
        client=FakeFmpClient(),  # type: ignore[arg-type]
    )

    assert result is not None
    assert result["provider"] == "fmp"
    assert result["provider_symbol"] == "AAPL"
    assert set(result["sections"]) == {
        "profile",
        "financials",
        "key_metrics",
        "ratios",
        "dividends",
        "splits",
    }
    assert result["section_errors"] == {}


def test_fmp_etf_reference_uses_fmp_fund_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        instrument_reference,
        "get_instrument",
        lambda _instrument_id: _instrument(
            "etf",
            source_profile="fmp",
            identifier="510300.SS",
        ),
    )

    result = instrument_reference.get_instrument_reference_data(
        "test-etf",
        client=FakeFmpClient(),  # type: ignore[arg-type]
    )

    assert result is not None
    assert result["provider"] == "fmp"
    assert result["provider_symbol"] == "510300.SS"
    assert set(result["sections"]) == {
        "fund_info",
        "holdings",
        "sector_weights",
        "country_weights",
    }
    assert result["section_errors"] == {}


def test_tushare_etf_reference_uses_fund_profile_and_holdings_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        instrument_reference,
        "get_instrument",
        lambda _instrument_id: _instrument(
            "etf",
            source_profile="tushare",
            identifier="510300.SH",
        ),
    )

    def fake_rows(**kwargs: object) -> list[dict[str, object]]:
        calls.append(str(kwargs["api_name"]))
        return [
            {
                "ts_code": "510300.SH",
                "end_date": "20260630",
                "symbol": "600519.SH",
                "stk_mkv_ratio": "5.2",
            }
        ]

    monkeypatch.setattr(instrument_reference, "_datahub_rows", fake_rows)

    result = instrument_reference.get_instrument_reference_data("test-etf")

    assert result is not None
    assert result["provider"] == "datahub:tushare"
    assert calls == ["fund_basic", "fund_portfolio"]
    assert set(result["sections"]) == {"fund_info", "holdings"}
    assert result["section_errors"] == {}


def test_tushare_index_reference_uses_index_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        instrument_reference,
        "get_instrument",
        lambda _instrument_id: _instrument(
            "index",
            source_profile="tushare",
            identifier="000300.SH",
        ),
    )

    def fake_rows(**kwargs: object) -> list[dict[str, object]]:
        api_name = str(kwargs["api_name"])
        calls.append(api_name)
        return [{"ts_code": "000300.SH", "name": "沪深300"}]

    monkeypatch.setattr(instrument_reference, "_datahub_rows", fake_rows)

    result = instrument_reference.get_instrument_reference_data("test-index")

    assert result is not None
    assert calls == ["index_basic"]
    assert result["sections"] == {"index_info": {"ts_code": "000300.SH", "name": "沪深300"}}
    assert result["section_errors"] == {}


def test_fmp_index_reference_exposes_exact_index_catalog_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        instrument_reference,
        "get_instrument",
        lambda _instrument_id: _instrument(
            "index",
            source_profile="fmp",
            identifier="^GSPC",
        ),
    )

    result = instrument_reference.get_instrument_reference_data(
        "test-index",
        client=FakeFmpClient(),  # type: ignore[arg-type]
    )

    assert result is not None
    assert result["provider"] == "fmp"
    assert result["sections"]["index_info"] == {
        "symbol": "^GSPC",
        "name": "S&P 500",
        "exchange": "SNP",
        "currency": "USD",
    }
    assert result["section_errors"] == {}


def test_private_fund_reference_does_not_substitute_public_market_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        instrument_reference,
        "get_instrument",
        lambda _instrument_id: {
            "instrument_id": "private-fund",
            "instrument_type": "private_fund",
            "source_settings": {
                "source_mode": "email",
                "source_location": "IMAP",
            },
            "refresh_status": {},
            "identifiers": [],
        },
    )

    result = instrument_reference.get_instrument_reference_data("private-fund")

    assert result is not None
    assert result["provider"] == "email/manual"
    assert result["sections"] == {}
    assert result["section_errors"] == {}
