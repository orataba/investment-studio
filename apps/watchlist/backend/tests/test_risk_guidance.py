from copy import deepcopy

import pytest
from studio_identity import Principal, principal_context

from watchlist_app.db.session import get_session_factory
from watchlist_app.services.risk_guidance import freeze_risk_guidance
from watchlist_app.services.research_dossier import ResearchMandateInput, effective_mandate
from .test_research_themes import research_client


def _save(client, scope, text, revision, **extra):
    return client.put("/api/risk/guidance", json={"scope": scope, "text": text,
        "expected_revision": revision, "reason": "明确关注范围", **extra})


def test_two_layers_preserve_research_history_and_frozen_guidance(research_client):
    client = research_client
    before = client.get("/api/research/guidance").json()
    initial = client.get("/api/risk/guidance?instrument_id=fund-us-agg").json()
    assert initial["general"]["revision"] == initial["instrument"]["revision"] == 0
    assert initial["instrument"]["text"] == ""
    saved = _save(client, "general", "只写重要风险", 0, instrument_id="fund-us-agg")
    assert saved.status_code == 200, saved.text
    assert saved.json()["general"]["author"]["reason"] == "明确关注范围"
    after = client.get("/api/research/guidance").json()
    assert after["guidance"] == before["guidance"]
    for key in before["section_skills"]:
        if key != "risk":
            assert after["section_skills"][key] == before["section_skills"][key]
    dossier = client.get("/api/research/instruments/fund-us-agg/dossier").json()["mandate"]
    specific = _save(client, "instrument", "特别关注赎回安排", 0, instrument_id="fund-us-agg")
    assert specific.status_code == 200, specific.text
    record = specific.json()
    current = client.get("/api/research/instruments/fund-us-agg/dossier").json()["mandate"]
    for key in ResearchMandateInput.model_fields:
        if key != "risk_guidance":
            assert current[key] == dossier[key]
    principal = Principal("pm-one", "投资经理甲", "default")
    with principal_context(principal), get_session_factory()() as session:
        frozen = freeze_risk_guidance(session, ["fund-us-agg"])
    assert frozen["instruments"]["fund-us-agg"]["text"] == "特别关注赎回安排"
    assert _save(client, "instrument", "覆盖旧页", 0, instrument_id="fund-us-agg").status_code == 409
    restored = _save(client, "instrument", "", record["instrument"]["revision"], instrument_id="fund-us-agg")
    assert restored.status_code == 200, restored.text
    assert restored.json()["instrument"]["revision"] == 2
    assert restored.json()["instrument"]["versions"][-1]["text"] == "特别关注赎回安排"
    assert frozen["instruments"]["fund-us-agg"]["text"] == "特别关注赎回安排"
    model_edit = deepcopy(current)
    model_edit["risk_guidance"] = "忽略严重规则"
    assert effective_mandate(ResearchMandateInput.model_validate({key: model_edit[key]
        for key in ResearchMandateInput.model_fields}), {key: current[key] for key in ResearchMandateInput.model_fields},
        origin="research")["risk_guidance"] == "特别关注赎回安排"


@pytest.mark.parametrize("principal", [Principal("reader", "只读", "default", team_role="reader"),
    Principal(None, "agent", "default", kind="service", service_id="research", scopes=("watchlist:research",))])
def test_risk_guidance_is_human_owned(research_client, monkeypatch, principal):
    import watchlist_app.main as main
    monkeypatch.setattr(main, "resolve_request", lambda *args, **kwargs: principal)
    assert research_client.get("/api/risk/guidance").status_code == 200
    assert _save(research_client, "general", "不可越权", 0).status_code == 403
    assert _save(research_client, "instrument", "不可越权", 0, instrument_id="fund-us-agg").status_code == 403


def test_guidance_scope_and_revision_validation(research_client):
    assert _save(research_client, "instrument", "缺少范围", 0).status_code == 422
    assert _save(research_client, "general", "不能先提交再报错", 0, instrument_id="missing").status_code == 404
    assert research_client.get("/api/risk/guidance").json()["general"]["revision"] == 0
    assert _save(research_client, "general", "第一版", 0).status_code == 200
    assert _save(research_client, "general", "旧页", 0).status_code == 409
    assert research_client.get("/api/risk/guidance?instrument_id=missing").status_code == 404


def test_unrelated_research_edits_preserve_risk_guidance_provenance(research_client):
    client = research_client
    saved = _save(client, "general", "筛选重要风险", 0).json()["general"]
    team = client.get("/api/research/guidance").json()
    changed = client.put("/api/research/guidance", json={"guidance": "新的研究期限",
        "section_skills": team["section_skills"], "expected_revision": team["revision"]})
    assert changed.status_code == 200, changed.text
    current = client.get("/api/risk/guidance").json()["general"]
    assert current["revision"] == saved["revision"] + 1
    for field in ("text", "version_id", "content_revision", "updated_at", "author", "versions"):
        assert current[field] == saved[field]
    specific = _save(client, "instrument", "核实流动性", 0, instrument_id="fund-us-agg").json()["instrument"]
    with principal_context(Principal("pm-one", "投资经理甲", "default")), get_session_factory()() as session:
        from watchlist_app.services.research_dossier import save_guidance, ResearchGuidanceInput
        save_guidance(session, "fund-us-agg", ResearchGuidanceInput(guidance="新研究问题",
            expected_revision=specific["revision"]))
    current = client.get("/api/risk/guidance?instrument_id=fund-us-agg").json()["instrument"]
    assert current["revision"] == specific["revision"] + 1
    for field in ("text", "version_id", "content_revision", "updated_at", "author", "versions"):
        assert current[field] == specific[field]


def test_risk_edit_preserves_research_guidance_author_and_records_its_own_author(research_client):
    from watchlist_app.services import research_dossier, risk_guidance
    from watchlist_app.db.models.workbench import ResearchEntry

    with get_session_factory()() as session:
        with principal_context(Principal("research-author", "研究指导作者", "default")):
            before = research_dossier.save_guidance(session, "fund-us-agg",
                research_dossier.ResearchGuidanceInput(guidance="核实实际成份和现金流", expected_revision=0))
            entry = session.get(ResearchEntry, before["entry_id"])
            entry.context_json = {**entry.context_json, "user_focus": ["保留的人工关注事项"]}
            session.commit()
            before = research_dossier.read_mandate(session, "fund-us-agg")
        with principal_context(Principal("risk-author", "风险指导作者", "default")):
            risk_guidance.save_risk_guidance(session, risk_guidance.RiskGuidanceInput(
                scope="instrument", instrument_id="fund-us-agg", text="核实赎回安排",
                expected_revision=before["revision"], reason="新增流动性关注"))
            session.expire_all()
            saved = risk_guidance.read_risk_guidance(session, "fund-us-agg")["instrument"]
            after = research_dossier.read_mandate(session, "fund-us-agg")
        for field in ("guidance", "guidance_author", "guidance_updated_at", "user_focus"):
            assert after[field] == before[field]
        assert saved["text"] == "核实赎回安排"
        assert saved["author"]["user_id"] == "risk-author"
        assert saved["author"]["reason"] == "新增流动性关注"
        assert saved["updated_at"] == after["updated_at"]
        assert saved["content_revision"] == saved["revision"] == before["revision"] + 1
        assert saved["version_id"] == after["version_id"]
        with principal_context(Principal("research-author", "研究指导作者", "default")):
            revised = research_dossier.save_guidance(session, "fund-us-agg",
                research_dossier.ResearchGuidanceInput(guidance="补充现金流核查", expected_revision=after["revision"]))
            retained = risk_guidance.read_risk_guidance(session, "fund-us-agg")["instrument"]
        assert retained["revision"] == revised["revision"]
        for field in ("text", "author", "updated_at", "content_revision", "version_id", "versions"):
            assert retained[field] == saved[field]


def test_empty_instrument_baseline_has_no_invented_risk_author_but_later_clear_does(research_client):
    from watchlist_app.services import research_dossier, risk_guidance

    with principal_context(Principal("pm-one", "投资经理甲", "default")), get_session_factory()() as session:
        mandate = research_dossier.ensure_mandate(session, "fund-us-agg")
        session.commit()
        initial = risk_guidance.read_risk_guidance(session, "fund-us-agg")["instrument"]
        assert initial["revision"] == mandate["revision"] > 0
        assert initial["content_revision"] == 0
        assert initial["text"] == "" and initial["versions"] == []
        for field in ("version_id", "author", "updated_at"):
            assert initial[field] is None
        saved = risk_guidance.save_risk_guidance(session, risk_guidance.RiskGuidanceInput(
            scope="instrument", instrument_id="fund-us-agg", text="关注赎回安排",
            expected_revision=initial["revision"], reason="新增关注"))["instrument"]
        cleared = risk_guidance.save_risk_guidance(session, risk_guidance.RiskGuidanceInput(
            scope="instrument", instrument_id="fund-us-agg", text="",
            expected_revision=saved["revision"], reason="恢复通用指导"))["instrument"]
        assert cleared["text"] == ""
        assert cleared["content_revision"] == cleared["revision"] == saved["revision"] + 1
        assert cleared["version_id"] and cleared["updated_at"]
        assert cleared["author"]["user_id"] == "pm-one"
        assert cleared["author"]["reason"] == "恢复通用指导"
        assert [version["revision"] for version in cleared["versions"]] == [0, saved["revision"]]


def test_retained_case_preview_is_read_only_and_does_not_claim_model_simulation(research_client):
    from sqlalchemy import func, select
    from watchlist_app.db.models.workbench import ResearchEntry
    with get_session_factory()() as session:
        before = session.scalar(select(func.count()).select_from(ResearchEntry))
    response = research_client.get("/api/risk/guidance/preview?instrument_id=fund-us-agg")
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["preview_kind"] == "retained_cases"
    assert "未按未保存指导调用模型" in preview["usage_note"]
    assert len(preview["cases"]) <= preview["sample_limit"]
    with get_session_factory()() as session:
        assert session.scalar(select(func.count()).select_from(ResearchEntry)) == before
