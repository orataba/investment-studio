from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from watchlist_app.db.models import InstrumentChartReadModel, InstrumentManualProfile, InstrumentPerformanceReadModel
from watchlist_app.db.models.recalc import RecalcJob
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import read_model_freshness, risk_performance
from watchlist_app.services.recalc_worker import drain_recalc_jobs
from watchlist_app.services.research_errors import ResearchInputUnavailable

IID = "sxv264"


def seed(client):
    wid = client.post("/api/watchlists", json={"name": "Input read consistency"}).json()["watchlist_id"]
    assert client.post(f"/api/watchlists/{wid}/items", json={"instrument_ids": [IID]}).status_code == 200
    assert drain_recalc_jobs() == 1


def edit(client):
    response = client.put(f"/api/instruments/{IID}/nav-settings", json={"nav_basis_preference": "nav_with_dividend"})
    assert response.status_code == 200


def snapshot_id(session):
    return session.get(InstrumentPerformanceReadModel, IID).payload_json["comparison_basis"]["snapshot_id"]


def test_pending_input_is_rejected_even_if_worker_finishes_before_check_returns(client, monkeypatch):
    seed(client)
    edit(client)
    original = read_model_freshness._recalculation_state
    calls = 0

    def finish_after_capture(session, ids, **kwargs):
        nonlocal calls
        state = original(session, ids, **kwargs)
        calls += 1
        if calls == 1:
            assert drain_recalc_jobs() == 1
        return state

    monkeypatch.setattr(read_model_freshness, "_recalculation_state", finish_after_capture)
    with get_session_factory()() as session:
        with pytest.raises(ResearchInputUnavailable):
            risk_performance.performance_context(session, [IID])
    with get_session_factory()() as session:
        assert original(session, [IID], configuration_only=True)[0] == {}


def test_configuration_saved_and_completed_during_read_rejects_old_rows(client, monkeypatch):
    seed(client)
    original = risk_performance._load_performance_context
    snapshots = {}

    def finish_after_load(session, ids, **kwargs):
        context = original(session, ids, **kwargs)
        snapshots["retained"] = context.get(InstrumentPerformanceReadModel, IID).payload_json["comparison_basis"]["snapshot_id"]
        edit(client)
        assert drain_recalc_jobs() == 1
        with get_session_factory()() as current:
            snapshots["completed"] = snapshot_id(current)
            job = current.scalar(select(RecalcJob).where(RecalcJob.instrument_id == IID,
                RecalcJob.payload_json["configuration_changed"].as_boolean().is_(True)))
            assert job.job_status == "completed"
        return context

    monkeypatch.setattr(risk_performance, "_load_performance_context", finish_after_load)
    with get_session_factory()() as session:
        with pytest.raises(ResearchInputUnavailable, match="读取期间"):
            risk_performance.performance_context(session, [IID])
    assert snapshots["retained"] != snapshots["completed"]


def test_stable_read_refreshes_preexisting_session_identity_map(client):
    seed(client)
    with get_session_factory()() as session:
        # Retain strong references, as a caller assembling other inputs may do.
        old_performance = session.get(InstrumentPerformanceReadModel, IID)
        old_chart = session.get(InstrumentChartReadModel, IID)
        before = snapshot_id(session)
        edit(client)
        assert drain_recalc_jobs() == 1
        context = risk_performance.performance_context(session, [IID])
        with get_session_factory()() as current:
            completed = snapshot_id(current)
        assert before != completed
        assert context.get(InstrumentPerformanceReadModel, IID) is old_performance
        assert snapshot_id(session) == completed
        assert context.get(InstrumentChartReadModel, IID) is old_chart
        assert context.get(InstrumentManualProfile, IID).nav_settings_json["nav_basis_preference"] == "nav_with_dividend"
        assert risk_performance.performance_evidence(session, IID, context=context)["available"]


@pytest.mark.parametrize("reader", ["workbench", "price_risk", "observations", "event_reaction"])
def test_new_configuration_during_evidence_assembly_is_not_published(client, monkeypatch, reader):
    from watchlist_app.services import research_metrics, research_observations, research_workbench
    seed(client)
    if reader == "workbench":
        module, name = research_workbench, "_read_instrument_evidence"
    elif reader == "price_risk":
        module, name = research_metrics, "_read_instrument_price_risk"
    else:
        module, name = research_observations, "_read_snapshot"
    original = getattr(module, name)
    changed = False

    def finish_after_read(*args, **kwargs):
        nonlocal changed
        result = original(*args, **kwargs)
        if not changed:
            changed = True
            edit(client)
            assert drain_recalc_jobs() == 1
        return result

    monkeypatch.setattr(module, name, finish_after_read)
    with get_session_factory()() as session:
        cutoff = datetime.now(UTC) + timedelta(seconds=1)
        if reader == "workbench":
            with pytest.raises(ResearchInputUnavailable, match="读取期间"):
                research_workbench.instrument_evidence(session, [IID], include_dossier=False)
        elif reader == "price_risk":
            result = research_metrics.instrument_price_risk(session, IID, as_of=cutoff)
        elif reader == "observations":
            result = research_observations.instrument_observations(session, IID, as_of=cutoff)
        else:
            result = research_observations.instrument_event_reaction(session, IID, as_of=cutoff, event_date="2026-04-13")
        if reader != "workbench":
            assert result["data"]["status"] == "unavailable"
            assert "读取期间" in result["data"]["limitations"][0]
        assert changed


def test_risk_snapshot_rejects_configuration_completed_after_workspace_read(client, monkeypatch):
    from watchlist_app.api.routes import workbench
    from watchlist_app.services import risk_officer
    seed(client)
    original = workbench.risk_workspace

    def finish_after_workspace(*args, **kwargs):
        result = original(*args, **kwargs)
        edit(client)
        assert drain_recalc_jobs() == 1
        return result

    monkeypatch.setattr(workbench, "risk_workspace", finish_after_workspace)
    with get_session_factory()() as session:
        result = risk_officer.read_snapshot(session, instrument_id=IID)
    assert result["scope_available"] is False
    assert result["input_retryable"] is True
    assert result["instruments"] == []
    assert any("读取期间" in value for value in result["limitations"])


def test_risk_snapshot_refreshes_retained_risk_model(client):
    from watchlist_app.db.models import InstrumentRiskReadModel
    from watchlist_app.services import risk_officer
    seed(client)
    with get_session_factory()() as reader:
        retained = reader.get(InstrumentRiskReadModel, IID)
        with get_session_factory()() as writer:
            current = writer.get(InstrumentRiskReadModel, IID)
            current.payload_json = {**current.payload_json, "current_drawdown": -42.0}
            writer.commit()
        assert retained.payload_json["current_drawdown"] != -42.0
        result = risk_officer.read_snapshot(reader, instrument_id=IID)
        assert result["scope_available"] is True
        assert result["instruments"][0]["risk"]["current_drawdown"] == -42.0
