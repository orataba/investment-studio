from sqlalchemy import event, select

from watchlist_app.db.models.read_models import InstrumentChartReadModel
from watchlist_app.db.models.recalc import RecalcJob
from watchlist_app.db.session import get_engine, get_session_factory
from watchlist_app.services.canonical_recalc import CanonicalRecalcService
from watchlist_app.services.read_models import instrument_quote_payload, serialize_payload


def materialize(client, iid="sxv264"):
    assert client.post(f"/api/instruments/{iid}/resolve").status_code == 200
    response = client.post(f"/api/recalc/instruments/{iid}/execute", json={"job_type": "all"})
    assert response.status_code == 200, response.text


def test_foreground_reads_never_load_market_or_nav_audit_history(client, monkeypatch):
    from investment_studio_instrument_core import instrument_store
    materialize(client)
    assert client.get("/api/watchlists/all-instruments").status_code == 200
    def forbidden(*args, **kwargs):
        raise AssertionError("Full Registry detail/online NAV calculation reached from GET")
    monkeypatch.setattr(instrument_store, "get_instrument", forbidden)
    monkeypatch.setattr(CanonicalRecalcService, "build_nav_series_payload", forbidden)
    from watchlist_app.api.routes import funds, monitoring
    monkeypatch.setattr(funds.read_model_repository, "get_chart", forbidden)
    monkeypatch.setattr(monitoring.read_model_repository, "list_charts", forbidden)
    statements = []
    def trace(_conn, _cursor, statement, *_):
        statements.append(statement)
    event.listen(get_engine(), "before_cursor_execute", trace)
    try:
        for path in ("summary", "performance", "risk", "chart?projection=quote", "nav-series"):
            response = client.get(f"/api/instruments/sxv264/{path}")
            assert response.status_code == 200, response.text
        assert client.post("/api/instruments/sxv264/resolve").status_code == 200
        assert client.get("/api/monitoring/dashboard").status_code == 200
    finally:
        event.remove(get_engine(), "before_cursor_execute", trace)
    assert not any("fund_nav_adjustment_factor" in sql or "fund_nav_projection_run" in sql for sql in statements)


def test_nav_projection_retains_generation_and_queues_missing_repair(client, monkeypatch):
    from watchlist_app.services.recalc_worker import drain_recalc_jobs
    materialize(client)
    with get_session_factory()() as session:
        expected = serialize_payload(CanonicalRecalcService().build_nav_series_payload(session, instrument_id="sxv264"))
        chart = session.get(InstrumentChartReadModel, "sxv264")
        assert chart.nav_payload_json == expected
        assert chart.nav_payload_json["return_series"] == chart.payload_json["return_series"]
        cutoff, calculated = chart.source_cutoff_at, chart.last_recalculated_at
    first = client.get("/api/instruments/sxv264/nav-series").json()
    assert {key: first[key] for key in expected} == expected
    assert first["source_cutoff_at"] == serialize_payload(cutoff)
    assert first["last_recalculated_at"] == serialize_payload(calculated)
    assert client.post("/api/instruments/savf63/resolve").status_code == 200
    response = client.put("/api/instruments/sxv264/nav-settings", json={"default_benchmark_instrument_id": "savf63"})
    assert response.status_code == 200, response.text
    pending = client.get("/api/instruments/sxv264/nav-series").json()
    assert pending["compare_settings"]["default_benchmark_instrument_id"] == "savf63"
    assert pending["return_series"] == first["return_series"]
    assert pending["source_cutoff_at"] == first["source_cutoff_at"]
    assert pending["freshness"]["data_freshness_status"] == "pending_recalc"
    drain_recalc_jobs()
    assert client.get("/api/instruments/sxv264/nav-series").json()["compare_settings"]["default_benchmark_instrument_id"] == "savf63"
    with get_session_factory()() as session:
        session.get(InstrumentChartReadModel, "sxv264").nav_payload_json = None
        session.commit()
    response = client.get("/api/instruments/sxv264/nav-series")
    assert response.status_code == 503 and response.headers["Retry-After"] == "1"
    with get_session_factory()() as session:
        assert session.scalar(select(RecalcJob).where(RecalcJob.instrument_id == "sxv264", RecalcJob.job_status == "queued"))
    drain_recalc_jobs()
    assert client.get("/api/instruments/sxv264/nav-series").status_code == 200


def test_chart_display_and_quote_project_without_changing_full_contract(client):
    materialize(client)
    source = client.get("/api/instruments/sxv264/chart").json()
    display = client.get("/api/instruments/sxv264/chart?projection=display").json()
    quote = client.get("/api/instruments/sxv264/chart?projection=quote").json()
    assert display == {key: value for key, value in source.items() if key not in {"return_series", "research_returns"}}
    assert quote == instrument_quote_payload(source)
    with get_session_factory()() as session:
        chart = session.get(InstrumentChartReadModel, "sxv264")
        assert "return_series" in chart.payload_json and "research_returns" in chart.payload_json


def test_chart_projection_preserves_explicit_null_and_omits_absent_keys(client):
    materialize(client)
    source = {"selected_series": None, "date_range": None, "currency": None, "series": []}
    with get_session_factory()() as session:
        session.get(InstrumentChartReadModel, "sxv264").payload_json = source
        session.commit()
        from watchlist_app.repositories.sqlalchemy.read_models import SQLAlchemyReadModelRepository
        repository = SQLAlchemyReadModelRepository()
        assert repository.get_chart_projection(session, "sxv264", projection="display") == source
        assert instrument_quote_payload(repository.get_chart_projection(session, "sxv264", projection="quote")) == instrument_quote_payload(source)


def test_archived_nav_keeps_retained_projection_and_does_not_offer_impossible_repair(client):
    from investment_studio_instrument_core import instrument_store
    materialize(client)
    before = client.get("/api/instruments/sxv264/nav-series").json()
    instrument_store.archive_instrument(get_session_factory(), instrument_id="sxv264", updated_by="pytest")
    with get_session_factory()() as session:
        jobs_before = set(session.scalars(select(RecalcJob.recalc_job_id)))
        archived_payload = serialize_payload(CanonicalRecalcService().build_nav_series_payload(session, instrument_id="sxv264"))
    after = client.get("/api/instruments/sxv264/nav-series").json()
    assert after["return_series"] == before["return_series"]
    assert after["source_cutoff_at"] == before["source_cutoff_at"]
    with get_session_factory()() as session:
        session.get(InstrumentChartReadModel, "sxv264").nav_payload_json = None
        session.commit()
    missing = client.get("/api/instruments/sxv264/nav-series")
    assert missing.status_code == 200
    assert missing.json() == archived_payload
    assert "source_cutoff_at" not in missing.json()
    with get_session_factory()() as session:
        assert set(session.scalars(select(RecalcJob.recalc_job_id))) == jobs_before


def test_scoped_peer_context_matches_global_comparison_and_exclusion_counts(client, monkeypatch):
    from .test_risk_performance_peers import seed, TARGET
    seed(client)
    service = CanonicalRecalcService()
    observed_ids = []
    read = service.snapshot_repository.list_current_performance
    def capture(session, *, instrument_ids):
        observed_ids.append(set(instrument_ids))
        return read(session, instrument_ids=instrument_ids)
    with get_session_factory()() as session:
        global_context = service._peer_comparison_context(session)
        expected = service.current_peer_comparison(session, instrument_id=TARGET, context=global_context)
        monkeypatch.setattr(service.snapshot_repository, "list_current_performance", capture)
        actual = service.current_peer_comparison(session, instrument_id=TARGET)
    assert actual == expected
    assert observed_ids == [{TARGET, "peer-same"}]
    assert actual["excluded_dimension_mismatch_count"] == 1


def test_idle_worker_reconciles_time_freshness_without_sweeping_price_histories(monkeypatch):
    from types import SimpleNamespace
    from threading import Event
    from watchlist_app.services import recalc_worker, risk_workbench
    from watchlist_app.services.read_model_freshness import ReconciliationBatchResult
    stop = Event()
    calls = []
    monkeypatch.setattr(recalc_worker, "get_settings", lambda: SimpleNamespace(
        recalc_worker_poll_interval_seconds=1, recalc_worker_reconcile_interval_seconds=60,
        recalc_worker_reconcile_batch_size=2))
    def reconcile(**kwargs):
        calls.append(kwargs)
        return ReconciliationBatchResult(2, 1, None, True)
    def process():
        stop.set()
        return True
    def forbidden(*args, **kwargs):
        raise AssertionError("Unchanged price histories must not be swept")
    monkeypatch.setattr(recalc_worker, "reconcile_stale_instrument_read_models", reconcile)
    monkeypatch.setattr(recalc_worker, "process_next_recalc_job", process)
    monkeypatch.setattr(risk_workbench, "refresh_risk_cases", forbidden)
    recalc_worker.run_recalc_worker_loop(stop_event=stop)
    assert calls == [{"limit": 2, "after_instrument_id": None}]
