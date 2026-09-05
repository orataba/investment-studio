from __future__ import annotations

from investment_studio_instrument_core import fx_rates as shared_fx_rates
from investment_studio_instrument_core import instrument_store as shared_store

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


def get_registry_instrument_details(
    instrument_ids: list[str] | set[str] | tuple[str, ...],
) -> dict[str, dict[str, object] | None]:
    """Load market-data details without hydrating the fund NAV audit ledger."""
    try:
        return shared_store.get_instrument_details(
            get_session_factory(),
            instrument_ids,
            include_fund_nav_ledger=False,
        )
    except Exception as error:  # pragma: no cover - defensive wrapper
        raise InstrumentRegistryError("Failed to query shared instrument registry.") from error


def get_registry_instrument_event_details(
    instrument_ids: list[str] | set[str] | tuple[str, ...],
) -> dict[str, dict[str, object] | None]:
    try:
        return shared_store.get_instrument_event_details(
            get_session_factory(),
            instrument_ids,
        )
    except Exception as error:  # pragma: no cover - defensive wrapper
        raise InstrumentRegistryError(
            "Failed to query shared instrument event ledgers."
        ) from error


def get_registry_instrument_summaries(
    instrument_ids: list[str] | set[str] | tuple[str, ...],
) -> dict[str, dict[str, object] | None]:
    try:
        return shared_store.get_instrument_summaries(
            get_session_factory(),
            instrument_ids,
        )
    except Exception as error:  # pragma: no cover - defensive wrapper
        raise InstrumentRegistryError(
            "Failed to query shared instrument summaries."
        ) from error


def list_registry_corporate_actions(
    instrument_ids: list[str] | set[str] | tuple[str, ...],
    *,
    effective_on_or_before=None,
) -> list[dict[str, object]]:
    try:
        return shared_store.list_corporate_actions(
            get_session_factory(),
            instrument_ids,
            effective_on_or_before=effective_on_or_before,
        )
    except Exception as error:  # pragma: no cover - defensive wrapper
        raise InstrumentRegistryError("Failed to query shared corporate actions.") from error


def get_shared_fx_rates() -> dict[str, object]:
    try:
        return shared_fx_rates.get_fx_payload(get_session_factory())
    except Exception as error:  # pragma: no cover - defensive wrapper
        raise InstrumentRegistryError("Failed to query shared FX rates.") from error
