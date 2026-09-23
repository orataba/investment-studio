"""Explicit local Research review: API fixtures/runs and read-only market-data scenarios.

Run from the repository root with its venv and Portfolio runtime environment.
`setup` creates a clearly named synthetic portfolio; it never changes real trades.
`run ID` saves the existing run before the product's latest-run retention replaces it.
`scenarios ID` only reads the database and writes evidence files.
`verify ID` independently values saved execution quantities against canonical prices.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


API = "http://127.0.0.1:8001/api"
OUTPUT = Path(__file__).resolve().parents[4] / "outputs" / "research-review-20260905"
FIXTURE_NAME = "Research FCN Option QA 20260905"


def save(name, payload):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")


def api(path, payload=None, method=None):
    request = Request(API + path, data=json.dumps(payload).encode() if payload is not None else None,
                      headers={"Content-Type": "application/json"}, method=method)
    try:
        with urlopen(request, timeout=600) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"{request.get_method()} {path}: {error.code} {error.read().decode()}") from error


def setup():
    if any(p["portfolio_name"] == FIXTURE_NAME for p in api("/portfolios")):
        raise ValueError("The review fixture already exists; use its ID instead of creating a duplicate.")
    p = api("/portfolios", {"name": FIXTURE_NAME, "base_currency": "CNY", "inception_date": "2026-08-03"})
    pid = p["portfolio_id"]
    print("CREATED", pid, flush=True)
    prefix = f"/portfolios/{pid}"
    receipt = {"portfolio": p, "synthetic_fixture": True, "accounts": [], "transactions": []}
    save("test-setup", receipt)
    accounts = {}
    for category in ("cash", "security", "fcn", "option"):
        payload = {"account_name": f"QA {category}", "account_category": category, "currency": "CNY", "opened_at": "2026-08-03"}
        if category != "cash":
            payload["default_settlement_cash_account_id"] = accounts["cash"]
        account = api(prefix + "/accounts", payload)
        accounts[category] = account["account_id"]
        receipt["accounts"].append(account)
    save("test-setup", receipt)

    def txn(key, category, kind, amount, day="2026-08-03", **extra):
        payload = {"transaction_type": kind, "trade_date": day, "settlement_date": day,
                   "account_id": accounts[category], "gross_amount": amount, "currency": "CNY",
                   "source_system": "research-review-qa", "external_reference": key,
                   "note": "Synthetic Research QA only; not a real investment or order.", **extra}
        if category != "cash" and payload.get("lifecycle_event_type") != "option_writer_expiry":
            payload["settlement_cash_account_id"] = accounts["cash"]
        row = api(prefix + "/transactions", payload)
        receipt["transactions"].append(row)
        save("test-setup", receipt)
        print("FACT", key, row["transaction_id"], flush=True)

    txn("funding", "cash", "deposit", 1_000_000)
    txn("stock", "security", "buy", 260_000, instrument_id="600519-sh", quantity=200, price=1300)
    txn("gold", "security", "buy", 100_000, instrument_id="518880-sh", quantity=10_000, price=10)
    fcn_id = f"{pid}-fcn"
    txn("fcn-entry", "fcn", "buy", 300_000, quantity=1, price=300_000, derivative_contract_id=fcn_id,
        derivative_contract={"derivative_contract_id": fcn_id, "contract_name": "QA FCN (synthetic)", "contract_type": "fcn", "terms": {
            "notional": 300_000, "annual_coupon_rate_pct": 8, "issue_date": "2026-08-03", "maturity_date": "2027-02-03",
            "issuer": "Synthetic QA issuer", "counterparty": "Synthetic QA broker", "underlyings": [{
                "instrument_id": "600519-sh", "initial_reference_price": 1300, "strike_level_pct": 80,
                "knock_in_level_pct": 70, "knock_out_level_pct": 100, "deliverable": True,
            }],
        }})
    for suffix, kind, qty, premium, expiry, option_type in (
        ("long", "buy", 2, 50, "2027-02-03", "put"),
        ("written", "option_write", 1, 30, "2027-02-03", "call"),
        ("expiry", "option_write", 1, 20, "2026-08-25", "call"),
    ):
        cid = f"{pid}-{suffix}"
        txn(f"option-{suffix}", "option", kind, qty * premium * 100, quantity=qty, price=premium,
            derivative_contract_id=cid, derivative_contract={"derivative_contract_id": cid,
            "contract_name": f"QA {suffix} option (synthetic)", "contract_type": "option", "terms": {
                "underlying_instrument_id": "600519-sh", "option_type": option_type, "expiry_date": expiry,
                "strike": 1400, "contract_multiplier": 100,
            }})
    txn("fcn-coupon", "fcn", "coupon", 2000, day="2026-08-10", derivative_contract_id=fcn_id)
    txn("option-partial-close", "option", "sell", 7000, day="2026-08-20", quantity=1, price=70, derivative_contract_id=f"{pid}-long")
    txn("writer-expiry", "option", "lifecycle_event", 0, day="2026-08-25", quantity=1,
        lifecycle_event_type="option_writer_expiry", derivative_contract_id=f"{pid}-expiry")
    return configure_test(receipt)


def configure_test(receipt):
    pid = receipt["portfolio"]["portfolio_id"]
    prefix = f"/portfolios/{pid}"
    tax = api(prefix + "/taxonomies", {"name": "Research QA allocation", "root_allocation_basis": "risk_budget",
        "purpose": "Synthetic current-target configuration for functional QA, not historical investment evidence."})
    tid = tax["taxonomy_id"]
    nodes = []
    for name, instrument in (("Stock", "600519-sh"), ("Gold", "518880-sh")):
        node = api(prefix + f"/taxonomies/{tid}/nodes", {"node_name": name, "allocation_basis": "weight"})
        nodes.append(node)
        api(prefix + f"/taxonomies/{tid}/assignments", {"target_scope": "instrument",
            "target_entity_id": instrument, "taxonomy_node_id": node["taxonomy_node_id"]})
    targets = api(prefix + f"/taxonomies/{tid}/target-sets", {"target_set_type": "saa", "name": "QA risk and capital",
        "lines": [
            {"target_member_type": "taxonomy_node", "target_member_id": node["taxonomy_node_id"], "target_value": 0.5}
            for node in nodes
        ] + [
            {"target_member_type": "cash_bucket", "target_member_id": "__cash__", "target_value": 0.1},
        ]})
    settings = api(prefix + "/research/settings", {"planning_taxonomy_id": tid, "as_of_mode": "pinned", "as_of_date": "2026-09-03",
        "lookback_days": 30, "calculation_frequency": "daily", "missing_return_policy": "complete_case_drop",
        "covariance_model_id": "ewma_vol_shrinkage_corr_covariance", "contribution_mode": "signed",
        "capital_mode": "volatility_cap", "target_volatility": 0.15, "backtest_rebalance_frequency": "1w",
        "backtest_cash_yield_annual": 0.02, "backtest_commission_bps": 2, "backtest_tax_bps": 0, "backtest_slippage_bps": 5,
        "notes": "Synthetic QA. Securities risk budgets 50/50; derivative capital 30%, cash 10%. Not real orders or historical strategy evidence."}, "PUT")
    receipt.update(taxonomy=tax, nodes=nodes, targets=targets, settings=settings)
    save("test-setup", receipt)
    return pid


def summarize(run):
    detail = run.get("detail", run)
    b = detail.get("backtest", {})
    return {"run_id": run.get("research_run_id"), "status": run.get("status"), "solve": detail.get("solve_event"),
        "leaf_targets": [{k: r.get(k) for k in ("member_id", "label", "target_weight", "current_weight")} for r in detail.get("leaf_targets", [])],
        "backtest": {"points": len(b.get("points", [])), "start": b.get("start_date"), "end": b.get("end_date"),
                     "executions": len(b.get("execution_records", [])), "metrics": b.get("metrics"), "coverage": b.get("point_in_time_coverage"),
                     "max_contribution_residual": max((abs(r["residual"]) for r in b.get("contribution_reconciliation_points", [])), default=None)}}


def run_api(pid):
    prefix = f"/portfolios/{pid}"
    baseline = api(prefix + "/research/workbench")
    save(f"{pid}-before", {"workbench": baseline, "taxonomy": api(prefix + "/taxonomies"), "transactions": api(prefix + "/transactions")})
    for old in baseline.get("runs", []):
        save(f"{pid}-previous-{old['research_run_id']}", api(prefix + f"/research/runs/{old['research_run_id']}"))
    result = api(prefix + "/research/runs", {"requested_by": "research-review-20260905"})
    save(f"{pid}-api-run", result)
    save(f"{pid}-after", api(prefix + "/research/workbench"))
    print(json.dumps(summarize(result), ensure_ascii=False), flush=True)


def scenarios(pid):
    from portfolio_app.services.research_solver import solve_current_target_weights, build_current_target_backtest
    from portfolio_app.services.risk_model import normalize_portfolio_risk_policy
    wb = api(f"/portfolios/{pid}/research/workbench")
    s = wb["settings"]
    base = {key: deepcopy(s.get(key)) for key in (
        "planning_taxonomy_id", "comparator_taxonomy_node_id", "lookback_days", "calculation_frequency", "missing_return_policy",
        "capital_mode", "gross_exposure", "target_volatility", "max_gross_exposure", "frozen_taxonomy_node_ids", "top_sleeve_weight_bounds",
    )}
    base.update(as_of_date=date.fromisoformat(s["as_of_date"]), risk_model_config=wb["risk_policy"], _instrument_detail_cache={})
    results = []
    cases = [("weekly", {"rebalance_frequency": "1w"}), ("monthly", {"rebalance_frequency": "1m"}), ("quarterly", {"rebalance_frequency": "3m"})]
    for name, controls in cases:
        try:
            result = build_current_target_backtest(pid, **base, **controls, cash_yield_annual=s["backtest_cash_yield_annual"],
                commission_bps=s["backtest_commission_bps"], tax_bps=s["backtest_tax_bps"], slippage_bps=s["backtest_slippage_bps"],
                implementation_delay_days=s["backtest_implementation_delay_days"], robustness_scenarios=s["backtest_robustness_scenarios"])
            save(f"{pid}-{name}", result)
            summary = summarize(result)
            results.append({"case": name, "result": summary})
            print(name, json.dumps(summary["backtest"], ensure_ascii=False), flush=True)
        except ValueError as error:
            results.append({"case": name, "error": str(error)})
            print(name, "UNAVAILABLE", str(error), flush=True)
    for name, policy_change, setting_change in (
        ("sample_covariance", {"covariance_model_id": "sample_covariance"}, {}),
        ("ewma_covariance", {"covariance_model_id": "ewma_covariance"}, {}),
        ("signed", {"contribution_mode": "signed"}, {}),
        ("three_month_risk", {"lookback_days": 90}, {"lookback_days": 90}),
        ("fixed_gross_60pct", {}, {"capital_mode": "fixed_gross", "gross_exposure": 0.6, "target_volatility": None}),
        ("volatility_cap_3pct", {}, {"capital_mode": "volatility_cap", "target_volatility": 0.03}),
    ):
        inputs = {**base, **setting_change, "risk_model_config": normalize_portfolio_risk_policy({**wb["risk_policy"], **policy_change})}
        try:
            result = solve_current_target_weights(pid, **inputs)
            save(f"{pid}-{name}", result)
            results.append({"case": name, "solve": result["solve_event"]})
            print(name, "OK", result["solve_event"]["target_weight_total"], flush=True)
        except ValueError as error:
            results.append({"case": name, "error": str(error)})
            print(name, "UNAVAILABLE", str(error), flush=True)
    save(f"{pid}-scenario-summary", results)


def verify(pid):
    from portfolio_app.services.instrument_registry import get_registry_instrument_detail
    from portfolio_app.services.market_data import analytical_return_quote_bases, resolve_quote_series

    run = json.loads((OUTPUT / f"{pid}-api-run.json").read_text())
    portfolio = next(
        row for row in api("/portfolios") if row["portfolio_id"] == pid
    )
    base_currency = str(portfolio["base_currency"])
    backtest = run["detail"]["backtest"]
    assumptions = backtest["methodology"]["assumptions"]
    records = {r["actual_execution_date"]: r for r in backtest["execution_records"]}
    instruments = {r["instrument_id"] for e in records.values() for r in e["target_weights"]}
    prices = {}
    for instrument in instruments:
        detail = get_registry_instrument_detail(instrument)
        resolution = resolve_quote_series(detail, candidate_bases=analytical_return_quote_bases(detail),
                                          end_date=date.fromisoformat(backtest["end_date"]))
        assert resolution.available
        assert all(p["currency"] == base_currency for p in resolution.points), (
            f"Independent replay requires every analytical quote in portfolio base currency {base_currency}."
        )
        prices[instrument] = {p["as_of_date"].isoformat(): float(p["value"]) for p in resolution.points}
    quantities, cash, derivative = {}, 1.0, 0.0
    previous = date.fromisoformat(backtest["points"][0]["date"])
    evidence = []
    for point in backtest["points"][1:]:
        day = point["date"]
        elapsed = (date.fromisoformat(day) - previous).days
        cash *= (1 + assumptions["cash_yield_annual"]) ** (elapsed / 365.25)
        values = {key: quantity * prices[key][day] for key, quantity in quantities.items()}
        before = cash + derivative + sum(values.values())
        if day in records:
            record = records[day]
            assert abs(before - record["nav_before_execution"]) < 1e-10
            post_nav = record["nav_after_execution"]
            target = {r["instrument_id"]: r["target_weight"] * post_nav for r in record["target_weights"]}
            trades = {key: target.get(key, 0) - values.get(key, 0) for key in set(target) | set(values)}
            costs = (sum(abs(v) for v in trades.values()) * (assumptions["commission_bps"] + assumptions["slippage_bps"])
                     + sum(max(-v, 0) for v in trades.values()) * assumptions["tax_bps"]) / 10000
            assert abs(costs - record["total_cost"]) < 1e-10
            assert abs(before - costs - post_nav) < 1e-10
            derivative = record["derivative_target_weight"] * post_nav
            cash = before - costs - derivative - sum(target.values())
            quantities = {key: value / prices[key][day] for key, value in target.items()}
            values = target
        nav = cash + derivative + sum(values.values())
        assert cash >= -1e-10
        assert abs(nav - point["value"]) < 1e-10, (day, nav, point["value"])
        evidence.append({"date": day, "independent_nav": nav, "saved_nav": point["value"],
                         "difference": nav - point["value"], "cash": cash, "derivative_proxy": derivative})
        previous = date.fromisoformat(day)
    save(f"{pid}-independent-nav-reconciliation", evidence)
    print(pid, "RECONCILED", len(evidence), "NAV observations,", len(records), "executions; max error",
          max(abs(row["difference"]) for row in evidence), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("setup", "run", "scenarios", "verify"))
    parser.add_argument("portfolio_id", nargs="?")
    args = parser.parse_args()
    if args.mode == "setup":
        setup()
    elif not args.portfolio_id:
        parser.error("run/scenarios require a portfolio ID")
    elif args.mode == "run":
        run_api(args.portfolio_id)
    elif args.mode == "verify":
        verify(args.portfolio_id)
    else:
        scenarios(args.portfolio_id)
