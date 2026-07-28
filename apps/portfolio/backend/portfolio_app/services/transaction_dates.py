from __future__ import annotations

from datetime import date


ENTITLEMENT_ACCRUAL_TRANSACTION_TYPES = frozenset({"dividend", "coupon"})
EXTERNAL_FLOW_TRANSACTION_TYPES = frozenset({"deposit", "withdrawal"})
POSITION_EFFECTIVE_TRANSACTION_TYPES = frozenset(
    {
        "buy",
        "sell",
        "opening_balance",
        "dividend_reinvestment",
        "maturity_redemption",
    }
)
POSITION_CASH_TRANSFER_TRANSACTION_TYPES = frozenset(
    {"buy", "sell", "maturity_redemption"}
)


def _transaction_has_position_effect(
    transaction: dict[str, object],
) -> bool:
    transaction_type = str(transaction.get("transaction_type") or "").strip()
    if transaction_type in POSITION_EFFECTIVE_TRANSACTION_TYPES:
        return bool(transaction.get("instrument_id")) or transaction_type != "opening_balance"
    return (
        transaction_type in {"transfer_in", "transfer_out"}
        and str(transaction.get("transfer_object_type") or "").strip() == "position"
    )


def transaction_position_effective_date(
    transaction: dict[str, object],
) -> date | None:
    """Return the EOD position-recognition boundary for a transaction.

    Most exchange trades become part of the same day's closing position, so
    legacy facts and requests that omit ``position_effective_date`` retain
    ``trade_date`` behavior.  Fund subscriptions/redemptions and other
    confirmed-later facts can carry an explicit later date without falsifying
    their order, execution, or pricing date.
    """

    if not _transaction_has_position_effect(transaction):
        return None
    trade_date = _parse_date(transaction.get("trade_date"))
    return (
        _parse_date(transaction.get("position_effective_date"))
        or trade_date
    )


def transaction_sort_key(
    transaction: dict[str, object],
) -> tuple[str, str, str, str, str]:
    """Return the canonical deterministic economic-recognition ordering key."""

    effective_date = transaction_performance_effective_date(transaction)

    return (
        effective_date.isoformat() if effective_date is not None else "",
        str(transaction.get("trade_at") or ""),
        str(transaction.get("created_at") or ""),
        str(transaction.get("transaction_id") or ""),
        str(transaction.get("settlement_date") or ""),
    )


def transaction_execution_sort_key(
    transaction: dict[str, object],
) -> tuple[str, str, str, str, str]:
    """Return deterministic order-entry/execution ordering.

    This is intentionally separate from the economic-recognition sort. A
    confirmed-later acquisition is not available to a disposal placed before
    that acquisition's position-effective date.
    """

    return (
        str(transaction.get("trade_date") or ""),
        str(transaction.get("trade_at") or ""),
        str(transaction.get("created_at") or ""),
        str(transaction.get("transaction_id") or ""),
        str(transaction.get("settlement_date") or ""),
    )


def _parse_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if normalized:
            try:
                return date.fromisoformat(normalized[:10])
            except ValueError:
                return None
    return None


def transaction_precedes_entitlement_bod(
    transaction: dict[str, object],
    entitlement_date: date,
) -> bool:
    """Whether a position fact belongs to entitlement-date beginning of day.

    Same-day ordinary trades are excluded: a same-day purchase is not entitled,
    while a same-day sale has not reduced the opening position.  A same-day
    opening balance is included only when its acquisition date proves earlier
    ownership.
    """

    position_effective_date = transaction_position_effective_date(transaction)
    if position_effective_date is None:
        return False
    if position_effective_date < entitlement_date:
        return True
    if position_effective_date > entitlement_date:
        return False
    if str(transaction.get("transaction_type") or "") != "opening_balance":
        return False
    acquisition_date = _parse_date(transaction.get("acquisition_date"))
    return acquisition_date is not None and acquisition_date < entitlement_date


def transaction_economic_date(transaction: dict[str, object]) -> date | None:
    """Return the date on which the economic fact is recognized.

    Security income is recognized from entitlement. Position-changing facts
    are recognized from their explicit holding boundary (falling back to the
    trade date for all legacy and same-day trades). Cash settlement remains a
    separate fact.
    """

    transaction_type = str(transaction.get("transaction_type") or "").strip()
    if transaction_type in ENTITLEMENT_ACCRUAL_TRANSACTION_TYPES:
        entitlement_date = _parse_date(transaction.get("entitlement_date"))
        if entitlement_date is not None:
            return entitlement_date
    if _transaction_has_position_effect(transaction):
        return transaction_position_effective_date(transaction)
    return _parse_date(transaction.get("trade_date"))


def transaction_external_flow_date(transaction: dict[str, object]) -> date | None:
    """Return the portfolio-boundary cash-flow date, if this is one.

    Deposits and withdrawals enter the cash ledger on their settlement date;
    they must be neutralized in TWR on that same date, not prematurely on the
    order/trade date.  ``external_flow_date`` remains an additive override for
    imported facts; records without one derive deterministically.
    """

    transaction_type = str(transaction.get("transaction_type") or "").strip()
    if transaction_type not in EXTERNAL_FLOW_TRANSACTION_TYPES:
        return None
    return (
        _parse_date(transaction.get("external_flow_date"))
        or _parse_date(transaction.get("settlement_date"))
        or _parse_date(transaction.get("trade_date"))
    )


def transaction_performance_effective_date(
    transaction: dict[str, object],
) -> date | None:
    """Return the canonical date used by NAV/performance recognition."""

    return transaction_external_flow_date(transaction) or transaction_economic_date(
        transaction
    )


def transaction_ledger_activity_date(
    transaction: dict[str, object],
) -> date | None:
    """Return the first date on which any ledger leg can affect NAV.

    Usually this is the performance-effective date. A fund subscription can
    settle cash before units become an EOD position; that earlier date starts
    a recognition-bridge asset instead of making units appear prematurely.
    """

    performance_effective_date = transaction_performance_effective_date(
        transaction
    )
    position_cash_transfer_date = transaction_position_cash_transfer_date(
        transaction
    )
    candidate_dates = [
        candidate
        for candidate in (
            performance_effective_date,
            position_cash_transfer_date,
        )
        if candidate is not None
    ]
    return min(candidate_dates) if candidate_dates else None


def transaction_position_cash_transfer_date(
    transaction: dict[str, object],
) -> date | None:
    """Return when value first moves between cash and a position group.

    A conventional exchange trade first changes the EOD position and may leave
    an unsettled cash balance, so the transfer starts on the position-effective
    date. A confirmed-later fund order can debit or credit cash first; in that
    case a position-linked recognition bridge starts on the cash settlement
    date. The later conversion between that bridge and units is not a second
    capital transfer.
    """

    transaction_type = str(transaction.get("transaction_type") or "").strip()
    if transaction_type not in POSITION_CASH_TRANSFER_TRANSACTION_TYPES:
        return None

    position_effective_date = transaction_position_effective_date(transaction)
    settlement_cash_account_id = str(
        transaction.get("settlement_cash_account_id") or ""
    ).strip()
    settlement_date = (
        _parse_date(transaction.get("settlement_date"))
        if settlement_cash_account_id
        else None
    )
    candidate_dates = [
        candidate
        for candidate in (position_effective_date, settlement_date)
        if candidate is not None
    ]
    return min(candidate_dates) if candidate_dates else None
