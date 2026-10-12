from .research_sections_fixture import completed_sections
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from watchlist_app.api.routes import sector_research as routes
from watchlist_app.db.models import InstrumentDetail, WatchlistItem
from watchlist_app.db.models.research import InstrumentResearchProfile
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic, RiskCase
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_runner, research_workbench, sector_research as service
from .research_policy_fixture import seed_invested_research


def seed_instruments(client, monkeypatch):
    monkeypatch.setattr(service, "_research_market", lambda session, iid: "us" if iid in {"xlk", "fund-us-agg"} else "cn")
    with get_session_factory()() as session:
        for iid, kind, name, active in (
            ("event-equity", "equity", "示例股份", True),
            ("event-index", "index", "示例价格指数", True),
            ("savf63", "public_fund", "SAVF63 Short Duration Income Fund", True),
            ("sxv264", "private_fund", "SXV264 Total Return Fund", True),
            ("fund-us-agg", "etf", "iShares Core U.S. Aggregate Bond ETF", True),
            ("xlk", "etf", "Technology Select Sector SPDR Fund", True),
            ("inactive-equity", "equity", "已停用股份", False),
        ):
            session.add(InstrumentDetail(instrument_id=iid, instrument_type=kind, detail_view_type=kind,
                instrument_name=name, is_active=active, metadata_json={}))
        session.commit()


def reply(iid, *, summary="存在需跟进的重要变化。", sources=None, themes=None, **changes):
    events = [] if sources is None else [{"event_key": "index-rule-change", "action": "new", "direction": "risk",
        "importance_score": 3, "importance_reason": "规则调整可能改变持有产品的实际敞口。",
        "title": "跟踪指数规则变更", "body": "编制机构公告确认指数规则调整，需核对实际跟踪敞口的变化。",
        "next_watch": "核对后续持仓披露和指数编制说明。", "confidence": "confirmed", "information_type": "fact",
        "recording_type": "backfill", "published_at": "2026-06-01", "occurred_at": None,
        "theme_ids": ["index-exposure"], "source_ids": sources, **changes}]
    if themes is None:
        themes = [{"theme_key": "index-exposure", "kind": "event", "title": "指数规则及跟踪敞口",
            "question": "指数编制调整是否改变产品实际跟踪敞口？",
            "priority_reason": "规则变化的组合影响仍未确认，需要结合后续披露跟进。", "source_ids": sources,
            "synthesis": "编制机构已公告规则调整，产品实际敞口是否随之改变仍需核实。",
            "next_check": "对照新编制说明及下一期持仓披露，核对实际敞口变化。"}] if events else []
    return json.dumps({"reviews": [{"instrument_id": iid, "summary": summary,
                                   "change_kind": "investment" if events else "none", "coverage": [], "events": events,
                                   "themes": themes, "research": None}]})


def test_single_instrument_scope_and_runs_do_not_expand_or_block_sector_daily_checks(client, monkeypatch):
    seed_instruments(client, monkeypatch)
    seed_invested_research(monkeypatch, ["event-equity", "event-index", "xlk"])
    ids = ["event-equity", "savf63", "sxv264", "fund-us-agg", "event-index"]
    with get_session_factory()() as session:
        names = {iid: session.get(InstrumentDetail, iid).instrument_name for iid in ids}
    for iid in ids:
        response = client.get("/api/sector-research", params={"instrument_id": iid})
        assert response.status_code == 200, response.text
        assert response.json()["available"]
        assert response.json()["sectors"] == [{"instrument_id": iid, "ticker": iid.upper(),
            "sector_name": names[iid], "latest_review": None, "last_completed_review": None}]
    assert client.get("/api/sector-research", params={"instrument_id": "inactive-equity"}).json()["sectors"] == []
    default = client.get("/api/sector-research").json()
    assert [row["instrument_id"] for row in default["sectors"]] == ["xlk"] and default["available"]
    wid = client.post("/api/watchlists", json={"name": "Mixed event scope"}).json()["watchlist_id"]
    with get_session_factory()() as session:
        session.add_all([WatchlistItem(watchlist_id=wid, instrument_id=iid, added_at=datetime.now(UTC))
                         for iid in ["xlk", "sxv264"]])
        session.commit()
    assert [row["instrument_id"] for row in client.get("/api/sector-research", params={"watchlist_id": wid}).json()["sectors"]] == ["xlk"]
    started = []
    monkeypatch.setattr(routes, "harness_available", lambda: True)
    monkeypatch.setattr("watchlist_app.services.research_execution.enqueue_run", lambda principal, run_id, **kwargs: started.append(run_id))
    first = client.post("/api/sector-research/runs", json={"instrument_ids": ["event-equity"]})
    second = client.post("/api/sector-research/runs", json={"instrument_ids": ["event-index"]})
    assert first.status_code == second.status_code == 202
    assert started == [first.json()["run_id"], second.json()["run_id"]]
    assert client.post("/api/sector-research/runs", json={"instrument_ids": ["event-equity", "event-index"]}).status_code == 422
    assert client.post("/api/sector-research/runs", json={"instrument_ids": ["sxv264", "savf63"]}).status_code == 422
    assert client.post("/api/sector-research/runs", json={"instrument_ids": ["xlk", "sxv264"]}).status_code == 422
    without_fmp = client.post("/api/sector-research/runs", json={"instrument_ids": ["xlk"]})
    assert without_fmp.status_code == 202
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, first.json()["run_id"]).topic_id == "instrument-events:event-equity"
        assert session.get(ResearchEntry, second.json()["run_id"]).topic_id == "instrument-events:event-index"
        daily, created = service.begin_run(session, ["xlk"])
        assert not created and daily.topic_id == "instrument-events:xlk" and daily.entry_id == without_fmp.json()["run_id"]
        assert "FMP" not in daily.source
        assert service.latest_reviews(session)["event-equity"]["run_id"] == first.json()["run_id"]


def test_prepare_preserves_bond_etf_evidence_and_original_event_survives_latest_empty_check(client, monkeypatch):
    seed_instruments(client, monkeypatch)
    asset = {"instrument_id": "fund-us-agg", "name": "iShares Core U.S. Aggregate Bond ETF", "instrument_type": "etf",
        "analyst_focus": "依据本标的已确认的债券结构研究利率与信用。",
        "materials": [{"title": "季度报告目录", "file_id": "catalogue-only"}],
        "materials_note": "没有正文的文件不能视为已阅读。",
        "holdings": {"data": {"report_period": "2026-03-31", "items": [{"name": "已披露债券", "weight_percent": 8}]},
                     "source_cutoff_at": "2026-04-30T00:00:00+00:00"}}
    calls = []
    def evidence(session, ids, *, include_dossier=False, as_of=None, working_only=False):
        assert working_only is True
        calls.append(ids)
        return {"assets": [deepcopy(asset)]}
    monkeypatch.setattr(research_workbench, "instrument_evidence", evidence)
    monkeypatch.setattr(service, "sector_snapshot", lambda *args: pytest.fail("A bond ETF run must not read equity-sector FMP data"))
    with get_session_factory()() as session:
        run, created = service.begin_run(session, ["fund-us-agg"])
        run_id = run.entry_id
        assert created
    service.prepare_run(run_id)
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        assert calls == [["fund-us-agg"]]
        bound = run.context_json["instrument_inputs"][0]
        assert {key: bound[key] for key in asset} == asset
        assert bound["source_id"] == f"instrument:{run_id}:fund-us-agg"
        assert datetime.fromisoformat(bound["snapshot_cutoff"]) <= datetime.fromisoformat(run.context_json["cutoff"])
        assert run.context_json["sector_company_data"] == {} and run.context_json["sector_estimate_evidence"] == []
        assert next(row for row in run.context_json["catalogue"] if row["instrument_id"] == "fund-us-agg")["instrument_type"] == "etf"
        source = {"source_id": "index-original", "url": "https://index.example/rule-change", "title": "指数规则公告",
                  "text": "编制机构公告确认指数规则调整。", "published_at": "2026-06-01", "time_status": "date_only"}
        run.context_json = {**run.context_json, "web_evidence": [{"operation": "search", "sources": []},
                            {"operation": "fetch", "sources": [source]}]}
        completed_sections(run)
        service.apply_result(session, run, reply("fund-us-agg", sources=["index-original"]))
        run.created_at = run.completed_at = datetime.now(UTC) - timedelta(days=1)
        session.commit()
        assert run.status == "completed" and asset["name"] in run.body
        case = session.scalar(select(RiskCase).where(RiskCase.instrument_id == "fund-us-agg"))
        assert not case.trigger_active and case.evidence_json["published_at"] == "2026-06-01"
        assert case.evidence_json["risk_assessment"]["status"] == "pending"
        assert case.history_json[0]["snapshot"]["sources"][0]["text"] == source["text"]
        newer, created = service.begin_run(session, ["fund-us-agg"])
        assert created
        from watchlist_app.services.research_dossier import read_dossier
        bound = read_dossier(session, "fund-us-agg")
        newer.context_json = {**newer.context_json, "research_dossiers": [bound],
                             "web_evidence": [{"operation": "search", "sources": []}]}
        completed_sections(newer)
        service.apply_result(session, newer, reply("fund-us-agg", summary="本轮未核实到重大新增。",
            themes=[{"theme_id": item["theme_id"], "theme_key": item["theme_key"]} for item in bound["themes"]]))
        newer.created_at = newer.completed_at = datetime.now(UTC) - timedelta(minutes=1)
        session.commit()
        assert not case.trigger_active and len(case.history_json) == 1
        assert case.evidence_json["follow_up"] == "watch"
        newest_id = newer.entry_id
        failed, created = service.begin_run(session, ["fund-us-agg"])
        assert created
        failed.status, failed.body = "failed", "本轮核证未完成。"
        failed.completed_at = datetime.now(UTC)
        session.commit()
        failed_id = failed.entry_id
    result = client.get("/api/sector-research", params={"instrument_id": "fund-us-agg"}).json()
    assert result["sectors"][0]["latest_review"]["run_id"] == failed_id
    assert result["sectors"][0]["latest_review"]["status"] == "failed"
    assert result["sectors"][0]["last_completed_review"]["run_id"] == newest_id
    # Research follow-up remains active while risk assessment is pending; event prose is not the overall judgment.
    assert result["sectors"][0]["last_completed_review"]["summary"] == ""
    assert result["sectors"][0]["last_completed_review"]["change_kind"] == "none"
    assert len(result["events"]) == 1 and not result["events"][0]["trigger_active"]
    assert result["events"][0]["risk_assessment"]["status"] == "pending"


def test_chat_publishes_authorized_equity_research_without_overwriting_pm_view(client, monkeypatch):
    seed_instruments(client, monkeypatch)
    seed_invested_research(monkeypatch, ["event-equity"])
    monkeypatch.setattr(research_runner, "harness_available", lambda: True)
    monkeypatch.setattr("watchlist_app.services.research_execution.enqueue_run", lambda principal, run_id, **kwargs: None)
    topic = client.post("/api/research/topics", json={"title": "股票普通对话", "instrument_ids": ["event-equity"]}).json()
    chat = client.post(f"/api/research/topics/{topic['topic_id']}/analysis", json={"question": "有哪些资料还需补充？"})
    assert chat.status_code == 202, chat.text
    chat_id = chat.json()["entry_id"]
    service.prepare_run(chat_id)
    assert client.post(f"/api/research/runs/{chat_id}/sector-evidence", json={"operation": "search"}).status_code == 200
    with get_session_factory()() as session:
        ordinary = session.get(ResearchEntry, chat_id)
        assert "event-equity" not in service.latest_reviews(session)
        session.add(InstrumentResearchProfile(instrument_id="event-equity", thesis="PM原始投资逻辑", current_view="等待更多资料", revision_number=1))
        ordinary.context_json = {**ordinary.context_json, "team_publication_instructions": {"event-equity": "请保存这轮研究进展到团队研究"}, "instrument_inputs": [{"instrument_id": "event-equity", "name": "示例股份",
            "instrument_type": "equity", "holdings": None, "materials": [{"title": "月报目录"}]}],
            "web_evidence": [{"operation": "search", "sources": [{"source_id": "material-directory",
                "title": "只有目录，没有正文", "published_at": None, "time_status": "unknown"}]}]}
        with pytest.raises(ValueError, match="未取得的原始依据：material-directory"):
            completed_sections(ordinary)
            service.apply_result(session, ordinary, reply("event-equity", sources=["material-directory"], direction="opportunity", published_at=None))
        assert session.scalar(select(RiskCase).where(RiskCase.instrument_id == "event-equity")) is None
        session.commit()
    draft = {"reviews": [{"instrument_id": "event-equity", "change_kind": "knowledge", "coverage": ["缺少底层敞口和材料正文"],
        "research": {"next_research": ["补充底层敞口与原始材料后再判断。"]}, "events": []}]}
    submitted = client.post(f"/api/research/runs/{chat_id}/sector-draft", json=draft)
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "pending_fact_review"
    with get_session_factory()() as session:
        assert "event-equity" not in service.latest_reviews(session)
        ordinary = session.get(ResearchEntry, chat_id)
        completed_sections(ordinary)
        service.apply_result(session, ordinary, json.dumps(draft))
        ordinary.status, ordinary.body = "draft", "缺少材料，已记录下一步研究任务。"
        profile = session.get(InstrumentResearchProfile, "event-equity")
        assert (profile.thesis, profile.current_view, profile.revision_number) == ("PM原始投资逻辑", "等待更多资料", 1)
        from watchlist_app.db.models import InstrumentAttributeValue
        session.add(InstrumentAttributeValue(instrument_id="event-equity", attribute_key="coverage_status",
            value_json="Exited", adopted_at=datetime.now(UTC)))
        session.commit()
    result = client.get("/api/sector-research", params={"instrument_id": "event-equity"}).json()
    assert result["events"] == [] and result["sectors"][0]["latest_review"]["run_id"] == chat_id
    # A saved conversational publication remains readable without making this
    # non-Invested asset eligible for another shared model run.
    assert result["research_enabled"] is False


@pytest.mark.parametrize("iid", ["savf63", "sxv264"])
def test_other_asset_research_is_paused_while_saved_dossier_remains_readable(client, monkeypatch, iid):
    seed_instruments(client, monkeypatch)
    seed_invested_research(monkeypatch, [iid])
    monkeypatch.setattr(routes, "harness_available", lambda: True)
    started = []
    monkeypatch.setattr("watchlist_app.services.research_execution.enqueue_run", lambda principal, run_id, **kwargs: started.append(run_id))
    with get_session_factory()() as session:
        topic_id, run_id = f"instrument-events:{iid}", f"saved-{iid}"
        session.add(ResearchTopic(topic_id=topic_id, title="已有研究", instrument_ids=[iid]))
        session.flush()
        session.add(ResearchEntry(entry_id=run_id, topic_id=topic_id, kind="analysis", title="已有研究", status="completed",
            context_json={"sector_run": True, "instrument_ids": [iid], "cutoff": "2026-09-01T00:00:00+00:00",
                "reviews": {iid: {"status": "completed", "summary": "以前保存的研究。", "research": {
                    "fundamental_view": "已有研究判断。", "sources": []}}}}))
        session.commit()
    requested = client.post("/api/sector-research/runs", json={"instrument_ids": [iid]})
    assert requested.status_code == 409, requested.text
    assert started == []
    overview = client.get("/api/sector-research", params={"instrument_id": iid}).json()
    assert overview["available"] and overview["research_enabled"] is False
    assert overview["sectors"][0]["last_completed_review"]["run_id"] == run_id
    dossier = client.get(f"/api/research/instruments/{iid}/dossier")
    assert dossier.status_code == 200, dossier.text
    assert dossier.json()["notebook"]["prior_analysis"]["fundamental_view"] == "已有研究判断。"


def test_non_sector_etf_singleton_keeps_its_instrument_sources(client, monkeypatch):
    seed_instruments(client, monkeypatch)
    monkeypatch.setattr(service, "sector_snapshot", lambda *args: pytest.fail("A non-sector ETF must not use the US11 FMP snapshot"))
    monkeypatch.setattr(research_workbench, "instrument_evidence", lambda session, ids, **kwargs: {"assets": [
        {"instrument_id": ids[0], "name": "Aggregate Bond ETF", "instrument_type": "etf"}]})
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, ["fund-us-agg"], scheduled=True)
        run_id = run.entry_id
    service.prepare_run(run_id)
    with get_session_factory()() as session:
        context = session.get(ResearchEntry, run_id).context_json
        assert context["sector_inputs"] == [] and context["sector_company_data"] == {}
        assert context["instrument_inputs"][0]["instrument_id"] == "fund-us-agg"


def test_daily_research_orders_invested_assets_but_only_dispatches_new_information(client, monkeypatch):
    from threading import Event
    from watchlist_app.services import risk_officer
    seed_instruments(client, monkeypatch)
    seed_invested_research(monkeypatch, ["xlk", "event-equity", "event-index"])
    monkeypatch.setattr(service, "_research_due", lambda market, now: True)
    monkeypatch.setattr(risk_officer, "begin_run", lambda session, **scope: (SimpleNamespace(entry_id="risk", status="completed"), False))
    with get_session_factory()() as session:
        prior, _ = service.begin_run(session, ["event-equity"])
        prior.status = "completed"
        prior.context_json = {**prior.context_json, "cutoff": (datetime.now(UTC) - timedelta(days=2)).isoformat()}
        session.commit()
        assert service.daily_review_groups(session) == [["event-index"], ["xlk"], ["event-equity"]]
    calls = []
    def analyze(run_id):
        with get_session_factory()() as session:
            run = session.get(ResearchEntry, run_id)
            assert run.context_json["scheduled"] is True
            ids = run.context_json["instrument_ids"]
            calls.append(ids)
            run.status = "failed" if ids == ["xlk"] else "completed"
            session.commit()
    monkeypatch.setattr("watchlist_app.services.research_execution.enqueue_run", lambda principal, run_id, **kwargs: analyze(run_id))
    service.run_daily_reviews(Event())
    # The completed equity has no new material input; scheduling it for a cheap
    # scan does not authorize another model call merely because a day passed.
    assert sorted(calls) == [["event-index"], ["xlk"]]
    service.run_daily_reviews(Event())
    assert len(calls) == 2
    with get_session_factory()() as session:
        for run in session.scalars(select(ResearchEntry)):
            age = 2 if run.context_json["instrument_ids"] == ["event-equity"] else 1
            run.context_json = {**run.context_json, "cutoff": (datetime.now(UTC) - timedelta(days=age)).isoformat()}
        session.commit()
        assert service.daily_review_groups(session)[0] == ["event-equity"]
    service.run_daily_reviews(Event())
    assert len(calls) == 2
    from watchlist_app.services.research_themes import ThemeInput, save_theme
    with get_session_factory()() as session:
        save_theme(session, "event-equity", ThemeInput(title="新请求的资本开支主题", question="资本开支是否改变融资需求？"))
        session.commit()
    service.run_daily_reviews(Event())
    assert calls[2:] == [["event-equity"]]


def test_daily_risk_waits_for_invested_research_without_expanding_to_watchlists(client, monkeypatch):
    from threading import Event
    from watchlist_app.core.settings import get_settings
    from watchlist_app.services import risk_officer
    from watchlist_app.db.models import Watchlist
    seed_instruments(client, monkeypatch)
    seed_invested_research(monkeypatch, ["event-equity", "event-index"])
    monkeypatch.setattr(get_settings(), "research_worker_concurrency", 1)
    with get_session_factory()() as session:
        session.add(Watchlist(watchlist_id="held", name="重点列表", owner_type="team", owner_id="investment-team"))
        session.flush()
        session.add(WatchlistItem(watchlist_id="held", instrument_id="event-index", added_at=datetime.now(UTC)))
        session.add(RiskCase(case_id="index-referral", instrument_id="event-index", signal="sector:index-change",
            title="待确认的指数规则风险", status="open", trigger_active=False, evidence_json={
                "direction": "risk", "risk_assessment": {"status": "pending", "event_version_id": "index-change:1"}}))
        session.commit()
    monkeypatch.setattr(service, "daily_review_groups", lambda session: [["event-equity"], ["event-index"]])
    monkeypatch.setattr(research_workbench, "portfolio_options", lambda:
        pytest.fail("Invested-asset scheduling must not enumerate portfolios"))
    monkeypatch.setattr(risk_officer, "read_snapshot", lambda *args, **kwargs:
        pytest.fail("Scheduling reads the member directory, not the full risk evidence corpus"))
    calls = []
    def analyze(run_id):
        with get_session_factory()() as session:
            run = session.get(ResearchEntry, run_id)
            if run.context_json.get("risk_run"):
                assert run.context_json["risk_scope"] == {"instrument_id": "event-index"}
                assert set(run.context_json["research_dates"]) == {"event-index"}
                member = session.scalar(select(ResearchEntry).where(
                    ResearchEntry.topic_id == "instrument-events:event-index", ResearchEntry.kind == "analysis"))
                assert member is not None and member.status == "completed"
                calls.append("risk")
            else:
                calls.append(run.context_json["instrument_ids"][0])
            run.status = "completed"
            session.commit()
    monkeypatch.setattr("watchlist_app.services.research_execution.enqueue_run", lambda principal, run_id, **kwargs: analyze(run_id))
    service.run_daily_reviews(Event())
    assert calls == ["event-equity", "event-index", "risk"]
