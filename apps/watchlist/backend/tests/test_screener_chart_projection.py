from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_screener_return_sparklines_use_confirmed_returns_and_their_exact_window_evidence():
    from types import SimpleNamespace
    from datetime import date
    from investment_studio_instrument_core import build_return_intervals
    from watchlist_app.services.read_models import build_screener_chart_projection, build_sparkline_payload, build_latest_quote_overrides
    from watchlist_app.services.calculation_frequency import build_calculation_frequency_context, _market_calendar_sessions

    days = _market_calendar_sessions("XNYS", date(2026, 6, 30), date(2026, 9, 30))
    for missing in (date(2026, 7, 8), date(2026, 9, 8)):
        points = [{"as_of_date": day, "date": day.isoformat(), "value": 100 + index} for index, day in enumerate(days) if day != missing]
        context = build_calculation_frequency_context(points, expected_frequency="daily", market_calendar="XNYS")
        payload = {"selected_series": {"return_kind": "price_return", "quote_basis": "close"},
            "series": [{"points": [{"date": "2026-08-28", "value": 100}, {"date": "2026-09-30", "value": 50}]}],
            "latest_values": {"valuation": {"date": "2026-09-30", "value": 50, "quote_basis": "close"}},
            "return_series": {"metadata": {"return_kind": "total_return", "return_series_status": "complete", "quote_basis": "adjusted_close"},
                "frequency": context["profile"], "wealth_points": points,
                "intervals": build_return_intervals(points, break_dates=context["missing_observation_dates"])}}
        chart = SimpleNamespace(instrument_id="test", payload_json=payload)
        original = build_sparkline_payload([chart], instrument_ids=["test"], selected_fields=["return_chart_1m"])
        compact = build_screener_chart_projection("test", payload)
        projected = build_sparkline_payload([SimpleNamespace(instrument_id="test", payload_json=compact)], instrument_ids=["test"], selected_fields=["return_chart_1m"])
        assert original == projected
        row = original["test"]["return_chart_1m"]
        assert row["return_kind"] == "total_return"
        assert build_latest_quote_overrides([chart])["test"]["latest_quote"] == 50
        if missing.month == 7:
            assert row["status"] == "ready"
            assert row["points"][-1]["value"] > 0  # Raw quote falls; the confirmed return path rises.
        else:
            assert row["status"] == "unavailable"
            assert row["points"] == []
            assert "缺失观察值" in row["reason"]


def test_raw_chart_points_do_not_become_a_return_sparkline_when_analytical_series_is_absent():
    from watchlist_app.services.read_models import build_screener_chart_projection
    result = build_screener_chart_projection("raw", {"selected_series": {"return_kind": "price_return"},
        "series": [{"points": [{"date": "2026-09-01", "value": 100}, {"date": "2026-09-30", "value": 105}]}]})
    assert result["selected_series"] is None
    assert all(item["points"] == [] and item["status"] == "unavailable" and item["reason"] for item in result["sparklines"].values())


@pytest.mark.parametrize("chart_field", [None, "return_chart_1d", "return_chart_1w", "return_chart_1m", "return_chart_1y"])
def test_screener_projection_preserves_response_and_freshness_targets(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    chart_field: str | None,
) -> None:
    from watchlist_app.api.routes import screener
    from watchlist_app.db.session import get_session_factory

    watchlist_id = "all-private-funds"
    assert client.get(f"/api/watchlists/{watchlist_id}").status_code == 200
    assert client.post("/api/recalc/instruments/sxv264/execute", json={"job_type": "all"}).status_code == 200
    fields = ["instrument_name", "latest_quote", "latest_quote_date", "latest_cumulative_nav", "metric_as_of_date"]
    if chart_field:
        fields.append(chart_field)
    request = {"watchlist_id": watchlist_id, "selected_fields": fields, "fetch_all": True}
    scheduled: list[dict[str, object]] = []
    monkeypatch.setattr(screener, "schedule_instrument_refreshes_if_stale", lambda **kwargs: scheduled.append(kwargs))
    repository = screener.read_model_repository
    projected_reader = repository.list_screener_charts
    monkeypatch.setattr(repository, "list_screener_charts", lambda session, ids, **_kwargs: repository.list_charts(session, ids))
    original = client.post("/api/screener/query", json=request)
    assert original.status_code == 200
    original_targets = scheduled.pop()

    monkeypatch.setattr(repository, "list_screener_charts", projected_reader)
    projected = client.post("/api/screener/query", json=request)
    assert projected.status_code == 200
    assert projected.json() == original.json()
    assert scheduled == [original_targets]
    with get_session_factory()() as session:
        records = projected_reader(session, ["sxv264"])
        assert len(records) == 1
        assert set(records[0].payload_json) == {"selected_series", "latest_values", "date_range", "sparklines"}


def test_screener_projection_honors_saved_chart_columns(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from watchlist_app.api.routes import screener

    assert client.get("/api/watchlists/all-private-funds").status_code == 200
    repository = screener.read_model_repository
    read = repository.list_screener_charts
    observed: list[object] = []

    def capture(session, ids):
        records = read(session, ids)
        observed.extend(record.payload_json for record in records)
        return records

    monkeypatch.setattr(repository, "list_screener_charts", capture)
    detail = client.get("/api/watchlists/all-private-funds").json()
    view = next(item for item in detail["views"] if item["view_id"] == "overview")
    expects_series = any(column.startswith("return_chart_") for column in view["columns"])
    response = client.post("/api/screener/query", json={"watchlist_id": "all-private-funds", "view_id": "overview", "fetch_all": True})
    assert response.status_code == 200
    assert all("series" not in payload for payload in observed)
    if not expects_series:
        assert response.json()["sparklines"] == {}


def test_missing_projection_queues_repair_and_fails_closed(client: TestClient) -> None:
    from sqlalchemy import select
    from watchlist_app.db.models.read_models import InstrumentChartReadModel
    from watchlist_app.db.models.recalc import RecalcJob
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.services.read_model_freshness import _load_reconciliation_targets

    assert client.get("/api/watchlists/all-private-funds").status_code == 200
    assert client.post("/api/recalc/instruments/sxv264/execute", json={"job_type": "all"}).status_code == 200
    with get_session_factory()() as session:
        session.get(InstrumentChartReadModel, "sxv264").screener_payload_json = None
        session.commit()
    targets, _, _ = _load_reconciliation_targets(limit=100, after_instrument_id=None)
    assert next(item for item in targets if item["instrument_id"] == "sxv264")["local_materialization_version"] is None
    response = client.post("/api/screener/query", json={"watchlist_id": "all-private-funds", "selected_fields": ["instrument_name"]})
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    with get_session_factory()() as session:
        jobs = session.scalars(select(RecalcJob).where(RecalcJob.instrument_id == "sxv264", RecalcJob.job_status == "queued")).all()
        assert len(jobs) == 1
        assert jobs[0].trigger_ref_type == "screener_projection_missing"
    from watchlist_app.services.recalc_worker import process_next_recalc_job
    assert process_next_recalc_job()
    with get_session_factory()() as session:
        assert session.get(InstrumentChartReadModel, "sxv264").screener_payload_json is not None
    assert client.post("/api/screener/query", json={"watchlist_id": "all-private-funds", "selected_fields": ["instrument_name"]}).status_code == 200
