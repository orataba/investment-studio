"""Exercise real credential parsing and Watchlist resource guards with a fake Home boundary."""
from dataclasses import replace
from io import BytesIO
import json
from urllib.error import HTTPError

import pytest
from sqlalchemy import select
from studio_identity import IdentityError, Principal, resolve_request, current_principal

from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory


@pytest.fixture
def accounts(client, monkeypatch):
    import studio_identity
    import watchlist_app.main as main
    from watchlist_app.services import research_workbench
    principals = {
        "alice": Principal("alice", "经理甲", "default", credential="alice"),
        "bob": Principal("bob", "经理乙", "default", credential="bob"),
        "reader": Principal("reader", "只读成员", "default", team_role="reader", credential="reader"),
    }
    grants = {"alice": {"A"}, "bob": {"B"}, "reader": {"A"}}
    def identity_call(path, credential, payload=None, **kwargs):
        if credential not in principals:
            raise IdentityError(401, "登录已失效")
        principal = principals[credential]
        if path == "/introspect":
            return principal.to_dict()
        if path == "/members":
            return {"members": [{"user_id": p.user_id, "display_name": p.display_name, "active": True} for p in principals.values() if not p.resource_scope]}
        if path == "/delegations":
            token = "delegated:" + credential + ":" + payload["resource_scope"]["id"]
            principals[token] = replace(principal, credential=token, resource_scope=payload["resource_scope"])
            return {"token": token}
        if path == "/delegations/revoke":
            return {}
        raise AssertionError(path)
    monkeypatch.setattr(studio_identity, "_call", identity_call)
    monkeypatch.setattr(main, "resolve_request", resolve_request)
    def portfolio_response(request, timeout):
        token = request.get_header("Authorization", "").removeprefix("Bearer ")
        p = principals.get(token)
        if p is None:
            raise HTTPError(request.full_url, 401, "unauthorized", {}, None)
        if request.full_url.endswith("/capabilities"):
            payload = {"research_enabled": True}
        elif request.full_url.endswith("/portfolios"):
            payload = [{"portfolio_id": item, "portfolio_name": item} for item in sorted(grants[p.user_id])]
        else:
            pid = request.full_url.split("/portfolios/")[1].split("/")[0]
            if pid not in grants[p.user_id]:
                raise HTTPError(request.full_url, 404, "not found", {}, None)
            payload = {"portfolio_id": pid, "role": "reader"}
        return BytesIO(json.dumps(payload).encode())
    monkeypatch.setattr(research_workbench, "urlopen", portfolio_response)
    from watchlist_app.db.models import InstrumentDetail
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id="fund-us-agg", instrument_type="etf", detail_view_type="etf",
                                    instrument_name="AGG", metadata_json={}))
        session.commit()
    return client, principals, grants


def as_user(client, name):
    client.headers["Authorization"] = "Bearer " + name
    return client


def create_topic(client, **scope):
    response = client.post("/api/research/topics", json={"title": "个人讨论", **scope})
    assert response.status_code == 201, response.text
    return response.json()["topic_id"]


def test_anonymous_and_forged_identity_headers_never_resolve(accounts):
    client, _, _ = accounts
    assert client.get("/api/research/catalogue").status_code == 401
    assert client.get("/api/research/topics", headers={"X-User-ID": "alice", "X-Team-Role": "admin"}).status_code == 401
    assert as_user(client, "alice").get("/api/identity").json()["user_id"] == "alice"
    response = as_user(client, "revoked").get("/api/research/catalogue", headers={"Origin": "http://127.0.0.1:5173"})
    assert response.status_code == 401
    assert response.headers["Access-Control-Allow-Origin"] == "http://127.0.0.1:5173"


def test_team_theme_and_pm_views_are_shared_but_authorship_is_immutable(accounts):
    client, _, _ = accounts
    as_user(client, "alice")
    themes_path = "/api/research/instruments/fund-us-agg/themes"
    theme = client.post(themes_path, json={"title": "共同主题", "question": "长债信用如何变化？"}).json()
    path = "/api/instruments/fund-us-agg/research/notes"
    response = client.post(path, json={"note": {"note_date": "2026-09-08", "title": "甲的判断", "author": "伪造姓名",
                           "research_context": {"theme_id": theme["theme_id"]}}})
    assert response.status_code == 200, response.text
    note = response.json()["notes"][0]
    assert note["author_user_id"] == "alice" and note["author"] == "经理甲"
    as_user(client, "bob")
    assert client.get(themes_path).json()["themes"][0]["notes"][0]["note_id"] == note["note_id"]
    assert client.put(path + "/" + note["note_id"], json={"note": {"note_date": "2026-09-08", "title": "替甲改口"}}).status_code in {403, 422}
    assert client.delete(path + "/" + note["note_id"]).status_code == 403
    review = client.post(path, json={"note": {"note_date": "2026-09-08", "title": "乙的复核",
        "research_context": {"theme_id": theme["theme_id"], "relationship": "review", "related_note_id": note["note_id"], "related_revision": 1}}})
    assert review.status_code == 200, review.text
    assert {item["author_user_id"] for item in review.json()["notes"]} == {"alice", "bob"}
    as_user(client, "reader")
    assert client.get(themes_path).status_code == 200
    assert client.post(themes_path, json={"title": "不能写", "question": "不能写"}).status_code == 403
    assert client.post(path, json={"note": {"note_date": "2026-09-08", "title": "不能写"}}).status_code == 403
    assert create_topic(client, instrument_ids=["fund-us-agg"])


def test_private_discussion_and_file_cannot_be_read_by_other_member_or_team_dossier(accounts):
    client, _, _ = accounts
    as_user(client, "alice")
    tid = create_topic(client, instrument_ids=["fund-us-agg"])
    entry = client.post(f"/api/research/topics/{tid}/files", files={"file": ("private.txt", b"private discussion", "text/plain")}).json()
    assert client.get(entry["source"]).status_code == 200
    dossier = client.get("/api/research/instruments/fund-us-agg/dossier").json()
    assert all(row.get("entry_id") != entry["entry_id"] for row in dossier["materials"])
    as_user(client, "bob")
    assert client.get("/api/research/topics").json() == []
    assert client.get(f"/api/research/topics/{tid}").status_code == 404
    assert client.get(entry["source"]).status_code == 404
    assert client.post(f"/api/research/topics/{tid}/entries", json={"title": "侵入"}).status_code == 404


def test_portfolio_scope_is_checked_again_on_history_download_and_run(accounts):
    client, principals, grants = accounts
    as_user(client, "alice")
    assert client.get("/api/research/connections").json()["portfolios"] == [{"portfolio_id": "A", "portfolio_name": "A"}]
    tid = create_topic(client, portfolio_id="A")
    entry = client.post(f"/api/research/topics/{tid}/files", files={"file": ("holdings.txt", b"A holdings", "text/plain")}).json()
    with get_session_factory()() as session:
        session.add(ResearchEntry(entry_id="portfolio-run", topic_id=tid, kind="analysis", title="讨论A", status="running",
                                 context_json={"research_run": True, "portfolio_id": "A", "research_actor": principals["alice"].to_dict()}))
        session.commit()
    principals["run-A"] = replace(principals["alice"], credential="run-A", resource_scope={"kind": "run", "id": "portfolio-run"})
    assert as_user(client, "alice").get("/api/research/runs/portfolio-run/context").status_code == 200
    assert client.post("/api/research/runs/portfolio-run/tools", json={"tool": "portfolio"}).status_code == 403
    assert as_user(client, "run-A").get("/api/research/topics").status_code == 403
    assert client.get("/api/research/runs/other-run/context").status_code == 403
    grants["alice"].clear()
    as_user(client, "alice")
    assert client.get("/api/research/topics").json() == []
    assert client.get(entry["source"]).status_code == 404
    assert client.get("/api/research/runs/portfolio-run/context").status_code == 404
    as_user(client, "bob")
    assert client.get(f"/api/research/topics/{tid}").status_code == 404


def test_browser_cookie_write_requires_trusted_origin(accounts):
    client, _, _ = accounts
    client.cookies.set("__Secure-yungu_session", "alice")
    assert client.get("/api/identity").status_code == 200
    payload = {"title": "个人讨论"}
    assert client.post("/api/research/topics", json=payload).status_code == 403
    assert client.post("/api/research/topics", json=payload, headers={"Origin": "https://attacker.example"}).status_code == 403
    assert client.post("/api/research/topics", json=payload, headers={"Origin": "http://testserver"}).status_code == 201
    assert client.post("/api/research/topics", json=payload, headers={"Origin": "http://127.0.0.1:5173"}).status_code == 201


def test_display_views_and_watchlist_order_are_personal_even_for_readers(accounts):
    client, _, _ = accounts
    as_user(client, "alice")
    first = client.post("/api/watchlists", json={"name": "Team one"}).json()["watchlist_id"]
    second = client.post("/api/watchlists", json={"name": "Team two"}).json()["watchlist_id"]
    initial_order = [row["watchlist_id"] for row in client.get("/api/watchlists").json()]
    initial = client.get(f"/api/watchlists/{first}").json()
    base = next(view for view in initial["views"] if view["view_id"] == initial["default_view_id"])
    payload = {"name": "甲的显示", "default_group_by": "none", "default_sort": [], "default_filters": {},
               "columns": [{"field_key": "instrument_name", "display_order": 1, "width": 320, "is_visible": True}]}
    response = client.put(f"/api/watchlists/{first}/views/{base['view_id']}", json=payload)
    assert response.status_code == 200, response.text
    personal = response.json()["view_id"]
    assert personal != base["view_id"]
    # An already queued save against the base updates the same personal copy.
    assert client.put(f"/api/watchlists/{first}/views/{base['view_id']}", json=payload).json()["view_id"] == personal
    own = client.get(f"/api/watchlists/{first}").json()
    assert own["default_view_id"] == personal
    assert sum(view["view_id"] == personal for view in own["views"]) == 1
    assert client.post("/api/watchlists/reorder", json={"watchlist_ids": list(reversed(initial_order))}).status_code == 200
    as_user(client, "bob")
    assert [row["watchlist_id"] for row in client.get("/api/watchlists").json()] == initial_order
    assert all(view["view_id"] != personal for view in client.get(f"/api/watchlists/{first}").json()["views"])
    assert client.post("/api/screener/query", json={"watchlist_id": first, "view_id": personal}).status_code == 404
    assert client.put(f"/api/watchlists/{first}/views/{personal}", json=payload).status_code == 404
    copied = client.post(f"/api/watchlists/{first}/copy").json()["watchlist_id"]
    assert all(view["name"] != payload["name"] for view in client.get(f"/api/watchlists/{copied}").json()["views"])
    as_user(client, "reader")
    assert client.post(f"/api/watchlists/{second}/views", json=payload).status_code == 200
    assert client.post(f"/api/watchlists/{second}/items", json={"instrument_ids": ["fund-us-agg"]}).status_code == 403


def test_explicit_legacy_claim_preserves_unknown_authors_and_is_dry_by_default(accounts):
    from datetime import UTC, date, datetime
    from watchlist_app.db.models.research import InstrumentResearchNote
    from watchlist_app.services.research_identity_migration import claim_research_identity
    with get_session_factory()() as session:
        session.add_all([
            InstrumentResearchNote(instrument_id="fund-us-agg", note_id="known", note_date=date(2026, 9, 8),
                                   note_type="thesis_update", title="旧作者", author_user_id="local-investor", created_at=datetime.now(UTC), updated_at=datetime.now(UTC)),
            InstrumentResearchNote(instrument_id="fund-us-agg", note_id="unknown", note_date=date(2026, 9, 8),
                                   note_type="thesis_update", title="未认领作者", created_at=datetime.now(UTC), updated_at=datetime.now(UTC)),
        ])
        topic = ResearchTopic(topic_id="unclaimed", title="旧对话", visibility="private", created_by_user_id="placeholder")
        session.add(topic)
        session.flush()
        topic.created_by_user_id = None
        session.commit()
        args = {"user_id": "alice", "display_name": "经理甲", "team_id": "default", "claim_unassigned_conversations": True}
        preview = claim_research_identity(session, **args)
        assert preview["notes"] == preview["conversations"] == 1
        assert session.get(InstrumentResearchNote, ("fund-us-agg", "known")).author_user_id == "local-investor"
        assert topic.created_by_user_id is None
        claim_research_identity(session, **args, dry_run=False)
        assert session.get(InstrumentResearchNote, ("fund-us-agg", "known")).author_user_id == "alice"
        assert session.get(InstrumentResearchNote, ("fund-us-agg", "unknown")).author_user_id is None
        assert topic.created_by_user_id == "alice"



def test_data_maintenance_token_can_enqueue_bulk_but_cannot_impersonate_a_researcher(accounts):
    client, principals, _ = accounts
    principals["maintenance"] = Principal(None, "数据维护", "default", kind="service", service_id="data-maintenance",
                                          team_role="reader", scopes=["watchlist:maintenance"], credential="maintenance")
    as_user(client, "maintenance")
    response = client.post("/api/recalc/bulk", json={"instrument_ids": ["fund-us-agg"]})
    assert response.status_code == 200, response.text
    assert response.json()["accepted_count"] == 1
    assert client.get("/api/research/topics").status_code == 403
    assert client.post("/api/sector-research/runs", json={"instrument_ids": ["fund-us-agg"]}).status_code == 403
    assert client.post("/api/instruments/fund-us-agg/research/notes", json={"note": {"note_date": "2026-09-08", "title": "不能冒充投资经理"}}).status_code == 403



def test_team_reader_can_analyze_an_authorized_portfolio_but_cannot_refresh_shared_team_risk(accounts, monkeypatch):
    from watchlist_app.api.routes import risk_officer
    client, _, grants = accounts
    monkeypatch.setattr(risk_officer, "harness_available", lambda: True)
    queued = []
    monkeypatch.setattr(risk_officer, "run_analysis", lambda run_id, token, issuer: queued.append(run_id))
    as_user(client, "reader")
    path = "/api/risk/review/runs"
    assert client.post(path, json={"instrument_id": "fund-us-agg"}).status_code == 403
    assert client.post(path, json={"portfolio_id": "B"}).status_code == 404
    response = client.post(path, json={"portfolio_id": "A"})
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    assert queued == [run_id]
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        assert run.context_json["risk_scope"] == {"portfolio_id": "A"}
        assert run.context_json["research_actor"]["user_id"] == "reader"
    grants["reader"].clear()
    assert client.get(f"/api/research/runs/{run_id}/context").status_code == 404
    assert client.post(path, json={"portfolio_id": "A"}).status_code == 404
