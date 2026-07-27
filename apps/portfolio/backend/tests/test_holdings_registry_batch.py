from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from portfolio_app.api.routes import workspace as workspace_routes
from portfolio_app.services import daily_snapshots, instrument_charts, performance
from portfolio_app.services.instrument_registry import get_registry_instrument_details


def _noncash_instrument_ids(payload: dict[str, object]) -> set[str]:
    return {
        str(instrument_core.get("instrument_id") or "")
        for row in payload.get("rows", [])
        if isinstance(row, dict)
        and isinstance((instrument_core := row.get("instrument_core")), dict)
        and str(instrument_core.get("instrument_type") or "") != "cash"
        and str(instrument_core.get("instrument_id") or "")
    }


def test_registry_batch_loads_full_details_and_marks_missing() -> None:
    details = get_registry_instrument_details(
        ["equity-us-abbv", "fund-us-agg", "equity-us-abbv", "missing-instrument", ""]
    )

    assert list(details) == ["equity-us-abbv", "fund-us-agg", "missing-instrument"]
    assert details["missing-instrument"] is None
    assert details["equity-us-abbv"] is not None
    assert len(details["equity-us-abbv"]["market_data"]) == 8
    assert details["fund-us-agg"] is not None
    assert len(details["fund-us-agg"]["market_data"]) == 4


def test_public_holdings_response_uses_canonical_instrument_core_ids(
    monkeypatch,
) -> None:
    reconciliation_calls: list[dict[str, object]] = []
    warning_calls: list[tuple[set[str], set[str]]] = []

    monkeypatch.setattr(
        workspace_routes,
        "reconcile_instrument_event_tasks",
        lambda **kwargs: reconciliation_calls.append(kwargs) or {"portfolio-1": 0},
    )

    def capture_warnings(instrument_types, instrument_ids, **_kwargs):
        warning_calls.append((set(instrument_types), set(instrument_ids)))
        return []

    monkeypatch.setattr(
        workspace_routes,
        "corporate_action_quality_warnings",
        capture_warnings,
    )
    monkeypatch.setattr(
        workspace_routes,
        "instrument_event_task_quality_warnings",
        lambda _portfolio_id: [],
    )

    payload = workspace_routes._public_holdings_workspace_response(
        {
            "portfolio_id": "portfolio-1",
            "rows": [
                {
                    "line_id": "equity-1",
                    "instrument_core": {
                        "instrument_id": "equity-1",
                        "instrument_type": "equity",
                    },
                    "price_chart_1m": [],
                    "price_chart_3m": [],
                    "price_chart_6m": [],
                    "price_chart_1y": [],
                },
                {
                    "line_id": "cash:CNY",
                    "instrument_core": {
                        "instrument_id": "cash:CNY",
                        "instrument_type": "cash",
                    },
                    "price_chart_1m": [],
                    "price_chart_3m": [],
                    "price_chart_6m": [],
                    "price_chart_1y": [],
                },
            ],
        },
        include_details=False,
        transactions=[],
        as_of_date=date(2026, 7, 24),
    )

    assert payload["detail_level"] == "compact"
    assert reconciliation_calls == [
        {
            "portfolio_ids": ["portfolio-1"],
            "instrument_ids": {"equity-1"},
        }
    ]
    assert warning_calls == [({"equity", "cash"}, {"equity-1"})]


def test_snapshot_holding_aggregation_preserves_accounts_and_earliest_holding_profile() -> None:
    def snapshot_row(
        *,
        account_id: str,
        holding_start_date: str,
        holding_max_drawdown: float,
    ) -> SimpleNamespace:
        holding_series = {
            "first_return_start_date": holding_start_date,
            "points": [
                {
                    "start_date": holding_start_date,
                    "date": "2026-07-24",
                    "value": holding_max_drawdown,
                }
            ],
        }
        return SimpleNamespace(
            as_of_date=date(2026, 7, 24),
            instrument_id="fund-1",
            holding_json={
                "account_id": account_id,
                "account_ids": [account_id],
                "instrument_id": "fund-1",
                "instrument_ref": {
                    "instrument_id": "fund-1",
                    "instrument_name": "Fund 1",
                    "instrument_type": "fund",
                    "currency": "CNY",
                    "identifiers": [],
                },
                "quantity": 1,
                "last_price": 100,
                "market_value": 100,
                "market_value_base": 100,
                "day_change_pct": 0,
                "day_change_value": 0,
                "day_change_value_base": 0,
                "cost_basis_method": "fifo",
                "cost_basis": 90,
                "cost_basis_base": 90,
                "open_position_lot_count": 1,
                "instrument_holding_start_date": holding_start_date,
                "instrument_holding_return_series": holding_series,
                "instrument_holding_max_drawdown": holding_max_drawdown,
                "coverage_status": "price-nav-fx",
            },
        )

    rows = [
        snapshot_row(
            account_id="account-a",
            holding_start_date="2026-07-20",
            holding_max_drawdown=-0.01,
        ),
        snapshot_row(
            account_id="account-b",
            holding_start_date="2026-06-01",
            holding_max_drawdown=-0.20,
        ),
    ]

    aggregated = daily_snapshots._aggregate_holding_rows(
        rows,  # type: ignore[arg-type]
        total_nav_base=200,
    )

    assert len(aggregated) == 1
    assert aggregated[0]["account_ids"] == ["account-a", "account-b"]
    assert aggregated[0]["account_count"] == 2
    assert aggregated[0]["instrument_holding_start_date"] == "2026-06-01"
    assert aggregated[0]["instrument_holding_max_drawdown"] == -0.20
    assert aggregated[0]["instrument_holding_return_series"] == {
        "first_return_start_date": "2026-06-01",
        "points": [
            {
                "start_date": "2026-06-01",
                "date": "2026-07-24",
                "value": -0.20,
            }
        ],
    }


def test_materialized_holdings_uses_one_bulk_detail_map(client, monkeypatch) -> None:
    snapshot_response = client.get("/api/portfolios/portfolio-ops/snapshots/daily")
    assert snapshot_response.status_code == 200

    original_bulk_loader = workspace_routes.get_registry_instrument_details
    bulk_calls: list[tuple[str, ...]] = []

    def recording_bulk_loader(instrument_ids):
        bulk_calls.append(tuple(instrument_ids))
        return original_bulk_loader(instrument_ids)

    def fail_single_chart_load(*_args, **_kwargs):
        raise AssertionError("holdings chart enrichment must reuse the bulk detail map")

    monkeypatch.setattr(workspace_routes, "get_registry_instrument_details", recording_bulk_loader)
    monkeypatch.setattr(instrument_charts, "get_registry_instrument_detail", fail_single_chart_load)
    monkeypatch.setattr(workspace_routes, "build_instrument_holdings_market_profile", fail_single_chart_load)

    response = client.get(
        "/api/workspace/holdings",
        params={
            "portfolio_id": "portfolio-ops",
            "as_of_date": "2026-04-15",
            "include_details": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(bulk_calls) == 1
    assert set(bulk_calls[0]) == _noncash_instrument_ids(payload)
    assert all(row.get("price_chart_6m") is not None for row in payload["rows"])


def test_instrument_holding_projection_skips_portfolio_wide_analytics(client, monkeypatch) -> None:
    snapshot_response = client.get("/api/portfolios/portfolio-ops/snapshots/daily")
    assert snapshot_response.status_code == 200

    def fail_portfolio_wide_load(*_args, **_kwargs):
        raise AssertionError("single-instrument holding projection must not build portfolio-wide analytics")

    monkeypatch.setattr(workspace_routes, "get_registry_instrument_details", fail_portfolio_wide_load)
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


def test_instrument_holding_projection_fails_closed_when_snapshot_is_unavailable(client, monkeypatch) -> None:
    monkeypatch.setattr(
        workspace_routes,
        "build_materialized_instrument_holding_projection",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        workspace_routes,
        "holdings_workspace",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("single-instrument projection must not rebuild the full workspace")
        ),
    )

    response = client.get(
        "/api/workspace/holdings/instrument",
        params={
            "portfolio_id": "portfolio-ops",
            "instrument_id": "equity-us-abbv",
            "as_of_date": "2026-04-12",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Materialized holding projection is unavailable for the requested date."
    )


def test_fallback_holdings_reuses_bulk_details_for_frequency_valuation_and_charts(
    client,
    monkeypatch,
) -> None:
    original_bulk_loader = workspace_routes.get_registry_instrument_details
    original_performance_loader = performance.get_registry_instrument_detail
    bulk_calls: list[tuple[str, ...]] = []
    singleton_calls: list[str] = []

    def recording_bulk_loader(instrument_ids):
        bulk_calls.append(tuple(instrument_ids))
        return original_bulk_loader(instrument_ids)

    def recording_performance_loader(instrument_id: str):
        singleton_calls.append(instrument_id)
        return original_performance_loader(instrument_id)

    def fail_single_chart_load(*_args, **_kwargs):
        raise AssertionError("holdings chart enrichment must reuse the bulk detail map")

    monkeypatch.setattr(
        workspace_routes,
        "get_cached_materialized_holdings_workspace",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(workspace_routes, "get_registry_instrument_details", recording_bulk_loader)
    monkeypatch.setattr(performance, "get_registry_instrument_detail", recording_performance_loader)
    monkeypatch.setattr(instrument_charts, "get_registry_instrument_detail", fail_single_chart_load)
    monkeypatch.setattr(workspace_routes, "build_instrument_holdings_market_profile", fail_single_chart_load)

    response = client.get(
        "/api/workspace/holdings",
        params={
            "portfolio_id": "portfolio-ops",
            "as_of_date": "2026-04-15",
            "include_details": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    held_instrument_ids = _noncash_instrument_ids(payload)
    assert len(bulk_calls) == 1
    assert set(bulk_calls[0]) == held_instrument_ids
    assert held_instrument_ids.isdisjoint(singleton_calls)
    assert all(row.get("price_chart_6m") is not None for row in payload["rows"])
