from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import delete, select

from portfolio_app.db.models import (
    PortfolioInstrumentEventTaskLinkModel,
    PortfolioInstrumentEventTaskModel,
    PortfolioInstrumentEventTaskReviewModel,
    PortfolioInstrumentUniverseRecordModel,
    PortfolioRecordModel,
    TransactionRecordModel,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.instrument_registry import (
    get_registry_instrument_event_details,
)
from portfolio_app.services.ledger import estimate_position_quantity
from portfolio_app.services.portfolio_store import load_locked_portfolio_ledger_state
from portfolio_app.services.transaction_dates import (
    transaction_precedes_entitlement_bod,
)


FUND_NAV_EVENT_SOURCE = "instrument_registry.fund_nav_action"
QUANTITY_QUANTUM = Decimal("0.000000000001")
AMOUNT_QUANTUM = Decimal("0.01")
_POSITION_EPSILON = Decimal("0.000000001")


class InstrumentEventTaskNotFoundError(LookupError):
    pass


class InstrumentEventTaskConflictError(RuntimeError):
    pass


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalized_text(value: object) -> str:
    return str(value or "").strip()


def _required_text(value: object, *, field_name: str) -> str:
    normalized = _normalized_text(value)
    if not normalized:
        raise ValueError(f"{field_name} is required.")
    return normalized


def _date_value(value: object) -> date | None:
    if isinstance(value, date):
        return value
    normalized = _normalized_text(value)
    if not normalized:
        return None
    try:
        return date.fromisoformat(normalized[:10])
    except ValueError as error:
        raise ValueError(f"Invalid Registry event date: {normalized}.") from error


def _decimal_value(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"Invalid Registry event decimal: {value}.") from error
    if not resolved.is_finite():
        raise ValueError(f"Invalid Registry event decimal: {value}.")
    return resolved


def _quantity_decimal(value: object) -> Decimal:
    resolved = _decimal_value(value) or Decimal("0")
    if resolved < 0 and abs(resolved) <= _POSITION_EPSILON:
        resolved = Decimal("0")
    if resolved < 0:
        raise ValueError("Entitled quantity cannot be negative.")
    return resolved.quantize(QUANTITY_QUANTUM, rounding=ROUND_HALF_UP)


def _task_id(
    *,
    portfolio_id: str,
    account_id: str,
    event_source: str,
    event_action_id: str,
) -> str:
    identity = ":".join(
        (portfolio_id, account_id, event_source, event_action_id)
    )
    return f"piet-{uuid5(NAMESPACE_URL, identity).hex}"


def _current_fund_event_heads(
    detail: Mapping[str, object],
) -> dict[str, dict[str, object]]:
    raw_revisions = detail.get("fund_nav_event_revisions")
    if not isinstance(raw_revisions, list):
        raw_revisions = detail.get("fund_nav_events")
    heads: dict[str, dict[str, object]] = {}
    for raw_event in raw_revisions if isinstance(raw_revisions, list) else []:
        if not isinstance(raw_event, Mapping):
            continue
        event = dict(raw_event)
        action_id = _required_text(
            event.get("fund_nav_action_id"),
            field_name="fund_nav_action_id",
        )
        revision_number = int(event.get("revision_number") or 0)
        current = heads.get(action_id)
        if current is None or revision_number > int(
            current.get("revision_number") or 0
        ):
            heads[action_id] = event
    return heads


def _current_reinvestment_nav_by_event_id(
    detail: Mapping[str, object],
) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    raw_evidence = detail.get("fund_nav_reinvestment_evidence")
    for item in raw_evidence if isinstance(raw_evidence, list) else []:
        if not isinstance(item, Mapping):
            continue
        event_id = _normalized_text(item.get("fund_nav_event_id"))
        reinvestment_nav = _decimal_value(item.get("reinvestment_nav"))
        if event_id and reinvestment_nav is not None:
            result[event_id] = reinvestment_nav
    return result


def _position_adjusting_actions(
    detail: Mapping[str, object],
) -> list[dict[str, object]]:
    return [
        dict(item)
        for item in detail.get("corporate_actions", [])
        if isinstance(item, Mapping)
    ]


def _instrument_ids_for_portfolios(
    portfolio_ids: set[str],
) -> set[str]:
    if not portfolio_ids:
        return set()
    session_factory = get_session_factory()
    with session_factory() as session:
        universe_ids = {
            str(instrument_id)
            for instrument_id in session.scalars(
                select(PortfolioInstrumentUniverseRecordModel.instrument_id).where(
                    PortfolioInstrumentUniverseRecordModel.portfolio_id.in_(
                        sorted(portfolio_ids)
                    ),
                    PortfolioInstrumentUniverseRecordModel.status == "active",
                )
            ).all()
        }
        transaction_ids = {
            str(instrument_id)
            for instrument_id in session.scalars(
                select(TransactionRecordModel.instrument_id).where(
                    TransactionRecordModel.portfolio_id.in_(
                        sorted(portfolio_ids)
                    ),
                    TransactionRecordModel.instrument_id.is_not(None),
                )
            ).all()
            if instrument_id is not None
        }
        existing_task_ids = {
            str(instrument_id)
            for instrument_id in session.scalars(
                select(PortfolioInstrumentEventTaskModel.instrument_id).where(
                    PortfolioInstrumentEventTaskModel.portfolio_id.in_(
                        sorted(portfolio_ids)
                    )
                )
            ).all()
        }
        return universe_ids | transaction_ids | existing_task_ids


def _portfolio_ids_for_instruments(instrument_ids: set[str]) -> set[str]:
    if not instrument_ids:
        return set()
    session_factory = get_session_factory()
    with session_factory() as session:
        universe_portfolio_ids = {
            str(portfolio_id)
            for portfolio_id in session.scalars(
                select(PortfolioInstrumentUniverseRecordModel.portfolio_id).where(
                    PortfolioInstrumentUniverseRecordModel.instrument_id.in_(
                        sorted(instrument_ids)
                    ),
                    PortfolioInstrumentUniverseRecordModel.status == "active",
                )
            ).all()
        }
        transaction_portfolio_ids = {
            str(portfolio_id)
            for portfolio_id in session.scalars(
                select(TransactionRecordModel.portfolio_id).where(
                    TransactionRecordModel.instrument_id.in_(
                        sorted(instrument_ids)
                    )
                )
            ).all()
        }
        existing_task_portfolio_ids = {
            str(portfolio_id)
            for portfolio_id in session.scalars(
                select(PortfolioInstrumentEventTaskModel.portfolio_id).where(
                    PortfolioInstrumentEventTaskModel.instrument_id.in_(
                        sorted(instrument_ids)
                    )
                )
            ).all()
        }
        return (
            universe_portfolio_ids
            | transaction_portfolio_ids
            | existing_task_portfolio_ids
        )


def _all_portfolio_ids() -> set[str]:
    session_factory = get_session_factory()
    with session_factory() as session:
        return {
            str(portfolio_id)
            for portfolio_id in session.scalars(
                select(PortfolioRecordModel.portfolio_id)
            ).all()
        }


def _task_projection_values(
    *,
    event: Mapping[str, object],
    reinvestment_nav: Decimal | None,
    entitled_quantity: Decimal,
) -> dict[str, object]:
    revision_kind = _required_text(
        event.get("revision_kind"),
        field_name="revision_kind",
    )
    effective_date = _date_value(event.get("effective_date"))
    if effective_date is None:
        raise ValueError("Registry fund NAV event requires effective_date.")
    return {
        "instrument_id": _required_text(
            event.get("instrument_id"),
            field_name="instrument_id",
        ),
        "current_event_revision_id": _required_text(
            event.get("fund_nav_event_id"),
            field_name="fund_nav_event_id",
        ),
        "event_type": _required_text(
            event.get("event_type"),
            field_name="event_type",
        ),
        "source_revision_kind": revision_kind,
        "source_event_state": (
            "cancelled" if revision_kind == "cancellation" else "active"
        ),
        "announcement_date": _date_value(event.get("announcement_date")),
        "record_date": _date_value(event.get("record_date")),
        "effective_date": effective_date,
        "payable_date": _date_value(event.get("payable_date")),
        "cash_per_unit": _decimal_value(event.get("cash_per_unit")),
        "unit_ratio": _decimal_value(event.get("unit_ratio")),
        "reinvestment_nav": reinvestment_nav,
        "entitled_quantity": entitled_quantity,
    }


def _apply_projection_values(
    task: PortfolioInstrumentEventTaskModel,
    values: Mapping[str, object],
    *,
    updated_at: str,
) -> bool:
    changed = False
    for field_name, value in values.items():
        if getattr(task, field_name) == value:
            continue
        setattr(task, field_name, value)
        changed = True
    if changed:
        task.row_version = int(task.row_version) + 1
        task.updated_at = updated_at
    return changed


def _sync_portfolio_tasks(
    *,
    portfolio_id: str,
    details_by_id: Mapping[str, Mapping[str, object] | None],
) -> int:
    session_factory = get_session_factory()
    changed_count = 0
    with session_factory() as session:
        ledger_state = load_locked_portfolio_ledger_state(session, portfolio_id)
        if ledger_state is None:
            return 0
        accounts, transactions = ledger_state
        account_cost_methods = {
            _normalized_text(account.get("account_id")): _normalized_text(
                account.get("cost_basis_method")
            )
            or "fifo"
            for account in accounts
            if _normalized_text(account.get("account_type"))
            == "securities_account"
        }
        securities_account_ids = sorted(account_cost_methods)
        existing_tasks = list(
            session.scalars(
                select(PortfolioInstrumentEventTaskModel).where(
                    PortfolioInstrumentEventTaskModel.portfolio_id == portfolio_id,
                    PortfolioInstrumentEventTaskModel.event_source
                    == FUND_NAV_EVENT_SOURCE,
                    PortfolioInstrumentEventTaskModel.instrument_id.in_(
                        sorted(details_by_id)
                    ),
                )
            ).all()
        )
        existing_by_key = {
            (task.account_id, task.event_action_id): task
            for task in existing_tasks
        }
        now = _utc_timestamp()

        for instrument_id, detail in details_by_id.items():
            if not isinstance(detail, Mapping):
                continue
            event_heads = _current_fund_event_heads(detail)
            reinvestment_nav_by_event_id = _current_reinvestment_nav_by_event_id(
                detail
            )
            for action_id, event in event_heads.items():
                if _normalized_text(event.get("event_type")) != "cash_distribution":
                    continue
                current_event_id = _required_text(
                    event.get("fund_nav_event_id"),
                    field_name="fund_nav_event_id",
                )
                revision_kind = _normalized_text(event.get("revision_kind"))
                entitlement_date = (
                    _date_value(event.get("record_date"))
                    or _date_value(event.get("effective_date"))
                )
                if entitlement_date is None:
                    raise ValueError("Cash distribution requires an entitlement date.")
                position_actions = _position_adjusting_actions(detail)

                existing_for_action = {
                    account_id: task
                    for (account_id, existing_action_id), task in existing_by_key.items()
                    if existing_action_id == action_id
                }
                target_account_ids = (
                    sorted(existing_for_action)
                    if revision_kind == "cancellation"
                    else sorted(set(securities_account_ids).union(existing_for_action))
                )
                for account_id in target_account_ids:
                    existing = existing_for_action.get(account_id)
                    if revision_kind == "cancellation":
                        entitled_quantity = (
                            Decimal(existing.entitled_quantity)
                            if existing is not None
                            else Decimal("0")
                        )
                    else:
                        entitlement_transactions = [
                            transaction
                            for transaction in transactions
                            if transaction_precedes_entitlement_bod(
                                transaction,
                                entitlement_date,
                            )
                        ]
                        entitled_quantity = _quantity_decimal(
                            estimate_position_quantity(
                                portfolio_id,
                                entitlement_transactions,
                                account_id=account_id,
                                instrument_id=instrument_id,
                                account_cost_methods=account_cost_methods,
                                corporate_actions=position_actions,
                                as_of_date=entitlement_date,
                            )
                        )
                    if existing is None and entitled_quantity <= _POSITION_EPSILON:
                        continue

                    values = _task_projection_values(
                        event=event,
                        reinvestment_nav=reinvestment_nav_by_event_id.get(
                            current_event_id
                        ),
                        entitled_quantity=entitled_quantity,
                    )
                    if existing is None:
                        task = PortfolioInstrumentEventTaskModel(
                            instrument_event_task_id=_task_id(
                                portfolio_id=portfolio_id,
                                account_id=account_id,
                                event_source=FUND_NAV_EVENT_SOURCE,
                                event_action_id=action_id,
                            ),
                            portfolio_id=portfolio_id,
                            account_id=account_id,
                            event_source=FUND_NAV_EVENT_SOURCE,
                            event_action_id=action_id,
                            resolution_status="pending",
                            row_version=1,
                            created_at=now,
                            updated_at=now,
                            **values,
                        )
                        session.add(task)
                        existing_by_key[(account_id, action_id)] = task
                        changed_count += 1
                    elif _apply_projection_values(existing, values, updated_at=now):
                        changed_count += 1

        session.commit()
    return changed_count


def reconcile_instrument_event_tasks(
    *,
    portfolio_ids: Iterable[str] | None = None,
    instrument_ids: Iterable[str] | None = None,
    refresh_all: bool = False,
) -> dict[str, int]:
    """Project current Registry fund actions into idempotent Portfolio tasks."""

    normalized_portfolio_ids = {
        _normalized_text(value) for value in (portfolio_ids or []) if _normalized_text(value)
    }
    normalized_instrument_ids = {
        _normalized_text(value) for value in (instrument_ids or []) if _normalized_text(value)
    }
    if refresh_all:
        normalized_portfolio_ids = _all_portfolio_ids()
    elif normalized_instrument_ids and not normalized_portfolio_ids:
        normalized_portfolio_ids = _portfolio_ids_for_instruments(
            normalized_instrument_ids
        )
    if not normalized_portfolio_ids:
        return {}
    if not normalized_instrument_ids:
        normalized_instrument_ids = _instrument_ids_for_portfolios(
            normalized_portfolio_ids
        )
    if not normalized_instrument_ids:
        return {portfolio_id: 0 for portfolio_id in normalized_portfolio_ids}

    raw_details = get_registry_instrument_event_details(
        sorted(normalized_instrument_ids)
    )
    details_by_id: dict[str, Mapping[str, object] | None] = {
        instrument_id: (
            detail if isinstance(detail, Mapping) else None
        )
        for instrument_id, detail in raw_details.items()
    }
    return {
        portfolio_id: _sync_portfolio_tasks(
            portfolio_id=portfolio_id,
            details_by_id=details_by_id,
        )
        for portfolio_id in sorted(normalized_portfolio_ids)
    }


def _transaction_decimal(
    transaction: TransactionRecordModel,
    *,
    source_field: str,
    projection_field: str,
) -> Decimal:
    source_value = getattr(transaction, source_field)
    value = source_value if source_value is not None else getattr(
        transaction,
        projection_field,
    )
    return _decimal_value(value) or Decimal("0")


def _transaction_link_role(transaction_type: str) -> str:
    if transaction_type == "dividend":
        return "distribution"
    if transaction_type == "dividend_reinvestment":
        return "reinvestment"
    if transaction_type == "buy":
        return "reinvestment_purchase"
    raise InstrumentEventTaskConflictError(
        f"Transaction type '{transaction_type}' cannot be linked to a cash distribution."
    )


def _validate_processed_transactions(
    task: PortfolioInstrumentEventTaskModel,
    transactions: list[TransactionRecordModel],
) -> list[tuple[TransactionRecordModel, str]]:
    if task.source_event_state != "active":
        raise InstrumentEventTaskConflictError(
            "A cancelled Registry event cannot be marked processed."
        )
    if Decimal(task.entitled_quantity) <= _POSITION_EPSILON:
        raise InstrumentEventTaskConflictError(
            "The task has no entitled position and cannot be marked processed."
        )
    if task.event_type != "cash_distribution" or task.cash_per_unit is None:
        raise InstrumentEventTaskConflictError(
            "Only a complete cash-distribution event can be processed."
        )
    if not transactions:
        raise InstrumentEventTaskConflictError(
            "Processed review requires at least one linked transaction."
        )

    linked: list[tuple[TransactionRecordModel, str]] = []
    income_transactions: list[TransactionRecordModel] = []
    income_types: set[str] = set()
    entitlement_date = task.record_date or task.effective_date
    for transaction in transactions:
        if transaction.portfolio_id != task.portfolio_id:
            raise InstrumentEventTaskConflictError(
                "Linked transaction belongs to a different portfolio."
            )
        if transaction.account_id != task.account_id:
            raise InstrumentEventTaskConflictError(
                "Linked transaction belongs to a different securities account."
            )
        if transaction.instrument_id != task.instrument_id:
            raise InstrumentEventTaskConflictError(
                "Linked transaction belongs to a different instrument."
            )
        transaction_type = _normalized_text(transaction.transaction_type)
        role = _transaction_link_role(transaction_type)
        if transaction_type in {"dividend", "dividend_reinvestment"}:
            if transaction.entitlement_date != entitlement_date:
                raise InstrumentEventTaskConflictError(
                    "Distribution transaction entitlement_date must match the Registry event."
                )
            income_transactions.append(transaction)
            income_types.add(transaction_type)
        elif transaction.trade_date < entitlement_date:
            raise InstrumentEventTaskConflictError(
                "Reinvestment purchase cannot precede the distribution entitlement date."
            )
        linked.append((transaction, role))

    if not income_transactions:
        raise InstrumentEventTaskConflictError(
            "A linked dividend or dividend-reinvestment transaction is required."
        )
    if income_types == {"dividend", "dividend_reinvestment"}:
        raise InstrumentEventTaskConflictError(
            "Use either cash-dividend facts or dividend-reinvestment facts, not both."
        )
    if income_types == {"dividend_reinvestment"} and any(
        role == "reinvestment_purchase" for _transaction, role in linked
    ):
        raise InstrumentEventTaskConflictError(
            "A dividend-reinvestment fact already includes the reinvested purchase."
        )

    expected_gross = (
        Decimal(task.entitled_quantity) * Decimal(task.cash_per_unit)
    ).quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP)
    recorded_gross = sum(
        (
            _transaction_decimal(
                transaction,
                source_field="source_gross_amount",
                projection_field="gross_amount",
            )
            for transaction in income_transactions
        ),
        start=Decimal("0"),
    ).quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP)
    if recorded_gross != expected_gross:
        raise InstrumentEventTaskConflictError(
            "Linked distribution gross amount does not match entitled quantity "
            f"times cash per unit ({recorded_gross} != {expected_gross})."
        )
    return linked


def _append_review(
    session,
    *,
    task: PortfolioInstrumentEventTaskModel,
    decision: str,
    transaction_ids: list[str],
    note: str,
    reviewed_by: str,
    reviewed_at: str,
) -> None:
    session.add(
        PortfolioInstrumentEventTaskReviewModel(
            instrument_event_task_review_id=f"pietr-{uuid4().hex}",
            instrument_event_task_id=task.instrument_event_task_id,
            portfolio_id=task.portfolio_id,
            event_revision_id=task.current_event_revision_id,
            decision=decision,
            linked_transaction_ids_json=transaction_ids,
            note=note,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
        )
    )


def review_instrument_event_task(
    *,
    portfolio_id: str,
    instrument_event_task_id: str,
    decision: str,
    transaction_ids: Iterable[str],
    note: str,
    reviewed_by: str,
    expected_row_version: int,
) -> None:
    normalized_portfolio_id = _required_text(
        portfolio_id,
        field_name="portfolio_id",
    )
    task_id = _required_text(
        instrument_event_task_id,
        field_name="instrument_event_task_id",
    )
    normalized_decision = _required_text(decision, field_name="decision")
    if normalized_decision not in {"processed", "not_applicable", "reopened"}:
        raise ValueError("Unsupported event-task review decision.")
    normalized_note = _required_text(note, field_name="note")
    normalized_reviewer = _required_text(reviewed_by, field_name="reviewed_by")
    normalized_transaction_ids = sorted(
        {
            _normalized_text(transaction_id)
            for transaction_id in transaction_ids
            if _normalized_text(transaction_id)
        }
    )

    session_factory = get_session_factory()
    with session_factory() as session:
        ledger_state = load_locked_portfolio_ledger_state(
            session,
            normalized_portfolio_id,
        )
        if ledger_state is None:
            raise InstrumentEventTaskNotFoundError("Portfolio not found.")
        task = session.scalar(
            select(PortfolioInstrumentEventTaskModel).where(
                PortfolioInstrumentEventTaskModel.portfolio_id
                == normalized_portfolio_id,
                PortfolioInstrumentEventTaskModel.instrument_event_task_id
                == task_id,
            )
        )
        if task is None:
            raise InstrumentEventTaskNotFoundError("Instrument event task not found.")
        if int(task.row_version) != int(expected_row_version):
            raise InstrumentEventTaskConflictError(
                "Event task changed; reload and review the current Registry revision."
            )

        existing_links = list(
            session.scalars(
                select(PortfolioInstrumentEventTaskLinkModel).where(
                    PortfolioInstrumentEventTaskLinkModel.instrument_event_task_id
                    == task_id
                )
            ).all()
        )
        if normalized_decision == "not_applicable" and existing_links:
            raise InstrumentEventTaskConflictError(
                "Reopen the task and remove its transaction links before marking it not applicable."
            )
        if normalized_decision != "processed" and normalized_transaction_ids:
            raise InstrumentEventTaskConflictError(
                "Only a processed review may carry linked transactions."
            )

        reviewed_at = _utc_timestamp()
        session.execute(
            delete(PortfolioInstrumentEventTaskLinkModel).where(
                PortfolioInstrumentEventTaskLinkModel.instrument_event_task_id
                == task_id
            )
        )
        if normalized_decision == "processed":
            transaction_records = list(
                session.scalars(
                    select(TransactionRecordModel).where(
                        TransactionRecordModel.portfolio_id
                        == normalized_portfolio_id,
                        TransactionRecordModel.transaction_id.in_(
                            normalized_transaction_ids
                        ),
                    )
                ).all()
            )
            if len(transaction_records) != len(normalized_transaction_ids):
                raise InstrumentEventTaskConflictError(
                    "One or more linked transactions no longer exist."
                )
            linked_records = _validate_processed_transactions(
                task,
                transaction_records,
            )
            for transaction, role in linked_records:
                session.add(
                    PortfolioInstrumentEventTaskLinkModel(
                        instrument_event_task_link_id=f"pietl-{uuid4().hex}",
                        instrument_event_task_id=task_id,
                        transaction_id=transaction.transaction_id,
                        link_role=role,
                        linked_event_revision_id=task.current_event_revision_id,
                        linked_by=normalized_reviewer,
                        linked_at=reviewed_at,
                    )
                )
            task.resolution_status = "processed"
            task.reviewed_event_revision_id = task.current_event_revision_id
            task.resolution_note = normalized_note
            task.resolved_by = normalized_reviewer
            task.resolved_at = reviewed_at
        elif normalized_decision == "not_applicable":
            task.resolution_status = "not_applicable"
            task.reviewed_event_revision_id = task.current_event_revision_id
            task.resolution_note = normalized_note
            task.resolved_by = normalized_reviewer
            task.resolved_at = reviewed_at
        else:
            task.resolution_status = "pending"
            task.reviewed_event_revision_id = None
            task.resolution_note = None
            task.resolved_by = None
            task.resolved_at = None
        task.row_version = int(task.row_version) + 1
        task.updated_at = reviewed_at
        _append_review(
            session,
            task=task,
            decision=normalized_decision,
            transaction_ids=normalized_transaction_ids,
            note=normalized_note,
            reviewed_by=normalized_reviewer,
            reviewed_at=reviewed_at,
        )
        session.commit()


def _linked_transaction_payload(transaction: TransactionRecordModel) -> dict[str, object]:
    return {
        "transaction_id": transaction.transaction_id,
        "transaction_type": transaction.transaction_type,
        "trade_date": transaction.trade_date.isoformat(),
        "settlement_date": transaction.settlement_date.isoformat(),
        "entitlement_date": (
            transaction.entitlement_date.isoformat()
            if transaction.entitlement_date is not None
            else None
        ),
        "gross_amount": str(
            transaction.source_gross_amount
            if transaction.source_gross_amount is not None
            else transaction.gross_amount
        ),
        "quantity": (
            str(
                transaction.source_quantity
                if transaction.source_quantity is not None
                else transaction.quantity
            )
            if transaction.quantity is not None
            else None
        ),
    }


def _task_status(
    task: PortfolioInstrumentEventTaskModel,
    linked_transactions: list[TransactionRecordModel],
) -> tuple[str, bool, str | None]:
    reviewed_current = (
        task.reviewed_event_revision_id == task.current_event_revision_id
    )
    if task.source_event_state == "cancelled":
        if linked_transactions or task.resolution_status == "processed":
            return (
                "needs_review",
                True,
                "The Registry event was cancelled after Portfolio transactions were linked.",
            )
        return "source_cancelled", False, None
    if Decimal(task.entitled_quantity) <= _POSITION_EPSILON:
        if linked_transactions or task.resolution_status == "processed":
            return (
                "needs_review",
                True,
                "The current ledger no longer shows an entitled position.",
            )
        return "no_entitlement", False, None
    if task.resolution_status == "not_applicable" and reviewed_current:
        return "not_applicable", False, None
    if task.resolution_status == "processed" and reviewed_current:
        try:
            _validate_processed_transactions(task, linked_transactions)
        except InstrumentEventTaskConflictError as error:
            return "needs_review", True, str(error)
        return "processed", False, None
    if task.resolution_status != "pending" or linked_transactions:
        return (
            "needs_review",
            True,
            "The Registry event revision changed after the last Portfolio review.",
        )
    return (
        "pending",
        True,
        "Record and link the cash distribution or dividend reinvestment.",
    )


def list_instrument_event_tasks(
    *,
    portfolio_id: str,
    attention_only: bool = False,
) -> list[dict[str, object]]:
    normalized_portfolio_id = _required_text(
        portfolio_id,
        field_name="portfolio_id",
    )
    session_factory = get_session_factory()
    with session_factory() as session:
        tasks = list(
            session.scalars(
                select(PortfolioInstrumentEventTaskModel)
                .where(
                    PortfolioInstrumentEventTaskModel.portfolio_id
                    == normalized_portfolio_id
                )
                .order_by(
                    PortfolioInstrumentEventTaskModel.effective_date.desc(),
                    PortfolioInstrumentEventTaskModel.instrument_id,
                    PortfolioInstrumentEventTaskModel.account_id,
                )
            ).all()
        )
        if not tasks:
            return []
        task_ids = [task.instrument_event_task_id for task in tasks]
        links = list(
            session.scalars(
                select(PortfolioInstrumentEventTaskLinkModel).where(
                    PortfolioInstrumentEventTaskLinkModel.instrument_event_task_id.in_(
                        task_ids
                    )
                )
            ).all()
        )
        transaction_ids = sorted({link.transaction_id for link in links})
        transaction_by_id = {
            transaction.transaction_id: transaction
            for transaction in session.scalars(
                select(TransactionRecordModel).where(
                    TransactionRecordModel.transaction_id.in_(transaction_ids)
                )
            ).all()
        }
        links_by_task_id: dict[
            str,
            list[PortfolioInstrumentEventTaskLinkModel],
        ] = {}
        for link in links:
            links_by_task_id.setdefault(link.instrument_event_task_id, []).append(
                link
            )

        payloads: list[dict[str, object]] = []
        for task in tasks:
            task_links = sorted(
                links_by_task_id.get(task.instrument_event_task_id, []),
                key=lambda item: (item.link_role, item.transaction_id),
            )
            linked_transactions = [
                transaction_by_id[link.transaction_id]
                for link in task_links
                if link.transaction_id in transaction_by_id
            ]
            status, attention_required, attention_reason = _task_status(
                task,
                linked_transactions,
            )
            if attention_only and not attention_required:
                continue
            expected_gross_amount = (
                Decimal(task.entitled_quantity) * Decimal(task.cash_per_unit)
                if task.cash_per_unit is not None
                else None
            )
            payloads.append(
                {
                    "instrument_event_task_id": task.instrument_event_task_id,
                    "portfolio_id": task.portfolio_id,
                    "account_id": task.account_id,
                    "instrument_id": task.instrument_id,
                    "event_source": task.event_source,
                    "event_action_id": task.event_action_id,
                    "current_event_revision_id": task.current_event_revision_id,
                    "event_type": task.event_type,
                    "source_revision_kind": task.source_revision_kind,
                    "source_event_state": task.source_event_state,
                    "announcement_date": task.announcement_date,
                    "record_date": task.record_date,
                    "effective_date": task.effective_date,
                    "payable_date": task.payable_date,
                    "cash_per_unit": task.cash_per_unit,
                    "unit_ratio": task.unit_ratio,
                    "reinvestment_nav": task.reinvestment_nav,
                    "entitled_quantity": task.entitled_quantity,
                    "expected_gross_amount": expected_gross_amount,
                    "resolution_status": task.resolution_status,
                    "reviewed_event_revision_id": task.reviewed_event_revision_id,
                    "resolution_note": task.resolution_note,
                    "resolved_by": task.resolved_by,
                    "resolved_at": task.resolved_at,
                    "status": status,
                    "attention_required": attention_required,
                    "attention_reason": attention_reason,
                    "linked_transactions": [
                        {
                            **_linked_transaction_payload(transaction_by_id[link.transaction_id]),
                            "link_role": link.link_role,
                            "linked_event_revision_id": link.linked_event_revision_id,
                            "linked_by": link.linked_by,
                            "linked_at": link.linked_at,
                        }
                        for link in task_links
                        if link.transaction_id in transaction_by_id
                    ],
                    "row_version": task.row_version,
                    "created_at": task.created_at,
                    "updated_at": task.updated_at,
                }
            )
        return payloads


def instrument_event_task_quality_warnings(portfolio_id: str) -> list[str]:
    attention_tasks = list_instrument_event_tasks(
        portfolio_id=portfolio_id,
        attention_only=True,
    )
    if not attention_tasks:
        return []
    pending_count = sum(
        1 for task in attention_tasks if task.get("status") == "pending"
    )
    review_count = len(attention_tasks) - pending_count
    parts: list[str] = []
    if pending_count:
        parts.append(f"{pending_count} confirmed fund distribution(s) need recording")
    if review_count:
        parts.append(f"{review_count} linked distribution(s) need re-review")
    return [
        "Fund income review required: "
        + "; ".join(parts)
        + ". Portfolio return remains calculated from official unit NAV under the "
        "explicit no-unrecorded-distribution assumption until the related cash or "
        "reinvestment transaction is confirmed."
    ]
