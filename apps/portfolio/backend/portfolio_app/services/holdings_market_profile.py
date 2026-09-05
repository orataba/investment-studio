from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import date, datetime

from investment_studio_instrument_core import InstrumentCore as SharedInstrumentCore

from portfolio_app.services import valuation_fx


_SUPPORTED_INSTRUMENT_TYPES = {
    "public_fund",
    "private_fund",
    "etf",
    "equity",
    "cash",
    "fx",
    "other",
}
_FORBIDDEN_INSTRUMENT_REF_KEYS = {"asset_id", "asset_name", "asset_type"}

SafeFloat = Callable[[object], float | None]
NormalizeCurrency = Callable[[object], str]
ParseIsoDate = Callable[[object], date | None]
PositionMarketValue = Callable[..., float | None]
ResolveFxRate = Callable[..., dict[str, object] | None]
DayChangeMetrics = Callable[..., dict[str, object]]


def _required_currency(
    value: object,
    *,
    normalize_currency: NormalizeCurrency,
    field_name: str,
) -> str:
    normalized = normalize_currency(value)
    if not normalized:
        raise ValueError(f"{field_name} is required.")
    return normalized


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_iso_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        try:
            return datetime.fromisoformat(normalized.replace("Z", "+00:00")).date()
        except ValueError:
            return None


def summarize_holding_day_change(
    rows: list[dict[str, object]],
    *,
    total_market_value_base: object,
    safe_float: SafeFloat = _safe_float,
) -> dict[str, float | None]:
    total_day_change_base = 0.0
    for row in rows:
        day_change_value = safe_float(row.get("day_change_value_base"))
        if day_change_value is None:
            return {"day_change_value": None, "day_change_pct": None}
        total_day_change_base += day_change_value

    market_value_base = safe_float(total_market_value_base)
    prior_market_value_base = (
        market_value_base - total_day_change_base if market_value_base is not None else None
    )
    day_change_pct = (
        total_day_change_base / prior_market_value_base
        if prior_market_value_base is not None and abs(prior_market_value_base) > 1e-12
        else None
    )
    return {
        "day_change_value": total_day_change_base,
        "day_change_pct": day_change_pct,
    }


def normalize_instrument_core(
    instrument_id: str,
    instrument_ref: dict[str, object] | None,
) -> dict[str, object]:
    if not isinstance(instrument_ref, dict):
        raise ValueError(f"Instrument reference is required for '{instrument_id}'.")
    forbidden_keys = sorted(
        key for key in _FORBIDDEN_INSTRUMENT_REF_KEYS if key in instrument_ref
    )
    if forbidden_keys:
        raise ValueError(
            f"Instrument reference for '{instrument_id}' contains non-canonical fields: "
            f"{', '.join(forbidden_keys)}"
        )

    resolved_id = str(instrument_ref.get("instrument_id") or "").strip()
    if not resolved_id or resolved_id != str(instrument_id or "").strip():
        raise ValueError(f"Instrument reference id must match '{instrument_id}'.")
    resolved_type = str(instrument_ref.get("instrument_type") or "").strip().lower()
    if resolved_type not in _SUPPORTED_INSTRUMENT_TYPES:
        raise ValueError(
            f"Instrument reference for '{instrument_id}' has unsupported instrument_type."
        )
    instrument_name = str(instrument_ref.get("instrument_name") or "").strip()
    currency = str(instrument_ref.get("currency") or "").strip().upper()
    if not instrument_name or not currency:
        raise ValueError(f"Instrument reference for '{instrument_id}' is incomplete.")
    payload = {
        "instrument_id": resolved_id,
        "instrument_name": instrument_name,
        "instrument_type": resolved_type,
        "currency": currency,
        "exchange_code": instrument_ref.get("exchange_code"),
        "identifiers": deepcopy(instrument_ref.get("identifiers") or []),
        "broker_identifiers": deepcopy(
            instrument_ref.get("broker_identifiers") or []
        ),
    }
    try:
        return SharedInstrumentCore.model_validate(payload).model_dump(
            mode="json",
            exclude_none=True,
        )
    except ValueError as error:
        raise ValueError(
            f"Instrument reference for '{instrument_id}' violates the canonical contract: {error}"
        ) from error


def is_derivative_contract(value: object) -> bool:
    return (
        isinstance(value, dict)
        and str(value.get("contract_type") or "").strip().lower()
        in {"fcn", "option"}
    )


def option_contract_exposure_fields(
    *,
    derivative_contract: dict[str, object] | None,
    quantity: float,
    as_of_date: date,
    base_currency: str,
    convert_amount_on: Callable[..., tuple[float | None, bool]],
    direct_fx_instruments: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
    instrument_detail_cache_get: Callable[..., dict[str, object] | None],
    safe_float: SafeFloat = _safe_float,
) -> dict[str, object]:
    """Project contract exposure for an open long-option position."""

    if (
        not isinstance(derivative_contract, dict)
        or str(derivative_contract.get("contract_type") or "").strip().lower()
        != "option"
    ):
        return {}
    raw_terms = derivative_contract.get("terms")
    terms = raw_terms if isinstance(raw_terms, dict) else {}
    open_contract_quantity = abs(quantity)
    multiplier = safe_float(terms.get("contract_multiplier"))
    required_underlying_quantity = (
        open_contract_quantity * multiplier if multiplier is not None else None
    )
    strike = safe_float(terms.get("strike"))
    strike_notional = (
        strike * required_underlying_quantity
        if strike is not None and required_underlying_quantity is not None
        else None
    )
    underlying_id = str(terms.get("underlying_instrument_id") or "").strip()
    underlying = instrument_detail_cache_get(underlying_id, instrument_detail_cache)
    currency = str((underlying or {}).get("currency") or "").strip().upper()
    strike_notional_base, _ = (
        convert_amount_on(
            strike_notional,
            as_of_date=as_of_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
        )
        if strike_notional is not None and currency
        else (None, False)
    )
    expiry_date = _parse_iso_date(terms.get("expiry_date"))
    return {
        "open_contract_quantity": open_contract_quantity,
        "required_underlying_quantity": required_underlying_quantity,
        "related_underlying_id": (
            str(terms.get("underlying_instrument_id") or "").strip() or None
        ),
        "expiry_date": expiry_date.isoformat() if expiry_date is not None else None,
        "days_to_expiry": (
            (expiry_date - as_of_date).days if expiry_date is not None else None
        ),
        "strike": strike,
        "strike_currency": currency or None,
        "option_type": str(terms.get("option_type") or "").strip().lower() or None,
        "contract_multiplier": multiplier,
        "strike_notional": strike_notional,
        "strike_notional_base": strike_notional_base,
    }


def resolve_position_valuation(
    *,
    quantity: float,
    cost_basis: float | None,
    instrument_ref: dict[str, object] | None,
    derivative_contract: dict[str, object] | None = None,
    quoted_price: float | None,
    quoted_price_scale: float | None = None,
    position_market_value: PositionMarketValue = valuation_fx.position_market_value,
) -> tuple[float | None, float | None, bool]:
    """Return display price, local market value, and event-valued status.

    Portfolio-local FCNs and options are carried at remaining transaction cost
    between lifecycle events. The derived per-unit value is a display aid, not
    an observed market quote.
    """

    if derivative_contract is not None:
        if cost_basis is None:
            return None, None, True
        carrying_value = float(cost_basis)
        carrying_price = (
            carrying_value / quantity if abs(quantity) > 1e-12 else None
        )
        return carrying_price, carrying_value, True

    return (
        quoted_price,
        position_market_value(
            quantity=quantity,
            last_price=quoted_price,
            price_scale=quoted_price_scale,
        ),
        False,
    )


def cash_holding_instrument_id(
    currency: str,
    *,
    normalize_currency: NormalizeCurrency = valuation_fx.normalized_currency,
) -> str:
    normalized = _required_currency(
        currency,
        normalize_currency=normalize_currency,
        field_name="cash currency",
    )
    return f"cash:{normalized}"


def is_cash_holding_instrument_id(instrument_id: object) -> bool:
    return str(instrument_id or "").strip().lower().startswith("cash:")


def is_pending_monetary_holding_instrument_id(instrument_id: object) -> bool:
    return str(instrument_id or "").strip().lower().startswith("pending:")


def is_pending_monetary_holding(row: dict[str, object]) -> bool:
    holding_kind = str(row.get("holding_kind") or "").strip().lower()
    return holding_kind.startswith("pending_") or is_pending_monetary_holding_instrument_id(
        row.get("instrument_id") or row.get("line_id")
    )


def is_market_priced_holding(row: dict[str, object]) -> bool:
    """Return whether a holding is backed by a complete market quote.

    Carrying values and premium-basis liabilities populate operational NAV
    amount fields. They are not priced lines merely because those amounts exist.
    """

    return bool(
        str(row.get("holding_kind") or "position") == "position"
        and str(row.get("valuation_basis") or "") == "market_quote"
        and str(row.get("fair_value_coverage_status") or "") == "complete"
        and row.get("last_price") is not None
    )


def cash_holding_instrument_ref(
    currency: str,
    *,
    normalize_currency: NormalizeCurrency = valuation_fx.normalized_currency,
    cash_instrument_id: Callable[[str], str] = cash_holding_instrument_id,
) -> dict[str, object]:
    normalized_currency = _required_currency(
        currency,
        normalize_currency=normalize_currency,
        field_name="cash currency",
    )
    instrument_id = cash_instrument_id(normalized_currency)
    return {
        "instrument_id": instrument_id,
        "instrument_name": f"Cash ({normalized_currency})",
        "instrument_type": "cash",
        "currency": normalized_currency,
        "identifiers": [
            {
                "identifier_type": "cash_currency",
                "identifier_value": normalized_currency,
                "is_primary": True,
            }
        ],
    }


def holding_day_change_metrics(
    *,
    quantity: float,
    current_price: float | None,
    previous_price: float | None,
    instrument_ref: dict[str, object] | None,
    current_return_price: float | None = None,
    previous_return_price: float | None = None,
    price_scale: float | None = None,
    position_market_value: PositionMarketValue = valuation_fx.position_market_value,
) -> tuple[float | None, float | None]:
    has_complete_total_return_pair = (
        current_return_price is not None and previous_return_price is not None
    )
    resolved_current_return_price = (
        current_return_price if has_complete_total_return_pair else current_price
    )
    resolved_previous_return_price = (
        previous_return_price if has_complete_total_return_pair else previous_price
    )
    if (
        current_price is None
        or resolved_current_return_price is None
        or resolved_previous_return_price is None
        or abs(resolved_previous_return_price) <= 1e-12
    ):
        return None, None
    day_change_pct = resolved_current_return_price / resolved_previous_return_price - 1.0
    current_market_value = position_market_value(
        quantity=quantity,
        last_price=current_price,
        price_scale=price_scale,
    )
    previous_market_value = (
        current_market_value / (1.0 + day_change_pct)
        if current_market_value is not None and abs(1.0 + day_change_pct) > 1e-12
        else None
    )
    day_change_value = (
        current_market_value - previous_market_value
        if current_market_value is not None and previous_market_value is not None
        else None
    )
    return day_change_pct, day_change_value


def cash_day_change_metrics(
    *,
    amount: float,
    currency: str,
    base_currency: str,
    as_of_date: date,
    previous_as_of_date: date | None,
    direct_fx_instruments: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
    resolve_fx_rate_on: ResolveFxRate,
    normalize_currency: NormalizeCurrency = valuation_fx.normalized_currency,
    safe_float: SafeFloat = _safe_float,
) -> dict[str, object]:
    normalized_currency = _required_currency(
        currency,
        normalize_currency=normalize_currency,
        field_name="cash currency",
    )
    normalized_base = _required_currency(
        base_currency,
        normalize_currency=normalize_currency,
        field_name="portfolio base currency",
    )
    return translated_day_change_metrics(
        current_local_value=amount,
        previous_local_value=amount,
        local_day_change_pct=0.0,
        local_day_change_value=0.0,
        currency=normalized_currency,
        base_currency=normalized_base,
        as_of_date=as_of_date,
        previous_as_of_date=previous_as_of_date,
        direct_fx_instruments=direct_fx_instruments,
        instrument_detail_cache=instrument_detail_cache,
        resolve_fx_rate_on=resolve_fx_rate_on,
        normalize_currency=normalize_currency,
        safe_float=safe_float,
    )


def translated_day_change_metrics(
    *,
    current_local_value: float,
    previous_local_value: float,
    local_day_change_pct: float,
    local_day_change_value: float,
    currency: str,
    base_currency: str,
    as_of_date: date,
    previous_as_of_date: date | None,
    direct_fx_instruments: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
    resolve_fx_rate_on: ResolveFxRate,
    normalize_currency: NormalizeCurrency = valuation_fx.normalized_currency,
    safe_float: SafeFloat = _safe_float,
) -> dict[str, object]:
    normalized_currency = _required_currency(
        currency,
        normalize_currency=normalize_currency,
        field_name="holding currency",
    )
    normalized_base = _required_currency(
        base_currency,
        normalize_currency=normalize_currency,
        field_name="portfolio base currency",
    )

    if normalized_currency == normalized_base:
        current_fx: dict[str, object] | None = {
            "rate": 1.0,
            "as_of_date": as_of_date.isoformat(),
            "source_instrument_ids": [],
            "stale": False,
        }
        previous_fx: dict[str, object] | None = (
            {
                "rate": 1.0,
                "as_of_date": previous_as_of_date.isoformat(),
                "source_instrument_ids": [],
                "stale": False,
            }
            if previous_as_of_date is not None
            else None
        )
    else:
        current_fx = resolve_fx_rate_on(
            as_of_date=as_of_date,
            base_currency=normalized_currency,
            quote_currency=normalized_base,
            direct_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
        )
        previous_fx = (
            resolve_fx_rate_on(
                as_of_date=previous_as_of_date,
                base_currency=normalized_currency,
                quote_currency=normalized_base,
                direct_instruments=direct_fx_instruments,
                instrument_detail_cache=instrument_detail_cache,
            )
            if previous_as_of_date is not None
            else None
        )

    current_rate = safe_float((current_fx or {}).get("rate"))
    previous_rate = safe_float((previous_fx or {}).get("rate"))
    current_rate_date = (current_fx or {}).get("as_of_date")
    previous_rate_date = (previous_fx or {}).get("as_of_date")
    source_instrument_ids = sorted(
        {
            str(instrument_id)
            for resolved in (current_fx, previous_fx)
            for instrument_id in list((resolved or {}).get("source_instrument_ids") or [])
            if str(instrument_id or "")
        }
    )
    rate_stale = bool((current_fx or {}).get("stale"))
    metadata = {
        "fx_rate_to_base": current_rate,
        "fx_rate_as_of_date": str(current_rate_date) if current_rate_date else None,
        "previous_fx_rate_to_base": previous_rate,
        "previous_fx_rate_as_of_date": (
            str(previous_rate_date) if previous_rate_date else None
        ),
        "fx_rate_source_instrument_ids": source_instrument_ids,
        "fx_rate_stale": rate_stale,
    }
    if (
        previous_as_of_date is None
        or current_rate is None
        or current_rate <= 0
        or previous_rate is None
        or previous_rate <= 0
    ):
        return {
            **metadata,
            "local_day_change_pct": local_day_change_pct,
            "local_day_change_value_base": None,
            "fx_day_change_value_base": None,
            "day_change_value_base": None,
            "day_change_pct": None,
        }

    local_day_change_value_base = local_day_change_value * current_rate
    fx_day_change_value_base = previous_local_value * (current_rate - previous_rate)
    day_change_value_base = (
        current_local_value * current_rate - previous_local_value * previous_rate
    )
    previous_value_base = previous_local_value * previous_rate
    day_change_pct = (
        day_change_value_base / previous_value_base
        if abs(previous_value_base) > 1e-12
        else None
    )
    return {
        **metadata,
        "local_day_change_pct": local_day_change_pct,
        "local_day_change_value_base": local_day_change_value_base,
        "fx_day_change_value_base": fx_day_change_value_base,
        "day_change_value_base": day_change_value_base,
        "day_change_pct": day_change_pct,
    }


def position_day_change_metrics_in_base(
    *,
    current_market_value: float | None,
    local_day_change_pct: float | None,
    local_day_change_value: float | None,
    currency: str,
    base_currency: str,
    as_of_date: date,
    previous_as_of_date: date | None,
    direct_fx_instruments: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
    resolve_fx_rate_on: ResolveFxRate,
    normalize_currency: NormalizeCurrency = valuation_fx.normalized_currency,
    safe_float: SafeFloat = _safe_float,
) -> dict[str, object]:
    if (
        current_market_value is None
        or local_day_change_pct is None
        or local_day_change_value is None
    ):
        return {
            "local_day_change_pct": local_day_change_pct,
            "local_day_change_value_base": None,
            "fx_day_change_value_base": None,
            "day_change_value_base": None,
            "day_change_pct": None,
            "fx_rate_to_base": None,
            "fx_rate_as_of_date": None,
            "previous_fx_rate_to_base": None,
            "previous_fx_rate_as_of_date": None,
            "fx_rate_source_instrument_ids": [],
            "fx_rate_stale": False,
        }

    previous_market_value = current_market_value - local_day_change_value
    return translated_day_change_metrics(
        current_local_value=current_market_value,
        previous_local_value=previous_market_value,
        local_day_change_pct=local_day_change_pct,
        local_day_change_value=local_day_change_value,
        currency=currency,
        base_currency=base_currency,
        as_of_date=as_of_date,
        previous_as_of_date=previous_as_of_date,
        direct_fx_instruments=direct_fx_instruments,
        instrument_detail_cache=instrument_detail_cache,
        resolve_fx_rate_on=resolve_fx_rate_on,
        normalize_currency=normalize_currency,
        safe_float=safe_float,
    )


_TRANSLATED_DAY_CHANGE_FIELDS = (
    "day_change_pct",
    "local_day_change_pct",
    "local_day_change_value_base",
    "fx_day_change_value_base",
    "day_change_value_base",
    "fx_rate_to_base",
    "fx_rate_as_of_date",
    "previous_fx_rate_to_base",
    "previous_fx_rate_as_of_date",
    "fx_rate_source_instrument_ids",
    "fx_rate_stale",
)


def _translated_day_change_fields(metrics: dict[str, object]) -> dict[str, object]:
    return {field: metrics.get(field) for field in _TRANSLATED_DAY_CHANGE_FIELDS}


def build_cash_holding_rows(
    *,
    cash_balances: list[dict[str, object]],
    as_of_date: date,
    previous_as_of_date: date | None,
    base_currency: str,
    direct_fx_instruments: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
    cash_day_change: DayChangeMetrics,
    normalize_currency: NormalizeCurrency = valuation_fx.normalized_currency,
    safe_float: SafeFloat = _safe_float,
    account_lookup: dict[str, dict[str, object]] | None = None,
    cash_instrument_id: Callable[[str], str] = cash_holding_instrument_id,
    cash_instrument_ref: Callable[[str], dict[str, object]] = cash_holding_instrument_ref,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for balance in cash_balances:
        currency = _required_currency(
            balance.get("currency"),
            normalize_currency=normalize_currency,
            field_name="cash-balance currency",
        )
        amount = safe_float(balance.get("amount"))
        if amount is None or abs(amount) <= 1e-9:
            continue
        amount_base = safe_float(balance.get("amount_base"))
        instrument_id = cash_instrument_id(currency)
        account_id = str(balance.get("account_id") or "").strip()
        if not account_id:
            raise ValueError("Cash balance is missing account_id.")
        position_reference_id = f"{instrument_id}:{account_id}"
        account = (account_lookup or {}).get(account_id, {})
        cash_purpose = str(account.get("cash_purpose") or "operating")
        day_change = cash_day_change(
            amount=amount,
            currency=currency,
            base_currency=base_currency,
            as_of_date=as_of_date,
            previous_as_of_date=previous_as_of_date,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
        )
        is_base_cash = currency == _required_currency(
            base_currency,
            normalize_currency=normalize_currency,
            field_name="portfolio base currency",
        )
        rows.append(
            {
                "position_id": position_reference_id,
                "position_reference_id": position_reference_id,
                "instrument_id": instrument_id,
                "account_id": account_id,
                "line_id": position_reference_id,
                "holding_kind": "settled_cash",
                "available_for_trading": amount > 0 and cash_purpose in {"operating", "margin"},
                "cash_purpose": cash_purpose,
                "collateral_reference": account.get("collateral_reference"),
                "financing_liability": abs(amount) if amount < 0 and cash_purpose in {"margin", "financing"} else 0.0,
                "instrument_ref": cash_instrument_ref(currency),
                "quantity": amount,
                "cost_basis_method": None,
                "cost_basis": None,
                "cost_basis_base": None,
                "cost_basis_historical_base": safe_float(
                    balance.get("cost_basis_historical_base")
                ),
                "unrealized_fx_pnl_base": safe_float(
                    balance.get("unrealized_fx_pnl_base")
                ),
                "cost_basis_fx_rate_to_base": safe_float(
                    balance.get("cost_basis_fx_rate_to_base")
                ),
                "cost_basis_fx_coverage_status": str(
                    balance.get("cost_basis_fx_coverage_status") or "unavailable"
                ),
                "last_price": 1.0,
                "quote_as_of_date": as_of_date.isoformat(),
                "quote_metric_family": "cash",
                "quote_basis": "cash_balance",
                "quote_provider": "ledger",
                "quote_status": "complete" if amount_base is not None else "unpriced",
                "market_value": amount,
                "market_value_base": amount_base,
                **_translated_day_change_fields(day_change),
                "day_change_value": 0.0 if is_base_cash else None,
                "currency": currency,
                "portfolio_weight": None,
                "account_ids": [account_id],
                "account_count": 1,
                "open_position_lot_count": 0,
                "instrument_holding_start_date": None,
                "transaction_ids": list(balance.get("transaction_ids") or []),
                "coverage_status": "cash" if amount_base is not None else "unpriced",
            }
        )
    rows.sort(
        key=lambda item: (
            str(item.get("account_id") or ""),
            str(item.get("currency") or ""),
        )
    )
    return rows


_PENDING_MONETARY_LABELS = {
    "pending_subscription": "Subscription receivable",
    "settlement_receivable": "Settlement receivable",
    "settlement_payable": "Settlement payable",
    "position_recognition_adjustment": "Position recognition adjustment",
}


def _pending_monetary_instrument_id(balance: dict[str, object], currency: str) -> str:
    holding_kind = str(balance.get("holding_kind") or "pending_settlement").strip().lower()
    account_id = str(balance.get("account_id") or "unassigned").strip()
    economic_instrument_id = str(
        balance.get("economic_instrument_id") or "cash"
    ).strip()
    settlement_date = str(balance.get("settlement_date") or "undated").strip()
    pending_until_date = str(balance.get("pending_until_date") or "undated").strip()
    return (
        f"pending:{holding_kind}:{account_id}:{economic_instrument_id}:{currency}:"
        f"{settlement_date}:{pending_until_date}"
    )


def build_pending_monetary_holding_rows(
    *,
    pending_balances: list[dict[str, object]],
    as_of_date: date,
    previous_as_of_date: date | None,
    base_currency: str,
    direct_fx_instruments: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
    cash_day_change: DayChangeMetrics,
    normalize_currency: NormalizeCurrency = valuation_fx.normalized_currency,
    safe_float: SafeFloat = _safe_float,
) -> list[dict[str, object]]:
    """Render unsettled monetary balances without calling them cash or positions."""

    normalized_base_currency = _required_currency(
        base_currency,
        normalize_currency=normalize_currency,
        field_name="portfolio base currency",
    )
    rows: list[dict[str, object]] = []
    for balance in pending_balances:
        currency = _required_currency(
            balance.get("currency"),
            normalize_currency=normalize_currency,
            field_name="pending-balance currency",
        )
        amount = safe_float(balance.get("amount"))
        if amount is None or abs(amount) <= 1e-9:
            continue
        amount_base = safe_float(balance.get("amount_base"))
        holding_kind = str(
            balance.get("holding_kind") or "pending_settlement"
        ).strip().lower()
        account_id = str(balance.get("account_id") or "").strip()
        economic_instrument_id = str(
            balance.get("economic_instrument_id") or ""
        ).strip()
        economic_instrument_ref = (
            deepcopy(balance.get("economic_instrument_ref"))
            if isinstance(balance.get("economic_instrument_ref"), dict)
            else None
        )
        economic_name = str(
            (economic_instrument_ref or {}).get("instrument_name")
            or economic_instrument_id
        ).strip()
        label = _PENDING_MONETARY_LABELS.get(
            holding_kind,
            "Pending settlement",
        )
        instrument_name = f"{label} · {economic_name}" if economic_name else label
        instrument_id = _pending_monetary_instrument_id(balance, currency)
        day_change = cash_day_change(
            amount=amount,
            currency=currency,
            base_currency=base_currency,
            as_of_date=as_of_date,
            previous_as_of_date=previous_as_of_date,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
        )
        account_ids = sorted(
            {
                str(candidate)
                for candidate in [
                    account_id,
                    *list(balance.get("account_ids") or []),
                ]
                if str(candidate or "")
            }
        )
        rows.append(
            {
                "position_id": instrument_id,
                "instrument_id": instrument_id,
                "account_id": account_id or instrument_id,
                "line_id": instrument_id,
                "holding_kind": holding_kind,
                "available_for_trading": False,
                "economic_instrument_id": economic_instrument_id or None,
                "economic_instrument_ref": economic_instrument_ref,
                "transaction_ids": sorted(
                    {
                        str(transaction_id)
                        for transaction_id in list(
                            balance.get("transaction_ids") or []
                        )
                        if str(transaction_id or "")
                    }
                ),
                "settlement_date": balance.get("settlement_date"),
                "pending_until_date": balance.get("pending_until_date"),
                "pending_status": balance.get("pending_status") or "pending",
                "monetary_recognition_date": balance.get(
                    "monetary_recognition_date"
                ),
                "settlement_amount": amount,
                "settlement_amount_base": amount_base,
                "instrument_ref": {
                    "instrument_id": instrument_id,
                    "instrument_name": instrument_name,
                    "instrument_type": "other",
                    "currency": currency,
                    "identifiers": [],
                },
                "quantity": amount,
                "cost_basis_method": None,
                "cost_basis": None,
                "cost_basis_base": None,
                "cost_basis_historical_base": safe_float(
                    balance.get("cost_basis_historical_base")
                ),
                "cost_basis_fx_rate_to_base": safe_float(
                    balance.get("cost_basis_fx_rate_to_base")
                ),
                "cost_basis_fx_coverage_status": str(
                    balance.get("cost_basis_fx_coverage_status") or "unavailable"
                ),
                "unrealized_fx_pnl_base": safe_float(
                    balance.get("unrealized_fx_pnl_base")
                ),
                "last_price": 1.0,
                "quote_as_of_date": as_of_date.isoformat(),
                "quote_metric_family": "cash",
                "quote_basis": "pending_settlement",
                "quote_provider": "ledger",
                "quote_status": (
                    "complete" if amount_base is not None else "unpriced"
                ),
                "market_value": amount,
                "market_value_base": amount_base,
                **_translated_day_change_fields(day_change),
                "day_change_value": (
                    0.0 if currency == normalized_base_currency else None
                ),
                "currency": currency,
                "portfolio_weight": None,
                "account_ids": account_ids,
                "account_count": len(account_ids) if account_ids else 1,
                "open_position_lot_count": 0,
                "instrument_holding_start_date": None,
                "coverage_status": (
                    "pending-settlement"
                    if amount_base is not None
                    else "unpriced"
                ),
            }
        )
    rows.sort(
        key=lambda item: (
            str(item.get("holding_kind") or ""),
            str(item.get("account_id") or ""),
            str(item.get("economic_instrument_id") or ""),
        )
    )
    return rows


def position_buckets_from_lots(
    position_lots: list[dict[str, object]],
    *,
    normalize_currency: NormalizeCurrency = valuation_fx.normalized_currency,
    safe_float: SafeFloat = _safe_float,
    copy_value: Callable[[object], object] = deepcopy,
) -> list[dict[str, object]]:
    positions_by_reference: dict[str, dict[str, object]] = {}
    for position_lot in position_lots:
        position_reference_id = str(
            position_lot.get("position_reference_id") or ""
        )
        if not position_reference_id:
            continue
        bucket = positions_by_reference.setdefault(
            position_reference_id,
            {
                "position_reference_id": position_reference_id,
                "instrument_id": position_lot.get("instrument_id"),
                "instrument_ref": copy_value(position_lot.get("instrument_ref")),
                "derivative_contract_id": position_lot.get(
                    "derivative_contract_id"
                ),
                "derivative_contract": copy_value(
                    position_lot.get("derivative_contract")
                ),
                "currency": _required_currency(
                    position_lot.get("currency"),
                    normalize_currency=normalize_currency,
                    field_name="position-lot currency",
                ),
                "quantity": 0.0,
                "cost_basis": 0.0,
                "open_position_lot_count": 0,
                "account_ids": set(),
                "cost_basis_methods": set(),
                "cost_basis_origins": [],
            },
        )
        bucket["quantity"] += safe_float(position_lot.get("remaining_quantity")) or 0.0
        bucket["cost_basis"] += safe_float(position_lot.get("remaining_cost_basis")) or 0.0
        bucket["open_position_lot_count"] += 1
        bucket["account_ids"].add(str(position_lot.get("account_id") or ""))
        bucket["cost_basis_methods"].add(
            str(position_lot.get("cost_basis_method") or "fifo")
        )
        origins = position_lot.get("cost_basis_origins")
        if isinstance(origins, list):
            bucket["cost_basis_origins"].extend(deepcopy(origins))
    rendered_buckets: list[dict[str, object]] = []
    for bucket in positions_by_reference.values():
        if abs(safe_float(bucket.get("quantity")) or 0.0) <= 1e-9:
            continue
        raw_account_ids = bucket.get("account_ids")
        account_ids = (
            sorted(account_id for account_id in raw_account_ids if account_id)
            if isinstance(raw_account_ids, set)
            else []
        )
        raw_methods = bucket.get("cost_basis_methods")
        cost_basis_methods = (
            sorted(method for method in raw_methods if method)
            if isinstance(raw_methods, set)
            else []
        )
        rendered_buckets.append(
            {
                **{
                    key: value
                    for key, value in bucket.items()
                    if key != "cost_basis_methods"
                },
                "account_ids": account_ids,
                "account_count": len(account_ids),
                "cost_basis_method": (
                    cost_basis_methods[0] if len(cost_basis_methods) == 1 else "mixed"
                ),
            }
        )
    return rendered_buckets


def position_buckets_by_account_reference_from_lots(
    position_lots: list[dict[str, object]],
    *,
    normalize_currency: NormalizeCurrency = valuation_fx.normalized_currency,
    safe_float: SafeFloat = _safe_float,
    parse_iso_date: ParseIsoDate = _parse_iso_date,
    copy_value: Callable[[object], object] = deepcopy,
) -> list[dict[str, object]]:
    positions_by_account_reference: dict[tuple[str, str], dict[str, object]] = {}
    for position_lot in position_lots:
        account_id = str(position_lot.get("account_id") or "")
        position_reference_id = str(
            position_lot.get("position_reference_id") or ""
        )
        if not account_id or not position_reference_id:
            continue
        bucket = positions_by_account_reference.setdefault(
            (account_id, position_reference_id),
            {
                "account_id": account_id,
                "position_reference_id": position_reference_id,
                "instrument_id": position_lot.get("instrument_id"),
                "instrument_ref": copy_value(position_lot.get("instrument_ref")),
                "derivative_contract_id": position_lot.get(
                    "derivative_contract_id"
                ),
                "derivative_contract": copy_value(
                    position_lot.get("derivative_contract")
                ),
                "currency": _required_currency(
                    position_lot.get("currency"),
                    normalize_currency=normalize_currency,
                    field_name="position-lot currency",
                ),
                "quantity": 0.0,
                "cost_basis": 0.0,
                "open_position_lot_count": 0,
                "holding_start_date": None,
                "cost_basis_methods": set(),
                "cost_basis_origins": [],
            },
        )
        bucket["quantity"] += safe_float(position_lot.get("remaining_quantity")) or 0.0
        bucket["cost_basis"] += safe_float(position_lot.get("remaining_cost_basis")) or 0.0
        bucket["open_position_lot_count"] += 1
        holding_start_date = parse_iso_date(
            position_lot.get("acquisition_date")
        ) or parse_iso_date(position_lot.get("opened_at"))
        current_holding_start_date = bucket.get("holding_start_date")
        if holding_start_date is not None and (
            not isinstance(current_holding_start_date, date)
            or holding_start_date < current_holding_start_date
        ):
            bucket["holding_start_date"] = holding_start_date
        bucket["cost_basis_methods"].add(
            str(position_lot.get("cost_basis_method") or "fifo")
        )
        origins = position_lot.get("cost_basis_origins")
        if isinstance(origins, list):
            bucket["cost_basis_origins"].extend(deepcopy(origins))

    rendered_buckets: list[dict[str, object]] = []
    for bucket in positions_by_account_reference.values():
        if abs(safe_float(bucket.get("quantity")) or 0.0) <= 1e-9:
            continue
        raw_methods = bucket.get("cost_basis_methods")
        cost_basis_methods = (
            sorted(method for method in raw_methods if method)
            if isinstance(raw_methods, set)
            else []
        )
        rendered_buckets.append(
            {
                **{
                    key: value
                    for key, value in bucket.items()
                    if key != "cost_basis_methods"
                },
                "cost_basis_method": (
                    cost_basis_methods[0] if len(cost_basis_methods) == 1 else "mixed"
                ),
            }
        )
    return rendered_buckets


def build_option_obligation_holding_rows(
    option_obligations: list[dict[str, object]] | None,
    *,
    as_of_date: date,
    base_currency: str,
    nav: float | None,
    normalize_currency: NormalizeCurrency = valuation_fx.normalized_currency,
    safe_float: SafeFloat = _safe_float,
    convert_amount_on: Callable[..., tuple[float | None, bool]],
    instrument_detail_cache_get: Callable[..., dict[str, object] | None],
    direct_fx_instruments: dict[tuple[str, str], str] | None = None,
    instrument_detail_cache: dict[str, dict[str, object] | None] | None = None,
) -> list[dict[str, object]]:
    """Render written obligations as explicit negative holding rows."""

    groups: dict[tuple[str, str, str, str], dict[str, object]] = {}
    for obligation in option_obligations or []:
        if str(obligation.get("status") or "") != "open":
            continue
        remaining = safe_float(obligation.get("remaining_quantity")) or 0.0
        liability = safe_float(obligation.get("carrying_liability")) or 0.0
        if remaining <= 1e-9 or liability <= 1e-9:
            continue
        account_id = str(obligation.get("account_id") or "")
        derivative_contract_id = str(
            obligation.get("derivative_contract_id") or ""
        )
        derivative_contract = (
            obligation.get("derivative_contract")
            if isinstance(obligation.get("derivative_contract"), dict)
            else None
        )
        if not derivative_contract_id or not is_derivative_contract(
            derivative_contract
        ):
            raise ValueError(
                "Option obligation requires a complete local derivative contract."
            )
        related_underlying_id = str(
            obligation.get("related_underlying_id") or ""
        ).strip()
        currency = _required_currency(
            obligation.get("contract_currency") or base_currency,
            normalize_currency=normalize_currency,
            field_name="option obligation currency",
        )
        group = groups.setdefault(
            (
                account_id,
                derivative_contract_id,
                related_underlying_id,
                currency,
            ),
            {
                "account_id": account_id,
                "derivative_contract_id": derivative_contract_id,
                "derivative_contract": deepcopy(derivative_contract),
                "currency": currency,
                "related_underlying_id": related_underlying_id,
                "remaining_quantity": 0.0,
                "open_contract_quantity": 0.0,
                "required_underlying_quantity": 0.0,
                "premium_received_gross": 0.0,
                "premium_basis_remaining": 0.0,
                "liability_value": 0.0,
                "carrying_value_historical_base": 0.0,
                "carrying_fx_coverage_complete": True,
                "carrying_fx_stale": False,
                "transaction_ids": set(),
                "first_obligation": obligation,
            },
        )
        group["remaining_quantity"] += remaining
        group["open_contract_quantity"] += safe_float(obligation.get("open_contract_quantity")) or 0.0
        group["required_underlying_quantity"] += safe_float(
            obligation.get("required_underlying_quantity")
        ) or 0.0
        group["premium_received_gross"] += safe_float(obligation.get("premium_received_gross")) or 0.0
        premium_basis_remaining = safe_float(
            obligation.get("premium_basis_remaining")
        ) or 0.0
        group["premium_basis_remaining"] += premium_basis_remaining
        group["liability_value"] += liability
        opened_at = _parse_iso_date(obligation.get("opened_at"))
        historical_liability_base, historical_fx_stale = (
            convert_amount_on(
                premium_basis_remaining,
                as_of_date=opened_at,
                from_currency=currency,
                to_currency=base_currency,
                direct_fx_instruments=direct_fx_instruments or {},
                instrument_detail_cache=instrument_detail_cache or {},
            )
            if opened_at is not None
            else (None, False)
        )
        if historical_liability_base is None:
            group["carrying_fx_coverage_complete"] = False
        else:
            group["carrying_value_historical_base"] += historical_liability_base
        group["carrying_fx_stale"] = bool(
            group.get("carrying_fx_stale") or historical_fx_stale
        )
        opened_by = str(obligation.get("_opened_by_transaction_id") or "")
        if opened_by:
            group["transaction_ids"].add(opened_by)

    rows: list[dict[str, object]] = []
    ordered_groups = sorted(
        groups.values(),
        key=lambda group: (
            str(
                (
                    group.get("first_obligation")
                    if isinstance(group.get("first_obligation"), dict)
                    else {}
                ).get("expiry_date")
                or "9999-12-31"
            ),
            str(group.get("account_id") or ""),
            str(group.get("derivative_contract_id") or ""),
        ),
    )
    for group in ordered_groups:
        account_id = str(group["account_id"])
        derivative_contract_id = str(group["derivative_contract_id"])
        currency = str(group["currency"])
        related_underlying_id = str(group["related_underlying_id"])
        required_underlying_quantity = float(group["required_underlying_quantity"])
        liability_value = float(group["liability_value"])
        liability_value_base, liability_fx_stale = convert_amount_on(
            liability_value,
            as_of_date=as_of_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments or {},
            instrument_detail_cache=instrument_detail_cache or {},
        )
        first = group["first_obligation"]
        if not isinstance(first, dict):
            first = {}
        derivative_contract = (
            deepcopy(first.get("derivative_contract"))
            if isinstance(first.get("derivative_contract"), dict)
            else None
        )
        strike = safe_float(first.get("strike"))
        strike_notional = (
            strike * required_underlying_quantity if strike is not None else None
        )
        underlying = instrument_detail_cache_get(
            related_underlying_id, instrument_detail_cache if instrument_detail_cache is not None else {},
        )
        strike_currency = str((underlying or {}).get("currency") or "").strip().upper()
        strike_notional_base, _ = (
            convert_amount_on(
                strike_notional,
                as_of_date=as_of_date,
                from_currency=strike_currency,
                to_currency=base_currency,
                direct_fx_instruments=direct_fx_instruments or {},
                instrument_detail_cache=instrument_detail_cache or {},
            )
            if strike_notional is not None and strike_currency
            else (None, False)
        )
        expiry_date = _parse_iso_date(first.get("expiry_date"))
        days_to_expiry = (
            (expiry_date - as_of_date).days if expiry_date is not None else None
        )
        carrying_value_historical_base = (
            -float(group["carrying_value_historical_base"])
            if bool(group.get("carrying_fx_coverage_complete"))
            else None
        )
        signed_liability_value_base = (
            -liability_value_base if liability_value_base is not None else None
        )
        rows.append(
            {
                "line_id": (
                    f"{account_id}:{derivative_contract_id}:obligation"
                ),
                "account_id": account_id,
                "position_reference_id": derivative_contract_id,
                "instrument_id": None,
                "derivative_contract_id": derivative_contract_id,
                "derivative_contract": derivative_contract,
                "holding_kind": "option_obligation",
                "available_for_trading": False,
                "is_liability": True,
                "performance_eligible": False,
                "risk_eligible": False,
                "economic_instrument_id": related_underlying_id,
                "economic_instrument_ref": None,
                "related_underlying_id": related_underlying_id,
                "transaction_ids": sorted(group["transaction_ids"]),
                "instrument_ref": None,
                "quantity": -float(group["open_contract_quantity"]),
                "open_contract_quantity": float(group["open_contract_quantity"]),
                "required_underlying_quantity": required_underlying_quantity,
                "obligation_status": "open",
                "expiry_date": expiry_date.isoformat() if expiry_date else None,
                "days_to_expiry": days_to_expiry,
                "strike": strike,
                "strike_currency": strike_currency or None,
                "option_type": first.get("option_type"),
                "contract_multiplier": first.get("contract_multiplier"),
                "strike_notional": strike_notional,
                "strike_notional_base": strike_notional_base,
                "cost_basis_method": None,
                "cost_basis": None,
                "cost_basis_base": None,
                "last_price": None,
                "quote_as_of_date": None,
                "quote_metric_family": None,
                "quote_basis": "premium_liability",
                "quote_provider": None,
                "quote_status": "event-cost" if liability_value_base is not None else "unavailable",
                "market_value": -liability_value,
                "market_value_base": signed_liability_value_base,
                "fair_value": None,
                "fair_value_coverage_status": "unavailable",
                "valuation_basis": "premium_liability",
                "carrying_value": liability_value,
                "carrying_value_base": liability_value_base,
                "carrying_value_historical_base": (
                    carrying_value_historical_base
                ),
                "carrying_fx_translation_base": (
                    signed_liability_value_base - carrying_value_historical_base
                    if signed_liability_value_base is not None
                    and carrying_value_historical_base is not None
                    else None
                ),
                "carrying_fx_coverage_status": (
                    "stale"
                    if carrying_value_historical_base is not None
                    and (
                        bool(group.get("carrying_fx_stale"))
                        or liability_fx_stale
                    )
                    else "complete"
                    if carrying_value_historical_base is not None
                    and signed_liability_value_base is not None
                    else "unavailable"
                ),
                "liability_value": liability_value,
                "liability_value_base": liability_value_base,
                "premium_received_gross": float(group["premium_received_gross"]),
                "premium_basis_remaining": float(group["premium_basis_remaining"]),
                "day_change_pct": None,
                "day_change_value": None,
                "day_change_value_base": None,
                "currency": currency,
                "portfolio_weight": (
                    -liability_value_base / nav
                    if liability_value_base is not None and nav is not None and nav > 1e-9
                    else None
                ),
                "account_ids": [account_id],
                "account_count": 1,
                "open_position_lot_count": 0,
                "instrument_holding_start_date": first.get("opened_at"),
                "coverage_status": "event-liability",
            }
        )
    return rows


_PENDING_SETTLEMENT_KINDS = frozenset(
    {
        "pending_subscription",
        "settlement_receivable",
        "settlement_payable",
        "position_recognition_adjustment",
    }
)


def _expiry_bucket(days_to_expiry: int | None) -> str:
    if days_to_expiry is None:
        return "unknown"
    if days_to_expiry <= 0:
        return "expired_or_due"
    if days_to_expiry <= 7:
        return "next_7_days"
    if days_to_expiry <= 30:
        return "next_30_days"
    if days_to_expiry <= 90:
        return "next_90_days"
    return "later"


def summarize_holdings_operational_status(
    rows: list[dict[str, object]],
    *,
    as_of_date: date,
    safe_float: SafeFloat = _safe_float,
) -> dict[str, object]:
    """Summarize option obligations and pending settlements."""

    obligation_rows = [
        row
        for row in rows
        if str(row.get("holding_kind") or "") == "option_obligation"
    ]
    option_rows = [
        row
        for row in rows
        if row in obligation_rows
        or (
            isinstance(row.get("derivative_contract"), dict)
            and str(row["derivative_contract"].get("contract_type") or "")
            .strip()
            .lower()
            == "option"
        )
    ]
    expiry_buckets_by_key: dict[str, dict[str, object]] = {}
    for row in obligation_rows:
        expiry_date = _parse_iso_date(row.get("expiry_date"))
        days_to_expiry = (
            (expiry_date - as_of_date).days if expiry_date is not None else None
        )
        bucket_key = _expiry_bucket(days_to_expiry)
        bucket = expiry_buckets_by_key.setdefault(
            bucket_key,
            {
                "bucket": bucket_key,
                "obligation_count": 0,
                "open_contract_quantity": 0.0,
                "required_underlying_quantity": 0.0,
                "carrying_liability_base": 0.0,
                "carrying_liability_base_complete": True,
            },
        )
        bucket["obligation_count"] = int(bucket["obligation_count"]) + 1
        for field_name in (
            "open_contract_quantity",
            "required_underlying_quantity",
        ):
            bucket[field_name] = float(bucket[field_name]) + (
                safe_float(row.get(field_name)) or 0.0
            )
        liability_base = safe_float(row.get("liability_value_base"))
        if liability_base is None:
            bucket["carrying_liability_base_complete"] = False
        else:
            bucket["carrying_liability_base"] = (
                float(bucket["carrying_liability_base"]) + liability_base
            )

    bucket_order = {
        "expired_or_due": 0,
        "next_7_days": 1,
        "next_30_days": 2,
        "next_90_days": 3,
        "later": 4,
        "unknown": 5,
    }
    expiry_buckets: list[dict[str, object]] = []
    for bucket in sorted(
        expiry_buckets_by_key.values(),
        key=lambda item: bucket_order[str(item["bucket"])],
    ):
        complete = bool(bucket.pop("carrying_liability_base_complete"))
        if not complete:
            bucket["carrying_liability_base"] = None
        expiry_buckets.append(bucket)

    obligation_notional_values = [
        safe_float(row.get("strike_notional_base")) for row in obligation_rows
    ]
    obligation_notional_complete = all(
        value is not None for value in obligation_notional_values
    )

    settlement_rows = [
        row
        for row in rows
        if str(row.get("holding_kind") or "") in _PENDING_SETTLEMENT_KINDS
    ]
    settlement_amounts = [
        safe_float(row.get("settlement_amount_base")) for row in settlement_rows
    ]
    settlement_amounts_complete = all(value is not None for value in settlement_amounts)
    receivable_base = sum(
        value for value in settlement_amounts if value is not None and value > 0
    )
    payable_base = sum(
        abs(value) for value in settlement_amounts if value is not None and value < 0
    )
    settlement_dates = sorted(
        str(row.get("settlement_date"))
        for row in settlement_rows
        if str(row.get("settlement_date") or "")
    )
    overdue_rows = [
        row
        for row in settlement_rows
        if str(row.get("pending_status") or "") == "overdue"
    ]
    negative_cash_rows = [
        row
        for row in rows
        if str(row.get("holding_kind") or "") == "settled_cash"
        and (safe_float(row.get("market_value")) or 0.0) < -1e-9
        and row.get("cash_purpose") not in {"margin", "financing"}
    ]

    operational_alerts: list[dict[str, object]] = []
    if negative_cash_rows:
        operational_alerts.append(
            {
                "code": "negative_settled_cash",
                "severity": "critical",
                "title": "Negative settled cash",
                "message": (
                    f"{len(negative_cash_rows)} settled cash line(s) are negative; "
                    "record the missing funding or financing fact."
                ),
                "related_line_ids": [
                    str(row.get("line_id") or "") for row in negative_cash_rows
                ],
            }
        )
    due_rows = [
        row
        for row in option_rows
        if _expiry_bucket(
            (expiry_date - as_of_date).days
            if (expiry_date := _parse_iso_date(row.get("expiry_date"))) is not None
            else None
        )
        == "expired_or_due"
    ]
    due_count = len(due_rows)
    if due_count:
        operational_alerts.append(
            {
                "code": "option_expiry_due",
                "severity": "critical",
                "title": "Option expiry action due",
                "message": f"{due_count} open option line(s) are at or past expiry.",
                "related_line_ids": [
                    str(row.get("line_id") or "") for row in due_rows
                ],
            }
        )
    near_expiry_rows = [
        row
        for row in option_rows
        if _expiry_bucket(
            (expiry_date - as_of_date).days
            if (expiry_date := _parse_iso_date(row.get("expiry_date"))) is not None
            else None
        )
        == "next_7_days"
    ]
    near_expiry_count = len(near_expiry_rows)
    if near_expiry_count:
        operational_alerts.append(
            {
                "code": "option_expiry_next_7_days",
                "severity": "warning",
                "title": "Option expiry within 7 days",
                "message": f"{near_expiry_count} open option line(s) require expiry review.",
                "related_line_ids": [
                    str(row.get("line_id") or "") for row in near_expiry_rows
                ],
            }
        )
    if overdue_rows:
        operational_alerts.append(
            {
                "code": "pending_settlement_overdue",
                "severity": "critical",
                "title": "Pending settlement overdue",
                "message": f"{len(overdue_rows)} settlement line(s) are past their settlement date.",
                "related_line_ids": [str(row.get("line_id") or "") for row in overdue_rows],
            }
        )
    if settlement_rows and not settlement_amounts_complete:
        operational_alerts.append(
            {
                "code": "pending_settlement_fx_unavailable",
                "severity": "warning",
                "title": "Settlement exposure conversion unavailable",
                "message": "At least one pending settlement line cannot be converted to base currency.",
                "related_line_ids": [
                    str(row.get("line_id") or "")
                    for row in settlement_rows
                    if safe_float(row.get("settlement_amount_base")) is None
                ],
            }
        )

    return {
        "operational_summary": {
            "expiry_buckets": expiry_buckets,
            "option_obligation_exposure": {
                "obligation_count": len(obligation_rows),
                "open_contract_quantity": sum(
                    safe_float(row.get("open_contract_quantity")) or 0.0
                    for row in obligation_rows
                ),
                "underlying_equivalent_quantity": sum(
                    safe_float(row.get("required_underlying_quantity")) or 0.0
                    for row in obligation_rows
                ),
                "strike_notional_base": (
                    sum(value for value in obligation_notional_values if value is not None)
                    if obligation_notional_complete
                    else None
                ),
            },
            "settlement_exposure": {
                "pending_line_count": len(settlement_rows),
                "receivable_base": receivable_base if settlement_amounts_complete else None,
                "payable_base": payable_base if settlement_amounts_complete else None,
                "net_base": (
                    receivable_base - payable_base
                    if settlement_amounts_complete
                    else None
                ),
                "earliest_settlement_date": settlement_dates[0] if settlement_dates else None,
                "overdue_line_count": len(overdue_rows),
                "unavailable_base_line_count": sum(
                    value is None for value in settlement_amounts
                ),
            },
        },
        "operational_alerts": operational_alerts,
    }


def position_unrealized_metrics(
    *,
    cost_basis: float | None,
    cost_basis_base_current_fx: float | None,
    current_fx_stale: bool,
    cost_basis_origins: object,
    market_value: float | None,
    market_value_base: float | None,
    currency: str,
    base_currency: str,
    as_of_date: date,
    direct_fx_instruments: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
    resolve_fx_rate_on: ResolveFxRate,
    normalize_currency: NormalizeCurrency = valuation_fx.normalized_currency,
    safe_float: SafeFloat = _safe_float,
    parse_iso_date: ParseIsoDate = _parse_iso_date,
) -> dict[str, object]:
    empty = {
        "cost_basis_historical_base": None,
        "cost_basis_current_fx_rate_to_base": None,
        "cost_basis_fx_rate_to_base": None,
        "cost_basis_fx_coverage_status": "unavailable",
        "unrealized_price_pnl": None,
        "unrealized_price_pnl_base": None,
        "unrealized_fx_pnl_base": None,
        "unrealized_pnl_base": None,
        "unrealized_return": None,
        "unrealized_return_base": None,
    }
    local_cost = safe_float(cost_basis)
    current_cost_base = safe_float(cost_basis_base_current_fx)
    if local_cost is None or current_cost_base is None:
        return empty

    normalized_currency = _required_currency(
        currency,
        normalize_currency=normalize_currency,
        field_name="position currency",
    )
    normalized_base = _required_currency(
        base_currency,
        normalize_currency=normalize_currency,
        field_name="portfolio base currency",
    )
    historical_cost_base: float | None = None
    historical_fx_stale = False
    origins = [
        origin
        for origin in list(cost_basis_origins or [])
        if isinstance(origin, dict)
        and (safe_float(origin.get("remaining_cost_basis")) or 0.0) > 1e-9
    ]
    origin_local_cost = sum(
        safe_float(origin.get("remaining_cost_basis")) or 0.0
        for origin in origins
    )
    origin_tolerance = max(abs(local_cost) * 1e-9, 1e-7)
    origins_complete = abs(origin_local_cost - local_cost) <= origin_tolerance
    if abs(local_cost) <= 1e-12:
        historical_cost_base = 0.0
        origins_complete = True
    elif normalized_currency == normalized_base:
        historical_cost_base = local_cost
        origins_complete = True
    elif origins and origins_complete:
        historical_cost_base = 0.0
        for origin in origins:
            origin_date = parse_iso_date(origin.get("acquisition_date"))
            origin_cost = safe_float(origin.get("remaining_cost_basis"))
            if origin_date is None or origin_cost is None:
                historical_cost_base = None
                break
            resolved_fx = resolve_fx_rate_on(
                as_of_date=origin_date,
                base_currency=normalized_currency,
                quote_currency=normalized_base,
                direct_instruments=direct_fx_instruments,
                instrument_detail_cache=instrument_detail_cache,
            )
            origin_rate = safe_float((resolved_fx or {}).get("rate"))
            if origin_rate is None or origin_rate <= 0:
                historical_cost_base = None
                break
            historical_cost_base += origin_cost * origin_rate
            historical_fx_stale = historical_fx_stale or bool(
                (resolved_fx or {}).get("stale")
            )

    local_market_value = safe_float(market_value)
    base_market_value = safe_float(market_value_base)
    current_fx_rate = (
        1.0
        if normalized_currency == normalized_base
        else base_market_value / local_market_value
        if base_market_value is not None
        and local_market_value is not None
        and abs(local_market_value) > 1e-12
        else current_cost_base / local_cost
        if abs(local_cost) > 1e-12
        else None
    )
    local_price_pnl = (
        local_market_value - local_cost
        if local_market_value is not None
        else None
    )
    price_pnl_base = (
        base_market_value - current_cost_base
        if base_market_value is not None
        else None
    )
    fx_pnl_base = (
        current_cost_base - historical_cost_base
        if historical_cost_base is not None
        else None
    )
    total_pnl_base = (
        base_market_value - historical_cost_base
        if base_market_value is not None and historical_cost_base is not None
        else None
    )
    return {
        "cost_basis_historical_base": historical_cost_base,
        "cost_basis_current_fx_rate_to_base": current_fx_rate,
        "cost_basis_fx_rate_to_base": (
            historical_cost_base / local_cost
            if historical_cost_base is not None and abs(local_cost) > 1e-12
            else None
        ),
        "cost_basis_fx_coverage_status": (
            "stale"
            if historical_cost_base is not None
            and (historical_fx_stale or current_fx_stale)
            else "complete" if historical_cost_base is not None
            else "unavailable"
        ),
        "unrealized_price_pnl": local_price_pnl,
        "unrealized_price_pnl_base": price_pnl_base,
        "unrealized_fx_pnl_base": fx_pnl_base,
        "unrealized_pnl_base": total_pnl_base,
        "unrealized_return": (
            local_price_pnl / abs(local_cost)
            if local_price_pnl is not None and abs(local_cost) > 1e-12
            else None
        ),
        "unrealized_return_base": (
            total_pnl_base / abs(historical_cost_base)
            if total_pnl_base is not None
            and historical_cost_base is not None
            and abs(historical_cost_base) > 1e-12
            else None
        ),
    }


def build_materialized_holding_rows(
    *,
    account_instrument_buckets: list[dict[str, object]],
    option_obligations: list[dict[str, object]] | None = None,
    cash_balances: list[dict[str, object]] | None = None,
    pending_balances: list[dict[str, object]] | None = None,
    as_of_date: date,
    previous_as_of_date: date | None,
    base_currency: str,
    direct_fx_instruments: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
    nav: float | None,
    normalize_currency: NormalizeCurrency,
    safe_float: SafeFloat,
    parse_iso_date: ParseIsoDate,
    convert_amount_on: Callable[..., tuple[float | None, bool]],
    instrument_detail_cache_get: Callable[..., dict[str, object] | None],
    select_market_point_as_of: Callable[..., dict[str, object] | None],
    previous_market_point_for_selected_point: Callable[..., dict[str, object] | None],
    position_market_value: PositionMarketValue,
    holding_day_change: Callable[..., tuple[float | None, float | None]],
    position_day_change: DayChangeMetrics,
    resolve_fx_rate_on: ResolveFxRate,
    normalize_instrument: Callable[..., dict[str, object]],
    build_cash_rows: Callable[..., list[dict[str, object]]],
    build_pending_rows: Callable[..., list[dict[str, object]]],
    apply_portfolio_weights: Callable[[list[dict[str, object]], float | None], None],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for bucket in account_instrument_buckets:
        account_id = str(bucket.get("account_id") or "")
        position_reference_id = str(
            bucket.get("position_reference_id")
            or bucket.get("derivative_contract_id")
            or bucket.get("instrument_id")
            or ""
        )
        instrument_id = str(bucket.get("instrument_id") or "") or None
        derivative_contract = (
            bucket.get("derivative_contract")
            if isinstance(bucket.get("derivative_contract"), dict)
            else None
        )
        derivative_contract_id = (
            str(bucket.get("derivative_contract_id") or "") or None
        )
        if not account_id or not position_reference_id:
            continue

        currency = _required_currency(
            bucket.get("currency"),
            normalize_currency=normalize_currency,
            field_name="position-bucket currency",
        )
        quantity = safe_float(bucket.get("quantity")) or 0.0
        cost_basis = safe_float(bucket.get("cost_basis"))
        converted_cost_basis, current_cost_fx_stale = convert_amount_on(
            cost_basis,
            as_of_date=as_of_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
        )
        instrument_ref = (
            bucket.get("instrument_ref")
            if isinstance(bucket.get("instrument_ref"), dict)
            else None
        )
        event_valued = is_derivative_contract(derivative_contract)
        detail = (
            None
            if event_valued or instrument_id is None
            else instrument_detail_cache_get(instrument_id, instrument_detail_cache)
        )
        price_point = (
            select_market_point_as_of(
                detail=detail,
                role="valuation",
                as_of_date=as_of_date,
            )
            if isinstance(detail, dict)
            else None
        )
        previous_price_point = (
            previous_market_point_for_selected_point(
                detail=detail,
                selected_point=price_point,
            )
            if isinstance(detail, dict)
            else None
        )
        return_price_point = (
            select_market_point_as_of(
                detail=detail,
                role="total_return",
                as_of_date=as_of_date,
            )
            if isinstance(detail, dict)
            else None
        )
        previous_return_price_point = (
            previous_market_point_for_selected_point(
                detail=detail,
                selected_point=return_price_point,
            )
            if isinstance(detail, dict)
            else None
        )
        holding_start_date = parse_iso_date(bucket.get("holding_start_date"))
        quoted_price = safe_float((price_point or {}).get("value"))
        last_price, market_value, event_valued = resolve_position_valuation(
            quantity=quantity,
            cost_basis=cost_basis,
            instrument_ref=instrument_ref,
            derivative_contract=derivative_contract,
            quoted_price=quoted_price,
            quoted_price_scale=safe_float((price_point or {}).get("price_scale")),
            position_market_value=position_market_value,
        )
        if event_valued:
            # Carrying basis is a useful operational NAV input, but it is not
            # an observed quote.  Never manufacture a zero daily return or a
            # complete quote record for an event-valued asset.
            day_change_pct, day_change_value = None, None
        else:
            day_change_pct, day_change_value = holding_day_change(
                quantity=quantity,
                current_price=last_price,
                previous_price=safe_float((previous_price_point or {}).get("value")),
                instrument_ref=instrument_ref,
                current_return_price=safe_float((return_price_point or {}).get("value")),
                previous_return_price=safe_float(
                    (previous_return_price_point or {}).get("value")
                ),
                price_scale=safe_float((price_point or {}).get("price_scale")),
            )
        converted_market_value, _ = convert_amount_on(
            market_value,
            as_of_date=as_of_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
        )
        day_change = (
            position_day_change(
                current_market_value=market_value,
                local_day_change_pct=day_change_pct,
                local_day_change_value=day_change_value,
                currency=currency,
                base_currency=base_currency,
                as_of_date=as_of_date,
                previous_as_of_date=previous_as_of_date,
                direct_fx_instruments=direct_fx_instruments,
                instrument_detail_cache=instrument_detail_cache,
            )
            if not event_valued
            else {
                "local_day_change_pct": None,
                "local_day_change_value_base": None,
                "fx_day_change_value_base": None,
                "day_change_value_base": None,
                "day_change_pct": None,
                "fx_rate_to_base": None,
                "fx_rate_as_of_date": None,
                "previous_fx_rate_to_base": None,
                "previous_fx_rate_as_of_date": None,
                "fx_rate_source_instrument_ids": [],
                "fx_rate_stale": False,
            }
        )
        cost_metrics = position_unrealized_metrics(
            cost_basis=cost_basis,
            cost_basis_base_current_fx=converted_cost_basis,
            current_fx_stale=current_cost_fx_stale,
            cost_basis_origins=bucket.get("cost_basis_origins"),
            market_value=market_value,
            market_value_base=converted_market_value,
            currency=currency,
            base_currency=base_currency,
            as_of_date=as_of_date,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
            resolve_fx_rate_on=resolve_fx_rate_on,
            normalize_currency=normalize_currency,
            safe_float=safe_float,
            parse_iso_date=parse_iso_date,
        )
        unrealized_metrics = cost_metrics if not event_valued else {}
        carrying_fx_metrics = (
            {
                "carrying_value_historical_base": cost_metrics.get(
                    "cost_basis_historical_base"
                ),
                "carrying_fx_translation_base": cost_metrics.get(
                    "unrealized_fx_pnl_base"
                ),
                "carrying_fx_coverage_status": cost_metrics.get(
                    "cost_basis_fx_coverage_status"
                ),
            }
            if event_valued
            else {}
        )
        instrument_core = (
            normalize_instrument(instrument_id, instrument_ref)
            if instrument_id is not None
            else None
        )
        option_exposure = option_contract_exposure_fields(
            derivative_contract=derivative_contract,
            quantity=quantity,
            as_of_date=as_of_date,
            base_currency=base_currency,
            convert_amount_on=convert_amount_on,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
            instrument_detail_cache_get=instrument_detail_cache_get,
            safe_float=safe_float,
        )
        rows.append(
            {
                "line_id": f"{account_id}:{position_reference_id}",
                "account_id": account_id,
                "position_reference_id": position_reference_id,
                "instrument_id": instrument_id,
                "derivative_contract_id": derivative_contract_id,
                "derivative_contract": deepcopy(derivative_contract),
                "holding_kind": (
                    "derivative_contract" if event_valued else "position"
                ),
                "available_for_trading": True,
                "instrument_ref": instrument_core,
                "quantity": quantity,
                "cost_basis_method": str(bucket.get("cost_basis_method") or "fifo"),
                "cost_basis": cost_basis,
                "cost_basis_base": converted_cost_basis,
                **unrealized_metrics,
                "last_price": last_price,
                "quote_as_of_date": (
                    None if event_valued else (price_point or {}).get("as_of_date")
                ),
                "quote_metric_family": (
                    None if event_valued else (price_point or {}).get("metric_family")
                ),
                "quote_basis": (
                    "carried_cost"
                    if event_valued
                    else (price_point or {}).get("quote_basis")
                ),
                "quote_provider": (
                    None if event_valued else (price_point or {}).get("provider")
                ),
                "quote_status": (
                    "event-cost" if event_valued and converted_market_value is not None
                    else "unavailable" if event_valued
                    else (price_point or {}).get("status")
                ),
                "quote_price_unit": (
                    "per_unit"
                    if event_valued
                    else (price_point or {}).get("price_unit")
                ),
                "quote_price_scale": (
                    1.0 if event_valued else (price_point or {}).get("price_scale")
                ),
                "market_value": market_value,
                "market_value_base": converted_market_value,
                "carrying_value": market_value if event_valued else None,
                "carrying_value_base": converted_market_value if event_valued else None,
                **carrying_fx_metrics,
                "fair_value": None if event_valued else market_value,
                "fair_value_coverage_status": (
                    "unavailable" if event_valued else "complete"
                ),
                "valuation_basis": "carried_cost" if event_valued else "market_quote",
                "performance_eligible": not event_valued,
                "risk_eligible": not event_valued,
                **option_exposure,
                **_translated_day_change_fields(day_change),
                "day_change_value": day_change_value,
                "currency": currency,
                "portfolio_weight": (
                    converted_market_value / nav
                    if converted_market_value is not None
                    and nav is not None
                    and nav > 1e-9
                    else None
                ),
                "account_ids": [account_id],
                "account_count": 1,
                "open_position_lot_count": int(
                    bucket.get("open_position_lot_count") or 0
                ),
                "instrument_holding_start_date": (
                    holding_start_date.isoformat()
                    if holding_start_date is not None
                    else None
                ),
                "coverage_status": (
                    "event-cost"
                    if event_valued and converted_market_value is not None
                    else "price-nav-fx"
                    if converted_market_value is not None
                    else "unpriced"
                ),
            }
        )

    rows.extend(
        build_option_obligation_holding_rows(
            option_obligations,
            as_of_date=as_of_date,
            base_currency=base_currency,
            nav=nav,
            normalize_currency=normalize_currency,
            safe_float=safe_float,
            convert_amount_on=convert_amount_on,
            instrument_detail_cache_get=instrument_detail_cache_get,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
        )
    )

    rows.extend(
        build_cash_rows(
            cash_balances=list(cash_balances or []),
            as_of_date=as_of_date,
            previous_as_of_date=previous_as_of_date,
            base_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
        )
    )
    rows.extend(
        build_pending_rows(
            pending_balances=list(pending_balances or []),
            as_of_date=as_of_date,
            previous_as_of_date=previous_as_of_date,
            base_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
        )
    )
    apply_portfolio_weights(rows, nav)
    rows.sort(
        key=lambda item: (
            str(item.get("account_id") or ""),
            -((safe_float(item.get("market_value_base")) or 0.0)),
            str(item.get("instrument_id") or ""),
        )
    )
    return rows


def apply_position_portfolio_weights(
    positions: list[dict[str, object]],
    denominator_nav: float | None,
    *,
    safe_float: SafeFloat = _safe_float,
) -> None:
    for rendered_position in positions:
        market_value_base = safe_float(rendered_position.get("market_value_base"))
        rendered_position["portfolio_weight"] = (
            market_value_base / denominator_nav
            if market_value_base is not None
            and denominator_nav is not None
            and denominator_nav > 1e-9
            else None
        )
