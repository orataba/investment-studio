from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from watchlist_app.services.research_dossier import ResearchMandateInput, effective_mandate
from watchlist_app.services.research_notebook import ResearchNotebook, retain_notebook, validate_notebook
from watchlist_app.services.sector_research import SectorEvent, _effective_event, _same_progress, event_record
from watchlist_app.services.risk_read_projection import _risk_case_brief


CUTOFF = "2026-09-24T00:00:00+00:00"
SOURCE = {"source_id": "source", "source_type": "public_source", "text": "发行人原文", "url": "https://issuer.test/news",
          "published_at": "2026-09-23"}


def retain(value, previous=None, run="first"):
    return retain_notebook(ResearchNotebook.model_validate(value), previous, {"source": SOURCE}, run, CUTOFF)


def baseline():
    return {"investment_view": {"direction": "有条件看好", "source_ids": ["source"]},
        "decision_brief": {"recommendation": "维持观察，等待订单兑现", "rationale": "估值已反映部分增长", "source_ids": ["source"]},
        "changes": [{"key": "demand", "title": "订单前景改善", "before": "订单持平", "after": "订单增加",
            "baseline_as_of": "2026-09-01", "mechanism": "提高产能利用率", "decision_implication": "改善回报前景，尚不追价",
            "source_ids": ["source"]}]}


def test_view_change_never_relabels_an_omitted_old_recommendation_as_current():
    first = retain(baseline())
    assert first["decision_brief"]["basis_view_version_id"] == first["investment_view"]["version_id"]
    assert not first["decision_brief"]["needs_review"]
    changed = retain({"investment_view": {"direction": "增长证据减弱"}}, first, "changed")
    assert changed["decision_brief"]["needs_review"]
    assert changed["decision_brief"]["updated_at"] == first["decision_brief"]["updated_at"]
    assert changed["changes"] == first["changes"]
    confirmed = retain({"decision_brief": baseline()["decision_brief"]}, changed, "confirmed")
    assert not confirmed["decision_brief"]["needs_review"]
    assert confirmed["decision_brief"]["version_id"] == "confirmed:decision-brief"
    assert confirmed["decision_brief"]["versions"][-1]["basis_view_version_id"] == first["investment_view"]["version_id"]


def test_unrelated_module_update_does_not_require_or_redate_recommendation():
    first = retain(baseline())
    updated = retain({"modules": [{"key": "identity-structure", "analysis": "补充证券单位说明"}]}, first, "metadata")
    assert updated["decision_brief"] == first["decision_brief"]
    assert updated["changes"] == first["changes"]
    quiet = retain({}, updated, "quiet")
    assert quiet["version_id"] == updated["version_id"]


def test_changes_require_real_sources_and_cannot_use_future_comparison_date():
    notebook = ResearchNotebook.model_validate(baseline())
    with pytest.raises(ValueError, match="未取得"):
        validate_notebook(notebook, "asset", {})
    notebook.changes[0].baseline_as_of = datetime(2026, 9, 25).date()
    with pytest.raises(ValueError, match="对照基准日期"):
        validate_notebook(notebook, "asset", {"source": SOURCE}, cutoff=datetime.fromisoformat(CUTOFF))


def test_ai_cannot_replace_user_methods_or_reading_preferences():
    previous = ResearchMandateInput(title="安排", background="背景", user_methods=["比较同财期增长"],
        report_preferences={"priority_modules": ["pricing-compensation"], "summary_focus": ["估值变化"], "detail_level": "concise"}).model_dump()
    proposed = ResearchMandateInput(title="安排", background="新背景", user_methods=["取消比较"],
        report_preferences={"priority_modules": ["events-expectations"], "detail_level": "detailed"})
    actual = effective_mandate(proposed, previous, origin="research")
    assert actual["user_methods"] == previous["user_methods"]
    assert actual["report_preferences"] == previous["report_preferences"]
    actual = effective_mandate(proposed, previous, origin="user")
    assert actual["user_methods"] == ["取消比较"]


def event(**changes):
    return SectorEvent.model_validate({"event_key": "credit", "action": "new", "direction": "risk", "title": "融资变化",
        "body": "融资渠道收窄", "follow_up": "none", "confidence": "reported", "information_type": "fact",
        "recording_type": "new", "source_ids": ["source"], **changes})


def test_impact_urgency_and_evidence_are_distinct_and_sparse_updates_keep_them():
    assessed = event(impact_level="major", urgency="immediate", risk_channels=["credit", "liquidity"],
        impact_analysis="融资中断可能影响下月到期债偿付", action_condition="在申赎截止前核实融资可用性")
    _effective_event(assessed, None)
    stamp = datetime.now(UTC)
    saved = SimpleNamespace(case_id="case", instrument_id="asset", signal="sector:credit", title=assessed.title,
        body=assessed.body, evidence_json={**assessed.model_dump(mode="json"), "sources": [SOURCE]}, history_json=[],
        status="recorded", trigger_active=False, created_at=stamp, updated_at=stamp)
    retained = _effective_event(event(action="updated"), saved)
    assert retained.confidence == "reported" and retained.impact_level == "major" and retained.urgency == "immediate"
    assert _same_progress(saved, retained, [SOURCE])
    assert event_record(saved)["action_condition"] == assessed.action_condition
    brief = _risk_case_brief({"case_id": "case", "evidence_json": deepcopy(saved.evidence_json)})
    assert brief["urgency"] == "immediate" and brief["confidence"] == "reported"
    with pytest.raises(ValueError, match="具体决策条件"):
        _effective_event(event(urgency="immediate"), None)
    with pytest.raises(ValueError, match="传导机制"):
        _effective_event(event(impact_level="major"), None)
