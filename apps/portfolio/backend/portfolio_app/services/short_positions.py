"""Use the same FIFO/average-cost engine for the liability side of stock lots.

Short opening proceeds are mirrored acquisitions; covers are mirrored disposals.
Their signed accounting results are reflected back, including both sets of fees.
Plain sales never create an unconfirmed short. Buys first cover an existing short.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import date

from portfolio_app.services.transaction_dates import transaction_position_effective_date, transaction_sort_key, transaction_precedes_entitlement_bod

SHORT_TRANSACTION_TYPES = {"short_sell", "buy_to_cover", "short_opening_balance"}


def partition_security_sides(transactions, *, corporate_actions, split_quantity):
    long_facts, short_facts = [], []
    quantities = defaultdict(float)
    side_history = defaultdict(list)
    timeline = [(False, tx) for tx in transactions] + [(True, event) for event in corporate_actions]

    def key(item):
        is_split, fact = item
        if is_split:
            return (str(fact["effective_date"]), 0, "", "", "", 0)
        effective = transaction_position_effective_date(fact)
        return (effective.isoformat() if effective else str(fact["trade_date"]), 1, *transaction_sort_key(fact)[1:])

    timeline.sort(key=key)
    for is_split, tx in timeline:
        if is_split:
            for position, quantity in list(quantities.items()):
                if position[1] == tx["instrument_id"]:
                    quantities[position] = (1 if quantity >= 0 else -1) * split_quantity(abs(quantity), tx)
            continue
        kind = str(tx.get("transaction_type") or "")
        instrument = tx.get("instrument_id")
        if not instrument:
            long_facts.append(tx)
            continue
        position = (tx["account_id"], instrument)
        held = quantities[position]
        quantity = float(tx.get("quantity") or 0)
        gross = float(tx.get("gross_amount") or 0)
        fees = float(tx.get("fees") or 0)
        taxes = float(tx.get("taxes") or 0)
        entitlement = date.fromisoformat(str(tx.get("entitlement_date") or tx["trade_date"]))
        entitlement_held = next((balance for source, balance in reversed(side_history[position]) if transaction_precedes_entitlement_bod(source, entitlement)), 0)

        def slice_fact(amount, target_type, *, short=False):
            ratio = amount / quantity
            fact = {**tx, "transaction_type": target_type, "quantity": amount,
                    "gross_amount": gross * ratio, "fees": fees * ratio, "taxes": taxes * ratio}
            if short:
                fact.update(_short_source_type=kind, fees=-fees * ratio, taxes=-taxes * ratio)
                short_facts.append(fact)
            else:
                long_facts.append(fact)

        if kind == "short_opening_balance":
            if held > 1e-9:
                raise ValueError("A short opening balance cannot overlap long shares in the same account.")
            slice_fact(quantity, "opening_balance", short=True)
            quantities[position] -= quantity
        elif kind == "short_sell":
            if gross < fees + taxes:
                raise ValueError("Record charges exceeding short-sale proceeds as a separate cash expense.")
            closing = min(max(held, 0), quantity)
            if closing > 1e-9:
                slice_fact(closing, "sell")
            if quantity - closing > 1e-9:
                slice_fact(quantity - closing, "buy", short=True)
            quantities[position] -= quantity
        elif kind in {"buy", "buy_to_cover"}:
            covering = min(max(-held, 0), quantity)
            if kind == "buy_to_cover" and quantity > covering + 1e-9:
                raise ValueError("Buy to cover exceeds the open short quantity at the trade time.")
            if covering > 1e-9:
                slice_fact(covering, "sell", short=True)
            if quantity - covering > 1e-9:
                slice_fact(quantity - covering, "buy")
            quantities[position] += quantity
        elif kind in {"fee", "tax"} and entitlement_held < -1e-9:
            short_facts.append({**tx, "gross_amount": -gross, "fees": -fees, "taxes": -taxes, "_short_source_type": kind})
        else:
            if kind in {"sell", "maturity_redemption"} and quantity > max(held, 0) + 1e-9:
                raise ValueError("Sale exceeds owned shares; explicitly record a confirmed short sale.")
            if kind in {"opening_balance", "dividend_reinvestment"}:
                quantities[position] += quantity
            elif kind in {"sell", "maturity_redemption"}:
                quantities[position] -= quantity
            elif tx.get("transfer_object_type") == "position" and kind in {"transfer_in", "transfer_out"}:
                if held < -1e-9:
                    raise ValueError("Transfer the broker's borrowed-stock obligation explicitly; a long-position transfer cannot move a short.")
                quantities[position] += quantity if kind == "transfer_in" else -quantity
            long_facts.append(tx)
        if quantities[position] != held:
            side_history[position].append((tx, quantities[position]))
    return long_facts, short_facts


def reflect_short_lot(lot):
    result = deepcopy(lot)
    result["position_side"] = "short"
    result["opening_transaction_type"] = {"buy": "short_sell", "opening_balance": "short_opening_balance"}.get(result["opening_transaction_type"], result["opening_transaction_type"])
    result["position_lot_id"] = "short-" + lot["position_lot_id"]
    if result.get("source_position_lot_id"):
        result["source_position_lot_id"] = "short-" + result["source_position_lot_id"]
    for field in ("entry_quantity", "remaining_quantity", "entry_gross_amount", "entry_cost_basis", "remaining_cost_basis", "realized_cost_basis", "realized_gross_proceeds", "realized_proceeds", "realized_pnl", "current_market_value", "unrealized_pnl", "transferred_cost_basis", "expense_cash_amount", "income_cash_amount", "corporate_action_quantity_out", "corporate_action_cost_basis_out", "predecessor_quantity", "transferred_quantity"):
        if result.get(field) is not None:
            result[field] = -result[field]
    for field in ("entry_fee_amount", "entry_tax_amount"):
        result[field] = -float(result.get(field) or 0)
    for realization in result.get("realizations") or []:
        realization["realization_id"] = "short-" + realization["realization_id"]
        realization["transaction_type"] = "buy_to_cover"
        for field in ("gross_proceeds", "proceeds", "cost_basis_released", "realized_pnl", "remaining_quantity_after", "remaining_cost_basis_after"):
            if realization.get(field) is not None:
                realization[field] = -realization[field]
        for origin in realization.get("cost_basis_origins") or []:
            origin["cost_basis_released"] = -origin["cost_basis_released"]
    for origin in result.get("cost_basis_origins") or []:
        for field in ("entry_cost_basis", "remaining_cost_basis"):
            origin[field] = -origin[field]
    return result
