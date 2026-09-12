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


def publish(session, *, events=None, research=None, themes=None, reflection=None):
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
           "reflection": reflection or {"status": "reviewed", "summary": "已检查相关判断", "reviewed_update_ids": []}}
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


def test_current_followups_show_unresolved_work_without_requiring_a_theme(activity_client):
    with get_session_factory()() as session:
        publish(session, events=[event(), event(event_key="brief", follow_up="none", next_watch="")],
            research={"questions": [{"key": "transmission", "question": "融资成本会否传导", "assessment": "尚无结论",
                "next_check": "核对经营披露"}]})
    data = activity_client.get("/api/research/instruments/xlk/activity").json()
    assert {row["kind"] for row in data["current_followups"]} == {"event", "question"}
    assert all(row["last_reviewed_at"] is None for row in data["current_followups"])
    assert len(data["updates"]) == 3


def test_current_followups_group_event_questions_and_hide_finished_work(activity_client):
    with get_session_factory()() as session:
        publish(session, events=[event()], research={"questions": [{"key": "terms", "event_key": "financing",
            "question": "资金如何使用", "assessment": "用途未明", "next_check": "取得说明"}]})
        current = research_activity(session, "xlk", include_followups=True)["current_followups"]
        assert len(current) == 1 and len(current[0]["related_updates"]) == 1
        assert current[0]["related_updates"][0]["kind"] == "question"
        publish(session, events=[event(action="resolved", follow_up="resolved")], research={"questions": [
            {"key": "terms", "event_key": "financing", "question": "资金如何使用", "assessment": "用途已核实",
             "status": "supported", "next_check": "", "tracking_status": "closed", "tracking_reason": "用途已核实，不再影响投资判断"}]})
        assert research_activity(session, "xlk", include_followups=True)["current_followups"] == []
        assert len(research_activity(session, "xlk")["updates"]) == 4


def test_supported_questions_keep_tracking_and_explicit_lifecycle_preserves_history(activity_client):
    from watchlist_app.services.research_dossier import read_dossier
    with get_session_factory()() as session:
        question = {"key": "capacity", "question": "经营改善是否持续", "assessment": "当期得到支持",
                    "status": "supported", "next_check": "下次披露核对持续性"}
        publish(session, research={"questions": [question]})
        original = research_activity(session, "xlk")["updates"][0]
        assert read_dossier(session, "xlk")["review_agenda"]["tracked_questions"][0]["update_id"] == original["update_id"]
        assert research_activity(session, "xlk", include_followups=True)["current_followups"][0]["followup_id"] == original["update_id"]
        publish(session, research={"questions": [{**question, "tracking_status": "paused", "tracking_reason": "等待产品重新披露"}]})
        publish(session, research={"questions": [{**question, "status": "refuted", "assessment": "对原披露的解释需要纠正"}]})
        assert not read_dossier(session, "xlk")["review_agenda"]["tracked_questions"]
        assert not research_activity(session, "xlk", include_followups=True)["current_followups"]
        history = research_activity(session, "xlk")["updates"]
        assert history[0]["tracking_status"] == "paused" and history[0]["status"] == "refuted"
        assert next(row for row in history if row["update_id"] == original["update_id"])["body"] == original["body"]
        publish(session, research={"questions": [{**question, "tracking_status": "active"}]})
        assert len(read_dossier(session, "xlk")["review_agenda"]["tracked_questions"]) == 1


def test_legacy_supported_question_stays_active_without_rewriting_its_history(activity_client):
    from watchlist_app.services.research_dossier import read_dossier
    with get_session_factory()() as session:
        run = publish(session, research={"questions": [{"key": "margin", "question": "利润率能否持续", "assessment": "初期得到支持", "status": "supported", "next_check": "核实后续结果"}]})
        legacy = deepcopy(run.context_json)
        legacy_question = legacy["reviews"]["xlk"]["research"]["questions"][0]
        legacy_question.pop("tracking_status")
        legacy_question.pop("tracking_reason")
        run.context_json = legacy
        session.commit()
        before = research_activity(session, "xlk", include_followups=True)
        assert len(before["current_followups"]) == 1
        assert len(read_dossier(session, "xlk")["review_agenda"]["tracked_questions"]) == 1
        publish(session)
        assert research_activity(session, "xlk", include_followups=True) == before
        assert "tracking_status" not in run.context_json["reviews"]["xlk"]["research"]["questions"][0]


def test_theme_keeps_each_active_question_and_its_own_exact_review_clock(activity_client):
    from watchlist_app.services.research_themes import themes_view
    theme = activity_client.post("/api/research/instruments/xlk/themes", json={"title": "融资传导", "question": "是否改善经营"}).json()
    with get_session_factory()() as session:
        publish(session, research={"questions": [{"key": key, "theme_id": theme["theme_id"], "question": key,
            "assessment": f"{key}判断", "status": "supported", "next_check": f"{key}下一观察"} for key in ("cost", "capacity")]})
        questions = themes_view(session, "xlk")["themes"][0]["current_questions"]
        assert {row["title"] for row in questions} == {"cost", "capacity"}
        run = publish(session, reflection={"status": "reviewed", "summary": "只核实融资成本", "reviewed_update_ids": [questions[0]["update_id"]]})
        after = {row["update_id"]: row for row in themes_view(session, "xlk")["themes"][0]["current_questions"]}
        assert after[questions[0]["update_id"]]["last_reviewed_at"] == run.completed_at.isoformat()
        assert after[questions[1]["update_id"]]["last_reviewed_at"] is None


def test_agenda_includes_current_judgment_and_schedule_without_implying_release(activity_client):
    from watchlist_app.services.research_dossier import read_dossier
    with get_session_factory()() as session:
        publish(session, research={"investment_view": {"direction": "等待经营兑现", "horizon": "两个季度", "risk": "融资成本上升",
                "invalidation": "现金流恶化", "next_check": "下季披露", "assumptions": ["资金投入产生回报"]},
            "catalysts": [{"key": "filing", "title": "预定报告", "scheduled_at": "2026-01-01",
                "relevance": "核对资金用途", "next_check": "取得实际披露", "source_ids": ["original"]}]})
        agenda = read_dossier(session, "xlk")["review_agenda"]
        judgment, schedule = agenda["current_judgment"][0], agenda["scheduled_catalysts"][0]
        assert judgment["next_check"] == "下季披露" and judgment["invalidation"] == "现金流恶化"
        assert judgment["reference"]["investment_view_version_id"]
        assert schedule["scheduled_at"] == "2026-01-01" and schedule["next_check"] == "取得实际披露"
        reviewed = publish(session, reflection={"status": "insufficient_evidence", "summary": "尚未取得实际披露",
            "reviewed_update_ids": [judgment["update_id"], schedule["update_id"]]})
        assert reviewed.status == "completed"


def test_withdrawn_lesson_leaves_current_agenda_but_keeps_original_version_and_sources(activity_client):
    from watchlist_app.services.research_dossier import read_dossier, read_dossier_version
    with get_session_factory()() as session:
        lesson = {"key": "financing", "lesson": "融资成本下降可以改善现金流", "applicability": "资金用途不变", "source_ids": ["original"]}
        publish(session, research={"lessons": [lesson]})
        original = read_dossier(session, "xlk")["notebook"]["lessons"][0]
        assert len(read_dossier(session, "xlk")["review_agenda"]["existing_lessons"]) == 1
        publish(session, research={"lessons": [{**lesson, "status": "withdrawn", "withdrawal_reason": "新增条件表明结论不能按原范围使用"}]})
        publish(session, research={"lessons": [{**lesson, "limitations": "资本开支变化也会影响现金流"}]})
        dossier = read_dossier(session, "xlk")
        assert dossier["review_agenda"]["existing_lessons"] == []
        assert dossier["notebook"]["lessons"][0]["status"] == "withdrawn"
        archived = read_dossier_version(session, "xlk", original["version_id"])
        assert archived["value"]["status"] == "active" and archived["value"]["lesson"] == lesson["lesson"]
        assert archived["value"]["updated_at"] == original["updated_at"]
        assert [source["source_id"] for source in archived["sources"]] == ["original"]


def test_exact_receipt_advances_check_clock_without_inventing_progress(activity_client):
    with get_session_factory()() as session:
        publish(session, events=[event()])
        before = research_activity(session, "xlk", include_followups=True)["current_followups"][0]
        run = publish(session, reflection={"status": "insufficient_evidence", "summary": "原文尚未更新",
            "reviewed_update_ids": [before["followup_id"]]})
        after = research_activity(session, "xlk", include_followups=True)["current_followups"][0]
        assert after["last_changed_at"] == before["last_changed_at"]
        assert after["last_reviewed_at"] == run.completed_at.isoformat()
        assert after["last_review_status"] == "insufficient_evidence"
        assert after["last_review_summary"] == "原文尚未更新"
        publish(session, events=[event(action="updated", body="资金用途出现新披露")])
        changed = research_activity(session, "xlk", include_followups=True)["current_followups"][0]
        assert changed["last_reviewed_at"] is None  # An old-version receipt cannot check a new judgment.


def test_active_themes_own_work_but_paused_dedicated_questions_stay_paused(activity_client):
    theme = activity_client.post("/api/research/instruments/xlk/themes", json={"title": "长期资金效果", "question": "能否改善经营"}).json()
    with get_session_factory()() as session:
        publish(session, research={"questions": [{"key": "capacity", "theme_id": theme["theme_id"],
            "question": "能否改善经营", "assessment": "需更多证据", "next_check": "核对披露"}]})
        original = next(row for row in research_activity(session, "xlk")["updates"] if row["kind"] == "question")
        run = publish(session, reflection={"status": "reviewed", "summary": "假设未变", "reviewed_update_ids": [original["update_id"]]})
        assert research_activity(session, "xlk", include_followups=True)["current_followups"] == []
        from watchlist_app.services.research_themes import themes_view
        projected = themes_view(session, "xlk")["themes"][0]
        assert projected["last_changed_at"] == original["recorded_at"]
        assert projected["current_questions"][0]["last_reviewed_at"] == run.completed_at.isoformat()
    assert activity_client.patch(f"/api/research/instruments/xlk/themes/{theme['theme_id']}", json={"status": "paused"}).status_code == 200
    data = activity_client.get("/api/research/instruments/xlk/activity").json()
    assert data["current_followups"] == []
    assert any(row["kind"] == "question" for row in data["updates"])


def test_quiet_check_keeps_current_research_distinct_from_its_own_increment(activity_client):
    with get_session_factory()() as session:
        publish(session, research={"investment_view": {"direction": "假设等待证据", "risk": "成本传导待验证"}})
        quiet = publish(session)
        latest = service.latest_reviews(session)["xlk"]
        assert latest["run_id"] == quiet.entry_id and latest["research"] is None
        assert latest["current_research"]["investment_view"]["risk"] == "成本传导待验证"


def test_activity_clock_normalizes_database_and_source_offsets_before_ordering():
    from datetime import datetime
    from watchlist_app.services.research_activity import _time
    assert _time(datetime.fromisoformat('2026-09-12T08:30:00+08:00')) == _time('2026-09-12T00:30:00Z')
    assert _time(datetime.fromisoformat('2026-09-11T21:00:00-04:00')) > _time('2026-09-12T00:30:00Z')


@pytest.mark.parametrize("status", ["paused", "closed"])
def test_review_agenda_excludes_inactive_theme_pm_checks_but_preserves_original_opinions(activity_client, status):
    from watchlist_app.services.research_dossier import read_dossier
    client = activity_client
    theme = client.post("/api/research/instruments/xlk/themes", json={"title": "资金改善", "question": "资金是否改善经营"}).json()
    for title, context in (("主题内投资观点", {"theme_id": theme["theme_id"]}), ("独立投资观点", {})):
        response = client.post("/api/instruments/xlk/research/notes", json={"note": {
            "note_date": "2026-09-09", "title": title, "body": "仍需核实经营效果。", "research_context": context}})
        assert response.status_code == 200, response.text
    with get_session_factory()() as session:
        before = read_dossier(session, "xlk")
        assert {row["title"] for row in before["review_agenda"]["pm_views"]} == {"主题内投资观点", "独立投资观点"}
    change = {"status": status, **({"close_reason": "暂不继续此主题"} if status == "closed" else {})}
    assert client.patch(f"/api/research/instruments/xlk/themes/{theme['theme_id']}", json=change).status_code == 200
    with get_session_factory()() as session:
        dossier = read_dossier(session, "xlk")
        assert [row["title"] for row in dossier["review_agenda"]["pm_views"]] == ["独立投资观点"]
        assert {row["title"] for row in dossier["pm_views"]} == {"主题内投资观点", "独立投资观点"}
        original = next(row for row in research_activity(session, "xlk")["updates"]
                        if row["kind"] == "opinion" and row["title"] == "主题内投资观点")
        with pytest.raises(ValueError, match="暂停或结束"):
            publish(session, reflection={"status": "reviewed", "summary": "复核暂停主题观点",
                "reviewed_update_ids": [original["update_id"]]})


def test_event_receipt_does_not_check_its_grouped_question_or_theme_judgment(activity_client):
    from watchlist_app.services.research_activity import review_receipts
    from watchlist_app.services.research_themes import themes_view
    client = activity_client
    theme = client.post("/api/research/instruments/xlk/themes", json={"title": "经营改善", "question": "资金能否改善经营"}).json()
    with get_session_factory()() as session:
        publish(session, events=[event()], research={"questions": [
            {"key": "terms", "event_key": "financing", "question": "资金如何使用", "assessment": "用途未明", "next_check": "取得说明"},
            {"key": "capacity", "theme_id": theme["theme_id"], "question": "经营能否改善", "assessment": "效果待证", "next_check": "经营披露"}]})
        current = research_activity(session, "xlk", include_followups=True)["current_followups"][0]
        child = current["related_updates"][0]
        reviewed = publish(session, reflection={"status": "reviewed", "summary": "只复核了原事件条款",
            "reviewed_update_ids": [current["followup_id"]]})
        after = research_activity(session, "xlk", include_followups=True)["current_followups"][0]
        assert after["last_reviewed_at"] == reviewed.completed_at.isoformat()
        assert child["update_id"] not in review_receipts(session, "xlk")
        assert themes_view(session, "xlk")["themes"][0]["current_questions"][0]["last_reviewed_at"] is None
        publish(session, reflection={"status": "insufficient_evidence", "summary": "子问题仍缺证据",
            "reviewed_update_ids": [child["update_id"]]})
        assert research_activity(session, "xlk", include_followups=True)["current_followups"][0]["last_reviewed_at"] == after["last_reviewed_at"]
