"""Replayable short-option obligation subledger.

Short options are not represented as negative long lots.  This module keeps
an auditable, deterministic read model that can be rebuilt from canonical
transaction facts at any as-of date.  The carrying liability is the remaining
unearned premium basis (no daily option quote is required in P0).
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Iterable

from portfolio_app.services.option_actions import resolve_option_action
from portfolio_app.services.transaction_dates import (
    transaction_performance_effective_date,
    transaction_sort_key,
)


EPSILON = 1e-9


def _float(value: object, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _contract(transaction: dict[str, object]) -> dict[str, object] | None:
    contract = transaction.get("derivative_contract")
    if not isinstance(contract, dict):
        return None
    if str(contract.get("contract_type") or "").strip().lower() != "option":
        return None
    raw = contract.get("terms")
    return raw if isinstance(raw, dict) else None


def option_contract_identity(
    transaction: dict[str, object],
) -> dict[str, object]:
    """Return normalized terms from the transaction's local option contract."""

    contract = _contract(transaction)
    if contract is None:
        raise ValueError("Option contract identity is required for option transactions.")
    required_fields = (
        "underlying_instrument_id",
        "option_type",
        "expiry_date",
        "strike",
        "contract_multiplier",
    )
    if any(contract.get(field) in (None, "") for field in required_fields):
        raise ValueError("Option contract terms are incomplete for option transactions.")
    underlying = str(contract.get("underlying_instrument_id") or "").strip()
    option_id = str(transaction.get("derivative_contract_id") or "").strip()
    if not underlying or not option_id:
        raise ValueError("Option transaction requires a local contract identity.")
    option_type = str(contract.get("option_type") or "").strip().lower()
    if option_type not in {"call", "put"}:
        raise ValueError("Option contract option_type must be call or put.")
    multiplier = _float(contract.get("contract_multiplier"))
    strike = _float(contract.get("strike"))
    if multiplier <= EPSILON or strike <= EPSILON:
        raise ValueError("Option contract strike and multiplier must be positive.")
    expiry = _date(contract.get("expiry_date"))
    if expiry is None:
        raise ValueError("Option contract expiry_date is required.")
    currency = str(
        (transaction.get("derivative_contract") or {}).get("currency")
        if isinstance(transaction.get("derivative_contract"), dict)
        else transaction.get("currency")
    ).strip().upper()
    transaction_currency = str(transaction.get("currency") or "").strip().upper()
    if not currency or (transaction_currency and currency != transaction_currency):
        raise ValueError("Option contract currency must match transaction currency.")
    return {
        **contract,
        "underlying_instrument_id": underlying,
        "option_type": option_type,
        "expiry_date": expiry.isoformat(),
        "strike": strike,
        "contract_multiplier": multiplier,
        "contract_currency": currency,
    }


def _validate_short_option_contract(
    transaction: dict[str, object],
) -> dict[str, object]:
    """Validate the option identity used by a short-option fact."""

    return option_contract_identity(transaction)


def _key(transaction: dict[str, object]) -> tuple[str, str, str]:
    contract = option_contract_identity(transaction)
    return (
        str(transaction.get("account_id") or "").strip(),
        str(transaction.get("derivative_contract_id") or "").strip(),
        str(contract.get("underlying_instrument_id") or "").strip(),
    )


def _new_obligation(transaction: dict[str, object], quantity: float) -> dict[str, object]:
    contract = option_contract_identity(transaction)
    required_underlying_quantity = quantity * _float(contract.get("contract_multiplier"))
    gross = _float(transaction.get("gross_amount"))
    opened_at = _date(
        transaction_performance_effective_date(transaction)
        or transaction.get("trade_date")
    )
    expiry_date = _date(contract.get("expiry_date"))
    transaction_id = str(transaction.get("transaction_id") or "")
    account_id, option_contract_id, underlying_id = _key(transaction)
    return {
        "obligation_id": f"obl-{transaction_id}",
        "portfolio_id": transaction.get("portfolio_id"),
        "account_id": account_id,
        "derivative_contract_id": option_contract_id,
        "derivative_contract": deepcopy(transaction.get("derivative_contract"))
        if isinstance(transaction.get("derivative_contract"), dict)
        else None,
        "related_underlying_id": underlying_id,
        "open_contract_quantity": quantity,
        "required_underlying_quantity": required_underlying_quantity,
        "remaining_quantity": quantity,
        "premium_received_gross": gross,
        "premium_basis_remaining": gross,
        "carrying_liability": gross,
        "opened_at": opened_at.isoformat() if opened_at else transaction.get("trade_date"),
        "expiry_date": expiry_date.isoformat() if expiry_date else None,
        "status": "open",
        "option_type": contract.get("option_type"),
        "strike": contract.get("strike"),
        "contract_multiplier": contract.get("contract_multiplier"),
        "contract_currency": contract.get("contract_currency") or transaction.get("currency"),
        # Premium is deferred in the liability subledger.  Attached charges
        # are recognized separately by the transaction buckets; opening an
        # obligation itself never creates premium income.
        "realized_pnl": 0.0,
        "opening_fee_expense": _float(transaction.get("fees"))
        + _float(transaction.get("taxes")),
        "realizations": [],
        "_opened_by_transaction_id": transaction_id,
    }


def _consume(
    obligation: dict[str, object],
    *,
    quantity: float,
    transaction: dict[str, object],
    reason: str,
    close_cost: float = 0.0,
    fees: float = 0.0,
    taxes: float = 0.0,
) -> dict[str, object]:
    remaining_quantity = _float(obligation.get("remaining_quantity"))
    if quantity <= EPSILON:
        quantity = remaining_quantity
    if quantity > remaining_quantity + EPSILON:
        raise ValueError("Short option close quantity exceeds the open position.")
    quantity = min(quantity, remaining_quantity)
    basis_remaining = _float(obligation.get("premium_basis_remaining"))
    released_basis = (
        basis_remaining
        if quantity >= remaining_quantity - EPSILON
        else basis_remaining * quantity / remaining_quantity
    )
    # Buy-to-close and cash settlement have explicit close costs. Expiry does not.
    realized_pnl = released_basis - close_cost - fees - taxes
    obligation["remaining_quantity"] = max(remaining_quantity - quantity, 0.0)
    obligation["open_contract_quantity"] = obligation["remaining_quantity"]
    obligation["required_underlying_quantity"] = (
        obligation["remaining_quantity"]
        * _float(obligation.get("contract_multiplier"), 0.0)
    )
    obligation["premium_basis_remaining"] = max(basis_remaining - released_basis, 0.0)
    obligation["carrying_liability"] = obligation["premium_basis_remaining"]
    obligation["realized_pnl"] = _float(obligation.get("realized_pnl")) + realized_pnl
    if obligation["remaining_quantity"] <= EPSILON:
        obligation["remaining_quantity"] = 0.0
        obligation["open_contract_quantity"] = 0.0
        obligation["required_underlying_quantity"] = 0.0
        obligation["premium_basis_remaining"] = 0.0
        obligation["carrying_liability"] = 0.0
        obligation["status"] = reason

    transaction_id = str(transaction.get("transaction_id") or "")
    realization = {
        "realization_id": f"{obligation['obligation_id']}-r{len(obligation.get('realizations') or []) + 1}",
        "transaction_id": transaction_id,
        "transaction_type": transaction.get("transaction_type"),
        "option_action": resolve_option_action(transaction),
        "lifecycle_event_type": transaction.get("lifecycle_event_type"),
        "trade_date": transaction.get("trade_date"),
        "quantity": quantity,
        "released_premium_basis": released_basis,
        "close_cost": close_cost,
        "fees": fees,
        "taxes": taxes,
        "realized_pnl": realized_pnl,
        "reason": reason,
    }
    obligation.setdefault("realizations", []).append(realization)
    return {
        "obligation_id": obligation["obligation_id"],
        "transaction_id": transaction_id,
        "event_type": reason,
        "released_quantity": quantity,
        "released_premium_basis": released_basis,
        "liability_delta": -released_basis,
        "realized_pnl_delta": realized_pnl,
        "obligation": deepcopy(obligation),
        "option_action": resolve_option_action(transaction),
        "event_date": transaction_performance_effective_date(transaction)
        or _date(transaction.get("trade_date")),
        "currency": transaction.get("currency")
        or obligation.get("contract_currency"),
    }


def derive_option_obligation_events(
    transactions: Iterable[dict[str, object]],
    *,
    as_of_date: date | None = None,
) -> list[dict[str, object]]:
    """Replay short-option facts and return one lifecycle event per affected row."""

    ordered = sorted(list(transactions), key=transaction_sort_key)
    obligations: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    events: list[dict[str, object]] = []
    for transaction in ordered:
        effective_date = transaction_performance_effective_date(transaction) or _date(
            transaction.get("trade_date")
        )
        if as_of_date is not None and (effective_date is None or effective_date > as_of_date):
            continue
        action = resolve_option_action(transaction)
        lifecycle = str(transaction.get("lifecycle_event_type") or "")
        quantity = _float(transaction.get("quantity"))
        if action == "sell_to_open":
            if quantity <= EPSILON:
                raise ValueError("Short option open requires positive contract quantity.")
            key = _key(transaction)
            identity = _validate_short_option_contract(transaction)
            opened_at = effective_date or _date(transaction.get("trade_date"))
            expiry_date = _date(identity.get("expiry_date"))
            if (
                opened_at is not None
                and expiry_date is not None
                and opened_at > expiry_date
            ):
                raise ValueError("Short option open date must not be after contract expiry.")
            obligation = _new_obligation(transaction, quantity)
            obligations.setdefault(key, []).append(obligation)
            events.append(
                {
                    "obligation_id": obligation["obligation_id"],
                    "transaction_id": transaction.get("transaction_id"),
                    "event_type": "open",
                    "released_quantity": 0.0,
                    "released_premium_basis": 0.0,
                    "liability_delta": _float(transaction.get("gross_amount")),
                    # Fees and taxes are ordinary expenses and are surfaced by
                    # the transaction buckets exactly once.  Do not also put
                    # them in the lifecycle realized bucket.
                    "realized_pnl_delta": 0.0,
                    "expense_delta": -_float(transaction.get("fees"))
                    - _float(transaction.get("taxes")),
                    "obligation": deepcopy(obligation),
                    "option_action": action,
                    "event_date": effective_date,
                    "currency": transaction.get("currency"),
                }
            )
            continue

        close_reason: str | None = None
        if action == "buy_to_close":
            close_reason = "closed"
        elif lifecycle == "option_writer_expiry":
            close_reason = "expired"
        elif lifecycle == "option_writer_cash_settlement":
            close_reason = "cash_settled"
        if close_reason is None:
            continue

        key = _key(transaction)
        open_rows = obligations.get(key, [])
        available = sum(_float(row.get("remaining_quantity")) for row in open_rows)
        requested = quantity if quantity > EPSILON else available
        if requested > available + EPSILON:
            raise ValueError("Short option close quantity exceeds the open position.")
        identity = _validate_short_option_contract(transaction)
        cash_settlement = lifecycle == "option_writer_cash_settlement"
        if lifecycle == "option_writer_expiry":
            expiry_date = _date(identity.get("expiry_date"))
            if (
                effective_date is not None
                and expiry_date is not None
                and effective_date < expiry_date
            ):
                raise ValueError(
                    "Short option expiry event must not precede contract expiry."
                )
        remaining = requested
        for obligation in open_rows:
            if remaining <= EPSILON:
                break
            take = min(remaining, _float(obligation.get("remaining_quantity")))
            if take <= EPSILON:
                continue
            # Allocate close cost and charges across FIFO quantity slices.
            ratio = take / requested if requested > EPSILON else 0.0
            event = _consume(
                obligation,
                quantity=take,
                transaction=transaction,
                reason=close_reason,
                close_cost=_float(transaction.get("gross_amount")) * ratio
                if action == "buy_to_close" or cash_settlement
                else 0.0,
                fees=_float(transaction.get("fees")) * ratio
                if action == "buy_to_close" or cash_settlement
                else 0.0,
                taxes=_float(transaction.get("taxes")) * ratio
                if action == "buy_to_close" or cash_settlement
                else 0.0,
            )
            events.append(event)
            remaining -= take
        obligations[key] = [row for row in open_rows if _float(row.get("remaining_quantity")) > EPSILON]

    return events


def build_option_obligations(
    transactions: Iterable[dict[str, object]],
    *,
    as_of_date: date | None = None,
) -> list[dict[str, object]]:
    """Return open and closed obligation rows at an as-of boundary."""

    events = derive_option_obligation_events(transactions, as_of_date=as_of_date)
    latest_by_id: dict[str, dict[str, object]] = {}
    for event in events:
        obligation = event.get("obligation")
        if isinstance(obligation, dict):
            latest_by_id[str(obligation.get("obligation_id") or "")] = obligation
    rows = [row for key, row in latest_by_id.items() if key]
    rows.sort(
        key=lambda row: (
            str(row.get("opened_at") or ""),
            str(row.get("obligation_id") or ""),
        )
    )
    return rows


def open_option_obligations(
    transactions: Iterable[dict[str, object]],
    *,
    as_of_date: date | None = None,
) -> list[dict[str, object]]:
    return [
        row
        for row in build_option_obligations(transactions, as_of_date=as_of_date)
        if _float(row.get("remaining_quantity")) > EPSILON and row.get("status") == "open"
    ]


def estimate_option_obligation_quantity_at_entitlement(
    transactions: Iterable[dict[str, object]],
    *,
    account_id: str,
    derivative_contract_id: str,
    entitlement_date: date,
    exclude_transaction_id: str | None = None,
) -> float:
    """Return the writer quantity owned at entitlement-date beginning of day."""

    prior_transactions = [
        transaction
        for transaction in transactions
        if (
            exclude_transaction_id is None
            or str(transaction.get("transaction_id") or "")
            != exclude_transaction_id
        )
        and (
            effective_date := transaction_performance_effective_date(transaction)
        )
        is not None
        and effective_date < entitlement_date
    ]

    return sum(
        _float(row.get("remaining_quantity"))
        for row in open_option_obligations(
            prior_transactions,
            as_of_date=entitlement_date,
        )
        if str(row.get("account_id") or "") == account_id
        and str(row.get("derivative_contract_id") or "")
        == derivative_contract_id
    )


def summarize_option_obligations(rows: Iterable[dict[str, object]]) -> dict[str, object]:
    items = list(rows)
    return {
        "obligation_count": len(items),
        "open_obligation_count": sum(1 for row in items if row.get("status") == "open"),
        "open_contract_quantity": sum(
            _float(row.get("open_contract_quantity"))
            for row in items
            if row.get("status") == "open"
        ),
        "required_underlying_quantity": sum(
            _float(row.get("required_underlying_quantity"))
            for row in items
            if row.get("status") == "open"
        ),
        "premium_basis_remaining": sum(
            _float(row.get("premium_basis_remaining"))
            for row in items
            if row.get("status") == "open"
        ),
        "carrying_liability": sum(
            _float(row.get("carrying_liability"))
            for row in items
            if row.get("status") == "open"
        ),
        "realized_pnl": sum(_float(row.get("realized_pnl")) for row in items),
    }


__all__ = [
    "build_option_obligations",
    "derive_option_obligation_events",
    "estimate_option_obligation_quantity_at_entitlement",
    "open_option_obligations",
    "option_contract_identity",
    "summarize_option_obligations",
]
