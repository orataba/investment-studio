from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from watchlist_app.api.routes import sector_research as routes
from watchlist_app.db.models import InstrumentAttributeValue, InstrumentDetail, WatchlistItem
from watchlist_app.db.models.workbench import ResearchEntry, RiskCase
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_runner, research_workbench, sector_research as service


def seed_instruments(client, monkeypatch):
    settings = SimpleNamespace(sector_market_database_path=None)
    monkeypatch.setattr(service, "get_settings", lambda: settings)
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
    return settings


def reply(iid, *, summary="存在需跟进的重要变化。", sources=None, **changes):
    events = [] if sources is None else [{"event_key": "manager-change", "action": "new", "direction": "risk",
        "title": "基金经理变更", "body": "管理人公告确认基金经理变更，需核实投资流程的连续性。",
        "next_watch": "核对后续持仓披露和管理人说明。", "confidence": "confirmed", "information_type": "fact",
        "recording_type": "backfill", "published_at": "2026-06-01", "occurred_at": None,
        "source_ids": sources, **changes}]
    return json.dumps({"reviews": [{"instrument_id": iid, "summary": summary, "coverage": [], "events": events,
                                   "research": {"fundamental_view": "", "valuation_view": ""}}]})


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
    monkeypatch.setattr(routes, "run_analysis", started.append)
    first = client.post("/api/sector-research/runs", json={"instrument_ids": ["sxv264"]})
    second = client.post("/api/sector-research/runs", json={"instrument_ids": ["savf63"]})
    assert first.status_code == second.status_code == 202
    assert started == [first.json()["run_id"], second.json()["run_id"]]
    assert client.post("/api/sector-research/runs", json={"instrument_ids": ["sxv264", "savf63"]}).status_code == 422
    assert client.post("/api/sector-research/runs", json={"instrument_ids": ["xlk", "sxv264"]}).status_code == 422
    without_fmp = client.post("/api/sector-research/runs", json={"instrument_ids": ["xlk"]})
    assert without_fmp.status_code == 202
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, first.json()["run_id"]).topic_id == "instrument-events:sxv264"
        assert session.get(ResearchEntry, second.json()["run_id"]).topic_id == "instrument-events:savf63"
        daily, created = service.begin_run(session, ["xlk"])
        assert not created and daily.topic_id == "instrument-events:xlk" and daily.entry_id == without_fmp.json()["run_id"]
        assert "FMP" not in daily.source
        assert service.latest_reviews(session)["sxv264"]["run_id"] == first.json()["run_id"]


def test_prepare_preserves_fund_evidence_and_original_event_survives_latest_empty_check(client, monkeypatch):
    seed_instruments(client, monkeypatch)
    asset = {"instrument_id": "savf63", "name": "SAVF63 Short Duration Income Fund", "instrument_type": "public_fund",
        "analyst_focus": research_workbench.ANALYST_FOCUS["public_fund"],
        "materials": [{"title": "季度报告目录", "file_id": "catalogue-only"}],
        "materials_note": "没有正文的文件不能视为已阅读。",
        "holdings": {"data": {"report_period": "2026-03-31", "items": [{"name": "已披露债券", "weight_percent": 8}]},
                     "source_cutoff_at": "2026-04-30T00:00:00+00:00"}}
    calls = []
    def evidence(session, ids, *, include_dossier=False):
        calls.append(ids)
        return {"assets": [deepcopy(asset)]}
    monkeypatch.setattr(research_workbench, "instrument_evidence", evidence)
    monkeypatch.setattr(service, "sector_snapshot", lambda *args: pytest.fail("A fund event run must not read sector FMP data"))
    with get_session_factory()() as session:
        run, created = service.begin_run(session, ["savf63"])
        run_id = run.entry_id
        assert created
    service.prepare_run(run_id)
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        assert calls == [["savf63"]]
        assert run.context_json["instrument_inputs"] == [{**asset, "source_id": f"instrument:{run_id}:savf63"}]
        assert run.context_json["sector_company_data"] == {} and run.context_json["sector_estimate_evidence"] == []
        assert run.context_json["catalogue"][0]["instrument_type"] == "public_fund"
        source = {"source_id": "manager-original", "url": "https://fund.example/manager-change", "title": "管理人公告",
                  "text": "管理人公告确认基金经理变更。", "published_at": "2026-06-01", "time_status": "date_only"}
        run.context_json = {**run.context_json, "web_evidence": [{"operation": "search", "sources": []},
                            {"operation": "fetch", "sources": [source]}]}
        service.apply_result(session, run, reply("savf63", sources=["manager-original"]))
        run.created_at = datetime.now(UTC) - timedelta(days=1)
        session.commit()
        assert run.status == "completed" and asset["name"] in run.body
        case = session.scalar(select(RiskCase).where(RiskCase.instrument_id == "savf63"))
        assert case.trigger_active and case.evidence_json["published_at"] == "2026-06-01"
        assert case.history_json[0]["snapshot"]["sources"][0]["text"] == source["text"]
        newer, created = service.begin_run(session, ["savf63"])
        assert created
        newer.context_json = {**newer.context_json, "web_evidence": [{"operation": "search", "sources": []}]}
        service.apply_result(session, newer, reply("savf63", summary="本轮未核实到重大新增。"))
        newer.created_at = datetime.now(UTC) - timedelta(minutes=1)
        session.commit()
        assert case.trigger_active and len(case.history_json) == 1
        newest_id = newer.entry_id
        failed, created = service.begin_run(session, ["savf63"])
        assert created
        failed.status, failed.body = "failed", "本轮核证未完成。"
        session.commit()
        failed_id = failed.entry_id
    result = client.get("/api/sector-research", params={"instrument_id": "savf63"}).json()
    assert result["sectors"][0]["latest_review"]["run_id"] == failed_id
    assert result["sectors"][0]["latest_review"]["status"] == "failed"
    assert result["sectors"][0]["last_completed_review"]["run_id"] == newest_id
    assert result["sectors"][0]["last_completed_review"]["summary"] == "本轮未核实到重大新增。"
    assert len(result["events"]) == 1 and result["events"][0]["trigger_active"]


def test_chat_is_not_an_event_review_and_missing_private_fund_material_is_not_opportunity(client, monkeypatch):
    seed_instruments(client, monkeypatch)
    monkeypatch.setattr(research_runner, "harness_available", lambda: True)
    monkeypatch.setattr(research_runner, "run_analysis", lambda run_id: None)
    topic = client.post("/api/research/topics", json={"title": "私募普通对话", "instrument_ids": ["sxv264"]}).json()
    chat = client.post(f"/api/research/topics/{topic['topic_id']}/analysis", json={"question": "有哪些资料还需补充？"})
    assert chat.status_code == 202, chat.text
    chat_id = chat.json()["entry_id"]
    assert client.post(f"/api/research/runs/{chat_id}/sector-evidence", json={"operation": "search"}).status_code == 404
    with get_session_factory()() as session:
        ordinary = session.get(ResearchEntry, chat_id)
        ordinary.status, ordinary.body = "draft", "常规对话回答。"
        session.commit()
        assert "sxv264" not in service.latest_reviews(session)
        run, _ = service.begin_run(session, ["sxv264"])
        run.context_json = {**run.context_json, "instrument_inputs": [{"instrument_id": "sxv264", "name": "SXV264 Total Return Fund",
            "instrument_type": "private_fund", "holdings": None, "materials": [{"title": "月报目录"}]}],
            "web_evidence": [{"operation": "search", "sources": [{"source_id": "material-directory",
                "title": "只有目录，没有正文", "published_at": None, "time_status": "unknown"}]}]}
        with pytest.raises(ValueError, match="原文"):
            service.apply_result(session, run, reply("sxv264", sources=["material-directory"], direction="opportunity", published_at=None))
        assert session.scalar(select(RiskCase).where(RiskCase.instrument_id == "sxv264")) is None
        service.apply_result(session, run, reply("sxv264", summary="缺少底层敞口和材料正文，尚无可核实的重大风险或机会。"))
        session.commit()
    result = client.get("/api/sector-research", params={"instrument_id": "sxv264"}).json()
    assert result["events"] == [] and result["sectors"][0]["latest_review"]["run_id"] != chat_id


def test_non_sector_etf_singleton_keeps_its_instrument_sources(client, monkeypatch):
    settings = seed_instruments(client, monkeypatch)
    settings.sector_market_database_path = "/test/fmp.duckdb"
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
    monkeypatch.setattr(shared_instrument_registry, "list_shared_active_instrument_ids",
        lambda **kwargs: ["xlk", "event-equity", "savf63", "inactive-equity"])
    monkeypatch.setattr(research_workbench, "portfolio_options", lambda: {"portfolios": []})
    monkeypatch.setattr(risk_officer, "begin_run", lambda session, **scope: (SimpleNamespace(entry_id="risk", status="completed"), False))
    with get_session_factory()() as session:
        session.add_all(InstrumentAttributeValue(instrument_id=iid, attribute_key="coverage_status", value_json="Invested", adopted_at=datetime.now(UTC))
            for iid in ["xlk", "event-equity", "savf63"])
        session.commit()
        prior, _ = service.begin_run(session, ["event-equity"])
        prior.status = "completed"
        prior.context_json = {**prior.context_json, "cutoff": (datetime.now(UTC) - timedelta(days=2)).isoformat()}
        session.commit()
        assert service.daily_review_groups(session) == [["savf63"], ["xlk"], ["event-equity"]]
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
    assert sorted(calls) == [["event-equity"], ["savf63"], ["xlk"]]
    service.run_daily_reviews(Event())
    assert len(calls) == 3
    with get_session_factory()() as session:
        for run in session.scalars(select(ResearchEntry)):
            age = 2 if run.context_json["instrument_ids"] == ["event-equity"] else 1
            run.context_json = {**run.context_json, "cutoff": (datetime.now(UTC) - timedelta(days=age)).isoformat()}
        session.commit()
        assert service.daily_review_groups(session)[0] == ["event-equity"]
    service.run_daily_reviews(Event())
    assert len(calls) == 6 and sorted(calls[3:]) == [["event-equity"], ["savf63"], ["xlk"]]


def test_daily_risk_receives_member_research_before_unwatched_backlog(client, monkeypatch):
    from threading import Event
    from watchlist_app.services import risk_officer
    from watchlist_app.db.models import Watchlist
    seed_instruments(client, monkeypatch)
    with get_session_factory()() as session:
        session.add(Watchlist(watchlist_id="held", name="重点列表", owner_type="team", owner_id="investment-team"))
        session.commit()
    monkeypatch.setattr(service, "daily_review_groups", lambda session: [["event-equity"], ["savf63"]])
    monkeypatch.setattr(research_workbench, "portfolio_options", lambda: {"portfolios": []})
    monkeypatch.setattr(risk_officer, "read_snapshot", lambda session, **scope: {"instrument_ids": ["savf63"]})
    calls = []
    def analyze(run_id):
        with get_session_factory()() as session:
            run = session.get(ResearchEntry, run_id)
            calls.append("risk" if run.context_json.get("risk_run") else run.context_json["instrument_ids"][0])
            run.status = "completed"
            session.commit()
    monkeypatch.setattr(research_runner, "run_analysis", analyze)
    service.run_daily_reviews(Event())
    assert calls[0] == "savf63" and calls[-1] == "event-equity"
    assert set(calls[1:-1]) == {"risk"}
