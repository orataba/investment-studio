from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from watchlist_app.db.session import get_db_session
from watchlist_app.services.instrument_resolution import resolve_watchlist_instrument
from watchlist_app.services.shared_instrument_registry import (
    SharedInstrumentRegistryError,
    list_shared_instruments,
    resolve_shared_instrument,
)


router = APIRouter()


@router.get("")
def list_instrument_records(
    search: str | None = Query(default=None),
    asset_type: str | None = Query(default=None),
    limit: int | None = Query(default=20, ge=1, le=100),
) -> list[dict[str, object]]:
    try:
        return list_shared_instruments(search=search, asset_type=asset_type, limit=limit)
    except SharedInstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.get("/resolve")
def resolve_shared_instrument_record(
    identifier_value: str = Query(min_length=1),
    identifier_type: str | None = Query(default=None),
) -> dict[str, object]:
    try:
        record = resolve_shared_instrument(
            identifier_value=identifier_value,
            identifier_type=identifier_type,
        )
    except SharedInstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found in shared registry")
    return record


@router.get("/{asset_id}/resolve")
def resolve_instrument_detail(
    asset_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    try:
        record = resolve_watchlist_instrument(session, asset_id=asset_id)
    except SharedInstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return record
