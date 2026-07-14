"""Preparation and single-pass orchestration for exact ledger replay."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import DivisionByZero, InvalidOperation, Overflow

from portfolio_app.calculations.portfolio_daily.ledger_actions import _expand_event
from portfolio_app.calculations.portfolio_daily.ledger_contracts import (
    CashTransferEvent,
    LedgerContractError,
    LedgerEvent,
    LedgerReasonCode,
    OpeningCashEvent,
    OpeningPositionEvent,
    PositionTransferEvent,
    SUPPORTED_LEDGER_EVENT_TYPES,
    require_date,
)
from portfolio_app.calculations.portfolio_daily.ledger_postings import (
    _apply_action,
    _validate_state_closure,
)
from portfolio_app.calculations.portfolio_daily.ledger_runtime import (
    _LedgerBuilder,
    _LedgerFailure,
    _PreparedLedger,
)
from portfolio_app.calculations.portfolio_daily.ledger_state import (
    DailyLedgerResult,
    DailyLedgerSnapshot,
    LedgerSeriesResult,
    LedgerStatus,
    OpeningAnchorEvidence,
)

def _failed_daily(
    *,
    status: LedgerStatus,
    reason_code: LedgerReasonCode,
    event_id: str | None,
    diagnostic: str,
) -> DailyLedgerResult:
    return DailyLedgerResult(
        status=status,
        reason_codes=(reason_code,),
        state=None,
        effects=(),
        failed_event_id=event_id,
        diagnostic=diagnostic,
    )


def _opening_anchor(
    events: tuple[LedgerEvent, ...],
) -> OpeningAnchorEvidence | DailyLedgerResult | None:
    openings = tuple(
        event
        for event in events
        if isinstance(event, (OpeningCashEvent, OpeningPositionEvent))
    )
    if not openings:
        return None
    dates = {event.effective_date for event in openings}
    if len(dates) != 1:
        return _failed_daily(
            status=LedgerStatus.FAILED,
            reason_code=LedgerReasonCode.OPENING_ANCHOR_MISMATCH,
            event_id=None,
            diagnostic="all opening facts must share one anchor date",
        )
    anchor_date = next(iter(dates))
    if any(
        action.effective_date < anchor_date
        for event in events
        if not isinstance(event, (OpeningCashEvent, OpeningPositionEvent))
        for action in _expand_event(event)
    ):
        return _failed_daily(
            status=LedgerStatus.FAILED,
            reason_code=LedgerReasonCode.OPENING_ANCHOR_MISMATCH,
            event_id=None,
            diagnostic="opening facts must be the first ledger anchor",
        )
    return OpeningAnchorEvidence(
        anchor_date=anchor_date,
        event_ids=tuple(
            event.event_id
            for event in sorted(openings, key=lambda item: (item.sequence, item.event_id))
        ),
    )


def _prepare(
    events: tuple[LedgerEvent, ...],
) -> _PreparedLedger | DailyLedgerResult:
    if not isinstance(events, tuple):
        raise LedgerContractError("events must be an immutable tuple")
    for event in events:
        if not isinstance(event, SUPPORTED_LEDGER_EVENT_TYPES):
            raise LedgerContractError(
                f"unsupported ledger event type {type(event).__name__}"
            )
    event_ids: set[str] = set()
    sequences: set[int] = set()
    source_fact_keys: set[str] = set()
    for event in events:
        if event.event_id in event_ids:
            return _failed_daily(
                status=LedgerStatus.FAILED,
                reason_code=LedgerReasonCode.DUPLICATE_EVENT_ID,
                event_id=event.event_id,
                diagnostic=f"duplicate event id {event.event_id}",
            )
        if event.sequence in sequences:
            return _failed_daily(
                status=LedgerStatus.FAILED,
                reason_code=LedgerReasonCode.DUPLICATE_SEQUENCE,
                event_id=event.event_id,
                diagnostic=f"duplicate sequence {event.sequence}",
            )
        primary_lineages = [event.lineage]
        if isinstance(event, (CashTransferEvent, PositionTransferEvent)):
            primary_lineages.append(event.counterparty_lineage)
        for lineage in primary_lineages:
            if lineage.manifest_fact_key in source_fact_keys:
                return _failed_daily(
                    status=LedgerStatus.FAILED,
                    reason_code=LedgerReasonCode.DUPLICATE_SOURCE_FACT,
                    event_id=event.event_id,
                    diagnostic=(
                        "a primary transaction revision was consumed by more "
                        f"than one ledger event: {lineage.manifest_fact_key}"
                    ),
                )
            source_fact_keys.add(lineage.manifest_fact_key)
        event_ids.add(event.event_id)
        sequences.add(event.sequence)
    anchor = _opening_anchor(events)
    if isinstance(anchor, DailyLedgerResult):
        return anchor
    return _PreparedLedger(
        actions=tuple(
            sorted(
                (action for event in events for action in _expand_event(event)),
                key=lambda action: (
                    action.effective_date,
                    int(action.phase),
                    action.sequence,
                    action.event_id,
                    action.kind.value,
                ),
            )
        ),
        opening_anchor=anchor,
    )


def _series_failure(result: DailyLedgerResult) -> LedgerSeriesResult:
    return LedgerSeriesResult(
        status=result.status,
        reason_codes=result.reason_codes,
        snapshots=(),
        effects=(),
        failed_event_id=result.failed_event_id,
        diagnostic=result.diagnostic,
    )


def replay_ledger_series(
    events: tuple[LedgerEvent, ...],
    *,
    start_date: date,
    end_date: date,
) -> LedgerSeriesResult:
    """Replay once and freeze exact immutable state at each requested day."""

    start = require_date(start_date, field_name="start_date")
    end = require_date(end_date, field_name="end_date")
    if end < start:
        raise LedgerContractError("end_date must not precede start_date")
    prepared = _prepare(events)
    if isinstance(prepared, DailyLedgerResult):
        return _series_failure(prepared)
    builder = _LedgerBuilder(
        as_of_date=start,
        opening_anchor=(
            prepared.opening_anchor
            if prepared.opening_anchor is not None
            and prepared.opening_anchor.anchor_date <= start
            else None
        ),
    )
    action_index = 0
    snapshots: list[DailyLedgerSnapshot] = []
    try:
        while (
            action_index < len(prepared.actions)
            and prepared.actions[action_index].effective_date < start
        ):
            action = prepared.actions[action_index]
            builder.as_of_date = action.effective_date
            _apply_action(builder, action)
            action_index += 1
        current = start
        while current <= end:
            builder.begin_day(current)
            if (
                prepared.opening_anchor is not None
                and prepared.opening_anchor.anchor_date <= current
            ):
                builder.opening_anchor = prepared.opening_anchor
            while (
                action_index < len(prepared.actions)
                and prepared.actions[action_index].effective_date == current
            ):
                _apply_action(builder, prepared.actions[action_index])
                action_index += 1
            _validate_state_closure(builder)
            snapshots.append(
                DailyLedgerSnapshot(
                    state=builder.freeze(),
                    daily_effects=tuple(builder.daily_effects),
                )
            )
            current += timedelta(days=1)
    except _LedgerFailure as exc:
        return LedgerSeriesResult(
            status=(
                LedgerStatus.UNAVAILABLE if exc.unavailable else LedgerStatus.FAILED
            ),
            reason_codes=(exc.reason_code,),
            snapshots=(),
            effects=(),
            failed_event_id=exc.event_id,
            diagnostic=str(exc),
        )
    except (DivisionByZero, InvalidOperation, Overflow) as exc:
        return LedgerSeriesResult(
            status=LedgerStatus.FAILED,
            reason_codes=(LedgerReasonCode.NUMERIC_FAILURE,),
            snapshots=(),
            effects=(),
            diagnostic=type(exc).__name__,
        )
    except LedgerContractError as exc:
        return LedgerSeriesResult(
            status=LedgerStatus.FAILED,
            reason_codes=(exc.reason_code,),
            snapshots=(),
            effects=(),
            failed_event_id=exc.event_id,
            diagnostic=str(exc),
        )
    return LedgerSeriesResult(
        status=LedgerStatus.SUCCEEDED,
        reason_codes=(),
        snapshots=tuple(snapshots),
        effects=tuple(builder.effects),
    )


def replay_daily_ledger(
    events: tuple[LedgerEvent, ...],
    *,
    as_of_date: date,
) -> DailyLedgerResult:
    result = replay_ledger_series(
        events,
        start_date=as_of_date,
        end_date=as_of_date,
    )
    if result.status is not LedgerStatus.SUCCEEDED:
        return DailyLedgerResult(
            status=result.status,
            reason_codes=result.reason_codes,
            state=None,
            effects=(),
            failed_event_id=result.failed_event_id,
            diagnostic=result.diagnostic,
        )
    snapshot = result.snapshots[0]
    return DailyLedgerResult(
        status=LedgerStatus.SUCCEEDED,
        reason_codes=(),
        state=snapshot.state,
        effects=result.effects,
    )


__all__ = ["replay_daily_ledger", "replay_ledger_series"]
