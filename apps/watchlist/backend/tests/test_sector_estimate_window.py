"""Retained PIT windows preserve reversals without promoting collection noise."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from watchlist_app.services import sector_estimates as estimates
from watchlist_app.services import research_triggers as triggers
from .test_sector_market_data import market_store, estimate_reader


def clock(day):
    return datetime(2026, 9, day, 12, tzinfo=UTC)


def row(value, *, period="2026-12-31", **extra):
    return {"symbol": "AAA", "estimate_period": "annual", "target_period_end": period,
            "revenue_avg": value, "eps_avg": 2, "currency": "USD",
            "num_analysts_revenue": 8, "num_analysts_eps": 7, **extra}


def test_window_preserves_intermediate_revision_and_return_to_baseline(market_store, estimate_reader):
    _, ingest = market_store
    for day, value in [(5, 100), (6, 110), (7, 100), (8, 100)]:
        ingest("analyst_estimates", [row(value)], clock(day).isoformat())
    evidence = estimate_reader(as_of=clock(9), since=clock(5))
    changes = evidence["changes"]
    assert [r["delta"] for r in changes] == [10, -10]
    assert [r["current_observation_at"] for r in changes] == [clock(6).isoformat(), clock(7).isoformat()]
    assert evidence["comparison_window"]["endpoint_change_count"] == 0
    assert len({r["current_source_id"] for r in changes}) == 2
    assert evidence["comparison_window"]["transition_count"] == 3
    metric = next(r for r in evidence["coverage"]["metrics"] if r["frequency"] == "annual" and r["metric"] == "revenue_avg")
    assert metric["changed_company_count"] == 1
    assert metric["changed_weight_pct"] == 99.9
    # A successfully covered window does not rediscover its prior transitions.
    assert estimate_reader(as_of=clock(9), since=clock(8))["changes"] == []


def test_rollover_empty_capture_and_currency_gaps_do_not_become_revisions(market_store, estimate_reader):
    store, ingest = market_store
    ingest("analyst_estimates", [row(100)], clock(5).isoformat())
    ingest("analyst_estimates", [row(110, period="2027-12-31")], clock(6).isoformat())
    store.ingest("analyst_estimates", [[]], source="fixture", observed_at=clock(7),
        details={"snapshot_scopes": [{"symbol": "AAA", "frequency": "annual", "snapshot_at": clock(7).isoformat()}]})
    ingest("analyst_estimates", [row(120, period="2027-12-31", currency="EUR")], clock(8).isoformat())
    evidence = estimate_reader(as_of=clock(9), since=clock(5))
    assert evidence["changes"] == []
    assert any(r["reason"] == "new_period_coverage" for r in evidence["unmatched"])
    assert any(r["reason"] == "missing_current_coverage" for r in evidence["unmatched"])


def test_window_can_span_more_than_one_hundred_captures_without_skipping_sparse_frequency():
    base = datetime(2025, 1, 1, tzinfo=UTC)
    captures = [{"symbol": "AAA", "estimate_period": frequency, "observed_at": (base + timedelta(days=day)).isoformat()}
                for frequency, days in [("annual", range(220)), ("quarter", range(0, 220, 3))] for day in days]
    calls = []
    def observations(dataset, *, symbols, as_of, limit):
        calls.append(as_of)
        rows = []
        for frequency in ("annual", "quarter"):
            eligible = sorted((r for r in captures if r["estimate_period"] == frequency
                and datetime.fromisoformat(r["observed_at"]) <= as_of), key=lambda r:r["observed_at"], reverse=True)
            rows.extend(eligible[:limit])
        return {"observations": rows}
    grouped = estimates._capture_window(SimpleNamespace(observations=observations), ["AAA"], base + timedelta(days=220), base + timedelta(days=1))
    assert len(grouped[("AAA", "annual")]) == 219
    assert len(grouped[("AAA", "quarter")]) == 74
    assert len(calls) == 3
    assert grouped[("AAA", "quarter")][0]["observed_at"] == base.isoformat()


def test_empty_fixed_company_scope_preserves_identity_and_coverage_semantics(monkeypatch):
    monkeypatch.setattr(estimates, "numeric_store", lambda: (_ for _ in ()).throw(AssertionError("No numeric query needed")))
    commodity = estimates.read_estimate_evidence(None, "gold", company_scope={
        "symbol": "GLDM", "instrument_type": "etf", "companies": [], "coverage": []})
    assert commodity["status"] == "not_applicable" and commodity["supported"] is True
    missing = estimates.read_estimate_evidence(None, "missing", company_scope={
        "symbol": None, "instrument_type": "etf", "companies": [], "coverage": ["未登记明确的FMP代码，未推断或替代。"]})
    assert missing["status"] == "unsupported" and missing["supported"] is False
    assert missing["gaps"] == ["未登记明确的FMP代码，未推断或替代。"]


def test_numeric_trigger_only_reports_same_basis_estimates_and_financial_content_versions(monkeypatch):
    from watchlist_app.services import research_fmp_scope, sector_market_data
    scope = {"instrument_id": "xlk", "instrument_type": "etf", "companies": [{"symbol": "AAA"}]}
    monkeypatch.setattr(research_fmp_scope, "build_fmp_research_scope", lambda *a, **k:{"instruments": [scope]})
    revisions = [{"symbol": "AAA", "frequency": "annual", "target_period_end": "2026-12-31", "metric": "eps_avg",
                  "delta": 1, "current_source_id": "numeric:revision:1", "previous_source_id": "numeric:baseline:1"}]
    seen = []
    def read(*a, **kw):
        seen.append(kw)
        return {"changes": revisions}
    monkeypatch.setattr(estimates, "read_estimate_evidence", read)
    monkeypatch.setattr(sector_market_data, "numeric_store", lambda: object())
    statement = {"symbol": "AAA", "statement_type": "income", "period_end": "2026-06-30", "fiscal_year": 2026,
                 "fiscal_period": "Q2", "row_index": 0}
    rows = [{**statement, "source_id": f"numeric:{day}:0", "batch_id": str(day), "observed_at": clock(day).isoformat(),
             "statement_content_sha256": content} for day, content in [(1,"A"),(3,"A"),(4,"B"),(5,"B"),(6,"A")]]
    monkeypatch.setattr(sector_market_data, "all_rows", lambda *a, **k: rows)
    result = triggers._numeric_changes(None, "xlk", clock(2), clock(7))
    assert [r["change"] for r in result] == ["consensus_revision", "revised_financial_statement", "revised_financial_statement"]
    assert [r["version_id"] for r in result] == ["numeric:revision:1", "numeric:4:0", "numeric:6:0"]
    assert seen[0]["since"] == clock(2) and seen[0]["company_scope"] == scope
    revisions.clear()
    assert triggers._numeric_changes(None, "xlk", clock(6), clock(7)) == []
    monkeypatch.setattr(research_fmp_scope, "build_fmp_research_scope", lambda *a, **k:{"instruments": []})
    assert triggers._numeric_changes(None, "unrelated", clock(2), clock(7)) == []


def test_failed_numeric_attempt_keeps_pending_reversal_and_apply_only_consumes_frozen_window(client, monkeypatch):
    from copy import deepcopy
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.services import research_monitor as monitor
    first = {"key": "estimate:AAA:annual:2026-12-31:eps_avg", "version_id": "numeric:A-to-B", "delta": 1}
    reverse = {**first, "version_id": "numeric:B-to-A", "delta": -1}
    current = [first]
    def trigger(*args, now, **kwargs):
        return {"reasons": [{"kind": "financial_input_change", "items": deepcopy(current)}] if current else [],
                "coverage_cursor": {"numeric_cutoff": now.isoformat()}}
    monkeypatch.setattr(triggers, "research_trigger", trigger)
    with get_session_factory()() as session:
        state, frozen = monitor.scan(session, "xlk", {"cutoff": clock(1).isoformat()}, team_id="default", now=clock(2))
        monitor.mark_attempt(state, "failed-first")
        session.commit()
        current.clear()
        _, quiet = monitor.scan(session, "xlk", {"cutoff": clock(2).isoformat()}, team_id="default", now=clock(3))
        assert quiet is None and len(state.pending_changes_json) == 1
        current.append(reverse)
        _, next_attempt = monitor.scan(session, "xlk", {"cutoff": clock(3).isoformat()}, team_id="default", now=clock(4))
        assert {item["value"]["delta"] for item in next_attempt["monitor_snapshot"]["changes"].values()} == {1, -1}
        run = SimpleNamespace(team_id="default", context_json={"instrument_ids": ["xlk"], "incremental_trigger": {"xlk": frozen}})
        monitor.mark_applied(session, run)
        assert [item["value"]["delta"] for item in state.pending_changes_json.values()] == [-1]
        run.context_json["incremental_trigger"]["xlk"] = next_attempt
        monitor.mark_applied(session, run)
        assert state.pending_changes_json == {}


def test_first_monitor_scan_uses_successful_numeric_boundary_not_failed_run_or_later_web_clock(client, monkeypatch):
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.services import sector_research as sector, research_monitor
    from .test_sector_research import seed_sector
    seed_sector(client, monkeypatch)
    captured = []
    def scan(session, instrument_id, context, **kwargs):
        captured.append(context)
        return None, None
    monkeypatch.setattr(research_monitor, "scan", scan)
    with get_session_factory()() as session:
        successful, _ = sector.begin_run(session, ["xlk"])
        successful.status = "completed"
        successful.context_json = {**successful.context_json, "cutoff": clock(5).isoformat(),
            "input_snapshot_cutoff": clock(2).isoformat()}
        session.commit()
        failed, _ = sector.begin_run(session, ["xlk"])
        failed.status = "failed"
        failed.context_json = {**failed.context_json, "cutoff": clock(8).isoformat(),
            "input_snapshot_cutoff": clock(7).isoformat()}
        session.commit()
        monkeypatch.setattr(sector, "automatic_recovery_runs", lambda *a, **k: {})
        sector.begin_run(session, ["xlk"], scheduled=True)
    assert captured[0]["last_successful_numeric_cutoff"] == clock(2).isoformat()
    assert captured[0]["input_snapshot_cutoff"] == clock(7).isoformat()
    from .test_research_triggers import session as fake_session
    monkeypatch.setattr(triggers, "_new_text", lambda *a, **k: [])
    monkeypatch.setattr(triggers, "_risk_changes", lambda *a, **k: [])
    monkeypatch.setattr(triggers, "_due_items", lambda *a, **k: [])
    monkeypatch.setattr("watchlist_app.services.research_dossier.material_index", lambda *a, **k: [])
    seen = []
    monkeypatch.setattr(triggers, "_numeric_changes", lambda session, iid, since, now: seen.append(since) or [])
    triggers.research_trigger(fake_session(), "xlk", captured[0], now=clock(9))
    assert seen == [clock(2)]


def test_workbench_uses_successful_frozen_input_cutoff_and_shared_company_scope(monkeypatch):
    from watchlist_app.services import research_workbench as workbench, sector_research, research_fmp_scope
    from watchlist_app.services import research_run_context, shared_instrument_registry
    scope = {"instrument_id": "xlk", "instrument_type": "etf", "companies": [{"symbol": "AAA"}]}
    monkeypatch.setattr(workbench, "catalogue", lambda session: [{"instrument_id": "xlk", "instrument_type": "etf"}])
    monkeypatch.setattr(workbench, "read_research_plan", lambda *a: {})
    monkeypatch.setattr(workbench, "analyst_guidance", lambda *a: {})
    monkeypatch.setattr(workbench, "read_research_records", lambda *a: {})
    monkeypatch.setattr(shared_instrument_registry, "get_shared_reference_data", lambda *a, **k: {})
    monkeypatch.setattr(sector_research, "latest_reviews", lambda *a, **k: {"xlk": {"run_id": "success", "checked_at": clock(6).isoformat()}})
    monkeypatch.setattr(research_run_context, "load_run_fields", lambda *a, **k: SimpleNamespace(context_json={"input_snapshot_cutoff": clock(2).isoformat()}))
    monkeypatch.setattr(research_fmp_scope, "build_fmp_research_scope", lambda *a, **k: {"instruments": [scope]})
    seen = []
    monkeypatch.setattr(estimates, "read_estimate_evidence", lambda *a, **k: seen.append(k) or {})
    session = SimpleNamespace(get=lambda *a, **k: None, scalars=lambda *a, **k: [])
    workbench._read_instrument_evidence(session, ["xlk"], as_of=clock(9), include_dossier=False)
    assert seen[0]["since"] == clock(2)
    assert seen[0]["company_scope"] == scope
