from __future__ import annotations
from portfolio_app.services.lot_selection import selected_quantities

from portfolio_app.services.asset_deliveries import expand_asset_deliveries, pending_asset_delivery_quantities
from portfolio_app.services.short_positions import SHORT_TRANSACTION_TYPES, partition_security_sides, reflect_short_lot

from collections import defaultdict
from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from functools import partial
from typing import Callable

from investment_studio_instrument_core import VALUATION_PROHIBITED_TOTAL_RETURN_BASES

from portfolio_app.services.instrument_registry import (
    InstrumentRegistryError,
    get_shared_fx_rates,
    get_registry_instrument_details,
    get_registry_instrument_detail,
    list_registry_corporate_actions,
    list_registry_instruments,
)
from portfolio_app.services import valuation_fx
from portfolio_app.services.valuation_quotes import load_valuation_quotes
from portfolio_app.services.holdings_market_profile import resolve_position_valuation
from portfolio_app.services.market_data import is_usable_market_data_point
from portfolio_app.services.market_data import (
    QuoteSeriesLookup, bind_initial_purchase_valuations, initial_purchase_valuation_point,
    quote_policy_bases, resolve_quote_point,
)
from portfolio_app.services.transaction_dates import (
    transaction_ledger_activity_date,
    transaction_performance_effective_date,
    transaction_position_effective_date,
    transaction_precedes_asset_cash_flow,
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
            "option_long_exercise",
        }:
            raise ValueError(
                "Option maturity redemption requires an explicit long-option outcome."
            )
        if transaction_type == "lifecycle_event" and lifecycle_event_type not in {
            "option_writer_expiry",
            "option_writer_cash_settlement",
            "option_writer_assignment",
        }:
            raise ValueError(
                "Option lifecycle event requires an explicit writer-option outcome."
            )
        if lifecycle_event_type in {
            "option_long_expiry",
            "option_long_cash_settlement",
            "option_long_exercise",
        } and transaction_type != "maturity_redemption":
            raise ValueError(
                "Long option outcome requires maturity_redemption transaction type."
            )
        if lifecycle_event_type in {
            "option_writer_expiry",
            "option_writer_cash_settlement",
            "option_writer_assignment",
        } and transaction_type != "lifecycle_event":
            raise ValueError(
                "Writer option outcome requires lifecycle_event transaction type."
            )
        option_lifecycle_event = lifecycle_event_type in {
            "option_long_expiry",
            "option_long_cash_settlement",
            "option_long_exercise",
            "option_writer_expiry",
            "option_writer_cash_settlement",
            "option_writer_assignment",
        }
        if option_action is None and not option_lifecycle_event and transaction_type != "option_opening_balance":
            return

        identity = option_contract_identity(transaction)
        expiry_date = _parse_iso_date(identity.get("expiry_date"))
        if expiry_date is None or event_date is None:
            raise ValueError("Option event and contract expiry dates are required.")
        physical_event = lifecycle_event_type in {"option_long_exercise", "option_writer_assignment"}
        cash_event = lifecycle_event_type in {"option_long_cash_settlement", "option_writer_cash_settlement"}
        settlement_type = identity.get("settlement_type")
        if physical_event and settlement_type == "cash":
            raise ValueError("Cash-settled option contracts cannot have physical delivery.")
        if cash_event and settlement_type == "physical":
            raise ValueError("Physically settled option contracts cannot be recorded as cash settlement; record the actual delivery and any closing trades.")
        if physical_event or cash_event:
            if identity.get("exercise_style") == "european" and event_date != expiry_date:
                raise ValueError("European option exercise or settlement must use the expiry date.")
            if identity.get("exercise_style") == "bermudan" and event_date.isoformat() not in {
                str(value) for value in identity.get("exercise_dates") or []
            }:
                raise ValueError("Bermudan option outcome must use a contractual exercise date.")
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
        if lifecycle_event_type in {
            "option_long_exercise",
            "option_writer_assignment",
        } and event_date > expiry_date:
            raise ValueError("Option physical settlement must not follow contract expiry.")

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
        elif lifecycle_event_type in {
            "option_long_exercise",
            "option_writer_assignment",
        }:
            if gross_amount > 1e-9 or charges > 1e-9 or settlement_cash_account_id:
                raise ValueError(
                    "Option physical outcome must not carry cash amounts; the linked stock trade carries settlement."
                )
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
    delivered = bool(transaction.get("asset_deliveries") or transaction.get("delivered_value") is not None)
    if transaction_type == "maturity_redemption" and isinstance(terms, dict):
        if terms.get("settlement_type") == "physical" and not delivered:
            raise ValueError("Physical FCN settlement requires the actual asset deliveries.")
        if delivered and terms.get("settlement_type") == "cash":
            raise ValueError("Cash-only FCN contracts cannot deliver securities.")
        if delivered:
            underlying_ids = {str(item.get("instrument_id") or "") for item in terms.get("underlyings") or [] if item.get("deliverable") is not False}
            if any(str(item.get("instrument_id") or "") not in underlying_ids for item in transaction.get("asset_deliveries") or []):
                raise ValueError("Delivered securities must belong to the FCN contractual underlyings.")
    if lifecycle_event_type in {"fcn_knock_in", "fcn_knock_out"} and not issue_date <= event_date <= maturity_date:
        raise ValueError("FCN transaction date must fall between contract issue and maturity.")
    if lifecycle_event_type == "fcn_knock_out" and isinstance(terms, dict):
        observation_dates = terms.get("knock_out_observation_dates")
        if observation_dates is not None and event_date.isoformat() not in {
            str(value) for value in observation_dates
        }:
            raise ValueError("FCN knock-out must use a contractual observation date.")
    if transaction_type == "maturity_redemption" and lifecycle_event_type == "fcn_knock_in":
        final_observation_date = _parse_iso_date(
            terms.get("final_observation_date") if isinstance(terms, dict) else None
        ) or maturity_date
        if event_date < final_observation_date:
            raise ValueError("A knock-in observation does not redeem the FCN. Record the observation, then record redemption at or after final observation.")
    if transaction_type == "lifecycle_event" and lifecycle_event_type == "fcn_knock_in" and isinstance(terms, dict):
        final_observation_date = _parse_iso_date(terms.get("final_observation_date")) or maturity_date
        if terms.get("knock_in_observation") == "final_close" and event_date != final_observation_date:
            raise ValueError("Final-close knock-in can only be confirmed on the final observation date.")

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
    transactions: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    normalized_instrument_ids = {instrument_id for instrument_id in (instrument_ids or set()) if instrument_id}
    if normalized_instrument_ids or as_of_date is not None:
        target_instrument_ids = normalized_instrument_ids or {
            str(item.get("instrument_id") or "")
            for item in list_registry_instruments()
            if str(item.get("instrument_id") or "")
        }
        if instrument_detail_cache is None:
            quotes = load_valuation_quotes(
                target_instrument_ids,
                as_of_date=as_of_date or date.max,
                detail_loader=get_registry_instrument_details,
            )
            valuation_details = {instrument_id: item["detail"] for instrument_id, item in quotes.items()}
            if transactions is not None:
                bind_initial_purchase_valuations(
                    transactions, valuation_details, instrument_detail_loader=valuation_details.get,
                )
            pricing_map = {}
            for instrument_id, item in quotes.items():
                if not item["eligible"]:
                    continue
                detail = valuation_details[instrument_id]
                point = initial_purchase_valuation_point(
                    detail, market_point=item["point"], as_of_date=as_of_date or date.max,
                    candidate_bases=quote_policy_bases(detail, "valuation"),
                    series_unavailable_reason=item["unavailable_reason"],
                )
                if point is not None:
                    pricing_map[instrument_id] = point
            return pricing_map
        missing_instrument_ids = target_instrument_ids - instrument_detail_cache.keys()
        loaded_details = (
            get_registry_instrument_details(missing_instrument_ids)
            if missing_instrument_ids
            else {}
        )
        # A bulk miss must not suppress a caller's authoritative fallback
        # loader (performance/reporting layers may provide one). Positive
        # details can be shared; unresolved ids remain eligible for fallback.
        instrument_detail_cache.update({
            instrument_id: detail for instrument_id, detail in loaded_details.items()
            if isinstance(detail, dict)
        })
        instrument_details = instrument_detail_cache
        if transactions is not None:
            bind_initial_purchase_valuations(
                transactions, instrument_details,
                instrument_detail_loader=instrument_details.get,
            )
        quote_lookup = (
            instrument_detail_cache.quote_lookup
            if isinstance(instrument_detail_cache, valuation_fx.HistoricalInstrumentDetails)
            else None
        )
        pricing_map: dict[str, object] = {}
        for instrument_id in target_instrument_ids:
            detail = instrument_details.get(instrument_id)
            if not isinstance(detail, dict):
                continue
            resolved = _select_quote_point(
                detail,
                role="valuation",
                as_of_date=as_of_date,
                lookup=quote_lookup,
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
        payload = get_shared_fx_rates()
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
    lookup: QuoteSeriesLookup | None = None,
) -> dict[str, object] | None:
    candidate_bases = quote_policy_bases(instrument, role)
    if role == "valuation" and any(
        quote_basis.strip().lower() in VALUATION_PROHIBITED_TOTAL_RETURN_BASES
        for quote_basis in candidate_bases
    ):
        return None
    resolved_as_of_date = as_of_date or date.max
    point = (
        lookup.point(instrument, candidate_bases=candidate_bases, as_of_date=resolved_as_of_date)
        if lookup is not None else resolve_quote_point(
            instrument,
            candidate_bases=candidate_bases,
            as_of_date=resolved_as_of_date,
        ).point
    )
    return initial_purchase_valuation_point(
        instrument, market_point=point, as_of_date=resolved_as_of_date,
        candidate_bases=candidate_bases,
    ) if role == "valuation" else point


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
    *,
    position_recognition: bool = False,
) -> tuple[str, int, str, str, int, str]:
    trade_date = str(transaction.get("trade_date") or "")
    effective_date = transaction_performance_effective_date(transaction)
    if position_recognition and transaction.get("transaction_type") == "dividend_reinvestment":
        effective_date = transaction_position_effective_date(transaction)
    acquisition_date = str(transaction.get("acquisition_date") or "")
    is_prior_opening_balance = (
        str(transaction.get("transaction_type") or "") == "opening_balance"
        and acquisition_date
        and acquisition_date < trade_date
    )
    canonical_key = transaction_sort_key(transaction)
    return (
        effective_date.isoformat() if effective_date is not None else trade_date,
        -1 if is_prior_opening_balance else 1,
        canonical_key[1],
        canonical_key[2],
        canonical_key[3],
        canonical_key[4],
    )


def _fifo_acquisition_sort_key(transaction: dict[str, object]) -> tuple[object, ...]:
    acquisition_date = (
        transaction.get("acquisition_date")
        or transaction_position_effective_date(transaction)
        or transaction.get("trade_date")
    )
    return (str(acquisition_date or ""), *transaction_sort_key(transaction)[1:])


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


def apply_monetary_cost_basis_delta(
    state: dict[str, object],
    *,
    amount_delta: float,
    acquisition_fx_rate: float | None,
) -> float | None:
    """Apply one signed monetary movement and return the released base basis."""

    balance = _safe_float(state.get("amount")) or 0.0
    historical_basis = _safe_float(state.get("historical_cost_basis_base"))
    coverage_complete = bool(state.get("cost_basis_complete", True))
    next_balance = balance + amount_delta

    if abs(next_balance) <= 1e-9:
        released_basis = historical_basis
        state.update(
            {
                "amount": 0.0,
                "historical_cost_basis_base": 0.0,
                "cost_basis_complete": True,
            }
        )
        return released_basis

    increases_exposure = abs(balance) <= 1e-9 or balance * amount_delta > 0
    crosses_zero = balance * next_balance < 0
    if increases_exposure or crosses_zero:
        if acquisition_fx_rate is None:
            state.update(
                {
                    "amount": next_balance,
                    "historical_cost_basis_base": None,
                    "cost_basis_complete": False,
                }
            )
            return None
        if crosses_zero:
            next_basis = next_balance * acquisition_fx_rate
            coverage_complete = True
        else:
            next_basis = (
                historical_basis + amount_delta * acquisition_fx_rate
                if coverage_complete and historical_basis is not None
                else None
            )
        released_basis = (
            historical_basis - next_basis
            if historical_basis is not None and next_basis is not None
            else None
        )
    else:
        next_basis = (
            historical_basis * (next_balance / balance)
            if coverage_complete
            and historical_basis is not None
            and abs(balance) > 1e-12
            else None
        )
        released_basis = (
            historical_basis - next_basis
            if historical_basis is not None and next_basis is not None
            else None
        )

    state.update(
        {
            "amount": next_balance,
            "historical_cost_basis_base": next_basis,
            "cost_basis_complete": coverage_complete and next_basis is not None,
        }
    )
    return released_basis


def _monetary_recognition_date(posting: dict[str, object]) -> date | None:
    return _parse_iso_date(
        posting.get("monetary_recognition_date")
        or posting.get("recognition_start_date")
        or posting.get("trade_date")
        or ledger_posting_effective_date_iso(posting)
    )


def _fx_conversion_legs(
    postings: list[dict[str, object]],
) -> dict[str, dict[str, dict[str, object]]]:
    legs: dict[str, dict[str, dict[str, object]]] = defaultdict(dict)
    for posting in postings:
        if str(posting.get("source_transaction_type") or "") != "fx_conversion":
            continue
        posting_role = str(posting.get("posting_role") or "")
        if posting_role not in {
            "fx_conversion_source_cash",
            "fx_conversion_target_cash",
        }:
            continue
        transaction_id = str(posting.get("transaction_id") or "").strip()
        if transaction_id:
            legs[transaction_id][posting_role] = posting
    return legs


def _monetary_acquisition_fx_rate(
    posting: dict[str, object],
    *,
    amount_delta: float,
    base_currency: str,
    direct_fx_instruments: valuation_fx.FxInstrumentMap,
    instrument_detail_cache: dict[str, dict[str, object] | None],
    fx_conversion_legs: dict[str, dict[str, dict[str, object]]],
    resolve_fx_rate_on: Callable[..., dict[str, object] | None],
) -> tuple[float | None, bool]:
    basis_currency = valuation_fx.required_currency(
        posting.get("currency"),
        field_name="ledger-posting currency",
    )
    basis_amount = abs(amount_delta)
    transaction_type = str(posting.get("source_transaction_type") or "")
    if transaction_type == "fx_conversion":
        transaction_id = str(posting.get("transaction_id") or "").strip()
        posting_role = str(posting.get("posting_role") or "")
        if posting_role == "fx_conversion_target_cash":
            if basis_currency == valuation_fx.required_currency(
                base_currency,
                field_name="portfolio base currency",
            ):
                return 1.0, False
            paired_posting = fx_conversion_legs.get(transaction_id, {}).get(
                "fx_conversion_source_cash"
            )
            if paired_posting is not None:
                basis_currency = valuation_fx.required_currency(
                    paired_posting.get("currency"),
                    field_name="FX conversion source currency",
                )
                basis_amount = abs(
                    _safe_float(paired_posting.get("cash_amount_delta")) or 0.0
                )

    basis_date = _monetary_recognition_date(posting)
    resolved_fx = (
        resolve_fx_rate_on(
            as_of_date=basis_date,
            base_currency=basis_currency,
            quote_currency=base_currency,
            direct_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
        )
        if basis_date is not None and basis_amount > 0
        else None
    )
    basis_fx_rate = _safe_float((resolved_fx or {}).get("rate"))
    acquisition_fx_rate = (
        basis_amount * basis_fx_rate / abs(amount_delta)
        if basis_fx_rate is not None
        and basis_fx_rate > 0
        and abs(amount_delta) > 1e-12
        else None
    )
    return acquisition_fx_rate, bool((resolved_fx or {}).get("stale"))


def _replay_settled_monetary_postings(
    *,
    postings: list[dict[str, object]],
    as_of_date: date,
    base_currency: str,
    direct_fx_instruments: valuation_fx.FxInstrumentMap,
    instrument_detail_cache: dict[str, dict[str, object] | None],
    resolve_fx_rate_on: Callable[..., dict[str, object] | None],
    impact_transaction_ids: set[str] | None = None,
) -> tuple[
    dict[tuple[str, str], dict[str, object]],
    list[dict[str, object]],
]:
    """Replay settled monetary balances and explain selected FX realizations."""

    normalized_base = valuation_fx.required_currency(
        base_currency,
        field_name="portfolio base currency",
    )
    states: dict[tuple[str, str], dict[str, object]] = {}
    impacts: list[dict[str, object]] = []
    transferred_basis_by_group: dict[str, tuple[float | None, bool]] = {}
    conversion_legs = _fx_conversion_legs(postings)
    as_of_iso = as_of_date.isoformat()
    ordered_postings = sorted(
        enumerate(postings),
        key=lambda item: (
            ledger_posting_effective_date_iso(item[1]),
            str(item[1].get("trade_at") or ""),
            str(item[1].get("created_at") or ""),
            int(item[1].get("transaction_sequence") or 0),
            0
            if str(item[1].get("source_transaction_type") or "")
            == "transfer_out"
            else 2
            if str(item[1].get("source_transaction_type") or "")
            == "transfer_in"
            else 1,
            item[0],
        ),
    )
    for _, posting in ordered_postings:
        if ledger_posting_pending_amount_as_of(posting, as_of_date) is not None:
            continue
        amount_delta = _safe_float(posting.get("cash_amount_delta"))
        effective_date_iso = ledger_posting_effective_date_iso(posting)
        if amount_delta is None or effective_date_iso > as_of_iso:
            continue
        account_id = str(posting.get("account_id") or "").strip()
        if not account_id:
            raise ValueError("Settled monetary posting is missing account_id.")
        currency = valuation_fx.required_currency(
            posting.get("currency"),
            field_name="ledger-posting currency",
        )
        state = states.setdefault(
            (account_id, currency),
            {
                "account_id": account_id,
                "currency": currency,
                "amount": 0.0,
                "historical_cost_basis_base": 0.0,
                "cost_basis_complete": True,
                "historical_fx_stale": False,
                "transaction_ids": set(),
            },
        )
        balance_before = _safe_float(state.get("amount")) or 0.0
        historical_basis_before = _safe_float(
            state.get("historical_cost_basis_base")
        )
        historical_basis_complete_before = bool(
            state.get("cost_basis_complete", True)
        )
        historical_fx_stale_before = bool(state.get("historical_fx_stale"))
        transaction_ids = state.get("transaction_ids")
        transaction_id = str(posting.get("transaction_id") or "").strip()
        if isinstance(transaction_ids, set) and transaction_id:
            transaction_ids.add(transaction_id)

        transfer_group_id = str(posting.get("transfer_group_id") or "").strip()
        transaction_type = str(posting.get("source_transaction_type") or "")
        balance_after_candidate = balance_before + amount_delta
        crosses_zero_candidate = balance_before * balance_after_candidate < 0
        adds_basis_candidate = (
            abs(balance_before) <= 1e-9 or balance_before * amount_delta > 0
        )
        acquisition_fx_rate: float | None = None
        acquisition_fx_stale = False
        if transaction_type == "transfer_in" and transfer_group_id:
            transferred_basis, acquisition_fx_stale = transferred_basis_by_group.get(
                transfer_group_id,
                (None, False),
            )
            acquisition_fx_rate = (
                transferred_basis / amount_delta
                if transferred_basis is not None and abs(amount_delta) > 1e-12
                else None
            )
        elif crosses_zero_candidate or adds_basis_candidate:
            acquisition_fx_rate, acquisition_fx_stale = (
                _monetary_acquisition_fx_rate(
                    posting,
                    amount_delta=amount_delta,
                    base_currency=normalized_base,
                    direct_fx_instruments=direct_fx_instruments,
                    instrument_detail_cache=instrument_detail_cache,
                    fx_conversion_legs=conversion_legs,
                    resolve_fx_rate_on=resolve_fx_rate_on,
                )
            )

        reduces_existing_exposure = balance_before * amount_delta < 0
        if (
            impact_transaction_ids is not None
            and transaction_id in impact_transaction_ids
            and transaction_type not in {"transfer_in", "transfer_out"}
            and currency != normalized_base
            and reduces_existing_exposure
        ):
            released_local_amount = (
                (1.0 if balance_before > 0 else -1.0)
                * min(abs(balance_before), abs(amount_delta))
            )
            released_basis = (
                historical_basis_before
                * abs(released_local_amount)
                / abs(balance_before)
                if historical_basis_complete_before
                and historical_basis_before is not None
                and abs(balance_before) > 1e-12
                else None
            )
            recognition_date = _parse_iso_date(effective_date_iso)
            recognition_fx = (
                resolve_fx_rate_on(
                    as_of_date=recognition_date,
                    base_currency=currency,
                    quote_currency=normalized_base,
                    direct_instruments=direct_fx_instruments,
                    instrument_detail_cache=instrument_detail_cache,
                )
                if recognition_date is not None
                else None
            )
            recognition_fx_rate = _safe_float((recognition_fx or {}).get("rate"))
            fair_value_base = (
                released_local_amount * recognition_fx_rate
                if recognition_fx_rate is not None
                else None
            )
            realized_fx_pnl_base = (
                fair_value_base - released_basis
                if fair_value_base is not None and released_basis is not None
                else None
            )
            impacts.append(
                {
                    "transaction_id": transaction_id,
                    "posting_role": str(posting.get("posting_role") or ""),
                    "account_id": account_id,
                    "currency": currency,
                    "recognition_date": (
                        recognition_date.isoformat()
                        if recognition_date is not None
                        else None
                    ),
                    "recognition_fx_rate_to_base": recognition_fx_rate,
                    "local_exposure_released": released_local_amount,
                    "historical_cost_basis_base": released_basis,
                    "fair_value_base": fair_value_base,
                    "realized_cash_fx_pnl_base": realized_fx_pnl_base,
                    "fx_coverage_status": (
                        "unavailable"
                        if realized_fx_pnl_base is None
                        else "stale"
                        if historical_fx_stale_before
                        or bool((recognition_fx or {}).get("stale"))
                        else "complete"
                    ),
                }
            )

        transferred_or_released_basis = apply_monetary_cost_basis_delta(
            state,
            amount_delta=amount_delta,
            acquisition_fx_rate=acquisition_fx_rate,
        )
        balance_after = _safe_float(state.get("amount")) or 0.0
        crosses_zero = balance_before * balance_after < 0
        adds_basis = abs(balance_before) <= 1e-9 or balance_before * amount_delta > 0
        if transaction_type == "transfer_out" and transfer_group_id:
            transferred_basis_by_group[transfer_group_id] = (
                transferred_or_released_basis,
                historical_fx_stale_before
                or (acquisition_fx_stale and (crosses_zero or adds_basis)),
            )
        if abs(balance_after) <= 1e-9:
            state["historical_fx_stale"] = False
        elif crosses_zero:
            state["historical_fx_stale"] = acquisition_fx_stale
        elif adds_basis:
            state["historical_fx_stale"] = (
                historical_fx_stale_before or acquisition_fx_stale
            )
        else:
            state["historical_fx_stale"] = historical_fx_stale_before

    return states, impacts


def settled_monetary_balances_from_postings(
    *,
    postings: list[dict[str, object]],
    as_of_date: date,
    base_currency: str,
    direct_fx_instruments: valuation_fx.FxInstrumentMap,
    instrument_detail_cache: dict[str, dict[str, object] | None],
    resolve_fx_rate_on: Callable[..., dict[str, object] | None] | None = None,
    fx_resolution_cache: valuation_fx.FxRateResolutionCache | None = None,
) -> list[dict[str, object]]:
    """Replay settled cash with account/currency historical base basis."""

    resolved_fx_rate_on = resolve_fx_rate_on or partial(
        valuation_fx.resolve_fx_rate_on,
        instrument_detail_loader=get_registry_instrument_detail,
    )
    states, _impacts = _replay_settled_monetary_postings(
        postings=postings,
        as_of_date=as_of_date,
        base_currency=base_currency,
        direct_fx_instruments=direct_fx_instruments,
        instrument_detail_cache=instrument_detail_cache,
        resolve_fx_rate_on=resolved_fx_rate_on,
    )

    rendered: list[dict[str, object]] = []
    for (account_id, currency), state in sorted(states.items()):
        amount = _safe_float(state.get("amount")) or 0.0
        if abs(amount) <= 1e-9:
            continue
        amount_base, current_fx_stale = valuation_fx.convert_amount_on(
            amount,
            as_of_date=as_of_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
            instrument_detail_loader=get_registry_instrument_detail,
            resolution_cache=fx_resolution_cache,
        )
        historical_basis = (
            _safe_float(state.get("historical_cost_basis_base"))
            if bool(state.get("cost_basis_complete"))
            else None
        )
        transaction_ids = state.get("transaction_ids")
        rendered.append(
            {
                "account_id": account_id,
                "account_ids": [account_id],
                "currency": currency,
                "amount": amount,
                "amount_base": amount_base,
                "cost_basis_historical_base": historical_basis,
                "cost_basis_fx_rate_to_base": (
                    historical_basis / amount
                    if historical_basis is not None and abs(amount) > 1e-12
                    else None
                ),
                "cost_basis_fx_coverage_status": (
                    "stale"
                    if historical_basis is not None
                    and (bool(state.get("historical_fx_stale")) or current_fx_stale)
                    else "complete"
                    if historical_basis is not None and amount_base is not None
                    else "unavailable"
                ),
                "unrealized_fx_pnl_base": (
                    amount_base - historical_basis
                    if amount_base is not None and historical_basis is not None
                    else None
                ),
                "transaction_ids": sorted(
                    item
                    for item in transaction_ids
                    if item
                )
                if isinstance(transaction_ids, set)
                else [],
            }
        )
    return rendered


def build_transaction_cash_fx_impacts(
    *,
    transaction_ids: set[str],
    postings: list[dict[str, object]],
    as_of_date: date,
    base_currency: str,
    direct_fx_instruments: valuation_fx.FxInstrumentMap | None = None,
    instrument_detail_cache: dict[str, dict[str, object] | None] | None = None,
    resolve_fx_rate_on: Callable[..., dict[str, object] | None] | None = None,
) -> list[dict[str, object]]:
    """Return realized cash FX for selected settled monetary postings."""

    # Pair eligibility and every dated cash boundary share this request's full
    # observations and quote index. Same-currency cash never loads FX history.
    resolved_instrument_cache = (
        instrument_detail_cache
        if instrument_detail_cache is not None
        else valuation_fx.HistoricalInstrumentDetails(end_date=as_of_date)
    )
    resolved_direct_instruments = (
        direct_fx_instruments
        if direct_fx_instruments is not None
        else valuation_fx.HistoricalFxInstruments(
            resolved_instrument_cache,
            detail_loader=get_registry_instrument_detail,
        )
    )
    resolved_fx_rate_on = resolve_fx_rate_on or partial(
        valuation_fx.resolve_fx_rate_on,
        instrument_detail_loader=get_registry_instrument_detail,
    )
    _states, impacts = _replay_settled_monetary_postings(
        postings=postings,
        as_of_date=as_of_date,
        base_currency=base_currency,
        direct_fx_instruments=resolved_direct_instruments,
        instrument_detail_cache=resolved_instrument_cache,
        resolve_fx_rate_on=resolved_fx_rate_on,
        impact_transaction_ids=transaction_ids,
    )
    return impacts


def pending_monetary_balances_from_postings(
    *,
    postings: list[dict[str, object]],
    as_of_date: date,
    base_currency: str,
    direct_fx_instruments: valuation_fx.FxInstrumentMap,
    instrument_detail_cache: dict[str, dict[str, object] | None],
    resolve_fx_rate_on: Callable[..., dict[str, object] | None] | None = None,
    fx_resolution_cache: valuation_fx.FxRateResolutionCache | None = None,
) -> list[dict[str, object]]:
    """Build active receivable, payable, and position-recognition balances."""

    resolved_fx_rate_on = resolve_fx_rate_on or partial(
        valuation_fx.resolve_fx_rate_on,
        instrument_detail_loader=get_registry_instrument_detail,
    )
    conversion_legs = _fx_conversion_legs(postings)
    grouped: dict[
        tuple[str, str, str, str, str, str],
        dict[str, object],
    ] = {}
    as_of_iso = as_of_date.isoformat()
    for posting in postings:
        pending_amount = ledger_posting_pending_amount_as_of(posting, as_of_date)
        if pending_amount is not None:
            if abs(pending_amount) <= 1e-9:
                continue
            source_transaction_type = str(
                posting.get("source_transaction_type") or ""
            ).strip()
            if (
                str(posting.get("posting_role") or "")
                == "position_recognition_bridge"
                and source_transaction_type == "buy"
                and pending_amount > 0
            ):
                holding_kind = "pending_subscription"
            elif source_transaction_type == "dividend_reinvestment":
                holding_kind = "settlement_receivable"
            else:
                holding_kind = "position_recognition_adjustment"
            amount = pending_amount
            account_id = str(
                posting.get("settlement_cash_account_id")
                or posting.get("account_id")
                or ""
            ).strip()
        else:
            cash_delta = _safe_float(posting.get("cash_amount_delta"))
            if cash_delta is None or abs(cash_delta) <= 1e-9:
                continue
            if ledger_posting_effective_date_iso(posting) <= as_of_iso:
                continue
            amount = cash_delta
            holding_kind = (
                "settlement_receivable"
                if cash_delta > 0
                else "settlement_payable"
            )
            account_id = str(posting.get("account_id") or "").strip()

        currency = valuation_fx.required_currency(
            posting.get("currency"),
            field_name="ledger-posting currency",
        )
        economic_instrument_id = str(posting.get("instrument_id") or "").strip()
        settlement_date = str(
            posting.get("settlement_date")
            or posting.get("recognition_start_date")
            or ""
        )[:10]
        pending_until_date = str(
            posting.get("recognition_end_date")
            or posting.get("effective_date")
            or settlement_date
        )[:10]
        if settlement_date and settlement_date > as_of_iso:
            pending_status = "awaiting_settlement"
        elif pending_until_date and pending_until_date > as_of_iso:
            pending_status = "settled_awaiting_position"
        elif settlement_date and settlement_date < as_of_iso:
            pending_status = "overdue"
        else:
            pending_status = "due_today"
        key = (
            holding_kind,
            account_id,
            economic_instrument_id,
            currency,
            settlement_date,
            pending_until_date,
        )
        monetary_recognition_date = _monetary_recognition_date(posting)
        monetary_recognition_date_iso = (
            monetary_recognition_date.isoformat()
            if monetary_recognition_date is not None
            else None
        )
        bucket = grouped.setdefault(
            key,
            {
                "holding_kind": holding_kind,
                "account_id": account_id,
                "account_ids": [account_id] if account_id else [],
                "economic_instrument_id": economic_instrument_id or None,
                "economic_instrument_ref": (
                    deepcopy(posting.get("instrument_ref"))
                    if isinstance(posting.get("instrument_ref"), dict)
                    else None
                ),
                "currency": currency,
                "settlement_date": settlement_date or None,
                "pending_until_date": pending_until_date or None,
                "pending_status": pending_status,
                "monetary_recognition_date": monetary_recognition_date_iso,
                "amount": 0.0,
                "amount_base": 0.0,
                "amount_base_complete": True,
                "cost_basis_historical_base": 0.0,
                "cost_basis_complete": True,
                "historical_fx_stale": False,
                "current_fx_stale": False,
                "transaction_ids": set(),
            },
        )
        if bucket.get("monetary_recognition_date") != monetary_recognition_date_iso:
            bucket["monetary_recognition_date"] = None
        bucket["amount"] = (_safe_float(bucket.get("amount")) or 0.0) + amount
        converted_amount, current_fx_stale = valuation_fx.convert_amount_on(
            amount,
            as_of_date=as_of_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
            instrument_detail_loader=get_registry_instrument_detail,
            resolution_cache=fx_resolution_cache,
        )
        if converted_amount is None:
            bucket["amount_base_complete"] = False
        else:
            bucket["amount_base"] = (
                _safe_float(bucket.get("amount_base")) or 0.0
            ) + converted_amount
        acquisition_fx_rate, historical_fx_stale = _monetary_acquisition_fx_rate(
            posting,
            amount_delta=amount,
            base_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
            fx_conversion_legs=conversion_legs,
            resolve_fx_rate_on=resolved_fx_rate_on,
        )
        if acquisition_fx_rate is None:
            bucket["cost_basis_complete"] = False
        else:
            bucket["cost_basis_historical_base"] = (
                _safe_float(bucket.get("cost_basis_historical_base")) or 0.0
            ) + amount * acquisition_fx_rate
        bucket["historical_fx_stale"] = bool(
            bucket.get("historical_fx_stale") or historical_fx_stale
        )
        bucket["current_fx_stale"] = bool(
            bucket.get("current_fx_stale") or current_fx_stale
        )
        transaction_id = str(posting.get("transaction_id") or "").strip()
        transaction_ids = bucket.get("transaction_ids")
        if transaction_id and isinstance(transaction_ids, set):
            transaction_ids.add(transaction_id)

    rendered: list[dict[str, object]] = []
    for bucket in grouped.values():
        amount = _safe_float(bucket.get("amount")) or 0.0
        if abs(amount) <= 1e-9:
            continue
        transaction_ids = bucket.pop("transaction_ids", set())
        amount_base_complete = bool(bucket.pop("amount_base_complete", False))
        cost_basis_complete = bool(bucket.pop("cost_basis_complete", False))
        historical_fx_stale = bool(bucket.pop("historical_fx_stale", False))
        current_fx_stale = bool(bucket.pop("current_fx_stale", False))
        amount_base = (
            _safe_float(bucket.get("amount_base"))
            if amount_base_complete
            else None
        )
        historical_basis = (
            _safe_float(bucket.get("cost_basis_historical_base"))
            if cost_basis_complete
            else None
        )
        rendered.append(
            {
                **bucket,
                "amount_base": amount_base,
                "cost_basis_historical_base": historical_basis,
                "cost_basis_fx_rate_to_base": (
                    historical_basis / amount
                    if historical_basis is not None and abs(amount) > 1e-12
                    else None
                ),
                "cost_basis_fx_coverage_status": (
                    "stale"
                    if historical_basis is not None
                    and (historical_fx_stale or current_fx_stale)
                    else "complete"
                    if historical_basis is not None and amount_base is not None
                    else "unavailable"
                ),
                "unrealized_fx_pnl_base": (
                    amount_base - historical_basis
                    if amount_base is not None and historical_basis is not None
                    else None
                ),
                "transaction_ids": sorted(
                    transaction_ids
                    if isinstance(transaction_ids, set)
                    else []
                ),
            }
        )
    rendered.sort(
        key=lambda item: (
            str(item.get("holding_kind") or ""),
            str(item.get("account_id") or ""),
            str(item.get("economic_instrument_id") or ""),
            str(item.get("currency") or ""),
        )
    )
    return rendered


def build_monetary_subledger(
    *,
    postings: list[dict[str, object]],
    as_of_date: date,
    base_currency: str,
    direct_fx_instruments: valuation_fx.FxInstrumentMap,
    instrument_detail_cache: dict[str, dict[str, object] | None],
    resolve_fx_rate_on: Callable[..., dict[str, object] | None] | None = None,
    fx_resolution_cache: valuation_fx.FxRateResolutionCache | None = None,
) -> dict[str, list[dict[str, object]]]:
    common = {
        "postings": postings,
        "as_of_date": as_of_date,
        "base_currency": base_currency,
        "direct_fx_instruments": direct_fx_instruments,
        "instrument_detail_cache": instrument_detail_cache,
        "resolve_fx_rate_on": resolve_fx_rate_on,
        "fx_resolution_cache": fx_resolution_cache,
    }
    return {
        "settled_balances": settled_monetary_balances_from_postings(**common),
        "pending_balances": pending_monetary_balances_from_postings(**common),
    }


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
    if transaction_type == "dividend_reinvestment":
        entitlement_date = transaction_performance_effective_date(transaction)
        position_date = transaction_position_effective_date(transaction)
        if entitlement_date is None or position_date is None or entitlement_date >= position_date:
            return None
        return {
            "account_id": str(transaction.get("account_id") or ""),
            "posting_account_id": str(transaction.get("account_id") or ""),
            "recognition_start_date": entitlement_date,
            "recognition_end_date": position_date,
            "pending_amount_delta": _safe_float(transaction.get("gross_amount")) or 0.0,
            "settlement_cash_delta": None,
        }
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
        "posting_account_id": settlement_cash_account_id,
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
                **raw_lot,
                "quantity": quantity,
                "cost_basis": cost_basis,
            }
        )
        total_quantity += quantity
        total_cost_basis += cost_basis

    if cleaned_lots:
        cleaned_lots.sort(key=lambda lot: lot.get("fifo_order") or ())
        if cost_basis_method == "moving_average":
            bucket["lots"] = (
                [{**cleaned_lots[0], "quantity": total_quantity, "cost_basis": total_cost_basis}]
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
    opened_by_transaction_id: str | None = None,
    acquisition_sort_key: tuple[object, ...] | None = None,
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
            **lot,
            "opened_by_transaction_id": lot.get("opened_by_transaction_id") or opened_by_transaction_id,
            "fifo_order": lot.get("fifo_order") or acquisition_sort_key,
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
    lot_selections: list[dict[str, object]] | None = None,
) -> tuple[float, list[dict[str, float]]]:
    bucket = _ensure_position_bucket(
        position_state,
        account_id,
        position_reference_id,
    )
    if quantity <= 0:
        return 0.0, []
    if lot_selections and cost_basis_method != "fifo":
        raise ValueError("Explicit lots require FIFO accounting; pooled moving-average cost cannot select individual costs.")

    current_quantity = _safe_float(bucket.get("quantity")) or 0.0
    current_cost_basis = _safe_float(bucket.get("cost_basis")) or 0.0
    if current_quantity <= 1e-9 or quantity > current_quantity + 1e-9:
        raise ValueError(error_message or "Position quantity exceeds available lots as of trade_date.")

    if cost_basis_method == "fifo":
        remaining = min(quantity, current_quantity)
        moved_lots: list[dict[str, float]] = []
        lots = list(bucket.get("lots", []))
        selections = selected_quantities(lots, lot_selections, quantity_key="quantity") if lot_selections else None
        for index, lot in enumerate(lots):
            if remaining <= 1e-9:
                break
            lot_quantity = _safe_float(lot.get("quantity")) or 0.0
            lot_cost_basis = _safe_float(lot.get("cost_basis")) or 0.0
            if lot_quantity <= 1e-9:
                continue
            take_quantity = selections[index] if selections is not None else min(remaining, lot_quantity)
            if take_quantity <= 1e-9:
                continue
            take_cost_basis = lot_cost_basis * (take_quantity / lot_quantity)
            lot["quantity"] = lot_quantity - take_quantity
            lot["cost_basis"] = lot_cost_basis - take_cost_basis
            moved_lots.append({**lot, "quantity": take_quantity, "cost_basis": take_cost_basis})
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
        moved_lots.append({**lot, "quantity": take_quantity, "cost_basis": take_cost_basis})

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
    if (_safe_float(bucket.get("quantity")) or 0.0) <= 1e-9:
        raise ValueError("Return of capital requires an open account position as of trade_date.")
    current_cost_basis = _safe_float(bucket.get("cost_basis")) or 0.0
    reduction = min(amount, current_cost_basis)
    if reduction <= 0:
        return 0.0

    lots = list(bucket.get("lots", []))
    if lots:
        allocations = _proportional_allocations(amount, [(_safe_float(lot.get("quantity")) or 0.0) for lot in lots])
        actual_reduction = 0.0
        for index, lot in enumerate(lots):
            lot_cost_basis = _safe_float(lot.get("cost_basis")) or 0.0
            lot_reduction = min(allocations[index], lot_cost_basis)
            lot["cost_basis"] = lot_cost_basis - lot_reduction
            actual_reduction += lot_reduction
        bucket["lots"] = lots
        _normalize_position_bucket(bucket, cost_basis_method)
        return actual_reduction

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
    transactions = expand_asset_deliveries(transactions)
    if any(tx.get("transaction_type") in SHORT_TRANSACTION_TYPES for tx in transactions):
        actions = _resolved_corporate_actions(transactions, corporate_actions=corporate_actions, as_of_date=as_of_date)
        long_facts, short_facts = partition_security_sides(transactions, corporate_actions=actions, split_quantity=_rounded_split_quantity)
        state = _build_position_state(long_facts, account_cost_methods=account_cost_methods, corporate_actions=actions, as_of_date=as_of_date)
        short_state = _build_position_state(short_facts, account_cost_methods=account_cost_methods, corporate_actions=actions, as_of_date=as_of_date)
        for key, short in short_state.items():
            bucket = state.setdefault(key, {"quantity": 0.0, "cost_basis": 0.0, "lots": []})
            bucket["quantity"] -= short["quantity"]
            bucket["cost_basis"] -= short["cost_basis"]
            bucket["lots"].extend({"quantity": -lot["quantity"], "cost_basis": -lot["cost_basis"]} for lot in short["lots"])
        return state
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
            else _transaction_timeline_sort_key(item[1], position_recognition=True)
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
                opened_by_transaction_id=str(transaction.get("transaction_id") or ""),
                acquisition_sort_key=_fifo_acquisition_sort_key(transaction),
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
                opened_by_transaction_id=str(transaction.get("transaction_id") or ""),
                acquisition_sort_key=_fifo_acquisition_sort_key(transaction),
            )
            continue

        if transaction_type in {"sell", "maturity_redemption"}:
            _consume_position_state(
                position_state,
                account_id,
                position_reference_id,
                quantity=quantity or 0.0,
                cost_basis_method=cost_basis_method,
                lot_selections=transaction.get("lot_selections") or [],
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
            if as_of_date is not None and transaction_position_effective_date(transaction) > as_of_date:
                continue
            _add_position_state(
                position_state,
                account_id,
                position_reference_id,
                quantity=quantity or 0.0,
                cost_basis=gross_amount,
                cost_basis_method=cost_basis_method,
                opened_by_transaction_id=str(transaction.get("transaction_id") or ""),
                acquisition_sort_key=_fifo_acquisition_sort_key(transaction),
            )
            continue

        if transaction_type == "transfer_out" and transaction.get("transfer_object_type") == "position":
            transferred_cost_basis, transferred_lots = _consume_position_state(
                position_state,
                account_id,
                position_reference_id,
                quantity=quantity or 0.0,
                cost_basis_method=cost_basis_method,
                lot_selections=transaction.get("lot_selections") or [],
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
                opened_by_transaction_id=str(transaction.get("transaction_id") or ""),
                acquisition_sort_key=_fifo_acquisition_sort_key(transaction),
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
    transactions = expand_asset_deliveries(transactions)
    if any(tx.get("transaction_type") in SHORT_TRANSACTION_TYPES for tx in transactions):
        actions = _resolved_corporate_actions(transactions, corporate_actions=corporate_actions, as_of_date=as_of_date)
        long_facts, short_facts = partition_security_sides(transactions, corporate_actions=actions, split_quantity=_rounded_split_quantity)
        kwargs = dict(account_cost_methods=account_cost_methods, account_currency_map=account_currency_map, corporate_actions=actions, as_of_date=as_of_date)
        postings = derive_ledger_postings(portfolio_id, long_facts, **kwargs)
        source_types = {tx["transaction_id"]: tx["transaction_type"] for tx in transactions}
        for posting in derive_ledger_postings(portfolio_id, short_facts, **kwargs):
            posting["posting_id"] += "-short"
            posting["source_transaction_type"] = source_types.get(posting["transaction_id"], posting["source_transaction_type"])
            for field in ("quantity_delta", "cost_basis_delta", "cash_amount_delta", "pending_amount_delta", "realized_pnl_delta"):
                if posting.get(field) is not None:
                    posting[field] = -posting[field]
            postings.append(posting)
        return postings
    postings: list[dict[str, object]] = []
    posting_counts: dict[str, int] = defaultdict(int)
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
        monetary_recognition_date: date | None = None,
    ) -> None:
        posting_currency = valuation_fx.required_currency(
            currency if currency is not None else transaction.get("currency"),
            field_name="ledger-posting currency",
        )
        posting_counts[transaction["transaction_id"]] += 1
        posting_index = posting_counts[transaction["transaction_id"]]
        resolved_monetary_recognition_date = (
            monetary_recognition_date
            or transaction_ledger_activity_date(transaction)
        )
        postings.append(
            {
                "posting_id": f"{transaction['transaction_id']}-p{posting_index}",
                "transaction_id": transaction["transaction_id"],
                "transaction_sequence": transaction["transaction_sequence"],
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
                "monetary_recognition_date": (
                    resolved_monetary_recognition_date.isoformat()
                    if resolved_monetary_recognition_date is not None
                    else None
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
            account_id=str(bridge["posting_account_id"]),
            attribution_account_id=str(bridge["account_id"]),
            pending_amount_delta=float(bridge["pending_amount_delta"]),
            currency=str(transaction.get("currency") or ""),
            recognition_start_date=recognition_start_date,
            explicit_effective_date=recognition_end_date,
            monetary_recognition_date=recognition_start_date,
        )
        if (
            as_of_date is not None
            and recognition_start_date <= as_of_date < recognition_end_date
            and bridge["settlement_cash_delta"] is not None
        ):
            transaction_type = str(transaction.get("transaction_type") or "")
            append_posting(
                transaction,
                posting_role=(
                    "security_redemption_cash"
                    if transaction_type == "maturity_redemption"
                    else "security_settlement_cash"
                ),
                account_id=str(bridge["posting_account_id"]),
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
            else _transaction_timeline_sort_key(item[1], position_recognition=True)
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
                    opened_by_transaction_id=str(transaction.get("transaction_id") or ""),
                    acquisition_sort_key=_fifo_acquisition_sort_key(transaction),
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
                opened_by_transaction_id=str(transaction.get("transaction_id") or ""),
                acquisition_sort_key=_fifo_acquisition_sort_key(transaction),
            )
            if isinstance(settlement_cash_account_id, str) and settlement_cash_account_id:
                append_posting(
                    transaction,
                    posting_role="security_settlement_cash",
                    account_id=settlement_cash_account_id,
                    cash_amount_delta=-(
                        (0.0 if transaction.get("noncash_delivery") else gross_amount)
                        + fees + taxes
                    ),
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
                lot_selections=transaction.get("lot_selections") or [],
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
        if option_action == "sell_to_open" or transaction_type == "option_opening_balance":
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
                realized_pnl_delta=max(gross_amount - returned_cost_basis, 0.0),
                currency=currency,
            )
            continue

        if transaction_type == "dividend_reinvestment":
            if as_of_date is not None and transaction_position_effective_date(transaction) > as_of_date:
                continue
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
                opened_by_transaction_id=str(transaction.get("transaction_id") or ""),
                acquisition_sort_key=_fifo_acquisition_sort_key(transaction),
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
                lot_selections=transaction.get("lot_selections") or [],
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
        ) in {
            "option_writer_expiry",
            "option_writer_cash_settlement",
            "option_writer_assignment",
        }:
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
                    lot_selections=transaction.get("lot_selections") or [],
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
                    opened_by_transaction_id=str(transaction.get("transaction_id") or ""),
                    acquisition_sort_key=_fifo_acquisition_sort_key(transaction),
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
    # Every ownership boundary in this validation uses the same source input.
    # Fetch once, including physical-delivery underlyings and future explicit
    # boundaries; individual replays still apply their own effective-date cut.
    if corporate_actions is None:
        latest_boundary = max(
            [date.today()]
            + [
                boundary
                for transaction in ordered_transactions
                for boundary in (
                    _parse_iso_date(transaction.get("trade_date")),
                    _parse_iso_date(transaction.get("entitlement_date")),
                    transaction_performance_effective_date(transaction),
                )
                if boundary is not None
            ]
        )
        instrument_ids = {
            str(transaction["instrument_id"])
            for transaction in expand_asset_deliveries(ordered_transactions)
            if transaction.get("instrument_id")
        }
        corporate_actions = (
            list_registry_corporate_actions(instrument_ids, effective_on_or_before=latest_boundary)
            if instrument_ids else []
        )
    _build_position_state(
        ordered_transactions,
        account_cost_methods=account_cost_methods,
        corporate_actions=corporate_actions,
    )
    # Rebuild the durable writer-obligation subledger as part of the same
    # history validation. This catches partial-close/expiry/cash-settlement
    # quantity errors before a transaction can be persisted.
    derive_option_obligation_events(ordered_transactions)

    entitlement_quantities: dict[date, dict[tuple[str, str], float]] = {}
    for transaction in ordered_transactions:
        transaction_type = str(transaction.get("transaction_type") or "")
        position_reference_id = _position_reference_id(transaction)
        if transaction.get("instrument_id") and (
            transaction_type in {"sell", "short_sell"}
            or (transaction_type == "transfer_out" and transaction.get("transfer_object_type") == "position")
        ):
            trade_date = _parse_iso_date(transaction.get("trade_date"))
            prior_transactions = [
                candidate for candidate in ordered_transactions
                if transaction_sort_key(candidate) < transaction_sort_key(transaction)
            ]
            key = (str(transaction["account_id"]), str(transaction["instrument_id"]))
            pending_quantity = pending_asset_delivery_quantities(prior_transactions, trade_date).get(key, 0.0)
            if pending_quantity > 0:
                prior_state = _build_position_state(
                    prior_transactions,
                    account_cost_methods=account_cost_methods, corporate_actions=corporate_actions, as_of_date=trade_date,
                )
                owned_quantity = float(prior_state.get(key, {}).get("quantity") or 0)
                disposal_quantity = float(transaction.get("quantity") or 0)
                if transaction_type == "short_sell":
                    disposal_quantity = min(disposal_quantity, max(owned_quantity, 0.0))
                if disposal_quantity > owned_quantity - pending_quantity + 1e-9:
                    raise ValueError("Transaction quantity exceeds delivered shares; FCN shares awaiting delivery cannot be sold or transferred.")
                cost_method = _resolve_cost_basis_method(account_cost_methods, key[0])
                if cost_method == "moving_average":
                    raise ValueError("Moving-average disposals must wait until pending FCN delivery completes; the pooled cost method would otherwise release undelivered share origins.")
                pending_source_ids = {
                    str(source["transaction_id"])
                    for source in expand_asset_deliveries(prior_transactions)
                    if source.get("noncash_delivery")
                    and (str(source["account_id"]), str(source["instrument_id"])) == key
                    and transaction_position_effective_date(source) <= trade_date
                    and str(source["delivery_date"]) > trade_date.isoformat()
                }
                _, consumed_lots = _consume_position_state(
                    prior_state, key[0], key[1], quantity=disposal_quantity,
                    cost_basis_method=cost_method, lot_selections=transaction.get("lot_selections") or [],
                )
                if any(str(lot.get("opened_by_transaction_id") or "") in pending_source_ids for lot in consumed_lots):
                    raise ValueError("The disposal selects FCN shares awaiting delivery. Select an already delivered opening lot or wait until delivery completes.")
        if transaction_type == "lifecycle_event" and transaction.get("lifecycle_event_type") == "fcn_knock_in":
            prior_transactions = [
                candidate for candidate in ordered_transactions
                if transaction_sort_key(candidate) < transaction_sort_key(transaction)
            ]
            available_quantity = estimate_position_quantity(
                portfolio_id,
                prior_transactions,
                account_id=str(transaction.get("account_id") or ""),
                position_reference_id=position_reference_id,
                account_cost_methods=account_cost_methods,
                corporate_actions=corporate_actions,
                as_of_date=transaction_performance_effective_date(transaction),
            )
            if available_quantity <= 1e-9:
                raise ValueError("FCN knock-in observation requires an open position at the observation time.")
            continue
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
        # Ordinary income shares one beginning-of-day entitlement state across
        # all accounts/assets. Undated expenses use their actual execution
        # moment, and a fact preceding its own entitlement must remain excluded.
        shared_entitlement = not (
            transaction_type in {"fee", "tax"}
            and _parse_iso_date(transaction.get("entitlement_date")) is None
        ) and not transaction_precedes_entitlement_bod(transaction, entitlement_date)
        quantities = entitlement_quantities.get(entitlement_date) if shared_entitlement else None
        prior_transactions = None
        if quantities is None:
            prior_transactions = [
                candidate
                for candidate in ordered_transactions
                if str(candidate.get("transaction_id") or "") != transaction_id
                and transaction_precedes_asset_cash_flow(candidate, transaction)
            ]
            state = _build_position_state(
                prior_transactions,
                account_cost_methods=account_cost_methods,
                corporate_actions=corporate_actions,
                as_of_date=entitlement_date,
            )
            quantities = {
                key: _safe_float(bucket.get("quantity")) or 0.0
                for key, bucket in state.items()
            }
            if shared_entitlement:
                entitlement_quantities[entitlement_date] = quantities
        available_quantity = quantities.get(
            (str(transaction.get("account_id") or ""), position_reference_id), 0.0
        )
        derivative_contract = _derivative_contract(transaction)
        if (
            available_quantity <= 1e-9
            and isinstance(derivative_contract, dict)
            and str(derivative_contract.get("contract_type") or "").lower()
            == "option"
        ):
            if transaction_type in {"fee", "tax"} and not transaction.get("entitlement_date"):
                assert prior_transactions is not None
                available_quantity = sum(
                    _safe_float(obligation.get("remaining_quantity")) or 0.0
                    for obligation in build_option_obligations(prior_transactions, as_of_date=trade_date)
                    if obligation.get("account_id") == transaction.get("account_id")
                    and obligation.get("derivative_contract_id") == transaction.get("derivative_contract_id")
                )
            else:
                available_quantity = estimate_option_obligation_quantity_at_entitlement(
                    ordered_transactions,
                    account_id=str(transaction.get("account_id") or ""),
                    derivative_contract_id=str(
                        transaction.get("derivative_contract_id") or ""
                    ),
                    entitlement_date=entitlement_date,
                    exclude_transaction_id=transaction_id,
                )
        if transaction_type in {"fee", "tax"}:
            available_quantity = abs(available_quantity)
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
    cost_basis_acquisition_date: str | None = None,
    entry_quantity: float,
    entry_gross_amount: float,
    entry_fee_amount: float,
    entry_tax_amount: float,
    entry_cost_basis: float,
    source_position_lot_id: str | None = None,
    linked_transaction_ids: set[str] | None = None,
    cost_basis_origins: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    resolved_linked_transaction_ids = set(linked_transaction_ids or [])
    resolved_linked_transaction_ids.add(opened_by_transaction_id)
    resolved_cost_basis_origins = deepcopy(cost_basis_origins or [])
    if cost_basis_origins is None and entry_cost_basis > 1e-9:
        resolved_cost_basis_origins = [
            {
                "origin_transaction_id": opened_by_transaction_id,
                "acquisition_date": cost_basis_acquisition_date
                or acquisition_date,
                "entry_cost_basis": entry_cost_basis,
                "remaining_cost_basis": entry_cost_basis,
            }
        ]
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
        "_cost_basis_origins": resolved_cost_basis_origins,
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


def _take_position_lot_cost_basis_origins(
    position_lot: dict[str, object],
    amount: float,
) -> list[dict[str, object]]:
    """Release historical-cost origins in proportion to the lot basis."""

    if amount <= 1e-9:
        return []
    origins = position_lot.get("_cost_basis_origins")
    if not isinstance(origins, list):
        return []
    weights = [
        max(_safe_float(origin.get("remaining_cost_basis")) or 0.0, 0.0)
        if isinstance(origin, dict)
        else 0.0
        for origin in origins
    ]
    allocations = _proportional_allocations(amount, weights)
    released: list[dict[str, object]] = []
    for origin, allocation in zip(origins, allocations, strict=True):
        if not isinstance(origin, dict) or allocation <= 1e-9:
            continue
        remaining = _safe_float(origin.get("remaining_cost_basis")) or 0.0
        released_amount = min(allocation, remaining)
        origin["remaining_cost_basis"] = remaining - released_amount
        released.append(
            {
                "origin_transaction_id": origin.get("origin_transaction_id"),
                "acquisition_date": origin.get("acquisition_date"),
                "entry_cost_basis": origin.get("entry_cost_basis"),
                "remaining_cost_basis": released_amount,
            }
        )
    return released


def _realization_cost_basis_origins(
    released_origins: object,
) -> list[dict[str, object]]:
    return [
        {
            "origin_transaction_id": origin.get("origin_transaction_id"),
            "acquisition_date": origin.get("acquisition_date"),
            "entry_cost_basis": origin.get("entry_cost_basis"),
            "cost_basis_released": origin.get("remaining_cost_basis"),
        }
        for origin in list(released_origins or [])
        if isinstance(origin, dict)
    ]


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
    active_lots = [
        position_lot
        for position_lot in position_lots_by_key.get(
            (account_id, position_reference_id),
            [],
        )
        if (_safe_float(position_lot.get("remaining_quantity")) or 0.0) > 1e-9
    ]
    return sorted(
        active_lots,
        key=lambda lot: lot["_fifo_order"],
    )


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
    lot_selections: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    active_lots = _active_position_lots(
        position_lots_by_key,
        account_id,
        position_reference_id,
    )
    if quantity <= 0:
        return []
    if lot_selections and cost_basis_method != "fifo":
        raise ValueError("Explicit lots require FIFO accounting; pooled moving-average cost cannot select individual costs.")

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
        selections = selected_quantities(active_lots, lot_selections, quantity_key="remaining_quantity") if lot_selections else None
        for index, position_lot in enumerate(active_lots):
            if remaining <= 1e-9:
                break
            lot_quantity = _safe_float(position_lot.get("remaining_quantity")) or 0.0
            lot_cost_basis = _safe_float(position_lot.get("remaining_cost_basis")) or 0.0
            if lot_quantity <= 1e-9:
                continue
            take_quantity = selections[index] if selections is not None else min(remaining, lot_quantity)
            if take_quantity <= 1e-9:
                continue
            take_cost_basis = lot_cost_basis * (take_quantity / lot_quantity) if lot_quantity > 0 else 0.0
            released_origins = _take_position_lot_cost_basis_origins(
                position_lot,
                take_cost_basis,
            )
            position_lot["remaining_quantity"] = lot_quantity - take_quantity
            position_lot["remaining_cost_basis"] = lot_cost_basis - take_cost_basis
            slices.append(
                {
                    "position_lot": position_lot,
                    "quantity": take_quantity,
                    "cost_basis": take_cost_basis,
                    "cost_basis_origins": released_origins,
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
        released_origins = _take_position_lot_cost_basis_origins(
            position_lot,
            take_cost_basis,
        )
        position_lot["remaining_quantity"] = lot_quantity - take_quantity
        position_lot["remaining_cost_basis"] = lot_cost_basis - take_cost_basis
        slices.append(
            {
                "position_lot": position_lot,
                "quantity": take_quantity,
                "cost_basis": take_cost_basis,
                "cost_basis_origins": released_origins,
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
    trade_date: str,
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

    quantity_weights = [(_safe_float(position_lot.get("remaining_quantity")) or 0.0) for position_lot in active_lots]
    allocations = _proportional_allocations(amount, quantity_weights)
    for index, position_lot in enumerate(active_lots):
        allocation = allocations[index]
        if allocation <= 1e-9:
            continue
        current_remaining_cost = _safe_float(position_lot.get("remaining_cost_basis")) or 0.0
        reduced_amount = min(allocation, current_remaining_cost)
        _take_position_lot_cost_basis_origins(position_lot, reduced_amount)
        position_lot["remaining_cost_basis"] = current_remaining_cost - reduced_amount
        position_lot["return_of_capital_amount"] = (
            (_safe_float(position_lot.get("return_of_capital_amount")) or 0.0) + reduced_amount
        )
        excess = allocation - reduced_amount
        if excess > 1e-9:
            for field in ("realized_gross_proceeds", "realized_proceeds", "realized_pnl"):
                position_lot[field] = (_safe_float(position_lot.get(field)) or 0.0) + excess
            realizations = position_lot.setdefault("realizations", [])
            realizations.append({
                "realization_id": f"{position_lot['position_lot_id']}-r{len(realizations) + 1}",
                "transaction_id": transaction_id, "transaction_type": "return_of_capital",
                "trade_date": trade_date, "position_effective_date": trade_date,
                "quantity": 0.0, "gross_proceeds": excess, "proceeds": excess,
                "cost_basis_released": 0.0, "cost_basis_origins": [], "realized_pnl": excess,
                "price": None, "remaining_quantity_after": position_lot["remaining_quantity"],
                "remaining_cost_basis_after": position_lot["remaining_cost_basis"],
                "status_after": position_lot["status"], "note": "Return of capital exceeding this lot's remaining book basis.",
            })
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
    transactions = expand_asset_deliveries(transactions)
    if any(tx.get("transaction_type") in SHORT_TRANSACTION_TYPES for tx in transactions):
        actions = _resolved_corporate_actions(transactions, corporate_actions=corporate_actions, as_of_date=as_of_date)
        long_facts, short_facts = partition_security_sides(transactions, corporate_actions=actions, split_quantity=_rounded_split_quantity)
        kwargs = dict(account_id=account_id, position_reference_id=position_reference_id, status=status, as_of_date=as_of_date,
                      corporate_actions=actions, pricing_map=pricing_map, instrument_detail_cache=instrument_detail_cache, resolve_pricing=resolve_pricing)
        lots = build_position_lots(portfolio_id, accounts, long_facts, **kwargs)
        lots.extend(reflect_short_lot(lot) for lot in build_position_lots(portfolio_id, accounts, short_facts, **kwargs))
        return sorted(lots, key=lambda lot: (str(lot["opened_at"]), str(lot["position_lot_id"])), reverse=True)
    account_cost_methods = {
        str(account.get("account_id") or ""): str(account.get("cost_basis_method") or "fifo")
        for account in accounts
        if account.get("account_type") == "securities_account"
    }
    fifo_order_by_transaction = {
        str(transaction.get("transaction_id") or ""): _fifo_acquisition_sort_key(transaction)
        for transaction in transactions
        if not transaction.get("fcn_settlement_cashflow")
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
        cost_basis_acquisition_date: str | None = None,
        entry_quantity: float,
        entry_gross_amount: float,
        entry_fee_amount: float,
        entry_tax_amount: float,
        entry_cost_basis: float,
        source_position_lot_id: str | None = None,
        linked_transaction_ids: set[str] | None = None,
        cost_basis_origins: list[dict[str, object]] | None = None,
        acquisition_sort_key: tuple[object, ...] | None = None,
    ) -> dict[str, object]:
        resolved_cost_basis_method = _resolve_cost_basis_method(account_cost_methods, target_account_id)
        fifo_order = acquisition_sort_key or fifo_order_by_transaction[opened_by_transaction_id]
        if resolved_cost_basis_method == "moving_average":
            active_lots = _active_position_lots(
                position_lots_by_key,
                target_account_id,
                target_position_reference_id,
            )
            if active_lots:
                position_lot = active_lots[0]
                position_lot["_fifo_order"] = min(position_lot["_fifo_order"], fifo_order)
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
                origins = position_lot.setdefault("_cost_basis_origins", [])
                if isinstance(origins, list):
                    if cost_basis_origins is not None:
                        origins.extend(deepcopy(cost_basis_origins))
                    elif entry_cost_basis > 1e-9:
                        origins.append(
                            {
                                "origin_transaction_id": opened_by_transaction_id,
                                "acquisition_date": cost_basis_acquisition_date
                                or acquisition_date,
                                "entry_cost_basis": entry_cost_basis,
                                "remaining_cost_basis": entry_cost_basis,
                            }
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
            cost_basis_acquisition_date=cost_basis_acquisition_date,
            entry_quantity=entry_quantity,
            entry_gross_amount=entry_gross_amount,
            entry_fee_amount=entry_fee_amount,
            entry_tax_amount=entry_tax_amount,
            entry_cost_basis=entry_cost_basis,
            source_position_lot_id=source_position_lot_id,
            linked_transaction_ids=linked_transaction_ids,
            cost_basis_origins=cost_basis_origins,
        )
        position_lot["_fifo_order"] = fifo_order
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
                if transaction_precedes_asset_cash_flow(
                    transaction_item, sorted_transactions[transaction_index]
                )
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
        if abs(amount) <= 1e-9:
            return
        entitled_lots = entitled_position_lot_snapshots(
            transaction_index=transaction_index,
            target_account_id=target_account_id,
            target_position_reference_id=target_position_reference_id,
            entitlement_date=entitlement_date,
            error_message=error_message,
        )
        weights = [(_safe_float(position_lot.get(weight_field)) or 0.0) for position_lot in entitled_lots]
        allocations = [(1 if amount >= 0 else -1) * value for value in _proportional_allocations(abs(amount), weights)]
        for index, snapshot_lot in enumerate(entitled_lots):
            allocation = allocations[index]
            if abs(allocation) <= 1e-9:
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
                cost_basis_origins = _take_position_lot_cost_basis_origins(
                    position_lot,
                    remaining_cost_basis,
                )
                successor_specs.append(
                    {
                        "old_lot_id": str(position_lot.get("position_lot_id") or ""),
                        "old_quantity": old_quantity,
                        "target_quantity": target_quantity,
                        "remaining_cost_basis": remaining_cost_basis,
                        "instrument_ref": deepcopy(position_lot.get("instrument_ref") or {}),
                        "currency": str(position_lot.get("currency") or ""),
                        "acquisition_date": str(position_lot.get("acquisition_date") or effective_date),
                        "acquisition_sort_key": position_lot["_fifo_order"],
                        "linked_transaction_ids": set(linked_ids) if isinstance(linked_ids, set) else set(),
                        "cost_basis_origins": cost_basis_origins,
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
                    acquisition_sort_key=spec["acquisition_sort_key"],
                    entry_quantity=target_quantity,
                    entry_gross_amount=remaining_cost_basis,
                    entry_fee_amount=0.0,
                    entry_tax_amount=0.0,
                    entry_cost_basis=remaining_cost_basis,
                    source_position_lot_id=str(spec["old_lot_id"]),
                    linked_transaction_ids=linked_transaction_ids,
                    cost_basis_origins=list(spec["cost_basis_origins"]),
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
    timeline.extend(
        ("reinvestment_position", transaction_index_by_identity[id(transaction)], transaction)
        for transaction in sorted_transactions
        if transaction.get("transaction_type") == "dividend_reinvestment"
        and transaction_performance_effective_date(transaction) != transaction_position_effective_date(transaction)
        and (as_of_date is None or transaction_position_effective_date(transaction) <= as_of_date)
    )
    timeline.sort(
        key=lambda item: (
            _corporate_action_sort_key(item[2])
            if item[0] == "corporate_action"
            else _transaction_timeline_sort_key(
                item[2], position_recognition=item[0] == "reinvestment_position"
            )
        )
    )

    settlement_lot_cashflows: list[dict[str, object]] = []
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

        if transaction.get("fcn_settlement_cashflow") and (
            trade_date >= str(transaction["settlement_source_economic_date"])
            or currency != str((derivative_contract or {}).get("currency") or currency)
        ):
            # A final payment can be recognized after the note has closed.
            # Attach same-currency amounts to the lots redeemed by this fact;
            # cross-currency expenses stay native in the cash/performance ledger.
            settlement_lot_cashflows.append(transaction)
            continue

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
            monetary_recognition_date = transaction_ledger_activity_date(transaction)
            cost_basis_acquisition_date = (
                monetary_recognition_date.isoformat()
                if transaction_type == "buy"
                and monetary_recognition_date is not None
                else acquisition_date
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
                acquisition_date=acquisition_date,
                cost_basis_acquisition_date=cost_basis_acquisition_date,
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
            if item_kind == "transaction":
                allocate_snapshot_cash_flow(
                    transaction_index=transaction_index,
                    target_account_id=account_key,
                    target_position_reference_id=resolved_position_reference_id,
                    entitlement_date=entitlement_date,
                    amount=gross_amount + fees,
                    field_name="income_cash_amount",
                    weight_field="remaining_quantity",
                    transaction_id=transaction_id,
                    error_message=(
                        "Dividend reinvestment requires entitled position lots as of entitlement_date."
                    ),
                )
                if fees > 0:
                    allocate_snapshot_cash_flow(
                        transaction_index=transaction_index,
                        target_account_id=account_key,
                        target_position_reference_id=resolved_position_reference_id,
                        entitlement_date=entitlement_date,
                        amount=fees,
                        field_name="expense_cash_amount",
                        weight_field="remaining_quantity",
                        transaction_id=transaction_id,
                        error_message=(
                            "Dividend reinvestment requires entitled position lots as of entitlement_date."
                        ),
                    )
                if transaction_performance_effective_date(transaction) != position_effective_date:
                    continue
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
                cost_basis_acquisition_date=entitlement_date.isoformat(),
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
                lot_selections=transaction.get("lot_selections") or [],
                error_message="Transaction quantity exceeds account position as of position_effective_date.",
            )
            gross_amount += _safe_float(transaction.get("delivered_value")) or 0.0
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
                            "cost_basis_origins": _realization_cost_basis_origins(
                                slice_item.get("cost_basis_origins")
                            ),
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
                trade_date=str(transaction.get("trade_date") or ""),
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
                lot_selections=transaction.get("lot_selections") or [],
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
                        "acquisition_sort_key": position_lot["_fifo_order"],
                        "entry_quantity": matched_quantity,
                        "entry_gross_amount": matched_entry_gross_amount,
                        "entry_fee_amount": matched_entry_fee_amount,
                        "entry_tax_amount": matched_entry_tax_amount,
                        "entry_cost_basis": matched_cost_basis,
                        "cost_basis_origins": deepcopy(
                            slice_item.get("cost_basis_origins") or []
                        ),
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
                    acquisition_sort_key=incoming_slice["acquisition_sort_key"],
                    entry_quantity=entry_quantity,
                    entry_gross_amount=entry_gross_amount,
                    entry_fee_amount=entry_fee_amount,
                    entry_tax_amount=entry_tax_amount,
                    entry_cost_basis=entry_cost_basis,
                    source_position_lot_id=(
                        str(incoming_slice.get("source_position_lot_id") or "") or None
                    ),
                    linked_transaction_ids=linked_transaction_ids,
                    cost_basis_origins=list(
                        incoming_slice.get("cost_basis_origins") or []
                    ),
                )
            continue

    for cashflow in settlement_lot_cashflows:
        contract_currency = str((cashflow.get("derivative_contract") or {}).get("currency") or cashflow["currency"])
        if cashflow["currency"] != contract_currency:
            continue
        matched_lots = [
            lot for lot in all_position_lots
            if lot["account_id"] == cashflow["account_id"]
            and lot.get("derivative_contract_id") == cashflow.get("derivative_contract_id")
            and any(item["transaction_id"] == cashflow["transaction_id"] for item in lot["realizations"])
        ]
        weights = [sum(
            float(item["quantity"]) for item in lot["realizations"]
            if item["transaction_id"] == cashflow["transaction_id"]
        ) for lot in matched_lots]
        amounts = _proportional_allocations(float(cashflow["gross_amount"]), weights)
        field = "income_cash_amount" if cashflow["transaction_type"] == "coupon" else "expense_cash_amount"
        for lot, amount in zip(matched_lots, amounts, strict=True):
            lot[field] += amount
            _touch_position_lot(lot, str(cashflow["transaction_id"]))

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
                transactions=transactions,
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
        instrument_price, current_market_value, event_valued = resolve_position_valuation(
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
                "_fifo_order": raw_position_lot["_fifo_order"],
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
                "cost_basis_origins": deepcopy(
                    raw_position_lot.get("_cost_basis_origins") or []
                ),
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
                "valuation_basis": (
                    "carried_cost" if event_valued else "transaction_price"
                    if isinstance(instrument_quote, dict) and instrument_quote.get("status") == "transaction-price"
                    else "market_quote"
                ),
                "valuation_source_transaction_ids": (
                    instrument_quote.get("valuation_source_transaction_ids", [])
                    if isinstance(instrument_quote, dict) else []
                ),
                "unrealized_pnl": (
                    current_market_value - remaining_cost_basis
                    if current_market_value is not None and not event_valued
                    else None
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


def build_transaction_accounting_impact(
    transaction: dict[str, object] | None,
    position_lots: list[dict[str, object]],
    *,
    base_currency: str,
    direct_fx_instruments: dict[tuple[str, str], str] | None = None,
    instrument_detail_cache: dict[str, dict[str, object] | None] | None = None,
    resolve_fx_rate_on: Callable[..., dict[str, object] | None] | None = None,
) -> dict[str, object] | None:
    """Explain a disposal's price and FX realization from released lot origins."""

    if transaction is None or str(transaction.get("transaction_type") or "") not in {
        "sell",
        "maturity_redemption",
    }:
        return None
    transaction_id = str(transaction.get("transaction_id") or "").strip()
    realizations = [
        realization
        for position_lot in position_lots
        for realization in list(position_lot.get("realizations") or [])
        if isinstance(realization, dict)
        and str(realization.get("transaction_id") or "") == transaction_id
    ]
    if not realizations:
        return None

    currency = valuation_fx.required_currency(
        transaction.get("currency"),
        field_name="transaction currency",
    )
    normalized_base = valuation_fx.required_currency(
        base_currency,
        field_name="portfolio base currency",
    )
    resolved_direct_instruments = (
        direct_fx_instruments
        if direct_fx_instruments is not None
        else valuation_fx.fx_direct_instrument_map(get_shared_fx_rates())
    )
    resolved_instrument_cache = (
        instrument_detail_cache
        if instrument_detail_cache is not None
        else {}
    )
    resolved_fx_rate_on = resolve_fx_rate_on or partial(
        valuation_fx.resolve_fx_rate_on,
        instrument_detail_loader=get_registry_instrument_detail,
    )

    recognition_date = transaction_position_effective_date(transaction)
    recognition_fx = (
        resolved_fx_rate_on(
            as_of_date=recognition_date,
            base_currency=currency,
            quote_currency=normalized_base,
            direct_instruments=resolved_direct_instruments,
            instrument_detail_cache=resolved_instrument_cache,
        )
        if recognition_date is not None
        else None
    )
    recognition_fx_rate = _safe_float((recognition_fx or {}).get("rate"))
    local_cost_basis_released = sum(
        _safe_float(realization.get("cost_basis_released")) or 0.0
        for realization in realizations
    )
    local_net_proceeds = sum(
        _safe_float(realization.get("proceeds")) or 0.0
        for realization in realizations
    )
    historical_cost_basis_base = 0.0
    historical_basis_complete = True
    historical_fx_stale = False
    released_origin_total = 0.0
    for realization in realizations:
        origins = [
            origin
            for origin in list(realization.get("cost_basis_origins") or [])
            if isinstance(origin, dict)
        ]
        for origin in origins:
            origin_amount = _safe_float(origin.get("cost_basis_released"))
            origin_date = _parse_iso_date(origin.get("acquisition_date"))
            if origin_amount is None or origin_date is None:
                historical_basis_complete = False
                continue
            released_origin_total += origin_amount
            origin_fx = resolved_fx_rate_on(
                as_of_date=origin_date,
                base_currency=currency,
                quote_currency=normalized_base,
                direct_instruments=resolved_direct_instruments,
                instrument_detail_cache=resolved_instrument_cache,
            )
            origin_fx_rate = _safe_float((origin_fx or {}).get("rate"))
            if origin_fx_rate is None or origin_fx_rate <= 0:
                historical_basis_complete = False
                continue
            historical_cost_basis_base += origin_amount * origin_fx_rate
            historical_fx_stale = historical_fx_stale or bool(
                (origin_fx or {}).get("stale")
            )

    if currency == normalized_base:
        historical_cost_basis_base = local_cost_basis_released
        released_origin_total = local_cost_basis_released
        historical_basis_complete = True
    origin_tolerance = max(abs(local_cost_basis_released) * 1e-9, 1e-7)
    historical_basis_complete = historical_basis_complete and (
        abs(released_origin_total - local_cost_basis_released) <= origin_tolerance
    )
    realized_price_pnl_base = (
        (local_net_proceeds - local_cost_basis_released) * recognition_fx_rate
        if recognition_fx_rate is not None
        else None
    )
    realized_position_fx_pnl_base = (
        local_cost_basis_released * recognition_fx_rate
        - historical_cost_basis_base
        if recognition_fx_rate is not None and historical_basis_complete
        else None
    )
    realized_position_pnl_base = (
        realized_price_pnl_base + realized_position_fx_pnl_base
        if realized_price_pnl_base is not None
        and realized_position_fx_pnl_base is not None
        else None
    )

    monetary_recognition_date = transaction_ledger_activity_date(transaction)
    monetary_fx = (
        resolved_fx_rate_on(
            as_of_date=monetary_recognition_date,
            base_currency=currency,
            quote_currency=normalized_base,
            direct_instruments=resolved_direct_instruments,
            instrument_detail_cache=resolved_instrument_cache,
        )
        if monetary_recognition_date is not None
        else None
    )
    monetary_fx_rate = _safe_float((monetary_fx or {}).get("rate"))
    settlement_monetary_cost_basis_base = (
        local_net_proceeds * monetary_fx_rate
        if monetary_fx_rate is not None
        else None
    )
    coverage_status = (
        "unavailable"
        if recognition_fx_rate is None
        or monetary_fx_rate is None
        or not historical_basis_complete
        else "stale"
        if historical_fx_stale
        or bool((recognition_fx or {}).get("stale"))
        or bool((monetary_fx or {}).get("stale"))
        else "complete"
    )
    return {
        "base_currency": normalized_base,
        "recognition_date": (
            recognition_date.isoformat() if recognition_date is not None else None
        ),
        "recognition_fx_rate_to_base": recognition_fx_rate,
        "local_cost_basis_released": local_cost_basis_released,
        "local_net_proceeds": local_net_proceeds,
        "historical_cost_basis_base": (
            historical_cost_basis_base if historical_basis_complete else None
        ),
        "realized_price_pnl_base": realized_price_pnl_base,
        "realized_position_fx_pnl_base": realized_position_fx_pnl_base,
        "realized_position_pnl_base": realized_position_pnl_base,
        "monetary_recognition_date": (
            monetary_recognition_date.isoformat()
            if monetary_recognition_date is not None
            else None
        ),
        "settlement_monetary_cost_basis_base": (
            settlement_monetary_cost_basis_base
        ),
        "fx_coverage_status": coverage_status,
    }


def build_current_position_cycle_costs(
    transactions: list[dict[str, object]],
    *,
    as_of_date: date,
    corporate_actions: list[dict[str, object]] | None = None,
) -> dict[str, dict[str, object]]:
    """Return economic net investment for each currently open security cycle."""

    states: dict[str, dict[str, object]] = {}
    recognized_transactions = [
        item
        for item in transactions
        if _transaction_is_recognized_as_of(item, as_of_date)
    ]
    timeline: list[tuple[str, dict[str, object]]] = [
        ("corporate_action", event)
        for event in _resolved_corporate_actions(
            recognized_transactions,
            corporate_actions=corporate_actions,
            as_of_date=as_of_date,
        )
    ] + [("transaction", transaction) for transaction in recognized_transactions]
    timeline.sort(
        key=lambda item: (
            _corporate_action_sort_key(item[1])
            if item[0] == "corporate_action"
            else _transaction_timeline_sort_key(item[1], position_recognition=True)
        )
    )

    for item_kind, transaction in timeline:
        if item_kind == "corporate_action":
            position_reference_id = str(transaction.get("instrument_id") or "")
            state = states.get(position_reference_id)
            if state is None:
                continue
            quantities = state.get("quantities_by_account")
            if not isinstance(quantities, dict):
                continue
            for account_id, raw_quantity in list(quantities.items()):
                quantities[account_id] = _rounded_split_quantity(
                    _safe_float(raw_quantity) or 0.0,
                    transaction,
                )
            state["quantity"] = sum(
                _safe_float(raw_quantity) or 0.0
                for raw_quantity in quantities.values()
            )
            continue

        if _derivative_contract(transaction) is not None:
            continue
        position_reference_id = _position_reference_id(transaction)
        if not position_reference_id:
            continue
        transaction_type = str(transaction.get("transaction_type") or "")
        account_id = str(transaction.get("account_id") or "")
        quantity = _safe_float(transaction.get("quantity")) or 0.0
        gross_amount = _safe_float(transaction.get("gross_amount")) or 0.0
        fees = _safe_float(transaction.get("fees")) or 0.0
        taxes = _safe_float(transaction.get("taxes")) or 0.0
        currency = valuation_fx.normalized_currency(transaction.get("currency"))
        state = states.setdefault(
            position_reference_id,
            {
                "quantity": 0.0,
                "net_invested": 0.0,
                "currency": currency,
                "coverage_complete": True,
                "quantities_by_account": {},
            },
        )
        current_quantity = _safe_float(state.get("quantity")) or 0.0
        quantities = state.get("quantities_by_account")
        if not isinstance(quantities, dict):
            state["coverage_complete"] = False
            continue

        if (
            transaction_type in {"transfer_in", "transfer_out"}
            and transaction.get("transfer_object_type") == "position"
            and quantity > 0
        ):
            account_quantity = _safe_float(quantities.get(account_id)) or 0.0
            quantities[account_id] = max(
                account_quantity
                + (quantity if transaction_type == "transfer_in" else -quantity),
                0.0,
            )
            state["quantity"] = sum(
                _safe_float(raw_quantity) or 0.0
                for raw_quantity in quantities.values()
            )
            continue

        if transaction_type in {"opening_balance", "buy"} and quantity > 0:
            if current_quantity <= 1e-9:
                state.update(
                    {
                        "quantity": 0.0,
                        "net_invested": 0.0,
                        "currency": currency,
                        "coverage_complete": True,
                        "quantities_by_account": {},
                    }
                )
                current_quantity = 0.0
                quantities = state["quantities_by_account"]
            if currency != state.get("currency"):
                state["coverage_complete"] = False
            invested_amount = (
                gross_amount
                if transaction_type == "opening_balance"
                else gross_amount + fees + taxes
            )
            quantities[account_id] = (
                (_safe_float(quantities.get(account_id)) or 0.0) + quantity
            )
            state["quantity"] = current_quantity + quantity
            state["net_invested"] = (
                (_safe_float(state.get("net_invested")) or 0.0)
                + invested_amount
            )
            continue

        if transaction_type == "dividend_reinvestment" and quantity > 0:
            if transaction_position_effective_date(transaction) > as_of_date:
                continue
            if current_quantity <= 1e-9:
                state["coverage_complete"] = False
            if currency != state.get("currency"):
                state["coverage_complete"] = False
            quantities[account_id] = (
                (_safe_float(quantities.get(account_id)) or 0.0) + quantity
            )
            state["quantity"] = current_quantity + quantity
            # A withheld performance fee reduces the reinvested distribution;
            # it is not additional external capital invested in this position.
            continue

        if transaction_type in {"sell", "maturity_redemption"} and quantity > 0:
            quantities[account_id] = max(
                (_safe_float(quantities.get(account_id)) or 0.0) - quantity,
                0.0,
            )
            state["quantity"] = sum(
                _safe_float(raw_quantity) or 0.0
                for raw_quantity in quantities.values()
            )
            state["net_invested"] = (
                (_safe_float(state.get("net_invested")) or 0.0)
                - (gross_amount - fees - taxes)
            )
            continue

        if transaction_type in {"dividend", "coupon", "return_of_capital"}:
            if current_quantity > 1e-9:
                state["net_invested"] = (
                    (_safe_float(state.get("net_invested")) or 0.0)
                    - (gross_amount - fees - taxes)
                )
            continue

        if transaction_type in {"fee", "tax"} and current_quantity > 1e-9:
            state["net_invested"] = (
                (_safe_float(state.get("net_invested")) or 0.0) + gross_amount
            )

    return {
        position_reference_id: {
            key: value
            for key, value in state.items()
            if key != "quantities_by_account"
        }
        for position_reference_id, state in states.items()
        if (_safe_float(state.get("quantity")) or 0.0) > 1e-9
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
        last_price, market_value, event_valued = resolve_position_valuation(
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
                "valuation_basis": (
                    "carried_cost" if event_valued else "transaction_price" if isinstance(pricing_quote, dict)
                    and pricing_quote.get("status") == "transaction-price" else "market_quote"
                ),
                "valuation_source_transaction_ids": (
                    pricing_quote.get("valuation_source_transaction_ids", [])
                    if isinstance(pricing_quote, dict) else []
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
    as_of_date: date,
    selected_account_id: str | None = None,
    base_currency: str = "USD",
    instrument_detail_cache: dict[str, dict[str, object] | None] | None = None,
    direct_fx_instruments: valuation_fx.FxInstrumentMap | None = None,
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
    resolved_instrument_detail_cache = (
        instrument_detail_cache
        if instrument_detail_cache is not None
        else valuation_fx.HistoricalInstrumentDetails(end_date=as_of_date)
    )
    if direct_fx_instruments is None:
        direct_fx_instruments = valuation_fx.HistoricalFxInstruments(
            resolved_instrument_detail_cache,
            detail_loader=get_registry_instrument_detail,
        )

    fx_resolution_cache: valuation_fx.FxRateResolutionCache = {}
    resolve_account_fx = partial(
        valuation_fx.resolve_fx_rate_on_cached,
        instrument_detail_loader=get_registry_instrument_detail,
        resolution_cache=fx_resolution_cache,
    )

    def convert_to_base(amount: float | None, *, from_currency: str) -> float | None:
        normalized_currency = str(from_currency or "").strip().upper()
        converted_amount, _ = valuation_fx.convert_amount_on(
            amount,
            as_of_date=as_of_date,
            from_currency=normalized_currency,
            to_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=resolved_instrument_detail_cache,
            instrument_detail_loader=get_registry_instrument_detail,
            resolution_cache=fx_resolution_cache,
        )
        return converted_amount

    linked_transaction_ids: dict[str, set[str]] = defaultdict(set)
    linked_posting_count: dict[str, int] = defaultdict(int)

    for posting in postings:
        account_id = str(posting.get("account_id") or "")
        linked_posting_count[account_id] += 1
        linked_transaction_ids[account_id].add(str(posting.get("transaction_id") or ""))

    monetary_subledger = build_monetary_subledger(
        resolve_fx_rate_on=resolve_account_fx,
        fx_resolution_cache=fx_resolution_cache,
        postings=postings,
        as_of_date=as_of_date,
        base_currency=base_currency,
        direct_fx_instruments=direct_fx_instruments,
        instrument_detail_cache=resolved_instrument_detail_cache,
    )
    settled_balances_by_account: dict[str, list[dict[str, object]]] = defaultdict(list)
    pending_balances_by_account: dict[str, list[dict[str, object]]] = defaultdict(list)
    for balance in monetary_subledger["settled_balances"]:
        settled_balances_by_account[str(balance.get("account_id") or "")].append(
            balance
        )
    for balance in monetary_subledger["pending_balances"]:
        pending_balances_by_account[str(balance.get("account_id") or "")].append(
            balance
        )

    # Written options are liabilities, not negative long positions.  Rebuild
    # the obligation read model at the same boundary as cash and lots so the
    # account NAV cannot recognize the received premium twice.
    option_obligation_rows = build_option_obligations(
        boundary_transactions,
        as_of_date=as_of_date,
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
        instrument_detail_cache=(resolved_instrument_detail_cache if instrument_detail_cache is not None else None),
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
                "fair_value": None if event_valued or (isinstance(pricing_quote, dict) and pricing_quote.get("status") == "transaction-price") else market_value,
                "fair_value_coverage_status": (
                    "unavailable"
                    if event_valued or market_value is None
                    else "partial" if isinstance(pricing_quote, dict) and pricing_quote.get("status") == "transaction-price"
                    else "complete"
                ),
                "valuation_basis": (
                    "carried_cost" if event_valued else "transaction_price"
                    if isinstance(pricing_quote, dict) and pricing_quote.get("status") == "transaction-price"
                    else "market_quote"
                ),
                "valuation_source_transaction_ids": (
                    pricing_quote.get("valuation_source_transaction_ids", [])
                    if isinstance(pricing_quote, dict) else []
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

    def complete_balance_sum(
        rows: list[dict[str, object]],
        field: str,
    ) -> float | None:
        values = [_safe_float(row.get(field)) for row in rows]
        if any(value is None for value in values):
            return None
        return sum(value for value in values if value is not None)

    def monetary_fx_coverage(
        rows: list[dict[str, object]],
    ) -> str:
        statuses = {
            str(row.get("cost_basis_fx_coverage_status") or "unavailable")
            for row in rows
        }
        if "unavailable" in statuses:
            return "unavailable"
        if "stale" in statuses:
            return "stale"
        return "complete"

    account_rows: list[dict[str, object]] = []
    for account in accounts:
        account_id = str(account["account_id"])
        settlement_name = None
        settlement_id = account.get("default_settlement_cash_account_id")
        if isinstance(settlement_id, str):
            settlement_name = str(account_lookup.get(settlement_id, {}).get("account_name") or "")
        settled_balances = settled_balances_by_account[account_id]
        pending_balances = pending_balances_by_account[account_id]
        derived_cash_balance = complete_balance_sum(settled_balances, "amount") or 0.0
        derived_cash_balance_base = complete_balance_sum(
            settled_balances,
            "amount_base",
        )
        if derived_cash_balance_base is None:
            valuation_missing_components_by_account[account_id].add("cash_fx")
        settled_cash_cost_basis_base = complete_balance_sum(
            settled_balances,
            "cost_basis_historical_base",
        )
        settled_cash_unrealized_fx_pnl_base = complete_balance_sum(
            settled_balances,
            "unrealized_fx_pnl_base",
        )
        pending_settlement_amount = complete_balance_sum(
            pending_balances,
            "amount",
        ) or 0.0
        pending_settlement_base = complete_balance_sum(
            pending_balances,
            "amount_base",
        )
        if pending_settlement_base is None:
            valuation_missing_components_by_account[account_id].add("pending_settlement_fx")
        pending_settlement_cost_basis_base = complete_balance_sum(
            pending_balances,
            "cost_basis_historical_base",
        )
        pending_settlement_unrealized_fx_pnl_base = complete_balance_sum(
            pending_balances,
            "unrealized_fx_pnl_base",
        )
        monetary_unrealized_fx_pnl_base = (
            settled_cash_unrealized_fx_pnl_base
            + pending_settlement_unrealized_fx_pnl_base
            if settled_cash_unrealized_fx_pnl_base is not None
            and pending_settlement_unrealized_fx_pnl_base is not None
            else None
        )
        monetary_coverage_status = monetary_fx_coverage(
            settled_balances + pending_balances
        )
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
                "settled_cash_cost_basis_base": settled_cash_cost_basis_base,
                "settled_cash_unrealized_fx_pnl_base": (
                    settled_cash_unrealized_fx_pnl_base
                ),
                "pending_settlement": pending_settlement_amount,
                "pending_settlement_base": pending_settlement_base,
                "pending_settlement_cost_basis_base": (
                    pending_settlement_cost_basis_base
                ),
                "pending_settlement_unrealized_fx_pnl_base": (
                    pending_settlement_unrealized_fx_pnl_base
                ),
                "monetary_unrealized_fx_pnl_base": (
                    monetary_unrealized_fx_pnl_base
                ),
                "monetary_fx_coverage_status": monetary_coverage_status,
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
