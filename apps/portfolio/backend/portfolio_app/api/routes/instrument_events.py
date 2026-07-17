from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from portfolio_app.api.contracts import (
    InstrumentEventTaskListResponse,
    InstrumentEventTaskRecord,
    InstrumentEventTaskReviewRequest,
)
from portfolio_app.services.instrument_event_tasks import (
    InstrumentEventTaskConflictError,
    InstrumentEventTaskNotFoundError,
    list_instrument_event_tasks,
    reconcile_instrument_event_tasks,
    review_instrument_event_task,
)
from portfolio_app.services.instrument_registry import (
    InstrumentRegistryError,
    get_registry_instrument_summaries,
)
from portfolio_app.services.portfolio_store import get_portfolio


router = APIRouter()


def _require_portfolio(portfolio_id: str) -> None:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")


def _current_task_records(
    portfolio_id: str,
    *,
    attention_only: bool,
) -> list[InstrumentEventTaskRecord]:
    tasks = list_instrument_event_tasks(
        portfolio_id=portfolio_id,
        attention_only=attention_only,
    )
    instrument_ids = {
        str(task.get("instrument_id") or "").strip()
        for task in tasks
        if str(task.get("instrument_id") or "").strip()
    }
    details = get_registry_instrument_summaries(sorted(instrument_ids))
    records: list[InstrumentEventTaskRecord] = []
    for task in tasks:
        instrument_id = str(task.get("instrument_id") or "")
        detail = details.get(instrument_id)
        task["instrument_name"] = (
            str(detail.get("instrument_name") or "")
            if isinstance(detail, dict)
            else None
        ) or None
        records.append(InstrumentEventTaskRecord.model_validate(task))
    return records


@router.get(
    "/{portfolio_id}/instrument-event-tasks",
    response_model=InstrumentEventTaskListResponse,
)
def list_portfolio_instrument_event_tasks(
    portfolio_id: str,
    attention_only: bool = Query(default=False),
) -> InstrumentEventTaskListResponse:
    _require_portfolio(portfolio_id)
    try:
        reconcile_instrument_event_tasks(portfolio_ids=[portfolio_id])
        tasks = _current_task_records(
            portfolio_id,
            attention_only=attention_only,
        )
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return InstrumentEventTaskListResponse(
        portfolio_id=portfolio_id,
        accounting_policy="official_unit_nav_assume_no_unrecorded_distribution",
        attention_count=sum(1 for task in tasks if task.attention_required),
        tasks=tasks,
    )


@router.post(
    "/{portfolio_id}/instrument-event-tasks/{instrument_event_task_id}/reviews",
    response_model=InstrumentEventTaskRecord,
)
def review_portfolio_instrument_event_task(
    portfolio_id: str,
    instrument_event_task_id: str,
    payload: InstrumentEventTaskReviewRequest,
) -> InstrumentEventTaskRecord:
    _require_portfolio(portfolio_id)
    try:
        reconcile_instrument_event_tasks(portfolio_ids=[portfolio_id])
        review_instrument_event_task(
            portfolio_id=portfolio_id,
            instrument_event_task_id=instrument_event_task_id,
            decision=payload.decision,
            transaction_ids=payload.transaction_ids,
            note=payload.note,
            reviewed_by=payload.reviewed_by,
            expected_row_version=payload.expected_row_version,
        )
        record = next(
            (
                task
                for task in _current_task_records(
                    portfolio_id,
                    attention_only=False,
                )
                if task.instrument_event_task_id == instrument_event_task_id
            ),
            None,
        )
    except InstrumentEventTaskNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except InstrumentEventTaskConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument event task not found.")
    return record
