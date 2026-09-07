from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from investment_studio_instrument_core.db_models import Instrument

from portfolio_app.api.routes import workspace
from portfolio_app.db.models import PortfolioCalculationStateModel, ResearchRunRecordModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services import daily_snapshot_worker, daily_snapshots


PORTFOLIO_ID = "investment-studio"
HOLDINGS_URL = f"/api/workspace/holdings?portfolio_id={PORTFOLIO_ID}"
PERFORMANCE_URL = f"/api/portfolios/{PORTFOLIO_ID}/performance"


def _state():
    with get_session_factory()() as session:
        state = session.get(PortfolioCalculationStateModel, PORTFOLIO_ID)
        return None if state is None else (state.daily_snapshot_status, state.refresh_request_id)


def _assert_pending(response, status):
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert response.json() == {
        "detail": {
            "code": "portfolio_calculation_pending",
            "portfolio_id": PORTFOLIO_ID,
            "status": status,
            "message": "Portfolio calculations are updating. Please retry shortly.",
        }
    }


def test_first_get_returns_pending_without_recalculating_and_worker_makes_it_readable(raw_client, monkeypatch):
    original_builder = daily_snapshots.performance.build_daily_portfolio_snapshots

    def no_request_recalculation(*_args, **_kwargs):
        raise AssertionError("GET must not run the calculation kernel")

    monkeypatch.setattr(daily_snapshots.performance, "build_daily_portfolio_snapshots", no_request_recalculation)
    response = raw_client.get(PERFORMANCE_URL, headers={"Origin": "http://localhost:5174"})
    _assert_pending(response, "stale")
    assert "Retry-After" in response.headers["Access-Control-Expose-Headers"]
    queued_state = _state()
    _assert_pending(raw_client.get(HOLDINGS_URL), "stale")
    assert _state() == queued_state

    monkeypatch.setattr(daily_snapshots.performance, "build_daily_portfolio_snapshots", original_builder)
    assert daily_snapshot_worker.run_daily_snapshot_recalculation_worker_once()
    assert raw_client.get(PERFORMANCE_URL).status_code == 200
    assert raw_client.get(HOLDINGS_URL).status_code == 200


def test_get_never_supersedes_a_running_generation(raw_client):
    with get_session_factory()() as session:
        state = daily_snapshots._state_for_portfolio(session, PORTFOLIO_ID)
        state.daily_snapshot_status = "running"
        state.refresh_request_id = "worker-owned-request"
        state.refresh_started_at = datetime.now(UTC).isoformat()
        session.commit()
    for url in (PERFORMANCE_URL, HOLDINGS_URL, PERFORMANCE_URL):
        _assert_pending(raw_client.get(url), "running")
    assert _state() == ("running", "worker-owned-request")


@pytest.mark.parametrize("suffix", [
    "calculation/entries",
    "calculation/entries/calendar",
    "contribution/entries",
    "contribution/entries/calendar",
])
def test_entry_get_reads_facts_without_queuing_portfolio_materialization(raw_client, suffix):
    before = _state()
    response = raw_client.get(f"{PERFORMANCE_URL}/{suffix}")
    assert response.status_code == 200
    assert _state() == before


def test_explicit_research_post_does_not_create_a_run_while_inputs_are_owned_by_another_worker(raw_client):
    with get_session_factory()() as session:
        state = daily_snapshots._state_for_portfolio(session, PORTFOLIO_ID)
        state.daily_snapshot_status = "running"
        state.refresh_request_id = "worker-owned-request"
        state.refresh_started_at = datetime.now(UTC).isoformat()
        session.commit()
    response = raw_client.post(f"/api/portfolios/{PORTFOLIO_ID}/research/runs", json={})
    _assert_pending(response, "running")
    assert _state() == ("running", "worker-owned-request")
    with get_session_factory()() as session:
        assert session.scalar(select(ResearchRunRecordModel).limit(1)) is None


def test_failed_calculation_is_terminal_and_does_not_enqueue(raw_client):
    with get_session_factory()() as session:
        state = daily_snapshots._state_for_portfolio(session, PORTFOLIO_ID)
        state.daily_snapshot_status = "failed"
        state.refresh_request_id = "failed-request"
        state.error_message = "Invalid transaction accounting facts."
        session.commit()
    response = raw_client.get(PERFORMANCE_URL)
    assert response.status_code == 503
    assert "Retry-After" not in response.headers
    assert response.json()["detail"] == {
        "code": "portfolio_calculation_failed",
        "portfolio_id": PORTFOLIO_ID,
        "status": "failed",
        "message": "Invalid transaction accounting facts.",
    }
    assert _state() == ("failed", "failed-request")


def test_missed_source_invalidation_is_queued_once_even_for_a_cached_report(raw_client):
    assert daily_snapshot_worker.run_daily_snapshot_recalculation_worker_once()
    assert raw_client.get(PERFORMANCE_URL).status_code == 200
    with get_session_factory()() as session:
        instrument = session.get(Instrument, "equity-us-abbv")
        instrument.market_data_updated_at = "2099-01-01T00:00:00.000000Z"
        session.commit()
    _assert_pending(raw_client.get(PERFORMANCE_URL), "stale")
    queued_state = _state()
    assert queued_state[1] is not None
    _assert_pending(raw_client.get(PERFORMANCE_URL), "stale")
    assert _state() == queued_state


def test_financial_get_discards_response_when_sources_change_during_enrichment(raw_client, monkeypatch):
    assert daily_snapshot_worker.run_daily_snapshot_recalculation_worker_once()
    original = workspace._public_holdings_workspace_response

    def change_sources_after_enrichment(*args, **kwargs):
        response = original(*args, **kwargs)
        with get_session_factory()() as session:
            instrument = session.get(Instrument, "equity-us-abbv")
            instrument.market_data_updated_at = "2099-01-01T00:00:00.000000Z"
            session.commit()
        return response

    monkeypatch.setattr(workspace, "_public_holdings_workspace_response", change_sources_after_enrichment)
    _assert_pending(raw_client.get(HOLDINGS_URL), "stale")


def test_financial_get_discards_old_response_when_new_generation_is_already_published(raw_client, monkeypatch):
    assert daily_snapshot_worker.run_daily_snapshot_recalculation_worker_once()
    original = workspace._public_holdings_workspace_response

    def refresh_after_enrichment(*args, **kwargs):
        response = original(*args, **kwargs)
        daily_snapshots.enqueue_selected_portfolio_daily_snapshot_recalculations([PORTFOLIO_ID])
        assert daily_snapshot_worker.run_daily_snapshot_recalculation_worker_once()
        return response

    monkeypatch.setattr(workspace, "_public_holdings_workspace_response", refresh_after_enrichment)
    _assert_pending(raw_client.get(HOLDINGS_URL), "changed")
