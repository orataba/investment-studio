"""Expand one settlement fact into non-cash accounting legs, never cash trades.

gross_amount remains the actual cash consideration. Fair value is the confirmed
native-currency value of each delivered asset; FX is contract currency per unit
of that currency. The source transaction id stays attached to every posting/lot.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal

from portfolio_app.services.transaction_dates import transaction_position_effective_date


def expand_asset_deliveries(transactions: list[dict[str, object]]) -> list[dict[str, object]]:
    expanded = []
    for transaction in transactions:
        deliveries = transaction.get("asset_deliveries") or []
        cashflows = transaction.get("settlement_cashflows") or []
        if not deliveries and not cashflows:
            expanded.append(transaction)
            continue
        parent = {**transaction, "asset_deliveries": [], "settlement_cashflows": []}
        if deliveries:
            parent["delivered_value"] = float(sum(
                (Decimal(str(leg["fair_value"])) * Decimal(str(leg["fx_rate_to_contract"])) for leg in deliveries),
                Decimal(0),
            ))
        expanded.append(parent)
        # Only redemption receives the total delivery consideration. Child
        # receipts (including shares covering a short) carry their own value.
        child_base = {key: value for key, value in parent.items() if key != "delivered_value"}
        economic_date = transaction_position_effective_date(transaction).isoformat()
        for leg in deliveries:
            expanded.append({
                **child_base,
                "lot_selections": [],
                "transaction_type": "buy",
                "lifecycle_event_type": None,
                "account_id": leg["account_id"],
                "instrument_id": leg["instrument_id"],
                "instrument_ref": deepcopy(leg["instrument_ref"]),
                "derivative_contract_id": None,
                "derivative_contract": None,
                "quantity": float(leg["quantity"]),
                "gross_amount": float(leg["fair_value"]),
                "currency": leg["currency"],
                "price": None,
                "fees": float(leg.get("fees") or 0),
                "taxes": float(leg.get("taxes") or 0),
                "fee_category": leg.get("fee_category") or "unknown",
                "settlement_cash_account_id": leg.get("settlement_cash_account_id"),
                "settlement_date": str(leg.get("fee_settlement_date") or economic_date),
                "delivery_date": str(leg.get("delivery_date") or economic_date),
                "noncash_delivery": True,
                "delivery_source_account_id": transaction["account_id"],
                "delivery_source_contract_id": transaction["derivative_contract_id"],
                "delivery_source_contract": transaction.get("derivative_contract"),
            })
        for cashflow in cashflows:
            recognition_date = str(cashflow.get("recognition_date") or economic_date)
            expanded.append({
                **child_base,
                "lot_selections": [],
                "transaction_type": cashflow["kind"],
                "lifecycle_event_type": None,
                "trade_date": recognition_date,
                "position_effective_date": None,
                "entitlement_date": recognition_date,
                "settlement_date": str(cashflow.get("settlement_date") or recognition_date),
                "settlement_cash_account_id": cashflow["cash_account_id"],
                "quantity": None,
                "price": None,
                "gross_amount": float(cashflow["amount"]),
                "currency": cashflow["currency"],
                "fees": 0.0,
                "taxes": 0.0,
                "fee_category": cashflow.get("fee_category") or "unknown",
                "note": cashflow.get("note") or transaction.get("note"),
                "fcn_settlement_cashflow": True,
                "settlement_source_economic_date": economic_date,
            })
    return expanded


def pending_asset_delivery_quantities(
    transactions: list[dict[str, object]], as_of_date: date,
) -> dict[tuple[str, str], float]:
    """Economic holdings whose broker delivery has not completed yet."""
    pending: dict[tuple[str, str], Decimal] = {}
    as_of_iso = as_of_date.isoformat()
    for transaction in expand_asset_deliveries(transactions):
        if not transaction.get("noncash_delivery"):
            continue
        economic_date = transaction_position_effective_date(transaction)
        if economic_date <= as_of_date and as_of_iso < str(transaction["delivery_date"]):
            key = (str(transaction["account_id"]), str(transaction["instrument_id"]))
            pending[key] = pending.get(key, Decimal(0)) + Decimal(str(transaction["quantity"]))
    return {key: float(quantity) for key, quantity in pending.items()}
