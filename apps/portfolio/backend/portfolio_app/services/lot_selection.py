"""Allocate explicit opening-fact selections without changing chronological lot order."""
from copy import deepcopy


def resolve_import_lot_references(records, transaction_ids):
    references = {}
    for record, transaction_id in zip(records, transaction_ids, strict=True):
        reference = record.get("_record_reference")
        if reference:
            if reference in references:
                raise ValueError("Each file record_reference must be unique.")
            references[reference] = transaction_id
    result = deepcopy(records)
    for record in result:
        for selection in record.get("lot_selections") or []:
            identity = selection["opening_transaction_id"]
            selection["opening_transaction_id"] = references.get(identity, identity)
    return result


def selected_quantities(lots, selections, *, quantity_key, identity_key="opened_by_transaction_id"):
    remaining = {str(item["opening_transaction_id"]): float(item["quantity"]) for item in selections}
    allocated = []
    for lot in lots:
        identity = str(lot.get(identity_key) or "")
        quantity = min(float(lot.get(quantity_key) or 0), remaining.get(identity, 0))
        allocated.append(quantity)
        if identity in remaining:
            remaining[identity] -= quantity
    if any(quantity > 1e-9 for quantity in remaining.values()):
        raise ValueError("Selected opening lot is unavailable or its remaining quantity is insufficient at this transaction time.")
    return allocated
