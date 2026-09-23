from copy import deepcopy
from datetime import date, timedelta

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
    return {"taxonomies": [{"taxonomy_id": "tax", "name": "Risk", "root_allocation_basis": "risk_budget", "status": "active"}], "taxonomy_nodes": [
        {"taxonomy_id": "tax", "taxonomy_node_id": key, "node_name": key, "parent_taxonomy_node_id": None, "status": "active"}
        for key in ["macro", "gold"]], "taxonomy_assignments": [
        {"taxonomy_id": "tax", "taxonomy_node_id": key, "target_scope": "instrument", "target_entity_id": iid, "status": "active"}
        for key, iid in [("macro", "alpha"), ("gold", "beta")]], "target_sets": [
        {"target_set_id": "saa", "taxonomy_id": "tax", "target_set_type": "saa", "name": "SAA", "status": "active",
         "comparator_taxonomy_node_id": None}], "target_set_lines": [
        {"target_set_id": "saa", "target_member_type": "taxonomy_node", "target_member_id": key, "target_value": 0.5}
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


def test_historical_risk_context_uses_requested_holding_date(monkeypatch):
    from types import SimpleNamespace
    from portfolio_app.services import holdings_workspace as workspace_routes
    from portfolio_app.api.routes import taxonomies
    calls = []
    selected = date(2026, 3, 10)
    def read_holdings(**kwargs):
        calls.append(kwargs)
        value = workspace()
        value['as_of_date'] = kwargs['as_of_date'].isoformat()
        return value
    monkeypatch.setattr(workspace_routes, 'holdings_workspace', read_holdings)
    monkeypatch.setattr(taxonomies, 'get_portfolio_taxonomies', lambda *args, **kwargs: SimpleNamespace(model_dump=lambda **kwargs: catalog()))
    from portfolio_app.services import concentration, tail_risk
    def read_extra(portfolio_id, *, workspace):
        assert portfolio_id == workspace['portfolio_id']
        return {'as_of_date': workspace['as_of_date'], 'sources': []}
    monkeypatch.setattr(concentration, 'read_portfolio_concentration', read_extra)
    monkeypatch.setattr(tail_risk, 'read_portfolio_tail_risk', read_extra)
    result = service.read_portfolio_risk_context('p', as_of_date=selected)
    assert calls == [{'portfolio_id': 'p', 'as_of_date': selected, 'include_details': True}]
    assert result['as_of_date'] == result['workspace']['as_of_date'] == selected.isoformat()
    assert result['concentration']['as_of_date'] == result['tail_risk']['as_of_date'] == selected.isoformat()
    assert all(source['end_date'] == selected.isoformat() for source in result['sources'])
    assert "planning_as_of_date" not in result
    assert {row["group_id"] for row in result["targets"]["rows"]} == {"macro", "gold", "instrument:alpha", "instrument:beta"}
    target_source = next(source for source in result["sources"] if source["source_id"].endswith(":targets"))
    assert "当前保存配置" in target_source["date_basis"]


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


def test_only_global_rc_targets_are_compared_and_effective_tactical_keeps_source():
    result = service.project_portfolio_risk(workspace(), catalog())
    targets = result["targets"]["rows"]
    assert targets and all(row["dimension"] == "risk_budget" for row in targets)
    assert all(row["risk_attribution_scope"] == "portfolio" for row in targets)
    tactical = [row for row in targets if row["target_set_type"] == "taa"]
    assert tactical and all(row["source_stage"] in {"saa", "single_member"} for row in tactical)
    assert all(row["source_stage"] == "saa" for row in tactical if row["scope_node_id"] is None)
    config = catalog()
    config["taxonomies"][0]["root_allocation_basis"] = "weight"
    capital_root = service.project_portfolio_risk(workspace(), config)
    assert capital_root["targets"]["rows"] == []


def test_unassigned_exposure_prevents_partial_denominator_target_comparison():
    config = catalog()
    config["taxonomy_assignments"] = config["taxonomy_assignments"][:1]
    result = service.project_portfolio_risk(workspace(), config)
    groups = result["portfolio_metrics"]["groups"]
    assert groups["status"] == "ok"
    assert sum(row["risk_share"] or 0. for row in groups["rows"]) == pytest.approx(1.)
    assert groups["target_comparison_available"] is False
    assert result["targets"]["rows"]
    assert all(row["current"] is None and row["gap_pp"] is None for row in result["targets"]["rows"])


def test_known_zero_security_exposure_does_not_disable_other_members_risk_targets():
    current = workspace()
    current["rows"][1]["market_value_base"] = 0.0
    enrich_holdings_forward_risk(current, as_of_date=AS_OF_DATE, calculation_frequency="daily", risk_policy=_risk_policy())
    assert current["forward_risk"]["status"] == "ok"
    assert current["rows"][1]["forward_risk_status"] == "no_exposure"
    result = service.project_portfolio_risk(current, catalog())
    groups = result["portfolio_metrics"]["groups"]
    assert groups["target_comparison_available"] is True
    assert next(row for row in groups["rows"] if row["group_id"] == "gold")["risk_share"] == 0.0
    assert next(row for row in result["targets"]["rows"] if row["group_id"] == "gold")["current"] == 0.0
    assert next(row for row in result["targets"]["rows"] if row["group_id"] == "macro")["current"] == pytest.approx(1.0)


def test_held_unmodeled_member_is_not_zero_and_multiple_taxonomies_require_explicit_context():
    current = workspace()
    current["rows"][0].update(risk_eligible=False, forward_risk_status="outside_model", forward_risk_share=None)
    result = service.project_portfolio_risk(current, catalog())
    assert all(row["current"] is None for row in result["targets"]["rows"])
    config = catalog()
    config["taxonomies"].append({"taxonomy_id": "other", "name": "Other", "status": "active"})
    unspecified = service.project_portfolio_risk(workspace(), config)
    assert unspecified["portfolio_metrics"]["groups"]["taxonomy_id"] is None
    assert unspecified["targets"]["rows"] == []
    selected = service.project_portfolio_risk(workspace(), config, taxonomy_id="tax")
    assert selected["portfolio_metrics"]["groups"]["taxonomy_id"] == "tax"
    assert selected["targets"]["rows"]


def test_groups_outside_market_risk_model_are_not_reported_as_zero_risk():
    current, previous = workspace(), workspace(previous=True)
    for value in [current, previous]:
        value["rows"].extend([
            {"position_reference_id": "cash:CNY", "holding_category": "cash_and_settlement", "holding_kind": "cash",
             "instrument_core": {"instrument_id": "cash:CNY", "instrument_type": "cash"},
             "market_value_base": 100_000, "risk_eligible": False},
            {"position_reference_id": "fcn", "holding_category": "derivatives", "holding_kind": "derivative_contract",
             "instrument_core": {"instrument_name": "FCN"}, "derivative_contract_id": "fcn",
             "market_value_base": 200_000, "risk_eligible": False},
        ])
    result = service.project_portfolio_risk(current, catalog(), previous)
    groups = {row["group_id"]: row for row in result["portfolio_metrics"]["groups"]["rows"]}
    for group_id in ["cash_bucket:__cash__", "derivative_bucket:__derivatives__"]:
        assert groups[group_id]["risk_status"] == "outside_model"
        assert groups[group_id]["risk_share"] is None
        assert groups[group_id]["contribution_to_variance"] is None
        assert groups[group_id]["risk_budget_share"] is None
    assert groups["cash_bucket:__cash__"]["weight"] == .1
    assert groups["derivative_bucket:__derivatives__"]["weight"] == .2
    assert {row["group_id"] for row in result["comparisons"]["risk_group_changes"]} == {"macro", "gold"}
    assert sum(row["risk_share"] for row in groups.values() if row["risk_share"] is not None) == pytest.approx(1)


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
