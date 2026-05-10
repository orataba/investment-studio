from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from watchlist_app.api.contracts import FundCopilotChatRequest, WatchlistCopilotChatRequest
from watchlist_app.db.session import get_db_session
from watchlist_app.services.copilot import CopilotService


router = APIRouter()
copilot_service = CopilotService()


@router.post("/watchlists/{watchlist_id}/chat")
def chat_watchlist(
    watchlist_id: str,
    payload: WatchlistCopilotChatRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    advanced_filters = (
        payload.advanced_filters.model_dump()
        if payload.advanced_filters is not None
        else None
    )
    return copilot_service.chat_watchlist(
        session,
        watchlist_id=watchlist_id,
        question=payload.question.strip(),
        view_id=payload.view_id,
        selected_fields=payload.selected_fields,
        filters=payload.filters,
        advanced_filters=advanced_filters,
        sort=[rule.model_dump() for rule in payload.sort],
        group_by=payload.group_by,
    )


@router.post("/instruments/{instrument_id}/chat")
def chat_fund(
    instrument_id: str,
    payload: FundCopilotChatRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    return copilot_service.chat_fund(
        session,
        instrument_id=instrument_id,
        question=payload.question.strip(),
        active_tab=payload.active_tab,
    )
