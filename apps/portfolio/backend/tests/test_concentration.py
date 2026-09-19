from copy import deepcopy
from datetime import date

import pytest
from pydantic import ValidationError
from fastapi import HTTPException

from portfolio_app.api.concentration_contracts import ConcentrationSettingsUpdate
from portfolio_app.services.concentration import project_portfolio_concentration
from portfolio_app.services.concentration_settings import read_concentration_settings


def security(iid="A", value=100_000, *, account="one"):
    return {"instrument_core": {"instrument_id": iid, "instrument_name": iid, "instrument_type": "equity", "currency": "USD"},
            "position_reference_id": iid, "holding_kind": "position", "account_id": account,
            "quantity": 100 if value is None or value >= 0 else -100, "market_value_base": value, "risk_eligible": False}


def fcn(*, quantity=2, currency="USD", rate=None):
    return {"position_reference_id": "F", "derivative_contract_id": "F", "holding_kind": "derivative_contract",
            "quantity": quantity, "market_value_base": 140_000, "fx_rate_to_base": rate,
            "derivative_contract": {"derivative_contract_id": "F", "contract_name": "FCN F", "contract_type": "fcn", "currency": currency,
                                    "terms": {"notional": 100_000, "underlyings": [{"instrument_id": "A"}, {"instrument_id": "B"}]}}}


def workspace(rows):
    return {"portfolio_id": "p", "as_of_date": "2026-09-08", "base_currency": "USD", "totals": {"nav": 1_000_000}, "rows": rows}


def catalog():
    return {"taxonomies": [{"taxonomy_id": "industry", "name": "Industry"}, {"taxonomy_id": "country", "name": "Country"}],
            "taxonomy_nodes": [
                {"taxonomy_id": "industry", "taxonomy_node_id": "tech", "node_name": "Technology"},
                {"taxonomy_id": "industry", "taxonomy_node_id": "hardware", "node_name": "Hardware", "parent_taxonomy_node_id": "tech"},
                {"taxonomy_id": "country", "taxonomy_node_id": "US", "node_name": "US"},
                {"taxonomy_id": "country", "taxonomy_node_id": "CN", "node_name": "CN"}],
            "taxonomy_assignments": [{"taxonomy_id": tid, "target_scope": "instrument", "target_entity_id": iid, "taxonomy_node_id": node}
                                     for tid, iid, node in [("industry", "A", "hardware"), ("industry", "B", "tech"), ("country", "A", "US"), ("country", "B", "CN")]]}


def group(result, scope, entity, tid=None):
    selected = next(row for row in result["scopes"] if row["scope"] == scope and row["taxonomy_id"] == tid)
    return next(row for row in selected["rows"] if row["entity_id"] == entity)


def rule(scope, *, tid=None, entity=None, limit=0.2, watch=None, enabled=True, rule_id="r"):
    return {"scope": scope, "taxonomy_id": tid, "entity_id": entity, "limit_weight": limit, "watch_weight": watch, "enabled": enabled, "rule_id": rule_id}


def test_remaining_principal_is_allocated_once_and_not_added_to_direct_stock_limits():
    original = workspace([security(), fcn()])
    before = deepcopy(original)
    result = project_portfolio_concentration(original, catalog())
    assert original == before
    assert group(result, "security", "A")["weight"] == 0.1
    assert group(result, "fcn", "F")["weight"] == 0.2
    assert group(result, "taxonomy", "tech", "industry")["weight"] == 0.3
    assert group(result, "taxonomy", "hardware", "industry")["weight"] == 0.2
    assert group(result, "taxonomy", "US", "country")["weight"] == 0.2
    assert group(result, "taxonomy", "CN", "country")["weight"] == 0.1
    assert result["status"] == "complete"


def test_account_gross_exposure_is_not_lost_to_workspace_netting():
    result = project_portfolio_concentration(workspace([security(value=70_000)]), catalog(),
        holding_rows=[security(value=100_000), security(value=-30_000, account="two")])
    assert group(result, "security", "A")["weight"] == 0.13
    assert len(group(result, "security", "A")["sources"]) == 2


def test_missing_account_snapshots_cannot_turn_live_exposure_into_zero():
    with pytest.raises(HTTPException) as error:
        project_portfolio_concentration(workspace([security()]), catalog(), holding_rows=[])
    assert error.value.status_code == 503


def test_custom_allocation_and_fx_are_applied_to_nominal_not_cost():
    settings = {"revision": 1, "rules": [], "fcn_allocations": [{"contract_id": "F", "method": "custom", "weights": [
        {"instrument_id": "A", "weight": 0.75}, {"instrument_id": "B", "weight": 0.25}]}]}
    result = project_portfolio_concentration(workspace([fcn(currency="HKD", rate=0.125)]), catalog(), settings)
    assert group(result, "fcn", "F")["exposure_base"] == 25_000
    assert group(result, "taxonomy", "US", "country")["exposure_base"] == 18_750
    assert group(result, "taxonomy", "CN", "country")["exposure_base"] == 6_250


def test_options_and_closed_fcns_are_excluded_without_removing_nav():
    option = {**fcn(), "derivative_contract": {"contract_type": "option"}}
    result = project_portfolio_concentration(workspace([security(), fcn(quantity=0), option]), catalog())
    assert result["nav"] == 1_000_000
    assert result["excluded_option_positions"] == 1
    assert not result["scopes"][1]["rows"]
    assert group(result, "security", "A")["weight"] == 0.1


def test_limits_override_defaults_and_missing_data_never_claims_within_limit():
    settings = {"revision": 1, "fcn_allocations": [], "rules": [rule("security", limit=0.15),
        rule("security", entity="A", limit=0.1, rule_id="A"), rule("fcn", limit=0.3, watch=0.15, rule_id="F")]}
    result = project_portfolio_concentration(workspace([security(), fcn()]), catalog(), settings)
    assert group(result, "security", "A")["status"] == "breached"
    assert group(result, "fcn", "F")["status"] == "watch"
    missing = project_portfolio_concentration(workspace([fcn(currency="HKD")]), catalog(), settings)
    assert group(missing, "fcn", "F")["status"] == "unavailable"
    assert group(missing, "fcn", "F")["exposure_base"] is None


def test_incomplete_classification_keeps_unclassified_and_known_breach():
    partial = catalog()
    partial["taxonomy_assignments"] = [row for row in partial["taxonomy_assignments"] if row["target_entity_id"] != "B"]
    settings = {"revision": 1, "rules": [rule("taxonomy", tid="country", limit=0.15)], "fcn_allocations": []}
    result = project_portfolio_concentration(workspace([security(), fcn()]), partial, settings)
    us = group(result, "taxonomy", "US", "country")
    assert us["status"] == "breached"
    assert us["weight"] is None and us["lower_bound_weight"] == 0.2
    assert group(result, "taxonomy", "CN", "country")["status"] == "unavailable"
    assert group(result, "taxonomy", "unassigned:country", "country")["weight"] == 0.1
    assert group(result, "taxonomy", "unassigned:country", "country")["status"] == "unconfigured"


def test_disabled_scope_preserves_but_disables_specific_limits():
    settings = {"revision": 1, "rules": [rule("security", enabled=False), rule("security", entity="A", limit=0.01, rule_id="A")], "fcn_allocations": []}
    result = project_portfolio_concentration(workspace([security()]), catalog(), settings)
    assert group(result, "security", "A")["status"] == "unconfigured"
    settings["rules"][0]["enabled"] = True
    result = project_portfolio_concentration(workspace([security()]), catalog(), settings)
    assert group(result, "security", "A")["status"] == "breached"


@pytest.mark.parametrize("rules,allocations", [
    ([rule("taxonomy")], []), ([rule("security", watch=0.3)], []),
    ([rule("security"), rule("security", rule_id="other")], []),
    ([], [{"contract_id": "F", "method": "custom", "weights": [{"instrument_id": "A", "weight": 0.7}]}]),
])
def test_invalid_limits_and_allocations_are_rejected(rules, allocations):
    with pytest.raises(ValidationError):
        ConcentrationSettingsUpdate(expected_revision=0, effective_from=date(2026, 9, 8), rules=rules, fcn_allocations=allocations)


def test_settings_are_dated_and_revision_checked(client):
    path = "/api/portfolios/investment-studio/concentration/settings"
    initial = client.get(path)
    assert initial.status_code == 200, initial.text
    assert initial.json()["revision"] == 0
    first = {"expected_revision": 0, "effective_from": "2026-04-01", "rules": [rule("security")], "fcn_allocations": []}
    saved = client.put(path, json=first)
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 1
    assert client.put(path, json=first).status_code == 409
    assert read_concentration_settings("investment-studio", as_of_date=date(2026, 3, 31))["revision"] == 0
    assert read_concentration_settings("investment-studio", as_of_date=date(2026, 4, 2))["revision"] == 1
    second = {**first, "expected_revision": 1, "effective_from": "2026-05-01", "rules": [rule("security", limit=0.25)]}
    assert client.put(path, json=second).status_code == 200
    assert read_concentration_settings("investment-studio", as_of_date=date(2026, 4, 15))["rules"][0]["limit_weight"] == 0.2
    assert read_concentration_settings("investment-studio", as_of_date=date(2026, 5, 1))["rules"][0]["limit_weight"] == 0.25
    assert client.get(path).json()["revision"] == 2


def test_api_concentration_reconciles_direct_securities_with_account_snapshots(client):
    response = client.get("/api/portfolios/investment-studio/concentration")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["nav"] > 0
    for row in data["scopes"][0]["rows"]:
        if row["weight"] is not None:
            assert row["weight"] == pytest.approx(row["exposure_base"] / data["nav"])
    assert data["settings_revision"] == 0


def test_archived_unchanged_rules_do_not_block_other_limit_edits(client):
    from portfolio_app.db.models import TaxonomyRecordModel
    from portfolio_app.db.session import get_session_factory
    with get_session_factory()() as session:
        tid = "archive-limits"
        session.add(TaxonomyRecordModel(taxonomy_id=tid, portfolio_id="investment-studio", name="Archive limits", taxonomy_type="custom", primary_assignment_scope="instrument", status="active", planning_enabled=False))
        session.commit()
    path = "/api/portfolios/investment-studio/concentration/settings"
    payload = {"expected_revision": 0, "effective_from": "2026-04-01", "rules": [rule("taxonomy", tid=tid)], "fcn_allocations": []}
    assert client.put(path, json=payload).status_code == 200
    with get_session_factory()() as session:
        session.get(TaxonomyRecordModel, tid).status = "inactive"
        session.commit()
    payload["expected_revision"] = 1
    payload["rules"].append(rule("security", rule_id="s"))
    assert client.put(path, json=payload).status_code == 200


def test_portfolio_copy_cannot_silently_drop_dated_concentration_policy(client):
    from portfolio_app.services.portfolio_store import copy_portfolio
    path = "/api/portfolios/investment-studio/concentration/settings"
    payload = {"expected_revision": 0, "effective_from": "2026-04-01", "rules": [rule("security")], "fcn_allocations": []}
    assert client.put(path, json=payload).status_code == 200
    with pytest.raises(ValueError, match="explicit remapping of concentration policy references"):
        copy_portfolio("investment-studio")


def test_historical_concentration_uses_current_classification_and_keeps_holding_date(client):
    path = "/api/portfolios/investment-studio/taxonomies"
    created = client.post(path, json={"name": "Original industry", "taxonomy_type": "custom", "primary_assignment_scope": "instrument"})
    assert created.status_code == 200, created.text
    tid = created.json()["taxonomy_id"]
    read_path = "/api/portfolios/investment-studio/concentration?as_of_date=2026-04-15"
    before = client.get(read_path)
    assert before.status_code == 200, before.text
    original = next(row for row in before.json()["scopes"] if row["taxonomy_id"] == tid)
    renamed = client.patch(f"{path}/{tid}", json={"name": "Updated industry"})
    assert renamed.status_code == 200, renamed.text
    response = client.get(read_path)
    assert response.status_code == 200, response.text
    current = next(row for row in response.json()["scopes"] if row["taxonomy_id"] == tid)
    assert current["name"] == "Updated industry"
    assert current["taxonomy_configuration"]["configuration_version"] > original["taxonomy_configuration"]["configuration_version"]
    assert "effective_from" not in current["taxonomy_configuration"]
    assert response.json()["as_of_date"] == before.json()["as_of_date"] == "2026-04-15"
    assert response.json()["nav"] == before.json()["nav"]
    archived = client.patch(f"{path}/{tid}", json={"status": "inactive"})
    assert archived.status_code == 200, archived.text
    hidden = client.get(read_path)
    assert hidden.status_code == 200, hidden.text
    assert not any(row["taxonomy_id"] == tid for row in hidden.json()["scopes"])


def test_risk_context_contains_concentration_and_tail_sources(client):
    response = client.get("/api/portfolios/investment-studio/risk-context")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["concentration"]["as_of_date"] == data["tail_risk"]["as_of_date"] == data["as_of_date"]
    source_ids = {row["source_id"] for row in data["sources"]}
    assert data["concentration"]["source_id"] in source_ids
    assert data["tail_risk"]["sources"][0]["source_id"] in source_ids
    assert "targets_by_taxonomy" in data


def test_risk_context_keeps_other_modules_when_account_concentration_is_unavailable(client, monkeypatch):
    from portfolio_app.services import concentration
    def missing(*args, **kwargs):
        raise concentration.ConcentrationUnavailable()
    monkeypatch.setattr(concentration, "read_portfolio_concentration", missing)
    response = client.get("/api/portfolios/investment-studio/risk-context")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["concentration"]["status"] == "unavailable"
    assert data["concentration"]["scopes"] == []
    assert data["portfolio_metrics"] and data["tail_risk"]


def test_viewer_can_read_limits_but_cannot_save_and_unrelated_user_cannot_read(client):
    from studio_identity import Principal
    from portfolio_app.api.authorization import authenticated_principal
    from portfolio_app.db.models import PortfolioMembershipModel
    from portfolio_app.db.session import get_session_factory
    with get_session_factory()() as session:
        session.get(PortfolioMembershipModel, ("investment-studio", "test-manager")).role = "viewer"
        session.commit()
    path = "/api/portfolios/investment-studio/concentration/settings"
    assert client.get(path).status_code == 200
    assert client.put(path, json={"expected_revision": 0, "effective_from": "2026-04-01"}).status_code == 403
    client.app.dependency_overrides[authenticated_principal] = lambda: Principal("outsider", "Outsider", "default")
    assert client.get(path).status_code == 404
