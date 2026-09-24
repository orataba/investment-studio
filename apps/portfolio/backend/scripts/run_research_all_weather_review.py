"""Build and exercise a long-history USD risk-budget portfolio with derivatives.

The fixture is deliberately named as synthetic QA.  Its ETFs use FMP-adjusted
history, while FCNs and options are genuine Portfolio ledger contracts/events.
Research still treats the derivative sleeve as fixed-capital, zero-return proxy
capital; this script verifies that boundary rather than claiming a derivative
payoff backtest.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date
import json

from run_research_review import OUTPUT, api, save, summarize, verify


FIXTURE_NAME = "Research Global All Weather FCN Option QA 20260905"
ETF_IDS = ("spy", "vea", "vwo", "tlt", "ief", "tip", "gld", "dbc", "vnq")
START = "2010-01-04"
AS_OF = "2026-09-03"


def _instrument_detail(instrument_id: str) -> dict[str, object]:
    from portfolio_app.services.instrument_registry import get_registry_instrument_detail

    detail = get_registry_instrument_detail(instrument_id)
    if detail is None:
        raise ValueError(f"Registry instrument not found: {instrument_id}")
    return detail


def _price(instrument_id: str, day: str) -> float:
    detail = _instrument_detail(instrument_id)
    matches = [
        row
        for row in list(detail.get("market_data") or [])
        if row.get("metric_family") == "price"
        and row.get("quote_basis") == "close"
        and str(row.get("as_of_date")) == day
        and row.get("status") == "complete"
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected one complete close for {instrument_id} on {day}; found {len(matches)}.")
    return float(matches[0]["value"])


def _coverage() -> dict[str, object]:
    result: dict[str, object] = {}
    for instrument_id in ETF_IDS:
        detail = _instrument_detail(instrument_id)
        rows = [
            row
            for row in list(detail.get("market_data") or [])
            if row.get("metric_family") == "price"
            and row.get("quote_basis") == "adjusted_close"
            and row.get("status") == "complete"
        ]
        dates = sorted(str(row["as_of_date"]) for row in rows)
        providers = sorted({str(row.get("provider") or "") for row in rows})
        if not dates or dates[0] > START or dates[-1] < AS_OF:
            raise ValueError(f"Insufficient adjusted-close coverage for {instrument_id}: {dates[:1]} to {dates[-1:]}")
        result[instrument_id] = {
            "name": detail["instrument_name"],
            "currency": detail["currency"],
            "exchange_code": detail["exchange_code"],
            "observations": len(rows),
            "first_date": dates[0],
            "last_date": dates[-1],
            "providers": providers,
            "return_quote_priority": list((detail.get("quote_selection_policy") or {}).get("total_return") or []),
        }
    return result


def _find_existing() -> str | None:
    matches = [row for row in api("/portfolios") if row["portfolio_name"] == FIXTURE_NAME]
    if len(matches) > 1:
        raise ValueError(f"More than one portfolio is named {FIXTURE_NAME!r}.")
    return str(matches[0]["portfolio_id"]) if matches else None


def setup() -> str:
    existing = _find_existing()
    coverage = _coverage()
    if existing is not None:
        pid = existing
        portfolio = next(row for row in api("/portfolios") if row["portfolio_id"] == pid)
        prefix = f"/portfolios/{pid}"
        existing_accounts = api(prefix + "/accounts")["accounts"]
        existing_transactions = api(prefix + "/transactions")["transactions"]
        existing_taxonomy_state = api(prefix + "/taxonomies")
        taxonomies = existing_taxonomy_state["taxonomies"]
        if taxonomies and existing_taxonomy_state["target_sets"]:
            print("EXISTING", existing, flush=True)
            return existing
        print("RESUMING", existing, len(existing_transactions), "existing facts", flush=True)
    else:
        portfolio = api(
            "/portfolios",
            {"name": FIXTURE_NAME, "base_currency": "USD", "inception_date": START},
        )
        pid = str(portfolio["portfolio_id"])
        prefix = f"/portfolios/{pid}"
        existing_accounts = []
        existing_transactions = []
        existing_taxonomy_state = None
        print("CREATED", pid, flush=True)
    receipt: dict[str, object] = {
        "synthetic_fixture": True,
        "purpose": "Long-history Research, recursive risk-budget, rebalance, FCN, and option QA only.",
        "portfolio": portfolio,
        "fmp_adjusted_close_coverage": coverage,
        "accounts": existing_accounts,
        "transactions": existing_transactions,
    }
    save(f"{pid}-all-weather-setup", receipt)

    accounts: dict[str, str] = {
        str(row["account_category"]): str(row["account_id"])
        for row in existing_accounts
    }
    for category in ("cash", "security", "fcn", "option"):
        if category in accounts:
            continue
        payload: dict[str, object] = {
            "account_name": f"All Weather QA {category}",
            "account_category": category,
            "currency": "USD",
            "institution": "Synthetic QA Broker",
            "opened_at": START,
        }
        if category != "cash":
            payload["default_settlement_cash_account_id"] = accounts["cash"]
        account = api(prefix + "/accounts", payload)
        accounts[category] = str(account["account_id"])
        receipt["accounts"].append(account)  # type: ignore[union-attr]
        save(f"{pid}-all-weather-setup", receipt)

    def txn(
        reference: str,
        category: str,
        transaction_type: str,
        gross_amount: float,
        day: str,
        **extra: object,
    ) -> dict[str, object]:
        if reference in existing_by_reference:
            row = existing_by_reference[reference]
            print("EXISTING FACT", reference, row["transaction_id"], flush=True)
            return row
        payload: dict[str, object] = {
            "transaction_type": transaction_type,
            "trade_date": day,
            "settlement_date": day,
            "account_id": accounts[category],
            "gross_amount": gross_amount,
            "currency": "USD",
            "source_system": "research-all-weather-qa",
            "external_reference": reference,
            "note": "Synthetic long-history Research QA; never a real order or product return.",
            **extra,
        }
        if category != "cash" and payload.get("lifecycle_event_type") != "option_writer_expiry":
            payload["settlement_cash_account_id"] = accounts["cash"]
        row = api(prefix + "/transactions", payload)
        receipt["transactions"].append(row)  # type: ignore[union-attr]
        save(f"{pid}-all-weather-setup", receipt)
        print("FACT", reference, row["transaction_id"], flush=True)
        return row

    existing_by_reference = {
        str(row["external_reference"]): row
        for row in existing_transactions
        if row.get("external_reference")
    }

    txn("all-weather-funding", "cash", "deposit", 10_000_000, START)
    initial_allocations = {
        "spy": 900_000,
        "vea": 600_000,
        "vwo": 375_000,
        "tlt": 1_500_000,
        "ief": 1_125_000,
        "tip": 900_000,
        "gld": 900_000,
        "dbc": 600_000,
        "vnq": 600_000,
    }
    for instrument_id, amount in initial_allocations.items():
        price = _price(instrument_id, START)
        txn(
            f"initial-{instrument_id}",
            "security",
            "buy",
            amount,
            START,
            instrument_id=instrument_id,
            quantity=amount / price,
            price=price,
        )

    def fcn_contract(contract_id: str, name: str, issue: str, maturity: str, notional: float) -> dict[str, object]:
        return {
            "derivative_contract_id": contract_id,
            "contract_name": name,
            "contract_type": "fcn",
            "external_reference": contract_id.upper(),
            "terms": {
                "notional": notional,
                "annual_coupon_rate_pct": 8,
                "issue_date": issue,
                "maturity_date": maturity,
                "issuer": "Synthetic QA Bank",
                "counterparty": "Synthetic QA Broker",
                "underlyings": [
                    {
                        "instrument_id": "spy",
                        "initial_reference_price": _price("spy", issue),
                        "strike_level_pct": 80,
                        "knock_in_level_pct": 65,
                        "knock_out_level_pct": 100,
                        "deliverable": True,
                    }
                ],
            },
        }

    def option_contract(
        contract_id: str,
        name: str,
        option_type: str,
        issue: str,
        expiry: str,
        strike_multiple: float,
    ) -> dict[str, object]:
        return {
            "derivative_contract_id": contract_id,
            "contract_name": name,
            "contract_type": "option",
            "external_reference": contract_id.upper(),
            "terms": {
                "underlying_instrument_id": "spy",
                "option_type": option_type,
                "expiry_date": expiry,
                "strike": round(_price("spy", issue) * strike_multiple, 2),
                "contract_multiplier": 100,
            },
        }

    fcn_2015 = f"{pid}-fcn-2015"
    txn(
        "fcn-2015-entry", "fcn", "buy", 1_000_000, "2015-06-15",
        quantity=1, price=1_000_000, derivative_contract_id=fcn_2015,
        derivative_contract=fcn_contract(fcn_2015, "Synthetic 2015 SPY FCN", "2015-06-15", "2016-06-30", 1_000_000),
    )
    txn("fcn-2015-coupon-1", "fcn", "coupon", 40_000, "2015-12-15", derivative_contract_id=fcn_2015)
    txn("fcn-2015-coupon-2", "fcn", "coupon", 40_000, "2016-06-30", derivative_contract_id=fcn_2015)
    txn(
        "fcn-2015-redemption", "fcn", "maturity_redemption", 1_000_000, "2016-06-30",
        quantity=1, lifecycle_event_type="fcn_knock_out", derivative_contract_id=fcn_2015,
    )

    put_2018 = f"{pid}-put-2018"
    txn(
        "put-2018-entry", "option", "buy", 20_000, "2018-09-04",
        quantity=10, price=20, derivative_contract_id=put_2018,
        derivative_contract=option_contract(put_2018, "Synthetic 2018 SPY Put", "put", "2018-09-04", "2019-03-15", 0.90),
    )
    txn(
        "put-2018-cash-settlement", "option", "maturity_redemption", 250_000, "2019-03-15",
        quantity=10, lifecycle_event_type="option_long_cash_settlement", derivative_contract_id=put_2018,
    )

    fcn_2020 = f"{pid}-fcn-2020"
    txn(
        "fcn-2020-entry", "fcn", "buy", 1_200_000, "2020-04-01",
        quantity=1, price=1_200_000, derivative_contract_id=fcn_2020,
        derivative_contract=fcn_contract(fcn_2020, "Synthetic 2020 SPY FCN", "2020-04-01", "2021-04-01", 1_200_000),
    )
    txn("fcn-2020-coupon-1", "fcn", "coupon", 48_000, "2020-10-01", derivative_contract_id=fcn_2020)
    txn("fcn-2020-coupon-2", "fcn", "coupon", 48_000, "2021-04-01", derivative_contract_id=fcn_2020)
    txn(
        "fcn-2020-redemption", "fcn", "maturity_redemption", 1_200_000, "2021-04-01",
        quantity=1, lifecycle_event_type="fcn_knock_out", derivative_contract_id=fcn_2020,
    )

    call_2020 = f"{pid}-call-2020"
    txn(
        "call-2020-write", "option", "option_write", 50_000, "2020-06-01",
        quantity=20, price=25, derivative_contract_id=call_2020,
        derivative_contract=option_contract(call_2020, "Synthetic 2020 Written SPY Call", "call", "2020-06-01", "2020-12-18", 1.10),
    )
    txn(
        "call-2020-expiry", "option", "lifecycle_event", 0, "2020-12-18",
        quantity=20, lifecycle_event_type="option_writer_expiry", derivative_contract_id=call_2020,
    )

    fcn_open = f"{pid}-fcn-open"
    txn(
        "fcn-open-entry", "fcn", "buy", 1_500_000, "2024-01-02",
        quantity=1, price=1_500_000, derivative_contract_id=fcn_open,
        derivative_contract=fcn_contract(fcn_open, "Synthetic Open SPY FCN", "2024-01-02", "2027-01-04", 1_500_000),
    )
    for index, coupon_day in enumerate(("2024-07-02", "2025-01-02", "2025-07-02", "2026-01-02", "2026-07-02"), start=1):
        txn(f"fcn-open-coupon-{index}", "fcn", "coupon", 60_000, coupon_day, derivative_contract_id=fcn_open)

    put_open = f"{pid}-put-open"
    txn(
        "put-open-entry", "option", "buy", 80_000, "2025-12-01",
        quantity=20, price=40, derivative_contract_id=put_open,
        derivative_contract=option_contract(put_open, "Synthetic Open SPY Put", "put", "2025-12-01", "2027-01-15", 0.90),
    )
    call_open = f"{pid}-call-open"
    txn(
        "call-open-write", "option", "option_write", 25_000, "2026-06-01",
        quantity=10, price=25, derivative_contract_id=call_open,
        derivative_contract=option_contract(call_open, "Synthetic Open Written SPY Call", "call", "2026-06-01", "2027-01-15", 1.10),
    )

    top_specs = (
        ("Global Equity", 0.25, False),
        ("Nominal Bonds", 0.35, False),
        ("Inflation Hedges", 0.30, False),
        ("Real Assets", 0.10, True),
    )
    child_specs = {
        "Global Equity": (("US Equity", "spy", 0.50), ("Developed ex-US", "vea", 0.30), ("Emerging Markets", "vwo", 0.20)),
        "Nominal Bonds": (("Long Treasury", "tlt", 0.60), ("Intermediate Treasury", "ief", 0.40)),
        "Inflation Hedges": (("TIPS", "tip", 0.30), ("Gold", "gld", 0.40), ("Broad Commodities", "dbc", 0.30)),
    }
    if existing_taxonomy_state and existing_taxonomy_state["taxonomies"]:
        taxonomy = existing_taxonomy_state["taxonomies"][0]
        taxonomy_id = str(taxonomy["taxonomy_id"])
        if taxonomy.get("root_allocation_basis") != "risk_budget":
            taxonomy = api(
                prefix + f"/taxonomies/{taxonomy_id}",
                {
                    "root_allocation_basis": "risk_budget",
                },
                "PATCH",
            )
        nodes_by_name = {
            str(row["node_name"]): row
            for row in existing_taxonomy_state["taxonomy_nodes"]
            if row.get("status") == "active"
        }
        top_nodes = {name: nodes_by_name[name] for name, _, _ in top_specs}
        child_nodes = {
            parent_name: [
                (nodes_by_name[name], risk_share)
                for name, _, risk_share in children
            ]
            for parent_name, children in child_specs.items()
        }
        expected_assignments = {
            instrument_id
            for children in child_specs.values()
            for _, instrument_id, _ in children
        } | {"vnq"}
        actual_assignments = {
            str(row["target_entity_id"])
            for row in existing_taxonomy_state["taxonomy_assignments"]
            if row.get("status") == "active"
        }
        if actual_assignments != expected_assignments:
            raise ValueError(
                f"Existing taxonomy assignments are incomplete: expected {sorted(expected_assignments)}, "
                f"found {sorted(actual_assignments)}."
            )
        print("RESUMING TAXONOMY", taxonomy_id, flush=True)
    else:
        taxonomy = api(
            prefix + "/taxonomies",
            {
                "name": "Global All Weather Risk Budget QA",
                "root_allocation_basis": "risk_budget",
                "purpose": "Synthetic current hierarchical risk budgets with actual dated derivative capital.",
            },
        )
        taxonomy_id = str(taxonomy["taxonomy_id"])
        top_nodes: dict[str, dict[str, object]] = {}
        for sort_order, (name, _, terminal) in enumerate(top_specs, start=1):
            top_nodes[name] = api(
                prefix + f"/taxonomies/{taxonomy_id}/nodes",
                {
                    "node_name": name,
                    "sort_order": sort_order,
                    "is_terminal": terminal,
                    "allocation_basis": "risk_budget",
                },
            )
        child_nodes: dict[str, list[tuple[dict[str, object], float]]] = {}
        for parent_name, children in child_specs.items():
            parent_id = str(top_nodes[parent_name]["taxonomy_node_id"])
            child_nodes[parent_name] = []
            for sort_order, (name, instrument_id, risk_share) in enumerate(children, start=1):
                node = api(
                    prefix + f"/taxonomies/{taxonomy_id}/nodes",
                    {
                            "node_name": name,
                        "parent_taxonomy_node_id": parent_id,
                        "sort_order": sort_order,
                        "is_terminal": True,
                        "allocation_basis": "risk_budget",
                    },
                )
                api(
                    prefix + f"/taxonomies/{taxonomy_id}/assignments",
                    {
                            "target_scope": "instrument",
                        "target_entity_id": instrument_id,
                        "taxonomy_node_id": node["taxonomy_node_id"],
                    },
                )
                child_nodes[parent_name].append((node, risk_share))

        api(
            prefix + f"/taxonomies/{taxonomy_id}/assignments",
            {
                "target_scope": "instrument",
                "target_entity_id": "vnq",
                "taxonomy_node_id": top_nodes["Real Assets"]["taxonomy_node_id"],
            },
        )

    risk_shares = {name: share for name, share, _ in top_specs}

    def root_lines() -> list[dict[str, object]]:
        return [{"target_member_type": "taxonomy_node",
                 "target_member_id": top_nodes[name]["taxonomy_node_id"],
                 "target_value": risk_share} for name, risk_share in risk_shares.items()] + [
            {"target_member_type": "cash_bucket", "target_member_id": "__cash__", "target_value": 0.05},
        ]

    root_target = api(
        prefix + f"/taxonomies/{taxonomy_id}/target-sets",
        {
            "target_set_type": "saa",
            "name": "All Weather top-level risk and fixed capital",
            "notes": "Risk budgets apply only to risky ETF sleeves; FCN/options are fixed-capital proxy exposure.",
            "lines": root_lines(),
        },
    )
    for parent_name, children in child_nodes.items():
        api(
            prefix + f"/taxonomies/{taxonomy_id}/target-sets",
            {
                "comparator_taxonomy_node_id": top_nodes[parent_name]["taxonomy_node_id"],
                "target_set_type": "saa",
                "name": f"{parent_name} child risk budgets",
                "lines": [
                    {
                        "target_member_type": "taxonomy_node",
                        "target_member_id": node["taxonomy_node_id"],
                        "target_value": risk_share,
                    }
                    for node, risk_share in children
                ],
            },
        )

    settings = api(
        prefix + "/research/settings",
        {
            "planning_taxonomy_id": taxonomy_id,
            "as_of_mode": "pinned",
            "as_of_date": AS_OF,
            "lookback_days": 180,
            "calculation_frequency": "daily",
            "missing_return_policy": "complete_case_drop",
            "covariance_model_id": "ewma_vol_shrinkage_corr_covariance",
            "contribution_mode": "signed",
            "capital_mode": "volatility_cap",
            "target_volatility": 0.10,
            "frozen_taxonomy_node_ids": [],
            "top_sleeve_weight_bounds": [],
            "backtest_rebalance_frequency": "1m",
            "backtest_cash_yield_annual": 0.02,
            "backtest_commission_bps": 1,
            "backtest_tax_bps": 0,
            "backtest_slippage_bps": 2,
            "backtest_implementation_delay_days": 1,
            "notes": "Synthetic global all-weather QA. Derivatives reserve capital but have zero modeled Research returns; ledger cashflows remain real facts.",
        },
        "PUT",
    )
    receipt.update(
        taxonomy=taxonomy,
        top_nodes=top_nodes,
        child_nodes=child_nodes,
        root_target_set=root_target,
        settings=settings,
    )
    save(f"{pid}-all-weather-setup", receipt)
    print("CONFIGURED", pid, taxonomy_id, flush=True)
    return pid


def run(pid: str) -> None:
    prefix = f"/portfolios/{pid}"
    save(f"{pid}-before", api(prefix + "/research/workbench"))
    result = api(prefix + "/research/runs", {"requested_by": "research-all-weather-review-20260905"})
    save(f"{pid}-api-run", result)
    save(f"{pid}-after", api(prefix + "/research/workbench"))
    print(json.dumps(summarize(result), ensure_ascii=False), flush=True)


def scenarios(pid: str) -> None:
    from portfolio_app.services.research_solver import build_current_target_backtest, solve_current_target_weights
    from portfolio_app.services.risk_model import normalize_portfolio_risk_policy

    wb = api(f"/portfolios/{pid}/research/workbench")
    settings = wb["settings"]
    taxonomy = api(f"/portfolios/{pid}/taxonomies")
    top_nodes = {
        row["node_name"]: row["taxonomy_node_id"]
        for row in taxonomy["taxonomy_nodes"]
        if row.get("parent_taxonomy_node_id") is None
        and row.get("status") == "active"
    }
    base = {key: deepcopy(settings.get(key)) for key in (
        "planning_taxonomy_id", "comparator_taxonomy_node_id", "lookback_days", "calculation_frequency",
        "missing_return_policy", "capital_mode", "gross_exposure", "target_volatility",
        "max_gross_exposure", "frozen_taxonomy_node_ids", "top_sleeve_weight_bounds",
    )}
    base.update(
        as_of_date=date.fromisoformat(settings["as_of_date"]),
        risk_model_config=wb["risk_policy"],
        _instrument_detail_cache={},
    )
    results: list[dict[str, object]] = []

    baseline = solve_current_target_weights(pid, **base)
    save(f"{pid}-all-weather-baseline", baseline)
    results.append({"case": "baseline", "solve": baseline["solve_event"]})
    base_vol = float(baseline["solve_event"]["estimated_risk_sleeve_volatility"])

    cases = (
        ("sample_covariance", {"covariance_model_id": "sample_covariance"}, {}),
        ("ewma_covariance", {"covariance_model_id": "ewma_covariance"}, {}),
        ("absolute_contribution", {"contribution_mode": "abs"}, {}),
        ("fixed_gross_70pct", {}, {"capital_mode": "fixed_gross", "gross_exposure": 0.70, "target_volatility": None}),
        (
            "bounded_top_sleeves",
            {},
            {
                "top_sleeve_weight_bounds": [
                    {"taxonomy_node_id": top_nodes["Global Equity"], "min_weight": 0.10, "max_weight": 0.35},
                    {"taxonomy_node_id": top_nodes["Nominal Bonds"], "min_weight": 0.20, "max_weight": 0.50},
                    {"taxonomy_node_id": top_nodes["Inflation Hedges"], "min_weight": 0.15, "max_weight": 0.40},
                    {"taxonomy_node_id": top_nodes["Real Assets"], "min_weight": 0.03, "max_weight": 0.20},
                ]
            },
        ),
        ("frozen_nominal_bonds", {}, {"frozen_taxonomy_node_ids": [top_nodes["Nominal Bonds"]]}),
        (
            "reachable_target_volatility",
            {},
            {
                "capital_mode": "target_volatility",
                "gross_exposure": None,
                "target_volatility": base_vol * 1.10,
                "max_gross_exposure": 1.20,
            },
        ),
    )
    for name, policy_change, setting_change in cases:
        inputs = {
            **base,
            **setting_change,
            "risk_model_config": normalize_portfolio_risk_policy({**wb["risk_policy"], **policy_change}),
        }
        try:
            result = solve_current_target_weights(pid, **inputs)
            save(f"{pid}-{name}", result)
            results.append({"case": name, "solve": result["solve_event"]})
            print(name, "OK", result["solve_event"]["execution_ready"], flush=True)
        except ValueError as error:
            results.append({"case": name, "error": str(error)})
            print(name, "UNAVAILABLE", str(error), flush=True)

    try:
        solve_current_target_weights(
            pid,
            **{
                **base,
                "capital_mode": "target_volatility",
                "gross_exposure": None,
                "target_volatility": base_vol * 2,
                "max_gross_exposure": 1.0,
            },
        )
        impossible = {"case": "unreachable_target_volatility", "error": "expected rejection did not occur"}
    except ValueError as error:
        impossible = {"case": "unreachable_target_volatility", "expected_error": str(error)}
    results.append(impossible)
    save(f"{pid}-unreachable_target_volatility", impossible)

    quarterly = build_current_target_backtest(
        pid,
        **base,
        rebalance_frequency="3m",
        cash_yield_annual=0.02,
        commission_bps=5,
        tax_bps=0,
        slippage_bps=10,
        implementation_delay_days=5,
    )
    save(f"{pid}-quarterly-severe-friction", quarterly)
    results.append({"case": "quarterly_severe_friction", "backtest": summarize(quarterly)["backtest"]})
    print("quarterly_severe_friction", len(quarterly["backtest"]["execution_records"]), "executions", flush=True)

    series_dates: dict[str, set[str]] = {}
    for instrument_id in ETF_IDS:
        detail = _instrument_detail(instrument_id)
        series_dates[instrument_id] = {
            str(row["as_of_date"])
            for row in list(detail.get("market_data") or [])
            if row.get("metric_family") == "price"
            and row.get("quote_basis") == "adjusted_close"
            and row.get("status") == "complete"
            and START <= str(row["as_of_date"]) <= AS_OF
        }
    union = set().union(*series_dates.values())
    intersection = set.intersection(*series_dates.values())
    natural_gaps = {
        "union_date_count": len(union),
        "complete_case_date_count": len(intersection),
        "dates_dropped_by_complete_case": len(union - intersection),
        "missing_by_instrument": {
            instrument_id: {
                "count": len(union - dates),
                "sample": sorted(union - dates)[:20],
            }
            for instrument_id, dates in series_dates.items()
        },
    }
    save(f"{pid}-natural-fmp-date-gaps", natural_gaps)
    results.append({"case": "natural_fmp_date_gaps", **natural_gaps})
    save(f"{pid}-all-weather-scenario-summary", results)


def derivative_boundary(pid: str) -> None:
    from portfolio_app.services.research_solver import solve_current_target_weights

    run_result = json.loads((OUTPUT / f"{pid}-api-run.json").read_text())
    detail = run_result["detail"]
    derivative_rows = [
        row
        for row in detail["leaf_targets"]
        if row.get("member_type") in {"derivative_bucket", "cash_bucket"}
    ]
    wb = api(f"/portfolios/{pid}/research/workbench")
    settings = wb["settings"]
    direct_solution = solve_current_target_weights(
        pid,
        planning_taxonomy_id=settings["planning_taxonomy_id"],
        comparator_taxonomy_node_id=settings["comparator_taxonomy_node_id"],
        as_of_date=date.fromisoformat(settings["as_of_date"]),
        lookback_days=settings["lookback_days"],
        calculation_frequency=settings["calculation_frequency"],
        missing_return_policy=settings["missing_return_policy"],
        capital_mode=settings["capital_mode"],
        gross_exposure=settings["gross_exposure"],
        target_volatility=settings["target_volatility"],
        max_gross_exposure=settings["max_gross_exposure"],
        frozen_taxonomy_node_ids=settings["frozen_taxonomy_node_ids"],
        top_sleeve_weight_bounds=settings["top_sleeve_weight_bounds"],
        risk_model_config=wb["risk_policy"],
        _instrument_detail_cache={},
    )
    observations = direct_solution["return_observations"]
    derivative_contract_ids = {
        row.get("derivative_contract_id")
        for row in api(f"/portfolios/{pid}/transactions")["transactions"]
        if row.get("derivative_contract_id")
    }
    encoded_observations = json.dumps(observations, ensure_ascii=False)
    checks = {
        "derivative_and_cash_target_rows": derivative_rows,
        "derivative_contract_ids": sorted(derivative_contract_ids),
        "derivative_contracts_absent_from_return_matrix": all(
            contract_id not in encoded_observations for contract_id in derivative_contract_ids
        ),
        "all_fixed_capital_rows_have_no_risk_share": all(
            row.get("target_risk_share") is None for row in derivative_rows
        ),
        "backtest_methodology": detail["backtest"]["methodology"],
    }
    if not checks["derivative_contracts_absent_from_return_matrix"]:
        raise AssertionError("A derivative contract entered the Research return matrix.")
    if not checks["all_fixed_capital_rows_have_no_risk_share"]:
        raise AssertionError("Derivative/cash proxy capital received a risk-budget contribution.")
    save(f"{pid}-derivative-boundary", checks)
    print("DERIVATIVE BOUNDARY VERIFIED", len(derivative_contract_ids), "contracts", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("setup", "run", "scenarios", "verify", "derivative-boundary"))
    parser.add_argument("portfolio_id", nargs="?")
    args = parser.parse_args()
    if args.mode == "setup":
        setup()
    elif not args.portfolio_id:
        parser.error("run/scenarios/verify/derivative-boundary require a portfolio ID")
    elif args.mode == "run":
        run(args.portfolio_id)
    elif args.mode == "scenarios":
        scenarios(args.portfolio_id)
    elif args.mode == "verify":
        verify(args.portfolio_id)
    else:
        derivative_boundary(args.portfolio_id)
