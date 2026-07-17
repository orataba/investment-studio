from __future__ import annotations

from datetime import date

from portfolio_ops_instrument_core import instrument_store as shared_store

from watchlist_app.db.session import get_session_factory


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
            if status != "archived" or not canonical_id or canonical_id in visited:
                break
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
        return shared_store.find_instrument_by_identifier(
            get_session_factory(),
            identifier_value=normalized_value,
            identifier_type=identifier_type,
        )
    except Exception as error:  # pragma: no cover - defensive wrapper
        raise _registry_error("Failed to query shared instrument registry.") from error
