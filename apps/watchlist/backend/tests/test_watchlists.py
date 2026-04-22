from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from .conftest import TEST_SHARED_INSTRUMENTS, seed_shared_instrument


def test_create_watchlist_generates_unique_ids_and_required_columns(
    client: TestClient,
) -> None:
    first = client.post(
        "/api/watchlists",
        json={"name": "My Focus", "description": "Primary coverage list"},
    )
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["watchlist_id"] == "my-focus"

    duplicate = client.post(
        "/api/watchlists",
        json={"name": "My Focus", "description": "Second list with same name"},
    )
    assert duplicate.status_code == 200
    duplicate_payload = duplicate.json()
    assert duplicate_payload["watchlist_id"] == "my-focus-2"

    detail = client.get(f"/api/watchlists/{first_payload['watchlist_id']}")
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["default_view_id"] == "overview"
    overview_view = next(
        item for item in detail_payload["views"] if item["view_id"] == "overview"
    )
    assert overview_view["columns"] == [
        "asset_name",
        "price_chart_1m",
        "latest_quote",
        "latest_quote_date",
        "return_1w",
        "return_1m",
        "return_ytd",
        "ticker_or_isin",
        "data_freshness_status",
    ]
    private_fund_view = next(
        item
        for item in detail_payload["views"]
        if item["view_id"] == "private-fund-screening"
    )
    assert private_fund_view["name"] == "私募分类筛选"
    assert private_fund_view["default_group_by"] == "attr.fund_category_l1"
    assert private_fund_view["default_filters"] == {
        "asset_type": ["fund"],
        "attr.fund_regime": ["私募"],
    }
    assert private_fund_view["columns"] == [
        "asset_name",
        "attr.fund_regime",
        "attr.fund_category_l1",
        "attr.fund_category_l2",
        "attr.fund_category_l3",
        "attr.implementation_style",
        "attr.style_profile",
        "attr.manager_assessment",
        "attr.volatility_bucket",
        "attr.drawdown_control",
        "attr.style_stability",
        "attr.transparency_quality",
        "data_freshness_status",
    ]


def test_adding_shared_registry_instrument_to_created_watchlist_materializes_rows(
    client: TestClient,
) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Tactical Ideas", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"asset_ids": ["fund-us-agg"]},
    )
    assert add_response.status_code == 200
    assert add_response.json()["accepted_count"] == 1

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": ["ticker_or_isin", "asset_name", "overall_rating"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    payload = screener.json()
    assert payload["total_rows"] == 1
    assert payload["rows"][0]["asset_name"] == "iShares Core U.S. Aggregate Bond ETF"
    assert payload["rows"][0]["ticker_or_isin"] == "AGG"


def test_adding_non_fund_shared_registry_instrument_is_rejected(
    client: TestClient,
) -> None:
    seed_shared_instrument(
        {
            "asset_id": "equity-demo",
            "asset_name": "Demo Equity",
            "asset_type": "equity",
            "currency": "USD",
            "identifiers": [
                {"identifier_type": "ticker", "identifier_value": "DEMO", "is_primary": True},
            ],
            "market_data": [
                {
                    "metric_family": "price",
                    "quote_basis": "close",
                    "as_of_date": "2026-04-15",
                    "value": "12.3400",
                    "currency": "USD",
                    "status": "complete",
                }
            ],
            "lifecycle_state": {"status": "active"},
        }
    )
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Fund Only", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"asset_ids": ["equity-demo"]},
    )

    assert add_response.status_code == 400
    assert "fund instruments only" in add_response.json()["detail"]


def test_move_watchlist_items_transfers_membership_to_target_watchlist(
    client: TestClient,
) -> None:
    source = client.post(
        "/api/watchlists",
        json={"name": "Source List", "description": None},
    )
    target = client.post(
        "/api/watchlists",
        json={"name": "Target List", "description": None},
    )
    source_watchlist_id = source.json()["watchlist_id"]
    target_watchlist_id = target.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{source_watchlist_id}/items",
        json={"asset_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    move_response = client.post(
        f"/api/watchlists/{source_watchlist_id}/items/move",
        json={"asset_ids": ["sxv264"], "target_watchlist_id": target_watchlist_id},
    )
    assert move_response.status_code == 200
    assert move_response.json() == {
        "source_watchlist_id": source_watchlist_id,
        "target_watchlist_id": target_watchlist_id,
        "moved_count": 1,
        "added_count": 1,
        "already_present_count": 0,
    }

    source_rows = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": source_watchlist_id,
            "view_id": "overview",
            "selected_fields": ["asset_name"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert source_rows.status_code == 200
    assert source_rows.json()["total_rows"] == 0

    target_rows = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": target_watchlist_id,
            "view_id": "overview",
            "selected_fields": ["asset_name", "latest_quote", "latest_quote_date"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert target_rows.status_code == 200
    target_payload = target_rows.json()
    assert target_payload["total_rows"] == 1
    assert target_payload["rows"][0]["asset_name"] == "SXV264 Total Return Fund"
    assert target_payload["rows"][0]["latest_quote"] == pytest.approx(101.2365, abs=1e-6)
    assert target_payload["rows"][0]["latest_quote_date"] == "2026-04-14"


def test_copy_watchlist_items_adds_membership_to_target_without_removing_source(
    client: TestClient,
) -> None:
    source = client.post(
        "/api/watchlists",
        json={"name": "Copy Source", "description": None},
    )
    target = client.post(
        "/api/watchlists",
        json={"name": "Copy Target", "description": None},
    )
    source_watchlist_id = source.json()["watchlist_id"]
    target_watchlist_id = target.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{source_watchlist_id}/items",
        json={"asset_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    copy_response = client.post(
        f"/api/watchlists/{source_watchlist_id}/items/copy",
        json={"asset_ids": ["sxv264"], "target_watchlist_id": target_watchlist_id},
    )
    assert copy_response.status_code == 200
    assert copy_response.json() == {
        "source_watchlist_id": source_watchlist_id,
        "target_watchlist_id": target_watchlist_id,
        "copied_count": 1,
        "added_count": 1,
        "already_present_count": 0,
    }

    source_rows = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": source_watchlist_id,
            "view_id": "overview",
            "selected_fields": ["asset_name"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert source_rows.status_code == 200
    assert source_rows.json()["total_rows"] == 1

    target_rows = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": target_watchlist_id,
            "view_id": "overview",
            "selected_fields": ["asset_name", "latest_quote", "latest_quote_date"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert target_rows.status_code == 200
    target_payload = target_rows.json()
    assert target_payload["total_rows"] == 1
    assert target_payload["rows"][0]["asset_name"] == "SXV264 Total Return Fund"
    assert target_payload["rows"][0]["latest_quote"] == pytest.approx(101.2365, abs=1e-6)
    assert target_payload["rows"][0]["latest_quote_date"] == "2026-04-14"


def test_move_watchlist_items_rejects_assets_missing_from_source(
    client: TestClient,
) -> None:
    source = client.post(
        "/api/watchlists",
        json={"name": "Move Missing Source", "description": None},
    )
    target = client.post(
        "/api/watchlists",
        json={"name": "Move Missing Target", "description": None},
    )
    source_watchlist_id = source.json()["watchlist_id"]
    target_watchlist_id = target.json()["watchlist_id"]

    move_response = client.post(
        f"/api/watchlists/{source_watchlist_id}/items/move",
        json={"asset_ids": ["sxv264"], "target_watchlist_id": target_watchlist_id},
    )
    assert move_response.status_code == 400
    assert move_response.json()["detail"] == "Assets not found in source watchlist: sxv264."

    target_rows = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": target_watchlist_id,
            "view_id": "overview",
            "selected_fields": ["asset_name"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert target_rows.status_code == 200
    assert target_rows.json()["total_rows"] == 0


def test_copy_watchlist_items_rejects_assets_missing_from_source(
    client: TestClient,
) -> None:
    source = client.post(
        "/api/watchlists",
        json={"name": "Copy Missing Source", "description": None},
    )
    target = client.post(
        "/api/watchlists",
        json={"name": "Copy Missing Target", "description": None},
    )
    source_watchlist_id = source.json()["watchlist_id"]
    target_watchlist_id = target.json()["watchlist_id"]

    copy_response = client.post(
        f"/api/watchlists/{source_watchlist_id}/items/copy",
        json={"asset_ids": ["sxv264"], "target_watchlist_id": target_watchlist_id},
    )
    assert copy_response.status_code == 400
    assert copy_response.json()["detail"] == "Assets not found in source watchlist: sxv264."

    target_rows = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": target_watchlist_id,
            "view_id": "overview",
            "selected_fields": ["asset_name"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert target_rows.status_code == 200
    assert target_rows.json()["total_rows"] == 0


def test_adding_shared_nav_instrument_recalculates_last_nav_fields(
    client: TestClient,
) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "FOF Coverage", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"asset_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200
    assert add_response.json()["accepted_count"] == 1
    assert add_response.json()["recalculated_asset_ids"] == ["sxv264"]

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": [
                "ticker_or_isin",
                "asset_name",
                "latest_quote",
                "latest_quote_date",
                "last_nav_date",
                "data_freshness_status",
                "return_1w",
                "return_1m",
                "annualized_return",
            ],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    payload = screener.json()
    assert payload["total_rows"] == 1
    assert payload["rows"][0]["ticker_or_isin"] == "SXV264"
    assert payload["rows"][0]["latest_quote"] == pytest.approx(101.2365, abs=1e-6)
    assert payload["rows"][0]["latest_quote_date"] == "2026-04-14"
    assert payload["rows"][0]["last_nav_date"] == "2026-04-14"
    assert payload["rows"][0]["data_freshness_status"] == "fresh"
    assert payload["rows"][0]["return_1w"] == pytest.approx(1.236476, abs=1e-6)
    assert payload["rows"][0]["return_1m"] == pytest.approx(2.259067, abs=1e-6)
    assert payload["rows"][0]["annualized_return"] == pytest.approx(14.119462, abs=1e-6)
    assert payload["snapshot_metadata"]["as_of_date"] == "2026-04-14"


def test_instrument_performance_and_risk_payloads_include_materialized_metrics(
    client: TestClient,
) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Performance Coverage", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"asset_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    performance_response = client.get("/api/instruments/sxv264/performance")
    assert performance_response.status_code == 200
    performance_payload = performance_response.json()
    trailing_by_window = {
        row["window"]: row
        for row in performance_payload["trailing_returns"]
    }
    assert performance_payload["snapshot_metadata"]["as_of_date"] == "2026-04-14"
    assert trailing_by_window["1M"]["investment_nav"] == pytest.approx(2.259067, abs=1e-6)
    assert trailing_by_window["YTD"]["investment_nav"] == pytest.approx(3.832283, abs=1e-6)
    assert trailing_by_window["Ann."]["investment_nav"] == pytest.approx(14.119462, abs=1e-6)

    risk_response = client.get("/api/instruments/sxv264/risk")
    assert risk_response.status_code == 200
    risk_payload = risk_response.json()
    risk_metrics = {
        row["metric"]: row
        for row in risk_payload["risk_metrics"]
    }
    assert risk_payload["snapshot_metadata"]["as_of_date"] == "2026-04-14"
    assert len(risk_payload["scatter_points"]) == 1
    assert risk_payload["scatter_points"][0]["name"] == "Investment"
    assert risk_payload["scatter_points"][0]["return"] == pytest.approx(14.119462, abs=1e-6)
    assert risk_payload["scatter_points"][0]["volatility"] == pytest.approx(
        risk_metrics["volatility"]["investment"],
        abs=1e-9,
    )
    assert risk_metrics["annualized_return"]["investment"] == pytest.approx(14.119462, abs=1e-6)
    assert risk_metrics["volatility"]["investment"] is not None
    assert risk_payload["drawdown_summary"]["maximum"] is not None
    assert risk_payload["risk_structure"]["rows"]
    assert risk_payload["current_watch"]["overall_level"] in {"Normal", "Elevated", "High"}
    assert risk_payload["current_watch"]["rows"]
    assert risk_payload["change_monitor"]["rows"]


def test_instrument_nav_settings_round_trip_and_surface_compare_settings(
    client: TestClient,
) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Nav Settings Coverage", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"asset_ids": ["sxv264", "savf63", "fund-us-agg"]},
    )
    assert add_response.status_code == 200

    initial_response = client.get("/api/instruments/sxv264/nav-settings")
    assert initial_response.status_code == 200
    assert initial_response.json() == {
        "nav_basis_preference": "auto",
        "source_mode": "manual",
        "source_email": "",
        "source_location": "Manual upload",
        "source_api_profile": "",
        "default_benchmark_asset_id": None,
        "peer_baseline_asset_ids": [],
    }

    update_response = client.put(
        "/api/instruments/sxv264/nav-settings",
        json={
            "nav_basis_preference": "nav",
            "source_mode": "email",
            "source_email": "ops@example.com",
            "source_location": "INBOX",
            "source_api_profile": "nav-email-profile",
            "default_benchmark_asset_id": "savf63",
            "peer_baseline_asset_ids": ["fund-us-agg", "savf63", "sxv264", "fund-us-agg"],
            "updated_by": "test-suite",
        },
    )
    assert update_response.status_code == 200
    assert update_response.json() == {
        "nav_basis_preference": "nav",
        "source_mode": "email",
        "source_email": "ops@example.com",
        "source_location": "INBOX",
        "source_api_profile": "nav-email-profile",
        "default_benchmark_asset_id": "savf63",
        "peer_baseline_asset_ids": ["fund-us-agg", "savf63"],
    }

    nav_series_response = client.get("/api/instruments/sxv264/nav-series")
    assert nav_series_response.status_code == 200
    nav_series_payload = nav_series_response.json()
    assert nav_series_payload["nav_basis_preference"] == "nav"
    assert nav_series_payload["source_settings"] == {
        "source_mode": "email",
        "source_email": "ops@example.com",
        "source_location": "INBOX",
        "source_api_profile": "nav-email-profile",
    }
    assert nav_series_payload["compare_settings"] == {
        "default_benchmark_asset_id": "savf63",
        "peer_asset_ids": ["fund-us-agg", "savf63"],
    }

    clear_response = client.put(
        "/api/instruments/sxv264/nav-settings",
        json={
            "default_benchmark_asset_id": None,
            "peer_baseline_asset_ids": [],
            "updated_by": "test-suite",
        },
    )
    assert clear_response.status_code == 200
    assert clear_response.json()["default_benchmark_asset_id"] is None
    assert clear_response.json()["peer_baseline_asset_ids"] == []


def test_watchlist_add_rolls_back_when_recalc_fails(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException
    from watchlist_app.api.routes import watchlists as watchlists_route

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Atomic Guardrail", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    call_count = 0

    def _recalc_then_fail(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise HTTPException(status_code=500, detail="recalc failed")
        return {"job_status": "completed", "result": {}}

    monkeypatch.setattr(watchlists_route.canonical_recalc_service, "execute_recalc", _recalc_then_fail)

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"asset_ids": ["sxv264", "savf63"]},
    )
    assert add_response.status_code == 500
    assert add_response.json()["detail"] == "recalc failed"

    detail = client.get(f"/api/watchlists/{watchlist_id}")
    assert detail.status_code == 200
    assert detail.json()["item_count"] == 0

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": ["asset_name"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    assert screener.json()["total_rows"] == 0


def test_watchlist_rejects_unknown_shared_instrument_ids(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Unknown Guardrail", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"asset_ids": ["not-in-registry"]},
    )
    assert add_response.status_code == 404
    assert "Platform / Instruments" in add_response.json()["detail"]


def test_screener_query_triggers_async_refresh_when_shared_data_is_newer(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.api.routes import screener as screener_route

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Freshness Repair", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"asset_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    scheduled: list[dict[str, object]] = []

    monkeypatch.setattr(
        screener_route,
        "schedule_asset_refresh_if_stale",
        lambda **kwargs: scheduled.append(kwargs) or True,
    )

    response = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": ["asset_name", "latest_quote", "latest_quote_date", "last_nav_date"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )

    assert response.status_code == 200
    assert scheduled == [
        {
            "asset_id": "sxv264",
            "local_latest_date": date(2026, 4, 14),
            "trigger_ref_type": "screener_query",
            "trigger_ref_id": watchlist_id,
        }
    ]


def test_instrument_summary_triggers_async_refresh_when_shared_data_is_newer(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.services import read_model_freshness

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Instrument Freshness", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"asset_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    scheduled: list[dict[str, object]] = []

    monkeypatch.setattr(
        read_model_freshness,
        "get_shared_instrument",
        lambda asset_id: {
            "asset_id": asset_id,
            "market_data": [
                {
                    "metric_family": "nav",
                    "quote_basis": "official_nav",
                    "as_of_date": "2026-04-15",
                    "value": "101.500000",
                }
            ],
        },
    )
    monkeypatch.setattr(
        read_model_freshness,
        "_enqueue_stale_recalc_job",
        lambda **kwargs: scheduled.append(kwargs) or True,
    )

    response = client.get("/api/instruments/sxv264/summary")

    assert response.status_code == 200
    assert scheduled == [
        {
            "asset_id": "sxv264",
            "trigger_ref_type": "instrument_summary_read",
            "trigger_ref_id": "sxv264",
        }
    ]


def test_stale_read_repair_enqueues_single_durable_recalc_job(client: TestClient) -> None:
    from watchlist_app.db import session as session_module
    from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
    from watchlist_app.services.read_model_freshness import schedule_asset_refresh_if_stale

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Durable Freshness Repair", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"asset_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    first = schedule_asset_refresh_if_stale(
        asset_id="sxv264",
        local_latest_date=date(2026, 4, 13),
        trigger_ref_type="instrument_summary_read",
        trigger_ref_id="sxv264",
    )
    second = schedule_asset_refresh_if_stale(
        asset_id="sxv264",
        local_latest_date=date(2026, 4, 13),
        trigger_ref_type="instrument_summary_read",
        trigger_ref_id="sxv264",
    )

    assert first is True
    assert second is True

    session_factory = session_module.get_session_factory()
    with session_factory() as session:
        jobs = list(SQLAlchemyRecalcJobRepository().list_recent(session))

    matching = [
        job
        for job in jobs
        if job.asset_id == "sxv264"
        and job.job_type == "all"
        and job.trigger_type == "stale_read_repair"
        and job.trigger_ref_type == "instrument_summary_read"
        and job.trigger_ref_id == "sxv264"
    ]
    assert len(matching) == 1
    assert matching[0].job_status == "queued"


def test_process_next_recalc_job_refreshes_shared_metadata_drift(
    client: TestClient,
) -> None:
    from watchlist_app.db import session as session_module
    from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
    from watchlist_app.services.recalc_worker import process_next_recalc_job

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Metadata Drift Repair", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"asset_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    seed_shared_instrument(
        {
            **TEST_SHARED_INSTRUMENTS["sxv264"],
            "asset_name": "SXV264 Renamed Total Return Fund",
            "identifiers": [
                {
                    "identifier_type": "ticker",
                    "identifier_value": "SXV264X",
                    "is_primary": True,
                }
            ],
        }
    )

    summary_response = client.get("/api/instruments/sxv264/summary")
    assert summary_response.status_code == 200

    session_factory = session_module.get_session_factory()
    with session_factory() as session:
        jobs = list(SQLAlchemyRecalcJobRepository().list_recent(session))
    queued_jobs = [
        job
        for job in jobs
        if job.asset_id == "sxv264"
        and job.trigger_type == "stale_read_repair"
        and job.job_status == "queued"
    ]
    assert len(queued_jobs) == 1

    assert process_next_recalc_job() is True

    with session_factory() as session:
        jobs = list(SQLAlchemyRecalcJobRepository().list_recent(session))
    completed_jobs = [
        job
        for job in jobs
        if job.asset_id == "sxv264"
        and job.trigger_type == "stale_read_repair"
        and job.job_status == "completed"
    ]
    assert len(completed_jobs) == 1

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": ["asset_name", "ticker_or_isin"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    payload = screener.json()
    assert payload["rows"][0]["asset_name"] == "SXV264 Renamed Total Return Fund"
    assert payload["rows"][0]["ticker_or_isin"] == "SXV264X"


def test_process_next_recalc_job_recovers_stale_running_job(
    client: TestClient,
) -> None:
    from watchlist_app.db import session as session_module
    from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
    from watchlist_app.services.recalc_job_ids import make_recalc_job_id
    from watchlist_app.services.recalc_worker import process_next_recalc_job

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Worker Recovery", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"asset_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    session_factory = session_module.get_session_factory()
    repository = SQLAlchemyRecalcJobRepository()
    job_id = make_recalc_job_id()
    with session_factory() as session:
        record = repository.create(
            session,
            recalc_job_id=job_id,
            job_type="all",
            asset_id="sxv264",
            trigger_type="stale_read_repair",
            trigger_ref_type="instrument_summary_read",
            trigger_ref_id="sxv264",
            job_status="queued",
            priority=95,
            dedupe_key=f"all:sxv264:{job_id}",
            payload_json={"requested_by": "test"},
        )
        repository.mark_running(session, record)
        record.started_at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=10)
        session.commit()

    assert process_next_recalc_job() is True

    with session_factory() as session:
        recovered = repository.get(session, job_id)

    assert recovered is not None
    assert recovered.job_status == "completed"


def test_legacy_funds_summary_route_is_gone(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Removed Compat Alias", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"asset_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    response = client.get("/api/funds/sxv264/summary")

    assert response.status_code == 404


def test_instruments_library_alias_lists_local_assets(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Library Alias", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"asset_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    response = client.get("/api/instruments/library")

    assert response.status_code == 200
    assert any(item["asset_id"] == "sxv264" for item in response.json())


def test_execute_recalc_returns_404_for_unknown_asset(client: TestClient) -> None:
    response = client.post(
        "/api/recalc/assets/not-in-watchlist/execute",
        json={
            "job_type": "all",
            "trigger_type": "shared_asset_write",
            "trigger_ref_type": "instrument_nav_history_replace",
            "trigger_ref_id": "2026-04-16",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Asset not found: not-in-watchlist"


def test_watchlist_rejects_archived_shared_instrument_ids(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.api.routes import watchlists as watchlists_route

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Archived Guardrail", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    monkeypatch.setattr(
        watchlists_route,
        "get_shared_instrument",
        lambda asset_id: {
            "asset_id": asset_id,
            "asset_name": "Retired Asset",
            "asset_type": "fund",
            "currency": "USD",
            "lifecycle_state": {"status": "archived"},
            "identifiers": [],
        },
    )

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"asset_ids": ["fund-archived"]},
    )
    assert add_response.status_code == 404
    assert "Platform / Instruments" in add_response.json()["detail"]


def test_manual_instrument_creation_route_is_gone(client: TestClient) -> None:
    response = client.post(
        "/api/instruments/manual",
        json={"ticker": "TACT-01", "name": "Tactical Test Fund"},
    )
    assert response.status_code == 410


def test_watchlist_add_returns_502_when_shared_registry_is_unreachable(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.api.routes import watchlists as watchlists_route
    from watchlist_app.services.shared_instrument_registry import SharedInstrumentRegistryTransportError

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Registry Outage", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    def _raise_registry_transport_error(asset_id: str):
        raise SharedInstrumentRegistryTransportError("Failed to reach shared instrument registry.")

    monkeypatch.setattr(watchlists_route, "get_shared_instrument", _raise_registry_transport_error)

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"asset_ids": ["fund-us-agg"]},
    )
    assert add_response.status_code == 502
    assert "Failed to reach shared instrument registry" in add_response.json()["detail"]


def test_detail_resolution_uses_local_overlay_when_shared_registry_returns_500(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.services import instrument_resolution
    from watchlist_app.services.shared_instrument_registry import SharedInstrumentRegistryHttpError

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Detail Fallback", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"asset_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    def _raise_registry_error(asset_id: str):
        raise SharedInstrumentRegistryHttpError(
            status_code=500,
            message="Shared instrument registry returned HTTP 500.",
        )

    monkeypatch.setattr(instrument_resolution, "get_shared_instrument", _raise_registry_error)

    response = client.get("/api/instruments/sxv264/resolve")
    assert response.status_code == 200
    payload = response.json()
    assert payload["requested_asset_id"] == "sxv264"
    assert payload["canonical_asset_id"] == "sxv264"
    assert payload["detail_subject_id"] == "sxv264"
    assert payload["detail_supported"] is True
    assert payload["detail_view_type"] == "fund"
    assert payload["support_reason"] == "detail_ready_local_cache"


def test_detail_resolution_uses_cached_watchlist_row_when_shared_registry_returns_500(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.api.routes import watchlists as watchlists_route
    from watchlist_app.services import instrument_resolution
    from watchlist_app.services.shared_instrument_registry import SharedInstrumentRegistryHttpError

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Stub Fallback", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    fund_record = {
        "asset_id": "fund-msft-strategy",
        "asset_name": "Microsoft Strategy Fund",
        "asset_type": "fund",
        "currency": "USD",
        "lifecycle_state": {"status": "active"},
        "identifiers": [
            {
                "identifier_type": "ticker",
                "identifier_value": "MSFTX",
                "is_primary": True,
            }
        ],
    }

    monkeypatch.setattr(
        watchlists_route,
        "get_shared_instrument",
        lambda asset_id: fund_record if asset_id == "fund-msft-strategy" else None,
    )
    monkeypatch.setattr(
        instrument_resolution,
        "get_shared_instrument",
        lambda asset_id: fund_record if asset_id == "fund-msft-strategy" else None,
    )

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"asset_ids": ["fund-msft-strategy"]},
    )
    assert add_response.status_code == 200

    def _raise_registry_error(asset_id: str):
        raise SharedInstrumentRegistryHttpError(
            status_code=500,
            message="Shared instrument registry returned HTTP 500.",
        )

    monkeypatch.setattr(instrument_resolution, "get_shared_instrument", _raise_registry_error)

    response = client.get("/api/instruments/fund-msft-strategy/resolve")
    assert response.status_code == 200
    payload = response.json()
    assert payload["requested_asset_id"] == "fund-msft-strategy"
    assert payload["canonical_asset_id"] == "fund-msft-strategy"
    assert payload["asset_name"] == "Microsoft Strategy Fund"
    assert payload["asset_type"] == "fund"
    assert payload["primary_identifier"] == "MSFTX"
    assert payload["detail_subject_id"] == "fund-msft-strategy"
    assert payload["detail_supported"] is True
    assert payload["support_reason"] == "detail_ready_local_cache"


def test_shared_registry_service_returns_none_for_missing_asset() -> None:
    from watchlist_app.services import shared_instrument_registry as registry

    assert registry.get_shared_instrument("stale-asset") is None


def test_local_detail_support_is_explicit_by_asset_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.api.routes import watchlists as watchlists_route

    assert watchlists_route._supports_local_detail({"asset_type": "fund"}) is True
    assert watchlists_route._supports_local_detail({"asset_type": "equity"}) is False


def test_shared_registry_service_wraps_storage_errors_without_local_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.services import shared_instrument_registry as registry

    def _raise_transport_error(*_args, **_kwargs):
        raise registry.SharedInstrumentRegistryTransportError("Failed to reach shared instrument registry.")

    monkeypatch.setattr(registry.shared_store, "list_instruments", _raise_transport_error)
    monkeypatch.setattr(registry.shared_store, "find_instrument_by_identifier", _raise_transport_error)

    with pytest.raises(registry.SharedInstrumentRegistryTransportError):
        registry.list_shared_instruments()

    with pytest.raises(registry.SharedInstrumentRegistryTransportError):
        registry.resolve_shared_instrument(identifier_value="ARCH")


def test_database_starts_without_seeded_watchlists(client: TestClient) -> None:
    response = client.get("/api/watchlists")
    assert response.status_code == 200
    assert response.json() == []


def test_duplicate_custom_view_name_returns_409(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "View Collisions", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    payload = {
        "name": "My View",
        "description": None,
        "default_group_by": "none",
        "default_sort": [],
        "default_filters": {},
        "default_advanced_filters": None,
        "columns": [
            {"field_key": "asset_name", "display_order": 1, "width": 320, "is_visible": True}
        ],
    }

    first = client.post(f"/api/watchlists/{watchlist_id}/views", json=payload)
    assert first.status_code == 200

    duplicate = client.post(f"/api/watchlists/{watchlist_id}/views", json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Watchlist view name already exists"


def test_custom_view_ids_are_slugged_to_path_safe_values(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Path Safe Views", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    payload = {
        "name": "A/B",
        "description": None,
        "default_group_by": "none",
        "default_sort": [],
        "default_filters": {},
        "default_advanced_filters": None,
        "columns": [
            {"field_key": "asset_name", "display_order": 1, "width": 320, "is_visible": True}
        ],
    }

    created = client.post(f"/api/watchlists/{watchlist_id}/views", json=payload)
    assert created.status_code == 200
    assert created.json()["view_id"] == "a-b"

    updated = client.put(
        f"/api/watchlists/{watchlist_id}/views/{created.json()['view_id']}",
        json={
            **payload,
            "name": "A/B Updated",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "A/B Updated"


def test_copy_watchlist_sanitizes_legacy_custom_view_ids(client: TestClient) -> None:
    from watchlist_app.db import session as session_module
    from watchlist_app.db.models.watchlists import WatchlistView, WatchlistViewColumn

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Legacy View Copy", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    legacy_view_id = f"{watchlist_id}::a/b"
    with session_module.get_session_factory()() as session:
        session.add(
            WatchlistView(
                watchlist_view_id=legacy_view_id,
                watchlist_id=watchlist_id,
                name="A/B",
                description=None,
                kind="custom",
                default_sort_json=[],
                default_filters_json={},
                default_advanced_filter_json={},
                default_group_by="none",
                density="standard",
                is_default=False,
                created_at=datetime.now(UTC).replace(microsecond=0),
            )
        )
        session.add(
            WatchlistViewColumn(
                watchlist_view_id=legacy_view_id,
                field_key="asset_name",
                display_order=1,
                width=320,
                is_visible=True,
                pin_side=None,
            )
        )
        session.commit()

    copied = client.post(f"/api/watchlists/{watchlist_id}/copy")
    assert copied.status_code == 200
    copied_watchlist_id = copied.json()["watchlist_id"]

    copied_detail = client.get(f"/api/watchlists/{copied_watchlist_id}")
    assert copied_detail.status_code == 200
    copied_view = next(
        item for item in copied_detail.json()["views"] if item["name"] == "A/B"
    )
    assert copied_view["view_id"] == "a-b"

    updated = client.put(
        f"/api/watchlists/{copied_watchlist_id}/views/{copied_view['view_id']}",
        json={
            "name": "A/B Updated",
            "description": None,
            "default_group_by": "none",
            "default_sort": [],
            "default_filters": {},
            "default_advanced_filters": None,
            "columns": [
                {
                    "field_key": "asset_name",
                    "display_order": 1,
                    "width": 320,
                    "is_visible": True,
                }
            ],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "A/B Updated"


def test_default_view_remains_overview_after_creating_custom_view(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Stable Default", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    created_view = client.post(
        f"/api/watchlists/{watchlist_id}/views",
        json={
            "name": "A Custom View",
            "description": None,
            "default_group_by": "none",
            "default_sort": [],
            "default_filters": {},
            "default_advanced_filters": None,
            "columns": [
                {"field_key": "asset_name", "display_order": 1, "width": 320, "is_visible": True}
            ],
        },
    )
    assert created_view.status_code == 200

    detail = client.get(f"/api/watchlists/{watchlist_id}")
    assert detail.status_code == 200
    assert detail.json()["default_view_id"] == "overview"


def test_create_watchlist_retries_when_slug_conflicts_during_insert(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.api.routes import watchlists as watchlists_route

    original_create = watchlists_route.watchlist_repository.create
    generated_ids = iter(["retry-list", "retry-list-2"])
    attempted_ids: list[str] = []
    failed_once = False

    def _generate_retry_id(_session, _name: str) -> str:
        return next(generated_ids)

    def _create(*args, **kwargs):
        nonlocal failed_once
        attempted_ids.append(kwargs["watchlist_id"])
        if not failed_once:
            failed_once = True
            raise IntegrityError("insert", {}, Exception("duplicate key value violates unique constraint"))
        return original_create(*args, **kwargs)

    monkeypatch.setattr(watchlists_route, "_generate_watchlist_id", _generate_retry_id)
    monkeypatch.setattr(watchlists_route.watchlist_repository, "create", _create)

    response = client.post(
        "/api/watchlists",
        json={"name": "Retry List", "description": None},
    )
    assert response.status_code == 200
    assert response.json()["watchlist_id"] == "retry-list-2"
    assert attempted_ids == ["retry-list", "retry-list-2"]


def test_copy_watchlist_retries_when_slug_conflicts_during_insert(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.api.routes import watchlists as watchlists_route

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Retry Source", "description": None},
    )
    source_watchlist_id = created_watchlist.json()["watchlist_id"]

    original_duplicate = watchlists_route.watchlist_repository.duplicate
    generated_ids = iter(["retry-source-copy", "retry-source-copy-2"])
    attempted_ids: list[str] = []
    failed_once = False

    def _generate_retry_id(_session, _name: str) -> str:
        return next(generated_ids)

    def _duplicate(*args, **kwargs):
        nonlocal failed_once
        attempted_ids.append(kwargs["watchlist_id"])
        if not failed_once:
            failed_once = True
            raise IntegrityError("insert", {}, Exception("duplicate key value violates unique constraint"))
        return original_duplicate(*args, **kwargs)

    monkeypatch.setattr(watchlists_route, "_generate_watchlist_id", _generate_retry_id)
    monkeypatch.setattr(watchlists_route.watchlist_repository, "duplicate", _duplicate)

    response = client.post(f"/api/watchlists/{source_watchlist_id}/copy")
    assert response.status_code == 200
    assert response.json()["watchlist_id"] == "retry-source-copy-2"
    assert attempted_ids == ["retry-source-copy", "retry-source-copy-2"]


def test_create_watchlist_view_retries_when_slug_conflicts_during_insert(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.api.routes import watchlists as watchlists_route

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Retry View Parent", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    original_create_view = watchlists_route.watchlist_repository.create_view
    generated_ids = iter(["retry-view", "retry-view-2"])
    attempted_ids: list[str] = []
    failed_once = False

    def _generate_retry_id(_session, **_kwargs) -> str:
        return next(generated_ids)

    def _create_view(*args, **kwargs):
        nonlocal failed_once
        attempted_ids.append(kwargs["view_id"])
        if not failed_once:
            failed_once = True
            raise IntegrityError(
                "insert",
                {},
                Exception("duplicate key value violates unique constraint"),
            )
        return original_create_view(*args, **kwargs)

    monkeypatch.setattr(
        watchlists_route,
        "_generate_watchlist_view_id",
        _generate_retry_id,
    )
    monkeypatch.setattr(watchlists_route.watchlist_repository, "create_view", _create_view)

    response = client.post(
        f"/api/watchlists/{watchlist_id}/views",
        json={
            "name": "Retry View",
            "description": None,
            "default_group_by": "none",
            "default_sort": [],
            "default_filters": {},
            "default_advanced_filters": None,
            "columns": [
                {"field_key": "asset_name", "display_order": 1, "width": 320, "is_visible": True}
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["view_id"] == "retry-view-2"
    assert attempted_ids == ["retry-view", "retry-view-2"]


def test_screener_filters_match_multi_select_attribute_values(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Attribute Filters", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"asset_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    definition_response = client.post(
        "/api/instrument-attributes/definitions",
        json={
            "attribute_key": "strategy_tags",
            "label": "Strategy Tags",
            "description": "Test multi-select attribute",
            "data_type": "multi_select",
            "domain_code": "research",
            "group_code": "custom",
            "options": ["市场中性", "套利", "CTA"],
            "is_groupable": True,
            "is_filterable": True,
            "is_view_column": True,
            "default_visible": False,
        },
    )
    assert definition_response.status_code == 200

    value_response = client.post(
        "/api/instrument-attributes/assets/sxv264",
        json={
            "values": [
                {
                    "attribute_key": "strategy_tags",
                    "value": ["市场中性", "套利"],
                }
            ]
        },
    )
    assert value_response.status_code == 200

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": ["asset_name", "attr.strategy_tags"],
            "filters": {"attr.strategy_tags": ["市场中性"]},
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    payload = screener.json()
    assert payload["total_rows"] == 1
    assert payload["rows"][0]["attr.strategy_tags"] == ["市场中性", "套利"]


def test_explicit_empty_filters_override_view_defaults(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Explicit Filters", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    first_add = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"asset_ids": ["fund-us-agg"]},
    )
    assert first_add.status_code == 200

    second_add = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"asset_ids": ["sxv264"]},
    )
    assert second_add.status_code == 200

    view_response = client.post(
        f"/api/watchlists/{watchlist_id}/views",
        json={
            "name": "Filtered View",
            "description": None,
            "default_group_by": "none",
            "default_sort": [],
            "default_filters": {"asset_name": ["iShares Core U.S. Aggregate Bond ETF"]},
            "default_advanced_filters": None,
            "columns": [
                {"field_key": "asset_name", "display_order": 1, "width": 320, "is_visible": True}
            ],
        },
    )
    assert view_response.status_code == 200
    view_id = view_response.json()["view_id"]

    filtered = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": view_id,
            "selected_fields": ["asset_name"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert filtered.status_code == 200
    assert filtered.json()["total_rows"] == 1

    unfiltered = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": view_id,
            "selected_fields": ["asset_name"],
            "filters": {},
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert unfiltered.status_code == 200
    assert unfiltered.json()["total_rows"] == 2


def test_legacy_catalog_module_is_removed() -> None:
    with pytest.raises(ModuleNotFoundError):
        __import__("watchlist_app.domain.catalog")


def test_watchlist_api_runs_without_legacy_catalog_module(
    client: TestClient,
) -> None:

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Catalog Independence", "description": None},
    )
    assert created_watchlist.status_code == 200
    watchlist_id = created_watchlist.json()["watchlist_id"]

    detail = client.get(f"/api/watchlists/{watchlist_id}")
    assert detail.status_code == 200
    assert detail.json()["watchlist_id"] == watchlist_id

    created_view = client.post(
        f"/api/watchlists/{watchlist_id}/views",
        json={
            "name": "Independent View",
            "description": None,
            "default_group_by": "none",
            "default_sort": [],
            "default_filters": {},
            "default_advanced_filters": None,
            "columns": [
                {"field_key": "asset_name", "display_order": 1, "width": 320, "is_visible": True}
            ],
        },
    )
    assert created_view.status_code == 200

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"asset_ids": ["fund-us-agg"]},
    )
    assert add_response.status_code == 200
    assert add_response.json()["accepted_count"] == 1

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": ["asset_name", "ticker_or_isin"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    assert screener.json()["total_rows"] == 1


def test_watchlist_child_routes_return_404_for_missing_watchlists(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.api.routes import watchlists as watchlists_route

    def _unexpected_registry_lookup(*args, **kwargs):
        raise AssertionError("shared registry lookup should not run for missing watchlists")

    monkeypatch.setattr(watchlists_route, "get_shared_instrument", _unexpected_registry_lookup)

    add_response = client.post(
        "/api/watchlists/missing/items",
        json={"asset_ids": ["fund-us-agg"]},
    )
    assert add_response.status_code == 404
    assert add_response.json()["detail"] == "Watchlist not found"

    delete_items_response = client.post(
        "/api/watchlists/missing/items/delete",
        json={"asset_ids": ["fund-us-agg"]},
    )
    assert delete_items_response.status_code == 404
    assert delete_items_response.json()["detail"] == "Watchlist not found"

    list_views_response = client.get("/api/watchlists/missing/views")
    assert list_views_response.status_code == 404
    assert list_views_response.json()["detail"] == "Watchlist not found"

    create_view_response = client.post(
        "/api/watchlists/missing/views",
        json={
            "name": "Ghost View",
            "description": None,
            "default_group_by": "none",
            "default_sort": [],
            "default_filters": {},
            "default_advanced_filters": None,
            "columns": [
                {"field_key": "asset_name", "display_order": 1, "width": 320, "is_visible": True}
            ],
        },
    )
    assert create_view_response.status_code == 404
    assert create_view_response.json()["detail"] == "Watchlist not found"


def test_screener_returns_404_for_missing_watchlists_and_views(client: TestClient) -> None:
    missing_watchlist = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": "missing",
            "view_id": "overview",
            "selected_fields": ["asset_name"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert missing_watchlist.status_code == 404
    assert missing_watchlist.json()["detail"] == "Watchlist not found"

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Missing View Guard", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    missing_view = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "not-a-view",
            "selected_fields": ["asset_name"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert missing_view.status_code == 404
    assert missing_view.json()["detail"] == "Watchlist view not found"


def test_delete_watchlist_removes_watchlist_read_model_rows(client: TestClient) -> None:
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.repositories.sqlalchemy.read_models import SQLAlchemyReadModelRepository

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Delete Cleanup", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"asset_ids": ["fund-us-agg"]},
    )
    assert add_response.status_code == 200

    delete_response = client.delete(f"/api/watchlists/{watchlist_id}")
    assert delete_response.status_code == 200

    session = get_session_factory()()
    try:
        repository = SQLAlchemyReadModelRepository()
        assert repository.list_watchlist_rows(session, watchlist_id) == []
    finally:
        session.close()


def test_instrument_attribute_routes_return_404_for_missing_assets(client: TestClient) -> None:
    get_response = client.get("/api/instrument-attributes/assets/missing-asset")
    assert get_response.status_code == 404
    assert get_response.json()["detail"] == "Asset not found"

    post_response = client.post(
        "/api/instrument-attributes/assets/missing-asset",
        json={
            "values": [
                {
                    "attribute_key": "coverage_status",
                    "value": "Watch",
                }
            ]
        },
    )
    assert post_response.status_code == 404
    assert post_response.json()["detail"] == "Asset not found"


def test_seeded_private_fund_watchlist_tags_are_available(client: TestClient) -> None:
    definitions_response = client.get("/api/instrument-attributes/definitions")
    assert definitions_response.status_code == 200
    definitions = definitions_response.json()
    definitions_by_key = {item["attribute_key"]: item for item in definitions}

    expected_keys = {
        "fund_regime",
        "fund_vehicle",
        "fund_category_l1",
        "fund_category_l2",
        "fund_category_l3",
        "implementation_style",
        "trading_universe",
        "alpha_source",
        "style_profile",
        "manager_assessment",
        "team_stability_assessment",
        "portfolio_construction",
        "capacity_bucket",
        "historical_delivery",
        "volatility_bucket",
        "drawdown_control",
        "equity_correlation_bucket",
        "preferred_regime",
        "weak_regime",
        "style_stability",
        "transparency_quality",
    }
    assert expected_keys.issubset(definitions_by_key.keys())
    assert definitions_by_key["fund_regime"]["options"] == ["公募", "私募"]
    assert definitions_by_key["fund_category_l1"]["domain_code"] == "classification"
    assert definitions_by_key["fund_category_l2"]["required_for_monitoring"] is True
    assert definitions_by_key["style_profile"]["group_code"] == "research_style"
    assert definitions_by_key["preferred_regime"]["data_type"] == "multi_select"

    field_registry_response = client.get("/api/field-registry")
    assert field_registry_response.status_code == 200
    fields = field_registry_response.json()["fields"]
    fields_by_key = {item["field_key"]: item for item in fields}

    assert "attr.fund_regime" in fields_by_key
    assert fields_by_key["attr.fund_regime"]["filter_mode"] == "multi_select"
    assert "attr.fund_category_l1" in fields_by_key
    assert fields_by_key["attr.fund_category_l1"]["filter_mode"] == "multi_select"
    assert fields_by_key["attr.fund_category_l1"]["group_mode"] == "discrete"
    assert fields_by_key["attr.fund_category_l1"]["category_code"] == "product_classification"
    assert fields_by_key["attr.coverage_status"]["product_scope_json"] == []
    assert fields_by_key["latest_quote"]["asset_scope_json"] == []
    assert fields_by_key["latest_quote"]["source_metric_code"] == "asset_chart_read_model.series.latest_quote"
    assert fields_by_key["latest_quote_date"]["data_type"] == "date"


def test_adding_funds_does_not_inject_product_framework_values(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Clean Product Framework", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"asset_ids": ["fund-us-agg", "sxv264"]},
    )
    assert add_response.status_code == 200

    public_response = client.get("/api/instrument-attributes/assets/fund-us-agg")
    assert public_response.status_code == 200
    public_values = public_response.json()["values"]
    assert "fund_regime" not in public_values
    assert "fund_vehicle" not in public_values
    assert "fund_category_l1" not in public_values
    assert "fund_category_l2" not in public_values
    assert "fund_category_l3" not in public_values
    assert "alpha_source" not in public_values

    private_response = client.get("/api/instrument-attributes/assets/sxv264")
    assert private_response.status_code == 200
    private_values = private_response.json()["values"]
    assert "fund_regime" not in private_values
    assert "fund_vehicle" not in private_values
    assert "fund_category_l1" not in private_values
    assert "fund_category_l2" not in private_values
    assert "fund_category_l3" not in private_values
    assert "alpha_source" not in private_values


def test_instrument_attributes_can_be_cleared_with_null_and_empty_list(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Attribute Clear State", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"asset_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    single_definition = client.post(
        "/api/instrument-attributes/definitions",
        json={
            "attribute_key": "tag_clear_single",
            "label": "Tag Clear Single",
            "description": "Single-select clear test",
            "data_type": "single_select",
            "domain_code": "research",
            "group_code": "custom",
            "options": ["低波", "高波"],
            "is_groupable": True,
            "is_filterable": True,
            "is_view_column": True,
            "default_visible": False,
        },
    )
    assert single_definition.status_code == 200

    multi_definition = client.post(
        "/api/instrument-attributes/definitions",
        json={
            "attribute_key": "tag_clear_multi",
            "label": "Tag Clear Multi",
            "description": "Multi-select clear test",
            "data_type": "multi_select",
            "domain_code": "research",
            "group_code": "custom",
            "options": ["市场中性", "CTA"],
            "is_groupable": True,
            "is_filterable": True,
            "is_view_column": True,
            "default_visible": False,
        },
    )
    assert multi_definition.status_code == 200

    first_upsert = client.post(
        "/api/instrument-attributes/assets/sxv264",
        json={
            "values": [
                {"attribute_key": "tag_clear_single", "value": "低波"},
                {"attribute_key": "tag_clear_multi", "value": ["市场中性", "CTA"]},
            ]
        },
    )
    assert first_upsert.status_code == 200

    cleared_upsert = client.post(
        "/api/instrument-attributes/assets/sxv264",
        json={
            "values": [
                {"attribute_key": "tag_clear_single", "value": None},
                {"attribute_key": "tag_clear_multi", "value": []},
            ]
        },
    )
    assert cleared_upsert.status_code == 200
    payload = cleared_upsert.json()
    assert payload["values"]["tag_clear_single"] is None
    assert payload["values"]["tag_clear_multi"] == []


def test_monitoring_dashboard_surfaces_missing_labels_quotes_and_open_recalc_jobs(
    client: TestClient,
) -> None:
    seed_shared_instrument(
        {
        "asset_id": "fund-no-data",
        "asset_name": "No Data Fund",
        "asset_type": "fund",
        "currency": "USD",
        "identifiers": [
            {
                "identifier_type": "ticker",
                "identifier_value": "NODATA",
                "is_primary": True,
            }
        ],
        "market_data": [],
        "lifecycle_state": {"status": "active"},
        },
    )

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Monitoring Coverage", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"asset_ids": ["sxv264", "fund-no-data"]},
    )
    assert add_response.status_code == 200
    assert add_response.json()["accepted_count"] == 2

    recalc_response = client.post("/api/recalc/assets/sxv264/performance")
    assert recalc_response.status_code == 200

    response = client.get("/api/monitoring/dashboard")
    assert response.status_code == 200
    payload = response.json()

    assert payload["overview"] == {
        "watchlist_count": 1,
        "unique_asset_count": 2,
        "needs_refresh_count": 1,
        "missing_quote_count": 1,
        "missing_label_count": 2,
        "open_recalc_job_count": 1,
        "failed_recalc_job_count": 0,
    }

    watchlist_summary = payload["watchlists"][0]
    assert watchlist_summary["watchlist_id"] == watchlist_id
    assert watchlist_summary["item_count"] == 2
    assert watchlist_summary["needs_refresh_count"] == 1
    assert watchlist_summary["missing_quote_count"] == 1
    assert watchlist_summary["missing_label_count"] == 2
    assert watchlist_summary["open_recalc_job_count"] == 1

    attention_asset = next(
        item
        for item in payload["needs_attention_assets"]
        if item["asset_id"] == "fund-no-data"
    )
    assert attention_asset["data_freshness_status"] == "pending_recalc"
    assert "needs_refresh" in attention_asset["issue_flags"]
    assert "missing_quote" in attention_asset["issue_flags"]

    missing_label_asset = next(
        item
        for item in payload["missing_label_assets"]
        if item["asset_id"] == "fund-no-data"
    )
    assert "fund_regime" in missing_label_asset["missing_attribute_keys"]
    assert "fund_category_l1" in missing_label_asset["missing_attribute_keys"]
    assert len(payload["missing_label_assets"]) == 2

    assert len(payload["open_recalc_jobs"]) == 1
    assert payload["open_recalc_jobs"][0]["asset_id"] == "sxv264"
    assert payload["open_recalc_jobs"][0]["job_type"] == "performance"
    assert payload["open_recalc_jobs"][0]["job_status"] == "queued"
    assert payload["open_recalc_jobs"][0]["primary_watchlist_id"] == watchlist_id
