from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date

from portfolio_app.services.instrument_registry import (
    InstrumentRegistryError,
    get_platform_fx_rates,
    get_registry_instrument_detail,
    list_registry_instruments,
)


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolve_pricing_map(
    asset_ids: set[str] | None = None,
    *,
    as_of_date: date | None = None,
) -> dict[str, float]:
    normalized_asset_ids = {asset_id for asset_id in (asset_ids or set()) if asset_id}
    if as_of_date is not None:
        target_asset_ids = normalized_asset_ids
        if not target_asset_ids:
            target_asset_ids = {
                str(item.get("asset_id") or "")
                for item in list_registry_instruments()
                if str(item.get("asset_id") or "")
            }
        pricing_map: dict[str, float] = {}
        for asset_id in target_asset_ids:
            detail = get_registry_instrument_detail(asset_id)
            if not isinstance(detail, dict):
                continue
            resolved = _select_quote_value(detail, role="valuation", as_of_date=as_of_date)
            if resolved is not None:
                pricing_map[asset_id] = resolved
        return pricing_map

    instruments = list_registry_instruments()
    pricing_map: dict[str, float] = {}
    for instrument in instruments:
        asset_id = str(instrument.get("asset_id") or "")
        if normalized_asset_ids and asset_id not in normalized_asset_ids:
            continue
        resolved = _select_quote_value(instrument, role="valuation")
        if resolved is not None:
            pricing_map[asset_id] = resolved
    return pricing_map


def resolve_fx_rate_map() -> dict[tuple[str, str], float]:
    try:
        payload = get_platform_fx_rates()
    except InstrumentRegistryError:
        return {}

    fx_rate_map: dict[tuple[str, str], float] = {}
    supported_currencies = {
        str(currency or "").strip().upper()
        for currency in payload.get("supported_currencies", [])
        if str(currency or "").strip()
    }
    for item in payload.get("rates", []):
        if not isinstance(item, dict):
            continue
        base_currency = str(item.get("base_currency") or "").strip().upper()
        quote_currency = str(item.get("quote_currency") or "").strip().upper()
        rate = _safe_float(item.get("rate"))
        if not base_currency or not quote_currency or rate is None or rate <= 0:
            continue
        fx_rate_map[(base_currency, quote_currency)] = rate
        supported_currencies.add(base_currency)
        supported_currencies.add(quote_currency)

    for currency in supported_currencies:
        fx_rate_map[(currency, currency)] = 1.0
    return fx_rate_map


def _direct_fx_asset_map(fx_payload: dict[str, object]) -> dict[tuple[str, str], str]:
    direct_assets: dict[tuple[str, str], str] = {}
    for item in fx_payload.get("rates", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("source_kind") or "") != "direct":
            continue
        base_currency = str(item.get("base_currency") or "").strip().upper()
        quote_currency = str(item.get("quote_currency") or "").strip().upper()
        asset_id = str(item.get("asset_id") or "").strip()
        if base_currency and quote_currency and asset_id:
            direct_assets[(base_currency, quote_currency)] = asset_id
    return direct_assets


def convert_amount(
    amount: float | None,
    *,
    from_currency: str,
    to_currency: str,
    fx_rate_map: dict[tuple[str, str], float] | None = None,
) -> float | None:
    if amount is None:
        return None

    normalized_from = from_currency.strip().upper()
    normalized_to = to_currency.strip().upper()
    if not normalized_from or not normalized_to:
        return None
    if normalized_from == normalized_to:
        return float(amount)

    resolved_fx_rate_map = fx_rate_map if fx_rate_map is not None else resolve_fx_rate_map()
    rate = _safe_float(resolved_fx_rate_map.get((normalized_from, normalized_to)))
    if rate is None or rate <= 0:
        return None
    return float(amount) * rate


def _normalized_policy_bases(instrument: dict[str, object], role: str) -> list[str]:
    policy = instrument.get("quote_selection_policy", {})
    if not isinstance(policy, dict):
        return []
    raw_values = policy.get(role)
    if not isinstance(raw_values, list):
        return []
    normalized_values: list[str] = []
    for raw_value in raw_values:
        value = str(raw_value or "").strip()
        if value and value not in normalized_values:
            normalized_values.append(value)
    return normalized_values


def _market_points_by_basis(detail: dict[str, object]) -> dict[str, list[dict[str, object]]]:
    market_data = detail.get("market_data", [])
    points_by_basis: dict[str, list[dict[str, object]]] = defaultdict(list)
    if not isinstance(market_data, list):
        return points_by_basis
    for point in market_data:
        if not isinstance(point, dict):
            continue
        quote_basis = str(point.get("quote_basis") or "").strip()
        if not quote_basis:
            continue
        points_by_basis[quote_basis].append(point)
    for points in points_by_basis.values():
        points.sort(key=lambda item: str(item.get("as_of_date") or ""))
    return points_by_basis


def _latest_point_on_or_before(
    points: list[dict[str, object]],
    as_of_date: date,
) -> dict[str, object] | None:
    as_of_iso = as_of_date.isoformat()
    latest: dict[str, object] | None = None
    for point in points:
        point_date = str(point.get("as_of_date") or "")
        if point_date and point_date <= as_of_iso:
            latest = point
    return latest


def _latest_points_by_basis(instrument: dict[str, object]) -> dict[str, dict[str, object]]:
    latest_market_data = instrument.get("latest_market_data", [])
    if not isinstance(latest_market_data, list):
        return {}

    latest_by_basis: dict[str, dict[str, object]] = {}
    for point in latest_market_data:
        if not isinstance(point, dict):
            continue
        quote_basis = str(point.get("quote_basis") or "").strip()
        if not quote_basis:
            continue
        as_of_date = str(point.get("as_of_date") or "")
        current = latest_by_basis.get(quote_basis)
        if current is None or as_of_date >= str(current.get("as_of_date") or ""):
            latest_by_basis[quote_basis] = point
    return latest_by_basis


def _select_quote_value(
    instrument: dict[str, object],
    *,
    role: str,
    as_of_date: date | None = None,
) -> float | None:
    if as_of_date is not None:
        points_by_basis = _market_points_by_basis(instrument)
        for quote_basis in _normalized_policy_bases(instrument, role):
            point = _latest_point_on_or_before(points_by_basis.get(quote_basis, []), as_of_date)
            resolved = _safe_float((point or {}).get("value"))
            if resolved is not None:
                return resolved

        for quote_basis in _normalized_policy_bases(instrument, "reference"):
            point = _latest_point_on_or_before(points_by_basis.get(quote_basis, []), as_of_date)
            resolved = _safe_float((point or {}).get("value"))
            if resolved is not None:
                return resolved

        fallback_points: list[dict[str, object]] = []
        for points in points_by_basis.values():
            fallback_points.extend(points)
        fallback_points.sort(key=lambda point: str(point.get("as_of_date") or ""))
        point = _latest_point_on_or_before(fallback_points, as_of_date)
        return _safe_float((point or {}).get("value"))

    latest_by_basis = _latest_points_by_basis(instrument)
    for quote_basis in _normalized_policy_bases(instrument, role):
        resolved = _safe_float(latest_by_basis.get(quote_basis, {}).get("value"))
        if resolved is not None:
            return resolved

    for quote_basis in _normalized_policy_bases(instrument, "reference"):
        resolved = _safe_float(latest_by_basis.get(quote_basis, {}).get("value"))
        if resolved is not None:
            return resolved

    fallback_points = sorted(
        latest_by_basis.values(),
        key=lambda point: str(point.get("as_of_date") or ""),
        reverse=True,
    )
    for point in fallback_points:
        resolved = _safe_float(point.get("value"))
        if resolved is not None:
            return resolved
    return None


def _position_market_value(
    *,
    quantity: float,
    last_price: float | None,
    instrument_ref: dict[str, object] | None,
) -> float | None:
    if last_price is None:
        return None
    asset_type = str((instrument_ref or {}).get("asset_type") or "").strip().lower()
    if asset_type == "bond":
        return quantity * last_price / 100.0
    return quantity * last_price


def _transaction_sort_key(transaction: dict[str, object]) -> tuple[str, str, str, str, str]:
    return (
        str(transaction.get("trade_date") or ""),
        str(transaction.get("trade_at") or ""),
        str(transaction.get("created_at") or ""),
        str(transaction.get("transaction_id") or ""),
        str(transaction.get("settlement_date") or ""),
    )


def ledger_posting_effective_date_iso(posting: dict[str, object]) -> str:
    effective_date = str(posting.get("effective_date") or "").strip()
    if effective_date:
        return effective_date
    if _safe_float(posting.get("cash_amount_delta")) is not None:
        return str(posting.get("settlement_date") or posting.get("trade_date") or "")
    return str(posting.get("trade_date") or posting.get("settlement_date") or "")


def _resolve_cost_basis_method(account_cost_methods: dict[str, str] | None, account_id: str) -> str:
    resolved = str((account_cost_methods or {}).get(account_id) or "fifo")
    return resolved if resolved in {"moving_average", "fifo"} else "fifo"


def _resolve_account_currency(
    account_currency_map: dict[str, str] | None,
    account_id: str,
    fallback_currency: str,
) -> str:
    resolved = str((account_currency_map or {}).get(account_id) or "").strip().upper()
    return resolved or fallback_currency


def _ensure_position_bucket(
    position_state: dict[tuple[str, str], dict[str, object]],
    account_id: str,
    asset_id: str,
) -> dict[str, object]:
    return position_state.setdefault(
        (account_id, asset_id),
        {
            "quantity": 0.0,
            "cost_basis": 0.0,
            "lots": [],
        },
    )


def _normalize_position_bucket(bucket: dict[str, object], cost_basis_method: str) -> None:
    cleaned_lots: list[dict[str, float]] = []
    total_quantity = 0.0
    total_cost_basis = 0.0
    for raw_lot in list(bucket.get("lots", [])):
        quantity = _safe_float(raw_lot.get("quantity")) or 0.0
        cost_basis = _safe_float(raw_lot.get("cost_basis")) or 0.0
        if quantity <= 1e-9:
            continue
        if cost_basis < 0 and abs(cost_basis) <= 1e-9:
            cost_basis = 0.0
        cleaned_lots.append(
            {
                "quantity": quantity,
                "cost_basis": cost_basis,
            }
        )
        total_quantity += quantity
        total_cost_basis += cost_basis

    if cleaned_lots:
        bucket["lots"] = cleaned_lots
        bucket["quantity"] = 0.0 if abs(total_quantity) < 1e-9 else total_quantity
        bucket["cost_basis"] = 0.0 if abs(total_cost_basis) < 1e-9 else total_cost_basis
        return

    quantity = _safe_float(bucket.get("quantity")) or 0.0
    cost_basis = _safe_float(bucket.get("cost_basis")) or 0.0
    quantity = 0.0 if abs(quantity) < 1e-9 else quantity
    cost_basis = 0.0 if abs(cost_basis) < 1e-9 else cost_basis
    bucket["quantity"] = quantity
    bucket["cost_basis"] = cost_basis
    bucket["lots"] = (
        [{"quantity": quantity, "cost_basis": cost_basis}]
        if quantity > 0 and cost_basis >= 0
        else []
    )


def _preview_position_cost_release(
    position_state: dict[tuple[str, str], dict[str, object]],
    account_id: str,
    asset_id: str,
    quantity: float,
    cost_basis_method: str,
) -> float:
    bucket = position_state.get((account_id, asset_id))
    if bucket is None or quantity <= 0:
        return 0.0

    current_quantity = _safe_float(bucket.get("quantity")) or 0.0
    current_cost_basis = _safe_float(bucket.get("cost_basis")) or 0.0
    if current_quantity <= 0 or current_cost_basis <= 0:
        return 0.0

    if cost_basis_method == "fifo":
        if quantity >= current_quantity:
            return current_cost_basis
        remaining = quantity
        released_cost_basis = 0.0
        for raw_lot in list(bucket.get("lots", [])):
            lot_quantity = _safe_float(raw_lot.get("quantity")) or 0.0
            lot_cost_basis = _safe_float(raw_lot.get("cost_basis")) or 0.0
            if lot_quantity <= 0:
                continue
            take_quantity = min(remaining, lot_quantity)
            if take_quantity >= lot_quantity:
                take_cost_basis = lot_cost_basis
            else:
                take_cost_basis = lot_cost_basis * (take_quantity / lot_quantity)
            released_cost_basis += take_cost_basis
            remaining -= take_quantity
            if remaining <= 1e-9:
                break
        return released_cost_basis

    if quantity >= current_quantity:
        return current_cost_basis
    return current_cost_basis * (quantity / current_quantity)


def _add_position_state(
    position_state: dict[tuple[str, str], dict[str, object]],
    account_id: str,
    asset_id: str,
    *,
    quantity: float,
    cost_basis: float,
    cost_basis_method: str,
    incoming_lots: list[dict[str, float]] | None = None,
) -> None:
    if quantity <= 0 and cost_basis <= 0:
        return
    bucket = _ensure_position_bucket(position_state, account_id, asset_id)
    bucket["quantity"] = (_safe_float(bucket.get("quantity")) or 0.0) + quantity
    bucket["cost_basis"] = (_safe_float(bucket.get("cost_basis")) or 0.0) + cost_basis
    lots = incoming_lots or [{"quantity": quantity, "cost_basis": cost_basis}]
    bucket.setdefault("lots", [])
    bucket["lots"].extend(
        {
            "quantity": _safe_float(lot.get("quantity")) or 0.0,
            "cost_basis": _safe_float(lot.get("cost_basis")) or 0.0,
        }
        for lot in lots
        if (_safe_float(lot.get("quantity")) or 0.0) > 1e-9
    )
    _normalize_position_bucket(bucket, cost_basis_method)


def _consume_position_state(
    position_state: dict[tuple[str, str], dict[str, object]],
    account_id: str,
    asset_id: str,
    *,
    quantity: float,
    cost_basis_method: str,
) -> tuple[float, list[dict[str, float]]]:
    bucket = _ensure_position_bucket(position_state, account_id, asset_id)
    if quantity <= 0:
        return 0.0, []

    current_quantity = _safe_float(bucket.get("quantity")) or 0.0
    current_cost_basis = _safe_float(bucket.get("cost_basis")) or 0.0
    if current_quantity <= 0 or current_cost_basis <= 0:
        return 0.0, []

    if cost_basis_method == "fifo":
        remaining = min(quantity, current_quantity)
        moved_lots: list[dict[str, float]] = []
        lots = list(bucket.get("lots", []))
        while remaining > 1e-9 and lots:
            lot = lots[0]
            lot_quantity = _safe_float(lot.get("quantity")) or 0.0
            lot_cost_basis = _safe_float(lot.get("cost_basis")) or 0.0
            if lot_quantity <= 1e-9:
                lots.pop(0)
                continue
            take_quantity = min(remaining, lot_quantity)
            if take_quantity >= lot_quantity:
                take_cost_basis = lot_cost_basis
                lots.pop(0)
            else:
                take_cost_basis = lot_cost_basis * (take_quantity / lot_quantity)
                lot["quantity"] = lot_quantity - take_quantity
                lot["cost_basis"] = lot_cost_basis - take_cost_basis
            moved_lots.append({"quantity": take_quantity, "cost_basis": take_cost_basis})
            remaining -= take_quantity
        consumed_quantity = sum(lot["quantity"] for lot in moved_lots)
        consumed_cost_basis = sum(lot["cost_basis"] for lot in moved_lots)
        bucket["lots"] = lots
        bucket["quantity"] = max(current_quantity - consumed_quantity, 0.0)
        bucket["cost_basis"] = max(current_cost_basis - consumed_cost_basis, 0.0)
        _normalize_position_bucket(bucket, cost_basis_method)
        return consumed_cost_basis, moved_lots

    consumed_quantity = min(quantity, current_quantity)
    active_lots = [
        raw_lot
        for raw_lot in list(bucket.get("lots", []))
        if (_safe_float(raw_lot.get("quantity")) or 0.0) > 1e-9
    ]
    if consumed_quantity <= 1e-9 or not active_lots:
        return 0.0, []

    quantities = [(_safe_float(lot.get("quantity")) or 0.0) for lot in active_lots]
    quantity_allocations = _proportional_allocations(consumed_quantity, quantities)
    moved_lots: list[dict[str, float]] = []
    for index, lot in enumerate(active_lots):
        take_quantity = quantity_allocations[index]
        if take_quantity <= 1e-9:
            continue
        lot_quantity = quantities[index]
        lot_cost_basis = _safe_float(lot.get("cost_basis")) or 0.0
        take_cost_basis = lot_cost_basis * (take_quantity / lot_quantity) if lot_quantity > 0 else 0.0
        lot["quantity"] = lot_quantity - take_quantity
        lot["cost_basis"] = lot_cost_basis - take_cost_basis
        moved_lots.append({"quantity": take_quantity, "cost_basis": take_cost_basis})

    consumed_cost_basis = sum(lot["cost_basis"] for lot in moved_lots)
    bucket["quantity"] = max(current_quantity - consumed_quantity, 0.0)
    bucket["cost_basis"] = max(current_cost_basis - consumed_cost_basis, 0.0)
    _normalize_position_bucket(bucket, cost_basis_method)
    return consumed_cost_basis, moved_lots


def _apply_return_of_capital(
    position_state: dict[tuple[str, str], dict[str, object]],
    account_id: str,
    asset_id: str,
    *,
    amount: float,
    cost_basis_method: str,
) -> float:
    bucket = _ensure_position_bucket(position_state, account_id, asset_id)
    current_cost_basis = _safe_float(bucket.get("cost_basis")) or 0.0
    reduction = min(amount, current_cost_basis)
    if reduction <= 0:
        return 0.0

    lots = list(bucket.get("lots", []))
    if lots:
        original_total_cost = sum((_safe_float(lot.get("cost_basis")) or 0.0) for lot in lots)
        remaining_reduction = reduction
        for index, lot in enumerate(lots):
            lot_cost_basis = _safe_float(lot.get("cost_basis")) or 0.0
            if index == len(lots) - 1:
                lot_reduction = min(remaining_reduction, lot_cost_basis)
            else:
                prorated_reduction = reduction * (lot_cost_basis / original_total_cost) if original_total_cost > 0 else 0.0
                lot_reduction = min(prorated_reduction, lot_cost_basis)
            lot["cost_basis"] = lot_cost_basis - lot_reduction
            remaining_reduction -= lot_reduction
        bucket["lots"] = lots
        _normalize_position_bucket(bucket, cost_basis_method)
        return reduction - max(remaining_reduction, 0.0)

    bucket["cost_basis"] = current_cost_basis - reduction
    _normalize_position_bucket(bucket, cost_basis_method)
    return reduction


def _build_position_state(
    transactions: list[dict[str, object]],
    *,
    account_cost_methods: dict[str, str] | None = None,
) -> dict[tuple[str, str], dict[str, object]]:
    position_state: dict[tuple[str, str], dict[str, object]] = {}
    position_transfer_lots_by_group: dict[str, list[dict[str, float]]] = {}

    for transaction in sorted(transactions, key=_transaction_sort_key):
        transaction_type = str(transaction.get("transaction_type") or "")
        gross_amount = _safe_float(transaction.get("gross_amount")) or 0.0
        fees = _safe_float(transaction.get("fees")) or 0.0
        taxes = _safe_float(transaction.get("taxes")) or 0.0
        quantity = _safe_float(transaction.get("quantity"))
        account_id = str(transaction.get("account_id") or "")
        asset_id = str(transaction.get("asset_id") or "")
        cost_basis_method = _resolve_cost_basis_method(account_cost_methods, account_id)

        if not asset_id:
            continue

        if transaction_type == "opening_balance":
            _add_position_state(
                position_state,
                account_id,
                asset_id,
                quantity=quantity or 0.0,
                cost_basis=gross_amount,
                cost_basis_method=cost_basis_method,
            )
            continue

        if transaction_type == "buy":
            _add_position_state(
                position_state,
                account_id,
                asset_id,
                quantity=quantity or 0.0,
                cost_basis=gross_amount + fees + taxes,
                cost_basis_method=cost_basis_method,
            )
            continue

        if transaction_type in {"sell", "maturity_redemption"}:
            _consume_position_state(
                position_state,
                account_id,
                asset_id,
                quantity=quantity or 0.0,
                cost_basis_method=cost_basis_method,
            )
            continue

        if transaction_type == "return_of_capital":
            _apply_return_of_capital(
                position_state,
                account_id,
                asset_id,
                amount=gross_amount,
                cost_basis_method=cost_basis_method,
            )
            continue

        if transaction_type == "dividend_reinvestment":
            current_bucket = position_state.get((account_id, asset_id))
            current_quantity = _safe_float((current_bucket or {}).get("quantity")) or 0.0
            if current_quantity <= 1e-9:
                raise ValueError("Dividend reinvestment requires existing position as of trade_date.")
            _add_position_state(
                position_state,
                account_id,
                asset_id,
                quantity=quantity or 0.0,
                cost_basis=gross_amount,
                cost_basis_method=cost_basis_method,
            )
            continue

        if transaction_type == "transfer_out" and transaction.get("transfer_object_type") == "position":
            transferred_cost_basis, transferred_lots = _consume_position_state(
                position_state,
                account_id,
                asset_id,
                quantity=quantity or 0.0,
                cost_basis_method="fifo",
            )
            if transferred_cost_basis <= 0 or not transferred_lots:
                raise ValueError("Position transfer requires source lots as of trade_date.")
            transfer_group_id = str(transaction.get("transfer_group_id") or "")
            if transfer_group_id and transferred_lots:
                position_transfer_lots_by_group[transfer_group_id] = deepcopy(transferred_lots)
            continue

        if transaction_type == "transfer_in" and transaction.get("transfer_object_type") == "position":
            transfer_group_id = str(transaction.get("transfer_group_id") or "")
            incoming_lots = deepcopy(position_transfer_lots_by_group.get(transfer_group_id, []))
            if not incoming_lots:
                raise ValueError("Position transfer requires linked source lots.")
            received_cost_basis = sum((_safe_float(lot.get("cost_basis")) or 0.0) for lot in incoming_lots)
            if received_cost_basis <= 0:
                raise ValueError("Position transfer requires positive linked source lot cost basis.")
            _add_position_state(
                position_state,
                account_id,
                asset_id,
                quantity=quantity or 0.0,
                cost_basis=received_cost_basis,
                cost_basis_method=cost_basis_method,
                incoming_lots=incoming_lots,
            )

    return position_state


def derive_ledger_postings(
    portfolio_id: str,
    transactions: list[dict[str, object]],
    *,
    account_cost_methods: dict[str, str] | None = None,
    account_currency_map: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    postings: list[dict[str, object]] = []
    position_state: dict[tuple[str, str], dict[str, object]] = {}
    position_transfer_lots_by_group: dict[str, list[dict[str, float]]] = {}

    def append_posting(
        transaction: dict[str, object],
        *,
        posting_role: str,
        account_id: str,
        cash_amount_delta: float | None = None,
        quantity_delta: float | None = None,
        cost_basis_delta: float | None = None,
        currency: str | None = None,
    ) -> None:
        posting_index = len([item for item in postings if item["transaction_id"] == transaction["transaction_id"]]) + 1
        postings.append(
            {
                "posting_id": f"{transaction['transaction_id']}-p{posting_index}",
                "transaction_id": transaction["transaction_id"],
                "portfolio_id": portfolio_id,
                "account_id": account_id,
                "posting_role": posting_role,
                "source_transaction_type": transaction["transaction_type"],
                "trade_date": transaction["trade_date"],
                "trade_at": transaction.get("trade_at"),
                "settlement_date": transaction["settlement_date"],
                "effective_date": (
                    transaction["settlement_date"]
                    if cash_amount_delta is not None
                    else transaction["trade_date"]
                ),
                "asset_id": transaction.get("asset_id"),
                "instrument_ref": deepcopy(transaction.get("instrument_ref")),
                "cash_amount_delta": cash_amount_delta,
                "quantity_delta": quantity_delta,
                "cost_basis_delta": cost_basis_delta,
                "currency": currency or str(transaction.get("currency") or ""),
                "transfer_group_id": transaction.get("transfer_group_id"),
                "note": transaction.get("note"),
                "created_at": transaction.get("created_at"),
            }
        )

    for transaction in sorted(transactions, key=_transaction_sort_key):
        transaction_type = str(transaction.get("transaction_type") or "")
        gross_amount = _safe_float(transaction.get("gross_amount")) or 0.0
        fees = _safe_float(transaction.get("fees")) or 0.0
        taxes = _safe_float(transaction.get("taxes")) or 0.0
        quantity = _safe_float(transaction.get("quantity"))
        account_id = str(transaction.get("account_id") or "")
        settlement_cash_account_id = transaction.get("settlement_cash_account_id")
        currency = str(transaction.get("currency") or "")
        asset_id = str(transaction.get("asset_id") or "")
        cost_basis_method = _resolve_cost_basis_method(account_cost_methods, account_id)

        if transaction_type == "opening_balance":
            if transaction.get("asset_id"):
                opening_quantity = quantity or 0.0
                append_posting(
                    transaction,
                    posting_role="opening_position",
                    account_id=account_id,
                    quantity_delta=opening_quantity,
                    cost_basis_delta=gross_amount,
                    currency=currency,
                )
                _add_position_state(
                    position_state,
                    account_id,
                    asset_id,
                    quantity=opening_quantity,
                    cost_basis=gross_amount,
                    cost_basis_method=cost_basis_method,
                )
            else:
                append_posting(
                    transaction,
                    posting_role="opening_cash",
                    account_id=account_id,
                    cash_amount_delta=gross_amount,
                    currency=currency,
                )
            continue

        if transaction_type == "deposit":
            append_posting(
                transaction,
                posting_role="external_cash_flow",
                account_id=account_id,
                cash_amount_delta=gross_amount,
                currency=currency,
            )
            continue

        if transaction_type == "withdrawal":
            append_posting(
                transaction,
                posting_role="external_cash_flow",
                account_id=account_id,
                cash_amount_delta=-gross_amount,
                currency=currency,
            )
            continue

        if transaction_type == "fx_conversion":
            target_account_id = str(transaction.get("counterparty_account_id") or "")
            target_amount = _safe_float(transaction.get("counter_amount")) or 0.0
            append_posting(
                transaction,
                posting_role="fx_conversion_source_cash",
                account_id=account_id,
                cash_amount_delta=-gross_amount,
                currency=currency,
            )
            if target_account_id and target_amount > 0:
                append_posting(
                    transaction,
                    posting_role="fx_conversion_target_cash",
                    account_id=target_account_id,
                    cash_amount_delta=target_amount,
                    currency=_resolve_account_currency(account_currency_map, target_account_id, currency),
                )
            continue

        if transaction_type == "buy":
            bought_quantity = quantity or 0.0
            bought_cost_basis = gross_amount + fees + taxes
            append_posting(
                transaction,
                posting_role="security_position",
                account_id=account_id,
                quantity_delta=bought_quantity,
                cost_basis_delta=bought_cost_basis,
                currency=currency,
            )
            _add_position_state(
                position_state,
                account_id,
                asset_id,
                quantity=bought_quantity,
                cost_basis=bought_cost_basis,
                cost_basis_method=cost_basis_method,
            )
            if isinstance(settlement_cash_account_id, str) and settlement_cash_account_id:
                append_posting(
                    transaction,
                    posting_role="security_settlement_cash",
                    account_id=settlement_cash_account_id,
                    cash_amount_delta=-(gross_amount + fees + taxes),
                    currency=currency,
                )
            continue

        if transaction_type == "sell":
            sold_quantity = quantity or 0.0
            sold_cost_basis, _ = _consume_position_state(
                position_state,
                account_id,
                asset_id,
                quantity=sold_quantity,
                cost_basis_method=cost_basis_method,
            )
            append_posting(
                transaction,
                posting_role="security_position",
                account_id=account_id,
                quantity_delta=-sold_quantity,
                cost_basis_delta=-sold_cost_basis,
                currency=currency,
            )
            if isinstance(settlement_cash_account_id, str) and settlement_cash_account_id:
                append_posting(
                    transaction,
                    posting_role="security_settlement_cash",
                    account_id=settlement_cash_account_id,
                    cash_amount_delta=gross_amount - fees - taxes,
                    currency=currency,
                )
            continue

        if transaction_type in {"dividend", "coupon"}:
            if isinstance(settlement_cash_account_id, str) and settlement_cash_account_id:
                append_posting(
                    transaction,
                    posting_role="security_income_cash",
                    account_id=settlement_cash_account_id,
                    cash_amount_delta=gross_amount - fees - taxes,
                    currency=currency,
                )
            continue

        if transaction_type == "interest":
            append_posting(
                transaction,
                posting_role="account_income_cash",
                account_id=account_id,
                cash_amount_delta=gross_amount,
                currency=currency,
            )
            continue

        if transaction_type == "return_of_capital":
            returned_cost_basis = _apply_return_of_capital(
                position_state,
                account_id,
                asset_id,
                amount=gross_amount,
                cost_basis_method=cost_basis_method,
            )
            if isinstance(settlement_cash_account_id, str) and settlement_cash_account_id:
                append_posting(
                    transaction,
                    posting_role="security_income_cash",
                    account_id=settlement_cash_account_id,
                    cash_amount_delta=gross_amount - fees - taxes,
                    currency=currency,
                )
            append_posting(
                transaction,
                posting_role="security_position",
                account_id=account_id,
                quantity_delta=None,
                cost_basis_delta=-returned_cost_basis,
                currency=currency,
            )
            continue

        if transaction_type == "dividend_reinvestment":
            reinvested_quantity = quantity or 0.0
            current_bucket = position_state.get((account_id, asset_id))
            current_quantity = _safe_float((current_bucket or {}).get("quantity")) or 0.0
            if current_quantity <= 1e-9:
                raise ValueError("Dividend reinvestment requires existing position as of trade_date.")
            append_posting(
                transaction,
                posting_role="security_reinvestment_position",
                account_id=account_id,
                quantity_delta=reinvested_quantity,
                cost_basis_delta=gross_amount,
                currency=currency,
            )
            _add_position_state(
                position_state,
                account_id,
                asset_id,
                quantity=reinvested_quantity,
                cost_basis=gross_amount,
                cost_basis_method=cost_basis_method,
            )
            continue

        if transaction_type == "maturity_redemption":
            redeemed_quantity = quantity or 0.0
            redeemed_cost_basis, _ = _consume_position_state(
                position_state,
                account_id,
                asset_id,
                quantity=redeemed_quantity,
                cost_basis_method=cost_basis_method,
            )
            append_posting(
                transaction,
                posting_role="security_position",
                account_id=account_id,
                quantity_delta=-redeemed_quantity,
                cost_basis_delta=-redeemed_cost_basis,
                currency=currency,
            )
            if isinstance(settlement_cash_account_id, str) and settlement_cash_account_id:
                append_posting(
                    transaction,
                    posting_role="security_redemption_cash",
                    account_id=settlement_cash_account_id,
                    cash_amount_delta=gross_amount - fees - taxes,
                    currency=currency,
                )
            continue

        if transaction_type in {"fee", "tax"}:
            expense_account_id = account_id
            if isinstance(settlement_cash_account_id, str) and settlement_cash_account_id:
                expense_account_id = settlement_cash_account_id
            append_posting(
                transaction,
                posting_role="account_expense_cash",
                account_id=expense_account_id,
                cash_amount_delta=-gross_amount,
                currency=currency,
            )
            continue

        if transaction_type == "transfer_out":
            if transaction.get("transfer_object_type") == "cash":
                append_posting(
                    transaction,
                    posting_role="internal_cash_transfer",
                    account_id=account_id,
                    cash_amount_delta=-gross_amount,
                    currency=currency,
                )
            else:
                transferred_quantity = quantity or 0.0
                transferred_cost_basis, transferred_lots = _consume_position_state(
                    position_state,
                    account_id,
                    asset_id,
                    quantity=transferred_quantity,
                    cost_basis_method="fifo",
                )
                if transferred_cost_basis <= 0 or not transferred_lots:
                    raise ValueError("Position transfer requires source lots as of trade_date.")
                transfer_group_id = str(transaction.get("transfer_group_id") or "")
                if transfer_group_id and transferred_lots:
                    position_transfer_lots_by_group[transfer_group_id] = deepcopy(transferred_lots)
                append_posting(
                    transaction,
                    posting_role="internal_position_transfer",
                    account_id=account_id,
                    quantity_delta=-transferred_quantity,
                    cost_basis_delta=-transferred_cost_basis,
                    currency=currency,
                )
            continue

        if transaction_type == "transfer_in":
            if transaction.get("transfer_object_type") == "cash":
                append_posting(
                    transaction,
                    posting_role="internal_cash_transfer",
                    account_id=account_id,
                    cash_amount_delta=gross_amount,
                    currency=currency,
                )
            else:
                received_quantity = quantity or 0.0
                transfer_group_id = str(transaction.get("transfer_group_id") or "")
                incoming_lots = deepcopy(position_transfer_lots_by_group.get(transfer_group_id, []))
                if not incoming_lots:
                    raise ValueError("Position transfer requires linked source lots.")
                received_cost_basis = sum((_safe_float(lot.get("cost_basis")) or 0.0) for lot in incoming_lots)
                if received_cost_basis <= 0:
                    raise ValueError("Position transfer requires positive linked source lot cost basis.")
                append_posting(
                    transaction,
                    posting_role="internal_position_transfer",
                    account_id=account_id,
                    quantity_delta=received_quantity,
                    cost_basis_delta=received_cost_basis,
                    currency=currency,
                )
                _add_position_state(
                    position_state,
                    account_id,
                    asset_id,
                    quantity=received_quantity,
                    cost_basis=received_cost_basis,
                    cost_basis_method=cost_basis_method,
                    incoming_lots=incoming_lots,
                )
            continue

    postings.sort(
        key=lambda item: (
            str(item.get("trade_date") or ""),
            str(item.get("trade_at") or ""),
            str(item.get("created_at") or ""),
            str(item.get("settlement_date") or ""),
            str(item.get("posting_id") or ""),
        ),
        reverse=True,
    )
    return postings


def estimate_position_cost_basis(
    portfolio_id: str,
    transactions: list[dict[str, object]],
    *,
    account_id: str,
    asset_id: str,
    quantity: float,
    account_cost_methods: dict[str, str] | None = None,
    consumption_method: str | None = None,
) -> float:
    if quantity <= 0:
        return 0.0

    position_state = _build_position_state(
        transactions,
        account_cost_methods=account_cost_methods,
    )
    bucket = position_state.get((account_id, asset_id))
    if bucket is None:
        return 0.0

    current_quantity = _safe_float(bucket.get("quantity")) or 0.0
    current_cost_basis = _safe_float(bucket.get("cost_basis")) or 0.0
    if current_quantity <= 0 or current_cost_basis <= 0:
        return 0.0

    cost_basis_method = consumption_method or _resolve_cost_basis_method(account_cost_methods, account_id)
    return _preview_position_cost_release(
        position_state,
        account_id,
        asset_id,
        quantity=min(quantity, current_quantity),
        cost_basis_method=cost_basis_method,
    )


def estimate_position_remaining_cost_basis(
    portfolio_id: str,
    transactions: list[dict[str, object]],
    *,
    account_id: str,
    asset_id: str,
    account_cost_methods: dict[str, str] | None = None,
) -> float:
    position_state = _build_position_state(
        transactions,
        account_cost_methods=account_cost_methods,
    )
    bucket = position_state.get((account_id, asset_id))
    if bucket is None:
        return 0.0
    return _safe_float(bucket.get("cost_basis")) or 0.0


def estimate_position_quantity(
    portfolio_id: str,
    transactions: list[dict[str, object]],
    *,
    account_id: str,
    asset_id: str,
    account_cost_methods: dict[str, str] | None = None,
) -> float:
    position_state = _build_position_state(
        transactions,
        account_cost_methods=account_cost_methods,
    )
    bucket = position_state.get((account_id, asset_id))
    if bucket is None:
        return 0.0
    return _safe_float(bucket.get("quantity")) or 0.0


def _parse_iso_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        raw_value = value.strip()
        if not raw_value:
            return None
        try:
            return date.fromisoformat(raw_value[:10])
        except ValueError:
            return None
    return None


def _proportional_allocations(total: float, weights: list[float]) -> list[float]:
    if total <= 0:
        return [0.0 for _ in weights]

    positive_indices = [index for index, weight in enumerate(weights) if weight > 1e-9]
    if not positive_indices:
        return [0.0 for _ in weights]

    weight_total = sum(weights[index] for index in positive_indices)
    allocations = [0.0 for _ in weights]
    remaining = total
    for offset, index in enumerate(positive_indices):
        if offset == len(positive_indices) - 1:
            allocation = remaining
        else:
            allocation = total * (weights[index] / weight_total) if weight_total > 0 else 0.0
            allocation = min(allocation, remaining)
        allocations[index] = allocation
        remaining -= allocation
    return allocations


def _new_position_lot(
    *,
    position_lot_id: str,
    portfolio_id: str,
    account_id: str,
    asset_id: str,
    instrument_ref: dict[str, object],
    currency: str,
    cost_basis_method: str,
    opened_by_transaction_id: str,
    opening_transaction_type: str,
    opened_at: str,
    acquisition_date: str,
    entry_quantity: float,
    entry_cost_basis: float,
    source_position_lot_id: str | None = None,
    linked_transaction_ids: set[str] | None = None,
) -> dict[str, object]:
    resolved_linked_transaction_ids = set(linked_transaction_ids or [])
    resolved_linked_transaction_ids.add(opened_by_transaction_id)
    return {
        "position_lot_id": position_lot_id,
        "portfolio_id": portfolio_id,
        "account_id": account_id,
        "asset_id": asset_id,
        "instrument_ref": deepcopy(instrument_ref),
        "currency": currency,
        "cost_basis_method": cost_basis_method,
        "opened_by_transaction_id": opened_by_transaction_id,
        "opening_transaction_type": opening_transaction_type,
        "opened_at": opened_at,
        "acquisition_date": acquisition_date,
        "closed_at": None,
        "status": "open",
        "close_reason": None,
        "source_position_lot_id": source_position_lot_id,
        "entry_quantity": entry_quantity,
        "remaining_quantity": entry_quantity,
        "realized_quantity": 0.0,
        "transferred_quantity": 0.0,
        "entry_cost_basis": entry_cost_basis,
        "remaining_cost_basis": entry_cost_basis,
        "realized_cost_basis": 0.0,
        "transferred_cost_basis": 0.0,
        "realized_proceeds": 0.0,
        "realized_pnl": 0.0,
        "income_cash_amount": 0.0,
        "expense_cash_amount": 0.0,
        "return_of_capital_amount": 0.0,
        "realizations": [],
        "_linked_transaction_ids": resolved_linked_transaction_ids,
    }


def _touch_position_lot(position_lot: dict[str, object], transaction_id: str | None) -> None:
    if not transaction_id:
        return
    linked_transaction_ids = position_lot.setdefault("_linked_transaction_ids", set())
    if isinstance(linked_transaction_ids, set):
        linked_transaction_ids.add(transaction_id)


def _close_position_lot_if_needed(
    position_lot: dict[str, object],
    *,
    close_date: str,
    close_reason: str | None = None,
) -> None:
    remaining_quantity = _safe_float(position_lot.get("remaining_quantity")) or 0.0
    remaining_cost_basis = _safe_float(position_lot.get("remaining_cost_basis")) or 0.0
    if abs(remaining_quantity) <= 1e-9:
        position_lot["remaining_quantity"] = 0.0
    if abs(remaining_cost_basis) <= 1e-9:
        position_lot["remaining_cost_basis"] = 0.0

    if (_safe_float(position_lot.get("remaining_quantity")) or 0.0) <= 1e-9:
        position_lot["status"] = "closed"
        position_lot["closed_at"] = close_date
        if close_reason:
            position_lot["close_reason"] = close_reason
        return

    position_lot["status"] = "open"
    position_lot["closed_at"] = None
    if close_reason is None:
        position_lot["close_reason"] = None


def _position_lot_status(position_lot: dict[str, object]) -> str:
    return "closed" if (_safe_float(position_lot.get("remaining_quantity")) or 0.0) <= 1e-9 else "open"


def _active_position_lots(
    position_lots_by_key: dict[tuple[str, str], list[dict[str, object]]],
    account_id: str,
    asset_id: str,
) -> list[dict[str, object]]:
    return [
        position_lot
        for position_lot in position_lots_by_key.get((account_id, asset_id), [])
        if (_safe_float(position_lot.get("remaining_quantity")) or 0.0) > 1e-9
    ]


def _require_active_position_lots(
    position_lots_by_key: dict[tuple[str, str], list[dict[str, object]]],
    *,
    account_id: str,
    asset_id: str,
    error_message: str,
) -> list[dict[str, object]]:
    active_lots = _active_position_lots(position_lots_by_key, account_id, asset_id)
    if not active_lots:
        raise ValueError(error_message)
    return active_lots


def _consume_position_lots(
    position_lots_by_key: dict[tuple[str, str], list[dict[str, object]]],
    *,
    account_id: str,
    asset_id: str,
    quantity: float,
    cost_basis_method: str,
) -> list[dict[str, object]]:
    active_lots = _active_position_lots(position_lots_by_key, account_id, asset_id)
    if quantity <= 0 or not active_lots:
        return []

    total_open_quantity = sum((_safe_float(lot.get("remaining_quantity")) or 0.0) for lot in active_lots)
    target_quantity = min(quantity, total_open_quantity)
    if target_quantity <= 1e-9:
        return []

    slices: list[dict[str, object]] = []
    if cost_basis_method == "fifo":
        remaining = target_quantity
        for position_lot in active_lots:
            if remaining <= 1e-9:
                break
            lot_quantity = _safe_float(position_lot.get("remaining_quantity")) or 0.0
            lot_cost_basis = _safe_float(position_lot.get("remaining_cost_basis")) or 0.0
            if lot_quantity <= 1e-9:
                continue
            take_quantity = min(remaining, lot_quantity)
            take_cost_basis = lot_cost_basis * (take_quantity / lot_quantity) if lot_quantity > 0 else 0.0
            position_lot["remaining_quantity"] = lot_quantity - take_quantity
            position_lot["remaining_cost_basis"] = lot_cost_basis - take_cost_basis
            slices.append(
                {
                    "position_lot": position_lot,
                    "quantity": take_quantity,
                    "cost_basis": take_cost_basis,
                }
            )
            remaining -= take_quantity
        return slices

    quantities = [(_safe_float(position_lot.get("remaining_quantity")) or 0.0) for position_lot in active_lots]
    quantity_allocations = _proportional_allocations(target_quantity, quantities)
    for index, position_lot in enumerate(active_lots):
        take_quantity = quantity_allocations[index]
        if take_quantity <= 1e-9:
            continue
        lot_quantity = quantities[index]
        lot_cost_basis = _safe_float(position_lot.get("remaining_cost_basis")) or 0.0
        take_cost_basis = lot_cost_basis * (take_quantity / lot_quantity) if lot_quantity > 0 else 0.0
        position_lot["remaining_quantity"] = lot_quantity - take_quantity
        position_lot["remaining_cost_basis"] = lot_cost_basis - take_cost_basis
        slices.append(
            {
                "position_lot": position_lot,
                "quantity": take_quantity,
                "cost_basis": take_cost_basis,
            }
        )
    return slices


def _allocate_lot_cash_flow_by_quantity(
    position_lots_by_key: dict[tuple[str, str], list[dict[str, object]]],
    *,
    account_id: str,
    asset_id: str,
    amount: float,
    field_name: str,
    transaction_id: str,
    error_message: str,
) -> None:
    if amount <= 0:
        return
    active_lots = _require_active_position_lots(
        position_lots_by_key,
        account_id=account_id,
        asset_id=asset_id,
        error_message=error_message,
    )

    quantities = [(_safe_float(position_lot.get("remaining_quantity")) or 0.0) for position_lot in active_lots]
    allocations = _proportional_allocations(amount, quantities)
    for index, position_lot in enumerate(active_lots):
        allocation = allocations[index]
        if allocation <= 1e-9:
            continue
        position_lot[field_name] = (_safe_float(position_lot.get(field_name)) or 0.0) + allocation
        _touch_position_lot(position_lot, transaction_id)


def _apply_position_lot_return_of_capital(
    position_lots_by_key: dict[tuple[str, str], list[dict[str, object]]],
    *,
    account_id: str,
    asset_id: str,
    amount: float,
    transaction_id: str,
    error_message: str,
) -> None:
    if amount <= 0:
        return
    active_lots = _require_active_position_lots(
        position_lots_by_key,
        account_id=account_id,
        asset_id=asset_id,
        error_message=error_message,
    )

    cost_basis_weights = [(_safe_float(position_lot.get("remaining_cost_basis")) or 0.0) for position_lot in active_lots]
    allocations = _proportional_allocations(amount, cost_basis_weights)
    for index, position_lot in enumerate(active_lots):
        allocation = allocations[index]
        if allocation <= 1e-9:
            continue
        current_remaining_cost = _safe_float(position_lot.get("remaining_cost_basis")) or 0.0
        reduced_amount = min(allocation, current_remaining_cost)
        position_lot["remaining_cost_basis"] = current_remaining_cost - reduced_amount
        position_lot["return_of_capital_amount"] = (
            (_safe_float(position_lot.get("return_of_capital_amount")) or 0.0) + reduced_amount
        )
        _touch_position_lot(position_lot, transaction_id)


def build_position_lots(
    portfolio_id: str,
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    account_id: str | None = None,
    asset_id: str | None = None,
    status: str | None = None,
    as_of_date: date | None = None,
) -> list[dict[str, object]]:
    account_cost_methods = {
        str(account.get("account_id") or ""): str(account.get("cost_basis_method") or "fifo")
        for account in accounts
        if account.get("account_type") == "securities_account"
    }
    position_lots_by_key: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    transfer_lot_slices_by_group: dict[str, list[dict[str, object]]] = {}
    all_position_lots: list[dict[str, object]] = []
    position_lot_by_id: dict[str, dict[str, object]] = {}
    lot_sequence = 0
    sorted_transactions = sorted(transactions, key=_transaction_sort_key)
    entitlement_snapshot_cache: dict[tuple[int, str, str, str], list[dict[str, object]]] = {}

    def next_position_lot_id() -> str:
        nonlocal lot_sequence
        lot_sequence += 1
        return f"plt-{lot_sequence:05d}"

    def append_position_lot(
        *,
        target_account_id: str,
        target_asset_id: str,
        instrument_ref: dict[str, object],
        currency: str,
        opened_by_transaction_id: str,
        opening_transaction_type: str,
        opened_at: str,
        acquisition_date: str,
        entry_quantity: float,
        entry_cost_basis: float,
        source_position_lot_id: str | None = None,
        linked_transaction_ids: set[str] | None = None,
    ) -> dict[str, object]:
        resolved_cost_basis_method = _resolve_cost_basis_method(account_cost_methods, target_account_id)
        position_lot = _new_position_lot(
            position_lot_id=next_position_lot_id(),
            portfolio_id=portfolio_id,
            account_id=target_account_id,
            asset_id=target_asset_id,
            instrument_ref=instrument_ref,
            currency=currency,
            cost_basis_method=resolved_cost_basis_method,
            opened_by_transaction_id=opened_by_transaction_id,
            opening_transaction_type=opening_transaction_type,
            opened_at=opened_at,
            acquisition_date=acquisition_date,
            entry_quantity=entry_quantity,
            entry_cost_basis=entry_cost_basis,
            source_position_lot_id=source_position_lot_id,
            linked_transaction_ids=linked_transaction_ids,
        )
        position_lots_by_key[(target_account_id, target_asset_id)].append(position_lot)
        all_position_lots.append(position_lot)
        position_lot_by_id[str(position_lot.get("position_lot_id") or "")] = position_lot
        return position_lot

    def entitled_position_lot_snapshots(
        *,
        transaction_index: int,
        target_account_id: str,
        target_asset_id: str,
        entitlement_date: date,
        error_message: str,
    ) -> list[dict[str, object]]:
        cache_key = (
            transaction_index,
            target_account_id,
            target_asset_id,
            entitlement_date.isoformat(),
        )
        entitled_lots = entitlement_snapshot_cache.get(cache_key)
        if entitled_lots is None:
            snapshot_transactions = [
                transaction_item
                for transaction_item in sorted_transactions[:transaction_index]
                if (_parse_iso_date(transaction_item.get("trade_date")) or date.min) <= entitlement_date
            ]
            entitled_lots = [
                position_lot
                for position_lot in build_position_lots(
                    portfolio_id,
                    accounts,
                    snapshot_transactions,
                    account_id=target_account_id,
                    asset_id=target_asset_id,
                )
                if (_safe_float(position_lot.get("remaining_quantity")) or 0.0) > 1e-9
            ]
            entitlement_snapshot_cache[cache_key] = entitled_lots
        if not entitled_lots:
            raise ValueError(error_message)
        return entitled_lots

    def allocate_snapshot_cash_flow(
        *,
        transaction_index: int,
        target_account_id: str,
        target_asset_id: str,
        entitlement_date: date,
        amount: float,
        field_name: str,
        weight_field: str,
        transaction_id: str,
        error_message: str,
    ) -> None:
        if amount <= 0:
            return
        entitled_lots = entitled_position_lot_snapshots(
            transaction_index=transaction_index,
            target_account_id=target_account_id,
            target_asset_id=target_asset_id,
            entitlement_date=entitlement_date,
            error_message=error_message,
        )
        weights = [(_safe_float(position_lot.get(weight_field)) or 0.0) for position_lot in entitled_lots]
        allocations = _proportional_allocations(amount, weights)
        for index, snapshot_lot in enumerate(entitled_lots):
            allocation = allocations[index]
            if allocation <= 1e-9:
                continue
            target_position_lot = position_lot_by_id.get(str(snapshot_lot.get("position_lot_id") or ""))
            if target_position_lot is None:
                raise ValueError("Entitled position lot is missing from current lot state.")
            target_position_lot[field_name] = (_safe_float(target_position_lot.get(field_name)) or 0.0) + allocation
            _touch_position_lot(target_position_lot, transaction_id)

    for transaction_index, transaction in enumerate(sorted_transactions):
        transaction_id = str(transaction.get("transaction_id") or "")
        transaction_type = str(transaction.get("transaction_type") or "")
        trade_date = str(transaction.get("trade_date") or "")
        entitlement_date = _parse_iso_date(transaction.get("entitlement_date")) or _parse_iso_date(trade_date)
        currency = str(transaction.get("currency") or "")
        account_key = str(transaction.get("account_id") or "")
        instrument_ref = (
            deepcopy(transaction.get("instrument_ref"))
            if isinstance(transaction.get("instrument_ref"), dict)
            else None
        )
        gross_amount = _safe_float(transaction.get("gross_amount")) or 0.0
        fees = _safe_float(transaction.get("fees")) or 0.0
        taxes = _safe_float(transaction.get("taxes")) or 0.0
        quantity = _safe_float(transaction.get("quantity")) or 0.0
        resolved_asset_id = str(transaction.get("asset_id") or "")
        cost_basis_method = _resolve_cost_basis_method(account_cost_methods, account_key)

        if transaction_type in {"opening_balance", "buy"} and resolved_asset_id and quantity > 0:
            entry_cost_basis = gross_amount
            if transaction_type == "buy":
                entry_cost_basis = gross_amount + fees + taxes
            acquisition_date = (
                str(transaction.get("acquisition_date") or "").strip()
                if transaction_type == "opening_balance"
                else trade_date
            ) or trade_date
            append_position_lot(
                target_account_id=account_key,
                target_asset_id=resolved_asset_id,
                instrument_ref=instrument_ref or {},
                currency=currency,
                opened_by_transaction_id=transaction_id,
                opening_transaction_type=transaction_type,
                opened_at=trade_date,
                acquisition_date=acquisition_date,
                entry_quantity=quantity,
                entry_cost_basis=entry_cost_basis,
            )
            continue

        if transaction_type == "dividend_reinvestment" and resolved_asset_id and quantity > 0:
            _allocate_lot_cash_flow_by_quantity(
                position_lots_by_key,
                account_id=account_key,
                asset_id=resolved_asset_id,
                amount=gross_amount,
                field_name="income_cash_amount",
                transaction_id=transaction_id,
                error_message="Dividend reinvestment requires open position lots as of trade_date.",
            )
            append_position_lot(
                target_account_id=account_key,
                target_asset_id=resolved_asset_id,
                instrument_ref=instrument_ref or {},
                currency=currency,
                opened_by_transaction_id=transaction_id,
                opening_transaction_type=transaction_type,
                opened_at=trade_date,
                acquisition_date=trade_date,
                entry_quantity=quantity,
                entry_cost_basis=gross_amount,
            )
            continue

        if transaction_type in {"sell", "maturity_redemption"} and resolved_asset_id and quantity > 0:
            disposal_slices = _consume_position_lots(
                position_lots_by_key,
                account_id=account_key,
                asset_id=resolved_asset_id,
                quantity=quantity,
                cost_basis_method=cost_basis_method,
            )
            net_proceeds = gross_amount - fees - taxes
            quantity_weights = [(_safe_float(slice_item.get("quantity")) or 0.0) for slice_item in disposal_slices]
            proceeds_allocations = _proportional_allocations(net_proceeds, quantity_weights)
            for index, slice_item in enumerate(disposal_slices):
                position_lot = slice_item["position_lot"]
                matched_quantity = _safe_float(slice_item.get("quantity")) or 0.0
                matched_cost_basis = _safe_float(slice_item.get("cost_basis")) or 0.0
                proceeds = proceeds_allocations[index]
                realized_pnl = proceeds - matched_cost_basis
                position_lot["realized_quantity"] = (
                    (_safe_float(position_lot.get("realized_quantity")) or 0.0) + matched_quantity
                )
                position_lot["realized_cost_basis"] = (
                    (_safe_float(position_lot.get("realized_cost_basis")) or 0.0) + matched_cost_basis
                )
                position_lot["realized_proceeds"] = (
                    (_safe_float(position_lot.get("realized_proceeds")) or 0.0) + proceeds
                )
                position_lot["realized_pnl"] = (
                    (_safe_float(position_lot.get("realized_pnl")) or 0.0) + realized_pnl
                )
                realizations = position_lot.setdefault("realizations", [])
                if isinstance(realizations, list):
                    realizations.append(
                        {
                            "realization_id": f"{position_lot['position_lot_id']}-r{len(realizations) + 1}",
                            "transaction_id": transaction_id,
                            "transaction_type": transaction_type,
                            "trade_date": trade_date,
                            "quantity": matched_quantity,
                            "proceeds": proceeds,
                            "cost_basis_released": matched_cost_basis,
                            "realized_pnl": realized_pnl,
                            "price": _safe_float(transaction.get("price")),
                            "remaining_quantity_after": _safe_float(position_lot.get("remaining_quantity")) or 0.0,
                            "remaining_cost_basis_after": _safe_float(position_lot.get("remaining_cost_basis")) or 0.0,
                            "status_after": _position_lot_status(position_lot),
                            "note": transaction.get("note"),
                        }
                    )
                _touch_position_lot(position_lot, transaction_id)
                _close_position_lot_if_needed(
                    position_lot,
                    close_date=trade_date,
                    close_reason="disposed",
                )
            continue

        if transaction_type in {"dividend", "coupon"} and resolved_asset_id:
            if entitlement_date is None:
                raise ValueError("Instrument income requires entitlement_date.")
            allocate_snapshot_cash_flow(
                transaction_index=transaction_index,
                target_account_id=account_key,
                target_asset_id=resolved_asset_id,
                entitlement_date=entitlement_date,
                amount=gross_amount,
                field_name="income_cash_amount",
                weight_field="remaining_quantity",
                transaction_id=transaction_id,
                error_message="Instrument income requires entitled position lots as of entitlement_date.",
            )
            if fees > 0 or taxes > 0:
                allocate_snapshot_cash_flow(
                    transaction_index=transaction_index,
                    target_account_id=account_key,
                    target_asset_id=resolved_asset_id,
                    entitlement_date=entitlement_date,
                    amount=fees + taxes,
                    field_name="expense_cash_amount",
                    weight_field="remaining_quantity",
                    transaction_id=transaction_id,
                    error_message="Instrument income requires entitled position lots as of entitlement_date.",
                )
            continue

        if transaction_type == "return_of_capital" and resolved_asset_id:
            _apply_position_lot_return_of_capital(
                position_lots_by_key,
                account_id=account_key,
                asset_id=resolved_asset_id,
                amount=gross_amount,
                transaction_id=transaction_id,
                error_message="Return of capital requires open position lots as of trade_date.",
            )
            if fees > 0 or taxes > 0:
                _allocate_lot_cash_flow_by_quantity(
                    position_lots_by_key,
                    account_id=account_key,
                    asset_id=resolved_asset_id,
                    amount=fees + taxes,
                    field_name="expense_cash_amount",
                    transaction_id=transaction_id,
                    error_message="Return of capital requires open position lots as of trade_date.",
                )
            continue

        if transaction_type in {"fee", "tax"} and resolved_asset_id:
            if entitlement_date is None:
                raise ValueError("Instrument-linked expense requires entitlement_date.")
            allocate_snapshot_cash_flow(
                transaction_index=transaction_index,
                target_account_id=account_key,
                target_asset_id=resolved_asset_id,
                entitlement_date=entitlement_date,
                amount=gross_amount,
                field_name="expense_cash_amount",
                weight_field="remaining_quantity",
                transaction_id=transaction_id,
                error_message="Instrument-linked expense requires entitled position lots as of entitlement_date.",
            )
            continue

        if (
            transaction_type == "transfer_out"
            and transaction.get("transfer_object_type") == "position"
            and resolved_asset_id
            and quantity > 0
        ):
            disposal_slices = _consume_position_lots(
                position_lots_by_key,
                account_id=account_key,
                asset_id=resolved_asset_id,
                quantity=quantity,
                cost_basis_method="fifo",
            )
            if not disposal_slices:
                raise ValueError("Position transfer requires source position lots as of trade_date.")
            transferred_slices: list[dict[str, object]] = []
            for slice_item in disposal_slices:
                position_lot = slice_item["position_lot"]
                matched_quantity = _safe_float(slice_item.get("quantity")) or 0.0
                matched_cost_basis = _safe_float(slice_item.get("cost_basis")) or 0.0
                position_lot["transferred_quantity"] = (
                    (_safe_float(position_lot.get("transferred_quantity")) or 0.0) + matched_quantity
                )
                position_lot["transferred_cost_basis"] = (
                    (_safe_float(position_lot.get("transferred_cost_basis")) or 0.0) + matched_cost_basis
                )
                _touch_position_lot(position_lot, transaction_id)
                _close_position_lot_if_needed(
                    position_lot,
                    close_date=trade_date,
                    close_reason="transferred",
                )
                transferred_slices.append(
                    {
                        "account_id": str(transaction.get("counterparty_account_id") or ""),
                        "asset_id": resolved_asset_id,
                        "instrument_ref": deepcopy(position_lot.get("instrument_ref")),
                        "currency": str(position_lot.get("currency") or currency),
                        "opened_by_transaction_id": str(position_lot.get("opened_by_transaction_id") or transaction_id),
                        "opening_transaction_type": str(position_lot.get("opening_transaction_type") or "transfer_in"),
                        "opened_at": str(position_lot.get("opened_at") or trade_date),
                        "acquisition_date": str(
                            position_lot.get("acquisition_date") or position_lot.get("opened_at") or trade_date
                        ),
                        "entry_quantity": matched_quantity,
                        "entry_cost_basis": matched_cost_basis,
                        "source_position_lot_id": str(position_lot.get("position_lot_id") or "") or None,
                    }
                )
            transfer_group_id = str(transaction.get("transfer_group_id") or "")
            if transfer_group_id and transferred_slices:
                transfer_lot_slices_by_group[transfer_group_id] = transferred_slices
            continue

        if (
            transaction_type == "transfer_in"
            and transaction.get("transfer_object_type") == "position"
            and resolved_asset_id
            and quantity > 0
        ):
            transfer_group_id = str(transaction.get("transfer_group_id") or "")
            incoming_slices = list(transfer_lot_slices_by_group.get(transfer_group_id, []))
            if not incoming_slices:
                raise ValueError("Position transfer requires linked source position lots.")
            for incoming_slice in incoming_slices:
                entry_quantity = _safe_float(incoming_slice.get("entry_quantity")) or 0.0
                entry_cost_basis = _safe_float(incoming_slice.get("entry_cost_basis"))
                if entry_quantity <= 0 or entry_cost_basis is None or entry_cost_basis < 0:
                    raise ValueError("Position transfer requires valid linked source lot slices.")
                linked_transaction_ids = {
                    str(incoming_slice.get("opened_by_transaction_id") or transaction_id),
                    transaction_id,
                }
                append_position_lot(
                    target_account_id=account_key,
                    target_asset_id=resolved_asset_id,
                    instrument_ref=(
                        incoming_slice.get("instrument_ref")
                        if isinstance(incoming_slice.get("instrument_ref"), dict)
                        else (instrument_ref or {})
                    ),
                    currency=str(incoming_slice.get("currency") or currency),
                    opened_by_transaction_id=str(incoming_slice.get("opened_by_transaction_id") or transaction_id),
                    opening_transaction_type=str(
                        incoming_slice.get("opening_transaction_type") or transaction_type
                    ),
                    opened_at=str(incoming_slice.get("opened_at") or trade_date),
                    acquisition_date=str(
                        incoming_slice.get("acquisition_date") or incoming_slice.get("opened_at") or trade_date
                    ),
                    entry_quantity=entry_quantity,
                    entry_cost_basis=entry_cost_basis,
                    source_position_lot_id=(
                        str(incoming_slice.get("source_position_lot_id") or "") or None
                    ),
                    linked_transaction_ids=linked_transaction_ids,
                )
            continue

    pricing_map = _resolve_pricing_map(
        {
            str(position_lot.get("asset_id") or "")
            for position_lot in all_position_lots
            if str(position_lot.get("asset_id") or "")
        },
        as_of_date=as_of_date,
    )
    resolved_as_of_date = as_of_date or date.today()
    rendered_position_lots: list[dict[str, object]] = []
    for raw_position_lot in all_position_lots:
        if account_id and raw_position_lot.get("account_id") != account_id:
            continue
        if asset_id and raw_position_lot.get("asset_id") != asset_id:
            continue
        if status and raw_position_lot.get("status") != status:
            continue

        remaining_quantity = _safe_float(raw_position_lot.get("remaining_quantity")) or 0.0
        remaining_cost_basis = _safe_float(raw_position_lot.get("remaining_cost_basis")) or 0.0
        realized_quantity = _safe_float(raw_position_lot.get("realized_quantity")) or 0.0
        entry_quantity = _safe_float(raw_position_lot.get("entry_quantity")) or 0.0
        entry_cost_basis = _safe_float(raw_position_lot.get("entry_cost_basis")) or 0.0
        realized_proceeds = _safe_float(raw_position_lot.get("realized_proceeds")) or 0.0
        asset_price = pricing_map.get(str(raw_position_lot.get("asset_id") or ""))
        instrument_ref = (
            raw_position_lot.get("instrument_ref")
            if isinstance(raw_position_lot.get("instrument_ref"), dict)
            else {}
        )
        acquisition_date_value = _parse_iso_date(raw_position_lot.get("acquisition_date")) or _parse_iso_date(
            raw_position_lot.get("opened_at")
        )
        closed_at_value = _parse_iso_date(raw_position_lot.get("closed_at")) or resolved_as_of_date
        holding_period_days = None
        if acquisition_date_value is not None and closed_at_value is not None:
            holding_period_days = max((closed_at_value - acquisition_date_value).days, 0)
        current_market_value = _position_market_value(
            quantity=remaining_quantity,
            last_price=asset_price,
            instrument_ref=instrument_ref,
        )

        rendered_position_lots.append(
            {
                "position_lot_id": raw_position_lot["position_lot_id"],
                "portfolio_id": portfolio_id,
                "account_id": raw_position_lot["account_id"],
                "asset_id": raw_position_lot["asset_id"],
                "instrument_ref": deepcopy(instrument_ref),
                "currency": raw_position_lot["currency"],
                "cost_basis_method": raw_position_lot["cost_basis_method"],
                "opened_by_transaction_id": raw_position_lot["opened_by_transaction_id"],
                "opening_transaction_type": raw_position_lot["opening_transaction_type"],
                "opened_at": raw_position_lot["opened_at"],
                "acquisition_date": raw_position_lot.get("acquisition_date") or raw_position_lot["opened_at"],
                "closed_at": raw_position_lot.get("closed_at"),
                "status": raw_position_lot["status"],
                "close_reason": raw_position_lot.get("close_reason"),
                "source_position_lot_id": raw_position_lot.get("source_position_lot_id"),
                "entry_quantity": entry_quantity,
                "remaining_quantity": remaining_quantity,
                "realized_quantity": realized_quantity,
                "transferred_quantity": _safe_float(raw_position_lot.get("transferred_quantity")) or 0.0,
                "entry_cost_basis": entry_cost_basis,
                "remaining_cost_basis": remaining_cost_basis,
                "realized_cost_basis": _safe_float(raw_position_lot.get("realized_cost_basis")) or 0.0,
                "transferred_cost_basis": _safe_float(raw_position_lot.get("transferred_cost_basis")) or 0.0,
                "realized_proceeds": realized_proceeds,
                "realized_pnl": _safe_float(raw_position_lot.get("realized_pnl")) or 0.0,
                "income_cash_amount": _safe_float(raw_position_lot.get("income_cash_amount")) or 0.0,
                "expense_cash_amount": _safe_float(raw_position_lot.get("expense_cash_amount")) or 0.0,
                "return_of_capital_amount": _safe_float(raw_position_lot.get("return_of_capital_amount")) or 0.0,
                "entry_price": (entry_cost_basis / entry_quantity) if entry_quantity > 1e-9 else None,
                "average_exit_price": (
                    realized_proceeds / realized_quantity if realized_quantity > 1e-9 else None
                ),
                "current_market_value": current_market_value,
                "unrealized_pnl": (
                    current_market_value - remaining_cost_basis if current_market_value is not None else None
                ),
                "holding_period_days": holding_period_days,
                "linked_transaction_count": len(raw_position_lot.get("_linked_transaction_ids", set())),
                "realization_count": len(raw_position_lot.get("realizations") or []),
                "realizations": deepcopy(raw_position_lot.get("realizations") or []),
            }
        )

    rendered_position_lots.sort(
        key=lambda item: (
            str(item.get("opened_at") or ""),
            str(item.get("position_lot_id") or ""),
        ),
        reverse=True,
    )
    return rendered_position_lots


def summarize_position_lots(position_lots: list[dict[str, object]]) -> dict[str, object]:
    return {
        "position_lot_count": len(position_lots),
        "open_position_lot_count": sum(1 for position_lot in position_lots if position_lot.get("status") == "open"),
        "closed_position_lot_count": sum(
            1 for position_lot in position_lots if position_lot.get("status") == "closed"
        ),
        "realized_pnl": sum((_safe_float(position_lot.get("realized_pnl")) or 0.0) for position_lot in position_lots),
    }


def build_portfolio_positions(
    portfolio_id: str,
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    as_of_date: date | None = None,
) -> list[dict[str, object]]:
    position_lots = build_position_lots(
        portfolio_id,
        accounts,
        transactions,
        status="open",
        as_of_date=as_of_date,
    )
    pricing_map = _resolve_pricing_map(
        {
            str(position_lot.get("asset_id") or "")
            for position_lot in position_lots
            if str(position_lot.get("asset_id") or "")
        },
        as_of_date=as_of_date,
    )
    positions_by_asset: dict[str, dict[str, object]] = {}

    for position_lot in position_lots:
        asset_key = str(position_lot.get("asset_id") or "")
        bucket = positions_by_asset.setdefault(
            asset_key,
            {
                "position_id": asset_key,
                "portfolio_id": portfolio_id,
                "asset_id": asset_key,
                "instrument_ref": deepcopy(position_lot.get("instrument_ref")),
                "quantity": 0.0,
                "cost_basis": 0.0,
                "currency": str(position_lot.get("currency") or ""),
                "account_ids": set(),
                "open_position_lot_count": 0,
            },
        )
        bucket["quantity"] += _safe_float(position_lot.get("remaining_quantity")) or 0.0
        bucket["cost_basis"] += _safe_float(position_lot.get("remaining_cost_basis")) or 0.0
        bucket["open_position_lot_count"] += 1
        bucket["account_ids"].add(str(position_lot.get("account_id") or ""))

    rendered_positions: list[dict[str, object]] = []
    for bucket in positions_by_asset.values():
        quantity = _safe_float(bucket.get("quantity")) or 0.0
        if abs(quantity) <= 1e-9:
            continue
        last_price = pricing_map.get(str(bucket.get("asset_id") or ""))
        account_ids = sorted(account_id for account_id in bucket["account_ids"] if account_id)
        rendered_positions.append(
            {
                "position_id": bucket["position_id"],
                "portfolio_id": portfolio_id,
                "asset_id": bucket["asset_id"],
                "instrument_ref": deepcopy(bucket.get("instrument_ref") or {}),
                "quantity": quantity,
                "cost_basis": _safe_float(bucket.get("cost_basis")),
                "last_price": last_price,
                "market_value": _position_market_value(
                    quantity=quantity,
                    last_price=last_price,
                    instrument_ref=(
                        bucket.get("instrument_ref")
                        if isinstance(bucket.get("instrument_ref"), dict)
                        else None
                    ),
                ),
                "currency": bucket["currency"],
                "account_ids": account_ids,
                "account_count": len(account_ids),
                "open_position_lot_count": int(bucket.get("open_position_lot_count") or 0),
            }
        )

    rendered_positions.sort(
        key=lambda item: (
            -((_safe_float(item.get("market_value")) or 0.0)),
            str(item.get("asset_id") or ""),
        )
    )
    return rendered_positions


def summarize_positions(positions: list[dict[str, object]]) -> dict[str, int]:
    return {
        "position_count": len(positions),
        "priced_position_count": sum(1 for position in positions if position.get("last_price") is not None),
        "open_position_lot_count": sum(
            int(position.get("open_position_lot_count") or 0) for position in positions
        ),
    }


def build_account_workspace(
    portfolio_id: str,
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    selected_account_id: str | None = None,
    base_currency: str = "USD",
    as_of_date: date | None = None,
) -> dict[str, object]:
    account_lookup = {str(account["account_id"]): account for account in accounts}
    resolved_selected_account_id = selected_account_id or next(iter(account_lookup.keys()), None)
    account_cost_methods = {
        str(account.get("account_id") or ""): str(account.get("cost_basis_method") or "fifo")
        for account in accounts
        if account.get("account_type") == "securities_account"
    }
    account_currency_map = {
        str(account.get("account_id") or ""): str(account.get("currency") or "")
        for account in accounts
    }
    boundary_transactions = list(transactions)
    as_of_iso = as_of_date.isoformat() if as_of_date is not None else None
    if as_of_date is not None:
        boundary_transactions = [
            transaction
            for transaction in transactions
            if (_parse_iso_date(transaction.get("trade_date")) or date.min) <= as_of_date
        ]
    postings = derive_ledger_postings(
        portfolio_id,
        boundary_transactions,
        account_cost_methods=account_cost_methods,
        account_currency_map=account_currency_map,
    )
    fx_rate_map = resolve_fx_rate_map()
    convert_amount_on_fn = None
    direct_fx_assets: dict[tuple[str, str], str] = {}
    instrument_detail_cache: dict[str, dict[str, object] | None] = {}
    if as_of_date is not None:
        from portfolio_app.services.performance import convert_amount_on

        convert_amount_on_fn = convert_amount_on
        direct_fx_assets = _direct_fx_asset_map(get_platform_fx_rates())

    def convert_to_base(amount: float | None, *, from_currency: str) -> float | None:
        normalized_currency = str(from_currency or "").strip().upper()
        if as_of_date is None or convert_amount_on_fn is None:
            return convert_amount(
                amount,
                from_currency=normalized_currency,
                to_currency=base_currency,
                fx_rate_map=fx_rate_map,
            )
        converted_amount, _ = convert_amount_on_fn(
            amount,
            as_of_date=as_of_date,
            from_currency=normalized_currency,
            to_currency=base_currency,
            direct_fx_assets=direct_fx_assets,
            instrument_detail_cache=instrument_detail_cache,
        )
        return converted_amount

    linked_transaction_ids: dict[str, set[str]] = defaultdict(set)
    linked_posting_count: dict[str, int] = defaultdict(int)
    cash_balance: dict[str, float] = defaultdict(float)

    for posting in postings:
        account_id = str(posting.get("account_id") or "")
        linked_posting_count[account_id] += 1
        linked_transaction_ids[account_id].add(str(posting.get("transaction_id") or ""))

        cash_delta = _safe_float(posting.get("cash_amount_delta"))
        if cash_delta is not None and (as_of_iso is None or ledger_posting_effective_date_iso(posting) <= as_of_iso):
            cash_balance[account_id] += cash_delta

    position_lots = build_position_lots(
        portfolio_id,
        accounts,
        boundary_transactions,
        status="open",
        as_of_date=as_of_date,
    )
    positions_by_account_asset: dict[tuple[str, str], dict[str, object]] = {}
    for position_lot in position_lots:
        account_id = str(position_lot.get("account_id") or "")
        asset_id = str(position_lot.get("asset_id") or "")
        key = (account_id, asset_id)
        bucket = positions_by_account_asset.setdefault(
            key,
            {
                "position_id": f"{account_id}:{asset_id}",
                "account_id": account_id,
                "asset_id": asset_id,
                "instrument_ref": deepcopy(position_lot.get("instrument_ref")),
                "quantity": 0.0,
                "cost_basis": 0.0,
                "currency": str(position_lot.get("currency") or ""),
                "cost_basis_method": str(position_lot.get("cost_basis_method") or ""),
                "open_position_lot_count": 0,
            },
        )
        bucket["quantity"] += _safe_float(position_lot.get("remaining_quantity")) or 0.0
        bucket["cost_basis"] += _safe_float(position_lot.get("remaining_cost_basis")) or 0.0
        bucket["open_position_lot_count"] += 1

    pricing_map = _resolve_pricing_map(
        {str(bucket.get("asset_id") or "") for bucket in positions_by_account_asset.values()},
        as_of_date=as_of_date,
    )
    positions: list[dict[str, object]] = []
    position_count_by_account: dict[str, int] = defaultdict(int)
    market_value_by_account: dict[str, float] = defaultdict(float)
    valuation_complete_by_account: dict[str, bool] = defaultdict(lambda: True)
    for bucket in positions_by_account_asset.values():
        quantity = float(bucket["quantity"])
        if abs(quantity) < 1e-9:
            continue
        asset_id = str(bucket["asset_id"])
        last_price = pricing_map.get(asset_id)
        market_value = _position_market_value(
            quantity=quantity,
            last_price=last_price,
            instrument_ref=(
                bucket["instrument_ref"]
                if isinstance(bucket.get("instrument_ref"), dict)
                else None
            ),
        )
        account_id = str(bucket["account_id"])
        position_count_by_account[account_id] += 1
        if market_value is not None:
            converted_market_value = convert_to_base(
                market_value,
                from_currency=str(bucket.get("currency") or ""),
            )
            if converted_market_value is None:
                valuation_complete_by_account[account_id] = False
            else:
                market_value_by_account[account_id] += converted_market_value

        positions.append(
            {
                "position_id": bucket["position_id"],
                "account_id": account_id,
                "asset_id": asset_id,
                "instrument_ref": bucket["instrument_ref"],
                "quantity": quantity,
                "cost_basis": float(bucket["cost_basis"]),
                "last_price": last_price,
                "market_value": market_value,
                "currency": bucket["currency"],
                "cost_basis_method": bucket["cost_basis_method"] or None,
                "open_position_lot_count": int(bucket["open_position_lot_count"] or 0),
            }
        )

    positions.sort(
        key=lambda item: (
            str(item.get("account_id") or ""),
            str(item.get("asset_id") or ""),
        )
    )

    account_rows: list[dict[str, object]] = []
    for account in accounts:
        account_id = str(account["account_id"])
        account_currency = str(account.get("currency") or "")
        settlement_name = None
        settlement_id = account.get("default_settlement_cash_account_id")
        if isinstance(settlement_id, str):
            settlement_name = str(account_lookup.get(settlement_id, {}).get("account_name") or "")
        derived_cash_balance = cash_balance[account_id]
        derived_cash_balance_base = convert_to_base(
            derived_cash_balance,
            from_currency=account_currency,
        )
        account_rows.append(
            {
                "account": deepcopy(account),
                "default_settlement_cash_account_name": settlement_name or None,
                "linked_transaction_count": len(linked_transaction_ids[account_id]),
                "linked_posting_count": linked_posting_count[account_id],
                "derived_cash_balance": derived_cash_balance,
                "derived_cash_balance_base": derived_cash_balance_base,
                "position_line_count": position_count_by_account[account_id],
                "position_market_value": (
                    market_value_by_account[account_id]
                    if position_count_by_account[account_id] and valuation_complete_by_account[account_id]
                    else None
                ),
                "position_market_value_currency": base_currency if position_count_by_account[account_id] else None,
            }
        )

    visible_postings = postings
    visible_positions = positions
    if resolved_selected_account_id:
        visible_postings = [posting for posting in postings if posting["account_id"] == resolved_selected_account_id]
        visible_positions = [position for position in positions if position["account_id"] == resolved_selected_account_id]

    return {
        "portfolio_id": portfolio_id,
        "base_currency": base_currency,
        "summary": {
            "account_count": len(accounts),
            "deposit_account_count": sum(1 for account in accounts if account.get("account_type") == "deposit_account"),
            "securities_account_count": sum(
                1 for account in accounts if account.get("account_type") == "securities_account"
            ),
            "ledger_posting_count": len(postings),
            "position_line_count": len(positions),
        },
        "derivation_boundary": {
            "ledger_postings": "next_layer",
            "positions": "next_layer",
            "position_lots": "next_layer",
            "holdings": "next_layer",
            "snapshot": "not_started",
        },
        "selected_account_id": resolved_selected_account_id,
        "accounts": account_rows,
        "ledger_postings": visible_postings,
        "positions": visible_positions,
    }


def list_ledger_postings(
    portfolio_id: str,
    transactions: list[dict[str, object]],
    *,
    account_cost_methods: dict[str, str] | None = None,
    account_currency_map: dict[str, str] | None = None,
    account_id: str | None = None,
    transaction_id: str | None = None,
    asset_id: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict[str, object]]:
    postings = derive_ledger_postings(
        portfolio_id,
        transactions,
        account_cost_methods=account_cost_methods,
        account_currency_map=account_currency_map,
    )
    filtered: list[dict[str, object]] = []
    for posting in postings:
        if account_id and posting.get("account_id") != account_id:
            continue
        if transaction_id and posting.get("transaction_id") != transaction_id:
            continue
        if asset_id and posting.get("asset_id") != asset_id:
            continue

        effective_date_value = ledger_posting_effective_date_iso(posting)
        if start_date and effective_date_value < start_date.isoformat():
            continue
        if end_date and effective_date_value > end_date.isoformat():
            continue

        filtered.append(posting)
    return filtered


def summarize_ledger_postings(postings: list[dict[str, object]]) -> dict[str, int]:
    return {
        "posting_count": len(postings),
        "cash_posting_count": sum(1 for posting in postings if posting.get("cash_amount_delta") is not None),
        "position_posting_count": sum(
            1
            for posting in postings
            if posting.get("quantity_delta") is not None or posting.get("cost_basis_delta") is not None
        ),
    }
