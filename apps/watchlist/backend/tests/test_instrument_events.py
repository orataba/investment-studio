from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from watchlist_app.api.routes import sector_research as routes
from watchlist_app.db.models import InstrumentAttributeValue, InstrumentDetail, WatchlistItem
from watchlist_app.db.models.research import InstrumentResearchProfile
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic, RiskCase
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_runner, research_workbench, sector_research as service


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


def reply(iid, *, summary="存在需跟进的重要变化。", sources=None, **changes):
    events = [] if sources is None else [{"event_key": "index-rule-change", "action": "new", "direction": "risk",
        "title": "跟踪指数规则变更", "body": "编制机构公告确认指数规则调整，需核对实际跟踪敞口的变化。",
        "next_watch": "核对后续持仓披露和指数编制说明。", "confidence": "confirmed", "information_type": "fact",
        "recording_type": "backfill", "published_at": "2026-06-01", "occurred_at": None,
        "source_ids": sources, **changes}]
    return json.dumps({"reviews": [{"instrument_id": iid, "summary": summary, "coverage": [], "events": events,
                                   "research": None}]})


def test_single_instrument_scope_and_runs_do_not_expand_or_block_sector_daily_checks(client, monkeypatch):
    seed_instruments(client, monkeypatch)
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
    monkeypatch.setattr(routes, "run_analysis", lambda run_id, token: started.append(run_id))
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
        "analyst_focus": research_workbench.ANALYST_FOCUS["etf"],
        "materials": [{"title": "季度报告目录", "file_id": "catalogue-only"}],
        "materials_note": "没有正文的文件不能视为已阅读。",
        "holdings": {"data": {"report_period": "2026-03-31", "items": [{"name": "已披露债券", "weight_percent": 8}]},
                     "source_cutoff_at": "2026-04-30T00:00:00+00:00"}}
    calls = []
    def evidence(session, ids, *, include_dossier=False, as_of=None):
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
        service.apply_result(session, run, reply("fund-us-agg", sources=["index-original"]))
        run.created_at = run.completed_at = datetime.now(UTC) - timedelta(days=1)
        session.commit()
        assert run.status == "completed" and asset["name"] in run.body
        case = session.scalar(select(RiskCase).where(RiskCase.instrument_id == "fund-us-agg"))
        assert case.trigger_active and case.evidence_json["published_at"] == "2026-06-01"
        assert case.history_json[0]["snapshot"]["sources"][0]["text"] == source["text"]
        newer, created = service.begin_run(session, ["fund-us-agg"])
        assert created
        newer.context_json = {**newer.context_json, "web_evidence": [{"operation": "search", "sources": []}]}
        service.apply_result(session, newer, reply("fund-us-agg", summary="本轮未核实到重大新增。"))
        newer.created_at = newer.completed_at = datetime.now(UTC) - timedelta(minutes=1)
        session.commit()
        assert case.trigger_active and len(case.history_json) == 1
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
    assert result["sectors"][0]["last_completed_review"]["summary"] == "存在需跟进的重要变化。"
    assert result["sectors"][0]["last_completed_review"]["change_kind"] == "none"
    assert len(result["events"]) == 1 and result["events"][0]["trigger_active"]


def test_chat_publishes_explicitly_authorized_research_without_pm_adoption_or_invented_fund_opportunity(client, monkeypatch):
    seed_instruments(client, monkeypatch)
    monkeypatch.setattr(research_runner, "harness_available", lambda: True)
    monkeypatch.setattr(research_runner, "run_analysis", lambda run_id, token=None: None)
    topic = client.post("/api/research/topics", json={"title": "私募普通对话", "instrument_ids": ["sxv264"]}).json()
    chat = client.post(f"/api/research/topics/{topic['topic_id']}/analysis", json={"question": "有哪些资料还需补充？"})
    assert chat.status_code == 202, chat.text
    chat_id = chat.json()["entry_id"]
    service.prepare_run(chat_id)
    assert client.post(f"/api/research/runs/{chat_id}/sector-evidence", json={"operation": "search"}).status_code == 200
    with get_session_factory()() as session:
        ordinary = session.get(ResearchEntry, chat_id)
        assert "sxv264" not in service.latest_reviews(session)
        session.add(InstrumentResearchProfile(instrument_id="sxv264", thesis="PM原始投资逻辑", current_view="等待更多资料", revision_number=1))
        ordinary.context_json = {**ordinary.context_json, "team_publication_instructions": {"sxv264": "请保存这轮研究进展到团队研究"}, "instrument_inputs": [{"instrument_id": "sxv264", "name": "SXV264 Total Return Fund",
            "instrument_type": "private_fund", "holdings": None, "materials": [{"title": "月报目录"}]}],
            "web_evidence": [{"operation": "search", "sources": [{"source_id": "material-directory",
                "title": "只有目录，没有正文", "published_at": None, "time_status": "unknown"}]}]}
        with pytest.raises(ValueError, match="原文"):
            service.apply_result(session, ordinary, reply("sxv264", sources=["material-directory"], direction="opportunity", published_at=None))
        assert session.scalar(select(RiskCase).where(RiskCase.instrument_id == "sxv264")) is None
        session.commit()
    draft = {"reviews": [{"instrument_id": "sxv264", "change_kind": "knowledge", "coverage": ["缺少底层敞口和材料正文"],
        "research": {"next_research": ["补充底层敞口与原始材料后再判断。"]}, "events": []}]}
    submitted = client.post(f"/api/research/runs/{chat_id}/sector-draft", json=draft)
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "pending_fact_review"
    with get_session_factory()() as session:
        assert "sxv264" not in service.latest_reviews(session)
        ordinary = session.get(ResearchEntry, chat_id)
        service.apply_result(session, ordinary, json.dumps(draft))
        ordinary.status, ordinary.body = "draft", "缺少材料，已记录下一步研究任务。"
        profile = session.get(InstrumentResearchProfile, "sxv264")
        assert (profile.thesis, profile.current_view, profile.revision_number) == ("PM原始投资逻辑", "等待更多资料", 1)
        session.commit()
    result = client.get("/api/sector-research", params={"instrument_id": "sxv264"}).json()
    assert result["events"] == [] and result["sectors"][0]["latest_review"]["run_id"] == chat_id
    assert result["research_enabled"] is False


@pytest.mark.parametrize("iid", ["savf63", "sxv264"])
def test_fund_run_is_paused_while_saved_research_and_dossier_remain_readable(client, monkeypatch, iid):
    seed_instruments(client, monkeypatch)
    monkeypatch.setattr(routes, "harness_available", lambda: True)
    monkeypatch.setattr(routes, "run_analysis", lambda run_id, token=None: pytest.fail("Paused fund must not launch research"))
    with get_session_factory()() as session:
        topic_id, run_id = f"instrument-events:{iid}", f"saved-{iid}"
        session.add(ResearchTopic(topic_id=topic_id, title="已有研究", instrument_ids=[iid]))
        session.flush()
        session.add(ResearchEntry(entry_id=run_id, topic_id=topic_id, kind="analysis", title="已有研究", status="completed",
            context_json={"sector_run": True, "instrument_ids": [iid], "cutoff": "2026-09-01T00:00:00+00:00",
                "reviews": {iid: {"status": "completed", "summary": "以前保存的研究。", "research": {
                    "fundamental_view": "已有研究判断。", "sources": []}}}}))
        session.commit()
    blocked = client.post("/api/sector-research/runs", json={"instrument_ids": [iid]})
    assert blocked.status_code == 422 and "暂缓" in blocked.text
    overview = client.get("/api/sector-research", params={"instrument_id": iid}).json()
    assert overview["available"] and overview["research_enabled"] is False
    assert overview["sectors"][0]["last_completed_review"]["run_id"] == run_id
    dossier = client.get(f"/api/research/instruments/{iid}/dossier")
    assert dossier.status_code == 200, dossier.text
    assert dossier.json()["notebook"]["fundamental_view"] == "已有研究判断。"


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


def test_daily_research_dispatches_selected_oldest_first_and_does_not_retry_failed_attempts(client, monkeypatch):
    from threading import Event
    from watchlist_app.services import shared_instrument_registry, risk_officer
    seed_instruments(client, monkeypatch)
    monkeypatch.setattr(service, "_research_due", lambda market, now: True)
    monkeypatch.setattr(shared_instrument_registry, "list_shared_active_instrument_ids",
        lambda **kwargs: ["xlk", "event-equity", "event-index", "inactive-equity"])
    monkeypatch.setattr(research_workbench, "portfolio_options", lambda: {"portfolios": []})
    monkeypatch.setattr(risk_officer, "begin_run", lambda session, **scope: (SimpleNamespace(entry_id="risk", status="completed"), False))
    with get_session_factory()() as session:
        session.add_all(InstrumentAttributeValue(instrument_id=iid, attribute_key="coverage_status", value_json="Invested", adopted_at=datetime.now(UTC))
            for iid in ["xlk", "event-equity", "event-index"])
        session.commit()
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
    monkeypatch.setattr(research_runner, "run_analysis", analyze)
    service.run_daily_reviews(Event())
    assert sorted(calls) == [["event-equity"], ["event-index"], ["xlk"]]
    service.run_daily_reviews(Event())
    assert len(calls) == 3
    with get_session_factory()() as session:
        for run in session.scalars(select(ResearchEntry)):
            age = 2 if run.context_json["instrument_ids"] == ["event-equity"] else 1
            run.context_json = {**run.context_json, "cutoff": (datetime.now(UTC) - timedelta(days=age)).isoformat()}
        session.commit()
        assert service.daily_review_groups(session)[0] == ["event-equity"]
    service.run_daily_reviews(Event())
    assert len(calls) == 6 and sorted(calls[3:]) == [["event-equity"], ["event-index"], ["xlk"]]


def test_daily_risk_receives_member_research_before_unwatched_backlog(client, monkeypatch):
    from threading import Event
    from watchlist_app.services import risk_officer
    from watchlist_app.db.models import Watchlist
    seed_instruments(client, monkeypatch)
    with get_session_factory()() as session:
        session.add(Watchlist(watchlist_id="held", name="重点列表", owner_type="team", owner_id="investment-team"))
        session.commit()
    monkeypatch.setattr(service, "daily_review_groups", lambda session: [["event-equity"], ["event-index"]])
    monkeypatch.setattr(research_workbench, "portfolio_options", lambda: {"portfolios": []})
    monkeypatch.setattr(risk_officer, "read_snapshot", lambda session, **scope: {"instrument_ids": ["event-index"]})
    calls = []
    def analyze(run_id):
        with get_session_factory()() as session:
            run = session.get(ResearchEntry, run_id)
            calls.append("risk" if run.context_json.get("risk_run") else run.context_json["instrument_ids"][0])
            run.status = "completed"
            session.commit()
    monkeypatch.setattr(research_runner, "run_analysis", analyze)
    service.run_daily_reviews(Event())
    assert calls[0] == "event-index" and calls[-1] == "event-equity"
    assert set(calls[1:-1]) == {"risk"}
