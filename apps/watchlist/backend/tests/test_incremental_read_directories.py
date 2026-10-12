"""Working directories keep current judgments and exact, separately read originals."""
from copy import deepcopy

import pytest

from watchlist_app.services.research_read_projection import (
    event_working_view, json_bytes, working_set_view,
)
from watchlist_app.services import research_tool_projection as projection
from .test_research_read_paging import complete
from .research_projection_fixture import server_projection
from watchlist_app import research_mcp as mcp


@pytest.mark.parametrize("role", ["recent_updates", "themes", "quantitative"])
@pytest.mark.parametrize("section", ["research_working_sets", "section_results"])
def test_specialists_cannot_reload_all_role_contexts_through_generic_reader(role, section):
    context = {"sector_run": True, "instrument_ids": ["fund"],
        "execution": {"research_section": role},
        "research_working_sets": {"fund": {"affected_theme_ids": ["theme"]}},
        "section_results": {"fund": {"themes": {"summary": "Other role's draft"}}}}
    with pytest.raises(ValueError, match="自己分工"):
        projection.read_research_context(lambda _: context, section=section)
    overview = projection.read_research_context(lambda _: context)
    assert overview["section_reads"][0]["section"] == role
    assert "Other role's draft" not in str(overview)

    # The synthesis owner and an ordinary conversation retain their explicit
    # bound read; only specialist working contexts are separated.
    context["execution"]["research_section"] = "overview"
    assert projection.read_research_context(lambda _: context, section=section)["data"]
    context["sector_run"] = False
    context["execution"]["research_section"] = role
    assert projection.read_research_context(lambda _: context, section=section)["data"]


def test_current_theme_sources_are_selectable_originals_not_embedded_copies(monkeypatch):
    original = {"source_id": "computed:old:fund", "source_type": "computed_metric", "title": "原净值区间",
        "retrieved_at": "2026-10-01T00:00:00+00:00", "as_of": "2026-09-30", "data": {"points": [1, 2, 3] * 10000}}
    theme = {"theme_id": "important", "synthesis": "当前判断\x00保留", "source_ids": [original["source_id"]],
             "sources": [original], "unknown_judgment_field": {"original": "未知字段也保留"}}
    dossier = {"instrument_id": "fund", "themes": [theme], "prior_sources": [original], "notebook": {}}
    context = {"run_id": "run", "cutoff": "2026-10-02T00:00:00+00:00", "research_dossiers": [dossier]}
    frozen = deepcopy(context)
    def backend(suffix):
        if suffix == "context": return context
        assert suffix == "dossier/fund?source_id=computed%3Aold%3Afund"
        return original
    monkeypatch.setattr(mcp, "request", server_projection(backend))
    rows = complete("read_research_dossier", {"instrument_id": "fund", "section": "themes"})
    assert rows[0]["synthesis"] == theme["synthesis"]
    assert rows[0]["unknown_judgment_field"] == theme["unknown_judgment_field"]
    source = rows[0]["sources"][0]
    assert source["retrieved_at"] == original["retrieved_at"] and source["as_of"] == original["as_of"]
    assert "data" not in source
    read = dict(source["read"]); tool = read.pop("tool")
    assert complete(tool, read) == original
    assert context == frozen
    assert json_bytes(rows) < json_bytes([theme]) / 10


def test_event_directory_exposes_current_snapshot_once_and_exact_version():
    source = {"source_id": "text:original", "published_at": "2026-09-01", "text": "原件" * 10000}
    event = {"instrument_id": "fund", "case_id": "case", "event_version_id": "case:7", "event_key": "stable",
        "body": "当前机制", "impact_analysis": "重要影响", "next_watch": "条件", "published_at": "2026-09-01",
        "sources": [source], "evidence": {"body": "当前机制", "sources": [source], "unknown_saved_field": "原快照保留"}}
    frozen = deepcopy(event)
    result = event_working_view(event)
    assert result["body"] == event["body"] and result["impact_analysis"] == event["impact_analysis"]
    assert result["published_at"] == event["published_at"] and result["event_key"] == "stable"
    assert "evidence" not in result
    assert result["snapshot_read"] == {"tool": "read_research_dossier", "instrument_id": "fund", "version_id": "event:case:7"}
    assert result["sources"][0]["read"]["source_id"] == "text:original"
    assert event == frozen and json_bytes(result) < json_bytes(event) / 100


def test_working_set_trigger_pointer_returns_exact_frozen_corrections_and_both_affected_sets():
    reasons = [{"kind": "bound_source_revision", "sources": [{"source_id": "new", "previous_source_ids": ["old1", "old2"], "withdrawn": True}]}]
    working = {"affected_event_ids": ["case"], "affected_theme_ids": ["theme"], "reasons": reasons,
        "source_dependencies": {"old1": [{"kind": "event", "id": "case"}], "old2": [{"kind": "theme", "id": "theme"}]}}
    context = {"run_id": "run", "cutoff": "2026-10-02T00:00:00+00:00", "research_working_sets": {"fund": working},
               "incremental_trigger": {"fund": {"reasons": reasons}}}
    frozen = deepcopy(context)
    page = projection.read_research_context(lambda _: context, section="research_working_sets")
    result = page["data"]["fund"]
    assert result["affected_event_ids"] == ["case"] and result["affected_theme_ids"] == ["theme"]
    assert result["source_dependencies"] == working["source_dependencies"] and "reasons" not in result
    read = dict(result["trigger_read"]); read.pop("tool")
    assert projection.read_research_context(lambda _: context, **read)["data"] == context["incremental_trigger"]["fund"]
    assert context == frozen
    assert "trigger_read" not in working_set_view({"reasons": []}, "fund")


@pytest.mark.parametrize("kind,supported", [("public_fund", False), ("private_fund", False), ("etf", True), ("equity", True), ("index", True), ("crypto", True)])
def test_instrument_overview_states_existing_numeric_applicability(kind, supported):
    context = {"run_id": "run", "cutoff": "2026-10-02T00:00:00+00:00", "catalogue": [{"instrument_id": "fund"}],
        "instrument_inputs": [{"instrument_id": "fund", "instrument_type": kind}],
        "research_dossiers": [{"instrument_id": "fund", "notebook": {}}]}
    view = projection.read_research_instrument(lambda _: context, "fund")
    assert view["numeric_tools"]["event_reaction"]["supported"] is supported
    assert view["numeric_tools"]["observations"]["supported"] is True


def test_http_event_directory_reads_its_retained_version_and_original_under_same_run(client, monkeypatch):
    from watchlist_app.db.models.workbench import ResearchEntry, RiskCase
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.services import sector_research
    from .test_sector_research import seed_sector
    seed_sector(client, monkeypatch)
    original = {"source_id": "original-event", "source_type": "public_source", "title": "Original notice",
        "url": "https://issuer.example/notice", "text": "Retained original. " * 3000,
        "published_at": "2026-01-01T00:00:00+00:00", "retrieved_at": "2026-01-02T00:00:00+00:00"}
    evidence = {"event_key": "stable-event", "event_version_id": "directory-event:1", "body": "Current mechanism",
        "follow_up": "watch", "recorded_at": "2026-01-02T00:00:00+00:00", "checked_at": "2026-01-02T00:00:00+00:00",
        "source_ids": [original["source_id"]], "sources": [original], "legacy_unknown": {"value": "do not invent a date"}}
    with get_session_factory()() as session:
        session.add(RiskCase(case_id="directory-event", instrument_id="xlk", signal="sector:stable-event",
            title="Current matter", body="Current mechanism", status="recorded", trigger_active=False,
            evidence_json=evidence, history_json=[]))
        session.commit()
        run, _ = sector_research.begin_run(session, ["xlk"])
        run_id = run.entry_id
    sector_research.prepare_run(run_id)
    def request(suffix, payload=None):
        response = client.post(f"/api/research/runs/{run_id}/{suffix}", json=payload)
        assert response.status_code == 200, response.text
        return response.json()
    monkeypatch.setattr(mcp, "request", request)
    records = complete("read_research_instrument", {"instrument_id": "xlk", "section": "events"})
    item = next(x for x in records if x["case_id"] == "directory-event")
    assert "evidence" not in item and "text" not in item["sources"][0]
    read = dict(item["snapshot_read"]); tool = read.pop("tool")
    snapshot = complete(tool, read)
    assert snapshot["value"]["legacy_unknown"] == evidence["legacy_unknown"]
    assert snapshot["sources"][0]["text"] == original["text"]
    read = dict(item["sources"][0]["read"]); tool = read.pop("tool")
    source = complete(tool, read)
    assert source["text"] == original["text"] and source["published_at"] == original["published_at"]
    with get_session_factory()() as session:
        assert session.get(RiskCase, "directory-event").evidence_json == evidence
        run = session.get(ResearchEntry, run_id)
        assert "xlk:event:directory-event:1" in run.context_json["research_historical_reads"]
