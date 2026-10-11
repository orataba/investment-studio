from copy import deepcopy

import pytest

from watchlist_app.services.research_methods import analyst_guidance, build_research_plan, method_library
from watchlist_app.services.research_dossier import ResearchMandateInput, plan_for_mandate


def registration(kind="etf", **values):
    return {"name": "研究对象", "instrument_type": kind, **values}


def modules(plan):
    return {item["id"]: item for item in plan["modules"]}


@pytest.mark.parametrize("kind", ["equity", "etf", "index", "public_fund", "private_fund", "crypto"])
def test_identity_and_taxonomy_do_not_assign_a_required_chapter_grid(kind):
    plan = build_research_plan(registration(kind, asset_class="Equity", taxonomy={
        "assigned_node_id": "fund-private-credit", "path_labels": ["信用策略"]}))
    assert plan["mode"] == "question_driven" and plan["modules"] == []
    assert "Equity" in "\n".join(plan["basis"]) and "信用策略" in "\n".join(plan["basis"])
    assert "不是实际敞口的证明" in "\n".join(plan["basis"])


def test_unknown_underlying_and_disclosed_strategy_keep_their_evidence_limits():
    plan = build_research_plan(registration("private_fund", name="股票收益基金", benchmark="某股票指数",
        disclosed_strategy={"summary": "管理人的策略说明"}))
    assert plan["modules"] == [] and plan["gaps"]
    assert "某股票指数" in "\n".join(plan["basis"])
    assert "原作者、日期和证据范围" in "\n".join(plan["basis"])


def test_research_accepts_a_local_method_without_library_registration_or_user_impersonation():
    payload = ResearchMandateInput(title="研究安排", background="待核实背景", guidance="伪造用户要求",
        module_focus=[{"module_id": "refinancing-feedback", "reason": "检验融资与需求的相互作用", "selected_by": "user"}])
    plan = plan_for_mandate(registration(), payload, origin="research")
    local = modules(plan)["refinancing-feedback"]
    assert local["reference_kind"] == "local_method" and local["version"] is None
    assert local["applicability"] == "optional"
    assert plan["guidance"] == "" and "伪造用户要求" not in analyst_guidance(plan)


def test_user_guidance_survives_research_working_note_changes():
    previous = ResearchMandateInput(title="研究安排", background="背景", guidance="只研究现有成份的资本回报").model_dump()
    changed = ResearchMandateInput(title="研究安排", background="新背景", guidance="扩大至全部科技公司",
        module_focus=[{"module_id": "new-hypothesis", "reason": "本轮提出的新问题"}])
    plan = plan_for_mandate(registration(), changed, origin="research", previous_mandate=previous)
    assert plan["guidance"] == previous["guidance"]
    assert "只研究现有成份" in analyst_guidance(plan)
    assert "扩大至全部科技公司" not in analyst_guidance(plan)


def test_explicit_shared_references_bind_versions_and_return_independent_content():
    focus = [{"module_id": "equity-aggregation", "reason": "检验已披露成份的盈利驱动"}]
    first = build_research_plan(registration(), module_focus=focus)
    expected = deepcopy(first)
    first["modules"][0]["questions"].clear()
    assert build_research_plan(registration(), module_focus=focus) == expected
    method = expected["modules"][0]
    assert method["version"] and method["questions"] and method["evidence_requirements"]
    assert method["reference_kind"] == "shared_method" and "industry_guides" not in method
    aggregation = next(item for item in method_library()["frameworks"] if item["id"] == "equity-aggregation")
    assert len(aggregation["industry_guides"]) == 11
    assert any("FFO" in item["body"] for item in aggregation["industry_guides"])
