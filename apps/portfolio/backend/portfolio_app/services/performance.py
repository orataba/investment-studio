from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, timedelta
from math import isfinite, sqrt

from portfolio_app.services.asset_charts import build_asset_sparkline_from_detail
from portfolio_app.core.settings import get_settings
from portfolio_app.services.instrument_registry import (
    InstrumentRegistryError,
    get_platform_fx_rates,
    get_registry_instrument_detail,
)
from portfolio_app.services.ledger import (
    build_position_lots,
    derive_ledger_postings,
    ledger_posting_effective_date_iso,
)


DEFAULT_VALUATION_CUTOFF_POLICY = "latest_complete_eod"
EXTERNAL_CASH_IN_TYPES = {"deposit"}
EXTERNAL_CASH_OUT_TYPES = {"withdrawal"}
EARNINGS_TRANSACTION_TYPES = {"dividend", "coupon", "interest", "dividend_reinvestment"}
REALIZED_GAIN_TRANSACTION_TYPES = {"sell", "maturity_redemption"}
NON_CAPITALIZED_ATTACHED_CHARGE_TRANSACTION_TYPES = {
    "dividend",
    "coupon",
    "dividend_reinvestment",
    "interest",
    "return_of_capital",
}
DAYS_PER_YEAR = 365.25


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


def _normalized_currency(value: object, *, fallback: str = "USD") -> str:
    normalized = str(value or "").strip().upper()
    return normalized or fallback


def _transaction_sort_key(transaction: dict[str, object]) -> tuple[str, str, str, str, str]:
    return (
        str(transaction.get("trade_date") or ""),
        str(transaction.get("trade_at") or ""),
        str(transaction.get("created_at") or ""),
        str(transaction.get("transaction_id") or ""),
        str(transaction.get("settlement_date") or ""),
    )


def _iter_dates(start_date: date, end_date: date) -> list[date]:
    if end_date < start_date:
        return []
    span = (end_date - start_date).days
    return [start_date + timedelta(days=offset) for offset in range(span + 1)]


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


def _account_cost_methods(accounts: list[dict[str, object]]) -> dict[str, str]:
    return {
        str(account.get("account_id") or ""): str(account.get("cost_basis_method") or "fifo")
        for account in accounts
        if str(account.get("account_type") or "") == "securities_account"
    }


def _account_currency_map(accounts: list[dict[str, object]]) -> dict[str, str]:
    return {
        str(account.get("account_id") or ""): _normalized_currency(account.get("currency"))
        for account in accounts
    }


def _resolve_portfolio_valuation_timezone(portfolio: dict[str, object]) -> str:
    settings = get_settings()
    return str(portfolio.get("valuation_timezone") or settings.default_trade_timezone)


def _resolve_portfolio_valuation_cutoff_policy(portfolio: dict[str, object]) -> str:
    return str(portfolio.get("valuation_cutoff_policy") or DEFAULT_VALUATION_CUTOFF_POLICY)


def _instrument_detail_cache_get(
    asset_id: str,
    cache: dict[str, dict[str, object] | None],
) -> dict[str, object] | None:
    if asset_id not in cache:
        cache[asset_id] = get_registry_instrument_detail(asset_id)
    return cache[asset_id]


def _normalized_policy_bases(detail: dict[str, object], role: str) -> list[str]:
    policy = detail.get("quote_selection_policy", {})
    if not isinstance(policy, dict):
        return []
    raw_values = policy.get(role)
    if not isinstance(raw_values, list):
        return []
    normalized: list[str] = []
    for raw_value in raw_values:
        value = str(raw_value or "").strip()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


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


def _select_market_point_as_of(
    *,
    detail: dict[str, object],
    role: str,
    as_of_date: date,
) -> dict[str, object] | None:
    points_by_basis = _market_points_by_basis(detail)
    candidate_bases = [
        *_normalized_policy_bases(detail, role),
        *_normalized_policy_bases(detail, "reference"),
    ]
    seen_bases: set[str] = set()
    for quote_basis in candidate_bases:
        if quote_basis in seen_bases:
            continue
        seen_bases.add(quote_basis)
        point = _latest_point_on_or_before(points_by_basis.get(quote_basis, []), as_of_date)
        if point is not None:
            resolved_value = _safe_float(point.get("value"))
            if resolved_value is None:
                continue
            point_date = _parse_iso_date(point.get("as_of_date"))
            return {
                "value": resolved_value,
                "as_of_date": point_date,
                "currency": _normalized_currency(point.get("currency"), fallback=str(detail.get("currency") or "USD")),
                "metric_family": str(point.get("metric_family") or ""),
                "quote_basis": quote_basis,
                "provider": point.get("provider"),
                "status": str(point.get("status") or "complete"),
                "stale": point_date is not None and point_date < as_of_date,
            }

    fallback_points: list[dict[str, object]] = []
    for points in points_by_basis.values():
        fallback_points.extend(points)
    fallback_points.sort(key=lambda item: str(item.get("as_of_date") or ""))
    point = _latest_point_on_or_before(fallback_points, as_of_date)
    if point is None:
        return None
    resolved_value = _safe_float(point.get("value"))
    if resolved_value is None:
        return None
    point_date = _parse_iso_date(point.get("as_of_date"))
    return {
        "value": resolved_value,
        "as_of_date": point_date,
        "currency": _normalized_currency(point.get("currency"), fallback=str(detail.get("currency") or "USD")),
        "metric_family": str(point.get("metric_family") or ""),
        "quote_basis": str(point.get("quote_basis") or ""),
        "provider": point.get("provider"),
        "status": str(point.get("status") or "complete"),
        "stale": point_date is not None and point_date < as_of_date,
    }


def _fx_direct_asset_map(fx_payload: dict[str, object]) -> dict[tuple[str, str], str]:
    direct_assets: dict[tuple[str, str], str] = {}
    for item in fx_payload.get("rates", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("source_kind") or "") != "direct":
            continue
        base_currency = _normalized_currency(item.get("base_currency"), fallback="")
        quote_currency = _normalized_currency(item.get("quote_currency"), fallback="")
        asset_id = str(item.get("asset_id") or "").strip()
        if base_currency and quote_currency and asset_id:
            direct_assets[(base_currency, quote_currency)] = asset_id
    return direct_assets


def _direct_fx_point_as_of(
    *,
    asset_id: str,
    as_of_date: date,
    instrument_detail_cache: dict[str, dict[str, object] | None],
) -> dict[str, object] | None:
    detail = _instrument_detail_cache_get(asset_id, instrument_detail_cache)
    if not isinstance(detail, dict):
        return None
    market_data = detail.get("market_data", [])
    if not isinstance(market_data, list):
        return None
    points = [
        point
        for point in market_data
        if isinstance(point, dict)
        and str(point.get("metric_family") or "") == "fx"
        and str(point.get("quote_basis") or "") == "spot"
    ]
    points.sort(key=lambda item: str(item.get("as_of_date") or ""))
    point = _latest_point_on_or_before(points, as_of_date)
    if point is None:
        return None
    rate = _safe_float(point.get("value"))
    point_date = _parse_iso_date(point.get("as_of_date"))
    if rate is None or rate <= 0 or point_date is None:
        return None
    return {
        "rate": rate,
        "as_of_date": point_date,
        "status": str(point.get("status") or "complete"),
        "stale": point_date < as_of_date,
    }


def resolve_fx_rate_on(
    *,
    as_of_date: date,
    base_currency: str,
    quote_currency: str,
    direct_assets: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
) -> dict[str, object] | None:
    normalized_base = _normalized_currency(base_currency, fallback="")
    normalized_quote = _normalized_currency(quote_currency, fallback="")
    if not normalized_base or not normalized_quote:
        return None
    if normalized_base == normalized_quote:
        return {
            "rate": 1.0,
            "as_of_date": as_of_date,
            "status": "complete",
            "stale": False,
        }

    direct_asset_id = direct_assets.get((normalized_base, normalized_quote))
    if direct_asset_id:
        direct_point = _direct_fx_point_as_of(
            asset_id=direct_asset_id,
            as_of_date=as_of_date,
            instrument_detail_cache=instrument_detail_cache,
        )
        if direct_point is not None:
            return direct_point

    inverse_asset_id = direct_assets.get((normalized_quote, normalized_base))
    if inverse_asset_id:
        inverse_point = _direct_fx_point_as_of(
            asset_id=inverse_asset_id,
            as_of_date=as_of_date,
            instrument_detail_cache=instrument_detail_cache,
        )
        if inverse_point is not None:
            return {
                "rate": 1.0 / float(inverse_point["rate"]),
                "as_of_date": inverse_point["as_of_date"],
                "status": inverse_point["status"],
                "stale": bool(inverse_point["stale"]),
            }

    pivot_currency = "USD"
    if normalized_base == pivot_currency or normalized_quote == pivot_currency:
        return None

    base_leg = resolve_fx_rate_on(
        as_of_date=as_of_date,
        base_currency=pivot_currency,
        quote_currency=normalized_base,
        direct_assets=direct_assets,
        instrument_detail_cache=instrument_detail_cache,
    )
    quote_leg = resolve_fx_rate_on(
        as_of_date=as_of_date,
        base_currency=pivot_currency,
        quote_currency=normalized_quote,
        direct_assets=direct_assets,
        instrument_detail_cache=instrument_detail_cache,
    )
    if base_leg is None or quote_leg is None:
        return None

    base_rate = _safe_float(base_leg.get("rate"))
    quote_rate = _safe_float(quote_leg.get("rate"))
    base_date = _parse_iso_date(base_leg.get("as_of_date"))
    quote_date = _parse_iso_date(quote_leg.get("as_of_date"))
    if base_rate is None or quote_rate is None or base_rate <= 0 or base_date is None or quote_date is None:
        return None
    return {
        "rate": quote_rate / base_rate,
        "as_of_date": min(base_date, quote_date),
        "status": "partial" if "partial" in {base_leg.get("status"), quote_leg.get("status")} else "complete",
        "stale": bool(base_leg.get("stale")) or bool(quote_leg.get("stale")),
    }


def convert_amount_on(
    amount: float | None,
    *,
    as_of_date: date,
    from_currency: str,
    to_currency: str,
    direct_fx_assets: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
) -> tuple[float | None, bool]:
    if amount is None:
        return None, False
    resolved_fx = resolve_fx_rate_on(
        as_of_date=as_of_date,
        base_currency=from_currency,
        quote_currency=to_currency,
        direct_assets=direct_fx_assets,
        instrument_detail_cache=instrument_detail_cache,
    )
    if resolved_fx is None:
        return None, False
    rate = _safe_float(resolved_fx.get("rate"))
    if rate is None or rate <= 0:
        return None, False
    return float(amount) * rate, bool(resolved_fx.get("stale"))


def _position_buckets_from_lots(position_lots: list[dict[str, object]]) -> list[dict[str, object]]:
    positions_by_asset: dict[str, dict[str, object]] = {}
    for position_lot in position_lots:
        asset_id = str(position_lot.get("asset_id") or "")
        if not asset_id:
            continue
        bucket = positions_by_asset.setdefault(
            asset_id,
            {
                "asset_id": asset_id,
                "instrument_ref": deepcopy(position_lot.get("instrument_ref")),
                "currency": _normalized_currency(position_lot.get("currency")),
                "quantity": 0.0,
                "cost_basis": 0.0,
                "open_position_lot_count": 0,
                "account_ids": set(),
                "cost_basis_methods": set(),
            },
        )
        bucket["quantity"] += _safe_float(position_lot.get("remaining_quantity")) or 0.0
        bucket["cost_basis"] += _safe_float(position_lot.get("remaining_cost_basis")) or 0.0
        bucket["open_position_lot_count"] += 1
        bucket["account_ids"].add(str(position_lot.get("account_id") or ""))
        bucket["cost_basis_methods"].add(str(position_lot.get("cost_basis_method") or "fifo"))
    rendered_buckets: list[dict[str, object]] = []
    for bucket in positions_by_asset.values():
        if abs(_safe_float(bucket.get("quantity")) or 0.0) <= 1e-9:
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
                **{key: value for key, value in bucket.items() if key != "cost_basis_methods"},
                "account_ids": account_ids,
                "account_count": len(account_ids),
                "cost_basis_method": cost_basis_methods[0] if len(cost_basis_methods) == 1 else "mixed",
            }
        )
    return rendered_buckets


def _position_buckets_by_account_asset_from_lots(position_lots: list[dict[str, object]]) -> list[dict[str, object]]:
    positions_by_account_asset: dict[tuple[str, str], dict[str, object]] = {}
    for position_lot in position_lots:
        account_id = str(position_lot.get("account_id") or "")
        asset_id = str(position_lot.get("asset_id") or "")
        if not account_id or not asset_id:
            continue
        bucket = positions_by_account_asset.setdefault(
            (account_id, asset_id),
            {
                "account_id": account_id,
                "asset_id": asset_id,
                "instrument_ref": deepcopy(position_lot.get("instrument_ref")),
                "currency": _normalized_currency(position_lot.get("currency")),
                "quantity": 0.0,
                "cost_basis": 0.0,
                "open_position_lot_count": 0,
                "cost_basis_methods": set(),
            },
        )
        bucket["quantity"] += _safe_float(position_lot.get("remaining_quantity")) or 0.0
        bucket["cost_basis"] += _safe_float(position_lot.get("remaining_cost_basis")) or 0.0
        bucket["open_position_lot_count"] += 1
        bucket["cost_basis_methods"].add(str(position_lot.get("cost_basis_method") or "fifo"))

    rendered_buckets: list[dict[str, object]] = []
    for bucket in positions_by_account_asset.values():
        if abs(_safe_float(bucket.get("quantity")) or 0.0) <= 1e-9:
            continue
        raw_methods = bucket.get("cost_basis_methods")
        cost_basis_methods = (
            sorted(method for method in raw_methods if method)
            if isinstance(raw_methods, set)
            else []
        )
        rendered_buckets.append(
            {
                **{key: value for key, value in bucket.items() if key != "cost_basis_methods"},
                "cost_basis_method": cost_basis_methods[0] if len(cost_basis_methods) == 1 else "mixed",
            }
        )
    return rendered_buckets


def _resolve_snapshot_window(
    portfolio: dict[str, object],
    transactions: list[dict[str, object]],
    *,
    start_date: date | None,
    end_date: date | None,
) -> tuple[date, date] | None:
    transaction_dates = [
        parsed
        for parsed in (_parse_iso_date(item.get("trade_date")) for item in transactions)
        if parsed is not None
    ]
    portfolio_as_of = _parse_iso_date(portfolio.get("as_of_date"))
    if not transaction_dates and portfolio_as_of is None:
        return None

    resolved_start = start_date or (min(transaction_dates) if transaction_dates else portfolio_as_of)
    resolved_end = end_date or portfolio_as_of or (max(transaction_dates) if transaction_dates else None)
    if resolved_start is None or resolved_end is None:
        return None
    if resolved_end < resolved_start:
        return None
    return resolved_start, resolved_end


def _build_materialized_holding_rows(
    *,
    account_asset_buckets: list[dict[str, object]],
    as_of_date: date,
    base_currency: str,
    direct_fx_assets: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
    nav: float | None,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for bucket in account_asset_buckets:
        account_id = str(bucket.get("account_id") or "")
        asset_id = str(bucket.get("asset_id") or "")
        if not account_id or not asset_id:
            continue

        currency = _normalized_currency(bucket.get("currency"), fallback=base_currency)
        quantity = _safe_float(bucket.get("quantity")) or 0.0
        cost_basis = _safe_float(bucket.get("cost_basis"))
        converted_cost_basis, _ = convert_amount_on(
            cost_basis,
            as_of_date=as_of_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_assets=direct_fx_assets,
            instrument_detail_cache=instrument_detail_cache,
        )
        detail = _instrument_detail_cache_get(asset_id, instrument_detail_cache)
        price_point = (
            _select_market_point_as_of(detail=detail, role="valuation", as_of_date=as_of_date)
            if isinstance(detail, dict)
            else None
        )
        last_price = _safe_float((price_point or {}).get("value"))
        market_value = _position_market_value(
            quantity=quantity,
            last_price=last_price,
            instrument_ref=(bucket.get("instrument_ref") if isinstance(bucket.get("instrument_ref"), dict) else None),
        )
        converted_market_value, _ = convert_amount_on(
            market_value,
            as_of_date=as_of_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_assets=direct_fx_assets,
            instrument_detail_cache=instrument_detail_cache,
        )
        rows.append(
            {
                "line_id": f"{account_id}:{asset_id}",
                "account_id": account_id,
                "asset_id": asset_id,
                "instrument_ref": deepcopy(bucket.get("instrument_ref") or {}),
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
                "market_value": market_value,
                "market_value_base": converted_market_value,
                "currency": currency,
                "portfolio_weight": (
                    converted_market_value / nav
                    if converted_market_value is not None and nav is not None and nav > 1e-9
                    else None
                ),
                "account_ids": [account_id],
                "account_count": 1,
                "open_position_lot_count": int(bucket.get("open_position_lot_count") or 0),
                "price_chart": (
                    build_asset_sparkline_from_detail(
                        detail,
                        asset_id=asset_id,
                        as_of_date=as_of_date,
                    )
                    if isinstance(detail, dict)
                    else []
                ),
                "coverage_status": "price-nav-fx" if converted_market_value is not None else "unpriced",
            }
        )

    rows.sort(
        key=lambda item: (
            str(item.get("account_id") or ""),
            -((_safe_float(item.get("market_value_base")) or 0.0)),
            str(item.get("asset_id") or ""),
        )
    )
    return rows


def _build_single_date_snapshot(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    as_of_date: date,
) -> dict[str, object]:
    portfolio_id = str(portfolio.get("portfolio_id") or "")
    if portfolio_id:
        from portfolio_app.services.daily_snapshots import get_materialized_daily_snapshot

        materialized_snapshot = get_materialized_daily_snapshot(portfolio_id, as_of_date)
        if materialized_snapshot is not None:
            return materialized_snapshot

    portfolio_view = deepcopy(portfolio)
    portfolio_view["as_of_date"] = as_of_date.isoformat()
    historical_start_date = min(
        (
            parsed_trade_date
            for parsed_trade_date in (_parse_iso_date(item.get("trade_date")) for item in transactions)
            if parsed_trade_date is not None
        ),
        default=as_of_date,
    )
    snapshots = build_daily_portfolio_snapshots(
        portfolio_view,
        accounts,
        transactions,
        start_date=historical_start_date,
        end_date=as_of_date,
    )
    if snapshots:
        return snapshots[-1]
    return {
        "as_of_date": as_of_date,
        "coverage_state": "unavailable",
        "stale_price_flag": False,
        "stale_fx_flag": False,
        "nav": None,
        "unrealized_pnl": None,
        "market_observation_count": 0,
        "return_observation_eligible": False,
    }


def _build_period_start_boundary_transactions(
    transactions: list[dict[str, object]],
    *,
    start_date: date,
) -> list[dict[str, object]]:
    start_iso = start_date.isoformat()
    boundary_transactions: list[dict[str, object]] = []
    for transaction in sorted(transactions, key=_transaction_sort_key):
        trade_date = str(transaction.get("trade_date") or "")
        if trade_date < start_iso:
            boundary_transactions.append(transaction)
            continue
        if trade_date == start_iso and str(transaction.get("transaction_type") or "") == "opening_balance":
            boundary_transactions.append(transaction)
    return boundary_transactions


def _transactions_in_period(
    transactions: list[dict[str, object]],
    *,
    start_date: date,
    end_date: date,
) -> list[dict[str, object]]:
    start_iso = start_date.isoformat()
    end_iso = end_date.isoformat()
    return [
        transaction
        for transaction in sorted(transactions, key=_transaction_sort_key)
        if start_iso <= str(transaction.get("trade_date") or "") <= end_iso
    ]


def _transactions_as_of_end_date(
    transactions: list[dict[str, object]],
    *,
    end_date: date,
) -> list[dict[str, object]]:
    end_iso = end_date.isoformat()
    return [
        transaction
        for transaction in sorted(transactions, key=_transaction_sort_key)
        if str(transaction.get("trade_date") or "") <= end_iso
    ]


def _initial_boundary_date(resolved_start_date: date, requested_start_date: date | None) -> date:
    return resolved_start_date - timedelta(days=1) if requested_start_date is not None else resolved_start_date


def _sum_period_transaction_buckets(
    transactions: list[dict[str, object]],
    *,
    base_currency: str,
    direct_fx_assets: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
) -> dict[str, object]:
    deposits = 0.0
    withdrawals = 0.0
    earnings = 0.0
    fees = 0.0
    taxes = 0.0
    return_of_capital_amount = 0.0
    coverage_complete = True
    stale_fx_flag = False

    def convert_component(amount: float, *, trade_date: date, currency: str) -> float | None:
        nonlocal coverage_complete, stale_fx_flag
        converted_amount, is_stale = convert_amount_on(
            amount,
            as_of_date=trade_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_assets=direct_fx_assets,
            instrument_detail_cache=instrument_detail_cache,
        )
        if converted_amount is None:
            coverage_complete = False
            return None
        stale_fx_flag = stale_fx_flag or is_stale
        return converted_amount

    for transaction in transactions:
        trade_date = _parse_iso_date(transaction.get("trade_date"))
        if trade_date is None:
            coverage_complete = False
            continue
        currency = _normalized_currency(transaction.get("currency"), fallback=base_currency)
        transaction_type = str(transaction.get("transaction_type") or "")
        gross_amount = _safe_float(transaction.get("gross_amount")) or 0.0
        fee_amount = _safe_float(transaction.get("fees")) or 0.0
        tax_amount = _safe_float(transaction.get("taxes")) or 0.0

        if transaction_type in EXTERNAL_CASH_IN_TYPES:
            converted = convert_component(gross_amount, trade_date=trade_date, currency=currency)
            if converted is not None:
                deposits += converted
        elif transaction_type in EXTERNAL_CASH_OUT_TYPES:
            converted = convert_component(gross_amount, trade_date=trade_date, currency=currency)
            if converted is not None:
                withdrawals += converted

        if transaction_type in EARNINGS_TRANSACTION_TYPES:
            converted = convert_component(gross_amount, trade_date=trade_date, currency=currency)
            if converted is not None:
                earnings += converted

        if transaction_type == "return_of_capital":
            converted = convert_component(gross_amount, trade_date=trade_date, currency=currency)
            if converted is not None:
                return_of_capital_amount += converted

        if transaction_type == "fee":
            converted = convert_component(gross_amount, trade_date=trade_date, currency=currency)
            if converted is not None:
                fees += converted
        if transaction_type == "tax":
            converted = convert_component(gross_amount, trade_date=trade_date, currency=currency)
            if converted is not None:
                taxes += converted

        if fee_amount > 0 and transaction_type in NON_CAPITALIZED_ATTACHED_CHARGE_TRANSACTION_TYPES:
            converted = convert_component(fee_amount, trade_date=trade_date, currency=currency)
            if converted is not None:
                fees += converted
        if tax_amount > 0 and transaction_type in NON_CAPITALIZED_ATTACHED_CHARGE_TRANSACTION_TYPES:
            converted = convert_component(tax_amount, trade_date=trade_date, currency=currency)
            if converted is not None:
                taxes += converted

    return {
        "deposits": deposits,
        "withdrawals": withdrawals,
        "net_external_inflow": deposits - withdrawals,
        "earnings": earnings,
        "fees": fees,
        "taxes": taxes,
        "return_of_capital_amount": return_of_capital_amount,
        "coverage_complete": coverage_complete,
        "stale_fx_flag": stale_fx_flag,
    }


def _sum_period_realized_capital_gains(
    position_lots: list[dict[str, object]],
    *,
    start_date: date | None,
    end_date: date | None,
    base_currency: str,
    direct_fx_assets: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
) -> dict[str, object]:
    realized_capital_gains = 0.0
    coverage_complete = True
    stale_fx_flag = False
    start_iso = start_date.isoformat() if start_date is not None else None
    end_iso = end_date.isoformat() if end_date is not None else None

    for position_lot in position_lots:
        currency = _normalized_currency(position_lot.get("currency"), fallback=base_currency)
        realizations = position_lot.get("realizations") or []
        if not isinstance(realizations, list):
            continue
        for realization in realizations:
            if not isinstance(realization, dict):
                continue
            transaction_type = str(realization.get("transaction_type") or "")
            trade_date = _parse_iso_date(realization.get("trade_date"))
            realized_pnl = _safe_float(realization.get("realized_pnl"))
            if transaction_type not in REALIZED_GAIN_TRANSACTION_TYPES:
                continue
            if trade_date is None:
                continue
            trade_date_iso = trade_date.isoformat()
            if start_iso is not None and trade_date_iso < start_iso:
                continue
            if end_iso is not None and trade_date_iso > end_iso:
                continue
            if realized_pnl is None:
                coverage_complete = False
                continue
            converted_amount, is_stale = convert_amount_on(
                realized_pnl,
                as_of_date=trade_date,
                from_currency=currency,
                to_currency=base_currency,
                direct_fx_assets=direct_fx_assets,
                instrument_detail_cache=instrument_detail_cache,
            )
            if converted_amount is None:
                coverage_complete = False
                continue
            realized_capital_gains += converted_amount
            stale_fx_flag = stale_fx_flag or is_stale

    return {
        "realized_capital_gains": realized_capital_gains,
        "coverage_complete": coverage_complete,
        "stale_fx_flag": stale_fx_flag,
    }


def _daily_external_flow_breakdown(
    transactions: list[dict[str, object]],
    as_of_date: date,
    *,
    base_currency: str,
    direct_fx_assets: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
) -> dict[str, object]:
    as_of_iso = as_of_date.isoformat()
    same_day_transactions = [
        transaction
        for transaction in transactions
        if str(transaction.get("trade_date") or "") == as_of_iso
    ]
    buckets = _sum_period_transaction_buckets(
        same_day_transactions,
        base_currency=base_currency,
        direct_fx_assets=direct_fx_assets,
        instrument_detail_cache=instrument_detail_cache,
    )
    return {
        "external_cash_in": buckets["deposits"],
        "external_cash_out": buckets["withdrawals"],
        "net_external_inflow": buckets["net_external_inflow"],
        "coverage_complete": buckets["coverage_complete"],
        "stale_fx_flag": buckets["stale_fx_flag"],
    }


def build_daily_portfolio_snapshots(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    include_materialized_rows: bool = False,
) -> list[dict[str, object]]:
    window = _resolve_snapshot_window(
        portfolio,
        transactions,
        start_date=start_date,
        end_date=end_date,
    )
    if window is None:
        return []

    resolved_start_date, resolved_end_date = window
    sorted_transactions = sorted(transactions, key=_transaction_sort_key)
    account_cost_methods = _account_cost_methods(accounts)
    account_currency_map = _account_currency_map(accounts)
    account_name_map = _account_name_map(accounts)
    portfolio_id = str(portfolio.get("portfolio_id") or "")
    base_currency = _normalized_currency(portfolio.get("base_currency"))
    valuation_timezone = _resolve_portfolio_valuation_timezone(portfolio)
    valuation_cutoff_policy = _resolve_portfolio_valuation_cutoff_policy(portfolio)

    fx_payload = get_platform_fx_rates()
    direct_fx_assets = _fx_direct_asset_map(fx_payload)
    instrument_detail_cache: dict[str, dict[str, object] | None] = {}
    transactions_by_date: dict[str, list[dict[str, object]]] = defaultdict(list)
    if include_materialized_rows:
        for transaction in sorted_transactions:
            transactions_by_date[str(transaction.get("trade_date") or "")].append(transaction)

    snapshots: list[dict[str, object]] = []
    last_complete_nav: float | None = None
    growth_index = 1.0
    peak_growth_index = 1.0
    has_return_history = False
    previous_cash_balances_by_currency: dict[str, float] = {}
    previous_cash_balance_date: date | None = None
    cumulative_cash_currency_gains = 0.0
    cash_currency_gain_history_complete = True
    previous_position_market_values_local_by_asset: dict[str, dict[str, object]] = {}
    previous_position_market_value_date: date | None = None
    cumulative_asset_currency_gains = 0.0
    asset_currency_gain_history_complete = True
    previous_contribution_states_by_axis: dict[str, dict[str, dict[str, object]]] = {
        "instrument": {},
        "account": {},
    }

    for as_of_date in _iter_dates(resolved_start_date, resolved_end_date):
        as_of_iso = as_of_date.isoformat()
        transactions_as_of = [
            item
            for item in sorted_transactions
            if str(item.get("trade_date") or "") <= as_of_iso
        ]
        postings = derive_ledger_postings(
            portfolio_id,
            transactions_as_of,
            account_cost_methods=account_cost_methods,
            account_currency_map=account_currency_map,
        )
        position_lots = build_position_lots(
            portfolio_id,
            accounts,
            transactions_as_of,
        )
        all_position_lots = position_lots
        open_position_lots = [position_lot for position_lot in all_position_lots if position_lot.get("status") == "open"]
        position_buckets = _position_buckets_from_lots(open_position_lots)
        account_asset_buckets = (
            _position_buckets_by_account_asset_from_lots(open_position_lots)
            if include_materialized_rows
            else []
        )
        transaction_buckets = _sum_period_transaction_buckets(
            transactions_as_of,
            base_currency=base_currency,
            direct_fx_assets=direct_fx_assets,
            instrument_detail_cache=instrument_detail_cache,
        )
        realized_pnl_summary = _sum_period_realized_capital_gains(
            position_lots,
            start_date=None,
            end_date=as_of_date,
            base_currency=base_currency,
            direct_fx_assets=direct_fx_assets,
            instrument_detail_cache=instrument_detail_cache,
        )
        pnl_components = {
            "realized_pnl": realized_pnl_summary["realized_capital_gains"],
            "income_cash_amount": transaction_buckets["earnings"],
            "expense_cash_amount": transaction_buckets["fees"] + transaction_buckets["taxes"],
            "return_of_capital_amount": transaction_buckets["return_of_capital_amount"],
        }

        cash_balance_base = 0.0
        cash_complete = True
        pending_settlement_base = 0.0
        pending_settlement_complete = True
        stale_fx_flag = False
        cash_balances_by_currency: dict[str, float] = defaultdict(float)
        for posting in postings:
            cash_delta = _safe_float(posting.get("cash_amount_delta"))
            if cash_delta is None:
                continue
            posting_currency = _normalized_currency(posting.get("currency"), fallback=base_currency)
            posting_effective_date = ledger_posting_effective_date_iso(posting)
            converted_cash_delta, is_stale = convert_amount_on(
                cash_delta,
                as_of_date=as_of_date,
                from_currency=posting_currency,
                to_currency=base_currency,
                direct_fx_assets=direct_fx_assets,
                instrument_detail_cache=instrument_detail_cache,
            )
            if converted_cash_delta is None:
                if posting_effective_date <= as_of_iso:
                    cash_complete = False
                else:
                    pending_settlement_complete = False
                continue
            stale_fx_flag = stale_fx_flag or is_stale
            if posting_effective_date <= as_of_iso:
                cash_balances_by_currency[posting_currency] += cash_delta
                cash_balance_base += converted_cash_delta
            else:
                pending_settlement_base += converted_cash_delta

        daily_cash_currency_gain = 0.0
        cash_currency_gain_complete = True
        if previous_cash_balance_date is not None:
            for currency, previous_balance in previous_cash_balances_by_currency.items():
                if currency == base_currency or abs(previous_balance) <= 1e-9:
                    continue
                previous_balance_at_previous_fx, previous_fx_stale = convert_amount_on(
                    previous_balance,
                    as_of_date=previous_cash_balance_date,
                    from_currency=currency,
                    to_currency=base_currency,
                    direct_fx_assets=direct_fx_assets,
                    instrument_detail_cache=instrument_detail_cache,
                )
                previous_balance_at_current_fx, current_fx_stale = convert_amount_on(
                    previous_balance,
                    as_of_date=as_of_date,
                    from_currency=currency,
                    to_currency=base_currency,
                    direct_fx_assets=direct_fx_assets,
                    instrument_detail_cache=instrument_detail_cache,
                )
                if previous_balance_at_previous_fx is None or previous_balance_at_current_fx is None:
                    cash_currency_gain_complete = False
                    continue
                daily_cash_currency_gain += previous_balance_at_current_fx - previous_balance_at_previous_fx
                stale_fx_flag = stale_fx_flag or previous_fx_stale or current_fx_stale

        cash_currency_gains = None
        if cash_currency_gain_history_complete and cash_currency_gain_complete:
            cumulative_cash_currency_gains += daily_cash_currency_gain
            cash_currency_gains = cumulative_cash_currency_gains
        else:
            cash_currency_gain_history_complete = False

        priced_position_count = 0
        total_position_count = len(position_buckets)
        position_market_value_base = 0.0
        open_cost_basis_base = 0.0
        cost_basis_complete = True
        position_valuation_complete = True
        stale_price_flag = False
        fresh_price_count = 0
        current_position_market_values_local_by_asset: dict[str, dict[str, object]] = {}
        for bucket in position_buckets:
            currency = _normalized_currency(bucket.get("currency"), fallback=base_currency)
            quantity = _safe_float(bucket.get("quantity")) or 0.0
            cost_basis = _safe_float(bucket.get("cost_basis")) or 0.0
            converted_cost_basis, cost_basis_fx_stale = convert_amount_on(
                cost_basis,
                as_of_date=as_of_date,
                from_currency=currency,
                to_currency=base_currency,
                direct_fx_assets=direct_fx_assets,
                instrument_detail_cache=instrument_detail_cache,
            )
            if converted_cost_basis is None:
                cost_basis_complete = False
            else:
                open_cost_basis_base += converted_cost_basis
                stale_fx_flag = stale_fx_flag or cost_basis_fx_stale

            detail = _instrument_detail_cache_get(str(bucket.get("asset_id") or ""), instrument_detail_cache)
            if not isinstance(detail, dict):
                position_valuation_complete = False
                continue
            price_point = _select_market_point_as_of(
                detail=detail,
                role="valuation",
                as_of_date=as_of_date,
            )
            if price_point is None:
                position_valuation_complete = False
                continue
            market_value_local = _position_market_value(
                quantity=quantity,
                last_price=_safe_float(price_point.get("value")),
                instrument_ref=(
                    bucket.get("instrument_ref")
                    if isinstance(bucket.get("instrument_ref"), dict)
                    else None
                ),
            )
            converted_market_value, valuation_fx_stale = convert_amount_on(
                market_value_local,
                as_of_date=as_of_date,
                from_currency=currency,
                to_currency=base_currency,
                direct_fx_assets=direct_fx_assets,
                instrument_detail_cache=instrument_detail_cache,
            )
            if market_value_local is None or converted_market_value is None:
                position_valuation_complete = False
                continue
            price_point_date = _parse_iso_date(price_point.get("as_of_date"))
            if price_point_date == as_of_date:
                fresh_price_count += 1
            current_position_market_values_local_by_asset[str(bucket.get("asset_id") or "")] = {
                "currency": currency,
                "market_value_local": market_value_local,
            }
            priced_position_count += 1
            position_market_value_base += converted_market_value
            stale_price_flag = stale_price_flag or bool(price_point.get("stale"))
            stale_fx_flag = stale_fx_flag or valuation_fx_stale

        daily_asset_currency_gain = 0.0
        asset_currency_gain_complete = True
        if previous_position_market_value_date is not None:
            for asset_id, previous_position_value in previous_position_market_values_local_by_asset.items():
                del asset_id
                currency = _normalized_currency(previous_position_value.get("currency"), fallback=base_currency)
                if currency == base_currency:
                    continue
                previous_market_value_local = _safe_float(previous_position_value.get("market_value_local"))
                if previous_market_value_local is None or abs(previous_market_value_local) <= 1e-9:
                    continue
                previous_value_at_previous_fx, previous_fx_stale = convert_amount_on(
                    previous_market_value_local,
                    as_of_date=previous_position_market_value_date,
                    from_currency=currency,
                    to_currency=base_currency,
                    direct_fx_assets=direct_fx_assets,
                    instrument_detail_cache=instrument_detail_cache,
                )
                previous_value_at_current_fx, current_fx_stale = convert_amount_on(
                    previous_market_value_local,
                    as_of_date=as_of_date,
                    from_currency=currency,
                    to_currency=base_currency,
                    direct_fx_assets=direct_fx_assets,
                    instrument_detail_cache=instrument_detail_cache,
                )
                if previous_value_at_previous_fx is None or previous_value_at_current_fx is None:
                    asset_currency_gain_complete = False
                    continue
                daily_asset_currency_gain += previous_value_at_current_fx - previous_value_at_previous_fx
                stale_fx_flag = stale_fx_flag or previous_fx_stale or current_fx_stale

        asset_currency_gains = None
        if asset_currency_gain_history_complete and asset_currency_gain_complete:
            cumulative_asset_currency_gains += daily_asset_currency_gain
            asset_currency_gains = cumulative_asset_currency_gains
        else:
            asset_currency_gain_history_complete = False

        coverage_state = "complete"
        if (
            not cash_complete
            or not pending_settlement_complete
            or not cost_basis_complete
            or not position_valuation_complete
        ):
            has_partial_content = bool(postings) or bool(position_buckets)
            coverage_state = "partial" if has_partial_content else "unavailable"
        if not cash_currency_gain_history_complete and coverage_state == "complete":
            coverage_state = "partial"
        if not asset_currency_gain_history_complete and coverage_state == "complete":
            coverage_state = "partial"
        if (
            coverage_state == "complete"
            and (
                not transaction_buckets["coverage_complete"]
                or not realized_pnl_summary["coverage_complete"]
            )
        ):
            coverage_state = "partial"

        stale_fx_flag = (
            stale_fx_flag
            or bool(transaction_buckets["stale_fx_flag"])
            or bool(realized_pnl_summary["stale_fx_flag"])
        )

        resolved_cash_balance = cash_balance_base if cash_complete else None
        resolved_position_market_value = (
            position_market_value_base if position_valuation_complete else None
        )
        resolved_open_cost_basis = open_cost_basis_base if cost_basis_complete else None
        nav = None
        unrealized_pnl = None
        total_pnl = None
        if (
            resolved_cash_balance is not None
            and resolved_position_market_value is not None
            and coverage_state == "complete"
        ):
            nav = resolved_cash_balance + pending_settlement_base + resolved_position_market_value
            if resolved_open_cost_basis is not None:
                unrealized_pnl = resolved_position_market_value - resolved_open_cost_basis
                total_pnl = (
                    (pnl_components["realized_pnl"] + pnl_components["income_cash_amount"])
                    - pnl_components["expense_cash_amount"]
                    + unrealized_pnl
                )
                if cash_currency_gains is not None:
                    total_pnl += cash_currency_gains
                elif not cash_currency_gain_history_complete:
                    total_pnl = None
                if total_pnl is not None:
                    if asset_currency_gains is not None:
                        total_pnl += asset_currency_gains
                    elif not asset_currency_gain_history_complete:
                        total_pnl = None

        flow_breakdown = _daily_external_flow_breakdown(
            sorted_transactions,
            as_of_date,
            base_currency=base_currency,
            direct_fx_assets=direct_fx_assets,
            instrument_detail_cache=instrument_detail_cache,
        )
        if coverage_state == "complete" and not flow_breakdown["coverage_complete"]:
            coverage_state = "partial"
        stale_fx_flag = stale_fx_flag or bool(flow_breakdown["stale_fx_flag"])
        beginning_nav = last_complete_nav
        absolute_change = None
        delta = None
        daily_twr = None
        if nav is not None and last_complete_nav is not None and flow_breakdown["coverage_complete"]:
            absolute_change = nav - last_complete_nav
            delta = absolute_change - flow_breakdown["net_external_inflow"]
            denominator = last_complete_nav + flow_breakdown["external_cash_in"]
            numerator = nav + flow_breakdown["external_cash_out"]
            if denominator > 1e-9 and numerator >= 0:
                daily_twr = (numerator / denominator) - 1.0

        if daily_twr is not None and isfinite(daily_twr):
            has_return_history = True
            growth_index *= 1.0 + daily_twr
            peak_growth_index = max(peak_growth_index, growth_index)

        cumulative_twr = (growth_index - 1.0) if has_return_history else None
        drawdown = (
            (growth_index / peak_growth_index) - 1.0
            if has_return_history and peak_growth_index > 0
            else None
        )

        if nav is not None:
            last_complete_nav = nav

        snapshot_payload: dict[str, object] = {
            "as_of_date": as_of_date,
            "base_currency": base_currency,
            "valuation_timezone": valuation_timezone,
            "valuation_cutoff_policy": valuation_cutoff_policy,
            "coverage_state": coverage_state,
            "stale_price_flag": stale_price_flag,
            "stale_fx_flag": stale_fx_flag,
            "total_position_count": total_position_count,
            "priced_position_count": priced_position_count,
            "market_observation_count": fresh_price_count,
            "return_observation_eligible": (
                daily_twr is not None
                and isfinite(daily_twr)
                and (fresh_price_count > 0 or abs(daily_twr) > 1e-12)
            ),
            "cash_balance": resolved_cash_balance,
            "pending_settlement": pending_settlement_base if pending_settlement_complete else None,
            "position_market_value": resolved_position_market_value,
            "nav": nav,
            "open_cost_basis": resolved_open_cost_basis,
            "unrealized_pnl": unrealized_pnl,
            "realized_pnl": pnl_components["realized_pnl"],
            "income_cash_amount": pnl_components["income_cash_amount"],
            "expense_cash_amount": pnl_components["expense_cash_amount"],
            "cash_currency_gains": cash_currency_gains,
            "asset_currency_gains": asset_currency_gains,
            "return_of_capital_amount": pnl_components["return_of_capital_amount"],
            "total_pnl": total_pnl,
            "external_cash_in": flow_breakdown["external_cash_in"],
            "external_cash_out": flow_breakdown["external_cash_out"],
            "net_external_inflow": flow_breakdown["net_external_inflow"],
            "beginning_nav": beginning_nav,
            "ending_nav": nav,
            "absolute_change": absolute_change,
            "delta": delta,
            "daily_twr": daily_twr,
            "cumulative_twr": cumulative_twr,
            "drawdown": drawdown,
        }

        if include_materialized_rows:
            snapshot_payload["_holding_rows"] = _build_materialized_holding_rows(
                account_asset_buckets=account_asset_buckets,
                as_of_date=as_of_date,
                base_currency=base_currency,
                direct_fx_assets=direct_fx_assets,
                instrument_detail_cache=instrument_detail_cache,
                nav=nav,
            )
            contribution_slices: list[dict[str, object]] = []
            for contribution_axis in ("instrument", "account"):
                current_states = _build_contribution_group_end_states(
                    axis=contribution_axis,
                    portfolio_id=portfolio_id,
                    accounts=accounts,
                    transactions_as_of=transactions_as_of,
                    position_lots=position_lots,
                    postings=postings,
                    as_of_date=as_of_date,
                    base_currency=base_currency,
                    account_cost_methods=account_cost_methods,
                    account_currency_map=account_currency_map,
                    account_name_map=account_name_map,
                    direct_fx_assets=direct_fx_assets,
                    instrument_detail_cache=instrument_detail_cache,
                )
                current_events = _build_contribution_daily_events(
                    axis=contribution_axis,
                    as_of_date=as_of_date,
                    position_lots=all_position_lots,
                    transactions_on_date=transactions_by_date.get(as_of_iso, []),
                    base_currency=base_currency,
                    account_name_map=account_name_map,
                    direct_fx_assets=direct_fx_assets,
                    instrument_detail_cache=instrument_detail_cache,
                )
                contribution_slices.extend(
                    _build_contribution_slices_for_date(
                        axis=contribution_axis,
                        as_of_date=as_of_date,
                        previous_states=previous_contribution_states_by_axis.get(contribution_axis, {}),
                        current_states=current_states,
                        current_events=current_events,
                        snapshot=snapshot_payload,
                        previous_date=as_of_date - timedelta(days=1),
                        base_currency=base_currency,
                        direct_fx_assets=direct_fx_assets,
                        instrument_detail_cache=instrument_detail_cache,
                    )
                )
                previous_contribution_states_by_axis[contribution_axis] = current_states
            snapshot_payload["_contribution_slices"] = contribution_slices

        snapshots.append(snapshot_payload)

        previous_cash_balances_by_currency = dict(cash_balances_by_currency)
        previous_cash_balance_date = as_of_date
        previous_position_market_values_local_by_asset = deepcopy(current_position_market_values_local_by_asset)
        previous_position_market_value_date = as_of_date

    return snapshots


def summarize_daily_snapshots(snapshots: list[dict[str, object]]) -> dict[str, object]:
    latest_complete = next(
        (
            snapshot
            for snapshot in reversed(snapshots)
            if str(snapshot.get("coverage_state") or "") == "complete" and snapshot.get("nav") is not None
        ),
        None,
    )
    return {
        "snapshot_count": len(snapshots),
        "complete_count": sum(1 for snapshot in snapshots if snapshot.get("coverage_state") == "complete"),
        "partial_count": sum(1 for snapshot in snapshots if snapshot.get("coverage_state") == "partial"),
        "unavailable_count": sum(1 for snapshot in snapshots if snapshot.get("coverage_state") == "unavailable"),
        "latest_complete_as_of_date": latest_complete.get("as_of_date") if latest_complete else None,
    }


def _year_fraction(start_date: date, end_date: date) -> float:
    return max((end_date - start_date).days / DAYS_PER_YEAR, 0.0)


def _periods_per_year_from_observations(
    *,
    observation_count: int,
    start_date: date | None,
    end_date: date | None,
) -> float | None:
    if observation_count < 1 or start_date is None or end_date is None:
        return None
    elapsed_days = (end_date - start_date).days
    if elapsed_days <= 0:
        return None
    return float(observation_count) / float(elapsed_days) * DAYS_PER_YEAR


def _sample_stddev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    if variance < 0:
        return None
    return sqrt(variance)


def _downside_deviation(values: list[float], *, minimum_acceptable_return: float = 0.0) -> float | None:
    if not values:
        return None
    downside_squares = [
        min(0.0, value - minimum_acceptable_return) ** 2
        for value in values
    ]
    if not any(square > 0 for square in downside_squares):
        return None
    return sqrt(sum(downside_squares) / len(downside_squares))


def _compound_daily_twr(snapshots: list[dict[str, object]]) -> float | None:
    growth_index = 1.0
    has_return = False
    for snapshot in snapshots:
        daily_twr = _safe_float(snapshot.get("daily_twr"))
        if daily_twr is None or not isfinite(daily_twr):
            continue
        growth_index *= 1.0 + daily_twr
        has_return = True
    return (growth_index - 1.0) if has_return else None


def _drawdown_stats(
    snapshots: list[dict[str, object]],
    *,
    start_anchor_date: date | None = None,
) -> dict[str, int | float | None]:
    growth_points: list[tuple[date, float]] = []
    growth_index = 1.0
    for snapshot in snapshots:
        daily_twr = _safe_float(snapshot.get("daily_twr"))
        snapshot_date = snapshot.get("as_of_date")
        if daily_twr is None or not isfinite(daily_twr) or not isinstance(snapshot_date, date):
            continue
        growth_index *= 1.0 + daily_twr
        growth_points.append((snapshot_date, growth_index))
    if not growth_points:
        return {
            "current_drawdown": None,
            "max_drawdown": None,
            "max_drawdown_days": None,
            "drawdown_duration_days": None,
        }

    running_peak = 1.0
    running_peak_date = start_anchor_date or growth_points[0][0]
    current_drawdown = 0.0
    max_drawdown = 0.0
    max_drawdown_peak_date = running_peak_date
    max_drawdown_trough_date = running_peak_date
    max_drawdown_peak_growth = running_peak
    recovery_index = None

    for point_date, growth_index in growth_points:
        if growth_index >= running_peak - 1e-12:
            running_peak = growth_index
            running_peak_date = point_date
        drawdown = (growth_index / running_peak) - 1.0 if running_peak > 1e-12 else None
        if drawdown is not None:
            current_drawdown = drawdown
        if drawdown is not None and drawdown < max_drawdown:
            max_drawdown = drawdown
            max_drawdown_peak_date = running_peak_date
            max_drawdown_trough_date = point_date
            max_drawdown_peak_growth = running_peak
            recovery_index = None

    if max_drawdown_peak_date != max_drawdown_trough_date:
        for point_date, growth_index in growth_points:
            if point_date <= max_drawdown_trough_date:
                continue
            if growth_index >= max_drawdown_peak_growth - 1e-12:
                recovery_index = point_date
                break

    return {
        "current_drawdown": current_drawdown,
        "max_drawdown": max_drawdown,
        "max_drawdown_days": (max_drawdown_trough_date - max_drawdown_peak_date).days
        if max_drawdown_peak_date != max_drawdown_trough_date
        else 0,
        "drawdown_duration_days": (recovery_index - max_drawdown_peak_date).days
        if recovery_index is not None and max_drawdown_peak_date != max_drawdown_trough_date
        else None,
    }


def _rebased_twr_series(snapshots: list[dict[str, object]]) -> list[dict[str, object]]:
    growth_index = 1.0
    peak_growth_index = 1.0
    has_return_history = False
    rendered_snapshots: list[dict[str, object]] = []

    for snapshot in snapshots:
        rendered_snapshot = dict(snapshot)
        daily_twr = _safe_float(snapshot.get("daily_twr"))
        if daily_twr is not None and isfinite(daily_twr):
            has_return_history = True
            growth_index *= 1.0 + daily_twr
            peak_growth_index = max(peak_growth_index, growth_index)

        rendered_snapshot["cumulative_twr"] = (growth_index - 1.0) if has_return_history else None
        rendered_snapshot["drawdown"] = (
            (growth_index / peak_growth_index) - 1.0
            if has_return_history and peak_growth_index > 0
            else None
        )
        rendered_snapshots.append(rendered_snapshot)

    return rendered_snapshots


def _xnpv(rate: float, cash_flows: list[tuple[date, float]]) -> float:
    if rate <= -0.999999999:
        return float("inf")
    start_date = cash_flows[0][0]
    total = 0.0
    for cash_flow_date, amount in cash_flows:
        years = _year_fraction(start_date, cash_flow_date)
        total += amount / ((1.0 + rate) ** years)
    return total


def _solve_xirr(cash_flows: list[tuple[date, float]]) -> float | None:
    if len(cash_flows) < 2:
        return None
    has_positive = any(amount > 0 for _, amount in cash_flows)
    has_negative = any(amount < 0 for _, amount in cash_flows)
    if not has_positive or not has_negative:
        return None

    low = -0.9999
    high = 0.1
    low_value = _xnpv(low, cash_flows)
    high_value = _xnpv(high, cash_flows)
    iterations = 0
    while low_value * high_value > 0 and high < 1_000_000 and iterations < 64:
        high *= 2.0
        high_value = _xnpv(high, cash_flows)
        iterations += 1
    if low_value * high_value > 0:
        return None

    for _ in range(128):
        mid = (low + high) / 2.0
        mid_value = _xnpv(mid, cash_flows)
        if abs(mid_value) <= 1e-10:
            return mid
        if low_value * mid_value <= 0:
            high = mid
            high_value = mid_value
        else:
            low = mid
            low_value = mid_value
    return (low + high) / 2.0


def build_portfolio_performance_report(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, object]:
    calculation_start_date = start_date - timedelta(days=1) if start_date is not None else None
    snapshots = build_daily_portfolio_snapshots(
        portfolio,
        accounts,
        transactions,
        start_date=calculation_start_date,
        end_date=end_date,
    )
    return build_portfolio_performance_report_from_snapshots(
        portfolio,
        snapshots,
        transactions=transactions,
        start_date=start_date,
        end_date=end_date,
    )


def build_portfolio_performance_report_from_snapshots(
    portfolio: dict[str, object],
    snapshots: list[dict[str, object]],
    *,
    transactions: list[dict[str, object]] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, object]:
    normalized_snapshots = []
    for snapshot in snapshots:
        normalized_snapshot = dict(snapshot)
        snapshot_date = _parse_iso_date(normalized_snapshot.get("as_of_date"))
        if snapshot_date is not None:
            normalized_snapshot["as_of_date"] = snapshot_date
        normalized_snapshots.append(normalized_snapshot)
    snapshots = sorted(
        normalized_snapshots,
        key=lambda item: item.get("as_of_date") if isinstance(item.get("as_of_date"), date) else date.min,
    )
    visible_snapshots = [
        snapshot
        for snapshot in snapshots
        if start_date is None
        or (isinstance(snapshot.get("as_of_date"), date) and snapshot["as_of_date"] >= start_date)
    ]
    visible_daily_series_snapshots = _rebased_twr_series(visible_snapshots)
    snapshot_summary = summarize_daily_snapshots(visible_snapshots)
    complete_snapshots = [
        snapshot
        for snapshot in snapshots
        if snapshot.get("coverage_state") == "complete" and snapshot.get("nav") is not None
    ]
    if start_date is not None:
        transaction_dates = [
            parsed
            for parsed in (_parse_iso_date(item.get("trade_date")) for item in (transactions or []))
            if parsed is not None
        ]
        first_transaction_date = min(transaction_dates) if transaction_dates else None
        if first_transaction_date is not None:
            complete_snapshots = [
                snapshot
                for snapshot in complete_snapshots
                if isinstance(snapshot.get("as_of_date"), date)
                and snapshot["as_of_date"] >= first_transaction_date
            ]
    visible_complete_snapshots = [
        snapshot
        for snapshot in visible_snapshots
        if snapshot.get("coverage_state") == "complete" and snapshot.get("nav") is not None
    ]
    return_observation_count = sum(1 for snapshot in visible_snapshots if snapshot.get("daily_twr") is not None)
    risk_return_snapshots = [
        snapshot
        for snapshot in visible_snapshots
        if snapshot.get("daily_twr") is not None and bool(snapshot.get("return_observation_eligible"))
    ]
    risk_return_observation_count = len(risk_return_snapshots)
    start_snapshot = complete_snapshots[0] if complete_snapshots else None
    end_snapshot = visible_complete_snapshots[-1] if visible_complete_snapshots else (complete_snapshots[-1] if complete_snapshots else None)
    start_anchor_date = start_snapshot.get("as_of_date") if isinstance((start_snapshot or {}).get("as_of_date"), date) else None
    end_anchor_date = end_snapshot.get("as_of_date") if isinstance((end_snapshot or {}).get("as_of_date"), date) else None
    display_start_date = start_anchor_date
    if start_date is not None and start_anchor_date is not None and start_anchor_date < start_date:
        display_start_date = start_date

    def snapshot_period_delta(field_name: str) -> float | None:
        end_value = _safe_float((end_snapshot or {}).get(field_name))
        if end_value is None:
            return None
        if start_snapshot is None:
            return end_value
        start_value = _safe_float((start_snapshot or {}).get(field_name))
        if start_value is None:
            return None
        return end_value - start_value

    cumulative_twr = _compound_daily_twr(visible_snapshots) if end_snapshot and return_observation_count > 0 else None
    annualized_twr = None
    if (
        cumulative_twr is not None
        and start_snapshot is not None
        and end_snapshot is not None
        and isinstance(start_snapshot.get("as_of_date"), date)
        and isinstance(end_snapshot.get("as_of_date"), date)
    ):
        years = _year_fraction(start_snapshot["as_of_date"], end_snapshot["as_of_date"])
        if years > 1e-9:
            annualized_twr = (1.0 + cumulative_twr) ** (1.0 / years) - 1.0

    flow_snapshots = visible_snapshots
    if start_anchor_date is not None and end_anchor_date is not None:
        flow_snapshots = [
            snapshot
            for snapshot in visible_snapshots
            if isinstance(snapshot.get("as_of_date"), date)
            and start_anchor_date < snapshot["as_of_date"] <= end_anchor_date
        ]
    external_cash_in = sum((_safe_float(snapshot.get("external_cash_in")) or 0.0) for snapshot in flow_snapshots)
    external_cash_out = sum((_safe_float(snapshot.get("external_cash_out")) or 0.0) for snapshot in flow_snapshots)
    net_external_inflow = external_cash_in - external_cash_out

    start_nav = _safe_float((start_snapshot or {}).get("nav"))
    end_nav = _safe_float((end_snapshot or {}).get("nav"))
    absolute_change = (end_nav - start_nav) if start_nav is not None and end_nav is not None else None
    delta = (
        absolute_change - net_external_inflow
        if absolute_change is not None
        else None
    )
    realized_pnl = snapshot_period_delta("realized_pnl")
    unrealized_pnl = snapshot_period_delta("unrealized_pnl")
    income_cash_amount = snapshot_period_delta("income_cash_amount")
    expense_cash_amount = snapshot_period_delta("expense_cash_amount")
    cash_currency_gains = snapshot_period_delta("cash_currency_gains")
    asset_currency_gains = snapshot_period_delta("asset_currency_gains")
    return_of_capital_amount = snapshot_period_delta("return_of_capital_amount")
    total_pnl = snapshot_period_delta("total_pnl")

    irr = None
    if (
        start_snapshot is not None
        and end_snapshot is not None
        and start_nav is not None
        and end_nav is not None
        and start_anchor_date is not None
        and end_anchor_date is not None
    ):
        cash_flows: list[tuple[date, float]] = [(start_anchor_date, -start_nav)]
        for snapshot in flow_snapshots:
            snapshot_date = snapshot.get("as_of_date")
            if not isinstance(snapshot_date, date):
                continue
            contribution = _safe_float(snapshot.get("external_cash_in")) or 0.0
            distribution = _safe_float(snapshot.get("external_cash_out")) or 0.0
            if contribution > 0:
                cash_flows.append((snapshot_date, -contribution))
            if distribution > 0:
                cash_flows.append((snapshot_date, distribution))
        cash_flows.append((end_anchor_date, end_nav))
        irr = _solve_xirr(cash_flows)

    daily_returns = [
        _safe_float(snapshot.get("daily_twr"))
        for snapshot in risk_return_snapshots
        if snapshot.get("daily_twr") is not None
    ]
    daily_returns = [value for value in daily_returns if value is not None]
    mean_daily_return = (sum(daily_returns) / len(daily_returns)) if daily_returns else None
    volatility = _sample_stddev(daily_returns)
    periods_per_year = _periods_per_year_from_observations(
        observation_count=len(daily_returns),
        start_date=start_anchor_date,
        end_date=end_anchor_date,
    )
    annualized_volatility = (
        volatility * sqrt(periods_per_year)
        if volatility is not None and periods_per_year is not None
        else None
    )
    annualized_return_from_daily_mean = (
        mean_daily_return * periods_per_year
        if mean_daily_return is not None and periods_per_year is not None
        else None
    )
    sharpe_ratio = (
        annualized_return_from_daily_mean / annualized_volatility
        if annualized_return_from_daily_mean is not None
        and annualized_volatility is not None
        and annualized_volatility > 1e-12
        else None
    )
    downside_volatility = _downside_deviation(daily_returns)
    annualized_downside_volatility = (
        downside_volatility * sqrt(periods_per_year)
        if downside_volatility is not None and periods_per_year is not None
        else None
    )
    sortino_ratio = (
        annualized_return_from_daily_mean / annualized_downside_volatility
        if annualized_return_from_daily_mean is not None
        and annualized_downside_volatility is not None
        and annualized_downside_volatility > 1e-12
        else None
    )

    drawdown_stats = _drawdown_stats(visible_snapshots, start_anchor_date=start_anchor_date)
    current_drawdown = drawdown_stats["current_drawdown"]
    max_drawdown = drawdown_stats["max_drawdown"]
    max_drawdown_days = drawdown_stats["max_drawdown_days"]
    drawdown_duration_days = drawdown_stats["drawdown_duration_days"]

    coverage_state = "unavailable"
    if visible_complete_snapshots:
        coverage_state = "complete"
        if snapshot_summary["partial_count"] or snapshot_summary["unavailable_count"]:
            coverage_state = "partial"
        if cumulative_twr is None:
            coverage_state = "partial"

    return {
        "portfolio_id": str(portfolio.get("portfolio_id") or ""),
        "base_currency": _normalized_currency(portfolio.get("base_currency")),
        "valuation_timezone": _resolve_portfolio_valuation_timezone(portfolio),
        "valuation_cutoff_policy": _resolve_portfolio_valuation_cutoff_policy(portfolio),
        "summary": {
            "start_date": display_start_date,
            "end_date": end_anchor_date or (snapshots[-1]["as_of_date"] if snapshots else None),
            "coverage_state": coverage_state,
            "snapshot_count": len(visible_snapshots),
            "return_observation_count": return_observation_count,
            "risk_return_observation_count": risk_return_observation_count,
            "risk_annualization_periods_per_year": periods_per_year,
            "latest_complete_as_of_date": snapshot_summary["latest_complete_as_of_date"],
            "start_nav": start_nav,
            "end_nav": end_nav,
            "external_cash_in": external_cash_in,
            "external_cash_out": external_cash_out,
            "net_external_inflow": net_external_inflow,
            "cumulative_twr": cumulative_twr,
            "annualized_twr": annualized_twr,
            "irr": irr,
            "mwror": irr,
            "absolute_change": absolute_change,
            "delta": delta,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "income_cash_amount": income_cash_amount,
            "expense_cash_amount": expense_cash_amount,
            "cash_currency_gains": cash_currency_gains,
            "asset_currency_gains": asset_currency_gains,
            "return_of_capital_amount": return_of_capital_amount,
            "total_pnl": total_pnl,
            "mean_daily_return": mean_daily_return,
            "annualized_return_from_daily_mean": annualized_return_from_daily_mean,
            "annualized_volatility": annualized_volatility,
            "annualized_downside_volatility": annualized_downside_volatility,
            "sharpe_ratio": sharpe_ratio,
            "sortino_ratio": sortino_ratio,
            "current_drawdown": current_drawdown,
            "max_drawdown": max_drawdown,
            "max_drawdown_days": max_drawdown_days,
            "drawdown_duration_days": drawdown_duration_days,
        },
        "daily_series": [
            {
                "as_of_date": snapshot["as_of_date"],
                "coverage_state": snapshot["coverage_state"],
                "stale_price_flag": snapshot["stale_price_flag"],
                "stale_fx_flag": snapshot["stale_fx_flag"],
                "market_observation_count": snapshot.get("market_observation_count", 0),
                "return_observation_eligible": bool(snapshot.get("return_observation_eligible")),
                "beginning_nav": snapshot["beginning_nav"],
                "ending_nav": snapshot["ending_nav"],
                "realized_pnl": snapshot.get("realized_pnl"),
                "unrealized_pnl": snapshot.get("unrealized_pnl"),
                "income_cash_amount": snapshot.get("income_cash_amount"),
                "expense_cash_amount": snapshot.get("expense_cash_amount"),
                "cash_currency_gains": snapshot.get("cash_currency_gains"),
                "asset_currency_gains": snapshot.get("asset_currency_gains"),
                "return_of_capital_amount": snapshot.get("return_of_capital_amount"),
                "total_pnl": snapshot.get("total_pnl"),
                "external_cash_in": snapshot["external_cash_in"],
                "external_cash_out": snapshot["external_cash_out"],
                "net_external_inflow": snapshot["net_external_inflow"],
                "absolute_change": snapshot["absolute_change"],
                "delta": snapshot["delta"],
                "daily_twr": snapshot["daily_twr"],
                "cumulative_twr": snapshot["cumulative_twr"],
                "drawdown": snapshot["drawdown"],
            }
            for snapshot in visible_daily_series_snapshots
        ],
    }


def build_period_calculation_report(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, object]:
    window = _resolve_snapshot_window(
        portfolio,
        transactions,
        start_date=start_date,
        end_date=end_date,
    )
    base_currency = _normalized_currency(portfolio.get("base_currency"))
    valuation_timezone = _resolve_portfolio_valuation_timezone(portfolio)
    valuation_cutoff_policy = _resolve_portfolio_valuation_cutoff_policy(portfolio)
    if window is None:
        return {
            "portfolio_id": str(portfolio.get("portfolio_id") or ""),
            "base_currency": base_currency,
            "valuation_timezone": valuation_timezone,
            "valuation_cutoff_policy": valuation_cutoff_policy,
            "summary": {
                "start_date": start_date,
                "end_date": end_date,
                "coverage_state": "unavailable",
                "stale_price_flag": False,
                "stale_fx_flag": False,
                "initial_value": None,
                "final_value": None,
                "delta": None,
                "capital_gains": None,
                "realized_capital_gains": None,
                "earnings": None,
                "fees": None,
                "taxes": None,
                "cash_currency_gains": None,
                "asset_currency_gains": None,
                "deposits": 0.0,
                "withdrawals": 0.0,
                "net_external_inflow": 0.0,
            },
            "lines": [],
        }

    resolved_start_date, resolved_end_date = window
    initial_boundary_date = _initial_boundary_date(resolved_start_date, start_date)
    sorted_transactions = sorted(transactions, key=_transaction_sort_key)
    start_boundary_transactions = _transactions_as_of_end_date(
        sorted_transactions,
        end_date=initial_boundary_date,
    )
    end_boundary_transactions = _transactions_as_of_end_date(
        sorted_transactions,
        end_date=resolved_end_date,
    )
    period_transactions = _transactions_in_period(
        sorted_transactions,
        start_date=resolved_start_date,
        end_date=resolved_end_date,
    )

    start_snapshot = _build_single_date_snapshot(
        portfolio,
        accounts,
        start_boundary_transactions,
        as_of_date=initial_boundary_date,
    )
    end_snapshot = _build_single_date_snapshot(
        portfolio,
        accounts,
        end_boundary_transactions,
        as_of_date=resolved_end_date,
    )

    initial_value = _safe_float(start_snapshot.get("nav"))
    start_unrealized_pnl = _safe_float(start_snapshot.get("unrealized_pnl"))
    start_cash_currency_gains = _safe_float(start_snapshot.get("cash_currency_gains"))
    start_asset_currency_gains = _safe_float(start_snapshot.get("asset_currency_gains"))
    if not start_boundary_transactions:
        initial_value = 0.0
        start_unrealized_pnl = 0.0
        start_cash_currency_gains = 0.0
        start_asset_currency_gains = 0.0

    final_value = _safe_float(end_snapshot.get("nav"))
    end_unrealized_pnl = _safe_float(end_snapshot.get("unrealized_pnl"))
    end_cash_currency_gains = _safe_float(end_snapshot.get("cash_currency_gains"))
    end_asset_currency_gains = _safe_float(end_snapshot.get("asset_currency_gains"))

    fx_payload = get_platform_fx_rates()
    direct_fx_assets = _fx_direct_asset_map(fx_payload)
    instrument_detail_cache: dict[str, dict[str, object] | None] = {}

    transaction_buckets = _sum_period_transaction_buckets(
        period_transactions,
        base_currency=base_currency,
        direct_fx_assets=direct_fx_assets,
        instrument_detail_cache=instrument_detail_cache,
    )
    position_lots = build_position_lots(
        str(portfolio.get("portfolio_id") or ""),
        accounts,
        end_boundary_transactions,
    )
    realized_capital_gains_summary = _sum_period_realized_capital_gains(
        position_lots,
        start_date=resolved_start_date,
        end_date=resolved_end_date,
        base_currency=base_currency,
        direct_fx_assets=direct_fx_assets,
        instrument_detail_cache=instrument_detail_cache,
    )

    delta = None
    if initial_value is not None and final_value is not None:
        delta = final_value - initial_value - transaction_buckets["net_external_inflow"]

    capital_gains = None
    if start_unrealized_pnl is not None and end_unrealized_pnl is not None:
        capital_gains = end_unrealized_pnl - start_unrealized_pnl

    cash_currency_gains = None
    if start_cash_currency_gains is not None and end_cash_currency_gains is not None:
        cash_currency_gains = end_cash_currency_gains - start_cash_currency_gains
    asset_currency_gains = None
    if start_asset_currency_gains is not None and end_asset_currency_gains is not None:
        asset_currency_gains = end_asset_currency_gains - start_asset_currency_gains

    residual_gains = None
    if delta is not None and capital_gains is not None:
        residual_gains = (
            delta
            - capital_gains
            - realized_capital_gains_summary["realized_capital_gains"]
            - transaction_buckets["earnings"]
            + transaction_buckets["fees"]
            + transaction_buckets["taxes"]
        )
        if cash_currency_gains is not None:
            residual_gains -= cash_currency_gains
        if asset_currency_gains is not None:
            residual_gains -= asset_currency_gains
    bridge_closed = residual_gains is None or abs(residual_gains) <= 1e-9

    coverage_state = "complete"
    if (
        start_snapshot.get("coverage_state") != "complete"
        or end_snapshot.get("coverage_state") != "complete"
        or not transaction_buckets["coverage_complete"]
        or not realized_capital_gains_summary["coverage_complete"]
        or initial_value is None
        or final_value is None
    ):
        has_any_content = (
            bool(start_boundary_transactions)
            or bool(end_boundary_transactions)
            or bool(period_transactions)
            or initial_value is not None
            or final_value is not None
        )
        coverage_state = "partial" if has_any_content else "unavailable"
    if coverage_state == "complete" and not bridge_closed:
        coverage_state = "partial"

    stale_price_flag = bool(start_snapshot.get("stale_price_flag")) or bool(end_snapshot.get("stale_price_flag"))
    stale_fx_flag = (
        bool(start_snapshot.get("stale_fx_flag"))
        or bool(end_snapshot.get("stale_fx_flag"))
        or bool(transaction_buckets["stale_fx_flag"])
        or bool(realized_capital_gains_summary["stale_fx_flag"])
    )

    lines = [
        {
            "key": "initial_value",
            "label": f"Initial value ({initial_boundary_date.isoformat()})",
            "amount": initial_value,
            "line_kind": "boundary",
            "parent_key": None,
            "sort_order": 10,
        },
        {
            "key": "capital_gains",
            "label": "Capital Gains",
            "amount": capital_gains,
            "line_kind": "performance",
            "parent_key": None,
            "sort_order": 20,
        },
        {
            "key": "asset_currency_gains",
            "label": "Asset Currency Gains",
            "amount": asset_currency_gains,
            "line_kind": "performance",
            "parent_key": None,
            "sort_order": 25,
        },
        {
            "key": "realized_capital_gains",
            "label": "Realized Capital Gains",
            "amount": realized_capital_gains_summary["realized_capital_gains"],
            "line_kind": "performance",
            "parent_key": None,
            "sort_order": 30,
        },
        {
            "key": "earnings",
            "label": "Earnings",
            "amount": transaction_buckets["earnings"],
            "line_kind": "performance",
            "parent_key": None,
            "sort_order": 40,
        },
        {
            "key": "fees",
            "label": "Fees",
            "amount": transaction_buckets["fees"],
            "line_kind": "performance",
            "parent_key": None,
            "sort_order": 50,
        },
        {
            "key": "taxes",
            "label": "Taxes",
            "amount": transaction_buckets["taxes"],
            "line_kind": "performance",
            "parent_key": None,
            "sort_order": 60,
        },
        {
            "key": "cash_currency_gains",
            "label": "Cash Currency Gains",
            "amount": cash_currency_gains,
            "line_kind": "performance",
            "parent_key": None,
            "sort_order": 70,
        },
        {
            "key": "performance_neutral_transfers",
            "label": "Performance Neutral Transfers",
            "amount": transaction_buckets["net_external_inflow"],
            "line_kind": "external",
            "parent_key": None,
            "sort_order": 80,
        },
        {
            "key": "deposits",
            "label": "Deposits",
            "amount": transaction_buckets["deposits"],
            "line_kind": "detail",
            "parent_key": "performance_neutral_transfers",
            "sort_order": 81,
        },
        {
            "key": "withdrawals",
            "label": "Withdrawals",
            "amount": transaction_buckets["withdrawals"],
            "line_kind": "detail",
            "parent_key": "performance_neutral_transfers",
            "sort_order": 82,
        },
        {
            "key": "final_value",
            "label": f"Final value ({resolved_end_date.isoformat()})",
            "amount": final_value,
            "line_kind": "boundary",
            "parent_key": None,
            "sort_order": 90,
        },
    ]

    return {
        "portfolio_id": str(portfolio.get("portfolio_id") or ""),
        "base_currency": base_currency,
        "valuation_timezone": valuation_timezone,
        "valuation_cutoff_policy": valuation_cutoff_policy,
        "summary": {
            "start_date": resolved_start_date,
            "end_date": resolved_end_date,
            "coverage_state": coverage_state,
            "stale_price_flag": stale_price_flag,
            "stale_fx_flag": stale_fx_flag,
            "initial_value": initial_value,
            "final_value": final_value,
            "delta": delta,
            "capital_gains": capital_gains,
            "realized_capital_gains": realized_capital_gains_summary["realized_capital_gains"],
            "earnings": transaction_buckets["earnings"],
            "fees": transaction_buckets["fees"],
            "taxes": transaction_buckets["taxes"],
            "cash_currency_gains": cash_currency_gains,
            "asset_currency_gains": asset_currency_gains,
            "deposits": transaction_buckets["deposits"],
            "withdrawals": transaction_buckets["withdrawals"],
            "net_external_inflow": transaction_buckets["net_external_inflow"],
        },
        "lines": lines,
    }


def _build_boundary_holding_records(
    *,
    portfolio_id: str,
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    as_of_date: date,
    base_currency: str,
    direct_fx_assets: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
    boundary_nav: float | None,
) -> tuple[list[dict[str, object]], float | None]:
    position_lots = build_position_lots(
        portfolio_id,
        accounts,
        transactions,
        status="open",
    )
    position_buckets = _position_buckets_from_lots(position_lots)
    rendered_positions: list[dict[str, object]] = []
    total_market_value_base = 0.0
    total_market_value_complete = True

    for bucket in position_buckets:
        currency = _normalized_currency(bucket.get("currency"), fallback=base_currency)
        quantity = _safe_float(bucket.get("quantity")) or 0.0
        cost_basis = _safe_float(bucket.get("cost_basis"))
        converted_cost_basis, _ = convert_amount_on(
            cost_basis,
            as_of_date=as_of_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_assets=direct_fx_assets,
            instrument_detail_cache=instrument_detail_cache,
        )
        detail = _instrument_detail_cache_get(str(bucket.get("asset_id") or ""), instrument_detail_cache)
        price_point = (
            _select_market_point_as_of(detail=detail, role="valuation", as_of_date=as_of_date)
            if isinstance(detail, dict)
            else None
        )
        last_price = _safe_float((price_point or {}).get("value"))
        market_value_local = _position_market_value(
            quantity=quantity,
            last_price=last_price,
            instrument_ref=(bucket.get("instrument_ref") if isinstance(bucket.get("instrument_ref"), dict) else None),
        )
        converted_market_value, _ = convert_amount_on(
            market_value_local,
            as_of_date=as_of_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_assets=direct_fx_assets,
            instrument_detail_cache=instrument_detail_cache,
        )
        if market_value_local is None or converted_market_value is None:
            total_market_value_complete = False
        else:
            total_market_value_base += converted_market_value

        rendered_positions.append(
            {
                "position_id": str(bucket.get("asset_id") or ""),
                "asset_id": str(bucket.get("asset_id") or ""),
                "instrument_ref": deepcopy(bucket.get("instrument_ref") or {}),
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
                "market_value": market_value_local,
                "market_value_base": converted_market_value,
                "currency": currency,
                "portfolio_weight": None,
                "account_ids": list(bucket.get("account_ids") or []),
                "account_count": int(bucket.get("account_count") or 0),
                "open_position_lot_count": int(bucket.get("open_position_lot_count") or 0),
            }
        )

    resolved_total_market_value_base = total_market_value_base if total_market_value_complete else None
    _apply_position_portfolio_weights(rendered_positions, boundary_nav)

    rendered_positions.sort(
        key=lambda item: (
            -((_safe_float(item.get("market_value_base")) or 0.0)),
            str(item.get("asset_id") or ""),
        )
    )
    return rendered_positions, resolved_total_market_value_base


def _apply_position_portfolio_weights(
    positions: list[dict[str, object]],
    denominator_nav: float | None,
) -> None:
    for rendered_position in positions:
        market_value_base = _safe_float(rendered_position.get("market_value_base"))
        rendered_position["portfolio_weight"] = (
            market_value_base / denominator_nav
            if market_value_base is not None and denominator_nav is not None and denominator_nav > 1e-9
            else None
        )


def _statement_cash_nav_components(
    *,
    portfolio_id: str,
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    as_of_date: date,
    base_currency: str,
    direct_fx_assets: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
) -> dict[str, object]:
    postings = derive_ledger_postings(
        portfolio_id,
        transactions,
        account_cost_methods=_account_cost_methods(accounts),
        account_currency_map=_account_currency_map(accounts),
    )
    as_of_iso = as_of_date.isoformat()
    cash_balance_base = 0.0
    pending_settlement_base = 0.0
    cash_complete = True
    pending_settlement_complete = True
    for posting in postings:
        cash_delta = _safe_float(posting.get("cash_amount_delta"))
        if cash_delta is None:
            continue
        posting_currency = _normalized_currency(posting.get("currency"), fallback=base_currency)
        posting_effective_date = ledger_posting_effective_date_iso(posting)
        converted_cash_delta, _ = convert_amount_on(
            cash_delta,
            as_of_date=as_of_date,
            from_currency=posting_currency,
            to_currency=base_currency,
            direct_fx_assets=direct_fx_assets,
            instrument_detail_cache=instrument_detail_cache,
        )
        if converted_cash_delta is None:
            if posting_effective_date <= as_of_iso:
                cash_complete = False
            else:
                pending_settlement_complete = False
            continue
        if posting_effective_date <= as_of_iso:
            cash_balance_base += converted_cash_delta
        else:
            pending_settlement_base += converted_cash_delta

    return {
        "cash_balance_base": cash_balance_base if cash_complete else None,
        "pending_settlement_base": pending_settlement_base if pending_settlement_complete else None,
    }


def build_statement_of_assets_report(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    as_of_date: date,
) -> dict[str, object]:
    base_currency = _normalized_currency(portfolio.get("base_currency"))
    valuation_timezone = _resolve_portfolio_valuation_timezone(portfolio)
    valuation_cutoff_policy = _resolve_portfolio_valuation_cutoff_policy(portfolio)
    sorted_transactions = sorted(transactions, key=_transaction_sort_key)
    boundary_transactions = _transactions_as_of_end_date(sorted_transactions, end_date=as_of_date)

    fx_payload = get_platform_fx_rates()
    direct_fx_assets = _fx_direct_asset_map(fx_payload)
    instrument_detail_cache: dict[str, dict[str, object] | None] = {}
    positions, total_market_value_base = _build_boundary_holding_records(
        portfolio_id=str(portfolio.get("portfolio_id") or ""),
        accounts=accounts,
        transactions=boundary_transactions,
        as_of_date=as_of_date,
        base_currency=base_currency,
        direct_fx_assets=direct_fx_assets,
        instrument_detail_cache=instrument_detail_cache,
        boundary_nav=None,
    )
    cash_components = _statement_cash_nav_components(
        portfolio_id=str(portfolio.get("portfolio_id") or ""),
        accounts=accounts,
        transactions=boundary_transactions,
        as_of_date=as_of_date,
        base_currency=base_currency,
        direct_fx_assets=direct_fx_assets,
        instrument_detail_cache=instrument_detail_cache,
    )
    cash_balance_base = _safe_float(cash_components.get("cash_balance_base"))
    pending_settlement_base = _safe_float(cash_components.get("pending_settlement_base"))
    total_nav_base = (
        cash_balance_base + pending_settlement_base + total_market_value_base
        if (
            cash_balance_base is not None
            and pending_settlement_base is not None
            and total_market_value_base is not None
        )
        else None
    )
    _apply_position_portfolio_weights(positions, total_nav_base)
    return {
        "portfolio_id": str(portfolio.get("portfolio_id") or ""),
        "portfolio_name": str(portfolio.get("portfolio_name") or portfolio.get("portfolio_id") or ""),
        "base_currency": base_currency,
        "valuation_timezone": valuation_timezone,
        "valuation_cutoff_policy": valuation_cutoff_policy,
        "as_of_date": as_of_date,
        "positions": positions,
        "total_market_value_base": total_market_value_base,
        "cash_balance_base": cash_balance_base,
        "pending_settlement_base": pending_settlement_base,
        "total_nav_base": total_nav_base,
    }


def _filter_boundary_positions(
    *,
    positions: list[dict[str, object]],
    axis: str | None,
    group_key: str | None,
    as_of_date: date | None,
    taxonomy_context: tuple[dict[str, object], dict[str, dict[str, object]], dict[str, list[dict[str, object]]], str]
    | None = None,
    account_name_map: dict[str, str] | None = None,
) -> tuple[list[dict[str, object]], str | None]:
    resolved_axis = str(axis or "").strip()
    resolved_group_key = str(group_key or "").strip()
    if not resolved_axis or not resolved_group_key:
        return list(positions), None

    if resolved_axis == "instrument":
        filtered_positions = [
            position
            for position in positions
            if str(position.get("asset_id") or "") == resolved_group_key
        ]
        group_label = None
        if filtered_positions:
            instrument_ref = (
                filtered_positions[0].get("instrument_ref")
                if isinstance(filtered_positions[0].get("instrument_ref"), dict)
                else {}
            )
            group_label = str(instrument_ref.get("asset_name") or resolved_group_key)
        return filtered_positions, group_label

    if resolved_axis == "account":
        filtered_positions = [
            position
            for position in positions
            if resolved_group_key in list(position.get("account_ids") or [])
        ]
        group_label = (account_name_map or {}).get(resolved_group_key)
        return filtered_positions, group_label

    if resolved_axis != "taxonomy":
        raise ValueError("axis must be instrument, account, or taxonomy")
    if taxonomy_context is None:
        raise ValueError("taxonomy_id is required when axis=taxonomy.")
    if as_of_date is None:
        return list(positions), None

    taxonomy, taxonomy_nodes_by_id, assignments_by_entity, target_scope = taxonomy_context
    if target_scope != "instrument":
        raise ValueError("Boundary holdings taxonomy filters currently support instrument-scoped taxonomies only.")

    filtered_positions: list[dict[str, object]] = []
    group_label = None
    for position in positions:
        asset_id = str(position.get("asset_id") or "")
        if not asset_id:
            continue
        position_group_key, position_group_label = _resolve_taxonomy_group_for_date(
            taxonomy=taxonomy,
            taxonomy_nodes_by_id=taxonomy_nodes_by_id,
            assignments_by_entity=assignments_by_entity,
            target_entity_id=asset_id,
            as_of_date=as_of_date,
        )
        if position_group_key != resolved_group_key:
            continue
        filtered_positions.append(position)
        if group_label is None:
            group_label = position_group_label
    return filtered_positions, group_label


def build_period_boundary_holdings_report(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    taxonomies: list[dict[str, object]] | None = None,
    taxonomy_nodes: list[dict[str, object]] | None = None,
    taxonomy_assignments: list[dict[str, object]] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str | None = None,
    taxonomy_id: str | None = None,
    group_key: str | None = None,
) -> dict[str, object]:
    window = _resolve_snapshot_window(
        portfolio,
        transactions,
        start_date=start_date,
        end_date=end_date,
    )
    base_currency = _normalized_currency(portfolio.get("base_currency"))
    valuation_timezone = _resolve_portfolio_valuation_timezone(portfolio)
    valuation_cutoff_policy = _resolve_portfolio_valuation_cutoff_policy(portfolio)
    if window is None:
        return {
            "portfolio_id": str(portfolio.get("portfolio_id") or ""),
            "base_currency": base_currency,
            "valuation_timezone": valuation_timezone,
            "valuation_cutoff_policy": valuation_cutoff_policy,
            "summary": {
                "axis": axis,
                "taxonomy_id": taxonomy_id,
                "group_key": group_key,
                "group_label": None,
                "start_date": start_date,
                "end_date": end_date,
                "start_position_count": 0,
                "end_position_count": 0,
                "start_total_market_value_base": None,
                "end_total_market_value_base": None,
            },
            "start_positions": [],
            "end_positions": [],
        }

    resolved_start_date, resolved_end_date = window
    initial_boundary_date = _initial_boundary_date(resolved_start_date, start_date)
    resolved_axis = str(axis or "").strip() or None
    if resolved_axis not in {None, "instrument", "account", "taxonomy"}:
        raise ValueError("axis must be instrument, account, or taxonomy")
    resolved_taxonomy_id = str(taxonomy_id or "").strip()
    resolved_group_key = str(group_key or "").strip()
    account_name_map = {
        str(account.get("account_id") or ""): str(account.get("account_name") or account.get("account_id") or "")
        for account in accounts
        if str(account.get("account_id") or "")
    }
    sorted_transactions = sorted(transactions, key=_transaction_sort_key)
    start_boundary_transactions = _transactions_as_of_end_date(
        sorted_transactions,
        end_date=initial_boundary_date,
    )
    end_boundary_transactions = _transactions_as_of_end_date(
        sorted_transactions,
        end_date=resolved_end_date,
    )

    fx_payload = get_platform_fx_rates()
    direct_fx_assets = _fx_direct_asset_map(fx_payload)
    instrument_detail_cache: dict[str, dict[str, object] | None] = {}
    start_snapshot = _build_single_date_snapshot(
        portfolio,
        accounts,
        start_boundary_transactions,
        as_of_date=initial_boundary_date,
    )
    end_snapshot = _build_single_date_snapshot(
        portfolio,
        accounts,
        end_boundary_transactions,
        as_of_date=resolved_end_date,
    )

    start_positions, start_total_market_value_base = _build_boundary_holding_records(
        portfolio_id=str(portfolio.get("portfolio_id") or ""),
        accounts=accounts,
        transactions=start_boundary_transactions,
        as_of_date=initial_boundary_date,
        base_currency=base_currency,
        direct_fx_assets=direct_fx_assets,
        instrument_detail_cache=instrument_detail_cache,
        boundary_nav=_safe_float(start_snapshot.get("nav")),
    )
    end_positions, end_total_market_value_base = _build_boundary_holding_records(
        portfolio_id=str(portfolio.get("portfolio_id") or ""),
        accounts=accounts,
        transactions=end_boundary_transactions,
        as_of_date=resolved_end_date,
        base_currency=base_currency,
        direct_fx_assets=direct_fx_assets,
        instrument_detail_cache=instrument_detail_cache,
        boundary_nav=_safe_float(end_snapshot.get("nav")),
    )

    taxonomy_context = None
    if resolved_axis == "taxonomy":
        if not resolved_taxonomy_id:
            raise ValueError("taxonomy_id is required when axis=taxonomy.")
        taxonomy_context = _build_taxonomy_assignment_context(
            taxonomies=taxonomies or [],
            taxonomy_nodes=taxonomy_nodes or [],
            taxonomy_assignments=taxonomy_assignments or [],
            taxonomy_id=resolved_taxonomy_id,
        )

    start_group_label = None
    end_group_label = None
    if resolved_axis and resolved_group_key:
        start_positions, start_group_label = _filter_boundary_positions(
            positions=start_positions,
            axis=resolved_axis,
            group_key=resolved_group_key,
            as_of_date=resolved_start_date,
            taxonomy_context=taxonomy_context,
            account_name_map=account_name_map,
        )
        end_positions, end_group_label = _filter_boundary_positions(
            positions=end_positions,
            axis=resolved_axis,
            group_key=resolved_group_key,
            as_of_date=resolved_end_date,
            taxonomy_context=taxonomy_context,
            account_name_map=account_name_map,
        )
        start_total_market_value_base = sum(
            (_safe_float(position.get("market_value_base")) or 0.0) for position in start_positions
        )
        end_total_market_value_base = sum(
            (_safe_float(position.get("market_value_base")) or 0.0) for position in end_positions
        )

    return {
        "portfolio_id": str(portfolio.get("portfolio_id") or ""),
        "base_currency": base_currency,
        "valuation_timezone": valuation_timezone,
        "valuation_cutoff_policy": valuation_cutoff_policy,
        "summary": {
            "axis": resolved_axis,
            "taxonomy_id": resolved_taxonomy_id or None,
            "group_key": resolved_group_key or None,
            "group_label": start_group_label or end_group_label,
            "start_date": resolved_start_date,
            "start_boundary_date": initial_boundary_date,
            "end_date": resolved_end_date,
            "start_position_count": len(start_positions),
            "end_position_count": len(end_positions),
            "start_total_market_value_base": start_total_market_value_base,
            "end_total_market_value_base": end_total_market_value_base,
        },
        "start_positions": start_positions,
        "end_positions": end_positions,
    }


def _group_boundary_holdings_by_taxonomy(
    *,
    taxonomy: dict[str, object],
    taxonomy_nodes: list[dict[str, object]],
    taxonomy_assignments: list[dict[str, object]],
    positions: list[dict[str, object]],
    as_of_date: date,
) -> list[dict[str, object]]:
    taxonomy_id = str(taxonomy.get("taxonomy_id") or "")
    taxonomy_nodes_by_id = {
        str(node.get("taxonomy_node_id") or ""): node
        for node in taxonomy_nodes
        if str(node.get("taxonomy_id") or "") == taxonomy_id
    }
    assignments_by_entity: dict[str, list[dict[str, object]]] = defaultdict(list)
    for assignment in taxonomy_assignments:
        if str(assignment.get("taxonomy_id") or "") != taxonomy_id:
            continue
        if str(assignment.get("target_scope") or "") != "instrument":
            continue
        assignments_by_entity[str(assignment.get("target_entity_id") or "")].append(assignment)

    grouped: dict[str, dict[str, object]] = {}
    asset_ids_by_group: dict[str, set[str]] = defaultdict(set)
    for position in positions:
        asset_id = str(position.get("asset_id") or "")
        if not asset_id:
            continue
        group_key, group_label = _resolve_taxonomy_group_for_date(
            taxonomy=taxonomy,
            taxonomy_nodes_by_id=taxonomy_nodes_by_id,
            assignments_by_entity=assignments_by_entity,
            target_entity_id=asset_id,
            as_of_date=as_of_date,
        )
        group = grouped.setdefault(
            group_key,
            {
                "axis": "taxonomy",
                "taxonomy_id": taxonomy_id,
                "group_key": group_key,
                "group_label": group_label,
                "position_count": 0,
                "asset_count": 0,
                "cost_basis_base": 0.0,
                "market_value_base": 0.0,
                "unrealized_pnl": 0.0,
                "portfolio_weight": 0.0,
                "open_position_lot_count": 0,
            },
        )
        group["position_count"] = int(group.get("position_count") or 0) + 1
        asset_ids_by_group[group_key].add(asset_id)
        group["open_position_lot_count"] = int(group.get("open_position_lot_count") or 0) + int(
            position.get("open_position_lot_count") or 0
        )
        for field_name in ("cost_basis_base", "market_value_base"):
            value = _safe_float(position.get(field_name))
            if value is None:
                group[field_name] = None
                continue
            current_value = group.get(field_name)
            if current_value is None:
                continue
            group[field_name] = (_safe_float(current_value) or 0.0) + value
        portfolio_weight = _safe_float(position.get("portfolio_weight"))
        if portfolio_weight is None:
            group["portfolio_weight"] = None
        elif group.get("portfolio_weight") is not None:
            group["portfolio_weight"] = (_safe_float(group.get("portfolio_weight")) or 0.0) + portfolio_weight

    rendered_groups: list[dict[str, object]] = []
    for group_key, group in grouped.items():
        market_value_base = _safe_float(group.get("market_value_base"))
        cost_basis_base = _safe_float(group.get("cost_basis_base"))
        group["asset_count"] = len(asset_ids_by_group[group_key])
        group["unrealized_pnl"] = (
            market_value_base - cost_basis_base
            if market_value_base is not None and cost_basis_base is not None
            else None
        )
        rendered_groups.append(group)

    rendered_groups.sort(
        key=lambda item: (
            -((_safe_float(item.get("market_value_base")) or 0.0)),
            str(item.get("group_key") or ""),
        )
    )
    return rendered_groups


def build_period_boundary_groups_report(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    taxonomies: list[dict[str, object]] | None = None,
    taxonomy_nodes: list[dict[str, object]] | None = None,
    taxonomy_assignments: list[dict[str, object]] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    taxonomy_id: str | None = None,
) -> dict[str, object]:
    resolved_taxonomy_id = str(taxonomy_id or "").strip()
    if not resolved_taxonomy_id:
        raise ValueError("taxonomy_id is required for boundary taxonomy groups.")
    taxonomy = next(
        (item for item in (taxonomies or []) if str(item.get("taxonomy_id") or "") == resolved_taxonomy_id),
        None,
    )
    if taxonomy is None:
        raise ValueError("Selected taxonomy was not found.")
    if str(taxonomy.get("primary_assignment_scope") or "") != "instrument":
        raise ValueError("Boundary taxonomy groups currently support instrument-scoped taxonomies only.")

    report = build_period_boundary_holdings_report(
        portfolio,
        accounts,
        transactions,
        start_date=start_date,
        end_date=end_date,
    )
    summary = report["summary"]
    resolved_start_date = summary["start_date"]
    resolved_end_date = summary["end_date"]
    start_groups = (
        _group_boundary_holdings_by_taxonomy(
            taxonomy=taxonomy,
            taxonomy_nodes=taxonomy_nodes or [],
            taxonomy_assignments=taxonomy_assignments or [],
            positions=list(report["start_positions"]),
            as_of_date=resolved_start_date,
        )
        if isinstance(resolved_start_date, date)
        else []
    )
    end_groups = (
        _group_boundary_holdings_by_taxonomy(
            taxonomy=taxonomy,
            taxonomy_nodes=taxonomy_nodes or [],
            taxonomy_assignments=taxonomy_assignments or [],
            positions=list(report["end_positions"]),
            as_of_date=resolved_end_date,
        )
        if isinstance(resolved_end_date, date)
        else []
    )
    return {
        "portfolio_id": report["portfolio_id"],
        "base_currency": report["base_currency"],
        "valuation_timezone": report["valuation_timezone"],
        "valuation_cutoff_policy": report["valuation_cutoff_policy"],
        "summary": {
            "axis": "taxonomy",
            "taxonomy_id": resolved_taxonomy_id,
            "start_date": resolved_start_date,
            "end_date": resolved_end_date,
            "start_group_count": len(start_groups),
            "end_group_count": len(end_groups),
            "start_total_market_value_base": sum(
                (_safe_float(item.get("market_value_base")) or 0.0) for item in start_groups
            ) if start_groups else 0.0,
            "end_total_market_value_base": sum(
                (_safe_float(item.get("market_value_base")) or 0.0) for item in end_groups
            ) if end_groups else 0.0,
        },
        "start_groups": start_groups,
        "end_groups": end_groups,
    }


def _calendar_bucket_key(as_of_date: date, frequency: str) -> str:
    if frequency == "weekly":
        iso_year, iso_week, _ = as_of_date.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    return f"{as_of_date.year:04d}-{as_of_date.month:02d}"


def _compute_currency_translation_gain(
    local_amounts_by_currency: dict[str, object] | None,
    *,
    previous_date: date,
    current_date: date,
    base_currency: str,
    direct_fx_assets: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
) -> tuple[float | None, bool]:
    if not isinstance(local_amounts_by_currency, dict) or not local_amounts_by_currency:
        return 0.0, False

    gain = 0.0
    stale_fx_flag = False
    coverage_complete = True
    for raw_currency, raw_amount in local_amounts_by_currency.items():
        currency = _normalized_currency(raw_currency, fallback=base_currency)
        if currency == base_currency:
            continue
        amount = _safe_float(raw_amount)
        if amount is None or abs(amount) <= 1e-9:
            continue
        previous_value, previous_stale = convert_amount_on(
            amount,
            as_of_date=previous_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_assets=direct_fx_assets,
            instrument_detail_cache=instrument_detail_cache,
        )
        current_value, current_stale = convert_amount_on(
            amount,
            as_of_date=current_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_assets=direct_fx_assets,
            instrument_detail_cache=instrument_detail_cache,
        )
        if previous_value is None or current_value is None:
            coverage_complete = False
            continue
        gain += current_value - previous_value
        stale_fx_flag = stale_fx_flag or previous_stale or current_stale

    return (gain if coverage_complete else None), stale_fx_flag


def build_return_calendar_report(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    frequency: str = "monthly",
) -> dict[str, object]:
    report = build_portfolio_performance_report(
        portfolio,
        accounts,
        transactions,
        start_date=start_date,
        end_date=end_date,
    )
    buckets_by_key: dict[str, dict[str, object]] = {}
    ordered_keys: list[str] = []
    for point in report["daily_series"]:
        as_of_date = point.get("as_of_date")
        if not isinstance(as_of_date, date):
            continue
        bucket_key = _calendar_bucket_key(as_of_date, frequency)
        bucket = buckets_by_key.get(bucket_key)
        if bucket is None:
            bucket = {
                "bucket_key": bucket_key,
                "frequency": frequency,
                "start_date": as_of_date,
                "end_date": as_of_date,
                "coverage_state": "unavailable",
                "observation_count": 0,
                "start_nav": point.get("beginning_nav"),
                "end_nav": point.get("ending_nav"),
                "external_cash_in": 0.0,
                "external_cash_out": 0.0,
                "net_external_inflow": 0.0,
                "absolute_change": 0.0,
                "delta": 0.0,
                "_growth_index": 1.0,
                "_has_return": False,
                "_seen_complete": False,
                "_seen_partial": False,
                "_seen_unavailable": False,
                "_absolute_change_complete": True,
                "_delta_complete": True,
            }
            buckets_by_key[bucket_key] = bucket
            ordered_keys.append(bucket_key)

        bucket["end_date"] = as_of_date
        bucket["end_nav"] = point.get("ending_nav")
        bucket["external_cash_in"] += _safe_float(point.get("external_cash_in")) or 0.0
        bucket["external_cash_out"] += _safe_float(point.get("external_cash_out")) or 0.0
        bucket["net_external_inflow"] += _safe_float(point.get("net_external_inflow")) or 0.0

        absolute_change = _safe_float(point.get("absolute_change"))
        if absolute_change is None:
            bucket["_absolute_change_complete"] = False
        else:
            bucket["absolute_change"] += absolute_change
        delta = _safe_float(point.get("delta"))
        if delta is None:
            bucket["_delta_complete"] = False
        else:
            bucket["delta"] += delta

        daily_twr = _safe_float(point.get("daily_twr"))
        if daily_twr is not None and isfinite(daily_twr):
            bucket["_growth_index"] *= 1.0 + daily_twr
            bucket["_has_return"] = True
            bucket["observation_count"] += 1

        coverage_state = str(point.get("coverage_state") or "unavailable")
        if coverage_state == "complete":
            bucket["_seen_complete"] = True
        elif coverage_state == "partial":
            bucket["_seen_partial"] = True
        else:
            bucket["_seen_unavailable"] = True

    rendered_buckets: list[dict[str, object]] = []
    for bucket_key in ordered_keys:
        bucket = buckets_by_key[bucket_key]
        coverage_state = "unavailable"
        if bucket["_seen_complete"]:
            coverage_state = "complete"
            if bucket["_seen_partial"] or bucket["_seen_unavailable"]:
                coverage_state = "partial"
        elif bucket["_seen_partial"]:
            coverage_state = "partial"
        rendered_buckets.append(
            {
                "bucket_key": bucket["bucket_key"],
                "frequency": bucket["frequency"],
                "start_date": bucket["start_date"],
                "end_date": bucket["end_date"],
                "coverage_state": coverage_state,
                "observation_count": bucket["observation_count"],
                "start_nav": bucket["start_nav"],
                "end_nav": bucket["end_nav"],
                "external_cash_in": bucket["external_cash_in"],
                "external_cash_out": bucket["external_cash_out"],
                "net_external_inflow": bucket["net_external_inflow"],
                "absolute_change": bucket["absolute_change"] if bucket["_absolute_change_complete"] else None,
                "delta": bucket["delta"] if bucket["_delta_complete"] else None,
                "cumulative_twr": (bucket["_growth_index"] - 1.0) if bucket["_has_return"] else None,
            }
        )

    return {
        "portfolio_id": report["portfolio_id"],
        "base_currency": report["base_currency"],
        "valuation_timezone": report["valuation_timezone"],
        "valuation_cutoff_policy": report["valuation_cutoff_policy"],
        "summary": {
            "frequency": frequency,
            "bucket_count": len(rendered_buckets),
            "complete_bucket_count": sum(1 for bucket in rendered_buckets if bucket["coverage_state"] == "complete"),
            "partial_bucket_count": sum(1 for bucket in rendered_buckets if bucket["coverage_state"] == "partial"),
            "unavailable_bucket_count": sum(
                1 for bucket in rendered_buckets if bucket["coverage_state"] == "unavailable"
            ),
            "start_date": rendered_buckets[0]["start_date"] if rendered_buckets else None,
            "end_date": rendered_buckets[-1]["end_date"] if rendered_buckets else None,
        },
        "buckets": rendered_buckets,
    }


def _account_name_map(accounts: list[dict[str, object]]) -> dict[str, str]:
    return {
        str(account.get("account_id") or ""): str(account.get("account_name") or account.get("account_id") or "")
        for account in accounts
    }


def _cash_bucket_account_ids(accounts: list[dict[str, object]]) -> set[str]:
    return {
        str(account.get("account_id") or "")
        for account in accounts
        if str(account.get("account_id") or "")
        and str(account.get("account_type") or "") == "deposit_account"
    }


def _ensure_contribution_group_state(
    states: dict[str, dict[str, object]],
    *,
    group_key: str,
    group_label: str,
    axis: str,
) -> dict[str, object]:
    return states.setdefault(
        group_key,
        {
            "axis": axis,
            "group_key": group_key,
            "group_label": group_label or group_key,
            "cash_balance_base": 0.0,
            "pending_settlement_base": 0.0,
            "position_market_value_base": 0.0,
            "open_cost_basis_base": 0.0,
            "unrealized_pnl": 0.0,
            "ending_value_base": 0.0,
            "_position_market_value_local_by_currency": defaultdict(float),
            "_cash_balance_local_by_currency": defaultdict(float),
            "_cash_complete": True,
            "_pending_settlement_complete": True,
            "_position_complete": True,
            "_cost_complete": True,
        },
    )


def _build_contribution_group_end_states(
    *,
    axis: str,
    portfolio_id: str,
    accounts: list[dict[str, object]],
    transactions_as_of: list[dict[str, object]],
    position_lots: list[dict[str, object]] | None = None,
    postings: list[dict[str, object]] | None = None,
    as_of_date: date,
    base_currency: str,
    account_cost_methods: dict[str, str],
    account_currency_map: dict[str, str],
    account_name_map: dict[str, str],
    direct_fx_assets: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
) -> dict[str, dict[str, object]]:
    states: dict[str, dict[str, object]] = {}
    resolved_position_lots = (
        position_lots
        if position_lots is not None
        else build_position_lots(portfolio_id, accounts, transactions_as_of)
    )
    open_position_lots = [
        position_lot for position_lot in resolved_position_lots if position_lot.get("status") == "open"
    ]

    for position_lot in open_position_lots:
        if axis == "instrument":
            group_key = str(position_lot.get("asset_id") or "")
            group_label = str(
                (
                    (position_lot.get("instrument_ref") or {}) if isinstance(position_lot.get("instrument_ref"), dict) else {}
                ).get("asset_name")
                or group_key
            )
        else:
            group_key = str(position_lot.get("account_id") or "")
            group_label = account_name_map.get(group_key, group_key)
        if not group_key:
            continue
        state = _ensure_contribution_group_state(
            states,
            group_key=group_key,
            group_label=group_label,
            axis=axis,
        )
        currency = _normalized_currency(position_lot.get("currency"), fallback=base_currency)
        remaining_cost_basis = _safe_float(position_lot.get("remaining_cost_basis")) or 0.0
        converted_cost_basis, cost_basis_fx_stale = convert_amount_on(
            remaining_cost_basis,
            as_of_date=as_of_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_assets=direct_fx_assets,
            instrument_detail_cache=instrument_detail_cache,
        )
        if converted_cost_basis is None:
            state["_cost_complete"] = False
        else:
            state["open_cost_basis_base"] = (_safe_float(state.get("open_cost_basis_base")) or 0.0) + converted_cost_basis
            state["stale_fx_flag"] = bool(state.get("stale_fx_flag")) or cost_basis_fx_stale

        detail = _instrument_detail_cache_get(str(position_lot.get("asset_id") or ""), instrument_detail_cache)
        price_point = (
            _select_market_point_as_of(detail=detail, role="valuation", as_of_date=as_of_date)
            if isinstance(detail, dict)
            else None
        )
        last_price = _safe_float((price_point or {}).get("value"))
        market_value_local = _position_market_value(
            quantity=_safe_float(position_lot.get("remaining_quantity")) or 0.0,
            last_price=last_price,
            instrument_ref=(
                position_lot.get("instrument_ref")
                if isinstance(position_lot.get("instrument_ref"), dict)
                else None
            ),
        )
        converted_market_value, valuation_fx_stale = convert_amount_on(
            market_value_local,
            as_of_date=as_of_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_assets=direct_fx_assets,
            instrument_detail_cache=instrument_detail_cache,
        )
        if market_value_local is None or converted_market_value is None:
            state["_position_complete"] = False
        else:
            state["position_market_value_base"] = (
                (_safe_float(state.get("position_market_value_base")) or 0.0) + converted_market_value
            )
            position_market_values_local_by_currency = state.get("_position_market_value_local_by_currency")
            if isinstance(position_market_values_local_by_currency, (dict, defaultdict)):
                position_market_values_local_by_currency[currency] += market_value_local
            state["stale_price_flag"] = bool(state.get("stale_price_flag")) or bool((price_point or {}).get("stale"))
            state["stale_fx_flag"] = bool(state.get("stale_fx_flag")) or valuation_fx_stale

    if axis == "account":
        resolved_postings = (
            postings
            if postings is not None
            else derive_ledger_postings(
                portfolio_id,
                transactions_as_of,
                account_cost_methods=account_cost_methods,
                account_currency_map=account_currency_map,
            )
        )
        for posting in resolved_postings:
            cash_amount_delta = _safe_float(posting.get("cash_amount_delta"))
            if cash_amount_delta is None:
                continue
            group_key = str(posting.get("account_id") or "")
            if not group_key:
                continue
            state = _ensure_contribution_group_state(
                states,
                group_key=group_key,
                group_label=account_name_map.get(group_key, group_key),
                axis=axis,
            )
            converted_cash_delta, is_stale = convert_amount_on(
                cash_amount_delta,
                as_of_date=as_of_date,
                from_currency=_normalized_currency(posting.get("currency"), fallback=base_currency),
                to_currency=base_currency,
                direct_fx_assets=direct_fx_assets,
                instrument_detail_cache=instrument_detail_cache,
            )
            if converted_cash_delta is None:
                if ledger_posting_effective_date_iso(posting) <= as_of_date.isoformat():
                    state["_cash_complete"] = False
                else:
                    state["_pending_settlement_complete"] = False
                continue
            cash_balances_local_by_currency = state.get("_cash_balance_local_by_currency")
            posting_currency = _normalized_currency(posting.get("currency"), fallback=base_currency)
            if isinstance(cash_balances_local_by_currency, (dict, defaultdict)):
                cash_balances_local_by_currency[posting_currency] += cash_amount_delta
            if ledger_posting_effective_date_iso(posting) <= as_of_date.isoformat():
                state["cash_balance_base"] = (_safe_float(state.get("cash_balance_base")) or 0.0) + converted_cash_delta
            else:
                state["pending_settlement_base"] = (
                    (_safe_float(state.get("pending_settlement_base")) or 0.0) + converted_cash_delta
                )
            state["stale_fx_flag"] = bool(state.get("stale_fx_flag")) or is_stale

    for state in states.values():
        if not state.get("_position_complete"):
            state["position_market_value_base"] = None
        if not state.get("_cost_complete"):
            state["open_cost_basis_base"] = None
        if axis == "account" and not state.get("_cash_complete"):
            state["cash_balance_base"] = None
        if axis == "account" and not state.get("_pending_settlement_complete"):
            state["pending_settlement_base"] = None

        position_market_value_base = _safe_float(state.get("position_market_value_base"))
        open_cost_basis_base = _safe_float(state.get("open_cost_basis_base"))
        cash_balance_base = _safe_float(state.get("cash_balance_base"))
        pending_settlement_base = _safe_float(state.get("pending_settlement_base"))

        if position_market_value_base is not None and open_cost_basis_base is not None:
            state["unrealized_pnl"] = position_market_value_base - open_cost_basis_base
        else:
            state["unrealized_pnl"] = None

        if axis == "account":
            state["ending_value_base"] = (
                cash_balance_base + pending_settlement_base + position_market_value_base
                if (
                    cash_balance_base is not None
                    and pending_settlement_base is not None
                    and position_market_value_base is not None
                )
                else None
            )
        else:
            state["ending_value_base"] = position_market_value_base

        state.pop("_pending_settlement_complete", None)
        state.pop("_cash_complete", None)
        state.pop("_position_complete", None)
        state.pop("_cost_complete", None)

    return states


def _build_contribution_daily_events(
    *,
    axis: str,
    as_of_date: date,
    position_lots: list[dict[str, object]],
    transactions_on_date: list[dict[str, object]],
    base_currency: str,
    account_name_map: dict[str, str],
    direct_fx_assets: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
) -> dict[str, dict[str, object]]:
    events: dict[str, dict[str, object]] = {}
    as_of_iso = as_of_date.isoformat()

    def ensure_event(group_key: str, group_label: str) -> dict[str, object]:
        return events.setdefault(
            group_key,
            {
                "axis": axis,
                "group_key": group_key,
                "group_label": group_label or group_key,
                "realized_pnl": 0.0,
                "income_cash_amount": 0.0,
                "expense_cash_amount": 0.0,
                "fee_amount": 0.0,
                "tax_amount": 0.0,
                "cash_currency_gains": 0.0,
                "asset_currency_gains": 0.0,
            },
        )

    def add_amount(
        *,
        group_key: str,
        group_label: str,
        field_name: str,
        amount: float,
        trade_date: date,
        currency: str,
    ) -> None:
        converted_amount, _ = convert_amount_on(
            amount,
            as_of_date=trade_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_assets=direct_fx_assets,
            instrument_detail_cache=instrument_detail_cache,
        )
        if converted_amount is None:
            event = ensure_event(group_key, group_label)
            event[field_name] = None
            return
        event = ensure_event(group_key, group_label)
        current_value = event.get(field_name)
        if current_value is None:
            return
        event[field_name] = (_safe_float(current_value) or 0.0) + converted_amount

    for position_lot in position_lots:
        if axis == "instrument":
            group_key = str(position_lot.get("asset_id") or "")
            group_label = str(
                (
                    (position_lot.get("instrument_ref") or {}) if isinstance(position_lot.get("instrument_ref"), dict) else {}
                ).get("asset_name")
                or group_key
            )
        else:
            group_key = str(position_lot.get("account_id") or "")
            group_label = account_name_map.get(group_key, group_key)
        if not group_key:
            continue
        for realization in position_lot.get("realizations") or []:
            if not isinstance(realization, dict):
                continue
            if str(realization.get("trade_date") or "") != as_of_iso:
                continue
            realized_pnl = _safe_float(realization.get("realized_pnl"))
            if realized_pnl is None:
                event = ensure_event(group_key, group_label)
                event["realized_pnl"] = None
                continue
            add_amount(
                group_key=group_key,
                group_label=group_label,
                field_name="realized_pnl",
                amount=realized_pnl,
                trade_date=as_of_date,
                currency=_normalized_currency(position_lot.get("currency"), fallback=base_currency),
            )

    for transaction in transactions_on_date:
        transaction_type = str(transaction.get("transaction_type") or "")
        asset_id = str(transaction.get("asset_id") or "")
        account_id = str(transaction.get("account_id") or "")
        currency = _normalized_currency(transaction.get("currency"), fallback=base_currency)
        instrument_name = str(
            (
                (transaction.get("instrument_ref") or {})
                if isinstance(transaction.get("instrument_ref"), dict)
                else {}
            ).get("asset_name")
            or asset_id
        )
        if axis == "instrument":
            group_key = asset_id
            group_label = instrument_name
        else:
            group_key = account_id
            group_label = account_name_map.get(group_key, group_key)
        if not group_key:
            continue

        gross_amount = _safe_float(transaction.get("gross_amount")) or 0.0
        fee_amount = _safe_float(transaction.get("fees")) or 0.0
        tax_amount = _safe_float(transaction.get("taxes")) or 0.0

        if transaction_type in {"dividend", "coupon", "dividend_reinvestment"}:
            if axis == "instrument" and not asset_id:
                continue
            add_amount(
                group_key=group_key,
                group_label=group_label,
                field_name="income_cash_amount",
                amount=gross_amount,
                trade_date=as_of_date,
                currency=currency,
            )
        elif (
            transaction_type == "interest"
            and axis == "account"
        ):
            add_amount(
                group_key=group_key,
                group_label=group_label,
                field_name="income_cash_amount",
                amount=gross_amount,
                trade_date=as_of_date,
                currency=currency,
            )

        if transaction_type in {"fee", "tax"}:
            if axis == "instrument" and not asset_id:
                continue
            add_amount(
                group_key=group_key,
                group_label=group_label,
                field_name="expense_cash_amount",
                amount=gross_amount,
                trade_date=as_of_date,
                currency=currency,
            )
            add_amount(
                group_key=group_key,
                group_label=group_label,
                field_name="fee_amount" if transaction_type == "fee" else "tax_amount",
                amount=gross_amount,
                trade_date=as_of_date,
                currency=currency,
            )

        if fee_amount > 0 or tax_amount > 0:
            attached_expense = fee_amount + tax_amount
            if attached_expense > 0 and (
                transaction_type in NON_CAPITALIZED_ATTACHED_CHARGE_TRANSACTION_TYPES
            ):
                if axis == "instrument" and not asset_id:
                    continue
                add_amount(
                    group_key=group_key,
                    group_label=group_label,
                    field_name="expense_cash_amount",
                    amount=attached_expense,
                    trade_date=as_of_date,
                    currency=currency,
                )
                if fee_amount > 0:
                    add_amount(
                        group_key=group_key,
                        group_label=group_label,
                        field_name="fee_amount",
                        amount=fee_amount,
                        trade_date=as_of_date,
                        currency=currency,
                    )
                if tax_amount > 0:
                    add_amount(
                        group_key=group_key,
                        group_label=group_label,
                        field_name="tax_amount",
                        amount=tax_amount,
                        trade_date=as_of_date,
                        currency=currency,
                    )

    return events


def _build_contribution_slices_for_date(
    *,
    axis: str,
    as_of_date: date,
    previous_states: dict[str, dict[str, object]],
    current_states: dict[str, dict[str, object]],
    current_events: dict[str, dict[str, object]],
    snapshot: dict[str, object] | None,
    previous_date: date,
    base_currency: str,
    direct_fx_assets: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
) -> list[dict[str, object]]:
    daily_slices: list[dict[str, object]] = []
    group_keys = sorted(set(previous_states) | set(current_states) | set(current_events))
    beginning_nav = _safe_float((snapshot or {}).get("beginning_nav"))
    ending_nav = _safe_float((snapshot or {}).get("ending_nav"))

    for candidate_group_key in group_keys:
        previous_state = previous_states.get(candidate_group_key)
        current_state = current_states.get(candidate_group_key)
        current_event = current_events.get(candidate_group_key)

        group_label = str(
            (current_state or {}).get("group_label")
            or (previous_state or {}).get("group_label")
            or (current_event or {}).get("group_label")
            or candidate_group_key
        )

        if previous_state is None:
            beginning_value_base = 0.0
            previous_unrealized_pnl = 0.0
        else:
            beginning_value_base = _safe_float(previous_state.get("ending_value_base"))
            previous_unrealized_pnl = _safe_float(previous_state.get("unrealized_pnl"))

        if current_state is None:
            ending_value_base = 0.0
            ending_cash_balance_base = 0.0 if axis == "account" else None
            ending_position_market_value_base = 0.0
            ending_open_cost_basis_base = 0.0
            ending_unrealized_pnl = 0.0
        else:
            ending_value_base = _safe_float(current_state.get("ending_value_base"))
            ending_cash_balance_base = _safe_float(current_state.get("cash_balance_base"))
            ending_position_market_value_base = _safe_float(current_state.get("position_market_value_base"))
            ending_open_cost_basis_base = _safe_float(current_state.get("open_cost_basis_base"))
            ending_unrealized_pnl = _safe_float(current_state.get("unrealized_pnl"))

        realized_pnl = _safe_float((current_event or {}).get("realized_pnl"))
        income_cash_amount = _safe_float((current_event or {}).get("income_cash_amount"))
        expense_cash_amount = _safe_float((current_event or {}).get("expense_cash_amount"))
        fee_amount = _safe_float((current_event or {}).get("fee_amount"))
        tax_amount = _safe_float((current_event or {}).get("tax_amount"))
        cash_currency_gains = None
        asset_currency_gains = None
        if previous_state is None:
            cash_currency_gains = 0.0 if axis == "account" else None
            asset_currency_gains = 0.0
        else:
            asset_currency_gains, _ = _compute_currency_translation_gain(
                previous_state.get("_position_market_value_local_by_currency"),
                previous_date=previous_date,
                current_date=as_of_date,
                base_currency=base_currency,
                direct_fx_assets=direct_fx_assets,
                instrument_detail_cache=instrument_detail_cache,
            )
            if axis == "account":
                cash_currency_gains, _ = _compute_currency_translation_gain(
                    previous_state.get("_cash_balance_local_by_currency"),
                    previous_date=previous_date,
                    current_date=as_of_date,
                    base_currency=base_currency,
                    direct_fx_assets=direct_fx_assets,
                    instrument_detail_cache=instrument_detail_cache,
                )
            else:
                cash_currency_gains = None
        if realized_pnl is None and current_event is None:
            realized_pnl = 0.0
        if income_cash_amount is None and current_event is None:
            income_cash_amount = 0.0
        if expense_cash_amount is None and current_event is None:
            expense_cash_amount = 0.0
        if fee_amount is None and current_event is None:
            fee_amount = 0.0
        if tax_amount is None and current_event is None:
            tax_amount = 0.0
        if asset_currency_gains is None and current_event is None:
            asset_currency_gains = 0.0
        if axis == "account" and cash_currency_gains is None and current_event is None:
            cash_currency_gains = 0.0

        unrealized_pnl_change = None
        if (
            (previous_state is None or previous_unrealized_pnl is not None)
            and (current_state is None or ending_unrealized_pnl is not None)
        ):
            unrealized_pnl_change = (ending_unrealized_pnl or 0.0) - (previous_unrealized_pnl or 0.0)

        total_pnl = None
        if (
            realized_pnl is not None
            and income_cash_amount is not None
            and expense_cash_amount is not None
            and unrealized_pnl_change is not None
        ):
            total_pnl = realized_pnl + income_cash_amount - expense_cash_amount + unrealized_pnl_change
            if asset_currency_gains is not None:
                total_pnl += asset_currency_gains
            if axis == "account" and cash_currency_gains is not None:
                total_pnl += cash_currency_gains

        slice_coverage_state = str((snapshot or {}).get("coverage_state") or "unavailable")
        if (
            (previous_state is not None and beginning_value_base is None)
            or (current_state is not None and ending_value_base is None)
            or (current_state is not None and ending_position_market_value_base is None)
            or (current_state is not None and ending_open_cost_basis_base is None)
            or (axis == "account" and current_state is not None and ending_cash_balance_base is None)
            or (asset_currency_gains is None)
            or (axis == "account" and cash_currency_gains is None)
            or total_pnl is None
        ):
            if slice_coverage_state == "complete":
                slice_coverage_state = "partial"

        daily_slices.append(
            {
                "as_of_date": as_of_date,
                "axis": axis,
                "group_key": candidate_group_key,
                "group_label": group_label,
                "coverage_state": slice_coverage_state,
                "market_observation_count": int((snapshot or {}).get("market_observation_count") or 0),
                "return_observation_eligible": bool((snapshot or {}).get("return_observation_eligible")),
                "beginning_value_base": beginning_value_base,
                "ending_value_base": ending_value_base,
                "beginning_weight": (
                    beginning_value_base / beginning_nav
                    if beginning_value_base is not None and beginning_nav is not None and beginning_nav > 1e-9
                    else None
                ),
                "ending_weight": (
                    ending_value_base / ending_nav
                    if ending_value_base is not None and ending_nav is not None and ending_nav > 1e-9
                    else None
                ),
                "cash_balance_base": ending_cash_balance_base,
                "position_market_value_base": ending_position_market_value_base,
                "open_cost_basis_base": ending_open_cost_basis_base,
                "realized_pnl": realized_pnl,
                "unrealized_pnl": ending_unrealized_pnl,
                "income_cash_amount": income_cash_amount,
                "expense_cash_amount": expense_cash_amount,
                "fee_amount": fee_amount,
                "tax_amount": tax_amount,
                "cash_currency_gains": cash_currency_gains,
                "asset_currency_gains": asset_currency_gains,
                "total_pnl": total_pnl,
                "daily_return": (
                    total_pnl / beginning_value_base
                    if total_pnl is not None and beginning_value_base is not None and beginning_value_base > 1e-9
                    else None
                ),
                "daily_contribution": (
                    total_pnl / beginning_nav
                    if total_pnl is not None and beginning_nav is not None and beginning_nav > 1e-9
                    else None
                ),
            }
        )

    return daily_slices


def _is_effective_on(
    *,
    as_of_date: date,
    effective_from: date | None,
    effective_to: date | None,
) -> bool:
    if effective_from is not None and as_of_date < effective_from:
        return False
    if effective_to is not None and as_of_date > effective_to:
        return False
    return True


def _merge_group_coverage_state(states: list[str]) -> str:
    normalized_states = [state for state in states if state]
    if not normalized_states:
        return "unavailable"
    if all(state == "complete" for state in normalized_states):
        return "complete"
    if any(state in {"complete", "partial"} for state in normalized_states):
        return "partial"
    return "unavailable"


def _resolve_taxonomy_group_for_date(
    *,
    taxonomy: dict[str, object],
    taxonomy_nodes_by_id: dict[str, dict[str, object]],
    assignments_by_entity: dict[str, list[dict[str, object]]],
    target_entity_id: str,
    as_of_date: date,
) -> tuple[str, str]:
    taxonomy_id = str(taxonomy.get("taxonomy_id") or "")
    if not _is_effective_on(
        as_of_date=as_of_date,
        effective_from=_parse_iso_date(taxonomy.get("effective_from")),
        effective_to=_parse_iso_date(taxonomy.get("effective_to")),
    ):
        return (f"unassigned:{taxonomy_id}", "Unassigned")

    active_assignments = [
        assignment
        for assignment in assignments_by_entity.get(target_entity_id, [])
        if str(assignment.get("status") or "active") == "active"
        and _is_effective_on(
            as_of_date=as_of_date,
            effective_from=_parse_iso_date(assignment.get("effective_from")),
            effective_to=_parse_iso_date(assignment.get("effective_to")),
        )
    ]
    if len(active_assignments) > 1:
        raise ValueError(
            f"Multiple active taxonomy assignments overlap for {target_entity_id} on {as_of_date.isoformat()}."
        )
    if not active_assignments:
        return (f"unassigned:{taxonomy_id}", "Unassigned")

    assignment = active_assignments[0]
    taxonomy_node_id = str(assignment.get("taxonomy_node_id") or "")
    taxonomy_node = taxonomy_nodes_by_id.get(taxonomy_node_id)
    if taxonomy_node is None:
        raise ValueError(f"Taxonomy assignment references missing node {taxonomy_node_id}.")
    if str(taxonomy_node.get("status") or "active") != "active":
        raise ValueError(f"Taxonomy node {taxonomy_node_id} is not active.")
    return (
        taxonomy_node_id,
        str(taxonomy_node.get("node_name") or taxonomy_node_id),
    )


def _group_contribution_slices_by_taxonomy(
    *,
    taxonomy: dict[str, object],
    taxonomy_nodes: list[dict[str, object]],
    taxonomy_assignments: list[dict[str, object]],
    base_daily_slices: list[dict[str, object]],
) -> list[dict[str, object]]:
    taxonomy_id = str(taxonomy.get("taxonomy_id") or "")
    target_scope = str(taxonomy.get("primary_assignment_scope") or "")
    taxonomy_nodes_by_id = {
        str(node.get("taxonomy_node_id") or ""): node
        for node in taxonomy_nodes
        if str(node.get("taxonomy_id") or "") == taxonomy_id
    }
    assignments_by_entity: dict[str, list[dict[str, object]]] = defaultdict(list)
    for assignment in taxonomy_assignments:
        if str(assignment.get("taxonomy_id") or "") != taxonomy_id:
            continue
        if str(assignment.get("target_scope") or "") != target_scope:
            continue
        assignments_by_entity[str(assignment.get("target_entity_id") or "")].append(assignment)
    for entity_assignments in assignments_by_entity.values():
        entity_assignments.sort(
            key=lambda item: (
                str(item.get("effective_from") or ""),
                str(item.get("effective_to") or ""),
                str(item.get("assignment_id") or ""),
            )
        )

    grouped: dict[tuple[date, str], dict[str, object]] = {}
    coverage_states_by_group: dict[tuple[date, str], list[str]] = defaultdict(list)

    for base_slice in base_daily_slices:
        as_of_date = base_slice.get("as_of_date")
        if not isinstance(as_of_date, date):
            continue
        base_group_key = str(base_slice.get("group_key") or "")
        taxonomy_group_key, taxonomy_group_label = _resolve_taxonomy_group_for_date(
            taxonomy=taxonomy,
            taxonomy_nodes_by_id=taxonomy_nodes_by_id,
            assignments_by_entity=assignments_by_entity,
            target_entity_id=base_group_key,
            as_of_date=as_of_date,
        )
        slice_key = (as_of_date, taxonomy_group_key)
        grouped_slice = grouped.setdefault(
            slice_key,
            {
                "as_of_date": as_of_date,
                "axis": "taxonomy",
                "group_key": taxonomy_group_key,
                "group_label": taxonomy_group_label,
                "coverage_state": "complete",
                "beginning_value_base": 0.0,
                "ending_value_base": 0.0,
                "beginning_weight": 0.0,
                "ending_weight": 0.0,
                "cash_balance_base": 0.0,
                "position_market_value_base": 0.0,
                "open_cost_basis_base": 0.0,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "income_cash_amount": 0.0,
                "expense_cash_amount": 0.0,
                "fee_amount": 0.0,
                "tax_amount": 0.0,
                "cash_currency_gains": 0.0,
                "asset_currency_gains": 0.0,
                "total_pnl": 0.0,
                "daily_return": None,
                "daily_contribution": 0.0,
            },
        )
        coverage_states_by_group[slice_key].append(str(base_slice.get("coverage_state") or "unavailable"))

        for field_name in (
            "beginning_value_base",
            "ending_value_base",
            "beginning_weight",
            "ending_weight",
            "cash_balance_base",
            "position_market_value_base",
            "open_cost_basis_base",
            "realized_pnl",
            "unrealized_pnl",
            "income_cash_amount",
            "expense_cash_amount",
            "fee_amount",
            "tax_amount",
            "cash_currency_gains",
            "asset_currency_gains",
            "total_pnl",
            "daily_contribution",
        ):
            value = _safe_float(base_slice.get(field_name))
            if value is None:
                grouped_slice[field_name] = None
                continue
            current_value = grouped_slice.get(field_name)
            if current_value is None:
                continue
            grouped_slice[field_name] = (_safe_float(current_value) or 0.0) + value

    grouped_slices = sorted(grouped.values(), key=lambda item: (item["as_of_date"], item["group_key"]))
    for grouped_slice in grouped_slices:
        slice_key = (grouped_slice["as_of_date"], grouped_slice["group_key"])
        grouped_slice["coverage_state"] = _merge_group_coverage_state(coverage_states_by_group[slice_key])
        total_pnl = _safe_float(grouped_slice.get("total_pnl"))
        beginning_value_base = _safe_float(grouped_slice.get("beginning_value_base"))
        grouped_slice["daily_return"] = (
            total_pnl / beginning_value_base
            if total_pnl is not None and beginning_value_base is not None and beginning_value_base > 1e-9
            else None
        )
    return grouped_slices


def _build_taxonomy_contribution_report(
    *,
    taxonomy: dict[str, object],
    base_report: dict[str, object],
    grouped_daily_slices: list[dict[str, object]],
) -> dict[str, object]:
    base_summary = (
        deepcopy(base_report.get("summary"))
        if isinstance(base_report.get("summary"), dict)
        else {}
    )
    taxonomy_id = str(taxonomy.get("taxonomy_id") or "")
    resolved_start_date = _parse_iso_date(base_summary.get("start_date"))
    resolved_end_date = _parse_iso_date(base_summary.get("end_date"))

    if resolved_start_date is None or resolved_end_date is None:
        return {
            "portfolio_id": base_report["portfolio_id"],
            "base_currency": base_report["base_currency"],
            "valuation_timezone": base_report["valuation_timezone"],
            "valuation_cutoff_policy": base_report["valuation_cutoff_policy"],
            "summary": {
                **base_summary,
                "axis": "taxonomy",
                "taxonomy_id": taxonomy_id,
                "slice_count": 0,
                "group_count": 0,
                "total_period_contribution": None,
                "contribution_residual": None,
            },
            "lines": [],
            "daily_slices": [],
        }

    grouped_slices = sorted(grouped_daily_slices, key=lambda item: (item["as_of_date"], item["group_key"]))
    available_beginning_weight_dates = {
        item["as_of_date"]
        for item in grouped_slices
        if isinstance(item.get("as_of_date"), date) and _safe_float(item.get("beginning_weight")) is not None
    }

    line_accumulators: dict[str, dict[str, object]] = {}
    start_values: dict[str, float | None] = {}
    end_values: dict[str, float | None] = {}
    ending_weights: dict[str, float | None] = {}
    first_slice_dates: dict[str, date] = {}

    for grouped_slice in grouped_slices:
        group_key = str(grouped_slice.get("group_key") or "")
        as_of_date = grouped_slice.get("as_of_date")
        if not group_key or not isinstance(as_of_date, date):
            continue

        if group_key not in first_slice_dates or as_of_date < first_slice_dates[group_key]:
            start_values[group_key] = _safe_float(grouped_slice.get("beginning_value_base"))
            first_slice_dates[group_key] = as_of_date
        if as_of_date == resolved_end_date:
            end_values[group_key] = _safe_float(grouped_slice.get("ending_value_base"))
            ending_weights[group_key] = _safe_float(grouped_slice.get("ending_weight"))

        accumulator = line_accumulators.setdefault(
            group_key,
            {
                "axis": "taxonomy",
                "group_key": group_key,
                "group_label": str(grouped_slice.get("group_label") or group_key),
                "start_value_base": 0.0,
                "end_value_base": 0.0,
                "average_weight": 0.0,
                "ending_weight": 0.0,
                "realized_pnl": 0.0,
                "unrealized_pnl_change": 0.0,
                "income_cash_amount": 0.0,
                "expense_cash_amount": 0.0,
                "fee_amount": 0.0,
                "tax_amount": 0.0,
                "cash_currency_gains": 0.0,
                "asset_currency_gains": 0.0,
                "total_pnl": 0.0,
                "period_contribution": 0.0,
            },
        )
        beginning_weight = _safe_float(grouped_slice.get("beginning_weight"))
        if beginning_weight is not None:
            accumulator["average_weight"] = (_safe_float(accumulator.get("average_weight")) or 0.0) + beginning_weight
        for field_name in (
            "realized_pnl",
            "income_cash_amount",
            "expense_cash_amount",
            "fee_amount",
            "tax_amount",
            "cash_currency_gains",
            "asset_currency_gains",
            "total_pnl",
            "daily_contribution",
        ):
            value = _safe_float(grouped_slice.get(field_name))
            if value is None:
                continue
            target_field = "period_contribution" if field_name == "daily_contribution" else field_name
            accumulator[target_field] = (_safe_float(accumulator.get(target_field)) or 0.0) + value
        total_pnl = _safe_float(grouped_slice.get("total_pnl"))
        if total_pnl is not None:
            accumulator["unrealized_pnl_change"] = (
                (_safe_float(accumulator.get("unrealized_pnl_change")) or 0.0)
                + total_pnl
                - (_safe_float(grouped_slice.get("realized_pnl")) or 0.0)
                - (_safe_float(grouped_slice.get("income_cash_amount")) or 0.0)
                + (_safe_float(grouped_slice.get("expense_cash_amount")) or 0.0)
            )

    lines: list[dict[str, object]] = []
    weight_denominator = len(available_beginning_weight_dates)
    end_weight_available = _safe_float(base_summary.get("end_nav")) is not None
    start_value_available = _safe_float(base_summary.get("start_nav")) is not None
    end_value_available = _safe_float(base_summary.get("end_nav")) is not None

    for group_key, accumulator in line_accumulators.items():
        accumulator["start_value_base"] = (
            start_values.get(group_key, 0.0)
            if start_value_available
            else None
        )
        accumulator["end_value_base"] = (
            end_values.get(group_key, 0.0)
            if end_value_available
            else None
        )
        accumulator["ending_weight"] = (
            ending_weights.get(group_key, 0.0)
            if end_weight_available
            else None
        )
        accumulator["average_weight"] = (
            (_safe_float(accumulator.get("average_weight")) or 0.0) / weight_denominator
            if weight_denominator > 0
            else None
        )
        lines.append(accumulator)

    lines.sort(
        key=lambda item: (
            -abs(_safe_float(item.get("period_contribution")) or 0.0),
            str(item.get("group_key") or ""),
        )
    )

    total_period_contribution = sum((_safe_float(item.get("period_contribution")) or 0.0) for item in lines)
    portfolio_arithmetic_return = _safe_float(base_summary.get("portfolio_arithmetic_return"))
    contribution_residual = (
        portfolio_arithmetic_return - total_period_contribution
        if portfolio_arithmetic_return is not None
        else None
    )
    coverage_state = str(base_summary.get("coverage_state") or "unavailable")
    if coverage_state == "complete" and any(
        str(item.get("coverage_state") or "unavailable") != "complete" for item in grouped_slices
    ):
        coverage_state = "partial"

    return {
        "portfolio_id": base_report["portfolio_id"],
        "base_currency": base_report["base_currency"],
        "valuation_timezone": base_report["valuation_timezone"],
        "valuation_cutoff_policy": base_report["valuation_cutoff_policy"],
        "summary": {
            **base_summary,
            "axis": "taxonomy",
            "taxonomy_id": taxonomy_id,
            "coverage_state": coverage_state,
            "slice_count": len(grouped_slices),
            "group_count": len(lines),
            "total_period_contribution": total_period_contribution,
            "contribution_residual": contribution_residual,
        },
        "lines": lines,
        "daily_slices": grouped_slices,
    }


def _apply_taxonomy_boundary_values_to_contribution_report(
    report: dict[str, object],
    *,
    boundary_report: dict[str, object],
) -> dict[str, object]:
    start_groups = {
        str(item.get("group_key") or ""): item
        for item in list(boundary_report.get("start_groups") or [])
        if str(item.get("group_key") or "")
    }
    end_groups = {
        str(item.get("group_key") or ""): item
        for item in list(boundary_report.get("end_groups") or [])
        if str(item.get("group_key") or "")
    }
    existing_lines = {
        str(item.get("group_key") or ""): item
        for item in list(report.get("lines") or [])
        if str(item.get("group_key") or "")
    }
    all_group_keys = set(existing_lines.keys()) | set(start_groups.keys()) | set(end_groups.keys())

    updated_lines: list[dict[str, object]] = []
    for group_key in all_group_keys:
        line = deepcopy(existing_lines.get(group_key) or {})
        start_group = start_groups.get(group_key) or {}
        end_group = end_groups.get(group_key) or {}
        line.setdefault("axis", "taxonomy")
        line["group_key"] = group_key
        line["group_label"] = str(
            line.get("group_label")
            or start_group.get("group_label")
            or end_group.get("group_label")
            or group_key
        )
        line["start_value_base"] = _safe_float(start_group.get("market_value_base")) or 0.0
        line["end_value_base"] = _safe_float(end_group.get("market_value_base")) or 0.0
        line["ending_weight"] = _safe_float(end_group.get("portfolio_weight")) or 0.0
        line.setdefault("average_weight", 0.0)
        line.setdefault("realized_pnl", 0.0)
        line.setdefault("unrealized_pnl_change", 0.0)
        line.setdefault("income_cash_amount", 0.0)
        line.setdefault("expense_cash_amount", 0.0)
        line.setdefault("fee_amount", 0.0)
        line.setdefault("tax_amount", 0.0)
        line.setdefault("cash_currency_gains", 0.0)
        line.setdefault("asset_currency_gains", 0.0)
        line.setdefault("total_pnl", 0.0)
        line.setdefault("period_contribution", 0.0)
        updated_lines.append(line)

    updated_lines.sort(
        key=lambda item: (
            -abs(_safe_float(item.get("period_contribution")) or 0.0),
            str(item.get("group_key") or ""),
        )
    )
    report["lines"] = updated_lines
    summary = report.get("summary")
    if isinstance(summary, dict):
        summary["group_count"] = len(updated_lines)
    return report


def _filter_contribution_report_by_group_key(
    report: dict[str, object],
    *,
    group_key: str | None,
) -> dict[str, object]:
    resolved_group_key = str(group_key or "").strip()
    if not resolved_group_key:
        return report

    filtered_report = deepcopy(report)
    summary = (
        filtered_report.get("summary")
        if isinstance(filtered_report.get("summary"), dict)
        else {}
    )
    lines = [
        item
        for item in list(filtered_report.get("lines") or [])
        if str(item.get("group_key") or "") == resolved_group_key
    ]
    daily_slices = [
        item
        for item in list(filtered_report.get("daily_slices") or [])
        if str(item.get("group_key") or "") == resolved_group_key
    ]
    group_label = None
    if lines:
        group_label = str(lines[0].get("group_label") or resolved_group_key)
    elif daily_slices:
        group_label = str(daily_slices[0].get("group_label") or resolved_group_key)

    filtered_report["lines"] = lines
    filtered_report["daily_slices"] = daily_slices

    total_period_contribution = sum((_safe_float(item.get("period_contribution")) or 0.0) for item in lines)
    portfolio_arithmetic_return = _safe_float(summary.get("portfolio_arithmetic_return"))
    contribution_residual = (
        portfolio_arithmetic_return - total_period_contribution
        if portfolio_arithmetic_return is not None
        else None
    )
    observation_dates = {
        as_of_date
        for item in daily_slices
        if isinstance((as_of_date := item.get("as_of_date")), date)
        and _safe_float(item.get("daily_contribution")) is not None
    }
    coverage_state = _merge_group_coverage_state(
        [str(item.get("coverage_state") or "unavailable") for item in daily_slices]
    )
    if not daily_slices:
        coverage_state = "unavailable"

    summary["group_key"] = resolved_group_key
    summary["group_label"] = group_label
    summary["coverage_state"] = coverage_state
    summary["slice_count"] = len(daily_slices)
    summary["group_count"] = len({str(item.get("group_key") or "") for item in lines})
    summary["observation_count"] = len(observation_dates)
    summary["total_period_contribution"] = total_period_contribution
    summary["contribution_residual"] = contribution_residual
    return filtered_report


def build_contribution_report_from_daily_slices(
    portfolio: dict[str, object],
    snapshots: list[dict[str, object]],
    daily_slices: list[dict[str, object]],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    group_key: str | None = None,
) -> dict[str, object]:
    base_currency = _normalized_currency(portfolio.get("base_currency"))
    valuation_timezone = _resolve_portfolio_valuation_timezone(portfolio)
    valuation_cutoff_policy = _resolve_portfolio_valuation_cutoff_policy(portfolio)

    normalized_snapshots: list[dict[str, object]] = []
    for snapshot in snapshots:
        normalized_snapshot = dict(snapshot)
        snapshot_date = _parse_iso_date(normalized_snapshot.get("as_of_date"))
        if snapshot_date is not None:
            normalized_snapshot["as_of_date"] = snapshot_date
            normalized_snapshots.append(normalized_snapshot)

    normalized_slices: list[dict[str, object]] = []
    for daily_slice in daily_slices:
        normalized_slice = dict(daily_slice)
        slice_date = _parse_iso_date(normalized_slice.get("as_of_date"))
        if slice_date is not None:
            normalized_slice["as_of_date"] = slice_date
            normalized_slices.append(normalized_slice)

    available_dates = [
        item["as_of_date"]
        for item in [*normalized_snapshots, *normalized_slices]
        if isinstance(item.get("as_of_date"), date)
    ]
    resolved_start_date = start_date or (min(available_dates) if available_dates else None)
    resolved_end_date = end_date or (max(available_dates) if available_dates else None)
    if resolved_start_date is None or resolved_end_date is None or resolved_end_date < resolved_start_date:
        return {
            "portfolio_id": str(portfolio.get("portfolio_id") or ""),
            "base_currency": base_currency,
            "valuation_timezone": valuation_timezone,
            "valuation_cutoff_policy": valuation_cutoff_policy,
            "summary": {
                "axis": axis,
                "group_key": str(group_key or "").strip() or None,
                "group_label": None,
                "start_date": start_date,
                "end_date": end_date,
                "coverage_state": "unavailable",
                "slice_count": 0,
                "group_count": 0,
                "observation_count": 0,
                "start_nav": None,
                "end_nav": None,
                "portfolio_arithmetic_return": None,
                "portfolio_cumulative_twr": None,
                "total_period_contribution": None,
                "contribution_residual": None,
            },
            "lines": [],
            "daily_slices": [],
        }

    snapshots_by_date = {
        snapshot["as_of_date"]: snapshot
        for snapshot in normalized_snapshots
        if isinstance(snapshot.get("as_of_date"), date)
    }
    in_period_slices = [
        daily_slice
        for daily_slice in normalized_slices
        if isinstance(daily_slice.get("as_of_date"), date)
        and resolved_start_date <= daily_slice["as_of_date"] <= resolved_end_date
    ]

    contribution_growth_index = 1.0
    has_return_observation = False
    arithmetic_return = 0.0
    observation_count = 0
    for as_of_date in _iter_dates(resolved_start_date, resolved_end_date):
        snapshot = snapshots_by_date.get(as_of_date)
        daily_twr = _safe_float((snapshot or {}).get("daily_twr"))
        if daily_twr is not None and isfinite(daily_twr):
            arithmetic_return += daily_twr
            contribution_growth_index *= 1.0 + daily_twr
            has_return_observation = True
            observation_count += 1

    line_accumulators: dict[str, dict[str, object]] = {}
    for daily_slice in in_period_slices:
        line_group_key = str(daily_slice.get("group_key") or "")
        accumulator = line_accumulators.setdefault(
            line_group_key,
            {
                "axis": axis,
                "group_key": line_group_key,
                "group_label": str(daily_slice.get("group_label") or line_group_key),
                "start_value_base": daily_slice.get("beginning_value_base"),
                "end_value_base": daily_slice.get("ending_value_base"),
                "average_weight": 0.0,
                "_weight_count": 0,
                "ending_weight": daily_slice.get("ending_weight"),
                "realized_pnl": 0.0,
                "unrealized_pnl_change": 0.0,
                "income_cash_amount": 0.0,
                "expense_cash_amount": 0.0,
                "fee_amount": 0.0,
                "tax_amount": 0.0,
                "cash_currency_gains": 0.0,
                "asset_currency_gains": 0.0,
                "total_pnl": 0.0,
                "period_contribution": 0.0,
            },
        )
        accumulator["end_value_base"] = daily_slice.get("ending_value_base")
        accumulator["ending_weight"] = daily_slice.get("ending_weight")
        beginning_weight = _safe_float(daily_slice.get("beginning_weight"))
        if beginning_weight is not None:
            accumulator["average_weight"] = (_safe_float(accumulator.get("average_weight")) or 0.0) + beginning_weight
            accumulator["_weight_count"] = int(accumulator.get("_weight_count") or 0) + 1
        for field_name in (
            "realized_pnl",
            "income_cash_amount",
            "expense_cash_amount",
            "fee_amount",
            "tax_amount",
            "cash_currency_gains",
            "asset_currency_gains",
            "total_pnl",
            "daily_contribution",
        ):
            value = _safe_float(daily_slice.get(field_name))
            if value is None:
                continue
            target_field = "period_contribution" if field_name == "daily_contribution" else field_name
            accumulator[target_field] = (_safe_float(accumulator.get(target_field)) or 0.0) + value
        unrealized_delta = None
        realized_value = _safe_float(daily_slice.get("realized_pnl")) or 0.0
        income_value = _safe_float(daily_slice.get("income_cash_amount")) or 0.0
        expense_value = _safe_float(daily_slice.get("expense_cash_amount")) or 0.0
        total_value = _safe_float(daily_slice.get("total_pnl"))
        if total_value is not None:
            unrealized_delta = total_value - realized_value - income_value + expense_value
        if unrealized_delta is not None:
            accumulator["unrealized_pnl_change"] = (
                (_safe_float(accumulator.get("unrealized_pnl_change")) or 0.0) + unrealized_delta
            )

    lines: list[dict[str, object]] = []
    for accumulator in line_accumulators.values():
        weight_count = int(accumulator.pop("_weight_count", 0))
        average_weight_total = _safe_float(accumulator.get("average_weight")) or 0.0
        accumulator["average_weight"] = (average_weight_total / weight_count) if weight_count > 0 else None
        lines.append(accumulator)
    lines.sort(
        key=lambda item: (
            -abs(_safe_float(item.get("period_contribution")) or 0.0),
            str(item.get("group_key") or ""),
        )
    )

    in_period_snapshots = [
        snapshots_by_date[as_of_date]
        for as_of_date in _iter_dates(resolved_start_date, resolved_end_date)
        if as_of_date in snapshots_by_date
    ]
    coverage_state = "unavailable"
    if in_period_snapshots:
        coverage_state = "complete"
        if any(str(snapshot.get("coverage_state") or "unavailable") != "complete" for snapshot in in_period_snapshots):
            coverage_state = "partial"
        if any(str(item.get("coverage_state") or "unavailable") != "complete" for item in in_period_slices):
            coverage_state = "partial"

    total_period_contribution = sum((_safe_float(line.get("period_contribution")) or 0.0) for line in lines)
    portfolio_arithmetic_return = arithmetic_return if observation_count > 0 else None
    portfolio_cumulative_twr = (
        contribution_growth_index - 1.0
        if has_return_observation
        else None
    )
    contribution_residual = (
        portfolio_arithmetic_return - total_period_contribution
        if portfolio_arithmetic_return is not None
        else None
    )

    report = {
        "portfolio_id": str(portfolio.get("portfolio_id") or ""),
        "base_currency": base_currency,
        "valuation_timezone": valuation_timezone,
        "valuation_cutoff_policy": valuation_cutoff_policy,
        "summary": {
            "axis": axis,
            "group_key": None,
            "group_label": None,
            "start_date": resolved_start_date,
            "end_date": resolved_end_date,
            "coverage_state": coverage_state,
            "slice_count": len(in_period_slices),
            "group_count": len(lines),
            "observation_count": observation_count,
            "start_nav": _safe_float((in_period_snapshots[0] if in_period_snapshots else {}).get("beginning_nav")),
            "end_nav": _safe_float((in_period_snapshots[-1] if in_period_snapshots else {}).get("ending_nav")),
            "portfolio_arithmetic_return": portfolio_arithmetic_return,
            "portfolio_cumulative_twr": portfolio_cumulative_twr,
            "total_period_contribution": total_period_contribution,
            "contribution_residual": contribution_residual,
        },
        "lines": lines,
        "daily_slices": in_period_slices,
    }
    return _filter_contribution_report_by_group_key(report, group_key=group_key)


def build_contribution_report(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    taxonomies: list[dict[str, object]] | None = None,
    taxonomy_nodes: list[dict[str, object]] | None = None,
    taxonomy_assignments: list[dict[str, object]] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    taxonomy_id: str | None = None,
    group_key: str | None = None,
) -> dict[str, object]:
    if axis == "taxonomy":
        resolved_taxonomy_id = str(taxonomy_id or "").strip()
        if not resolved_taxonomy_id:
            raise ValueError("taxonomy_id is required when axis=taxonomy.")

        taxonomy = next(
            (
                item
                for item in (taxonomies or [])
                if str(item.get("taxonomy_id") or "") == resolved_taxonomy_id
            ),
            None,
        )
        if taxonomy is None:
            raise ValueError("Selected taxonomy was not found.")
        primary_assignment_scope = str(taxonomy.get("primary_assignment_scope") or "")
        if primary_assignment_scope not in {"instrument", "account", "cash_bucket"}:
            raise ValueError(
                "taxonomy contribution currently supports instrument-, account-, or cash-bucket-scoped taxonomies only."
            )
        base_axis = "account" if primary_assignment_scope in {"account", "cash_bucket"} else "instrument"

        base_report = build_contribution_report(
            portfolio,
            accounts,
            transactions,
            taxonomies=taxonomies,
            taxonomy_nodes=taxonomy_nodes,
            taxonomy_assignments=taxonomy_assignments,
            start_date=start_date,
            end_date=end_date,
            axis=base_axis,
        )
        base_daily_slices = list(base_report.get("daily_slices") or [])
        if primary_assignment_scope == "cash_bucket":
            cash_bucket_ids = _cash_bucket_account_ids(accounts)
            base_daily_slices = [
                item for item in base_daily_slices if str(item.get("group_key") or "") in cash_bucket_ids
            ]
        grouped_daily_slices = _group_contribution_slices_by_taxonomy(
            taxonomy=taxonomy,
            taxonomy_nodes=taxonomy_nodes or [],
            taxonomy_assignments=taxonomy_assignments or [],
            base_daily_slices=base_daily_slices,
        )
        report = _build_taxonomy_contribution_report(
            taxonomy=taxonomy,
            base_report=base_report,
            grouped_daily_slices=grouped_daily_slices,
        )
        if primary_assignment_scope == "instrument":
            boundary_report = build_period_boundary_groups_report(
                portfolio,
                accounts,
                transactions,
                taxonomies=taxonomies,
                taxonomy_nodes=taxonomy_nodes,
                taxonomy_assignments=taxonomy_assignments,
                start_date=start_date,
                end_date=end_date,
                taxonomy_id=resolved_taxonomy_id,
            )
            report = _apply_taxonomy_boundary_values_to_contribution_report(
                report,
                boundary_report=boundary_report,
            )
        return _filter_contribution_report_by_group_key(
            report,
            group_key=group_key,
        )

    if axis not in {"instrument", "account"}:
        raise ValueError("axis must be instrument, account, or taxonomy")

    window = _resolve_snapshot_window(
        portfolio,
        transactions,
        start_date=start_date,
        end_date=end_date,
    )
    base_currency = _normalized_currency(portfolio.get("base_currency"))
    valuation_timezone = _resolve_portfolio_valuation_timezone(portfolio)
    valuation_cutoff_policy = _resolve_portfolio_valuation_cutoff_policy(portfolio)
    if window is None:
        return {
            "portfolio_id": str(portfolio.get("portfolio_id") or ""),
            "base_currency": base_currency,
            "valuation_timezone": valuation_timezone,
            "valuation_cutoff_policy": valuation_cutoff_policy,
            "summary": {
                "axis": axis,
                "group_key": str(group_key or "").strip() or None,
                "group_label": None,
                "start_date": start_date,
                "end_date": end_date,
                "coverage_state": "unavailable",
                "slice_count": 0,
                "group_count": 0,
                "observation_count": 0,
                "start_nav": None,
                "end_nav": None,
                "portfolio_arithmetic_return": None,
                "portfolio_cumulative_twr": None,
                "total_period_contribution": None,
                "contribution_residual": None,
            },
            "lines": [],
            "daily_slices": [],
        }

    resolved_start_date, resolved_end_date = window
    boundary_start_date = resolved_start_date - timedelta(days=1)
    portfolio_view = deepcopy(portfolio)
    portfolio_view["as_of_date"] = resolved_end_date.isoformat()
    snapshots = build_daily_portfolio_snapshots(
        portfolio_view,
        accounts,
        transactions,
        start_date=boundary_start_date,
        end_date=resolved_end_date,
    )
    snapshots_by_date = {
        snapshot["as_of_date"]: snapshot
        for snapshot in snapshots
        if isinstance(snapshot.get("as_of_date"), date)
    }

    sorted_transactions = sorted(transactions, key=_transaction_sort_key)
    transactions_by_date: dict[str, list[dict[str, object]]] = defaultdict(list)
    for transaction in sorted_transactions:
        transactions_by_date[str(transaction.get("trade_date") or "")].append(transaction)

    portfolio_id = str(portfolio.get("portfolio_id") or "")
    account_cost_methods = _account_cost_methods(accounts)
    account_currency_map = _account_currency_map(accounts)
    account_name_map = _account_name_map(accounts)
    fx_payload = get_platform_fx_rates()
    direct_fx_assets = _fx_direct_asset_map(fx_payload)
    instrument_detail_cache: dict[str, dict[str, object] | None] = {}

    group_states_by_date: dict[date, dict[str, dict[str, object]]] = {}
    group_events_by_date: dict[date, dict[str, dict[str, object]]] = {}

    for as_of_date in _iter_dates(boundary_start_date, resolved_end_date):
        transactions_as_of = _transactions_as_of_end_date(sorted_transactions, end_date=as_of_date)
        position_lots = build_position_lots(portfolio_id, accounts, transactions_as_of)
        group_states_by_date[as_of_date] = _build_contribution_group_end_states(
            axis=axis,
            portfolio_id=portfolio_id,
            accounts=accounts,
            transactions_as_of=transactions_as_of,
            as_of_date=as_of_date,
            base_currency=base_currency,
            account_cost_methods=account_cost_methods,
            account_currency_map=account_currency_map,
            account_name_map=account_name_map,
            direct_fx_assets=direct_fx_assets,
            instrument_detail_cache=instrument_detail_cache,
        )
        group_events_by_date[as_of_date] = _build_contribution_daily_events(
            axis=axis,
            as_of_date=as_of_date,
            position_lots=position_lots,
            transactions_on_date=transactions_by_date.get(as_of_date.isoformat(), []),
            base_currency=base_currency,
            account_name_map=account_name_map,
            direct_fx_assets=direct_fx_assets,
            instrument_detail_cache=instrument_detail_cache,
        )

    daily_slices: list[dict[str, object]] = []

    for as_of_date in _iter_dates(resolved_start_date, resolved_end_date):
        snapshot = snapshots_by_date.get(as_of_date)
        previous_date = as_of_date - timedelta(days=1)
        previous_states = group_states_by_date.get(previous_date, {})
        current_states = group_states_by_date.get(as_of_date, {})
        current_events = group_events_by_date.get(as_of_date, {})

        daily_slices.extend(
            _build_contribution_slices_for_date(
                axis=axis,
                as_of_date=as_of_date,
                previous_states=previous_states,
                current_states=current_states,
                current_events=current_events,
                snapshot=snapshot,
                previous_date=previous_date,
                base_currency=base_currency,
                direct_fx_assets=direct_fx_assets,
                instrument_detail_cache=instrument_detail_cache,
            )
        )

    return build_contribution_report_from_daily_slices(
        portfolio,
        snapshots,
        daily_slices,
        start_date=resolved_start_date,
        end_date=resolved_end_date,
        axis=axis,
        group_key=group_key,
    )


def build_contribution_calendar_report(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    taxonomies: list[dict[str, object]] | None = None,
    taxonomy_nodes: list[dict[str, object]] | None = None,
    taxonomy_assignments: list[dict[str, object]] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    taxonomy_id: str | None = None,
    frequency: str = "monthly",
    group_key: str | None = None,
) -> dict[str, object]:
    if frequency not in {"monthly", "weekly"}:
        raise ValueError("frequency must be monthly or weekly")

    contribution_report = build_contribution_report(
        portfolio,
        accounts,
        transactions,
        taxonomies=taxonomies,
        taxonomy_nodes=taxonomy_nodes,
        taxonomy_assignments=taxonomy_assignments,
        start_date=start_date,
        end_date=end_date,
        axis=axis,
        taxonomy_id=taxonomy_id,
        group_key=group_key,
    )
    summary = (
        deepcopy(contribution_report.get("summary"))
        if isinstance(contribution_report.get("summary"), dict)
        else {}
    )
    resolved_start_date = _parse_iso_date(summary.get("start_date"))
    resolved_end_date = _parse_iso_date(summary.get("end_date"))
    if resolved_start_date is None or resolved_end_date is None:
        return {
            "portfolio_id": contribution_report["portfolio_id"],
            "base_currency": contribution_report["base_currency"],
            "valuation_timezone": contribution_report["valuation_timezone"],
            "valuation_cutoff_policy": contribution_report["valuation_cutoff_policy"],
            "summary": {
                "axis": axis,
                "taxonomy_id": taxonomy_id,
                "group_key": str(group_key or "").strip() or None,
                "group_label": None,
                "frequency": frequency,
                "start_date": start_date,
                "end_date": end_date,
                "bucket_count": 0,
                "group_count": 0,
                "observation_count": 0,
                "total_bucket_contribution": None,
                "contribution_residual": None,
            },
            "buckets": [],
        }

    daily_slices = sorted(
        list(contribution_report.get("daily_slices") or []),
        key=lambda item: (
            item.get("as_of_date") or date.min,
            str(item.get("group_key") or ""),
        ),
    )
    bucket_windows: dict[str, dict[str, object]] = {}
    for as_of_date in _iter_dates(resolved_start_date, resolved_end_date):
        bucket_key = _calendar_bucket_key(as_of_date, frequency)
        bucket = bucket_windows.get(bucket_key)
        if bucket is None:
            bucket_windows[bucket_key] = {
                "bucket_key": bucket_key,
                "start_date": as_of_date,
                "end_date": as_of_date,
                "available_weight_dates": set(),
            }
        else:
            bucket["end_date"] = as_of_date

    bucket_accumulators: dict[tuple[str, str], dict[str, object]] = {}
    bucket_start_values: dict[tuple[str, str], float | None] = {}
    bucket_end_values: dict[tuple[str, str], float | None] = {}
    bucket_ending_weights: dict[tuple[str, str], float | None] = {}
    bucket_coverage_states: dict[tuple[str, str], list[str]] = defaultdict(list)
    bucket_observation_dates: dict[tuple[str, str], set[date]] = defaultdict(set)

    for daily_slice in daily_slices:
        as_of_date = daily_slice.get("as_of_date")
        group_key = str(daily_slice.get("group_key") or "")
        if not isinstance(as_of_date, date) or not group_key:
            continue
        bucket_key = _calendar_bucket_key(as_of_date, frequency)
        window = bucket_windows[bucket_key]
        if _safe_float(daily_slice.get("beginning_weight")) is not None:
            available_dates = window.get("available_weight_dates")
            if isinstance(available_dates, set):
                available_dates.add(as_of_date)
        bucket_group_key = (bucket_key, group_key)
        if as_of_date == window["start_date"]:
            bucket_start_values[bucket_group_key] = _safe_float(daily_slice.get("beginning_value_base"))
        if as_of_date == window["end_date"]:
            bucket_end_values[bucket_group_key] = _safe_float(daily_slice.get("ending_value_base"))
            bucket_ending_weights[bucket_group_key] = _safe_float(daily_slice.get("ending_weight"))

        bucket_coverage_states[bucket_group_key].append(str(daily_slice.get("coverage_state") or "unavailable"))
        if _safe_float(daily_slice.get("daily_contribution")) is not None:
            bucket_observation_dates[bucket_group_key].add(as_of_date)

        accumulator = bucket_accumulators.setdefault(
            bucket_group_key,
            {
                "bucket_key": bucket_key,
                "frequency": frequency,
                "start_date": window["start_date"],
                "end_date": window["end_date"],
                "axis": axis,
                "group_key": group_key,
                "group_label": str(daily_slice.get("group_label") or group_key),
                "coverage_state": "complete",
                "observation_count": 0,
                "beginning_value_base": 0.0,
                "ending_value_base": 0.0,
                "average_weight": 0.0,
                "ending_weight": 0.0,
                "realized_pnl": 0.0,
                "unrealized_pnl_change": 0.0,
                "income_cash_amount": 0.0,
                "expense_cash_amount": 0.0,
                "fee_amount": 0.0,
                "tax_amount": 0.0,
                "cash_currency_gains": 0.0,
                "asset_currency_gains": 0.0,
                "total_pnl": 0.0,
                "bucket_contribution": 0.0,
            },
        )

        beginning_weight = _safe_float(daily_slice.get("beginning_weight"))
        if beginning_weight is not None:
            accumulator["average_weight"] = (_safe_float(accumulator.get("average_weight")) or 0.0) + beginning_weight
        for field_name in (
            "realized_pnl",
            "income_cash_amount",
            "expense_cash_amount",
            "fee_amount",
            "tax_amount",
            "cash_currency_gains",
            "asset_currency_gains",
            "total_pnl",
            "daily_contribution",
        ):
            value = _safe_float(daily_slice.get(field_name))
            if value is None:
                continue
            target_field = "bucket_contribution" if field_name == "daily_contribution" else field_name
            accumulator[target_field] = (_safe_float(accumulator.get(target_field)) or 0.0) + value

        total_pnl = _safe_float(daily_slice.get("total_pnl"))
        if total_pnl is not None:
            accumulator["unrealized_pnl_change"] = (
                (_safe_float(accumulator.get("unrealized_pnl_change")) or 0.0)
                + total_pnl
                - (_safe_float(daily_slice.get("realized_pnl")) or 0.0)
                - (_safe_float(daily_slice.get("income_cash_amount")) or 0.0)
                + (_safe_float(daily_slice.get("expense_cash_amount")) or 0.0)
            )

    rendered_buckets: list[dict[str, object]] = []
    for bucket_group_key, accumulator in bucket_accumulators.items():
        bucket_key, group_key = bucket_group_key
        window = bucket_windows[bucket_key]
        available_weight_dates = window.get("available_weight_dates")
        weight_denominator = len(available_weight_dates) if isinstance(available_weight_dates, set) else 0
        accumulator["coverage_state"] = _merge_group_coverage_state(bucket_coverage_states[bucket_group_key])
        accumulator["observation_count"] = len(bucket_observation_dates[bucket_group_key])
        accumulator["beginning_value_base"] = (
            bucket_start_values.get(bucket_group_key, 0.0)
            if _safe_float(summary.get("start_nav")) is not None
            else None
        )
        accumulator["ending_value_base"] = (
            bucket_end_values.get(bucket_group_key, 0.0)
            if _safe_float(summary.get("end_nav")) is not None
            else None
        )
        accumulator["ending_weight"] = (
            bucket_ending_weights.get(bucket_group_key, 0.0)
            if bucket_group_key in bucket_ending_weights or _safe_float(summary.get("end_nav")) is not None
            else None
        )
        accumulator["average_weight"] = (
            (_safe_float(accumulator.get("average_weight")) or 0.0) / weight_denominator
            if weight_denominator > 0
            else None
        )
        rendered_buckets.append(accumulator)

    rendered_buckets.sort(
        key=lambda item: (
            item["start_date"],
            -abs(_safe_float(item.get("bucket_contribution")) or 0.0),
            str(item.get("group_key") or ""),
        )
    )

    total_bucket_contribution = sum((_safe_float(item.get("bucket_contribution")) or 0.0) for item in rendered_buckets)
    total_period_contribution = _safe_float(summary.get("total_period_contribution"))
    contribution_residual = (
        total_period_contribution - total_bucket_contribution
        if total_period_contribution is not None
        else None
    )

    return {
        "portfolio_id": contribution_report["portfolio_id"],
        "base_currency": contribution_report["base_currency"],
        "valuation_timezone": contribution_report["valuation_timezone"],
        "valuation_cutoff_policy": contribution_report["valuation_cutoff_policy"],
        "summary": {
            "axis": axis,
            "taxonomy_id": summary.get("taxonomy_id"),
            "group_key": summary.get("group_key"),
            "group_label": summary.get("group_label"),
            "frequency": frequency,
            "start_date": resolved_start_date,
            "end_date": resolved_end_date,
            "bucket_count": len(bucket_windows),
            "group_count": len({str(item.get("group_key") or "") for item in rendered_buckets}),
            "observation_count": int(summary.get("observation_count") or 0),
            "total_bucket_contribution": total_bucket_contribution,
            "contribution_residual": contribution_residual,
        },
        "buckets": rendered_buckets,
    }


_CONTRIBUTION_BUCKET_FIELD_MAP: dict[str, str] = {
    "start_value": "start_value_base",
    "end_value": "end_value_base",
    "average_weight": "average_weight",
    "ending_weight": "ending_weight",
    "realized_pnl": "realized_pnl",
    "unrealized_pnl_change": "unrealized_pnl_change",
    "income_cash_amount": "income_cash_amount",
    "expense_cash_amount": "expense_cash_amount",
    "fee_amount": "fee_amount",
    "tax_amount": "tax_amount",
    "cash_currency_gains": "cash_currency_gains",
    "asset_currency_gains": "asset_currency_gains",
    "total_pnl": "total_pnl",
    "contribution": "period_contribution",
}
_CONTRIBUTION_CALENDAR_BUCKET_FIELD_MAP: dict[str, str] = {
    "start_value": "beginning_value_base",
    "end_value": "ending_value_base",
    "average_weight": "average_weight",
    "ending_weight": "ending_weight",
    "realized_pnl": "realized_pnl",
    "unrealized_pnl_change": "unrealized_pnl_change",
    "income_cash_amount": "income_cash_amount",
    "expense_cash_amount": "expense_cash_amount",
    "fee_amount": "fee_amount",
    "tax_amount": "tax_amount",
    "cash_currency_gains": "cash_currency_gains",
    "asset_currency_gains": "asset_currency_gains",
    "total_pnl": "total_pnl",
    "contribution": "bucket_contribution",
}


def build_contribution_bucket_report(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    taxonomies: list[dict[str, object]] | None = None,
    taxonomy_nodes: list[dict[str, object]] | None = None,
    taxonomy_assignments: list[dict[str, object]] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    bucket: str = "total_pnl",
    taxonomy_id: str | None = None,
    group_key: str | None = None,
) -> dict[str, object]:
    field_name = _CONTRIBUTION_BUCKET_FIELD_MAP.get(bucket)
    if field_name is None:
        raise ValueError(
            "bucket must be one of "
            + ", ".join(sorted(_CONTRIBUTION_BUCKET_FIELD_MAP.keys()))
        )

    contribution_report = build_contribution_report(
        portfolio,
        accounts,
        transactions,
        taxonomies=taxonomies,
        taxonomy_nodes=taxonomy_nodes,
        taxonomy_assignments=taxonomy_assignments,
        start_date=start_date,
        end_date=end_date,
        axis=axis,
        taxonomy_id=taxonomy_id,
        group_key=group_key,
    )
    summary = (
        deepcopy(contribution_report.get("summary"))
        if isinstance(contribution_report.get("summary"), dict)
        else {}
    )

    rendered_groups: list[dict[str, object]] = []
    total_amount = 0.0
    total_amount_complete = True
    for line in list(contribution_report.get("lines") or []):
        amount = _safe_float(line.get(field_name))
        rendered_groups.append(
            {
                "axis": axis,
                "taxonomy_id": summary.get("taxonomy_id"),
                "bucket": bucket,
                "group_key": str(line.get("group_key") or ""),
                "group_label": str(line.get("group_label") or line.get("group_key") or ""),
                "amount": amount,
                "start_value": _safe_float(line.get("start_value_base")),
                "end_value": _safe_float(line.get("end_value_base")),
                "total_pnl": _safe_float(line.get("total_pnl")),
                "contribution": _safe_float(line.get("period_contribution")),
            }
        )
        if amount is None:
            total_amount_complete = False
        else:
            total_amount += amount

    rendered_groups.sort(
        key=lambda item: (
            -abs(_safe_float(item.get("amount")) or 0.0),
            str(item.get("group_key") or ""),
        )
    )
    return {
        "portfolio_id": contribution_report["portfolio_id"],
        "base_currency": contribution_report["base_currency"],
        "valuation_timezone": contribution_report["valuation_timezone"],
        "valuation_cutoff_policy": contribution_report["valuation_cutoff_policy"],
        "summary": {
            "axis": axis,
            "taxonomy_id": summary.get("taxonomy_id"),
            "group_key": summary.get("group_key"),
            "group_label": summary.get("group_label"),
            "bucket": bucket,
            "start_date": summary.get("start_date"),
            "end_date": summary.get("end_date"),
            "group_count": len(rendered_groups),
            "total_amount": total_amount if total_amount_complete else None,
        },
        "groups": rendered_groups,
    }


def build_contribution_bucket_calendar_report(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    taxonomies: list[dict[str, object]] | None = None,
    taxonomy_nodes: list[dict[str, object]] | None = None,
    taxonomy_assignments: list[dict[str, object]] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    bucket: str = "total_pnl",
    taxonomy_id: str | None = None,
    frequency: str = "monthly",
    group_key: str | None = None,
) -> dict[str, object]:
    field_name = _CONTRIBUTION_CALENDAR_BUCKET_FIELD_MAP.get(bucket)
    if field_name is None:
        raise ValueError(
            "bucket must be one of "
            + ", ".join(sorted(_CONTRIBUTION_CALENDAR_BUCKET_FIELD_MAP.keys()))
        )

    contribution_calendar_report = build_contribution_calendar_report(
        portfolio,
        accounts,
        transactions,
        taxonomies=taxonomies,
        taxonomy_nodes=taxonomy_nodes,
        taxonomy_assignments=taxonomy_assignments,
        start_date=start_date,
        end_date=end_date,
        axis=axis,
        taxonomy_id=taxonomy_id,
        frequency=frequency,
        group_key=group_key,
    )
    summary = (
        deepcopy(contribution_calendar_report.get("summary"))
        if isinstance(contribution_calendar_report.get("summary"), dict)
        else {}
    )

    rendered_buckets: list[dict[str, object]] = []
    total_amount = 0.0
    total_amount_complete = True
    for bucket_item in list(contribution_calendar_report.get("buckets") or []):
        amount = _safe_float(bucket_item.get(field_name))
        rendered_buckets.append(
            {
                "bucket_key": str(bucket_item.get("bucket_key") or ""),
                "frequency": frequency,
                "axis": axis,
                "taxonomy_id": summary.get("taxonomy_id"),
                "bucket": bucket,
                "group_key": str(bucket_item.get("group_key") or ""),
                "group_label": str(bucket_item.get("group_label") or bucket_item.get("group_key") or ""),
                "start_date": bucket_item.get("start_date"),
                "end_date": bucket_item.get("end_date"),
                "coverage_state": bucket_item.get("coverage_state"),
                "observation_count": int(bucket_item.get("observation_count") or 0),
                "amount": amount,
                "start_value": _safe_float(bucket_item.get("beginning_value_base")),
                "end_value": _safe_float(bucket_item.get("ending_value_base")),
                "total_pnl": _safe_float(bucket_item.get("total_pnl")),
                "contribution": _safe_float(bucket_item.get("bucket_contribution")),
            }
        )
        if amount is None:
            total_amount_complete = False
        else:
            total_amount += amount

    rendered_buckets.sort(
        key=lambda item: (
            item.get("start_date") or date.min,
            -abs(_safe_float(item.get("amount")) or 0.0),
            str(item.get("group_key") or ""),
        )
    )
    return {
        "portfolio_id": contribution_calendar_report["portfolio_id"],
        "base_currency": contribution_calendar_report["base_currency"],
        "valuation_timezone": contribution_calendar_report["valuation_timezone"],
        "valuation_cutoff_policy": contribution_calendar_report["valuation_cutoff_policy"],
        "summary": {
            "axis": axis,
            "taxonomy_id": summary.get("taxonomy_id"),
            "group_key": summary.get("group_key"),
            "group_label": summary.get("group_label"),
            "bucket": bucket,
            "frequency": frequency,
            "start_date": summary.get("start_date"),
            "end_date": summary.get("end_date"),
            "bucket_count": len(rendered_buckets),
            "group_count": len({str(item.get("group_key") or "") for item in rendered_buckets}),
            "observation_count": int(summary.get("observation_count") or 0),
            "total_amount": total_amount if total_amount_complete else None,
        },
        "buckets": rendered_buckets,
    }


def build_period_calculation_groups_report(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    taxonomies: list[dict[str, object]] | None = None,
    taxonomy_nodes: list[dict[str, object]] | None = None,
    taxonomy_assignments: list[dict[str, object]] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    taxonomy_id: str | None = None,
    group_key: str | None = None,
) -> dict[str, object]:
    contribution_report = build_contribution_report(
        portfolio,
        accounts,
        transactions,
        taxonomies=taxonomies,
        taxonomy_nodes=taxonomy_nodes,
        taxonomy_assignments=taxonomy_assignments,
        start_date=start_date,
        end_date=end_date,
        axis=axis,
        taxonomy_id=taxonomy_id,
    )
    summary = (
        deepcopy(contribution_report.get("summary"))
        if isinstance(contribution_report.get("summary"), dict)
        else {}
    )
    resolved_start_date = _parse_iso_date(summary.get("start_date"))
    resolved_end_date = _parse_iso_date(summary.get("end_date"))
    boundary_start_values: dict[str, float | None] = {}
    boundary_end_values: dict[str, float | None] = {}
    boundary_labels: dict[str, str] = {}
    if axis == "taxonomy":
        boundary_report = build_period_boundary_groups_report(
            portfolio,
            accounts,
            transactions,
            taxonomies=taxonomies,
            taxonomy_nodes=taxonomy_nodes,
            taxonomy_assignments=taxonomy_assignments,
            start_date=start_date,
            end_date=end_date,
            taxonomy_id=taxonomy_id,
        )
        for item in list(boundary_report.get("start_groups") or []):
            boundary_group_key = str(item.get("group_key") or "")
            if not boundary_group_key:
                continue
            boundary_start_values[boundary_group_key] = _safe_float(item.get("market_value_base"))
            boundary_labels[boundary_group_key] = str(item.get("group_label") or boundary_group_key)
        for item in list(boundary_report.get("end_groups") or []):
            boundary_group_key = str(item.get("group_key") or "")
            if not boundary_group_key:
                continue
            boundary_end_values[boundary_group_key] = _safe_float(item.get("market_value_base"))
            boundary_labels[boundary_group_key] = str(item.get("group_label") or boundary_group_key)
    else:
        for item in list(contribution_report.get("daily_slices") or []):
            slice_group_key = str(item.get("group_key") or "")
            as_of_date = item.get("as_of_date")
            if not slice_group_key or not isinstance(as_of_date, date):
                continue
            boundary_labels[slice_group_key] = str(item.get("group_label") or slice_group_key)
            if as_of_date == resolved_start_date:
                start_value_field = "beginning_value_base" if start_date is not None else "ending_value_base"
                boundary_start_values[slice_group_key] = _safe_float(item.get(start_value_field))
            if as_of_date == resolved_end_date:
                boundary_end_values[slice_group_key] = _safe_float(item.get("ending_value_base"))

    line_map = {
        str(item.get("group_key") or ""): item
        for item in list(contribution_report.get("lines") or [])
        if str(item.get("group_key") or "")
    }
    group_keys = set(line_map.keys()) | set(boundary_start_values.keys()) | set(boundary_end_values.keys())
    groups: list[dict[str, object]] = []
    total_initial_value = 0.0
    total_final_value = 0.0
    initial_value_complete = True
    final_value_complete = True
    total_pnl = 0.0
    total_pnl_complete = True

    for candidate_group_key in sorted(group_keys):
        line = line_map.get(candidate_group_key, {})
        initial_value = (
            boundary_start_values.get(candidate_group_key, 0.0)
            if boundary_start_values
            else _safe_float(line.get("start_value_base"))
        )
        final_value = (
            boundary_end_values.get(candidate_group_key, 0.0)
            if boundary_end_values
            else _safe_float(line.get("end_value_base"))
        )
        delta = (
            final_value - initial_value
            if initial_value is not None and final_value is not None
            else None
        )
        group_total_pnl = _safe_float(line.get("total_pnl"))
        residual_delta = (
            delta - group_total_pnl
            if delta is not None and group_total_pnl is not None
            else None
        )
        groups.append(
            {
                "axis": axis,
                "taxonomy_id": summary.get("taxonomy_id"),
                "group_key": candidate_group_key,
                "group_label": str(
                    line.get("group_label")
                    or boundary_labels.get(candidate_group_key)
                    or candidate_group_key
                ),
                "initial_value": initial_value,
                "final_value": final_value,
                "delta": delta,
                "residual_delta": residual_delta,
                "realized_capital_gains": _safe_float(line.get("realized_pnl")),
                "unrealized_pnl_change": _safe_float(line.get("unrealized_pnl_change")),
                "earnings": _safe_float(line.get("income_cash_amount")),
                "expense_cash_amount": _safe_float(line.get("expense_cash_amount")),
                "fees": _safe_float(line.get("fee_amount")),
                "taxes": _safe_float(line.get("tax_amount")),
                "cash_currency_gains": _safe_float(line.get("cash_currency_gains")),
                "asset_currency_gains": _safe_float(line.get("asset_currency_gains")),
                "total_pnl": group_total_pnl,
                "period_contribution": _safe_float(line.get("period_contribution")),
            }
        )
        if initial_value is None:
            initial_value_complete = False
        else:
            total_initial_value += initial_value
        if final_value is None:
            final_value_complete = False
        else:
            total_final_value += final_value
        if group_total_pnl is None:
            total_pnl_complete = False
        else:
            total_pnl += group_total_pnl

    groups.sort(
        key=lambda item: (
            -abs(_safe_float(item.get("period_contribution")) or 0.0),
            str(item.get("group_key") or ""),
        )
    )
    resolved_group_key = str(group_key or "").strip()
    group_label = None
    if resolved_group_key:
        groups = [item for item in groups if str(item.get("group_key") or "") == resolved_group_key]
        if groups:
            group_label = str(groups[0].get("group_label") or resolved_group_key)

    total_initial_value = 0.0
    total_final_value = 0.0
    total_pnl = 0.0
    total_period_contribution = 0.0
    initial_value_complete = True
    final_value_complete = True
    total_pnl_complete = True
    total_period_contribution_complete = True
    for item in groups:
        initial_value = _safe_float(item.get("initial_value"))
        final_value = _safe_float(item.get("final_value"))
        group_total_pnl = _safe_float(item.get("total_pnl"))
        group_period_contribution = _safe_float(item.get("period_contribution"))
        if initial_value is None:
            initial_value_complete = False
        else:
            total_initial_value += initial_value
        if final_value is None:
            final_value_complete = False
        else:
            total_final_value += final_value
        if group_total_pnl is None:
            total_pnl_complete = False
        else:
            total_pnl += group_total_pnl
        if group_period_contribution is None:
            total_period_contribution_complete = False
        else:
            total_period_contribution += group_period_contribution

    total_delta = total_final_value - total_initial_value if initial_value_complete and final_value_complete else None
    total_residual_delta = total_delta - total_pnl if total_delta is not None and total_pnl_complete else None
    portfolio_arithmetic_return = _safe_float(summary.get("portfolio_arithmetic_return"))
    contribution_residual = (
        portfolio_arithmetic_return - total_period_contribution
        if portfolio_arithmetic_return is not None and total_period_contribution_complete
        else None
    )
    return {
        "portfolio_id": contribution_report["portfolio_id"],
        "base_currency": contribution_report["base_currency"],
        "valuation_timezone": contribution_report["valuation_timezone"],
        "valuation_cutoff_policy": contribution_report["valuation_cutoff_policy"],
        "summary": {
            "axis": axis,
            "taxonomy_id": summary.get("taxonomy_id"),
            "group_key": resolved_group_key or None,
            "group_label": group_label,
            "start_date": summary.get("start_date"),
            "end_date": summary.get("end_date"),
            "group_count": len(groups),
            "total_initial_value": total_initial_value if initial_value_complete else None,
            "total_final_value": total_final_value if final_value_complete else None,
            "total_delta": total_delta,
            "total_residual_delta": total_residual_delta,
            "total_pnl": total_pnl if total_pnl_complete else None,
            "total_period_contribution": (
                total_period_contribution if total_period_contribution_complete else None
            ),
            "contribution_residual": contribution_residual,
        },
        "groups": groups,
    }


def build_period_calculation_groups_calendar_report(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    taxonomies: list[dict[str, object]] | None = None,
    taxonomy_nodes: list[dict[str, object]] | None = None,
    taxonomy_assignments: list[dict[str, object]] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    taxonomy_id: str | None = None,
    frequency: str = "monthly",
    group_key: str | None = None,
) -> dict[str, object]:
    contribution_calendar_report = build_contribution_calendar_report(
        portfolio,
        accounts,
        transactions,
        taxonomies=taxonomies,
        taxonomy_nodes=taxonomy_nodes,
        taxonomy_assignments=taxonomy_assignments,
        start_date=start_date,
        end_date=end_date,
        axis=axis,
        taxonomy_id=taxonomy_id,
        frequency=frequency,
    )
    summary = (
        deepcopy(contribution_calendar_report.get("summary"))
        if isinstance(contribution_calendar_report.get("summary"), dict)
        else {}
    )

    rendered_buckets: list[dict[str, object]] = []
    total_delta = 0.0
    total_residual_delta = 0.0
    total_pnl = 0.0
    delta_complete = True
    residual_complete = True
    pnl_complete = True
    for bucket in list(contribution_calendar_report.get("buckets") or []):
        initial_value = _safe_float(bucket.get("beginning_value_base"))
        final_value = _safe_float(bucket.get("ending_value_base"))
        delta = (
            final_value - initial_value
            if initial_value is not None and final_value is not None
            else None
        )
        bucket_total_pnl = _safe_float(bucket.get("total_pnl"))
        residual_delta = (
            delta - bucket_total_pnl
            if delta is not None and bucket_total_pnl is not None
            else None
        )
        rendered_buckets.append(
            {
                "bucket_key": str(bucket.get("bucket_key") or ""),
                "frequency": frequency,
                "start_date": bucket.get("start_date"),
                "end_date": bucket.get("end_date"),
                "axis": axis,
                "taxonomy_id": summary.get("taxonomy_id"),
                "group_key": str(bucket.get("group_key") or ""),
                "group_label": str(bucket.get("group_label") or bucket.get("group_key") or ""),
                "coverage_state": str(bucket.get("coverage_state") or "unavailable"),
                "observation_count": int(bucket.get("observation_count") or 0),
                "average_weight": _safe_float(bucket.get("average_weight")),
                "ending_weight": _safe_float(bucket.get("ending_weight")),
                "initial_value": initial_value,
                "final_value": final_value,
                "delta": delta,
                "residual_delta": residual_delta,
                "realized_capital_gains": _safe_float(bucket.get("realized_pnl")),
                "unrealized_pnl_change": _safe_float(bucket.get("unrealized_pnl_change")),
                "earnings": _safe_float(bucket.get("income_cash_amount")),
                "expense_cash_amount": _safe_float(bucket.get("expense_cash_amount")),
                "fees": _safe_float(bucket.get("fee_amount")),
                "taxes": _safe_float(bucket.get("tax_amount")),
                "cash_currency_gains": _safe_float(bucket.get("cash_currency_gains")),
                "asset_currency_gains": _safe_float(bucket.get("asset_currency_gains")),
                "total_pnl": bucket_total_pnl,
                "bucket_contribution": _safe_float(bucket.get("bucket_contribution")),
            }
        )
        if delta is None:
            delta_complete = False
        else:
            total_delta += delta
        if residual_delta is None:
            residual_complete = False
        else:
            total_residual_delta += residual_delta
        if bucket_total_pnl is None:
            pnl_complete = False
        else:
            total_pnl += bucket_total_pnl

    rendered_buckets.sort(
        key=lambda item: (
            item.get("start_date") or date.min,
            -abs(_safe_float(item.get("bucket_contribution")) or 0.0),
            str(item.get("group_key") or ""),
        )
    )
    resolved_group_key = str(group_key or "").strip()
    group_label = None
    total_bucket_contribution = _safe_float(summary.get("total_bucket_contribution"))
    contribution_residual = _safe_float(summary.get("contribution_residual"))
    observation_count = int(summary.get("observation_count") or 0)
    if resolved_group_key:
        rendered_buckets = [
            item
            for item in rendered_buckets
            if str(item.get("group_key") or "") == resolved_group_key
        ]
        if rendered_buckets:
            group_label = str(rendered_buckets[0].get("group_label") or resolved_group_key)
        total_delta = 0.0
        total_residual_delta = 0.0
        total_pnl = 0.0
        total_bucket_contribution = 0.0
        observation_count = 0
        delta_complete = True
        residual_complete = True
        pnl_complete = True
        total_bucket_contribution_complete = True
        for item in rendered_buckets:
            delta = _safe_float(item.get("delta"))
            residual_delta = _safe_float(item.get("residual_delta"))
            bucket_total_pnl = _safe_float(item.get("total_pnl"))
            bucket_contribution = _safe_float(item.get("bucket_contribution"))
            observation_count += int(item.get("observation_count") or 0)
            if delta is None:
                delta_complete = False
            else:
                total_delta += delta
            if residual_delta is None:
                residual_complete = False
            else:
                total_residual_delta += residual_delta
            if bucket_total_pnl is None:
                pnl_complete = False
            else:
                total_pnl += bucket_total_pnl
            if bucket_contribution is None:
                total_bucket_contribution_complete = False
            else:
                total_bucket_contribution += bucket_contribution
        contribution_residual = 0.0 if total_bucket_contribution_complete else None
    return {
        "portfolio_id": contribution_calendar_report["portfolio_id"],
        "base_currency": contribution_calendar_report["base_currency"],
        "valuation_timezone": contribution_calendar_report["valuation_timezone"],
        "valuation_cutoff_policy": contribution_calendar_report["valuation_cutoff_policy"],
        "summary": {
            "axis": axis,
            "taxonomy_id": summary.get("taxonomy_id"),
            "group_key": resolved_group_key or None,
            "group_label": group_label,
            "frequency": frequency,
            "start_date": summary.get("start_date"),
            "end_date": summary.get("end_date"),
            "bucket_count": len({str(item.get("bucket_key") or "") for item in rendered_buckets}),
            "group_count": len({str(item.get("group_key") or "") for item in rendered_buckets}),
            "observation_count": observation_count,
            "total_delta": total_delta if delta_complete else None,
            "total_residual_delta": total_residual_delta if residual_complete else None,
            "total_pnl": total_pnl if pnl_complete else None,
            "total_bucket_contribution": total_bucket_contribution,
            "contribution_residual": contribution_residual,
        },
        "buckets": rendered_buckets,
    }


_CALCULATION_BUCKET_FIELD_MAP: dict[str, str] = {
    "initial_value": "initial_value",
    "final_value": "final_value",
    "capital_gains": "unrealized_pnl_change",
    "realized_capital_gains": "realized_capital_gains",
    "earnings": "earnings",
    "fees": "fees",
    "taxes": "taxes",
    "cash_currency_gains": "cash_currency_gains",
    "asset_currency_gains": "asset_currency_gains",
    "total_pnl": "total_pnl",
    "period_contribution": "period_contribution",
    "residual_delta": "residual_delta",
}
_CALCULATION_ENTRY_BUCKETS = {
    "deposits",
    "withdrawals",
    "earnings",
    "fees",
    "taxes",
    "realized_capital_gains",
}


def build_period_calculation_bucket_report(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    taxonomies: list[dict[str, object]] | None = None,
    taxonomy_nodes: list[dict[str, object]] | None = None,
    taxonomy_assignments: list[dict[str, object]] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    taxonomy_id: str | None = None,
    bucket: str = "total_pnl",
    group_key: str | None = None,
) -> dict[str, object]:
    field_name = _CALCULATION_BUCKET_FIELD_MAP.get(bucket)
    if field_name is None:
        raise ValueError(
            "bucket must be one of "
            + ", ".join(sorted(_CALCULATION_BUCKET_FIELD_MAP.keys()))
        )

    groups_report = build_period_calculation_groups_report(
        portfolio,
        accounts,
        transactions,
        taxonomies=taxonomies,
        taxonomy_nodes=taxonomy_nodes,
        taxonomy_assignments=taxonomy_assignments,
        start_date=start_date,
        end_date=end_date,
        axis=axis,
        taxonomy_id=taxonomy_id,
        group_key=group_key,
    )
    summary = (
        deepcopy(groups_report.get("summary"))
        if isinstance(groups_report.get("summary"), dict)
        else {}
    )

    rendered_groups: list[dict[str, object]] = []
    total_amount = 0.0
    total_amount_complete = True
    for group in list(groups_report.get("groups") or []):
        amount = _safe_float(group.get(field_name))
        rendered_groups.append(
            {
                "axis": axis,
                "taxonomy_id": summary.get("taxonomy_id"),
                "bucket": bucket,
                "group_key": str(group.get("group_key") or ""),
                "group_label": str(group.get("group_label") or group.get("group_key") or ""),
                "amount": amount,
                "initial_value": _safe_float(group.get("initial_value")),
                "final_value": _safe_float(group.get("final_value")),
                "total_pnl": _safe_float(group.get("total_pnl")),
                "period_contribution": _safe_float(group.get("period_contribution")),
            }
        )
        if amount is None:
            total_amount_complete = False
        else:
            total_amount += amount

    rendered_groups.sort(
        key=lambda item: (
            -abs(_safe_float(item.get("amount")) or 0.0),
            str(item.get("group_key") or ""),
        )
    )
    return {
        "portfolio_id": groups_report["portfolio_id"],
        "base_currency": groups_report["base_currency"],
        "valuation_timezone": groups_report["valuation_timezone"],
        "valuation_cutoff_policy": groups_report["valuation_cutoff_policy"],
        "summary": {
            "axis": axis,
            "taxonomy_id": summary.get("taxonomy_id"),
            "group_key": summary.get("group_key"),
            "group_label": summary.get("group_label"),
            "bucket": bucket,
            "start_date": summary.get("start_date"),
            "end_date": summary.get("end_date"),
            "group_count": len(rendered_groups),
            "total_amount": total_amount if total_amount_complete else None,
        },
        "groups": rendered_groups,
    }


def _build_taxonomy_assignment_context(
    *,
    taxonomies: list[dict[str, object]],
    taxonomy_nodes: list[dict[str, object]],
    taxonomy_assignments: list[dict[str, object]],
    taxonomy_id: str,
) -> tuple[dict[str, object], dict[str, dict[str, object]], dict[str, list[dict[str, object]]], str]:
    taxonomy = next(
        (item for item in taxonomies if str(item.get("taxonomy_id") or "") == taxonomy_id),
        None,
    )
    if taxonomy is None:
        raise ValueError("Selected taxonomy was not found.")
    target_scope = str(taxonomy.get("primary_assignment_scope") or "")
    if target_scope not in {"instrument", "account", "cash_bucket"}:
        raise ValueError(
            "taxonomy calculation entries currently support instrument-, account-, or cash-bucket-scoped taxonomies only."
        )

    taxonomy_nodes_by_id = {
        str(node.get("taxonomy_node_id") or ""): node
        for node in taxonomy_nodes
        if str(node.get("taxonomy_id") or "") == taxonomy_id
    }
    assignments_by_entity: dict[str, list[dict[str, object]]] = defaultdict(list)
    for assignment in taxonomy_assignments:
        if str(assignment.get("taxonomy_id") or "") != taxonomy_id:
            continue
        if str(assignment.get("target_scope") or "") != target_scope:
            continue
        assignments_by_entity[str(assignment.get("target_entity_id") or "")].append(assignment)
    for entity_assignments in assignments_by_entity.values():
        entity_assignments.sort(
            key=lambda item: (
                str(item.get("effective_from") or ""),
                str(item.get("effective_to") or ""),
                str(item.get("assignment_id") or ""),
            )
        )
    return taxonomy, taxonomy_nodes_by_id, assignments_by_entity, target_scope


def _resolve_calculation_entry_group(
    *,
    axis: str,
    trade_date: date | None,
    account_id: str | None,
    account_name_map: dict[str, str],
    asset_id: str | None,
    asset_name: str | None,
    taxonomy_context: tuple[dict[str, object], dict[str, dict[str, object]], dict[str, list[dict[str, object]]], str]
    | None,
) -> tuple[str, str]:
    if axis == "instrument":
        if asset_id:
            return (asset_id, asset_name or asset_id)
        return ("unassigned:instrument", "Unassigned")
    if axis == "account":
        if account_id:
            return (account_id, account_name_map.get(account_id, account_id))
        return ("unassigned:account", "Unassigned")
    if axis != "taxonomy":
        raise ValueError("axis must be instrument, account, or taxonomy")

    if taxonomy_context is None:
        raise ValueError("taxonomy_id is required when axis=taxonomy.")
    taxonomy, taxonomy_nodes_by_id, assignments_by_entity, target_scope = taxonomy_context
    target_entity_id = ""
    if target_scope == "instrument":
        target_entity_id = asset_id or ""
    elif target_scope == "account":
        target_entity_id = account_id or ""
    elif target_scope == "cash_bucket":
        target_entity_id = account_id or ""
    if not target_entity_id or trade_date is None:
        return (f"unassigned:{taxonomy.get('taxonomy_id')}", "Unassigned")
    return _resolve_taxonomy_group_for_date(
        taxonomy=taxonomy,
        taxonomy_nodes_by_id=taxonomy_nodes_by_id,
        assignments_by_entity=assignments_by_entity,
        target_entity_id=target_entity_id,
        as_of_date=trade_date,
    )


def _append_calculation_transaction_entry(
    entries: list[dict[str, object]],
    *,
    axis: str,
    bucket: str,
    transaction: dict[str, object],
    component_kind: str,
    local_amount: float,
    base_currency: str,
    direct_fx_assets: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
    account_name_map: dict[str, str],
    taxonomy_context: tuple[dict[str, object], dict[str, dict[str, object]], dict[str, list[dict[str, object]]], str]
    | None,
) -> None:
    trade_date = _parse_iso_date(transaction.get("trade_date"))
    settlement_date = _parse_iso_date(transaction.get("settlement_date"))
    currency = _normalized_currency(transaction.get("currency"), fallback=base_currency)
    account_id = str(transaction.get("account_id") or "") or None
    asset_id = str(transaction.get("asset_id") or "") or None
    instrument_ref = (
        transaction.get("instrument_ref")
        if isinstance(transaction.get("instrument_ref"), dict)
        else None
    )
    asset_name = str((instrument_ref or {}).get("asset_name") or asset_id or "")
    group_key, group_label = _resolve_calculation_entry_group(
        axis=axis,
        trade_date=trade_date,
        account_id=account_id,
        account_name_map=account_name_map,
        asset_id=asset_id,
        asset_name=asset_name or None,
        taxonomy_context=taxonomy_context,
    )
    base_amount = None
    stale_fx_flag = False
    if trade_date is not None:
        base_amount, stale_fx_flag = convert_amount_on(
            local_amount,
            as_of_date=trade_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_assets=direct_fx_assets,
            instrument_detail_cache=instrument_detail_cache,
        )
    entries.append(
        {
            "axis": axis,
            "taxonomy_id": taxonomy_context[0].get("taxonomy_id") if taxonomy_context is not None else None,
            "bucket": bucket,
            "entry_kind": "transaction",
            "component_kind": component_kind,
            "transaction_id": str(transaction.get("transaction_id") or ""),
            "transaction_type": str(transaction.get("transaction_type") or ""),
            "trade_date": trade_date,
            "settlement_date": settlement_date,
            "group_key": group_key,
            "group_label": group_label,
            "account_id": account_id,
            "account_name": account_name_map.get(account_id, account_id or "") if account_id else None,
            "asset_id": asset_id,
            "asset_name": asset_name or None,
            "currency": currency,
            "local_amount": local_amount,
            "base_amount": base_amount,
            "note": str(transaction.get("note") or "") or None,
            "stale_fx_flag": stale_fx_flag,
        }
    )


_CONTRIBUTION_ENTRY_BUCKETS = {
    "realized_pnl",
    "income_cash_amount",
    "expense_cash_amount",
    "fee_amount",
    "tax_amount",
}


def build_contribution_entries_report(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    taxonomies: list[dict[str, object]] | None = None,
    taxonomy_nodes: list[dict[str, object]] | None = None,
    taxonomy_assignments: list[dict[str, object]] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    taxonomy_id: str | None = None,
    bucket: str = "income_cash_amount",
    group_key: str | None = None,
) -> dict[str, object]:
    if bucket not in _CONTRIBUTION_ENTRY_BUCKETS:
        raise ValueError(
            "bucket must be one of "
            + ", ".join(sorted(_CONTRIBUTION_ENTRY_BUCKETS))
        )
    if axis not in {"instrument", "account", "taxonomy"}:
        raise ValueError("axis must be instrument, account, or taxonomy")

    window = _resolve_snapshot_window(
        portfolio,
        transactions,
        start_date=start_date,
        end_date=end_date,
    )
    base_currency = _normalized_currency(portfolio.get("base_currency"))
    valuation_timezone = _resolve_portfolio_valuation_timezone(portfolio)
    valuation_cutoff_policy = _resolve_portfolio_valuation_cutoff_policy(portfolio)
    if window is None:
        return {
            "portfolio_id": str(portfolio.get("portfolio_id") or ""),
            "base_currency": base_currency,
            "valuation_timezone": valuation_timezone,
            "valuation_cutoff_policy": valuation_cutoff_policy,
            "summary": {
                "axis": axis,
                "taxonomy_id": taxonomy_id,
                "group_key": str(group_key or "").strip() or None,
                "group_label": None,
                "bucket": bucket,
                "start_date": start_date,
                "end_date": end_date,
                "entry_count": 0,
                "total_amount": None,
            },
            "entries": [],
        }

    resolved_start_date, resolved_end_date = window
    sorted_transactions = sorted(transactions, key=_transaction_sort_key)
    period_transactions = _transactions_in_period(
        sorted_transactions,
        start_date=resolved_start_date,
        end_date=resolved_end_date,
    )
    end_boundary_transactions = _transactions_as_of_end_date(
        sorted_transactions,
        end_date=resolved_end_date,
    )

    account_name_map = _account_name_map(accounts)
    fx_payload = get_platform_fx_rates()
    direct_fx_assets = _fx_direct_asset_map(fx_payload)
    instrument_detail_cache: dict[str, dict[str, object] | None] = {}
    taxonomy_context = None
    cash_bucket_account_ids: set[str] = set()
    if axis == "taxonomy":
        resolved_taxonomy_id = str(taxonomy_id or "").strip()
        if not resolved_taxonomy_id:
            raise ValueError("taxonomy_id is required when axis=taxonomy.")
        taxonomy_context = _build_taxonomy_assignment_context(
            taxonomies=taxonomies or [],
            taxonomy_nodes=taxonomy_nodes or [],
            taxonomy_assignments=taxonomy_assignments or [],
            taxonomy_id=resolved_taxonomy_id,
        )
        if taxonomy_context[3] == "cash_bucket":
            cash_bucket_account_ids = _cash_bucket_account_ids(accounts)

    entries: list[dict[str, object]] = []
    if bucket != "realized_pnl":
        for transaction in period_transactions:
            transaction_type = str(transaction.get("transaction_type") or "")
            gross_amount = _safe_float(transaction.get("gross_amount")) or 0.0
            fee_amount = _safe_float(transaction.get("fees")) or 0.0
            tax_amount = _safe_float(transaction.get("taxes")) or 0.0
            asset_id = str(transaction.get("asset_id") or "")
            account_id = str(transaction.get("account_id") or "")
            if (
                taxonomy_context is not None
                and taxonomy_context[3] == "cash_bucket"
                and account_id not in cash_bucket_account_ids
            ):
                continue

            income_account_scoped = axis == "account" or (
                taxonomy_context is not None and taxonomy_context[3] in {"account", "cash_bucket"}
            )

            if bucket == "income_cash_amount":
                if transaction_type in {"dividend", "coupon", "dividend_reinvestment"}:
                    if axis == "instrument" and not asset_id:
                        continue
                    _append_calculation_transaction_entry(
                        entries,
                        axis=axis,
                        bucket=bucket,
                        transaction=transaction,
                        component_kind="gross_amount",
                        local_amount=gross_amount,
                        base_currency=base_currency,
                        direct_fx_assets=direct_fx_assets,
                        instrument_detail_cache=instrument_detail_cache,
                        account_name_map=account_name_map,
                        taxonomy_context=taxonomy_context,
                    )
                elif income_account_scoped and transaction_type == "interest":
                    _append_calculation_transaction_entry(
                        entries,
                        axis=axis,
                        bucket=bucket,
                        transaction=transaction,
                        component_kind="gross_amount",
                        local_amount=gross_amount,
                        base_currency=base_currency,
                        direct_fx_assets=direct_fx_assets,
                        instrument_detail_cache=instrument_detail_cache,
                        account_name_map=account_name_map,
                        taxonomy_context=taxonomy_context,
                    )
            elif bucket == "expense_cash_amount":
                if transaction_type in {"fee", "tax"}:
                    if axis == "instrument" and not asset_id:
                        continue
                    _append_calculation_transaction_entry(
                        entries,
                        axis=axis,
                        bucket=bucket,
                        transaction=transaction,
                        component_kind="gross_amount",
                        local_amount=gross_amount,
                        base_currency=base_currency,
                        direct_fx_assets=direct_fx_assets,
                        instrument_detail_cache=instrument_detail_cache,
                        account_name_map=account_name_map,
                        taxonomy_context=taxonomy_context,
                    )
                attached_expense = fee_amount + tax_amount
                if attached_expense > 0 and (
                    transaction_type in NON_CAPITALIZED_ATTACHED_CHARGE_TRANSACTION_TYPES
                ):
                    if axis == "instrument" and not asset_id:
                        continue
                    _append_calculation_transaction_entry(
                        entries,
                        axis=axis,
                        bucket=bucket,
                        transaction=transaction,
                        component_kind="attached_expense",
                        local_amount=attached_expense,
                        base_currency=base_currency,
                        direct_fx_assets=direct_fx_assets,
                        instrument_detail_cache=instrument_detail_cache,
                        account_name_map=account_name_map,
                        taxonomy_context=taxonomy_context,
                    )
            elif bucket == "fee_amount":
                if transaction_type == "fee":
                    if axis == "instrument" and not asset_id:
                        continue
                    _append_calculation_transaction_entry(
                        entries,
                        axis=axis,
                        bucket=bucket,
                        transaction=transaction,
                        component_kind="gross_amount",
                        local_amount=gross_amount,
                        base_currency=base_currency,
                        direct_fx_assets=direct_fx_assets,
                        instrument_detail_cache=instrument_detail_cache,
                        account_name_map=account_name_map,
                        taxonomy_context=taxonomy_context,
                    )
                if fee_amount > 0 and (
                    transaction_type in NON_CAPITALIZED_ATTACHED_CHARGE_TRANSACTION_TYPES
                ):
                    if axis == "instrument" and not asset_id:
                        continue
                    _append_calculation_transaction_entry(
                        entries,
                        axis=axis,
                        bucket=bucket,
                        transaction=transaction,
                        component_kind="attached_fee",
                        local_amount=fee_amount,
                        base_currency=base_currency,
                        direct_fx_assets=direct_fx_assets,
                        instrument_detail_cache=instrument_detail_cache,
                        account_name_map=account_name_map,
                        taxonomy_context=taxonomy_context,
                    )
            elif bucket == "tax_amount":
                if transaction_type == "tax":
                    if axis == "instrument" and not asset_id:
                        continue
                    _append_calculation_transaction_entry(
                        entries,
                        axis=axis,
                        bucket=bucket,
                        transaction=transaction,
                        component_kind="gross_amount",
                        local_amount=gross_amount,
                        base_currency=base_currency,
                        direct_fx_assets=direct_fx_assets,
                        instrument_detail_cache=instrument_detail_cache,
                        account_name_map=account_name_map,
                        taxonomy_context=taxonomy_context,
                    )
                if tax_amount > 0 and (
                    transaction_type in NON_CAPITALIZED_ATTACHED_CHARGE_TRANSACTION_TYPES
                ):
                    if axis == "instrument" and not asset_id:
                        continue
                    _append_calculation_transaction_entry(
                        entries,
                        axis=axis,
                        bucket=bucket,
                        transaction=transaction,
                        component_kind="attached_tax",
                        local_amount=tax_amount,
                        base_currency=base_currency,
                        direct_fx_assets=direct_fx_assets,
                        instrument_detail_cache=instrument_detail_cache,
                        account_name_map=account_name_map,
                        taxonomy_context=taxonomy_context,
                    )
    else:
        position_lots = build_position_lots(
            str(portfolio.get("portfolio_id") or ""),
            accounts,
            end_boundary_transactions,
        )
        for position_lot in position_lots:
            realizations = position_lot.get("realizations") or []
            if not isinstance(realizations, list):
                continue
            account_id = str(position_lot.get("account_id") or "") or None
            if (
                taxonomy_context is not None
                and taxonomy_context[3] == "cash_bucket"
                and (account_id or "") not in cash_bucket_account_ids
            ):
                continue
            instrument_ref = (
                position_lot.get("instrument_ref")
                if isinstance(position_lot.get("instrument_ref"), dict)
                else None
            )
            asset_id = str(position_lot.get("asset_id") or "") or None
            asset_name = str((instrument_ref or {}).get("asset_name") or asset_id or "")
            currency = _normalized_currency(position_lot.get("currency"), fallback=base_currency)
            for realization in realizations:
                if not isinstance(realization, dict):
                    continue
                transaction_type = str(realization.get("transaction_type") or "")
                if transaction_type not in REALIZED_GAIN_TRANSACTION_TYPES:
                    continue
                trade_date = _parse_iso_date(realization.get("trade_date"))
                if trade_date is None:
                    continue
                if trade_date < resolved_start_date or trade_date > resolved_end_date:
                    continue
                local_amount = _safe_float(realization.get("realized_pnl"))
                realization_group_key, realization_group_label = _resolve_calculation_entry_group(
                    axis=axis,
                    trade_date=trade_date,
                    account_id=account_id,
                    account_name_map=account_name_map,
                    asset_id=asset_id,
                    asset_name=asset_name or None,
                    taxonomy_context=taxonomy_context,
                )
                base_amount = None
                stale_fx_flag = False
                if local_amount is not None:
                    base_amount, stale_fx_flag = convert_amount_on(
                        local_amount,
                        as_of_date=trade_date,
                        from_currency=currency,
                        to_currency=base_currency,
                        direct_fx_assets=direct_fx_assets,
                        instrument_detail_cache=instrument_detail_cache,
                    )
                entries.append(
                    {
                        "axis": axis,
                        "taxonomy_id": taxonomy_context[0].get("taxonomy_id") if taxonomy_context is not None else None,
                        "bucket": bucket,
                        "entry_kind": "realization",
                        "component_kind": "realization",
                        "transaction_id": str(realization.get("transaction_id") or ""),
                        "transaction_type": transaction_type,
                        "trade_date": trade_date,
                        "settlement_date": None,
                        "group_key": realization_group_key,
                        "group_label": realization_group_label,
                        "account_id": account_id,
                        "account_name": account_name_map.get(account_id, account_id or "") if account_id else None,
                        "asset_id": asset_id,
                        "asset_name": asset_name or None,
                        "currency": currency,
                        "local_amount": local_amount,
                        "base_amount": base_amount,
                        "note": None,
                        "stale_fx_flag": stale_fx_flag,
                    }
                )

    entries.sort(
        key=lambda item: (
            item.get("trade_date") or date.min,
            str(item.get("transaction_id") or ""),
            str(item.get("component_kind") or ""),
            str(item.get("group_key") or ""),
        )
    )
    resolved_group_key = str(group_key or "").strip()
    group_label = None
    if resolved_group_key:
        entries = [
            item
            for item in entries
            if str(item.get("group_key") or "") == resolved_group_key
        ]
        if entries:
            group_label = str(entries[0].get("group_label") or resolved_group_key)
    total_amount = 0.0
    total_amount_complete = True
    for entry in entries:
        amount = _safe_float(entry.get("base_amount"))
        if amount is None:
            total_amount_complete = False
        else:
            total_amount += amount

    return {
        "portfolio_id": str(portfolio.get("portfolio_id") or ""),
        "base_currency": base_currency,
        "valuation_timezone": valuation_timezone,
        "valuation_cutoff_policy": valuation_cutoff_policy,
        "summary": {
            "axis": axis,
            "taxonomy_id": taxonomy_context[0].get("taxonomy_id") if taxonomy_context is not None else None,
            "group_key": resolved_group_key or None,
            "group_label": group_label,
            "bucket": bucket,
            "start_date": resolved_start_date,
            "end_date": resolved_end_date,
            "entry_count": len(entries),
            "total_amount": total_amount if total_amount_complete else None,
        },
        "entries": entries,
    }


def build_contribution_entries_calendar_report(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    taxonomies: list[dict[str, object]] | None = None,
    taxonomy_nodes: list[dict[str, object]] | None = None,
    taxonomy_assignments: list[dict[str, object]] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    taxonomy_id: str | None = None,
    bucket: str = "income_cash_amount",
    frequency: str = "monthly",
    group_key: str | None = None,
) -> dict[str, object]:
    if frequency not in {"monthly", "weekly"}:
        raise ValueError("frequency must be monthly or weekly")

    entries_report = build_contribution_entries_report(
        portfolio,
        accounts,
        transactions,
        taxonomies=taxonomies,
        taxonomy_nodes=taxonomy_nodes,
        taxonomy_assignments=taxonomy_assignments,
        start_date=start_date,
        end_date=end_date,
        axis=axis,
        taxonomy_id=taxonomy_id,
        bucket=bucket,
        group_key=group_key,
    )
    summary = (
        deepcopy(entries_report.get("summary"))
        if isinstance(entries_report.get("summary"), dict)
        else {}
    )

    bucket_accumulators: dict[tuple[str, str], dict[str, object]] = {}
    for entry in list(entries_report.get("entries") or []):
        trade_date = entry.get("trade_date")
        if not isinstance(trade_date, date):
            continue
        entry_group_key = str(entry.get("group_key") or "")
        if not entry_group_key:
            continue
        bucket_key = _calendar_bucket_key(trade_date, frequency)
        accumulator_key = (bucket_key, entry_group_key)
        accumulator = bucket_accumulators.setdefault(
            accumulator_key,
            {
                "bucket_key": bucket_key,
                "frequency": frequency,
                "axis": axis,
                "taxonomy_id": summary.get("taxonomy_id"),
                "contribution_bucket": bucket,
                "group_key": entry_group_key,
                "group_label": str(entry.get("group_label") or entry_group_key),
                "start_date": trade_date,
                "end_date": trade_date,
                "entry_count": 0,
                "total_amount": 0.0,
                "_amount_complete": True,
            },
        )
        if trade_date < accumulator["start_date"]:
            accumulator["start_date"] = trade_date
        if trade_date > accumulator["end_date"]:
            accumulator["end_date"] = trade_date
        accumulator["entry_count"] = int(accumulator.get("entry_count") or 0) + 1
        amount = _safe_float(entry.get("base_amount"))
        if amount is None:
            accumulator["_amount_complete"] = False
        else:
            accumulator["total_amount"] = (_safe_float(accumulator.get("total_amount")) or 0.0) + amount

    rendered_buckets: list[dict[str, object]] = []
    total_amount = 0.0
    total_amount_complete = True
    for accumulator in bucket_accumulators.values():
        amount = (
            _safe_float(accumulator.get("total_amount"))
            if bool(accumulator.get("_amount_complete"))
            else None
        )
        if amount is None:
            total_amount_complete = False
        else:
            total_amount += amount
        rendered_buckets.append(
            {
                "bucket_key": accumulator["bucket_key"],
                "frequency": accumulator["frequency"],
                "axis": accumulator["axis"],
                "taxonomy_id": accumulator["taxonomy_id"],
                "contribution_bucket": accumulator["contribution_bucket"],
                "group_key": accumulator["group_key"],
                "group_label": accumulator["group_label"],
                "start_date": accumulator["start_date"],
                "end_date": accumulator["end_date"],
                "entry_count": accumulator["entry_count"],
                "total_amount": amount,
            }
        )

    rendered_buckets.sort(
        key=lambda item: (
            str(item.get("bucket_key") or ""),
            -abs(_safe_float(item.get("total_amount")) or 0.0),
            str(item.get("group_key") or ""),
        )
    )
    return {
        "portfolio_id": entries_report["portfolio_id"],
        "base_currency": entries_report["base_currency"],
        "valuation_timezone": entries_report["valuation_timezone"],
        "valuation_cutoff_policy": entries_report["valuation_cutoff_policy"],
        "summary": {
            "axis": axis,
            "taxonomy_id": summary.get("taxonomy_id"),
            "group_key": summary.get("group_key"),
            "group_label": summary.get("group_label"),
            "bucket": bucket,
            "frequency": frequency,
            "start_date": summary.get("start_date"),
            "end_date": summary.get("end_date"),
            "bucket_count": len({str(item.get('bucket_key') or '') for item in rendered_buckets}),
            "group_count": len({str(item.get('group_key') or '') for item in rendered_buckets}),
            "entry_count": int(summary.get("entry_count") or 0),
            "total_amount": total_amount if total_amount_complete else None,
        },
        "buckets": rendered_buckets,
    }


def build_period_calculation_entries_report(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    taxonomies: list[dict[str, object]] | None = None,
    taxonomy_nodes: list[dict[str, object]] | None = None,
    taxonomy_assignments: list[dict[str, object]] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    taxonomy_id: str | None = None,
    bucket: str = "earnings",
    group_key: str | None = None,
) -> dict[str, object]:
    if bucket not in _CALCULATION_ENTRY_BUCKETS:
        raise ValueError(
            "bucket must be one of "
            + ", ".join(sorted(_CALCULATION_ENTRY_BUCKETS))
        )
    if axis not in {"instrument", "account", "taxonomy"}:
        raise ValueError("axis must be instrument, account, or taxonomy")

    window = _resolve_snapshot_window(
        portfolio,
        transactions,
        start_date=start_date,
        end_date=end_date,
    )
    base_currency = _normalized_currency(portfolio.get("base_currency"))
    valuation_timezone = _resolve_portfolio_valuation_timezone(portfolio)
    valuation_cutoff_policy = _resolve_portfolio_valuation_cutoff_policy(portfolio)
    if window is None:
        return {
            "portfolio_id": str(portfolio.get("portfolio_id") or ""),
            "base_currency": base_currency,
            "valuation_timezone": valuation_timezone,
            "valuation_cutoff_policy": valuation_cutoff_policy,
            "summary": {
                "axis": axis,
                "taxonomy_id": taxonomy_id,
                "group_key": str(group_key or "").strip() or None,
                "group_label": None,
                "bucket": bucket,
                "start_date": start_date,
                "end_date": end_date,
                "entry_count": 0,
                "total_amount": None,
            },
            "entries": [],
        }

    resolved_start_date, resolved_end_date = window
    sorted_transactions = sorted(transactions, key=_transaction_sort_key)
    period_transactions = _transactions_in_period(
        sorted_transactions,
        start_date=resolved_start_date,
        end_date=resolved_end_date,
    )
    end_boundary_transactions = _transactions_as_of_end_date(
        sorted_transactions,
        end_date=resolved_end_date,
    )

    account_name_map = _account_name_map(accounts)
    fx_payload = get_platform_fx_rates()
    direct_fx_assets = _fx_direct_asset_map(fx_payload)
    instrument_detail_cache: dict[str, dict[str, object] | None] = {}
    taxonomy_context = None
    if axis == "taxonomy":
        resolved_taxonomy_id = str(taxonomy_id or "").strip()
        if not resolved_taxonomy_id:
            raise ValueError("taxonomy_id is required when axis=taxonomy.")
        taxonomy_context = _build_taxonomy_assignment_context(
            taxonomies=taxonomies or [],
            taxonomy_nodes=taxonomy_nodes or [],
            taxonomy_assignments=taxonomy_assignments or [],
            taxonomy_id=resolved_taxonomy_id,
        )

    entries: list[dict[str, object]] = []
    if bucket != "realized_capital_gains":
        for transaction in period_transactions:
            transaction_type = str(transaction.get("transaction_type") or "")
            gross_amount = _safe_float(transaction.get("gross_amount")) or 0.0
            fee_amount = _safe_float(transaction.get("fees")) or 0.0
            tax_amount = _safe_float(transaction.get("taxes")) or 0.0
            if bucket == "deposits" and transaction_type in EXTERNAL_CASH_IN_TYPES:
                _append_calculation_transaction_entry(
                    entries,
                    axis=axis,
                    bucket=bucket,
                    transaction=transaction,
                    component_kind="gross_amount",
                    local_amount=gross_amount,
                    base_currency=base_currency,
                    direct_fx_assets=direct_fx_assets,
                    instrument_detail_cache=instrument_detail_cache,
                    account_name_map=account_name_map,
                    taxonomy_context=taxonomy_context,
                )
            elif bucket == "withdrawals" and transaction_type in EXTERNAL_CASH_OUT_TYPES:
                _append_calculation_transaction_entry(
                    entries,
                    axis=axis,
                    bucket=bucket,
                    transaction=transaction,
                    component_kind="gross_amount",
                    local_amount=gross_amount,
                    base_currency=base_currency,
                    direct_fx_assets=direct_fx_assets,
                    instrument_detail_cache=instrument_detail_cache,
                    account_name_map=account_name_map,
                    taxonomy_context=taxonomy_context,
                )
            elif bucket == "earnings" and transaction_type in EARNINGS_TRANSACTION_TYPES:
                _append_calculation_transaction_entry(
                    entries,
                    axis=axis,
                    bucket=bucket,
                    transaction=transaction,
                    component_kind="gross_amount",
                    local_amount=gross_amount,
                    base_currency=base_currency,
                    direct_fx_assets=direct_fx_assets,
                    instrument_detail_cache=instrument_detail_cache,
                    account_name_map=account_name_map,
                    taxonomy_context=taxonomy_context,
                )
            elif bucket == "fees":
                if transaction_type == "fee":
                    _append_calculation_transaction_entry(
                        entries,
                        axis=axis,
                        bucket=bucket,
                        transaction=transaction,
                        component_kind="gross_amount",
                        local_amount=gross_amount,
                        base_currency=base_currency,
                        direct_fx_assets=direct_fx_assets,
                        instrument_detail_cache=instrument_detail_cache,
                        account_name_map=account_name_map,
                        taxonomy_context=taxonomy_context,
                    )
                if fee_amount > 0 and transaction_type in NON_CAPITALIZED_ATTACHED_CHARGE_TRANSACTION_TYPES:
                    _append_calculation_transaction_entry(
                        entries,
                        axis=axis,
                        bucket=bucket,
                        transaction=transaction,
                        component_kind="attached_fee",
                        local_amount=fee_amount,
                        base_currency=base_currency,
                        direct_fx_assets=direct_fx_assets,
                        instrument_detail_cache=instrument_detail_cache,
                        account_name_map=account_name_map,
                        taxonomy_context=taxonomy_context,
                    )
            elif bucket == "taxes":
                if transaction_type == "tax":
                    _append_calculation_transaction_entry(
                        entries,
                        axis=axis,
                        bucket=bucket,
                        transaction=transaction,
                        component_kind="gross_amount",
                        local_amount=gross_amount,
                        base_currency=base_currency,
                        direct_fx_assets=direct_fx_assets,
                        instrument_detail_cache=instrument_detail_cache,
                        account_name_map=account_name_map,
                        taxonomy_context=taxonomy_context,
                    )
                if tax_amount > 0 and transaction_type in NON_CAPITALIZED_ATTACHED_CHARGE_TRANSACTION_TYPES:
                    _append_calculation_transaction_entry(
                        entries,
                        axis=axis,
                        bucket=bucket,
                        transaction=transaction,
                        component_kind="attached_tax",
                        local_amount=tax_amount,
                        base_currency=base_currency,
                        direct_fx_assets=direct_fx_assets,
                        instrument_detail_cache=instrument_detail_cache,
                        account_name_map=account_name_map,
                        taxonomy_context=taxonomy_context,
                    )
    else:
        position_lots = build_position_lots(
            str(portfolio.get("portfolio_id") or ""),
            accounts,
            end_boundary_transactions,
        )
        for position_lot in position_lots:
            realizations = position_lot.get("realizations") or []
            if not isinstance(realizations, list):
                continue
            account_id = str(position_lot.get("account_id") or "") or None
            instrument_ref = (
                position_lot.get("instrument_ref")
                if isinstance(position_lot.get("instrument_ref"), dict)
                else None
            )
            asset_id = str(position_lot.get("asset_id") or "") or None
            asset_name = str((instrument_ref or {}).get("asset_name") or asset_id or "")
            currency = _normalized_currency(position_lot.get("currency"), fallback=base_currency)
            for realization in realizations:
                if not isinstance(realization, dict):
                    continue
                transaction_type = str(realization.get("transaction_type") or "")
                if transaction_type not in REALIZED_GAIN_TRANSACTION_TYPES:
                    continue
                trade_date = _parse_iso_date(realization.get("trade_date"))
                if trade_date is None:
                    continue
                if trade_date < resolved_start_date or trade_date > resolved_end_date:
                    continue
                local_amount = _safe_float(realization.get("realized_pnl"))
                realization_group_key, realization_group_label = _resolve_calculation_entry_group(
                    axis=axis,
                    trade_date=trade_date,
                    account_id=account_id,
                    account_name_map=account_name_map,
                    asset_id=asset_id,
                    asset_name=asset_name or None,
                    taxonomy_context=taxonomy_context,
                )
                base_amount = None
                stale_fx_flag = False
                if local_amount is not None:
                    base_amount, stale_fx_flag = convert_amount_on(
                        local_amount,
                        as_of_date=trade_date,
                        from_currency=currency,
                        to_currency=base_currency,
                        direct_fx_assets=direct_fx_assets,
                        instrument_detail_cache=instrument_detail_cache,
                    )
                entries.append(
                    {
                        "axis": axis,
                        "taxonomy_id": taxonomy_context[0].get("taxonomy_id") if taxonomy_context is not None else None,
                        "bucket": bucket,
                        "entry_kind": "realization",
                        "component_kind": "realization",
                        "transaction_id": str(realization.get("transaction_id") or ""),
                        "transaction_type": transaction_type,
                        "trade_date": trade_date,
                        "settlement_date": None,
                        "group_key": realization_group_key,
                        "group_label": realization_group_label,
                        "account_id": account_id,
                        "account_name": account_name_map.get(account_id, account_id or "") if account_id else None,
                        "asset_id": asset_id,
                        "asset_name": asset_name or None,
                        "currency": currency,
                        "local_amount": local_amount,
                        "base_amount": base_amount,
                        "note": None,
                        "stale_fx_flag": stale_fx_flag,
                    }
                )

    entries.sort(
        key=lambda item: (
            item.get("trade_date") or date.min,
            str(item.get("transaction_id") or ""),
            str(item.get("component_kind") or ""),
            str(item.get("group_key") or ""),
        )
    )
    resolved_group_key = str(group_key or "").strip()
    group_label = None
    if resolved_group_key:
        entries = [
            item
            for item in entries
            if str(item.get("group_key") or "") == resolved_group_key
        ]
        if entries:
            group_label = str(entries[0].get("group_label") or resolved_group_key)
    total_amount = 0.0
    total_amount_complete = True
    for entry in entries:
        amount = _safe_float(entry.get("base_amount"))
        if amount is None:
            total_amount_complete = False
        else:
            total_amount += amount

    return {
        "portfolio_id": str(portfolio.get("portfolio_id") or ""),
        "base_currency": base_currency,
        "valuation_timezone": valuation_timezone,
        "valuation_cutoff_policy": valuation_cutoff_policy,
        "summary": {
            "axis": axis,
            "taxonomy_id": taxonomy_context[0].get("taxonomy_id") if taxonomy_context is not None else None,
            "group_key": resolved_group_key or None,
            "group_label": group_label,
            "bucket": bucket,
            "start_date": resolved_start_date,
            "end_date": resolved_end_date,
            "entry_count": len(entries),
            "total_amount": total_amount if total_amount_complete else None,
        },
        "entries": entries,
    }


def build_period_calculation_entries_calendar_report(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    taxonomies: list[dict[str, object]] | None = None,
    taxonomy_nodes: list[dict[str, object]] | None = None,
    taxonomy_assignments: list[dict[str, object]] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    taxonomy_id: str | None = None,
    bucket: str = "earnings",
    frequency: str = "monthly",
    group_key: str | None = None,
) -> dict[str, object]:
    if frequency not in {"monthly", "weekly"}:
        raise ValueError("frequency must be monthly or weekly")

    entries_report = build_period_calculation_entries_report(
        portfolio,
        accounts,
        transactions,
        taxonomies=taxonomies,
        taxonomy_nodes=taxonomy_nodes,
        taxonomy_assignments=taxonomy_assignments,
        start_date=start_date,
        end_date=end_date,
        axis=axis,
        taxonomy_id=taxonomy_id,
        bucket=bucket,
        group_key=group_key,
    )
    summary = (
        deepcopy(entries_report.get("summary"))
        if isinstance(entries_report.get("summary"), dict)
        else {}
    )

    bucket_accumulators: dict[tuple[str, str], dict[str, object]] = {}
    for entry in list(entries_report.get("entries") or []):
        trade_date = entry.get("trade_date")
        if not isinstance(trade_date, date):
            continue
        entry_group_key = str(entry.get("group_key") or "")
        if not entry_group_key:
            continue
        bucket_key = _calendar_bucket_key(trade_date, frequency)
        accumulator_key = (bucket_key, entry_group_key)
        accumulator = bucket_accumulators.setdefault(
            accumulator_key,
            {
                "bucket_key": bucket_key,
                "frequency": frequency,
                "axis": axis,
                "taxonomy_id": summary.get("taxonomy_id"),
                "calculation_bucket": bucket,
                "group_key": entry_group_key,
                "group_label": str(entry.get("group_label") or entry_group_key),
                "start_date": trade_date,
                "end_date": trade_date,
                "entry_count": 0,
                "total_amount": 0.0,
                "_amount_complete": True,
            },
        )
        if trade_date < accumulator["start_date"]:
            accumulator["start_date"] = trade_date
        if trade_date > accumulator["end_date"]:
            accumulator["end_date"] = trade_date
        accumulator["entry_count"] = int(accumulator.get("entry_count") or 0) + 1
        amount = _safe_float(entry.get("base_amount"))
        if amount is None:
            accumulator["_amount_complete"] = False
        else:
            accumulator["total_amount"] = (_safe_float(accumulator.get("total_amount")) or 0.0) + amount

    rendered_buckets: list[dict[str, object]] = []
    total_amount = 0.0
    total_amount_complete = True
    for accumulator in bucket_accumulators.values():
        amount = (
            _safe_float(accumulator.get("total_amount"))
            if bool(accumulator.get("_amount_complete"))
            else None
        )
        if amount is None:
            total_amount_complete = False
        else:
            total_amount += amount
        rendered_buckets.append(
            {
                "bucket_key": accumulator["bucket_key"],
                "frequency": accumulator["frequency"],
                "axis": accumulator["axis"],
                "taxonomy_id": accumulator["taxonomy_id"],
                "calculation_bucket": accumulator["calculation_bucket"],
                "group_key": accumulator["group_key"],
                "group_label": accumulator["group_label"],
                "start_date": accumulator["start_date"],
                "end_date": accumulator["end_date"],
                "entry_count": accumulator["entry_count"],
                "total_amount": amount,
            }
        )

    rendered_buckets.sort(
        key=lambda item: (
            str(item.get("bucket_key") or ""),
            -abs(_safe_float(item.get("total_amount")) or 0.0),
            str(item.get("group_key") or ""),
        )
    )
    return {
        "portfolio_id": entries_report["portfolio_id"],
        "base_currency": entries_report["base_currency"],
        "valuation_timezone": entries_report["valuation_timezone"],
        "valuation_cutoff_policy": entries_report["valuation_cutoff_policy"],
        "summary": {
            "axis": axis,
            "taxonomy_id": summary.get("taxonomy_id"),
            "group_key": summary.get("group_key"),
            "group_label": summary.get("group_label"),
            "bucket": bucket,
            "frequency": frequency,
            "start_date": summary.get("start_date"),
            "end_date": summary.get("end_date"),
            "bucket_count": len({str(item.get('bucket_key') or '') for item in rendered_buckets}),
            "group_count": len({str(item.get('group_key') or '') for item in rendered_buckets}),
            "entry_count": int(summary.get("entry_count") or 0),
            "total_amount": total_amount if total_amount_complete else None,
        },
        "buckets": rendered_buckets,
    }
