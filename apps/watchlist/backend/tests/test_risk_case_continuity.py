from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import JSON, Column, MetaData, String, Table, create_engine, select

from watchlist_app.services.risk_case_state import (
    advance_event_risk, assessed_risk_fields, risk_state_projection, should_refer_event,
)


def event(version="v1", **changes):
    return {"event_version_id": version, "event_key": "redemption", "run_id": "event-run",
        "title": "赎回限制", "body": "暂停赎回", "direction": "risk", "impact_level": "major",
        "urgency": "immediate", "confidence": "reported", "information_type": "fact",
        "impact_analysis": "现金无法在期限前到账", "action_condition": "核实明日到期资金安排",
        "risk_channels": ["liquidity"], "source_ids": ["original"], "checked_at": "2026-10-11T00:00:00Z",
        "recorded_at": "2026-10-11T00:01:00Z", "sources": [{"source_id": "original", "source_type": "public_source",
            "document_id": "notice", "version_id": "notice-v1", "published_at": "2026-10-11"}], **changes}


def decision(version="v1", status="active"):
    return {"event_version_id": version, "status": status, "reason": "条款明确，尚未解除",
        "run_id": "risk-run", "reviewed_at": "2026-10-11T01:00:00Z"}


def current_case():
    value = event(risk_assessment=decision())
    return SimpleNamespace(case_id="case", instrument_id="fund", title=value["title"], body=value["body"],
        evidence_json=value, history_json=[], status="open", trigger_active=True)


@pytest.mark.parametrize("material, changes", [(True, {"body": "延期公告等待核实"}), (False, {"next_watch": "跟进说明"})])
def test_new_revision_keeps_old_confirmed_content_and_exact_versions(material, changes):
    case = current_case()
    new = event("v2", title="未经复核的新标题", sources=[{**event()["sources"][0], "version_id": "notice-v2", "text": "not projected"}], **changes)
    result = advance_event_risk(case, new, material_change=material, sources=new["sources"])
    assert result["last_assessed_snapshot"]["title"] == "赎回限制"
    assert result["last_assessed_snapshot"]["event_version_id"] == "v1"
    assert result["last_assessed_snapshot"]["reviewed_at"] == decision()["reviewed_at"]
    assert result["pending_revision"]["title"] == "未经复核的新标题"
    assert result["pending_revision"]["event_version_id"] == "v2"
    assert result["last_assessed_snapshot"]["sources"][0]["version_id"] == "notice-v1"
    assert result["pending_revision"]["sources"][0]["version_id"] == "notice-v2"
    assert "text" not in result["pending_revision"]["sources"][0]
    assert case.evidence_json["risk_assessment"] == decision()  # pure, no mutation
    if material:
        assert result["risk_assessment"]["status"] == "pending"
        assert "reviewed_at" not in result["risk_assessment"]  # no old receipt on new facts


def test_independent_pending_cannot_clear_old_decision_and_resolution_is_explicit():
    case = current_case()
    new = event("v2")
    case.evidence_json = {**new, **advance_event_risk(case, new, material_change=True, sources=new["sources"])}
    pending = assessed_risk_fields(case, decision("v2", "pending"))
    assert pending["last_assessed_snapshot"]["event_version_id"] == "v1"
    assert pending["last_assessed_snapshot"]["status"] == "active"
    resolved = assessed_risk_fields(case, decision("v2", "resolved"))
    assert resolved["last_assessed_snapshot"]["status"] == "resolved"
    assert resolved["pending_revision"] is None


def test_original_required_for_unverified_urgent_and_ordinary_pending_not_promoted():
    new = event()
    pending = advance_event_risk(None, new, material_change=True, sources=new["sources"])["pending_revision"]
    assert pending["urgent_review_required"]
    for changes in ({"information_type": "rumor"}, {"sources": []}, {"confidence": "unverified"},
                    {"impact_level": "limited"}, {"action_condition": ""}):
        changed = {**new, **changes}
        result = advance_event_risk(None, changed, material_change=True, sources=changed["sources"])
        assert not result["pending_revision"]["urgent_review_required"]


def test_eight_ordinary_nav_updates_do_not_create_eight_risk_referrals():
    case = current_case()
    case.evidence_json = event(impact_level="limited", urgency="monitor", impact_analysis="归因尚未明确", action_condition="",
        risk_channels=["market"], risk_assessment={"status": "not_submitted"})
    for index in range(8):
        new = {**case.evidence_json, "event_version_id": f"nav-{index}", "source_ids": [f"nav-{index}"]}
        sources = [{"source_type": "computed_metric", "data": {"rows": [{"date": f"2026-10-{index + 1:02}", "value": 1 + index / 1000}]}}]
        state = advance_event_risk(case, new, material_change=True, sources=sources)
        assert state["risk_assessment"]["status"] == "not_submitted"
        case.evidence_json = {**new, **state}
    changed = {**new, "impact_level": "material", "action_condition": "已设回撤条件触发", "impact_analysis": "触发既有合同观察条件"}
    assert should_refer_event(case, changed, sources, material_change=True)
    ordinary = event(impact_level="limited", urgency="monitor", action_condition="")
    assert not should_refer_event(None, ordinary, sources, material_change=True)
    assert should_refer_event(case, event(), event()["sources"], material_change=True)  # actual redemption notice


@pytest.mark.parametrize("has_material_pending", [False, True])
def test_confirmed_risk_numeric_updates_preserve_exact_existing_pending_state(has_material_pending):
    from watchlist_app.services.risk_review_state import automatic_review_inputs
    case = current_case()
    case.evidence_json = {**case.evidence_json, **assessed_risk_fields(case, decision())}
    if has_material_pending:
        new = event("material-v2", body="后续正式通知需要独立复核")
        case.evidence_json = {**new, **advance_event_risk(case, new, material_change=True, sources=new["sources"])}
    expected = deepcopy(case.evidence_json["pending_revision"])
    def automatic():
        return automatic_review_inputs({"scope": {"kind": "instrument", "id": "fund"}, "instrument_ids": ["fund"],
            "cases": [{**vars(case), "signal": "sector:redemption", "severity": "attention"}]})
    original_inputs = automatic()
    for index in range(8):
        sources = [{"source_type": "computed_metric", "source_id": f"nav-{index}", "data": {"value": 1 + index / 1000}}]
        new = {**case.evidence_json, "event_version_id": f"nav-{index}", "sources": sources,
            "source_ids": [sources[0]["source_id"]], "body": f"数值观测更新为 {1 + index / 1000}"}
        fields = advance_event_risk(case, new, material_change=True, sources=sources)
        case.evidence_json = {**new, **fields}
        assert fields["pending_revision"] == expected
        assert risk_state_projection(case)["pending_revision"] == expected
        assert fields["last_assessed_snapshot"]["event_version_id"] == "v1"
        assert fields["last_assessed_snapshot"]["body"] == "暂停赎回"
        assert automatic() == original_inputs
    for change in ({"withdrawn": True}, {"confidence": "unverified"}, {"impact_analysis": "新证据与既有解释冲突"}):
        changed = {**new, "event_version_id": "contradiction", **change}
        assert advance_event_risk(case, changed, material_change=True, sources=sources)["pending_revision"]["event_version_id"] == "contradiction"


def legacy_case():
    case = current_case()
    old = deepcopy(case.evidence_json)
    new = event("v2", title="新的未核实标题", risk_assessment={"status": "pending", "event_version_id": "v2"})
    case.evidence_json = new
    case.history_json = [{"action": "new", "snapshot": old},
        {"action": "risk_assessed", "risk_assessment": decision()},
        {"action": "updated", "snapshot": new}]
    return case


def test_legacy_backfill_recovers_exact_old_decision_not_new_text_or_unknown_history():
    case = legacy_case()
    projected = risk_state_projection(case, recover_history=True)
    assert projected["last_assessed_snapshot"]["event_version_id"] == "v1"
    assert projected["last_assessed_snapshot"]["title"] == "赎回限制"
    assert projected["pending_revision"]["event_version_id"] == "v2"
    case.history_json = []
    assert risk_state_projection(case, recover_history=True)["last_assessed_snapshot"] is None
    case = legacy_case()
    case.history_json.append({"action": "risk_assessed", "risk_assessment": decision("missing", "resolved")})
    assert risk_state_projection(case, recover_history=True)["last_assessed_snapshot"] is None


def test_explicit_empty_decision_does_not_resurrect_history_after_repeated_publication():
    case = legacy_case()
    original_history = deepcopy(case.history_json)
    case.evidence_json = {**case.evidence_json, "withdrawn": True,
        "risk_assessment": None, "last_assessed_snapshot": None, "pending_revision": None}
    case.status, case.trigger_active = "handled", False
    assert risk_state_projection(case, recover_history=True)["last_assessed_snapshot"] is None
    for version in ("v3", "v4"):
        new = event(version, title="新的公告，需要独立核实")
        state = advance_event_risk(case, new, material_change=True, sources=new["sources"])
        assert state["last_assessed_snapshot"] is None
        assert state["risk_assessment"]["status"] == "pending"
        assert state["pending_revision"]["event_version_id"] == version
        case.evidence_json = {**new, **state}
        case.history_json.append({"action": "updated", "snapshot": deepcopy(case.evidence_json)})
        assert assessed_risk_fields(case, decision(version, "pending"))["last_assessed_snapshot"] is None
        assert not case.trigger_active and case.status == "handled"
    assert case.history_json[:len(original_history)] == original_history
    # A new independent decision can establish a current risk after the reset.
    confirmed = assessed_risk_fields(case, decision("v4"))
    assert confirmed["last_assessed_snapshot"]["event_version_id"] == "v4"
    assert confirmed["last_assessed_snapshot"]["status"] == "active"


def test_explicit_empty_retained_decision_does_not_hide_exact_current_assessment():
    case = current_case()
    case.evidence_json["last_assessed_snapshot"] = None
    for recover_history in (False, True):
        result = risk_state_projection(case, recover_history=recover_history)
        assert result["last_assessed_snapshot"]["event_version_id"] == "v1"
        assert result["last_assessed_snapshot"]["status"] == "active"


@pytest.mark.parametrize("explicit_none", [False, True])
def test_migration_preserves_original_history_and_matches_live_projection(monkeypatch, explicit_none):
    path = Path(__file__).parents[1] / "alembic/versions/20261012_0071_risk_assessment_continuity.py"
    spec = importlib.util.spec_from_file_location("risk_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    case = legacy_case()
    if explicit_none:
        case = current_case()
        case.evidence_json = {**case.evidence_json, **assessed_risk_fields(case, decision()), "event_version_id": "nav-version"}
    original = deepcopy(case.evidence_json)
    table = Table("risk_case", MetaData(), Column("case_id", String, primary_key=True), Column("signal", String),
        Column("title", String), Column("body", String), Column("evidence_json", JSON), Column("history_json", JSON))
    engine = create_engine("sqlite://")
    table.create(engine)
    with engine.begin() as connection:
        connection.execute(table.insert().values(case_id=case.case_id, signal="sector:test", title=case.title, body=case.body,
            evidence_json=original, history_json=case.history_json))
        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
        migration.upgrade()
        row = connection.execute(select(table)).one()
        expected = risk_state_projection(case, recover_history=True)
        assert row.evidence_json == {**original, **expected}
        assert row.history_json == case.history_json
        migration.downgrade()
        assert connection.execute(select(table)).one().evidence_json == row.evidence_json
        migration.upgrade()
        assert connection.execute(select(table)).one().evidence_json == row.evidence_json


@pytest.mark.parametrize("follow_up", ["none", "resolved"])
def test_real_publication_ending_research_followup_does_not_resolve_assessed_risk(client, monkeypatch, follow_up):
    from sqlalchemy import select
    from watchlist_app.db.models.workbench import RiskCase
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.services import sector_research as service
    from watchlist_app.services.risk_alerts import important_risk_alerts
    from .test_sector_research import seed_sector, bind_policy_theme, add_sources, result
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, ["xlk"])
        theme = bind_policy_theme(session, run)
        add_sources(run)
        service.apply_result(session, run, result(theme=theme, sources=["web-one"], direction="risk", impact_level="major",
            impact_analysis="政策限制改变现金流可用性与期限安排", risk_channels=["liquidity"]))
        session.flush()
        case = session.scalar(select(RiskCase).where(RiskCase.signal == "sector:new-policy"))
        old_version = case.evidence_json["event_version_id"]
        assessment = decision(old_version)
        case.evidence_json = {**case.evidence_json, **assessed_risk_fields(case, assessment), "risk_assessment": assessment}
        case.status, case.trigger_active = "open", True
        session.commit()
        service.apply_result(session, run, result(theme=theme, sources=["web-one"], direction="risk", impact_level="major",
            action="resolved" if follow_up == "resolved" else "updated", follow_up=follow_up, next_watch="",
            impact_analysis="政策限制改变现金流可用性与期限安排", risk_channels=["liquidity"], body="研究跟踪阶段结束，风险尚待独立核实解除。"))
        session.commit()
        assert case.evidence_json["follow_up"] == follow_up
        assert case.status == "open" and case.trigger_active
        alert = next(row for row in important_risk_alerts(session)["alerts"] if row["case_id"] == case.case_id)
        assert alert["event_version_id"] == old_version and alert["alert_state"] == "assessment_pending"
