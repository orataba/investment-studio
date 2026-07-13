from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from watchlist_app.api.contracts import ScreenerQueryRequest
from watchlist_app.db.session import get_db_session
from watchlist_app.repositories.sqlalchemy.read_models import SQLAlchemyReadModelRepository
from watchlist_app.repositories.sqlalchemy.watchlists import SQLAlchemyWatchlistRepository
from watchlist_app.services.read_models import execute_watchlist_query


router = APIRouter()
read_model_repository = SQLAlchemyReadModelRepository()
watchlist_repository = SQLAlchemyWatchlistRepository()


@router.post("/query")
def run_screener_query(
    payload: ScreenerQueryRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    if watchlist_repository.get(session, payload.watchlist_id) is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    payload_data = payload.model_dump(exclude_unset=True)
    view = None
    if payload.view_id:
        view = read_model_repository.get_view(
            session,
            watchlist_id=payload.watchlist_id,
            view_id=payload.view_id,
        )
        if view is None:
            raise HTTPException(status_code=404, detail="Watchlist view not found")
    rows = read_model_repository.list_watchlist_rows(session, payload.watchlist_id)
    charts = read_model_repository.list_charts(
        session,
        [row.instrument_id for row in rows],
    )
    response = execute_watchlist_query(
        rows=rows,
        charts=charts,
        payload=payload_data,
        view=view,
    )
    return response
