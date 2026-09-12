from datetime import UTC, datetime, timedelta

import pytest
from studio_identity import Principal, principal_context

from watchlist_app.db.session import get_session_factory
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.services import research_access, sector_research
from watchlist_app.services.research_activity import research_activity, review_agenda
from watchlist_app.services.research_dossier import _notebooks
from watchlist_app.services.research_themes import ThemeInput, ThemePatch, save_theme

from .test_research_activity import activity_client, event, publish


@pytest.mark.parametrize("original_kind", ["question", "forecast"])
def test_automatic_review_cannot_resume_paused_theme_through_implicit_original_link(activity_client, original_kind):
    """An original-version reference carries the same lifecycle scope as theme_id."""
    with get_session_factory()() as session:
        theme = save_theme(session, "xlk", ThemeInput(title="融资效果", question="资金是否改善经营？"))
        session.commit()
        initial = ({"questions": [{"key": "funding", "theme_id": theme["theme_id"],
            "question": theme["question"], "assessment": "资金来源已确认，经营效果待验证。",
            "next_check": "下一次经营披露", "source_ids": ["original"]}]}
            if original_kind == "question" else {"forecasts": [{"key": "funding", "theme_id": theme["theme_id"],
                "claim": "融资可能缓解经营约束", "horizon": "下一季度", "source_ids": ["original"]}]})
        publish(session, research=initial)
        original = next(row for row in research_activity(session, "xlk")["updates"] if row["kind"] == original_kind)
        save_theme(session, "xlk", ThemePatch(status="paused"), theme_id=theme["theme_id"])
        session.commit()
        reference = ({"related_research_update_id": original["update_id"]} if original_kind == "question" else
            {"forecast_key": "funding", "forecast_version_id": original["reference"]["forecast_version_id"]})
        with pytest.raises(ValueError, match="暂停或结束"):
            publish(session, research={"forecast_reviews": [{"key": "funding-review", **reference,
                "outcome": "自动研究继续评估了已暂停问题。", "source_ids": ["original"]}]})


@pytest.mark.parametrize("status", ["paused", "closed"])
def test_review_agenda_omits_inactive_theme_assignments_but_keeps_shared_events(activity_client, status):
    with get_session_factory()() as session:
        theme = save_theme(session, "xlk", ThemeInput(title="融资效果", question="资金是否改善经营？"))
        session.commit()
        publish(session, events=[event(theme_ids=[theme["theme_id"]])], research={
            "questions": [{"key": "funding", "theme_id": theme["theme_id"], "question": theme["question"],
                "assessment": "效果待验证", "next_check": "下一季", "source_ids": ["original"]}],
            "forecasts": [{"key": "theme-forecast", "theme_id": theme["theme_id"], "claim": "经营可能改善",
                "horizon": "下一季", "source_ids": ["original"]},
                {"key": "independent-forecast", "claim": "另一项独立判断", "horizon": "下一季", "source_ids": ["original"]}]})
        original = next(row for row in research_activity(session, "xlk")["updates"] if row["kind"] == "question")
        publish(session, research={"lessons": [{"key": "funding-lesson", "related_research_update_id": original["update_id"],
            "lesson": "融资不是经营改善的充分条件", "source_ids": ["original"]}]})
        save_theme(session, "xlk", ThemePatch(status=status), theme_id=theme["theme_id"])
        session.commit()
        notebook, _ = _notebooks(session, "xlk", False)
        agenda = review_agenda(session, "xlk", notebook, [])
        assert agenda["tracked_questions"] == [] and agenda["existing_lessons"] == []
        assert [row["key"] for row in agenda["active_forecasts"]] == ["independent-forecast"]
        assert len(agenda["pending_events"]) == 1
        original_event = next(row for row in research_activity(session, "xlk")["updates"] if row["kind"] == "event")
        publish(session, research={"forecast_reviews": [{"key": "shared-fact-review",
            "related_research_update_id": original_event["update_id"],
            "outcome": "复核共享事件的新事实不恢复专属主题研究。", "source_ids": ["original"]}]})


@pytest.mark.parametrize("status", ["paused", "closed"])
@pytest.mark.parametrize("kind", ["question", "forecast"])
def test_reflection_cannot_recheck_an_inactive_theme_assignment(activity_client, status, kind):
    with get_session_factory()() as session:
        theme = save_theme(session, "xlk", ThemeInput(title="融资效果", question="资金是否改善经营？"))
        session.commit()
        research = ({"questions": [{"key": "funding", "theme_id": theme["theme_id"], "question": theme["question"],
            "assessment": "效果待验证", "next_check": "下一次披露", "source_ids": ["original"]}]} if kind == "question" else {"forecasts": [{
                "key": "funding", "theme_id": theme["theme_id"], "claim": "经营可能改善", "horizon": "下一季",
                "source_ids": ["original"]}]})
        publish(session, events=[event(theme_ids=[theme["theme_id"]])], research=research)
        updates = research_activity(session, "xlk")["updates"]
        assignment = next(row for row in updates if row["kind"] == kind)
        shared_event = next(row for row in updates if row["kind"] == "event")
        save_theme(session, "xlk", ThemePatch(status=status), theme_id=theme["theme_id"])
        session.commit()
        with pytest.raises(ValueError, match="暂停或结束"):
            publish(session, reflection={"status": "reviewed", "summary": "专属问题已复核",
                "reviewed_update_ids": [assignment["update_id"]]})
        session.rollback()
        # A failed validation is not a completed run; clear this fixture's queue before the next attempt.
        for run in session.query(ResearchEntry).filter(ResearchEntry.status == "queued"):
            run.status = "failed"
        session.commit()
        published = publish(session, reflection={"status": "reviewed", "summary": "只复核共享事件的新事实",
            "reviewed_update_ids": [shared_event["update_id"]]})
        assert published.context_json["reviews"]["xlk"]["reflection"]["reviewed_update_ids"] == [shared_event["update_id"]]


def test_theme_creation_or_status_change_is_not_a_checked_judgment(activity_client):
    with get_session_factory()() as session:
        save_theme(session, "xlk", ThemeInput(title="融资效果", question="资金是否改善经营？"))
        session.commit()
        theme_update = next(row for row in research_activity(session, "xlk")["updates"] if row["kind"] == "theme")
        with pytest.raises(ValueError, match="不是事前判断"):
            publish(session, reflection={"status": "reviewed", "summary": "主题已复核",
                "reviewed_update_ids": [theme_update["update_id"]]})


@pytest.mark.parametrize("instrument_ids", [None, ["fund-us-agg"]])
def test_review_states_batch_topic_scopes_without_reusing_them_across_reads(client, monkeypatch, instrument_ids):
    calls = []
    check_scopes = research_access.topic_portfolio_ids_by_topic
    def measured_scopes(session, topics):
        calls.append(sorted(topic.topic_id for topic in topics))
        return check_scopes(session, topics)
    monkeypatch.setattr(research_access, "topic_portfolio_ids_by_topic", measured_scopes)
    with get_session_factory()() as session:
        for topic_id in ("repeated-public-topic", "repeated-portfolio-topic", "unrelated-analysis-topic"):
            session.add(ResearchTopic(topic_id=topic_id, title=topic_id, visibility="team"))
        session.flush()
        for index in range(3):
            for scope in ("public", "portfolio"):
                session.add(ResearchEntry(entry_id=f"memo-{scope}-{index}", topic_id=f"repeated-{scope}-topic",
                    kind="analysis", status="completed", title="研究", context_json={
                        "research_run": True, "instrument_ids": ["fund-us-agg"],
                        "portfolio_id": "private-historical-scope" if scope == "portfolio" and index == 0 else None,
                        "reviews": {"fund-us-agg": {"status": "completed", "summary": f"{scope}-{index}",
                            "research": {"investment_view": {"direction": f"{scope}-{index}"}}}}}))
        session.add(ResearchEntry(entry_id="unrelated-analysis", topic_id="unrelated-analysis-topic", kind="analysis",
            status="completed", title="无关分析", context_json={"instrument_ids": ["fund-us-agg"]}))
        session.commit()
        queries = []
        execute = session.execute
        def measured_execute(statement, *args, **kwargs):
            queries.append(statement)
            return execute(statement, *args, **kwargs)
        monkeypatch.setattr(session, "execute", measured_execute)
        states = sector_research.review_states(session, instrument_ids=instrument_ids)
        assert states["latest"]["fund-us-agg"]["current_summary"].startswith("public-")
        assert states["last_completed"] == states["latest"]
        assert calls == [["repeated-portfolio-topic", "repeated-public-topic"]]
        assert len(queries) == 3
        calls.clear()
        queries.clear()
        # A newly retained private scope must take effect on the next read.
        session.add(ResearchEntry(entry_id="new-private-scope", topic_id="repeated-public-topic", kind="note",
            status="recorded", title="私有组合历史", context_json={"portfolio_id": "new-private-scope"}))
        session.commit()
        assert sector_research.review_states(session, instrument_ids=instrument_ids) == {"latest": {}, "last_completed": {}}
        assert calls == [["repeated-portfolio-topic", "repeated-public-topic"]]
        assert len(queries) == 3


@pytest.mark.parametrize("instrument_ids", [None, ["fund-us-agg"]])
def test_shared_review_states_preserve_quiet_check_current_judgment_and_failed_attempt(client, instrument_ids):
    notebook = {"investment_view": {"direction": "信用质量仍待验证", "risk": "信用质量仍待验证",
        "updated_at": "2026-09-01T00:00:00+00:00", "source_run_id": "published-view"}}
    with get_session_factory()() as session:
        topic = ResearchTopic(topic_id="shared-state", title="研究", visibility="team")
        session.add(topic)
        session.flush()
        for index, (run_id, status, sector, review) in enumerate([
            ("published-view", "draft", False, {"status": "completed", "summary": "信用质量仍待验证", "research": notebook}),
            ("quiet-review", "completed", True, {"status": "limited", "change_kind": "none",
                "coverage": ["本轮仅覆盖已披露资料"], "reflection": {"status": "insufficient_evidence"}}),
            ("failed-check", "failed", True, {}),
            ("private-conversation", "completed", False, {}),
        ]):
            when = datetime(2026, 9, 1, tzinfo=UTC) + timedelta(days=index)
            session.add(ResearchEntry(entry_id=run_id, topic_id=topic.topic_id, kind="analysis", title="研究",
                status=status, body="本轮执行失败" if status == "failed" else "私人对话内容", completed_at=when,
                context_json={"research_run": True, "sector_run": sector, "instrument_ids": ["fund-us-agg"],
                    "cutoff": when.isoformat(), "reviews": {"fund-us-agg": review}}))
        session.commit()
        states = sector_research.review_states(session, instrument_ids=instrument_ids)
        latest, completed = states["latest"]["fund-us-agg"], states["last_completed"]["fund-us-agg"]
        assert (latest["run_id"], latest["status"], latest["summary"]) == ("failed-check", "failed", "本轮执行失败")
        assert (completed["run_id"], completed["status"], completed["change_kind"]) == ("quiet-review", "limited", "none")
        assert completed["research"] is None
        for state in (latest, completed):
            assert state["current_summary"] == "信用质量仍待验证"
            assert state["current_research"] == notebook
            assert state["view_run_id"] == "published-view"
            assert state["view_updated_at"] == "2026-09-01T00:00:00+00:00"
        assert sector_research.latest_reviews(session, instrument_ids=instrument_ids) == states["latest"]
        assert sector_research.latest_reviews(session, completed_only=True, instrument_ids=instrument_ids) == states["last_completed"]


@pytest.mark.parametrize("private_scope", ["topic", "entry", "risk_entry", "foreign_topic", "foreign_entry"])
@pytest.mark.parametrize("instrument_ids", [None, ["fund-us-agg"]])
def test_shared_review_states_exclude_current_and_historical_private_scopes(client, private_scope, instrument_ids):
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="scoped-state", title="研究", visibility="team",
            team_id="other-team" if private_scope == "foreign_topic" else "default",
            portfolio_id="private-portfolio" if private_scope == "topic" else None))
        session.flush()
        session.add(ResearchEntry(entry_id="candidate-publication", topic_id="scoped-state", kind="analysis",
            team_id="other-team" if private_scope == "foreign_entry" else "default", status="completed", title="研究",
            context_json={"sector_run": True, "instrument_ids": ["fund-us-agg"],
                "reviews": {"fund-us-agg": {"status": "completed", "summary": "私有判断"}}}))
        if private_scope in {"entry", "risk_entry"}:
            scope = {"portfolio_id": "private-portfolio"}
            session.add(ResearchEntry(entry_id="old-private-note", topic_id="scoped-state", kind="note",
                team_id="other-team", status="recorded", title="历史组合上下文",
                context_json=scope if private_scope == "entry" else {"risk_scope": scope}))
        session.commit()
        with principal_context(Principal("alice", "经理甲", "default")):
            assert sector_research.review_states(session, instrument_ids=instrument_ids) == {"latest": {}, "last_completed": {}}


@pytest.mark.parametrize("evidence", ["current_snapshot", "other_snapshot", "missing", "future_metric"])
def test_reflection_publication_validates_original_evidence_scope_and_cutoff(activity_client, evidence):
    with get_session_factory()() as session:
        run, _ = sector_research.begin_run(session, ["xlk"])
        run.context_json = {**run.context_json, "cutoff": "2026-09-12T00:00:00+00:00",
            "instrument_inputs": [{"instrument_id": iid, "name": iid.upper(),
                "reference_data": {"sections": {"financials": [{"report_period": "2026-06-30", "value": 12}]}}}
                for iid in ("xlk", "xlf")],
            "computed_metrics": [{"source_id": "future-metric", "source_type": "computed_metric",
                "instrument_id": "xlk", "scope": "instrument", "as_of": "2026-09-13T00:00:00+00:00",
                "methodology": "Application measurement", "data": {"value": 12}}]}
        source_id = {"current_snapshot": f"instrument:{run.entry_id}:xlk",
            "other_snapshot": f"instrument:{run.entry_id}:xlf", "missing": "missing-source",
            "future_metric": "future-metric"}[evidence]
        draft = sector_research.ReviewResult.model_validate({"reviews": [{"instrument_id": "xlk",
            "reflection": {"status": "reviewed", "summary": "已核对披露财务，未来结果待验证", "source_ids": [source_id]}}]})
        if evidence == "current_snapshot":
            sector_research.validate_result(session, run, draft)
            assert sector_research.draft_payload(draft)["reviews"][0]["reflection"]["source_ids"] == [source_id]
        else:
            with pytest.raises(ValueError, match="研究底稿"):
                sector_research.validate_result(session, run, draft)
