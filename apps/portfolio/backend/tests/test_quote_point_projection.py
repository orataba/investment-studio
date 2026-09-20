"""Single-quote reads retain the complete causal series validation contract."""
from copy import deepcopy
from datetime import date

import pytest

from portfolio_app.services import market_data
from .test_quote_identity import _detail, _point


END = date(2026, 1, 3)


@pytest.mark.parametrize(("changes", "expected_reason"), [
    # Several errors deliberately coexist. A last-row or one-pass early exit
    # must not change the reason established by the full financial resolver.
    ([{"value": "nan"}, {"currency": "HKD", "price_scale": None}], "invalid_quote_observation"),
    ([{"as_of_date": "invalid"}, {"currency": "HKD"}], "invalid_quote_observation"),
    ([{"currency": "HKD"}, {"price_scale": None}], "ambiguous_quote_series_identity"),
    ([{"metric_family": ""}, {"metric_family": "", "price_scale": None}], "incomplete_quote_series_identity"),
    ([{"currency": "HKD"}, {"currency": "HKD", "as_of_date": "2026-01-01"}], "quote_currency_mismatch"),
    ([{}, {"as_of_date": "2026-01-01", "price_scale": None}], "duplicate_quote_observation"),
    ([{"price_scale": None}, {"price_unit": "rate"}], "quote_price_contract_incomplete"),
    ([{"price_unit": "rate"}, {}], "ambiguous_quote_price_contract"),
    ([{"metric_family": "nav"}, {"metric_family": "nav"}], "quote_metric_family_mismatch"),
    ([{"price_scale": 100}, {"price_scale": 100}], "price_contract_unsupported"),
])
def test_point_and_series_preserve_competing_error_precedence(changes, expected_reason):
    samples = [{**_point(as_of_date=f"2026-01-0{i + 1}", value="100"), **change}
               for i, change in enumerate(changes)]
    detail = _detail(samples)
    before = deepcopy(detail)
    for bases in (["close"], ["close", "last"]):
        point = market_data.resolve_quote_point(detail, candidate_bases=bases, as_of_date=END)
        series = market_data.resolve_quote_series(detail, candidate_bases=bases, end_date=END)
        assert point.point is None and series.points == ()
        assert point.unavailable_reason == series.unavailable_reason == expected_reason
    assert detail == before


@pytest.mark.parametrize(("later", "expected_reason"), [
    ({"as_of_date": "2026-01-04", "value": "nan", "price_scale": None}, None),
    ({"as_of_date": "bad-future-date", "value": "100"}, "invalid_quote_observation"),
    ({"as_of_date": "bad-date", "value": "nan", "status": "partial"}, None),
])
def test_future_value_and_unusable_status_do_not_change_causal_selection(later, expected_reason):
    raw = _point(as_of_date="2026-01-02", value="105")
    detail = _detail([{**raw, **later}, raw, _point(as_of_date="2026-01-01", value="100")])
    result = market_data.resolve_quote_point(detail, candidate_bases=["close"], as_of_date=END)
    assert result.unavailable_reason == expected_reason
    if expected_reason is None:
        assert result.point == {**raw, "as_of_date": date(2026, 1, 2), "value": 105.0, "stale": True}


def test_unsorted_fallback_preserves_all_metadata_and_stale_calendar(monkeypatch):
    raw = _point(as_of_date="2026-01-02", value="105")
    raw["lineage"] = {"source": ["retained"]}
    detail = _detail([raw, _point(as_of_date="2026-01-01", value="100"),
                      _point(quote_basis="last", as_of_date="2026-01-05", value="999")])
    detail["exchange_code"] = "TEST"
    calendars = []
    monkeypatch.setattr(market_data, "market_calendar_sessions", lambda *args: calendars.append(args) or ())
    result = market_data.resolve_quote_point(detail, candidate_bases=["last", "close"], as_of_date=END)
    series = market_data.resolve_quote_series(detail, candidate_bases=["last", "close"], end_date=END)
    assert result.point == {**series.points[-1], "stale": False}
    assert calendars == [("TEST", END, END)]
    assert result.point["lineage"] == raw["lineage"]
    result.point["value"] = 0
    assert raw["value"] == "105"
    latest = market_data.resolve_quote_point(detail, candidate_bases=["last", "close"], as_of_date=date.max)
    assert latest.point["value"] == 999 and latest.point["stale"] is False
    assert len(calendars) == 1


def test_policy_errors_precede_available_observations_and_latest_view_fallback():
    raw = _point(as_of_date="2026-01-02", value="105")
    detail = _detail([raw])
    assert market_data.resolve_quote_point(detail, candidate_bases=["close", "unsupported"], as_of_date=END).unavailable_reason == "unsupported_quote_basis"
    detail["market_data"] = []
    assert market_data.resolve_quote_point(detail, candidate_bases=["close"], as_of_date=END).point["value"] == 105
    detail["market_data"] = [None]
    assert market_data.resolve_quote_point(detail, candidate_bases=["close"], as_of_date=END).unavailable_reason == "quote_series_unavailable"


def test_lookup_fallback_and_dates_outside_index_match_full_point_selection():
    detail = _detail([
        _point(as_of_date="2026-01-03", value="0"),
        _point(as_of_date="2026-01-01", value="100"),
        _point(as_of_date="2026-01-02", value="105"),
        _point(quote_basis="last", as_of_date="2026-01-04", value="108"),
    ])
    lookup = market_data.QuoteSeriesLookup(end_date=END)
    for day in range(1, 6):
        cutoff = date(2026, 1, day)
        for bases in (["close"], ["last", "close"]):
            actual = lookup.point(detail, candidate_bases=bases, as_of_date=cutoff)
            expected = market_data.resolve_quote_point(detail, candidate_bases=bases, as_of_date=cutoff).point
            assert actual == expected
