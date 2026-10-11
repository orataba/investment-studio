from copy import deepcopy
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import sessionmaker

from investment_studio_instrument_core.db_models import Instrument, InstrumentMarketData, InstrumentRegistryBase
from studio_data.services import listed_close_acceptance as acceptance
from studio_market.numeric.providers.hkex import daily_quotations_url
from studio_market.numeric.schema import batches, current, datasets, metadata


CUTOFF = datetime.fromisoformat("2026-10-08T16:30:00+08:00")
PROVIDER = "fmp:historical-price-eod:non-split-adjusted"
RESULT = {"instrument_id": "sample", "instrument_type": "etf", "source_mode": "api",
          "source_api_profile": "fmp", "status": "no_new_data"}


def quote(day, value="100", **changes):
    return {"as_of_date": day, "value": value, "currency": "HKD", "provider": PROVIDER,
            "price_unit": "per_unit", "price_scale": 1, **changes}


def instrument(*quotes, **changes):
    return {"instrument_id": "sample", "instrument_type": "etf", "currency": "HKD", "exchange_code": "XHKG",
            "source_settings": {"source_mode": "api", "source_api_profile": "fmp", "source_price_multiplier": "1"},
            "exchange_tickers": ["2822.HK"], "close": max(quotes, key=lambda row: row["as_of_date"]) if quotes else None,
            "window_closes": list(quotes), **changes}


def evidence(day, close="100", **changes):
    return {"symbol": "2822.HK", "date": day, "official_close": close, "currency": "HKD",
            "session_status": "no_trade", "source": "hkex", "source_id": f"numeric:report-{day}:1",
            "raw_ref": "numeric/raw/hkex/sample", "source_url": daily_quotations_url(date.fromisoformat(day)),
            **changes}


def verify(monkeypatch, record, *, cutoff=CUTOFF, baseline=None, result=RESULT):
    def read(ids, **kwargs):
        assert ids == ["sample"]
        return {"sample": record}
    monkeypatch.setattr(acceptance, "_read_listed_close_inputs", read)
    return acceptance.verify_listed_close_coverage([result], cutoff=cutoff,
                                                  baseline={"sample": baseline})


@pytest.mark.parametrize("clock,latest,expected,passed", [
    ("2026-10-08T16:09:00+08:00", "2026-10-07", "2026-10-07", True),
    ("2026-10-08T16:10:00+08:00", "2026-10-07", "2026-10-08", False),
    ("2026-10-08T16:30:00+08:00", "2026-10-08", "2026-10-08", True),
    ("2026-02-16T12:30:00+08:00", "2026-02-13", "2026-02-16", False),
    ("2023-09-08T16:30:00+08:00", "2023-09-07", "2023-09-07", True),
])
def test_frozen_request_clock_observes_actual_hk_close(monkeypatch, clock, latest, expected, passed):
    monkeypatch.setattr(acceptance, "_read_hkex_evidence", lambda *args: [])
    result = verify(monkeypatch, instrument(quote(latest)), cutoff=datetime.fromisoformat(clock))
    assert (result["status"] == "passed") is passed
    assert result["results"][0]["expected_close_date"] == expected


@pytest.mark.parametrize("baseline,expected_reason", [
    (None, "close_after_batch_cutoff"), ("2026-10-09", "baseline_after_batch_cutoff"),
])
def test_later_quote_cannot_empty_an_unverified_window(monkeypatch, baseline, expected_reason):
    result = verify(monkeypatch, instrument(quote("2026-10-09")), baseline=baseline)
    assert result["status"] == "failed"
    assert result["results"][0]["reason"] == expected_reason


def test_after_cutoff_latest_still_requires_all_baseline_sessions(monkeypatch):
    monkeypatch.setattr(acceptance, "_read_hkex_evidence", lambda *args: [])
    record = instrument(quote("2026-10-05"), quote("2026-10-09"))
    result = verify(monkeypatch, record, baseline="2026-10-05")
    assert result["status"] == "failed"
    assert result["results"][0]["missing_session_dates"] == ["2026-10-06", "2026-10-07", "2026-10-08"]


def test_no_trade_evidence_uses_each_missing_days_preceding_real_close_and_source_units(monkeypatch):
    rows = [evidence("2026-10-06", "100"), evidence("2026-10-08", "105")]
    monkeypatch.setattr(acceptance, "_read_hkex_evidence", lambda *args: rows)
    record = instrument(quote("2026-10-05", "200"), quote("2026-10-07", "210"))
    record["source_settings"]["source_price_multiplier"] = "2"
    result = verify(monkeypatch, record, baseline="2026-10-05")
    assert result["status"] == "passed"
    checked = result["results"][0]
    assert checked["reason"] == "confirmed_no_trade"
    assert checked["latest_close_date"] == "2026-10-07"
    assert checked["evidence_source_ids"] == [row["source_id"] for row in rows]


@pytest.mark.parametrize("change", [
    {"session_status": "suspended"}, {"session_status": "traded"}, {"currency": "USD"},
    {"official_close": "101"}, {"source_id": ""}, {"raw_ref": "unverified"},
    {"source_url": "https://example.invalid"}, {"date": "2026-10-07"},
])
def test_missing_day_is_not_waived_by_inadequate_exchange_evidence(monkeypatch, change):
    rows = [evidence("2026-10-08", **change)]
    monkeypatch.setattr(acceptance, "_read_hkex_evidence", lambda *args: rows)
    result = verify(monkeypatch, instrument(quote("2026-10-07")), baseline="2026-10-07")
    assert result["status"] == "failed"
    assert result["results"][0]["reason"] == "closed_session_price_missing"


@pytest.mark.parametrize("changes", [
    {"value": "NaN"}, {"value": "0"}, {"currency": "USD"}, {"price_scale": "NaN"},
    {"provider": "futu:history-kline:unadjusted"}, {"provider": "fmp:historical-price-eod:dividend-adjusted"},
])
def test_only_valid_raw_close_of_configured_source_can_satisfy_acceptance(monkeypatch, changes):
    result = verify(monkeypatch, instrument(quote("2026-10-08", **changes)))
    assert result["status"] == "failed"
    assert result["results"][0]["reason"] == "raw_close_contract_mismatch"


def test_partial_ohlc_or_return_history_does_not_block_complete_raw_window(monkeypatch):
    record = instrument(*(quote(f"2026-10-{day:02d}") for day in range(5, 9)))
    result = verify(monkeypatch, record, baseline="2026-10-05", result={**RESULT, "status": "partial"})
    assert result["status"] == "passed"


def test_first_capture_does_not_invent_a_history_start(monkeypatch):
    result = verify(monkeypatch, instrument(quote("2026-10-08")))
    assert result["status"] == "passed"
    assert result["results"][0]["baseline_close_date"] is None


def test_only_this_runs_nonfailed_listed_api_results_are_checked(monkeypatch):
    rows = [{**RESULT, "status": "failed"}, {**RESULT, "instrument_id": "nav", "instrument_type": "private_fund"},
            {**RESULT, "instrument_id": "manual", "source_mode": "manual"}]
    monkeypatch.setattr(acceptance, "_read_listed_close_inputs", lambda *a, **k: pytest.fail("No target"))
    assert acceptance.verify_listed_close_coverage(rows, cutoff=CUTOFF, baseline={})["status"] == "not_required"


def test_read_failure_is_explicit_and_does_not_leak_exception_payload(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("postgresql://secret:password@private-host/db")
    monkeypatch.setattr(acceptance, "_read_listed_close_inputs", broken)
    result = acceptance.verify_listed_close_coverage([RESULT], cutoff=CUTOFF, baseline={})
    assert result["status"] == "failed"
    assert result["results"][0]["error_type"] == "RuntimeError"
    assert "password" not in str(result)


def test_readers_use_canonical_complete_closes_and_only_published_exchange_evidence(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:", execution_options={"schema_translate_map": {"market_data": None}})
    InstrumentRegistryBase.metadata.create_all(engine)
    metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(acceptance, "get_session_factory", lambda: factory)
    with engine.begin() as connection:
        connection.execute(insert(Instrument).values(
            instrument_id="sample", instrument_name="sample", instrument_type="etf", currency="HKD", exchange_code="XHKG",
            source_settings_json={"source_mode": "api", "source_api_profile": "fmp"}, lifecycle_state_json={"status": "active"},
            quote_selection_policy_json={"total_return": ["adjusted_close"]},
        ))
        for day, status in [(5, "complete"), (7, "complete"), (8, "partial")]:
            connection.execute(insert(InstrumentMarketData).values(
                instrument_id="sample", metric_family="price", quote_basis="close", as_of_date=date(2026, 10, day),
                value="100", currency="HKD", price_unit="per_unit", price_scale=1, provider=PROVIDER, status=status,
            ))
        connection.execute(insert(datasets).values(name="hkex_security_sessions", description="test"))
        for day, status in [(6, "ready"), (8, "failed")]:
            batch_id = f"report-{day}"
            connection.execute(insert(batches).values(id=batch_id, dataset="hkex_security_sessions", source="hkex", status=status,
                                                     started_at=datetime(2026, 10, day, 9, tzinfo=UTC), row_count=1, details={}))
            connection.execute(insert(current).values(dataset="hkex_security_sessions", key=str(day), symbol="2822.HK",
                observed_at=datetime(2026, 10, day, 9, tzinfo=UTC), batch_id=batch_id, row_index=1,
                payload=evidence(f"2026-10-{day:02d}")))
    assert acceptance.listed_close_baseline() == {"sample": "2026-10-07"}
    loaded = acceptance._read_listed_close_inputs(["sample"], baseline={"sample": "2026-10-05"})["sample"]
    assert loaded["close"]["as_of_date"] == date(2026, 10, 7)
    assert {row["as_of_date"] for row in loaded["window_closes"]} == {date(2026, 10, 5), date(2026, 10, 7)}
    retained = acceptance._read_hkex_evidence("2822.HK", date(2026, 10, 5), date(2026, 10, 8))
    assert [row["date"] for row in retained] == ["2026-10-06"]
    engine.dispose()
