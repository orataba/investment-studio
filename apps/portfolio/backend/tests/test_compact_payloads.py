from __future__ import annotations

from datetime import date

from portfolio_app.db.models import ResearchRunRecordModel
from portfolio_app.db.session import get_session_factory


def test_holdings_default_rows_are_compact_and_details_are_explicit(client) -> None:
    compact_response = client.get(
        "/api/workspace/holdings",
        params={"portfolio_id": "portfolio-ops"},
    )

    assert compact_response.status_code == 200, compact_response.json()
    compact = compact_response.json()
    assert compact["detail_level"] == "compact"
    compact_abbv = next(
        row
        for row in compact["rows"]
        if row["instrument_core"]["instrument_id"] == "equity-us-abbv"
    )
    assert compact_abbv["price_chart_1m"] == []
    assert compact_abbv["price_chart_3m"] == []
    assert compact_abbv["price_chart_1y"] == []
    assert 1 <= len(compact_abbv["price_chart_6m"]) <= 24
    assert "instrument_return_series_all" not in compact_abbv
    assert "instrument_holding_return_series" not in compact_abbv

    detailed_response = client.get(
        "/api/workspace/holdings",
        params={"portfolio_id": "portfolio-ops", "include_details": True},
    )

    assert detailed_response.status_code == 200, detailed_response.json()
    detailed = detailed_response.json()
    assert detailed["detail_level"] == "full"
    detailed_abbv = next(
        row
        for row in detailed["rows"]
        if row["instrument_core"]["instrument_id"] == "equity-us-abbv"
    )
    assert detailed_abbv["price_chart_1m"]
    assert detailed_abbv["price_chart_3m"]
    assert detailed_abbv["price_chart_1y"]
    assert detailed_abbv["instrument_return_series_all"]["points"]
    assert len(compact_response.content) < len(detailed_response.content)

def test_research_workbench_defaults_to_compact_runs_and_expands_explicit_selection(client) -> None:
    run_id = "research-compact-payload"
    detail = {
        "headline": "Compact payload fixture",
        "backtest": {
            "points": [
                {"date": f"2026-01-{day:02d}", "value": 100.0 + day}
                for day in range(1, 29)
            ],
            "top_sleeve_weight_points": [],
            "top_sleeve_contribution_points": [],
            "warnings": [],
        },
        "target_rows": [
            {
                "member_type": "instrument",
                "member_id": "equity-us-abbv",
                "label": "AbbVie Inc",
                "execution_status": "ready",
            }
        ],
    }
    session_factory = get_session_factory()
    with session_factory() as session:
        session.add(
            ResearchRunRecordModel(
                research_run_id=run_id,
                portfolio_id="portfolio-ops",
                job_type="target_weight_solve",
                status="completed",
                requested_at="2026-07-15T10:00:00Z",
                started_at="2026-07-15T10:00:00Z",
                finished_at="2026-07-15T10:01:00Z",
                as_of_date=date(2026, 4, 15),
                planning_taxonomy_id=None,
                lookback_days=90,
                requested_by="pytest",
                headline="Compact payload fixture",
                detail_json=detail,
                artifacts_json=[
                    {
                        "artifact_id": "summary",
                        "label": "Summary JSON",
                        "path": "portfolio-ops/research-compact-payload/summary.json",
                        "media_type": "application/json",
                        "preview_kind": "text",
                    }
                ],
                request_payload_json={},
                error_message=None,
            )
        )
        session.commit()

    compact_response = client.get("/api/portfolios/portfolio-ops/research/workbench")

    assert compact_response.status_code == 200, compact_response.json()
    compact = compact_response.json()
    assert compact["detail_level"] == "compact"
    assert compact["selected_run"]["research_run_id"] == run_id
    assert compact["selected_run"]["detail"] is None
    assert compact["selected_run"]["artifact_count"] == 1
    assert compact["selected_run"]["artifacts"] == []
    assert all(run["detail"] is None for run in compact["runs"])
    assert all(run["artifacts"] == [] for run in compact["runs"])

    selected_response = client.get(
        "/api/portfolios/portfolio-ops/research/workbench",
        params={"selected_run_id": run_id},
    )

    assert selected_response.status_code == 200, selected_response.json()
    selected = selected_response.json()
    assert selected["detail_level"] == "selected_run"
    assert selected["selected_run"]["detail"]["backtest"]["points"] == detail["backtest"]["points"]
    assert selected["selected_run"]["artifacts"][0]["artifact_id"] == "summary"
    assert len(compact_response.content) < len(selected_response.content)

    explicit_full_response = client.get(
        "/api/portfolios/portfolio-ops/research/workbench",
        params={"include_details": True},
    )
    assert explicit_full_response.status_code == 200
    assert explicit_full_response.json()["detail_level"] == "selected_run"
    assert explicit_full_response.json()["selected_run"]["detail"] is not None
