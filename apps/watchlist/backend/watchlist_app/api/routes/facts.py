from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from watchlist_app.api.contracts import FundHoldingSnapshotIngestRequest
from watchlist_app.db.session import get_db_session
from watchlist_app.repositories.sqlalchemy.instruments import SQLAlchemyInstrumentRepository
from watchlist_app.repositories.sqlalchemy.facts import SQLAlchemyFactsRepository
from watchlist_app.services.canonical_recalc import CanonicalRecalcService


router = APIRouter()
facts_repository = SQLAlchemyFactsRepository()
instrument_repository = SQLAlchemyInstrumentRepository()
canonical_recalc_service = CanonicalRecalcService()


def _ensure_instrument_exists(session: Session, instrument_id: str) -> None:
    if instrument_repository.get(session, instrument_id) is None:
        raise HTTPException(status_code=404, detail="Instrument not found")


@router.get("/instruments/{instrument_id}/nav")
def list_nav_facts(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    records = facts_repository.list_nav_facts(
        session,
        instrument_id=instrument_id,
        nav_type=None,
        primary_only=True,
    )
    return {
        "instrument_id": instrument_id,
        "rows": [
            {
                "nav_fact_id": item.nav_fact_id,
                "as_of_date": item.as_of_date.isoformat(),
                "nav_type": item.nav_type,
                "value": float(item.value),
                "currency": item.currency,
                "frequency": item.frequency,
                "is_primary": item.is_primary,
                "observation_id": item.observation_id,
                "adopted_at": item.adopted_at.isoformat().replace("+00:00", "Z"),
            }
            for item in records
        ],
        "total_rows": len(records),
    }


@router.get("/instruments/{instrument_id}/holdings/current")
def get_current_instrument_holding_snapshot(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    record = facts_repository.get_current_holding_snapshot(session, instrument_id=instrument_id)
    if record is None:
        return {"instrument_id": instrument_id, "holding_snapshot_id": None, "positions": [], "position_count": 0}
    return {
        "instrument_id": instrument_id,
        "holding_snapshot_id": record.holding_snapshot_id,
        "as_of_date": record.as_of_date.isoformat(),
        "source_cutoff_at": record.source_cutoff_at.isoformat().replace("+00:00", "Z"),
        "methodology_version": record.methodology_version,
        "positions": [
            {
                "holding_name": item.holding_name,
                "holding_type": item.holding_type,
                "portfolio_weight": float(item.portfolio_weight) if item.portfolio_weight is not None else None,
                "market_value": float(item.market_value) if item.market_value is not None else None,
                "quantity": float(item.quantity) if item.quantity is not None else None,
                "currency": item.currency,
                "market_price": float(item.market_price) if item.market_price is not None else None,
                "share_change_pct": float(item.share_change_pct) if item.share_change_pct is not None else None,
                "maturity_date": item.maturity_date.isoformat() if item.maturity_date else None,
                "coupon_rate": float(item.coupon_rate) if item.coupon_rate is not None else None,
                "credit_rating": item.credit_rating,
                "effective_duration": float(item.effective_duration) if item.effective_duration is not None else None,
                "modified_duration": float(item.modified_duration) if item.modified_duration is not None else None,
                "yield_to_worst": float(item.yield_to_worst) if item.yield_to_worst is not None else None,
                "issuer_name": item.issuer_name,
                "issuer_type": item.issuer_type,
                "security_identifier": item.security_identifier,
                "sector": item.sector,
                "country_code": item.country_code,
            }
            for item in record.positions
        ],
        "position_count": len(record.positions),
    }


@router.post("/instruments/{instrument_id}/holdings")
def ingest_instrument_holding_snapshot(
    instrument_id: str,
    payload: FundHoldingSnapshotIngestRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    result = canonical_recalc_service.ingest_instrument_holding_snapshot(
        session,
        instrument_id=instrument_id,
        as_of_date=payload.as_of_date,
        source_cutoff_at=payload.source_cutoff_at,
        methodology_version=payload.methodology_version,
        source_record_id=payload.source_record_id,
        positions=[item.model_dump() for item in payload.positions],
        auto_recalculate=payload.auto_recalculate,
    )
    session.commit()
    return result
