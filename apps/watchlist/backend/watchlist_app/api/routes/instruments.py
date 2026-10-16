from datetime import date
from math import isfinite

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
    limit: int | None = Query(default=None, ge=1, le=5000),
) -> dict[str, object]:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise HTTPException(
            status_code=422,
            detail="start_date must be on or before end_date",
        )
    try:
        instrument = get_shared_instrument(instrument_id, metadata_only=True)
        if instrument is None:
            raise HTTPException(status_code=404, detail="Instrument not found")
        canonical_id = str(instrument.get("instrument_id") or instrument_id)
        instrument_type = str(instrument.get("instrument_type") or "").strip().lower()
        if instrument_type not in PRICE_BAR_INSTRUMENT_TYPES:
            raise HTTPException(
                status_code=422,
                detail=f'{instrument_type or "unknown"} instruments do not expose OHLCV bars',
            )
        # Fixed full-history anchors make display adjustment independent of
        # the requested chart range. Reads never mutate prices or analytical TR.
        all_bars = get_shared_price_bars(instrument_id=canonical_id, end_date=end_date)
        all_bars = [bar for bar in all_bars if end_date is None or str(bar["date"]) <= end_date.isoformat()]
        bars = [bar for bar in all_bars
            if (start_date is None or str(bar["date"]) >= start_date.isoformat())
            and (end_date is None or str(bar["date"]) <= end_date.isoformat())]
        if limit is not None:
            bars = bars[-limit:]
    except HTTPException:
        raise
    except SharedInstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    def valid_factor(bar):
        try:
            factor = float(bar.get("adjustment_factor"))
            return not isinstance(bar.get("adjustment_factor"), bool) and isfinite(factor) and factor > 0
        except (TypeError, ValueError):
            return False

    factor_count = sum(valid_factor(bar) for bar in bars)
    factor_bars = [bar for bar in all_bars if valid_factor(bar)]
    anchors = {}
    unavailable_modes = {}
    available_modes = ["raw"]
    settings = instrument.get("source_settings") or {}
    if instrument_type in {"equity", "etf"}:
        segments = settings.get("total_return_segments")
        for mode in ("front", "back"):
            if not factor_bars:
                unavailable_modes[mode] = "No confirmed multiplicative total-return factors are available."
                continue
            anchor = factor_bars[-1] if mode == "front" else factor_bars[0]
            segment = (next((item for item in segments if isinstance(item, dict)
                and str(item.get("start_date") or "") <= anchor["date"] <= str(item.get("end_date") or "")), None)
                if isinstance(segments, list) else {"start_date": all_bars[0]["date"], "end_date": all_bars[-1]["date"]})
            if not segment:
                unavailable_modes[mode] = "The anchor is outside confirmed total-return coverage."
                continue
            anchors[mode] = {"date": anchor["date"], "factor": float(anchor["adjustment_factor"]),
                "start_date": segment["start_date"], "end_date": min(segment["end_date"], all_bars[-1]["date"])}
            selected = [bar for bar in bars if segment["start_date"] <= bar["date"] <= segment["end_date"]]
            if not selected:
                unavailable_modes[mode] = "The requested range has no observations in the anchor's confirmed segment."
            elif any(not valid_factor(bar) for bar in selected):
                unavailable_modes[mode] = "Some requested observations have no confirmed adjustment factor."
            elif mode == "front" and bars and bars[-1]["date"] > anchor["date"]:
                unavailable_modes[mode] = "The latest observed price is newer than the last confirmed adjustment factor."
            else:
                available_modes.append(mode)
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
        "available_price_modes": available_modes,
        "unavailable_price_mode_reasons": unavailable_modes,
        "adjustment_method": "multiplicative_total_return_chain" if anchors else None,
        "adjustment_anchors": anchors,
        "total_return_segments": settings.get("total_return_segments"),
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
