from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import sector_research
from watchlist_app.services.research_activity import research_activity
from watchlist_app.services.research_dossier import _notebooks
from watchlist_app.services.research_notebook import ResearchNotebook, retain_notebook, validate_notebook
from watchlist_app.services.research_themes import ThemeInput, save_theme, themes_view

from .test_research_activity import activity_client, publish


@pytest.mark.parametrize("with_theme", [False, True])
def test_system_citation_revision_updates_current_sources_without_changing_judgment_or_check_clocks(activity_client, with_theme):
    with get_session_factory()() as session:
        theme = save_theme(session, "xlk", ThemeInput(title="盈利质量", question="盈利质量是否改善")) if with_theme else None
        session.commit()
        question = {"key": "earnings", "question": "盈利质量是否改善", "assessment": "披露数据仍待持续验证", "next_check": "下一次财报"}
        if theme:
            question["theme_id"] = theme["theme_id"]
        original_run = publish(session, research={"source_ids": ["original"], "questions": [question],
            "investment_view": {"direction": "有条件看好", "horizon": "未来一季"}})
        original_context = deepcopy(original_run.context_json)
        original_updates = research_activity(session, "xlk")["updates"]
        originals = {row["kind"]: row for row in original_updates if row["kind"] in {"question", "judgment"}}
        checked = publish(session, reflection={"status": "reviewed", "summary": "复核原判断，仍待观察",
            "reviewed_update_ids": [originals["question"]["update_id"]]})
        before_states = sector_research.review_states(session)
        before_followups = research_activity(session, "xlk", include_followups=True)["current_followups"]
        before_theme = themes_view(session, "xlk")["themes"][0] if theme else None
        current, _ = _notebooks(session, "xlk", True)
        evidence = {source["source_id"]: source for source in current["sources"]}
        delta = ResearchNotebook.model_validate({"questions": [{**question, "source_ids": ["original"]}],
            "investment_view": {"source_ids": ["original"]}})
        validate_notebook(delta, "xlk", evidence)
        repaired = retain_notebook(delta, current, evidence, "citation-correction", current["checked_at"])
        for key in ("created_at", "updated_at", "checked_at"):
            repaired[key] = current[key]
        for key in ("created_at", "updated_at", "source_run_id"):
            repaired["investment_view"][key] = current["investment_view"][key]
        corrected_at = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        provenance = {"source_run_id": original_run.entry_id, "source_notebook_version_id": current["version_id"],
            "reason": "恢复同轮独立核证明确返回的合法来源引用", "author": {"role": "system", "display_name": "系统"},
            "corrected_at": corrected_at, "updates": {f"{field}:{key}": {
                "source_update_id": originals[kind]["update_id"], "original_recorded_at": originals[kind]["recorded_at"]}
                for field, key, kind in (("questions", "earnings", "question"), ("investment_view", "investment-view", "judgment"))}}
        repaired["citation_correction"] = provenance
        session.add(ResearchEntry(entry_id="citation-correction", topic_id=original_run.topic_id, kind="analysis",
            title="引用修正", source="Investment Studio 引用修正", status="completed", completed_at=datetime.fromisoformat(corrected_at),
            context_json={"research_run": True, "recordkeeping_only": True, "instrument_ids": ["xlk"],
                "cutoff": current["checked_at"], "citation_correction": provenance,
                "reviews": {"xlk": {"status": "completed", "change_kind": "knowledge", "research": repaired}}}))
        session.commit()

        states = sector_research.review_states(session)
        for key in ("latest", "last_completed"):
            assert {k: v for k, v in states[key]["xlk"].items() if k != "current_research"} == {
                k: v for k, v in before_states[key]["xlk"].items() if k != "current_research"}
            assert states[key]["xlk"]["run_id"] == checked.entry_id
            assert states[key]["xlk"]["current_research"]["investment_view"]["source_ids"] == ["original"]
        notebook, history = _notebooks(session, "xlk", True)
        assert notebook["version_id"] == "citation-correction" and notebook["checked_at"] == current["checked_at"]
        assert notebook["investment_view"]["updated_at"] == current["investment_view"]["updated_at"]
        assert notebook["questions"][0] == {**current["questions"][0], "source_ids": ["original"]}
        assert any(row["version_id"] == current["version_id"] for row in history)
        assert session.get(ResearchEntry, original_run.entry_id).context_json == original_context
        activity = research_activity(session, "xlk", include_followups=True)
        revisions = [row for row in activity["updates"] if row.get("change") == "citation_corrected"]
        assert len(revisions) == 2
        for row in revisions:
            original = originals[row["kind"]]
            assert row["title"] == original["title"] and row["body"] == original["body"]
            assert row["author_role"] == "system" and row["recorded_at"] == corrected_at
            assert row["citation_correction"]["source_update_id"] == original["update_id"]
            assert [source["source_id"] for source in row["sources"]] == ["original"]
        if theme:
            after = themes_view(session, "xlk")["themes"][0]
            assert after["last_changed_at"] == before_theme["last_changed_at"]
            assert after["last_reviewed_at"] == before_theme["last_reviewed_at"]
            assert after["current_assessment"]["updated_at"] == before_theme["current_assessment"]["updated_at"]
            assert after["current_assessment"]["source_ids"] == ["original"]
        else:
            followup = activity["current_followups"][0]
            assert followup["title"] == before_followups[0]["title"]
            assert followup["last_changed_at"] == before_followups[0]["last_changed_at"]
            assert followup["last_reviewed_at"] == before_followups[0]["last_reviewed_at"]
