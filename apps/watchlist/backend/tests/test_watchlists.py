from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import math
import statistics

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .conftest import TEST_SHARED_INSTRUMENTS, seed_shared_instrument


def test_peer_metric_percentile_uses_midrank_for_ties() -> None:
    from watchlist_app.services.canonical_recalc import _rank_metric_value

    tied = _rank_metric_value(
        value=1.0,
        samples=[("a", 1.0), ("b", 1.0), ("c", 1.0)],
        direction="higher",
    )
    assert tied["rank"] == 1
    assert tied["percentile"] == pytest.approx(50.0)
    assert tied["quartile"] == 2

    unique_best = _rank_metric_value(
        value=3.0,
        samples=[("a", 3.0), ("b", 2.0), ("c", 1.0)],
        direction="higher",
    )
    assert unique_best["rank"] == 1
    assert unique_best["percentile"] == pytest.approx(100.0)

    lower_is_better_tied = _rank_metric_value(
        value=1.0,
        samples=[("a", 1.0), ("b", 1.0), ("c", 1.0)],
        direction="lower",
    )
    assert lower_is_better_tied["percentile"] == pytest.approx(50.0)


def test_risk_metrics_annualize_from_actual_observation_spacing() -> None:
    from watchlist_app.services.canonical_recalc import _compute_sharpe, _compute_volatility

    nav_points = [
        {"as_of_date": date(2026, 1, 1), "value": 100.0},
        {"as_of_date": date(2026, 1, 8), "value": 101.0},
        {"as_of_date": date(2026, 1, 15), "value": 99.0},
        {"as_of_date": date(2026, 1, 22), "value": 102.0},
    ]
    returns = [
        nav_points[index]["value"] / nav_points[index - 1]["value"] - 1
        for index in range(1, len(nav_points))
    ]
    periods_per_year = len(returns) / 21 * 365.25
    expected_volatility = statistics.stdev(returns) * math.sqrt(periods_per_year) * 100
    expected_sharpe = statistics.fmean(returns) / statistics.stdev(returns) * math.sqrt(periods_per_year)

    assert _compute_volatility(nav_points) == pytest.approx(expected_volatility, abs=1e-12)
    assert _compute_sharpe(nav_points) == pytest.approx(expected_sharpe, abs=1e-12)


def test_calculation_frequency_context_resamples_declared_weekly_points() -> None:
    from watchlist_app.services.calculation_frequency import build_calculation_frequency_context

    nav_points = [
        {"as_of_date": date(2026, 1, 5), "value": 100.0, "frequency": "daily"},
        {"as_of_date": date(2026, 1, 6), "value": 101.0, "frequency": "daily"},
        {"as_of_date": date(2026, 1, 9), "value": 102.0, "frequency": "weekly"},
        {"as_of_date": date(2026, 1, 12), "value": 103.0, "frequency": "daily"},
        {"as_of_date": date(2026, 1, 16), "value": 104.0, "frequency": "weekly"},
    ]

    context = build_calculation_frequency_context(nav_points)

    assert context["profile"]["resolved_frequency"] == "weekly"
    assert context["profile"]["raw_observation_count"] == 5
    assert context["profile"]["observation_count"] == 2
    assert [point["as_of_date"] for point in context["points"]] == [
        date(2026, 1, 9),
        date(2026, 1, 16),
    ]


def test_return_nav_basis_prefers_cumulative_nav_when_available() -> None:
    from watchlist_app.services.canonical_recalc import _group_shared_nav_rows, _select_nav_basis_rows

    rows = _group_shared_nav_rows(
        [
            {
                "quote_basis": "official_nav",
                "as_of_date": "2026-01-01",
                "value": "1.000000",
                "currency": "CNY",
            },
            {
                "quote_basis": "CUMULATIVE_NAV",
                "as_of_date": "2026-01-01",
                "value": "1.250000",
                "currency": "CNY",
            },
            {
                "quote_basis": "official_nav",
                "as_of_date": "2026-01-08",
                "value": "1.010000",
                "currency": "CNY",
            },
            {
                "quote_basis": "CUMULATIVE_NAV",
                "as_of_date": "2026-01-08",
                "value": "1.262500",
                "currency": "CNY",
            },
        ]
    )

    selection = _select_nav_basis_rows(rows, preference="auto")

    assert selection["nav_basis_type"] == "nav_with_dividend"
    assert [point["value"] for point in selection["points"]] == [1.25, 1.2625]


def test_return_nav_basis_does_not_fallback_to_ordinary_nav() -> None:
    from watchlist_app.services.canonical_recalc import _group_shared_nav_rows, _select_nav_basis_rows

    rows = _group_shared_nav_rows(
        [
            {
                "quote_basis": "official_nav",
                "as_of_date": "2026-01-01",
                "value": "1.000000",
                "currency": "CNY",
            },
            {
                "quote_basis": "official_nav",
                "as_of_date": "2026-01-08",
                "value": "1.010000",
                "currency": "CNY",
            },
        ]
    )

    auto_selection = _select_nav_basis_rows(rows, preference="auto")
    explicit_nav_selection = _select_nav_basis_rows(rows, preference="nav")
    quote_selection = _select_nav_basis_rows(rows, preference="auto", allow_ordinary_nav=True)

    assert auto_selection["nav_basis_type"] is None
    assert auto_selection["points"] == []
    assert explicit_nav_selection["nav_basis_type"] is None
    assert explicit_nav_selection["points"] == []
    assert quote_selection["nav_basis_type"] == "nav"
    assert [point["value"] for point in quote_selection["points"]] == [1.0, 1.01]


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
        "instrument_name",
        "attr.coverage_status",
        "price_chart_1m",
        "latest_quote",
        "latest_quote_date",
        "return_1w",
        "return_mtd",
        "return_ytd",
        "attr.current_drawdown",
    ]
    fund_screening_view = next(
        item
        for item in detail_payload["views"]
        if item["view_id"] == "fund-screening"
    )
    assert fund_screening_view["name"] == "产品分类筛选"
    assert fund_screening_view["default_group_by"] == "attr.fund_taxonomy_level_1"
    assert fund_screening_view["default_filters"] == {
        "instrument_type": ["fund", "index"],
    }
    assert fund_screening_view["columns"] == [
        "instrument_name",
        "attr.implementation_style",
        "attr.style_profile",
        "attr.manager_assessment",
        "attr.volatility_bucket",
        "attr.drawdown_control",
        "attr.style_stability",
        "attr.transparency_quality",
        "data_freshness_status",
    ]


def test_empty_watchlist_still_exposes_fund_field_scope(
    client: TestClient,
) -> None:
    created = client.post(
        "/api/watchlists",
        json={"name": "Empty Fund Scope", "description": None},
    )
    assert created.status_code == 200
    watchlist_id = created.json()["watchlist_id"]

    detail = client.get(f"/api/watchlists/{watchlist_id}")

    assert detail.status_code == 200
    payload = detail.json()
    assert payload["item_count"] == 0
    assert "taxonomy" in [item["code"] for item in payload["available_group_bys"]]
    assert "attr.coverage_status" in payload["default_filters_summary"]


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
        json={"instrument_ids": ["fund-us-agg"]},
    )
    assert add_response.status_code == 200
    assert add_response.json()["accepted_count"] == 1

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": ["ticker_or_isin", "instrument_name", "overall_rating"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    payload = screener.json()
    assert payload["total_rows"] == 1
    assert payload["rows"][0]["instrument_name"] == "iShares Core U.S. Aggregate Bond ETF"
    assert payload["rows"][0]["ticker_or_isin"] == "AGG"


def test_adding_index_shared_registry_instrument_is_supported(
    client: TestClient,
) -> None:
    seed_shared_instrument(
        {
            "instrument_id": "index-csi-300",
            "instrument_name": "CSI 300 Index",
            "instrument_type": "index",
            "currency": "CNY",
            "identifiers": [
                {"identifier_type": "ticker", "identifier_value": "000300", "is_primary": True},
            ],
            "market_data": [
                {
                    "metric_family": "price",
                    "quote_basis": "close",
                    "as_of_date": "2026-04-15",
                    "value": "3600.1200",
                    "currency": "CNY",
                    "status": "complete",
                }
            ],
            "lifecycle_state": {"status": "active"},
        }
    )
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Index Watch", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["index-csi-300"]},
    )
    assert add_response.status_code == 200

    resolve_response = client.get("/api/instruments/index-csi-300/resolve")
    assert resolve_response.status_code == 200
    payload = resolve_response.json()
    assert payload["detail_supported"] is True
    assert payload["detail_view_type"] == "index"
    assert payload["detail_subject_id"] == "index-csi-300"

    tree_response = client.get("/api/taxonomies/fund-taxonomy")
    assert tree_response.status_code == 200
    tree_payload = tree_response.json()
    assert tree_payload["instrument_types"] == ["fund", "index"]
    assert "index" in {node["node_id"] for node in tree_payload["nodes"]}

    taxonomy_response = client.get("/api/taxonomies/fund-taxonomy/instruments/index-csi-300")
    assert taxonomy_response.status_code == 200
    taxonomy_payload = taxonomy_response.json()
    assert taxonomy_payload["taxonomy_code"] == "fund_taxonomy"
    assert taxonomy_payload["assigned_node_id"] is None
    assert taxonomy_payload["derived_values"] == {}

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": ["instrument_name", "instrument_type"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    screener_payload = screener.json()
    assert screener_payload["rows"][0]["instrument_type"] == "index"

    screening_screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "fund-screening",
            "selected_fields": ["instrument_name", "instrument_type"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screening_screener.status_code == 200
    assert screening_screener.json()["rows"][0]["instrument_type"] == "index"


def test_adding_unsupported_shared_registry_instrument_is_rejected(
    client: TestClient,
) -> None:
    seed_shared_instrument(
        {
            "instrument_id": "equity-demo",
            "instrument_name": "Demo Equity",
            "instrument_type": "equity",
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
        json={"name": "Unsupported Type", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["equity-demo"]},
    )

    assert add_response.status_code == 400
    assert "fund and index instruments only" in add_response.json()["detail"]


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
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    move_response = client.post(
        f"/api/watchlists/{source_watchlist_id}/items/move",
        json={"instrument_ids": ["sxv264"], "target_watchlist_id": target_watchlist_id},
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
            "selected_fields": ["instrument_name"],
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
            "selected_fields": ["instrument_name", "latest_quote", "latest_quote_date"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert target_rows.status_code == 200
    target_payload = target_rows.json()
    assert target_payload["total_rows"] == 1
    assert target_payload["rows"][0]["instrument_name"] == "SXV264 Total Return Fund"
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
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    copy_response = client.post(
        f"/api/watchlists/{source_watchlist_id}/items/copy",
        json={"instrument_ids": ["sxv264"], "target_watchlist_id": target_watchlist_id},
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
            "selected_fields": ["instrument_name"],
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
            "selected_fields": ["instrument_name", "latest_quote", "latest_quote_date"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert target_rows.status_code == 200
    target_payload = target_rows.json()
    assert target_payload["total_rows"] == 1
    assert target_payload["rows"][0]["instrument_name"] == "SXV264 Total Return Fund"
    assert target_payload["rows"][0]["latest_quote"] == pytest.approx(101.2365, abs=1e-6)
    assert target_payload["rows"][0]["latest_quote_date"] == "2026-04-14"


def test_move_watchlist_items_rejects_instruments_missing_from_source(
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
        json={"instrument_ids": ["sxv264"], "target_watchlist_id": target_watchlist_id},
    )
    assert move_response.status_code == 400
    assert move_response.json()["detail"] == "Instruments not found in source watchlist: sxv264."

    target_rows = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": target_watchlist_id,
            "view_id": "overview",
            "selected_fields": ["instrument_name"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert target_rows.status_code == 200
    assert target_rows.json()["total_rows"] == 0


def test_copy_watchlist_items_rejects_instruments_missing_from_source(
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
        json={"instrument_ids": ["sxv264"], "target_watchlist_id": target_watchlist_id},
    )
    assert copy_response.status_code == 400
    assert copy_response.json()["detail"] == "Instruments not found in source watchlist: sxv264."

    target_rows = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": target_watchlist_id,
            "view_id": "overview",
            "selected_fields": ["instrument_name"],
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
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200
    assert add_response.json()["accepted_count"] == 1
    assert add_response.json()["recalculated_instrument_ids"] == ["sxv264"]

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": [
                "ticker_or_isin",
                "instrument_name",
                "latest_quote",
                "latest_quote_date",
                "last_nav_date",
                "data_freshness_status",
                "return_1w",
                "return_mtd",
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
    assert payload["rows"][0]["return_mtd"] == pytest.approx(2.259067, abs=1e-6)
    assert payload["rows"][0]["annualized_return"] == pytest.approx(14.119462, abs=1e-6)
    assert payload["snapshot_metadata"]["as_of_date"] == "2026-04-14"


def test_adding_existing_watchlist_item_is_noop_without_membership_recalc(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.api.routes import watchlists as watchlists_route

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Duplicate Add Guard", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    first_add = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert first_add.status_code == 200
    assert first_add.json()["accepted_count"] == 1

    def _unexpected_recalc(*_args, **_kwargs):
        raise AssertionError("duplicate membership add should not execute recalc")

    monkeypatch.setattr(
        watchlists_route.canonical_recalc_service,
        "execute_recalc",
        _unexpected_recalc,
    )

    duplicate_add = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )

    assert duplicate_add.status_code == 200
    assert duplicate_add.json() == {
        "watchlist_id": watchlist_id,
        "accepted_count": 0,
        "pending_recalc_instrument_ids": [],
        "recalculated_instrument_ids": [],
    }


def test_screener_sort_keeps_missing_values_last_for_descending_metrics(
    client: TestClient,
) -> None:
    seed_shared_instrument(
        {
            "instrument_id": "fund-no-return",
            "instrument_name": "No Return Fund",
            "instrument_type": "fund",
            "currency": "USD",
            "identifiers": [
                {"identifier_type": "ticker", "identifier_value": "NORET", "is_primary": True},
            ],
            "market_data": [],
            "lifecycle_state": {"status": "active"},
        }
    )
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Sort Missing Returns", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["fund-no-return", "sxv264"]},
    )
    assert add_response.status_code == 200

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": ["instrument_name", "return_ytd"],
            "sort": [{"field": "return_ytd", "direction": "desc"}],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )

    assert screener.status_code == 200
    rows = screener.json()["rows"]
    assert [row["instrument_id"] for row in rows] == ["sxv264", "fund-no-return"]
    assert rows[0]["return_ytd"] is not None
    assert rows[1]["return_ytd"] is None


def test_calendar_period_returns_use_prior_close_as_base(
    client: TestClient,
) -> None:
    seed_shared_instrument(
        {
            "instrument_id": "calendar-boundary-fund",
            "instrument_name": "Calendar Boundary Fund",
            "instrument_type": "fund",
            "currency": "USD",
            "identifiers": [
                {"identifier_type": "ticker", "identifier_value": "CBF", "is_primary": True},
            ],
            "market_data": [
                {
                    "metric_family": "nav",
                    "quote_basis": "cumulative_nav",
                    "as_of_date": as_of_date,
                    "value": value,
                    "currency": "USD",
                    "status": "complete",
                }
                for as_of_date, value in (
                    ("2025-12-31", "100.000000"),
                    ("2026-01-01", "110.000000"),
                    ("2026-03-31", "120.000000"),
                    ("2026-04-01", "130.000000"),
                    ("2026-04-14", "156.000000"),
                )
            ],
            "lifecycle_state": {"status": "active"},
        }
    )
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Calendar Return Coverage", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["calendar-boundary-fund"]},
    )
    assert add_response.status_code == 200

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": ["ticker_or_isin", "return_mtd", "return_ytd"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    row = screener.json()["rows"][0]
    assert row["ticker_or_isin"] == "CBF"
    assert row["return_mtd"] == pytest.approx(30.0, abs=1e-6)
    assert row["return_ytd"] == pytest.approx(56.0, abs=1e-6)


def test_index_close_series_calculates_watchlist_performance_metrics(
    client: TestClient,
) -> None:
    seed_shared_instrument(
        {
            "instrument_id": "index-close-only",
            "instrument_name": "Close Only Index",
            "instrument_type": "index",
            "currency": "USD",
            "identifiers": [
                {
                    "identifier_type": "ticker",
                    "identifier_value": "CLOSEIDX",
                    "is_primary": True,
                },
            ],
            "market_data": [
                {
                    "metric_family": "price",
                    "quote_basis": "close",
                    "as_of_date": as_of_date,
                    "value": value,
                    "currency": "USD",
                    "status": "complete",
                }
                for as_of_date, value in (
                    ("2025-12-31", "100.000000"),
                    ("2026-03-15", "110.000000"),
                    ("2026-04-01", "115.000000"),
                    ("2026-04-15", "121.000000"),
                )
            ],
            "lifecycle_state": {"status": "active"},
        }
    )
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Index Return Coverage", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["index-close-only"]},
    )
    assert add_response.status_code == 200
    assert add_response.json()["recalculated_instrument_ids"] == ["index-close-only"]

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": [
                "ticker_or_isin",
                "instrument_type",
                "return_ytd",
                "return_mtd",
                "return_1m",
                "annualized_return",
                "max_drawdown",
                "volatility",
                "sharpe_ratio",
                "attr.current_drawdown",
            ],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    row = screener.json()["rows"][0]
    assert row["ticker_or_isin"] == "CLOSEIDX"
    assert row["instrument_type"] == "index"
    assert row["return_ytd"] == pytest.approx(21.0, abs=1e-6)
    assert row["return_mtd"] == pytest.approx(10.0, abs=1e-6)
    assert row["return_1m"] == pytest.approx(10.0, abs=1e-6)
    assert row["annualized_return"] is not None
    assert row["max_drawdown"] == pytest.approx(0.0, abs=1e-6)
    assert row["volatility"] is not None
    assert row["sharpe_ratio"] is not None
    assert row["attr.current_drawdown"] == pytest.approx(0.0, abs=1e-6)

    field_registry = client.get("/api/field-registry", params={"instrument_type": "index"})
    assert field_registry.status_code == 200
    index_field_keys = {item["field_key"] for item in field_registry.json()["fields"]}
    assert {
        "return_ytd",
        "return_mtd",
        "return_1m",
        "annualized_return",
        "max_drawdown",
        "volatility",
        "sharpe_ratio",
    }.issubset(index_field_keys)


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
        json={"instrument_ids": ["sxv264"]},
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
    assert trailing_by_window["MTD"]["investment_nav"] == pytest.approx(2.259067, abs=1e-6)
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
    assert risk_payload["calculation_frequency_profile"]["resolved_frequency"] == "daily"
    assert risk_payload["calculation_frequency_profile"]["gap_count"] > 0
    assert performance_payload["calculation_frequency_profile"]["resolved_frequency"] == "daily"


def test_instrument_detail_payload_exposes_weekly_calculation_frequency(
    client: TestClient,
) -> None:
    seed_shared_instrument(
        {
            "instrument_id": "weekly-risk-fund",
            "instrument_name": "Weekly Risk Fund",
            "instrument_type": "fund",
            "currency": "USD",
            "identifiers": [
                {"identifier_type": "ticker", "identifier_value": "WRF", "is_primary": True},
            ],
            "market_data": [
                {
                    "metric_family": "nav",
                    "quote_basis": "cumulative_nav",
                    "as_of_date": as_of_date,
                    "value": value,
                    "currency": "USD",
                    "frequency": "weekly",
                    "status": "complete",
                }
                for as_of_date, value in zip(
                    ["2026-01-02", "2026-01-09", "2026-01-16", "2026-01-23"],
                    ["100.000000", "101.000000", "99.500000", "102.000000"],
                )
            ],
            "lifecycle_state": {"status": "active"},
        }
    )
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Weekly Frequency Coverage", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["weekly-risk-fund"]},
    )
    assert add_response.status_code == 200

    nav_response = client.get("/api/instruments/weekly-risk-fund/nav-series")
    assert nav_response.status_code == 200
    nav_payload = nav_response.json()
    assert nav_payload["calculation_frequency_profile"]["resolved_frequency"] == "weekly"
    assert [point["date"] for point in nav_payload["calculation_series"]] == [
        "2026-01-02",
        "2026-01-09",
        "2026-01-16",
        "2026-01-23",
    ]

    risk_response = client.get("/api/instruments/weekly-risk-fund/risk")
    assert risk_response.status_code == 200
    risk_payload = risk_response.json()
    assert risk_payload["snapshot_metadata"]["methodology_version"] == "canonical-risk/v3"
    assert risk_payload["calculation_frequency_profile"]["resolved_frequency"] == "weekly"
    assert risk_payload["calculation_frequency_profile"]["annualization_periods_per_year"] == pytest.approx(
        52.178571,
        abs=1e-6,
    )


def test_instrument_performance_payload_includes_taxonomy_peer_ranking(
    client: TestClient,
) -> None:
    for instrument_id, instrument_name, ticker, values in (
        (
            "peer-strong",
            "Peer Strong Fund",
            "PSTR",
            ["97.500000", "100.000000", "103.000000", "105.000000"],
        ),
        (
            "peer-weak",
            "Peer Weak Fund",
            "PWK",
            ["100.000000", "99.000000", "98.000000", "99.000000"],
        ),
        (
            "peer-archived",
            "Peer Archived Fund",
            "POLD",
            ["90.000000", "110.000000", "125.000000", "140.000000"],
        ),
    ):
        seed_shared_instrument(
            {
                "instrument_id": instrument_id,
                "instrument_name": instrument_name,
                "instrument_type": "fund",
                "currency": "USD",
                "identifiers": [
                    {"identifier_type": "ticker", "identifier_value": ticker, "is_primary": True},
                ],
                "market_data": [
                    {
                        "metric_family": "nav",
                        "quote_basis": "cumulative_nav",
                        "as_of_date": as_of_date,
                        "value": value,
                        "currency": "USD",
                        "status": "complete",
                    }
                    for as_of_date, value in zip(
                        ["2025-12-31", "2026-03-14", "2026-04-07", "2026-04-14"],
                        values,
                    )
                ],
                "lifecycle_state": {"status": "active"},
            }
        )

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Peer Ranking Coverage", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264", "peer-strong", "peer-weak", "peer-archived"]},
    )
    assert add_response.status_code == 200

    for instrument_id in ("sxv264", "peer-strong", "peer-weak", "peer-archived"):
        update_response = client.put(
            f"/api/taxonomies/fund-taxonomy/instruments/{instrument_id}",
            json={"node_id": "fund-private-equity-quant-long-500", "updated_by": "test"},
        )
        assert update_response.status_code == 200

    recalc_archived_peer_response = client.post(
        "/api/recalc/instruments/peer-archived/execute",
        json={
            "job_type": "performance",
            "trigger_type": "test",
            "trigger_ref_type": "taxonomy_peer_ranking",
            "trigger_ref_id": "peer-archived",
        },
    )
    assert recalc_archived_peer_response.status_code == 200

    from yungu_instrument_core import instrument_store as shared_store
    from watchlist_app.db import session as session_module

    shared_store.archive_instrument(
        session_module.get_session_factory(),
        instrument_id="peer-archived",
        updated_by="test",
    )
    with session_module.get_session_factory()() as session:
        session.execute(
            text(
                """
                UPDATE instrument_detail
                   SET is_active = false
                 WHERE instrument_id = 'peer-archived'
                """
            )
        )
        session.commit()

    recalc_response = client.post(
        "/api/recalc/instruments/sxv264/execute",
        json={
            "job_type": "performance",
            "trigger_type": "test",
            "trigger_ref_type": "taxonomy_peer_ranking",
            "trigger_ref_id": "sxv264",
        },
    )
    assert recalc_response.status_code == 200

    performance_response = client.get("/api/instruments/sxv264/performance")
    assert performance_response.status_code == 200
    payload = performance_response.json()
    peer_comparison = payload["peer_comparison"]
    assert peer_comparison["status"] == "ready"
    assert peer_comparison["peer_node_id"] == "fund-private-equity-quant-long-500"
    assert peer_comparison["sample_count"] == 3
    assert payload["ranking"]["sample_count"] == 3
    assert payload["ranking"]["rank"] == 2
    assert payload["ranking"]["quartile"] == 2

    metrics_by_key = {row["metric_key"]: row for row in peer_comparison["metrics"]}
    assert metrics_by_key["annualized_return"]["rank"] == 2
    assert metrics_by_key["annualized_return"]["percentile"] == pytest.approx(50.0)
    trailing_by_window = {row["window"]: row for row in payload["trailing_returns"]}
    assert trailing_by_window["Ann."]["category_nav"] is not None

    screener_response = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "fund-screening",
            "selected_fields": [
                "instrument_name",
                "attr.peer_group",
                "attr.peer_sample_count",
                "attr.peer_return_1w_percentile",
                "attr.peer_return_1m_percentile",
                "attr.peer_annualized_return_percentile",
            ],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener_response.status_code == 200
    sxv_row = next(
        row
        for row in screener_response.json()["rows"]
        if row["instrument_id"] == "sxv264"
    )
    assert sxv_row["attr.peer_group"] == "私募 / 股票策略 / 量化多头 / 500指增"
    assert sxv_row["attr.peer_sample_count"] == 3
    assert sxv_row["attr.peer_return_1w_percentile"] == pytest.approx(50.0)
    assert sxv_row["attr.peer_return_1m_percentile"] == pytest.approx(50.0)
    assert sxv_row["attr.peer_annualized_return_percentile"] == pytest.approx(50.0)


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
        json={"instrument_ids": ["sxv264", "savf63", "fund-us-agg"]},
    )
    assert add_response.status_code == 200

    initial_response = client.get("/api/instruments/sxv264/nav-settings")
    assert initial_response.status_code == 200
    assert initial_response.json() == {
        "nav_basis_preference": "auto",
        "default_benchmark_instrument_id": None,
        "peer_baseline_instrument_ids": [],
    }

    update_response = client.put(
        "/api/instruments/sxv264/nav-settings",
        json={
            "nav_basis_preference": "nav_with_dividend",
            "default_benchmark_instrument_id": "savf63",
            "peer_baseline_instrument_ids": ["fund-us-agg", "savf63", "sxv264", "fund-us-agg"],
            "updated_by": "test-suite",
        },
    )
    assert update_response.status_code == 200
    assert update_response.json() == {
        "nav_basis_preference": "nav_with_dividend",
        "default_benchmark_instrument_id": "savf63",
        "peer_baseline_instrument_ids": ["fund-us-agg", "savf63"],
    }

    nav_series_response = client.get("/api/instruments/sxv264/nav-series")
    assert nav_series_response.status_code == 200
    nav_series_payload = nav_series_response.json()
    assert nav_series_payload["nav_basis_preference"] == "nav_with_dividend"
    assert nav_series_payload["compare_settings"] == {
        "default_benchmark_instrument_id": "savf63",
        "peer_instrument_ids": ["fund-us-agg", "savf63"],
    }

    clear_response = client.put(
        "/api/instruments/sxv264/nav-settings",
        json={
            "default_benchmark_instrument_id": None,
            "peer_baseline_instrument_ids": [],
            "updated_by": "test-suite",
        },
    )
    assert clear_response.status_code == 200
    assert clear_response.json()["default_benchmark_instrument_id"] is None
    assert clear_response.json()["peer_baseline_instrument_ids"] == []


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
        json={"instrument_ids": ["sxv264", "savf63"]},
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
            "selected_fields": ["instrument_name"],
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
        json={"instrument_ids": ["not-in-registry"]},
    )
    assert add_response.status_code == 404
    assert "Database Dashboard" in add_response.json()["detail"]


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
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    scheduled: list[dict[str, object]] = []

    monkeypatch.setattr(
        screener_route,
        "schedule_instrument_refresh_if_stale",
        lambda **kwargs: scheduled.append(kwargs) or True,
    )

    response = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": ["instrument_name", "latest_quote", "latest_quote_date", "last_nav_date"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )

    assert response.status_code == 200
    assert scheduled == [
        {
            "instrument_id": "sxv264",
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
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    scheduled: list[dict[str, object]] = []

    monkeypatch.setattr(
        read_model_freshness,
        "get_shared_instrument",
        lambda instrument_id: {
            "instrument_id": instrument_id,
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
            "instrument_id": "sxv264",
            "trigger_ref_type": "instrument_summary_read",
            "trigger_ref_id": "sxv264",
        }
    ]


def test_stale_read_repair_enqueues_single_durable_recalc_job(client: TestClient) -> None:
    from watchlist_app.db import session as session_module
    from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
    from watchlist_app.services.read_model_freshness import schedule_instrument_refresh_if_stale

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Durable Freshness Repair", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    first = schedule_instrument_refresh_if_stale(
        instrument_id="sxv264",
        local_latest_date=date(2026, 4, 13),
        trigger_ref_type="instrument_summary_read",
        trigger_ref_id="sxv264",
    )
    second = schedule_instrument_refresh_if_stale(
        instrument_id="sxv264",
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
        if job.instrument_id == "sxv264"
        and job.job_type == "all"
        and job.trigger_type == "stale_read_repair"
        and job.trigger_ref_type == "instrument_summary_read"
        and job.trigger_ref_id == "sxv264"
    ]
    assert len(matching) == 1
    assert matching[0].job_status == "queued"


def test_stale_read_repair_commits_requeued_existing_job(client: TestClient) -> None:
    from watchlist_app.db import session as session_module
    from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
    from watchlist_app.services.read_model_freshness import schedule_instrument_refresh_if_stale
    from watchlist_app.services.recalc_job_ids import make_recalc_dedupe_key

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Stale Repair Requeue", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    session_factory = session_module.get_session_factory()
    repo = SQLAlchemyRecalcJobRepository()
    with session_factory() as session:
        record = repo.create(
            session,
            recalc_job_id="stale-repair-running",
            job_type="all",
            instrument_id="sxv264",
            trigger_type="stale_read_repair",
            trigger_ref_type="instrument_summary_read",
            trigger_ref_id="sxv264",
            job_status="running",
            priority=95,
            dedupe_key=make_recalc_dedupe_key(
                job_type="all",
                instrument_id="sxv264",
                trigger_type="stale_read_repair",
                trigger_ref_type="instrument_summary_read",
                trigger_ref_id="sxv264",
            ),
            payload_json={"requested_by": "stale_read_repair"},
        )
        record.started_at = datetime.now(UTC) - timedelta(seconds=600)
        session.commit()

    scheduled = schedule_instrument_refresh_if_stale(
        instrument_id="sxv264",
        local_latest_date=date(2026, 4, 13),
        trigger_ref_type="instrument_summary_read",
        trigger_ref_id="sxv264",
    )

    assert scheduled is True

    with session_factory() as session:
        record = repo.get(session, "stale-repair-running")

    assert record is not None
    assert record.job_status == "queued"
    assert record.started_at is None
    assert record.error_message == "Recovered stale running job after worker interruption."


def test_manual_recalc_enqueue_reuses_open_dedupe_job(client: TestClient) -> None:
    from watchlist_app.db import session as session_module
    from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Manual Recalc Dedupe", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    first = client.post("/api/recalc/instruments/sxv264/performance")
    second = client.post("/api/recalc/instruments/sxv264/performance")

    assert first.status_code == 200
    assert second.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    assert second_payload["recalc_job_id"] == first_payload["recalc_job_id"]
    assert second_payload["dedupe_key"] == first_payload["dedupe_key"]

    session_factory = session_module.get_session_factory()
    with session_factory() as session:
        jobs = list(SQLAlchemyRecalcJobRepository().list_recent(session))

    matching = [
        job
        for job in jobs
        if job.instrument_id == "sxv264"
        and job.job_type == "performance"
        and job.trigger_type == "manual_api"
        and job.trigger_ref_type == "api_request"
    ]
    assert len(matching) == 1


def test_manual_recalc_enqueue_returns_existing_job_after_dedupe_race(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.api.routes import recalc as recalc_route
    from watchlist_app.db import session as session_module
    from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
    from watchlist_app.services.recalc_job_ids import make_recalc_dedupe_key

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Manual Recalc Race", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    repo = SQLAlchemyRecalcJobRepository()
    session_factory = session_module.get_session_factory()
    with session_factory() as session:
        repo.create(
            session,
            recalc_job_id="manual-race-existing",
            job_type="performance",
            instrument_id="sxv264",
            trigger_type="manual_api",
            trigger_ref_type="api_request",
            trigger_ref_id=None,
            job_status="queued",
            priority=85,
            dedupe_key=make_recalc_dedupe_key(
                job_type="performance",
                instrument_id="sxv264",
                trigger_type="manual_api",
                trigger_ref_type="api_request",
                trigger_ref_id=None,
            ),
            payload_json={"requested_by": "api"},
        )
        session.commit()

    original_find_open_job = recalc_route.recalc_repository.find_open_job
    find_calls = 0

    def _find_open_job(*args, **kwargs):
        nonlocal find_calls
        find_calls += 1
        if find_calls == 1:
            return None
        return original_find_open_job(*args, **kwargs)

    monkeypatch.setattr(recalc_route.recalc_repository, "find_open_job", _find_open_job)

    response = client.post("/api/recalc/instruments/sxv264/performance")

    assert response.status_code == 200
    assert response.json()["recalc_job_id"] == "manual-race-existing"
    assert find_calls == 2


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
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    seed_shared_instrument(
        {
            **TEST_SHARED_INSTRUMENTS["sxv264"],
            "instrument_name": "SXV264 Renamed Total Return Fund",
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
        if job.instrument_id == "sxv264"
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
        if job.instrument_id == "sxv264"
        and job.trigger_type == "stale_read_repair"
        and job.job_status == "completed"
    ]
    assert len(completed_jobs) == 1

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": ["instrument_name", "ticker_or_isin"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    payload = screener.json()
    assert payload["rows"][0]["instrument_name"] == "SXV264 Renamed Total Return Fund"
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
        json={"instrument_ids": ["sxv264"]},
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
            instrument_id="sxv264",
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
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    response = client.get("/api/funds/sxv264/summary")

    assert response.status_code == 404


def test_instruments_library_alias_lists_local_instruments(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Library Alias", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    response = client.get("/api/instruments/library")

    assert response.status_code == 200
    assert any(item["instrument_id"] == "sxv264" for item in response.json())


def test_execute_recalc_returns_404_for_unknown_asset(client: TestClient) -> None:
    response = client.post(
        "/api/recalc/instruments/not-in-watchlist/execute",
        json={
            "job_type": "all",
            "trigger_type": "instrument_registry_write",
            "trigger_ref_type": "instrument_nav_history_replace",
            "trigger_ref_id": "2026-04-16",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Instrument not found: not-in-watchlist"


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
        lambda instrument_id: {
            "instrument_id": instrument_id,
            "instrument_name": "Retired Asset",
            "instrument_type": "fund",
            "currency": "USD",
            "lifecycle_state": {"status": "archived"},
            "identifiers": [],
        },
    )

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["fund-archived"]},
    )
    assert add_response.status_code == 404
    assert "Database Dashboard" in add_response.json()["detail"]


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

    def _raise_registry_transport_error(instrument_id: str):
        raise SharedInstrumentRegistryTransportError("Failed to reach shared instrument registry.")

    monkeypatch.setattr(watchlists_route, "get_shared_instrument", _raise_registry_transport_error)

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["fund-us-agg"]},
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
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    def _raise_registry_error(instrument_id: str):
        raise SharedInstrumentRegistryHttpError(
            status_code=500,
            message="Shared instrument registry returned HTTP 500.",
        )

    monkeypatch.setattr(instrument_resolution, "get_shared_instrument", _raise_registry_error)

    response = client.get("/api/instruments/sxv264/resolve")
    assert response.status_code == 200
    payload = response.json()
    assert payload["requested_instrument_id"] == "sxv264"
    assert payload["canonical_instrument_id"] == "sxv264"
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
        "instrument_id": "fund-msft-strategy",
        "instrument_name": "Microsoft Strategy Fund",
        "instrument_type": "fund",
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
        lambda instrument_id: fund_record if instrument_id == "fund-msft-strategy" else None,
    )
    monkeypatch.setattr(
        instrument_resolution,
        "get_shared_instrument",
        lambda instrument_id: fund_record if instrument_id == "fund-msft-strategy" else None,
    )

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["fund-msft-strategy"]},
    )
    assert add_response.status_code == 200

    def _raise_registry_error(instrument_id: str):
        raise SharedInstrumentRegistryHttpError(
            status_code=500,
            message="Shared instrument registry returned HTTP 500.",
        )

    monkeypatch.setattr(instrument_resolution, "get_shared_instrument", _raise_registry_error)

    response = client.get("/api/instruments/fund-msft-strategy/resolve")
    assert response.status_code == 200
    payload = response.json()
    assert payload["requested_instrument_id"] == "fund-msft-strategy"
    assert payload["canonical_instrument_id"] == "fund-msft-strategy"
    assert payload["instrument_name"] == "Microsoft Strategy Fund"
    assert payload["instrument_type"] == "fund"
    assert payload["primary_identifier"] == "MSFTX"
    assert payload["detail_subject_id"] == "fund-msft-strategy"
    assert payload["detail_supported"] is True
    assert payload["support_reason"] == "detail_ready_local_cache"


def test_shared_registry_service_returns_none_for_missing_instrument(client: TestClient) -> None:
    from watchlist_app.services import shared_instrument_registry as registry

    del client
    assert registry.get_shared_instrument("stale-instrument") is None


def test_local_detail_support_is_explicit_by_instrument_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.api.routes import watchlists as watchlists_route

    assert watchlists_route._supports_local_detail({"instrument_type": "fund"}) is True
    assert watchlists_route._supports_local_detail({"instrument_type": "index"}) is True
    assert watchlists_route._supports_local_detail({"instrument_type": "equity"}) is False


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


def test_default_all_coverage_watchlist_syncs_active_shared_funds(
    client: TestClient,
) -> None:
    response = client.get("/api/watchlists")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["watchlist_id"] == "all-coverage"
    assert payload[0]["name"] == "All Covered"
    assert payload[0]["owner_type"] == "system"
    assert payload[0]["is_default"] is True
    assert payload[0]["is_shared"] is True
    assert payload[0]["item_count"] == len(TEST_SHARED_INSTRUMENTS)

    detail = client.get("/api/watchlists/all-coverage")
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["item_count"] == len(TEST_SHARED_INSTRUMENTS)
    overview_view = next(
        item for item in detail_payload["views"] if item["view_id"] == "overview"
    )
    assert overview_view["default_group_by"] == "taxonomy"

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": "all-coverage",
            "view_id": "overview",
            "selected_fields": ["instrument_name"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    screener_payload = screener.json()
    assert screener_payload["total_rows"] == len(TEST_SHARED_INSTRUMENTS)
    assert {row["instrument_id"] for row in screener_payload["rows"]} == set(TEST_SHARED_INSTRUMENTS)


def test_default_all_coverage_watchlist_resyncs_when_registry_grows(
    client: TestClient,
) -> None:
    initial = client.get("/api/watchlists")
    assert initial.status_code == 200
    assert initial.json()[0]["item_count"] == len(TEST_SHARED_INSTRUMENTS)

    seed_shared_instrument(
        {
            **TEST_SHARED_INSTRUMENTS["savf63"],
            "instrument_id": "fund-new-income",
            "instrument_name": "New Income Fund",
            "identifiers": [
                {
                    "identifier_type": "ticker",
                    "identifier_value": "NEWINC",
                    "is_primary": True,
                },
            ],
        }
    )

    detail = client.get("/api/watchlists/all-coverage")
    assert detail.status_code == 200
    assert detail.json()["item_count"] == len(TEST_SHARED_INSTRUMENTS) + 1

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": "all-coverage",
            "view_id": "overview",
            "selected_fields": ["instrument_name"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    assert "fund-new-income" in {row["instrument_id"] for row in screener.json()["rows"]}


def test_default_all_coverage_watchlist_syncs_active_shared_indexes(
    client: TestClient,
) -> None:
    initial = client.get("/api/watchlists")
    assert initial.status_code == 200
    assert initial.json()[0]["item_count"] == len(TEST_SHARED_INSTRUMENTS)

    seed_shared_instrument(
        {
            "instrument_id": "index-csi-300",
            "instrument_name": "CSI 300 Index",
            "instrument_type": "index",
            "currency": "CNY",
            "identifiers": [
                {"identifier_type": "ticker", "identifier_value": "000300.SH", "is_primary": True},
            ],
            "market_data": [
                {
                    "metric_family": "price",
                    "quote_basis": "close",
                    "as_of_date": "2026-04-15",
                    "value": "3600.1200",
                    "currency": "CNY",
                    "status": "complete",
                }
            ],
            "lifecycle_state": {"status": "active"},
        }
    )

    detail = client.get("/api/watchlists/all-coverage")
    assert detail.status_code == 200
    assert detail.json()["item_count"] == len(TEST_SHARED_INSTRUMENTS) + 1

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": "all-coverage",
            "view_id": "overview",
            "selected_fields": ["instrument_name", "instrument_type"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    screener_payload = screener.json()
    index_row = next(row for row in screener_payload["rows"] if row["instrument_id"] == "index-csi-300")
    assert index_row["instrument_type"] == "index"

    screening_screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": "all-coverage",
            "view_id": "fund-screening",
            "selected_fields": ["instrument_name", "instrument_type"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screening_screener.status_code == 200
    assert "index-csi-300" in {row["instrument_id"] for row in screening_screener.json()["rows"]}


def test_default_all_coverage_watchlist_cannot_be_reduced_manually(
    client: TestClient,
) -> None:
    list_response = client.get("/api/watchlists")
    assert list_response.status_code == 200

    delete_items = client.post(
        "/api/watchlists/all-coverage/items/delete",
        json={"instrument_ids": ["sxv264"]},
    )
    assert delete_items.status_code == 400
    assert "system-maintained" in delete_items.json()["detail"]

    target = client.post(
        "/api/watchlists",
        json={"name": "Copy Target", "description": None},
    )
    target_watchlist_id = target.json()["watchlist_id"]
    move_items = client.post(
        "/api/watchlists/all-coverage/items/move",
        json={"instrument_ids": ["sxv264"], "target_watchlist_id": target_watchlist_id},
    )
    assert move_items.status_code == 400
    assert "system-maintained" in move_items.json()["detail"]

    copy_items = client.post(
        "/api/watchlists/all-coverage/items/copy",
        json={"instrument_ids": ["sxv264"], "target_watchlist_id": target_watchlist_id},
    )
    assert copy_items.status_code == 200
    assert copy_items.json()["added_count"] == 1

    delete_watchlist = client.delete("/api/watchlists/all-coverage")
    assert delete_watchlist.status_code == 400
    assert "system-maintained" in delete_watchlist.json()["detail"]


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
            {"field_key": "instrument_name", "display_order": 1, "width": 320, "is_visible": True}
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
            {"field_key": "instrument_name", "display_order": 1, "width": 320, "is_visible": True}
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
                field_key="instrument_name",
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
                    "field_key": "instrument_name",
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
                {"field_key": "instrument_name", "display_order": 1, "width": 320, "is_visible": True}
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
                {"field_key": "instrument_name", "display_order": 1, "width": 320, "is_visible": True}
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
        json={"instrument_ids": ["sxv264"]},
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
        "/api/instrument-attributes/instruments/sxv264",
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
            "selected_fields": ["instrument_name", "attr.strategy_tags"],
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
        json={"instrument_ids": ["fund-us-agg"]},
    )
    assert first_add.status_code == 200

    second_add = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert second_add.status_code == 200

    view_response = client.post(
        f"/api/watchlists/{watchlist_id}/views",
        json={
            "name": "Filtered View",
            "description": None,
            "default_group_by": "none",
            "default_sort": [],
            "default_filters": {"instrument_name": ["iShares Core U.S. Aggregate Bond ETF"]},
            "default_advanced_filters": None,
            "columns": [
                {"field_key": "instrument_name", "display_order": 1, "width": 320, "is_visible": True}
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
            "selected_fields": ["instrument_name"],
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
            "selected_fields": ["instrument_name"],
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
                {"field_key": "instrument_name", "display_order": 1, "width": 320, "is_visible": True}
            ],
        },
    )
    assert created_view.status_code == 200

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["fund-us-agg"]},
    )
    assert add_response.status_code == 200
    assert add_response.json()["accepted_count"] == 1

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": ["instrument_name", "ticker_or_isin"],
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
        json={"instrument_ids": ["fund-us-agg"]},
    )
    assert add_response.status_code == 404
    assert add_response.json()["detail"] == "Watchlist not found"

    delete_items_response = client.post(
        "/api/watchlists/missing/items/delete",
        json={"instrument_ids": ["fund-us-agg"]},
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
                {"field_key": "instrument_name", "display_order": 1, "width": 320, "is_visible": True}
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
            "selected_fields": ["instrument_name"],
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
            "selected_fields": ["instrument_name"],
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
        json={"instrument_ids": ["fund-us-agg"]},
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


def test_instrument_attribute_routes_return_404_for_missing_instruments(client: TestClient) -> None:
    get_response = client.get("/api/instrument-attributes/instruments/missing-instrument")
    assert get_response.status_code == 404
    assert get_response.json()["detail"] == "Instrument not found"

    post_response = client.post(
        "/api/instrument-attributes/instruments/missing-instrument",
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
    assert post_response.json()["detail"] == "Instrument not found"


def test_seeded_private_fund_watchlist_tags_are_available(client: TestClient) -> None:
    definitions_response = client.get("/api/instrument-attributes/definitions")
    assert definitions_response.status_code == 200
    definitions = definitions_response.json()
    definitions_by_key = {item["attribute_key"]: item for item in definitions}

    expected_keys = {
        "fund_vehicle",
        "implementation_style",
        "trading_universe",
        "alpha_source",
        "research_evidence_level",
        "investment_edge_quality",
        "process_repeatability",
        "decision_discipline",
        "style_profile",
        "style_drift_risk",
        "manager_assessment",
        "team_stability_assessment",
        "portfolio_construction",
        "risk_management_quality",
        "capacity_bucket",
        "liquidity_terms_fit",
        "fee_value_assessment",
        "alignment_quality",
        "historical_delivery",
        "portfolio_role",
        "volatility_bucket",
        "drawdown_control",
        "equity_correlation_bucket",
        "preferred_regime",
        "weak_regime",
        "style_stability",
        "transparency_quality",
    }
    assert expected_keys.issubset(definitions_by_key.keys())
    assert definitions_by_key["fund_vehicle"]["domain_code"] == "overview"
    assert definitions_by_key["fund_vehicle"]["required_for_monitoring"] is True
    assert definitions_by_key["coverage_status"]["label"] == "Status"
    assert definitions_by_key["coverage_status"]["options"] == [
        "Watch",
        "Proposed",
        "Invested",
        "Paused",
        "Exited",
    ]
    assert definitions_by_key["investment_edge_quality"]["group_code"] == "research_edge"
    assert definitions_by_key["process_repeatability"]["options"] == [
        "可重复",
        "部分可重复",
        "关键人驱动",
        "不透明",
    ]
    assert definitions_by_key["style_profile"]["group_code"] == "research_style"
    assert definitions_by_key["risk_management_quality"]["group_code"] == "research_risk"
    assert definitions_by_key["preferred_regime"]["data_type"] == "multi_select"

    field_registry_response = client.get("/api/field-registry")
    assert field_registry_response.status_code == 200
    fields = field_registry_response.json()["fields"]
    fields_by_key = {item["field_key"]: item for item in fields}

    assert "attr.fund_regime" in fields_by_key
    assert fields_by_key["attr.fund_regime"]["filter_mode"] == "multi_select"
    assert "attr.fund_taxonomy_level_1" in fields_by_key
    assert fields_by_key["attr.fund_taxonomy_level_1"]["filter_mode"] == "multi_select"
    assert fields_by_key["attr.fund_taxonomy_level_1"]["group_mode"] == "discrete"
    assert (
        fields_by_key["attr.fund_taxonomy_level_1"]["category_code"]
        == "product_taxonomy"
    )
    assert fields_by_key["attr.coverage_status"]["product_scope_json"] == []
    assert fields_by_key["attr.coverage_status"]["label"] == "Status"
    assert fields_by_key["attr.investment_edge_quality"]["category_code"] == "research_framework"
    assert fields_by_key["latest_quote"]["instrument_scope_json"] == []
    assert fields_by_key["latest_quote"]["source_metric_code"] == "instrument_chart_read_model.series.latest_quote"
    assert fields_by_key["latest_quote_date"]["data_type"] == "date"
    assert fields_by_key["price_chart_1m"]["label"] == "Chart 1M"
    assert fields_by_key["price_chart_1m"]["description"] == "1-month NAV chart from the current chart read model."
    assert fields_by_key["return_1w"]["label"] == "1W Return"
    assert fields_by_key["return_mtd"]["label"] == "MTD"
    assert fields_by_key["return_ytd"]["label"] == "YTD"
    assert fields_by_key["return_1m"]["label"] == "1M"
    assert fields_by_key["return_1y"]["label"] == "1Y"
    assert fields_by_key["annualized_return"]["label"] == "Ann."
    assert fields_by_key["return_3y"]["label"] == "3Y"
    assert fields_by_key["return_5y"]["label"] == "5Y"
    assert fields_by_key["max_drawdown"]["label"] == "Max DD"
    assert fields_by_key["attr.current_drawdown"]["label"] == "Current DD"
    assert fields_by_key["attr.peer_return_1w_percentile"]["label"] == "1W Return Pctl"
    assert fields_by_key["attr.peer_annualized_return_percentile"]["label"] == "Ann. Pctl"


def test_custom_attribute_group_by_is_available_for_watchlist_views(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Attribute Grouping", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["fund-us-agg", "sxv264"]},
    )
    assert add_response.status_code == 200

    update_response = client.post(
        "/api/instrument-attributes/instruments/sxv264",
        json={"values": [{"attribute_key": "coverage_status", "value": "Invested"}]},
    )
    assert update_response.status_code == 200

    detail_response = client.get(f"/api/watchlists/{watchlist_id}")
    assert detail_response.status_code == 200
    group_by_codes = [item["code"] for item in detail_response.json()["available_group_bys"]]
    assert "attr.coverage_status" in group_by_codes

    screener_response = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "selected_fields": ["instrument_name", "attr.coverage_status"],
            "group_by": "attr.coverage_status",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener_response.status_code == 200
    payload = screener_response.json()
    group_counts = {item["group_value"]: item["row_count"] for item in payload["groups"]}
    assert group_counts["Invested"] == 1
    assert group_counts["Unspecified"] == 1


def test_adding_funds_does_not_inject_product_framework_values(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Clean Product Framework", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["fund-us-agg", "sxv264"]},
    )
    assert add_response.status_code == 200

    public_response = client.get("/api/instrument-attributes/instruments/fund-us-agg")
    assert public_response.status_code == 200
    public_payload = public_response.json()
    public_values = public_payload["values"]
    assert "fund_vehicle" not in public_values
    assert "alpha_source" not in public_values
    assert public_payload["taxonomy"]["assigned_node_id"] is None

    private_response = client.get("/api/instrument-attributes/instruments/sxv264")
    assert private_response.status_code == 200
    private_payload = private_response.json()
    private_values = private_payload["values"]
    assert "fund_vehicle" not in private_values
    assert "alpha_source" not in private_values
    assert private_payload["taxonomy"]["assigned_node_id"] is None


def test_fund_research_profile_normalizes_research_notes_and_manual_rating(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Research Profile", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    default_response = client.get("/api/instruments/sxv264/research")
    assert default_response.status_code == 200
    default_payload = default_response.json()
    assert default_payload["manual_rating"] is None
    assert "research_view" in default_payload["overview"]
    assert "research_status" not in default_payload["overview"]
    assert default_payload["timeline_notes"] == []
    assert "thesis" not in default_payload
    assert "conclusions" not in default_payload
    assert "notes" not in default_payload

    upsert_response = client.put(
        "/api/instruments/sxv264/research",
        json={
            "payload": {
                "overview": {
                    "current_view": "Constructive",
                    "research_view": "Constructive research view",
                },
                "manual_rating": 9,
                "timeline_notes": [
                    {
                        "note_id": "n1",
                        "note_date": "2026-04-30",
                        "title": "Manager call",
                        "summary": "Capacity now needs review.",
                    }
                ],
                "notes": ["discarded old note channel"],
            },
            "updated_by": "test",
        },
    )
    assert upsert_response.status_code == 200
    payload = upsert_response.json()
    assert payload["manual_rating"] == 5
    assert payload["overview"]["research_view"] == "Constructive research view"
    assert "research_status" not in payload["overview"]
    assert payload["timeline_notes"][0]["note_id"] == "n1"
    assert "thesis" not in payload
    assert "conclusions" not in payload
    assert "notes" not in payload


def test_fund_document_upload_adds_profile_row_and_allows_download(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Document Upload", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    upload_response = client.post(
        "/api/instruments/sxv264/documents/upload",
        files={"file": ("../Manager DD.pdf", b"manager diligence packet", "application/pdf")},
        data={
            "title": "Manager DD",
            "document_type": "due_diligence",
            "as_of_date": "2026-04-30",
            "source": "manager",
            "status": "uploaded",
            "updated_by": "test",
        },
    )
    assert upload_response.status_code == 200
    payload = upload_response.json()
    document = payload["current_documents"][0]
    assert document["title"] == "Manager DD"
    assert document["file_name"] == "Manager_DD.pdf"
    assert document["file_size"] == len(b"manager diligence packet")
    assert document["download_url"].startswith("/api/instruments/sxv264/documents/files/")
    assert payload["recent_imports"][0]["file_name"] == "Manager_DD.pdf"

    download_response = client.get(document["download_url"])
    assert download_response.status_code == 200
    assert download_response.content == b"manager diligence packet"

    empty_upload = client.post(
        "/api/instruments/sxv264/documents/upload",
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert empty_upload.status_code == 400


def test_instrument_attributes_can_be_cleared_with_null_and_empty_list(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Attribute Clear State", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
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
        "/api/instrument-attributes/instruments/sxv264",
        json={
            "values": [
                {"attribute_key": "tag_clear_single", "value": "低波"},
                {"attribute_key": "tag_clear_multi", "value": ["市场中性", "CTA"]},
            ]
        },
    )
    assert first_upsert.status_code == 200

    cleared_upsert = client.post(
        "/api/instrument-attributes/instruments/sxv264",
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


def test_fund_taxonomy_assignment_updates_summary_attribute_context_and_watchlist_rows(
    client: TestClient,
) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Taxonomy Coverage", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    tree_response = client.get("/api/taxonomies/fund-taxonomy")
    assert tree_response.status_code == 200
    tree_payload = tree_response.json()
    assert tree_payload["taxonomy_code"] == "fund_taxonomy"
    assert any(node["node_id"] == "fund-private-equity-quant-long-500" for node in tree_payload["nodes"])

    update_response = client.put(
        "/api/taxonomies/fund-taxonomy/instruments/sxv264",
        json={"node_id": "fund-private-equity-quant-long-500", "updated_by": "test"},
    )
    assert update_response.status_code == 200
    update_payload = update_response.json()
    assert update_payload["path_labels"] == ["私募", "股票策略", "量化多头", "500指增"]
    assert update_payload["derived_values"]["fund_regime"] == "私募"
    assert update_payload["derived_values"]["fund_taxonomy_level_1"] == "股票策略"
    assert update_payload["derived_values"]["fund_taxonomy_level_2"] == "量化多头"
    assert update_payload["derived_values"]["fund_taxonomy_leaf"] == "500指增"

    attributes_response = client.get("/api/instrument-attributes/instruments/sxv264")
    assert attributes_response.status_code == 200
    attributes_payload = attributes_response.json()
    assert attributes_payload["taxonomy"]["assigned_node_id"] == "fund-private-equity-quant-long-500"
    assert "fund_regime" not in attributes_payload["values"]

    summary_response = client.get("/api/instruments/sxv264/summary")
    assert summary_response.status_code == 200
    summary_payload = summary_response.json()
    assert summary_payload["taxonomy"]["path_labels"] == ["私募", "股票策略", "量化多头", "500指增"]

    detail_response = client.get(f"/api/watchlists/{watchlist_id}")
    assert detail_response.status_code == 200
    group_by_codes = [item["code"] for item in detail_response.json()["available_group_bys"]]
    assert group_by_codes[:6] == [
        "none",
        "taxonomy",
        "management_firm_name",
        "overall_rating",
        "analyst_stance",
        "data_freshness_status",
    ]
    assert "attr.coverage_status" in group_by_codes
    assert "attr.focus_bucket" in group_by_codes

    screener_response = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "fund-screening",
            "selected_fields": [
                "instrument_name",
                "attr.fund_regime",
                "attr.fund_taxonomy_level_1",
                "attr.fund_taxonomy_leaf",
            ],
            "group_by": "attr.fund_taxonomy_level_1",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener_response.status_code == 200
    screener_payload = screener_response.json()
    assert screener_payload["groups"] == [{"group_value": "股票策略", "row_count": 1}]
    assert screener_payload["rows"][0]["attr.fund_regime"] == "私募"
    assert screener_payload["rows"][0]["attr.fund_taxonomy_level_1"] == "股票策略"
    assert screener_payload["rows"][0]["attr.fund_taxonomy_leaf"] == "500指增"

    taxonomy_group_response = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "fund-screening",
            "selected_fields": ["instrument_name"],
            "filters": {
                "attr.fund_regime": ["私募"],
                "attr.fund_taxonomy_level_1": ["股票策略"],
                "attr.fund_taxonomy_level_2": ["量化多头"],
            },
            "group_by": "taxonomy",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert taxonomy_group_response.status_code == 200
    taxonomy_group_payload = taxonomy_group_response.json()
    assert taxonomy_group_payload["total_rows"] == 1
    assert taxonomy_group_payload["rows"][0]["attr.fund_regime"] == "私募"
    assert taxonomy_group_payload["rows"][0]["attr.fund_taxonomy_level_1"] == "股票策略"
    assert taxonomy_group_payload["rows"][0]["attr.fund_taxonomy_level_2"] == "量化多头"
    assert [
        (item["group_value"], item["group_depth"], item["row_count"])
        for item in taxonomy_group_payload["groups"]
    ] == [
        ("私募", 0, 1),
        ("私募 / 股票策略", 1, 1),
        ("私募 / 股票策略 / 量化多头", 2, 1),
        ("私募 / 股票策略 / 量化多头 / 500指增", 3, 1),
    ]


def test_monitoring_dashboard_surfaces_missing_labels_quotes_and_open_recalc_jobs(
    client: TestClient,
) -> None:
    seed_shared_instrument(
        {
        "instrument_id": "fund-no-data",
        "instrument_name": "No Data Fund",
        "instrument_type": "fund",
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
        json={"instrument_ids": ["sxv264", "fund-no-data"]},
    )
    assert add_response.status_code == 200
    assert add_response.json()["accepted_count"] == 2

    recalc_response = client.post("/api/recalc/instruments/sxv264/performance")
    assert recalc_response.status_code == 200

    response = client.get("/api/monitoring/dashboard")
    assert response.status_code == 200
    payload = response.json()

    assert payload["overview"] == {
        "watchlist_count": 1,
        "unique_instrument_count": 2,
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
        for item in payload["needs_attention_instruments"]
        if item["instrument_id"] == "fund-no-data"
    )
    assert attention_asset["data_freshness_status"] == "pending_recalc"
    assert "needs_refresh" in attention_asset["issue_flags"]
    assert "missing_quote" in attention_asset["issue_flags"]

    missing_label_asset = next(
        item
        for item in payload["missing_label_instruments"]
        if item["instrument_id"] == "fund-no-data"
    )
    assert "fund_regime" in missing_label_asset["missing_attribute_keys"]
    assert "fund_taxonomy_leaf" in missing_label_asset["missing_attribute_keys"]
    assert len(payload["missing_label_instruments"]) == 2

    assert len(payload["open_recalc_jobs"]) == 1
    assert payload["open_recalc_jobs"][0]["instrument_id"] == "sxv264"
    assert payload["open_recalc_jobs"][0]["job_type"] == "performance"
    assert payload["open_recalc_jobs"][0]["job_status"] == "queued"
    assert payload["open_recalc_jobs"][0]["primary_watchlist_id"] == watchlist_id
