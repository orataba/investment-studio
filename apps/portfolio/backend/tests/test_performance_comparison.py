from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, event

from portfolio_app.api.contracts import PerformanceComparisonResponse
from portfolio_app.db.models import PortfolioDailySnapshotModel
from portfolio_app.db.session import get_engine, get_session_factory
from portfolio_app.services import performance_comparison
from portfolio_app.services.daily_snapshots import DAILY_SNAPSHOT_CALCULATION_VERSION
from portfolio_ops_instrument_core import instrument_store as shared_store


PORTFOLIO_ID = "portfolio-ops"
BENCHMARK_ID = "fund-us-agg"


def _snapshot_payload(point_date: date, daily_twr: float) -> dict[str, object]:
    return {
        "portfolio_id": PORTFOLIO_ID,
        "base_currency": "USD",
        "as_of_date": point_date.isoformat(),
        "nav_coverage_state": "complete",
        "nav_coverage_reason_codes": [],
        "book_pnl_coverage_state": "complete",
        "book_pnl_coverage_reason_codes": [],
        "return_observation_eligible": True,
        "twr_state": "linked",
        "twr_reliability_status": "reliable",
        "twr_reliability_reasons": [],
        "daily_twr": daily_twr,
        "calculation_version": DAILY_SNAPSHOT_CALCULATION_VERSION,
    }


def _replace_snapshots(returns_by_date: dict[date, float]) -> None:
    with get_session_factory()() as session:
        session.execute(
            delete(PortfolioDailySnapshotModel).where(
                PortfolioDailySnapshotModel.portfolio_id == PORTFOLIO_ID
            )
        )
        for point_date, daily_twr in sorted(returns_by_date.items()):
            session.add(
                PortfolioDailySnapshotModel(
                    portfolio_id=PORTFOLIO_ID,
                    as_of_date=point_date,
                    nav_coverage_state="complete",
                    nav_coverage_reason_codes=[],
                    book_pnl_coverage_state="complete",
                    book_pnl_coverage_reason_codes=[],
                    nav=100.0,
                    beginning_nav=100.0,
                    ending_nav=100.0 * (1.0 + daily_twr),
                    daily_twr=daily_twr,
                    cumulative_twr=None,
                    drawdown=None,
                    snapshot_json=_snapshot_payload(point_date, daily_twr),
                    calculated_at="2026-07-13T00:00:00Z",
                )
            )
        session.commit()


def _request(client, *, benchmark_id: str = BENCHMARK_ID):
    return client.get(
        f"/api/portfolios/{PORTFOLIO_ID}/performance/comparison",
        params={
            "benchmark_instrument_id": benchmark_id,
            "start_date": "2026-04-14",
            "end_date": "2026-04-15",
            "as_of_date": "2026-04-15",
        },
    )


def _upsert_ready_anchor() -> None:
    shared_store.upsert_market_data_points(
        get_session_factory(),
        instrument_id=BENCHMARK_ID,
        rows=[
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "as_of_date": "2026-04-13",
                "value": "97.00",
                "currency": "USD",
                "status": "complete",
                "source_ref": "test:comparison-anchor",
            }
        ],
    )


def test_comparison_uses_materialized_twr_and_canonical_total_return(
    client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        performance_comparison,
        "ensure_portfolio_daily_snapshots",
        lambda _portfolio_id: None,
    )
    _replace_snapshots(
        {
            date(2026, 4, 14): 0.01,
            date(2026, 4, 15): -0.02,
        }
    )
    _upsert_ready_anchor()

    response = _request(client)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["market_data_role"] == "total_return"
    assert payload["coverage"]["benchmark_quote_basis"] == "total_return_nav"
    assert payload["coverage"]["benchmark_reliability_status"] == "reliable"
    assert payload["coverage"]["portfolio_twr_reliability_status"] == "reliable"
    assert payload["coverage"]["coverage_ratio"] == 1.0
    assert payload["coverage"]["required_observation_count"] == 2
    assert payload["portfolio_metrics"]["period_return"] == pytest.approx(
        1.01 * 0.98 - 1.0
    )
    expected_benchmark_return = 91.62 / 97.0 - 1.0
    assert payload["benchmark_metrics"]["period_return"] == pytest.approx(
        expected_benchmark_return
    )
    assert payload["differences"]["period_return"] == pytest.approx(
        payload["portfolio_metrics"]["period_return"]
        - payload["benchmark_metrics"]["period_return"]
    )
    assert payload["history_reliability"] == {
        "start_date": "2026-04-13",
        "end_date": "2026-04-15",
        "elapsed_days": 2,
        "calendar_span_days": 3,
        "minimum_history_days": 365,
        "annualized_return_eligible": False,
        "annualized_return_reason_codes": [
            "annualized_return_history_below_minimum"
        ],
        "sample_label": (
            "2026-04-13 to 2026-04-15 · 3 calendar days · 2 snapshots · "
            "2 return observations · 2 risk observations"
        ),
        "annualization_message": (
            "Insufficient history: annualized TWR, IRR / MWRR, and Calmar "
            "Ratio require at least one year. Period TWR remains the primary "
            "return."
        ),
    }
    for metric_group in ("portfolio_metrics", "benchmark_metrics", "differences"):
        assert payload[metric_group]["annualized_return"] is None
        assert payload[metric_group]["calmar_ratio"] is None
    assert payload["portfolio_metrics"]["annualized_volatility"] is not None
    assert payload["benchmark_metrics"]["annualized_volatility"] is not None
    assert payload["relative_metrics"]["tracking_error"] is not None
    assert payload["relative_metrics"]["information_ratio"] is not None
    assert payload["relative_metrics"]["correlation"] is not None
    assert payload["lineage"]["method_version"].startswith(
        "performance-comparison.v20260713"
    )
    assert payload["lineage"]["method_version"].endswith(".v2")
    assert payload["lineage"]["portfolio"]["portfolio_id"] == PORTFOLIO_ID
    assert payload["lineage"]["portfolio"]["base_currency"] == "USD"
    assert payload["lineage"]["portfolio"]["fingerprint"].startswith("sha256:")
    assert payload["lineage"]["benchmark"]["fingerprint"].startswith("sha256:")
    assert payload["lineage"]["fingerprint"].startswith("sha256:")
    for metric_group in ("portfolio_metrics", "benchmark_metrics", "differences"):
        for metric_name in ("annualized_return", "calmar_ratio"):
            invalid_payload = deepcopy(payload)
            invalid_payload[metric_group][metric_name] = 0.1
            with pytest.raises(
                ValidationError,
                match="Ineligible comparison history requires null",
            ):
                PerformanceComparisonResponse.model_validate(invalid_payload)


def test_comparison_keeps_annualized_return_and_calmar_at_365_day_boundary(
    client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        performance_comparison,
        "ensure_portfolio_daily_snapshots",
        lambda _portfolio_id: None,
    )
    start_date = date(2025, 7, 10)
    end_date = date(2026, 7, 9)
    start_boundary = start_date - timedelta(days=1)
    _replace_snapshots(
        {
            start_date: 0.01,
            end_date: -0.02,
        }
    )
    shared_store.upsert_market_data_points(
        get_session_factory(),
        instrument_id=BENCHMARK_ID,
        rows=[
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "as_of_date": start_boundary.isoformat(),
                "value": "100.00",
                "currency": "USD",
                "status": "complete",
                "source_ref": "test:annualization-boundary-anchor",
            },
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "as_of_date": start_date.isoformat(),
                "value": "102.00",
                "currency": "USD",
                "status": "complete",
                "source_ref": "test:annualization-boundary-start",
            },
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "as_of_date": end_date.isoformat(),
                "value": "101.00",
                "currency": "USD",
                "status": "complete",
                "source_ref": "test:annualization-boundary-end",
            },
        ],
    )

    response = client.get(
        f"/api/portfolios/{PORTFOLIO_ID}/performance/comparison",
        params={
            "benchmark_instrument_id": BENCHMARK_ID,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "as_of_date": end_date.isoformat(),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["history_reliability"]["start_date"] == start_boundary.isoformat()
    assert payload["history_reliability"]["end_date"] == end_date.isoformat()
    assert payload["history_reliability"]["elapsed_days"] == 365
    assert payload["history_reliability"]["calendar_span_days"] == 366
    assert payload["history_reliability"]["minimum_history_days"] == 365
    assert payload["history_reliability"]["annualized_return_eligible"] is True
    assert payload["history_reliability"]["annualized_return_reason_codes"] == []
    assert payload["history_reliability"]["annualization_message"] is None
    for metric_group in ("portfolio_metrics", "benchmark_metrics", "differences"):
        assert payload[metric_group]["annualized_return"] is not None
        assert payload[metric_group]["calmar_ratio"] is not None


def test_comparison_withholds_all_relative_metrics_on_incomplete_date_coverage(
    client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        performance_comparison,
        "ensure_portfolio_daily_snapshots",
        lambda _portfolio_id: None,
    )
    _replace_snapshots(
        {
            date(2026, 4, 13): 0.01,
            date(2026, 4, 14): -0.02,
        }
    )

    response = client.get(
        f"/api/portfolios/{PORTFOLIO_ID}/performance/comparison",
        params={
            "benchmark_instrument_id": BENCHMARK_ID,
            "start_date": "2026-04-13",
            "end_date": "2026-04-14",
            "as_of_date": "2026-04-15",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "unavailable"
    assert "benchmark_date_coverage_incomplete" in payload["unavailable_reasons"]
    assert set(payload["portfolio_metrics"].values()) == {None}
    assert set(payload["benchmark_metrics"].values()) == {None}
    assert set(payload["relative_metrics"].values()) == {None}
    assert set(payload["differences"].values()) == {None}
    assert payload["points"] == []
    assert payload["lineage"]["fingerprint"].startswith("sha256:")


def test_comparison_rejects_newer_noncomplete_total_return_revision(
    client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        performance_comparison,
        "ensure_portfolio_daily_snapshots",
        lambda _portfolio_id: None,
    )
    _replace_snapshots(
        {
            date(2026, 4, 14): 0.01,
            date(2026, 4, 15): -0.02,
        }
    )
    shared_store.upsert_market_data_points(
        get_session_factory(),
        instrument_id=BENCHMARK_ID,
        rows=[
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "as_of_date": "2026-04-15",
                "value": "91.70",
                "currency": "USD",
                "status": "partial",
                "source_ref": "test:partial-total-return",
            }
        ],
    )

    response = _request(client)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "unavailable"
    assert "partial_series" in payload["unavailable_reasons"]
    assert set(payload["relative_metrics"].values()) == {None}


def test_comparison_rejects_raw_currency_mismatch(client, monkeypatch) -> None:
    monkeypatch.setattr(
        performance_comparison,
        "ensure_portfolio_daily_snapshots",
        lambda _portfolio_id: None,
    )
    _replace_snapshots(
        {
            date(2026, 4, 14): 0.01,
            date(2026, 4, 15): -0.02,
        }
    )

    response = _request(client, benchmark_id="fund-hk-2800")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "unavailable"
    assert "benchmark_currency_mismatch" in payload["unavailable_reasons"]
    assert set(payload["relative_metrics"].values()) == {None}


def test_comparison_rejects_malformed_materialized_twr_facts(
    client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        performance_comparison,
        "ensure_portfolio_daily_snapshots",
        lambda _portfolio_id: None,
    )
    _replace_snapshots(
        {
            date(2026, 4, 14): 0.01,
            date(2026, 4, 15): -0.02,
        }
    )
    with get_session_factory()() as session:
        row = session.get(
            PortfolioDailySnapshotModel,
            {"portfolio_id": PORTFOLIO_ID, "as_of_date": date(2026, 4, 14)},
        )
        assert row is not None
        payload = dict(row.snapshot_json)
        payload["return_observation_eligible"] = "true"
        row.snapshot_json = payload
        session.commit()

    response = _request(client)

    assert response.status_code == 422
    assert "return eligibility must be explicit" in response.json()["detail"]


def test_comparison_query_count_is_independent_of_history_length(monkeypatch) -> None:
    monkeypatch.setattr(
        performance_comparison,
        "ensure_portfolio_daily_snapshots",
        lambda _portfolio_id: None,
    )
    start_date = date(2026, 1, 1)
    end_date = date(2026, 4, 15)
    point_count = (end_date - start_date).days + 1
    benchmark_rows = [
        {
            "metric_family": "nav",
            "quote_basis": "total_return_nav",
            "as_of_date": (start_date + timedelta(days=index)).isoformat(),
            "value": str(100.0 + index * 0.1),
            "currency": "USD",
            "status": "complete",
            "source_ref": f"test:query-count:{index}",
        }
        for index in range(point_count)
    ]
    shared_store.upsert_market_data_points(
        get_session_factory(),
        instrument_id=BENCHMARK_ID,
        rows=benchmark_rows,
    )
    _replace_snapshots(
        {
            start_date + timedelta(days=index): 0.001 if index % 2 else -0.0005
            for index in range(1, point_count)
        }
    )

    engine = get_engine()
    query_count = 0

    def count_query(*_args, **_kwargs) -> None:
        nonlocal query_count
        query_count += 1

    event.listen(engine, "before_cursor_execute", count_query)
    try:
        query_count = 0
        short = performance_comparison.build_performance_comparison(
            portfolio_id=PORTFOLIO_ID,
            benchmark_instrument_id=BENCHMARK_ID,
            start_date=date(2026, 4, 14),
            end_date=end_date,
            as_of_date=end_date,
        )
        short_query_count = query_count
        query_count = 0
        long = performance_comparison.build_performance_comparison(
            portfolio_id=PORTFOLIO_ID,
            benchmark_instrument_id=BENCHMARK_ID,
            start_date=start_date + timedelta(days=1),
            end_date=end_date,
            as_of_date=end_date,
        )
        long_query_count = query_count
    finally:
        event.remove(engine, "before_cursor_execute", count_query)

    assert short is not None and short["status"] == "ready"
    assert long is not None and long["status"] == "ready"
    assert short_query_count == long_query_count
    assert long_query_count < 30


def test_comparison_validates_explicit_window_order(client) -> None:
    response = client.get(
        f"/api/portfolios/{PORTFOLIO_ID}/performance/comparison",
        params={
            "benchmark_instrument_id": BENCHMARK_ID,
            "start_date": "2026-04-15",
            "end_date": "2026-04-14",
            "as_of_date": "2026-04-15",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "start_date must be on or before end_date"


def test_authoritative_calmar_ratio_fails_closed_without_a_drawdown() -> None:
    assert performance_comparison.authoritative_calmar_ratio(0.12, -0.20) == pytest.approx(0.6)
    assert performance_comparison.authoritative_calmar_ratio(0.12, 0.0) is None
    assert performance_comparison.authoritative_calmar_ratio(None, -0.20) is None
