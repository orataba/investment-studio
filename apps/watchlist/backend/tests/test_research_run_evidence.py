"""New calculations must not deserialize/rewrite unrelated retained context."""
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select
from studio_identity import current_principal, principal_context

from watchlist_app.api.routes import sector_research as routes
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.models.research_run_evidence import ResearchRunEvidence
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_run_evidence as evidence, quant_sandbox
from watchlist_app.services.research_quant import QuantAnalysisInput

CUTOFF = "2026-10-01T00:00:00+00:00"
IID = "fund-us-agg"


def source(source_id="computed:old", value=10, **extra):
    return {"source_id": source_id, "source_type": "computed_metric", "scope": "instrument", "instrument_id": IID,
        "as_of": CUTOFF, "methodology": "Actual retained sample", "data": {"value": value}, **extra}


@pytest.mark.parametrize("fields,domain", [
    ({"data": {"analysis_kind": "watchlist_observations"}}, "market"),
    ({"data": {"analysis_kind": "event_market_reaction"}}, "market"),
    ({"methodology": {"metric": "ewma_volatility"}}, "market"),
    ({"input_series": []}, "market"),
    ({"data": {"dataset": "macro_series"}, "methodology": {"field": "close"}}, "market"),
    ({"observation_domain": "fundamental", "input_series": []}, "fundamental"),
    ({"observation_domain": "market"}, "market"),
])
def test_computed_reference_keeps_real_domain_contract(fields, domain):
    reference = evidence.computed_reference(source(**fields), run_id="origin")
    assert reference["observation_domain"] == domain
    assert evidence.computed_reference(reference)["observation_domain"] == domain
    assert "data" not in reference


def test_unclassified_metadata_does_not_invent_persisted_domain():
    reference = evidence.computed_reference(source(), run_id="origin")
    assert "observation_domain" not in reference


def test_repeated_notebook_sources_resolve_once_per_evidence_build(stored_run):
    from watchlist_app.services.research_notebook import research_sources
    reference = evidence.computed_reference(source(), run_id="evidence-run")
    counts = []
    for repetitions in (1, 20):
        context = {"cutoff": CUTOFF, "research_dossiers": [{"instrument_id": IID,
            "prior_sources": [deepcopy(reference)] * repetitions,
            "notebook": {"sources": [deepcopy(reference)] * repetitions},
            "themes": [{"sources": [deepcopy(reference)] * repetitions}]}]}
        with get_session_factory()() as session:
            statements = []
            event.listen(session, "do_orm_execute", lambda state: statements.append(state.statement))
            result = research_sources(context, "evidence-run", session=session)
            counts.append(len(statements))
            assert result["computed:old"]["data"] == {"value": 10}
            assert result["computed:old"]["as_of"] == CUTOFF
            assert "data" not in context["research_dossiers"][0]["prior_sources"][0]
    assert counts[0] == counts[1], f"Duplicate original references must not multiply SQL: {counts}"


def test_evidence_build_does_not_merge_different_source_owners_or_versions(stored_run):
    from watchlist_app.services.market_evidence import SourceHydrator
    ref = evidence.computed_reference(source(), run_id="evidence-run")
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "evidence-run")
        run.context_json = {**run.context_json, "referenced_research_versions": [
            {"version_id": "earlier", "instrument_id": IID, "information_cutoff": CUTOFF,
             "sources": [source(value=3)]}]}
        session.commit()
        hydrate = SourceHydrator(session=session)
        assert hydrate(ref, bound_source=True)["data"]["value"] == 10
        historical = {**ref, "retained_reference": {"run_id": "evidence-run",
            "path": ["referenced_research_versions", "*", "sources", "*"], "version_id": "earlier"}}
        assert hydrate(historical, bound_source=True)["data"]["value"] == 3
        with pytest.raises(ValueError, match="标的归属不一致"):
            hydrate({**ref, "instrument_id": "another-instrument"}, bound_source=True)
        with pytest.raises(ValueError, match="不存在"):
            hydrate({**historical, "retained_reference": {**historical["retained_reference"], "version_id": "missing"}}, bound_source=True)


def test_bound_source_reuse_does_not_grant_private_topic_access(stored_run):
    from watchlist_app.services.market_evidence import SourceHydrator
    actor = replace(current_principal(), local_unrestricted=False)
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="private-evidence-owner", title="Private", visibility="private",
            created_by_user_id="another-user", team_id=actor.team_id))
        session.flush()
        session.add(ResearchEntry(entry_id="private-evidence-original", topic_id="private-evidence-owner",
            team_id=actor.team_id, kind="analysis", title="Original", status="completed",
            context_json={"computed_metrics": [source()]}))
        session.commit()
        ref = evidence.computed_reference(source(), run_id="private-evidence-original")
        with principal_context(actor):
            hydrate = SourceHydrator(session=session)
            assert hydrate(ref, bound_source=True)["data"] == {"value": 10}
            with pytest.raises(HTTPException) as denied:
                hydrate(ref)
            assert denied.value.status_code == 404


@pytest.mark.parametrize("retained", [False, True])
def test_display_projection_keeps_results_and_clocks_without_inputs(stored_run, retained):
    original = source("computed:figure", data={"analysis_kind": "watchlist_observations",
        "metrics": {"成交量较前一交易日（%）": 8.5}, "tables": [], "charts": [],
        "observations": {"volume": {"current": {"date": "2026-09-29", "value": 217},
            "previous": {"date": "2026-09-28", "value": 200}}, "ewma": {"input_points": [100] * 1000}},
        "input_series": [100] * 1000, "input_snapshot": {"raw": "private"}, "sources": [{"text": "private"}],
        "series": [{"series_id": "fixture", "first": {"date": "2026-09-28", "value": 200},
            "latest": {"date": "2026-09-29", "value": 217}, "observations": 2, "points": [100] * 1000}]},
        methodology={"description": "Observed calculation", "input_sources": [{"data": {"private": True}}], "code": "private"},
        input_snapshot={"large": "private"})
    with get_session_factory()() as session:
        if retained:
            run = evidence.locked_computation_run(session, "evidence-run")
            evidence.retain_computed_source(session, run, original)
        else:
            run = session.get(ResearchEntry, "evidence-run")
            run.context_json = {**run.context_json, "computed_metrics": [original]}
        session.commit()
        ref = evidence.computed_reference(original, run_id="evidence-run")
        classified = evidence.resolve_reference(ref, session=session, projection="classification")
        assert classified["observation_domain"] == "market"
        assert "metrics" not in classified["data"]
        figure = evidence.resolve_reference(ref, session=session, projection="figure")
        assert figure["data"]["metrics"] == original["data"]["metrics"]
        assert figure["data"]["observation_clocks"]["volume"] == {
            "current_date": "2026-09-29", "previous_date": "2026-09-28"}
        assert "observations" not in figure["data"]
        assert figure["data"]["series"][0]["observations"] == 2
        assert "points" not in figure["data"]["series"][0]
        assert figure["methodology"] == {"description": "Observed calculation"}
        assert "private" not in json.dumps(figure)
        assert "input_" not in json.dumps(figure)
        assert figure["display_projection"] == "figure"
        assert evidence.resolve_reference(ref, session=session)["data"] == original["data"]


def test_inline_figure_preserves_estimate_coverage_and_holdings():
    original = {"source_id": "estimates:old", "source_type": "analyst_estimate_changes",
        "current_snapshot": {"collected_at": CUTOFF}, "previous_snapshot": None, "changes": [],
        "coverage": {"status": "incomparable"}, "gaps": ["No earlier snapshot"],
        "snapshot": {"top_holdings": [{"symbol": "ABC", "weight_percent": 5}],
            "holdings_as_of": "2026-09-01", "holdings_observed_on": "2026-10-01", "private": "raw"}}
    figure = evidence.resolve_reference(original, projection="figure")
    assert figure["changes"] == [] and figure["coverage"] == original["coverage"]
    assert figure["gaps"] == original["gaps"]
    assert figure["snapshot"]["top_holdings"] == original["snapshot"]["top_holdings"]
    assert "private" not in figure["snapshot"]


@pytest.fixture
def stored_run(client):
    actor = current_principal()
    context = {"research_run": True, "research_actor": actor.to_dict(), "execution": {"attempt": 1},
        "instrument_ids": [IID], "cutoff": CUTOFF, "input_snapshot_cutoff": CUTOFF,
        "computed_metrics": [source()], "instrument_inputs": [{"instrument_id": IID, "snapshot_cutoff": CUTOFF}],
        "unrelated_archive": {"large_original": "unrelated-original-" * 100000}}
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="evidence-test-topic", title="Research", instrument_ids=[IID],
            visibility="team", team_id=actor.team_id))
        session.flush()
        session.add(ResearchEntry(entry_id="evidence-run", topic_id="evidence-test-topic", title="Research",
            team_id=actor.team_id, kind="analysis", status="running", context_json=context))
        session.commit()
    with principal_context(replace(actor, resource_scope={"kind": "run", "id": "evidence-run", "attempt": "1"})):
        yield context


def test_numeric_retention_reads_small_metadata_and_does_not_touch_original_context(stored_run, monkeypatch):
    from watchlist_app.services import research_metrics
    monkeypatch.setattr(research_metrics, "research_numeric_data", lambda **kwargs: source("computed:new", 20))
    statements = []
    with get_session_factory()() as session:
        engine = session.get_bind()
        def record(_connection, _cursor, statement, _params, _context, _many):
            statements.append(statement)
        event.listen(engine, "before_cursor_execute", record)
        try:
            result = routes.numeric_research("evidence-run", routes.NumericResearchInput(), session)
        finally:
            event.remove(engine, "before_cursor_execute", record)
        assert result["source_id"] == "computed:new"
        assert not any(sql.startswith("SELECT research_entry.context_json ") for sql in statements)
        assert not any("UPDATE research_entry" in sql for sql in statements)
        assert session.scalar(select(ResearchEntry.context_json).where(ResearchEntry.entry_id == "evidence-run")) == stored_run
        saved = evidence.read_computed_source(session, "evidence-run", result["source_id"])
        assert saved["data"]["value"] == 20 and saved["source_run_id"] == "evidence-run"
        assert session.get(ResearchRunEvidence, ("evidence-run", result["source_id"])).attempt == 1
        assert evidence.read_computed_source(session, "evidence-run", "computed:old")["data"]["value"] == 10


def test_quant_only_reads_selected_sources_and_retains_reference_inputs(stored_run, monkeypatch):
    seen = []
    monkeypatch.setattr(quant_sandbox, "execute", lambda code, inputs, params: (
        seen.append(deepcopy(inputs)) or {"summary": "Observed", "metrics": {"value": 10}}, {"backend": "test"}))
    request = QuantAnalysisInput(instrument_id=IID, title="Observed", source_ids=["computed:old"],
        code="result = {}", methodology="Read selected retained observation")
    with get_session_factory()() as session:
        receipt = routes.run_quant("evidence-run", request, session)
        result = evidence.read_computed_source(session, "evidence-run", receipt["source_id"])
        assert set(seen[0]) == {"computed:old"}
        assert seen[0]["computed:old"]["data"]["value"] == 10
        retained_input = result["methodology"]["input_sources"][0]
        assert retained_input["source_id"] == "computed:old" and "data" not in retained_input
        assert evidence.resolve_reference(retained_input, session=session)["data"]["value"] == 10
        assert session.scalar(select(ResearchEntry.context_json).where(ResearchEntry.entry_id == "evidence-run")) == stored_run
        catalogue = routes.quant_inputs("evidence-run", IID, session=session)["sources"]
        assert {item["source_id"] for item in catalogue} == {"computed:old", receipt["source_id"], f"instrument:evidence-run:{IID}"}
        assert len(json.dumps(catalogue)) < 5000


def test_stale_attempt_and_unbound_source_are_rejected_before_sandbox(stored_run, monkeypatch):
    monkeypatch.setattr(quant_sandbox, "execute", lambda *args: pytest.fail("must not execute"))
    with get_session_factory()() as session:
        run = evidence.locked_computation_run(session, "evidence-run")
        with pytest.raises(ValueError, match="未在本轮留存"):
            evidence.selected_quant_sources(session, run, IID, ["computed:guessed-other-run"])
    actor = current_principal()
    with principal_context(replace(actor, resource_scope={"kind": "run", "id": "evidence-run", "attempt": "2"})):
        with get_session_factory()() as session, pytest.raises(HTTPException) as error:
            evidence.locked_computation_run(session, "evidence-run")
        assert error.value.status_code == 409


def test_explicit_historical_namespace_does_not_substitute_current_source(stored_run):
    historical = {"version_id": "old-version", "instrument_id": IID,
        "information_cutoff": CUTOFF, "sources": [source(value=3)]}
    with get_session_factory()() as session:
        row = session.get(ResearchEntry, "evidence-run")
        row.context_json = {**row.context_json, "referenced_research_versions": [historical]}
        session.commit()
        run = evidence.locked_computation_run(session, "evidence-run")
        selected, cutoff = evidence.selected_quant_sources(session, run, IID, ["computed:old"], "old-version")
        assert selected["computed:old"]["data"]["value"] == 3 and cutoff == CUTOFF
        with pytest.raises(ValueError, match="历史版本未"):
            evidence.selected_quant_sources(session, run, IID, ["computed:old"], "not-bound")


def test_bound_old_notebook_source_is_referenced_without_copying_its_tree(stored_run):
    actor = current_principal()
    legacy = source("computed:old-tree", value=5, input_snapshot={"huge": "old-calculation-input" * 100000})
    with get_session_factory()() as session:
        session.add(ResearchEntry(entry_id="prior-source-run", topic_id="evidence-test-topic", title="Old research",
            team_id=actor.team_id, kind="analysis", status="completed", context_json={"research_run": True,
                "instrument_ids": [IID], "cutoff": CUTOFF, "reviews": {IID: {"research": {"sources": [legacy]}}}}))
        session.commit()
        reference = evidence.computed_reference(legacy, run_id="prior-source-run", path=["reviews", IID, "research", "sources", "*"])
        assert len(json.dumps(reference)) < 1500
        row = session.get(ResearchEntry, "evidence-run")
        row.context_json = {**row.context_json, "research_dossiers": [{"instrument_id": IID, "notebook": {"sources": [reference]}}]}
        session.commit()
        run = evidence.locked_computation_run(session, "evidence-run")
        selected, _ = evidence.selected_quant_sources(session, run, IID, [legacy["source_id"]])
        assert selected[legacy["source_id"]]["input_snapshot"] == legacy["input_snapshot"]
        assert session.scalar(select(ResearchEntry.context_json).where(ResearchEntry.entry_id == "prior-source-run"))["reviews"][IID]["research"]["sources"][0] == legacy


def test_comparison_appends_an_original_without_rewriting_bound_context(stored_run, monkeypatch):
    from watchlist_app.api.routes import workbench
    with get_session_factory()() as session:
        row = session.get(ResearchEntry, "evidence-run")
        original = {**row.context_json, "catalogue": [{"instrument_id": IID}]}
        row.context_json = original
        session.commit()
        calls = []
        monkeypatch.setattr(workbench, "compare_series", lambda *args, cutoff: calls.append((args, cutoff)) or {
            "method": "retained sample", "rows": [], "dates": ["2026-09-01"]})
        statements = []
        def record(_connection, _cursor, sql, *_args):
            statements.append(sql)
        event.listen(session.get_bind(), "before_cursor_execute", record)
        try:
            receipt = workbench.research_tool("evidence-run", workbench.ResearchToolInput(tool="comparison",
                instrument_ids=[IID], start_date="2026-09-01", end_date="2026-09-30"), session)
        finally:
            event.remove(session.get_bind(), "before_cursor_execute", record)
        assert len(calls) == 1
        assert calls[0][1].isoformat() == CUTOFF
        assert not any("UPDATE research_entry" in sql or sql.startswith("SELECT research_entry.context_json ") for sql in statements)
        source = evidence.read_computed_source(session, "evidence-run", receipt["source_id"])
        assert source["request"] == receipt["request"] and source["data"] == receipt["result"]
        assert session.scalar(select(ResearchEntry.context_json).where(ResearchEntry.entry_id == "evidence-run")) == original


def test_exact_historical_source_stays_referenced_after_quant(stored_run, monkeypatch):
    monkeypatch.setattr(quant_sandbox, "execute", lambda *args: ({"summary": "Historical", "metrics": {"value": 3}}, {}))
    with get_session_factory()() as session:
        row = session.get(ResearchEntry, "evidence-run")
        row.context_json = {**row.context_json, "referenced_research_versions": [{"version_id": "historical",
            "instrument_id": IID, "information_cutoff": CUTOFF, "sources": [source(value=3)]}]}
        session.commit()
        receipt = routes.run_quant("evidence-run", QuantAnalysisInput(instrument_id=IID, title="Historical",
            source_ids=["computed:old"], version_id="historical", code="result = {}", methodology="Historical observation"), session)
        ref = evidence.read_computed_source(session, "evidence-run", receipt["source_id"])["methodology"]["input_sources"][0]
        assert "data" not in ref and ref["retained_reference"]["version_id"] == "historical"
        assert evidence.resolve_reference(ref, session=session)["data"]["value"] == 3


@pytest.mark.parametrize("projection", [None, "classification", "figure"])
def test_empty_original_is_readable_and_not_recursively_resolved(stored_run, projection):
    with get_session_factory()() as session:
        run = evidence.locked_computation_run(session, "evidence-run")
        saved = evidence.retain_computed_source(session, run, {**source("computed:empty"), "data": {}})
        session.commit()
        assert evidence.resolve_reference(evidence.computed_reference(saved), session=session, projection=projection)["data"] == {}


@pytest.mark.parametrize("projection", [None, "classification", "figure"])
def test_shared_reference_preserves_publication_boundary_and_team_isolation(stored_run, projection):
    actor = current_principal()
    with get_session_factory()() as session:
        session.add_all([ResearchTopic(topic_id="private-origin", title="Private", visibility="private",
            created_by_user_id="other-author", team_id=actor.team_id),
            ResearchTopic(topic_id="other-team-origin", title="Other team", visibility="team", team_id="other-team")])
        session.flush()
        for identifier, topic, team in (("private-original", "private-origin", actor.team_id),
                                       ("other-team-original", "other-team-origin", "other-team")):
            session.add(ResearchEntry(entry_id=identifier, topic_id=topic, kind="analysis", team_id=team, title="Original",
                context_json={"computed_metrics": [source()], "instrument_ids": [IID], "cutoff": CUTOFF}))
        session.commit()
        private = evidence.computed_reference(source(), run_id="private-original")
        with pytest.raises(HTTPException):
            evidence.resolve_reference(private, session=session, projection=projection)
        # A current authorized dossier retained this exact shared source. Other
        # original IDs in the private run are not thereby made selectable.
        row = session.get(ResearchEntry, "evidence-run")
        row.context_json = {**row.context_json, "research_dossiers": [{"instrument_id": IID,
            "notebook": {"sources": [private]}}], "computed_metrics": []}
        session.commit()
        run = evidence.locked_computation_run(session, "evidence-run")
        selected, _ = evidence.selected_quant_sources(session, run, IID, ["computed:old"])
        assert selected["computed:old"]["data"]["value"] == 10
        assert evidence.resolve_reference(private, session=session, bound_source=True, projection=projection)["source_id"] == "computed:old"
        outside = evidence.computed_reference(source(), run_id="other-team-original")
        with pytest.raises(ValueError, match="当前可访问"):
            evidence.resolve_reference(outside, session=session, bound_source=True, projection=projection)
        with pytest.raises(ValueError, match="受支持"):
            evidence.resolve_reference({**private, "retained_reference": {"run_id": "private-original",
                "path": ["arbitrary", "data", "*"]}}, session=session, bound_source=True, projection=projection)


@pytest.mark.parametrize("projection", [None, "classification", "figure"])
def test_reference_chain_retains_identity_checks_and_cycle_rejection(stored_run, projection):
    with get_session_factory()() as session:
        row = session.get(ResearchEntry, "evidence-run")
        original = source(data={"analysis_kind": "watchlist_observations", "metrics": {"actual": 5}})
        session.add(ResearchEntry(entry_id="figure-chain", topic_id=row.topic_id, team_id=row.team_id,
            kind="analysis", title="Earlier output", context_json={"computed_metrics": [original]}))
        row.context_json = {**row.context_json, "computed_metrics": [evidence.computed_reference(original, run_id="figure-chain")]}
        session.commit()
        selected = evidence.resolve_reference(evidence.computed_reference(original, run_id="evidence-run"),
            session=session, projection=projection)
        assert selected["source_id"] == original["source_id"]
        if projection != "classification":
            assert selected["data"]["metrics"] == {"actual": 5}
        with pytest.raises(ValueError, match="归属不一致"):
            evidence.resolve_reference({**evidence.computed_reference(original, run_id="evidence-run"),
                "instrument_id": "another-instrument"}, session=session, projection=projection)
        row.context_json = {**row.context_json, "computed_metrics": [evidence.computed_reference(original, run_id="evidence-run")]}
        session.commit()
        with pytest.raises(ValueError, match="循环"):
            evidence.resolve_reference(evidence.computed_reference(original, run_id="evidence-run"), session=session, projection=projection)


def test_snapshot_catalogue_keeps_existing_quant_source_kinds(stored_run):
    with get_session_factory()() as session:
        row = session.get(ResearchEntry, "evidence-run")
        estimate = {"source_id": "estimate:one", "source_type": "analyst_estimate_changes", "instrument_id": IID,
            "current_snapshot": {"actual": 2}}
        row.context_json = {**row.context_json,
            "sector_inputs": [{"instrument_id": IID, "components": ["ACME"], "snapshot_cutoff": CUTOFF}],
            "sector_company_data": {IID: {"ACME": {"revenue": 42}}}, "sector_estimate_evidence": [estimate]}
        session.commit()
        run = evidence.locked_computation_run(session, "evidence-run")
        ids = [f"instrument:evidence-run:{IID}", f"sector:evidence-run:{IID}", f"fmp:evidence-run:{IID}:ACME", "estimate:one"]
        catalogue = evidence.quant_source_catalogue(session, run, IID)
        assert set(ids).issubset({row["source_id"] for row in catalogue})
        selected, _ = evidence.selected_quant_sources(session, run, IID, ids)
        assert selected[ids[2]]["company"] == {"revenue": 42}
        assert selected[ids[3]]["current_snapshot"] == estimate["current_snapshot"]


def test_review_subprocess_reads_only_cited_original_via_bound_api_without_database():
    import os
    import subprocess
    import sys
    script = r'''
import json
from watchlist_app.services import sector_fact_review as review
from watchlist_app.db import session as database

def no_database(*args, **kwargs):
    raise AssertionError("Independent reviewer must not use a database identity")
database.get_session_factory = no_database
cutoff = "2026-10-01T00:00:00+00:00"
source = {"source_id": "computed:cited", "source_type": "computed_metric", "instrument_id": "xle",
    "as_of": cutoff, "scope": "instrument", "data": {"value": 10, "status": "available"}, "methodology": "Recorded calculation",
    "retained_reference": {"run_id": "test-run", "path": ["computed_metrics", "*"]}}
context = {"research_run": True, "instrument_ids": ["xle"], "cutoff": cutoff,
    "research_dossiers": [{"instrument_id": "xle", "notebook": {"sources": [
        {"source_id": "computed:historical-unused", "source_type": "computed_metric", "instrument_id": "xle"}]}}],
    "computed_metrics": [{key: value for key, value in source.items() if key != "data"},
        {"source_id": "computed:not-cited", "source_type": "computed_metric",
         "retained_reference": {"run_id": "test-run", "path": ["computed_metrics", "*"]}}]}
draft = {"reviews": [{"instrument_id": "xle", "summary": "Check retained calculation", "events": [{
    "event_key": "observed", "action": "new", "direction": "risk", "title": "Observation",
    "body": "Recorded calculation was 10", "next_watch": "Check mechanism", "confidence": "confirmed",
    "information_type": "fact", "recording_type": "backfill", "source_ids": ["computed:cited"]}]}]}
calls = []
def api(run_id, suffix, payload=None):
    assert run_id == "test-run"
    calls.append(suffix)
    if suffix == "computed-source?source_id=computed%3Acited":
        return source
    assert suffix == "sector-evidence"
    return {}
def check(packet):
    assert [row["source_id"] for row in packet["sources"]] == ["computed:cited"]
    assert packet["sources"][0]["data"] == {"value": 10, "status": "available"}
    return {"reviews": [{"instrument_id": "xle", "summary": "Measurement is not an investment event", "coverage": [],
        "decisions": [{"event_key": "observed", "decision": "remove", "reason": "No mechanism established", "event": None}]}]}
review._api_request, review._call_reviewer = api, check
result = review.review_output(json.dumps(draft), context=context)
assert result["reviews"][0]["events"] == []
assert sum(value.startswith("computed-source") for value in calls) == 1
print("bound API original reviewed without a database")
'''
    result = subprocess.run([sys.executable, "-c", script], env={"PATH": os.environ["PATH"],
        "PYTHONPATH": os.pathsep.join(sys.path), "INVESTMENT_STUDIO_RESEARCH_RUN_ID": "test-run"},
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "without a database" in result.stdout


def test_reviewer_context_never_hydrates_unreferenced_historical_computation(stored_run, monkeypatch):
    from watchlist_app.api.routes.workbench import run_context
    reference = evidence.computed_reference(source("computed:historical"), run_id="private-original-never-requested")
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "evidence-run")
        run.context_json = {**run.context_json, "research_dossiers": [{"instrument_id": IID,
            "prior_sources": [reference], "notebook": {"sources": [reference]},
            "themes": [{"sources": [reference]}], "pm_views": [{"sources": [reference]}]}]}
        session.commit()
        monkeypatch.setattr(evidence, "resolve_reference", lambda *args, **kwargs: pytest.fail("uncited historical original expanded"))
        packet = run_context("evidence-run", originals=True, session=session)
        dossier = packet["research_dossiers"][0]
        for item in [dossier["prior_sources"][0], dossier["notebook"]["sources"][0],
                     dossier["themes"][0]["sources"][0], dossier["pm_views"][0]["sources"][0]]:
            assert item["source_id"] == "computed:historical"
            assert "data" not in item and "retained_reference" not in item
        assert "data" not in packet["computed_metrics"][0]
        assert evidence.read_computed_source(session, "evidence-run", "computed:old")["data"]["value"] == 10


def test_old_dossier_quant_inputs_require_explicit_bound_source_selector(stored_run):
    from watchlist_app.api.routes.workbench import ComputedReadInput, read_computed_page
    actor = current_principal()
    input_source = source("computed:prior-input", value=5)
    quant = source("computed:prior-quant", value=5)
    quant["data"] = {"analysis_kind": "python_quant", "summary": "Prior result", "metrics": {"value": 5}, "tables": []}
    quant["methodology"] = {"input_sources": [evidence.computed_reference(input_source, run_id="prior-quant-run")]}
    with get_session_factory()() as session:
        session.add(ResearchEntry(entry_id="prior-quant-run", topic_id="evidence-test-topic", title="Prior quant",
            team_id=actor.team_id, kind="analysis", status="completed", context_json={"research_run": True,
                "instrument_ids": [IID], "cutoff": CUTOFF, "computed_metrics": [input_source, quant]}))
        session.flush()
        run = session.get(ResearchEntry, "evidence-run")
        run.context_json = {**run.context_json, "research_dossiers": [{"instrument_id": IID, "notebook": {
            "sources": [evidence.computed_reference(quant, run_id="prior-quant-run")]}}]}
        session.commit()
        base = {"source_id": quant["source_id"], "tool": "quant", "section": "inputs", "path": [0, "data", "value"]}
        with pytest.raises(HTTPException) as missing:
            read_computed_page("evidence-run", ComputedReadInput(**base), session)
        assert missing.value.status_code == 404
        result = read_computed_page("evidence-run", ComputedReadInput(**base, instrument_id=IID), session)
        assert result["data"] == 5
        for selector in ({"instrument_id": "other-instrument"}, {"instrument_id": IID, "source_id": input_source["source_id"]}):
            with pytest.raises(HTTPException) as outside:
                read_computed_page("evidence-run", ComputedReadInput(**{**base, **selector}), session)
            assert outside.value.status_code == 404
