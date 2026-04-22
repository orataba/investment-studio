from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from watchlist_app.api.contracts import ScreenerQueryRequest
from watchlist_app.db.session import get_db_session
from watchlist_app.repositories.sqlalchemy.read_models import SQLAlchemyReadModelRepository
from watchlist_app.repositories.sqlalchemy.watchlists import SQLAlchemyWatchlistRepository
from watchlist_app.services.read_models import execute_watchlist_query
from watchlist_app.services.read_model_freshness import (
    latest_local_market_data_date,
    schedule_asset_refresh_if_stale,
)


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
        [row.asset_id for row in rows],
    )
    response = execute_watchlist_query(
        rows=rows,
        charts=charts,
        payload=payload_data,
        view=view,
    )
    chart_map = {item.asset_id: item for item in charts}
    row_map = {item.asset_id: item for item in rows}
    for item in response.get("rows", []):
        asset_id = str(item.get("asset_id") or "").strip()
        if not asset_id:
            continue
        chart_record = chart_map.get(asset_id)
        row_record = row_map.get(asset_id)
        schedule_asset_refresh_if_stale(
            asset_id=asset_id,
            local_latest_date=latest_local_market_data_date(
                chart_payload=getattr(chart_record, "payload_json", None),
                fallback_values=(
                    getattr(row_record, "last_nav_date", None),
                    item.get("latest_quote_date"),
                    item.get("last_nav_date"),
                ),
            ),
            trigger_ref_type="screener_query",
            trigger_ref_id=payload.watchlist_id,
        )
    return response
