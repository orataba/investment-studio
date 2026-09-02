from __future__ import annotations

from datetime import date

from portfolio_app.services.execution_quotes import build_execution_quote_from_detail
from portfolio_app.services.instrument_registry import get_registry_instrument_details


def _number(value: object) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _contract(row: dict[str, object]) -> dict[str, object] | None:
    value = row.get("derivative_contract")
    return value if isinstance(value, dict) else None


def _terms(contract: dict[str, object]) -> dict[str, object]:
    value = contract.get("terms")
    return value if isinstance(value, dict) else {}


def _instrument_core(row: dict[str, object]) -> dict[str, object]:
    value = row.get("instrument_core")
    return value if isinstance(value, dict) else {}


def _underlying_ids(rows: list[dict[str, object]]) -> list[str]:
    instrument_ids: set[str] = set()
    for row in rows:
        contract = _contract(row)
        if contract is None:
            continue
        terms = _terms(contract)
        if str(contract.get("contract_type") or "") == "option":
            instrument_id = str(terms.get("underlying_instrument_id") or "").strip()
            if instrument_id:
                instrument_ids.add(instrument_id)
        elif str(contract.get("contract_type") or "") == "fcn":
            underlyings = terms.get("underlyings")
            if not isinstance(underlyings, list):
                continue
            instrument_ids.update(
                str(item.get("instrument_id") or "").strip()
                for item in underlyings
                if isinstance(item, dict)
                and str(item.get("instrument_id") or "").strip()
            )
    return sorted(instrument_ids)


def _quote_by_instrument(
    instrument_ids: list[str],
    *,
    as_of_date: date,
) -> tuple[dict[str, dict[str, object] | None], dict[str, dict[str, object]]]:
    details = get_registry_instrument_details(instrument_ids) if instrument_ids else {}
    quotes: dict[str, dict[str, object]] = {}
    for instrument_id in instrument_ids:
        detail = details.get(instrument_id)
        if not isinstance(detail, dict):
            continue
        quotes[instrument_id] = build_execution_quote_from_detail(
            detail,
            instrument_id=instrument_id,
            as_of_date=as_of_date,
        )
    return details, quotes


def _fcn_lifecycle_by_contract(
    transactions: list[dict[str, object]],
    *,
    as_of_date: date,
) -> dict[str, str]:
    lifecycle_events = {
        "fcn_knock_in": (0, "knocked_in"),
        "fcn_knock_out": (1, "knocked_out"),
        "fcn_maturity": (2, "matured"),
    }
    latest_by_contract: dict[str, tuple[str, int, str]] = {}
    for transaction in transactions:
        trade_date = str(transaction.get("trade_date") or "")[:10]
        if not trade_date or trade_date > as_of_date.isoformat():
            continue
        contract_id = str(transaction.get("derivative_contract_id") or "").strip()
        event_type = str(transaction.get("lifecycle_event_type") or "").strip()
        event = lifecycle_events.get(event_type)
        if not contract_id or event is None:
            continue
        priority, state = event
        candidate = (trade_date, priority, state)
        current = latest_by_contract.get(contract_id)
        if current is None or candidate[:2] > current[:2]:
            latest_by_contract[contract_id] = candidate
    return {
        contract_id: event[2]
        for contract_id, event in latest_by_contract.items()
    }


def _fcn_underlying_risk(
    item: dict[str, object],
    *,
    detail: dict[str, object] | None,
    quote: dict[str, object] | None,
) -> dict[str, object]:
    instrument_id = str(item.get("instrument_id") or "").strip()
    initial = _number(item.get("initial_reference_price"))
    strike_level = _number(item.get("strike_level_pct"))
    knock_in_level = _number(item.get("knock_in_level_pct"))
    knock_out_level = _number(item.get("knock_out_level_pct"))
    spot = _number((quote or {}).get("value"))

    def level_price(level: float | None) -> float | None:
        return initial * level / 100 if initial is not None and level is not None else None

    strike_price = level_price(strike_level)
    knock_in_price = level_price(knock_in_level)
    knock_out_price = level_price(knock_out_level)

    def distance(level: float | None) -> float | None:
        return spot / level - 1 if spot is not None and level is not None and level > 0 else None

    if spot is None:
        current_region = "quote_unavailable"
    elif knock_in_price is not None and spot <= knock_in_price:
        current_region = "at_or_below_knock_in"
    elif strike_price is not None and spot < strike_price:
        current_region = "below_strike"
    elif knock_out_price is not None and spot >= knock_out_price:
        current_region = "at_or_above_knock_out"
    elif strike_price is not None and knock_out_price is not None:
        current_region = "between_strike_and_knock_out"
    elif strike_price is not None:
        current_region = "at_or_above_strike"
    elif knock_out_price is not None:
        current_region = "below_knock_out"
    else:
        current_region = "levels_unavailable"

    return {
        "instrument_id": instrument_id,
        "instrument_name": str((detail or {}).get("instrument_name") or instrument_id),
        "currency": str((detail or {}).get("currency") or (quote or {}).get("currency") or ""),
        "spot": spot,
        "quote_as_of_date": (quote or {}).get("quote_date"),
        "quote_status": (quote or {}).get("status") or "unavailable",
        "initial_reference_price": initial,
        "strike_price": strike_price,
        "knock_in_price": knock_in_price,
        "knock_out_price": knock_out_price,
        "performance_to_reference_pct": (
            spot / initial - 1
            if spot is not None and initial is not None and initial > 0
            else None
        ),
        "distance_to_strike_pct": distance(strike_price),
        "distance_to_knock_in_pct": distance(knock_in_price),
        "distance_to_knock_out_pct": distance(knock_out_price),
        "current_region": current_region,
        "missing_terms": [
            label
            for label, value in (
                ("initial_reference_price", initial),
                ("strike_level_pct", strike_level),
                ("knock_in_level_pct", knock_in_level),
                ("knock_out_level_pct", knock_out_level),
            )
            if value is None
        ],
    }


def enrich_derivative_holding_risk(
    rows: list[dict[str, object]],
    *,
    transactions: list[dict[str, object]],
    as_of_date: date,
) -> None:
    underlying_ids = _underlying_ids(rows)
    details, quotes = _quote_by_instrument(underlying_ids, as_of_date=as_of_date)

    security_quantity: dict[str, float] = {}
    settled_cash: dict[str, float] = {}
    for row in rows:
        holding_kind = str(row.get("holding_kind") or "position")
        instrument = _instrument_core(row)
        instrument_id = str(instrument.get("instrument_id") or "").strip()
        if holding_kind == "position" and instrument_id:
            security_quantity[instrument_id] = (
                security_quantity.get(instrument_id, 0.0)
                + max(_number(row.get("quantity")) or 0.0, 0.0)
            )
        if holding_kind == "settled_cash":
            currency = str(instrument.get("currency") or "").strip().upper()
            amount = _number(row.get("market_value"))
            if currency and amount is not None:
                settled_cash[currency] = settled_cash.get(currency, 0.0) + amount

    written_call_required: dict[str, float] = {}
    written_put_required: dict[str, float] = {}
    for row in rows:
        if str(row.get("holding_kind") or "") != "option_obligation":
            continue
        contract = _contract(row)
        if contract is None or str(contract.get("contract_type") or "") != "option":
            continue
        terms = _terms(contract)
        option_type = str(terms.get("option_type") or "")
        underlying_id = str(terms.get("underlying_instrument_id") or "").strip()
        required_quantity = _number(row.get("required_underlying_quantity")) or 0.0
        if option_type == "call" and underlying_id:
            written_call_required[underlying_id] = (
                written_call_required.get(underlying_id, 0.0) + required_quantity
            )
        elif option_type == "put":
            currency = str(contract.get("currency") or "").strip().upper()
            strike_notional = _number(row.get("strike_notional")) or 0.0
            if currency:
                written_put_required[currency] = (
                    written_put_required.get(currency, 0.0) + strike_notional
                )

    fcn_lifecycle = _fcn_lifecycle_by_contract(
        transactions,
        as_of_date=as_of_date,
    )
    for row in rows:
        contract = _contract(row)
        if contract is None:
            continue
        contract_type = str(contract.get("contract_type") or "")
        terms = _terms(contract)
        if contract_type == "option":
            underlying_id = str(terms.get("underlying_instrument_id") or "").strip()
            detail = details.get(underlying_id)
            quote = quotes.get(underlying_id)
            spot = _number((quote or {}).get("value"))
            strike = _number(terms.get("strike"))
            option_type = str(terms.get("option_type") or "")
            is_written = str(row.get("holding_kind") or "") == "option_obligation"
            if option_type == "call":
                moneyness = (
                    spot / strike - 1
                    if spot is not None and strike is not None and strike > 0
                    else None
                )
            else:
                moneyness = (
                    (strike - spot) / strike
                    if spot is not None and strike is not None and strike > 0
                    else None
                )
            backing: dict[str, object] | None = None
            if is_written and option_type == "call":
                required = written_call_required.get(underlying_id, 0.0)
                available = security_quantity.get(underlying_id, 0.0)
                backing = {
                    "kind": "portfolio_underlying_shares",
                    "available": available,
                    "required": required,
                    "ratio": min(available / required, 1.0) if required > 0 else None,
                    "shortfall": max(required - available, 0.0),
                    "currency": None,
                }
            elif is_written and option_type == "put":
                currency = str(contract.get("currency") or "").strip().upper()
                required = written_put_required.get(currency, 0.0)
                available = max(settled_cash.get(currency, 0.0), 0.0)
                backing = {
                    "kind": "portfolio_settled_cash",
                    "available": available,
                    "required": required,
                    "ratio": min(available / required, 1.0) if required > 0 else None,
                    "shortfall": max(required - available, 0.0),
                    "currency": currency,
                }
            expiry_date = str(terms.get("expiry_date") or "")[:10]
            days_to_expiry = (
                (date.fromisoformat(expiry_date) - as_of_date).days
                if expiry_date
                else None
            )
            if days_to_expiry is not None and days_to_expiry < 0:
                risk_state = "expired_unresolved"
            elif backing is not None and (_number(backing.get("shortfall")) or 0.0) > 0:
                risk_state = "uncovered"
            elif spot is None:
                risk_state = "quote_unavailable"
            elif moneyness is not None and moneyness > 0:
                risk_state = "in_the_money"
            else:
                risk_state = "open"
            row["option_risk"] = {
                "underlying_instrument_id": underlying_id,
                "underlying_name": str((detail or {}).get("instrument_name") or underlying_id),
                "underlying_quote_currency": str((detail or {}).get("currency") or (quote or {}).get("currency") or ""),
                "underlying_spot": spot,
                "underlying_quote_as_of_date": (quote or {}).get("quote_date"),
                "underlying_quote_status": (quote or {}).get("status") or "unavailable",
                "moneyness_pct": moneyness,
                "intrinsic_value_per_share": (
                    max(spot - strike, 0.0)
                    if option_type == "call" and spot is not None and strike is not None
                    else max(strike - spot, 0.0)
                    if option_type == "put" and spot is not None and strike is not None
                    else None
                ),
                "max_loss_local": (
                    None
                    if is_written
                    else _number(row.get("cost_basis"))
                ),
                "days_to_expiry": days_to_expiry,
                "risk_state": risk_state,
                "backing": backing,
            }
            continue

        if contract_type != "fcn":
            continue
        underlyings = terms.get("underlyings")
        risk_items = [
            _fcn_underlying_risk(
                item,
                detail=(
                    details.get(str(item.get("instrument_id") or ""))
                    if isinstance(details.get(str(item.get("instrument_id") or "")), dict)
                    else None
                ),
                quote=quotes.get(str(item.get("instrument_id") or "")),
            )
            for item in underlyings
            if isinstance(item, dict)
        ] if isinstance(underlyings, list) else []
        complete_performance = [
            item
            for item in risk_items
            if _number(item.get("performance_to_reference_pct")) is not None
        ]
        worst = min(
            complete_performance,
            key=lambda item: float(item["performance_to_reference_pct"]),
            default=None,
        )
        lifecycle_status = fcn_lifecycle.get(
            str(contract.get("derivative_contract_id") or ""),
            "open",
        )
        if lifecycle_status != "open":
            risk_state = lifecycle_status
        elif any(item.get("current_region") == "at_or_below_knock_in" for item in risk_items):
            risk_state = "current_price_at_or_below_knock_in"
        elif any(item.get("missing_terms") for item in risk_items):
            risk_state = "terms_incomplete"
        elif any(item.get("quote_status") != "complete" for item in risk_items):
            risk_state = "quote_unavailable"
        else:
            risk_state = "open"
        row["fcn_risk"] = {
            "lifecycle_status": lifecycle_status,
            "risk_state": risk_state,
            "worst_underlying_instrument_id": (
                str(worst.get("instrument_id") or "") if worst else None
            ),
            "underlyings": risk_items,
        }
