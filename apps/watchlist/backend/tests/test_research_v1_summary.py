"""V1 uses one versioned judgment and the existing publication/source boundary."""
from copy import deepcopy

import pytest

from watchlist_app.services.research_notebook import ResearchNotebook, notebook_current_view, retain_notebook, validate_notebook
from watchlist_app.services import sector_fact_review
from .test_research_activity import activity_client, publish
from .test_research_themes import research_client, _theme, _themes
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.research_dossier import read_dossier


SOURCE = {"source_id": "original", "source_type": "public_source", "text": "Fictional issuer disclosed a constraint.",
          "published_at": "2026-09-01", "url": "https://example.test/filing"}


def risk(**changes):
    return {"key": "funding", "title": "融资约束", "explanation": "融资渠道缩窄可能限制投资", "next_watch": "下一次融资披露",
            "source_ids": ["original"], **changes}


def retain(payload, previous=None, run="first"):
    return retain_notebook(ResearchNotebook.model_validate(payload), previous, {"original": SOURCE}, run, "2026-09-25T00:00:00+00:00")


def test_current_status_can_skip_all_event_history(activity_client, monkeypatch):
    from watchlist_app.services import sector_research
    monkeypatch.setattr(sector_research, "events_for_instruments", lambda *_: pytest.fail("Status must not load event history"))
    response = activity_client.get("/api/sector-research", params={"instrument_id": "xlk", "include_events": "false"})
    assert response.status_code == 200
    assert response.json()["events"] == []
    assert response.json()["sectors"][0]["instrument_id"] == "xlk"


def test_current_dossier_skips_auxiliary_history_but_keeps_exact_sources(activity_client, monkeypatch):
    from watchlist_app.services import research_activity, research_notebook
    from watchlist_app.api.routes.research import research_repository
    with get_session_factory()() as session:
        publish(session, research={"investment_view": {"direction": "保留当前判断", "risks": [risk()]}})
    complete = activity_client.get("/api/research/instruments/xlk/dossier").json()
    with monkeypatch.context() as patch:
        blocked = lambda *_args, **_kwargs: pytest.fail("Initial page must not read auxiliary history")
        patch.setattr(research_activity, "research_activity", blocked)
        patch.setattr(research_notebook, "retained_public_sources", blocked)
        patch.setattr(research_repository, "list_note_revisions", blocked)
        response = activity_client.get("/api/research/instruments/xlk/dossier?current_only=true")
        assert response.status_code == 200
        current = response.json()
        assert current["notebook"] == complete["notebook"]
        assert current["prior_sources"] == current["materials"] == current["notebook_history"] == []
    original = activity_client.get("/api/research/instruments/xlk/dossier", params={
        "current_only": "true", "version_id": current["notebook"]["version_id"], "source_id": "original"})
    assert original.status_code == 200 and original.json()["text"]
    assert activity_client.get("/api/research/instruments/xlk/dossier?include_history=true").json()["prior_sources"]


def test_opportunities_and_risks_share_view_version_and_quiet_check_preserves_exact_judgment():
    first = retain({"investment_view": {"direction": "需求改善与融资风险同时存在", "risks": [risk()],
        "opportunities": [risk(key="demand", title="需求改善")], "coverage_status": "limited", "coverage_note": "融资资料待补充"}})
    quiet = retain({}, first, "quiet")
    assert quiet["investment_view"] == first["investment_view"]
    assert quiet["version_id"] == first["version_id"]
    revised = retain({"investment_view": {"direction": "需求继续改善"}}, first, "new-view")
    assert revised["investment_view"]["risks"] == first["investment_view"]["risks"]
    assert revised["investment_view"]["versions"][-1]["risks"] == first["investment_view"]["risks"]
    cleared = retain({"investment_view": {"risks": [], "direction": "融资约束已有明确解除依据", "source_ids": ["original"]}}, revised, "clear")
    assert cleared["investment_view"]["risks"] == []
    assert cleared["investment_view"]["versions"][-1]["risks"]


def test_legacy_prose_is_unknown_not_an_assessed_empty_list_and_is_not_rewritten():
    old = {"investment_view": {"direction": "原判断", "risk": "原风险", "updated_at": "2025-01-01"}}
    original = deepcopy(old)
    read = notebook_current_view(old)
    assert old == original
    assert read["investment_view"]["risks"] is None
    assert read["investment_view"]["coverage_status"] is None
    assert read["investment_view"]["updated_at"] == "2025-01-01"
    assert read["investment_view"]["risk"] == "原风险"


def test_nested_citations_must_exist_and_figures_must_be_numerical():
    with pytest.raises(ValueError, match="引用"):
        ResearchNotebook.model_validate({"investment_view": {"risks": [risk(source_ids=[])]}})
    missing = ResearchNotebook.model_validate({"investment_view": {"risks": [risk(source_ids=["missing"])]}})
    with pytest.raises(ValueError, match="未取得"):
        validate_notebook(missing, "xlk", {"original": SOURCE})
    prose = ResearchNotebook.model_validate({"investment_view": {"risks": [risk(figure_source_ids=["original"])]}})
    with pytest.raises(ValueError, match="数值依据"):
        validate_notebook(prose, "xlk", {"original": SOURCE})
    private = {**SOURCE, "instrument_id": "another-instrument"}
    with pytest.raises(ValueError, match="其他标的"):
        validate_notebook(ResearchNotebook.model_validate({"investment_view": {"risks": [risk()]}}), "xlk", {"original": private})


def test_reviewer_cannot_invent_or_retarget_attention_items():
    proposed = {"investment_view": {"risks": [risk(event_keys=["funding-event"])]}}
    for changed in (risk(key="invented"), risk(event_keys=["another-event"])):
        with pytest.raises(ValueError, match="Fact review cannot"):
            sector_fact_review._reviewed_delta(proposed, ResearchNotebook.model_validate({"investment_view": {"risks": [changed]}}))


def test_real_publication_preserves_attention_sources_and_history(activity_client):
    with get_session_factory()() as session:
        first = publish(session, research={"investment_view": {"direction": "融资需要继续关注", "risks": [risk()],
            "opportunities": [], "coverage_status": "assessed"}})
        baseline = deepcopy(read_dossier(session, "xlk")["notebook"]["investment_view"])
        publish(session)
        assert read_dossier(session, "xlk")["notebook"]["investment_view"] == baseline
        publish(session, research={"investment_view": {"direction": "约束仍存，等待披露"}})
        dossier = read_dossier(session, "xlk")
        view = dossier["notebook"]["investment_view"]
        assert view["risks"] == baseline["risks"]
        assert view["versions"][-1]["source_run_id"] == first.entry_id
        assert "original" in {source["source_id"] for source in dossier["notebook"]["sources"]}
    response = activity_client.get("/api/research/instruments/xlk/dossier")
    assert response.status_code == 200
    assert response.json()["notebook"]["investment_view"]["risks"] == view["risks"]


def test_theme_summary_skips_history_and_detail_remains_scoped(research_client, monkeypatch):
    from watchlist_app.services import research_activity
    theme = _theme(research_client)
    path = f"{_themes()}/{theme['theme_id']}"
    research_client.patch(path, json={"background": "修订后的背景"})
    full = research_client.get(path)
    assert full.status_code == 200 and len(full.json()["versions"]) == 1
    monkeypatch.setattr(research_activity, "research_activity", lambda *args, **kwargs: pytest.fail("summary must not load history"))
    summary = research_client.get(_themes() + "?include_history=false")
    assert summary.status_code == 200
    row = summary.json()["themes"][0]
    assert row["theme_id"] == theme["theme_id"] and row["background"] == "修订后的背景"
    assert "versions" not in row and "updates" not in row and "notes" not in row
    wrong = research_client.get(f"/api/research/instruments/sxv264/themes/{theme['theme_id']}")
    assert wrong.status_code == 404
