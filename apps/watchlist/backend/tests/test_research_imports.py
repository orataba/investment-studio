"""Compiled research shares publication invariants without fabricating a model run."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select

from watchlist_app.db.models import InstrumentDetail
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.session import get_session_factory
from .test_account_research_access import accounts, as_user
from .test_research_activity import activity_client, publish as publish_automatic


def package(iid="fund-us-agg"):
    now = datetime.now(UTC)
    return {"schema_version": 1, "import_id": str(uuid4()), "instrument_id": iid,
        "title": "已核对来源的研究基线", "research_as_of": now.date().isoformat(),
        "baseline": True, "verification_note": "编制者核对公告表格；未运行独立模型复核。",
        "sources": [{"source_id": "official", "title": "Original report", "url": "https://example.com/report",
            "published_at": (now - timedelta(days=1)).date().isoformat(),
            "retrieved_at": (now - timedelta(minutes=1)).isoformat(),
            "text": "Quarter ending June 30. Revenue, USD million: 2025 10; 2026 12.",
            "body_kind": "original_excerpt", "locator": "Revenue table, USD million"}],
        "figures": [{"source_id": "computed:import-table", "title": "Revenue", "as_of": now.date().isoformat(),
            "source_ids": ["official"], "methodology": "直接抄录同份披露中的两年同季数据；未执行Python。",
            "output": {"summary": "Reported revenue comparison", "tables": [{"key": "revenue", "title": "Revenue",
                "columns": [{"key": "year", "label": "Year"}, {"key": "value", "label": "Revenue", "unit": "USD million"}],
                "rows": [{"year": "2025", "value": 10}, {"year": "2026", "value": 12}]}],
                "charts": [{"key": "revenue", "title": "Revenue", "kind": "bar", "table_key": "revenue", "x_key": "year",
                    "series": [{"key": "value", "label": "Revenue"}], "x_label": "Year", "y_label": "USD million"}]}}],
        "review": {"instrument_id": iid, "change_kind": "investment", "summary": "首次基线，待进一步验证。",
            "themes": [{"theme_key": "earnings-quality", "title": "增长能否转成现金？", "question": "收入增长是否有现金支撑？",
                "priority": "core", "priority_reason": "决定增长的可持续性", "synthesis": "收入改善，现金回收待验证。",
                "next_check": "下一份财报核对回款", "source_ids": ["official"], "figure_source_ids": ["computed:import-table"]}],
            "research": {"investment_view": {"direction": "收入改善，资本回报仍需验证。", "source_ids": ["official"]},
                "modules": [{"key": "identity-structure", "summary": "研究对象与证据边界", "analysis": "保留原始表格及其统计口径。",
                    "coverage": "supported", "source_ids": ["official"], "figure_source_ids": ["computed:import-table"]}],
                "important_changes": [], "key_drivers": ["收入增长需与现金回收共同验证"],
                "next_research": ["下一财报核对经营现金流"],
                "source_ids": ["official", "computed:import-table"]}}}


def seed(client):
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id="fund-us-agg", instrument_type="etf", detail_view_type="etf", instrument_name="AGG", metadata_json={}))
        session.commit()


def preview(client, draft):
    result = client.post("/api/research/imports/preview", json=draft)
    assert result.status_code == 200, result.text
    return result.json()


def publish(client, draft, state):
    return client.post("/api/research/imports/publish", json={"package": draft, "expected_versions": state["expected_versions"]})


def test_preview_is_read_only_and_publication_retains_originals_and_honest_authorship(client):
    seed(client)
    draft = package()
    state = preview(client, draft)
    with get_session_factory()() as session:
        assert session.scalar(select(func.count()).select_from(ResearchEntry)) == 0
    from watchlist_app.services.market_evidence import text_store
    assert text_store().search("", limit=10)["total"] == 0
    response = publish(client, draft, state)
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    dossier = client.get("/api/research/instruments/fund-us-agg/dossier").json()
    assert dossier["notebook"]["investment_view"]["direction"] == draft["review"]["research"]["investment_view"]["direction"]
    assert dossier["notebook"]["publication"]["display_name"] == "Codex"
    theme = client.get("/api/research/instruments/fund-us-agg/themes").json()["themes"][0]
    assert theme["author"] == theme["updated_by"] == "Codex"
    assert theme["publication"]["independent_model_review"] is False
    assert dossier["pm_views"] == []
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        assert run.source == "Codex · 研究员编制" and "fact_review" not in run.context_json
        source = run.context_json["web_evidence"][0]["sources"][0]
        assert "document_id" in source and source["text"] == draft["sources"][0]["text"]
        assert source["content_completeness"] == "source_excerpt"
        assert source["provenance"]["raw_capture_kind"] == "author_provided_text"
        assert source["provenance"]["locator"] == draft["sources"][0]["locator"]
        assert run.context_json["web_evidence"][0]["operation"] == "author_import"
        computed = run.context_json["computed_metrics"][0]
        assert computed["methodology"]["executed_at"] is None
        assert computed["methodology"]["input_sources"][0]["text"] == draft["sources"][0]["text"]
        assert computed["source_ids"] == [source["source_id"]]
        assert run.author_user_id is None
        fetched = text_store().capture_public_source(draft["sources"][0])
        assert fetched["document_id"] != source["document_id"]
        assert fetched["provenance"]["raw_capture_kind"] == "fetched_text"
    original = client.get("/api/research/instruments/fund-us-agg/dossier", params={
        "source_id": source["source_id"], "version_id": dossier["notebook"]["version_id"]}).json()
    assert original["text"] == draft["sources"][0]["text"]
    assert original["provenance"]["body_kind"] == "original_excerpt"
    assert original["provenance"]["locator"] == draft["sources"][0]["locator"]
    repeated = publish(client, draft, state)
    assert repeated.status_code == 200 and repeated.json()["already_published"] is True
    changed = deepcopy(draft)
    changed["review"]["summary"] = "Different content"
    assert publish(client, changed, state).status_code == 409


def test_optional_figures_and_second_team_publication_are_isolated(accounts):
    from dataclasses import replace
    client, principals, _ = accounts
    principals["carol"] = replace(principals["alice"], user_id="carol", team_id="other", credential="carol")
    as_user(client, "carol")
    draft = package()
    draft.pop("figures")
    draft["review"]["themes"][0].pop("figure_source_ids")
    draft["review"]["research"]["modules"][0].pop("figure_source_ids")
    draft["review"]["research"]["source_ids"] = ["official"]
    result = publish(client, draft, preview(client, draft))
    assert result.status_code == 200, result.text
    run_id = result.json()["run_id"]
    assert client.get("/api/research/topics").json() == []
    dossier_path = "/api/research/instruments/fund-us-agg/dossier"
    assert client.get(dossier_path).json()["notebook"]["publication"]["submitted_by"]["team_id"] == "other"
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        from watchlist_app.db.models.workbench import ResearchTopic
        assert run.team_id == session.get(ResearchTopic, run.topic_id).team_id == "other"
    as_user(client, "alice")
    dossier = client.get(dossier_path).json()
    assert not dossier.get("notebook") and client.get("/api/research/instruments/fund-us-agg/themes").json()["themes"] == []


def test_preview_conflict_and_active_run_do_not_publish_or_capture_sources(client):
    seed(client)
    draft = package()
    state = preview(client, draft)
    from watchlist_app.services.research_themes import ThemeInput, save_theme
    with get_session_factory()() as session:
        save_theme(session, "fund-us-agg", ThemeInput(title="新的重点", question="需要验证什么？"))
        session.commit()
    assert publish(client, draft, state).status_code == 409
    state = preview(client, draft)
    from watchlist_app.services.sector_research import begin_run
    with get_session_factory()() as session:
        begin_run(session, ["fund-us-agg"])
    assert publish(client, draft, state).status_code == 409
    from watchlist_app.services.market_evidence import text_store
    assert text_store().search("", limit=10)["total"] == 0


def test_compiled_replacement_requires_explicit_transition_and_keeps_history(client):
    seed(client)
    first = package()
    assert publish(client, first, preview(client, first)).status_code == 200
    dossier = client.get("/api/research/instruments/fund-us-agg/dossier").json()
    old = client.get("/api/research/instruments/fund-us-agg/themes").json()["themes"][0]
    second = package()
    second["review"]["themes"][0].update(theme_key="returns", title="投资回报能否持续？", question="投入何时回收？")
    second["review"]["themes"].append({"theme_key": old["theme_key"], "theme_id": old["theme_id"],
        "status": "closed", "close_reason": "合并至资本回报主题，保留原结论和来源。"})
    second["review"]["research"]["investment_view"]["direction"] = "重心转向资本回报。"
    assert publish(client, second, preview(client, second)).status_code == 200
    after = client.get("/api/research/instruments/fund-us-agg/dossier?include_history=true").json()
    assert len(after["notebook_history"]) == 2
    closed = next(row for row in client.get("/api/research/instruments/fund-us-agg/themes").json()["themes"] if row["theme_id"] == old["theme_id"])
    assert closed["status"] == "closed" and closed["versions"][0]["status"] == "active"
    assert after["pm_views"] == []


def test_import_rejects_scope_sources_and_baseline_fake_changes(client):
    seed(client)
    for edit in (lambda p: p["review"].update(instrument_id="different"),
                 lambda p: p["sources"][0].update(text=" "),
                 lambda p: p["review"]["research"].update(important_changes=["首次基线不能称为改判"]),
                 lambda p: p["figures"][0].update(source_ids=["missing"]),
                 lambda p: p["sources"][0].update(published_at="2099-01-01"),
                 lambda p: p["review"]["research"].pop("key_drivers"),
                 lambda p: p["review"]["research"].update(mandate_update={"title": "不得变更", "background": "不可借导入修改用户设置"}),
                 lambda p: p["review"]["research"]["modules"][0].update(source_ids=["material:private-other-team"])):
        draft = package()
        edit(draft)
        assert client.post("/api/research/imports/preview", json=draft).status_code == 422


def test_import_cannot_change_reviewed_package_or_pinned_lifecycle(client):
    seed(client)
    draft = package()
    state = preview(client, draft)
    altered = deepcopy(draft)
    altered["review"]["summary"] = "Changed after review"
    assert publish(client, altered, state).status_code == 409
    theme = client.post("/api/research/instruments/fund-us-agg/themes", json={"title": "用户固定的核心问题", "pinned": True}).json()
    draft["review"]["themes"] = [{"theme_id": theme["theme_id"], "theme_key": theme["theme_key"],
                                  "status": "closed", "close_reason": "不允许代替用户取消固定主题"}]
    assert client.post("/api/research/imports/preview", json=draft).status_code == 422


def test_import_does_not_backfill_event_history(client):
    seed(client)
    from watchlist_app.services.research_imports import ResearchImport
    draft = ResearchImport.model_validate(package())
    # Even an existing well-formed event cannot use the compiled-report path.
    from watchlist_app.services.sector_research import SectorEvent
    event = SectorEvent.model_construct(event_key="prior-event")
    import pytest
    with pytest.raises(ValueError, match="不补写事件研究历史"):
        draft.model_copy(update={"review": draft.review.model_copy(update={"events": [event]})}).single_scope()


def test_later_research_does_not_inherit_codex_authorship_and_can_withdraw(activity_client):
    client = activity_client
    draft = package("xlk")
    assert publish(client, draft, preview(client, draft)).status_code == 200
    path = "/api/research/instruments/xlk/dossier"
    theme = client.get("/api/research/instruments/xlk/themes").json()["themes"][0]
    with get_session_factory()() as session:
        publish_automatic(session, research={"investment_view": {"direction": "后续研究修订现金回报判断"}},
            themes=[{"theme_id": theme["theme_id"], "theme_key": theme["theme_key"], "synthesis": "后续研究修订主题认识"}])
    dossier = client.get(path).json()
    assert "publication" not in dossier["notebook"]
    assert "publication" not in dossier["notebook"]["investment_view"]
    saved_theme = client.get("/api/research/instruments/xlk/themes").json()["themes"][0]
    assert not saved_theme["publication"]
    assert saved_theme["versions"][-1]["publication"]["display_name"] == "Codex"
    with get_session_factory()() as session:
        publish_automatic(session, research={"investment_view": None})
    assert client.get(path).json()["notebook"]["investment_view"] is None


def test_import_requires_team_writer_and_run_tokens_cannot_publish(accounts):
    client, principals, _ = accounts
    draft = package()
    assert as_user(client, "reader").post("/api/research/imports/preview", json=draft).status_code == 403
    assert as_user(client, "alice").post("/api/research/imports/preview", json=draft).status_code == 200
    from dataclasses import replace
    principals["run-token"] = replace(principals["alice"], resource_scope={"kind": "run", "id": "other"}, credential="run-token")
    assert as_user(client, "run-token").post("/api/research/imports/preview", json=draft).status_code == 403


def test_existing_research_service_can_compile_without_becoming_a_user(accounts):
    from studio_identity import Principal
    from dataclasses import replace
    client, principals, _ = accounts
    service = Principal(None, "研究发布服务", "default", kind="service", service_id="research-service",
                        team_role="reader", scopes=["watchlist:research"], credential="research-service")
    principals["research-service"] = service
    principals["missing-scope"] = replace(service, scopes=["watchlist:maintenance"], credential="missing-scope")
    principals["service-run"] = replace(service, resource_scope={"kind": "run", "id": "only-this-run"}, credential="service-run")
    draft = package()
    for identity in ["missing-scope", "service-run", "reader"]:
        as_user(client, identity)
        assert client.post("/api/research/imports/preview", json=draft).status_code == 403
        assert client.post("/api/research/imports/publish", json={"package": draft, "expected_versions": {}}).status_code == 403
    as_user(client, "research-service")
    state = preview(client, draft)
    identity = state["publication"]["submitted_by"]
    assert identity["kind"] == "service" and identity["service_id"] == "research-service" and identity["user_id"] is None
    result = publish(client, draft, state)
    assert result.status_code == 200, result.text
    dossier = client.get("/api/research/instruments/fund-us-agg/dossier").json()
    assert dossier["notebook"]["publication"]["submitted_by"] == identity
    assert dossier["notebook"]["publication"]["display_name"] == "Codex"
    assert dossier["pm_views"] == []
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, result.json()["run_id"])
        assert run.author_user_id is None and run.context_json["research_actor"] == identity
    for route in ["/api/instruments/fund-us-agg/research/notes",
                  "/api/research/instruments/fund-us-agg/dossier/materials",
                  "/api/research/topics", "/api/research/imports/anything-else"]:
        assert client.post(route, json={}).status_code == 403
    assert client.put("/api/research/imports/publish", json={}).status_code == 403
    assert client.put("/api/research/instruments/fund-us-agg/dossier/mandate", json={}).status_code == 403
