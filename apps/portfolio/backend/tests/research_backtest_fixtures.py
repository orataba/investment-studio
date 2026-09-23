"""Explicit historical books for replay tests; production has no cash fallback."""
from copy import deepcopy


def initial_book(day, *, securities=None, cash=100.0, derivative=0.0):
    securities = securities or {}
    nav = sum(securities.values()) + cash + derivative
    return {
        "source": "portfolio_inception_eod_holdings", "status": "available",
        "as_of_date": day, "base_currency": "CNY", "scope_node_id": None,
        "portfolio_nav_base": nav, "scope_nav_base": nav,
        "cash_value_base": cash, "derivative_value_base": derivative,
        "securities": [
            {"instrument_id": key, "label": key, "market_value_base": value,
             "initial_weight": value / nav, "top_sleeve_id": key, "top_sleeve_label": key}
            for key, value in securities.items()
        ],
        "unavailable_reason": None,
    }


def stub_inception_statement(monkeypatch, solver, *, day="2026-01-01", securities=None, cash=100.0, derivative=0.0, currency="CNY"):
    book = initial_book(day, securities=securities, cash=cash, derivative=derivative)
    positions = [
        {"instrument_id": row["instrument_id"], "market_value_base": row["market_value_base"],
         "quote_as_of_date": day, "valuation_basis": "market_quote", "currency": currency}
        for row in book["securities"]
    ]
    if derivative:
        positions.append({
            "instrument_id": None, "market_value_base": derivative,
            "derivative_contract": {"contract_type": "fcn"}, "currency": currency,
        })
    statement = {
        "total_nav_base": book["scope_nav_base"], "cash_balance_base": cash,
        "pending_settlement_base": 0.0, "positions": positions,
    }
    monkeypatch.setattr(solver, "list_accounts", lambda _: [])
    monkeypatch.setattr(solver, "list_transactions", lambda _: [])
    monkeypatch.setattr(solver, "build_holdings_report", lambda *args, **kwargs: deepcopy(statement))
    return statement
