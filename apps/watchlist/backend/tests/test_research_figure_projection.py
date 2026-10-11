"""Saved charts select one authorized historical original before projection."""
from copy import deepcopy
from datetime import datetime

import pytest
from studio_identity import current_principal

from watchlist_app.db.models import InstrumentDetail
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_run_evidence as evidence


IID = "fund-us-agg"
SOURCE = "computed:shared-figure"
URL = f"/api/research/instruments/{IID}/dossier"


def test_current_browser_directory_classifies_figures_after_locator_was_materialized_away(figure_sources, monkeypatch):
    client, _ = figure_sources
    with get_session_factory()() as session:
        origin = session.get(ResearchEntry, "figure-origin-new")
        context = deepcopy(origin.context_json)
        context["computed_metrics"][0]["methodology"]["metric"] = "ewma_volatility"
        origin.context_json = context
        publication = session.get(ResearchEntry, "figure-publication-new")
        context = deepcopy(publication.context_json)
        notebook = context["reviews"][IID]["research"]
        notebook["modules"] = [{"key": "market-quantitative", "figure_source_ids": [SOURCE]}]
        # This represents an already published directory produced before domain
        # retention was fixed. Its raw saved node still has the original locator.
        notebook["sources"][0].pop("observation_domain", None)
        publication.context_json = context
        session.commit()
        directory = publication.read_context_json["reviews"][IID]["research"]["sources"][0]
        assert "retained_reference" not in directory
        assert directory["observation_domain"] == "unclassified"
        before = deepcopy(publication.context_json)

    calls = []
    original_resolver = evidence.resolve_reference

    def tracked(source, **kwargs):
        calls.append((deepcopy(source), kwargs.get("projection")))
        assert source["source_id"] == SOURCE  # Unavailable sibling is never read.
        assert kwargs.get("projection") == "classification"
        return original_resolver(source, **kwargs)

    monkeypatch.setattr(evidence, "resolve_reference", tracked)
    response = client.get(URL, params={"current_only": "true"})
    assert response.status_code == 200, response.text
    notebook = response.json()["notebook"]
    assert notebook["version_id"] == "figure-notebook-new"
    assert notebook["modules"][0]["figure_source_ids"] == [SOURCE]
    assert next(row for row in notebook["sources"] if row["source_id"] == SOURCE)["observation_domain"] == "market"
    assert calls[0][0]["retained_reference"] == {"run_id": "figure-publication-new",
        "path": ["reviews", IID, "research", "sources", "*"]}
    assert [row[0]["retained_reference"]["run_id"] for row in calls] == ["figure-publication-new", "figure-origin-new"]
    assert all(not {"data", "input_series", "retained_reference"}.intersection(row) for row in notebook["sources"])
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, "figure-publication-new").context_json == before


@pytest.fixture
def figure_sources(client):
    actor = current_principal()
    originals = {}
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id=IID, instrument_name="Saved chart", instrument_type="etf",
            detail_view_type="etf", metadata_json={}))
        for name, visibility, team in (("figure-published", "team", actor.team_id),
                                       ("figure-private-origin", "private", actor.team_id),
                                       ("figure-other-team", "team", "another-team")):
            session.add(ResearchTopic(topic_id=name, title=name, visibility=visibility, team_id=team,
                created_by_user_id="another-author", instrument_ids=[IID]))
        session.flush()
        for name, day, value in (("old", "2026-09-01", 5), ("new", "2026-10-01", 9)):
            stamp = day + "T00:00:00+00:00"
            original = {"source_id": SOURCE, "source_type": "computed_metric", "instrument_id": IID,
                "scope": "instrument", "title": "Frozen calculation", "as_of": stamp,
                "methodology": {"method_version": "fixture/v1", "code": "result = retained_input",
                    "input_sources": [{"source_id": "private-input", "data": {"points": [value] * 1200}}]},
                "data": {"analysis_kind": "python_quant", "domain": "market", "summary": "Retained calculation",
                    "metrics": [{"label": "Value", "value": value, "unit": "USD"}],
                    "tables": [], "charts": [{"type": "line", "title": "Observed value",
                        "points": [{"date": day, "value": value}]}],
                    "input_series": {"points": [value] * 1200},
                    "input_snapshot": {"raw_rows": [value] * 1200},
                    "sources": [{"source_id": "private-input", "data": {"points": [value] * 1200}}]}}
            originals[name] = original
            session.add(ResearchEntry(entry_id=f"figure-origin-{name}", topic_id="figure-private-origin",
                team_id=actor.team_id, kind="analysis", title="Private calculation", status="completed",
                context_json={"research_actor": {**actor.to_dict(), "user_id": "another-author"},
                    "instrument_ids": [IID], "cutoff": stamp, "computed_metrics": [original]}))
            reference = evidence.computed_reference(original, run_id=f"figure-origin-{name}")
            # An unrelated unavailable original must not prevent the selected
            # chart or selected full-original endpoint from working.
            unrelated = {**evidence.computed_reference({**original, "source_id": "computed:unrelated"},
                run_id="unavailable-origin"), "title": "Unrelated retained computation"}
            notebook = {"instrument_id": IID, "version_id": f"figure-notebook-{name}",
                "source_ids": [SOURCE, "computed:unrelated"], "sources": [reference, unrelated]}
            session.add(ResearchEntry(entry_id=f"figure-publication-{name}", topic_id="figure-published",
                team_id=actor.team_id, kind="analysis", title="Saved research", status="completed",
                created_at=datetime.fromisoformat(stamp), completed_at=datetime.fromisoformat(stamp),
                context_json={"research_run": True, "research_actor": actor.to_dict(), "instrument_ids": [IID],
                    "cutoff": stamp, "reviews": {IID: {"status": "completed", "research": notebook}}}))
        foreign = {"instrument_id": IID, "version_id": "other-team-version", "source_ids": [SOURCE],
                   "sources": [evidence.computed_reference(originals["new"], run_id="figure-origin-new")]}
        session.add(ResearchEntry(entry_id="foreign-publication", topic_id="figure-other-team", team_id="another-team",
            kind="analysis", title="Another team", status="completed", context_json={"research_run": True,
                "instrument_ids": [IID], "cutoff": "2026-10-02T00:00:00+00:00",
                "reviews": {IID: {"status": "completed", "research": foreign}}}))
        session.commit()
    return client, originals


@pytest.mark.parametrize("projection", [None, "figure"])
def test_version_source_resolves_only_selected_original_and_preserves_history(figure_sources, monkeypatch, projection):
    client, originals = figure_sources
    resolved = []
    original_resolver = evidence.resolve_reference

    def tracked(source, **kwargs):
        resolved.append(source["source_id"])
        assert source["source_id"] != "computed:unrelated"
        return original_resolver(source, **kwargs)

    monkeypatch.setattr(evidence, "resolve_reference", tracked)
    params = {"source_id": SOURCE, "version_id": "figure-notebook-old"}
    if projection:
        params["projection"] = projection
    result = client.get(URL, params=params)
    assert result.status_code == 200, result.text
    saved = result.json()
    assert saved["data"]["charts"] == originals["old"]["data"]["charts"]
    assert saved["as_of"] == originals["old"]["as_of"]
    assert saved["data"]["metrics"][0]["value"] == 5
    assert resolved and set(resolved) == {SOURCE}
    if projection:
        assert saved["display_projection"] == "figure"
        assert not {"input_series", "input_snapshot", "sources"}.intersection(saved["data"])
        assert "input_sources" not in saved.get("methodology", {})
    else:
        assert saved["data"]["input_series"] == originals["old"]["data"]["input_series"]
        assert saved["methodology"]["input_sources"] == originals["old"]["methodology"]["input_sources"]
    later = client.get(URL, params={**params, "version_id": "figure-notebook-new"})
    assert later.status_code == 200, later.text
    assert later.json()["data"]["metrics"][0]["value"] == 9


def test_figure_requires_source_membership_and_team_version(figure_sources, monkeypatch):
    client, _ = figure_sources
    assert client.get(URL, params={"projection": "figure"}).status_code == 422
    assert client.get(URL, params={"source_id": SOURCE, "projection": "arbitrary"}).status_code == 422
    params = {"source_id": "computed:not-cited", "version_id": "figure-notebook-old", "projection": "figure"}
    assert client.get(URL, params=params).status_code == 422
    params.update(source_id=SOURCE, version_id="other-team-version")
    assert client.get(URL, params=params).status_code == 404
    # Sharing this exact chart does not open its private origin run or sibling sources.
    # The general run-tool fixture intentionally impersonates each run's actor;
    # this browser access check must keep the actual requesting team member.
    import watchlist_app.main as main
    actor = current_principal()
    monkeypatch.setattr(main, "resolve_request", lambda *_a, **_kw: actor)
    assert client.get("/api/research/runs/figure-origin-old/computed-source", params={"source_id": SOURCE}).status_code in {403, 404}


def test_current_source_selection_keeps_instrument_guard_and_does_not_resolve_siblings(figure_sources):
    client, originals = figure_sources
    from watchlist_app.services.research_notebook import dossier_source
    current = {"instrument_id": IID, "notebook": {"sources": [
        evidence.computed_reference(originals["new"], run_id="figure-origin-new"),
        {"source_id": "computed:unrelated", "source_type": "computed_metric", "instrument_id": IID,
         "scope": "instrument", "retained_reference": {"run_id": "missing", "path": ["computed_metrics", "*"]}}]}}
    result = client.get(URL, params={"source_id": SOURCE, "projection": "figure"})
    assert result.status_code == 200, result.text
    assert result.json()["data"]["metrics"][0]["value"] == 9
    invalid = deepcopy(current)
    invalid["notebook"]["sources"][0]["instrument_id"] = "another-instrument"
    with pytest.raises(ValueError, match="不属于"):
        dossier_source(invalid, SOURCE, references_only=True)
    assert client.get(URL, params={"source_id": "not-in-dossier", "projection": "figure"}).status_code == 422
