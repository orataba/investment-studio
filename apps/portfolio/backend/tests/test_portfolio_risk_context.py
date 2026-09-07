from copy import deepcopy
from datetime import timedelta

import pytest

from portfolio_app.services import portfolio_risk_context as service
from portfolio_app.services.risk_model import enrich_holdings_forward_risk
from .test_risk_model import AS_OF_DATE, _holding, _return_points, _risk_policy, _workspace


def workspace(*, previous=False):
    as_of = AS_OF_DATE - timedelta(days=1) if previous else AS_OF_DATE
    rows = [_holding("alpha", 0.3 if previous else 0.6, points=_return_points()),
            _holding("beta", 0.7 if previous else 0.4, points=_return_points(2))]
    for row in rows:
        row.update(position_reference_id=row["instrument_core"]["instrument_id"], holding_category="securities", holding_kind="position")
    value = _workspace(rows, portfolio_id="p", portfolio_name="组合", as_of_date=as_of.isoformat(),
                       totals={"nav": 1_000_000}, quality_warnings=[])
    return enrich_holdings_forward_risk(value, as_of_date=as_of, calculation_frequency="daily", risk_policy=_risk_policy())


def catalog():
    return {"default_planning_taxonomy_id": "tax", "taxonomy_nodes": [
        {"taxonomy_id": "tax", "taxonomy_node_id": key, "node_name": key, "parent_taxonomy_node_id": None, "status": "active"}
        for key in ["macro", "gold"]], "taxonomy_assignments": [
        {"taxonomy_id": "tax", "taxonomy_node_id": key, "target_scope": "instrument", "target_entity_id": iid, "status": "active"}
        for key, iid in [("macro", "alpha"), ("gold", "beta")]], "target_sets": [
        {"target_set_id": "saa", "taxonomy_id": "tax", "target_set_type": "saa", "name": "SAA", "status": "active",
         "comparator_taxonomy_node_id": None, "weight_enabled": True, "risk_budget_enabled": True}], "target_set_lines": [
        {"target_set_id": "saa", "target_member_type": "taxonomy_node", "target_member_id": key, "target_weight": 0.5, "target_risk_share": 0.5}
        for key in ["macro", "gold"]]}


def test_reuses_production_rc_and_covariance_with_same_workspace_and_real_dated_comparison():
    current, previous = workspace(), workspace(previous=True)
    current["risk_basis"] = {"resolved_frequency": "daily", "gap_instrument_ids": ["alpha"]}
    original = deepcopy(current)
    result = service.project_portfolio_risk(current, catalog(), previous)
    assert current == original
    assert result["workspace"]["as_of_date"] == result["as_of_date"] == AS_OF_DATE.isoformat()
    assert result["portfolio_metrics"]["risk_basis"] == result["workspace"]["risk_basis"] == current["risk_basis"]
    assert result["portfolio_metrics"]["forward_risk"]["status"] == "ok"
    assert "全样本" in result["portfolio_metrics"]["coverage_note"]
    assert "instrument_return_series_all" not in result["workspace"]["rows"][0]
    macro = next(row for row in result["portfolio_metrics"]["groups"]["rows"] if row["group_id"] == "macro")
    assert macro["risk_share"] == pytest.approx(current["rows"][0]["forward_risk_share"])
    assert macro["weight"] == pytest.approx(0.6)
    assert sum(row["risk_share"] for row in result["portfolio_metrics"]["groups"]["rows"]) == pytest.approx(1)
    change = next(row for row in result["comparisons"]["risk_group_changes"] if row["group_id"] == "macro")
    assert change["change_pp"] == pytest.approx((current["rows"][0]["forward_risk_share"] - previous["rows"][0]["forward_risk_share"]) * 100)
    assert result["comparisons"]["status"] == "available"
    assert result["portfolio_metrics"]["correlations"]["pairs"][0]["correlation"] == pytest.approx(1)
    assert result["comparisons"]["correlation_changes"][0]["change"] == pytest.approx(0)
    assert all(row["breach"] is None and row["threshold_status"] == "not_configured" for row in result["targets"]["rows"])
    assert {source["detail_path"] for source in result["sources"]} == {"/portfolios/p/risk"}
    assert result["holdings"][0] == {"holding_id": "alpha", "instrument_id": "alpha", "name": "alpha", "detail_path": "/portfolios/p/holdings/alpha"}
    sources = {row["source_id"].rsplit(":", 1)[1]: row for row in result["sources"]}
    assert sources["targets"]["start_date"] is sources["targets"]["frequency"] is None
    assert sources["comparison"]["start_date"] == previous["as_of_date"]
    assert sources["correlations"]["start_date"] == current["forward_risk"]["coverage"]["window_start_date"]


def test_derivative_holding_references_and_sources_keep_contract_scope_and_snapshot_clock(monkeypatch):
    from portfolio_app.services import portfolio_risk_derivatives
    hid = "fcn/local"
    source = {"source_id": "portfolio-derivative:p:fcn/local", "holding_id": hid,
        "start_date": AS_OF_DATE.isoformat(), "end_date": AS_OF_DATE.isoformat(), "detail_path": "/portfolios/p/holdings/fcn%2Flocal"}
    monkeypatch.setattr(portfolio_risk_derivatives, "project_derivative_risk", lambda _workspace: {
        "positions": [{"holding_id": hid, "name": "本地FCN", "detail_path": source["detail_path"]}], "sources": [source]})
    result = service.project_portfolio_risk(workspace(), catalog())
    holding = next(item for item in result["holdings"] if item["holding_id"] == hid)
    assert holding == {"holding_id": hid, "name": "本地FCN", "detail_path": source["detail_path"]}
    projected_source = result["sources"][-1]
    assert projected_source["portfolio_id"] == "p"
    assert projected_source["start_date"] == projected_source["end_date"] == AS_OF_DATE.isoformat()
    assert "报价" in projected_source["date_basis"]
    assert "portfolio_id" not in source


def test_route_reuses_financial_read_generation_contract():
    from portfolio_app.api.financial_read import FinancialReadRoute
    from portfolio_app.api.routes.portfolio_risk_context import router
    assert len(router.routes) == 1
    assert isinstance(router.routes[0], FinancialReadRoute)


@pytest.mark.parametrize("change", ["currency", "model", "date"])
def test_incompatible_currency_model_or_date_does_not_invent_risk_or_correlation_increases(change):
    current, previous = workspace(), workspace(previous=True)
    if change == "currency":
        previous["base_currency"] = "USD"
    elif change == "model":
        previous["forward_risk"]["risk_model"]["lookback_days"] = 90
    else:
        previous["as_of_date"] = current["as_of_date"]
    result = service.project_portfolio_risk(current, catalog(), previous)
    assert result["comparisons"]["status"] == "unavailable"
    assert result["comparisons"]["risk_group_changes"] == result["comparisons"]["correlation_changes"] == []
    assert result["comparisons"]["limitations"]


def test_unavailable_model_and_missing_targets_stay_explicit_without_zero_risk_claim():
    current = workspace()
    current["forward_risk"] = {"status": "unavailable", "errors": ["观察数据不足"]}
    config = catalog()
    config["target_sets"] = []
    result = service.project_portfolio_risk(current, config)
    assert result["portfolio_metrics"]["correlations"] == {"status": "unavailable", "pairs": [], "limitations": ["观察数据不足"]}
    assert all(row["risk_share"] is None for row in result["portfolio_metrics"]["groups"]["rows"])
    assert result["targets"]["status"] == "unavailable" and result["targets"]["rows"] == []
    assert result["comparisons"]["status"] == "unavailable" and result["comparisons"]["limitations"]


@pytest.mark.parametrize("new_position", [True, False])
def test_risk_changes_include_new_and_fully_disposed_categories(new_position):
    current, previous = workspace(), workspace(previous=True)
    reduced = previous if new_position else current
    reduced["rows"] = reduced["rows"][:1]
    enrich_holdings_forward_risk(
        reduced, as_of_date=AS_OF_DATE - timedelta(days=1) if new_position else AS_OF_DATE,
        calculation_frequency="daily", risk_policy=_risk_policy(),
    )
    result = service.project_portfolio_risk(current, catalog(), previous)
    change = next(row for row in result["comparisons"]["risk_group_changes"] if row["group_id"] == "gold")
    assert change["previous" if new_position else "current"] == 0
    assert change["change_pp"] > 0 if new_position else change["change_pp"] < 0
    assert change["instrument_ids"] == change["holding_ids"] == ["beta"]
