from __future__ import annotations

from datetime import date, datetime

from investment_studio_instrument_core import instrument_store as shared_store
from sqlalchemy import select

from watchlist_app.db.session import get_session_factory
from investment_studio_instrument_core.db_models import InstrumentReferenceObservation, InstrumentReferenceSnapshot


def get_shared_reference_data(instrument_id: str, *, as_of: datetime | None = None) -> dict[str, object] | None:
    instrument = get_shared_instrument(instrument_id)
    if instrument is None:
        return None
    canonical_id = str(instrument["instrument_id"])
    with get_session_factory()() as session:
        if as_of is None:
            snapshot = session.get(InstrumentReferenceSnapshot, canonical_id)
        else:
            if as_of.tzinfo is None:
                raise ValueError("Reference cutoff must include a timezone.")
            snapshot = session.scalar(select(InstrumentReferenceObservation).where(
                InstrumentReferenceObservation.instrument_id == canonical_id,
                InstrumentReferenceObservation.collected_at <= as_of,
            ).order_by(InstrumentReferenceObservation.collected_at.desc(),
                       InstrumentReferenceObservation.observation_id.desc()).limit(1))
        if snapshot is not None:
            # Historical reference observations can still contain the former
            # embedded packet. Current research reads canonical numeric batches.
            return {**snapshot.value_json, "sections": {key: value for key, value in
                (snapshot.value_json.get("sections") or {}).items() if key != "sector_market_data"}}
    return {
        "instrument_id": canonical_id,
        "instrument_type": instrument["instrument_type"],
        "provider": "unavailable", "provider_symbol": None, "fetched_at": None,
        "source": {}, "sections": {},
        "section_errors": {"reference": "No retained reference data is available for the requested time."},
    }


class SharedInstrumentRegistryError(RuntimeError):
    pass


class SharedInstrumentRegistryTransportError(SharedInstrumentRegistryError):
    pass


class SharedInstrumentRegistryHttpError(SharedInstrumentRegistryError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class SharedInstrumentRegistryNotFoundError(SharedInstrumentRegistryHttpError):
    def __init__(self, message: str = "Instrument not found in shared registry.") -> None:
        super().__init__(status_code=404, message=message)


def _registry_error(message: str) -> SharedInstrumentRegistryTransportError:
    return SharedInstrumentRegistryTransportError(message)


def list_shared_instruments(
    *,
    search: str | None = None,
    instrument_type: str | None = None,
    limit: int | None = None,
) -> list[dict[str, object]]:
    try:
        return shared_store.list_instruments(
            get_session_factory(),
            search=search,
            instrument_type=instrument_type,
            limit=limit,
        )
    except Exception as error:  # pragma: no cover - defensive wrapper
        raise _registry_error("Failed to query shared instrument registry.") from error


def search_shared_instrument_identities(*, search: str, limit: int) -> list[dict[str, object]]:
    from watchlist_app.services.instrument_resolution import LOCAL_DETAIL_INSTRUMENT_TYPES

    try:
        return shared_store.search_instrument_identities(
            get_session_factory(), search=search,
            instrument_types=LOCAL_DETAIL_INSTRUMENT_TYPES, limit=limit,
        )
    except Exception as error:
        raise _registry_error("Failed to search shared instrument registry.") from error


def list_shared_instrument_identities(
    *, instrument_type: str | None = None,
) -> list[dict[str, object]]:
    """Read the complete supported directory without quotes or event histories."""
    from watchlist_app.services.instrument_resolution import LOCAL_DETAIL_INSTRUMENT_TYPES

    try:
        return shared_store.search_instrument_identities(
            get_session_factory(),
            search="",
            instrument_types=(
                {instrument_type} if instrument_type else LOCAL_DETAIL_INSTRUMENT_TYPES
            ),
            limit=None,
        )
    except Exception as error:
        raise _registry_error("Failed to query shared instrument registry identities.") from error


def list_shared_active_instrument_ids(
    *,
    instrument_types: set[str] | frozenset[str] | None = None,
) -> list[str]:
    try:
        return shared_store.list_active_instrument_ids(
            get_session_factory(),
            instrument_types=instrument_types,
        )
    except Exception as error:  # pragma: no cover - defensive wrapper
        raise _registry_error("Failed to query shared instrument registry membership.") from error


def get_shared_instrument_summaries(
    instrument_ids: list[str] | set[str] | tuple[str, ...],
) -> dict[str, dict[str, object] | None]:
    """Load a bounded set of Registry records without an all-registry scan."""

    try:
        return shared_store.get_instrument_summaries(
            get_session_factory(),
            instrument_ids,
        )
    except Exception as error:  # pragma: no cover - defensive wrapper
        raise _registry_error("Failed to query shared instrument registry.") from error


def get_shared_instrument(instrument_id: str) -> dict[str, object] | None:
    try:
        record = shared_store.get_instrument(get_session_factory(), instrument_id)
        visited = {instrument_id}
        while isinstance(record, dict):
            lifecycle = record.get("lifecycle_state")
            if not isinstance(lifecycle, dict):
                break
            canonical_id = str(lifecycle.get("canonical_instrument_id") or "").strip()
            status = str(lifecycle.get("status") or "active").strip().lower()
            if status != "archived":
                break
            if not canonical_id or canonical_id in visited:
                return None
            visited.add(canonical_id)
            canonical = shared_store.get_instrument(get_session_factory(), canonical_id)
            if not isinstance(canonical, dict):
                return None
            record = canonical
        return record
    except Exception as error:  # pragma: no cover - defensive wrapper
        raise _registry_error("Failed to query shared instrument registry.") from error


def get_shared_price_bars(
    *,
    instrument_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int | None = None,
) -> list[dict[str, object]]:
    try:
        return shared_store.get_price_bars(
            get_session_factory(),
            instrument_id=instrument_id,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
    except Exception as error:  # pragma: no cover - defensive wrapper
        raise _registry_error("Failed to query shared OHLCV price bars.") from error


def resolve_shared_instrument(
    *,
    identifier_value: str,
    identifier_type: str | None = None,
) -> dict[str, object] | None:
    normalized_value = identifier_value.strip()
    if not normalized_value:
        return None
    try:
        record = shared_store.find_instrument_by_identifier(
            get_session_factory(),
            identifier_value=normalized_value,
            identifier_type=identifier_type,
        )
        if not isinstance(record, dict):
            return None
        instrument_id = str(record.get("instrument_id") or "").strip()
        return get_shared_instrument(instrument_id) if instrument_id else None
    except Exception as error:  # pragma: no cover - defensive wrapper
        raise _registry_error("Failed to query shared instrument registry.") from error
