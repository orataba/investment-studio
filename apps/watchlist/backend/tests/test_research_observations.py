from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from watchlist_app.services import research_observations as observations


def series(points, currency="USD", kind="total_return"):
    return {"currency": currency, "metadata": {"quote_basis": "adjusted_close", "return_kind": kind,
        "return_series_status": "ready", "return_segment_breaks": []},
        "points": [{"date": day, "value": value} for day, value in points]}


def test_relative_uses_calendar_month_and_same_actual_endpoints():
    target = series([("2026-07-30", 80), ("2026-07-31", 100), ("2026-08-21", 110),
        ("2026-08-24", 112), ("2026-08-27", 115), ("2026-08-28", 121)])
    benchmark = series([("2026-07-31", 200), ("2026-08-21", 210), ("2026-08-27", 220)])
    week, month = observations.relative_performance(target, benchmark, as_of_date=date(2026, 8, 31))
    assert month["requested_start_date"] == "2026-07-31"
    assert month["anchor_date"] == "2026-07-31"
    assert month["end_date"] == "2026-08-27"
    assert month["target_return_pct"] == pytest.approx(15)
    assert month["benchmark_return_pct"] == pytest.approx(10)
    assert month["difference_pp"] == pytest.approx(5)
    assert week["anchor_date"] == "2026-08-21"
    assert week["requested_start_date"] == "2026-08-24"


@pytest.mark.parametrize("currency,kind", [("EUR", "total_return"), (None, "total_return"), ("USD", "price_return"), ("USD", None)])
def test_relative_missing_currency_or_adjustment_never_computes_excess(currency, kind):
    points = [("2026-07-31", 100), ("2026-08-31", 110)]
    result = observations.relative_performance(series(points), series(points, currency, kind), as_of_date=date(2026, 8, 31))[1]
    assert result["status"] == "partial"
    assert result["difference_pp"] is None
    assert result["target_return_pct"] == pytest.approx(10)


def basket_fixture(monkeypatch, count=12):
    days = [date(2026, 4, 1) + timedelta(days=i) for i in range(100)]
    days = [d for d in days if d.weekday() < 5][:51]
    monkeypatch.setattr(observations, "_market_calendar_sessions", lambda calendar, start, end: tuple(d for d in days if start <= d <= end))
    holdings = [{"holding_key": f"security-{i}", "holding_symbol": f"S{i}", "holding_name": f"Synthetic {i}",
        "weight_percent": 12 - i, "snapshot_date": days[-1].isoformat(), "source_id": f"holding:{i}"} for i in range(count)]
    # Last current price 110/90, all earlier observations 100. Previous breadth
    # is 0 (price equals mean), current is 50%; weights sum to 78% for 12 names.
    members = {f"S{i}": series([(d.isoformat(), 100 if index < 50 else 110 if i % 2 == 0 else 90)
        for index, d in enumerate(days)]) for i in range(count)}
    return days, holdings, members


def test_breadth_hand_calculation_top10_and_concentration(monkeypatch):
    days, holdings, members = basket_fixture(monkeypatch)
    result = observations.basket_observations(list(reversed(holdings)), members,
        as_of_date=days[-1], total_members=12, ordinary_equity=True, calendar="TEST")
    assert [r["symbol"] for r in result["top10"]] == [f"S{i}" for i in range(10)]
    assert result["concentration_pct"] == 75
    breadth = result["breadth"]
    assert breadth["status"] == "available"
    assert breadth["valid_members"] == 12
    assert breadth["above_members"] == 6
    assert breadth["current_pct"] == 50
    assert breadth["previous_pct"] == 0
    assert breadth["change_pp"] == 50
    assert breadth["weight_coverage_pct"] == 78
    assert len(breadth["common_members"]) == 12


def test_breadth_changed_coverage_compares_only_common_members(monkeypatch):
    days, holdings, members = basket_fixture(monkeypatch)
    # S0 has enough current observations but no prior 50-observation window.
    del members["S0"]["points"][0]
    # S1 lacks a current session and must not be filled with yesterday's price.
    del members["S1"]["points"][-1]
    result = observations.basket_observations(holdings, members, as_of_date=days[-1],
        total_members=12, ordinary_equity=True, calendar="TEST")["breadth"]
    assert result["status"] == "partial"
    assert result["valid_members"] == 11
    assert result["current_pct"] == pytest.approx(6 / 11 * 100)
    assert result["comparison_members"] == 10
    assert result["change_pp"] == 50
    assert result["current_common_pct"] == 50
    assert "security-0" not in result["common_members"]


def test_top10_only_does_not_claim_full_etf_breadth(monkeypatch):
    days, holdings, members = basket_fixture(monkeypatch)
    members = {key: value for key, value in members.items() if key not in {"S10", "S11"}}
    result = observations.basket_observations(holdings, members, as_of_date=days[-1],
        total_members=12, ordinary_equity=True, calendar="TEST")
    assert result["breadth"]["status"] == "unavailable"
    assert result["breadth"]["scope"] == "top10_only"
    assert result["breadth"]["current_pct"] is None
    assert len(result["top10"]) == 10


@pytest.mark.parametrize("change", ["negative", "levered", "missing", "mixed_dates", "unknown_structure"])
def test_unverified_weights_and_structure_never_publish_ordinary_concentration(monkeypatch, change):
    days, holdings, members = basket_fixture(monkeypatch)
    if change == "negative": holdings[0]["weight_percent"] = -10
    if change == "levered": holdings[0]["weight_percent"] = 120
    if change == "missing": holdings[0]["weight_percent"] = None
    if change == "mixed_dates": holdings[0]["snapshot_date"] = days[-2].isoformat()
    result = observations.basket_observations(holdings, members, as_of_date=days[-1],
        total_members=12, ordinary_equity=change != "unknown_structure", calendar="TEST")
    assert result["concentration_pct"] is None
    if change != "mixed_dates":
        assert result["top10"]


def test_event_date_only_and_after_close_never_reuse_prior_day_move():
    target = series([("2026-09-23", 80), ("2026-09-24", 100), ("2026-09-25", 110)])
    benchmark = series([("2026-09-23", 100), ("2026-09-24", 100), ("2026-09-25", 102)])
    for timing in ("date_only", "after_close"):
        pending = observations.event_reaction(target, event_date="2026-09-24", calendar="XNYS",
            as_of=datetime(2026, 9, 25, 15, tzinfo=UTC), benchmark=benchmark, timing=timing)
        assert pending["status"] == "pending"
        assert pending["target_return_pct"] is None
        completed = observations.event_reaction(target, event_date="2026-09-24", calendar="XNYS",
            as_of=datetime(2026, 9, 25, 22, tzinfo=UTC), benchmark=benchmark, timing=timing)
        assert completed["anchor_date"] == "2026-09-24"
        assert completed["end_date"] == "2026-09-25"
        assert completed["target_return_pct"] == pytest.approx(10)
        assert completed["difference_pp"] == pytest.approx(8)


def test_before_open_and_weekend_event_use_actual_sessions():
    target = series([("2026-09-23", 80), ("2026-09-24", 100), ("2026-09-25", 110), ("2026-09-28", 121)])
    before = observations.event_reaction(target, event_date="2026-09-24", calendar="XNYS",
        as_of=datetime(2026, 9, 25, 22, tzinfo=UTC), timing="before_open")
    assert before["anchor_date"] == "2026-09-23"
    assert before["target_return_pct"] == pytest.approx(25)
    weekend = observations.event_reaction(target, event_date="2026-09-26", calendar="XNYS",
        as_of=datetime(2026, 9, 29, tzinfo=UTC))
    assert weekend["anchor_date"] == "2026-09-25"
    assert weekend["end_date"] == "2026-09-28"
    assert weekend["target_return_pct"] == pytest.approx(10)


def test_event_missing_dates_and_cross_currency_are_unavailable_or_partial():
    target = series([("2026-09-23", 80), ("2026-09-25", 110)])
    cutoff = datetime(2026, 9, 25, 22, tzinfo=UTC)
    missing = observations.event_reaction(target, event_date="2026-09-24", calendar="XNYS", as_of=cutoff)
    assert missing["status"] == "unavailable"
    assert missing["target_return_pct"] is None
    target["points"].insert(1, {"date": "2026-09-24", "value": 100})
    other = {**target, "currency": "EUR"}
    partial = observations.event_reaction(target, event_date="2026-09-24", calendar="XNYS", as_of=cutoff, benchmark=other)
    assert partial["status"] == "partial"
    assert partial["difference_pp"] is None


def test_volume_zero_is_observed_but_missing_base_is_not_zero_or_infinity():
    cutoff = datetime(2026, 9, 25, 22, tzinfo=UTC)
    rows = [{"date": "2026-09-24", "volume": 100, "source_id": "p1"},
        {"date": "2026-09-25", "volume": 150, "source_id": "p2"}]
    assert observations.volume_observation(rows, calendar="XNYS", as_of=cutoff)["change_pct"] == 50
    rows[0]["volume"] = 0
    result = observations.volume_observation(rows, calendar="XNYS", as_of=cutoff)
    assert result["status"] == "partial" and result["change_pct"] is None
    rows[0]["volume"] = None
    assert observations.volume_observation(rows, calendar="XNYS", as_of=cutoff)["status"] == "unavailable"


def test_business_comparison_ignores_new_source_id_but_preserves_sample_and_method():
    before = {"data": {"method_version": "v1", "observations": {"sample": ["a", "b"], "value": 10, "source_id": "old"}}}
    after = deepcopy(before)
    after["data"]["observations"]["source_id"] = "new"
    assert observations.observation_business_values(before) == observations.observation_business_values(after)
    after["data"]["observations"]["sample"].pop()
    assert observations.observation_business_values(before) != observations.observation_business_values(after)


def test_snapshot_clock_and_retained_figure_schema():
    from investment_studio_instrument_core.db_models import Instrument
    from watchlist_app.db.models import InstrumentChartReadModel
    from watchlist_app.services.research_quant import QuantOutput
    instrument = SimpleNamespace(instrument_type="index", source_settings_json={"market_calendar": "XNYS"}, exchange_code=None)
    chart = SimpleNamespace(last_recalculated_at=datetime(2026, 9, 25, 21, tzinfo=UTC),
        source_cutoff_at=datetime(2026, 9, 25, 20, tzinfo=UTC), materialization_version="fixture", data_freshness_status="fresh",
        payload_json={"research_returns": series([("2026-08-24", 100), ("2026-09-24", 110), ("2026-09-25", 121)])})
    session = SimpleNamespace(get=lambda model, iid: instrument if model is Instrument else chart if model is InstrumentChartReadModel else None)
    early = observations.instrument_observations(session, "fixture", as_of=datetime(2026, 9, 25, 19, tzinfo=UTC))
    assert early["data"]["status"] == "unavailable"
    saved = observations.instrument_observations(session, "fixture", as_of=datetime(2026, 9, 25, 22, tzinfo=UTC))
    assert saved["source_type"] == "computed_metric"
    assert saved["input_snapshot"]["known_at"] == chart.last_recalculated_at.isoformat()
    assert saved["data"]["observations"]["relative_performance"][1]["target_return_pct"] == pytest.approx(21)
    QuantOutput.model_validate({key: saved["data"][key] for key in ("summary", "metrics", "tables", "charts", "limitations")})


def test_instrument_observation_reads_actual_retained_market_versions_in_batches(tmp_path):
    from investment_studio_instrument_core.db_models import Instrument
    from studio_market.config import MarketSettings
    from studio_market.numeric import NumericStore
    from watchlist_app.db.models import InstrumentChartReadModel
    from watchlist_app.services.research_notebook import ResearchModule, ResearchNotebook, validate_notebook
    from watchlist_app.services.research_quant import QuantOutput
    cutoff = datetime(2026, 9, 25, 22, tzinfo=UTC)
    known = datetime(2026, 9, 25, 21, tzinfo=UTC)
    days = observations._market_calendar_sessions("XNYS", date(2026, 6, 1), date(2026, 9, 24))[-51:]
    instrument = SimpleNamespace(instrument_type="etf", source_settings_json={}, exchange_code="XNYS",
        identifiers=[SimpleNamespace(identifier_type="provider_symbol", identifier_value="fmp:FIXTURE")])
    own_series = series([(d.isoformat(), 100 + index) for index, d in enumerate(days)])
    chart = SimpleNamespace(last_recalculated_at=known, source_cutoff_at=known, materialization_version="fixture",
        data_freshness_status="fresh", payload_json={"research_returns": own_series})
    session = SimpleNamespace(get=lambda model, iid: instrument if model is Instrument else chart if model is InstrumentChartReadModel else None)
    store = NumericStore(MarketSettings(f"sqlite:///{tmp_path / 'market.db'}", tmp_path / "data"))
    store.create_schema_for_testing()
    try:
        def save(dataset, rows):
            store.ingest(dataset, [[{**row, "collected_at": known} for row in rows]], source="synthetic-fixture")
        save("etf_holdings", [{"etf_symbol": "FIXTURE", "holding_key": f"holding-{i}", "holding_symbol": f"S{i}",
            "holding_name": f"Synthetic {i}", "snapshot_date": days[-1], "weight_percent": weight}
            for i, weight in enumerate((20, 30, 49))] + [{"etf_symbol": "FIXTURE", "holding_key": "cash",
                "holding_symbol": "CASH", "holding_name": "US DOLLAR", "snapshot_date": days[-1], "weight_percent": 1}])
        save("etf_info", [{"symbol": "FIXTURE", "holdings_count": 4, "asset_class": "Equity"}])
        save("company_profiles", [{"symbol": f"S{i}", "company_name": f"Synthetic {i}", "currency": "USD", "is_fund": False, "is_etf": False} for i in range(3)])
        save("us_eod_daily", [{"symbol": symbol, "date": day, "close": 100 + index, "adjusted_close": 100 + index, "volume": 100 + index}
            for symbol in ("FIXTURE", "S0", "S1", "S2") for index, day in enumerate(days)])
        result = observations.instrument_observations(session, "fixture-etf", as_of=cutoff, store=store)
        basket = result["data"]["observations"]["basket"]
        assert basket["concentration_pct"] == 99
        assert basket["breadth"]["current_pct"] == 100
        assert basket["breadth"]["weight_coverage_pct"] == 99
        assert basket["total_members"] == 3 and basket["total_positions"] == 4
        assert [row["symbol"] for row in basket["top10"]] == ["S2", "S1", "S0"]
        assert len(result["sources"]) == 4 + 1 + 3 + 4 * 51
        assert store.read_source(basket["top10"][0]["source_id"])["weight_percent"] == 49
        QuantOutput.model_validate({key: result["data"][key] for key in ("summary", "metrics", "tables", "charts", "limitations")})
        # The actual figure-source validator accepts this retained evidence;
        # rendering does not need another value supplied by a model.
        module = ResearchModule(key="market-quantitative", summary="固定样例", source_ids=[result["source_id"]], figure_source_ids=[result["source_id"]])
        validate_notebook(ResearchNotebook(modules=[module]), "fixture-etf", {result["source_id"]: result})
    finally:
        store.close()
