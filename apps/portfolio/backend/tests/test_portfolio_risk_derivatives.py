from copy import deepcopy

import pytest

from portfolio_app.services.portfolio_risk_derivatives import project_derivative_risk


def _option(holding_id="put-a", *, written=True, option_type="put", settlement_type="physical", strike_currency="USD"):
    return {
        "derivative_contract_id": holding_id,
        "position_reference_id": holding_id,
        "holding_kind": "option_obligation" if written else "derivative_contract",
        "quantity": -2 if written else 2,
        "open_contract_quantity": 2,
        "required_underlying_quantity": 200,
        "strike_notional": 20000,
        "strike_currency": "USD",
        "account_ids": ["broker-a"],
        "valuation_basis": "premium_liability" if written else "carried_cost",
        "fair_value_coverage_status": "unavailable",
        "derivative_contract": {
            "derivative_contract_id": holding_id,
            "contract_name": "An option",
            "contract_type": "option",
            "currency": "HKD",  # Premium currency does not determine strike currency.
            "terms": {
                "underlying_instrument_id": "actual-stock-id",
                "option_type": option_type,
                "expiry_date": "2026-10-16",
                "contract_multiplier": "100",
                "strike": "100",
                "strike_currency": strike_currency,
                "settlement_type": settlement_type,
                "exercise_style": "american",
            },
        },
        "option_risk": {
            "underlying_name": "Actual stock",
            "underlying_quote_currency": "USD",
            "underlying_spot": 90,
            "underlying_quote_as_of_date": "2026-09-04",
            "underlying_quote_status": "complete",
            "moneyness_pct": 0.1,
            "intrinsic_value_per_share": 10,
            "days_to_expiry": 42,
            # Existing repeated aggregate backing must not be summed per contract.
            "backing": {"available": 30000, "required": 40000, "shortfall": 10000},
        },
    }


def _workspace(*rows):
    return {"portfolio_id": "p/a", "as_of_date": "2026-09-04", "base_currency": "HKD", "rows": list(rows)}


@pytest.mark.parametrize("written,option_type,shares_in", [(True, "put", True), (True, "call", False), (False, "put", False), (False, "call", True)])
def test_explicit_physical_contract_projects_conditional_cash_and_shares(written, option_type, shares_in):
    result = project_derivative_risk(_workspace(_option(written=written, option_type=option_type)))
    position = result["positions"][0]
    flow = position["option"]["conditional_physical_settlement"]
    assert flow["shares_in"] == (200 if shares_in else 0)
    assert flow["shares_out"] == (0 if shares_in else 200)
    assert flow["cash_out"] == (20000 if shares_in else 0)
    assert flow["cash_in"] == (0 if shares_in else 20000)
    assert flow["cash_currency"] == "USD"
    assert flow["role"] == ("assignment_obligation" if written else "exercise_right")
    assert position["contract_currency"] == "HKD"
    assert position["underlyings"][0]["instrument_id"] == "actual-stock-id"
    assert result["sources"][0]["holding_ids"] == ["put-a"]
    assert result["sources"][0]["instrument_ids"] == ["actual-stock-id"]
    assert result["sources"][0]["currency"] == "HKD"
    assert result["sources"][0]["end_date"] == "2026-09-04"
    assert position["detail_path"] == "/portfolios/p%2Fa/holdings/put-a"


@pytest.mark.parametrize("settlement", [None, "cash"])
def test_unknown_or_cash_settlement_does_not_assert_share_delivery_or_full_strike_cash(settlement):
    position = project_derivative_risk(_workspace(_option(settlement_type=settlement)))["positions"][0]
    assert position["option"]["conditional_physical_settlement"] is None
    assert position["option"]["strike_notional_reference"] == 20000
    assert "backing" not in position["option"]
    assert any("结算" in line for line in position["coverage"])


@pytest.mark.parametrize("strike_currency", [None, "CNY"])
def test_unknown_or_different_strike_currency_does_not_compare_with_usd_spot(strike_currency):
    position = project_derivative_risk(_workspace(_option(strike_currency=strike_currency)))["positions"][0]
    assert position["option"]["moneyness_ratio"] is None
    assert position["option"]["intrinsic_value_per_share"] is None
    assert any("币种" in line for line in position["coverage"])
    if strike_currency is None:
        assert position["option"]["conditional_physical_settlement"] is None


def test_fcn_keeps_current_barrier_touch_separate_from_recorded_lifecycle_and_notional():
    row = {
        "position_reference_id": "fcn-a",
        "holding_kind": "derivative_contract",
        "quantity": 2,
        "market_value_base": 160000,
        "derivative_contract": {
            "contract_type": "fcn", "currency": "USD",
            "terms": {
                "notional": "100000",
                "maturity_date": "2027-01-04",
                "knock_out_observation_dates": ["2026-08-04", "2026-10-04"],
                "underlyings": [{"instrument_id": "actual-stock-id", "deliverable": True, "initial_reference_price": "100", "strike_level_pct": "80", "knock_in_level_pct": "70"}],
            },
        },
        "fcn_risk": {
            "lifecycle_status": "open",
            "risk_state": "current_price_at_or_below_knock_in",
            "underlyings": [{"instrument_id": "actual-stock-id", "spot": 65, "quote_status": "complete", "quote_as_of_date": "2026-09-03", "distance_to_knock_in_pct": -0.07142857, "current_region": "at_or_below_knock_in", "missing_terms": ["knock_out_level_pct"]}],
        },
    }
    position = project_derivative_risk(_workspace(row))["positions"][0]
    assert position["fcn"]["lifecycle_status"] == "open"
    assert position["fcn"]["original_contract_notional"] == "100000"
    assert position["fcn"]["next_recorded_knock_out_observation_date"] == "2026-10-04"
    assert position["terms"].get("final_observation_date") is None
    assert position["underlyings"][0]["distance_to_knock_in_pct"] == -0.07142857
    assert any("不等于已确认敲入" in line for line in position["coverage"])
    assert any("早于持仓日期" in line for line in position["coverage"])


def test_resources_are_not_repeated_or_netted_and_input_is_unchanged():
    cash = {"holding_kind": "settled_cash", "line_id": "cash-usd", "instrument_core": {"currency": "USD"}, "market_value": 30000, "cash_purpose": "collateral", "available_for_trading": False, "collateral_reference": "margin-a", "account_ids": ["broker-a"]}
    debit = {**cash, "line_id": "debit-usd", "market_value": -10000, "financing_liability": 10000}
    stock = {"holding_kind": "position", "instrument_core": {"instrument_id": "actual-stock-id"}, "position_reference_id": "stock-a", "quantity": 150, "account_ids": ["broker-b"]}
    pending = {**cash, "holding_kind": "pending_cash", "line_id": "pending", "market_value": 99999}
    workspace = _workspace(_option("put-a"), _option("put-b"), cash, debit, stock, pending)
    original = deepcopy(workspace)
    result = project_derivative_risk(workspace)
    assert workspace == original
    assert len(result["positions"]) == 2
    assert len(result["resources"]["cash"]) == 2
    assert result["resources"]["cash"][1]["market_value"] == -10000
    assert result["resources"]["cash"][0]["available_for_trading"] is False
    assert len(result["resources"]["underlying_positions"]) == 1
    assert result["sources"][0]["holding_ids"] == ["put-a"]
    assert result["sources"][1]["holding_ids"] == ["put-b"]
    result["positions"][0]["terms"]["strike"] = "999"
    assert workspace == original


def test_registered_contract_without_current_holding_is_not_current_exposure():
    workspace = _workspace()
    workspace["derivative_contracts"] = [_option()["derivative_contract"]]
    assert project_derivative_risk(workspace)["positions"] == []
    assert project_derivative_risk(workspace)["sources"] == []
