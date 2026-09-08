"""Previously editable conversation scopes must not expose retained portfolio facts."""
from types import SimpleNamespace

from fastapi import HTTPException
import pytest
from studio_identity import Principal, principal_context

from .test_account_research_access import accounts, as_user, create_topic
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.research_access import enforce_request


def test_legacy_conversation_keeps_all_retained_portfolio_scopes(accounts):
    client, principals, grants = accounts
    as_user(client, "alice")
    topic_id = create_topic(client, portfolio_id="A")
    with get_session_factory()() as session:
        # Older versions allowed changing a populated topic's portfolio. The old
        # run still knows its original scope and must protect the whole history.
        session.add(ResearchEntry(entry_id="legacy-b-run", topic_id=topic_id, kind="analysis", title="组合B旧讨论",
            body="B confidential holding", status="draft", context_json={
                "research_run": True, "portfolio_id": "B", "research_actor": principals["alice"].to_dict(),
                "holdings": [{"private": "B confidential holding"}]}))
        session.commit()
    assert client.get("/api/research/runs/legacy-b-run/context").status_code == 404
    response = client.get(f"/api/research/topics/{topic_id}")
    assert response.status_code == 404
    assert "B confidential holding" not in response.text
    assert client.get("/api/research/topics").json() == []
    # Restoring the actual combination of resource permissions restores history.
    grants["alice"].add("B")
    assert client.get(f"/api/research/topics/{topic_id}").status_code == 200
    grants["alice"].remove("B")
    assert client.post(f"/api/research/topics/{topic_id}/entries", json={"title": "追加讨论"}).status_code == 404


def test_legacy_risk_metadata_is_checked_without_reading_topic_as_team(accounts):
    client, principals, _ = accounts
    as_user(client, "alice")
    topic_id = create_topic(client)
    with get_session_factory()() as session:
        session.add(ResearchEntry(entry_id="retained-risk-run", topic_id=topic_id, kind="analysis", title="旧风险记录",
            body="B confidential risk", status="completed", context_json={"risk_run": True,
                "risk_scope": {"portfolio_id": "B"}, "research_actor": principals["alice"].to_dict()}))
        session.commit()
    assert client.get(f"/api/research/topics/{topic_id}").status_code == 404
    assert client.get("/api/research/runs/retained-risk-run/context").status_code == 404


def test_maintenance_identity_has_only_the_bulk_recalculation_entry(accounts):
    _, _, _ = accounts
    service = Principal(None, "数值维护", "default", kind="service", service_id="data-worker",
                        team_role="reader", scopes=["watchlist:maintenance"], credential="test-maintenance")
    with principal_context(service), get_session_factory()() as session:
        enforce_request(SimpleNamespace(method="POST", url=SimpleNamespace(path="/api/recalc/bulk")), session)
        for method, path in (("GET", "/api/research/catalogue"),
                             ("POST", "/api/instruments/fund-us-agg/research/notes"),
                             ("POST", "/api/sector-research/runs")):
            with pytest.raises(HTTPException) as error:
                enforce_request(SimpleNamespace(method=method, url=SimpleNamespace(path=path)), session)
            assert error.value.status_code == 403


def test_legacy_portfolio_history_cannot_be_republished_as_team_research(accounts, monkeypatch):
    from dataclasses import replace
    from watchlist_app.services import research_runner
    from watchlist_app.services.sector_research import ReviewResult, validate_result
    client, principals, grants = accounts
    grants["alice"].add("B")
    as_user(client, "alice")
    topic_id = create_topic(client, instrument_ids=["fund-us-agg"])
    with get_session_factory()() as session:
        session.add(ResearchEntry(entry_id="old-b", topic_id=topic_id, kind="analysis", title="旧组合B讨论", status="draft",
            context_json={"research_run": True, "portfolio_id": "B", "research_actor": principals["alice"].to_dict()}))
        session.add(ResearchEntry(entry_id="old-unbound", topic_id=topic_id, kind="analysis", title="保存为我的观点", status="draft",
            context_json={"research_run": True, "portfolio_id": None, "instrument_ids": ["fund-us-agg"],
                          "research_actor": principals["alice"].to_dict()}))
        session.commit()
    assert client.get(f"/api/research/topics/{topic_id}").status_code == 200
    monkeypatch.setattr(research_runner, "harness_available", lambda: True)
    response = client.post(f"/api/research/topics/{topic_id}/analysis", json={"question": "继续分析"})
    assert response.status_code == 422 and "新建" in response.text
    note = {"note_date": "2026-09-08", "title": "不能直接把旧组合内容发布到团队"}
    response = client.post("/api/instruments/fund-us-agg/research/notes", json={"note": note, "source_entry_id": "old-unbound"})
    assert response.status_code == 422 and "组合" in response.text
    with get_session_factory()() as session:
        source = session.get(ResearchEntry, "old-unbound")
        with principal_context(principals["alice"]), pytest.raises(ValueError, match="组合"):
            validate_result(session, source, ReviewResult(reviews=[]))
        source.status = "running"
        session.commit()
    principals["bound-old"] = replace(principals["alice"], resource_scope={"kind": "run", "id": "old-unbound"}, credential="bound-old")
    as_user(client, "bound-old")
    for command in ({"action": "publish_research"}, {"action": "record_view", "note": note}):
        response = client.post("/api/research/runs/old-unbound/user-command", json={
            **command, "instrument_id": "fund-us-agg", "source_quote": "保存为我的观点"})
        assert response.status_code == 422 and "组合" in response.text


def test_bound_run_cannot_switch_author_or_use_the_normal_pm_write_api(accounts):
    from dataclasses import replace
    client, principals, _ = accounts
    as_user(client, "alice")
    topic_id = create_topic(client, instrument_ids=["fund-us-agg"])
    with get_session_factory()() as session:
        session.add(ResearchEntry(entry_id="alice-run", topic_id=topic_id, kind="analysis", title="保存为我的观点", status="running",
            context_json={"research_run": True, "instrument_ids": ["fund-us-agg"], "research_actor": principals["alice"].to_dict()}))
        session.commit()
    principals["alice-task"] = replace(principals["alice"], credential="alice-task", resource_scope={"kind": "run", "id": "alice-run"})
    principals["bob-forged-task"] = replace(principals["bob"], credential="bob-forged-task", resource_scope={"kind": "run", "id": "alice-run"})
    assert as_user(client, "bob-forged-task").get("/api/research/runs/alice-run/context").status_code == 403
    as_user(client, "alice-task")
    assert client.get("/api/research/runs/alice-run/context").status_code == 200
    assert client.post("/api/instruments/fund-us-agg/research/notes", json={"note": {"title": "越过工具入口", "note_date": "2026-09-08"}}).status_code == 403
    assert client.get(f"/api/research/topics/{topic_id}").status_code == 403


def test_legacy_portfolio_projection_is_excluded_from_team_research_reads(accounts):
    client, principals, grants = accounts
    grants["alice"].add("B")
    as_user(client, "alice")
    topic_id = create_topic(client, instrument_ids=["fund-us-agg"])
    with get_session_factory()() as session:
        session.add(ResearchEntry(entry_id="projection-source-b", topic_id=topic_id, kind="analysis", title="旧组合研究", status="draft",
            context_json={"research_run": True, "portfolio_id": "B", "research_actor": principals["alice"].to_dict()}))
        session.add(ResearchEntry(entry_id="projection-unbound", topic_id=topic_id, kind="analysis", title="旧汇总", status="draft",
            context_json={"research_run": True, "instrument_ids": ["fund-us-agg"],
                "reviews": {"fund-us-agg": {"status": "completed", "summary": "B confidential thesis",
                    "research": {"fundamental_view": "B confidential thesis", "version_id": "legacy-projection"}}}}))
        session.commit()
    # A published projection derived from retained portfolio history cannot become
    # a team asset, even when this particular reader has access to the portfolio.
    for user in ("alice", "reader"):
        as_user(client, user)
        for path in ("/api/research/instruments/fund-us-agg/dossier?include_history=true",
                     "/api/sector-research?instrument_id=fund-us-agg"):
            response = client.get(path)
            assert response.status_code == 200, response.text
            assert "B confidential thesis" not in response.text
