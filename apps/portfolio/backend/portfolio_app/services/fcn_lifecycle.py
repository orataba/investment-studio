from __future__ import annotations

from datetime import date
from math import isclose
from typing import Callable

from portfolio_app.services import valuation_fx
from portfolio_app.services.asset_deliveries import expand_asset_deliveries
from portfolio_app.services.instrument_registry import get_registry_instrument_detail, get_shared_fx_rates
from portfolio_app.services.ledger import build_position_lots
from portfolio_app.services.transaction_dates import (
    transaction_performance_effective_date,
    transaction_position_effective_date,
    transaction_precedes_asset_cash_flow,
)


def _number(value: object) -> float:
    return float(value or 0)


def _date(value: object) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _sum(values: list[float | None]) -> float | None:
    return None if any(value is None for value in values) else sum(values)


def _origin_basis(lot: dict, origin_ids: set[str]) -> float:
    return sum(
        _number(origin.get("remaining_cost_basis"))
        for origin in lot.get("cost_basis_origins") or []
        if origin.get("origin_transaction_id") in origin_ids
    )


def build_fcn_lifecycles(
    *,
    portfolio_id: str,
    position_reference_id: str,
    accounts: list[dict],
    transactions: list[dict],
    contracts: list[dict],
    as_of_date: date,
    position_lots: list[dict] | None = None,
    resolve_rate: Callable[[str, str, date], float | None] | None = None,
) -> dict:
    """Read-only attribution using the ledger's actual released/remaining origins.

    The reporting currency is the FCN currency. The delivery's confirmed value
    closes the FCN and starts its stock phase; actual later cash and marks use
    historical/current market FX. Acquisition charges are already in stock basis.
    """
    fcn_contracts = [contract for contract in contracts if contract.get("contract_type") == "fcn"]
    account_names = {str(account["account_id"]): account.get("account_name") or account["account_id"] for account in accounts}
    fcn_ids = {contract["derivative_contract_id"] for contract in fcn_contracts}
    relevant_ids = {
        str(tx.get("derivative_contract_id"))
        for tx in transactions
        if tx.get("derivative_contract_id") in fcn_ids
        and (
            tx.get("derivative_contract_id") == position_reference_id
            or any(leg.get("instrument_id") == position_reference_id for leg in tx.get("asset_deliveries") or [])
        )
    }
    response = {"portfolio_id": portfolio_id, "position_reference_id": position_reference_id, "as_of_date": as_of_date.isoformat(), "lifecycles": []}
    if not relevant_ids:
        return response

    lots = position_lots if position_lots is not None else build_position_lots(
        portfolio_id, accounts, transactions, as_of_date=as_of_date,
    )
    detail_cache: dict = {}
    direct_fx: dict | None = None
    rate_cache: dict[tuple[str, str, date], float | None] = {}

    def rate(currency: str, target: str, on: date) -> float | None:
        nonlocal direct_fx
        if currency == target:
            return 1.0
        key = (currency, target, on)
        if key not in rate_cache:
            if resolve_rate is not None:
                rate_cache[key] = resolve_rate(currency, target, on)
            else:
                if direct_fx is None:
                    direct_fx = valuation_fx.fx_direct_instrument_map(get_shared_fx_rates())
                resolved = valuation_fx.resolve_fx_rate_on(
                    as_of_date=on, base_currency=currency, quote_currency=target,
                    direct_instruments=direct_fx, instrument_detail_cache=detail_cache,
                    instrument_detail_loader=get_registry_instrument_detail,
                )
                rate_cache[key] = float(resolved["rate"]) if resolved else None
        return rate_cache[key]

    income_snapshots: dict[str, list[dict]] = {}
    expanded = expand_asset_deliveries(transactions)
    source_entries: dict[tuple[str, str, str], dict] = {}
    for fact in expanded:
        if not fact.get("instrument_id") or fact.get("transaction_type") not in {"buy", "opening_balance", "dividend_reinvestment"}:
            continue
        key = (str(fact["transaction_id"]), str(fact["instrument_id"]), str(fact["currency"]))
        entry = source_entries.setdefault(key, {
            "quantity": 0.0, "basis": 0.0,
            "effective_date": transaction_position_effective_date(fact),
        })
        entry["quantity"] += _number(fact.get("quantity"))
        entry["basis"] += _number(fact.get("gross_amount"))
        if fact["transaction_type"] == "buy":
            entry["basis"] += _number(fact.get("fees")) + _number(fact.get("taxes"))
    # The ledger retains each split's actual before/after unit costs even when
    # its successor lot later absorbs purchases. Their ratio also preserves
    # confirmed aggregate rounding; original purchase quantities alone do not.
    split_events: dict[tuple[str, str], dict] = {}
    for lot in lots:
        event = lot.get("corporate_action_event") or {}
        if event.get("action_type") != "share_split":
            continue
        key = (str(lot["instrument_id"]), str(event["corporate_action_event_id"]))
        split = split_events.setdefault(key, {"date": _date(event["effective_date"]), "factors": []})
        before, after = lot.get("unit_cost_basis_before"), lot.get("unit_cost_basis_after")
        if before is not None and after is not None and float(after) > 0:
            split["factors"].append(float(before) / float(after))
    for contract in fcn_contracts:
        contract_id = contract["derivative_contract_id"]
        if contract_id not in relevant_ids:
            continue
        currency = str(contract["currency"])
        warnings: set[str] = set()

        def convert(amount: float, source: str, on: date) -> float | None:
            if abs(amount) < 1e-12:
                return 0.0
            resolved = rate(source, currency, on)
            if resolved is None:
                warnings.add(f"Missing {source}/{currency} FX on {on.isoformat()}")
                return None
            return amount * resolved

        contract_transactions = [tx for tx in transactions if tx.get("derivative_contract_id") == contract_id]
        recognized = [tx for tx in contract_transactions if (transaction_performance_effective_date(tx) or _date(tx["trade_date"])) <= as_of_date]
        if not recognized:
            continue
        contract_lots = [lot for lot in lots if lot.get("derivative_contract_id") == contract_id]
        contract_disposal = sum(_number(lot.get("realized_pnl")) for lot in contract_lots)
        coupon_values: list[float | None] = []
        contract_charges: list[float | None] = []
        deliveries: list[dict] = []
        # Each origin retains its original cost through partial sales, transfers,
        # and pooled average-cost lots. Preserve the confirmed FX for that basis.
        origin_values: dict[tuple[str, str, str], dict] = {}
        for tx in recognized:
            event_date = transaction_performance_effective_date(tx) or _date(tx["trade_date"])
            transaction_type = tx.get("transaction_type")
            if transaction_type == "coupon":
                coupon_values.append(convert(_number(tx.get("gross_amount")), str(tx["currency"]), event_date))
            # Event-valued FCN acquisition charges are expensed immediately;
            # disposal charges already reduce the ledger's realized proceeds.
            if transaction_type in {"buy", "coupon", "fee", "tax"}:
                charge = _number(tx.get("fees")) + _number(tx.get("taxes"))
                if transaction_type in {"fee", "tax"}:
                    charge += _number(tx.get("gross_amount"))
                contract_charges.append(convert(charge, str(tx["currency"]), event_date))
            effective_date = transaction_position_effective_date(tx) or event_date
            for leg in tx.get("asset_deliveries") or []:
                if effective_date > as_of_date:
                    continue
                source_currency = str(leg["currency"])
                charges = _number(leg.get("fees")) + _number(leg.get("taxes"))
                charge_value = convert(charges, source_currency, effective_date)
                fair_value = _number(leg["fair_value"])
                confirmed_value = fair_value * _number(leg["fx_rate_to_contract"])
                origin_key = (str(tx["transaction_id"]), str(leg["instrument_id"]), source_currency)
                origin_value = origin_values.setdefault(origin_key, {"cost": 0.0, "contract_values": []})
                origin_value["cost"] += fair_value + charges
                origin_value["contract_values"].append(None if charge_value is None else confirmed_value + charge_value)
                delivery_date = _date(leg["delivery_date"]) if leg.get("delivery_date") else None
                instrument = leg.get("instrument_ref") or {}
                deliveries.append({
                    "transaction_id": tx["transaction_id"], "instrument_id": leg["instrument_id"],
                    "instrument_name": instrument.get("instrument_name") or instrument.get("display_name") or instrument.get("name") or leg["instrument_id"],
                    "account_id": leg["account_id"], "account_name": account_names.get(str(leg["account_id"]), leg["account_id"]), "currency": source_currency,
                    "quantity": _number(leg["quantity"]), "fair_value": fair_value,
                    "capitalized_charges": charges, "effective_date": effective_date.isoformat(),
                    "delivery_date": delivery_date.isoformat() if delivery_date else None,
                    "status": "unknown" if delivery_date is None else "pending" if delivery_date > as_of_date else "delivered",
                })
        # Cash components can have a recognition date independent of the parent.
        for tx in contract_transactions:
            for cashflow in tx.get("settlement_cashflows") or []:
                on = _date(cashflow.get("recognition_date") or tx.get("position_effective_date") or tx["trade_date"])
                if on > as_of_date:
                    continue
                value = convert(_number(cashflow.get("amount")), str(cashflow["currency"]), on)
                (coupon_values if cashflow["kind"] == "coupon" else contract_charges).append(value)

        origin_ids = {key[0] for key in origin_values}

        def quantity_share(origins: list[dict], instrument_id: str, source: str, field: str, on: date) -> float | None:
            """Recover source units before allocating pooled shares or proceeds.

            Cost-origin proportions alone are not quantity proportions when
            acquisitions had different prices. Apply splits only to origins
            already held then, and only through this realization/valuation date.
            """
            matched_units = total_units = 0.0
            for origin in origins:
                amount = _number(origin.get(field))
                if amount <= 0:
                    continue
                entry = source_entries.get((str(origin.get("origin_transaction_id")), instrument_id, source))
                if entry is None or entry["basis"] <= 0:
                    warnings.add("Source share quantities are unavailable for a pooled lot")
                    return None
                units = amount * entry["quantity"] / entry["basis"]
                for (split_instrument, _event_id), split in split_events.items():
                    if split_instrument != instrument_id or not entry["effective_date"] < split["date"] <= on:
                        continue
                    factors = split["factors"]
                    if not factors or any(not isclose(factor, factors[0], rel_tol=1e-9) for factor in factors):
                        warnings.add("Source share quantities are unavailable for a pooled lot")
                        return None
                    units *= factors[0]
                total_units += units
                if origin.get("origin_transaction_id") in origin_ids:
                    matched_units += units
            return matched_units / total_units if total_units > 0 else None

        def basis_in_contract(origins: list[dict], instrument_id: str, source: str, field: str) -> float | None:
            values: list[float | None] = []
            for origin in origins:
                origin_id = origin.get("origin_transaction_id")
                if origin_id not in origin_ids:
                    continue
                amount = _number(origin.get(field))
                if abs(amount) < 1e-12:
                    continue
                initial = origin_values.get((str(origin_id), instrument_id, source))
                if initial is None or initial["cost"] <= 0:
                    warnings.add("Delivery origin basis is unavailable in the current security currency")
                    return None
                initial_value = _sum(initial["contract_values"])
                values.append(None if initial_value is None else amount / initial["cost"] * initial_value)
            return _sum(values)

        stock_rows: dict[tuple[str, str, str], dict] = {}
        for lot in lots:
            if not lot.get("instrument_id") or lot.get("position_side") == "short":
                continue
            matched_basis = _origin_basis(lot, origin_ids)
            relevant_realizations = [
                realization for realization in lot.get("realizations") or []
                if any(origin.get("origin_transaction_id") in origin_ids for origin in realization.get("cost_basis_origins") or [])
            ]
            if matched_basis <= 0 and not relevant_realizations:
                continue
            row_key = (str(lot["account_id"]), str(lot["instrument_id"]), str(lot["currency"]))
            instrument = lot.get("instrument_ref") or {}
            row = stock_rows.setdefault(row_key, {
                "account_id": lot["account_id"], "instrument_id": lot["instrument_id"],
                "account_name": account_names.get(str(lot["account_id"]), lot["account_id"]),
                "instrument_name": instrument.get("instrument_name") or instrument.get("display_name") or instrument.get("name") or lot["instrument_id"],
                "currency": lot["currency"], "remaining_quantity": 0.0, "realized_quantity": 0.0,
                "remaining_cost_basis": 0.0, "realized_values": [], "unrealized_values": [], "market_values": [],
            })
            if matched_basis > 0:
                share = quantity_share(lot.get("cost_basis_origins") or [], str(lot["instrument_id"]), str(lot["currency"]), "remaining_cost_basis", as_of_date)
                if share is None:
                    row["unrealized_values"].append(None)
                    row["market_values"].append(None)
                    row["remaining_quantity"] = None
                else:
                    if row["remaining_quantity"] is not None:
                        row["remaining_quantity"] += _number(lot.get("remaining_quantity")) * share
                    row["remaining_cost_basis"] += matched_basis
                    mark = lot.get("current_market_value")
                    market_value = None if mark is None else convert(float(mark) * share, str(lot["currency"]), as_of_date)
                    basis_value = basis_in_contract(lot.get("cost_basis_origins") or [], str(lot["instrument_id"]), str(lot["currency"]), "remaining_cost_basis")
                    row["market_values"].append(market_value)
                    row["unrealized_values"].append(None if market_value is None or basis_value is None else market_value - basis_value)
                    if mark is None:
                        warnings.add(f"Missing valuation for {lot['instrument_id']}")
            for realization in relevant_realizations:
                origins = realization.get("cost_basis_origins") or []
                total_basis = _number(realization.get("cost_basis_released"))
                if total_basis <= 0:
                    warnings.add("Realization has no attributable cost basis")
                    row["realized_values"].append(None)
                    continue
                share = quantity_share(origins, str(lot["instrument_id"]), str(lot["currency"]), "cost_basis_released", _date(realization["position_effective_date"]))
                if share is None:
                    row["realized_values"].append(None)
                    row["realized_quantity"] = None
                    continue
                if row["realized_quantity"] is not None:
                    row["realized_quantity"] += _number(realization.get("quantity")) * share
                proceeds = realization.get("proceeds")
                converted_proceeds = None if proceeds is None else convert(float(proceeds) * share, str(lot["currency"]), _date(realization["position_effective_date"]))
                released_basis = basis_in_contract(origins, str(lot["instrument_id"]), str(lot["currency"]), "cost_basis_released")
                row["realized_values"].append(None if converted_proceeds is None or released_basis is None else converted_proceeds - released_basis)

        stock_income_values: list[float | None] = []
        delivery_instruments = {leg["instrument_id"] for leg in deliveries} | {row["instrument_id"] for row in stock_rows.values()}
        for tx in transactions:
            if tx.get("instrument_id") not in delivery_instruments or tx.get("transaction_type") not in {"dividend", "coupon", "fee", "tax", "dividend_reinvestment", "return_of_capital"}:
                continue
            on = transaction_performance_effective_date(tx) or _date(tx["trade_date"])
            if on > as_of_date:
                continue
            tx_id = str(tx["transaction_id"])
            if tx_id not in income_snapshots:
                income_snapshots[tx_id] = build_position_lots(
                    portfolio_id, accounts,
                    [fact for fact in expanded if transaction_precedes_asset_cash_flow(fact, tx)],
                    as_of_date=on, account_id=str(tx["account_id"]),
                    position_reference_id=str(tx["instrument_id"]), resolve_pricing=False,
                )
            entitled = income_snapshots[tx_id]
            total_quantity = sum(_number(lot.get("remaining_quantity")) for lot in entitled)
            source_quantities: list[float | None] = []
            for lot in entitled:
                if _origin_basis(lot, origin_ids) <= 0:
                    continue
                share = quantity_share(lot.get("cost_basis_origins") or [], str(lot["instrument_id"]), str(lot["currency"]), "remaining_cost_basis", on)
                source_quantities.append(None if share is None else _number(lot.get("remaining_quantity")) * share)
            source_quantity = _sum(source_quantities)
            if source_quantity is None:
                stock_income_values.append(None)
                continue
            if source_quantity <= 0 or total_quantity <= 0:
                continue
            if tx["transaction_type"] in {"dividend_reinvestment", "return_of_capital"}:
                warnings.add("Stock reinvestment or capital repayment attribution requires review")
                stock_income_values.append(None)
                continue
            amount = _number(tx.get("gross_amount")) * (-1 if tx["transaction_type"] in {"fee", "tax"} else 1)
            amount -= _number(tx.get("fees")) + _number(tx.get("taxes"))
            stock_income_values.append(convert(amount * source_quantity / total_quantity, str(tx["currency"]), on))

        rendered_stocks = []
        for row in stock_rows.values():
            rendered_stocks.append({
                **{key: value for key, value in row.items() if not key.endswith("_values")},
                "current_market_value": _sum(row["market_values"]),
                "realized_pnl": _sum(row["realized_values"]),
                "unrealized_pnl": _sum(row["unrealized_values"]),
            })
        coupons = _sum(coupon_values)
        charges = _sum(contract_charges)
        contract_pnl = None if coupons is None or charges is None else contract_disposal + coupons - charges
        realized_pnl = _sum([row["realized_pnl"] for row in rendered_stocks])
        unrealized_pnl = _sum([row["unrealized_pnl"] for row in rendered_stocks])
        stock_income = _sum(stock_income_values)
        stock_pnl = _sum([realized_pnl, unrealized_pnl, stock_income])
        if any(value["cost"] <= 0 for value in origin_values.values()):
            # Zero-value deliveries are valid facts, but a cost-origin ledger
            # cannot recover their source units after pooling/disposal. Do not
            # silently report the untracked stock phase as zero profit.
            warnings.add("Source share quantities are unavailable for a pooled lot")
            realized_pnl = unrealized_pnl = stock_income = stock_pnl = None
        response["lifecycles"].append({
            "derivative_contract_id": contract_id, "contract_name": contract["contract_name"], "currency": currency,
            "contract_status": "open" if any(_number(lot.get("remaining_quantity")) > 0 for lot in contract_lots) else "closed",
            "contract_disposal_pnl": contract_disposal, "coupon_income": coupons, "contract_charges": charges,
            "contract_pnl": contract_pnl, "stock_realized_pnl": realized_pnl, "stock_unrealized_pnl": unrealized_pnl,
            "stock_income": stock_income, "stock_pnl": stock_pnl, "total_pnl": _sum([contract_pnl, stock_pnl]),
            "deliveries": deliveries, "stocks": rendered_stocks, "warnings": sorted(warnings),
        })
    return response
