from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
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
                               currency="USD", exchange_code="XNYS", quote_selection_policy_json={
                                   "trading": ["close"], "valuation": ["close"], "total_return": ["adjusted_close"],
                                   "chart": ["close"], "reference": ["close"]}))
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


@pytest.mark.parametrize("has_previous", [False, True])
def test_incomplete_datahub_reference_preserves_snapshot_clock_and_observations(monkeypatch, has_previous):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    InstrumentRegistryBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        session.add(Instrument(instrument_id="test", instrument_name="Fund", instrument_type="public_fund",
                               currency="CNY", exchange_code=None, quote_selection_policy_json={}))
        session.commit()
    monkeypatch.setattr(instrument_reference, "get_session_factory", lambda: factory)
    record = {
        "instrument_id": "test", "instrument_type": "public_fund", "provider": "datahub:tushare",
        "provider_symbol": "018654.OF", "fetched_at": "2026-09-05T08:00:00Z", "source": {},
        "sections": {"fund_info": {"name": "Fund"}, "holdings": [{"symbol": "600519.SH"}]},
        "section_errors": {},
    }
    saved = deepcopy(record)
    monkeypatch.setattr(instrument_reference, "get_instrument_reference_data", lambda _: deepcopy(record))
    if has_previous:
        instrument_reference.refresh_instrument_reference_data("test")
    record["fetched_at"] = "2026-09-27T08:00:00Z"
    record["sections"] = {"fund_info": {"name": "New profile"}}
    record["section_errors"] = {"holdings": "Provider row limit; complete holdings cannot be confirmed"}
    monkeypatch.setattr(instrument_reference, "list_instruments", lambda **_: [{"instrument_id": "test", "instrument_type": "public_fund"}])
    outcome = instrument_reference.refresh_reference_data_batch()
    assert outcome["refreshed_count"] == 0
    assert outcome["results"][0]["status"] == "failed"
    assert "complete holdings cannot be confirmed" in outcome["results"][0]["message"]
    with factory() as session:
        snapshot = session.get(InstrumentReferenceSnapshot, "test")
        observations = list(session.scalars(select(InstrumentReferenceObservation)))
        assert (snapshot.value_json if snapshot else None) == (saved if has_previous else None)
        assert [row.value_json for row in observations] == ([saved] if has_previous else [])


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


def test_reference_batch_shares_reader_and_releases_it_after_independent_failure(monkeypatch):
    instruments = [_instrument("etf", source_profile="fmp", identifier=symbol)
                   for symbol in ("SPY", "SPMO", "QQQ")]
    for item, symbol in zip(instruments, ("SPY", "SPMO", "QQQ")):
        item["instrument_id"] = symbol
    # Explicit Tushare routing wins even if an old FMP identifier is retained.
    tushare = _instrument("etf", source_profile="tushare", identifier="510300.SH")
    tushare["identifiers"].append({"identifier_type": "provider_symbol", "identifier_value": "fmp:510300.SS"})
    instruments.insert(0, tushare)
    monkeypatch.setattr(instrument_reference, "list_instruments", lambda **_: instruments)
    monkeypatch.setattr(instrument_reference.MarketSettings, "from_environment", lambda: "settings")
    events, stores = [], []
    class Store:
        def __init__(self, settings):
            assert settings == "settings"
            stores.append(self)
        @contextmanager
        def query_session(self):
            events.append("enter")
            try:
                yield self
            finally:
                events.append("exit")
        def close(self):
            events.append("close")
    monkeypatch.setattr(instrument_reference, "NumericStore", Store)
    def refresh(instrument_id, *, public_store=None):
        if instrument_id == tushare["instrument_id"]:
            assert public_store is None and stores == []
        else:
            assert public_store is stores[0] and events == ["enter"]
        if instrument_id == "SPMO":
            raise ValueError("Incomplete source snapshot")
        return {"provider": "fmp", "section_errors": {}, "source": {}}
    monkeypatch.setattr(instrument_reference, "refresh_instrument_reference_data", refresh)
    result = instrument_reference.refresh_reference_data_batch()
    assert [row["status"] for row in result["results"]] == ["refreshed", "refreshed", "failed", "refreshed"]
    assert result["refreshed_count"] == 3
    assert len(stores) == 1 and events == ["enter", "exit", "close"]


@pytest.mark.parametrize("kind,symbol", [("equity", "AAPL"), ("etf", "510300.SS"), ("index", "^GSPC")])
def test_fmp_reference_reads_shared_versions_without_refreshing_the_clock(monkeypatch, kind, symbol):
    monkeypatch.setattr(instrument_reference, "get_instrument", lambda _: _instrument(kind, source_profile="fmp", identifier=symbol))
    monkeypatch.setattr(instrument_reference.MarketSettings, "from_environment", lambda: "settings")
    calls = []
    def read(settings, provider_symbol, instrument_type, *, store=None):
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
    assert "field_semantics" not in result["source"]


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


@pytest.mark.parametrize("kind", ["public_fund", "etf"])
def test_tushare_holdings_retains_field_contract_and_original_selection(monkeypatch, kind):
    monkeypatch.setattr(instrument_reference, "get_instrument", lambda _: _instrument(
        kind, source_profile="tushare", identifier="000940.OF"))
    rows = [{"ts_code": "000940.OF", "symbol": f"{index // 2:06}.SH", "ann_date": "20260831",
        "end_date": "20260630", "stk_mkv_ratio": str(103 - index), "mkv": "12345.67", "amount": "890.0"}
        for index in range(103)]
    rows.append({**rows[0], "end_date": "20260331", "stk_mkv_ratio": "999"})
    before = deepcopy(rows)
    monkeypatch.setattr(instrument_reference, "_datahub_rows", lambda **kwargs:
        deepcopy(rows) if kwargs["api_name"] == "fund_portfolio" else [{"name": "Fund"}])
    result = instrument_reference.get_instrument_reference_data("test-" + kind)
    assert result["sections"]["holdings"] == before[:100]
    assert rows == before  # Repeated symbols, original types, amounts and clocks stay untouched.
    contract = result["source"]["field_semantics"]["holdings"]
    assert (contract["provider"], contract["api"]) == ("datahub:tushare", "fund_portfolio")
    assert contract["documentation_url"] == "https://tushare.pro/document/2?doc_id=121"
    fields = contract["fields"]
    assert fields["stk_mkv_ratio"]["unit"] == "%"
    assert fields["stk_mkv_ratio"]["denominator"] == "基金持有股票市值，不是基金资产净值（NAV）"
    assert fields["mkv"]["unit"] == "元" and fields["amount"]["unit"] == "股"
    assert fields["ann_date"]["description"] == "公告日期"
    assert fields["end_date"]["description"] == "报告期末日期"
    assert contract["selection"] == {"report_period": "最新 end_date", "order": "stk_mkv_ratio 降序",
        "row_limit": 100, "scope": "最多100条原始披露行，未去重，不保证全量持仓。"}


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
