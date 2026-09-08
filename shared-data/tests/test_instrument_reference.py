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


def test_completed_empty_financial_coverage_is_projected_without_failed_refresh(monkeypatch):
    monkeypatch.setattr(instrument_reference, "list_instruments", lambda **kwargs: [{"instrument_id": "6082-hk", "instrument_type": "equity"}])
    monkeypatch.setattr(instrument_reference, "refresh_instrument_reference_data", lambda _: {
        "provider": "fmp", "section_errors": {},
        "source": {"section_coverage": {"financials": {"status": "unavailable", "reason": "供应商已完成查询，但未提供年度利润表"}}},
    })
    result = instrument_reference.refresh_reference_data_batch()
    assert result["refreshed_count"] == 1
    assert result["results"][0]["status"] == "refreshed"
    assert "未提供年度利润表" in result["results"][0]["message"]


@pytest.mark.parametrize("kind,symbol", [("equity", "AAPL"), ("etf", "510300.SS"), ("index", "^GSPC")])
def test_fmp_reference_reads_shared_versions_without_refreshing_the_clock(monkeypatch, kind, symbol):
    monkeypatch.setattr(instrument_reference, "get_instrument", lambda _: _instrument(kind, source_profile="fmp", identifier=symbol))
    monkeypatch.setattr(instrument_reference.MarketSettings, "from_environment", lambda: "settings")
    calls = []
    def read(settings, provider_symbol, instrument_type):
        calls.append((settings, provider_symbol, instrument_type))
        return {"sections": {"saved": [{"value": 10}]}, "section_errors": {}, "observed_at": "2026-09-01T08:00:00Z",
                "source": {"storage": "studio_market", "section_sources": {"saved": [{"source_id": "numeric:batch:0"}]}}}
    monkeypatch.setattr(instrument_reference, "read_reference_data", read)
    result = instrument_reference.get_instrument_reference_data("test-" + kind)
    assert calls == [("settings", symbol, kind)]
    assert result["provider"] == "fmp" and result["provider_symbol"] == symbol
    assert result["sections"] == {"saved": [{"value": 10}]}
    assert result["fetched_at"] == "2026-09-01T08:00:00Z"
    assert result["source"]["storage"] == "studio_market"


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
