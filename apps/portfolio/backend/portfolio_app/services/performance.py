from __future__ import annotations

from collections import OrderedDict, defaultdict
from copy import deepcopy
from datetime import date, timedelta
from functools import partial
from math import isfinite, sqrt
from threading import RLock
from time import monotonic
from typing import cast

from portfolio_ops_instrument_core import VALUATION_PROHIBITED_TOTAL_RETURN_BASES

from portfolio_app.services import (
    attribution,
    holdings_market_profile,
    period_metrics,
    return_chain,
    valuation_fx,
)
from portfolio_app.services.annualization import annualization_eligibility
from portfolio_app.services.calculation_frequency import CalculationFrequency, period_end_date
from portfolio_app.core.settings import get_settings
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.instrument_registry import (
    InstrumentRegistryError,
    get_platform_fx_rates,
    get_registry_instrument_detail,
    get_registry_instrument_details,
    list_registry_corporate_actions,
)
from portfolio_app.services.instrument_event_tasks import (
    instrument_event_task_quality_warnings,
)
from portfolio_app.services.ledger import (
    _split_lot_quantity_allocations,
    build_position_lots,
    derive_ledger_postings,
    ledger_posting_effective_date_iso,
    ledger_posting_pending_amount_as_of,
)
from portfolio_app.services.option_obligations import (
    build_option_obligations,
    derive_option_obligation_events,
)
from portfolio_app.services.option_actions import resolve_option_action
from portfolio_app.services.market_data import (
    previous_quote_point,
    quote_policy_bases,
    resolve_quote_point,
)
from portfolio_app.services.risk_basis import calculation_frequency_profile_for_instruments
from portfolio_app.services.transaction_dates import (
    transaction_external_flow_date,
    transaction_ledger_activity_date,
    transaction_performance_effective_date,
    transaction_position_cash_transfer_date,
    transaction_precedes_entitlement_bod,
    transaction_sort_key,
)


DEFAULT_VALUATION_CUTOFF_POLICY = "latest_complete_eod"
EXTERNAL_CASH_IN_TYPES = {"deposit"}
EXTERNAL_CASH_OUT_TYPES = {"withdrawal"}
EARNINGS_TRANSACTION_TYPES = {
    "dividend",
    "coupon",
    "interest",
    "dividend_reinvestment",
}
REALIZED_GAIN_TRANSACTION_TYPES = {"sell", "maturity_redemption"}
NON_CAPITALIZED_ATTACHED_CHARGE_TRANSACTION_TYPES = {
    "dividend",
    "coupon",
    "dividend_reinvestment",
    "interest",
    "return_of_capital",
    "option_write",
}


def _has_derivative_lifecycle_activity(
    transaction: dict[str, object],
) -> bool:
    if resolve_option_action(transaction) is not None:
        return True
    lifecycle_event_type = str(
        transaction.get("lifecycle_event_type") or ""
    ).strip()
    if lifecycle_event_type.startswith(("fcn_", "option_")):
        return True
    instrument_ref = (
        transaction.get("instrument_ref")
        if isinstance(transaction.get("instrument_ref"), dict)
        else None
    )
    return bool(
        holdings_market_profile.is_event_valued_instrument_ref(instrument_ref)
        and str(transaction.get("transaction_type") or "")
        in {"buy", "sell", "opening_balance", "maturity_redemption"}
    )


# The Performance page requests the portfolio calculation and its grouped
# breakdown at the same time. Both need the same large instrument-history
# payload, so briefly coalesce those sibling reads instead of deserializing the
# full payload twice. The short TTL intentionally keeps this separate from a
# durable market-data cache.
CALCULATION_INSTRUMENT_DETAIL_CACHE_TTL_SECONDS = 2.0
CALCULATION_INSTRUMENT_DETAIL_CACHE_MAX_ENTRIES = 2
_calculation_instrument_detail_cache: OrderedDict[
    tuple[int, int, tuple[str, ...]],
    tuple[float, dict[str, dict[str, object] | None]],
] = OrderedDict()
_calculation_instrument_detail_cache_lock = RLock()


def _clear_calculation_instrument_detail_cache() -> None:
    with _calculation_instrument_detail_cache_lock:
        _calculation_instrument_detail_cache.clear()


def _get_calculation_instrument_details(
    instrument_ids: list[str] | set[str] | tuple[str, ...],
) -> dict[str, dict[str, object] | None]:
    normalized_ids = tuple(
        sorted(
            {
                str(instrument_id).strip()
                for instrument_id in instrument_ids
                if str(instrument_id).strip()
            }
        )
    )
    if not normalized_ids:
        return {}

    cache_key = (
        id(get_registry_instrument_details),
        id(get_session_factory()),
        normalized_ids,
    )
    now = monotonic()
    with _calculation_instrument_detail_cache_lock:
        expired_keys = [
            key
            for key, (expires_at, _) in _calculation_instrument_detail_cache.items()
            if expires_at <= now
        ]
        for key in expired_keys:
            _calculation_instrument_detail_cache.pop(key, None)

        cached = _calculation_instrument_detail_cache.get(cache_key)
        if cached is not None:
            _calculation_instrument_detail_cache.move_to_end(cache_key)
            return dict(cached[1])

        loaded = get_registry_instrument_details(normalized_ids)
        cached_details = dict(loaded)
        _calculation_instrument_detail_cache[cache_key] = (
            monotonic() + CALCULATION_INSTRUMENT_DETAIL_CACHE_TTL_SECONDS,
            cached_details,
        )
        _calculation_instrument_detail_cache.move_to_end(cache_key)
        while (
            len(_calculation_instrument_detail_cache)
            > CALCULATION_INSTRUMENT_DETAIL_CACHE_MAX_ENTRIES
        ):
            _calculation_instrument_detail_cache.popitem(last=False)
        return dict(cached_details)


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def corporate_action_quality_warnings(
    instrument_types: set[str],
    instrument_ids: set[str] | None = None,
    *,
    transactions: list[dict[str, object]],
    as_of_date: date | None = None,
) -> list[str]:
    normalized_types = {str(value or "").strip().lower() for value in instrument_types}
    if (
        not normalized_types.intersection({"equity", "etf"})
        or not instrument_ids
        or not transactions
    ):
        return []

    corporate_actions = list_registry_corporate_actions(
        instrument_ids,
        effective_on_or_before=as_of_date,
    )
    detected_actions = [
        event
        for event in corporate_actions
        if str(event.get("status") or "").strip().lower() == "detected"
    ]
    if not detected_actions:
        return []

    portfolio_id = next(
        (
            str(transaction.get("portfolio_id") or "").strip()
            for transaction in transactions
            if str(transaction.get("portfolio_id") or "").strip()
        ),
        "",
    )
    held_actions: list[dict[str, object]] = []
    for event in detected_actions:
        entitlement_date = _parse_iso_date(event.get("record_date")) or _parse_iso_date(
            event.get("effective_date")
        )
        if entitlement_date is None:
            continue
        entitlement_transactions = [
            transaction
            for transaction in transactions
            if transaction_precedes_entitlement_bod(
                transaction,
                entitlement_date,
            )
        ]
        if not entitlement_transactions:
            continue
        position_lots = build_position_lots(
            portfolio_id,
            [],
            entitlement_transactions,
            instrument_id=str(event.get("instrument_id") or ""),
            status="open",
            as_of_date=entitlement_date,
            corporate_actions=corporate_actions,
            resolve_pricing=False,
        )
        if any(
            (_safe_float(position_lot.get("remaining_quantity")) or 0.0) > 1e-9
            for position_lot in position_lots
        ):
            held_actions.append(event)

    if not held_actions:
        return []

    action_labels = sorted(
        {
            f"{str(event.get('instrument_id') or 'unknown instrument')} effective "
            f"{str(event.get('effective_date') or 'unknown date')}"
            for event in held_actions
        }
    )
    visible_labels = ", ".join(action_labels[:5])
    omitted_count = max(0, len(action_labels) - 5)
    omitted_label = f", plus {omitted_count} more" if omitted_count else ""
    count = len(held_actions)
    return [
        f"Corporate action review required: {count} provider-detected share-adjustment "
        f"event(s) remain unconfirmed ({visible_labels}{omitted_label}). Confirm the issuer, "
        "exchange, or depository ratio and fractional treatment before ledger posting; "
        "affected valuation and cost accounting remain withheld."
    ]


def _transaction_instrument_types(
    transactions: list[dict[str, object]] | None,
) -> set[str]:
    return {
        str(instrument_ref.get("instrument_type") or "").strip().lower()
        for transaction in transactions or []
        if isinstance((instrument_ref := transaction.get("instrument_ref")), dict)
        and str(instrument_ref.get("instrument_type") or "").strip()
    }


def _corporate_actions_for_transactions(
    transactions: list[dict[str, object]],
    *,
    effective_on_or_before: date | None = None,
) -> list[dict[str, object]]:
    instrument_ids = {
        str(transaction.get("instrument_id") or "").strip()
        for transaction in transactions
        if str(transaction.get("instrument_id") or "").strip()
    }
    if not instrument_ids:
        return []
    return list_registry_corporate_actions(
        instrument_ids,
        effective_on_or_before=effective_on_or_before,
    )


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


def _transaction_is_recognized_as_of(
    transaction: dict[str, object],
    *,
    as_of_date: date,
) -> bool:
    effective_date = transaction_performance_effective_date(transaction)
    return effective_date is not None and effective_date <= as_of_date


def _transaction_has_ledger_activity_as_of(
    transaction: dict[str, object],
    *,
    as_of_date: date,
) -> bool:
    activity_date = transaction_ledger_activity_date(transaction)
    return activity_date is not None and activity_date <= as_of_date


def _iter_dates(start_date: date, end_date: date) -> list[date]:
    if end_date < start_date:
        return []
    span = (end_date - start_date).days
    return [start_date + timedelta(days=offset) for offset in range(span + 1)]


def _account_cost_methods(accounts: list[dict[str, object]]) -> dict[str, str]:
    return {
        str(account.get("account_id") or ""): str(account.get("cost_basis_method") or "fifo")
        for account in accounts
        if str(account.get("account_type") or "") == "securities_account"
    }


def _account_currency_map(accounts: list[dict[str, object]]) -> dict[str, str]:
    return {
        str(account.get("account_id") or ""): valuation_fx.required_currency(
            account.get("currency"), field_name="account currency"
        )
        for account in accounts
    }


def _resolve_portfolio_valuation_timezone(portfolio: dict[str, object]) -> str:
    settings = get_settings()
    return str(portfolio.get("valuation_timezone") or settings.default_trade_timezone)


def _resolve_portfolio_valuation_cutoff_policy(portfolio: dict[str, object]) -> str:
    return str(portfolio.get("valuation_cutoff_policy") or DEFAULT_VALUATION_CUTOFF_POLICY)


def _select_market_point_as_of(
    *,
    detail: dict[str, object],
    role: str,
    as_of_date: date,
) -> dict[str, object] | None:
    candidate_bases = quote_policy_bases(detail, role)
    if role == "valuation" and any(
        quote_basis.strip().lower() in VALUATION_PROHIBITED_TOTAL_RETURN_BASES
        for quote_basis in candidate_bases
    ):
        return None
    return resolve_quote_point(
        detail,
        candidate_bases=candidate_bases,
        as_of_date=as_of_date,
    ).point


def _previous_market_point_for_selected_point(
    *,
    detail: dict[str, object],
    selected_point: dict[str, object] | None,
) -> dict[str, object] | None:
    return previous_quote_point(detail, selected_point=selected_point).point


def _resolve_snapshot_window(
    portfolio: dict[str, object],
    transactions: list[dict[str, object]],
    *,
    start_date: date | None,
    end_date: date | None,
) -> tuple[date, date] | None:
    transaction_dates = [
        activity_date
        for item in transactions
        if (
            activity_date := transaction_ledger_activity_date(item)
        )
        is not None
    ]
    portfolio_as_of = _parse_iso_date(portfolio.get("as_of_date"))
    if not transaction_dates and portfolio_as_of is None:
        return None

    inception_date = min(transaction_dates) if transaction_dates else None
    resolved_start = start_date or inception_date or portfolio_as_of
    if (
        start_date is not None
        and inception_date is not None
        and resolved_start is not None
        and resolved_start < inception_date
    ):
        resolved_start = inception_date
    resolved_end = end_date or portfolio_as_of or (max(transaction_dates) if transaction_dates else None)
    if portfolio_as_of is not None and resolved_end is not None and resolved_end > portfolio_as_of:
        resolved_end = portfolio_as_of
    if resolved_start is None or resolved_end is None:
        return None
    if resolved_end < resolved_start:
        return None
    return resolved_start, resolved_end


def _build_single_date_snapshot(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    as_of_date: date,
    allow_materialized: bool = True,
) -> dict[str, object]:
    portfolio_id = str(portfolio.get("portfolio_id") or "")
    if portfolio_id and allow_materialized:
        from portfolio_app.services.daily_snapshots import get_materialized_daily_snapshot

        materialized_snapshot = get_materialized_daily_snapshot(portfolio_id, as_of_date)
        if materialized_snapshot is not None:
            return materialized_snapshot

    portfolio_view = deepcopy(portfolio)
    portfolio_view["as_of_date"] = as_of_date.isoformat()
    historical_start_date = min(
        (
            activity_date
            for item in transactions
            if (
                activity_date := transaction_ledger_activity_date(item)
            )
            is not None
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
        "valuation_coverage_state": "unavailable",
        "return_coverage_state": "unavailable",
        "book_pnl_coverage_state": "unavailable",
        "attribution_coverage_state": "unavailable",
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
    for transaction in sorted(transactions, key=transaction_sort_key):
        effective_date = transaction_performance_effective_date(transaction)
        effective_date_iso = effective_date.isoformat() if effective_date is not None else ""
        if effective_date_iso < start_iso:
            boundary_transactions.append(transaction)
            continue
        if effective_date_iso == start_iso and str(transaction.get("transaction_type") or "") == "opening_balance":
            boundary_transactions.append(transaction)
    return boundary_transactions


def _transactions_in_period(
    transactions: list[dict[str, object]],
    *,
    start_date: date,
    end_date: date,
    include_start_date: bool = True,
) -> list[dict[str, object]]:
    start_iso = start_date.isoformat()
    end_iso = end_date.isoformat()
    return [
        transaction
        for transaction in sorted(transactions, key=transaction_sort_key)
        if (
            (effective_date := transaction_performance_effective_date(transaction)) is not None
            and (
                start_iso <= effective_date.isoformat() <= end_iso
                if include_start_date
                else start_iso < effective_date.isoformat() <= end_iso
            )
        )
    ]


def _transactions_as_of_end_date(
    transactions: list[dict[str, object]],
    *,
    end_date: date,
) -> list[dict[str, object]]:
    end_iso = end_date.isoformat()
    return [
        transaction
        for transaction in sorted(transactions, key=transaction_sort_key)
        if (
            (effective_date := transaction_performance_effective_date(transaction)) is not None
            and effective_date.isoformat() <= end_iso
        )
    ]


def _transactions_with_ledger_activity_as_of_end_date(
    transactions: list[dict[str, object]],
    *,
    end_date: date,
) -> list[dict[str, object]]:
    end_iso = end_date.isoformat()
    return [
        transaction
        for transaction in sorted(transactions, key=transaction_sort_key)
        if (
            (activity_date := transaction_ledger_activity_date(transaction))
            is not None
            and activity_date.isoformat() <= end_iso
        )
    ]


def _starts_on_imported_valuation_anchor(
    transactions: list[dict[str, object]],
    *,
    resolved_start_date: date,
) -> bool:
    """Return whether ``resolved_start_date`` is an imported EOD boundary.

    A same-day external contribution creates investable BOD capital and is
    therefore part of the first return subperiod.  ``opening_balance`` is
    different: it imports an already-existing fair-value state.  When that
    transaction is the first portfolio fact, its EOD value is the initial
    boundary and must not be reported as day-one performance.
    """

    effective_transactions: list[tuple[date, dict[str, object]]] = []
    for transaction in transactions:
        effective_date = transaction_performance_effective_date(transaction)
        if effective_date is not None:
            effective_transactions.append((effective_date, transaction))
    if not effective_transactions:
        return False
    inception_date = min(item[0] for item in effective_transactions)
    if inception_date != resolved_start_date:
        return False
    return any(
        effective_date == resolved_start_date
        and str(transaction.get("transaction_type") or "")
        == "opening_balance"
        for effective_date, transaction in effective_transactions
    )


def _initial_boundary_date(
    resolved_start_date: date,
    requested_start_date: date | None,
    *,
    transactions: list[dict[str, object]],
) -> date:
    # ``start_date`` is an EOD valuation anchor.  Funded inception is handled
    # by the first subperiod's BOD cash-flow denominator, not by fabricating a
    # prior calendar snapshot.
    del requested_start_date, transactions
    return resolved_start_date


def _requested_start_is_close_boundary(
    transactions: list[dict[str, object]],
    *,
    requested_start_date: date | None,
    resolved_start_date: date,
) -> bool:
    """Whether the requested start has an existing portfolio EOD boundary.

    A funded inception date has no preceding portfolio close.  Even when it is
    supplied explicitly, that date denotes the portfolio's first BOD-to-EOD
    subperiod.  Imported opening balances remain EOD anchors.
    """

    if requested_start_date is None:
        return False
    inception_date = min(
        (
            effective_date
            for transaction in transactions
            if (
                effective_date := transaction_performance_effective_date(
                    transaction
                )
            )
            is not None
        ),
        default=None,
    )
    return inception_date is None or requested_start_date > inception_date


def _snapshot_starts_funded_segment(
    snapshot: dict[str, object] | None,
    *,
    resolved_start_date: date,
) -> bool:
    """Return whether a daily row restarts performance from zero portfolio NAV.

    Cash that remains inside the portfolio is still part of portfolio NAV even
    when it earns no return.  A funded segment therefore starts only when the
    prior EOD portfolio NAV is genuinely zero and a positive BOD contribution
    supplies the day's return denominator.  This is deliberately narrower than
    "a deposit happened today": an ordinary contribution to a live portfolio
    remains a normal close-to-close subperiod.
    """

    if not isinstance(snapshot, dict):
        return False
    snapshot_date = _parse_iso_date(snapshot.get("as_of_date"))
    beginning_nav = _safe_float(snapshot.get("beginning_nav"))
    external_cash_in = _safe_float(snapshot.get("external_cash_in"))
    daily_twr = _safe_float(snapshot.get("daily_twr"))
    return bool(
        snapshot_date == resolved_start_date
        and beginning_nav is not None
        and abs(beginning_nav) <= 1e-9
        and external_cash_in is not None
        and external_cash_in > 1e-9
        and daily_twr is not None
        and isfinite(daily_twr)
        and return_chain.snapshot_coverage_state(snapshot, "return")
        == "complete"
    )


def _period_start_is_close_boundary(
    transactions: list[dict[str, object]],
    *,
    requested_start_date: date | None,
    resolved_start_date: date,
    start_snapshot: dict[str, object] | None = None,
) -> bool:
    """Whether the period begins at an existing EOD portfolio state.

    Every explicit start date is an EOD query anchor once the portfolio exists.
    An imported opening balance is also an EOD state even for a since-inception
    query.  A true funded inception, or a later re-funding after portfolio NAV
    reached zero, remains a BOD-to-EOD first subperiod for that funded segment.
    A live portfolio that merely holds non-earning cash does not qualify.
    """

    if _starts_on_imported_valuation_anchor(
        transactions,
        resolved_start_date=resolved_start_date,
    ):
        return True
    if _snapshot_starts_funded_segment(
        start_snapshot,
        resolved_start_date=resolved_start_date,
    ):
        return False
    return _requested_start_is_close_boundary(
        transactions,
        requested_start_date=requested_start_date,
        resolved_start_date=resolved_start_date,
    )


def _raw_period_start_snapshot(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    resolved_start_date: date,
) -> dict[str, object]:
    """Load the unprojected daily row used to classify a period boundary."""

    return _build_single_date_snapshot(
        portfolio,
        accounts,
        _transactions_as_of_end_date(
            transactions,
            end_date=resolved_start_date,
        ),
        as_of_date=resolved_start_date,
    )


def _period_initial_value_from_daily_slice(
    daily_slice: dict[str, object],
) -> float | None:
    """Return the value immediately before the requested performance interval.

    A normal daily slice starts at BOD.  A close-to-close query anchor and an
    imported opening balance are EOD boundaries, so their ending value is the
    interval's initial value.
    """

    field_name = (
        "ending_value_base"
        if bool(daily_slice.get("_is_initial_valuation_anchor"))
        else "beginning_value_base"
    )
    return _safe_float(daily_slice.get(field_name))


def _period_risk_start_boundary_date(
    resolved_start_date: date | None,
    daily_slices: list[dict[str, object]],
) -> date | None:
    """Return the EOD boundary preceding the first included risk subperiod."""

    if resolved_start_date is None:
        return None
    starts_on_imported_anchor = any(
        item.get("as_of_date") == resolved_start_date
        and bool(item.get("_is_initial_valuation_anchor"))
        for item in daily_slices
    )
    return (
        resolved_start_date
        if starts_on_imported_anchor
        else resolved_start_date - timedelta(days=1)
    )


def _sum_period_transaction_buckets(
    transactions: list[dict[str, object]],
    *,
    base_currency: str,
    direct_fx_instruments: dict[tuple[str, str], str],
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
        converted_amount, is_stale = valuation_fx.convert_amount_on(
            amount,
            as_of_date=trade_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
            instrument_detail_loader=get_registry_instrument_detail,
        )
        if converted_amount is None:
            coverage_complete = False
            return None
        stale_fx_flag = stale_fx_flag or is_stale
        return converted_amount

    for transaction in transactions:
        effective_date = transaction_performance_effective_date(transaction)
        if effective_date is None:
            coverage_complete = False
            continue
        currency = valuation_fx.required_currency(
            transaction.get("currency"), field_name="transaction currency"
        )
        transaction_type = str(transaction.get("transaction_type") or "")
        gross_amount = _safe_float(transaction.get("gross_amount")) or 0.0
        fee_amount = _safe_float(transaction.get("fees")) or 0.0
        tax_amount = _safe_float(transaction.get("taxes")) or 0.0

        if transaction_type in EXTERNAL_CASH_IN_TYPES:
            converted = convert_component(gross_amount, trade_date=effective_date, currency=currency)
            if converted is not None:
                deposits += converted
        elif transaction_type in EXTERNAL_CASH_OUT_TYPES:
            converted = convert_component(gross_amount, trade_date=effective_date, currency=currency)
            if converted is not None:
                withdrawals += converted

        if transaction_type in EARNINGS_TRANSACTION_TYPES:
            converted = convert_component(gross_amount, trade_date=effective_date, currency=currency)
            if converted is not None:
                earnings += converted

        if transaction_type == "return_of_capital":
            converted = convert_component(gross_amount, trade_date=effective_date, currency=currency)
            if converted is not None:
                return_of_capital_amount += converted

        if transaction_type == "fee":
            converted = convert_component(gross_amount, trade_date=effective_date, currency=currency)
            if converted is not None:
                fees += converted
        if transaction_type == "tax":
            converted = convert_component(gross_amount, trade_date=effective_date, currency=currency)
            if converted is not None:
                taxes += converted

        # Attached charges are always disclosed as fees/taxes.  Whether they
        # are also an expense-cash component is a separate question: trade
        # charges are already capitalized into cost/proceeds by the ledger.
        if fee_amount > 0:
            converted = convert_component(fee_amount, trade_date=effective_date, currency=currency)
            if converted is not None:
                fees += converted
        if tax_amount > 0:
            converted = convert_component(tax_amount, trade_date=effective_date, currency=currency)
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
    transactions: list[dict[str, object]] | None = None,
    start_date: date | None,
    end_date: date | None,
    base_currency: str,
    direct_fx_instruments: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
) -> dict[str, object]:
    realized_capital_gains = 0.0
    derivative_lifecycle_realized_pnl = 0.0
    coverage_complete = True
    stale_fx_flag = False
    start_iso = start_date.isoformat() if start_date is not None else None
    end_iso = end_date.isoformat() if end_date is not None else None

    for position_lot in position_lots:
        currency = valuation_fx.required_currency(
            position_lot.get("currency"), field_name="position-lot currency"
        )
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
            converted_amount, is_stale = valuation_fx.convert_amount_on(
                realized_pnl,
                as_of_date=trade_date,
                from_currency=currency,
                to_currency=base_currency,
                direct_fx_instruments=direct_fx_instruments,
                instrument_detail_cache=instrument_detail_cache,
                instrument_detail_loader=get_registry_instrument_detail,
            )
            if converted_amount is None:
                coverage_complete = False
                continue
            realized_capital_gains += converted_amount
            instrument_ref = (
                position_lot.get("instrument_ref")
                if isinstance(position_lot.get("instrument_ref"), dict)
                else {}
            )
            if str(instrument_ref.get("instrument_type") or "").lower() in {
                "fcn",
                "option",
            }:
                derivative_lifecycle_realized_pnl += converted_amount
            stale_fx_flag = stale_fx_flag or is_stale

    # Written-option close/expiry/assignment results live in the obligation
    # subledger rather than in long position lots.  Include them in the same
    # realized-P&L bucket so Calculation, snapshots and attribution reconcile.
    if transactions:
        for event in derive_option_obligation_events(transactions, as_of_date=end_date):
            realized_pnl = _safe_float(event.get("realized_pnl_delta"))
            if realized_pnl is None or abs(realized_pnl) <= 1e-15:
                continue
            event_transaction = next(
                (
                    item
                    for item in transactions
                    if str(item.get("transaction_id") or "")
                    == str(event.get("transaction_id") or "")
                ),
                None,
            )
            event_date = transaction_performance_effective_date(event_transaction or {})
            if event_date is None:
                raw_event_date = event.get("event_date")
                event_date = (
                    raw_event_date
                    if isinstance(raw_event_date, date)
                    else _parse_iso_date(raw_event_date)
                )
            if event_date is None:
                continue
            if start_date is not None and event_date < start_date:
                continue
            currency = valuation_fx.required_currency(
                (event_transaction or {}).get("currency") or event.get("currency"),
                field_name="option obligation currency",
            )
            converted_amount, is_stale = valuation_fx.convert_amount_on(
                realized_pnl,
                as_of_date=event_date,
                from_currency=currency,
                to_currency=base_currency,
                direct_fx_instruments=direct_fx_instruments,
                instrument_detail_cache=instrument_detail_cache,
                instrument_detail_loader=get_registry_instrument_detail,
            )
            if converted_amount is None:
                coverage_complete = False
                continue
            realized_capital_gains += converted_amount
            derivative_lifecycle_realized_pnl += converted_amount
            stale_fx_flag = stale_fx_flag or is_stale

    return {
        "realized_capital_gains": realized_capital_gains,
        "derivative_lifecycle_realized_pnl": derivative_lifecycle_realized_pnl,
        "coverage_complete": coverage_complete,
        "stale_fx_flag": stale_fx_flag,
    }


def _capital_gains_from_components(line: dict[str, object]) -> float | None:
    total_pnl = _safe_float(line.get("total_pnl"))
    earnings = _safe_float(line.get("income_cash_amount"))
    expense_cash_amount = _safe_float(line.get("expense_cash_amount"))
    cash_currency_gains = _safe_float(line.get("cash_currency_gains")) or 0.0
    pending_settlement_currency_gains = (
        _safe_float(line.get("pending_settlement_currency_gains")) or 0.0
    )
    instrument_currency_gains = _safe_float(line.get("instrument_currency_gains")) or 0.0
    if total_pnl is None or earnings is None or expense_cash_amount is None:
        return None
    return (
        total_pnl
        - earnings
        + expense_cash_amount
        - cash_currency_gains
        - pending_settlement_currency_gains
        - instrument_currency_gains
    )


def _consume_period_lots(
    lots_by_key: dict[tuple[str, str], list[dict[str, object]]],
    *,
    account_id: str,
    instrument_id: str,
    quantity: float,
) -> list[dict[str, object]]:
    remaining_quantity = quantity
    consumed_slices: list[dict[str, object]] = []
    for lot in lots_by_key.get((account_id, instrument_id), []):
        if remaining_quantity <= 1e-9:
            break
        lot_quantity = _safe_float(lot.get("quantity")) or 0.0
        if lot_quantity <= 1e-9:
            continue
        take_quantity = min(lot_quantity, remaining_quantity)
        lot_cost_local = _safe_float(lot.get("cost_local")) or 0.0
        released_cost_local = lot_cost_local * (take_quantity / lot_quantity)
        lot["quantity"] = lot_quantity - take_quantity
        lot["cost_local"] = lot_cost_local - released_cost_local
        remaining_quantity -= take_quantity
        consumed_slice = {
            **lot,
            "quantity": take_quantity,
            "cost_local": released_cost_local,
        }
        consumed_slices.append(consumed_slice)
    return consumed_slices


def _append_period_lot(
    lots_by_key: dict[tuple[str, str], list[dict[str, object]]],
    *,
    account_id: str,
    instrument_id: str,
    instrument_ref: dict[str, object],
    currency: str,
    quantity: float,
    cost_local: float,
) -> None:
    if quantity <= 1e-9:
        return
    lots_by_key[(account_id, instrument_id)].append(
        {
            "account_id": account_id,
            "instrument_id": instrument_id,
            "instrument_ref": deepcopy(instrument_ref),
            "currency": currency,
            "quantity": quantity,
            "cost_local": max(cost_local, 0.0),
        }
    )


def _reduce_period_lot_cost(
    lots_by_key: dict[tuple[str, str], list[dict[str, object]]],
    *,
    account_id: str,
    instrument_id: str,
    amount_local: float,
) -> None:
    if amount_local <= 1e-9:
        return
    active_lots = [
        lot
        for lot in lots_by_key.get((account_id, instrument_id), [])
        if (_safe_float(lot.get("quantity")) or 0.0) > 1e-9
    ]
    total_quantity = sum((_safe_float(lot.get("quantity")) or 0.0) for lot in active_lots)
    if total_quantity <= 1e-9:
        return
    for lot in active_lots:
        lot_quantity = _safe_float(lot.get("quantity")) or 0.0
        reduction = amount_local * (lot_quantity / total_quantity)
        lot["cost_local"] = max((_safe_float(lot.get("cost_local")) or 0.0) - reduction, 0.0)


def _period_lot_market_value_local(
    lot: dict[str, object],
    *,
    as_of_date: date,
    instrument_detail_cache: dict[str, dict[str, object] | None],
) -> float | None:
    instrument_id = str(lot.get("instrument_id") or "")
    instrument_ref = (
        lot.get("instrument_ref")
        if isinstance(lot.get("instrument_ref"), dict)
        else None
    )
    _carrying_price, carrying_value, event_valued = (
        holdings_market_profile.resolve_position_valuation(
            quantity=_safe_float(lot.get("quantity")) or 0.0,
            cost_basis=_safe_float(lot.get("cost_local")),
            instrument_ref=instrument_ref,
            quoted_price=None,
        )
    )
    if event_valued:
        return carrying_value
    detail = valuation_fx.instrument_detail_cache_get(instrument_id, instrument_detail_cache, instrument_detail_loader=get_registry_instrument_detail)
    if not isinstance(detail, dict):
        return None
    price_point = _select_market_point_as_of(
        detail=detail,
        role="valuation",
        as_of_date=as_of_date,
    )
    if price_point is None:
        return None
    _last_price, market_value, _ = holdings_market_profile.resolve_position_valuation(
        quantity=_safe_float(lot.get("quantity")) or 0.0,
        cost_basis=_safe_float(lot.get("cost_local")),
        instrument_ref=instrument_ref,
        quoted_price=_safe_float(price_point.get("value")),
        quoted_price_scale=_safe_float(price_point.get("price_scale")),
    )
    return market_value


def _period_taxonomy_group_resolver(
    *,
    axis: str,
    taxonomy_id: str | None,
    taxonomies: list[dict[str, object]] | None,
    taxonomy_nodes: list[dict[str, object]] | None,
    taxonomy_assignments: list[dict[str, object]] | None,
    as_of_date: date,
):
    if axis != "taxonomy":
        return None
    taxonomy = next(
        (
            item
            for item in taxonomies or []
            if str(item.get("taxonomy_id") or "") == str(taxonomy_id or "")
        ),
        None,
    )
    if taxonomy is None:
        return None
    taxonomy_id_value = str(taxonomy.get("taxonomy_id") or "")
    target_scope = str(taxonomy.get("primary_assignment_scope") or "")
    taxonomy_nodes_by_id = {
        str(node.get("taxonomy_node_id") or ""): node
        for node in taxonomy_nodes or []
        if str(node.get("taxonomy_id") or "") == taxonomy_id_value
    }
    assignments_by_entity: dict[str, list[dict[str, object]]] = defaultdict(list)
    for assignment in taxonomy_assignments or []:
        if str(assignment.get("taxonomy_id") or "") != taxonomy_id_value:
            continue
        if str(assignment.get("target_scope") or "") != target_scope:
            continue
        assignments_by_entity[str(assignment.get("target_entity_id") or "")].append(assignment)

    def resolve(lot: dict[str, object]) -> str:
        if target_scope == "account":
            target_entity_id = str(lot.get("account_id") or "")
        else:
            target_entity_id = str(lot.get("instrument_id") or "")
        group_key, _group_label = attribution.resolve_taxonomy_group_for_date(
            taxonomy=taxonomy,
            taxonomy_nodes_by_id=taxonomy_nodes_by_id,
            assignments_by_entity=assignments_by_entity,
            target_entity_id=target_entity_id,
            as_of_date=as_of_date,
        )
        return group_key

    return resolve


def _period_unrealized_capital_gains_by_group(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    start_date: date,
    end_date: date,
    axis: str,
    taxonomy_id: str | None,
    taxonomies: list[dict[str, object]] | None,
    taxonomy_nodes: list[dict[str, object]] | None,
    taxonomy_assignments: list[dict[str, object]] | None,
    base_currency: str,
    direct_fx_instruments: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
    start_is_close_boundary: bool = False,
) -> dict[str, object]:
    lots_by_key: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    sorted_transactions = sorted(transactions, key=transaction_sort_key)
    start_boundary_date = (
        start_date
        if start_is_close_boundary
        else start_date - timedelta(days=1)
    )
    portfolio_id = str(portfolio.get("portfolio_id") or "")
    transactions_before_start = _transactions_as_of_end_date(
        sorted_transactions,
        end_date=start_boundary_date,
    )
    opening_boundary_transactions = [
        transaction
        for transaction in sorted_transactions
        if not start_is_close_boundary
        if transaction_performance_effective_date(transaction) == start_date
        and str(transaction.get("transaction_type") or "")
        == "opening_balance"
    ]
    opening_boundary_transaction_ids = {
        str(transaction.get("transaction_id") or "")
        for transaction in opening_boundary_transactions
    }
    coverage_complete = True
    stale_fx_flag = False

    def seed_boundary_lots(
        boundary_transactions: list[dict[str, object]],
        *,
        boundary_date: date,
        corporate_actions: list[dict[str, object]] | None = None,
    ) -> None:
        nonlocal coverage_complete
        for position_lot in build_position_lots(
            portfolio_id,
            accounts,
            boundary_transactions,
            as_of_date=boundary_date,
            corporate_actions=corporate_actions,
            instrument_detail_cache=instrument_detail_cache,
        ):
            if str(position_lot.get("status") or "") != "open":
                continue
            quantity = (
                _safe_float(position_lot.get("remaining_quantity")) or 0.0
            )
            if quantity <= 1e-9:
                continue
            lot = {
                "account_id": str(position_lot.get("account_id") or ""),
                "instrument_id": str(position_lot.get("instrument_id") or ""),
                "instrument_ref": (
                    deepcopy(position_lot.get("instrument_ref"))
                    if isinstance(position_lot.get("instrument_ref"), dict)
                    else {}
                ),
                "currency": valuation_fx.required_currency(
                    position_lot.get("currency"),
                    field_name="position-lot currency",
                ),
                "quantity": quantity,
                "cost_local": 0.0,
            }
            market_value_local = _period_lot_market_value_local(
                lot,
                as_of_date=boundary_date,
                instrument_detail_cache=instrument_detail_cache,
            )
            if market_value_local is None:
                coverage_complete = False
                continue
            lot["cost_local"] = market_value_local
            lots_by_key[
                (str(lot["account_id"]), str(lot["instrument_id"]))
            ].append(lot)

    seed_boundary_lots(
        transactions_before_start,
        boundary_date=start_boundary_date,
    )
    # Imported positions that establish the inception boundary are valued at
    # fair value on that boundary.  Their imported remaining book cost is not
    # a performance-period cost and must not create day-one gains or losses.
    if opening_boundary_transactions:
        seed_boundary_lots(
            opening_boundary_transactions,
            boundary_date=start_date,
            corporate_actions=[],
        )

    transfer_slices_by_group: dict[str, list[dict[str, object]]] = {}
    disposed_period_lots: list[dict[str, object]] = []
    corporate_action_timeline = [
        event
        for event in _corporate_actions_for_transactions(
            sorted_transactions,
            effective_on_or_before=end_date,
        )
        if str(event.get("action_type") or "") == "share_split"
        and str(event.get("status") or "") == "confirmed"
        if (
            effective_date := _parse_iso_date(event.get("effective_date"))
        )
        is not None
        and (
            start_date < effective_date <= end_date
            if start_is_close_boundary
            else start_date <= effective_date <= end_date
        )
    ]
    timeline: list[tuple[date, int, tuple[object, ...], dict[str, object]]] = [
        (
            cast(date, _parse_iso_date(event.get("effective_date"))),
            0,
            (str(event.get("corporate_action_event_id") or ""),),
            event,
        )
        for event in corporate_action_timeline
    ]
    timeline.extend(
        (
            effective_date,
            1,
            transaction_sort_key(transaction),
            transaction,
        )
        for transaction in sorted_transactions
        if (
            effective_date := transaction_performance_effective_date(
                transaction
            )
        )
        is not None
        and (
            start_date < effective_date <= end_date
            if start_is_close_boundary
            else start_date <= effective_date <= end_date
        )
    )
    for timeline_date, timeline_kind, _sort_key, transaction in sorted(
        timeline,
        key=lambda item: (item[0], item[1], item[2]),
    ):
        if timeline_kind == 0:
            split_instrument_id = str(transaction.get("instrument_id") or "")
            for (account_key, instrument_key), lots in list(
                lots_by_key.items()
            ):
                del account_key
                if instrument_key != split_instrument_id:
                    continue
                active_lots = [
                    lot
                    for lot in lots
                    if (_safe_float(lot.get("quantity")) or 0.0) > 1e-9
                ]
                quantities = [
                    _safe_float(lot.get("quantity")) or 0.0
                    for lot in active_lots
                ]
                target_quantities = _split_lot_quantity_allocations(
                    quantities,
                    transaction,
                )
                for lot, target_quantity in zip(
                    active_lots,
                    target_quantities,
                    strict=True,
                ):
                    lot["quantity"] = target_quantity
            continue

        trade_date = timeline_date
        if (
            trade_date is None
            or trade_date > end_date
            or (
                trade_date <= start_date
                if start_is_close_boundary
                else trade_date < start_date
            )
        ):
            continue
        transaction_type = str(transaction.get("transaction_type") or "")
        if (
            transaction_type == "opening_balance"
            and str(transaction.get("transaction_id") or "")
            in opening_boundary_transaction_ids
        ):
            continue
        account_id = str(transaction.get("account_id") or "")
        instrument_ref = (
            deepcopy(transaction.get("instrument_ref"))
            if isinstance(transaction.get("instrument_ref"), dict)
            else {}
        )
        instrument_id = str(transaction.get("instrument_id") or instrument_ref.get("instrument_id") or "")
        quantity = _safe_float(transaction.get("quantity")) or 0.0
        if not account_id or not instrument_id or quantity <= 1e-9:
            continue
        currency = valuation_fx.required_currency(
            transaction.get("currency"), field_name="transaction currency"
        )
        gross_amount = _safe_float(transaction.get("gross_amount")) or 0.0

        if transaction_type in {"opening_balance", "buy", "dividend_reinvestment"}:
            _append_period_lot(
                lots_by_key,
                account_id=account_id,
                instrument_id=instrument_id,
                instrument_ref=instrument_ref,
                currency=currency,
                quantity=quantity,
                cost_local=gross_amount,
            )
            continue

        if transaction_type in {"sell", "maturity_redemption"}:
            disposed_period_lots.extend(
                _consume_period_lots(
                    lots_by_key,
                    account_id=account_id,
                    instrument_id=instrument_id,
                    quantity=quantity,
                )
            )
            continue

        if transaction_type == "return_of_capital":
            _reduce_period_lot_cost(
                lots_by_key,
                account_id=account_id,
                instrument_id=instrument_id,
                amount_local=gross_amount,
            )
            continue

        if transaction_type == "transfer_out" and transaction.get("transfer_object_type") == "position":
            consumed_slices = _consume_period_lots(
                lots_by_key,
                account_id=account_id,
                instrument_id=instrument_id,
                quantity=quantity,
            )
            transfer_group_id = str(transaction.get("transfer_group_id") or "")
            if transfer_group_id:
                transfer_slices_by_group[transfer_group_id] = consumed_slices
            continue

        if transaction_type == "transfer_in" and transaction.get("transfer_object_type") == "position":
            transfer_group_id = str(transaction.get("transfer_group_id") or "")
            incoming_slices = transfer_slices_by_group.get(transfer_group_id, [])
            if incoming_slices:
                for incoming_slice in incoming_slices:
                    _append_period_lot(
                        lots_by_key,
                        account_id=account_id,
                        instrument_id=instrument_id,
                        instrument_ref=(
                            incoming_slice.get("instrument_ref")
                            if isinstance(incoming_slice.get("instrument_ref"), dict)
                            else instrument_ref
                        ),
                        currency=valuation_fx.required_currency(
                            incoming_slice.get("currency"), field_name="attribution-slice currency"
                        ),
                        quantity=_safe_float(incoming_slice.get("quantity")) or 0.0,
                        cost_local=_safe_float(incoming_slice.get("cost_local")) or 0.0,
                    )
                continue
            _append_period_lot(
                lots_by_key,
                account_id=account_id,
                instrument_id=instrument_id,
                instrument_ref=instrument_ref,
                currency=currency,
                quantity=quantity,
                cost_local=gross_amount,
            )

    detail_parent_axis = attribution.calculation_detail_parent_axis(axis)
    taxonomy_resolver = _period_taxonomy_group_resolver(
        axis="taxonomy" if axis == "taxonomy" or detail_parent_axis == "taxonomy" else axis,
        taxonomy_id=taxonomy_id,
        taxonomies=taxonomies,
        taxonomy_nodes=taxonomy_nodes,
        taxonomy_assignments=taxonomy_assignments,
        as_of_date=end_date,
    )
    account_name_map = attribution.account_name_map(accounts)

    def resolve_group_key(lot: dict[str, object]) -> str:
        if detail_parent_axis in {
            "instrument",
            "account",
            "instrument_type",
            "currency",
        }:
            parent_group_key, _parent_group_label = (
                attribution.position_group_for_axis(
                    axis=detail_parent_axis,
                    position_lot=lot,
                    account_name_map=account_name_map,
                    base_currency=base_currency,
                )
            )
            item_key = str(lot.get("instrument_id") or "")
            return (
                attribution.encode_calculation_detail_group_key(
                    parent_group_key=parent_group_key,
                    item_kind="instrument",
                    item_key=item_key,
                )
                if parent_group_key and item_key
                else ""
            )
        if detail_parent_axis == "taxonomy" and taxonomy_resolver is not None:
            parent_group_key = taxonomy_resolver(lot)
            item_key = str(lot.get("instrument_id") or "")
            return (
                attribution.encode_calculation_detail_group_key(
                    parent_group_key=parent_group_key,
                    item_kind="instrument",
                    item_key=item_key,
                )
                if parent_group_key and item_key
                else ""
            )
        if axis == "account":
            return str(lot.get("account_id") or "")
        if axis == "instrument_type":
            instrument_ref = attribution.instrument_ref_from_mapping(lot)
            group_key, _group_label = attribution.instrument_type_key_label(
                instrument_ref.get("instrument_type")
            )
            return group_key
        if axis == "currency":
            return valuation_fx.required_currency(
                lot.get("currency"), field_name="position-lot currency"
            )
        if axis == "taxonomy" and taxonomy_resolver is not None:
            return taxonomy_resolver(lot)
        return str(lot.get("instrument_id") or "")

    values: dict[str, float] = defaultdict(float)
    open_group_keys: set[str] = set()
    disposed_group_keys: set[str] = set()
    foreign_group_keys: set[str] = set()
    for disposed_lot in disposed_period_lots:
        group_key = resolve_group_key(disposed_lot)
        if not group_key:
            continue
        disposed_group_keys.add(group_key)
        if (
            valuation_fx.required_currency(
                disposed_lot.get("currency"),
                field_name="position-lot currency",
            )
            != base_currency
        ):
            foreign_group_keys.add(group_key)

    for lots in lots_by_key.values():
        for lot in lots:
            quantity = _safe_float(lot.get("quantity")) or 0.0
            if quantity <= 1e-9:
                continue
            group_key = resolve_group_key(lot)
            if group_key:
                open_group_keys.add(group_key)
                if (
                    valuation_fx.required_currency(
                        lot.get("currency"),
                        field_name="position-lot currency",
                    )
                    != base_currency
                ):
                    foreign_group_keys.add(group_key)
            end_market_value_local = _period_lot_market_value_local(
                lot,
                as_of_date=end_date,
                instrument_detail_cache=instrument_detail_cache,
            )
            if end_market_value_local is None:
                coverage_complete = False
                continue
            unrealized_local = end_market_value_local - (_safe_float(lot.get("cost_local")) or 0.0)
            currency = valuation_fx.required_currency(
                lot.get("currency"), field_name="position-lot currency"
            )
            unrealized_base, _is_stale = valuation_fx.convert_amount_on(
                unrealized_local,
                as_of_date=end_date,
                from_currency=currency,
                to_currency=base_currency,
                direct_fx_instruments=direct_fx_instruments,
                instrument_detail_cache=instrument_detail_cache,
                instrument_detail_loader=get_registry_instrument_detail,
            )
            if unrealized_base is None:
                coverage_complete = False
                continue
            stale_fx_flag = stale_fx_flag or _is_stale
            if group_key:
                values[group_key] += unrealized_base

    # Written options live in the obligation subledger rather than PositionLot.
    # Teach the realized/unrealized splitter about their lifecycle so a close,
    # expiry, or assignment is not mislabeled as an unrealized residual.
    def obligation_as_period_lot(
        obligation: dict[str, object],
    ) -> dict[str, object]:
        return {
            "account_id": obligation.get("account_id"),
            "instrument_id": obligation.get("option_instrument_id")
            or obligation.get("instrument_id"),
            "instrument_ref": obligation.get("instrument_ref"),
            "currency": obligation.get("contract_currency") or base_currency,
        }

    for event in derive_option_obligation_events(
        sorted_transactions,
        as_of_date=end_date,
    ):
        if str(event.get("event_type") or "") == "open":
            continue
        event_date = event.get("event_date")
        if isinstance(event_date, str):
            event_date = _parse_iso_date(event_date)
        if not isinstance(event_date, date) or not (
            start_date < event_date <= end_date
            if start_is_close_boundary
            else start_date <= event_date <= end_date
        ):
            continue
        if (_safe_float(event.get("released_premium_basis")) or 0.0) <= 1e-9:
            continue
        obligation = event.get("obligation")
        if not isinstance(obligation, dict):
            coverage_complete = False
            continue
        obligation_lot = obligation_as_period_lot(obligation)
        group_key = resolve_group_key(obligation_lot)
        if not group_key:
            continue
        disposed_group_keys.add(group_key)
        if (
            valuation_fx.required_currency(
                obligation_lot.get("currency"),
                field_name="option obligation currency",
            )
            != base_currency
        ):
            foreign_group_keys.add(group_key)

    for obligation in build_option_obligations(
        sorted_transactions,
        as_of_date=end_date,
    ):
        if (
            str(obligation.get("status") or "") != "open"
            or (_safe_float(obligation.get("remaining_quantity")) or 0.0)
            <= 1e-9
        ):
            continue
        obligation_lot = obligation_as_period_lot(obligation)
        group_key = resolve_group_key(obligation_lot)
        if not group_key:
            continue
        open_group_keys.add(group_key)
        values[group_key] += 0.0
        if (
            valuation_fx.required_currency(
                obligation_lot.get("currency"),
                field_name="option obligation currency",
            )
            != base_currency
        ):
            foreign_group_keys.add(group_key)

    return {
        "values": dict(values),
        "coverage_complete": coverage_complete,
        "stale_fx_flag": stale_fx_flag,
        "open_group_keys": sorted(open_group_keys),
        "disposed_group_keys": sorted(disposed_group_keys),
        "foreign_group_keys": sorted(foreign_group_keys),
    }


def _resolved_period_unrealized_capital_gain(
    *,
    group_key: str,
    capital_gains: float | None,
    unrealized_capital_summary: dict[str, object],
) -> tuple[float | None, bool]:
    """Split period capital gain without mixing book and performance bases.

    The period capital-gain line is already the exact NAV bridge after income,
    expenses, and currency effects.  If no units were disposed, the entire
    period capital gain is unrealized; if the group is fully disposed, it is
    entirely realized.  A remaining base-currency lot can be valued against
    its reset period cost.  A partially disposed foreign-currency lot needs
    lot-level daily price/FX attribution, so that ambiguous case fails closed
    instead of publishing an endpoint-FX approximation.
    """

    if capital_gains is None or not bool(
        unrealized_capital_summary.get("coverage_complete")
    ):
        return (None, False)
    disposed_group_keys = {
        str(item)
        for item in list(
            unrealized_capital_summary.get("disposed_group_keys") or []
        )
    }
    open_group_keys = {
        str(item)
        for item in list(unrealized_capital_summary.get("open_group_keys") or [])
    }
    foreign_group_keys = {
        str(item)
        for item in list(
            unrealized_capital_summary.get("foreign_group_keys") or []
        )
    }
    if group_key not in disposed_group_keys:
        return (capital_gains, True)
    if group_key not in open_group_keys:
        return (0.0, True)
    if group_key in foreign_group_keys:
        return (None, False)

    values = (
        unrealized_capital_summary.get("values")
        if isinstance(unrealized_capital_summary.get("values"), dict)
        else {}
    )
    return (_safe_float(values.get(group_key)) or 0.0, True)


def _daily_external_flow_breakdown(
    transactions: list[dict[str, object]],
    as_of_date: date,
    *,
    base_currency: str,
    direct_fx_instruments: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
) -> dict[str, object]:
    same_day_transactions = [
        transaction
        for transaction in transactions
        if transaction_external_flow_date(transaction) == as_of_date
    ]
    buckets = _sum_period_transaction_buckets(
        same_day_transactions,
        base_currency=base_currency,
        direct_fx_instruments=direct_fx_instruments,
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
    sorted_transactions = sorted(transactions, key=transaction_sort_key)
    account_cost_methods = _account_cost_methods(accounts)
    account_currency_map = _account_currency_map(accounts)
    account_name_map = attribution.account_name_map(accounts)
    portfolio_id = str(portfolio.get("portfolio_id") or "")
    corporate_actions = _corporate_actions_for_transactions(
        sorted_transactions,
        effective_on_or_before=resolved_end_date,
    )
    base_currency = valuation_fx.required_currency(
        portfolio.get("base_currency"), field_name="portfolio base currency"
    )
    valuation_timezone = _resolve_portfolio_valuation_timezone(portfolio)
    valuation_cutoff_policy = _resolve_portfolio_valuation_cutoff_policy(portfolio)

    fx_payload = get_platform_fx_rates()
    direct_fx_instruments = valuation_fx.fx_direct_instrument_map(fx_payload)
    instrument_detail_cache: dict[str, dict[str, object] | None] = {}
    transactions_by_date: dict[str, list[dict[str, object]]] = defaultdict(list)
    if include_materialized_rows:
        for transaction in sorted_transactions:
            event_dates = {
                candidate
                for candidate in (
                    transaction_performance_effective_date(transaction),
                    transaction_position_cash_transfer_date(transaction),
                )
                if candidate is not None
            }
            for event_date in event_dates:
                transactions_by_date[event_date.isoformat()].append(transaction)
    snapshots: list[dict[str, object]] = []
    last_complete_nav: float | None = None
    growth_index = 1.0
    peak_growth_index = 1.0
    has_return_history = False
    return_chain_broken = False
    previous_cash_balances_by_currency: dict[str, float] = {}
    previous_cash_balance_date: date | None = None
    cumulative_cash_currency_gains = 0.0
    cash_currency_gain_history_complete = True
    previous_pending_settlement_balances_by_currency: dict[str, float] = {}
    previous_pending_settlement_date: date | None = None
    cumulative_pending_settlement_currency_gains = 0.0
    pending_settlement_currency_gain_history_complete = True
    previous_position_market_values_local_by_instrument: dict[str, dict[str, object]] = {}
    previous_position_market_value_date: date | None = None
    cumulative_instrument_currency_gains = 0.0
    instrument_currency_gain_history_complete = True
    previous_contribution_states_by_axis: dict[str, dict[str, dict[str, object]]] = {
        "instrument": {},
        "account": {},
    }
    external_flow_inside_unreliable_gap = False
    cumulative_performance_pnl = 0.0
    performance_pnl_history_complete = True
    previous_derivative_exposure_present = False

    for as_of_date in _iter_dates(resolved_start_date, resolved_end_date):
        as_of_iso = as_of_date.isoformat()
        transactions_as_of = [
            item
            for item in sorted_transactions
            if _transaction_has_ledger_activity_as_of(
                item,
                as_of_date=as_of_date,
            )
        ]
        postings = derive_ledger_postings(
            portfolio_id,
            transactions_as_of,
            account_cost_methods=account_cost_methods,
            account_currency_map=account_currency_map,
            corporate_actions=corporate_actions,
            as_of_date=as_of_date,
        )
        position_lots = build_position_lots(
            portfolio_id,
            accounts,
            transactions_as_of,
            as_of_date=as_of_date,
            corporate_actions=corporate_actions,
        )
        all_position_lots = position_lots
        open_position_lots = [position_lot for position_lot in all_position_lots if position_lot.get("status") == "open"]
        position_buckets = holdings_market_profile.position_buckets_from_lots(open_position_lots)
        option_obligation_rows = build_option_obligations(
            transactions_as_of,
            # Explicit lifecycle facts are canonical.  As-of replay keeps an
            # obligation open until expiry/assignment/close is imported.
            as_of_date=as_of_date,
        )
        open_option_obligation_rows = [
            row
            for row in option_obligation_rows
            if str(row.get("status") or "") == "open"
            and (_safe_float(row.get("remaining_quantity")) or 0.0) > 1e-9
        ]
        account_instrument_buckets = (
            holdings_market_profile.position_buckets_by_account_instrument_from_lots(open_position_lots)
            if include_materialized_rows
            else []
        )
        transaction_buckets = _sum_period_transaction_buckets(
            transactions_as_of,
            base_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
        )
        realized_pnl_summary = _sum_period_realized_capital_gains(
            position_lots,
            transactions=transactions_as_of,
            start_date=None,
            end_date=as_of_date,
            base_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
        )
        pnl_components = {
            "realized_pnl": realized_pnl_summary["realized_capital_gains"],
            "income_cash_amount": transaction_buckets["earnings"],
            "expense_cash_amount": (
                transaction_buckets["fees"]
                + transaction_buckets["taxes"]
            ),
            "return_of_capital_amount": transaction_buckets["return_of_capital_amount"],
        }

        cash_balance_base = 0.0
        cash_complete = True
        pending_settlement_base = 0.0
        pending_settlement_complete = True
        stale_fx_flag = False
        cash_balances_by_currency: dict[str, float] = defaultdict(float)
        pending_settlement_balances_by_currency: dict[str, float] = defaultdict(float)
        cash_account_ids_by_currency: dict[str, set[str]] = defaultdict(set)
        for posting in postings:
            pending_amount_delta = ledger_posting_pending_amount_as_of(
                posting,
                as_of_date,
            )
            if pending_amount_delta is not None:
                if abs(pending_amount_delta) <= 1e-9:
                    continue
                posting_currency = valuation_fx.required_currency(
                    posting.get("currency"),
                    field_name="ledger-posting currency",
                )
                converted_pending_delta, is_stale = valuation_fx.convert_amount_on(
                    pending_amount_delta,
                    as_of_date=as_of_date,
                    from_currency=posting_currency,
                    to_currency=base_currency,
                    direct_fx_instruments=direct_fx_instruments,
                    instrument_detail_cache=instrument_detail_cache,
                    instrument_detail_loader=get_registry_instrument_detail,
                )
                pending_settlement_balances_by_currency[
                    posting_currency
                ] += pending_amount_delta
                if converted_pending_delta is None:
                    pending_settlement_complete = False
                else:
                    pending_settlement_base += converted_pending_delta
                    stale_fx_flag = stale_fx_flag or is_stale
                continue

            cash_delta = _safe_float(posting.get("cash_amount_delta"))
            if cash_delta is None:
                continue
            posting_currency = valuation_fx.required_currency(
                posting.get("currency"), field_name="ledger-posting currency"
            )
            posting_effective_date = ledger_posting_effective_date_iso(posting)
            posting_account_id = str(posting.get("account_id") or "").strip()
            converted_cash_delta, is_stale = valuation_fx.convert_amount_on(
                cash_delta,
                as_of_date=as_of_date,
                from_currency=posting_currency,
                to_currency=base_currency,
                direct_fx_instruments=direct_fx_instruments,
                instrument_detail_cache=instrument_detail_cache,
                instrument_detail_loader=get_registry_instrument_detail,
            )
            if converted_cash_delta is None:
                if posting_effective_date <= as_of_iso:
                    cash_balances_by_currency[posting_currency] += cash_delta
                    if posting_account_id:
                        cash_account_ids_by_currency[posting_currency].add(posting_account_id)
                    cash_complete = False
                else:
                    pending_settlement_balances_by_currency[posting_currency] += cash_delta
                    pending_settlement_complete = False
                continue
            stale_fx_flag = stale_fx_flag or is_stale
            if posting_effective_date <= as_of_iso:
                cash_balances_by_currency[posting_currency] += cash_delta
                if posting_account_id:
                    cash_account_ids_by_currency[posting_currency].add(posting_account_id)
                cash_balance_base += converted_cash_delta
            else:
                pending_settlement_balances_by_currency[posting_currency] += cash_delta
                pending_settlement_base += converted_cash_delta

        daily_cash_currency_gain = 0.0
        cash_currency_gain_complete = True
        if previous_cash_balance_date is not None:
            for currency, previous_balance in previous_cash_balances_by_currency.items():
                if currency == base_currency or abs(previous_balance) <= 1e-9:
                    continue
                previous_balance_at_previous_fx, previous_fx_stale = valuation_fx.convert_amount_on(
                    previous_balance,
                    as_of_date=previous_cash_balance_date,
                    from_currency=currency,
                    to_currency=base_currency,
                    direct_fx_instruments=direct_fx_instruments,
                    instrument_detail_cache=instrument_detail_cache,
                    instrument_detail_loader=get_registry_instrument_detail,
                )
                previous_balance_at_current_fx, current_fx_stale = valuation_fx.convert_amount_on(
                    previous_balance,
                    as_of_date=as_of_date,
                    from_currency=currency,
                    to_currency=base_currency,
                    direct_fx_instruments=direct_fx_instruments,
                    instrument_detail_cache=instrument_detail_cache,
                    instrument_detail_loader=get_registry_instrument_detail,
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

        daily_pending_settlement_currency_gain = 0.0
        pending_settlement_currency_gain_complete = True
        if previous_pending_settlement_date is not None:
            for currency, previous_balance in previous_pending_settlement_balances_by_currency.items():
                if currency == base_currency or abs(previous_balance) <= 1e-9:
                    continue
                previous_balance_at_previous_fx, previous_fx_stale = valuation_fx.convert_amount_on(
                    previous_balance,
                    as_of_date=previous_pending_settlement_date,
                    from_currency=currency,
                    to_currency=base_currency,
                    direct_fx_instruments=direct_fx_instruments,
                    instrument_detail_cache=instrument_detail_cache,
                    instrument_detail_loader=get_registry_instrument_detail,
                )
                previous_balance_at_current_fx, current_fx_stale = valuation_fx.convert_amount_on(
                    previous_balance,
                    as_of_date=as_of_date,
                    from_currency=currency,
                    to_currency=base_currency,
                    direct_fx_instruments=direct_fx_instruments,
                    instrument_detail_cache=instrument_detail_cache,
                    instrument_detail_loader=get_registry_instrument_detail,
                )
                if (
                    previous_balance_at_previous_fx is None
                    or previous_balance_at_current_fx is None
                ):
                    pending_settlement_currency_gain_complete = False
                    continue
                daily_pending_settlement_currency_gain += (
                    previous_balance_at_current_fx - previous_balance_at_previous_fx
                )
                stale_fx_flag = stale_fx_flag or previous_fx_stale or current_fx_stale

        pending_settlement_currency_gains = None
        if (
            pending_settlement_currency_gain_history_complete
            and pending_settlement_currency_gain_complete
        ):
            cumulative_pending_settlement_currency_gains += daily_pending_settlement_currency_gain
            pending_settlement_currency_gains = cumulative_pending_settlement_currency_gains
        else:
            pending_settlement_currency_gain_history_complete = False

        priced_position_count = 0
        total_position_count = len(position_buckets)
        position_market_value_base = 0.0
        open_cost_basis_base = 0.0
        cost_basis_complete = True
        position_valuation_complete = True
        stale_price_flag = False
        fresh_price_count = 0
        current_position_market_values_local_by_instrument: dict[str, dict[str, object]] = {}
        event_valued_position_present = False
        for bucket in position_buckets:
            currency = valuation_fx.required_currency(
                bucket.get("currency"), field_name="position-bucket currency"
            )
            quantity = _safe_float(bucket.get("quantity")) or 0.0
            cost_basis = _safe_float(bucket.get("cost_basis")) or 0.0
            converted_cost_basis, cost_basis_fx_stale = valuation_fx.convert_amount_on(
                cost_basis,
                as_of_date=as_of_date,
                from_currency=currency,
                to_currency=base_currency,
                direct_fx_instruments=direct_fx_instruments,
                instrument_detail_cache=instrument_detail_cache,
                instrument_detail_loader=get_registry_instrument_detail,
            )
            if converted_cost_basis is None:
                cost_basis_complete = False
            else:
                open_cost_basis_base += converted_cost_basis
                stale_fx_flag = stale_fx_flag or cost_basis_fx_stale

            instrument_ref = (
                bucket.get("instrument_ref")
                if isinstance(bucket.get("instrument_ref"), dict)
                else None
            )
            _carrying_price, market_value_local, event_valued = (
                holdings_market_profile.resolve_position_valuation(
                    quantity=quantity,
                    cost_basis=cost_basis,
                    instrument_ref=instrument_ref,
                    quoted_price=None,
                )
            )
            event_valued_position_present = event_valued_position_present or event_valued
            price_point = None
            if not event_valued:
                detail = valuation_fx.instrument_detail_cache_get(
                    str(bucket.get("instrument_id") or ""),
                    instrument_detail_cache,
                    instrument_detail_loader=get_registry_instrument_detail,
                )
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
                _last_price, market_value_local, _ = (
                    holdings_market_profile.resolve_position_valuation(
                        quantity=quantity,
                        cost_basis=cost_basis,
                        instrument_ref=instrument_ref,
                        quoted_price=_safe_float(price_point.get("value")),
                        quoted_price_scale=_safe_float(price_point.get("price_scale")),
                    )
                )
            converted_market_value, valuation_fx_stale = valuation_fx.convert_amount_on(
                market_value_local,
                as_of_date=as_of_date,
                from_currency=currency,
                to_currency=base_currency,
                direct_fx_instruments=direct_fx_instruments,
                instrument_detail_cache=instrument_detail_cache,
                instrument_detail_loader=get_registry_instrument_detail,
            )
            if market_value_local is None or converted_market_value is None:
                position_valuation_complete = False
                continue
            price_point_date = _parse_iso_date((price_point or {}).get("as_of_date"))
            if price_point_date == as_of_date:
                fresh_price_count += 1
            current_position_market_values_local_by_instrument[
                str(bucket.get("instrument_id") or "")
            ] = {
                "currency": currency,
                "market_value_local": market_value_local,
            }
            if not event_valued:
                priced_position_count += 1
            position_market_value_base += converted_market_value
            stale_price_flag = stale_price_flag or bool((price_point or {}).get("stale"))
            stale_fx_flag = stale_fx_flag or valuation_fx_stale

        daily_instrument_currency_gain = 0.0
        instrument_currency_gain_complete = True
        if previous_position_market_value_date is not None:
            for instrument_id, previous_position_value in previous_position_market_values_local_by_instrument.items():
                del instrument_id
                currency = valuation_fx.required_currency(
                    previous_position_value.get("currency"),
                    field_name="position market-value currency",
                )
                if currency == base_currency:
                    continue
                previous_market_value_local = _safe_float(previous_position_value.get("market_value_local"))
                if previous_market_value_local is None or abs(previous_market_value_local) <= 1e-9:
                    continue
                previous_value_at_previous_fx, previous_fx_stale = valuation_fx.convert_amount_on(
                    previous_market_value_local,
                    as_of_date=previous_position_market_value_date,
                    from_currency=currency,
                    to_currency=base_currency,
                    direct_fx_instruments=direct_fx_instruments,
                    instrument_detail_cache=instrument_detail_cache,
                    instrument_detail_loader=get_registry_instrument_detail,
                )
                previous_value_at_current_fx, current_fx_stale = valuation_fx.convert_amount_on(
                    previous_market_value_local,
                    as_of_date=as_of_date,
                    from_currency=currency,
                    to_currency=base_currency,
                    direct_fx_instruments=direct_fx_instruments,
                    instrument_detail_cache=instrument_detail_cache,
                    instrument_detail_loader=get_registry_instrument_detail,
                )
                if previous_value_at_previous_fx is None or previous_value_at_current_fx is None:
                    instrument_currency_gain_complete = False
                    continue
                daily_instrument_currency_gain += previous_value_at_current_fx - previous_value_at_previous_fx
                stale_fx_flag = stale_fx_flag or previous_fx_stale or current_fx_stale

        instrument_currency_gains = None
        if instrument_currency_gain_history_complete and instrument_currency_gain_complete:
            cumulative_instrument_currency_gains += daily_instrument_currency_gain
            instrument_currency_gains = cumulative_instrument_currency_gains
        else:
            instrument_currency_gain_history_complete = False

        derivative_liability_base = 0.0
        derivative_liability_complete = True
        derivative_liability_local = 0.0
        derivative_liability_currency_map: dict[str, float] = defaultdict(float)
        for obligation in open_option_obligation_rows:
            obligation_currency = valuation_fx.required_currency(
                obligation.get("contract_currency")
                or account_currency_map.get(str(obligation.get("account_id") or ""))
                or base_currency,
                field_name="option obligation currency",
            )
            liability = _safe_float(obligation.get("carrying_liability")) or 0.0
            derivative_liability_local += liability
            derivative_liability_currency_map[obligation_currency] += liability
            converted_liability, liability_fx_stale = valuation_fx.convert_amount_on(
                liability,
                as_of_date=as_of_date,
                from_currency=obligation_currency,
                to_currency=base_currency,
                direct_fx_instruments=direct_fx_instruments,
                instrument_detail_cache=instrument_detail_cache,
                instrument_detail_loader=get_registry_instrument_detail,
            )
            if converted_liability is None:
                derivative_liability_complete = False
            else:
                derivative_liability_base += converted_liability
                stale_fx_flag = stale_fx_flag or liability_fx_stale

        has_derivative_exposure = (
            bool(open_option_obligation_rows) or event_valued_position_present
        )
        has_derivative_lifecycle_activity = any(
            transaction_performance_effective_date(transaction) == as_of_date
            and _has_derivative_lifecycle_activity(transaction)
            for transaction in sorted_transactions
        )
        derivative_return_observation_excluded = (
            previous_derivative_exposure_present
            or has_derivative_exposure
            or has_derivative_lifecycle_activity
        )
        has_snapshot_content = bool(postings) or bool(position_buckets) or bool(open_option_obligation_rows)
        valuation_complete = (
            cash_complete
            and pending_settlement_complete
            and position_valuation_complete
            and derivative_liability_complete
        )
        valuation_coverage_state = (
            "complete"
            if valuation_complete
            else return_chain.incomplete_coverage_state(has_content=has_snapshot_content)
        )
        book_pnl_complete = (
            valuation_complete
            and cost_basis_complete
            and cash_currency_gain_history_complete
            and pending_settlement_currency_gain_history_complete
            and instrument_currency_gain_history_complete
            and bool(transaction_buckets["coverage_complete"])
            and bool(realized_pnl_summary["coverage_complete"])
        )
        book_pnl_coverage_state = (
            "complete"
            if book_pnl_complete
            else return_chain.incomplete_coverage_state(has_content=has_snapshot_content)
        )

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
            and valuation_coverage_state == "complete"
        ):
            nav = (
                resolved_cash_balance
                + pending_settlement_base
                + resolved_position_market_value
                - derivative_liability_base
            )
            if resolved_open_cost_basis is not None:
                unrealized_pnl = resolved_position_market_value - resolved_open_cost_basis

        flow_breakdown = _daily_external_flow_breakdown(
            sorted_transactions,
            as_of_date,
            base_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
        )
        flow_coverage_complete = bool(flow_breakdown["coverage_complete"])
        flow_has_external_cash = (
            abs(_safe_float(flow_breakdown["external_cash_in"]) or 0.0) > 1e-9
            or abs(_safe_float(flow_breakdown["external_cash_out"]) or 0.0) > 1e-9
        )
        if nav is None and flow_has_external_cash:
            external_flow_inside_unreliable_gap = True
            return_chain_broken = True
        stale_fx_flag = stale_fx_flag or bool(flow_breakdown["stale_fx_flag"])
        beginning_nav = last_complete_nav
        absolute_change = None
        delta = None
        daily_twr = None
        reanchor_after_flow_gap = bool(
            nav is not None
            and external_flow_inside_unreliable_gap
        )
        if reanchor_after_flow_gap:
            # An external flow occurred while fair value was unavailable, so
            # no BOD/EOD subperiod can isolate investment performance.  The
            # first reliable NAV is a new boundary, not a bridging return.
            beginning_nav = nav
        elif nav is not None and last_complete_nav is not None and flow_coverage_complete:
            absolute_change = nav - last_complete_nav
            delta = absolute_change - flow_breakdown["net_external_inflow"]
            denominator = last_complete_nav + flow_breakdown["external_cash_in"]
            numerator = nav + flow_breakdown["external_cash_out"]
            if denominator > 1e-9 and numerator >= 0:
                daily_twr = (numerator / denominator) - 1.0
        elif (
            nav is not None
            and last_complete_nav is None
            and flow_coverage_complete
            and float(flow_breakdown["external_cash_in"]) > 1e-9
        ):
            # Inception funding follows the same documented BOD contribution /
            # EOD withdrawal convention as every later daily subperiod.  A
            # null first-day return silently discarded inception-day P&L.
            beginning_nav = 0.0
            absolute_change = nav
            delta = nav - flow_breakdown["net_external_inflow"]
            denominator = float(flow_breakdown["external_cash_in"])
            numerator = nav + float(flow_breakdown["external_cash_out"])
            if numerator >= 0:
                daily_twr = (numerator / denominator) - 1.0

        # ``total_pnl`` is a performance-period bridge, not a reconstruction
        # from FIFO/moving-average book components.  Attached trade charges
        # are already embedded in lot cost or net sale proceeds, so summing
        # those book components and subtracting the charges again understates
        # economic P&L.  Accumulating the flow-neutral daily NAV delta makes
        # the invariant explicit:
        #
        #   ending NAV = opening NAV + net external flow + total P&L
        #
        # An imported opening balance establishes the first fair-value
        # boundary and therefore starts with zero period P&L.
        if nav is None and has_snapshot_content:
            # A later reliable NAV cannot reconstruct economic P&L across an
            # earlier valuation gap, even when no external flow occurred.
            # Keep the bridge unavailable instead of silently treating the
            # first recovered value as a zero-P&L imported anchor.
            performance_pnl_history_complete = False
        if reanchor_after_flow_gap or (
            flow_has_external_cash and not flow_coverage_complete
        ):
            performance_pnl_history_complete = False
        if delta is not None and performance_pnl_history_complete:
            cumulative_performance_pnl += delta
            total_pnl = cumulative_performance_pnl
        elif (
            nav is not None
            and last_complete_nav is None
            and not flow_has_external_cash
            and performance_pnl_history_complete
        ):
            total_pnl = cumulative_performance_pnl

        if valuation_coverage_state != "complete":
            return_coverage_state = valuation_coverage_state
        elif not flow_coverage_complete:
            return_coverage_state = return_chain.incomplete_coverage_state(
                has_content=has_snapshot_content
            )
        elif reanchor_after_flow_gap:
            return_coverage_state = "partial"
        elif daily_twr is not None and isfinite(daily_twr):
            return_coverage_state = "complete"
        else:
            return_coverage_state = "unavailable"

        if reanchor_after_flow_gap:
            return_chain_broken = True

        attribution_coverage_state = return_chain.merge_coverage_states(
            [book_pnl_coverage_state, return_coverage_state]
        )
        cash_flow_coverage_state = (
            "complete"
            if flow_coverage_complete
            else return_chain.incomplete_coverage_state(has_content=has_snapshot_content)
        )
        coverage_state = return_chain.merge_coverage_states(
            [
                valuation_coverage_state,
                book_pnl_coverage_state,
                cash_flow_coverage_state,
            ]
        )

        if daily_twr is not None and isfinite(daily_twr):
            has_return_history = True
            growth_index *= 1.0 + daily_twr
            peak_growth_index = max(peak_growth_index, growth_index)

        cumulative_twr = (
            (growth_index - 1.0)
            if has_return_history and not return_chain_broken
            else None
        )
        drawdown = (
            (growth_index / peak_growth_index) - 1.0
            if has_return_history and not return_chain_broken and peak_growth_index > 0
            else None
        )

        if nav is not None:
            last_complete_nav = nav
            if reanchor_after_flow_gap:
                external_flow_inside_unreliable_gap = False

        snapshot_payload: dict[str, object] = {
            "as_of_date": as_of_date,
            "base_currency": base_currency,
            "valuation_timezone": valuation_timezone,
            "valuation_cutoff_policy": valuation_cutoff_policy,
            "coverage_state": coverage_state,
            "valuation_coverage_state": valuation_coverage_state,
            "return_coverage_state": return_coverage_state,
            "book_pnl_coverage_state": book_pnl_coverage_state,
            "attribution_coverage_state": attribution_coverage_state,
            "return_chain_continuous": not return_chain_broken,
            "stale_price_flag": stale_price_flag,
            "stale_fx_flag": stale_fx_flag,
            "total_position_count": total_position_count,
            "priced_position_count": priced_position_count,
            "market_observation_count": fresh_price_count,
            "return_observation_eligible": (
                daily_twr is not None
                and isfinite(daily_twr)
                and return_coverage_state == "complete"
                and (fresh_price_count > 0 or abs(daily_twr) > 1e-12)
                and not stale_price_flag
                and not stale_fx_flag
                and not derivative_return_observation_excluded
            ),
            "return_observation_exclusion_reason": (
                "event_valued_or_derivative_liability"
                if derivative_return_observation_excluded
                else "stale_market_or_fx_input"
                if stale_price_flag or stale_fx_flag
                else None
            ),
            "cash_balance": resolved_cash_balance,
            "pending_settlement": pending_settlement_base if pending_settlement_complete else None,
            "position_market_value": resolved_position_market_value,
            "derivative_liability_base": (
                derivative_liability_base if derivative_liability_complete else None
            ),
            "derivative_liability": (
                derivative_liability_local if derivative_liability_complete else None
            ),
            "open_option_obligation_count": len(open_option_obligation_rows),
            "option_obligation_coverage_state": (
                "complete" if derivative_liability_complete else "unavailable"
            ),
            "nav": nav,
            "open_cost_basis": resolved_open_cost_basis,
            "unrealized_pnl": unrealized_pnl,
            "realized_pnl": pnl_components["realized_pnl"],
            "derivative_lifecycle_realized_pnl": realized_pnl_summary[
                "derivative_lifecycle_realized_pnl"
            ],
            "income_cash_amount": pnl_components["income_cash_amount"],
            "expense_cash_amount": pnl_components["expense_cash_amount"],
            "cash_currency_gains": cash_currency_gains,
            "pending_settlement_currency_gains": pending_settlement_currency_gains,
            "instrument_currency_gains": instrument_currency_gains,
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
            "performance_basis": (
                "operational_carrying_basis"
                if derivative_return_observation_excluded
                else "market_value"
            ),
            "performance_label": (
                "Total Portfolio Operational Return"
                if derivative_return_observation_excluded
                else "Total Portfolio Return"
            ),
        }

        if include_materialized_rows:
            snapshot_payload["_holding_rows"] = (
                holdings_market_profile.build_materialized_holding_rows(
                    account_instrument_buckets=account_instrument_buckets,
                    cash_balances=[
                        {
                            "currency": currency,
                            "amount": amount,
                            "amount_base": (
                                valuation_fx.convert_amount_on(
                                    amount,
                                    as_of_date=as_of_date,
                                    from_currency=currency,
                                    to_currency=base_currency,
                                    direct_fx_instruments=direct_fx_instruments,
                                    instrument_detail_cache=instrument_detail_cache,
                                    instrument_detail_loader=get_registry_instrument_detail,
                                )[0]
                            ),
                            "account_ids": sorted(cash_account_ids_by_currency[currency]),
                        }
                        for currency, amount in sorted(cash_balances_by_currency.items())
                        if abs(amount) > 1e-9
                    ],
                    pending_balances=_pending_monetary_balances_from_postings(
                        postings=postings,
                        as_of_date=as_of_date,
                        base_currency=base_currency,
                        direct_fx_instruments=direct_fx_instruments,
                        instrument_detail_cache=instrument_detail_cache,
                    ),
                    option_obligations=open_option_obligation_rows,
                    as_of_date=as_of_date,
                    base_currency=base_currency,
                    direct_fx_instruments=direct_fx_instruments,
                    instrument_detail_cache=instrument_detail_cache,
                    nav=nav,
                    normalize_currency=valuation_fx.normalized_currency,
                    safe_float=_safe_float,
                    parse_iso_date=_parse_iso_date,
                    convert_amount_on=partial(
                        valuation_fx.convert_amount_on,
                        instrument_detail_loader=get_registry_instrument_detail,
                    ),
                    instrument_detail_cache_get=partial(
                        valuation_fx.instrument_detail_cache_get,
                        instrument_detail_loader=get_registry_instrument_detail,
                    ),
                    select_market_point_as_of=_select_market_point_as_of,
                    previous_market_point_for_selected_point=(
                        _previous_market_point_for_selected_point
                    ),
                    position_market_value=valuation_fx.position_market_value,
                    holding_day_change=(
                        holdings_market_profile.holding_day_change_metrics
                    ),
                    normalize_instrument=(
                        holdings_market_profile.normalize_instrument_core
                    ),
                    build_cash_rows=partial(
                        holdings_market_profile.build_cash_holding_rows,
                        cash_day_change=partial(
                            holdings_market_profile.cash_day_change_metrics,
                            resolve_fx_rate_on=partial(
                                valuation_fx.resolve_fx_rate_on,
                                instrument_detail_loader=get_registry_instrument_detail,
                            ),
                            resolve_previous_fx_rate_before=partial(
                                valuation_fx.resolve_previous_fx_rate_before,
                                instrument_detail_loader=get_registry_instrument_detail,
                            ),
                        ),
                    ),
                    build_pending_rows=partial(
                        holdings_market_profile.build_pending_monetary_holding_rows,
                        cash_day_change=partial(
                            holdings_market_profile.cash_day_change_metrics,
                            resolve_fx_rate_on=partial(
                                valuation_fx.resolve_fx_rate_on,
                                instrument_detail_loader=get_registry_instrument_detail,
                            ),
                            resolve_previous_fx_rate_before=partial(
                                valuation_fx.resolve_previous_fx_rate_before,
                                instrument_detail_loader=get_registry_instrument_detail,
                            ),
                        ),
                    ),
                    apply_portfolio_weights=(
                        holdings_market_profile.apply_position_portfolio_weights
                    ),
                )
            )
            contribution_slices: list[dict[str, object]] = []
            for contribution_axis in attribution.MATERIALIZED_CONTRIBUTION_AXES:
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
                    direct_fx_instruments=direct_fx_instruments,
                    instrument_detail_cache=instrument_detail_cache,
                )
                current_events = _build_contribution_daily_events(
                    axis=contribution_axis,
                    as_of_date=as_of_date,
                    position_lots=all_position_lots,
                    transactions_on_date=transactions_by_date.get(as_of_iso, []),
                    transactions_as_of=transactions_as_of,
                    base_currency=base_currency,
                    account_name_map=account_name_map,
                    direct_fx_instruments=direct_fx_instruments,
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
                        direct_fx_instruments=direct_fx_instruments,
                        instrument_detail_cache=instrument_detail_cache,
                    )
                )
                previous_contribution_states_by_axis[contribution_axis] = current_states
            if contribution_slices:
                snapshot_payload["attribution_coverage_state"] = return_chain.merge_coverage_states(
                    [
                        str(item.get("coverage_state") or "unavailable")
                        for item in contribution_slices
                    ]
                )
            snapshot_payload["_contribution_slices"] = contribution_slices

        snapshots.append(snapshot_payload)

        previous_cash_balances_by_currency = dict(cash_balances_by_currency)
        previous_cash_balance_date = as_of_date
        previous_pending_settlement_balances_by_currency = dict(
            pending_settlement_balances_by_currency
        )
        previous_pending_settlement_date = as_of_date
        previous_position_market_values_local_by_instrument = deepcopy(current_position_market_values_local_by_instrument)
        previous_position_market_value_date = as_of_date
        previous_derivative_exposure_present = has_derivative_exposure

    return snapshots


def _portfolio_inception_date(
    transactions: list[dict[str, object]],
    snapshots: list[dict[str, object]],
) -> date | None:
    transaction_dates = [
        effective_date
        for transaction in transactions
        if (effective_date := transaction_performance_effective_date(transaction)) is not None
    ]
    if transaction_dates:
        return min(transaction_dates)
    activity_dates = [
        cast(date, snapshot["as_of_date"])
        for snapshot in snapshots
        if isinstance(snapshot.get("as_of_date"), date)
        and (
            abs(_safe_float(snapshot.get("nav")) or 0.0) > 1e-12
            or abs(_safe_float(snapshot.get("external_cash_in")) or 0.0) > 1e-12
            or abs(_safe_float(snapshot.get("external_cash_out")) or 0.0) > 1e-12
        )
    ]
    return min(activity_dates) if activity_dates else None


def _snapshot_as_close_boundary(
    snapshot: dict[str, object],
) -> dict[str, object]:
    """Project an EOD valuation row as the anchor of a queried interval.

    Stored daily snapshots describe that calendar day's BOD-to-EOD subperiod.
    A query ``start_date`` instead identifies the EOD state after that
    subperiod.  Keep its valuation and cumulative book balances, while removing
    the day's flow, P&L, and return observations from the queried interval.
    """

    boundary = dict(snapshot)
    ending_nav = _safe_float(snapshot.get("ending_nav"))
    if ending_nav is None:
        ending_nav = _safe_float(snapshot.get("nav"))
    boundary["_is_initial_valuation_anchor"] = True
    boundary["_is_period_start_close_anchor"] = True
    boundary["beginning_nav"] = ending_nav
    boundary["ending_nav"] = ending_nav
    boundary["nav"] = ending_nav
    boundary["external_cash_in"] = 0.0
    boundary["external_cash_out"] = 0.0
    boundary["net_external_inflow"] = 0.0
    boundary["absolute_change"] = 0.0 if ending_nav is not None else None
    boundary["delta"] = 0.0 if ending_nav is not None else None
    boundary["daily_twr"] = None
    boundary["cumulative_twr"] = None
    boundary["drawdown"] = None
    boundary["return_observation_eligible"] = False
    # An explicit complete EOD valuation is allowed to start a new chain even
    # when an earlier, out-of-window chain was broken.
    boundary["return_chain_continuous"] = return_chain.has_complete_valuation(
        boundary
    )
    boundary["attribution_coverage_state"] = return_chain.merge_coverage_states(
        [
            return_chain.snapshot_coverage_state(boundary, "valuation"),
            return_chain.snapshot_coverage_state(boundary, "book_pnl"),
        ]
    )
    return boundary


def _rebased_twr_series(snapshots: list[dict[str, object]]) -> list[dict[str, object]]:
    rendered_snapshots = return_chain.rebased_twr_series(snapshots)
    growth_index = 1.0
    peak_growth_index = 1.0
    has_return_history = False

    for snapshot, rendered_snapshot in zip(snapshots, rendered_snapshots, strict=True):
        daily_twr = _safe_float(snapshot.get("daily_twr"))
        point_return_complete = (
            return_chain.snapshot_coverage_state(snapshot, "return") == "complete"
            and daily_twr is not None
            and isfinite(daily_twr)
        )
        if point_return_complete and bool(rendered_snapshot["return_chain_continuous"]):
            has_return_history = True
            growth_index *= 1.0 + daily_twr
            peak_growth_index = max(peak_growth_index, growth_index)
        is_complete_initial_anchor = bool(
            snapshot.get("_is_initial_valuation_anchor")
        ) and bool(rendered_snapshot["return_chain_continuous"])
        if is_complete_initial_anchor and not has_return_history:
            rendered_snapshot["drawdown"] = 0.0
        else:
            rendered_snapshot["drawdown"] = (
                (growth_index / peak_growth_index) - 1.0
                if has_return_history
                and bool(rendered_snapshot["return_chain_continuous"])
                and peak_growth_index > 0
                else None
            )

    return rendered_snapshots


def build_portfolio_performance_report(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, object]:
    # Keep one predecessor EOD row as context.  It distinguishes an ordinary
    # contribution to a live cash-bearing portfolio from a genuine re-funding
    # after NAV reached zero, while the report builder still exposes only the
    # requested interval.
    snapshot_build_start = (
        start_date - timedelta(days=1)
        if start_date is not None
        else None
    )
    snapshots = build_daily_portfolio_snapshots(
        portfolio,
        accounts,
        transactions,
        start_date=snapshot_build_start,
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
    portfolio_default_end_date = _parse_iso_date(portfolio.get("as_of_date"))
    source_snapshots: list[dict[str, object]] = []
    for snapshot in snapshots:
        snapshot_date = _parse_iso_date(snapshot.get("as_of_date"))
        if snapshot_date is None:
            continue
        normalized_snapshot = dict(snapshot)
        normalized_snapshot["as_of_date"] = snapshot_date
        source_snapshots.append(normalized_snapshot)
    source_snapshots.sort(
        key=lambda item: cast(date, item["as_of_date"])
    )
    inception_date = _portfolio_inception_date(
        transactions or [],
        source_snapshots,
    )
    raw_start_snapshot = next(
        (
            snapshot
            for snapshot in source_snapshots
            if start_date is not None
            and snapshot.get("as_of_date") == start_date
        ),
        None,
    )
    predecessor_snapshot = next(
        (
            snapshot
            for snapshot in reversed(source_snapshots)
            if start_date is not None
            and isinstance(snapshot.get("as_of_date"), date)
            and snapshot["as_of_date"] < start_date
        ),
        None,
    )
    start_is_funded_segment = bool(
        start_date is not None
        and _snapshot_starts_funded_segment(
            raw_start_snapshot,
            resolved_start_date=start_date,
        )
    )
    start_is_close_boundary = bool(
        start_date is not None
        and _period_start_is_close_boundary(
            transactions or [],
            requested_start_date=start_date,
            resolved_start_date=start_date,
            start_snapshot=raw_start_snapshot,
        )
    )
    snapshot_window = return_chain.resolve_reliable_snapshot_window(
        source_snapshots,
        requested_start_date=start_date,
        requested_end_date=end_date,
        default_end_date=portfolio_default_end_date,
    )
    snapshots = list(cast(list[dict[str, object]], snapshot_window["snapshots"]))
    visible_snapshots = [
        (
            _snapshot_as_close_boundary(snapshot)
            if (
                start_is_close_boundary
                and start_date is not None
                and snapshot.get("as_of_date") == start_date
            )
            else dict(snapshot)
        )
        for snapshot in snapshots
        if start_date is None
        or (isinstance(snapshot.get("as_of_date"), date) and snapshot["as_of_date"] >= start_date)
    ]
    visible_daily_series_snapshots = _rebased_twr_series(visible_snapshots)
    snapshot_summary = return_chain.summarize_daily_snapshots(
        visible_snapshots,
        requested_start_date=cast(date | None, snapshot_window["requested_start_date"]),
        requested_end_date=cast(date | None, snapshot_window["requested_end_date"]),
        effective_start_date=cast(date | None, snapshot_window["effective_start_date"]),
        effective_end_date=cast(date | None, snapshot_window["effective_end_date"]),
        as_of_clamp_reason=cast(str | None, snapshot_window["as_of_clamp_reason"]),
    )
    return_coverage_state = return_chain.period_return_coverage_state(
        snapshots,
        visible_snapshots,
        requested_start_date=start_date,
        effective_end_date=cast(date | None, snapshot_window["effective_end_date"]),
        inception_date=inception_date,
        start_is_close_boundary=start_is_close_boundary,
        start_is_funded_segment=start_is_funded_segment,
    )
    valuation_coverage_state = return_chain.aggregate_snapshot_coverage(visible_snapshots, "valuation")
    book_pnl_coverage_state = return_chain.aggregate_snapshot_coverage(visible_snapshots, "book_pnl")
    attribution_coverage_state = return_chain.aggregate_snapshot_coverage(visible_snapshots, "attribution")
    complete_snapshots = [
        snapshot
        for snapshot in visible_snapshots
        if return_chain.has_complete_valuation(snapshot)
    ]
    visible_complete_snapshots = [
        snapshot
        for snapshot in visible_snapshots
        if return_chain.has_complete_valuation(snapshot)
    ]
    performance_is_operational = any(
        snapshot.get("performance_basis") == "operational_carrying_basis"
        for snapshot in visible_snapshots
    )
    return_observation_count = sum(
        1 for snapshot in visible_snapshots if _safe_float(snapshot.get("daily_twr")) is not None
    )
    risk_return_snapshots = [
        snapshot
        for snapshot in visible_snapshots
        if not performance_is_operational
        and return_coverage_state == "complete"
        and snapshot.get("daily_twr") is not None
        and bool(snapshot.get("return_observation_eligible"))
    ]
    risk_return_observation_count = len(risk_return_snapshots)
    start_snapshot = complete_snapshots[0] if complete_snapshots else None
    end_snapshot = visible_complete_snapshots[-1] if visible_complete_snapshots else (complete_snapshots[-1] if complete_snapshots else None)
    first_visible_complete_snapshot = (
        visible_complete_snapshots[0] if visible_complete_snapshots else None
    )
    funded_segment_window = bool(
        not start_is_close_boundary
        and start_snapshot is not None
        and first_visible_complete_snapshot is start_snapshot
        and abs(_safe_float(start_snapshot.get("beginning_nav")) or 0.0) <= 1e-12
        and (_safe_float(start_snapshot.get("external_cash_in")) or 0.0) > 1e-9
    )
    start_anchor_date = start_snapshot.get("as_of_date") if isinstance((start_snapshot or {}).get("as_of_date"), date) else None
    end_anchor_date = end_snapshot.get("as_of_date") if isinstance((end_snapshot or {}).get("as_of_date"), date) else None
    portfolio_inception_window = bool(
        funded_segment_window
        and start_anchor_date is not None
        and start_anchor_date == inception_date
    )
    starts_on_imported_anchor = bool(
        start_anchor_date is not None
        and _starts_on_imported_valuation_anchor(
            transactions or [],
            resolved_start_date=start_anchor_date,
        )
    )
    start_boundary_kind = (
        "funded_bod"
        if funded_segment_window
        else (
            "imported_opening_eod"
            if starts_on_imported_anchor
            else ("close_eod" if start_anchor_date is not None else None)
        )
    )
    # Dates label EOD observations.  A funded-segment start row is a real
    # start-day BOD-to-EOD subperiod, so its elapsed-time boundary is
    # represented as the preceding calendar date.  An imported opening
    # balance is an EOD anchor and is deliberately not shifted.
    performance_start_boundary_date = (
        start_anchor_date - timedelta(days=1)
        if funded_segment_window and start_anchor_date is not None
        else start_anchor_date
    )
    display_start_date = start_anchor_date
    if start_date is not None and start_anchor_date is not None and start_anchor_date < start_date:
        display_start_date = start_date

    def snapshot_period_delta(field_name: str) -> float | None:
        end_value = _safe_float((end_snapshot or {}).get(field_name))
        if end_value is None:
            return None
        if start_snapshot is None:
            return end_value
        if portfolio_inception_window:
            return end_value
        period_baseline_snapshot = (
            predecessor_snapshot
            if funded_segment_window
            else start_snapshot
        )
        start_value = _safe_float(
            (period_baseline_snapshot or {}).get(field_name)
        )
        if start_value is None:
            return None
        return end_value - start_value

    cumulative_twr = None
    if end_snapshot and return_coverage_state == "complete":
        cumulative_twr = return_chain.compound_daily_twr(visible_snapshots)
        if cumulative_twr is None and start_snapshot is end_snapshot:
            cumulative_twr = 0.0
    annualization_start_date = None
    if (
        start_snapshot is not None
        and end_snapshot is not None
        and isinstance(start_snapshot.get("as_of_date"), date)
        and isinstance(end_snapshot.get("as_of_date"), date)
    ):
        annualization_start_date = performance_start_boundary_date
    annualization = annualization_eligibility(annualization_start_date, end_anchor_date)
    annualization_eligible = annualization.eligible and not performance_is_operational
    annualization_unavailable_reason = (
        "operational_carrying_basis_not_annualized"
        if performance_is_operational
        else annualization.unavailable_reason
    )
    annualized_twr = None
    if (
        annualization_eligible
        and annualization.years is not None
        and cumulative_twr is not None
        and 1.0 + cumulative_twr >= 0
    ):
        annualized_twr = (1.0 + cumulative_twr) ** (1.0 / annualization.years) - 1.0

    flow_snapshots = visible_snapshots
    if start_anchor_date is not None and end_anchor_date is not None:
        flow_snapshots = [
            snapshot
            for snapshot in visible_snapshots
            if isinstance(snapshot.get("as_of_date"), date)
            and (
                start_anchor_date <= snapshot["as_of_date"] <= end_anchor_date
                if funded_segment_window
                else start_anchor_date < snapshot["as_of_date"] <= end_anchor_date
            )
        ]
    external_cash_in = sum((_safe_float(snapshot.get("external_cash_in")) or 0.0) for snapshot in flow_snapshots)
    external_cash_out = sum((_safe_float(snapshot.get("external_cash_out")) or 0.0) for snapshot in flow_snapshots)
    net_external_inflow = external_cash_in - external_cash_out

    start_nav = (
        _safe_float((start_snapshot or {}).get("beginning_nav"))
        if funded_segment_window
        else _safe_float((start_snapshot or {}).get("nav"))
    )
    end_nav = _safe_float((end_snapshot or {}).get("nav"))
    absolute_change = (end_nav - start_nav) if start_nav is not None and end_nav is not None else None
    delta = (
        absolute_change - net_external_inflow
        if absolute_change is not None
        else None
    )
    realized_pnl = snapshot_period_delta("realized_pnl")
    derivative_lifecycle_realized_pnl = snapshot_period_delta(
        "derivative_lifecycle_realized_pnl"
    )
    unrealized_pnl = snapshot_period_delta("unrealized_pnl")
    income_cash_amount = snapshot_period_delta("income_cash_amount")
    expense_cash_amount = snapshot_period_delta("expense_cash_amount")
    cash_currency_gains = snapshot_period_delta("cash_currency_gains")
    pending_settlement_currency_gains = snapshot_period_delta(
        "pending_settlement_currency_gains"
    )
    instrument_currency_gains = snapshot_period_delta("instrument_currency_gains")
    return_of_capital_amount = snapshot_period_delta("return_of_capital_amount")
    # Performance Total P&L is the period NAV bridge.  Book realized and
    # unrealized fields remain separately disclosed and may depend on the
    # account cost method, but they do not redefine economic period P&L.
    total_pnl = delta

    irr = None
    irr_solver_status: period_metrics.XirrSolveStatus | None = None
    irr_unavailable_reason: str | None = None
    irr_inputs_available = (
        start_snapshot is not None
        and end_snapshot is not None
        and start_nav is not None
        and end_nav is not None
        and start_anchor_date is not None
        and end_anchor_date is not None
    )
    if performance_is_operational:
        irr_unavailable_reason = "operational_carrying_basis_not_annualized"
    elif return_coverage_state != "complete":
        irr_unavailable_reason = "return_coverage_incomplete"
    elif not annualization_eligible:
        irr_unavailable_reason = (
            annualization_unavailable_reason or "annualization_ineligible"
        )
    elif not irr_inputs_available:
        irr_unavailable_reason = "cash_flow_window_unavailable"
    else:
        cash_flows: list[tuple[date, float]] = (
            [] if funded_segment_window else [(start_anchor_date, -start_nav)]
        )
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
        irr_result = period_metrics.solve_xirr_result(cash_flows)
        irr_solver_status = irr_result.status
        if irr_result.status == "unique_root":
            irr = irr_result.rate
        else:
            irr_unavailable_reason = irr_result.status

    daily_returns = [
        _safe_float(snapshot.get("daily_twr"))
        for snapshot in risk_return_snapshots
        if snapshot.get("daily_twr") is not None
    ]
    daily_returns = [value for value in daily_returns if value is not None]
    mean_daily_return = (sum(daily_returns) / len(daily_returns)) if daily_returns else None
    volatility = period_metrics.sample_stddev(daily_returns)
    periods_per_year = period_metrics.periods_per_year_from_observations(
        observation_count=len(daily_returns),
        start_date=cast(date | None, performance_start_boundary_date),
        end_date=cast(date | None, end_anchor_date),
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
    annualized_risk_free_rate = 0.0
    annualized_mean_excess_return = (
        annualized_return_from_daily_mean - annualized_risk_free_rate
        if annualized_return_from_daily_mean is not None
        else None
    )
    sharpe_ratio = (
        annualized_mean_excess_return / annualized_volatility
        if annualized_mean_excess_return is not None
        and annualized_volatility is not None
        and annualized_volatility > 1e-12
        else None
    )
    downside_volatility = period_metrics.downside_deviation(daily_returns)
    annualized_downside_volatility = (
        downside_volatility * sqrt(periods_per_year)
        if downside_volatility is not None and periods_per_year is not None
        else None
    )
    sortino_ratio = (
        annualized_mean_excess_return / annualized_downside_volatility
        if annualized_mean_excess_return is not None
        and annualized_downside_volatility is not None
        and annualized_downside_volatility > 1e-12
        else None
    )
    risk_result_contract = period_metrics.portfolio_risk_result_contract(
        return_coverage_state=return_coverage_state,
        sample_count=len(daily_returns),
        periods_per_year=periods_per_year,
        annualized_volatility=annualized_volatility,
    )

    drawdown_stats = period_metrics.drawdown_stats(
        (
            visible_snapshots
            if return_coverage_state == "complete" and not performance_is_operational
            else []
        ),
        start_anchor_date=performance_start_boundary_date,
    )
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

    total_pnl_baseline_snapshot = (
        None
        if portfolio_inception_window
        else (
            predecessor_snapshot
            if funded_segment_window
            else start_snapshot
        )
    )
    start_total_pnl = _safe_float(
        (total_pnl_baseline_snapshot or {}).get("total_pnl")
    )

    def period_total_pnl_to_date(snapshot: dict[str, object]) -> float | None:
        snapshot_total_pnl = _safe_float(snapshot.get("total_pnl"))
        if snapshot_total_pnl is None:
            return None
        if portfolio_inception_window:
            return snapshot_total_pnl
        if start_total_pnl is None:
            return None
        return snapshot_total_pnl - start_total_pnl

    return {
        "portfolio_id": str(portfolio.get("portfolio_id") or ""),
        "base_currency": valuation_fx.required_currency(
            portfolio.get("base_currency"), field_name="portfolio base currency"
        ),
        "valuation_timezone": _resolve_portfolio_valuation_timezone(portfolio),
        "valuation_cutoff_policy": _resolve_portfolio_valuation_cutoff_policy(portfolio),
        "summary": {
            "start_date": display_start_date,
            "end_date": end_anchor_date or (snapshots[-1]["as_of_date"] if snapshots else None),
            "coverage_state": coverage_state,
            "valuation_coverage_state": valuation_coverage_state,
            "return_coverage_state": return_coverage_state,
            "book_pnl_coverage_state": book_pnl_coverage_state,
            "attribution_coverage_state": attribution_coverage_state,
            "requested_start_date": snapshot_window["requested_start_date"],
            "requested_end_date": snapshot_window["requested_end_date"],
            "effective_start_date": snapshot_window["effective_start_date"],
            "effective_end_date": snapshot_window["effective_end_date"],
            "as_of_clamp_reason": snapshot_window["as_of_clamp_reason"],
            "start_boundary_kind": start_boundary_kind,
            "include_start_date_return": funded_segment_window,
            "snapshot_count": len(visible_snapshots),
            "return_observation_count": return_observation_count,
            "risk_return_observation_count": risk_return_observation_count,
            "risk_annualization_periods_per_year": periods_per_year,
            **risk_result_contract,
            "latest_complete_as_of_date": snapshot_summary["latest_complete_as_of_date"],
            "start_nav": start_nav,
            "end_nav": end_nav,
            "external_cash_in": external_cash_in,
            "external_cash_out": external_cash_out,
            "net_external_inflow": net_external_inflow,
            "cumulative_twr": cumulative_twr,
            "annualization_eligible": annualization_eligible,
            "annualization_years": (
                None if performance_is_operational else annualization.years
            ),
            "annualization_unavailable_reason": annualization_unavailable_reason,
            "annualized_twr": annualized_twr,
            "irr": irr,
            "mwror": irr,
            "irr_solver_status": irr_solver_status,
            "irr_unavailable_reason": irr_unavailable_reason,
            "absolute_change": absolute_change,
            "delta": delta,
            "realized_pnl": realized_pnl,
            "derivative_lifecycle_realized_pnl": derivative_lifecycle_realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "income_cash_amount": income_cash_amount,
            "expense_cash_amount": expense_cash_amount,
            "cash_currency_gains": cash_currency_gains,
            "pending_settlement_currency_gains": pending_settlement_currency_gains,
            "instrument_currency_gains": instrument_currency_gains,
            "return_of_capital_amount": return_of_capital_amount,
            "total_pnl": total_pnl,
            "performance_basis": (
                "operational_carrying_basis"
                if performance_is_operational
                else "market_value"
            ),
            "performance_label": (
                "Total Portfolio Operational Return"
                if performance_is_operational
                else "Total Portfolio Return"
            ),
            "ordinary_sleeve_twr_status": "unavailable",
            "ordinary_sleeve_twr_reason": (
                "Sleeve boundary cash flows are not maintained as a cash subledger; "
                "ordinary sleeve TWR is not derived by filtering total portfolio TWR."
            ),
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
            "quality_warnings": (
                corporate_action_quality_warnings(
                    _transaction_instrument_types(transactions),
                    {
                        str(transaction.get("instrument_id") or "").strip()
                        for transaction in transactions
                        if str(transaction.get("instrument_id") or "").strip()
                    },
                    transactions=transactions,
                    as_of_date=(
                        end_anchor_date
                        or cast(date | None, snapshot_window["effective_end_date"])
                    ),
                )
                + instrument_event_task_quality_warnings(
                    str(portfolio.get("portfolio_id") or "")
                )
            ),
        },
        "daily_series": [
            {
                "as_of_date": snapshot["as_of_date"],
                "coverage_state": snapshot["coverage_state"],
                "valuation_coverage_state": return_chain.snapshot_coverage_state(snapshot, "valuation"),
                "return_coverage_state": return_chain.snapshot_coverage_state(snapshot, "return"),
                "book_pnl_coverage_state": return_chain.snapshot_coverage_state(snapshot, "book_pnl"),
                "attribution_coverage_state": return_chain.snapshot_coverage_state(snapshot, "attribution"),
                "return_chain_continuous": bool(snapshot.get("return_chain_continuous")),
                "stale_price_flag": snapshot["stale_price_flag"],
                "stale_fx_flag": snapshot["stale_fx_flag"],
                "market_observation_count": snapshot.get("market_observation_count", 0),
                "return_observation_eligible": bool(snapshot.get("return_observation_eligible")),
                "return_observation_exclusion_reason": snapshot.get(
                    "return_observation_exclusion_reason"
                ),
                "performance_basis": snapshot.get("performance_basis")
                or "market_value",
                "performance_label": snapshot.get("performance_label")
                or "Total Portfolio Return",
                "beginning_nav": snapshot["beginning_nav"],
                "ending_nav": snapshot["ending_nav"],
                "pending_settlement": snapshot.get("pending_settlement"),
                "realized_pnl": snapshot.get("realized_pnl"),
                "derivative_lifecycle_realized_pnl": snapshot.get(
                    "derivative_lifecycle_realized_pnl"
                ),
                "unrealized_pnl": snapshot.get("unrealized_pnl"),
                "income_cash_amount": snapshot.get("income_cash_amount"),
                "expense_cash_amount": snapshot.get("expense_cash_amount"),
                "cash_currency_gains": snapshot.get("cash_currency_gains"),
                "pending_settlement_currency_gains": snapshot.get(
                    "pending_settlement_currency_gains"
                ),
                "instrument_currency_gains": snapshot.get("instrument_currency_gains"),
                "return_of_capital_amount": snapshot.get("return_of_capital_amount"),
                "total_pnl": period_total_pnl_to_date(snapshot),
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
    base_currency = valuation_fx.required_currency(
        portfolio.get("base_currency"), field_name="portfolio base currency"
    )
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
                "requested_start_date": start_date,
                "requested_end_date": end_date,
                "effective_start_date": None,
                "effective_end_date": None,
                "as_of_clamp_reason": None,
                "start_boundary_kind": None,
                "include_start_date_return": False,
                "coverage_state": "unavailable",
                "stale_price_flag": False,
                "stale_fx_flag": False,
                "initial_value": None,
                "final_value": None,
                "delta": None,
                "capital_gains": None,
                "realized_capital_gains": None,
                "unrealized_capital_gains": None,
                "earnings": None,
                "fees": None,
                "taxes": None,
                "cash_currency_gains": None,
                "pending_settlement_currency_gains": None,
                "instrument_currency_gains": None,
                "deposits": 0.0,
                "withdrawals": 0.0,
                "net_external_inflow": 0.0,
            },
            "lines": [],
        }

    resolved_start_date, resolved_end_date = window
    requested_resolved_end_date = end_date or resolved_end_date
    reliability_snapshots = build_daily_portfolio_snapshots(
        portfolio,
        accounts,
        transactions,
        start_date=resolved_start_date,
        end_date=resolved_end_date,
    )
    reliable_window = return_chain.resolve_reliable_snapshot_window(
        reliability_snapshots,
        requested_start_date=resolved_start_date,
        requested_end_date=requested_resolved_end_date,
        default_end_date=resolved_end_date,
    )
    reliable_end_date = cast(
        date | None, reliable_window.get("effective_end_date")
    )
    if reliable_end_date is not None:
        resolved_end_date = reliable_end_date
    sorted_transactions = sorted(transactions, key=transaction_sort_key)
    initial_boundary_date = _initial_boundary_date(
        resolved_start_date,
        start_date,
        transactions=sorted_transactions,
    )
    raw_start_snapshot = _raw_period_start_snapshot(
        portfolio,
        accounts,
        sorted_transactions,
        resolved_start_date=resolved_start_date,
    )
    start_is_close_boundary = _period_start_is_close_boundary(
        sorted_transactions,
        requested_start_date=start_date,
        resolved_start_date=resolved_start_date,
        start_snapshot=raw_start_snapshot,
    )
    starts_on_imported_anchor = _starts_on_imported_valuation_anchor(
        sorted_transactions,
        resolved_start_date=resolved_start_date,
    )
    starts_funded_segment = _snapshot_starts_funded_segment(
        raw_start_snapshot,
        resolved_start_date=resolved_start_date,
    )
    start_boundary_kind = (
        "funded_bod"
        if starts_funded_segment
        else (
            "imported_opening_eod"
            if starts_on_imported_anchor
            else "close_eod"
        )
    )
    start_boundary_transactions = (
        _transactions_as_of_end_date(
            sorted_transactions,
            end_date=initial_boundary_date,
        )
        if start_is_close_boundary
        else _build_period_start_boundary_transactions(
            sorted_transactions,
            start_date=resolved_start_date,
        )
    )
    end_boundary_transactions = _transactions_as_of_end_date(
        sorted_transactions,
        end_date=resolved_end_date,
    )
    period_transactions = _transactions_in_period(
        sorted_transactions,
        start_date=resolved_start_date,
        end_date=resolved_end_date,
        include_start_date=not start_is_close_boundary,
    )

    start_snapshot = _build_single_date_snapshot(
        portfolio,
        accounts,
        start_boundary_transactions,
        as_of_date=initial_boundary_date,
        allow_materialized=start_is_close_boundary,
    )
    end_snapshot = _build_single_date_snapshot(
        portfolio,
        accounts,
        end_boundary_transactions,
        as_of_date=resolved_end_date,
    )

    initial_value = _safe_float(start_snapshot.get("nav"))
    start_cash_currency_gains = _safe_float(start_snapshot.get("cash_currency_gains"))
    start_pending_settlement_currency_gains = _safe_float(
        start_snapshot.get("pending_settlement_currency_gains")
    )
    start_instrument_currency_gains = _safe_float(start_snapshot.get("instrument_currency_gains"))
    if not start_boundary_transactions:
        initial_value = 0.0
        start_cash_currency_gains = 0.0
        start_pending_settlement_currency_gains = 0.0
        start_instrument_currency_gains = 0.0

    final_value = _safe_float(end_snapshot.get("nav"))
    end_cash_currency_gains = _safe_float(end_snapshot.get("cash_currency_gains"))
    end_pending_settlement_currency_gains = _safe_float(
        end_snapshot.get("pending_settlement_currency_gains")
    )
    end_instrument_currency_gains = _safe_float(end_snapshot.get("instrument_currency_gains"))

    fx_payload = get_platform_fx_rates()
    direct_fx_instruments = valuation_fx.fx_direct_instrument_map(fx_payload)
    calculation_instrument_ids = _instrument_ids_for_calculation_risk_basis(
        portfolio,
        accounts,
        sorted_transactions,
        start_date=resolved_start_date,
        end_date=resolved_end_date,
    )
    loaded_instrument_details = _get_calculation_instrument_details(calculation_instrument_ids)
    instrument_detail_cache: dict[str, dict[str, object] | None] = {
        instrument_id: detail
        for instrument_id, detail in loaded_instrument_details.items()
        if isinstance(detail, dict)
    }

    transaction_buckets = _sum_period_transaction_buckets(
        period_transactions,
        base_currency=base_currency,
        direct_fx_instruments=direct_fx_instruments,
        instrument_detail_cache=instrument_detail_cache,
    )
    unrealized_capital_summary = _period_unrealized_capital_gains_by_group(
        portfolio,
        accounts,
        sorted_transactions,
        start_date=resolved_start_date,
        end_date=resolved_end_date,
        axis="instrument",
        taxonomy_id=None,
        taxonomies=None,
        taxonomy_nodes=None,
        taxonomy_assignments=None,
        base_currency=base_currency,
        direct_fx_instruments=direct_fx_instruments,
        instrument_detail_cache=instrument_detail_cache,
        start_is_close_boundary=start_is_close_boundary,
    )

    delta = None
    if initial_value is not None and final_value is not None:
        delta = final_value - initial_value - transaction_buckets["net_external_inflow"]

    cash_currency_gains = None
    if start_cash_currency_gains is not None and end_cash_currency_gains is not None:
        cash_currency_gains = end_cash_currency_gains - start_cash_currency_gains
    pending_settlement_currency_gains = None
    if (
        start_pending_settlement_currency_gains is not None
        and end_pending_settlement_currency_gains is not None
    ):
        pending_settlement_currency_gains = (
            end_pending_settlement_currency_gains
            - start_pending_settlement_currency_gains
        )
    instrument_currency_gains = None
    if start_instrument_currency_gains is not None and end_instrument_currency_gains is not None:
        instrument_currency_gains = end_instrument_currency_gains - start_instrument_currency_gains

    capital_gains = None
    if (
        delta is not None
        and cash_currency_gains is not None
        and pending_settlement_currency_gains is not None
        and instrument_currency_gains is not None
    ):
        capital_gains = (
            delta
            - transaction_buckets["earnings"]
            + transaction_buckets["fees"]
            + transaction_buckets["taxes"]
            - cash_currency_gains
            - pending_settlement_currency_gains
            - instrument_currency_gains
        )

    instrument_contribution_report = build_contribution_report(
        portfolio,
        accounts,
        sorted_transactions,
        start_date=start_date,
        end_date=resolved_end_date,
        axis="instrument",
    )
    capital_gains_by_group = {
        str(line.get("group_key") or ""): _capital_gains_from_components(line)
        for line in list(instrument_contribution_report.get("lines") or [])
        if str(line.get("group_key") or "")
    }
    period_group_keys = (
        set(capital_gains_by_group)
        | {
            str(item)
            for item in list(
                unrealized_capital_summary.get("open_group_keys") or []
            )
        }
        | {
            str(item)
            for item in list(
                unrealized_capital_summary.get("disposed_group_keys") or []
            )
        }
    )
    unrealized_capital_gains_total = 0.0
    unrealized_capital_gains_complete = True
    for period_group_key in period_group_keys:
        group_unrealized_capital_gains, group_split_complete = (
            _resolved_period_unrealized_capital_gain(
                group_key=period_group_key,
                capital_gains=capital_gains_by_group.get(period_group_key),
                unrealized_capital_summary=unrealized_capital_summary,
            )
        )
        if (
            not group_split_complete
            or group_unrealized_capital_gains is None
        ):
            unrealized_capital_gains_complete = False
            continue
        unrealized_capital_gains_total += group_unrealized_capital_gains
    unrealized_capital_gains = (
        unrealized_capital_gains_total
        if unrealized_capital_gains_complete
        else None
    )
    realized_capital_gains = (
        capital_gains - unrealized_capital_gains
        if capital_gains is not None and unrealized_capital_gains is not None
        else None
    )

    coverage_state = "complete"
    initial_boundary_complete = (
        not start_boundary_transactions
        or start_snapshot.get("coverage_state") == "complete"
    )
    if (
        not initial_boundary_complete
        or end_snapshot.get("coverage_state") != "complete"
        or not transaction_buckets["coverage_complete"]
        or not unrealized_capital_summary["coverage_complete"]
        or initial_value is None
        or final_value is None
        or capital_gains is None
        or realized_capital_gains is None
        or unrealized_capital_gains is None
    ):
        has_any_content = (
            bool(start_boundary_transactions)
            or bool(end_boundary_transactions)
            or bool(period_transactions)
            or initial_value is not None
            or final_value is not None
        )
        coverage_state = "partial" if has_any_content else "unavailable"

    stale_price_flag = bool(start_snapshot.get("stale_price_flag")) or bool(end_snapshot.get("stale_price_flag"))
    stale_fx_flag = (
        bool(start_snapshot.get("stale_fx_flag"))
        or bool(end_snapshot.get("stale_fx_flag"))
        or bool(transaction_buckets["stale_fx_flag"])
        or bool(unrealized_capital_summary.get("stale_fx_flag"))
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
            "label": "Capital Gain",
            "amount": capital_gains,
            "line_kind": "performance",
            "parent_key": None,
            "sort_order": 20,
        },
        {
            "key": "instrument_currency_gains",
            "label": "Instrument Currency Gains",
            "amount": instrument_currency_gains,
            "line_kind": "performance",
            "parent_key": None,
            "sort_order": 25,
        },
        {
            "key": "realized_capital_gains",
            "label": "Realized Gain",
            "amount": realized_capital_gains,
            "line_kind": "performance",
            "parent_key": None,
            "sort_order": 30,
        },
        {
            "key": "unrealized_capital_gains",
            "label": "Unrealized Gain",
            "amount": unrealized_capital_gains,
            "line_kind": "performance",
            "parent_key": None,
            "sort_order": 35,
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
            "key": "pending_settlement_currency_gains",
            "label": "Pending Settlement Monetary FX",
            "amount": pending_settlement_currency_gains,
            "line_kind": "performance",
            "parent_key": None,
            "sort_order": 72,
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
            "requested_start_date": start_date,
            "requested_end_date": requested_resolved_end_date,
            "effective_start_date": resolved_start_date,
            "effective_end_date": resolved_end_date,
            "as_of_clamp_reason": reliable_window.get(
                "as_of_clamp_reason"
            ),
            "start_boundary_kind": start_boundary_kind,
            "include_start_date_return": starts_funded_segment,
            "coverage_state": coverage_state,
            "stale_price_flag": stale_price_flag,
            "stale_fx_flag": stale_fx_flag,
            "initial_value": initial_value,
            "final_value": final_value,
            "delta": delta,
            "capital_gains": capital_gains,
            "realized_capital_gains": realized_capital_gains,
            "unrealized_capital_gains": unrealized_capital_gains,
            "earnings": transaction_buckets["earnings"],
            "fees": transaction_buckets["fees"],
            "taxes": transaction_buckets["taxes"],
            "cash_currency_gains": cash_currency_gains,
            "pending_settlement_currency_gains": pending_settlement_currency_gains,
            "instrument_currency_gains": instrument_currency_gains,
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
    direct_fx_instruments: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
    boundary_nav: float | None,
    option_obligations: list[dict[str, object]] | None = None,
) -> tuple[list[dict[str, object]], float | None]:
    position_lots = build_position_lots(
        portfolio_id,
        accounts,
        transactions,
        status="open",
        as_of_date=as_of_date,
        instrument_detail_cache=instrument_detail_cache,
    )
    position_buckets = holdings_market_profile.position_buckets_from_lots(position_lots)
    account_instrument_buckets = (
        holdings_market_profile.position_buckets_by_account_instrument_from_lots(
            position_lots
        )
    )
    rendered_positions: list[dict[str, object]] = []
    total_market_value_base = 0.0
    total_market_value_complete = True

    for bucket in position_buckets:
        currency = valuation_fx.required_currency(
            bucket.get("currency"), field_name="position-bucket currency"
        )
        quantity = _safe_float(bucket.get("quantity")) or 0.0
        cost_basis = _safe_float(bucket.get("cost_basis"))
        converted_cost_basis, _ = valuation_fx.convert_amount_on(
            cost_basis,
            as_of_date=as_of_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
            instrument_detail_loader=get_registry_instrument_detail,
        )
        instrument_ref = (
            bucket.get("instrument_ref")
            if isinstance(bucket.get("instrument_ref"), dict)
            else None
        )
        event_valued = holdings_market_profile.is_event_valued_instrument_ref(
            instrument_ref
        )
        detail = (
            None
            if event_valued
            else valuation_fx.instrument_detail_cache_get(
                str(bucket.get("instrument_id") or ""),
                instrument_detail_cache,
                instrument_detail_loader=get_registry_instrument_detail,
            )
        )
        price_point = (
            _select_market_point_as_of(
                detail=detail,
                role="valuation",
                as_of_date=as_of_date,
            )
            if isinstance(detail, dict)
            else None
        )
        previous_price_point = (
            _previous_market_point_for_selected_point(
                detail=detail,
                selected_point=price_point,
            )
            if isinstance(detail, dict)
            else None
        )
        return_price_point = (
            _select_market_point_as_of(
                detail=detail,
                role="total_return",
                as_of_date=as_of_date,
            )
            if isinstance(detail, dict)
            else None
        )
        previous_return_price_point = (
            _previous_market_point_for_selected_point(
                detail=detail,
                selected_point=return_price_point,
            )
            if isinstance(detail, dict)
            else None
        )
        quoted_price = _safe_float((price_point or {}).get("value"))
        previous_price = _safe_float((previous_price_point or {}).get("value"))
        last_price, market_value_local, event_valued = (
            holdings_market_profile.resolve_position_valuation(
                quantity=quantity,
                cost_basis=cost_basis,
                instrument_ref=instrument_ref,
                quoted_price=quoted_price,
                quoted_price_scale=_safe_float(
                    (price_point or {}).get("price_scale")
                ),
            )
        )
        if event_valued:
            day_change_pct, day_change_value = None, None
        else:
            day_change_pct, day_change_value = (
                holdings_market_profile.holding_day_change_metrics(
                    quantity=quantity,
                    current_price=last_price,
                    previous_price=previous_price,
                    instrument_ref=instrument_ref,
                    current_return_price=_safe_float(
                        (return_price_point or {}).get("value")
                    ),
                    previous_return_price=_safe_float(
                        (previous_return_price_point or {}).get("value")
                    ),
                    price_scale=_safe_float((price_point or {}).get("price_scale")),
                )
            )
        converted_day_change_value, _ = (
            valuation_fx.convert_amount_on(
                day_change_value,
                as_of_date=as_of_date,
                from_currency=currency,
                to_currency=base_currency,
                direct_fx_instruments=direct_fx_instruments,
                instrument_detail_cache=instrument_detail_cache,
                instrument_detail_loader=get_registry_instrument_detail,
            )
            if day_change_value is not None
            else (None, False)
        )
        converted_market_value, _ = valuation_fx.convert_amount_on(
            market_value_local,
            as_of_date=as_of_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
            instrument_detail_loader=get_registry_instrument_detail,
        )
        if market_value_local is None or converted_market_value is None:
            total_market_value_complete = False
        else:
            total_market_value_base += converted_market_value

        rendered_positions.append(
            {
                "position_id": str(bucket.get("instrument_id") or ""),
                "instrument_id": str(bucket.get("instrument_id") or ""),
                "holding_kind": "position",
                "available_for_trading": True,
                "instrument_ref": holdings_market_profile.normalize_instrument_core(
                    str(bucket.get("instrument_id") or ""),
                    instrument_ref,
                ),
                "quantity": quantity,
                "cost_basis_method": str(bucket.get("cost_basis_method") or "fifo"),
                "cost_basis": cost_basis,
                "cost_basis_base": converted_cost_basis,
                "last_price": last_price,
                "quote_as_of_date": (
                    None if event_valued else (price_point or {}).get("as_of_date")
                ),
                "quote_metric_family": (
                    None
                    if event_valued
                    else (price_point or {}).get("metric_family")
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
                    "event-cost"
                    if event_valued and converted_market_value is not None
                    else "unavailable"
                    if event_valued
                    else (price_point or {}).get("status")
                ),
                "quote_price_unit": (
                    "per_unit"
                    if event_valued
                    else (price_point or {}).get("price_unit")
                ),
                "quote_price_scale": (
                    1.0
                    if event_valued
                    else (price_point or {}).get("price_scale")
                ),
                "market_value": market_value_local,
                "market_value_base": converted_market_value,
                "carrying_value": market_value_local if event_valued else None,
                "carrying_value_base": converted_market_value if event_valued else None,
                "fair_value": None if event_valued else market_value_local,
                "fair_value_coverage_status": "unavailable" if event_valued else "complete",
                "valuation_basis": "carried_cost" if event_valued else "market_quote",
                "performance_eligible": not event_valued,
                "risk_eligible": not event_valued,
                "day_change_pct": day_change_pct,
                "day_change_value": day_change_value,
                "day_change_value_base": converted_day_change_value,
                "currency": currency,
                "portfolio_weight": None,
                "account_ids": list(bucket.get("account_ids") or []),
                "account_count": int(bucket.get("account_count") or 0),
                "open_position_lot_count": int(bucket.get("open_position_lot_count") or 0),
                "coverage_status": (
                    "event-cost"
                    if event_valued and converted_market_value is not None
                    else "price-nav-fx"
                    if converted_market_value is not None
                    else "unpriced"
                ),
            }
        )

    resolved_total_market_value_base = total_market_value_base if total_market_value_complete else None
    obligation_rows = holdings_market_profile.build_option_obligation_holding_rows(
        option_obligations,
        underlying_positions=account_instrument_buckets,
        as_of_date=as_of_date,
        base_currency=base_currency,
        nav=boundary_nav,
        convert_amount_on=partial(
            valuation_fx.convert_amount_on,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
            instrument_detail_loader=get_registry_instrument_detail,
        ),
        direct_fx_instruments=direct_fx_instruments,
        instrument_detail_cache=instrument_detail_cache,
        normalize_instrument=holdings_market_profile.normalize_instrument_core,
    )
    # Keep the returned position market value as the positive asset component;
    # callers subtract the separately disclosed liability when constructing NAV.
    rendered_positions.extend(obligation_rows)
    holdings_market_profile.apply_position_portfolio_weights(rendered_positions, boundary_nav)

    rendered_positions.sort(
        key=lambda item: (
            -((_safe_float(item.get("market_value_base")) or 0.0)),
            str(item.get("instrument_id") or ""),
        )
    )
    return rendered_positions, resolved_total_market_value_base


def _pending_monetary_balances_from_postings(
    *,
    postings: list[dict[str, object]],
    as_of_date: date,
    base_currency: str,
    direct_fx_instruments: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
) -> list[dict[str, object]]:
    """Build cash-account settlement subledger balances for Holdings."""

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
        economic_instrument_id = str(
            posting.get("instrument_id") or ""
        ).strip()
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
                "amount": 0.0,
                "amount_base": 0.0,
                "amount_base_complete": True,
                "transaction_ids": set(),
            },
        )
        bucket["amount"] = (_safe_float(bucket.get("amount")) or 0.0) + amount
        converted_amount, _ = valuation_fx.convert_amount_on(
            amount,
            as_of_date=as_of_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
            instrument_detail_loader=get_registry_instrument_detail,
        )
        if converted_amount is None:
            bucket["amount_base_complete"] = False
        else:
            bucket["amount_base"] = (
                _safe_float(bucket.get("amount_base")) or 0.0
            ) + converted_amount
        transaction_id = str(posting.get("transaction_id") or "").strip()
        if transaction_id:
            transaction_ids = bucket.get("transaction_ids")
            if isinstance(transaction_ids, set):
                transaction_ids.add(transaction_id)

    rendered: list[dict[str, object]] = []
    for bucket in grouped.values():
        amount = _safe_float(bucket.get("amount")) or 0.0
        if abs(amount) <= 1e-9:
            continue
        transaction_ids = bucket.pop("transaction_ids", set())
        amount_base_complete = bool(
            bucket.pop("amount_base_complete", False)
        )
        rendered.append(
            {
                **bucket,
                "amount_base": (
                    bucket.get("amount_base")
                    if amount_base_complete
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


def _statement_cash_nav_components(
    *,
    portfolio_id: str,
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    as_of_date: date,
    base_currency: str,
    direct_fx_instruments: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
) -> dict[str, object]:
    postings = derive_ledger_postings(
        portfolio_id,
        transactions,
        account_cost_methods=_account_cost_methods(accounts),
        account_currency_map=_account_currency_map(accounts),
        as_of_date=as_of_date,
    )
    as_of_iso = as_of_date.isoformat()
    cash_balance_base = 0.0
    pending_settlement_base = 0.0
    cash_complete = True
    pending_settlement_complete = True
    cash_balances_by_currency: dict[str, float] = defaultdict(float)
    cash_balance_base_by_currency: dict[str, float] = defaultdict(float)
    cash_balance_complete_by_currency: dict[str, bool] = defaultdict(lambda: True)
    cash_account_ids_by_currency: dict[str, set[str]] = defaultdict(set)
    for posting in postings:
        pending_amount_delta = ledger_posting_pending_amount_as_of(
            posting,
            as_of_date,
        )
        if pending_amount_delta is not None:
            if abs(pending_amount_delta) <= 1e-9:
                continue
            posting_currency = valuation_fx.required_currency(
                posting.get("currency"),
                field_name="ledger-posting currency",
            )
            converted_pending_delta, _ = valuation_fx.convert_amount_on(
                pending_amount_delta,
                as_of_date=as_of_date,
                from_currency=posting_currency,
                to_currency=base_currency,
                direct_fx_instruments=direct_fx_instruments,
                instrument_detail_cache=instrument_detail_cache,
                instrument_detail_loader=get_registry_instrument_detail,
            )
            if converted_pending_delta is None:
                pending_settlement_complete = False
            else:
                pending_settlement_base += converted_pending_delta
            continue

        cash_delta = _safe_float(posting.get("cash_amount_delta"))
        if cash_delta is None:
            continue
        posting_currency = valuation_fx.required_currency(
            posting.get("currency"), field_name="ledger-posting currency"
        )
        posting_effective_date = ledger_posting_effective_date_iso(posting)
        posting_account_id = str(posting.get("account_id") or "").strip()
        converted_cash_delta, _ = valuation_fx.convert_amount_on(
            cash_delta,
            as_of_date=as_of_date,
            from_currency=posting_currency,
            to_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
            instrument_detail_loader=get_registry_instrument_detail,
        )
        if converted_cash_delta is None:
            if posting_effective_date <= as_of_iso:
                cash_complete = False
                cash_balances_by_currency[posting_currency] += cash_delta
                cash_balance_complete_by_currency[posting_currency] = False
                if posting_account_id:
                    cash_account_ids_by_currency[posting_currency].add(posting_account_id)
            else:
                pending_settlement_complete = False
            continue
        if posting_effective_date <= as_of_iso:
            cash_balances_by_currency[posting_currency] += cash_delta
            cash_balance_base_by_currency[posting_currency] += converted_cash_delta
            if posting_account_id:
                cash_account_ids_by_currency[posting_currency].add(posting_account_id)
            cash_balance_base += converted_cash_delta
        else:
            pending_settlement_base += converted_cash_delta

    return {
        "cash_balance_base": cash_balance_base if cash_complete else None,
        "pending_settlement_base": pending_settlement_base if pending_settlement_complete else None,
        "cash_balances": [
            {
                "currency": currency,
                "amount": amount,
                "amount_base": (
                    cash_balance_base_by_currency[currency]
                    if cash_balance_complete_by_currency[currency]
                    else None
                ),
                "account_ids": sorted(cash_account_ids_by_currency[currency]),
            }
            for currency, amount in sorted(cash_balances_by_currency.items())
            if abs(amount) > 1e-9
        ],
        "pending_balances": _pending_monetary_balances_from_postings(
            postings=postings,
            as_of_date=as_of_date,
            base_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
        ),
    }


def _converted_option_liability_base(
    obligations: list[dict[str, object]],
    *,
    as_of_date: date,
    base_currency: str,
    direct_fx_instruments: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
) -> float | None:
    total = 0.0
    for obligation in obligations:
        liability = _safe_float(obligation.get("carrying_liability")) or 0.0
        currency = valuation_fx.required_currency(
            obligation.get("contract_currency") or base_currency,
            field_name="option obligation currency",
        )
        converted, _ = valuation_fx.convert_amount_on(
            liability,
            as_of_date=as_of_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
            instrument_detail_loader=get_registry_instrument_detail,
        )
        if converted is None:
            return None
        total += converted
    return total


def build_holdings_report(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    as_of_date: date,
    include_cash_rows: bool = False,
    calculation_frequency: CalculationFrequency = "daily",
    instrument_detail_cache: dict[str, dict[str, object] | None] | None = None,
) -> dict[str, object]:
    base_currency = valuation_fx.required_currency(
        portfolio.get("base_currency"), field_name="portfolio base currency"
    )
    valuation_timezone = _resolve_portfolio_valuation_timezone(portfolio)
    valuation_cutoff_policy = _resolve_portfolio_valuation_cutoff_policy(portfolio)
    sorted_transactions = sorted(transactions, key=transaction_sort_key)
    boundary_transactions = _transactions_as_of_end_date(
        sorted_transactions,
        end_date=as_of_date,
    )
    ledger_boundary_transactions = (
        _transactions_with_ledger_activity_as_of_end_date(
            sorted_transactions,
            end_date=as_of_date,
        )
    )

    fx_payload = get_platform_fx_rates()
    direct_fx_instruments = valuation_fx.fx_direct_instrument_map(fx_payload)
    resolved_instrument_detail_cache = (
        instrument_detail_cache if instrument_detail_cache is not None else {}
    )
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
    positions, position_market_value_base = _build_boundary_holding_records(
        portfolio_id=str(portfolio.get("portfolio_id") or ""),
        accounts=accounts,
        transactions=boundary_transactions,
        as_of_date=as_of_date,
        base_currency=base_currency,
        direct_fx_instruments=direct_fx_instruments,
        instrument_detail_cache=resolved_instrument_detail_cache,
        boundary_nav=None,
        option_obligations=open_option_obligation_rows,
    )
    cash_components = _statement_cash_nav_components(
        portfolio_id=str(portfolio.get("portfolio_id") or ""),
        accounts=accounts,
        transactions=ledger_boundary_transactions,
        as_of_date=as_of_date,
        base_currency=base_currency,
        direct_fx_instruments=direct_fx_instruments,
        instrument_detail_cache=resolved_instrument_detail_cache,
    )
    cash_balance_base = _safe_float(cash_components.get("cash_balance_base"))
    pending_settlement_base = _safe_float(cash_components.get("pending_settlement_base"))
    derivative_liability_base = _converted_option_liability_base(
        open_option_obligation_rows,
        as_of_date=as_of_date,
        base_currency=base_currency,
        direct_fx_instruments=direct_fx_instruments,
        instrument_detail_cache=resolved_instrument_detail_cache,
    )
    total_nav_base = (
        cash_balance_base
        + pending_settlement_base
        + position_market_value_base
        - derivative_liability_base
        if (
            cash_balance_base is not None
            and pending_settlement_base is not None
            and position_market_value_base is not None
            and derivative_liability_base is not None
        )
        else None
    )
    if include_cash_rows:
        positions = [
            *positions,
            *holdings_market_profile.build_cash_holding_rows(
                cash_balances=list(cash_components.get("cash_balances") or []),
                as_of_date=as_of_date,
                base_currency=base_currency,
                direct_fx_instruments=direct_fx_instruments,
                instrument_detail_cache=resolved_instrument_detail_cache,
                cash_day_change=partial(
                    holdings_market_profile.cash_day_change_metrics,
                    resolve_fx_rate_on=partial(
                        valuation_fx.resolve_fx_rate_on,
                        instrument_detail_loader=get_registry_instrument_detail,
                    ),
                    resolve_previous_fx_rate_before=partial(
                        valuation_fx.resolve_previous_fx_rate_before,
                        instrument_detail_loader=get_registry_instrument_detail,
                    ),
                ),
            ),
            *holdings_market_profile.build_pending_monetary_holding_rows(
                pending_balances=list(cash_components.get("pending_balances") or []),
                as_of_date=as_of_date,
                base_currency=base_currency,
                direct_fx_instruments=direct_fx_instruments,
                instrument_detail_cache=resolved_instrument_detail_cache,
                cash_day_change=partial(
                    holdings_market_profile.cash_day_change_metrics,
                    resolve_fx_rate_on=partial(
                        valuation_fx.resolve_fx_rate_on,
                        instrument_detail_loader=get_registry_instrument_detail,
                    ),
                    resolve_previous_fx_rate_before=partial(
                        valuation_fx.resolve_previous_fx_rate_before,
                        instrument_detail_loader=get_registry_instrument_detail,
                    ),
                ),
            ),
        ]
    holdings_market_profile.apply_position_portfolio_weights(positions, total_nav_base)
    total_market_value_base = (
        (position_market_value_base + cash_balance_base + pending_settlement_base - derivative_liability_base)
        if (
            include_cash_rows
            and position_market_value_base is not None
            and cash_balance_base is not None
            and pending_settlement_base is not None
            and derivative_liability_base is not None
        )
        else (
            position_market_value_base - derivative_liability_base
            if position_market_value_base is not None
            and derivative_liability_base is not None
            else None
        )
    )
    return {
        "portfolio_id": str(portfolio.get("portfolio_id") or ""),
        "portfolio_name": str(portfolio.get("portfolio_name") or portfolio.get("portfolio_id") or ""),
        "base_currency": base_currency,
        "valuation_timezone": valuation_timezone,
        "valuation_cutoff_policy": valuation_cutoff_policy,
        "as_of_date": as_of_date,
        "positions": positions,
        "total_market_value_base": total_market_value_base,
        "position_market_value_base": position_market_value_base,
        "cash_balance_base": cash_balance_base,
        "pending_settlement_base": pending_settlement_base,
        "derivative_liability_base": derivative_liability_base,
        "open_option_obligation_count": len(open_option_obligation_rows),
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
            if str(position.get("instrument_id") or "") == resolved_group_key
        ]
        group_label = None
        if filtered_positions:
            instrument_ref = (
                filtered_positions[0].get("instrument_ref")
                if isinstance(filtered_positions[0].get("instrument_ref"), dict)
                else {}
            )
            group_label = str(instrument_ref.get("instrument_name") or resolved_group_key)
        return filtered_positions, group_label

    if resolved_axis == "account":
        filtered_positions = [
            position
            for position in positions
            if resolved_group_key in list(position.get("account_ids") or [])
        ]
        group_label = (account_name_map or {}).get(resolved_group_key)
        return filtered_positions, group_label

    if resolved_axis == "instrument_type":
        filtered_positions = [
            position
            for position in positions
            if attribution.instrument_type_key_label(attribution.instrument_ref_from_mapping(position).get("instrument_type"))[0] == resolved_group_key
        ]
        group_label = attribution.instrument_type_key_label(
            attribution.instrument_ref_from_mapping(filtered_positions[0]).get("instrument_type")
            if filtered_positions
            else resolved_group_key
        )[1]
        return filtered_positions, group_label

    if resolved_axis == "currency":
        filtered_positions = [
            position
            for position in positions
            if valuation_fx.required_currency(
                position.get("currency"), field_name="position currency"
            ) == resolved_group_key
        ]
        return filtered_positions, resolved_group_key

    if resolved_axis != "taxonomy":
        raise ValueError(attribution.CONTRIBUTION_AXIS_ERROR)
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
        instrument_id = str(position.get("instrument_id") or "")
        if not instrument_id:
            continue
        position_group_key, position_group_label = attribution.resolve_taxonomy_group_for_date(
            taxonomy=taxonomy,
            taxonomy_nodes_by_id=taxonomy_nodes_by_id,
            assignments_by_entity=assignments_by_entity,
            target_entity_id=instrument_id,
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
    base_currency = valuation_fx.required_currency(
        portfolio.get("base_currency"), field_name="portfolio base currency"
    )
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
    resolved_axis = str(axis or "").strip() or None
    if resolved_axis not in (attribution.CONTRIBUTION_AXES | {None}):
        raise ValueError(attribution.CONTRIBUTION_AXIS_ERROR)
    resolved_taxonomy_id = str(taxonomy_id or "").strip()
    resolved_group_key = str(group_key or "").strip()
    account_name_map = {
        str(account.get("account_id") or ""): str(account.get("account_name") or account.get("account_id") or "")
        for account in accounts
        if str(account.get("account_id") or "")
    }
    sorted_transactions = sorted(transactions, key=transaction_sort_key)
    initial_boundary_date = _initial_boundary_date(
        resolved_start_date,
        start_date,
        transactions=sorted_transactions,
    )
    raw_start_snapshot = _raw_period_start_snapshot(
        portfolio,
        accounts,
        sorted_transactions,
        resolved_start_date=resolved_start_date,
    )
    start_is_close_boundary = _period_start_is_close_boundary(
        sorted_transactions,
        requested_start_date=start_date,
        resolved_start_date=resolved_start_date,
        start_snapshot=raw_start_snapshot,
    )
    start_boundary_transactions = (
        _transactions_as_of_end_date(
            sorted_transactions,
            end_date=initial_boundary_date,
        )
        if start_is_close_boundary
        else _build_period_start_boundary_transactions(
            sorted_transactions,
            start_date=resolved_start_date,
        )
    )
    end_boundary_transactions = _transactions_as_of_end_date(
        sorted_transactions,
        end_date=resolved_end_date,
    )

    fx_payload = get_platform_fx_rates()
    direct_fx_instruments = valuation_fx.fx_direct_instrument_map(fx_payload)
    instrument_detail_cache: dict[str, dict[str, object] | None] = {}
    start_snapshot = _build_single_date_snapshot(
        portfolio,
        accounts,
        start_boundary_transactions,
        as_of_date=initial_boundary_date,
        allow_materialized=start_is_close_boundary,
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
        direct_fx_instruments=direct_fx_instruments,
        instrument_detail_cache=instrument_detail_cache,
        boundary_nav=_safe_float(start_snapshot.get("nav")),
    )
    end_positions, end_total_market_value_base = _build_boundary_holding_records(
        portfolio_id=str(portfolio.get("portfolio_id") or ""),
        accounts=accounts,
        transactions=end_boundary_transactions,
        as_of_date=resolved_end_date,
        base_currency=base_currency,
        direct_fx_instruments=direct_fx_instruments,
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
    instrument_ids_by_group: dict[str, set[str]] = defaultdict(set)
    for position in positions:
        instrument_id = str(position.get("instrument_id") or "")
        if not instrument_id:
            continue
        group_key, group_label = attribution.resolve_taxonomy_group_for_date(
            taxonomy=taxonomy,
            taxonomy_nodes_by_id=taxonomy_nodes_by_id,
            assignments_by_entity=assignments_by_entity,
            target_entity_id=instrument_id,
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
                "instrument_count": 0,
                "cost_basis_base": 0.0,
                "market_value_base": 0.0,
                "unrealized_pnl": 0.0,
                "portfolio_weight": 0.0,
                "open_position_lot_count": 0,
            },
        )
        group["position_count"] = int(group.get("position_count") or 0) + 1
        instrument_ids_by_group[group_key].add(instrument_id)
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
        group["instrument_count"] = len(instrument_ids_by_group[group_key])
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
    direct_fx_instruments: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
) -> tuple[float | None, bool]:
    if not isinstance(local_amounts_by_currency, dict) or not local_amounts_by_currency:
        return 0.0, False

    gain = 0.0
    stale_fx_flag = False
    coverage_complete = True
    for raw_currency, raw_amount in local_amounts_by_currency.items():
        currency = valuation_fx.required_currency(
            raw_currency, field_name="local-amount currency"
        )
        if currency == base_currency:
            continue
        amount = _safe_float(raw_amount)
        if amount is None or abs(amount) <= 1e-9:
            continue
        previous_value, previous_stale = valuation_fx.convert_amount_on(
            amount,
            as_of_date=previous_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
            instrument_detail_loader=get_registry_instrument_detail,
        )
        current_value, current_stale = valuation_fx.convert_amount_on(
            amount,
            as_of_date=current_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
            instrument_detail_loader=get_registry_instrument_detail,
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
    return build_return_calendar_report_from_performance_report(
        report,
        frequency=frequency,
    )


def build_return_calendar_report_from_performance_report(
    report: dict[str, object],
    *,
    frequency: str = "monthly",
) -> dict[str, object]:
    """Roll an already-authoritative Performance window into calendar buckets."""

    report_summary = (
        report.get("summary") if isinstance(report.get("summary"), dict) else {}
    )
    report_start_boundary_date = _parse_iso_date(
        report_summary.get("effective_start_date")
        or report_summary.get("start_date")
    )
    ordered_points = sorted(
        (
            point
            for point in list(report.get("daily_series") or [])
            if isinstance(point.get("as_of_date"), date)
        ),
        key=lambda point: cast(date, point["as_of_date"]),
    )
    buckets_by_key: dict[str, dict[str, object]] = {}
    ordered_keys: list[str] = []
    initial_close_anchor: dict[str, object] | None = None
    for point_index, point in enumerate(ordered_points):
        as_of_date = cast(date, point["as_of_date"])
        beginning_nav = _safe_float(point.get("beginning_nav"))
        ending_nav = _safe_float(point.get("ending_nav"))
        summary_start_nav = _safe_float(report_summary.get("start_nav"))
        cumulative_twr = _safe_float(point.get("cumulative_twr"))
        has_close_anchor_shape = bool(
            ending_nav is not None
            and (
                (
                    beginning_nav is not None
                    and abs(beginning_nav - ending_nav) <= 1e-9
                )
                or (
                    cumulative_twr is not None
                    and abs(cumulative_twr) <= 1e-12
                    and summary_start_nav is not None
                    and abs(summary_start_nav - ending_nav) <= 1e-9
                )
            )
        )
        is_initial_close_anchor = bool(
            point_index == 0
            and report_start_boundary_date == as_of_date
            and _safe_float(point.get("daily_twr")) is None
            and has_close_anchor_shape
        )
        if is_initial_close_anchor:
            # The start close supplies the first bucket's opening boundary but
            # is not itself a return observation or a calendar bucket.
            initial_close_anchor = point
            continue

        bucket_key = _calendar_bucket_key(as_of_date, frequency)
        bucket = buckets_by_key.get(bucket_key)
        if bucket is None:
            is_first_bucket = not ordered_keys
            start_boundary_date = (
                cast(date, initial_close_anchor["as_of_date"])
                if is_first_bucket and initial_close_anchor is not None
                else (
                    as_of_date
                    if is_first_bucket
                    else as_of_date - timedelta(days=1)
                )
            )
            start_nav = (
                initial_close_anchor.get("ending_nav")
                if is_first_bucket and initial_close_anchor is not None
                else point.get("beginning_nav")
            )
            start_boundary_complete = (
                return_chain.snapshot_coverage_state(
                    initial_close_anchor, "valuation"
                )
                == "complete"
                if is_first_bucket and initial_close_anchor is not None
                else start_nav is not None
            )
            bucket = {
                "bucket_key": bucket_key,
                "frequency": frequency,
                "start_date": start_boundary_date,
                "end_date": as_of_date,
                "coverage_state": "unavailable",
                "observation_count": 0,
                "start_nav": start_nav,
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
                "_dates": [],
                "_return_points_complete": True,
                "_start_boundary_complete": start_boundary_complete,
                "_end_boundary_complete": False,
                "_absolute_change_complete": True,
                "_delta_complete": True,
            }
            buckets_by_key[bucket_key] = bucket
            ordered_keys.append(bucket_key)

        bucket["end_date"] = as_of_date
        bucket["end_nav"] = point.get("ending_nav")
        bucket["_dates"].append(as_of_date)
        bucket["_end_boundary_complete"] = (
            return_chain.snapshot_coverage_state(point, "valuation") == "complete"
            and _safe_float(point.get("ending_nav")) is not None
        )
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
        point_return_complete = (
            return_chain.snapshot_coverage_state(point, "return") == "complete"
            and daily_twr is not None
            and isfinite(daily_twr)
        )
        if point_return_complete:
            bucket["_growth_index"] *= 1.0 + daily_twr
            bucket["_has_return"] = True
            bucket["observation_count"] += 1
        else:
            bucket["_return_points_complete"] = False

        coverage_state = str(point.get("coverage_state") or "unavailable")
        if coverage_state == "complete":
            bucket["_seen_complete"] = True
        elif coverage_state == "partial":
            bucket["_seen_partial"] = True
        else:
            bucket["_seen_unavailable"] = True

    rendered_buckets: list[dict[str, object]] = []
    report_effective_end_date = _parse_iso_date(
        report_summary.get("effective_end_date")
    )
    for bucket_index, bucket_key in enumerate(ordered_keys):
        bucket = buckets_by_key[bucket_key]
        bucket_dates = list(bucket["_dates"])
        dates_are_contiguous = bool(
            bucket_dates
            and all(
                point_date == bucket_dates[0] + timedelta(days=index)
                for index, point_date in enumerate(bucket_dates)
            )
        )
        calendar_boundaries_complete = True
        if frequency == "monthly" and bucket_dates:
            first_bucket_date = cast(date, bucket_dates[0])
            month_start = first_bucket_date.replace(day=1)
            next_month_start = (
                date(first_bucket_date.year + 1, 1, 1)
                if first_bucket_date.month == 12
                else date(first_bucket_date.year, first_bucket_date.month + 1, 1)
            )
            month_end = next_month_start - timedelta(days=1)
            is_latest_bucket = bucket_index == len(ordered_keys) - 1
            expected_end = (
                report_effective_end_date
                if (
                    is_latest_bucket
                    and report_effective_end_date is not None
                    and month_start <= report_effective_end_date <= month_end
                )
                else month_end
            )
            calendar_boundaries_complete = bool(
                bucket_dates[0] == month_start
                and bucket_dates[-1] == expected_end
            )
        period_return_complete = bool(
            calendar_boundaries_complete
            and dates_are_contiguous
            and bucket["_return_points_complete"]
            and bucket["_start_boundary_complete"]
            and bucket["_end_boundary_complete"]
            and bucket["_has_return"]
        )
        coverage_state = (
            "complete"
            if period_return_complete
            else ("partial" if bucket_dates else "unavailable")
        )
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
                "cumulative_twr": (
                    bucket["_growth_index"] - 1.0
                    if period_return_complete
                    else None
                ),
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
            "derivative_liability_base": 0.0,
            "open_cost_basis_base": 0.0,
            "unrealized_pnl": 0.0,
            "ending_value_base": 0.0,
            "_position_market_value_local_by_currency": defaultdict(float),
            "_cash_balance_local_by_currency": defaultdict(float),
            "_pending_settlement_local_by_currency": defaultdict(float),
            "_cash_complete": True,
            "_pending_settlement_complete": True,
            "_position_complete": True,
            "_cost_complete": True,
            "_liability_complete": True,
            "_market_observation_instrument_ids": set(),
            "_has_event_valued_exposure": False,
            "_has_derivative_liability_exposure": False,
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
    direct_fx_instruments: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
) -> dict[str, dict[str, object]]:
    states: dict[str, dict[str, object]] = {}
    resolved_position_lots = (
        position_lots
        if position_lots is not None
        else build_position_lots(
            portfolio_id,
            accounts,
            transactions_as_of,
            as_of_date=as_of_date,
        )
    )
    open_position_lots = [
        position_lot for position_lot in resolved_position_lots if position_lot.get("status") == "open"
    ]

    option_obligations = build_option_obligations(
        transactions_as_of,
        as_of_date=as_of_date,
    )
    open_option_obligations = [
        row
        for row in option_obligations
        if str(row.get("status") or "") == "open"
        and (_safe_float(row.get("remaining_quantity")) or 0.0) > 1e-9
    ]
    for obligation in open_option_obligations:
        pseudo_lot = {
            "account_id": obligation.get("account_id"),
            "instrument_id": obligation.get("option_instrument_id")
            or obligation.get("instrument_id"),
            "instrument_ref": obligation.get("instrument_ref"),
            "currency": obligation.get("contract_currency") or base_currency,
        }
        group_key, group_label = attribution.position_group_for_axis(
            axis=axis,
            position_lot=pseudo_lot,
            account_name_map=account_name_map,
            base_currency=base_currency,
        )
        if not group_key:
            continue
        state = _ensure_contribution_group_state(
            states,
            group_key=group_key,
            group_label=group_label,
            axis=axis,
        )
        state["_has_derivative_liability_exposure"] = True
        currency = valuation_fx.required_currency(
            obligation.get("contract_currency") or base_currency,
            field_name="option obligation currency",
        )
        liability = _safe_float(obligation.get("carrying_liability")) or 0.0
        converted_liability, liability_fx_stale = valuation_fx.convert_amount_on(
            liability,
            as_of_date=as_of_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
            instrument_detail_loader=get_registry_instrument_detail,
        )
        if converted_liability is None:
            state["_liability_complete"] = False
        else:
            state["derivative_liability_base"] = (
                _safe_float(state.get("derivative_liability_base")) or 0.0
            ) + converted_liability
            state["stale_fx_flag"] = bool(state.get("stale_fx_flag")) or liability_fx_stale

    for position_lot in open_position_lots:
        group_key, group_label = attribution.position_group_for_axis(
            axis=axis,
            position_lot=position_lot,
            account_name_map=account_name_map,
            base_currency=base_currency,
        )
        if not group_key:
            continue
        state = _ensure_contribution_group_state(
            states,
            group_key=group_key,
            group_label=group_label,
            axis=axis,
        )
        currency = valuation_fx.required_currency(
            position_lot.get("currency"), field_name="position-lot currency"
        )
        remaining_cost_basis = _safe_float(position_lot.get("remaining_cost_basis")) or 0.0
        converted_cost_basis, cost_basis_fx_stale = valuation_fx.convert_amount_on(
            remaining_cost_basis,
            as_of_date=as_of_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
            instrument_detail_loader=get_registry_instrument_detail,
        )
        if converted_cost_basis is None:
            state["_cost_complete"] = False
        else:
            state["open_cost_basis_base"] = (_safe_float(state.get("open_cost_basis_base")) or 0.0) + converted_cost_basis
            state["stale_fx_flag"] = bool(state.get("stale_fx_flag")) or cost_basis_fx_stale

        instrument_ref = (
            position_lot.get("instrument_ref")
            if isinstance(position_lot.get("instrument_ref"), dict)
            else None
        )
        event_valued = holdings_market_profile.is_event_valued_instrument_ref(
            instrument_ref
        )
        if event_valued:
            state["_has_event_valued_exposure"] = True
        detail = (
            None
            if event_valued
            else valuation_fx.instrument_detail_cache_get(
                str(position_lot.get("instrument_id") or ""),
                instrument_detail_cache,
                instrument_detail_loader=get_registry_instrument_detail,
            )
        )
        price_point = (
            _select_market_point_as_of(
                detail=detail,
                role="valuation",
                as_of_date=as_of_date,
            )
            if isinstance(detail, dict)
            else None
        )
        price_point_date = _parse_iso_date((price_point or {}).get("as_of_date"))
        if price_point_date == as_of_date:
            observed_instrument_ids = state.get("_market_observation_instrument_ids")
            if isinstance(observed_instrument_ids, set):
                observed_instrument_ids.add(str(position_lot.get("instrument_id") or ""))
        _last_price, market_value_local, event_valued = (
            holdings_market_profile.resolve_position_valuation(
                quantity=_safe_float(position_lot.get("remaining_quantity")) or 0.0,
                cost_basis=remaining_cost_basis,
                instrument_ref=instrument_ref,
                quoted_price=_safe_float((price_point or {}).get("value")),
                quoted_price_scale=_safe_float(
                    (price_point or {}).get("price_scale")
                ),
            )
        )
        converted_market_value, valuation_fx_stale = valuation_fx.convert_amount_on(
            market_value_local,
            as_of_date=as_of_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
            instrument_detail_loader=get_registry_instrument_detail,
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

    if attribution.axis_includes_cash_balance(axis):
        resolved_postings = (
            postings
            if postings is not None
            else derive_ledger_postings(
                portfolio_id,
                transactions_as_of,
                account_cost_methods=account_cost_methods,
                account_currency_map=account_currency_map,
                as_of_date=as_of_date,
            )
        )
        for posting in resolved_postings:
            pending_amount_delta = ledger_posting_pending_amount_as_of(
                posting,
                as_of_date,
            )
            if pending_amount_delta is not None:
                if abs(pending_amount_delta) <= 1e-9:
                    continue
                cash_amount_delta = pending_amount_delta
                posting_is_settled = False
            else:
                cash_amount_delta = _safe_float(posting.get("cash_amount_delta"))
                if cash_amount_delta is None:
                    continue
                posting_is_settled = (
                    ledger_posting_effective_date_iso(posting)
                    <= as_of_date.isoformat()
                )
            posting_currency = valuation_fx.required_currency(
                posting.get("currency"), field_name="ledger-posting currency"
            )
            if (
                pending_amount_delta is not None
                and str(posting.get("posting_role") or "")
                == "position_recognition_bridge"
            ):
                group_key, group_label = attribution.transaction_group_for_axis(
                    axis=axis,
                    transaction=posting,
                    account_name_map=account_name_map,
                    base_currency=base_currency,
                )
            else:
                group_key, group_label = attribution.cash_group_for_axis(
                    axis=axis,
                    account_id=str(posting.get("account_id") or ""),
                    currency=posting_currency,
                    account_name_map=account_name_map,
                )
            if not group_key:
                continue
            state = _ensure_contribution_group_state(
                states,
                group_key=group_key,
                group_label=group_label,
                axis=axis,
            )
            local_balance_field = (
                "_cash_balance_local_by_currency"
                if posting_is_settled
                else "_pending_settlement_local_by_currency"
            )
            local_balances_by_currency = state.get(local_balance_field)
            if isinstance(local_balances_by_currency, (dict, defaultdict)):
                local_balances_by_currency[posting_currency] += cash_amount_delta

            converted_cash_delta, is_stale = valuation_fx.convert_amount_on(
                cash_amount_delta,
                as_of_date=as_of_date,
                from_currency=posting_currency,
                to_currency=base_currency,
                direct_fx_instruments=direct_fx_instruments,
                instrument_detail_cache=instrument_detail_cache,
                instrument_detail_loader=get_registry_instrument_detail,
            )
            if converted_cash_delta is None:
                if posting_is_settled:
                    state["_cash_complete"] = False
                else:
                    state["_pending_settlement_complete"] = False
                continue
            if posting_is_settled:
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
        if not state.get("_liability_complete"):
            state["derivative_liability_base"] = None
        if attribution.axis_includes_cash_balance(axis) and not state.get("_cash_complete"):
            state["cash_balance_base"] = None
        if attribution.axis_includes_cash_balance(axis) and not state.get("_pending_settlement_complete"):
            state["pending_settlement_base"] = None

        observed_instrument_ids = state.pop("_market_observation_instrument_ids", set())
        state["market_observation_count"] = (
            len([instrument_id for instrument_id in observed_instrument_ids if instrument_id])
            if isinstance(observed_instrument_ids, set)
            else 0
        )

        position_market_value_base = _safe_float(state.get("position_market_value_base"))
        open_cost_basis_base = _safe_float(state.get("open_cost_basis_base"))
        cash_balance_base = _safe_float(state.get("cash_balance_base"))
        pending_settlement_base = _safe_float(state.get("pending_settlement_base"))
        derivative_liability_base = _safe_float(state.get("derivative_liability_base"))

        if position_market_value_base is not None and open_cost_basis_base is not None:
            state["unrealized_pnl"] = position_market_value_base - open_cost_basis_base
        else:
            state["unrealized_pnl"] = None

        if attribution.axis_includes_cash_balance(axis):
            state["ending_value_base"] = (
                cash_balance_base
                + pending_settlement_base
                + position_market_value_base
                - (derivative_liability_base or 0.0)
                if (
                    cash_balance_base is not None
                    and pending_settlement_base is not None
                    and position_market_value_base is not None
                    and derivative_liability_base is not None
                )
                else None
            )
        else:
            state["ending_value_base"] = (
                position_market_value_base - derivative_liability_base
                if position_market_value_base is not None
                and derivative_liability_base is not None
                else None
            )

        state.pop("_pending_settlement_complete", None)
        state.pop("_cash_complete", None)
        state.pop("_position_complete", None)
        state.pop("_cost_complete", None)
        state.pop("_liability_complete", None)

    return states


def _build_contribution_daily_events(
    *,
    axis: str,
    as_of_date: date,
    position_lots: list[dict[str, object]],
    transactions_on_date: list[dict[str, object]],
    transactions_as_of: list[dict[str, object]] | None = None,
    base_currency: str,
    account_name_map: dict[str, str],
    direct_fx_instruments: dict[tuple[str, str], str],
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
                "pending_settlement_currency_gains": 0.0,
                "instrument_currency_gains": 0.0,
                attribution.GROUP_CAPITAL_FLOW_IN_FIELD: 0.0,
                attribution.GROUP_CAPITAL_FLOW_OUT_FIELD: 0.0,
                attribution.GROUP_CAPITAL_FLOW_IN_EOD_FIELD: 0.0,
                attribution.GROUP_CAPITAL_FLOW_OUT_EOD_FIELD: 0.0,
                "_derivative_lifecycle_activity": False,
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
        converted_amount, _ = valuation_fx.convert_amount_on(
            amount,
            as_of_date=trade_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
            instrument_detail_loader=get_registry_instrument_detail,
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

    def add_flow(
        *,
        group_key: str,
        group_label: str,
        field_name: str,
        amount: float,
        trade_date: date,
        currency: str,
    ) -> None:
        if not group_key or amount <= 1e-9:
            return
        add_amount(
            group_key=group_key,
            group_label=group_label,
            field_name=field_name,
            amount=amount,
            trade_date=trade_date,
            currency=currency,
        )

    def cash_group(account_id: object, currency: str) -> tuple[str, str]:
        resolved_account_id = str(account_id or "").strip()
        if not resolved_account_id:
            return ("", "")
        return attribution.cash_group_for_axis(
            axis=axis,
            account_id=resolved_account_id,
            currency=currency,
            account_name_map=account_name_map,
        )

    def add_transfer_flow(
        *,
        source_group_key: str,
        source_group_label: str,
        target_group_key: str,
        target_group_label: str,
        amount: float,
        trade_date: date,
        currency: str,
        eod: bool = False,
    ) -> None:
        if amount <= 1e-9:
            return
        if source_group_key and target_group_key and source_group_key == target_group_key:
            return
        add_flow(
            group_key=source_group_key,
            group_label=source_group_label,
            field_name=attribution.GROUP_CAPITAL_FLOW_OUT_FIELD,
            amount=amount,
            trade_date=trade_date,
            currency=currency,
        )
        if eod:
            add_flow(
                group_key=source_group_key,
                group_label=source_group_label,
                field_name=attribution.GROUP_CAPITAL_FLOW_OUT_EOD_FIELD,
                amount=amount,
                trade_date=trade_date,
                currency=currency,
            )
        add_flow(
            group_key=target_group_key,
            group_label=target_group_label,
            field_name=attribution.GROUP_CAPITAL_FLOW_IN_FIELD,
            amount=amount,
            trade_date=trade_date,
            currency=currency,
        )
        if eod:
            add_flow(
                group_key=target_group_key,
                group_label=target_group_label,
                field_name=attribution.GROUP_CAPITAL_FLOW_IN_EOD_FIELD,
                amount=amount,
                trade_date=trade_date,
                currency=currency,
            )

    for position_lot in position_lots:
        group_key, group_label = attribution.position_group_for_axis(
            axis=axis,
            position_lot=position_lot,
            account_name_map=account_name_map,
            base_currency=base_currency,
        )
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
                currency=valuation_fx.required_currency(
                    position_lot.get("currency"), field_name="position-lot currency"
                ),
            )

    for transaction in transactions_on_date:
        transaction_type = str(transaction.get("transaction_type") or "")
        option_action = resolve_option_action(transaction)
        performance_effective_date = transaction_performance_effective_date(
            transaction
        )
        position_cash_transfer_date = transaction_position_cash_transfer_date(
            transaction
        )
        is_performance_effective_date = performance_effective_date == as_of_date
        is_position_cash_transfer_date = (
            position_cash_transfer_date == as_of_date
        )
        instrument_id = str(transaction.get("instrument_id") or "")
        account_id = str(transaction.get("account_id") or "")
        currency = valuation_fx.required_currency(
            transaction.get("currency"), field_name="transaction currency"
        )
        instrument_name = str(
            (
                (transaction.get("instrument_ref") or {})
                if isinstance(transaction.get("instrument_ref"), dict)
                else {}
            ).get("instrument_name")
            or instrument_id
        )
        group_key, group_label = attribution.transaction_group_for_axis(
            axis=axis,
            transaction=transaction,
            account_name_map=account_name_map,
            base_currency=base_currency,
        )
        if not group_key:
            continue

        gross_amount = _safe_float(transaction.get("gross_amount")) or 0.0
        fee_amount = _safe_float(transaction.get("fees")) or 0.0
        tax_amount = _safe_float(transaction.get("taxes")) or 0.0
        settlement_cash_account_id = transaction.get("settlement_cash_account_id")

        if option_action == "sell_to_open" and is_performance_effective_date:
            target_group_key, target_group_label = cash_group(
                settlement_cash_account_id,
                currency,
            )
            add_transfer_flow(
                source_group_key=group_key,
                source_group_label=group_label,
                target_group_key=target_group_key,
                target_group_label=target_group_label,
                amount=max(gross_amount - fee_amount - tax_amount, 0.0),
                trade_date=as_of_date,
                currency=currency,
            )
        elif option_action == "buy_to_close" and is_performance_effective_date:
            source_group_key, source_group_label = cash_group(
                settlement_cash_account_id,
                currency,
            )
            add_transfer_flow(
                source_group_key=source_group_key,
                source_group_label=source_group_label,
                target_group_key=group_key,
                target_group_label=group_label,
                amount=gross_amount + fee_amount + tax_amount,
                trade_date=as_of_date,
                currency=currency,
            )

        if transaction_type == "opening_balance" and is_performance_effective_date:
            add_flow(
                group_key=group_key,
                group_label=group_label,
                field_name=attribution.GROUP_CAPITAL_FLOW_IN_FIELD,
                amount=gross_amount + fee_amount + tax_amount if instrument_id else gross_amount,
                trade_date=as_of_date,
                currency=currency,
            )
        elif transaction_type == "deposit" and is_performance_effective_date:
            add_flow(
                group_key=group_key,
                group_label=group_label,
                field_name=attribution.GROUP_CAPITAL_FLOW_IN_FIELD,
                amount=gross_amount,
                trade_date=as_of_date,
                currency=currency,
            )
        elif transaction_type == "withdrawal" and is_performance_effective_date:
            add_flow(
                group_key=group_key,
                group_label=group_label,
                field_name=attribution.GROUP_CAPITAL_FLOW_OUT_FIELD,
                amount=gross_amount,
                trade_date=as_of_date,
                currency=currency,
            )
        elif transaction_type == "buy" and is_position_cash_transfer_date:
            source_group_key, source_group_label = cash_group(settlement_cash_account_id, currency)
            add_transfer_flow(
                source_group_key=source_group_key,
                source_group_label=source_group_label,
                target_group_key=group_key,
                target_group_label=group_label,
                amount=gross_amount + fee_amount + tax_amount,
                trade_date=as_of_date,
                currency=currency,
                eod=(
                    performance_effective_date is not None
                    and position_cash_transfer_date is not None
                    and position_cash_transfer_date
                    < performance_effective_date
                ),
            )
        elif (
            transaction_type in {"sell", "maturity_redemption"}
            and is_position_cash_transfer_date
        ):
            target_group_key, target_group_label = cash_group(settlement_cash_account_id, currency)
            add_transfer_flow(
                source_group_key=group_key,
                source_group_label=group_label,
                target_group_key=target_group_key,
                target_group_label=target_group_label,
                amount=max(gross_amount - fee_amount - tax_amount, 0.0),
                trade_date=as_of_date,
                currency=currency,
                eod=(
                    performance_effective_date is not None
                    and position_cash_transfer_date is not None
                    and position_cash_transfer_date
                    < performance_effective_date
                ),
            )
        elif (
            transaction_type == "return_of_capital"
            and is_performance_effective_date
        ):
            target_group_key, target_group_label = cash_group(settlement_cash_account_id, currency)
            if target_group_key and target_group_key != group_key:
                add_transfer_flow(
                    source_group_key=group_key,
                    source_group_label=group_label,
                    target_group_key=target_group_key,
                    target_group_label=target_group_label,
                    amount=max(gross_amount - fee_amount - tax_amount, 0.0),
                    trade_date=as_of_date,
                    currency=currency,
                )

        if (
            transaction_type in {"dividend", "coupon", "dividend_reinvestment"}
            and is_performance_effective_date
        ):
            if axis == "instrument" and not instrument_id:
                continue
            add_amount(
                group_key=group_key,
                group_label=group_label,
                field_name="income_cash_amount",
                amount=gross_amount,
                trade_date=as_of_date,
                currency=currency,
            )
            if transaction_type in {"dividend", "coupon"}:
                target_group_key, target_group_label = cash_group(settlement_cash_account_id, currency)
                if target_group_key and target_group_key != group_key:
                    add_transfer_flow(
                        source_group_key=group_key,
                        source_group_label=group_label,
                        target_group_key=target_group_key,
                        target_group_label=target_group_label,
                        amount=max(gross_amount - fee_amount - tax_amount, 0.0),
                        trade_date=as_of_date,
                        currency=currency,
                    )
        elif (
            transaction_type == "interest"
            and is_performance_effective_date
            and attribution.axis_includes_cash_balance(axis)
        ):
            add_amount(
                group_key=group_key,
                group_label=group_label,
                field_name="income_cash_amount",
                amount=gross_amount,
                trade_date=as_of_date,
                currency=currency,
            )

        if (
            transaction_type in {"fee", "tax"}
            and is_performance_effective_date
        ):
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

        if (
            is_performance_effective_date
            and (fee_amount > 0 or tax_amount > 0)
        ):
            attached_expense = fee_amount + tax_amount
            if attached_expense > 0 and (
                transaction_type in NON_CAPITALIZED_ATTACHED_CHARGE_TRANSACTION_TYPES
            ):
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

    # Writer lifecycle P&L belongs to the option instrument/account group,
    # never to the settlement cash residual.  Replay the full as-of history so
    # partial close/expiry/assignment events can release the correct FIFO
    # premium basis even when the opening fact is not on today's date.
    obligation_history = transactions_as_of or transactions_on_date
    for obligation_event in derive_option_obligation_events(
        obligation_history,
        as_of_date=as_of_date,
    ):
        event_date = obligation_event.get("event_date")
        if isinstance(event_date, str):
            event_date = _parse_iso_date(event_date)
        if event_date != as_of_date:
            continue
        obligation = obligation_event.get("obligation")
        if not isinstance(obligation, dict):
            continue
        pseudo_transaction = {
            "account_id": obligation.get("account_id"),
            "instrument_id": obligation.get("option_instrument_id")
            or obligation.get("instrument_id"),
            "instrument_ref": obligation.get("instrument_ref"),
            "currency": obligation_event.get("currency")
            or obligation.get("contract_currency")
            or base_currency,
        }
        group_key, group_label = attribution.transaction_group_for_axis(
            axis=axis,
            transaction=pseudo_transaction,
            account_name_map=account_name_map,
            base_currency=base_currency,
        )
        if not group_key:
            continue
        ensure_event(group_key, group_label)[
            "_derivative_lifecycle_activity"
        ] = True
        realized = _safe_float(obligation_event.get("realized_pnl_delta"))
        if realized is not None and abs(realized) > 1e-15:
            add_amount(
                group_key=group_key,
                group_label=group_label,
                field_name="realized_pnl",
                amount=realized,
                trade_date=as_of_date,
                currency=str(pseudo_transaction["currency"]),
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
    direct_fx_instruments: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
) -> list[dict[str, object]]:
    daily_slices: list[dict[str, object]] = []
    group_keys = sorted(set(previous_states) | set(current_states) | set(current_events))
    beginning_nav = _safe_float((snapshot or {}).get("beginning_nav"))
    ending_nav = _safe_float((snapshot or {}).get("ending_nav"))
    is_initial_valuation_anchor = bool(
        not previous_states
        and snapshot is not None
        and return_chain.has_complete_valuation(snapshot)
        and _safe_float(snapshot.get("daily_twr")) is None
        and bool(snapshot.get("return_chain_continuous"))
    )

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
            ending_cash_balance_base = 0.0 if attribution.axis_includes_cash_balance(axis) else None
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
        capital_flow_in_base = _safe_float((current_event or {}).get(attribution.GROUP_CAPITAL_FLOW_IN_FIELD))
        capital_flow_out_base = _safe_float((current_event or {}).get(attribution.GROUP_CAPITAL_FLOW_OUT_FIELD))
        capital_flow_in_eod_base = _safe_float(
            (current_event or {}).get(
                attribution.GROUP_CAPITAL_FLOW_IN_EOD_FIELD
            )
        )
        capital_flow_out_eod_base = _safe_float(
            (current_event or {}).get(
                attribution.GROUP_CAPITAL_FLOW_OUT_EOD_FIELD
            )
        )
        cash_currency_gains = None
        pending_settlement_currency_gains = None
        instrument_currency_gains = None
        if previous_state is None:
            cash_currency_gains = 0.0 if attribution.axis_includes_cash_balance(axis) else None
            pending_settlement_currency_gains = (
                0.0 if attribution.axis_includes_cash_balance(axis) else None
            )
            instrument_currency_gains = 0.0
        else:
            instrument_currency_gains, _ = _compute_currency_translation_gain(
                previous_state.get("_position_market_value_local_by_currency"),
                previous_date=previous_date,
                current_date=as_of_date,
                base_currency=base_currency,
                direct_fx_instruments=direct_fx_instruments,
                instrument_detail_cache=instrument_detail_cache,
            )
            if attribution.axis_includes_cash_balance(axis):
                cash_currency_gains, _ = _compute_currency_translation_gain(
                    previous_state.get("_cash_balance_local_by_currency"),
                    previous_date=previous_date,
                    current_date=as_of_date,
                    base_currency=base_currency,
                    direct_fx_instruments=direct_fx_instruments,
                    instrument_detail_cache=instrument_detail_cache,
                )
                pending_settlement_currency_gains, _ = _compute_currency_translation_gain(
                    previous_state.get("_pending_settlement_local_by_currency"),
                    previous_date=previous_date,
                    current_date=as_of_date,
                    base_currency=base_currency,
                    direct_fx_instruments=direct_fx_instruments,
                    instrument_detail_cache=instrument_detail_cache,
                )
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
        if capital_flow_in_base is None and current_event is None:
            capital_flow_in_base = 0.0
        if capital_flow_out_base is None and current_event is None:
            capital_flow_out_base = 0.0
        if capital_flow_in_eod_base is None and current_event is None:
            capital_flow_in_eod_base = 0.0
        if capital_flow_out_eod_base is None and current_event is None:
            capital_flow_out_eod_base = 0.0
        if instrument_currency_gains is None and current_event is None:
            instrument_currency_gains = 0.0
        if attribution.axis_includes_cash_balance(axis) and cash_currency_gains is None and current_event is None:
            cash_currency_gains = 0.0
        unrealized_pnl_change = None
        if (
            (previous_state is None or previous_unrealized_pnl is not None)
            and (current_state is None or ending_unrealized_pnl is not None)
        ):
            unrealized_pnl_change = (ending_unrealized_pnl or 0.0) - (previous_unrealized_pnl or 0.0)

        total_pnl = None
        if (
            beginning_value_base is not None
            and ending_value_base is not None
            and capital_flow_in_base is not None
            and capital_flow_out_base is not None
        ):
            # Economic group P&L is the flow-neutral fair-value bridge.  Book
            # unrealized P&L is translated at the current FX rate, so adding
            # its change to a separately calculated FX effect double-counts
            # part of a multi-day price/FX interaction.
            total_pnl = (
                ending_value_base
                - beginning_value_base
                - capital_flow_in_base
                + capital_flow_out_base
            )

        slice_coverage_state = return_chain.snapshot_coverage_state(snapshot or {}, "attribution")
        if (
            (previous_state is not None and beginning_value_base is None)
            or (current_state is not None and ending_value_base is None)
            or (current_state is not None and ending_position_market_value_base is None)
            or (current_state is not None and ending_open_cost_basis_base is None)
            or (attribution.axis_includes_cash_balance(axis) and current_state is not None and ending_cash_balance_base is None)
            or (instrument_currency_gains is None)
            or (attribution.axis_includes_cash_balance(axis) and cash_currency_gains is None)
            or (
                attribution.axis_includes_cash_balance(axis)
                and pending_settlement_currency_gains is None
            )
            or capital_flow_in_base is None
            or capital_flow_out_base is None
            or capital_flow_in_eod_base is None
            or capital_flow_out_eod_base is None
            or total_pnl is None
        ):
            if slice_coverage_state == "complete":
                slice_coverage_state = "partial"

        daily_return = attribution.daily_group_return_from_components(
            beginning_value_base=beginning_value_base,
            ending_value_base=ending_value_base,
            total_pnl=total_pnl,
            capital_flow_in_base=capital_flow_in_base,
            capital_flow_out_base=capital_flow_out_base,
            capital_flow_in_eod_base=capital_flow_in_eod_base,
        )
        # Contribution is an arithmetic decomposition of the portfolio's
        # daily TWR.  Use the same BOD external-flow denominator as the
        # portfolio return so inception funding and later deposits reconcile
        # instead of falling into contribution residual.
        portfolio_return_denominator = (
            beginning_nav + (_safe_float((snapshot or {}).get("external_cash_in")) or 0.0)
            if beginning_nav is not None
            else None
        )
        daily_contribution = (
            total_pnl / portfolio_return_denominator
            if (
                total_pnl is not None
                and portfolio_return_denominator is not None
                and portfolio_return_denominator > 1e-9
            )
            else None
        )
        market_observation_count = int((current_state or {}).get("market_observation_count") or 0)
        has_derivative_exposure = any(
            bool((state or {}).get(field_name))
            for state in (previous_state, current_state)
            for field_name in (
                "_has_event_valued_exposure",
                "_has_derivative_liability_exposure",
            )
        )
        has_derivative_lifecycle_activity = bool(
            (current_event or {}).get("_derivative_lifecycle_activity")
        )
        has_stale_market_input = any(
            bool((current_state or {}).get(field_name))
            for field_name in ("stale_price_flag", "stale_fx_flag")
        )
        return_observation_eligible = (
            daily_return is not None
            and isfinite(daily_return)
            and slice_coverage_state == "complete"
            and (market_observation_count > 0 or abs(daily_return) > 1e-12)
            and not has_derivative_exposure
            and not has_derivative_lifecycle_activity
            and not has_stale_market_input
        )

        daily_slices.append(
            {
                "as_of_date": as_of_date,
                "axis": axis,
                "group_key": candidate_group_key,
                "group_label": group_label,
                "coverage_state": slice_coverage_state,
                # An imported opening balance establishes fair value; it is a
                # boundary observation, not a subperiod return.  Keep the
                # marker internal so period linking can skip it without
                # weakening fail-closed handling for later incomplete rows.
                "_is_initial_valuation_anchor": is_initial_valuation_anchor,
                "market_observation_count": market_observation_count,
                "return_observation_eligible": return_observation_eligible,
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
                "unrealized_pnl_change": unrealized_pnl_change,
                "income_cash_amount": income_cash_amount,
                "expense_cash_amount": expense_cash_amount,
                "fee_amount": fee_amount,
                "tax_amount": tax_amount,
                "cash_currency_gains": cash_currency_gains,
                "pending_settlement_currency_gains": pending_settlement_currency_gains,
                "instrument_currency_gains": instrument_currency_gains,
                attribution.GROUP_CAPITAL_FLOW_IN_FIELD: capital_flow_in_base,
                attribution.GROUP_CAPITAL_FLOW_OUT_FIELD: capital_flow_out_base,
                attribution.GROUP_CAPITAL_FLOW_IN_EOD_FIELD: (
                    capital_flow_in_eod_base
                ),
                attribution.GROUP_CAPITAL_FLOW_OUT_EOD_FIELD: (
                    capital_flow_out_eod_base
                ),
                "total_pnl": total_pnl,
                "daily_return": daily_return,
                "daily_contribution": daily_contribution,
            }
        )

    return daily_slices


def build_taxonomy_contribution_report_from_base_report(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    taxonomies: list[dict[str, object]] | None,
    taxonomy_nodes: list[dict[str, object]] | None,
    taxonomy_assignments: list[dict[str, object]] | None,
    start_date: date | None,
    end_date: date | None,
    taxonomy_id: str | None,
    group_key: str | None = None,
    base_report: dict[str, object],
    use_period_end_taxonomy_assignments: bool = False,
    apply_boundary_values: bool = True,
    preserve_cash_group: bool = False,
) -> dict[str, object]:
    resolved_taxonomy_id = str(taxonomy_id or "").strip()
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

    base_daily_slices = list(base_report.get("daily_slices") or [])
    if primary_assignment_scope == "cash_bucket":
        cash_bucket_ids = attribution.cash_bucket_account_ids(accounts)
        base_daily_slices = [
            item for item in base_daily_slices if str(item.get("group_key") or "") in cash_bucket_ids
        ]
    base_summary = base_report.get("summary") if isinstance(base_report.get("summary"), dict) else {}
    assignment_as_of_date = (
        _parse_iso_date(base_summary.get("end_date"))
        if use_period_end_taxonomy_assignments
        else None
    )
    grouped_daily_slices = attribution.group_contribution_slices_by_taxonomy(
        taxonomy=taxonomy,
        taxonomy_nodes=taxonomy_nodes or [],
        taxonomy_assignments=taxonomy_assignments or [],
        base_daily_slices=base_daily_slices,
        assignment_as_of_date=assignment_as_of_date,
        preserve_cash_group=preserve_cash_group,
    )
    report = attribution.build_taxonomy_contribution_report(
        taxonomy=taxonomy,
        base_report=base_report,
        grouped_daily_slices=grouped_daily_slices,
    )
    if use_period_end_taxonomy_assignments:
        report["_taxonomy_assignment_mode"] = "period_end"
    if preserve_cash_group:
        report["_taxonomy_preserve_cash_group"] = True
    if primary_assignment_scope == "instrument" and apply_boundary_values:
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
    return attribution.filter_contribution_report_by_group_key(
        report,
        group_key=group_key,
    )


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
        start_value = _safe_float(start_group.get("market_value_base"))
        end_value = _safe_float(end_group.get("market_value_base"))
        beginning_weight = _safe_float(start_group.get("portfolio_weight"))
        ending_weight = _safe_float(end_group.get("portfolio_weight"))
        line["start_value_base"] = (
            start_value
            if start_value is not None
            else (_safe_float(line.get("start_value_base")) or 0.0)
        )
        line["end_value_base"] = (
            end_value
            if end_value is not None
            else (_safe_float(line.get("end_value_base")) or 0.0)
        )
        line["beginning_weight"] = (
            beginning_weight
            if beginning_weight is not None
            else (_safe_float(line.get("beginning_weight")) or 0.0)
        )
        line["ending_weight"] = (
            ending_weight
            if ending_weight is not None
            else (_safe_float(line.get("ending_weight")) or 0.0)
        )
        line.setdefault("average_weight", 0.0)
        line.setdefault("realized_pnl", 0.0)
        line.setdefault("unrealized_pnl_change", 0.0)
        line.setdefault("income_cash_amount", 0.0)
        line.setdefault("expense_cash_amount", 0.0)
        line.setdefault("fee_amount", 0.0)
        line.setdefault("tax_amount", 0.0)
        line.setdefault("cash_currency_gains", 0.0)
        line.setdefault("pending_settlement_currency_gains", 0.0)
        line.setdefault("instrument_currency_gains", 0.0)
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


def build_contribution_report_from_daily_slices(
    portfolio: dict[str, object],
    snapshots: list[dict[str, object]],
    daily_slices: list[dict[str, object]],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    group_key: str | None = None,
    start_is_close_boundary: bool = False,
) -> dict[str, object]:
    base_currency = valuation_fx.required_currency(
        portfolio.get("base_currency"), field_name="portfolio base currency"
    )
    valuation_timezone = _resolve_portfolio_valuation_timezone(portfolio)
    valuation_cutoff_policy = _resolve_portfolio_valuation_cutoff_policy(portfolio)
    return attribution.build_contribution_report_from_daily_slices_core(
        portfolio_id=str(portfolio.get("portfolio_id") or ""),
        base_currency=base_currency,
        valuation_timezone=valuation_timezone,
        valuation_cutoff_policy=valuation_cutoff_policy,
        snapshots=snapshots,
        daily_slices=daily_slices,
        start_date=start_date,
        end_date=end_date,
        axis=axis,
        group_key=group_key,
        start_is_close_boundary=start_is_close_boundary,
    )


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
    allow_internal_detail_axis: bool = False,
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
        return build_taxonomy_contribution_report_from_base_report(
            portfolio,
            accounts,
            transactions,
            taxonomies=taxonomies,
            taxonomy_nodes=taxonomy_nodes,
            taxonomy_assignments=taxonomy_assignments,
            start_date=start_date,
            end_date=end_date,
            taxonomy_id=resolved_taxonomy_id,
            group_key=group_key,
            base_report=base_report,
        )

    if axis not in attribution.CONTRIBUTION_BASE_AXES and not (
        allow_internal_detail_axis and attribution.is_internal_calculation_axis(axis)
    ):
        raise ValueError(attribution.CONTRIBUTION_AXIS_ERROR)

    window = _resolve_snapshot_window(
        portfolio,
        transactions,
        start_date=start_date,
        end_date=end_date,
    )
    base_currency = valuation_fx.required_currency(
        portfolio.get("base_currency"), field_name="portfolio base currency"
    )
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
                "requested_start_date": start_date,
                "requested_end_date": end_date,
                "effective_start_date": None,
                "effective_end_date": None,
                "as_of_clamp_reason": None,
                "start_boundary_kind": None,
                "include_start_date_return": False,
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
    requested_resolved_end_date = end_date or resolved_end_date
    sorted_transactions = sorted(transactions, key=transaction_sort_key)
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
    reliable_window = return_chain.resolve_reliable_snapshot_window(
        snapshots,
        requested_start_date=resolved_start_date,
        requested_end_date=requested_resolved_end_date,
        default_end_date=resolved_end_date,
    )
    snapshots = list(
        cast(list[dict[str, object]], reliable_window["snapshots"])
    )
    reliable_end_date = cast(
        date | None, reliable_window.get("effective_end_date")
    )
    if reliable_end_date is not None:
        resolved_end_date = reliable_end_date
    snapshots_by_date = {
        snapshot["as_of_date"]: snapshot
        for snapshot in snapshots
        if isinstance(snapshot.get("as_of_date"), date)
    }
    start_is_close_boundary = _period_start_is_close_boundary(
        sorted_transactions,
        requested_start_date=start_date,
        resolved_start_date=resolved_start_date,
        start_snapshot=snapshots_by_date.get(resolved_start_date),
    )
    starts_on_imported_anchor = _starts_on_imported_valuation_anchor(
        sorted_transactions,
        resolved_start_date=resolved_start_date,
    )
    starts_funded_segment = _snapshot_starts_funded_segment(
        snapshots_by_date.get(resolved_start_date),
        resolved_start_date=resolved_start_date,
    )
    start_boundary_kind = (
        "funded_bod"
        if starts_funded_segment
        else (
            "imported_opening_eod"
            if starts_on_imported_anchor
            else "close_eod"
        )
    )

    transactions_by_date: dict[str, list[dict[str, object]]] = defaultdict(list)
    for transaction in sorted_transactions:
        event_dates = {
            candidate
            for candidate in (
                transaction_performance_effective_date(transaction),
                transaction_position_cash_transfer_date(transaction),
            )
            if candidate is not None
        }
        for event_date in event_dates:
            transactions_by_date[event_date.isoformat()].append(transaction)

    portfolio_id = str(portfolio.get("portfolio_id") or "")
    account_cost_methods = _account_cost_methods(accounts)
    account_currency_map = _account_currency_map(accounts)
    account_name_map = attribution.account_name_map(accounts)
    fx_payload = get_platform_fx_rates()
    direct_fx_instruments = valuation_fx.fx_direct_instrument_map(fx_payload)
    instrument_detail_cache: dict[str, dict[str, object] | None] = {}

    group_states_by_date: dict[date, dict[str, dict[str, object]]] = {}
    group_events_by_date: dict[date, dict[str, dict[str, object]]] = {}

    for as_of_date in _iter_dates(boundary_start_date, resolved_end_date):
        transactions_as_of = _transactions_with_ledger_activity_as_of_end_date(
            sorted_transactions,
            end_date=as_of_date,
        )
        position_lots = (
            []
            if axis == "cash_detail"
            else build_position_lots(
                portfolio_id,
                accounts,
                transactions_as_of,
                as_of_date=as_of_date,
            )
        )
        group_states_by_date[as_of_date] = _build_contribution_group_end_states(
            axis=axis,
            portfolio_id=portfolio_id,
            accounts=accounts,
            transactions_as_of=transactions_as_of,
            position_lots=position_lots,
            as_of_date=as_of_date,
            base_currency=base_currency,
            account_cost_methods=account_cost_methods,
            account_currency_map=account_currency_map,
            account_name_map=account_name_map,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
        )
        group_events_by_date[as_of_date] = _build_contribution_daily_events(
            axis=axis,
            as_of_date=as_of_date,
            position_lots=position_lots,
            transactions_on_date=transactions_by_date.get(as_of_date.isoformat(), []),
            transactions_as_of=transactions_as_of,
            base_currency=base_currency,
            account_name_map=account_name_map,
            direct_fx_instruments=direct_fx_instruments,
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
                direct_fx_instruments=direct_fx_instruments,
                instrument_detail_cache=instrument_detail_cache,
            )
        )

    report = build_contribution_report_from_daily_slices(
        portfolio,
        snapshots,
        daily_slices,
        start_date=resolved_start_date,
        end_date=resolved_end_date,
        axis=axis,
        group_key=group_key,
        start_is_close_boundary=start_is_close_boundary,
    )
    report_summary = report.get("summary")
    if isinstance(report_summary, dict):
        report_summary.update(
            {
                "requested_start_date": start_date,
                "requested_end_date": requested_resolved_end_date,
                "effective_start_date": resolved_start_date,
                "effective_end_date": resolved_end_date,
                "as_of_clamp_reason": reliable_window.get(
                    "as_of_clamp_reason"
                ),
                "start_boundary_kind": start_boundary_kind,
                "include_start_date_return": starts_funded_segment,
            }
        )
    return report


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
    starts_on_close_anchor = any(
        item.get("as_of_date") == resolved_start_date
        and bool(item.get("_is_initial_valuation_anchor"))
        for item in daily_slices
    )
    first_observation_date = (
        resolved_start_date + timedelta(days=1)
        if starts_on_close_anchor
        else resolved_start_date
    )
    bucket_windows: dict[str, dict[str, object]] = {}
    for as_of_date in _iter_dates(first_observation_date, resolved_end_date):
        bucket_key = _calendar_bucket_key(as_of_date, frequency)
        bucket = bucket_windows.get(bucket_key)
        if bucket is None:
            bucket_windows[bucket_key] = {
                "bucket_key": bucket_key,
                # Bucket dates are EOD boundaries.  The first return
                # observation on ``as_of_date`` starts from the preceding EOD.
                "start_date": as_of_date - timedelta(days=1),
                "first_observation_date": as_of_date,
                "end_date": as_of_date,
                "available_weight_dates": set(),
            }
        else:
            bucket["end_date"] = as_of_date

    bucket_accumulators: dict[tuple[str, str], dict[str, object]] = {}
    bucket_start_values: dict[tuple[str, str], float | None] = {}
    bucket_beginning_weights: dict[tuple[str, str], float | None] = {}
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
        window = bucket_windows.get(bucket_key)
        if window is None:
            continue
        if _safe_float(daily_slice.get("beginning_weight")) is not None:
            available_dates = window.get("available_weight_dates")
            if isinstance(available_dates, set):
                available_dates.add(as_of_date)
        bucket_group_key = (bucket_key, group_key)
        if as_of_date == window["first_observation_date"]:
            bucket_start_values[bucket_group_key] = (
                _period_initial_value_from_daily_slice(daily_slice)
            )
            bucket_beginning_weights[bucket_group_key] = _safe_float(
                daily_slice.get(
                    "ending_weight"
                    if bool(daily_slice.get("_is_initial_valuation_anchor"))
                    else "beginning_weight"
                )
            )
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
                "beginning_weight": 0.0,
                "average_weight": 0.0,
                "ending_weight": 0.0,
                "realized_pnl": 0.0,
                "unrealized_pnl_change": 0.0,
                "income_cash_amount": 0.0,
                "expense_cash_amount": 0.0,
                "fee_amount": 0.0,
                "tax_amount": 0.0,
                "cash_currency_gains": 0.0,
                "pending_settlement_currency_gains": 0.0,
                "instrument_currency_gains": 0.0,
                "total_pnl": 0.0,
                "bucket_contribution": 0.0,
            },
        )

        beginning_weight = _safe_float(daily_slice.get("beginning_weight"))
        if beginning_weight is not None:
            accumulator["average_weight"] = (_safe_float(accumulator.get("average_weight")) or 0.0) + beginning_weight
        for field_name in (
            "realized_pnl",
            "unrealized_pnl_change",
            "income_cash_amount",
            "expense_cash_amount",
            "fee_amount",
            "tax_amount",
            "cash_currency_gains",
            "pending_settlement_currency_gains",
            "instrument_currency_gains",
            "total_pnl",
            "daily_contribution",
        ):
            value = _safe_float(daily_slice.get(field_name))
            target_field = "bucket_contribution" if field_name == "daily_contribution" else field_name
            if value is None:
                if field_name == "pending_settlement_currency_gains":
                    accumulator[target_field] = None
                    accumulator["total_pnl"] = None
                    accumulator["bucket_contribution"] = None
                continue
            if accumulator.get(target_field) is None:
                continue
            accumulator[target_field] = (_safe_float(accumulator.get(target_field)) or 0.0) + value

    rendered_buckets: list[dict[str, object]] = []
    for bucket_group_key, accumulator in bucket_accumulators.items():
        bucket_key, group_key = bucket_group_key
        window = bucket_windows[bucket_key]
        available_weight_dates = window.get("available_weight_dates")
        weight_denominator = len(available_weight_dates) if isinstance(available_weight_dates, set) else 0
        accumulator["coverage_state"] = attribution.merge_group_coverage_state(bucket_coverage_states[bucket_group_key])
        accumulator["observation_count"] = len(bucket_observation_dates[bucket_group_key])
        # Group bridges use the group's own BOD boundary.  At portfolio
        # inception an imported opening balance intentionally has no prior
        # portfolio NAV, but a group may still have a valid zero BOD exposure
        # and an internal capital flow during that first day.
        accumulator["beginning_value_base"] = bucket_start_values.get(
            bucket_group_key, 0.0
        )
        accumulator["ending_value_base"] = (
            bucket_end_values.get(bucket_group_key, 0.0)
            if _safe_float(summary.get("end_nav")) is not None
            else None
        )
        accumulator["beginning_weight"] = (
            bucket_beginning_weights.get(bucket_group_key, 0.0)
            if bucket_group_key in bucket_beginning_weights or _safe_float(summary.get("start_nav")) is not None
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
    "beginning_weight": "beginning_weight",
    "average_weight": "average_weight",
    "ending_weight": "ending_weight",
    "realized_pnl": "realized_pnl",
    "unrealized_pnl_change": "unrealized_pnl_change",
    "income_cash_amount": "income_cash_amount",
    "expense_cash_amount": "expense_cash_amount",
    "fee_amount": "fee_amount",
    "tax_amount": "tax_amount",
    "cash_currency_gains": "cash_currency_gains",
    "pending_settlement_currency_gains": "pending_settlement_currency_gains",
    "instrument_currency_gains": "instrument_currency_gains",
    "total_pnl": "total_pnl",
    "contribution": "period_contribution",
}
_CONTRIBUTION_CALENDAR_BUCKET_FIELD_MAP: dict[str, str] = {
    "start_value": "beginning_value_base",
    "end_value": "ending_value_base",
    "beginning_weight": "beginning_weight",
    "average_weight": "average_weight",
    "ending_weight": "ending_weight",
    "realized_pnl": "realized_pnl",
    "unrealized_pnl_change": "unrealized_pnl_change",
    "income_cash_amount": "income_cash_amount",
    "expense_cash_amount": "expense_cash_amount",
    "fee_amount": "fee_amount",
    "tax_amount": "tax_amount",
    "cash_currency_gains": "cash_currency_gains",
    "pending_settlement_currency_gains": "pending_settlement_currency_gains",
    "instrument_currency_gains": "instrument_currency_gains",
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


_CALCULATION_DETAIL_ADDITIVE_SLICE_FIELDS = (
    "beginning_value_base",
    "ending_value_base",
    "beginning_weight",
    "ending_weight",
    "cash_balance_base",
    "position_market_value_base",
    "open_cost_basis_base",
    "realized_pnl",
    "unrealized_pnl",
    "unrealized_pnl_change",
    "income_cash_amount",
    "expense_cash_amount",
    "fee_amount",
    "tax_amount",
    "cash_currency_gains",
    "pending_settlement_currency_gains",
    "instrument_currency_gains",
    attribution.GROUP_CAPITAL_FLOW_IN_FIELD,
    attribution.GROUP_CAPITAL_FLOW_OUT_FIELD,
    attribution.GROUP_CAPITAL_FLOW_IN_EOD_FIELD,
    attribution.GROUP_CAPITAL_FLOW_OUT_EOD_FIELD,
    "total_pnl",
    "daily_contribution",
)


def _build_taxonomy_calculation_detail_report(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    taxonomies: list[dict[str, object]] | None,
    taxonomy_nodes: list[dict[str, object]] | None,
    taxonomy_assignments: list[dict[str, object]] | None,
    start_date: date | None,
    end_date: date | None,
    taxonomy_id: str | None,
    base_report: dict[str, object] | None = None,
    use_period_end_taxonomy_assignments: bool = False,
    preserve_cash_group: bool = False,
) -> dict[str, object]:
    resolved_taxonomy_id = str(taxonomy_id or "").strip()
    taxonomy = next(
        (
            item
            for item in taxonomies or []
            if str(item.get("taxonomy_id") or "") == resolved_taxonomy_id
        ),
        None,
    )
    if taxonomy is None:
        return build_contribution_report_from_daily_slices(
            portfolio,
            [],
            [],
            start_date=start_date,
            end_date=end_date,
            axis=attribution.calculation_detail_axis("taxonomy"),
        )

    target_scope = str(taxonomy.get("primary_assignment_scope") or "")
    if target_scope in {"account", "cash_bucket"}:
        base_axis = attribution.calculation_detail_axis("account")
    else:
        base_axis = attribution.calculation_detail_axis("instrument")
    resolved_base_report = base_report or build_contribution_report(
        portfolio,
        accounts,
        transactions,
        taxonomies=taxonomies,
        taxonomy_nodes=taxonomy_nodes,
        taxonomy_assignments=taxonomy_assignments,
        start_date=start_date,
        end_date=end_date,
        axis=base_axis,
        allow_internal_detail_axis=True,
    )
    base_summary = resolved_base_report.get("summary") if isinstance(resolved_base_report.get("summary"), dict) else {}
    assignment_as_of_date = (
        _parse_iso_date(base_summary.get("end_date"))
        if use_period_end_taxonomy_assignments
        else None
    )
    taxonomy_nodes_by_id = {
        str(node.get("taxonomy_node_id") or ""): node
        for node in taxonomy_nodes or []
        if str(node.get("taxonomy_id") or "") == resolved_taxonomy_id
    }
    assignments_by_entity: dict[str, list[dict[str, object]]] = defaultdict(list)
    for assignment in taxonomy_assignments or []:
        if str(assignment.get("taxonomy_id") or "") != resolved_taxonomy_id:
            continue
        if str(assignment.get("target_scope") or "") != target_scope:
            continue
        assignments_by_entity[str(assignment.get("target_entity_id") or "")].append(assignment)
    for entity_assignments in assignments_by_entity.values():
        entity_assignments.sort(
            key=lambda item: (
                str(item.get("assignment_id") or ""),
            )
        )

    cash_bucket_ids = attribution.cash_bucket_account_ids(accounts) if target_scope == "cash_bucket" else set()

    def detail_target_for_base_group_key(base_group_key: str) -> tuple[str, str, str] | None:
        if target_scope in {"account", "cash_bucket"}:
            decoded = attribution.decode_calculation_detail_group_key(base_group_key)
            if decoded is None:
                return None
            target_entity_id, item_kind, item_key = decoded
            if target_scope == "cash_bucket" and target_entity_id not in cash_bucket_ids:
                return None
            return (target_entity_id, item_kind, item_key)

        decoded = attribution.decode_calculation_detail_group_key(base_group_key)
        if decoded is None:
            target_entity_id = base_group_key
            item_kind = "cash" if base_group_key == "cash" else "instrument"
            detail_item_key = base_group_key
        else:
            target_entity_id, item_kind, detail_item_key = decoded
        return (target_entity_id, item_kind, detail_item_key)

    entities_present_at_assignment_date: set[str] = set()
    if assignment_as_of_date is not None:
        for base_slice in list(resolved_base_report.get("daily_slices") or []):
            as_of_date = base_slice.get("as_of_date")
            if as_of_date != assignment_as_of_date:
                continue
            base_group_key = str(base_slice.get("group_key") or "")
            if not base_group_key or not attribution.daily_slice_has_period_end_exposure(base_slice):
                continue
            resolved_detail_target = detail_target_for_base_group_key(base_group_key)
            if resolved_detail_target is None:
                continue
            target_entity_id, _item_kind, _detail_item_key = resolved_detail_target
            entities_present_at_assignment_date.add(target_entity_id)

    detail_axis = attribution.calculation_detail_axis("taxonomy")
    detail_daily_slices: list[dict[str, object]] = []
    for base_slice in list(resolved_base_report.get("daily_slices") or []):
        as_of_date = base_slice.get("as_of_date")
        if not isinstance(as_of_date, date):
            continue
        base_group_key = str(base_slice.get("group_key") or "")
        if not base_group_key:
            continue

        resolved_detail_target = detail_target_for_base_group_key(base_group_key)
        if resolved_detail_target is None:
            continue
        target_entity_id, item_kind, detail_item_key = resolved_detail_target

        if preserve_cash_group and target_scope == "instrument" and item_kind == "cash":
            parent_group_key = "cash"
        else:
            parent_group_key, _parent_group_label = attribution.resolve_period_taxonomy_group_for_slice(
                taxonomy=taxonomy,
                taxonomy_nodes_by_id=taxonomy_nodes_by_id,
                assignments_by_entity=assignments_by_entity,
                target_scope=target_scope,
                target_entity_id=target_entity_id,
                slice_date=as_of_date,
                assignment_as_of_date=assignment_as_of_date,
                entities_present_at_assignment_date=entities_present_at_assignment_date,
            )
        detail_slice = dict(base_slice)
        detail_slice["axis"] = detail_axis
        detail_slice["group_key"] = attribution.encode_calculation_detail_group_key(
            parent_group_key=parent_group_key,
            item_kind=item_kind,
            item_key=detail_item_key,
        )
        detail_slice["group_label"] = str(base_slice.get("group_label") or detail_item_key)
        detail_daily_slices.append(detail_slice)

    detail_report = build_contribution_report_from_daily_slices(
        portfolio,
        [],
        attribution.merge_calculation_detail_daily_slices(detail_daily_slices),
        start_date=start_date,
        end_date=end_date,
        axis=detail_axis,
    )
    summary = detail_report.get("summary")
    if isinstance(summary, dict):
        summary["taxonomy_id"] = resolved_taxonomy_id
    return detail_report


def build_taxonomy_calculation_detail_report_from_base_report(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    taxonomies: list[dict[str, object]] | None,
    taxonomy_nodes: list[dict[str, object]] | None,
    taxonomy_assignments: list[dict[str, object]] | None,
    start_date: date | None,
    end_date: date | None,
    taxonomy_id: str | None,
    base_report: dict[str, object],
    use_period_end_taxonomy_assignments: bool = False,
    preserve_cash_group: bool = False,
) -> dict[str, object]:
    return _build_taxonomy_calculation_detail_report(
        portfolio,
        accounts,
        transactions,
        taxonomies=taxonomies,
        taxonomy_nodes=taxonomy_nodes,
        taxonomy_assignments=taxonomy_assignments,
        start_date=start_date,
        end_date=end_date,
        taxonomy_id=taxonomy_id,
        base_report=base_report,
        use_period_end_taxonomy_assignments=use_period_end_taxonomy_assignments,
        preserve_cash_group=preserve_cash_group,
    )


def _instrument_ids_for_calculation_risk_basis(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    start_date: date | None,
    end_date: date | None,
) -> list[str]:
    portfolio_id = str(portfolio.get("portfolio_id") or "")
    instrument_ids: set[str] = set()

    def add_instrument_id(value: object) -> None:
        instrument_id = str(value or "").strip()
        if instrument_id:
            instrument_ids.add(instrument_id)

    if portfolio_id and end_date is not None:
        end_transactions = [
            transaction
            for transaction in transactions
            if (
                effective_date := transaction_performance_effective_date(
                    transaction
                )
            )
            is not None
            and effective_date <= end_date
        ]
        for position_lot in build_position_lots(
            portfolio_id,
            accounts,
            end_transactions,
            as_of_date=end_date,
            resolve_pricing=False,
        ):
            if str(position_lot.get("status") or "") == "open":
                add_instrument_id(position_lot.get("instrument_id"))

        if start_date is not None:
            start_transactions = [
                transaction
                for transaction in transactions
                if (
                    effective_date := transaction_performance_effective_date(
                        transaction
                    )
                )
                is not None
                and effective_date <= start_date
            ]
            for position_lot in build_position_lots(
                portfolio_id,
                accounts,
                start_transactions,
                as_of_date=start_date,
                resolve_pricing=False,
            ):
                if str(position_lot.get("status") or "") == "open":
                    add_instrument_id(position_lot.get("instrument_id"))

    for transaction in transactions:
        effective_date = transaction_performance_effective_date(transaction)
        if effective_date is None:
            continue
        if end_date is not None and effective_date > end_date:
            continue
        if start_date is not None and effective_date < start_date:
            continue
        add_instrument_id(transaction.get("instrument_id"))

    return sorted(instrument_ids)


def _build_period_calculation_child_records(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    taxonomies: list[dict[str, object]] | None,
    taxonomy_nodes: list[dict[str, object]] | None,
    taxonomy_assignments: list[dict[str, object]] | None,
    start_date: date | None,
    end_date: date | None,
    resolved_start_date: date | None,
    resolved_end_date: date | None,
    axis: str,
    taxonomy_id: str | None,
    parent_groups: list[dict[str, object]],
    risk_calculation_frequency: CalculationFrequency,
    portfolio_daily_series: list[dict[str, object]],
    risk_final_date: date | None,
    portfolio_start_weight_denominator: float | None,
    detail_contribution_report: dict[str, object] | None = None,
) -> dict[str, list[dict[str, object]]]:
    parent_labels = {
        str(item.get("group_key") or ""): str(item.get("group_label") or item.get("group_key") or "")
        for item in parent_groups
        if str(item.get("group_key") or "")
    }
    if not parent_labels:
        return {}

    detail_axis = "cash_detail" if axis == "instrument" else attribution.calculation_detail_axis(axis)
    if detail_contribution_report is not None:
        detail_report = detail_contribution_report
    elif axis in {"instrument", "account", "instrument_type", "currency"}:
        detail_report = build_contribution_report(
            portfolio,
            accounts,
            transactions,
            taxonomies=taxonomies,
            taxonomy_nodes=taxonomy_nodes,
            taxonomy_assignments=taxonomy_assignments,
            start_date=start_date,
            end_date=end_date,
            axis=detail_axis,
            allow_internal_detail_axis=True,
        )
    elif axis == "taxonomy":
        detail_report = _build_taxonomy_calculation_detail_report(
            portfolio,
            accounts,
            transactions,
            taxonomies=taxonomies,
            taxonomy_nodes=taxonomy_nodes,
            taxonomy_assignments=taxonomy_assignments,
            start_date=start_date,
            end_date=end_date,
            taxonomy_id=taxonomy_id,
            use_period_end_taxonomy_assignments=True,
            preserve_cash_group=True,
        )
    else:
        return {}

    boundary_start_values: dict[str, float | None] = {}
    boundary_end_values: dict[str, float | None] = {}
    for item in list(detail_report.get("daily_slices") or []):
        slice_group_key = str(item.get("group_key") or "")
        as_of_date = item.get("as_of_date")
        if not slice_group_key or not isinstance(as_of_date, date):
            continue
        if as_of_date == resolved_start_date:
            boundary_start_values[slice_group_key] = (
                _period_initial_value_from_daily_slice(item)
            )
        if as_of_date == resolved_end_date:
            boundary_end_values[slice_group_key] = _safe_float(item.get("ending_value_base"))

    line_map = {
        str(item.get("group_key") or ""): item
        for item in list(detail_report.get("lines") or [])
        if str(item.get("group_key") or "")
    }
    period_return_contracts = attribution.period_return_contracts_by_group(
        list(detail_report.get("daily_slices") or [])
    )
    detail_daily_slices = list(detail_report.get("daily_slices") or [])
    child_risk_metrics = attribution.realized_risk_attribution_by_group(
        detail_daily_slices,
        portfolio_daily_series,
        calculation_frequency=risk_calculation_frequency,
        start_date=_period_risk_start_boundary_date(
            resolved_start_date,
            detail_daily_slices,
        ),
        final_date=risk_final_date,
    )
    if axis == "instrument":
        unrealized_capital_summary = {"values": {}, "coverage_complete": True}
    else:
        raw_start_snapshot = (
            _raw_period_start_snapshot(
                portfolio,
                accounts,
                transactions,
                resolved_start_date=resolved_start_date,
            )
            if resolved_start_date is not None
            else None
        )
        period_start_is_close_boundary = bool(
            resolved_start_date is not None
            and _period_start_is_close_boundary(
                transactions,
                requested_start_date=start_date,
                resolved_start_date=resolved_start_date,
                start_snapshot=raw_start_snapshot,
            )
        )
        fx_payload = get_platform_fx_rates()
        direct_fx_instruments = valuation_fx.fx_direct_instrument_map(fx_payload)
        instrument_detail_cache: dict[str, dict[str, object] | None] = {}
        unrealized_capital_summary = (
            _period_unrealized_capital_gains_by_group(
                portfolio,
                accounts,
                transactions,
                start_date=resolved_start_date,
                end_date=resolved_end_date,
                axis=detail_axis,
                taxonomy_id=taxonomy_id,
                taxonomies=taxonomies,
                taxonomy_nodes=taxonomy_nodes,
                taxonomy_assignments=taxonomy_assignments,
                base_currency=str(detail_report["base_currency"]),
                direct_fx_instruments=direct_fx_instruments,
                instrument_detail_cache=instrument_detail_cache,
                start_is_close_boundary=period_start_is_close_boundary,
            )
            if resolved_start_date is not None and resolved_end_date is not None
            else {"values": {}, "coverage_complete": False}
        )
    children_by_parent: dict[str, list[dict[str, object]]] = defaultdict(list)
    child_keys = set(line_map.keys()) | set(boundary_start_values.keys()) | set(boundary_end_values.keys())
    for candidate_child_key in sorted(child_keys):
        decoded = attribution.decode_calculation_detail_group_key(candidate_child_key)
        if decoded is None:
            continue
        parent_group_key, item_kind, item_key = decoded
        if axis == "instrument" and (parent_group_key != "cash" or item_kind != "cash"):
            continue
        parent_group_label = parent_labels.get(parent_group_key)
        if parent_group_label is None:
            continue

        line = line_map.get(candidate_child_key, {})
        initial_value = (
            boundary_start_values.get(candidate_child_key, 0.0)
            if boundary_start_values
            else _safe_float(line.get("start_value_base"))
        )
        final_value = (
            boundary_end_values.get(candidate_child_key, 0.0)
            if boundary_end_values
            else _safe_float(line.get("end_value_base"))
        )
        delta = (
            final_value - initial_value
            if initial_value is not None and final_value is not None
            else None
        )
        child_total_pnl = _safe_float(line.get("total_pnl"))
        capital_gains = _capital_gains_from_components(line)
        unrealized_capital_gains, _unrealized_split_complete = (
            _resolved_period_unrealized_capital_gain(
                group_key=candidate_child_key,
                capital_gains=capital_gains,
                unrealized_capital_summary=unrealized_capital_summary,
            )
        )
        realized_capital_gains = (
            capital_gains - unrealized_capital_gains
            if capital_gains is not None and unrealized_capital_gains is not None
            else None
        )
        residual_delta = (
            delta - child_total_pnl
            if delta is not None and child_total_pnl is not None
            else None
        )
        beginning_weight = _safe_float(line.get("beginning_weight"))
        if initial_value is not None and portfolio_start_weight_denominator is not None:
            beginning_weight = initial_value / portfolio_start_weight_denominator
        children_by_parent[parent_group_key].append(
            {
                "axis": axis,
                "taxonomy_id": taxonomy_id if axis == "taxonomy" else None,
                "parent_group_key": parent_group_key,
                "parent_group_label": parent_group_label,
                "item_key": item_key,
                "item_label": str(line.get("group_label") or item_key),
                "item_kind": item_kind,
                "beginning_weight": beginning_weight,
                "average_weight": _safe_float(line.get("average_weight")),
                "ending_weight": _safe_float(line.get("ending_weight")),
                "period_return": (
                    period_return_contracts.get(candidate_child_key, {}).get(
                        "period_return"
                    )
                ),
                "period_return_coverage_state": str(
                    period_return_contracts.get(candidate_child_key, {}).get(
                        "coverage_state"
                    )
                    or "unavailable"
                ),
                "initial_value": initial_value,
                "final_value": final_value,
                "delta": delta,
                "residual_delta": residual_delta,
                "capital_gains": capital_gains,
                "realized_capital_gains": realized_capital_gains,
                "unrealized_pnl_change": unrealized_capital_gains,
                "earnings": _safe_float(line.get("income_cash_amount")),
                "expense_cash_amount": _safe_float(line.get("expense_cash_amount")),
                "fees": _safe_float(line.get("fee_amount")),
                "taxes": _safe_float(line.get("tax_amount")),
                "cash_currency_gains": _safe_float(line.get("cash_currency_gains")),
                "pending_settlement_currency_gains": _safe_float(
                    line.get("pending_settlement_currency_gains")
                ),
                "instrument_currency_gains": _safe_float(line.get("instrument_currency_gains")),
                "total_pnl": child_total_pnl,
                "period_contribution": _safe_float(line.get("period_contribution")),
                **child_risk_metrics.get(
                    candidate_child_key,
                    attribution.risk_metric_defaults(risk_calculation_frequency),
                ),
            }
        )

    for children in children_by_parent.values():
        children.sort(
            key=lambda item: (
                0 if item.get("item_kind") == "instrument" else 1,
                -abs(_safe_float(item.get("period_contribution")) or _safe_float(item.get("total_pnl")) or 0.0),
                str(item.get("item_label") or ""),
            )
        )
    return dict(children_by_parent)


def _build_period_calculation_cash_parent_group(
    portfolio: dict[str, object],
    accounts: list[dict[str, object]],
    transactions: list[dict[str, object]],
    *,
    start_date: date | None,
    end_date: date | None,
    resolved_start_date: date | None,
    resolved_end_date: date | None,
    risk_calculation_frequency: CalculationFrequency,
) -> dict[str, object] | None:
    if resolved_start_date is None or resolved_end_date is None:
        return None

    cash_detail_report = build_contribution_report(
        portfolio,
        accounts,
        transactions,
        start_date=start_date,
        end_date=end_date,
        axis="cash_detail",
        allow_internal_detail_axis=True,
    )
    parent_daily_slices: list[dict[str, object]] = []
    for daily_slice in list(cash_detail_report.get("daily_slices") or []):
        parent_slice = dict(daily_slice)
        parent_slice["axis"] = "instrument"
        parent_slice["group_key"] = "cash"
        parent_slice["group_label"] = "Cash"
        parent_daily_slices.append(parent_slice)
    parent_daily_slices = attribution.merge_calculation_detail_daily_slices(parent_daily_slices)
    if not parent_daily_slices:
        return None

    parent_report = build_contribution_report_from_daily_slices(
        portfolio,
        [],
        parent_daily_slices,
        start_date=resolved_start_date,
        end_date=resolved_end_date,
        axis="instrument",
        group_key="cash",
    )
    line_map = {
        str(item.get("group_key") or ""): item
        for item in list(parent_report.get("lines") or [])
        if str(item.get("group_key") or "")
    }
    line = line_map.get("cash")
    if line is None:
        return None

    initial_value = None
    final_value = None
    for daily_slice in parent_daily_slices:
        as_of_date = daily_slice.get("as_of_date")
        if as_of_date == resolved_start_date:
            initial_value = _period_initial_value_from_daily_slice(daily_slice)
        if as_of_date == resolved_end_date:
            final_value = _safe_float(daily_slice.get("ending_value_base"))

    delta = (
        final_value - initial_value
        if initial_value is not None and final_value is not None
        else None
    )
    group_total_pnl = _safe_float(line.get("total_pnl"))
    capital_gains = _capital_gains_from_components(line)
    unrealized_capital_gains = 0.0 if capital_gains is not None else None
    realized_capital_gains = (
        capital_gains - unrealized_capital_gains
        if capital_gains is not None and unrealized_capital_gains is not None
        else None
    )
    residual_delta = (
        delta - group_total_pnl
        if delta is not None and group_total_pnl is not None
        else None
    )
    period_return_contract = attribution.period_return_contracts_by_group(
        parent_daily_slices
    ).get("cash", {})
    return {
        "axis": "instrument",
        "taxonomy_id": None,
        "group_key": "cash",
        "group_label": "Cash",
        "beginning_weight": _safe_float(line.get("beginning_weight")),
        "average_weight": _safe_float(line.get("average_weight")),
        "ending_weight": _safe_float(line.get("ending_weight")),
        "period_return": period_return_contract.get("period_return"),
        "period_return_coverage_state": str(
            period_return_contract.get("coverage_state") or "unavailable"
        ),
        "initial_value": initial_value,
        "final_value": final_value,
        "delta": delta,
        "residual_delta": residual_delta,
        "capital_gains": capital_gains,
        "realized_capital_gains": realized_capital_gains,
        "unrealized_pnl_change": unrealized_capital_gains,
        "earnings": _safe_float(line.get("income_cash_amount")),
        "expense_cash_amount": _safe_float(line.get("expense_cash_amount")),
        "fees": _safe_float(line.get("fee_amount")),
        "taxes": _safe_float(line.get("tax_amount")),
        "cash_currency_gains": _safe_float(line.get("cash_currency_gains")),
        "pending_settlement_currency_gains": _safe_float(
            line.get("pending_settlement_currency_gains")
        ),
        "instrument_currency_gains": _safe_float(line.get("instrument_currency_gains")),
        "total_pnl": group_total_pnl,
        "period_contribution": _safe_float(line.get("period_contribution")),
        **attribution.risk_metric_defaults(risk_calculation_frequency),
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
    contribution_report: dict[str, object] | None = None,
    detail_contribution_report: dict[str, object] | None = None,
) -> dict[str, object]:
    if contribution_report is None:
        if axis == "taxonomy":
            resolved_taxonomy_id = str(taxonomy_id or "").strip()
            taxonomy = next(
                (
                    item
                    for item in taxonomies or []
                    if str(item.get("taxonomy_id") or "") == resolved_taxonomy_id
                ),
                None,
            )
            if taxonomy is None:
                raise ValueError("Selected taxonomy was not found.")
            primary_assignment_scope = str(taxonomy.get("primary_assignment_scope") or "")
            base_axis = "account" if primary_assignment_scope in {"account", "cash_bucket"} else "instrument"
            base_contribution_report = build_contribution_report(
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
            contribution_report = build_taxonomy_contribution_report_from_base_report(
                portfolio,
                accounts,
                transactions,
                taxonomies=taxonomies,
                taxonomy_nodes=taxonomy_nodes,
                taxonomy_assignments=taxonomy_assignments,
                start_date=start_date,
                end_date=end_date,
                taxonomy_id=resolved_taxonomy_id,
                base_report=base_contribution_report,
                use_period_end_taxonomy_assignments=True,
                apply_boundary_values=False,
                preserve_cash_group=True,
            )
        else:
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
    taxonomy_uses_period_end_assignments = (
        axis == "taxonomy" and contribution_report.get("_taxonomy_assignment_mode") == "period_end"
    )
    if axis == "taxonomy" and not taxonomy_uses_period_end_assignments:
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
                boundary_start_values[slice_group_key] = (
                    _period_initial_value_from_daily_slice(item)
                )
            if as_of_date == resolved_end_date:
                boundary_end_values[slice_group_key] = _safe_float(item.get("ending_value_base"))

    line_map = {
        str(item.get("group_key") or ""): item
        for item in list(contribution_report.get("lines") or [])
        if str(item.get("group_key") or "")
    }
    contribution_daily_slices = list(contribution_report.get("daily_slices") or [])
    portfolio_daily_series = (
        list(contribution_report.get("_portfolio_daily_series") or [])
        if isinstance(contribution_report.get("_portfolio_daily_series"), list)
        else []
    )
    period_return_contracts = attribution.period_return_contracts_by_group(
        contribution_daily_slices
    )
    risk_basis_end_date = resolved_end_date or date.today()
    risk_instrument_ids = _instrument_ids_for_calculation_risk_basis(
        portfolio,
        accounts,
        transactions,
        start_date=resolved_start_date,
        end_date=risk_basis_end_date,
    )
    instrument_detail_cache = _get_calculation_instrument_details(risk_instrument_ids)

    def risk_instrument_detail(instrument_id: str) -> dict[str, object] | None:
        detail = instrument_detail_cache.get(instrument_id)
        if isinstance(detail, dict):
            return detail
        detail = get_registry_instrument_detail(instrument_id)
        instrument_detail_cache[instrument_id] = detail
        return detail

    risk_frequency_profile = calculation_frequency_profile_for_instruments(
        risk_instrument_ids,
        end_date=risk_basis_end_date,
        detail_loader=risk_instrument_detail,
    )
    risk_calculation_frequency = cast(
        CalculationFrequency,
        str(risk_frequency_profile.get("resolved_frequency") or "daily"),
    )
    risk_basis_complete = (
        str(risk_frequency_profile.get("coverage_state") or "") == "complete"
    )
    risk_start_boundary_date = _period_risk_start_boundary_date(
        resolved_start_date,
        contribution_daily_slices,
    )
    risk_metrics_by_group = (
        attribution.realized_risk_attribution_by_group(
            contribution_daily_slices,
            portfolio_daily_series,
            calculation_frequency=risk_calculation_frequency,
            start_date=risk_start_boundary_date,
            final_date=resolved_end_date,
        )
        if risk_basis_complete
        else {}
    )
    portfolio_risk_summary = (
        attribution.portfolio_realized_risk_summary(
            portfolio_daily_series,
            calculation_frequency=risk_calculation_frequency,
            start_date=risk_start_boundary_date,
            final_date=resolved_end_date,
        )
        if risk_basis_complete
        else attribution.risk_metric_defaults(risk_calculation_frequency)
    )
    fx_payload = get_platform_fx_rates()
    direct_fx_instruments = valuation_fx.fx_direct_instrument_map(fx_payload)
    raw_start_snapshot = (
        _raw_period_start_snapshot(
            portfolio,
            accounts,
            transactions,
            resolved_start_date=resolved_start_date,
        )
        if resolved_start_date is not None
        else None
    )
    period_start_is_close_boundary = bool(
        resolved_start_date is not None
        and _period_start_is_close_boundary(
            transactions,
            requested_start_date=start_date,
            resolved_start_date=resolved_start_date,
            start_snapshot=raw_start_snapshot,
        )
    )
    unrealized_capital_summary = (
        _period_unrealized_capital_gains_by_group(
            portfolio,
            accounts,
            transactions,
            start_date=resolved_start_date,
            end_date=resolved_end_date,
            axis=axis,
            taxonomy_id=str(summary.get("taxonomy_id") or taxonomy_id or "") or None,
            taxonomies=taxonomies,
            taxonomy_nodes=taxonomy_nodes,
            taxonomy_assignments=taxonomy_assignments,
            base_currency=str(contribution_report["base_currency"]),
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
            start_is_close_boundary=period_start_is_close_boundary,
        )
        if resolved_start_date is not None and resolved_end_date is not None
        else {"values": {}, "coverage_complete": False}
    )
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
        capital_gains = _capital_gains_from_components(line)
        unrealized_capital_gains, _unrealized_split_complete = (
            _resolved_period_unrealized_capital_gain(
                group_key=candidate_group_key,
                capital_gains=capital_gains,
                unrealized_capital_summary=unrealized_capital_summary,
            )
        )
        realized_capital_gains = (
            capital_gains - unrealized_capital_gains
            if capital_gains is not None and unrealized_capital_gains is not None
            else None
        )
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
                "beginning_weight": _safe_float(line.get("beginning_weight")),
                "average_weight": _safe_float(line.get("average_weight")),
                "ending_weight": _safe_float(line.get("ending_weight")),
                "period_return": (
                    period_return_contracts.get(candidate_group_key, {}).get(
                        "period_return"
                    )
                ),
                "period_return_coverage_state": str(
                    period_return_contracts.get(candidate_group_key, {}).get(
                        "coverage_state"
                    )
                    or "unavailable"
                ),
                "initial_value": initial_value,
                "final_value": final_value,
                "delta": delta,
                "residual_delta": residual_delta,
                "capital_gains": capital_gains,
                "realized_capital_gains": realized_capital_gains,
                "unrealized_pnl_change": unrealized_capital_gains,
                "earnings": _safe_float(line.get("income_cash_amount")),
                "expense_cash_amount": _safe_float(line.get("expense_cash_amount")),
                "fees": _safe_float(line.get("fee_amount")),
                "taxes": _safe_float(line.get("tax_amount")),
                "cash_currency_gains": _safe_float(line.get("cash_currency_gains")),
                "pending_settlement_currency_gains": _safe_float(
                    line.get("pending_settlement_currency_gains")
                ),
                "instrument_currency_gains": _safe_float(line.get("instrument_currency_gains")),
                "total_pnl": group_total_pnl,
                "period_contribution": _safe_float(line.get("period_contribution")),
                **risk_metrics_by_group.get(
                    candidate_group_key,
                    attribution.risk_metric_defaults(risk_calculation_frequency),
                ),
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

    if axis == "instrument" and not any(str(item.get("group_key") or "") == "cash" for item in groups):
        cash_parent_group = _build_period_calculation_cash_parent_group(
            portfolio,
            accounts,
            transactions,
            start_date=start_date,
            end_date=end_date,
            resolved_start_date=resolved_start_date,
            resolved_end_date=resolved_end_date,
            risk_calculation_frequency=risk_calculation_frequency,
        )
        if cash_parent_group is not None:
            cash_parent_group.update(
                risk_metrics_by_group.get(
                    "cash",
                    attribution.risk_metric_defaults(risk_calculation_frequency),
                )
            )
            groups.append(cash_parent_group)

    full_period_initial_value_total = 0.0
    full_period_initial_value_complete = True
    for item in groups:
        initial_value = _safe_float(item.get("initial_value"))
        if initial_value is None:
            full_period_initial_value_complete = False
        else:
            full_period_initial_value_total += initial_value
    full_period_start_weight_denominator = (
        full_period_initial_value_total
        if full_period_initial_value_complete and full_period_initial_value_total > 1e-9
        else None
    )
    for item in groups:
        initial_value = _safe_float(item.get("initial_value"))
        item["beginning_weight"] = (
            initial_value / full_period_start_weight_denominator
            if initial_value is not None and full_period_start_weight_denominator is not None
            else _safe_float(item.get("beginning_weight"))
        )

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
    start_weight_denominator = total_initial_value if initial_value_complete and total_initial_value > 1e-9 else None
    end_weight_denominator = total_final_value if final_value_complete and total_final_value > 1e-9 else None
    for item in groups:
        initial_value = _safe_float(item.get("initial_value"))
        final_value = _safe_float(item.get("final_value"))
        ending_weight = _safe_float(item.get("ending_weight"))
        if ending_weight is None and final_value is not None and end_weight_denominator is not None:
            ending_weight = final_value / end_weight_denominator
            item["ending_weight"] = ending_weight
        if _safe_float(item.get("average_weight")) is None:
            start_weight = _safe_float(item.get("beginning_weight"))
            if start_weight is None and initial_value is not None and start_weight_denominator is not None:
                start_weight = initial_value / start_weight_denominator
            if start_weight is not None and ending_weight is not None:
                item["average_weight"] = (start_weight + ending_weight) / 2.0
            elif start_weight is not None:
                item["average_weight"] = start_weight
            elif ending_weight is not None:
                item["average_weight"] = ending_weight

    children_by_parent = _build_period_calculation_child_records(
        portfolio,
        accounts,
        transactions,
        taxonomies=taxonomies,
        taxonomy_nodes=taxonomy_nodes,
        taxonomy_assignments=taxonomy_assignments,
        start_date=start_date,
        end_date=end_date,
        resolved_start_date=resolved_start_date,
        resolved_end_date=resolved_end_date,
        axis=axis,
        taxonomy_id=str(summary.get("taxonomy_id") or taxonomy_id or "") or None,
        parent_groups=groups,
        risk_calculation_frequency=risk_calculation_frequency,
        portfolio_daily_series=portfolio_daily_series,
        risk_final_date=resolved_end_date,
        portfolio_start_weight_denominator=full_period_start_weight_denominator,
        detail_contribution_report=detail_contribution_report,
    )
    for item in groups:
        item["children"] = children_by_parent.get(str(item.get("group_key") or ""), [])
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
            "requested_start_date": summary.get("requested_start_date"),
            "requested_end_date": summary.get("requested_end_date"),
            "effective_start_date": summary.get("effective_start_date"),
            "effective_end_date": summary.get("effective_end_date"),
            "as_of_clamp_reason": summary.get("as_of_clamp_reason"),
            "start_boundary_kind": summary.get("start_boundary_kind"),
            "include_start_date_return": bool(
                summary.get("include_start_date_return")
            ),
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
            "risk_calculation_frequency": portfolio_risk_summary.get("risk_calculation_frequency"),
            "risk_frequency_status_label": risk_frequency_profile.get("status_label"),
            "risk_basis_coverage_state": risk_frequency_profile.get(
                "coverage_state"
            ),
            "risk_basis_requested_instrument_count": risk_frequency_profile.get(
                "requested_instrument_count"
            ),
            "risk_basis_resolved_instrument_count": risk_frequency_profile.get(
                "resolved_instrument_count"
            ),
            "risk_return_observation_count": portfolio_risk_summary.get("risk_return_observation_count"),
            "risk_annualization_periods_per_year": portfolio_risk_summary.get(
                "risk_annualization_periods_per_year"
            ),
            "annualized_volatility": portfolio_risk_summary.get("annualized_volatility"),
            "sharpe_ratio": portfolio_risk_summary.get("sharpe_ratio"),
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
    fx_payload = get_platform_fx_rates()
    direct_fx_instruments = valuation_fx.fx_direct_instrument_map(fx_payload)
    instrument_detail_cache: dict[str, dict[str, object] | None] = {}
    unrealized_capital_cache: dict[tuple[date, date], dict[str, object]] = {}

    def unrealized_capital_for_bucket(bucket_start: date | None, bucket_end: date | None) -> dict[str, object]:
        if bucket_start is None or bucket_end is None:
            return {"values": {}, "coverage_complete": False}
        cache_key = (bucket_start, bucket_end)
        if cache_key not in unrealized_capital_cache:
            unrealized_capital_cache[cache_key] = _period_unrealized_capital_gains_by_group(
                portfolio,
                accounts,
                transactions,
                start_date=bucket_start,
                end_date=bucket_end,
                axis=axis,
                taxonomy_id=str(summary.get("taxonomy_id") or taxonomy_id or "") or None,
                taxonomies=taxonomies,
                taxonomy_nodes=taxonomy_nodes,
                taxonomy_assignments=taxonomy_assignments,
                base_currency=str(contribution_calendar_report["base_currency"]),
                direct_fx_instruments=direct_fx_instruments,
                instrument_detail_cache=instrument_detail_cache,
                start_is_close_boundary=True,
            )
        return unrealized_capital_cache[cache_key]

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
        bucket_start = _parse_iso_date(bucket.get("start_date"))
        bucket_end = _parse_iso_date(bucket.get("end_date"))
        bucket_group_key = str(bucket.get("group_key") or "")
        delta = (
            final_value - initial_value
            if initial_value is not None and final_value is not None
            else None
        )
        bucket_total_pnl = _safe_float(bucket.get("total_pnl"))
        capital_gains = _capital_gains_from_components(bucket)
        unrealized_capital_summary = unrealized_capital_for_bucket(bucket_start, bucket_end)
        unrealized_capital_gains, unrealized_split_complete = (
            _resolved_period_unrealized_capital_gain(
                group_key=bucket_group_key,
                capital_gains=capital_gains,
                unrealized_capital_summary=unrealized_capital_summary,
            )
        )
        realized_capital_gains = (
            capital_gains - unrealized_capital_gains
            if capital_gains is not None and unrealized_capital_gains is not None
            else None
        )
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
                "group_key": bucket_group_key,
                "group_label": str(bucket.get("group_label") or bucket_group_key),
                "coverage_state": (
                    str(bucket.get("coverage_state") or "unavailable")
                    if unrealized_split_complete
                    else "partial"
                ),
                "observation_count": int(bucket.get("observation_count") or 0),
                "beginning_weight": _safe_float(bucket.get("beginning_weight")),
                "average_weight": _safe_float(bucket.get("average_weight")),
                "ending_weight": _safe_float(bucket.get("ending_weight")),
                "initial_value": initial_value,
                "final_value": final_value,
                "delta": delta,
                "residual_delta": residual_delta,
                "capital_gains": capital_gains,
                "realized_capital_gains": realized_capital_gains,
                "unrealized_pnl_change": unrealized_capital_gains,
                "earnings": _safe_float(bucket.get("income_cash_amount")),
                "expense_cash_amount": _safe_float(bucket.get("expense_cash_amount")),
                "fees": _safe_float(bucket.get("fee_amount")),
                "taxes": _safe_float(bucket.get("tax_amount")),
                "cash_currency_gains": _safe_float(bucket.get("cash_currency_gains")),
                "pending_settlement_currency_gains": _safe_float(
                    bucket.get("pending_settlement_currency_gains")
                ),
                "instrument_currency_gains": _safe_float(bucket.get("instrument_currency_gains")),
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
    "beginning_weight": "beginning_weight",
    "capital_gains": "capital_gains",
    "realized_capital_gains": "realized_capital_gains",
    "unrealized_capital_gains": "unrealized_pnl_change",
    "earnings": "earnings",
    "fees": "fees",
    "taxes": "taxes",
    "cash_currency_gains": "cash_currency_gains",
    "pending_settlement_currency_gains": "pending_settlement_currency_gains",
    "instrument_currency_gains": "instrument_currency_gains",
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
                str(item.get("assignment_id") or ""),
            )
        )
    return taxonomy, taxonomy_nodes_by_id, assignments_by_entity, target_scope


def _resolve_calculation_entry_group(
    *,
    axis: str,
    effective_date: date | None,
    account_id: str | None,
    account_name_map: dict[str, str],
    instrument_id: str | None,
    instrument_name: str | None,
    instrument_type: str | None,
    currency: str | None,
    taxonomy_context: tuple[dict[str, object], dict[str, dict[str, object]], dict[str, list[dict[str, object]]], str]
    | None,
) -> tuple[str, str]:
    if axis == "instrument":
        if instrument_id:
            return (instrument_id, instrument_name or instrument_id)
        return ("cash", "Cash")
    if axis == "account":
        if account_id:
            return (account_id, account_name_map.get(account_id, account_id))
        return ("unassigned:account", "Unassigned")
    if axis == "instrument_type":
        if instrument_id:
            return attribution.instrument_type_key_label(instrument_type)
        return ("cash", "Cash")
    if axis == "currency":
        normalized_currency = valuation_fx.normalized_currency(currency)
        if normalized_currency:
            return (normalized_currency, normalized_currency)
        return ("unassigned:currency", "Unassigned")
    if axis != "taxonomy":
        raise ValueError(attribution.CONTRIBUTION_AXIS_ERROR)

    if taxonomy_context is None:
        raise ValueError("taxonomy_id is required when axis=taxonomy.")
    taxonomy, taxonomy_nodes_by_id, assignments_by_entity, target_scope = taxonomy_context
    target_entity_id = ""
    if target_scope == "instrument":
        target_entity_id = instrument_id or ""
    elif target_scope == "account":
        target_entity_id = account_id or ""
    elif target_scope == "cash_bucket":
        target_entity_id = account_id or ""
    if not target_entity_id or effective_date is None:
        return (f"unassigned:{taxonomy.get('taxonomy_id')}", "Unassigned")
    return attribution.resolve_taxonomy_group_for_date(
        taxonomy=taxonomy,
        taxonomy_nodes_by_id=taxonomy_nodes_by_id,
        assignments_by_entity=assignments_by_entity,
        target_entity_id=target_entity_id,
        as_of_date=effective_date,
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
    direct_fx_instruments: dict[tuple[str, str], str],
    instrument_detail_cache: dict[str, dict[str, object] | None],
    account_name_map: dict[str, str],
    taxonomy_context: tuple[dict[str, object], dict[str, dict[str, object]], dict[str, list[dict[str, object]]], str]
    | None,
) -> None:
    trade_date = _parse_iso_date(transaction.get("trade_date"))
    settlement_date = _parse_iso_date(transaction.get("settlement_date"))
    effective_date = transaction_performance_effective_date(transaction)
    currency = valuation_fx.required_currency(
        transaction.get("currency"), field_name="transaction currency"
    )
    account_id = str(transaction.get("account_id") or "") or None
    instrument_id = str(transaction.get("instrument_id") or "") or None
    instrument_ref = (
        transaction.get("instrument_ref")
        if isinstance(transaction.get("instrument_ref"), dict)
        else None
    )
    instrument_name = str((instrument_ref or {}).get("instrument_name") or instrument_id or "")
    group_key, group_label = _resolve_calculation_entry_group(
        axis=axis,
        effective_date=effective_date,
        account_id=account_id,
        account_name_map=account_name_map,
        instrument_id=instrument_id,
        instrument_name=instrument_name or None,
        instrument_type=str((instrument_ref or {}).get("instrument_type") or "") or None,
        currency=currency,
        taxonomy_context=taxonomy_context,
    )
    base_amount = None
    stale_fx_flag = False
    if effective_date is not None:
        base_amount, stale_fx_flag = valuation_fx.convert_amount_on(
            local_amount,
            as_of_date=effective_date,
            from_currency=currency,
            to_currency=base_currency,
            direct_fx_instruments=direct_fx_instruments,
            instrument_detail_cache=instrument_detail_cache,
            instrument_detail_loader=get_registry_instrument_detail,
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
            "effective_date": effective_date,
            "group_key": group_key,
            "group_label": group_label,
            "account_id": account_id,
            "account_name": account_name_map.get(account_id, account_id or "") if account_id else None,
            "instrument_id": instrument_id,
            "instrument_name": instrument_name or None,
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
    if axis not in attribution.CONTRIBUTION_AXES:
        raise ValueError(attribution.CONTRIBUTION_AXIS_ERROR)

    window = _resolve_snapshot_window(
        portfolio,
        transactions,
        start_date=start_date,
        end_date=end_date,
    )
    base_currency = valuation_fx.required_currency(
        portfolio.get("base_currency"), field_name="portfolio base currency"
    )
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
    sorted_transactions = sorted(transactions, key=transaction_sort_key)
    raw_start_snapshot = _raw_period_start_snapshot(
        portfolio,
        accounts,
        sorted_transactions,
        resolved_start_date=resolved_start_date,
    )
    start_is_close_boundary = _period_start_is_close_boundary(
        sorted_transactions,
        requested_start_date=start_date,
        resolved_start_date=resolved_start_date,
        start_snapshot=raw_start_snapshot,
    )
    period_transactions = _transactions_in_period(
        sorted_transactions,
        start_date=resolved_start_date,
        end_date=resolved_end_date,
        include_start_date=not start_is_close_boundary,
    )
    end_boundary_transactions = _transactions_as_of_end_date(
        sorted_transactions,
        end_date=resolved_end_date,
    )

    account_name_map = attribution.account_name_map(accounts)
    fx_payload = get_platform_fx_rates()
    direct_fx_instruments = valuation_fx.fx_direct_instrument_map(fx_payload)
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
            cash_bucket_account_ids = attribution.cash_bucket_account_ids(accounts)

    entries: list[dict[str, object]] = []
    if bucket != "realized_pnl":
        for transaction in period_transactions:
            transaction_type = str(transaction.get("transaction_type") or "")
            gross_amount = _safe_float(transaction.get("gross_amount")) or 0.0
            fee_amount = _safe_float(transaction.get("fees")) or 0.0
            tax_amount = _safe_float(transaction.get("taxes")) or 0.0
            instrument_id = str(transaction.get("instrument_id") or "")
            account_id = str(transaction.get("account_id") or "")
            if (
                taxonomy_context is not None
                and taxonomy_context[3] == "cash_bucket"
                and account_id not in cash_bucket_account_ids
            ):
                continue

            income_account_scoped = attribution.axis_includes_cash_balance(axis) or (
                taxonomy_context is not None and taxonomy_context[3] in {"account", "cash_bucket"}
            )

            if bucket == "income_cash_amount":
                if transaction_type in {"dividend", "coupon", "dividend_reinvestment"}:
                    if axis == "instrument" and not instrument_id:
                        continue
                    _append_calculation_transaction_entry(
                        entries,
                        axis=axis,
                        bucket=bucket,
                        transaction=transaction,
                        component_kind="gross_amount",
                        local_amount=gross_amount,
                        base_currency=base_currency,
                        direct_fx_instruments=direct_fx_instruments,
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
                        direct_fx_instruments=direct_fx_instruments,
                        instrument_detail_cache=instrument_detail_cache,
                        account_name_map=account_name_map,
                        taxonomy_context=taxonomy_context,
                    )
            elif bucket == "expense_cash_amount":
                if transaction_type in {"fee", "tax"}:
                    _append_calculation_transaction_entry(
                        entries,
                        axis=axis,
                        bucket=bucket,
                        transaction=transaction,
                        component_kind="gross_amount",
                        local_amount=gross_amount,
                        base_currency=base_currency,
                        direct_fx_instruments=direct_fx_instruments,
                        instrument_detail_cache=instrument_detail_cache,
                        account_name_map=account_name_map,
                        taxonomy_context=taxonomy_context,
                    )
                attached_expense = fee_amount + tax_amount
                if attached_expense > 0 and (
                    transaction_type in NON_CAPITALIZED_ATTACHED_CHARGE_TRANSACTION_TYPES
                ):
                    _append_calculation_transaction_entry(
                        entries,
                        axis=axis,
                        bucket=bucket,
                        transaction=transaction,
                        component_kind="attached_expense",
                        local_amount=attached_expense,
                        base_currency=base_currency,
                        direct_fx_instruments=direct_fx_instruments,
                        instrument_detail_cache=instrument_detail_cache,
                        account_name_map=account_name_map,
                        taxonomy_context=taxonomy_context,
                    )
            elif bucket == "fee_amount":
                if transaction_type == "fee":
                    _append_calculation_transaction_entry(
                        entries,
                        axis=axis,
                        bucket=bucket,
                        transaction=transaction,
                        component_kind="gross_amount",
                        local_amount=gross_amount,
                        base_currency=base_currency,
                        direct_fx_instruments=direct_fx_instruments,
                        instrument_detail_cache=instrument_detail_cache,
                        account_name_map=account_name_map,
                        taxonomy_context=taxonomy_context,
                    )
                if fee_amount > 0:
                    _append_calculation_transaction_entry(
                        entries,
                        axis=axis,
                        bucket=bucket,
                        transaction=transaction,
                        component_kind="attached_fee",
                        local_amount=fee_amount,
                        base_currency=base_currency,
                        direct_fx_instruments=direct_fx_instruments,
                        instrument_detail_cache=instrument_detail_cache,
                        account_name_map=account_name_map,
                        taxonomy_context=taxonomy_context,
                    )
            elif bucket == "tax_amount":
                if transaction_type == "tax":
                    _append_calculation_transaction_entry(
                        entries,
                        axis=axis,
                        bucket=bucket,
                        transaction=transaction,
                        component_kind="gross_amount",
                        local_amount=gross_amount,
                        base_currency=base_currency,
                        direct_fx_instruments=direct_fx_instruments,
                        instrument_detail_cache=instrument_detail_cache,
                        account_name_map=account_name_map,
                        taxonomy_context=taxonomy_context,
                    )
                if tax_amount > 0:
                    _append_calculation_transaction_entry(
                        entries,
                        axis=axis,
                        bucket=bucket,
                        transaction=transaction,
                        component_kind="attached_tax",
                        local_amount=tax_amount,
                        base_currency=base_currency,
                        direct_fx_instruments=direct_fx_instruments,
                        instrument_detail_cache=instrument_detail_cache,
                        account_name_map=account_name_map,
                        taxonomy_context=taxonomy_context,
                    )
    else:
        position_lots = build_position_lots(
            str(portfolio.get("portfolio_id") or ""),
            accounts,
            end_boundary_transactions,
            as_of_date=resolved_end_date,
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
            instrument_id = str(position_lot.get("instrument_id") or "") or None
            instrument_name = str((instrument_ref or {}).get("instrument_name") or instrument_id or "")
            currency = valuation_fx.required_currency(
                position_lot.get("currency"), field_name="position-lot currency"
            )
            for realization in realizations:
                if not isinstance(realization, dict):
                    continue
                transaction_type = str(realization.get("transaction_type") or "")
                if transaction_type not in REALIZED_GAIN_TRANSACTION_TYPES:
                    continue
                trade_date = _parse_iso_date(realization.get("trade_date"))
                realization_date = _parse_iso_date(
                    realization.get("position_effective_date")
                    or realization.get("trade_date")
                )
                if trade_date is None or realization_date is None:
                    continue
                if (
                    realization_date > resolved_end_date
                    or (
                        realization_date <= resolved_start_date
                        if start_is_close_boundary
                        else realization_date < resolved_start_date
                    )
                ):
                    continue
                local_amount = _safe_float(realization.get("realized_pnl"))
                realization_group_key, realization_group_label = _resolve_calculation_entry_group(
                    axis=axis,
                    effective_date=realization_date,
                    account_id=account_id,
                    account_name_map=account_name_map,
                    instrument_id=instrument_id,
                    instrument_name=instrument_name or None,
                    instrument_type=str((instrument_ref or {}).get("instrument_type") or "") or None,
                    currency=currency,
                    taxonomy_context=taxonomy_context,
                )
                base_amount = None
                stale_fx_flag = False
                if local_amount is not None:
                    base_amount, stale_fx_flag = valuation_fx.convert_amount_on(
                        local_amount,
                        as_of_date=realization_date,
                        from_currency=currency,
                        to_currency=base_currency,
                        direct_fx_instruments=direct_fx_instruments,
                        instrument_detail_cache=instrument_detail_cache,
                        instrument_detail_loader=get_registry_instrument_detail,
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
                        "effective_date": realization_date,
                        "group_key": realization_group_key,
                        "group_label": realization_group_label,
                        "account_id": account_id,
                        "account_name": account_name_map.get(account_id, account_id or "") if account_id else None,
                        "instrument_id": instrument_id,
                        "instrument_name": instrument_name or None,
                        "currency": currency,
                        "local_amount": local_amount,
                        "base_amount": base_amount,
                        "note": None,
                        "stale_fx_flag": stale_fx_flag,
                    }
                )

    entries.sort(
        key=lambda item: (
            item.get("effective_date") or item.get("trade_date") or date.min,
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
        effective_date = entry.get("effective_date") or entry.get("trade_date")
        if not isinstance(effective_date, date):
            continue
        entry_group_key = str(entry.get("group_key") or "")
        if not entry_group_key:
            continue
        bucket_key = _calendar_bucket_key(effective_date, frequency)
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
                "start_date": effective_date,
                "end_date": effective_date,
                "entry_count": 0,
                "total_amount": 0.0,
                "_amount_complete": True,
            },
        )
        if effective_date < accumulator["start_date"]:
            accumulator["start_date"] = effective_date
        if effective_date > accumulator["end_date"]:
            accumulator["end_date"] = effective_date
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
    if axis not in attribution.CONTRIBUTION_AXES:
        raise ValueError(attribution.CONTRIBUTION_AXIS_ERROR)

    window = _resolve_snapshot_window(
        portfolio,
        transactions,
        start_date=start_date,
        end_date=end_date,
    )
    base_currency = valuation_fx.required_currency(
        portfolio.get("base_currency"), field_name="portfolio base currency"
    )
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
    sorted_transactions = sorted(transactions, key=transaction_sort_key)
    raw_start_snapshot = _raw_period_start_snapshot(
        portfolio,
        accounts,
        sorted_transactions,
        resolved_start_date=resolved_start_date,
    )
    start_is_close_boundary = _period_start_is_close_boundary(
        sorted_transactions,
        requested_start_date=start_date,
        resolved_start_date=resolved_start_date,
        start_snapshot=raw_start_snapshot,
    )
    period_transactions = _transactions_in_period(
        sorted_transactions,
        start_date=resolved_start_date,
        end_date=resolved_end_date,
        include_start_date=not start_is_close_boundary,
    )
    end_boundary_transactions = _transactions_as_of_end_date(
        sorted_transactions,
        end_date=resolved_end_date,
    )

    account_name_map = attribution.account_name_map(accounts)
    fx_payload = get_platform_fx_rates()
    direct_fx_instruments = valuation_fx.fx_direct_instrument_map(fx_payload)
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
                    direct_fx_instruments=direct_fx_instruments,
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
                    direct_fx_instruments=direct_fx_instruments,
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
                    direct_fx_instruments=direct_fx_instruments,
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
                        direct_fx_instruments=direct_fx_instruments,
                        instrument_detail_cache=instrument_detail_cache,
                        account_name_map=account_name_map,
                        taxonomy_context=taxonomy_context,
                    )
                if fee_amount > 0:
                    _append_calculation_transaction_entry(
                        entries,
                        axis=axis,
                        bucket=bucket,
                        transaction=transaction,
                        component_kind="attached_fee",
                        local_amount=fee_amount,
                        base_currency=base_currency,
                        direct_fx_instruments=direct_fx_instruments,
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
                        direct_fx_instruments=direct_fx_instruments,
                        instrument_detail_cache=instrument_detail_cache,
                        account_name_map=account_name_map,
                        taxonomy_context=taxonomy_context,
                    )
                if tax_amount > 0:
                    _append_calculation_transaction_entry(
                        entries,
                        axis=axis,
                        bucket=bucket,
                        transaction=transaction,
                        component_kind="attached_tax",
                        local_amount=tax_amount,
                        base_currency=base_currency,
                        direct_fx_instruments=direct_fx_instruments,
                        instrument_detail_cache=instrument_detail_cache,
                        account_name_map=account_name_map,
                        taxonomy_context=taxonomy_context,
                    )
    else:
        position_lots = build_position_lots(
            str(portfolio.get("portfolio_id") or ""),
            accounts,
            end_boundary_transactions,
            as_of_date=resolved_end_date,
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
            instrument_id = str(position_lot.get("instrument_id") or "") or None
            instrument_name = str((instrument_ref or {}).get("instrument_name") or instrument_id or "")
            currency = valuation_fx.required_currency(
                position_lot.get("currency"), field_name="position-lot currency"
            )
            for realization in realizations:
                if not isinstance(realization, dict):
                    continue
                transaction_type = str(realization.get("transaction_type") or "")
                if transaction_type not in REALIZED_GAIN_TRANSACTION_TYPES:
                    continue
                trade_date = _parse_iso_date(realization.get("trade_date"))
                realization_date = _parse_iso_date(
                    realization.get("position_effective_date")
                    or realization.get("trade_date")
                )
                if trade_date is None or realization_date is None:
                    continue
                if (
                    realization_date > resolved_end_date
                    or (
                        realization_date <= resolved_start_date
                        if start_is_close_boundary
                        else realization_date < resolved_start_date
                    )
                ):
                    continue
                local_amount = _safe_float(realization.get("realized_pnl"))
                realization_group_key, realization_group_label = _resolve_calculation_entry_group(
                    axis=axis,
                    effective_date=realization_date,
                    account_id=account_id,
                    account_name_map=account_name_map,
                    instrument_id=instrument_id,
                    instrument_name=instrument_name or None,
                    instrument_type=str((instrument_ref or {}).get("instrument_type") or "") or None,
                    currency=currency,
                    taxonomy_context=taxonomy_context,
                )
                base_amount = None
                stale_fx_flag = False
                if local_amount is not None:
                    base_amount, stale_fx_flag = valuation_fx.convert_amount_on(
                        local_amount,
                        as_of_date=realization_date,
                        from_currency=currency,
                        to_currency=base_currency,
                        direct_fx_instruments=direct_fx_instruments,
                        instrument_detail_cache=instrument_detail_cache,
                        instrument_detail_loader=get_registry_instrument_detail,
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
                        "effective_date": realization_date,
                        "group_key": realization_group_key,
                        "group_label": realization_group_label,
                        "account_id": account_id,
                        "account_name": account_name_map.get(account_id, account_id or "") if account_id else None,
                        "instrument_id": instrument_id,
                        "instrument_name": instrument_name or None,
                        "currency": currency,
                        "local_amount": local_amount,
                        "base_amount": base_amount,
                        "note": None,
                        "stale_fx_flag": stale_fx_flag,
                    }
                )

    entries.sort(
        key=lambda item: (
            item.get("effective_date") or item.get("trade_date") or date.min,
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
        effective_date = entry.get("effective_date") or entry.get("trade_date")
        if not isinstance(effective_date, date):
            continue
        entry_group_key = str(entry.get("group_key") or "")
        if not entry_group_key:
            continue
        bucket_key = _calendar_bucket_key(effective_date, frequency)
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
                "start_date": effective_date,
                "end_date": effective_date,
                "entry_count": 0,
                "total_amount": 0.0,
                "_amount_complete": True,
            },
        )
        if effective_date < accumulator["start_date"]:
            accumulator["start_date"] = effective_date
        if effective_date > accumulator["end_date"]:
            accumulator["end_date"] = effective_date
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
