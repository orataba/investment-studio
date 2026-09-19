from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from watchlist_app.api.contracts import InstrumentBulkResolveRequest
from watchlist_app.db.session import get_db_session
from watchlist_app.services.instrument_resolution import resolve_watchlist_instrument
from watchlist_app.services.identifier_files import (
    MAX_IDENTIFIER_FILE_BYTES,
    parse_identifier_file,
)
from watchlist_app.services.shared_instrument_registry import (
    SharedInstrumentRegistryError,
    get_shared_instrument,
    get_shared_price_bars,
    get_shared_reference_data,
    list_shared_instruments,
    resolve_shared_instrument,
    search_shared_instrument_identities,
)


router = APIRouter()
PRICE_BAR_INSTRUMENT_TYPES = {"etf", "equity", "index", "crypto"}


@router.get("/{instrument_id}/reference-data")
def read_instrument_reference(instrument_id: str) -> dict[str, object]:
    """Read a collected asset snapshot without contacting providers."""
    record = get_shared_reference_data(instrument_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return record


@router.get("")
def list_instrument_records(
    search: str | None = Query(default=None),
    instrument_type: str | None = Query(default=None),
    limit: int | None = Query(default=20, ge=1, le=100),
) -> list[dict[str, object]]:
    try:
        return list_shared_instruments(search=search, instrument_type=instrument_type, limit=limit)
    except SharedInstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.get("/search")
def search_instrument_records(
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=12, ge=1, le=100),
) -> list[dict[str, object]]:
    try:
        return search_shared_instrument_identities(search=q, limit=limit)
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


@router.post("/resolve-bulk")
def resolve_shared_instrument_records(
    payload: InstrumentBulkResolveRequest,
) -> dict[str, object]:
    identifiers = list(
        dict.fromkeys(value.strip() for value in payload.identifiers if value.strip())
    )
    if not identifiers:
        raise HTTPException(status_code=422, detail="At least one non-empty identifier is required")
    results: list[dict[str, object]] = []
    try:
        for identifier in identifiers:
            record = resolve_shared_instrument(identifier_value=identifier)
            results.append(
                {
                    "identifier": identifier,
                    "status": "resolved" if record is not None else "not_found",
                    "instrument": record,
                }
            )
    except SharedInstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {"results": results}


@router.post("/resolve-file")
async def resolve_shared_instrument_file(
    file: UploadFile = File(...),
) -> dict[str, object]:
    filename = file.filename
    try:
        content = await file.read(MAX_IDENTIFIER_FILE_BYTES + 1)
    finally:
        await file.close()
    try:
        identifiers = parse_identifier_file(filename, content)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return resolve_shared_instrument_records(
        InstrumentBulkResolveRequest(identifiers=identifiers)
    )


@router.get("/{instrument_id}/price-bars")
def get_instrument_price_bars(
    instrument_id: str,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    limit: int = Query(default=1250, ge=1, le=5000),
) -> dict[str, object]:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise HTTPException(
            status_code=422,
            detail="start_date must be on or before end_date",
        )
    try:
        instrument = get_shared_instrument(instrument_id)
        if instrument is None:
            raise HTTPException(status_code=404, detail="Instrument not found")
        canonical_id = str(instrument.get("instrument_id") or instrument_id)
        instrument_type = str(instrument.get("instrument_type") or "").strip().lower()
        if instrument_type not in PRICE_BAR_INSTRUMENT_TYPES:
            raise HTTPException(
                status_code=422,
                detail=f'{instrument_type or "unknown"} instruments do not expose OHLCV bars',
            )
        bars = get_shared_price_bars(
            instrument_id=canonical_id,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
    except HTTPException:
        raise
    except SharedInstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    factor_count = sum(1 for bar in bars if bar.get("adjustment_factor") is not None)
    refresh_status = (
        dict(instrument.get("refresh_status", {}))
        if isinstance(instrument.get("refresh_status"), dict)
        else {}
    )
    return {
        "instrument_id": canonical_id,
        "instrument_type": instrument_type,
        "currency": str(instrument.get("currency") or ""),
        "adjustment_mode": "raw_with_factor" if factor_count else "raw",
        "factor_coverage": factor_count / len(bars) if bars else 0.0,
        "source_refresh_status": str(refresh_status.get("status") or "idle"),
        "source_refresh_message": str(refresh_status.get("message") or ""),
        "count": len(bars),
        "bars": bars,
    }


@router.post("/{instrument_id}/resolve")
def resolve_instrument_detail(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    try:
        record = resolve_watchlist_instrument(session, instrument_id=instrument_id)
    except SharedInstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    session.commit()
    return record
