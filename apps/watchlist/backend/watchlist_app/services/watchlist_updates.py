"""Persist list edits without making them depend on synchronous price calculations."""
from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from studio_identity import current_principal

from watchlist_app.db.models.recalc import RecalcJob
from watchlist_app.db.models.watchlists import InstrumentAttributeValue
from watchlist_app.repositories.sqlalchemy.instrument_attributes import SQLAlchemyInstrumentAttributeRepository
from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
from watchlist_app.services.read_models import collapse_latest_attribute_values
from watchlist_app.services.recalc_job_ids import make_recalc_dedupe_key, make_recalc_job_id

attributes = SQLAlchemyInstrumentAttributeRepository()
jobs = SQLAlchemyRecalcJobRepository()


def lock_watchlist_instruments(session: Session, instrument_ids: list[str]) -> None:
    # Only serialize short list edits. The calculation lock can be held by an
    # explicit synchronous recalculation for its entire run, so it is separate.
    if session.get_bind().dialect.name == "postgresql":
        for instrument_id in sorted(set(instrument_ids)):
            session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:instrument_id, 20260929))"),
                {"instrument_id": instrument_id})


def set_coverage_status(session: Session, *, instrument_id: str, status: str) -> None:
    current = collapse_latest_attribute_values(attributes.get_values_for_asset(session, instrument_id))
    if current.get("coverage_status") != status:
        attributes.add_value(session, instrument_id=instrument_id, attribute_key="coverage_status",
            value_json=status, effective_from=None,
            source_record_id=current_principal().user_id or current_principal().service_id)


def coverage_status_overrides(session: Session, instrument_ids: list[str]) -> dict[str, dict[str, object]]:
    """Read authoritative status before filtering, sorting and grouping list rows."""
    if not instrument_ids:
        return {}
    values = session.execute(select(InstrumentAttributeValue.instrument_id, InstrumentAttributeValue.value_json)
        .where(InstrumentAttributeValue.instrument_id.in_(instrument_ids),
               InstrumentAttributeValue.attribute_key == "coverage_status")
        .order_by(InstrumentAttributeValue.adopted_at.desc(), InstrumentAttributeValue.instrument_attribute_value_id.desc()))
    result: dict[str, dict[str, object]] = {}
    for instrument_id, value in values:
        if instrument_id not in result:
            result[instrument_id] = {"coverage_status": value}
    return {instrument_id: result.get(instrument_id, {"coverage_status": None}) for instrument_id in instrument_ids}


def queue_watchlist_recalculation(session: Session, *, instrument_id: str, watchlist_id: str) -> None:
    lock_watchlist_instruments(session, [instrument_id])

    def queued():
        return session.scalar(select(RecalcJob).where(RecalcJob.instrument_id == instrument_id,
            RecalcJob.job_type == "all", RecalcJob.job_status == "queued").limit(1).with_for_update(skip_locked=True))

    if queued() is not None:
        return
    # A worker may have already locked/claimed the previous job and read old
    # membership. Its successor gets a fresh identity, avoiding its long-held
    # unique key. Later additions coalesce into this unlocked queued successor.
    job_id = make_recalc_job_id()
    jobs.create(session, recalc_job_id=job_id, job_type="all",
        instrument_id=instrument_id, trigger_type="watchlist_update",
        trigger_ref_type="watchlist", trigger_ref_id=watchlist_id,
        job_status="queued", priority=100,
        dedupe_key=f"{make_recalc_dedupe_key(job_type='all', instrument_id=instrument_id)}:watchlist_update:{job_id}",
        payload_json={"requested_by": "watchlist_update"})
