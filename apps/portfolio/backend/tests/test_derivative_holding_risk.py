from __future__ import annotations

from datetime import date

import pytest

from portfolio_app.services.derivative_holding_risk import enrich_derivative_holding_risk


def _option_obligation(
    contract_id: str,
    *,
    quantity: float,
    option_type: str = "call",
) -> dict[str, object]:
    return {
        "line_id": f"{contract_id}:obligation",
        "holding_kind": "option_obligation",
        "quantity": -quantity,
        "required_underlying_quantity": quantity * 100,
        "strike_notional": quantity * 100 * 200,
        "cost_basis": None,
        "derivative_contract": {
            "derivative_contract_id": contract_id,
            "contract_type": "option",
            "currency": "USD",
            "terms": {
                "underlying_instrument_id": "stock-1",
                "option_type": option_type,
                "expiry_date": "2026-12-18",
                "strike": 200,
                "contract_multiplier": 100,
            },
        },
    }


def test_option_risk_uses_portfolio_level_backing_across_written_contracts(monkeypatch) -> None:
    rows = [
        {
            "line_id": "stock-1",
            "holding_kind": "position",
            "quantity": 150,
            "instrument_core": {
                "instrument_id": "stock-1",
                "instrument_name": "Stock One",
                "currency": "USD",
            },
        },
        _option_obligation("call-a", quantity=1),
        _option_obligation("call-b", quantity=1),
    ]
    monkeypatch.setattr(
        "portfolio_app.services.derivative_holding_risk._quote_by_instrument",
        lambda instrument_ids, as_of_date: (
            {
                "stock-1": {
                    "instrument_id": "stock-1",
                    "instrument_name": "Stock One",
                    "currency": "USD",
                }
            },
            {
                "stock-1": {
                    "value": 220,
                    "currency": "USD",
                    "quote_date": "2026-09-01",
                    "status": "complete",
                }
            },
        ),
    )

    enrich_derivative_holding_risk(rows, transactions=[], as_of_date=date(2026, 9, 1))

    for row in rows[1:]:
        risk = row["option_risk"]
        assert risk["moneyness_pct"] == pytest.approx(0.1)
        assert risk["intrinsic_value_per_share"] == pytest.approx(20)
        assert risk["risk_state"] == "uncovered"
        assert risk["backing"] == {
            "kind": "portfolio_underlying_shares",
            "available": 150,
            "required": 200,
            "ratio": 0.75,
            "shortfall": 50,
            "currency": None,
        }


def test_written_put_backing_nets_positive_and_negative_cash_accounts(monkeypatch) -> None:
    rows = [
        {
            "line_id": "cash-positive",
            "holding_kind": "settled_cash",
            "market_value": 25_000,
            "instrument_core": {"currency": "USD"},
        },
        {
            "line_id": "cash-negative",
            "holding_kind": "settled_cash",
            "market_value": -10_000,
            "instrument_core": {"currency": "USD"},
        },
        _option_obligation("put-a", quantity=1, option_type="put"),
    ]
    monkeypatch.setattr(
        "portfolio_app.services.derivative_holding_risk._quote_by_instrument",
        lambda instrument_ids, as_of_date: (
            {"stock-1": {"instrument_name": "Stock One", "currency": "USD"}},
            {
                "stock-1": {
                    "value": 180,
                    "currency": "USD",
                    "quote_date": "2026-09-01",
                    "status": "complete",
                }
            },
        ),
    )

    enrich_derivative_holding_risk(rows, transactions=[], as_of_date=date(2026, 9, 1))

    assert rows[2]["option_risk"]["backing"] == {
        "kind": "portfolio_settled_cash",
        "available": 15_000,
        "required": 20_000,
        "ratio": 0.75,
        "shortfall": 5_000,
        "currency": "USD",
    }


def test_long_option_without_an_underlying_quote_is_not_reported_as_open(monkeypatch) -> None:
    rows = [
        {
            "line_id": "call-a",
            "holding_kind": "derivative_contract",
            "quantity": 1,
            "cost_basis": 300,
            "derivative_contract": {
                "derivative_contract_id": "call-a",
                "contract_type": "option",
                "currency": "USD",
                "terms": {
                    "underlying_instrument_id": "stock-1",
                    "option_type": "call",
                    "expiry_date": "2026-12-18",
                    "strike": 200,
                    "contract_multiplier": 100,
                },
            },
        }
    ]
    monkeypatch.setattr(
        "portfolio_app.services.derivative_holding_risk._quote_by_instrument",
        lambda instrument_ids, as_of_date: (
            {
                "stock-1": {
                    "instrument_id": "stock-1",
                    "instrument_name": "Stock One",
                    "currency": "USD",
                }
            },
            {
                "stock-1": {
                    "value": None,
                    "currency": "USD",
                    "quote_date": None,
                    "status": "unavailable",
                }
            },
        ),
    )

    enrich_derivative_holding_risk(rows, transactions=[], as_of_date=date(2026, 9, 1))

    assert rows[0]["option_risk"]["risk_state"] == "quote_unavailable"
    assert rows[0]["option_risk"]["moneyness_pct"] is None


def test_put_moneyness_uses_strike_as_the_common_denominator(monkeypatch) -> None:
    rows = [
        {
            "line_id": "put-a",
            "holding_kind": "derivative_contract",
            "quantity": 1,
            "cost_basis": 300,
            "derivative_contract": {
                "derivative_contract_id": "put-a",
                "contract_type": "option",
                "currency": "USD",
                "terms": {
                    "underlying_instrument_id": "stock-1",
                    "option_type": "put",
                    "expiry_date": "2026-12-18",
                    "strike": 100,
                    "contract_multiplier": 100,
                },
            },
        }
    ]
    monkeypatch.setattr(
        "portfolio_app.services.derivative_holding_risk._quote_by_instrument",
        lambda instrument_ids, as_of_date: (
            {"stock-1": {"instrument_name": "Stock One", "currency": "USD"}},
            {
                "stock-1": {
                    "value": 80,
                    "currency": "USD",
                    "quote_date": "2026-09-01",
                    "status": "complete",
                }
            },
        ),
    )

    enrich_derivative_holding_risk(rows, transactions=[], as_of_date=date(2026, 9, 1))

    assert rows[0]["option_risk"]["moneyness_pct"] == pytest.approx(0.2)
    assert rows[0]["option_risk"]["risk_state"] == "in_the_money"


def test_fcn_risk_reports_current_levels_without_inferring_historical_barrier_events(monkeypatch) -> None:
    rows = [
        {
            "line_id": "fcn-1",
            "holding_kind": "derivative_contract",
            "quantity": 1,
            "derivative_contract": {
                "derivative_contract_id": "fcn-1",
                "contract_type": "fcn",
                "currency": "USD",
                "terms": {
                    "underlyings": [
                        {
                            "instrument_id": "stock-1",
                            "initial_reference_price": 100,
                            "strike_level_pct": 80,
                            "knock_in_level_pct": 70,
                            "knock_out_level_pct": 105,
                        }
                    ]
                },
            },
        }
    ]
    monkeypatch.setattr(
        "portfolio_app.services.derivative_holding_risk._quote_by_instrument",
        lambda instrument_ids, as_of_date: (
            {
                "stock-1": {
                    "instrument_id": "stock-1",
                    "instrument_name": "Stock One",
                    "currency": "USD",
                }
            },
            {
                "stock-1": {
                    "value": 65,
                    "currency": "USD",
                    "quote_date": "2026-09-01",
                    "status": "complete",
                }
            },
        ),
    )

    enrich_derivative_holding_risk(rows, transactions=[], as_of_date=date(2026, 9, 1))

    risk = rows[0]["fcn_risk"]
    underlying = risk["underlyings"][0]
    assert risk["lifecycle_status"] == "open"
    assert risk["risk_state"] == "current_price_at_or_below_knock_in"
    assert underlying["current_region"] == "at_or_below_knock_in"
    assert underlying["strike_price"] == pytest.approx(80)
    assert underlying["knock_in_price"] == pytest.approx(70)
    assert underlying["distance_to_knock_in_pct"] == pytest.approx(65 / 70 - 1)

    enrich_derivative_holding_risk(
        rows,
        transactions=[
            {
                "trade_date": "2026-08-15",
                "derivative_contract_id": "fcn-1",
                "lifecycle_event_type": "fcn_knock_in",
            }
        ],
        as_of_date=date(2026, 9, 1),
    )
    assert rows[0]["fcn_risk"]["lifecycle_status"] == "knocked_in"
    assert rows[0]["fcn_risk"]["risk_state"] == "knocked_in"

    enrich_derivative_holding_risk(
        rows,
        transactions=[
            {
                "trade_date": "2026-08-30",
                "derivative_contract_id": "fcn-1",
                "lifecycle_event_type": "fcn_maturity",
            },
            {
                "trade_date": "2026-08-15",
                "derivative_contract_id": "fcn-1",
                "lifecycle_event_type": "fcn_knock_in",
            },
        ],
        as_of_date=date(2026, 9, 1),
    )
    assert rows[0]["fcn_risk"]["lifecycle_status"] == "matured"
    assert rows[0]["fcn_risk"]["risk_state"] == "matured"


def test_fcn_incomplete_terms_do_not_claim_a_complete_price_region(monkeypatch) -> None:
    rows = [
        {
            "line_id": "fcn-1",
            "holding_kind": "derivative_contract",
            "quantity": 1,
            "derivative_contract": {
                "derivative_contract_id": "fcn-1",
                "contract_type": "fcn",
                "currency": "USD",
                "terms": {
                    "underlyings": [
                        {
                            "instrument_id": "stock-1",
                            "initial_reference_price": 100,
                            "strike_level_pct": 80,
                        }
                    ]
                },
            },
        }
    ]
    monkeypatch.setattr(
        "portfolio_app.services.derivative_holding_risk._quote_by_instrument",
        lambda instrument_ids, as_of_date: (
            {
                "stock-1": {
                    "instrument_id": "stock-1",
                    "instrument_name": "Stock One",
                    "currency": "USD",
                }
            },
            {
                "stock-1": {
                    "value": 90,
                    "currency": "USD",
                    "quote_date": "2026-09-01",
                    "status": "complete",
                }
            },
        ),
    )

    enrich_derivative_holding_risk(rows, transactions=[], as_of_date=date(2026, 9, 1))

    risk = rows[0]["fcn_risk"]
    assert risk["risk_state"] == "terms_incomplete"
    assert risk["underlyings"][0]["current_region"] == "at_or_above_strike"
    assert risk["underlyings"][0]["missing_terms"] == [
        "knock_in_level_pct",
        "knock_out_level_pct",
    ]
