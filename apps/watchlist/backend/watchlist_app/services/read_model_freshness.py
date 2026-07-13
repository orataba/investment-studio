from __future__ import annotations

from datetime import date
import logging
from typing import Mapping

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from portfolio_ops_instrument_core.db_models import (
    Instrument as CanonicalInstrument,
    InstrumentIdentifier as CanonicalInstrumentIdentifier,
)
from portfolio_ops_instrument_core.quote_resolver import (
    CANONICAL_QUOTE_RESOLVER_STRATEGY_VERSION,
    QUOTE_SELECTION_POLICY_VERSION,
    resolve_role_quote_series_in_session,
)

from watchlist_app.core.settings import get_settings
from watchlist_app.db.models.instruments import InstrumentDetail
from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
from watchlist_app.services.recalc_job_ids import make_recalc_dedupe_key, make_recalc_job_id
from watchlist_app.services.quote_consumer_policy import (
    quote_consumer_dependency,
    watchlist_freshness_profile,
)


logger = logging.getLogger(__name__)
recalc_repository = SQLAlchemyRecalcJobRepository()

def quote_dependency_fingerprint(payload: object | None) -> str | None:
    """Read Watchlist's canonical-plus-consumer dependency fingerprint."""

    if not isinstance(payload, Mapping):
        return None
    for resolution in (
        payload,
        payload.get("quote_resolution"),
        payload.get("resolution"),
    ):
        if not isinstance(resolution, Mapping):
            continue
        dependency = resolution.get("consumer_dependency")
        if not isinstance(dependency, Mapping):
            continue
        fingerprint = str(dependency.get("fingerprint") or "").strip()
        if fingerprint:
            return fingerprint
    return None


def _metadata_drift(
    local_instrument: InstrumentDetail,
    canonical_instrument: CanonicalInstrument,
    canonical_identifier: CanonicalInstrumentIdentifier | None,
) -> bool:
    return (
        local_instrument.instrument_name != canonical_instrument.instrument_name
        or local_instrument.instrument_type != canonical_instrument.instrument_type
        or local_instrument.primary_identifier_type
        != (canonical_identifier.identifier_type if canonical_identifier is not None else None)
        or local_instrument.primary_identifier_value
        != (canonical_identifier.identifier_value if canonical_identifier is not None else None)
    )


def schedule_instrument_refresh_if_stale(
    session: Session,
    *,
    instrument_id: str,
    role: str,
    local_dependency_fingerprint: str | None,
    current_dependency_fingerprint: str | None = None,
    valuation_date: date,
    trigger_ref_type: str,
    trigger_ref_id: str | None = None,
) -> bool:
    """Compare one read model with the canonical role dependency in the same UoW."""

    normalized_instrument_id = instrument_id.strip()
    if not normalized_instrument_id:
        return False
    local_instrument = session.get(InstrumentDetail, normalized_instrument_id)
    canonical_instrument = session.get(CanonicalInstrument, normalized_instrument_id)
    if local_instrument is None or canonical_instrument is None:
        return False
    canonical_identifier = session.scalar(
        select(CanonicalInstrumentIdentifier)
        .where(
            CanonicalInstrumentIdentifier.instrument_id == normalized_instrument_id
        )
        .order_by(
            CanonicalInstrumentIdentifier.is_primary.desc(),
            CanonicalInstrumentIdentifier.instrument_identifier_id,
        )
        .limit(1)
    )
    current_fingerprint = current_dependency_fingerprint
    if current_fingerprint is None:
        try:
            consumer_profile = watchlist_freshness_profile(
                canonical_instrument.instrument_type
            )
            current = resolve_role_quote_series_in_session(
                session,
                resolver_strategy_version=CANONICAL_QUOTE_RESOLVER_STRATEGY_VERSION,
                instrument_id=normalized_instrument_id,
                role=role,
                currency=canonical_instrument.currency,
                range_mode="since_inception",
                start_date=None,
                end_date=valuation_date,
                freshness_policy=consumer_profile.resolver_policy,
                quote_selection_policy_version=QUOTE_SELECTION_POLICY_VERSION,
            )
            current_fingerprint = str(
                quote_consumer_dependency(
                    canonical_dependency_fingerprint=(
                        current.calculation_dependency.fingerprint
                    ),
                    consumer_profile=consumer_profile,
                )["fingerprint"]
            )
        except Exception:
            logger.exception(
                "Canonical stale-read comparison failed for %s.",
                normalized_instrument_id,
            )
            return False
    if (
        local_dependency_fingerprint == current_fingerprint
        and not _metadata_drift(
            local_instrument,
            canonical_instrument,
            canonical_identifier,
        )
    ):
        return False
    return _enqueue_stale_recalc_job(
        session,
        instrument_id=normalized_instrument_id,
        trigger_ref_type=trigger_ref_type,
        trigger_ref_id=trigger_ref_id or current_fingerprint,
    )


def _enqueue_stale_recalc_job(
    session: Session,
    *,
    instrument_id: str,
    trigger_ref_type: str,
    trigger_ref_id: str | None,
) -> bool:
    """Queue a repair in the caller's Session; no second registry/session boundary."""

    try:
        settings = get_settings()
        existing = recalc_repository.find_open_job(
            session,
            instrument_id=instrument_id,
            job_type="all",
            running_timeout_seconds=settings.recalc_worker_running_job_timeout_seconds,
        )
        if existing is not None:
            return True
        with session.begin_nested():
            recalc_repository.create(
                session,
                recalc_job_id=make_recalc_job_id(),
                job_type="all",
                instrument_id=instrument_id,
                trigger_type="stale_read_repair",
                trigger_ref_type=trigger_ref_type,
                trigger_ref_id=trigger_ref_id,
                job_status="queued",
                priority=95,
                dedupe_key=make_recalc_dedupe_key(
                    job_type="all",
                    instrument_id=instrument_id,
                ),
                payload_json={"requested_by": "stale_read_repair"},
            )
        session.commit()
        return True
    except IntegrityError:
        session.rollback()
        return True
    except Exception:
        session.rollback()
        logger.exception("Stale read repair enqueue failed for %s.", instrument_id)
        return False
