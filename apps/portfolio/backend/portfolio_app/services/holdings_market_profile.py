from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import date

from portfolio_app.services import valuation_fx


_SUPPORTED_INSTRUMENT_TYPES = {"fund", "etf", "bond", "equity", "cash", "fx", "other"}
_FORBIDDEN_INSTRUMENT_REF_KEYS = {"asset_id", "asset_name", "asset_type"}

SafeFloat = Callable[[object], float | None]
NormalizeCurrency = Callable[[object], str]
ParseIsoDate = Callable[[object], date | None]
PositionMarketValue = Callable[..., float | None]
ResolveFxRate = Callable[..., dict[str, object] | None]
CashDayChange = Callable[..., tuple[float | None, float | None]]


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
    identifiers = instrument_ref.get("identifiers")
    return {
        "instrument_id": resolved_id,
        "instrument_name": instrument_name,
        "instrument_type": resolved_type,
        "currency": currency,
        "identifiers": list(identifiers or []) if isinstance(identifiers, list) else [],
    }


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
        instrument_ref=instrument_ref,
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
    direct_fx_instruments: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
    resolve_fx_rate_on: ResolveFxRate,
    resolve_previous_fx_rate_before: ResolveFxRate,
    normalize_currency: NormalizeCurrency = valuation_fx.normalized_currency,
    safe_float: SafeFloat = _safe_float,
    parse_iso_date: ParseIsoDate = _parse_iso_date,
) -> tuple[float | None, float | None]:
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
    if normalized_currency == normalized_base:
        return 0.0, 0.0

    current_fx = resolve_fx_rate_on(
        as_of_date=as_of_date,
        base_currency=normalized_currency,
        quote_currency=normalized_base,
        direct_instruments=direct_fx_instruments,
        instrument_detail_cache=instrument_detail_cache,
    )
    current_rate = safe_float((current_fx or {}).get("rate"))
    current_rate_date = parse_iso_date((current_fx or {}).get("as_of_date"))
    if current_rate is None or current_rate <= 0 or current_rate_date is None:
        return None, None

    previous_fx = resolve_previous_fx_rate_before(
        before_date=current_rate_date,
        base_currency=normalized_currency,
        quote_currency=normalized_base,
        direct_instruments=direct_fx_instruments,
        instrument_detail_cache=instrument_detail_cache,
    )
    previous_rate = safe_float((previous_fx or {}).get("rate"))
    if previous_rate is None or previous_rate <= 0:
        return None, None

    day_change_pct = current_rate / previous_rate - 1.0
    return day_change_pct, amount * (current_rate - previous_rate)


def build_cash_holding_rows(
    *,
    cash_balances: list[dict[str, object]],
    as_of_date: date,
    base_currency: str,
    direct_fx_instruments: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
    cash_day_change: CashDayChange,
    normalize_currency: NormalizeCurrency = valuation_fx.normalized_currency,
    safe_float: SafeFloat = _safe_float,
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
        day_change_pct, day_change_value_base = cash_day_change(
            amount=amount,
            currency=currency,
            base_currency=base_currency,
            as_of_date=as_of_date,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
        )
        is_base_cash = currency == _required_currency(
            base_currency,
            normalize_currency=normalize_currency,
            field_name="portfolio base currency",
        )
        account_ids = sorted(
            str(account_id)
            for account_id in list(balance.get("account_ids") or [])
            if str(account_id or "")
        )
        rows.append(
            {
                "position_id": instrument_id,
                "instrument_id": instrument_id,
                "account_id": instrument_id,
                "line_id": instrument_id,
                "holding_kind": "settled_cash",
                "available_for_trading": True,
                "instrument_ref": cash_instrument_ref(currency),
                "quantity": amount,
                "cost_basis_method": None,
                "cost_basis": None,
                "cost_basis_base": None,
                "last_price": 1.0,
                "quote_as_of_date": as_of_date.isoformat(),
                "quote_metric_family": "cash",
                "quote_basis": "cash_balance",
                "quote_provider": "ledger",
                "quote_status": "complete" if amount_base is not None else "unpriced",
                "market_value": amount,
                "market_value_base": amount_base,
                "day_change_pct": day_change_pct,
                "day_change_value": 0.0 if is_base_cash else None,
                "day_change_value_base": day_change_value_base,
                "currency": currency,
                "portfolio_weight": None,
                "account_ids": account_ids,
                "account_count": len(account_ids) if account_ids else 1,
                "open_position_lot_count": 0,
                "instrument_holding_start_date": None,
                "coverage_status": "cash" if amount_base is not None else "unpriced",
            }
        )
    rows.sort(key=lambda item: str(item.get("currency") or ""))
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
    return f"pending:{holding_kind}:{account_id}:{economic_instrument_id}:{currency}"


def build_pending_monetary_holding_rows(
    *,
    pending_balances: list[dict[str, object]],
    as_of_date: date,
    base_currency: str,
    direct_fx_instruments: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
    cash_day_change: CashDayChange,
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
        day_change_pct, day_change_value_base = cash_day_change(
            amount=amount,
            currency=currency,
            base_currency=base_currency,
            as_of_date=as_of_date,
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
                "day_change_pct": day_change_pct,
                "day_change_value": (
                    0.0 if currency == normalized_base_currency else None
                ),
                "day_change_value_base": day_change_value_base,
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
    positions_by_instrument: dict[str, dict[str, object]] = {}
    for position_lot in position_lots:
        instrument_id = str(position_lot.get("instrument_id") or "")
        if not instrument_id:
            continue
        bucket = positions_by_instrument.setdefault(
            instrument_id,
            {
                "instrument_id": instrument_id,
                "instrument_ref": copy_value(position_lot.get("instrument_ref")),
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
            },
        )
        bucket["quantity"] += safe_float(position_lot.get("remaining_quantity")) or 0.0
        bucket["cost_basis"] += safe_float(position_lot.get("remaining_cost_basis")) or 0.0
        bucket["open_position_lot_count"] += 1
        bucket["account_ids"].add(str(position_lot.get("account_id") or ""))
        bucket["cost_basis_methods"].add(
            str(position_lot.get("cost_basis_method") or "fifo")
        )
    rendered_buckets: list[dict[str, object]] = []
    for bucket in positions_by_instrument.values():
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


def position_buckets_by_account_instrument_from_lots(
    position_lots: list[dict[str, object]],
    *,
    normalize_currency: NormalizeCurrency = valuation_fx.normalized_currency,
    safe_float: SafeFloat = _safe_float,
    parse_iso_date: ParseIsoDate = _parse_iso_date,
    copy_value: Callable[[object], object] = deepcopy,
) -> list[dict[str, object]]:
    positions_by_account_instrument: dict[tuple[str, str], dict[str, object]] = {}
    for position_lot in position_lots:
        account_id = str(position_lot.get("account_id") or "")
        instrument_id = str(position_lot.get("instrument_id") or "")
        if not account_id or not instrument_id:
            continue
        bucket = positions_by_account_instrument.setdefault(
            (account_id, instrument_id),
            {
                "account_id": account_id,
                "instrument_id": instrument_id,
                "instrument_ref": copy_value(position_lot.get("instrument_ref")),
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

    rendered_buckets: list[dict[str, object]] = []
    for bucket in positions_by_account_instrument.values():
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


def build_materialized_holding_rows(
    *,
    account_instrument_buckets: list[dict[str, object]],
    cash_balances: list[dict[str, object]] | None = None,
    pending_balances: list[dict[str, object]] | None = None,
    as_of_date: date,
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
    holding_day_change: CashDayChange,
    normalize_instrument: Callable[..., dict[str, object]],
    build_cash_rows: Callable[..., list[dict[str, object]]],
    build_pending_rows: Callable[..., list[dict[str, object]]],
    apply_portfolio_weights: Callable[[list[dict[str, object]], float | None], None],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for bucket in account_instrument_buckets:
        account_id = str(bucket.get("account_id") or "")
        instrument_id = str(bucket.get("instrument_id") or "")
        if not account_id or not instrument_id:
            continue

        currency = _required_currency(
            bucket.get("currency"),
            normalize_currency=normalize_currency,
            field_name="position-bucket currency",
        )
        quantity = safe_float(bucket.get("quantity")) or 0.0
        cost_basis = safe_float(bucket.get("cost_basis"))
        converted_cost_basis, _ = convert_amount_on(
            cost_basis,
            as_of_date=as_of_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
        )
        detail = instrument_detail_cache_get(instrument_id, instrument_detail_cache)
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
        last_price = safe_float((price_point or {}).get("value"))
        previous_price = safe_float((previous_price_point or {}).get("value"))
        instrument_ref = (
            bucket.get("instrument_ref")
            if isinstance(bucket.get("instrument_ref"), dict)
            else None
        )
        market_value = position_market_value(
            quantity=quantity,
            last_price=last_price,
            instrument_ref=instrument_ref,
            price_scale=safe_float((price_point or {}).get("price_scale")),
        )
        day_change_pct, day_change_value = holding_day_change(
            quantity=quantity,
            current_price=last_price,
            previous_price=previous_price,
            instrument_ref=instrument_ref,
            current_return_price=safe_float((return_price_point or {}).get("value")),
            previous_return_price=safe_float(
                (previous_return_price_point or {}).get("value")
            ),
            price_scale=safe_float((price_point or {}).get("price_scale")),
        )
        converted_day_change_value, _ = convert_amount_on(
            day_change_value,
            as_of_date=as_of_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
        )
        converted_market_value, _ = convert_amount_on(
            market_value,
            as_of_date=as_of_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
        )
        instrument_core = normalize_instrument(
            instrument_id,
            instrument_ref,
        )
        rows.append(
            {
                "line_id": f"{account_id}:{instrument_id}",
                "account_id": account_id,
                "instrument_id": instrument_id,
                "holding_kind": "position",
                "available_for_trading": True,
                "instrument_ref": instrument_core,
                "quantity": quantity,
                "cost_basis_method": str(bucket.get("cost_basis_method") or "fifo"),
                "cost_basis": cost_basis,
                "cost_basis_base": converted_cost_basis,
                "last_price": last_price,
                "quote_as_of_date": (price_point or {}).get("as_of_date"),
                "quote_metric_family": (price_point or {}).get("metric_family"),
                "quote_basis": (price_point or {}).get("quote_basis"),
                "quote_provider": (price_point or {}).get("provider"),
                "quote_status": (price_point or {}).get("status"),
                "quote_price_unit": (price_point or {}).get("price_unit"),
                "quote_price_scale": (price_point or {}).get("price_scale"),
                "market_value": market_value,
                "market_value_base": converted_market_value,
                "day_change_pct": day_change_pct,
                "day_change_value": day_change_value,
                "day_change_value_base": converted_day_change_value,
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
                    "price-nav-fx" if converted_market_value is not None else "unpriced"
                ),
            }
        )

    rows.extend(
        build_cash_rows(
            cash_balances=list(cash_balances or []),
            as_of_date=as_of_date,
            base_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
        )
    )
    rows.extend(
        build_pending_rows(
            pending_balances=list(pending_balances or []),
            as_of_date=as_of_date,
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
