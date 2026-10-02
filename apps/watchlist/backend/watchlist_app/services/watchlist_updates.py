"""Persist list edits without making them depend on synchronous price calculations."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session
from studio_identity import current_principal

from watchlist_app.db.models.watchlists import InstrumentAttributeValue
from watchlist_app.repositories.sqlalchemy.instrument_attributes import SQLAlchemyInstrumentAttributeRepository
from watchlist_app.services.read_models import collapse_latest_attribute_values
from watchlist_app.services.recalc import queue_configuration_recalculation

attributes = SQLAlchemyInstrumentAttributeRepository()


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
    queue_configuration_recalculation(session, instrument_id=instrument_id,
        trigger_type="watchlist_update", trigger_ref_type="watchlist", trigger_ref_id=watchlist_id,
        configuration_changed=False)
