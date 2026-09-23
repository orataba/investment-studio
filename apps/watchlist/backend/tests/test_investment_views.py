"""PM background and explicitly selected overall stance are separate from chronology."""
import pytest


@pytest.fixture
def instrument(client):
    wid = client.post("/api/watchlists", json={"name": "Investment views"}).json()["watchlist_id"]
    assert client.post(f"/api/watchlists/{wid}/items", json={"instrument_ids": ["fund-us-agg"]}).status_code == 200
    return "/api/instruments/fund-us-agg/research"


def save(client, path, **extra):
    payload = {"note_date": "2026-09-23", "title": "中期判断", "body": "等待期限溢价回落",
               "research_context": {"background": "长债收益率上升，尚待区分实际利率和期限溢价"}, **extra}
    response = client.post(path + "/notes", json={"note": payload})
    assert response.status_code == 200, response.text
    return next(note for note in response.json()["notes"] if note["title"] == payload["title"])


def test_new_view_requires_background_and_does_not_invent_legacy_context(client, instrument):
    rejected = client.post(instrument + "/notes", json={"note": {"title": "只有判断", "note_date": "2026-09-23", "body": "看多"}})
    assert rejected.status_code == 422 and "背景" in rejected.text
    note = save(client, instrument)
    assert note["research_context"]["background_origin"] == "user"
    assert client.get(instrument).json()["current_stance"] is None
    # A historical empty background remains an explicit absence on correction.
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.db.models.research import InstrumentResearchNote
    with get_session_factory()() as session:
        record = session.get(InstrumentResearchNote, ("fund-us-agg", note["note_id"]))
        record.research_context = {}
        session.commit()
    corrected = client.put(instrument + "/notes/" + note["note_id"], json={"note": {
        "title": "更正文字", "note_date": "2026-09-23", "body": "旧观点措辞更正"}})
    assert corrected.status_code == 200, corrected.text
    assert not corrected.json()["notes"][0]["research_context"].get("background")


def test_overall_stance_is_explicit_versioned_and_clears_on_deletion(client, instrument):
    original = save(client, instrument)
    selection = {"note_id": original["note_id"], "revision_number": 1}
    response = client.put(instrument + "/current-stance", json=selection)
    assert response.status_code == 200, response.text
    first = response.json()["current_stance"]
    assert first["note"]["body"] == original["body"]
    assert client.put(instrument + "/current-stance", json=selection).json()["current_stance"]["selection_id"] == first["selection_id"]
    save(client, instrument, title="较新的补充观察", body="新增的观察不代表总体判断变化")
    assert client.get(instrument).json()["current_stance"]["note"]["note_id"] == original["note_id"]
    update = client.put(instrument + "/notes/" + original["note_id"], json={"note": {
        "note_date": "2026-09-23", "title": original["title"], "body": "更正后的表述"}})
    assert update.status_code == 200, update.text
    assert update.json()["current_stance"]["note"]["body"] == original["body"]
    assert update.json()["current_stance"]["has_later_revision"] is True
    deleted = client.delete(instrument + "/notes/" + original["note_id"])
    assert deleted.status_code == 200 and deleted.json()["current_stance"] is None
    assert client.put(instrument + "/current-stance", json=selection).status_code == 422


def test_review_cannot_silently_become_overall_stance(client, instrument):
    review = save(client, instrument, title="复盘", note_type="review")
    assert client.put(instrument + "/current-stance", json={"note_id": review["note_id"], "revision_number": 1}).status_code == 422
    assert client.put(instrument + "/current-stance", json={"note_id": review["note_id"]}).status_code == 422


def test_corrected_research_binding_uses_exact_namespace_and_explicit_empty_sources(client, instrument, monkeypatch):
    from copy import deepcopy
    from watchlist_app.services import research_dossier
    versions = {key: {"kind": "notebook", "version_id": key, "value": {},
        "information_cutoff": "2026-09-01T00:00:00+00:00", "sources": [{
            "source_id": "same-id", "source_type": "instrument_snapshot", "instrument_id": "fund-us-agg",
            "snapshot": {"observation": value}}]} for key, value in (("first", 100), ("second", 200))}
    monkeypatch.setattr(research_dossier, "read_dossier_version", lambda session, iid, version_id, **kwargs: deepcopy(versions[version_id]))
    note = save(client, instrument, research_context={"background": "当时数据", "notebook_version_id": "first"})
    assert note["research_context"]["sources"][0]["snapshot"]["observation"] == 100
    def correct(context):
        response = client.put(instrument + "/notes/" + note["note_id"], json={"note": {
            "title": note["title"], "note_date": note["note_date"], "body": note["body"], "research_context": context}})
        assert response.status_code == 200, response.text
        return next(row for row in response.json()["notes"] if row["note_id"] == note["note_id"])["research_context"]
    changed = correct({"notebook_version_id": "second"})
    assert changed["sources"][0]["snapshot"]["observation"] == 200
    assert changed["research_snapshot"]["notebook_version_id"] == "second"
    versions["second"]["sources"][0]["snapshot"]["observation"] = 300
    unchanged = correct({"background": "修正说明", "source_ids": ["same-id"]})
    assert unchanged["sources"][0]["snapshot"]["observation"] == 200
    cleared = correct({"source_ids": []})
    assert cleared["source_ids"] == cleared["sources"] == []


def test_background_and_judgment_versions_must_belong_together(client, instrument, monkeypatch):
    from watchlist_app.services import research_dossier
    versions = {
        "book": {"kind": "notebook", "version_id": "book", "value": {"investment_view": {"version_id": "right-view"}}, "sources": []},
        "wrong-view": {"kind": "investment_view", "version_id": "wrong-view", "value": {}, "sources": []},
    }
    monkeypatch.setattr(research_dossier, "read_dossier_version", lambda session, iid, version_id, **kwargs: versions[version_id])
    response = client.post(instrument + "/notes", json={"note": {"title": "冲突背景", "note_date": "2026-09-23", "body": "判断",
        "research_context": {"background": "旧研究", "notebook_version_id": "book", "investment_view_version_id": "wrong-view"}}})
    assert response.status_code == 422 and "不属于" in response.text


def test_research_recorded_after_original_view_cannot_become_its_prior_background(client, instrument, monkeypatch):
    from watchlist_app.services import research_dossier
    note = save(client, instrument)
    later_research = {"kind": "notebook", "version_id": "later-study", "value": {}, "sources": [],
                      "information_cutoff": "2026-01-01T00:00:00+00:00", "recorded_at": "2099-01-01T00:00:00+00:00"}
    monkeypatch.setattr(research_dossier, "read_dossier_version", lambda *args, **kwargs: later_research)
    response = client.put(instrument + "/notes/" + note["note_id"], json={"note": {
        "title": note["title"], "note_date": note["note_date"], "body": note["body"],
        "research_context": {"notebook_version_id": "later-study"}}})
    assert response.status_code == 422 and "后来" in response.text
    current = next(row for row in client.get(instrument).json()["notes"] if row["note_id"] == note["note_id"])
    assert current["revision_number"] == note["revision_number"]
    assert current["research_context"] == note["research_context"]


def test_correcting_theme_reference_updates_the_bound_snapshot(client, instrument):
    themes = [client.post("/api/research/instruments/fund-us-agg/themes", json={"title": title}).json()
              for title in ("原归类", "正确归类")]
    note = save(client, instrument, research_context={"theme_id": themes[0]["theme_id"], "background": "当时的观察"})
    response = client.put(instrument + "/notes/" + note["note_id"], json={"note": {
        "title": note["title"], "note_date": note["note_date"], "body": note["body"],
        "research_context": {"theme_id": themes[1]["theme_id"]}}})
    assert response.status_code == 200, response.text
    current = next(row for row in response.json()["notes"] if row["note_id"] == note["note_id"])
    assert current["research_context"]["theme_snapshot"]["theme_id"] == themes[1]["theme_id"]
    assert current["research_context"]["theme_snapshot"]["title"] == "正确归类"


def test_theme_figure_opinion_freezes_the_selected_theme_version(client, instrument, monkeypatch):
    from watchlist_app.services import research_dossier, research_runner
    monkeypatch.setattr(research_runner, "harness_available", lambda: False)
    theme = client.post('/api/research/instruments/fund-us-agg/themes', json={"title": "当前主题"}).json()
    old = {"kind": "theme", "version_id": f"theme:{theme['theme_id']}:1",
        "information_cutoff": "2026-09-01T00:00:00+00:00",
        "value": {"theme_id": theme["theme_id"], "revision_number": 1, "title": "原主题", "question": "原研究问题", "synthesis": "原结论"},
        "sources": [{"source_id": "original", "source_type": "instrument_snapshot", "instrument_id": "fund-us-agg", "snapshot": {"observed": 100}}]}
    monkeypatch.setattr(research_dossier, "read_dossier_version", lambda session, iid, version_id, **kwargs: old)
    note = save(client, instrument, research_context={"theme_id": theme["theme_id"], "theme_version_id": old["version_id"], "source_ids": ["original"]})
    context = note["research_context"]
    assert "原研究问题" in context["background"]
    assert context["theme_snapshot"]["title"] == "原主题"
    assert context["sources"][0]["snapshot"] == {"observed": 100}
    assert context["research_snapshot"]["theme_revision"] == 1
