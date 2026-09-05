"""Expand one settlement fact into non-cash accounting legs, never cash trades.

gross_amount remains the actual cash consideration. Fair value is the confirmed
native-currency value of each delivered asset; FX is contract currency per unit
of that currency. The source transaction id stays attached to every posting/lot.
"""
from copy import deepcopy
from decimal import Decimal


def expand_asset_deliveries(transactions: list[dict[str, object]]) -> list[dict[str, object]]:
    expanded = []
    for transaction in transactions:
        deliveries = transaction.get("asset_deliveries") or []
        if not deliveries:
            expanded.append(transaction)
            continue
        parent = {**transaction, "asset_deliveries": []}
        parent["delivered_value"] = float(sum(
            (Decimal(str(leg["fair_value"])) * Decimal(str(leg["fx_rate_to_contract"])) for leg in deliveries),
            Decimal(0),
        ))
        expanded.append(parent)
        for leg in deliveries:
            expanded.append({
                **transaction,
                "asset_deliveries": [],
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
                "fees": 0.0,
                "taxes": 0.0,
                "settlement_cash_account_id": None,
                "noncash_delivery": True,
                "delivery_source_account_id": transaction["account_id"],
                "delivery_source_contract_id": transaction["derivative_contract_id"],
                "delivery_source_contract": transaction.get("derivative_contract"),
            })
    return expanded
