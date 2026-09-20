"""Display reads retain provenance while originals remain version-addressable."""
from copy import deepcopy

from .test_research_activity import activity_client, publish
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.research_dossier import read_dossier
from watchlist_app.services.sector_research import review_states


def test_browser_reads_leave_agent_inputs_and_versioned_originals_complete(activity_client):
    client = activity_client
    with get_session_factory()() as session:
        run = publish(session, research={"investment_view": {
            "direction": "盈利改善，估值和资金回款仍待验证", "horizon": "两个季度",
            "source_ids": ["original"]}})
        context = deepcopy(run.context_json)
        source = context["reviews"]["xlk"]["research"]["sources"][0]
        source.update(data={"input_points": list(range(1000))},
                      snapshot={"historical_rows": list(range(1000))},
                      company={"statements": list(range(1000))}, body="完整原文\x00" * 1000,
                      measurement={"current": {"date": "2026-09-01", "volatility_pct": 13.5}},
                      methodology={"half_life_sessions": 20, "annualization": 252},
                      pm_binding_note="原始归属保持不变")
        run.context_json = context
        session.commit()
        run_id = run.entry_id
        original_context = deepcopy(run.context_json)
        complete = read_dossier(session, "xlk", include_history=True)
        states = review_states(session, instrument_ids=["xlk"])
    notebook = complete["notebook"]
    version_id = notebook["version_id"]
    original = notebook["sources"][0]

    response = client.get("/api/sector-research?instrument_id=xlk")
    assert response.status_code == 200
    status = response.json()["sectors"][0]
    for field, key in (("latest_review", "latest"), ("last_completed_review", "last_completed")):
        shown = status[field]
        assert "research" not in shown
        assert shown["current_research"] == {"investment_view": notebook["investment_view"]}
        assert {k: v for k, v in shown.items() if k != "current_research"} == {
            k: v for k, v in states[key]["xlk"].items() if k not in {"current_research", "research"}}

    base = "/api/research/instruments/xlk/dossier"
    response = client.get(base + "?include_history=true")
    assert response.status_code == 200
    shown = response.json()
    assert shown["notebook"]["investment_view"] == notebook["investment_view"]
    for record in [shown["notebook"], *(item["notebook"] for item in shown["notebook_history"])]:
        assert record["sources"][0] == {key: value for key, value in original.items()
                                         if key not in {"text", "body", "data", "snapshot", "company"}}
    evidence = client.get(base, params={"source_id": original["source_id"], "version_id": version_id})
    assert evidence.status_code == 200 and evidence.json() == original
    assert client.get(base, params={"source_id": "not-cited", "version_id": version_id}).status_code == 422
    assert client.get("/api/research/instruments/xlf/dossier", params={
        "source_id": original["source_id"], "version_id": version_id}).status_code == 404
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, run_id).context_json == original_context
        assert read_dossier(session, "xlk", include_history=True) == complete


def test_browser_projection_preserves_missing_and_withdrawn_current_view():
    from watchlist_app.api.research_presentation import review_status_view
    assert review_status_view(None) is None
    for notebook in (None, {"investment_view": None, "sources": [{"text": "旧资料"}]}):
        result = review_status_view({"status": "failed", "summary": "本轮失败",
                                     "research": {"investment_view": {"direction": "旧观点"}},
                                     "current_research": notebook})
        assert result["summary"] == "本轮失败"
        assert result["current_research"] == (None if notebook is None else {"investment_view": None})
