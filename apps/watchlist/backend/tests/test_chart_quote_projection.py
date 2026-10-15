from copy import deepcopy
from decimal import Decimal

import pytest

from watchlist_app.services.read_models import instrument_quote_payload, serialize_payload


def chart_payload():
    return {
        "instrument_id": "fund-us-agg", "currency": "USD", "base_series_type": "adjusted_close",
        "selected_series": {"quote_basis": "adjusted_close", "return_kind": "total_return"},
        "price_series": {"quote_basis": "close", "currency": "USD", "points": [
            {"date": "2026-10-09", "value": 98.0, "segment_id": "old"},
            {"date": "2026-10-12", "value": 100.0, "segment_id": "old"},
            {"date": "2026-10-13", "value": 101.0, "segment_id": "new"},
            {"date": "2026-10-14", "value": None},
        ]},
        "latest_values": {"valuation": {"date": "2026-10-13", "value": 101.0}},
        "date_range": {"start": "2026-10-09", "end": "2026-10-14"},
        "series": [{"name": "total return", "points": [{"date": "2026-10-14", "value": 999.0}]}],
        "return_series": {"wealth_points": [{"date": "2026-10-14", "value": 999.0}]},
        "available_compare_targets": ["benchmark"], "freshness": {"status": "stale"},
    }


def test_header_projection_keeps_valuation_clock_and_segments_without_return_history():
    source = chart_payload()
    before = deepcopy(source)
    result = instrument_quote_payload(source)
    assert result["price_series"] == {**source["price_series"], "points": source["price_series"]["points"][1:3]}
    assert result["latest_values"] == source["latest_values"]
    assert result["selected_series"] == source["selected_series"]
    assert result["freshness"] == source["freshness"]
    assert result["series"] == result["available_compare_targets"] == []
    assert "return_series" not in result
    assert source == before


@pytest.mark.parametrize("values, expected", [
    ([], []), ([None, 0, -1, "100", True], []),
    ([1, float("nan"), float("inf"), 0, 2, -1], [1, 2]),
    ([Decimal("0.00000012"), Decimal("0.00000013")], [0.00000012, 0.00000013]),
])
def test_header_filters_before_taking_last_two_without_rounding(values, expected):
    source = {"price_series": {"points": [{"date": str(i), "value": value} for i, value in enumerate(values)]}}
    result = serialize_payload(instrument_quote_payload(source))
    assert [point["value"] for point in result["price_series"]["points"]] == expected


def test_chart_api_default_unchanged_quote_keeps_freshness_and_full_storage(client, monkeypatch):
    from watchlist_app.api.routes import funds
    from types import SimpleNamespace

    assert client.get("/api/watchlists/all-instruments").status_code == 200
    source = chart_payload()
    record = SimpleNamespace(payload_json=source)
    monkeypatch.setattr(funds.read_model_repository, "get_chart", lambda *_: record)
    scheduled = []
    monkeypatch.setattr(funds, "_schedule_instrument_refresh", lambda *_, **kw: scheduled.append(kw))
    monkeypatch.setattr(funds, "recalculation_freshness_overrides", lambda *_: {"fund-us-agg": {"status": "pending"}})
    full = client.get("/api/instruments/fund-us-agg/chart")
    quote = client.get("/api/instruments/fund-us-agg/chart?projection=quote")
    assert full.status_code == quote.status_code == 200
    assert full.json() == {**source, "freshness": {"status": "pending"}}
    assert quote.json() == {**instrument_quote_payload(source), "freshness": {"status": "pending"}}
    assert len(scheduled) == 2
    assert all(item["trigger_ref_type"] == "instrument_chart_read" for item in scheduled)
    assert source == chart_payload()
    assert client.get("/api/instruments/fund-us-agg/chart?projection=unknown").status_code == 422
    assert client.get("/api/instruments/not-registered/chart?projection=quote").status_code == 404


def test_quote_api_missing_chart_preserves_empty_default(client, monkeypatch):
    from watchlist_app.api.routes import funds
    assert client.get("/api/watchlists/all-instruments").status_code == 200
    monkeypatch.setattr(funds.read_model_repository, "get_chart", lambda *_: None)
    monkeypatch.setattr(funds, "_schedule_instrument_refresh", lambda *_, **kw: None)
    monkeypatch.setattr(funds, "recalculation_freshness_overrides", lambda *_: {})
    full = client.get("/api/instruments/fund-us-agg/chart")
    quote = client.get("/api/instruments/fund-us-agg/chart?projection=quote")
    assert full.status_code == quote.status_code == 200
    assert quote.json() == full.json()
    assert "price_series" not in quote.json()
