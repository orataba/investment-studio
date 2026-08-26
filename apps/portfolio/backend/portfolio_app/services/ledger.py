from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

from portfolio_ops_instrument_core import VALUATION_PROHIBITED_TOTAL_RETURN_BASES

from portfolio_app.services.instrument_registry import (
    InstrumentRegistryError,
    get_platform_fx_rates,
    get_registry_instrument_details,
    get_registry_instrument_detail,
    list_registry_corporate_actions,
    list_registry_instruments,
)
from portfolio_app.services import valuation_fx
from portfolio_app.services.holdings_market_profile import resolve_position_valuation
from portfolio_app.services.market_data import is_usable_market_data_point
from portfolio_app.services.market_data import quote_policy_bases, resolve_quote_point
from portfolio_app.services.transaction_dates import (
    transaction_ledger_activity_date,
    transaction_performance_effective_date,
    transaction_position_effective_date,
    transaction_precedes_entitlement_bod,
    transaction_sort_key,
)
from portfolio_app.services.transaction_pricing import transaction_price_scale
from portfolio_app.services.option_actions import resolve_option_action
from portfolio_app.services.option_obligations import (
    build_option_obligations,
    derive_option_obligation_events,
    estimate_option_obligation_quantity_at_entitlement,
    option_contract_identity,
)


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _derivative_contract(
    transaction: dict[str, object],
) -> dict[str, object] | None:
    contract = transaction.get("derivative_contract")
    return contract if isinstance(contract, dict) else None


def _position_reference_id(transaction: dict[str, object]) -> str:
    derivative_contract_id = str(
        transaction.get("derivative_contract_id") or ""
    ).strip()
    instrument_id = str(transaction.get("instrument_id") or "").strip()
    if derivative_contract_id and instrument_id:
        raise ValueError(
            "A transaction cannot reference both an instrument and a derivative contract."
        )
    return derivative_contract_id or instrument_id


def _position_reference_fields(transaction: dict[str, object]) -> dict[str, object]:
    return {
        "instrument_id": transaction.get("instrument_id"),
        "instrument_ref": deepcopy(transaction.get("instrument_ref")),
        "derivative_contract_id": transaction.get("derivative_contract_id"),
        "derivative_contract": deepcopy(transaction.get("derivative_contract")),
    }


def _transaction_is_event_valued(transaction: dict[str, object]) -> bool:
    return _derivative_contract(transaction) is not None


def validate_derivative_contract_event(transaction: dict[str, object]) -> None:
    """Validate a derivative fact against its immutable contract terms."""

    derivative_contract = _derivative_contract(transaction)
    if derivative_contract is None:
        return
    contract_type = str(derivative_contract.get("contract_type") or "").strip().lower()
    transaction_type = str(transaction.get("transaction_type") or "").strip()
    lifecycle_event_type = str(
        transaction.get("lifecycle_event_type") or ""
    ).strip()
    event_date = transaction_performance_effective_date(transaction)

    if contract_type == "option":
        option_action = resolve_option_action(transaction)
        if transaction_type == "maturity_redemption" and lifecycle_event_type not in {
            "option_long_expiry",
            "option_long_cash_settlement",
        }:
            raise ValueError(
                "Option maturity redemption requires option_long_expiry or "
                "option_long_cash_settlement."
            )
        if transaction_type == "lifecycle_event" and lifecycle_event_type not in {
            "option_writer_expiry",
            "option_writer_cash_settlement",
        }:
            raise ValueError(
                "Option lifecycle event requires option_writer_expiry or "
                "option_writer_cash_settlement."
            )
        if lifecycle_event_type in {
            "option_long_expiry",
            "option_long_cash_settlement",
        } and transaction_type != "maturity_redemption":
            raise ValueError(
                "Long option outcome requires maturity_redemption transaction type."
            )
        if lifecycle_event_type in {
            "option_writer_expiry",
            "option_writer_cash_settlement",
        } and transaction_type != "lifecycle_event":
            raise ValueError(
                "Writer option outcome requires lifecycle_event transaction type."
            )
        option_lifecycle_event = lifecycle_event_type in {
            "option_long_expiry",
            "option_long_cash_settlement",
            "option_writer_expiry",
            "option_writer_cash_settlement",
        }
        if option_action is None and not option_lifecycle_event:
            return

        identity = option_contract_identity(transaction)
        expiry_date = _parse_iso_date(identity.get("expiry_date"))
        if expiry_date is None or event_date is None:
            raise ValueError("Option event and contract expiry dates are required.")
        if option_action is not None and event_date > expiry_date:
            raise ValueError("Option transaction date must not follow contract expiry.")
        if (
            lifecycle_event_type
            in {"option_long_expiry", "option_writer_expiry"}
            and event_date < expiry_date
        ):
            raise ValueError("Option expiry event must not precede contract expiry.")
        if lifecycle_event_type in {
            "option_long_cash_settlement",
            "option_writer_cash_settlement",
        } and event_date > expiry_date:
            raise ValueError("Option cash settlement must not follow contract expiry.")

        gross_amount = _safe_float(transaction.get("gross_amount")) or 0.0
        fees = _safe_float(transaction.get("fees")) or 0.0
        taxes = _safe_float(transaction.get("taxes")) or 0.0
        settlement_cash_account_id = str(
            transaction.get("settlement_cash_account_id") or ""
        ).strip()
        charges = fees + taxes

        if lifecycle_event_type in {
            "option_long_cash_settlement",
            "option_writer_cash_settlement",
        }:
            if gross_amount <= 1e-9:
                raise ValueError("Option cash settlement requires positive gross_amount.")
            if not settlement_cash_account_id:
                raise ValueError("Option cash settlement requires settlement cash account.")
        elif lifecycle_event_type == "option_writer_expiry":
            if (
                gross_amount > 1e-9
                or charges > 1e-9
                or settlement_cash_account_id
            ):
                raise ValueError("Option writer expiry must not carry cash amounts.")
        elif lifecycle_event_type == "option_long_expiry":
            if (
                gross_amount > 1e-9
                or charges > 1e-9
                or settlement_cash_account_id
            ):
                raise ValueError("Long option expiry must not carry cash amounts.")
        return

    if contract_type != "fcn":
        return
    terms = derivative_contract.get("terms")
    issue_date = _parse_iso_date(
        terms.get("issue_date") if isinstance(terms, dict) else None
    )
    maturity_date = _parse_iso_date(
        terms.get("maturity_date") if isinstance(terms, dict) else None
    )
    if issue_date is None or maturity_date is None or event_date is None:
        raise ValueError("FCN event, issue, and maturity dates are required.")

    if (
        transaction_type == "maturity_redemption"
        and lifecycle_event_type in {"", "fcn_maturity"}
    ):
        if event_date < maturity_date:
            raise ValueError("FCN maturity event must not precede contract maturity.")
        return

    bounded_event = (
        transaction_type in {"buy", "sell", "opening_balance", "coupon"}
        or lifecycle_event_type in {"fcn_knock_in", "fcn_knock_out"}
    )
    if bounded_event and not issue_date <= event_date <= maturity_date:
        raise ValueError(
            "FCN transaction date must fall between contract issue and maturity."
        )


def _resolve_pricing_quote_map(
    instrument_ids: set[str] | None = None,
    *,
    as_of_date: date | None = None,
    instrument_detail_cache: dict[str, dict[str, object] | None] | None = None,
) -> dict[str, object]:
    normalized_instrument_ids = {instrument_id for instrument_id in (instrument_ids or set()) if instrument_id}
    if normalized_instrument_ids or as_of_date is not None:
        target_instrument_ids = normalized_instrument_ids or {
            str(item.get("instrument_id") or "")
            for item in list_registry_instruments()
            if str(item.get("instrument_id") or "")
        }
        missing_instrument_ids = (
            target_instrument_ids
            if instrument_detail_cache is None
            else target_instrument_ids - instrument_detail_cache.keys()
        )
        loaded_details = (
            get_registry_instrument_details(missing_instrument_ids)
            if missing_instrument_ids
            else {}
        )
        if instrument_detail_cache is not None:
            # A bulk miss must not suppress a caller's authoritative fallback
            # loader (performance/reporting layers may provide one).  Positive
            # details are safe to share across the request; unresolved ids stay
            # eligible for fallback resolution.
            instrument_detail_cache.update(
                {
                    instrument_id: detail
                    for instrument_id, detail in loaded_details.items()
                    if isinstance(detail, dict)
                }
            )
            instrument_details = instrument_detail_cache
        else:
            instrument_details = loaded_details
        pricing_map: dict[str, object] = {}
        for instrument_id in target_instrument_ids:
            detail = instrument_details.get(instrument_id)
            if not isinstance(detail, dict):
                continue
            resolved = _select_quote_point(
                detail,
                role="valuation",
                as_of_date=as_of_date,
            )
            if resolved is not None:
                pricing_map[instrument_id] = resolved
        return pricing_map

    instruments = list_registry_instruments()
    pricing_map: dict[str, object] = {}
    for instrument in instruments:
        instrument_id = str(instrument.get("instrument_id") or "")
        if normalized_instrument_ids and instrument_id not in normalized_instrument_ids:
            continue
        resolved = _select_quote_point(instrument, role="valuation")
        if resolved is not None:
            pricing_map[instrument_id] = resolved
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
        if not is_usable_market_data_point(item):
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


def _select_quote_point(
    instrument: dict[str, object],
    *,
    role: str,
    as_of_date: date | None = None,
) -> dict[str, object] | None:
    candidate_bases = quote_policy_bases(instrument, role)
    if role == "valuation" and any(
        quote_basis.strip().lower() in VALUATION_PROHIBITED_TOTAL_RETURN_BASES
        for quote_basis in candidate_bases
    ):
        return None
    resolved_as_of_date = as_of_date or date.max
    return resolve_quote_point(
        instrument,
        candidate_bases=candidate_bases,
        as_of_date=resolved_as_of_date,
    ).point


def _select_quote_value(
    instrument: dict[str, object],
    *,
    role: str,
    as_of_date: date | None = None,
) -> float | None:
    point = _select_quote_point(instrument, role=role, as_of_date=as_of_date)
    return _safe_float((point or {}).get("value"))


def _pricing_value(quote: object) -> float | None:
    if isinstance(quote, dict):
        return _safe_float(quote.get("value"))
    return _safe_float(quote)


def _pricing_scale(quote: object) -> float | None:
    if not isinstance(quote, dict):
        return None
    return _safe_float(quote.get("price_scale"))


def _display_price_from_gross(
    *,
    gross_amount: float,
    quantity: float,
    instrument_ref: dict[str, object] | None,
    derivative_contract: dict[str, object] | None = None,
) -> float | None:
    if quantity <= 1e-9 or (
        instrument_ref is None and derivative_contract is None
    ):
        return None
    try:
        price_scale = transaction_price_scale(
            instrument_ref=instrument_ref,
            derivative_contract=derivative_contract,
        )
    except ValueError:
        return None
    return gross_amount / quantity / price_scale


def _corporate_action_sort_key(event: dict[str, object]) -> tuple[str, int, str, str]:
    return (
        str(event.get("effective_date") or ""),
        0,
        str(event.get("instrument_id") or ""),
        str(event.get("corporate_action_event_id") or ""),
    )


def _transaction_timeline_sort_key(
    transaction: dict[str, object],
) -> tuple[str, int, str, str, str, str]:
    trade_date = str(transaction.get("trade_date") or "")
    effective_date = transaction_performance_effective_date(transaction)
    acquisition_date = str(transaction.get("acquisition_date") or "")
    is_prior_opening_balance = (
        str(transaction.get("transaction_type") or "") == "opening_balance"
        and acquisition_date
        and acquisition_date < trade_date
    )
    return (
        effective_date.isoformat() if effective_date is not None else trade_date,
        -1 if is_prior_opening_balance else 1,
        str(transaction.get("trade_at") or ""),
        str(transaction.get("created_at") or ""),
        str(transaction.get("transaction_id") or ""),
        str(transaction.get("settlement_date") or ""),
    )


def _resolved_corporate_actions(
    transactions: list[dict[str, object]],
    *,
    corporate_actions: list[dict[str, object]] | None,
    as_of_date: date | None,
) -> list[dict[str, object]]:
    instrument_ids = {
        str(transaction.get("instrument_id") or "").strip()
        for transaction in transactions
        if str(transaction.get("instrument_id") or "").strip()
    }
    if not instrument_ids:
        return []
    cutoff = as_of_date or date.today()
    raw_events = (
        corporate_actions
        if corporate_actions is not None
        else list_registry_corporate_actions(
            instrument_ids,
            effective_on_or_before=cutoff,
        )
    )
    resolved = sorted(
        [
            deepcopy(event)
            for event in raw_events
            if str(event.get("instrument_id") or "") in instrument_ids
            and str(event.get("action_type") or "") == "share_split"
            and str(event.get("status") or "") == "confirmed"
            and str(event.get("effective_date") or "") <= cutoff.isoformat()
        ],
        key=_corporate_action_sort_key,
    )
    for event in resolved:
        record_date = _parse_iso_date(event.get("record_date"))
        effective_date = _parse_iso_date(event.get("effective_date"))
        if record_date is None or effective_date is None:
            continue
        intervening = [
            transaction
            for transaction in transactions
            if str(transaction.get("instrument_id") or "")
            == str(event.get("instrument_id") or "")
            and (
                position_date := (
                    transaction_position_effective_date(transaction)
                    or _parse_iso_date(transaction.get("trade_date"))
                )
            )
            is not None
            and record_date < position_date < effective_date
        ]
        if intervening:
            raise ValueError(
                "Corporate-action entitlement has trades between record-date EOD and "
                "effective-date BOD; due-bill processing is required before the split can be applied."
            )
    return resolved


def _rounded_split_quantity(quantity: float, event: dict[str, object]) -> float:
    new_units = Decimal(str(event.get("new_units") or "0"))
    old_units = Decimal(str(event.get("old_units") or "0"))
    if new_units <= 0 or old_units <= 0:
        raise ValueError("Corporate-action share ratio must be positive.")
    raw_quantity = Decimal(str(quantity)) * new_units / old_units
    precision = max(0, int(event.get("quantity_precision") or 0))
    quantum = Decimal("1").scaleb(-precision)
    rounding = str(event.get("quantity_rounding") or "exact")
    if rounding == "truncate":
        resolved = raw_quantity.quantize(quantum, rounding=ROUND_DOWN)
    elif rounding == "round_half_up":
        resolved = raw_quantity.quantize(quantum, rounding=ROUND_HALF_UP)
    elif rounding == "exact":
        resolved = raw_quantity
    elif rounding == "cash_in_lieu":
        raise ValueError(
            "Corporate action requires cash-in-lieu valuation and receivable facts before quantity adjustment."
        )
    else:
        raise ValueError(f"Unsupported corporate-action quantity rounding: {rounding}.")
    return float(resolved)


def _split_lot_quantity_allocations(
    quantities: list[float],
    event: dict[str, object],
) -> list[float]:
    total_quantity = sum(max(quantity, 0.0) for quantity in quantities)
    if total_quantity <= 1e-9:
        return [0.0 for _ in quantities]
    target_total = _rounded_split_quantity(total_quantity, event)
    raw_targets = [
        _rounded_split_quantity(quantity, {**event, "quantity_rounding": "exact"})
        for quantity in quantities
    ]
    raw_total = sum(raw_targets)
    if raw_total <= 1e-9:
        return [0.0 for _ in quantities]
    allocations: list[float] = []
    remaining = target_total
    positive_indices = [index for index, quantity in enumerate(quantities) if quantity > 1e-9]
    last_positive = positive_indices[-1]
    for index, raw_target in enumerate(raw_targets):
        if index == last_positive:
            allocation = max(remaining, 0.0)
        else:
            allocation = max(target_total * raw_target / raw_total, 0.0)
            remaining -= allocation
        allocations.append(allocation)
    return allocations


def _apply_share_split_to_position_state(
    position_state: dict[tuple[str, str], dict[str, object]],
    event: dict[str, object],
    *,
    account_cost_methods: dict[str, str] | None,
) -> list[tuple[str, float]]:
    instrument_id = str(event.get("instrument_id") or "")
    adjustments: list[tuple[str, float]] = []
    for (account_id, bucket_instrument_id), bucket in position_state.items():
        if bucket_instrument_id != instrument_id:
            continue
        current_quantity = _safe_float(bucket.get("quantity")) or 0.0
        if current_quantity <= 1e-9:
            continue
        lots = list(bucket.get("lots", []))
        lot_quantities = [(_safe_float(lot.get("quantity")) or 0.0) for lot in lots]
        target_lot_quantities = _split_lot_quantity_allocations(lot_quantities, event)
        target_quantity = sum(target_lot_quantities)
        if target_quantity <= 1e-9 and (_safe_float(bucket.get("cost_basis")) or 0.0) > 1e-9:
            raise ValueError(
                "Corporate action would eliminate a cost-bearing position; record cash-in-lieu before applying it."
            )
        for lot, target_lot_quantity in zip(lots, target_lot_quantities, strict=True):
            lot["quantity"] = target_lot_quantity
        bucket["lots"] = lots
        bucket["quantity"] = target_quantity
        _normalize_position_bucket(
            bucket,
            _resolve_cost_basis_method(account_cost_methods, account_id),
        )
        adjustments.append((account_id, target_quantity - current_quantity))
    return adjustments


def ledger_posting_effective_date_iso(posting: dict[str, object]) -> str:
    effective_date = str(posting.get("effective_date") or "").strip()
    if effective_date:
        return effective_date
    if _safe_float(posting.get("cash_amount_delta")) is not None:
        return str(posting.get("settlement_date") or posting.get("trade_date") or "")
    return str(posting.get("trade_date") or posting.get("settlement_date") or "")


def ledger_posting_pending_amount_as_of(
    posting: dict[str, object],
    as_of_date: date | None,
) -> float | None:
    """Return an active monetary bridge balance for one posting."""

    pending_amount = _safe_float(posting.get("pending_amount_delta"))
    if pending_amount is None:
        return None
    if as_of_date is None:
        return 0.0
    recognition_start_date = _parse_iso_date(
        posting.get("recognition_start_date")
        or posting.get("settlement_date")
    )
    recognition_end_date = _parse_iso_date(posting.get("effective_date"))
    if (
        recognition_start_date is not None
        and recognition_end_date is not None
        and recognition_start_date <= as_of_date < recognition_end_date
    ):
        return pending_amount
    return 0.0


def _transaction_is_recognized_as_of(
    transaction: dict[str, object],
    as_of_date: date | None,
) -> bool:
    if as_of_date is None:
        return True
    effective_date = transaction_performance_effective_date(transaction)
    return effective_date is not None and effective_date <= as_of_date


def _transaction_has_ledger_activity_as_of(
    transaction: dict[str, object],
    as_of_date: date | None,
) -> bool:
    if as_of_date is None:
        return True
    activity_date = transaction_ledger_activity_date(transaction)
    return activity_date is not None and activity_date <= as_of_date


def _position_recognition_bridge(
    transaction: dict[str, object],
) -> dict[str, object] | None:
    transaction_type = str(transaction.get("transaction_type") or "")
    if transaction_type not in {"buy", "sell", "maturity_redemption"}:
        return None
    settlement_cash_account_id = str(
        transaction.get("settlement_cash_account_id") or ""
    ).strip()
    if not settlement_cash_account_id:
        return None
    settlement_date = _parse_iso_date(transaction.get("settlement_date"))
    position_effective_date = transaction_position_effective_date(transaction)
    if (
        settlement_date is None
        or position_effective_date is None
        or settlement_date >= position_effective_date
    ):
        return None

    gross_amount = _safe_float(transaction.get("gross_amount")) or 0.0
    fees = _safe_float(transaction.get("fees")) or 0.0
    taxes = _safe_float(transaction.get("taxes")) or 0.0
    if transaction_type == "buy":
        pending_amount = gross_amount + fees + taxes
    else:
        pending_amount = -(gross_amount - fees - taxes)

    return {
        "account_id": str(transaction.get("account_id") or ""),
        "settlement_cash_account_id": settlement_cash_account_id,
        "recognition_start_date": settlement_date,
        "recognition_end_date": position_effective_date,
        "pending_amount_delta": pending_amount,
        "settlement_cash_delta": -pending_amount,
    }


def _resolve_cost_basis_method(account_cost_methods: dict[str, str] | None, account_id: str) -> str:
    resolved = str((account_cost_methods or {}).get(account_id) or "fifo")
    return resolved if resolved in {"moving_average", "fifo"} else "fifo"


def _resolve_account_currency(
    account_currency_map: dict[str, str] | None,
    account_id: str,
) -> str:
    return valuation_fx.required_currency(
        (account_currency_map or {}).get(account_id),
        field_name=f"currency for account '{account_id}'",
    )


def _ensure_position_bucket(
    position_state: dict[tuple[str, str], dict[str, object]],
    account_id: str,
    position_reference_id: str,
) -> dict[str, object]:
    return position_state.setdefault(
        (account_id, position_reference_id),
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
        if cost_basis_method == "moving_average":
            bucket["lots"] = (
                [{"quantity": total_quantity, "cost_basis": total_cost_basis}]
                if total_quantity > 1e-9
                else []
            )
            bucket["quantity"] = 0.0 if abs(total_quantity) < 1e-9 else total_quantity
            bucket["cost_basis"] = 0.0 if abs(total_cost_basis) < 1e-9 else total_cost_basis
            return
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
    position_reference_id: str,
    quantity: float,
    cost_basis_method: str,
) -> float:
    bucket = position_state.get((account_id, position_reference_id))
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
    position_reference_id: str,
    *,
    quantity: float,
    cost_basis: float,
    cost_basis_method: str,
    incoming_lots: list[dict[str, float]] | None = None,
) -> None:
    if quantity <= 0 and cost_basis <= 0:
        return
    bucket = _ensure_position_bucket(
        position_state,
        account_id,
        position_reference_id,
    )
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
    position_reference_id: str,
    *,
    quantity: float,
    cost_basis_method: str,
    error_message: str | None = None,
) -> tuple[float, list[dict[str, float]]]:
    bucket = _ensure_position_bucket(
        position_state,
        account_id,
        position_reference_id,
    )
    if quantity <= 0:
        return 0.0, []

    current_quantity = _safe_float(bucket.get("quantity")) or 0.0
    current_cost_basis = _safe_float(bucket.get("cost_basis")) or 0.0
    if current_quantity <= 1e-9 or quantity > current_quantity + 1e-9:
        raise ValueError(error_message or "Position quantity exceeds available lots as of trade_date.")

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
    position_reference_id: str,
    *,
    amount: float,
    cost_basis_method: str,
) -> float:
    bucket = _ensure_position_bucket(
        position_state,
        account_id,
        position_reference_id,
    )
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
    corporate_actions: list[dict[str, object]] | None = None,
    as_of_date: date | None = None,
) -> dict[tuple[str, str], dict[str, object]]:
    position_state: dict[tuple[str, str], dict[str, object]] = {}
    position_transfer_lots_by_group: dict[str, list[dict[str, float]]] = {}

    resolved_actions = _resolved_corporate_actions(
        transactions,
        corporate_actions=corporate_actions,
        as_of_date=as_of_date,
    )
    recognized_transactions = [
        transaction
        for transaction in transactions
        if _transaction_is_recognized_as_of(transaction, as_of_date)
    ]
    timeline: list[tuple[str, dict[str, object]]] = [
        ("corporate_action", event) for event in resolved_actions
    ] + [
        ("transaction", transaction)
        for transaction in recognized_transactions
    ]
    timeline.sort(
        key=lambda item: (
            _corporate_action_sort_key(item[1])
            if item[0] == "corporate_action"
            else _transaction_timeline_sort_key(item[1])
        )
    )

    for item_kind, transaction in timeline:
        if item_kind == "corporate_action":
            _apply_share_split_to_position_state(
                position_state,
                transaction,
                account_cost_methods=account_cost_methods,
            )
            continue
        transaction_type = str(transaction.get("transaction_type") or "")
        gross_amount = _safe_float(transaction.get("gross_amount")) or 0.0
        fees = _safe_float(transaction.get("fees")) or 0.0
        taxes = _safe_float(transaction.get("taxes")) or 0.0
        quantity = _safe_float(transaction.get("quantity"))
        account_id = str(transaction.get("account_id") or "")
        position_reference_id = _position_reference_id(transaction)
        cost_basis_method = _resolve_cost_basis_method(account_cost_methods, account_id)

        if not position_reference_id:
            continue

        if transaction_type == "opening_balance":
            _add_position_state(
                position_state,
                account_id,
                position_reference_id,
                quantity=quantity or 0.0,
                cost_basis=gross_amount,
                cost_basis_method=cost_basis_method,
            )
            continue

        if transaction_type == "buy":
            bought_cost_basis = (
                gross_amount
                if _transaction_is_event_valued(transaction)
                else gross_amount + fees + taxes
            )
            _add_position_state(
                position_state,
                account_id,
                position_reference_id,
                quantity=quantity or 0.0,
                cost_basis=bought_cost_basis,
                cost_basis_method=cost_basis_method,
            )
            continue

        if transaction_type in {"sell", "maturity_redemption"}:
            _consume_position_state(
                position_state,
                account_id,
                position_reference_id,
                quantity=quantity or 0.0,
                cost_basis_method=cost_basis_method,
                error_message="Transaction quantity exceeds account position as of position_effective_date.",
            )
            continue

        if transaction_type == "return_of_capital":
            _apply_return_of_capital(
                position_state,
                account_id,
                position_reference_id,
                amount=gross_amount,
                cost_basis_method=cost_basis_method,
            )
            continue

        if transaction_type == "dividend_reinvestment":
            _add_position_state(
                position_state,
                account_id,
                position_reference_id,
                quantity=quantity or 0.0,
                cost_basis=gross_amount,
                cost_basis_method=cost_basis_method,
            )
            continue

        if transaction_type == "transfer_out" and transaction.get("transfer_object_type") == "position":
            transferred_cost_basis, transferred_lots = _consume_position_state(
                position_state,
                account_id,
                position_reference_id,
                quantity=quantity or 0.0,
                cost_basis_method=cost_basis_method,
                error_message="Position transfer requires source lots as of trade_date.",
            )
            if transferred_cost_basis < -1e-9 or not transferred_lots:
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
            if received_cost_basis < -1e-9:
                raise ValueError("Position transfer requires non-negative linked source lot cost basis.")
            _add_position_state(
                position_state,
                account_id,
                position_reference_id,
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
    corporate_actions: list[dict[str, object]] | None = None,
    as_of_date: date | None = None,
) -> list[dict[str, object]]:
    postings: list[dict[str, object]] = []
    position_state: dict[tuple[str, str], dict[str, object]] = {}
    position_transfer_lots_by_group: dict[str, list[dict[str, float]]] = {}
    option_events_by_transaction: dict[str, list[dict[str, object]]] = defaultdict(list)

    def append_posting(
        transaction: dict[str, object],
        *,
        posting_role: str,
        account_id: str,
        cash_amount_delta: float | None = None,
        pending_amount_delta: float | None = None,
        quantity_delta: float | None = None,
        cost_basis_delta: float | None = None,
        currency: str | None = None,
        recognition_start_date: date | None = None,
        explicit_effective_date: date | None = None,
        attribution_account_id: str | None = None,
        liability_amount_delta: float | None = None,
        realized_pnl_delta: float | None = None,
        obligation_id: str | None = None,
    ) -> None:
        posting_currency = valuation_fx.required_currency(
            currency if currency is not None else transaction.get("currency"),
            field_name="ledger-posting currency",
        )
        posting_index = len([item for item in postings if item["transaction_id"] == transaction["transaction_id"]]) + 1
        postings.append(
            {
                "posting_id": f"{transaction['transaction_id']}-p{posting_index}",
                "transaction_id": transaction["transaction_id"],
                "portfolio_id": portfolio_id,
                "account_id": account_id,
                "attribution_account_id": attribution_account_id,
                "posting_role": posting_role,
                "source_transaction_type": transaction["transaction_type"],
                "trade_date": transaction["trade_date"],
                "trade_at": transaction.get("trade_at"),
                "settlement_date": transaction["settlement_date"],
                "effective_date": (
                    explicit_effective_date.isoformat()
                    if explicit_effective_date is not None
                    else (
                        transaction["settlement_date"]
                        if cash_amount_delta is not None
                        else (
                            transaction_position_effective_date(transaction)
                            or _parse_iso_date(transaction.get("trade_date"))
                        ).isoformat()
                    )
                ),
                "recognition_start_date": (
                    recognition_start_date.isoformat()
                    if recognition_start_date is not None
                    else None
                ),
                "settlement_cash_account_id": transaction.get(
                    "settlement_cash_account_id"
                ),
                **_position_reference_fields(transaction),
                "cash_amount_delta": cash_amount_delta,
                "pending_amount_delta": pending_amount_delta,
                "quantity_delta": quantity_delta,
                "cost_basis_delta": cost_basis_delta,
                "liability_amount_delta": liability_amount_delta,
                "realized_pnl_delta": realized_pnl_delta,
                "option_action": resolve_option_action(transaction),
                "obligation_id": obligation_id,
                "currency": posting_currency,
                "transfer_group_id": transaction.get("transfer_group_id"),
                "note": transaction.get("note"),
                "created_at": transaction.get("created_at"),
            }
        )

    for transaction in transactions:
        bridge = _position_recognition_bridge(transaction)
        if bridge is None:
            continue
        recognition_start_date = bridge["recognition_start_date"]
        recognition_end_date = bridge["recognition_end_date"]
        append_posting(
            transaction,
            posting_role="position_recognition_bridge",
            account_id=str(bridge["settlement_cash_account_id"]),
            attribution_account_id=str(bridge["account_id"]),
            pending_amount_delta=float(bridge["pending_amount_delta"]),
            currency=str(transaction.get("currency") or ""),
            recognition_start_date=recognition_start_date,
            explicit_effective_date=recognition_end_date,
        )
        if (
            as_of_date is not None
            and recognition_start_date <= as_of_date < recognition_end_date
        ):
            transaction_type = str(transaction.get("transaction_type") or "")
            append_posting(
                transaction,
                posting_role=(
                    "security_redemption_cash"
                    if transaction_type == "maturity_redemption"
                    else "security_settlement_cash"
                ),
                account_id=str(bridge["settlement_cash_account_id"]),
                cash_amount_delta=float(bridge["settlement_cash_delta"]),
                currency=str(transaction.get("currency") or ""),
            )

    resolved_actions = _resolved_corporate_actions(
        transactions,
        corporate_actions=corporate_actions,
        as_of_date=as_of_date,
    )
    recognized_transactions = [
        transaction
        for transaction in transactions
        if _transaction_is_recognized_as_of(transaction, as_of_date)
    ]
    for event in derive_option_obligation_events(
        recognized_transactions,
        as_of_date=as_of_date,
    ):
        transaction_id = str(event.get("transaction_id") or "")
        if transaction_id:
            option_events_by_transaction[transaction_id].append(event)
    timeline: list[tuple[str, dict[str, object]]] = [
        ("corporate_action", event) for event in resolved_actions
    ] + [
        ("transaction", transaction)
        for transaction in recognized_transactions
    ]
    timeline.sort(
        key=lambda item: (
            _corporate_action_sort_key(item[1])
            if item[0] == "corporate_action"
            else _transaction_timeline_sort_key(item[1])
        )
    )

    for item_kind, transaction in timeline:
        if item_kind == "corporate_action":
            event_id = str(transaction.get("corporate_action_event_id") or "")
            effective_date = str(transaction.get("effective_date") or "")
            instrument_id = str(transaction.get("instrument_id") or "")
            adjustments = _apply_share_split_to_position_state(
                position_state,
                transaction,
                account_cost_methods=account_cost_methods,
            )
            for account_id, quantity_delta in adjustments:
                if abs(quantity_delta) <= 1e-9:
                    continue
                postings.append(
                    {
                        "posting_id": f"{event_id}:{account_id}",
                        "transaction_id": event_id,
                        "portfolio_id": portfolio_id,
                        "account_id": account_id,
                        "posting_role": "corporate_action_position_adjustment",
                        "source_transaction_type": "corporate_action",
                        "trade_date": effective_date,
                        "trade_at": f"{effective_date}T00:00:00",
                        "settlement_date": effective_date,
                        "effective_date": effective_date,
                        "instrument_id": instrument_id,
                        "instrument_ref": None,
                        "derivative_contract_id": None,
                        "derivative_contract": None,
                        "cash_amount_delta": None,
                        "quantity_delta": quantity_delta,
                        "cost_basis_delta": 0.0,
                        "currency": "",
                        "transfer_group_id": None,
                        "note": (
                            f"Share split {transaction.get('new_units')}:{transaction.get('old_units')} "
                            f"({event_id})"
                        ),
                        "created_at": transaction.get("updated_at"),
                        "corporate_action_event": deepcopy(transaction),
                    }
                )
            continue
        transaction_type = str(transaction.get("transaction_type") or "")
        gross_amount = _safe_float(transaction.get("gross_amount")) or 0.0
        fees = _safe_float(transaction.get("fees")) or 0.0
        taxes = _safe_float(transaction.get("taxes")) or 0.0
        quantity = _safe_float(transaction.get("quantity"))
        account_id = str(transaction.get("account_id") or "")
        settlement_cash_account_id = transaction.get("settlement_cash_account_id")
        currency = valuation_fx.required_currency(
            transaction.get("currency"), field_name="transaction currency"
        )
        position_reference_id = _position_reference_id(transaction)
        cost_basis_method = _resolve_cost_basis_method(account_cost_methods, account_id)

        if transaction_type == "opening_balance":
            if position_reference_id:
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
                    position_reference_id,
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
                    currency=_resolve_account_currency(account_currency_map, target_account_id),
                )
            continue

        if transaction_type == "buy":
            bought_quantity = quantity or 0.0
            bought_cost_basis = (
                gross_amount
                if _transaction_is_event_valued(transaction)
                else gross_amount + fees + taxes
            )
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
                position_reference_id,
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
                position_reference_id,
                quantity=sold_quantity,
                cost_basis_method=cost_basis_method,
                error_message="Transaction quantity exceeds account position as of position_effective_date.",
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

        option_action = resolve_option_action(transaction)
        if option_action == "sell_to_open":
            if isinstance(settlement_cash_account_id, str) and settlement_cash_account_id:
                append_posting(
                    transaction,
                    posting_role="security_settlement_cash",
                    account_id=settlement_cash_account_id,
                    cash_amount_delta=gross_amount - fees - taxes,
                    currency=currency,
                )
            for event in option_events_by_transaction.get(
                str(transaction.get("transaction_id") or ""), []
            ):
                append_posting(
                    transaction,
                    posting_role="option_premium_liability",
                    account_id=account_id,
                    liability_amount_delta=float(event.get("liability_delta") or 0.0),
                    realized_pnl_delta=float(event.get("realized_pnl_delta") or 0.0),
                    currency=currency,
                    obligation_id=str(event.get("obligation_id") or "") or None,
                )
            continue

        if option_action == "buy_to_close":
            if isinstance(settlement_cash_account_id, str) and settlement_cash_account_id:
                append_posting(
                    transaction,
                    posting_role="security_settlement_cash",
                    account_id=settlement_cash_account_id,
                    cash_amount_delta=-(gross_amount + fees + taxes),
                    currency=currency,
                )
            for event in option_events_by_transaction.get(
                str(transaction.get("transaction_id") or ""), []
            ):
                append_posting(
                    transaction,
                    posting_role="option_liability_release",
                    account_id=account_id,
                    liability_amount_delta=float(event.get("liability_delta") or 0.0),
                    realized_pnl_delta=float(event.get("realized_pnl_delta") or 0.0),
                    currency=currency,
                    obligation_id=str(event.get("obligation_id") or "") or None,
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
                position_reference_id,
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
                position_reference_id,
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
                position_reference_id,
                quantity=redeemed_quantity,
                cost_basis_method=cost_basis_method,
                error_message="Transaction quantity exceeds account position as of position_effective_date.",
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

        if transaction_type == "lifecycle_event" and str(
            transaction.get("lifecycle_event_type") or ""
        ) in {"option_writer_expiry", "option_writer_cash_settlement"}:
            if (
                str(transaction.get("lifecycle_event_type") or "")
                == "option_writer_cash_settlement"
                and isinstance(settlement_cash_account_id, str)
                and settlement_cash_account_id
            ):
                append_posting(
                    transaction,
                    posting_role="security_settlement_cash",
                    account_id=settlement_cash_account_id,
                    cash_amount_delta=-(gross_amount + fees + taxes),
                    currency=currency,
                )
            for event in option_events_by_transaction.get(
                str(transaction.get("transaction_id") or ""), []
            ):
                append_posting(
                    transaction,
                    posting_role="option_liability_release",
                    account_id=account_id,
                    liability_amount_delta=float(event.get("liability_delta") or 0.0),
                    realized_pnl_delta=float(event.get("realized_pnl_delta") or 0.0),
                    currency=currency,
                    obligation_id=str(event.get("obligation_id") or "") or None,
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
                    position_reference_id,
                    quantity=transferred_quantity,
                    cost_basis_method=cost_basis_method,
                    error_message="Position transfer requires source lots as of trade_date.",
                )
                if transferred_cost_basis < -1e-9 or not transferred_lots:
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
                if received_cost_basis < -1e-9:
                    raise ValueError("Position transfer requires non-negative linked source lot cost basis.")
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
                    position_reference_id,
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
    position_reference_id: str,
    quantity: float,
    account_cost_methods: dict[str, str] | None = None,
    consumption_method: str | None = None,
    corporate_actions: list[dict[str, object]] | None = None,
    as_of_date: date | None = None,
) -> float:
    if quantity <= 0:
        return 0.0

    position_state = _build_position_state(
        transactions,
        account_cost_methods=account_cost_methods,
        corporate_actions=corporate_actions,
        as_of_date=as_of_date,
    )
    bucket = position_state.get((account_id, position_reference_id))
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
        position_reference_id,
        quantity=min(quantity, current_quantity),
        cost_basis_method=cost_basis_method,
    )


def estimate_position_remaining_cost_basis(
    portfolio_id: str,
    transactions: list[dict[str, object]],
    *,
    account_id: str,
    position_reference_id: str,
    account_cost_methods: dict[str, str] | None = None,
    corporate_actions: list[dict[str, object]] | None = None,
    as_of_date: date | None = None,
) -> float:
    position_state = _build_position_state(
        transactions,
        account_cost_methods=account_cost_methods,
        corporate_actions=corporate_actions,
        as_of_date=as_of_date,
    )
    bucket = position_state.get((account_id, position_reference_id))
    if bucket is None:
        return 0.0
    return _safe_float(bucket.get("cost_basis")) or 0.0


def estimate_position_quantity(
    portfolio_id: str,
    transactions: list[dict[str, object]],
    *,
    account_id: str,
    position_reference_id: str,
    account_cost_methods: dict[str, str] | None = None,
    corporate_actions: list[dict[str, object]] | None = None,
    as_of_date: date | None = None,
) -> float:
    position_state = _build_position_state(
        transactions,
        account_cost_methods=account_cost_methods,
        corporate_actions=corporate_actions,
        as_of_date=as_of_date,
    )
    bucket = position_state.get((account_id, position_reference_id))
    if bucket is None:
        return 0.0
    return _safe_float(bucket.get("quantity")) or 0.0


def validate_transaction_position_history(
    portfolio_id: str,
    transactions: list[dict[str, object]],
    *,
    account_cost_methods: dict[str, str] | None = None,
    corporate_actions: list[dict[str, object]] | None = None,
) -> None:
    """Validate every position-consuming fact against one coherent history.

    The normal position-state replay covers trades, redemptions, return of
    capital, reinvestments, and paired position transfers. Instrument-linked
    income/expenses use their entitlement boundary, so those facts need an
    additional point-in-time ownership check.
    """

    ordered_transactions = sorted(transactions, key=transaction_sort_key)
    for transaction in ordered_transactions:
        validate_derivative_contract_event(transaction)
    _build_position_state(
        ordered_transactions,
        account_cost_methods=account_cost_methods,
        corporate_actions=corporate_actions,
    )
    # Rebuild the durable writer-obligation subledger as part of the same
    # history validation. This catches partial-close/expiry/cash-settlement
    # quantity errors before a transaction can be persisted.
    derive_option_obligation_events(ordered_transactions)

    for transaction in ordered_transactions:
        transaction_type = str(transaction.get("transaction_type") or "")
        position_reference_id = _position_reference_id(transaction)
        if (
            position_reference_id
            and transaction_type == "transfer_out"
            and transaction.get("transfer_object_type") == "position"
        ):
            transaction_id = str(transaction.get("transaction_id") or "")
            current_transaction_sort_key = transaction_sort_key(transaction)
            prior_transactions = [
                candidate
                for candidate in ordered_transactions
                if str(candidate.get("transaction_id") or "") != transaction_id
                and transaction_sort_key(candidate) <= current_transaction_sort_key
            ]
            expected_cost_basis = estimate_position_cost_basis(
                portfolio_id,
                prior_transactions,
                account_id=str(transaction.get("account_id") or ""),
                position_reference_id=position_reference_id,
                quantity=_safe_float(transaction.get("quantity")) or 0.0,
                account_cost_methods=account_cost_methods,
                corporate_actions=corporate_actions,
                as_of_date=_parse_iso_date(transaction.get("trade_date")),
            )
            recorded_cost_basis = _safe_float(transaction.get("gross_amount")) or 0.0
            if abs(recorded_cost_basis - expected_cost_basis) > 1e-6:
                raise ValueError(
                    "Position transfer gross_amount must match source cost basis as of trade_date."
                )

        if not position_reference_id or not (
            transaction_type in {"dividend", "dividend_reinvestment", "coupon"}
            or transaction_type in {"fee", "tax"}
        ):
            continue

        transaction_id = str(transaction.get("transaction_id") or "")
        trade_date = _parse_iso_date(transaction.get("trade_date")) or date.min
        entitlement_date = _parse_iso_date(transaction.get("entitlement_date")) or trade_date
        prior_transactions = [
            candidate
            for candidate in ordered_transactions
            if str(candidate.get("transaction_id") or "") != transaction_id
            and transaction_precedes_entitlement_bod(candidate, entitlement_date)
        ]

        available_quantity = estimate_position_quantity(
            portfolio_id,
            prior_transactions,
            account_id=str(transaction.get("account_id") or ""),
            position_reference_id=position_reference_id,
            account_cost_methods=account_cost_methods,
            corporate_actions=corporate_actions,
            as_of_date=entitlement_date,
        )
        derivative_contract = _derivative_contract(transaction)
        if (
            available_quantity <= 1e-9
            and isinstance(derivative_contract, dict)
            and str(derivative_contract.get("contract_type") or "").lower()
            == "option"
        ):
            available_quantity = estimate_option_obligation_quantity_at_entitlement(
                ordered_transactions,
                account_id=str(transaction.get("account_id") or ""),
                derivative_contract_id=str(
                    transaction.get("derivative_contract_id") or ""
                ),
                entitlement_date=entitlement_date,
                exclude_transaction_id=transaction_id,
            )
        if available_quantity <= 1e-9:
            raise ValueError(
                "Asset-linked income and expense requires an account position or "
                "written-option obligation as of entitlement_date."
            )


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
    position_reference_id: str,
    instrument_id: str | None,
    instrument_ref: dict[str, object] | None,
    derivative_contract_id: str | None,
    derivative_contract: dict[str, object] | None,
    currency: str,
    cost_basis_method: str,
    opened_by_transaction_id: str,
    opening_transaction_type: str,
    opened_at: str,
    acquisition_date: str,
    entry_quantity: float,
    entry_gross_amount: float,
    entry_fee_amount: float,
    entry_tax_amount: float,
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
        "position_reference_id": position_reference_id,
        "instrument_id": instrument_id,
        "instrument_ref": deepcopy(instrument_ref),
        "derivative_contract_id": derivative_contract_id,
        "derivative_contract": deepcopy(derivative_contract),
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
        "entry_gross_amount": entry_gross_amount,
        "entry_fee_amount": entry_fee_amount,
        "entry_tax_amount": entry_tax_amount,
        "entry_cost_basis": entry_cost_basis,
        "remaining_cost_basis": entry_cost_basis,
        "realized_cost_basis": 0.0,
        "transferred_cost_basis": 0.0,
        "realized_gross_proceeds": 0.0,
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


def _entry_amount_slice(position_lot: dict[str, object], field_name: str, quantity: float) -> float:
    if quantity <= 0:
        return 0.0
    entry_quantity = _safe_float(position_lot.get("entry_quantity")) or 0.0
    entry_amount = _safe_float(position_lot.get(field_name)) or 0.0
    if entry_quantity <= 1e-9 or entry_amount <= 1e-9:
        return 0.0
    return entry_amount * (quantity / entry_quantity)


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
    position_reference_id: str,
) -> list[dict[str, object]]:
    return [
        position_lot
        for position_lot in position_lots_by_key.get(
            (account_id, position_reference_id),
            [],
        )
        if (_safe_float(position_lot.get("remaining_quantity")) or 0.0) > 1e-9
    ]


def _require_active_position_lots(
    position_lots_by_key: dict[tuple[str, str], list[dict[str, object]]],
    *,
    account_id: str,
    position_reference_id: str,
    error_message: str,
) -> list[dict[str, object]]:
    active_lots = _active_position_lots(
        position_lots_by_key,
        account_id,
        position_reference_id,
    )
    if not active_lots:
        raise ValueError(error_message)
    return active_lots


def _consume_position_lots(
    position_lots_by_key: dict[tuple[str, str], list[dict[str, object]]],
    *,
    account_id: str,
    position_reference_id: str,
    quantity: float,
    cost_basis_method: str,
    error_message: str | None = None,
) -> list[dict[str, object]]:
    active_lots = _active_position_lots(
        position_lots_by_key,
        account_id,
        position_reference_id,
    )
    if quantity <= 0:
        return []

    total_open_quantity = sum((_safe_float(lot.get("remaining_quantity")) or 0.0) for lot in active_lots)
    if total_open_quantity <= 1e-9 or quantity > total_open_quantity + 1e-9:
        raise ValueError(
            error_message
            or "Position quantity exceeds available position lots as of position_effective_date."
        )
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
                    "entry_gross_amount": _entry_amount_slice(
                        position_lot,
                        "entry_gross_amount",
                        take_quantity,
                    ),
                    "entry_fee_amount": _entry_amount_slice(
                        position_lot,
                        "entry_fee_amount",
                        take_quantity,
                    ),
                    "entry_tax_amount": _entry_amount_slice(
                        position_lot,
                        "entry_tax_amount",
                        take_quantity,
                    ),
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
        entry_gross_amount = take_cost_basis
        entry_fee_amount = 0.0
        entry_tax_amount = 0.0
        if cost_basis_method != "moving_average":
            entry_gross_amount = _entry_amount_slice(
                position_lot,
                "entry_gross_amount",
                take_quantity,
            )
            entry_fee_amount = _entry_amount_slice(
                position_lot,
                "entry_fee_amount",
                take_quantity,
            )
            entry_tax_amount = _entry_amount_slice(
                position_lot,
                "entry_tax_amount",
                take_quantity,
            )
        position_lot["remaining_quantity"] = lot_quantity - take_quantity
        position_lot["remaining_cost_basis"] = lot_cost_basis - take_cost_basis
        slices.append(
            {
                "position_lot": position_lot,
                "quantity": take_quantity,
                "cost_basis": take_cost_basis,
                "entry_gross_amount": entry_gross_amount,
                "entry_fee_amount": entry_fee_amount,
                "entry_tax_amount": entry_tax_amount,
            }
        )
    return slices


def _allocate_lot_cash_flow_by_quantity(
    position_lots_by_key: dict[tuple[str, str], list[dict[str, object]]],
    *,
    account_id: str,
    position_reference_id: str,
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
        position_reference_id=position_reference_id,
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
    position_reference_id: str,
    amount: float,
    transaction_id: str,
    error_message: str,
) -> None:
    if amount <= 0:
        return
    active_lots = _require_active_position_lots(
        position_lots_by_key,
        account_id=account_id,
        position_reference_id=position_reference_id,
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
    position_reference_id: str | None = None,
    status: str | None = None,
    as_of_date: date | None = None,
    corporate_actions: list[dict[str, object]] | None = None,
    pricing_map: dict[str, object] | None = None,
    instrument_detail_cache: dict[str, dict[str, object] | None] | None = None,
    resolve_pricing: bool = True,
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
    sorted_transactions = sorted(
        [
            transaction
            for transaction in transactions
            if _transaction_is_recognized_as_of(transaction, as_of_date)
        ],
        key=transaction_sort_key,
    )
    resolved_actions = _resolved_corporate_actions(
        sorted_transactions,
        corporate_actions=corporate_actions,
        as_of_date=as_of_date,
    )
    entitlement_snapshot_cache: dict[tuple[int, str, str, str], list[dict[str, object]]] = {}

    def next_position_lot_id() -> str:
        nonlocal lot_sequence
        lot_sequence += 1
        return f"plt-{lot_sequence:05d}"

    def append_position_lot(
        *,
        target_account_id: str,
        target_position_reference_id: str,
        instrument_id: str | None,
        instrument_ref: dict[str, object] | None,
        derivative_contract_id: str | None,
        derivative_contract: dict[str, object] | None,
        currency: str,
        opened_by_transaction_id: str,
        opening_transaction_type: str,
        opened_at: str,
        acquisition_date: str,
        entry_quantity: float,
        entry_gross_amount: float,
        entry_fee_amount: float,
        entry_tax_amount: float,
        entry_cost_basis: float,
        source_position_lot_id: str | None = None,
        linked_transaction_ids: set[str] | None = None,
    ) -> dict[str, object]:
        resolved_cost_basis_method = _resolve_cost_basis_method(account_cost_methods, target_account_id)
        if resolved_cost_basis_method == "moving_average":
            active_lots = _active_position_lots(
                position_lots_by_key,
                target_account_id,
                target_position_reference_id,
            )
            if active_lots:
                position_lot = active_lots[0]
                position_lot["entry_quantity"] = (_safe_float(position_lot.get("entry_quantity")) or 0.0) + entry_quantity
                position_lot["remaining_quantity"] = (
                    (_safe_float(position_lot.get("remaining_quantity")) or 0.0) + entry_quantity
                )
                position_lot["entry_gross_amount"] = (
                    (_safe_float(position_lot.get("entry_gross_amount")) or 0.0) + entry_gross_amount
                )
                position_lot["entry_fee_amount"] = (
                    (_safe_float(position_lot.get("entry_fee_amount")) or 0.0) + entry_fee_amount
                )
                position_lot["entry_tax_amount"] = (
                    (_safe_float(position_lot.get("entry_tax_amount")) or 0.0) + entry_tax_amount
                )
                position_lot["entry_cost_basis"] = (
                    (_safe_float(position_lot.get("entry_cost_basis")) or 0.0) + entry_cost_basis
                )
                position_lot["remaining_cost_basis"] = (
                    (_safe_float(position_lot.get("remaining_cost_basis")) or 0.0) + entry_cost_basis
                )
                if not isinstance(position_lot.get("instrument_ref"), dict) or not position_lot.get("instrument_ref"):
                    position_lot["instrument_ref"] = deepcopy(instrument_ref)
                if (
                    not isinstance(position_lot.get("derivative_contract"), dict)
                    or not position_lot.get("derivative_contract")
                ):
                    position_lot["derivative_contract"] = deepcopy(
                        derivative_contract
                    )
                if source_position_lot_id != position_lot.get("source_position_lot_id"):
                    position_lot["source_position_lot_id"] = None
                existing_acquisition_date = str(position_lot.get("acquisition_date") or acquisition_date)
                if acquisition_date and acquisition_date < existing_acquisition_date:
                    position_lot["acquisition_date"] = acquisition_date
                for linked_transaction_id in linked_transaction_ids or set():
                    _touch_position_lot(position_lot, linked_transaction_id)
                _touch_position_lot(position_lot, opened_by_transaction_id)
                _close_position_lot_if_needed(position_lot, close_date=opened_at)
                return position_lot

        position_lot = _new_position_lot(
            position_lot_id=next_position_lot_id(),
            portfolio_id=portfolio_id,
            account_id=target_account_id,
            position_reference_id=target_position_reference_id,
            instrument_id=instrument_id,
            instrument_ref=instrument_ref,
            derivative_contract_id=derivative_contract_id,
            derivative_contract=derivative_contract,
            currency=currency,
            cost_basis_method=resolved_cost_basis_method,
            opened_by_transaction_id=opened_by_transaction_id,
            opening_transaction_type=opening_transaction_type,
            opened_at=opened_at,
            acquisition_date=acquisition_date,
            entry_quantity=entry_quantity,
            entry_gross_amount=entry_gross_amount,
            entry_fee_amount=entry_fee_amount,
            entry_tax_amount=entry_tax_amount,
            entry_cost_basis=entry_cost_basis,
            source_position_lot_id=source_position_lot_id,
            linked_transaction_ids=linked_transaction_ids,
        )
        position_lots_by_key[
            (target_account_id, target_position_reference_id)
        ].append(position_lot)
        all_position_lots.append(position_lot)
        position_lot_by_id[str(position_lot.get("position_lot_id") or "")] = position_lot
        return position_lot

    def entitled_position_lot_snapshots(
        *,
        transaction_index: int,
        target_account_id: str,
        target_position_reference_id: str,
        entitlement_date: date,
        error_message: str,
    ) -> list[dict[str, object]]:
        cache_key = (
            transaction_index,
            target_account_id,
            target_position_reference_id,
            entitlement_date.isoformat(),
        )
        entitled_lots = entitlement_snapshot_cache.get(cache_key)
        if entitled_lots is None:
            snapshot_transactions = [
                transaction_item
                for transaction_item in sorted_transactions[:transaction_index]
                if transaction_precedes_entitlement_bod(transaction_item, entitlement_date)
            ]
            entitled_lots = [
                position_lot
                for position_lot in build_position_lots(
                    portfolio_id,
                    accounts,
                    snapshot_transactions,
                    account_id=target_account_id,
                    position_reference_id=target_position_reference_id,
                    as_of_date=entitlement_date,
                    corporate_actions=resolved_actions,
                    resolve_pricing=False,
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
        target_position_reference_id: str,
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
            target_position_reference_id=target_position_reference_id,
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

    def apply_share_split(event: dict[str, object]) -> None:
        target_instrument_id = str(event.get("instrument_id") or "")
        event_id = str(event.get("corporate_action_event_id") or "")
        effective_date = str(event.get("effective_date") or "")
        for (target_account_id, instrument_key), raw_lots in list(position_lots_by_key.items()):
            if instrument_key != target_instrument_id:
                continue
            active_lots = [
                lot
                for lot in raw_lots
                if (_safe_float(lot.get("remaining_quantity")) or 0.0) > 1e-9
            ]
            if not active_lots:
                continue
            old_quantities = [
                (_safe_float(position_lot.get("remaining_quantity")) or 0.0)
                for position_lot in active_lots
            ]
            target_quantities = _split_lot_quantity_allocations(old_quantities, event)
            successor_specs: list[dict[str, object]] = []
            for position_lot, old_quantity, target_quantity in zip(
                active_lots,
                old_quantities,
                target_quantities,
                strict=True,
            ):
                remaining_cost_basis = _safe_float(position_lot.get("remaining_cost_basis")) or 0.0
                if target_quantity <= 1e-9 and remaining_cost_basis > 1e-9:
                    raise ValueError(
                        "Corporate action would eliminate a cost-bearing lot; record cash-in-lieu before applying it."
                    )
                linked_ids = position_lot.get("_linked_transaction_ids")
                successor_specs.append(
                    {
                        "old_lot_id": str(position_lot.get("position_lot_id") or ""),
                        "old_quantity": old_quantity,
                        "target_quantity": target_quantity,
                        "remaining_cost_basis": remaining_cost_basis,
                        "instrument_ref": deepcopy(position_lot.get("instrument_ref") or {}),
                        "currency": str(position_lot.get("currency") or ""),
                        "acquisition_date": str(position_lot.get("acquisition_date") or effective_date),
                        "linked_transaction_ids": set(linked_ids) if isinstance(linked_ids, set) else set(),
                    }
                )
                position_lot["remaining_quantity"] = 0.0
                position_lot["remaining_cost_basis"] = 0.0
                position_lot["corporate_action_quantity_out"] = old_quantity
                position_lot["corporate_action_cost_basis_out"] = remaining_cost_basis
                position_lot["corporate_action_event_id"] = event_id
                _touch_position_lot(position_lot, event_id)
                _close_position_lot_if_needed(
                    position_lot,
                    close_date=effective_date,
                    close_reason="corporate_action",
                )

            for spec in successor_specs:
                target_quantity = float(spec["target_quantity"])
                if target_quantity <= 1e-9:
                    continue
                remaining_cost_basis = float(spec["remaining_cost_basis"])
                linked_transaction_ids = set(spec["linked_transaction_ids"])
                linked_transaction_ids.add(event_id)
                successor = append_position_lot(
                    target_account_id=target_account_id,
                    target_position_reference_id=target_instrument_id,
                    instrument_id=target_instrument_id,
                    instrument_ref=dict(spec["instrument_ref"]),
                    derivative_contract_id=None,
                    derivative_contract=None,
                    currency=str(spec["currency"]),
                    opened_by_transaction_id=event_id,
                    opening_transaction_type="corporate_action",
                    opened_at=effective_date,
                    acquisition_date=str(spec["acquisition_date"]),
                    entry_quantity=target_quantity,
                    entry_gross_amount=remaining_cost_basis,
                    entry_fee_amount=0.0,
                    entry_tax_amount=0.0,
                    entry_cost_basis=remaining_cost_basis,
                    source_position_lot_id=str(spec["old_lot_id"]),
                    linked_transaction_ids=linked_transaction_ids,
                )
                successor["corporate_action_event"] = deepcopy(event)
                successor["predecessor_quantity"] = float(spec["old_quantity"])
                successor["unit_cost_basis_before"] = (
                    remaining_cost_basis / float(spec["old_quantity"])
                    if float(spec["old_quantity"]) > 1e-9
                    else None
                )
                successor["unit_cost_basis_after"] = (
                    remaining_cost_basis / target_quantity
                    if target_quantity > 1e-9
                    else None
                )

    transaction_index_by_identity = {
        id(transaction): index for index, transaction in enumerate(sorted_transactions)
    }
    timeline: list[tuple[str, int, dict[str, object]]] = [
        ("corporate_action", -1, event) for event in resolved_actions
    ] + [
        ("transaction", transaction_index_by_identity[id(transaction)], transaction)
        for transaction in sorted_transactions
    ]
    timeline.sort(
        key=lambda item: (
            _corporate_action_sort_key(item[2])
            if item[0] == "corporate_action"
            else _transaction_timeline_sort_key(item[2])
        )
    )

    for item_kind, transaction_index, transaction in timeline:
        if item_kind == "corporate_action":
            apply_share_split(transaction)
            continue
        transaction_id = str(transaction.get("transaction_id") or "")
        transaction_type = str(transaction.get("transaction_type") or "")
        trade_date = str(transaction.get("trade_date") or "")
        position_effective_date = (
            transaction_position_effective_date(transaction)
            or _parse_iso_date(trade_date)
        )
        position_effective_date_iso = (
            position_effective_date.isoformat()
            if position_effective_date is not None
            else trade_date
        )
        entitlement_date = _parse_iso_date(transaction.get("entitlement_date")) or _parse_iso_date(trade_date)
        currency = str(transaction.get("currency") or "")
        account_key = str(transaction.get("account_id") or "")
        instrument_ref = (
            deepcopy(transaction.get("instrument_ref"))
            if isinstance(transaction.get("instrument_ref"), dict)
            else None
        )
        derivative_contract = (
            deepcopy(transaction.get("derivative_contract"))
            if isinstance(transaction.get("derivative_contract"), dict)
            else None
        )
        gross_amount = _safe_float(transaction.get("gross_amount")) or 0.0
        fees = _safe_float(transaction.get("fees")) or 0.0
        taxes = _safe_float(transaction.get("taxes")) or 0.0
        quantity = _safe_float(transaction.get("quantity")) or 0.0
        resolved_instrument_id = str(transaction.get("instrument_id") or "") or None
        resolved_derivative_contract_id = (
            str(transaction.get("derivative_contract_id") or "") or None
        )
        resolved_position_reference_id = _position_reference_id(transaction)
        cost_basis_method = _resolve_cost_basis_method(account_cost_methods, account_key)

        if (
            transaction_type in {"opening_balance", "buy"}
            and resolved_position_reference_id
            and quantity > 0
        ):
            entry_cost_basis = gross_amount
            if transaction_type == "buy":
                entry_cost_basis = (
                    gross_amount
                    if _transaction_is_event_valued(transaction)
                    else gross_amount + fees + taxes
                )
            acquisition_date = (
                str(transaction.get("acquisition_date") or "").strip()
                if transaction_type == "opening_balance"
                else position_effective_date_iso
            ) or position_effective_date_iso
            append_position_lot(
                target_account_id=account_key,
                target_position_reference_id=resolved_position_reference_id,
                instrument_id=resolved_instrument_id,
                instrument_ref=instrument_ref,
                derivative_contract_id=resolved_derivative_contract_id,
                derivative_contract=derivative_contract,
                currency=currency,
                opened_by_transaction_id=transaction_id,
                opening_transaction_type=transaction_type,
                opened_at=position_effective_date_iso,
                acquisition_date=acquisition_date,
                entry_quantity=quantity,
                entry_gross_amount=gross_amount,
                entry_fee_amount=fees if transaction_type == "buy" else 0.0,
                entry_tax_amount=taxes if transaction_type == "buy" else 0.0,
                entry_cost_basis=entry_cost_basis,
            )
            continue

        if (
            transaction_type == "dividend_reinvestment"
            and resolved_position_reference_id
            and quantity > 0
        ):
            if entitlement_date is None:
                raise ValueError("Dividend reinvestment requires entitlement_date.")
            allocate_snapshot_cash_flow(
                transaction_index=transaction_index,
                target_account_id=account_key,
                target_position_reference_id=resolved_position_reference_id,
                entitlement_date=entitlement_date,
                amount=gross_amount,
                field_name="income_cash_amount",
                weight_field="remaining_quantity",
                transaction_id=transaction_id,
                error_message=(
                    "Dividend reinvestment requires entitled position lots as of entitlement_date."
                ),
            )
            append_position_lot(
                target_account_id=account_key,
                target_position_reference_id=resolved_position_reference_id,
                instrument_id=resolved_instrument_id,
                instrument_ref=instrument_ref,
                derivative_contract_id=resolved_derivative_contract_id,
                derivative_contract=derivative_contract,
                currency=currency,
                opened_by_transaction_id=transaction_id,
                opening_transaction_type=transaction_type,
                opened_at=position_effective_date_iso,
                acquisition_date=position_effective_date_iso,
                entry_quantity=quantity,
                entry_gross_amount=gross_amount,
                entry_fee_amount=0.0,
                entry_tax_amount=0.0,
                entry_cost_basis=gross_amount,
            )
            continue

        if (
            transaction_type in {"sell", "maturity_redemption"}
            and resolved_position_reference_id
            and quantity > 0
        ):
            disposal_slices = _consume_position_lots(
                position_lots_by_key,
                account_id=account_key,
                position_reference_id=resolved_position_reference_id,
                quantity=quantity,
                cost_basis_method=cost_basis_method,
                error_message="Transaction quantity exceeds account position as of position_effective_date.",
            )
            net_proceeds = gross_amount - fees - taxes
            quantity_weights = [(_safe_float(slice_item.get("quantity")) or 0.0) for slice_item in disposal_slices]
            gross_proceeds_allocations = _proportional_allocations(
                gross_amount,
                quantity_weights,
            )
            proceeds_allocations = _proportional_allocations(net_proceeds, quantity_weights)
            for index, slice_item in enumerate(disposal_slices):
                position_lot = slice_item["position_lot"]
                matched_quantity = _safe_float(slice_item.get("quantity")) or 0.0
                matched_cost_basis = _safe_float(slice_item.get("cost_basis")) or 0.0
                gross_proceeds = gross_proceeds_allocations[index]
                proceeds = proceeds_allocations[index]
                realized_pnl = proceeds - matched_cost_basis
                position_lot["realized_quantity"] = (
                    (_safe_float(position_lot.get("realized_quantity")) or 0.0) + matched_quantity
                )
                position_lot["realized_cost_basis"] = (
                    (_safe_float(position_lot.get("realized_cost_basis")) or 0.0) + matched_cost_basis
                )
                position_lot["realized_gross_proceeds"] = (
                    (_safe_float(position_lot.get("realized_gross_proceeds")) or 0.0)
                    + gross_proceeds
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
                            "position_effective_date": position_effective_date_iso,
                            "quantity": matched_quantity,
                            "gross_proceeds": gross_proceeds,
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
                    close_date=position_effective_date_iso,
                    close_reason="disposed",
                )
            continue

        if (
            transaction_type in {"dividend", "coupon"}
            and resolved_position_reference_id
        ):
            if entitlement_date is None:
                raise ValueError("Instrument income requires entitlement_date.")
            allocate_snapshot_cash_flow(
                transaction_index=transaction_index,
                target_account_id=account_key,
                target_position_reference_id=resolved_position_reference_id,
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
                    target_position_reference_id=resolved_position_reference_id,
                    entitlement_date=entitlement_date,
                    amount=fees + taxes,
                    field_name="expense_cash_amount",
                    weight_field="remaining_quantity",
                    transaction_id=transaction_id,
                    error_message="Instrument income requires entitled position lots as of entitlement_date.",
                )
            continue

        if transaction_type == "return_of_capital" and resolved_position_reference_id:
            _apply_position_lot_return_of_capital(
                position_lots_by_key,
                account_id=account_key,
                position_reference_id=resolved_position_reference_id,
                amount=gross_amount,
                transaction_id=transaction_id,
                error_message="Return of capital requires open position lots as of trade_date.",
            )
            if fees > 0 or taxes > 0:
                _allocate_lot_cash_flow_by_quantity(
                    position_lots_by_key,
                    account_id=account_key,
                    position_reference_id=resolved_position_reference_id,
                    amount=fees + taxes,
                    field_name="expense_cash_amount",
                    transaction_id=transaction_id,
                    error_message="Return of capital requires open position lots as of trade_date.",
                )
            continue

        if transaction_type in {"fee", "tax"} and resolved_position_reference_id:
            if entitlement_date is None:
                raise ValueError("Instrument-linked expense requires entitlement_date.")
            derivative_contract = _derivative_contract(transaction)
            if (
                isinstance(derivative_contract, dict)
                and str(derivative_contract.get("contract_type") or "").lower()
                == "option"
            ):
                # A standalone option fee/tax is a contract-level cash expense.
                # It is not part of a long lot's basis and does not rewrite a
                # writer obligation; cash and performance already recognize it.
                continue
            allocate_snapshot_cash_flow(
                transaction_index=transaction_index,
                target_account_id=account_key,
                target_position_reference_id=resolved_position_reference_id,
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
            and resolved_position_reference_id
            and quantity > 0
        ):
            disposal_slices = _consume_position_lots(
                position_lots_by_key,
                account_id=account_key,
                position_reference_id=resolved_position_reference_id,
                quantity=quantity,
                cost_basis_method=cost_basis_method,
                error_message="Position transfer requires source position lots as of trade_date.",
            )
            if not disposal_slices:
                raise ValueError("Position transfer requires source position lots as of trade_date.")
            transferred_slices: list[dict[str, object]] = []
            for slice_item in disposal_slices:
                position_lot = slice_item["position_lot"]
                matched_quantity = _safe_float(slice_item.get("quantity")) or 0.0
                matched_cost_basis = _safe_float(slice_item.get("cost_basis")) or 0.0
                matched_entry_gross_amount = _safe_float(slice_item.get("entry_gross_amount")) or 0.0
                matched_entry_fee_amount = _safe_float(slice_item.get("entry_fee_amount")) or 0.0
                matched_entry_tax_amount = _safe_float(slice_item.get("entry_tax_amount")) or 0.0
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
                        "position_reference_id": resolved_position_reference_id,
                        "instrument_id": resolved_instrument_id,
                        "instrument_ref": deepcopy(position_lot.get("instrument_ref")),
                        "derivative_contract_id": resolved_derivative_contract_id,
                        "derivative_contract": deepcopy(
                            position_lot.get("derivative_contract")
                        ),
                        "currency": str(position_lot.get("currency") or currency),
                        "opened_by_transaction_id": str(position_lot.get("opened_by_transaction_id") or transaction_id),
                        "opening_transaction_type": str(position_lot.get("opening_transaction_type") or "transfer_in"),
                        "opened_at": str(position_lot.get("opened_at") or trade_date),
                        "acquisition_date": str(
                            position_lot.get("acquisition_date") or position_lot.get("opened_at") or trade_date
                        ),
                        "entry_quantity": matched_quantity,
                        "entry_gross_amount": matched_entry_gross_amount,
                        "entry_fee_amount": matched_entry_fee_amount,
                        "entry_tax_amount": matched_entry_tax_amount,
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
            and resolved_position_reference_id
            and quantity > 0
        ):
            transfer_group_id = str(transaction.get("transfer_group_id") or "")
            incoming_slices = list(transfer_lot_slices_by_group.get(transfer_group_id, []))
            if not incoming_slices:
                raise ValueError("Position transfer requires linked source position lots.")
            for incoming_slice in incoming_slices:
                entry_quantity = _safe_float(incoming_slice.get("entry_quantity")) or 0.0
                entry_gross_amount = _safe_float(incoming_slice.get("entry_gross_amount")) or 0.0
                entry_fee_amount = _safe_float(incoming_slice.get("entry_fee_amount")) or 0.0
                entry_tax_amount = _safe_float(incoming_slice.get("entry_tax_amount")) or 0.0
                entry_cost_basis = _safe_float(incoming_slice.get("entry_cost_basis"))
                if entry_quantity <= 0 or entry_cost_basis is None or entry_cost_basis < 0:
                    raise ValueError("Position transfer requires valid linked source lot slices.")
                linked_transaction_ids = {
                    str(incoming_slice.get("opened_by_transaction_id") or transaction_id),
                    transaction_id,
                }
                append_position_lot(
                    target_account_id=account_key,
                    target_position_reference_id=resolved_position_reference_id,
                    instrument_id=resolved_instrument_id,
                    instrument_ref=(
                        incoming_slice.get("instrument_ref")
                        if isinstance(incoming_slice.get("instrument_ref"), dict)
                        else instrument_ref
                    ),
                    derivative_contract_id=resolved_derivative_contract_id,
                    derivative_contract=(
                        incoming_slice.get("derivative_contract")
                        if isinstance(
                            incoming_slice.get("derivative_contract"),
                            dict,
                        )
                        else derivative_contract
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
                    entry_gross_amount=entry_gross_amount,
                    entry_fee_amount=entry_fee_amount,
                    entry_tax_amount=entry_tax_amount,
                    entry_cost_basis=entry_cost_basis,
                    source_position_lot_id=(
                        str(incoming_slice.get("source_position_lot_id") or "") or None
                    ),
                    linked_transaction_ids=linked_transaction_ids,
                )
            continue

    filtered_position_lots = [
        position_lot
        for position_lot in all_position_lots
        if (not account_id or position_lot.get("account_id") == account_id)
        and (
            not position_reference_id
            or position_lot.get("position_reference_id")
            == position_reference_id
        )
        and (not status or position_lot.get("status") == status)
    ]
    returned_instrument_ids = {
        str(position_lot.get("instrument_id") or "")
        for position_lot in filtered_position_lots
        if str(position_lot.get("instrument_id") or "")
    }
    resolved_pricing_map = pricing_map if pricing_map is not None else {}
    missing_pricing_ids = returned_instrument_ids - resolved_pricing_map.keys()
    if resolve_pricing and missing_pricing_ids:
        resolved_pricing_map.update(
            _resolve_pricing_quote_map(
                missing_pricing_ids,
                as_of_date=as_of_date,
                instrument_detail_cache=instrument_detail_cache,
            )
        )
    resolved_as_of_date = as_of_date or date.today()
    rendered_position_lots: list[dict[str, object]] = []
    for raw_position_lot in filtered_position_lots:
        remaining_quantity = _safe_float(raw_position_lot.get("remaining_quantity")) or 0.0
        remaining_cost_basis = _safe_float(raw_position_lot.get("remaining_cost_basis")) or 0.0
        realized_quantity = _safe_float(raw_position_lot.get("realized_quantity")) or 0.0
        entry_quantity = _safe_float(raw_position_lot.get("entry_quantity")) or 0.0
        entry_gross_amount = _safe_float(raw_position_lot.get("entry_gross_amount")) or 0.0
        entry_fee_amount = _safe_float(raw_position_lot.get("entry_fee_amount")) or 0.0
        entry_tax_amount = _safe_float(raw_position_lot.get("entry_tax_amount")) or 0.0
        entry_cost_basis = _safe_float(raw_position_lot.get("entry_cost_basis")) or 0.0
        realized_proceeds = _safe_float(raw_position_lot.get("realized_proceeds")) or 0.0
        realized_gross_proceeds = _safe_float(
            raw_position_lot.get("realized_gross_proceeds")
        )
        if realized_gross_proceeds is None:
            raise ValueError("Position lot is missing canonical realized gross proceeds.")
        instrument_quote = resolved_pricing_map.get(str(raw_position_lot.get("instrument_id") or ""))
        instrument_price = _pricing_value(instrument_quote)
        instrument_ref = (
            raw_position_lot.get("instrument_ref")
            if isinstance(raw_position_lot.get("instrument_ref"), dict)
            else None
        )
        derivative_contract = (
            raw_position_lot.get("derivative_contract")
            if isinstance(raw_position_lot.get("derivative_contract"), dict)
            else None
        )
        acquisition_date_value = _parse_iso_date(raw_position_lot.get("acquisition_date")) or _parse_iso_date(
            raw_position_lot.get("opened_at")
        )
        closed_at_value = _parse_iso_date(raw_position_lot.get("closed_at")) or resolved_as_of_date
        holding_period_days = None
        if acquisition_date_value is not None and closed_at_value is not None:
            holding_period_days = max((closed_at_value - acquisition_date_value).days, 0)
        instrument_price, current_market_value, _ = resolve_position_valuation(
            quantity=remaining_quantity,
            cost_basis=remaining_cost_basis,
            instrument_ref=instrument_ref,
            derivative_contract=derivative_contract,
            quoted_price=instrument_price,
            quoted_price_scale=_pricing_scale(instrument_quote),
        )

        rendered_position_lots.append(
            {
                "position_lot_id": raw_position_lot["position_lot_id"],
                "portfolio_id": portfolio_id,
                "account_id": raw_position_lot["account_id"],
                "position_reference_id": raw_position_lot[
                    "position_reference_id"
                ],
                "instrument_id": raw_position_lot["instrument_id"],
                "instrument_ref": deepcopy(instrument_ref),
                "derivative_contract_id": raw_position_lot.get(
                    "derivative_contract_id"
                ),
                "derivative_contract": deepcopy(derivative_contract),
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
                "corporate_action_event_id": raw_position_lot.get("corporate_action_event_id"),
                "corporate_action_event": deepcopy(raw_position_lot.get("corporate_action_event")),
                "corporate_action_quantity_out": _safe_float(
                    raw_position_lot.get("corporate_action_quantity_out")
                ),
                "corporate_action_cost_basis_out": _safe_float(
                    raw_position_lot.get("corporate_action_cost_basis_out")
                ),
                "predecessor_quantity": _safe_float(raw_position_lot.get("predecessor_quantity")),
                "unit_cost_basis_before": _safe_float(raw_position_lot.get("unit_cost_basis_before")),
                "unit_cost_basis_after": _safe_float(raw_position_lot.get("unit_cost_basis_after")),
                "entry_quantity": entry_quantity,
                "remaining_quantity": remaining_quantity,
                "realized_quantity": realized_quantity,
                "transferred_quantity": _safe_float(raw_position_lot.get("transferred_quantity")) or 0.0,
                "entry_gross_amount": entry_gross_amount,
                "entry_fee_amount": entry_fee_amount,
                "entry_tax_amount": entry_tax_amount,
                "entry_cost_basis": entry_cost_basis,
                "entry_cost_per_unit": (
                    entry_cost_basis / entry_quantity if entry_quantity > 1e-9 else None
                ),
                "remaining_cost_basis": remaining_cost_basis,
                "realized_cost_basis": _safe_float(raw_position_lot.get("realized_cost_basis")) or 0.0,
                "transferred_cost_basis": _safe_float(raw_position_lot.get("transferred_cost_basis")) or 0.0,
                "realized_gross_proceeds": realized_gross_proceeds,
                "realized_proceeds": realized_proceeds,
                "realized_pnl": _safe_float(raw_position_lot.get("realized_pnl")) or 0.0,
                "income_cash_amount": _safe_float(raw_position_lot.get("income_cash_amount")) or 0.0,
                "expense_cash_amount": _safe_float(raw_position_lot.get("expense_cash_amount")) or 0.0,
                "return_of_capital_amount": _safe_float(raw_position_lot.get("return_of_capital_amount")) or 0.0,
                "entry_price": _display_price_from_gross(
                    gross_amount=entry_gross_amount,
                    quantity=entry_quantity,
                    instrument_ref=instrument_ref,
                    derivative_contract=derivative_contract,
                ),
                "average_exit_price": _display_price_from_gross(
                    gross_amount=realized_gross_proceeds,
                    quantity=realized_quantity,
                    instrument_ref=instrument_ref,
                    derivative_contract=derivative_contract,
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
    pricing_map: dict[str, object] = {}
    position_lots = build_position_lots(
        portfolio_id,
        accounts,
        transactions,
        status="open",
        as_of_date=as_of_date,
        pricing_map=pricing_map,
    )
    positions_by_reference: dict[str, dict[str, object]] = {}

    for position_lot in position_lots:
        position_reference_id = str(
            position_lot.get("position_reference_id") or ""
        )
        bucket = positions_by_reference.setdefault(
            position_reference_id,
            {
                "position_id": position_reference_id,
                "portfolio_id": portfolio_id,
                "position_reference_id": position_reference_id,
                "instrument_id": position_lot.get("instrument_id"),
                "instrument_ref": deepcopy(position_lot.get("instrument_ref")),
                "derivative_contract_id": position_lot.get(
                    "derivative_contract_id"
                ),
                "derivative_contract": deepcopy(
                    position_lot.get("derivative_contract")
                ),
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
    for bucket in positions_by_reference.values():
        quantity = _safe_float(bucket.get("quantity")) or 0.0
        if abs(quantity) <= 1e-9:
            continue
        pricing_quote = pricing_map.get(str(bucket.get("instrument_id") or ""))
        quoted_price = _pricing_value(pricing_quote)
        last_price, market_value, _ = resolve_position_valuation(
            quantity=quantity,
            cost_basis=_safe_float(bucket.get("cost_basis")),
            instrument_ref=(
                bucket.get("instrument_ref")
                if isinstance(bucket.get("instrument_ref"), dict)
                else None
            ),
            derivative_contract=(
                bucket.get("derivative_contract")
                if isinstance(bucket.get("derivative_contract"), dict)
                else None
            ),
            quoted_price=quoted_price,
            quoted_price_scale=_pricing_scale(pricing_quote),
        )
        account_ids = sorted(account_id for account_id in bucket["account_ids"] if account_id)
        rendered_positions.append(
            {
                "position_id": bucket["position_id"],
                "portfolio_id": portfolio_id,
                "position_reference_id": bucket["position_reference_id"],
                "instrument_id": bucket["instrument_id"],
                "instrument_ref": deepcopy(bucket.get("instrument_ref")),
                "derivative_contract_id": bucket.get(
                    "derivative_contract_id"
                ),
                "derivative_contract": deepcopy(
                    bucket.get("derivative_contract")
                ),
                "quantity": quantity,
                "cost_basis": _safe_float(bucket.get("cost_basis")),
                "last_price": last_price,
                "market_value": market_value,
                "currency": bucket["currency"],
                "account_ids": account_ids,
                "account_count": len(account_ids),
                "open_position_lot_count": int(bucket.get("open_position_lot_count") or 0),
            }
        )

    rendered_positions.sort(
        key=lambda item: (
            -((_safe_float(item.get("market_value")) or 0.0)),
            str(item.get("position_reference_id") or ""),
        )
    )
    return rendered_positions


def summarize_positions(positions: list[dict[str, object]]) -> dict[str, int]:
    return {
        "position_count": len(positions),
        "priced_position_count": sum(
            1
            for position in positions
            if position.get("instrument_id")
            and position.get("last_price") is not None
        ),
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
    instrument_detail_cache: dict[str, dict[str, object] | None] | None = None,
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
            if _transaction_has_ledger_activity_as_of(transaction, as_of_date)
        ]
    corporate_actions = _resolved_corporate_actions(
        boundary_transactions,
        corporate_actions=None,
        as_of_date=as_of_date,
    )
    postings = derive_ledger_postings(
        portfolio_id,
        boundary_transactions,
        account_cost_methods=account_cost_methods,
        account_currency_map=account_currency_map,
        corporate_actions=corporate_actions,
        as_of_date=as_of_date,
    )
    fx_rate_map = resolve_fx_rate_map()
    convert_amount_on_fn = None
    direct_fx_instruments: dict[tuple[str, str], str] = {}
    resolved_instrument_detail_cache = (
        instrument_detail_cache if instrument_detail_cache is not None else {}
    )
    if as_of_date is not None:
        convert_amount_on_fn = valuation_fx.convert_amount_on
        direct_fx_instruments = valuation_fx.fx_direct_instrument_map(
            get_platform_fx_rates()
        )

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
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=resolved_instrument_detail_cache,
            instrument_detail_loader=get_registry_instrument_detail,
        )
        return converted_amount

    linked_transaction_ids: dict[str, set[str]] = defaultdict(set)
    linked_posting_count: dict[str, int] = defaultdict(int)
    cash_balance: dict[str, float] = defaultdict(float)
    pending_settlement: dict[str, float] = defaultdict(float)

    for posting in postings:
        account_id = str(posting.get("account_id") or "")
        linked_posting_count[account_id] += 1
        linked_transaction_ids[account_id].add(str(posting.get("transaction_id") or ""))

        pending_amount_delta = ledger_posting_pending_amount_as_of(
            posting,
            as_of_date,
        )
        if pending_amount_delta is not None:
            pending_settlement[account_id] += pending_amount_delta
            continue

        cash_delta = _safe_float(posting.get("cash_amount_delta"))
        if cash_delta is None:
            continue
        if as_of_iso is None or ledger_posting_effective_date_iso(posting) <= as_of_iso:
            cash_balance[account_id] += cash_delta
        else:
            pending_settlement[account_id] += cash_delta

    # Written options are liabilities, not negative long positions.  Rebuild
    # the obligation read model at the same boundary as cash and lots so the
    # account NAV cannot recognize the received premium twice.
    obligation_as_of = as_of_date or date.today()
    option_obligation_rows = build_option_obligations(
        boundary_transactions,
        as_of_date=obligation_as_of,
    )
    open_option_obligation_rows = [
        row
        for row in option_obligation_rows
        if str(row.get("status") or "") == "open"
        and (_safe_float(row.get("remaining_quantity")) or 0.0) > 1e-9
    ]
    liability_by_account: dict[str, float] = defaultdict(float)
    liability_base_by_account: dict[str, float] = defaultdict(float)
    liability_fx_complete_by_account: dict[str, bool] = defaultdict(lambda: True)
    for obligation in open_option_obligation_rows:
        obligation_account_id = str(obligation.get("account_id") or "")
        obligation_currency = str(
            obligation.get("contract_currency")
            or account_currency_map.get(obligation_account_id)
            or ""
        )
        liability = _safe_float(obligation.get("carrying_liability")) or 0.0
        liability_by_account[obligation_account_id] += liability
        converted_liability = convert_to_base(
            liability,
            from_currency=obligation_currency,
        )
        if converted_liability is None and abs(liability) > 1e-9:
            liability_fx_complete_by_account[obligation_account_id] = False
        elif converted_liability is not None:
            liability_base_by_account[obligation_account_id] += converted_liability

    pricing_map: dict[str, object] = {}
    position_lots = build_position_lots(
        portfolio_id,
        accounts,
        boundary_transactions,
        status="open",
        as_of_date=as_of_date,
        corporate_actions=corporate_actions,
        pricing_map=pricing_map,
        instrument_detail_cache=resolved_instrument_detail_cache,
    )
    positions_by_account_reference: dict[tuple[str, str], dict[str, object]] = {}
    for position_lot in position_lots:
        account_id = str(position_lot.get("account_id") or "")
        position_reference_id = str(
            position_lot.get("position_reference_id") or ""
        )
        key = (account_id, position_reference_id)
        bucket = positions_by_account_reference.setdefault(
            key,
            {
                "position_id": f"{account_id}:{position_reference_id}",
                "account_id": account_id,
                "position_reference_id": position_reference_id,
                "instrument_id": position_lot.get("instrument_id"),
                "instrument_ref": deepcopy(position_lot.get("instrument_ref")),
                "derivative_contract_id": position_lot.get(
                    "derivative_contract_id"
                ),
                "derivative_contract": deepcopy(
                    position_lot.get("derivative_contract")
                ),
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

    positions: list[dict[str, object]] = []
    position_count_by_account: dict[str, int] = defaultdict(int)
    market_value_by_account: dict[str, float] = defaultdict(float)
    valuation_complete_by_account: dict[str, bool] = defaultdict(lambda: True)
    valuation_missing_components_by_account: dict[str, set[str]] = defaultdict(set)
    for bucket in positions_by_account_reference.values():
        quantity = float(bucket["quantity"])
        if abs(quantity) < 1e-9:
            continue
        instrument_id = str(bucket.get("instrument_id") or "")
        pricing_quote = pricing_map.get(instrument_id)
        quoted_price = _pricing_value(pricing_quote)
        last_price, market_value, event_valued = resolve_position_valuation(
            quantity=quantity,
            cost_basis=_safe_float(bucket.get("cost_basis")),
            instrument_ref=(
                bucket["instrument_ref"]
                if isinstance(bucket.get("instrument_ref"), dict)
                else None
            ),
            derivative_contract=(
                bucket["derivative_contract"]
                if isinstance(bucket.get("derivative_contract"), dict)
                else None
            ),
            quoted_price=quoted_price,
            quoted_price_scale=_pricing_scale(pricing_quote),
        )
        account_id = str(bucket["account_id"])
        position_count_by_account[account_id] += 1
        if market_value is None:
            valuation_complete_by_account[account_id] = False
            valuation_missing_components_by_account[account_id].add("position_price")
        else:
            converted_market_value = convert_to_base(
                market_value,
                from_currency=str(bucket.get("currency") or ""),
            )
            if converted_market_value is None and abs(market_value) <= 1e-9:
                converted_market_value = 0.0
            elif converted_market_value is None:
                valuation_complete_by_account[account_id] = False
                valuation_missing_components_by_account[account_id].add("position_fx")
            if converted_market_value is not None:
                market_value_by_account[account_id] += converted_market_value

        positions.append(
            {
                "position_id": bucket["position_id"],
                "account_id": account_id,
                "position_reference_id": bucket["position_reference_id"],
                "instrument_id": bucket.get("instrument_id"),
                "instrument_ref": bucket["instrument_ref"],
                "derivative_contract_id": bucket.get(
                    "derivative_contract_id"
                ),
                "derivative_contract": bucket.get("derivative_contract"),
                "quantity": quantity,
                "cost_basis": float(bucket["cost_basis"]),
                "last_price": None if event_valued else last_price,
                "market_value": market_value,
                "carrying_value": market_value if event_valued else None,
                "fair_value": None if event_valued else market_value,
                "fair_value_coverage_status": (
                    "unavailable"
                    if event_valued or market_value is None
                    else "complete"
                ),
                "valuation_basis": (
                    "carried_cost" if event_valued else "market_quote"
                ),
                "coverage_status": (
                    "event-cost"
                    if event_valued and market_value is not None
                    else "unavailable"
                    if event_valued or market_value is None
                    else "price-nav-fx"
                ),
                "currency": bucket["currency"],
                "cost_basis_method": bucket["cost_basis_method"] or None,
                "open_position_lot_count": int(bucket["open_position_lot_count"] or 0),
            }
        )

    positions.sort(
        key=lambda item: (
            str(item.get("account_id") or ""),
            str(item.get("position_reference_id") or ""),
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
        if derived_cash_balance_base is None and abs(derived_cash_balance) <= 1e-9:
            derived_cash_balance_base = 0.0
        elif derived_cash_balance_base is None:
            valuation_missing_components_by_account[account_id].add("cash_fx")
        pending_settlement_amount = pending_settlement[account_id]
        pending_settlement_base = convert_to_base(
            pending_settlement_amount,
            from_currency=account_currency,
        )
        if pending_settlement_base is None and abs(pending_settlement_amount) <= 1e-9:
            pending_settlement_base = 0.0
        elif pending_settlement_base is None:
            valuation_missing_components_by_account[account_id].add("pending_settlement_fx")
        position_market_value = (
            market_value_by_account[account_id]
            if position_count_by_account[account_id] and valuation_complete_by_account[account_id]
            else None
        )
        account_position_market_value = (
            position_market_value
            if position_count_by_account[account_id]
            else 0.0
        )
        derivative_liability_base = (
            liability_base_by_account[account_id]
            if liability_fx_complete_by_account[account_id]
            else None
        )
        account_value_base = (
            derived_cash_balance_base
            + pending_settlement_base
            + account_position_market_value
            - derivative_liability_base
            if (
                derived_cash_balance_base is not None
                and pending_settlement_base is not None
                and account_position_market_value is not None
                and derivative_liability_base is not None
            )
            else None
        )
        if not liability_fx_complete_by_account[account_id]:
            valuation_missing_components_by_account[account_id].add(
                "derivative_liability_fx"
            )
        missing_components = sorted(valuation_missing_components_by_account[account_id])
        if account_value_base is not None:
            valuation_coverage_state = "complete"
        elif any(
            value is not None
            for value in (
                derived_cash_balance_base,
                pending_settlement_base,
                position_market_value,
            )
        ):
            valuation_coverage_state = "partial"
        else:
            valuation_coverage_state = "unavailable"
        account_rows.append(
            {
                "account": deepcopy(account),
                "default_settlement_cash_account_name": settlement_name or None,
                "linked_transaction_count": len(linked_transaction_ids[account_id]),
                "linked_posting_count": linked_posting_count[account_id],
                "derived_cash_balance": derived_cash_balance,
                "derived_cash_balance_base": derived_cash_balance_base,
                "pending_settlement": pending_settlement_amount,
                "pending_settlement_base": pending_settlement_base,
                "derivative_liability": liability_by_account[account_id],
                "derivative_liability_base": derivative_liability_base,
                "open_option_obligation_count": sum(
                    1
                    for row in open_option_obligation_rows
                    if str(row.get("account_id") or "") == account_id
                ),
                "account_value_base": account_value_base,
                "valuation_coverage_state": valuation_coverage_state,
                "valuation_missing_components": missing_components,
                "position_line_count": position_count_by_account[account_id],
                "position_market_value": position_market_value,
                "position_market_value_currency": base_currency if position_count_by_account[account_id] else None,
            }
        )

    visible_postings = postings
    visible_positions = positions
    if resolved_selected_account_id:
        visible_postings = [posting for posting in postings if posting["account_id"] == resolved_selected_account_id]
        visible_positions = [position for position in positions if position["account_id"] == resolved_selected_account_id]

    valued_account_count = sum(
        1 for account_row in account_rows if account_row.get("valuation_coverage_state") == "complete"
    )
    unavailable_account_count = sum(
        1 for account_row in account_rows if account_row.get("valuation_coverage_state") == "unavailable"
    )
    if valued_account_count == len(account_rows):
        valuation_coverage_state = "complete"
    elif account_rows and unavailable_account_count == len(account_rows):
        valuation_coverage_state = "unavailable"
    else:
        valuation_coverage_state = "partial"

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
            "valuation_coverage_state": valuation_coverage_state,
            "valued_account_count": valued_account_count,
            "unvalued_account_count": len(account_rows) - valued_account_count,
            "open_option_obligation_count": len(open_option_obligation_rows),
            "derivative_liability_base": (
                sum(liability_base_by_account.values())
                if all(liability_fx_complete_by_account.values())
                else None
            ),
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
        "option_obligations": (
            open_option_obligation_rows
            if not resolved_selected_account_id
            else [
                row
                for row in open_option_obligation_rows
                if str(row.get("account_id") or "") == resolved_selected_account_id
            ]
        ),
    }


def list_ledger_postings(
    portfolio_id: str,
    transactions: list[dict[str, object]],
    *,
    account_cost_methods: dict[str, str] | None = None,
    account_currency_map: dict[str, str] | None = None,
    account_id: str | None = None,
    transaction_id: str | None = None,
    instrument_id: str | None = None,
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
        if instrument_id and posting.get("instrument_id") != instrument_id:
            continue

        pending_amount_delta = _safe_float(posting.get("pending_amount_delta"))
        effective_date_value = ledger_posting_effective_date_iso(posting)
        if pending_amount_delta is not None:
            recognition_start_value = str(
                posting.get("recognition_start_date")
                or posting.get("settlement_date")
                or ""
            )
            if start_date and effective_date_value < start_date.isoformat():
                continue
            if end_date and recognition_start_value > end_date.isoformat():
                continue
        else:
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
        "pending_posting_count": sum(
            1
            for posting in postings
            if posting.get("pending_amount_delta") is not None
        ),
        "position_posting_count": sum(
            1
            for posting in postings
            if posting.get("quantity_delta") is not None or posting.get("cost_basis_delta") is not None
        ),
        "liability_posting_count": sum(
            1
            for posting in postings
            if posting.get("liability_amount_delta") is not None
        ),
        "option_realized_pnl_posting_count": sum(
            1
            for posting in postings
            if posting.get("realized_pnl_delta") is not None
        ),
    }
