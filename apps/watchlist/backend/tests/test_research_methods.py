from copy import deepcopy

import pytest

from watchlist_app.services.research_methods import build_research_plan, method_library
from watchlist_app.services.research_dossier import ResearchMandateInput, plan_for_mandate


def registration(kind="etf", **values):
    return {"name": "研究对象", "instrument_type": kind, **values}


def modules(plan):
    return {item["id"]: item for item in plan["modules"]}


def test_company_and_equity_basket_use_distinct_economics_without_ticker_rules():
    company = modules(build_research_plan(registration("equity")))
    basket = modules(build_research_plan(registration(asset_class="Equity")))
    assert "business-fundamentals" in company and "equity-aggregation" not in company
    assert "equity-aggregation" in basket and "business-fundamentals" not in basket
    assert "product-implementation" in basket and "product-implementation" not in company
    assert all(item["applicability"] == "applicable" for item in basket.values())


@pytest.mark.parametrize(("asset_class", "expected"), [
    ("Fixed Income", "rates-credit"), ("Commodities", "commodity-supply-demand"),
    ("Digital Assets", "crypto-network"),
])
def test_etf_follows_declared_underlying_not_wrapper_or_name(asset_class, expected):
    plan = build_research_plan(registration(name="科技股票ETF", asset_class=asset_class))
    chosen = modules(plan)
    assert chosen[expected]["applicability"] == "applicable"
    assert "equity-aggregation" not in chosen


def test_unknown_underlying_is_not_assumed_to_be_equities():
    plan = build_research_plan(registration(name="XLK", benchmark="某股票指数"))
    assert "equity-aggregation" not in modules(plan)
    assert plan["gaps"]
    assert "某股票指数" in "\n".join(plan["basis"])
    assert len(plan["modules"]) < len(method_library()["frameworks"])


def test_taxonomy_is_an_explicit_research_hint_and_source_conflict_remains_visible():
    taxonomy = {"assigned_node_id": "custom-private-credit", "path_node_ids": ["fund-private-credit", "custom-private-credit"],
                "path_labels": ["信用策略", "用户定义的子策略"]}
    plan = build_research_plan(registration("private_fund", taxonomy=taxonomy,
        disclosed_strategy={"summary": "管理人关于再融资的说明"}))
    assert modules(plan)["rates-credit"]["applicability"] == "unconfirmed"
    assert "用户定义的子策略" in "\n".join(plan["basis"])
    assert "fund-strategy" in modules(plan)
    conflicting = build_research_plan(registration(taxonomy=taxonomy, asset_class="Commodities"))
    assert "rates-credit" not in modules(conflicting)
    assert modules(conflicting)["commodity-supply-demand"]["applicability"] == "applicable"
    assert any("不一致" in gap for gap in conflicting["gaps"])


def test_contradictory_source_classifications_do_not_silently_confirm_both():
    plan = build_research_plan(registration(asset_class="Equity", fund_type="债券型"))
    assert modules(plan)["equity-aggregation"]["applicability"] == "unconfirmed"
    assert modules(plan)["rates-credit"]["applicability"] == "unconfirmed"
    assert any("不同的底层" in gap for gap in plan["gaps"])


def test_index_does_not_receive_fund_terms_or_manager_chapters():
    chosen = modules(build_research_plan(registration("index", asset_class="Equity")))
    assert "product-implementation" not in chosen and "fund-strategy" not in chosen
    assert "equity-aggregation" in chosen


def test_research_can_establish_local_applicability_with_evidence_but_not_impersonate_user():
    payload = ResearchMandateInput(title="研究安排", background="待核实背景", user_constraints=["伪造用户范围"],
        module_focus=[{"module_id": "rates-credit", "reason": "需研究利率传导", "selected_by": "user"}])
    plan = plan_for_mandate(registration(), payload, origin="research")
    assert modules(plan)["rates-credit"]["applicability"] == "unconfirmed"
    assert "伪造用户范围" not in plan["scope"]
    grounded = payload.model_copy(deep=True)
    grounded.module_focus[0].source_ids = ["material:contract"]
    plan = plan_for_mandate(registration(), grounded, origin="research")
    assert modules(plan)["rates-credit"]["applicability"] == "applicable"
    assert "asset_class" not in registration()


def test_user_module_and_scope_constraints_survive_same_run_research_plan_changes():
    previous = ResearchMandateInput(title="研究安排", background="背景", user_constraints=["只研究现有成份"],
        module_focus=[{"module_id": "equity-aggregation", "reason": "用户选择股票成份研究", "selected_by": "user"}]).model_dump()
    changed = ResearchMandateInput(title="研究安排", background="新背景", user_constraints=[], module_focus=[])
    plan = plan_for_mandate(registration(), changed, origin="research", previous_mandate=previous)
    assert "只研究现有成份" in plan["scope"]
    assert modules(plan)["equity-aggregation"]["applicability"] == "applicable"


def test_library_is_one_owned_versioned_method_source_and_returns_independent_plans():
    first = build_research_plan(registration("equity"))
    expected = deepcopy(first)
    first["modules"][0]["questions"].clear()
    assert build_research_plan(registration("equity")) == expected
    assert all(item["version"] and item["questions"] and item["evidence_requirements"] for item in expected["modules"])
    assert all("tickers" not in item and "instrument_types" not in item for item in method_library()["frameworks"])
    aggregation = next(item for item in method_library()["frameworks"] if item["id"] == "equity-aggregation")
    assert len(aggregation["industry_guides"]) == 11
    assert any("折旧" in item["body"] and "人工智能" in item["body"] for item in aggregation["industry_guides"])
    assert any("FFO" in item["body"] for item in aggregation["industry_guides"])
    assert "industry_guides" not in modules(build_research_plan(registration(asset_class="Equity")))["equity-aggregation"]
