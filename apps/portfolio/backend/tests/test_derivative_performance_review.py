"""Independent payoff and coverage regressions from the ledger review."""
from copy import deepcopy
from datetime import date

import pytest

from portfolio_app.services import ledger, performance


PORTFOLIO = {
    "portfolio_id": "derivative-risk-review", "base_currency": "USD",
    "valuation_timezone": "Asia/Shanghai", "valuation_cutoff_policy": "latest_complete_eod",
    "inception_date": "2026-01-02", "as_of_date": "2026-06-01",
}
ACCOUNTS = [
    {"account_id": key, "account_name": key, "account_type": "deposit_account" if key == "cash" else "securities_account",
     "account_category": "cash" if key == "cash" else "option" if key == "option" else "security",
     "currency": "USD", "cost_basis_method": "fifo"}
    for key in ("cash", "stock", "option")
]
STOCK = {"instrument_id": "review-stock", "instrument_name": "Review Stock", "instrument_type": "equity", "currency": "USD", "exchange_code": "XNYS"}


def fact(sequence, kind, day, account, gross, *, quantity=None, contract=None, stock=False, **extra):
    return {
        "portfolio_id": PORTFOLIO["portfolio_id"], "transaction_id": f"review-{sequence}",
        "transaction_sequence": sequence, "transaction_type": kind, "trade_date": day,
        "settlement_date": day, "account_id": account, "gross_amount": gross,
        "quantity": quantity, "fees": 0.0, "taxes": 0.0, "currency": "USD",
        "instrument_id": STOCK["instrument_id"] if stock else None,
        "instrument_ref": deepcopy(STOCK) if stock else None,
        "derivative_contract_id": contract["derivative_contract_id"] if contract else None,
        "derivative_contract": deepcopy(contract), **extra,
    }


def market(monkeypatch, history):
    detail = {
        **STOCK,
        "quote_selection_policy": {role: ["close"] for role in ("trading", "valuation", "total_return", "chart", "reference")},
        "market_data": [
            {"metric_family": "price", "quote_basis": "close", "as_of_date": day,
             "value": price, "currency": "USD", "price_unit": "per_unit", "price_scale": 1,
             "status": "complete"}
            for day, price in history
        ],
    }
    for module in (performance, ledger):
        monkeypatch.setattr(module, "list_registry_corporate_actions", lambda *args, **kwargs: [])
        monkeypatch.setattr(module, "get_registry_instrument_detail", lambda key: deepcopy(detail) if key == STOCK["instrument_id"] else None)
        monkeypatch.setattr(module, "get_registry_instrument_details", lambda keys: {key: deepcopy(detail) for key in keys if key == STOCK["instrument_id"]})
    monkeypatch.setattr(performance, "get_shared_fx_rates", lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []})
    return detail


def physical_facts(side, option_type, spot):
    contract = {
        "derivative_contract_id": "review-option", "contract_name": "Review Option", "contract_type": "option", "currency": "USD",
        "terms": {"underlying_instrument_id": STOCK["instrument_id"], "option_type": option_type,
                  "strike": 100, "contract_multiplier": 100, "expiry_date": "2026-06-30"},
    }
    sell_stock = (side, option_type) in {("long", "put"), ("written", "call")}
    facts = [fact(1, "opening_balance", "2026-01-02", "cash", 20000 - 100 * spot if sell_stock else 20000)]
    if sell_stock:
        facts.append(fact(2, "opening_balance", "2026-01-02", "stock", 100 * spot, quantity=100, stock=True, acquisition_date="2025-12-01"))
    facts.extend([
        fact(3, "buy" if side == "long" else "option_write", "2026-01-05", "option", 500, quantity=1, contract=contract, settlement_cash_account_id="cash"),
        fact(4, "maturity_redemption" if side == "long" else "lifecycle_event", "2026-06-01", "option", 0, quantity=1, contract=contract,
             lifecycle_event_type="option_long_exercise" if side == "long" else "option_writer_assignment"),
        fact(5, "sell" if sell_stock else "buy", "2026-06-01", "stock", 10000, quantity=100, stock=True, settlement_cash_account_id="cash"),
    ])
    return facts


@pytest.mark.parametrize("side,option_type,spot,nav,excluded", [
    ("long", "call", 120, 21500, 1500),
    ("long", "put", 80, 21500, 1500),
    ("written", "call", 120, 18500, -1500),
    ("written", "put", 80, 18500, -1500),
])
def test_physical_option_intrinsic_is_excluded_from_portfolio_and_group_risk(monkeypatch, side, option_type, spot, nav, excluded):
    market(monkeypatch, [("2026-05-29", spot), ("2026-06-01", spot)])
    facts = physical_facts(side, option_type, spot)
    calls = []
    def links(portfolio_id):
        calls.append(portfolio_id)
        return [{"option_transaction_id": "review-4", "stock_transaction_id": "review-5"}]
    monkeypatch.setattr(performance, "list_option_delivery_links", links)
    rows = performance.build_daily_portfolio_snapshots(
        PORTFOLIO, ACCOUNTS, facts, start_date=date(2026, 5, 29), end_date=date(2026, 6, 1), include_materialized_rows=True,
    )
    result = rows[-1]
    assert result["nav"] == pytest.approx(nav)
    assert result["risk_scope_excluded_pnl"] == pytest.approx(excluded)
    assert result["market_risk_daily_return"] == pytest.approx(0)
    assert result["market_risk_return_coverage_state"] == "complete"
    assert calls == [PORTFOLIO["portfolio_id"]]
    for axis in ("instrument", "account"):
        slices = [row for row in result["_contribution_slices"] if row["axis"] == axis]
        assert sum(row["market_risk_total_pnl"] for row in slices) == pytest.approx(0)
        assert all(abs(row["market_risk_total_pnl"]) < 1e-9 for row in slices)


def test_direct_contribution_report_excludes_linked_delivery_value(monkeypatch):
    market(monkeypatch, [("2026-05-29", 120), ("2026-06-01", 120)])
    facts = physical_facts("long", "call", 120)
    calls = []
    def links(portfolio_id):
        calls.append(portfolio_id)
        return [{"option_transaction_id": "review-4", "stock_transaction_id": "review-5"}]
    monkeypatch.setattr(performance, "list_option_delivery_links", links)
    report = performance.build_contribution_report(
        PORTFOLIO, ACCOUNTS, facts, start_date=date(2026, 5, 29), end_date=date(2026, 6, 1), axis="instrument",
    )
    slices = [row for row in report["daily_slices"] if str(row["as_of_date"]) == "2026-06-01"]
    assert slices
    assert sum(row["market_risk_total_pnl"] for row in slices) == pytest.approx(0)
    assert all(abs(row["market_risk_total_pnl"]) < 1e-9 for row in slices)
    assert calls == [PORTFOLIO["portfolio_id"]]


def test_physical_option_missing_event_quote_does_not_publish_risk_exclusion(monkeypatch):
    market(monkeypatch, [("2026-05-29", 120)])
    facts = physical_facts("long", "call", 120)
    summary = performance._sum_cumulative_market_risk_excluded_pnl(
        facts, as_of_date=date(2026, 6, 1), derivative_lifecycle_realized_pnl=-500,
        derivative_lifecycle_coverage_complete=True, derivative_lifecycle_stale_fx_flag=False,
        base_currency="USD", direct_fx_instruments={}, instrument_detail_cache={},
    )
    assert summary["coverage_complete"] is False


def test_physical_option_on_non_session_day_uses_confirmed_previous_close(monkeypatch):
    market(monkeypatch, [("2026-05-29", 120)])
    event = physical_facts("long", "call", 120)[-2]
    event["trade_date"] = event["settlement_date"] = "2026-05-30"
    amount, currency = performance._physical_option_delivery_market_risk_pnl(event, instrument_detail_cache={})
    assert amount == 2000
    assert currency == "USD"


def test_physical_option_delivery_uses_strike_currency_not_premium_currency(monkeypatch):
    market(monkeypatch, [("2026-06-01", 120)])
    event = physical_facts("long", "call", 120)[-2]
    event["currency"] = "HKD"
    event["derivative_contract"]["currency"] = "HKD"
    event["derivative_contract"]["terms"]["strike_currency"] = "USD"
    amount, currency = performance._physical_option_delivery_market_risk_pnl(event, instrument_detail_cache={})
    assert amount == 2000
    assert currency == "USD"


def test_incomplete_risk_coverage_retains_diagnostics_but_clears_risk_metrics(monkeypatch):
    market(monkeypatch, [("2026-06-01", 100), ("2026-06-02", 110), ("2026-06-03", 99), ("2026-06-04", 105)])
    facts = [fact(1, "opening_balance", "2026-06-01", "stock", 10000, quantity=100, stock=True, acquisition_date="2026-05-01")]
    portfolio = {**PORTFOLIO, "inception_date": "2026-06-01", "as_of_date": "2026-06-04"}
    rows = performance.build_daily_portfolio_snapshots(portfolio, ACCOUNTS, facts, end_date=date(2026, 6, 4))
    rows[-1]["market_risk_return_coverage_state"] = "partial"
    rows[-1]["market_risk_return_observation_eligible"] = False
    report = performance.build_portfolio_performance_report_from_snapshots(portfolio, rows, transactions=facts, start_date=date(2026, 6, 1), end_date=date(2026, 6, 4))
    summary = report["summary"]
    assert summary["risk_result_status"] == "unavailable"
    assert summary["risk_sample_count"] == 2
    assert summary["risk_unavailable_reason"] == "return_coverage_incomplete"
    for field in ("mean_daily_return", "annualized_return_from_daily_mean", "annualized_volatility", "annualized_downside_volatility", "sharpe_ratio", "sortino_ratio"):
        assert summary[field] is None
