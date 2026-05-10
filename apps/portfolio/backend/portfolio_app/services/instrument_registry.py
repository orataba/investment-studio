from __future__ import annotations

from yungu_instrument_core import fx_rates as shared_fx_rates
from yungu_instrument_core import instrument_store as shared_store

from portfolio_app.db.session import get_session_factory


class InstrumentRegistryError(RuntimeError):
    pass


def list_registry_instruments() -> list[dict[str, object]]:
    try:
        return shared_store.list_instruments(get_session_factory())
    except Exception as error:  # pragma: no cover - defensive wrapper
        raise InstrumentRegistryError("Failed to query shared instrument registry.") from error


def get_registry_instrument(instrument_id: str) -> dict[str, object] | None:
    try:
        return shared_store.get_instrument(get_session_factory(), instrument_id)
    except Exception as error:  # pragma: no cover - defensive wrapper
        raise InstrumentRegistryError("Failed to query shared instrument registry.") from error


def get_registry_instrument_detail(instrument_id: str) -> dict[str, object] | None:
    return get_registry_instrument(instrument_id)


def get_platform_fx_rates() -> dict[str, object]:
    try:
        return shared_fx_rates.get_fx_payload(get_session_factory())
    except Exception as error:  # pragma: no cover - defensive wrapper
        raise InstrumentRegistryError("Failed to query shared FX rates.") from error
