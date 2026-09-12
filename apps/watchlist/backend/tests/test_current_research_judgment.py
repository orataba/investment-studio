"""One current analyst judgment across reports, dossier, tracking and risk readers."""
from copy import deepcopy

from .test_research_activity import activity_client, publish
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import sector_research
from watchlist_app.services.research_dossier import read_dossier


def test_report_prose_cannot_override_revised_or_withdrawn_current_judgment(activity_client):
    with get_session_factory()() as session:
        first = publish(session, research={"investment_view": {
            "direction": "盈利改善，但当前价格吸引力有限", "horizon": "未来两个季度",
            "invalidation": "同口径现金回款持续恶化", "next_check": "核实下期现金流与收入质量",
            "source_ids": ["original"]}})
        # A retained report may phrase the old view differently from the structured state.
        context = deepcopy(first.context_json)
        context["reviews"]["xlk"]["summary"] = "历史报告：估值有吸引力"
        first.context_json = context
        session.commit()
        original = deepcopy(read_dossier(session, "xlk")["notebook"]["investment_view"])

        publish(session, research={"investment_view": {"direction": "新增证据削弱盈利判断"}})
        state = sector_research.review_states(session)["last_completed"]["xlk"]
        assert state["summary"] == state["current_summary"] == "新增证据削弱盈利判断"
        view = state["current_research"]["investment_view"]
        assert view["next_check"] == original["next_check"]
        assert view["invalidation"] == original["invalidation"]
        assert state["view_updated_at"] == view["updated_at"]
        assert view["versions"][-1] == {key: value for key, value in original.items() if key != "versions"}

        publish(session, research={"investment_view": None})
        state = sector_research.review_states(session)["last_completed"]["xlk"]
        assert state["current_research"]["investment_view"] is None
        assert state["summary"] == state["current_summary"] == ""
        assert state["view_updated_at"] is None and state["view_run_id"] is None
        assert first.context_json["reviews"]["xlk"]["summary"] == "历史报告：估值有吸引力"


def test_quiet_or_failed_check_preserves_exact_current_judgment_version(activity_client):
    with get_session_factory()() as session:
        first = publish(session, research={"investment_view": {"direction": "等待现金流证据", "source_ids": ["original"]}})
        original = deepcopy(read_dossier(session, "xlk")["notebook"]["investment_view"])
        quiet = publish(session)
        quiet_state = sector_research.review_states(session)["last_completed"]["xlk"]
        assert quiet_state["run_id"] == quiet.entry_id
        assert quiet_state["summary"] == original["direction"]
        assert quiet_state["view_run_id"] == first.entry_id
        assert quiet_state["view_updated_at"] == original["updated_at"]
        failed, _ = sector_research.begin_run(session, ["xlk"])
        failed.status, failed.body = "failed", "来源不可用"
        session.commit()
        latest = sector_research.review_states(session)["latest"]["xlk"]
        assert latest["status"] == "failed" and latest["summary"] == "来源不可用"
        assert latest["current_summary"] == original["direction"]
        assert latest["current_research"]["investment_view"] == original


def test_report_without_structured_view_is_history_not_a_current_judgment(activity_client):
    with get_session_factory()() as session:
        run = publish(session)
        context = deepcopy(run.context_json)
        context["reviews"]["xlk"]["summary"] = "仅有旧报告正文"
        run.context_json = context
        session.commit()
        state = sector_research.review_states(session)["last_completed"]["xlk"]
        assert state["summary"] == "" and state["current_research"] is None
