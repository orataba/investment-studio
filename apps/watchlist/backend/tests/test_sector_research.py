import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import pytest
from sqlalchemy import select

from watchlist_app.services import sector_research as service
from watchlist_app.db.models import InstrumentDetail
from watchlist_app.db.models.workbench import ResearchEntry, RiskCase
from watchlist_app.db.session import get_session_factory


def seed_sector(client, monkeypatch):
    monkeypatch.setattr(service, "_research_market", lambda session, iid: "us")
    with get_session_factory()() as session:
        for iid in ("xlk", "xlf"):
            session.add(InstrumentDetail(instrument_id=iid, instrument_type="etf", detail_view_type="etf", instrument_name=iid.upper(), metadata_json={}))
        session.commit()


def result(iid="xlk", sources=None, **changes):
    events=[] if sources is None else [{"event_key":"new-policy", "action":"new", "direction":"opportunity", "title":"政策变化", "body":"新政策可能改善现金流，但市场预期仍需核实。", "next_watch":"观察公司原文披露。", "confidence":"reported", "information_type":"fact", "recording_type":"backfill", "published_at":None, "occurred_at":None, "source_ids":sources, **changes}]
    return json.dumps({"reviews":[{"instrument_id":iid,"summary":"仅保留需要关注的增量。","coverage":[],"events":events}]})


def add_sources(run, **overrides):
    source={"source_id":"web-one", "url":"https://example.com/news", "title":"Original", "text":"Original source text", "published_at":run.context_json["cutoff"], "time_status":"verified", **overrides}
    run.context_json={**run.context_json,"web_evidence":[{"operation":"search","sources":[]},{"operation":"fetch","sources":[source]}]}


def test_sectors_have_independent_runs_and_existing_batch_preserves_daily_dedup(client, monkeypatch):
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        first, created=service.begin_run(session,["xlk"])
        assert created
        assert service.latest_reviews(session)["xlk"]["status"]=="queued"
        other, created = service.begin_run(session,["xlf"])
        assert created and first.topic_id == "instrument-events:xlk" and other.topic_id == "instrument-events:xlf"
        other.status = "completed"
        first.status="completed"
        session.commit()
        full, created=service.begin_run(session,["xlk","xlf"], scheduled=True)
        assert created and full.entry_id!=first.entry_id
        full.status="completed"
        session.commit()
        same, created=service.begin_run(session,["xlk","xlf"], scheduled=True)
        assert not created and same.entry_id==full.entry_id
        same, created=service.begin_run(session,["xlk"], scheduled=True)
        assert not created and same.entry_id in {full.entry_id, first.entry_id}


def test_batch_and_single_instrument_cannot_publish_overlapping_runs(client, monkeypatch):
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        single, _ = service.begin_run(session, ["xlk"])
        with pytest.raises(service.ReviewInProgress):
            service.begin_run(session, ["xlk", "xlf"])
        single.status = "completed"
        session.commit()
        batch, created = service.begin_run(session, ["xlk", "xlf"])
        assert created
        same, created = service.begin_run(session, ["xlf"], scheduled=True)
        assert not created and same.entry_id == batch.entry_id
        same, created = service.begin_run(session, ["xlk"])
        assert not created and same.entry_id == batch.entry_id


def test_old_and_unknown_originals_keep_full_progress_without_duplicate_or_automatic_resolution(client, monkeypatch):
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run,_=service.begin_run(session,["xlk"])
        assert "window_start" not in run.context_json
        old_publication = "2020-06-01T00:00:00+00:00"
        add_sources(run,published_at=old_publication)
        run.context_json={**run.context_json,"sector_company_data":{"xlk":{"MSFT":{"name":"Microsoft"}}}}
        cited = ["web-one",f"fmp:{run.entry_id}:xlk:MSFT"]
        service.apply_result(session,run,result(sources=cited, published_at=old_publication, occurred_at="2020-05-30"))
        session.commit()
        case=session.scalar(select(RiskCase).where(RiskCase.signal=="sector:new-policy"))
        assert case.evidence_json["direction"]=="opportunity"
        assert len(case.evidence_json["sources"])==2
        first = case.history_json[0]["snapshot"]
        assert first["published_at"] == old_publication and first["occurred_at"] == "2020-05-30"
        assert first["recording_type"] == "backfill" and first["discovered_at"] != old_publication
        assert first["sources"][0]["text"] == "Original source text"
        add_sources(run,source_id="refetched-id",published_at=old_publication)
        service.apply_result(session,run,result(sources=["refetched-id"], action="updated", recording_type="update",
            published_at=old_publication, occurred_at="2020-05-30"))
        assert len(case.history_json) == 1
        add_sources(run,source_id="follow-up",published_at=None,time_status="unknown")
        service.apply_result(session,run,result(sources=["follow-up"],action="updated",recording_type="update",
            body="新的公开传闻尚待核实，可能改变政策兑现路径。",direction="uncertain",information_type="rumor",confidence="unverified"))
        assert len(case.history_json) == 2
        assert case.history_json[0]["snapshot"] == first
        assert case.history_json[1]["snapshot"]["published_at"] is None
        assert case.history_json[1]["snapshot"]["information_type"] == "rumor"
        add_sources(run,source_id="old-refetched-id",published_at=old_publication)
        service.apply_result(session,run,result(sources=["old-refetched-id"],action="updated",recording_type="update",
            published_at=old_publication,occurred_at="2020-05-30"))
        assert len(case.history_json) == 3 and case.evidence_json["information_type"] == "fact"
        assert case.history_json[1]["snapshot"]["information_type"] == "rumor"
        service.apply_result(session,run,result())
        assert case.evidence_json["follow_up"] == "watch" and not case.trigger_active and len(case.history_json) == 3
        from watchlist_app.services.risk_workbench import refresh_risk_cases
        refresh_risk_cases(session,["xlk"])
        assert case.evidence_json["follow_up"] == "watch" and not case.trigger_active
        add_sources(run,source_id="follow-up",published_at=None,time_status="unknown")
        service.apply_result(session,run,result(sources=["follow-up"],action="resolved",recording_type="update",
            body="原文澄清此前传闻，相关不确定性已经解除。"))
        service.apply_result(session,run,result())
        assert not case.trigger_active and case.status == "resolved"
        session.commit()
    response = client.get("/api/sector-research?instrument_id=xlk").json()
    stored = response["events"][0]
    assert len(stored["history"]) == 4 and stored["history"][0]["snapshot"]["occurred_at"] == "2020-05-30"
    assert not stored["trigger_active"] and stored["status"] == "resolved"


def test_event_times_preserve_precision_and_cannot_invent_publication(client, monkeypatch):
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run,_=service.begin_run(session,["xlk"])
        add_sources(run,published_at="2026-08-01")
        with pytest.raises(ValueError, match="发布时间"):
            service.apply_result(session,run,result(sources=["web-one"],published_at="2026-09-06"))
        with pytest.raises(ValueError, match="时区"):
            service.apply_result(session,run,result(sources=["web-one"],occurred_at="2026-08-01T12:00:00"))
        add_sources(run,published_at=(datetime.fromisoformat(run.context_json["cutoff"])+timedelta(days=1)).isoformat())
        with pytest.raises(ValueError, match="未来首发"):
            service.apply_result(session,run,result(sources=["web-one"]))
        add_sources(run,published_at="2026-08-01")
        service.apply_result(session,run,result(sources=["web-one"],published_at="2026-08-01"))
        session.flush()
        case=session.scalar(select(RiskCase).where(RiskCase.signal=="sector:new-policy"))
        assert case.evidence_json["published_at"] == "2026-08-01" and case.evidence_json["occurred_at"] is None


def test_legacy_events_remain_readable_without_fabricated_history_snapshots(client, monkeypatch):
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        session.add(RiskCase(case_id="legacy",instrument_id="xlk",signal="sector:old-report",title="旧跟进",body="原有正文",
            status="resolved",trigger_active=False,evidence_json={"direction":"risk"},
            history_json=[{"at":"2026-08-01T12:00:00+00:00","action":"new","detail":"原有历史记录"}]))
        session.commit()
    monkeypatch.setattr(service,"sector_snapshot",lambda *a: pytest.fail("Viewing retained events must not read FMP"))
    stored = client.get("/api/sector-research?instrument_id=xlk").json()["events"][0]
    assert stored["information_type"] is None and stored["published_at"] is None and stored["occurred_at"] is None
    assert stored["history"] == [{"at":"2026-08-01T12:00:00+00:00","action":"new","detail":"原有历史记录","snapshot":None}]
    assert not stored["trigger_active"]


def test_brief_follow_up_and_risk_trigger_are_independent_with_stable_versions(client, monkeypatch):
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, ["xlk"])
        add_sources(run)
        service.apply_result(session, run, result(sources=["web-one"], analysis_depth="brief", follow_up="none", next_watch=""))
        session.flush()
        case = session.scalar(select(RiskCase).where(RiskCase.signal == "sector:new-policy"))
        brief = service.event_record(case)
        assert brief["follow_up"] == "none" and brief["analysis_depth"] == "brief"
        assert brief["status"] == "recorded" and not brief["trigger_active"]
        assert brief["event_version_id"] == f"{case.case_id}:1"
        assert brief["recorded_at"] == brief["history"][0]["at"]
        assert brief["event_version_id"] == brief["history"][0]["snapshot"]["event_version_id"]
        service.apply_result(session, run, result(sources=["web-one"], action="updated", follow_up="watch",
            next_watch="等待后续正式指引", analysis_depth="analysis"))
        opportunity = service.event_record(case)
        assert opportunity["follow_up"] == "watch" and not opportunity["trigger_active"]
        assert opportunity["event_version_id"] == f"{case.case_id}:2"
        service.apply_result(session, run, result(sources=["web-one"], action="updated", direction="risk"))
        risk = service.event_record(case)
        assert risk["follow_up"] == "watch" and risk["trigger_active"]
        assert risk["event_version_id"] == f"{case.case_id}:3"
        assert risk["history"][0]["snapshot"] == brief["history"][0]["snapshot"]


def test_research_publication_creates_multiple_themes_and_preserves_sparse_links(client, monkeypatch):
    from watchlist_app.services.research_dossier import read_dossier
    from watchlist_app.services.research_themes import theme_index
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, ["xlk"])
        add_sources(run)
        payload = json.loads(result(sources=["web-one"], follow_up="none", next_watch="", theme_ids=["ai-demand", "margins"]))
        payload["reviews"][0].update(themes=[
            {"theme_key": "ai-demand", "title": "AI需求与投入回报", "question": "新增需求能否支撑持续投入？", "source_ids": ["web-one"]},
            {"theme_key": "margins", "title": "利润分配变化", "question": "成本与定价权如何变化？"},
        ], research={"questions": [{"key": "verify-demand", "theme_id": "ai-demand", "event_key": "new-policy",
            "question": "投入能否转化为收入？", "assessment": "仍需后续数据", "next_check": "下一次指引", "source_ids": ["web-one"]}]})
        draft = service.ReviewResult.model_validate(payload)
        service.validate_result(session, run, draft)
        assert theme_index(session, "xlk") == [], "Accepting a draft must not publish its themes"
        service.apply_result(session, run, json.dumps(payload))
        session.commit()
        themes = {theme["theme_key"]: theme for theme in theme_index(session, "xlk")}
        assert set(themes) == {"ai-demand", "margins"}
        assert all(theme["origin"] == theme["managed_by"] == "researcher" and theme["author_user_id"] is None for theme in themes.values())
        case = session.scalar(select(RiskCase).where(RiskCase.signal == "sector:new-policy"))
        ids = [themes[key]["theme_id"] for key in ("ai-demand", "margins")]
        assert case.evidence_json["theme_ids"] == ids
        dossier = read_dossier(session, "xlk")
        assert dossier["notebook"]["questions"][0]["theme_id"] == ids[0]
        assert run.context_json["reviews"]["xlk"]["themes"][0]["source_ids"] == ["web-one"]
        next_run, _ = service.begin_run(session, ["xlk"])
        next_run.context_json = {**next_run.context_json, "research_dossiers": [read_dossier(session, "xlk")],
                                 "prior_events": [service.event_record(case)]}
        add_sources(next_run)
        sparse = service.ReviewResult.model_validate_json(result(sources=["web-one"], action="updated", body="新原文补充了影响范围。"))
        submitted = service.draft_payload(sparse)
        assert "theme_ids" not in submitted["reviews"][0]["events"][0]
        assert "follow_up" not in submitted["reviews"][0]["events"][0]
        service.apply_result(session, next_run, json.dumps(submitted))
        assert case.evidence_json["theme_ids"] == ids and case.evidence_json["follow_up"] == "none"
        assert len(theme_index(session, "xlk")) == 2


def test_invalid_theme_reference_does_not_publish_new_theme_or_event(client, monkeypatch):
    from watchlist_app.services.research_themes import theme_index
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, ["xlk"])
        add_sources(run)
        payload = json.loads(result(sources=["web-one"], theme_ids=["foreign-theme"]))
        payload["reviews"][0]["themes"] = [{"theme_key": "valid-new", "title": "需求持续性", "question": "需求是否持续？"}]
        with pytest.raises(ValueError, match="未读取"):
            service.apply_result(session, run, json.dumps(payload))
        assert theme_index(session, "xlk") == []
        assert session.scalar(select(RiskCase).where(RiskCase.signal == "sector:new-policy")) is None


def test_new_reflection_is_saved_without_manufacturing_an_event(client, monkeypatch):
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, ["xlk"])
        payload = {"reviews": [{"instrument_id": "xlk", "reflection": {
            "status": "insufficient_evidence", "summary": "原判断尚无可观察结果，暂不提炼经验。", "reviewed_update_ids": []}}]}
        service.apply_result(session, run, json.dumps(payload))
        review = run.context_json["reviews"]["xlk"]
        assert review["change_kind"] == "none" and review["summary"] == ""
        assert review["reflection"]["status"] == "insufficient_evidence"
        assert "原判断尚无可观察结果，暂不提炼经验。" in review["coverage"]
        assert service.events_for_instruments(session, ["xlk"]) == []


def test_review_can_bind_an_actual_prior_event_judgment_without_fabricating_a_forecast(client, monkeypatch):
    from watchlist_app.services.research_dossier import read_dossier
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        original, _ = service.begin_run(session, ["xlk"])
        add_sources(original)
        service.apply_result(session, original, result(sources=["web-one"]))
        session.commit()
        event = service.events_for_instruments(session, ["xlk"])[0]
        update_id = f"event:{event['event_version_id']}"
        prior_cutoff = original.context_json["cutoff"]
        with pytest.raises(ValueError, match="事前判断"):
            service._validate_update_reference(session, original, "xlk", update_id)
        next_run, _ = service.begin_run(session, ["xlk"])
        next_run.context_json = {**next_run.context_json, "research_dossiers": [read_dossier(session, "xlk")], "prior_events": [event]}
        add_sources(next_run)
        valid_cutoff = next_run.context_json["cutoff"]
        next_run.context_json = {**next_run.context_json, "cutoff": prior_cutoff}
        with pytest.raises(ValueError, match="事前判断"):
            service._validate_update_reference(session, next_run, "xlk", update_id)
        next_run.context_json = {**next_run.context_json, "cutoff": valid_cutoff}
        next_run.context_json = {**next_run.context_json, "input_snapshot_cutoff": prior_cutoff}
        with pytest.raises(ValueError, match="事前判断"):
            service._validate_update_reference(session, next_run, "xlk", update_id)
        next_run.context_json = {key: value for key, value in next_run.context_json.items() if key != "input_snapshot_cutoff"}
        payload = {"reviews": [{"instrument_id": "xlk", "change_kind": "knowledge", "research": {
            "forecast_reviews": [{"key": "policy-outcome", "event_key": "new-policy", "related_research_update_id": update_id,
                "outcome": "最新披露尚不能支持最初的收入改善假设。", "mechanism_assessment": "需区分政策生效与收入兑现。", "source_ids": ["web-one"]}]},
            "reflection": {"status": "reviewed", "reviewed_update_ids": [update_id]}}]}
        service.apply_result(session, next_run, json.dumps(payload))
        session.commit()
        saved = read_dossier(session, "xlk")["notebook"]["forecast_reviews"][0]
        assert saved["related_research_update_id"] == update_id and saved["forecast_key"] is None
        assert len(service.events_for_instruments(session, ["xlk"])[0]["history"]) == 1
        assert next_run.context_json["reviews"]["xlk"]["change_kind"] == "knowledge"


def test_stale_event_version_cannot_overwrite_a_newer_published_assessment(client, monkeypatch):
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        original, _ = service.begin_run(session, ["xlk"])
        add_sources(original)
        service.apply_result(session, original, result(sources=["web-one"]))
        session.commit()
        case = session.scalar(select(RiskCase).where(RiskCase.signal == "sector:new-policy"))
        before = service.event_record(case)
        next_run, _ = service.begin_run(session, ["xlk"])
        next_run.context_json = {**next_run.context_json, "prior_events": [before]}
        add_sources(next_run)
        case.body = "另一轮研究已经修正判断。"
        case.evidence_json = {**case.evidence_json, "event_version_id": f"{case.case_id}:2"}
        case.history_json = [*case.history_json, {"at": datetime.now(UTC).isoformat(), "action": "updated",
            "snapshot": {**case.evidence_json, "body": case.body}}]
        session.flush()
        with pytest.raises(service.ResearchVersionConflict, match="事件"):
            service.apply_result(session, next_run, result(sources=["web-one"], action="updated", body="过期输入下的新判断"))
        assert case.body == "另一轮研究已经修正判断。" and len(case.history_json) == 2


def test_brief_updates_do_not_replace_the_instrument_investment_conclusion(client, monkeypatch):
    from watchlist_app.services.research_dossier import read_dossier
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        first, _ = service.begin_run(session, ["xlk"])
        service.apply_result(session, first, json.dumps({"reviews": [{"instrument_id": "xlk", "change_kind": "investment", "summary": "原有全标的投资判断",
            "research": {"investment_view": {"direction": "原有全标的投资判断"}}}]}))
        session.commit()
        second, _ = service.begin_run(session, ["xlk"])
        second.context_json = {**second.context_json, "research_dossiers": [read_dossier(session, "xlk")]}
        add_sources(second)
        service.apply_result(session, second, result(sources=["web-one"], analysis_depth="brief", follow_up="none", next_watch=""))
        review = second.context_json["reviews"]["xlk"]
        assert review["summary"] == "" and review["change_kind"] == "knowledge"
        session.commit()
        current = service.review_states(session)["last_completed"]["xlk"]
        assert current["summary"] == "原有全标的投资判断"
        assert current["view_run_id"] == first.entry_id


@pytest.mark.parametrize("use_alias", [True, False])
def test_analyst_can_close_own_active_theme_with_final_question_and_event_in_one_publication(client, monkeypatch, use_alias):
    from watchlist_app.services.research_dossier import read_dossier
    from watchlist_app.services.research_themes import theme_index
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        first, _ = service.begin_run(session, ["xlk"])
        add_sources(first)
        initial = json.loads(result(sources=["web-one"], theme_ids=["policy-effect"]))
        initial["reviews"][0]["themes"] = [{"theme_key": "policy-effect", "title": "政策兑现", "question": "新政策能否改善收入？"}]
        service.apply_result(session, first, json.dumps(initial))
        session.commit()
        theme = theme_index(session, "xlk")[0]
        ref = theme["theme_key"] if use_alias else theme["theme_id"]
        second, _ = service.begin_run(session, ["xlk"])
        second.context_json = {**second.context_json, "research_dossiers": [read_dossier(session, "xlk")],
                               "prior_events": service.events_for_instruments(session, ["xlk"])}
        add_sources(second)
        final = json.loads(result(sources=["web-one"], action="resolved", theme_ids=[ref], body="后续披露完成了原问题的核实。"))
        final["reviews"][0].update(themes=[{"theme_key": "policy-effect", "theme_id": theme["theme_id"],
            "status": "closed", "close_reason": "所需验证已完成，后续纳入一般背景研究"}], research={"questions": [{
                "key": "policy-outcome", "theme_id": ref, "question": theme["question"], "assessment": "现有证据支持改善但不证明唯一原因",
                "status": "supported", "next_check": "", "source_ids": ["web-one"]}]})
        service.apply_result(session, second, json.dumps(final))
        session.commit()
        assert theme_index(session, "xlk")[0]["status"] == "closed"
        assert service.events_for_instruments(session, ["xlk"])[0]["follow_up"] == "resolved"
        assert read_dossier(session, "xlk")["notebook"]["questions"][0]["theme_id"] == theme["theme_id"]


@pytest.mark.parametrize("status", ["paused", "closed"])
def test_automatic_publication_cannot_reopen_or_advance_a_previously_inactive_theme(client, monkeypatch, status):
    from watchlist_app.services.research_dossier import read_dossier
    from watchlist_app.services.research_themes import AnalystThemeUpdate, save_analyst_theme
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        theme = save_analyst_theme(session, "xlk", AnalystThemeUpdate(theme_key="inactive-policy", title="旧政策研究",
            question="是否值得继续研究？", status=status, close_reason="原研究已结束" if status == "closed" else ""))
        session.commit()
        run, _ = service.begin_run(session, ["xlk"])
        run.context_json = {**run.context_json, "research_dossiers": [read_dossier(session, "xlk")]}
        with pytest.raises(ValueError, match="不能由自动研究"):
            service.apply_result(session, run, json.dumps({"reviews": [{"instrument_id": "xlk", "themes": [
                {"theme_key": theme["theme_key"], "status": "active"}]}]}))
        with pytest.raises(ValueError, match="暂停或结束"):
            service.apply_result(session, run, json.dumps({"reviews": [{"instrument_id": "xlk", "research": {"questions": [{
                "key": "unrequested-followup", "theme_id": theme["theme_id"], "question": theme["question"],
                "assessment": "未经恢复便继续更新", "next_check": "后续披露"}]}}]}))


def test_shared_event_can_keep_inactive_theme_reference_without_reopening_or_new_assignment(client, monkeypatch):
    from watchlist_app.services.research_dossier import read_dossier
    from watchlist_app.services.research_themes import ThemeInput, ThemePatch, save_theme, theme_index
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        themes = [save_theme(session, "xlk", ThemeInput(title=title, question=title)) for title in ("需求变化", "利润变化")]
        session.commit()
        first, _ = service.begin_run(session, ["xlk"])
        first.context_json = {**first.context_json, "research_dossiers": [read_dossier(session, "xlk")]}
        add_sources(first)
        service.apply_result(session, first, result(sources=["web-one"], theme_ids=[theme["theme_id"] for theme in themes]))
        save_theme(session, "xlk", ThemePatch(status="paused"), theme_id=themes[0]["theme_id"])
        session.commit()
        second, _ = service.begin_run(session, ["xlk"])
        second.context_json = {**second.context_json, "research_dossiers": [read_dossier(session, "xlk")],
                               "prior_events": service.events_for_instruments(session, ["xlk"])}
        add_sources(second)
        service.apply_result(session, second, result(sources=["web-one"], action="updated", body="同一事件有了新的公开披露。"))
        session.commit()
        current = service.events_for_instruments(session, ["xlk"])[0]
        assert current["theme_ids"] == [theme["theme_id"] for theme in themes]
        assert current["body"] == "同一事件有了新的公开披露。"
        assert next(theme for theme in theme_index(session, "xlk") if theme["theme_id"] == themes[0]["theme_id"])["status"] == "paused"
        with pytest.raises(ValueError, match="暂停或结束"):
            service.validate_result(session, second, service.ReviewResult.model_validate_json(
                result(sources=["web-one"], event_key="new-assignment", theme_ids=[themes[0]["theme_id"]])))


def test_opening_compiler_byline_does_not_qualify_as_an_original():
    byline = "Sun, September 6, 2026 at 12:29 AM GMT+0·Geopolitics·Compiled by Adalytica Engine v1.12"
    source = {"time_status": "verified", "published_at": "2026-09-06T00:29:19+00:00",
              "text": "Geopolitics\nIraq fuel shortages as oil prices rise\n" + byline +
                      "\nThe USO oil ETF has its RSI above 70."}
    cutoff = datetime(2026, 9, 6, 1, tzinfo=UTC)
    assert not service.usable_original(source, cutoff)
    assert service.usable_original({**source, "text": source["text"].replace(byline, "")}, cutoff)


def test_failed_supplementary_search_keeps_explicit_coverage_gap(client, monkeypatch):
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run,_=service.begin_run(session,["xlk"])
        run.context_json={**run.context_json,"web_evidence":[{"operation":"error","coverage":["search timeout"]}]}
        service.apply_result(session,run,result())
        assert run.status=="completed"
        assert run.context_json["reviews"]["xlk"]["status"]=="limited"
        assert "search timeout" in run.context_json["reviews"]["xlk"]["coverage"]


def test_unchecked_news_cannot_be_reported_as_complete_coverage(client, monkeypatch):
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, ["xlk"])
        service.apply_result(session, run, result())
        review = run.context_json["reviews"]["xlk"]
        assert review["status"] == "limited"
        assert any("未检索" in gap for gap in review["coverage"])


def test_prepare_context_retains_scoped_sector_inputs(client, monkeypatch):
    seed_sector(client, monkeypatch)
    monkeypatch.setattr(service,"sector_snapshot",lambda iid,session,**kwargs: ({},{"instrument_id":iid,"ticker":iid.upper()},{}))
    with get_session_factory()() as session:
        run,_=service.begin_run(session,["xlk"])
        rid=run.entry_id
    service.prepare_run(rid)
    context=client.get(f"/api/research/runs/{rid}/context").json()
    assert {"xlk", "xlf"}.issubset({item["instrument_id"] for item in context["catalogue"]})
    assert context["instrument_ids"] == ["xlk"]
    assert "sector_company_data" not in context
    assert context["sector_estimate_evidence"] == []
    assert context["instrument_inputs"][0]["analyst_estimate_history"]["status"] == "no_snapshot"


def test_missing_owned_sector_snapshot_remains_a_gap_without_estimate_baseline(client, monkeypatch):
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, ["xlk"])
        rid = run.entry_id
    service.prepare_run(rid)
    context = client.get(f"/api/research/runs/{rid}/context").json()
    assert context["sector_inputs"] == [] and context["sector_estimate_evidence"] == []
    assert any("本项目留存" in gap for gap in context["data_gaps"])


def test_only_computed_comparable_estimate_changes_can_substantiate_an_event(client, monkeypatch):
    from watchlist_app.services.sector_estimates import compare_estimate_snapshots
    seed_sector(client, monkeypatch)
    old_row = {"target_period_end": "2027-12-31", "revenue_avg": 100, "currency": "USD",
               "collected_at": "2026-09-04T00:00:00+00:00", "num_analysts_revenue": 5,
               "source_dataset": "fmp_analyst_estimates_bulk", "raw_sha256": "older-raw"}
    old_company = {"AAA": {"name": "Alpha", "weight_percent": 60, "annual_estimates": [old_row], "quarterly_estimates": []}}
    def observation(identifier, row):
        return {"observation_id": identifier, "collected_at": row["collected_at"],
                "companies": {"AAA": {"name": "Alpha", "weight_percent": 60,
                    "annual_estimates": [row], "quarterly_estimates": []}}}

    previous = observation(1, old_row)
    with get_session_factory()() as session:
        run,_=service.begin_run(session,["xlk"])
        run.context_json={**run.context_json,"web_evidence":[{"operation":"search","sources":[]}]}
        for changes, baseline in (({}, True), ({"currency": None}, False), ({"target_period_end": "2028-12-31"}, False), ({}, False)):
            row = {**old_row, "revenue_avg": 110, "collected_at": "2026-09-05T00:00:00+00:00", "raw_sha256": "newer-raw", **changes}
            company = {"AAA": {**old_company["AAA"], "annual_estimates": [row]}}
            run.context_json = {**run.context_json,"sector_company_data":{"xlk":company}}
            evidence = compare_estimate_snapshots("xlk", observation(2, row), None if baseline else previous)
            run.context_json = {**run.context_json,"sector_estimate_evidence":[evidence]}
            reply = result(sources=[evidence["source_id"]],body="AAA同财年营收共识由100升至110，需判断对XLK的影响。")
            if baseline or changes:
                assert evidence["changes"] == []
                with pytest.raises(ValueError,match="可比较预期变动"):
                    service.apply_result(session,run,reply)
                assert not session.new
            else:
                with pytest.raises(ValueError,match="可比较预期变动"):
                    service.apply_result(session,run,result(sources=[f"fmp:{run.entry_id}:xlk:AAA"]))
                cross_etf = json.loads(reply)
                cross_etf["reviews"][0]["instrument_id"] = "xlf"
                cross_etf["reviews"].append({"instrument_id":"xlk","summary":"无新增","events":[]})
                run.context_json = {**run.context_json,"instrument_ids":["xlk","xlf"]}
                with pytest.raises(ValueError,match="可比较预期变动"):
                    service.apply_result(session,run,json.dumps(cross_etf))
                assert not session.new
                run.context_json = {**run.context_json,"instrument_ids":["xlk"]}
                with pytest.raises(ValueError,match="发生时间必须留空"):
                    service.apply_result(session,run,result(sources=[evidence["source_id"]],occurred_at=row["collected_at"]))
                service.apply_result(session,run,reply)
        session.flush()
        case=session.scalar(select(RiskCase).where(RiskCase.signal=="sector:new-policy"))
        source=case.history_json[0]["snapshot"]["sources"][0]
        assert source["source_id"] == "estimates:2:xlk"
        assert source["changes"][0]["delta"] == 10
        assert source["changes"][0]["current_collected_at"] == "2026-09-05T00:00:00+00:00"
        assert source["previous_snapshot"]["observation_id"] == 1
        assert "url" not in source and "published_at" not in source
        assert case.evidence_json["published_at"] is None and case.evidence_json["occurred_at"] is None
        view=service.event_record(case)["history"][0]["snapshot"]["sources"][0]
        assert view["changes"] == source["changes"]


def test_shared_original_can_support_research_without_online_search(client, monkeypatch):
    from watchlist_app.services.market_evidence import text_store
    seed_sector(client, monkeypatch)
    original = text_store().capture_public_source({"source_id": "captured", "url": "https://example.com/official",
        "title": "Official policy announcement", "text": "The official policy takes effect next quarter.",
        "published_at": "2026-09-01", "retrieved_at": "2026-09-05T08:00:00+00:00"})
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, ["xlk"])
        run_id = run.entry_id
    page = client.post(f"/api/research/runs/{run_id}/market-search", json={"instrument_id": "xlk", "query": "policy", "limit": 1}).json()
    assert page["total"] == 1
    assert page["rows"][0]["body_available"]
    assert "content_text" not in page["rows"][0] and "raw_path" not in page["rows"][0]
    with get_session_factory()() as session:
        assert not session.get(ResearchEntry, run_id).context_json.get("market_text_sources")
    source = client.get(f"/api/research/runs/{run_id}/market-source", params={"document_id": original["document_id"], "version_id": original["version_id"]}).json()
    assert source["published_at"] == "2026-09-01" and source["time_status"] == "date_only"
    assert source["text"] == "The official policy takes effect next quarter."
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        assert not run.context_json.get("web_evidence")
        assert run.context_json["market_coverage"] == page["coverage"]
        assert "text" not in run.context_json["market_text_sources"][0]
        service.apply_result(session, run, result(sources=[source["source_id"]], published_at="2026-09-01"))
        session.commit()
        assert run.status == "completed"
        case = session.scalar(select(RiskCase).where(RiskCase.instrument_id == "xlk"))
        assert case.evidence_json["sources"][0]["version_id"] == original["version_id"]
        assert "text" not in case.evidence_json["sources"][0]
    assert client.get(f"/api/research/runs/{run_id}/market-source", params={"document_id": original["document_id"]}).status_code == 409


def test_market_search_continuation_keeps_original_cutoff_after_new_capture(client, monkeypatch):
    from watchlist_app.services.market_evidence import text_store
    seed_sector(client, monkeypatch)
    original = text_store().capture_public_source({"source_id": "captured", "url": "https://example.com/continuation",
        "title": "Policy original", "text": "Policy conditions before revision", "published_at": "2026-09-01",
        "retrieved_at": "2026-09-05T08:00:00+00:00"})
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, ["xlk"])
        run_id = run.entry_id
    url = f"/api/research/runs/{run_id}/market-search"
    first = client.post(url, json={"query": "Policy"}).json()
    revised = text_store().capture_public_source({"source_id": "captured", "url": "https://example.com/continuation",
        "title": "Policy revised", "text": "Policy conditions after revision", "published_at": "2026-09-01",
        "retrieved_at": datetime.now(UTC).isoformat()})
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        run.context_json = {**run.context_json, "cutoff": datetime.now(UTC).isoformat()}
        session.commit()
    bound = client.post(url, json={"query": "Policy", "as_of": first["as_of"]}).json()
    assert bound["rows"][0]["version_id"] == original["version_id"]
    assert client.post(url, json={"query": "Policy"}).json()["rows"][0]["version_id"] == revised["version_id"]
    assert client.post(url, json={"as_of": "2099-01-01T00:00:00Z"}).status_code == 422
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        assert run.context_json["market_queries"][-2]["cutoff"] == first["as_of"]
        assert not run.context_json.get("market_text_sources")


def test_live_capture_advances_knowledge_cutoff_and_materializes_through_api(client, monkeypatch):
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, ["xlk"])
        run_id, starting_cutoff = run.entry_id, run.context_json["cutoff"]
    saved = client.post(f"/api/research/runs/{run_id}/sector-evidence", json={"operation": "fetch", "sources": [{
        "source_id": "live", "url": "https://example.com/new", "title": "Source with no publication date",
        "text": "Original evidence with an unknown publication date.", "published_at": None,
        "retrieved_at": datetime.now(UTC).isoformat()}]})
    assert saved.status_code == 200
    source = saved.json()["sources"][0]
    assert source["published_at"] is None
    context = client.get(f"/api/research/runs/{run_id}/context?originals=true").json()
    assert context["input_snapshot_cutoff"] == starting_cutoff
    assert context["cutoff"] >= source["received_at"]
    assert context["web_evidence"][0]["sources"][0]["text"] == source["text"]
    assert client.post(f"/api/research/runs/{run_id}/market-search", json={"published_after": "2026-09-01T00:00:00"}).status_code == 422
