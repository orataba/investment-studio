import pytest

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
        assert agenda["open_questions"] == [] and agenda["existing_lessons"] == []
        assert [row["key"] for row in agenda["active_forecasts"]] == ["independent-forecast"]
        assert len(agenda["pending_events"]) == 1
        original_event = next(row for row in research_activity(session, "xlk")["updates"] if row["kind"] == "event")
        publish(session, research={"forecast_reviews": [{"key": "shared-fact-review",
            "related_research_update_id": original_event["update_id"],
            "outcome": "复核共享事件的新事实不恢复专属主题研究。", "source_ids": ["original"]}]})


def test_latest_reviews_checks_each_topic_scope_once_per_read_and_never_reuses_it_across_reads(client, monkeypatch):
    calls = []
    check_scope = research_access.topic_portfolio_ids
    def measured_scope(session, topic):
        calls.append(topic.topic_id)
        return check_scope(session, topic)
    monkeypatch.setattr(research_access, "topic_portfolio_ids", measured_scope)
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
                        "reviews": {"fund-us-agg": {"status": "completed", "summary": f"{scope}-{index}"}}}))
        session.add(ResearchEntry(entry_id="unrelated-analysis", topic_id="unrelated-analysis-topic", kind="analysis",
            status="completed", title="无关分析", context_json={"instrument_ids": ["fund-us-agg"]}))
        session.commit()
        result = sector_research.latest_reviews(session)
        assert result["fund-us-agg"]["current_summary"].startswith("public-")
        assert sorted(calls) == ["repeated-portfolio-topic", "repeated-public-topic"]
        calls.clear()
        # A newly retained private scope must take effect on the next read.
        session.add(ResearchEntry(entry_id="new-private-scope", topic_id="repeated-public-topic", kind="note",
            status="recorded", title="私有组合历史", context_json={"portfolio_id": "new-private-scope"}))
        session.commit()
        assert sector_research.latest_reviews(session) == {}
        assert sorted(calls) == ["repeated-portfolio-topic", "repeated-public-topic"]
