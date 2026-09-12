from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json

import pytest
from sqlalchemy import select

from watchlist_app.db.models import InstrumentDetail
from watchlist_app.db.models.workbench import RiskCase
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import sector_research as service
from watchlist_app.services.research_activity import research_activity, resolve_event_reference


@pytest.fixture
def activity_client(client, monkeypatch):
    monkeypatch.setattr(service, "_research_market", lambda session, iid: "us")
    with get_session_factory()() as session:
        for iid in ("xlk", "xlf"):
            session.add(InstrumentDetail(instrument_id=iid, instrument_type="etf", detail_view_type="etf",
                instrument_name=iid.upper(), metadata_json={}))
        session.commit()
    return client


def publish(session, *, events=None, research=None, themes=None):
    from watchlist_app.services.research_dossier import _notebooks
    from watchlist_app.services.research_themes import theme_index
    run, _ = service.begin_run(session, ["xlk"])
    previous, _ = _notebooks(session, "xlk", False)
    source = {"source_id": "original", "url": "https://example.org/filing", "title": "Company filing",
              "text": "A financing transaction and its original terms.", "published_at": "2025-01-01T00:00:00+00:00",
              "time_status": "verified"}
    run.context_json = {**run.context_json,
        "research_dossiers": [{"instrument_id": "xlk", "notebook": previous, "themes": theme_index(session, "xlk")}],
        "web_evidence": [{"operation": "search", "sources": []}, {"operation": "fetch", "sources": [source]}]}
    row = {"instrument_id": "xlk", "change_kind": "knowledge", "events": events or [],
           "reflection": {"status": "reviewed", "summary": "已检查相关判断", "reviewed_update_ids": []}}
    if research is not None:
        row["research"] = research
    if themes is not None:
        row["themes"] = themes
    service.apply_result(session, run, json.dumps({"reviews": [row]}))
    session.commit()
    return run


def event(**values):
    return {"event_key": "financing", "action": "new", "direction": "uncertain", "title": "融资安排",
        "body": "已披露融资条款，后续经营影响尚待验证。", "next_watch": "核实资金使用效果。",
        "confidence": "reported", "information_type": "fact", "recording_type": "backfill",
        "published_at": "2025-01-01T00:00:00+00:00", "occurred_at": "2024-12-31",
        "source_ids": ["original"], **values}


def test_event_has_one_identity_in_global_and_multiple_theme_threads(activity_client):
    client = activity_client
    themes = [client.post("/api/research/instruments/xlk/themes", json={"title": title, "question": title}).json()
              for title in ("资金约束能否缓解", "融资是否改变股东回报")]
    ids = [row["theme_id"] for row in themes]
    with get_session_factory()() as session:
        publish(session, events=[event(theme_ids=ids)])
    global_events = [row for row in client.get("/api/research/instruments/xlk/activity").json()["updates"] if row["kind"] == "event"]
    assert len(global_events) == 1 and global_events[0]["theme_ids"] == ids
    for theme in client.get("/api/research/instruments/xlk/themes").json()["themes"]:
        assert [row for row in theme["updates"] if row["kind"] == "event"] == global_events
    assert global_events[0]["recorded_at"] > "2025-01-01" and global_events[0]["occurred_at"] == "2024-12-31"


def test_brief_event_does_not_create_follow_up_and_quiet_run_adds_no_activity(activity_client):
    with get_session_factory()() as session:
        publish(session, events=[event(follow_up="none", analysis_depth="brief", next_watch="")])
        before = research_activity(session, "xlk")
        publish(session)
        assert research_activity(session, "xlk") == before
        row = before["updates"][0]
        assert row["follow_up"] == "none" and row["analysis_depth"] == "brief"
        assert not session.scalar(select(RiskCase)).trigger_active


def test_question_review_and_lesson_are_dated_linked_updates_without_repeating_snapshots(activity_client):
    client = activity_client
    theme = client.post("/api/research/instruments/xlk/themes", json={"title": "融资影响", "question": "是否改善经营能力"}).json()
    with get_session_factory()() as session:
        run = publish(session, research={"questions": [{"key": "capacity", "theme_id": theme["theme_id"],
            "question": "是否改善经营能力", "assessment": "资金缓解约束，实际效果仍需核实。", "next_check": "核对后续经营披露。",
            "source_ids": ["original"]}]})
        original = next(row for row in research_activity(session, "xlk")["updates"] if row["kind"] == "question")
        publish(session, research={"forecast_reviews": [{"key": "capacity-review", "related_research_update_id": original["update_id"],
            "outcome": "实际效果尚不能确定。", "mechanism_assessment": "融资条款只验证资金来源。", "source_ids": ["original"]}],
            "lessons": [{"key": "capacity-lesson", "related_research_update_id": original["update_id"],
                "lesson": "区分融资完成与经营改善。", "applicability": "类似融资案例", "source_ids": ["original"]}]})
        updates = research_activity(session, "xlk")["updates"]
        assert len([row for row in updates if row["kind"] == "question"]) == 1
        for row in updates:
            if row["kind"] in {"review", "lesson"}:
                assert row["related_update_id"] == original["update_id"]
                assert row["theme_ids"] == [theme["theme_id"]]
        assert original["run_id"] == run.entry_id


def test_pm_opinion_binds_exact_event_version_and_rejects_wrong_scope(activity_client):
    client = activity_client
    with get_session_factory()() as session:
        publish(session, events=[event()])
        original = deepcopy(research_activity(session, "xlk")["updates"][0])
        publish(session, events=[event(action="updated", body="新增披露改变了对资金用途的认识。")])
        ref = original["reference"]
        assert resolve_event_reference(session, "xlk", ref["event_case_id"], ref["event_version_id"])["body"] == original["body"]
        with pytest.raises(ValueError, match="当前标的"):
            resolve_event_reference(session, "xlf", ref["event_case_id"], ref["event_version_id"])
    context = {key: value for key, value in ref.items() if key != "instrument_id"}
    request = {"note": {"note_date": "2026-09-09", "title": "我对原融资安排的判断", "body": "仍需核实资金使用效果。",
                        "research_context": context}}
    response = client.post("/api/instruments/xlk/research/notes", json=request)
    assert response.status_code == 200, response.text
    note = response.json()["notes"][0]
    assert note["research_context"]["event_version_id"] == ref["event_version_id"]
    assert note["research_context"]["research_update_title"] == original["title"]
    assert client.post("/api/instruments/xlf/research/notes", json=request).status_code == 422


def test_unpublished_and_foreign_team_notebooks_never_enter_activity(activity_client):
    from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
    from studio_identity import Principal, principal_context
    with get_session_factory()() as session:
        run = publish(session, research={"questions": [{"key": "test", "question": "已发布问题", "assessment": "尚待研究", "next_check": "核对原文"}]})
        topic = session.get(ResearchTopic, run.topic_id)
        run.team_id = topic.team_id = "other-team"
        session.commit()
        with principal_context(Principal("reader", "Reader", "default")):
            assert not any(row["kind"] == "question" for row in research_activity(session, "xlk")["updates"])
        run.team_id = topic.team_id = "default"
        run.context_json = {**run.context_json, "reviews": {}, "submitted_draft": {"reviews": [{"research": {"questions": []}}]}}
        session.commit()
        assert research_activity(session, "xlk")["updates"] == []


def test_judgment_partial_revision_keeps_original_clock_and_quiet_run_is_not_a_new_judgment(activity_client):
    from watchlist_app.services.research_dossier import _notebooks
    with get_session_factory()() as session:
        first = publish(session, research={"investment_view": {
            "direction": "中期方向暂未改变", "horizon": "未来两个季度", "risk": "等待经营披露验证",
            "source_ids": ["original"]}})
        notebook, _ = _notebooks(session, "xlk", False)
        original_view = deepcopy(notebook["investment_view"])
        original = next(row for row in research_activity(session, "xlk")["updates"] if row["kind"] == "judgment")
        assert original["recorded_at"] == original_view["updated_at"]
        assert original["reference"]["investment_view_version_id"] == original_view["version_id"]
        assert original["update_id"] == f"research:{first.entry_id}:investment-view"
        publish(session, research={"investment_view": {"risk": "新披露增加了短期资金约束的不确定性"}})
        revisions = [row for row in research_activity(session, "xlk")["updates"] if row["kind"] == "judgment"]
        assert len(revisions) == 2 and revisions[0]["change"] == "updated"
        assert revisions[0]["body"] == original_view["direction"]
        assert {item["label"]: item["text"] for item in revisions[0]["details"]}["风险判断"] == "新披露增加了短期资金约束的不确定性"
        assert revisions[1]["recorded_at"] == original["recorded_at"] and revisions[1]["superseded"]
        before = research_activity(session, "xlk")
        publish(session, research={})
        assert research_activity(session, "xlk") == before


def test_schedule_status_changes_use_recording_clock_without_claiming_release_from_due_date(activity_client):
    schedule = {"key": "filing", "title": "预定经营披露", "scheduled_at": "2030-06-30",
        "relevance": "验证资金用途与收入变化", "next_check": "读取实际披露原文", "source_ids": ["original"]}
    with get_session_factory()() as session:
        first = publish(session, research={"catalysts": [schedule], "questions": [{
            "key": "capacity", "question": "融资是否缓解资金约束", "assessment": "尚未验证", "status": "open", "next_check": "等待披露"}]})
        initial = research_activity(session, "xlk")["updates"]
        scheduled = next(row for row in initial if row["kind"] == "schedule")
        assert scheduled["scheduled_at"] == "2030-06-30" and scheduled["status"] == "scheduled"
        assert scheduled["recorded_at"] == first.completed_at.replace(tzinfo=first.completed_at.tzinfo or UTC).isoformat()
        assert scheduled["recorded_at"] < scheduled["scheduled_at"]
        assert scheduled.get("occurred_at") is None
        publish(session, research={"catalysts": [{**schedule, "status": "cancelled", "outcome": "发行人取消了原定披露安排"}],
            "questions": [{"key": "capacity", "question": "融资是否缓解资金约束", "assessment": "新增材料支持资金约束有所缓解",
                           "status": "supported", "next_check": "继续观察经营结果"}]})
        rows = research_activity(session, "xlk")["updates"]
        schedules = [row for row in rows if row["kind"] == "schedule"]
        questions = [row for row in rows if row["kind"] == "question"]
        assert [row["status"] for row in schedules] == ["cancelled", "scheduled"]
        assert schedules[1]["superseded"] and schedules[0]["scheduled_at"] == "2030-06-30"
        assert {item["label"]: item["text"] for item in schedules[0]["details"]}["日程结果"] == "发行人取消了原定披露安排"
        assert [row["status"] for row in questions] == ["supported", "open"] and questions[1]["superseded"]
        assert schedules[0]["recorded_at"] > schedules[1]["recorded_at"]


def test_explicitly_cleared_judgment_does_not_remain_current_in_activity(activity_client):
    with get_session_factory()() as session:
        publish(session, research={"investment_view": {"direction": "原有判断", "source_ids": ["original"]}})
        original = next(row for row in research_activity(session, "xlk")["updates"] if row["kind"] == "judgment")
        publish(session, research={"investment_view": None})
        judgments = [row for row in research_activity(session, "xlk")["updates"] if row["kind"] == "judgment"]
        retained = next(row for row in judgments if row["update_id"] == original["update_id"])
        assert retained["body"] == original["body"] and retained["superseded"]
        assert not any(not row.get("superseded") and row.get("status") != "withdrawn" for row in judgments)
