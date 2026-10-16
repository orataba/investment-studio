"""Economic-return boundaries are independent of observed quotes and display."""
from datetime import date, timedelta
from math import log

import pytest

from watchlist_app.services.canonical_recalc import (
    _build_chart_payload, _build_return_series, _growth_of_100,
    _select_quote_series, _periodic_returns,
)
from watchlist_app.services.calculation_frequency import build_calculation_frequency_context
from watchlist_app.services.return_windows import resolve_return_window, period_return_percent
from watchlist_app.services.price_risk import series_limitation
from watchlist_app.services.read_models import _chart_return_kind


def points(basis, values=(100, 102), days=("2026-09-01", "2026-09-02")):
    return [{"as_of_date": date.fromisoformat(day), "value": value, "currency": "HKD",
        "frequency": "daily", "status": "complete", "quote_basis": basis,
        "metric_family": "nav" if basis.endswith("nav") else "price"}
        for day, value in zip(days, values, strict=True)]


def test_price_bar_all_history_is_not_silently_capped_and_explicit_limit_keeps_anchors(client, monkeypatch):
    from watchlist_app.api.routes import instruments as api

    bars = [{"date": (date(2020, 1, 1) + timedelta(days=index)).isoformat(),
             "close": "100", "adjustment_factor": str(1 + index / 1300)}
            for index in range(1301)]
    def read_metadata(_id, *, metadata_only):
        assert metadata_only is True
        return {"instrument_id": "long-history", "instrument_type": "etf", "currency": "HKD"}

    monkeypatch.setattr(api, "get_shared_instrument", read_metadata)
    monkeypatch.setattr(api, "get_shared_price_bars", lambda **_kwargs: bars)
    complete = client.get("/api/instruments/long-history/price-bars")
    assert complete.status_code == 200
    complete = complete.json()
    assert complete["count"] == 1301
    assert complete["bars"][0]["date"] == "2020-01-01"
    limited = client.get("/api/instruments/long-history/price-bars", params={"limit": 1}).json()
    assert limited["bars"] == [bars[-1]]
    assert limited["adjustment_anchors"] == complete["adjustment_anchors"]
    assert complete["available_price_modes"] == ["raw", "front", "back"]


def select(kind, by_basis, settings=None, role="total_return", preference="auto"):
    return _select_quote_series(by_basis, shared_instrument={
        "instrument_type": kind, "source_settings": settings or {},
        "quote_selection_policy": {"total_return": ["adjusted_close", "close", "last", "total_return_nav", "official_nav"],
            "chart": ["adjusted_close", "close", "last", "total_return_nav"],
            "valuation": ["close", "official_nav"]}},
        role=role, preference=preference, instrument_type=kind)


@pytest.mark.parametrize("kind", ["equity", "etf"])
def test_raw_security_quotes_remain_visible_but_cannot_become_internal_returns(kind):
    quotes = {"close": points("close", (100, 50))}  # Could be a split, not a 50% investor loss.
    analysis = select(kind, quotes, {"return_semantics": "price_return"})
    observed = select(kind, quotes, role="valuation", preference="nav_with_dividend")
    assert analysis["points"] == []
    assert observed["points"][-1]["value"] == 50
    chart = _build_chart_payload("hk-test", analysis["points"], "HKD",
        selection=analysis, valuation_selection=observed, total_return_selection=analysis)
    assert chart["latest_values"]["valuation"]["value"] == 50
    assert chart["price_series"]["points"][-1]["value"] == 50
    assert chart["latest_values"]["total_return"] is None


def test_index_requires_confirmed_kind_and_crypto_preserves_price_return_contract():
    quotes = {"close": points("close")}
    assert select("index", quotes)["points"] == []
    for kind in ("price_return", "total_return"):
        chosen = select("index", quotes, {"return_semantics": kind})
        assert chosen["return_kind"] == kind
    assert select("crypto", quotes)["return_kind"] == "price_return"
    assert _chart_return_kind({"selected_series": {"quote_basis": "close"}}) is None


@pytest.mark.parametrize("kind,analytical_basis,observed_basis,settings", [
    ("equity", "adjusted_close", "close", {}),
    ("etf", "adjusted_close", "close", {}),
    ("public_fund", "total_return_nav", "official_nav", {}),
    ("index", "close", "last", {"return_semantics": "price_return"}),
])
def test_analysis_chart_uses_return_policy_even_when_registry_chart_selects_observed_prices(
    kind, analytical_basis, observed_basis, settings,
):
    quotes = {analytical_basis: points(analytical_basis, (100, 102)),
              observed_basis: points(observed_basis, (10, 5))}
    detail = {"instrument_type": kind, "source_settings": settings, "quote_selection_policy": {
        "total_return": [analytical_basis], "chart": [observed_basis], "valuation": [observed_basis],
    }}
    chosen = _select_quote_series(quotes, shared_instrument=detail, role="chart", preference="auto", instrument_type=kind)
    assert chosen["selected_role"] == "chart"
    assert chosen["selected_quote_basis"] == analytical_basis
    assert [point["value"] for point in chosen["points"]] == [100, 102]
    observed = _select_quote_series(quotes, shared_instrument=detail, role="valuation", preference="auto", instrument_type=kind)
    assert [point["value"] for point in observed["points"]] == [10, 5]


@pytest.mark.parametrize("kind", ["equity", "etf"])
def test_materialized_analysis_chart_matches_confirmed_returns_with_raw_chart_policy(client, kind):
    from .conftest import seed_shared_instrument, canonical_quote_policy
    iid = f"split-display-policy-{kind}"
    policy = canonical_quote_policy(kind)
    policy.update(chart=["close"], total_return=["adjusted_close"])
    seed_shared_instrument({"instrument_id": iid, "instrument_name": "Separate price and return policies",
        "instrument_type": kind, "currency": "HKD", "exchange_code": "XHKG",
        "quote_selection_policy": policy,
        "source_settings": {"expected_frequency": "daily", "market_calendar": "XHKG"},
        "identifiers": [{"identifier_type": "ticker", "identifier_value": iid.upper(), "is_primary": True}],
        "market_data": [{"metric_family": "price", "quote_basis": basis, "as_of_date": day,
            "value": value, "currency": "HKD", "price_unit": "per_unit", "price_scale": "1", "status": "complete"}
            for basis, values in [("close", ("10", "5")), ("adjusted_close", ("100", "102"))]
            for day, value in zip(("2026-09-01", "2026-09-02"), values, strict=True)],
        "lifecycle_state": {"status": "active"}})
    assert client.post(f"/api/instruments/{iid}/resolve").status_code == 200
    assert client.post(f"/api/recalc/instruments/{iid}/execute", json={"job_type": "all"}).status_code == 200
    payload = client.get(f"/api/instruments/{iid}/chart").json()
    assert payload["selected_series"]["quote_basis"] == "adjusted_close"
    assert payload["selected_series"]["return_kind"] == "total_return"
    assert payload["selected_series"]["return_series_status"] == "ready"
    assert [point["value"] for point in payload["series"][0]["points"]] == [100, 102]
    assert [point["value"] for point in payload["price_series"]["points"]] == [10, 5]
    assert payload["latest_values"]["valuation"]["value"] == 5
    assert payload["latest_values"]["total_return"]["value"] == 102
    assert payload["return_series"]["intervals"][0]["value"] == pytest.approx(.02)
    assert payload["research_returns"]["metadata"]["quote_basis"] == "adjusted_close"
    assert payload["research_returns"]["observation_schedule"] == payload["return_series"]["observation_schedule"]
    assert payload["research_returns"]["observation_schedule"]["market_calendar"] == "XHKG"
    assert payload["research_returns"]["observation_schedule"]["expected_frequency"] == "daily"
    assert payload["research_returns"]["observation_schedule"]["instrument_type"] == kind


def test_unit_nav_is_not_promoted_to_return_nav_and_confirmed_tr_is_not_double_counted():
    quotes = {"official_nav": points("official_nav", (1, 0.9)),
        "total_return_nav": points("total_return_nav", (1, 1.1))}
    chosen = select("public_fund", quotes)
    series = _build_return_series(chosen, build_calculation_frequency_context(chosen["points"]))
    assert series["metadata"]["return_kind"] == "total_return"
    assert series["intervals"][0]["value"] == pytest.approx(0.1)
    assert series["intervals"][0]["log_return"] == pytest.approx(log(1.1))
    assert series["wealth_points"][-1]["value"] == pytest.approx(110)
    assert select("public_fund", {"official_nav": quotes["official_nav"]})["points"] == []
    assert select("public_fund", quotes, role="valuation")["points"][-1]["value"] == 0.9


def test_separately_rebased_tr_segments_never_create_returns_or_window_metrics_across_boundary():
    days = ("2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04")
    settings = {"total_return_segments": [
        {"start_date": days[0], "end_date": days[1]}, {"start_date": days[2], "end_date": days[3]}],
        "total_return_unavailable_intervals": [{"start_date": days[1], "end_date": days[2], "reason": "unresolved_action"}]}
    chosen = select("etf", {"adjusted_close": points("adjusted_close", (100, 102, 1, 1.1), days)}, settings)
    series = _build_return_series(chosen, build_calculation_frequency_context(chosen["points"]))
    assert chosen["return_series_status"] == "partial"
    assert series["metadata"]["return_measure"] == "simple"
    assert [item["status"] for item in series["intervals"]] == ["complete", "unavailable", "complete"]
    assert series["intervals"][1]["value"] is None
    assert series["intervals"][1]["log_return"] is None
    assert [point["value"] for point in series["wealth_points"]] == pytest.approx([100, 102, 100, 110])
    chart = _build_chart_payload("hk-test", chosen["points"], "HKD", selection=chosen)
    assert [point["value"] for point in chart["series"][0]["points"]] == [100, 102, 1, 1.1]
    assert chart["series"][0]["points"][1]["segment_id"] != chart["series"][0]["points"][2]["segment_id"]
    assert resolve_return_window(chosen["points"], requested_start_date=date(2026, 9, 1), requested_end_date=date(2026, 9, 4)) is None
    recent = resolve_return_window(chosen["points"], requested_start_date=date(2026, 9, 3), requested_end_date=date(2026, 9, 4))
    assert period_return_percent(recent) == pytest.approx(10)
    assert _growth_of_100(chosen["points"]) is None
    assert _periodic_returns(chosen["points"]) == []


def test_all_missing_sessions_mask_daily_returns_but_do_not_erase_observed_endpoint_return():
    # The public missing-date sample is capped at 20; the arithmetic uses all missing dates.
    days = ("2026-01-01", "2026-02-01", "2026-03-01")
    chosen = select("crypto", {"close": points("close", (100, 110, 121), days)})
    context = build_calculation_frequency_context(chosen["points"], market_calendar="24/7")
    series = _build_return_series(chosen, context)
    assert len(context["profile"]["missing_observation_date_sample"]) == 20
    assert len(context["missing_observation_dates"]) > 20
    assert all(interval["value"] is None for interval in series["intervals"])
    window = resolve_return_window(chosen["points"], requested_start_date=date(2026, 1, 1), requested_end_date=date(2026, 3, 1))
    assert period_return_percent(window) == pytest.approx(21)
    assert series_limitation({**series, "points": series["wealth_points"]})


def test_declared_low_frequency_is_not_misrepresented_as_daily_or_zero_filled():
    chosen = select("public_fund", {"total_return_nav": points("total_return_nav", (1, 1.01), ("2026-09-04", "2026-09-11"))})
    context = build_calculation_frequency_context(chosen["points"], expected_frequency="weekly", market_calendar="XHKG")
    series = _build_return_series(chosen, context)
    assert context["profile"]["resolved_frequency"] == "weekly"
    assert series["intervals"][0]["start_date"] == "2026-09-04"
    assert series["intervals"][0]["date"] == "2026-09-11"
    assert series["intervals"][0]["value"] == pytest.approx(0.01)
    assert len(series["wealth_points"]) == 2
    assert "低频" in series_limitation({**series, "points": series["wealth_points"]})


def test_price_display_anchors_are_full_history_and_keep_their_confirmed_segment(monkeypatch):
    from watchlist_app.api.routes import instruments as api
    bars = [{"date": "2026-09-01", "close": 10, "adjustment_factor": 1},
        {"date": "2026-09-02", "close": 11, "adjustment_factor": 1},
        {"date": "2026-09-03", "close": 12, "adjustment_factor": 2},
        {"date": "2026-09-04", "close": 13, "adjustment_factor": 3}]
    segments = [{"start_date": "2026-09-01", "end_date": "2026-09-02"},
        {"start_date": "2026-09-03", "end_date": "2026-09-04"}]
    def read_metadata(_iid, *, metadata_only):
        assert metadata_only is True
        return {"instrument_id": "test", "instrument_type": "etf", "currency": "HKD",
            "source_settings": {"total_return_segments": segments}}

    monkeypatch.setattr(api, "get_shared_instrument", read_metadata)
    monkeypatch.setattr(api, "get_shared_price_bars", lambda **kwargs: bars)
    full = api.get_instrument_price_bars("test", start_date=None, end_date=None, limit=500)
    cropped = api.get_instrument_price_bars("test", start_date=date(2026, 9, 2), end_date=None, limit=1)
    assert cropped["count"] == 1
    assert full["adjustment_anchors"] == cropped["adjustment_anchors"]
    assert full["adjustment_anchors"]["front"] == {"date": "2026-09-04", "factor": 3, **segments[1]}
    assert full["adjustment_anchors"]["back"] == {"date": "2026-09-01", "factor": 1, **segments[0]}
    assert full["available_price_modes"] == ["raw", "front", "back"]
    assert bars[-1]["close"] == 13  # Display choice did not mutate any observation.


def test_price_adjustment_cutoff_never_uses_a_later_factor_and_missing_factor_is_explained(monkeypatch):
    from watchlist_app.api.routes import instruments as api
    bars = [{"date": "2026-09-01", "close": 10, "adjustment_factor": 1},
        {"date": "2026-09-02", "close": 11, "adjustment_factor": 2},
        {"date": "2026-09-03", "close": 12, "adjustment_factor": None},
        {"date": "2026-09-04", "close": 13, "adjustment_factor": 4}]
    def read_metadata(_iid, *, metadata_only):
        assert metadata_only is True
        return {"instrument_id": "test", "instrument_type": "equity", "currency": "HKD"}

    monkeypatch.setattr(api, "get_shared_instrument", read_metadata)
    monkeypatch.setattr(api, "get_shared_price_bars", lambda **kwargs: bars)
    past = api.get_instrument_price_bars("test", start_date=None, end_date=date(2026, 9, 2), limit=500)
    assert past["adjustment_anchors"]["front"]["factor"] == 2
    assert past["adjustment_anchors"]["front"]["end_date"] == "2026-09-02"
    missing = api.get_instrument_price_bars("test", start_date=None, end_date=date(2026, 9, 3), limit=500)
    assert missing["available_price_modes"] == ["raw"]
    assert missing["adjustment_anchors"]["front"]["date"] == "2026-09-02"
    assert "front" in missing["unavailable_price_mode_reasons"]


@pytest.mark.parametrize("kind", ["equity", "etf", "index"])
def test_materialized_detail_keeps_raw_observation_when_analytical_series_is_unavailable(client, kind):
    from .conftest import seed_shared_instrument, canonical_quote_policy
    iid = f"raw-observation-{kind}"
    seed_shared_instrument({"instrument_id": iid, "instrument_name": "Raw observation", "instrument_type": kind,
        "currency": "USD", **({"exchange_code": "XNYS"} if kind in {"equity", "etf"} else {}),
        "quote_selection_policy": canonical_quote_policy(kind),
        "identifiers": [{"identifier_type": "ticker", "identifier_value": iid.upper(), "is_primary": True}],
        "market_data": [{"metric_family": "price", "quote_basis": "close", "as_of_date": day,
            "value": value, "currency": "USD", "price_unit": "per_unit", "price_scale": "1", "status": "complete"}
            for day, value in [("2026-09-01", "100"), ("2026-09-02", "50")]],
        "lifecycle_state": {"status": "active"}})
    assert client.post(f"/api/instruments/{iid}/resolve").status_code == 200
    assert client.post(f"/api/recalc/instruments/{iid}/execute", json={"job_type": "all"}).status_code == 200
    chart = client.get(f"/api/instruments/{iid}/chart").json()
    assert chart["latest_values"]["valuation"]["value"] == 50
    assert chart["price_series"]["points"][-1]["value"] == 50
    assert chart["return_series"]["intervals"] == []
    assert chart["research_returns"]["points"] == []
    assert chart["series"][0]["points"] == []
    risk = client.get(f"/api/instruments/{iid}/risk").json()
    assert risk["data_quality"]["status"] == "unavailable"
    assert all(row["investment"] is None for row in risk["risk_metrics"])


def test_hkex_confirmed_full_day_closures_do_not_create_false_return_gaps():
    days = ("2023-08-31", "2023-09-04", "2023-09-05", "2023-09-06", "2023-09-07", "2023-09-11")
    chosen = select("etf", {"adjusted_close": points("adjusted_close", (100, 101, 100, 102, 103, 104), days)})
    context = build_calculation_frequency_context(chosen["points"], expected_frequency="daily", market_calendar="XHKG")
    assert context["profile"]["gap_count"] == 0
    series = _build_return_series(chosen, context)
    assert all(item["status"] == "complete" for item in series["intervals"])
    assert series["intervals"][0]["start_date"] == "2023-08-31"
    assert series["intervals"][0]["date"] == "2023-09-04"
    assert series["intervals"][-1]["start_date"] == "2023-09-07"
    assert series["intervals"][-1]["date"] == "2023-09-11"
    assert series_limitation({**series, "points": series["wealth_points"]}) is None
    missing = build_calculation_frequency_context([chosen["points"][0], *chosen["points"][2:]], expected_frequency="daily", market_calendar="XHKG")
    assert missing["profile"]["missing_observation_date_sample"] == ["2023-09-04"]

@pytest.mark.parametrize("kind,basis", [("etf", "adjusted_close"), ("public_fund", "total_return_nav"), ("private_fund", "total_return_nav")])
def test_nav_endpoint_retains_return_segment_identity_for_fund_and_benchmark_consumers(client, kind, basis):
    from .conftest import seed_shared_instrument, canonical_quote_policy
    iid = f"nav-segment-{kind}"
    days = ("2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04")
    seed_shared_instrument({"instrument_id": iid, "instrument_name": "Rebased return chain",
        "instrument_type": kind, "currency": "HKD", **({"exchange_code": "XHKG"} if kind == "etf" else {}),
        "quote_selection_policy": canonical_quote_policy(kind),
        "source_settings": {"expected_frequency": "daily", "market_calendar": "XHKG", "total_return_segments": [
            {"start_date": days[0], "end_date": days[1]}, {"start_date": days[2], "end_date": days[3]}]},
        "identifiers": [{"identifier_type": "ticker", "identifier_value": iid.upper(), "is_primary": True}],
        "market_data": [{"metric_family": "price" if kind == "etf" else "nav", "quote_basis": basis,
            "as_of_date": day, "value": value, "currency": "HKD", "price_unit": "per_unit", "price_scale": "1", "status": "complete"}
            for day, value in zip(days, ("100", "102", "1", "1.1"), strict=True)],
        "lifecycle_state": {"status": "active"}})
    # Coverage metadata is provider-owned, not a user-editable source setting.
    from investment_studio_instrument_core.db_models import Instrument
    from watchlist_app.db.session import get_session_factory
    with get_session_factory()() as session:
        record = session.get(Instrument, iid)
        record.source_settings_json = {**record.source_settings_json, "total_return_segments": [
            {"start_date": days[0], "end_date": days[1]}, {"start_date": days[2], "end_date": days[3]}]}
        session.commit()
    assert client.post(f"/api/instruments/{iid}/resolve").status_code == 200
    assert client.post(f"/api/recalc/instruments/{iid}/execute", json={"job_type": "all"}).status_code == 200
    nav = client.get(f"/api/instruments/{iid}/nav-series").json()
    assert [row["selected_value"] for row in nav["rows"]] == [100, 102, 1, 1.1]
    identities = [row["segment_id"] for row in nav["rows"]]
    assert identities[0] == identities[1] != identities[2] == identities[3]
    assert all(identities)
    assert client.get(f"/api/instruments/{iid}/summary").json()["tabs"] == [
        "overview", "risk", "investment-research", "views", "info"]


@pytest.mark.parametrize('kind', ['equity', 'etf'])
def test_listed_weekend_observation_cannot_change_monday_return(kind):
    from watchlist_app.services.price_risk import period_loss_readings
    quotes = points('adjusted_close', (100, 50, 100), ('2026-10-02', '2026-10-03', '2026-10-05'))
    detail = {'instrument_type':kind, 'exchange_code':'XNAS',
              'source_settings':{'expected_frequency':'daily', 'market_calendar':'XNAS'},
              'quote_selection_policy':{'total_return':['adjusted_close'], 'chart':['adjusted_close']}}
    chosen = _select_quote_series({'adjusted_close': quotes}, shared_instrument=detail,
        role='total_return', preference='auto', instrument_type=kind)
    context = build_calculation_frequency_context(chosen['points'], expected_frequency='daily', market_calendar='XNAS')
    series = _build_return_series(chosen, context)
    series['points'] = series['wealth_points']
    assert [point['as_of_date'].isoformat() for point in chosen['points']]==['2026-10-02','2026-10-05']
    assert period_loss_readings(series, {'day':3})[0]['return_pct']==0
    assert context['profile']['gap_status']=='aligned'
    direct = build_calculation_frequency_context(quotes, expected_frequency='daily', market_calendar='XNAS', instrument_type=kind)
    assert direct['profile']['excluded_non_session_count']==1


@pytest.mark.parametrize('kind,calendar', [('crypto','24/7'), ('public_fund','XNYS'), ('private_fund','XNYS')])
def test_nonlisted_weekend_nav_and_crypto_remain_observed_intervals(kind, calendar):
    quotes = points('close' if kind=='crypto' else 'total_return_nav', (100, 102, 104),
                    ('2026-10-02','2026-10-03','2026-10-05'))
    context = build_calculation_frequency_context(quotes, expected_frequency='daily',
                                                 market_calendar=calendar, instrument_type=kind)
    assert len(context['points'])==3
    assert context['profile']['excluded_non_session_count']==0
