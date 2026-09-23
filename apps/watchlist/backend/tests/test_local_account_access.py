"""Explicit local owner access expands business ACLs, never model task scope."""
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from studio_identity import LOCAL_OWNER_CREDENTIAL, Principal

from .test_account_research_access import accounts, as_user, create_topic
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.models.watchlists import WatchlistView
from watchlist_app.db.session import get_session_factory


@pytest.fixture
def local_owner(accounts, monkeypatch):
    import watchlist_app.main as main
    cloud, principals, grants = accounts
    owner = Principal("shaw", "Shaw", "default", team_role="admin", is_team_owner=True,
                      local_unrestricted=True, credential=LOCAL_OWNER_CREDENTIAL)
    principals[LOCAL_OWNER_CREDENTIAL] = owner
    grants["shaw"] = {"A", "B"}
    monkeypatch.setenv("INVESTMENT_STUDIO_AUTH_MODE", "local")
    local = TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50001),
                       headers={"Origin": "http://127.0.0.1"})
    yield local, cloud, principals, owner
    local.close()


def test_local_owner_reads_unclaimed_conversations_files_and_corrects_without_changing_authors(local_owner):
    local, cloud, _, _ = local_owner
    as_user(cloud, "bob")
    tid = create_topic(cloud, instrument_ids=["fund-us-agg"], portfolio_id="B")
    file = cloud.post(f"/api/research/topics/{tid}/files", files={"file": ("old.txt", b"old private holdings", "text/plain")}).json()
    path = "/api/instruments/fund-us-agg/research/notes"
    note = cloud.post(path, json={"note": {"note_date": "2026-09-08", "title": "乙的原判断", "research_context": {"background": "原始研究讨论"}}}).json()["notes"][0]
    with get_session_factory()() as session:
        topic = session.get(ResearchTopic, tid)
        topic.created_by_user_id = None
        session.commit()
    assert local.get("/api/identity").json()["local_unrestricted"] is True
    assert tid in {row["topic_id"] for row in local.get("/api/research/topics").json()}
    assert local.get(file["source"]).content == b"old private holdings"
    assert local.get(f"/api/research/topics/{tid}").status_code == 200
    result = local.put(path + "/" + note["note_id"], json={"note": {"note_date": "2026-09-08", "title": "本机更正原文字"}})
    assert result.status_code == 200, result.text
    updated = result.json()["notes"][0]
    assert (updated["author_user_id"], updated["author"], updated["updated_by"]) == ("bob", "经理乙", "shaw")
    history = local.get("/api/instruments/fund-us-agg/research/history").json()["note_revisions"]
    assert all(row["author_user_id"] == "bob" for row in history)
    # An explicit normal account bearer never inherits the host's local privileges.
    assert as_user(cloud, "alice").get(f"/api/research/topics/{tid}").status_code == 404


def test_local_run_credentials_stay_bound_and_portfolio_content_is_not_team_research(local_owner):
    local, cloud, principals, owner = local_owner
    as_user(cloud, "bob")
    tid = create_topic(cloud, portfolio_id="B")
    with get_session_factory()() as session:
        for rid in ("local-current", "local-other"):
            session.add(ResearchEntry(entry_id=rid, topic_id=tid, kind="analysis", title="请保存团队研究", status="running",
                context_json={"research_run": True, "portfolio_id": "B", "instrument_ids": ["fund-us-agg"], "research_actor": owner.to_dict()}))
        session.commit()
    path = "/api/research/runs/local-current"
    assert local.get(path + "/context").status_code == 200
    assert local.post(path + "/tools", json={"tool": "portfolio"}).status_code == 403
    principals["local-run"] = replace(owner, credential="local-run", resource_scope={"kind": "run", "id": "local-current"})
    local.headers["Authorization"] = "Bearer local-run"
    assert local.get(path + "/context").status_code == 200
    assert local.get("/api/research/runs/local-other/context").status_code == 403
    assert local.get("/api/research/topics").status_code == 403
    assert local.post("/api/recalc/bulk", json={"instrument_ids": ["fund-us-agg"]}).status_code == 403
    response = local.post(path + "/user-command", json={"action": "publish_research", "instrument_id": "fund-us-agg", "source_quote": "请保存团队研究"})
    assert response.status_code == 422 and "组合" in response.text


def test_local_owner_accesses_other_personal_views_without_taking_their_authorship(local_owner):
    local, cloud, _, _ = local_owner
    as_user(cloud, "bob")
    wid = cloud.post("/api/watchlists", json={"name": "Shared local access"}).json()["watchlist_id"]
    payload = {"name": "乙的视图", "default_group_by": "none", "columns": [{"field_key": "instrument_name", "display_order": 1}]}
    view = cloud.post(f"/api/watchlists/{wid}/views", json=payload).json()
    assert view["view_id"] in {row["view_id"] for row in local.get(f"/api/watchlists/{wid}/views").json()}
    assert local.post("/api/screener/query", json={"watchlist_id": wid, "view_id": view["view_id"]}).status_code == 200
    response = local.put(f"/api/watchlists/{wid}/views/{view['view_id']}", json={**payload, "name": "本机可维护的乙视图"})
    assert response.status_code == 200, response.text
    with get_session_factory()() as session:
        saved = session.scalar(select(WatchlistView).where(WatchlistView.watchlist_view_id == view["view_key"]))
        assert saved.author_user_id == "bob"
    assert local.post("/api/recalc/bulk", json={"instrument_ids": ["fund-us-agg"]}).status_code == 200
