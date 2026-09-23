from __future__ import annotations

from copy import deepcopy

import pytest

from portfolio_app.services.holdings_workspace import _enrich_holdings_model_coverage


def _market_position(instrument_id: str = "equity", exposure: float = 100.0) -> dict[str, object]:
    return {
        "line_id": f"holding:{instrument_id}",
        "holding_kind": "position",
        "instrument_core": {
            "instrument_id": instrument_id,
            "instrument_name": instrument_id,
            "instrument_type": "equity",
            "currency": "CNY",
        },
        "valuation_basis": "market_quote",
        "fair_value_coverage_status": "complete",
        "last_price": 10.0,
        "market_value_base": exposure,
    }


@pytest.mark.parametrize(
    "planning_metadata",
    [
        {},
        {"taxonomy_assignments": []},
        {
            "taxonomy_assignments": [],
            "target_set_lines": [{"target_member_id": "equity", "target_value": 0.0}],
        },
    ],
)
def test_actual_market_risk_does_not_require_classification_or_positive_targets(planning_metadata) -> None:
    row = _market_position()
    workspace = {"rows": [row], "totals": {"nav": 100.0}, **deepcopy(planning_metadata)}

    result = _enrich_holdings_model_coverage(workspace)

    assert row["risk_eligible"] is True
    assert row["modeling_status"] == "eligible"
    assert row["exclusion_reason"] is None
    assert row["holding_category"] == "securities"
    summary = result["risk_coverage_summary"]
    assert summary["model_name"] == "Market risk model"
    assert summary["modeled_net_exposure"] == pytest.approx(100.0)
    assert summary["coverage_ratio"] == pytest.approx(1.0)
    assert summary["excluded_rows"] == []
    assert not {"risk_budget_eligible", "scope_policy_version", "performance_scope"} & row.keys()
    assert not {"scope_policy_versions", "configuration_versions", "taxonomy_selection_versions", "cash_scope_breakdown", "ordinary_sleeve_twr_status"} & summary.keys()
    assert "analytics_scope_summary" not in result


@pytest.mark.parametrize(
    ("missing_data", "reason"),
    [
        ({"valuation_basis": "carried_cost"}, "valuation_basis=carried_cost"),
        ({"fair_value_coverage_status": "partial"}, "fair_value_coverage_status=partial"),
        ({"last_price": None}, "last_price is unavailable"),
        ({"instrument_core": {}}, "identity"),
        ({"holding_kind": "unknown"}, "holding_kind=unknown"),
    ],
)
def test_incomplete_market_position_keeps_uncovered_exposure(missing_data, reason) -> None:
    row = {**_market_position(), **missing_data}

    result = _enrich_holdings_model_coverage({"rows": [row], "totals": {"nav": 100.0}})

    assert row["risk_eligible"] is False
    assert row["modeling_status"] == "unavailable"
    assert reason in row["exclusion_reason"]
    summary = result["risk_coverage_summary"]
    assert summary["modeled_gross_exposure"] == 0.0
    assert summary["excluded_carrying_value"] == pytest.approx(100.0)
    assert summary["coverage_ratio"] == 0.0
    assert summary["excluded_rows"][0]["exposure_base"] == pytest.approx(100.0)
    assert summary["excluded_rows"][0]["modeling_status"] == "unavailable"
    assert summary["excluded_rows"][0]["exclusion_reason"] == row["exclusion_reason"]


@pytest.mark.parametrize(
    "contract_identity",
    [
        {"holding_kind": "derivative_contract", "derivative_contract_id": "fcn"},
        {"holding_kind": "option_obligation", "derivative_contract_id": "written-option"},
        {"derivative_contract_id": "fcn"},
    ],
)
def test_derivative_identity_cannot_be_enabled_by_market_valuation_fields(contract_identity) -> None:
    row = {**_market_position(), **contract_identity}

    result = _enrich_holdings_model_coverage({"rows": [row], "totals": {"nav": 100.0}})

    assert row["risk_eligible"] is False
    assert row["modeling_status"] == "unsupported"
    assert row["holding_category"] == "derivatives"
    assert "Derivative contracts" in row["exclusion_reason"]
    assert result["risk_coverage_summary"]["excluded_rows"][0]["modeling_status"] == "unsupported"


@pytest.mark.parametrize("holding_kind", ["settled_cash", "settlement_receivable", "settlement_payable"])
def test_cash_and_settlement_remain_separate_from_security_covariance(holding_kind) -> None:
    row = {
        **_market_position(),
        "holding_kind": holding_kind,
        "instrument_core": {"instrument_id": "cash:CNY", "instrument_type": "cash", "currency": "CNY"},
    }

    result = _enrich_holdings_model_coverage({"rows": [row], "totals": {"nav": 100.0}})

    assert row["risk_eligible"] is False
    assert row["modeling_status"] == "cash_or_settlement"
    assert row["holding_category"] == "cash_and_settlement"
    assert "Cash and settlement" in row["exclusion_reason"]
    summary = result["risk_coverage_summary"]
    assert summary["cash_unallocated_exposure"] == pytest.approx(100.0)
    assert summary["excluded_carrying_value"] == 0.0
    assert summary["excluded_rows"] == []


def test_coverage_preserves_signed_exposure_and_discloses_asset_and_liability_gaps() -> None:
    rows = [
        _market_position("long", 100.0),
        _market_position("short", -20.0),
        {**_market_position("fcn", 40.0), "derivative_contract_id": "fcn", "valuation_basis": "carried_cost"},
        {**_market_position("option", -10.0), "holding_kind": "option_obligation", "valuation_basis": "premium_liability", "is_liability": True},
        {**_market_position("cash", 30.0), "holding_kind": "settled_cash", "instrument_core": {"instrument_id": "cash:CNY", "instrument_type": "cash"}},
    ]

    result = _enrich_holdings_model_coverage({"rows": rows, "totals": {"nav": 140.0}})

    summary = result["risk_coverage_summary"]
    assert summary["total_nav"] == pytest.approx(140.0)
    assert summary["modeled_net_exposure"] == pytest.approx(80.0)
    assert summary["modeled_gross_exposure"] == pytest.approx(120.0)
    assert summary["excluded_carrying_value"] == pytest.approx(40.0)
    assert summary["excluded_liability"] == pytest.approx(10.0)
    assert summary["cash_unallocated_exposure"] == pytest.approx(30.0)
    assert summary["coverage_ratio"] == pytest.approx(0.6)
    assert [row["exposure_base"] for row in summary["excluded_rows"]] == [40.0, -10.0]


def test_missing_valuation_retains_unknown_exposure_and_cannot_report_full_coverage():
    missing = {**_market_position("missing"), "quantity": 100, "market_value_base": None}
    result = _enrich_holdings_model_coverage({
        "rows": [_market_position("known"), missing], "totals": {"nav": 200},
    })
    summary = result["risk_coverage_summary"]
    assert missing["risk_eligible"] is False
    assert missing["modeling_status"] == "unavailable"
    assert summary["coverage_ratio"] is None
    assert summary["modeled_gross_exposure"] == 100
    assert summary["excluded_rows"][0]["instrument_id"] == "missing"
    assert summary["excluded_rows"][0]["exposure_base"] is None


def test_explicit_zero_exposure_does_not_hide_coverage_of_held_securities():
    zero = {**_market_position("closed"), "quantity": 0, "market_value_base": None}
    result = _enrich_holdings_model_coverage({"rows": [_market_position("known"), zero], "totals": {"nav": 100}})
    assert result["risk_coverage_summary"]["coverage_ratio"] == 1
    assert result["risk_coverage_summary"]["excluded_rows"] == []
