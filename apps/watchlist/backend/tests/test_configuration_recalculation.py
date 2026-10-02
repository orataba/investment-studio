from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from watchlist_app.db.models import InstrumentManualProfile
from watchlist_app.db.models.recalc import RecalcJob
from watchlist_app.db.models.watchlists import (
    InstrumentAttributeValue, InstrumentTaxonomyAssignment, InstrumentTaxonomyAssignmentHistory,
)
from watchlist_app.db.session import get_session_factory
from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
from watchlist_app.services.read_model_freshness import recalculation_freshness_overrides
from watchlist_app.services.recalc_worker import drain_recalc_jobs

IID = "sxv264"
EDITS = [
    ("post", f"/api/instrument-attributes/instruments/{IID}",
     {"values": [{"attribute_key": "primary_geographic_exposure", "value": "中国 A 股"}]}),
    ("put", f"/api/instrument-attributes/instruments/{IID}/settings",
     {"taxonomy_node_id": "fund-private-equity-quant-index-enhanced", "coverage_status": "Invested"}),
    ("put", f"/api/taxonomies/instrument-taxonomy/instruments/{IID}",
     {"node_id": "fund-private-equity-quant-stock-selection"}),
    ("put", f"/api/instruments/{IID}/nav-settings", {"nav_basis_preference": "nav_with_dividend"}),
]


def seed(client):
    wid = client.post("/api/watchlists", json={"name": "Configuration publication"}).json()["watchlist_id"]
    assert client.post(f"/api/watchlists/{wid}/items", json={"instrument_ids": [IID]}).status_code == 200
    assert drain_recalc_jobs() > 0
    return wid


def job(session, name, *, status="queued", job_type="all"):
    record = SQLAlchemyRecalcJobRepository().create(session, recalc_job_id=name, job_type=job_type,
        instrument_id=IID, trigger_type="configuration-test", trigger_ref_type=None, trigger_ref_id=None,
        job_status=status, priority=100, dedupe_key=name, payload_json={})
    return record


def test_edits_during_running_calculation_share_one_successor_and_expose_pending(client):
    wid = seed(client)
    before = client.get(f"/api/instruments/{IID}/performance").json()
    repo = SQLAlchemyRecalcJobRepository()
    with get_session_factory()() as session:
        running = job(session, "old-input-worker")
        repo.mark_running(session, running)
        session.commit()
    for method, path, payload in EDITS:
        response = getattr(client, method)(path, json=payload)
        assert response.status_code == 200, response.text
        assert not response.json().get("recalculated", False)
    with get_session_factory()() as session:
        pending = list(session.scalars(select(RecalcJob).where(RecalcJob.instrument_id == IID,
            RecalcJob.job_status.in_(("running", "queued")))))
        assert sorted(row.job_status for row in pending) == ["queued", "running"]
        assert session.get(RecalcJob, "old-input-worker").job_status == "running"
    for section in ("summary", "chart", "performance", "risk"):
        payload = client.get(f"/api/instruments/{IID}/{section}").json()
        assert payload["freshness"]["data_freshness_status"] == "pending_recalc"
    after = client.get(f"/api/instruments/{IID}/performance").json()
    assert before["snapshot_metadata"] == after["snapshot_metadata"]
    rows = client.post("/api/screener/query", json={"watchlist_id": wid,
        "selected_fields": ["instrument_name", "data_freshness_status"], "group_by": "none"}).json()["rows"]
    assert rows[0]["data_freshness_status"] == "pending_recalc"
    # Completing the earlier job cannot clear its queued successor.
    with get_session_factory()() as session:
        running = session.get(RecalcJob, "old-input-worker")
        assert repo.mark_completed(session, running, lease_token=running.lease_token)
        session.commit()
        assert recalculation_freshness_overrides(session, [IID])[IID]["data_freshness_status"] == "pending_recalc"
    assert drain_recalc_jobs() == 1
    with get_session_factory()() as session:
        assert recalculation_freshness_overrides(session, [IID]) == {}


@pytest.mark.parametrize("method,path,payload", EDITS)
def test_configuration_and_queue_roll_back_together(client, monkeypatch, method, path, payload):
    from watchlist_app.services import recalc
    seed(client)
    def snapshot():
        with get_session_factory()() as session:
            return {model.__tablename__: sorted((dict(row) for row in session.execute(select(model.__table__)).mappings()), key=repr)
                for model in (InstrumentAttributeValue, InstrumentTaxonomyAssignment,
                              InstrumentTaxonomyAssignmentHistory, InstrumentManualProfile, RecalcJob)}
    before = snapshot()
    def fail(*args, **kwargs):
        raise HTTPException(status_code=500, detail="queue unavailable")
    monkeypatch.setattr(recalc.jobs, "create", fail)
    assert getattr(client, method)(path, json=payload).status_code == 500
    assert snapshot() == before


@pytest.mark.parametrize("recovery_type,offset,cleared", [
    ("performance", 1, True), ("all", 1, True), ("exposure", 1, False),
    ("all", -1, False), ("all", 0, False),
])
def test_failed_configuration_requires_proven_later_calculation(client, recovery_type, offset, cleared):
    seed(client)
    # Ordering within one second matters. Equal legacy second-resolution clocks
    # cannot establish recovery, and an exposure publication never does.
    instant = datetime.now(UTC) + timedelta(seconds=10)
    instant = instant.replace(microsecond=500000)
    with get_session_factory()() as session:
        failed = job(session, "failed-configuration", status="failed")
        failed.finished_at = instant
        failed.started_at = instant - timedelta(microseconds=2)
        recovered = job(session, "subsequent-publication", status="completed", job_type=recovery_type)
        recovered.started_at = instant + timedelta(microseconds=offset)
        recovered.finished_at = instant + timedelta(microseconds=2)
        session.commit()
        state = recalculation_freshness_overrides(session, [IID])
        assert (state == {}) is cleared
        if not cleared:
            assert state[IID]["data_freshness_status"] == "stale"


def test_failed_successor_retains_old_result_and_explicit_performance_recovers(client, monkeypatch):
    from watchlist_app.services import recalc_worker
    seed(client)
    before = client.get(f"/api/instruments/{IID}/performance").json()["snapshot_metadata"]
    method, path, payload = EDITS[0]
    assert getattr(client, method)(path, json=payload).status_code == 200
    def fail(*args, **kwargs):
        raise RuntimeError("injected calculation failure")
    with monkeypatch.context() as patch:
        patch.setattr(recalc_worker.canonical_recalc_service, "_execute_recalc_job", fail)
        assert drain_recalc_jobs() == 1
    failed = client.get(f"/api/instruments/{IID}/performance").json()
    assert failed["snapshot_metadata"] == before
    assert failed["freshness"]["data_freshness_status"] == "stale"
    assert "failed" in failed["freshness"]["staleness_reason"]
    with get_session_factory()() as session:
        assert recalculation_freshness_overrides(session, [IID], configuration_only=True)[IID]["data_freshness_status"] == "stale"
    response = client.post(f"/api/recalc/instruments/{IID}/execute",
        json={"job_type": "performance", "trigger_type": "test"})
    assert response.status_code == 200, response.text
    with get_session_factory()() as session:
        assert recalculation_freshness_overrides(session, [IID]) == {}


@pytest.mark.parametrize("final_status", ["failed", "completed"])
def test_job_lifecycle_preserves_subsecond_ordering(client, monkeypatch, final_status):
    from watchlist_app.repositories.sqlalchemy import recalc_jobs
    seed(client)
    instant = datetime(2026, 10, 2, tzinfo=UTC).replace(microsecond=123456)
    class Clock:
        @staticmethod
        def now(tz):
            return instant
    monkeypatch.setattr(recalc_jobs, "datetime", Clock)
    with get_session_factory()() as session:
        record = job(session, "precise-clock")
        assert record.enqueued_at == instant
        repo = SQLAlchemyRecalcJobRepository()
        repo.mark_running(session, record)
        assert record.started_at == instant
        if final_status == "failed":
            assert repo.mark_failed(session, record, lease_token=record.lease_token, error_message="test")
        else:
            assert repo.mark_completed(session, record, lease_token=record.lease_token)
        session.refresh(record)
        assert record.finished_at.replace(tzinfo=UTC) == instant


def test_membership_refresh_does_not_block_research_but_coalesced_settings_do(client):
    from watchlist_app.services.recalc import queue_configuration_recalculation
    from watchlist_app.services.watchlist_updates import queue_watchlist_recalculation
    from watchlist_app.services.read_model_freshness import calculation_input_read
    from watchlist_app.services.research_errors import ResearchInputUnavailable
    from watchlist_app.services.risk_performance import performance_context, performance_evidence
    seed(client)
    with get_session_factory()() as session:
        queue_watchlist_recalculation(session, instrument_id=IID, watchlist_id="another-list")
        session.commit()
        membership = session.scalar(select(RecalcJob).where(RecalcJob.instrument_id == IID, RecalcJob.job_status == "queued"))
        membership_id = membership.recalc_job_id
        with calculation_input_read(session, [IID]):
            pass
        assert performance_evidence(session, IID)["available"] is True
        result = queue_configuration_recalculation(session, instrument_id=IID,
            trigger_type="calculation_settings_changed", trigger_ref_type="nav_settings", trigger_ref_id=IID)
        session.commit()
        assert result["recalc_job_id"] == membership_id
        assert membership.payload_json["configuration_changed"] is True
        with pytest.raises(ResearchInputUnavailable):
            performance_evidence(session, IID)
        with pytest.raises(ResearchInputUnavailable):
            performance_context(session, [IID])
    assert drain_recalc_jobs() == 1
    with get_session_factory()() as session:
        assert performance_evidence(session, IID)["available"] is True
        assert recalculation_freshness_overrides(session, [IID], configuration_only=True) == {}


def test_pending_calculations_are_not_new_research_or_numeric_evidence(client):
    from watchlist_app.services import research_metrics, research_observations, research_workbench, risk_officer
    from watchlist_app.services.research_errors import ResearchInputUnavailable
    seed(client)
    method, path, payload = EDITS[0]
    assert getattr(client, method)(path, json=payload).status_code == 200
    with get_session_factory()() as session:
        with pytest.raises(ResearchInputUnavailable):
            research_workbench.instrument_evidence(session, [IID], include_dossier=False)
        snapshot = risk_officer.read_snapshot(session, instrument_id=IID)
        assert snapshot["scope_available"] is False
        assert snapshot["input_retryable"] is True
        assert snapshot["instruments"] == []
        cutoff = datetime.now(UTC) + timedelta(seconds=1)
        evidence = research_metrics.instrument_price_risk(session, IID, as_of=cutoff)
        assert evidence["data"]["status"] == "unavailable"
        assert "数值重算" in evidence["data"]["limitations"][0]
        evidence = research_observations.instrument_observations(session, IID, as_of=cutoff)
        assert evidence["data"]["status"] == "unavailable"
        assert evidence["data"]["metrics"] == {}
        assert "数值重算" in evidence["data"]["limitations"][0]


def test_configured_comparator_pending_blocks_new_evidence(client):
    from watchlist_app.services.recalc import queue_configuration_recalculation
    from watchlist_app.services.risk_performance import performance_context, performance_evidence
    from watchlist_app.services.research_errors import ResearchInputUnavailable
    wid = seed(client)
    assert client.post(f"/api/watchlists/{wid}/items", json={"instrument_ids": ["savf63"]}).status_code == 200
    drain_recalc_jobs()
    with get_session_factory()() as session:
        profile = InstrumentManualProfile(instrument_id=IID, updated_at=datetime.now(UTC),
            nav_settings_json={"default_benchmark_instrument_id": "savf63"})
        session.add(profile)
        queue_configuration_recalculation(session, instrument_id="savf63",
            trigger_type="calculation_settings_changed", trigger_ref_type="nav_settings", trigger_ref_id="savf63")
        session.commit()
        with pytest.raises(ResearchInputUnavailable, match="savf63"):
            performance_evidence(session, IID)
        with pytest.raises(ResearchInputUnavailable, match="savf63"):
            performance_context(session, [IID])


def test_pending_configuration_retries_preparation_without_freezing_inputs(client, monkeypatch, research_test_actor):
    from watchlist_app.services import research_runner, risk_officer
    from watchlist_app.db.models.workbench import ResearchEntry
    seed(client)
    method, path, payload = EDITS[0]
    assert getattr(client, method)(path, json=payload).status_code == 200
    monkeypatch.setattr(research_runner, "resolve_token", lambda *args: research_test_actor)
    monkeypatch.setattr(research_runner.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Inputs are not ready for a model"))
    with get_session_factory()() as session:
        run, created = risk_officer.begin_run(session, instrument_id=IID)
        assert created
        run_id = run.entry_id
    research_runner._run_analysis(run_id)
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        assert run.status == "failed"
        assert run.context_json["risk_inputs"]["scope_available"] is False
        assert "input_snapshot_cutoff" not in run.context_json
        assert run.context_json["runtime_error"]["type"] == "ResearchInputUnavailable"
        assert research_runner.automatic_retry_due(run.context_json, run.completed_at, run.body,
            now=datetime.now(UTC) + timedelta(minutes=10))


def test_pending_listed_event_tool_retains_unavailable_output_schema(client):
    from watchlist_app.services.recalc import queue_configuration_recalculation
    from watchlist_app.services.research_observations import instrument_event_reaction
    from watchlist_app.services.research_quant import QuantOutput
    wid = seed(client)
    assert client.post(f"/api/watchlists/{wid}/items", json={"instrument_ids": ["fund-us-agg"]}).status_code == 200
    drain_recalc_jobs()
    with get_session_factory()() as session:
        queue_configuration_recalculation(session, instrument_id="fund-us-agg",
            trigger_type="calculation_settings_changed", trigger_ref_type="nav_settings", trigger_ref_id="fund-us-agg")
        session.commit()
        evidence = instrument_event_reaction(session, "fund-us-agg", as_of=datetime.now(UTC) + timedelta(seconds=1),
            event_date="2026-04-15")
        assert evidence["data"]["status"] == "unavailable"
        assert "数值重算" in evidence["data"]["limitations"][0]
        QuantOutput.model_validate({key: evidence["data"][key]
            for key in ("summary", "metrics", "tables", "charts", "limitations")})
