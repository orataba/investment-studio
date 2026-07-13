from __future__ import annotations

from portfolio_app.api.routes import workspace as workspace_routes


def _noncash_instrument_ids(payload: dict[str, object]) -> set[str]:
    return {
        str(instrument_core.get("instrument_id") or "")
        for row in payload.get("rows", [])
        if isinstance(row, dict)
        and isinstance((instrument_core := row.get("instrument_core")), dict)
        and str(instrument_core.get("instrument_type") or "") != "cash"
        and str(instrument_core.get("instrument_id") or "")
    }


def test_materialized_holdings_uses_one_canonical_market_data_lock(client, monkeypatch) -> None:
    snapshot_response = client.get("/api/portfolios/portfolio-ops/snapshots/daily")
    assert snapshot_response.status_code == 200

    original_lock = workspace_routes.lock_instrument_market_data
    lock_calls: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    def recording_lock(instrument_ids, *, as_of_date, roles):
        lock_calls.append((tuple(instrument_ids), tuple(roles)))
        return original_lock(
            instrument_ids,
            as_of_date=as_of_date,
            roles=roles,
        )

    monkeypatch.setattr(workspace_routes, "lock_instrument_market_data", recording_lock)

    response = client.get(
        "/api/workspace/holdings",
        params={"portfolio_id": "portfolio-ops", "as_of_date": "2026-04-15"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(lock_calls) == 1
    assert set(lock_calls[0][0]) == _noncash_instrument_ids(payload)
    assert lock_calls[0][1] == ("chart", "total_return")
    assert all(row.get("price_chart_6m") is not None for row in payload["rows"])


def test_instrument_holding_projection_skips_portfolio_wide_analytics(client, monkeypatch) -> None:
    snapshot_response = client.get("/api/portfolios/portfolio-ops/snapshots/daily")
    assert snapshot_response.status_code == 200

    def fail_portfolio_wide_load(*_args, **_kwargs):
        raise AssertionError("single-instrument holding projection must not build portfolio-wide analytics")

    monkeypatch.setattr(workspace_routes, "lock_instrument_market_data", fail_portfolio_wide_load)
    monkeypatch.setattr(workspace_routes, "get_portfolio_risk_policy", fail_portfolio_wide_load)
    monkeypatch.setattr(workspace_routes, "enrich_holdings_forward_risk", fail_portfolio_wide_load)

    response = client.get(
        "/api/workspace/holdings/instrument",
        params={
            "portfolio_id": "portfolio-ops",
            "instrument_id": "equity-us-abbv",
            "as_of_date": "2026-04-15",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["as_of_date"] == "2026-04-15"
    assert payload["row"]["instrument_core"]["instrument_id"] == "equity-us-abbv"
    assert "rows" not in payload
    assert "price_chart_1m" not in payload["row"]
    assert "instrument_return_series_all" not in payload["row"]
    assert "forward_risk_share" not in payload["row"]
    assert len(response.content) < 5_000


def test_instrument_holding_projection_preserves_arbitrary_as_of_fallback(client, monkeypatch) -> None:
    monkeypatch.setattr(
        workspace_routes,
        "build_materialized_instrument_holding_projection",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        workspace_routes,
        "holdings_workspace",
        lambda **_kwargs: {
            "portfolio_id": "portfolio-ops",
            "portfolio_name": "Portfolio Operations",
            "base_currency": "USD",
            "as_of_date": "2026-04-12",
            "view_label": "View: Holdings",
            "rows": [
                {
                    "line_id": "equity-us-abbv",
                    "instrument_core": {
                        "instrument_id": "equity-us-abbv",
                        "instrument_name": "AbbVie Inc",
                        "instrument_type": "equity",
                        "currency": "USD",
                        "identifiers": [],
                    },
                    "quantity": 10.0,
                    "last_price": 200.0,
                    "market_value": 2_000.0,
                    "cost_basis": 1_800.0,
                    "allocation": 0.2,
                    "coverage_status": "price-nav-fx",
                    "price_chart_1m": [{"as_of_date": "2026-04-12", "value": 200.0}],
                    "instrument_return_series_all": {"points": []},
                }
            ],
        },
    )

    response = client.get(
        "/api/workspace/holdings/instrument",
        params={
            "portfolio_id": "portfolio-ops",
            "instrument_id": "equity-us-abbv",
            "as_of_date": "2026-04-12",
        },
    )

    assert response.status_code == 200
    assert response.json()["row"]["quantity"] == 10.0
    assert "price_chart_1m" not in response.json()["row"]
    assert "instrument_return_series_all" not in response.json()["row"]


def test_fallback_holdings_uses_one_canonical_lock_for_frequency_and_charts(
    client,
    monkeypatch,
) -> None:
    original_lock = workspace_routes.lock_instrument_market_data
    lock_calls: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    def recording_lock(instrument_ids, *, as_of_date, roles):
        lock_calls.append((tuple(instrument_ids), tuple(roles)))
        return original_lock(
            instrument_ids,
            as_of_date=as_of_date,
            roles=roles,
        )

    monkeypatch.setattr(
        workspace_routes,
        "get_cached_materialized_holdings_workspace",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(workspace_routes, "lock_instrument_market_data", recording_lock)

    response = client.get(
        "/api/workspace/holdings",
        params={"portfolio_id": "portfolio-ops", "as_of_date": "2026-04-15"},
    )

    assert response.status_code == 200
    payload = response.json()
    held_instrument_ids = _noncash_instrument_ids(payload)
    assert len(lock_calls) == 1
    assert set(lock_calls[0][0]) == held_instrument_ids
    assert lock_calls[0][1] == ("chart", "total_return")
    assert all(row.get("price_chart_6m") is not None for row in payload["rows"])
