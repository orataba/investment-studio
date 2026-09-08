from datetime import UTC, date, datetime, timedelta
from math import exp, sqrt
from types import SimpleNamespace

import pytest
from studio_market.config import MarketSettings
from studio_market.numeric import NumericStore

from watchlist_app.services import research_metrics as metrics


@pytest.fixture
def store(tmp_path):
    result = NumericStore(MarketSettings(f"sqlite:///{tmp_path / 'market.db'}", tmp_path / "data"))
    result.create_schema_for_testing()
    yield result
    result.close()


def stamp(day):
    return datetime(2026, 9, day, 12, tzinfo=UTC)


def macro(symbol, day, value, observed=1, **extra):
    return {"series_id": symbol, "date": date(2026, 8, day), "value": value,
            "unit": "percent", "collected_at": stamp(observed), **extra}


def test_numeric_series_keeps_original_revision_and_known_clock(store):
    store.ingest("macro_series", [[macro("Y10", 1, 4), macro("Y10", 2, 4.2)]], source="fixture")
    store.ingest("macro_series", [[macro("Y10", 1, 4.1, observed=3)]], source="fixture")
    evidence = metrics.research_numeric_data(action="series", dataset="macro_series", series_ids=["Y10"],
        start="2026-08-01", end="2026-08-02", as_of=stamp(2), store=store)
    row = evidence["data"]["series"][0]
    assert row["first"]["value"] == 4
    assert row["change"] == pytest.approx(0.2)
    assert row["change_unit"] == "percentage_points"
    assert row["relative_change_pct"] is None
    assert row["frequency"] == ["not_supplied"]
    original = store.read_source(row["first"]["source_id"])
    assert original["value"] == 4
    assert original["batch_id"] == row["first"]["batch_id"]
    assert evidence["source_type"] == "computed_metric"
    assert all(item["observed_at"] <= stamp(2).isoformat() for item in evidence["sources"])
    revised = metrics.research_numeric_data(action="series", dataset="macro_series", series_ids=["Y10"],
        start="2026-08-01", end="2026-08-02", as_of=stamp(4), store=store)
    assert revised["data"]["series"][0]["first"]["value"] == 4.1


def test_catalogue_reports_actual_coverage_and_unavailable_series(store):
    store.ingest("macro_series", [[macro("Y10", 1, 4)]], source="fixture")
    old = metrics.research_numeric_data(action="catalogue", dataset="macro_series",
                                       as_of=datetime(2026, 8, 31, tzinfo=UTC), store=store)
    assert old["data"]["total"] == 0
    current = metrics.research_numeric_data(action="catalogue", dataset="macro_series", as_of=stamp(2), store=store)
    assert [item["series_id"] for item in current["data"]["series"]] == ["Y10"]
    missing = metrics.research_numeric_data(action="series", dataset="macro_series", series_ids=["JOBS"],
        start="2026-08-01", end="2026-08-02", as_of=stamp(2), store=store)
    assert missing["data"]["series"][0]["status"] == "unavailable"
    assert missing["sources"] == []


def test_change_comparison_uses_common_dates_without_filling(store):
    store.ingest("macro_series", [[
        macro("Y10", 1, 4), macro("Y10", 2, 5), macro("Y10", 3, 6),
        macro("Y2", 2, 3), macro("Y2", 3, 3.2),
    ]], source="fixture")
    result = metrics.research_numeric_data(action="compare", dataset="macro_series", series_ids=["Y10", "Y2"],
        start="2026-08-01", end="2026-08-03", as_of=stamp(2), store=store)["data"]
    assert result["common_observation_count"] == 2
    assert result["aligned_changes"][0]["start"]["date"] == "2026-08-02"
    assert result["aligned_changes"][0]["change"] == 1
    assert result["aligned_changes"][1]["change"] == pytest.approx(0.2)


def test_known_but_not_released_rows_and_changed_units_are_not_mixed(store):
    store.ingest("macro_series", [[macro("Y10", 1, 4), macro("Y10", 2, 0.05, unit="fraction"),
                                   macro("Y10", 3, 7, available_at=stamp(4))]], source="fixture")
    result = metrics.research_numeric_data(action="series", dataset="macro_series", series_ids=["Y10"],
        start="2026-08-01", end="2026-08-03", as_of=stamp(2), store=store)["data"]["series"][0]
    assert result["latest"]["date"] == "2026-08-02"
    assert result["comparable_units"] is False
    assert result["change"] is None


def price_series(monkeypatch, count=101):
    # An explicit exchange-session fixture separates weekend closures from missing observations.
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(220)]
    days = [day for day in days if day.weekday() < 5][:count]
    monkeypatch.setattr(metrics, "_market_calendar_sessions", lambda calendar, start, end: tuple(d for d in days if start <= d <= end))
    returns = [0.005 if i % 3 else -0.01 for i in range(count - 1)]
    returns[-1] = 0.06
    points = [{"date": days[0].isoformat(), "value": 100.0}]
    for day, value in zip(days[1:], returns):
        points.append({"date": day.isoformat(), "value": points[-1]["value"] * exp(value)})
    return {"points": points, "currency": "USD", "metadata": {"quote_basis": "adjusted_close", "return_kind": "total_return",
            "return_series_status": "ready", "return_segment_breaks": []}}, returns


def test_ewma_matches_independent_weighted_variance_and_reports_changes(monkeypatch):
    series, returns = price_series(monkeypatch)
    result = metrics.ewma_price_evidence(series, instrument_type="etf", calendar="XNYS", as_of=stamp(1))
    weights = [2 ** (-(len(returns) - 1 - i) / 21) for i in range(len(returns))]
    mean = sum(w * r for w, r in zip(weights, returns)) / sum(weights)
    expected = sqrt(sum(w * (r - mean) ** 2 for w, r in zip(weights, returns)) / sum(weights) * 252) * 100
    assert result["status"] == "available"
    assert result["current"]["volatility_pct"] == pytest.approx(expected)
    assert result["change_pp"] > 0
    assert result["five_session_change_pp"] > 0
    assert result["historical_reference"]["end_date"] < result["current"]["date"]
    assert result["methodology"]["half_life_sessions"] == 21


def test_missing_session_restarts_sample_and_does_not_create_daily_return(monkeypatch):
    series, _ = price_series(monkeypatch)
    del series["points"][-10]
    result = metrics.ewma_price_evidence(series, instrument_type="equity", calendar="XNYS", as_of=stamp(1))
    assert result["status"] == "unavailable"
    assert len(result["input_points"]) == 9
    assert any("缺失交易日" in item for item in result["limitations"])


def test_long_price_history_outside_calendar_coverage_does_not_disable_recent_ewma(monkeypatch):
    days = [date(2023, 1, 1) + timedelta(days=i) for i in range(1200)]
    days = [day for day in days if day.weekday() < 5][-506:]
    monkeypatch.setattr(metrics, "_market_calendar_sessions",
        lambda calendar, start, end: tuple(day for day in days if start <= day <= end))
    series = {"metadata": {"quote_basis": "adjusted_close", "return_series_status": "ready"},
        "points": [{"date": "2000-01-03", "value": 50},
                   *[{"date": day.isoformat(), "value": 100 + i % 7} for i, day in enumerate(days)]]}
    result = metrics.ewma_price_evidence(series, instrument_type="etf", calendar="XNYS", as_of=stamp(1))
    assert result["status"] == "available"
    assert len(result["input_points"]) == 505
    assert result["current"]["returns"] == 252
    # Restricting the sample still must not bridge a real recent missing session.
    del series["points"][-10]
    missing = metrics.ewma_price_evidence(series, instrument_type="etf", calendar="XNYS", as_of=stamp(1))
    assert missing["status"] == "unavailable"
    assert any("缺失交易日" in item for item in missing["limitations"])


@pytest.mark.parametrize("kind,basis", [("private_fund", "total_return_nav"), ("public_fund", "official_nav"), ("etf", "official_nav")])
def test_low_frequency_or_nav_is_not_annualized_as_exchange_prices(monkeypatch, kind, basis):
    series, _ = price_series(monkeypatch)
    series["metadata"]["quote_basis"] = basis
    result = metrics.ewma_price_evidence(series, instrument_type=kind, calendar="XNYS", as_of=stamp(1))
    assert result["status"] == "unavailable"
    assert result["current"] is None


def test_calendar_and_historical_cutoff_are_enforced(monkeypatch):
    series, _ = price_series(monkeypatch)
    early = metrics.ewma_price_evidence(series, instrument_type="index", calendar="XNYS", as_of=datetime(2026, 2, 1, tzinfo=UTC))
    assert early["status"] == "unavailable"
    monkeypatch.setattr(metrics, "_market_calendar_sessions", lambda *args: None)
    assert metrics.ewma_price_evidence(series, instrument_type="index", calendar="UNKNOWN", as_of=stamp(1))["current"] is None
    with pytest.raises(ValueError, match="timezone"):
        metrics.ewma_price_evidence(series, instrument_type="index", calendar="XNYS", as_of=datetime(2026, 2, 1))


def test_price_tool_does_not_read_later_recalculated_snapshot(monkeypatch):
    from investment_studio_instrument_core.db_models import Instrument
    series, _ = price_series(monkeypatch)
    instrument = SimpleNamespace(instrument_type="etf", exchange_code="XNYS", source_settings_json={})
    chart = SimpleNamespace(payload_json={"research_returns": series}, last_recalculated_at=stamp(3),
                            source_cutoff_at=stamp(1), materialization_version="test", data_freshness_status="fresh")
    session = SimpleNamespace(get=lambda model, iid: instrument if model is Instrument else chart)
    early = metrics.instrument_price_risk(session, "spy", as_of=stamp(2))
    assert early["data"]["status"] == "unavailable"
    latest = metrics.instrument_price_risk(session, "spy", as_of=stamp(4))
    assert latest["data"]["status"] == "available"
    assert latest["input_snapshot"]["known_at"] == stamp(3).isoformat()
    assert latest["data"]["input_points"] == series["points"]
