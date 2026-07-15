from __future__ import annotations

from portfolio_ops_instrument_core import instrument_store as shared_store

from platform_app.db.session import get_session_factory


DEFAULT_REGISTRY_NAME = shared_store.DEFAULT_REGISTRY_NAME
EMPTY_STORE = shared_store.EMPTY_STORE
_normalize_store = shared_store._normalize_store
_SOURCE_SETTING_UNSET = object()


def reset_store(data: dict[str, object] | None = None) -> None:
    shared_store.reset_store(get_session_factory(), data)


def list_instruments(
    *,
    search: str | None = None,
    instrument_type: str | None = None,
    limit: int | None = None,
    include_inactive: bool = False,
) -> list[dict[str, object]]:
    return shared_store.list_instruments(
        get_session_factory(),
        search=search,
        instrument_type=instrument_type,
        limit=limit,
        include_inactive=include_inactive,
    )


def get_instrument(instrument_id: str) -> dict[str, object] | None:
    return shared_store.get_instrument(get_session_factory(), instrument_id)


def find_instrument_by_identifier(
    *,
    identifier_value: str,
    identifier_type: str | None = None,
    include_inactive: bool = False,
) -> dict[str, object] | None:
    return shared_store.find_instrument_by_identifier(
        get_session_factory(),
        identifier_value=identifier_value,
        identifier_type=identifier_type,
        include_inactive=include_inactive,
    )


def create_instrument(
    *,
    instrument_name: str,
    instrument_type: str,
    currency: str,
    identifiers: list[dict[str, object]],
    quote_selection_policy: dict[str, object] | None = None,
) -> dict[str, object]:
    return shared_store.create_instrument(
        get_session_factory(),
        instrument_name=instrument_name,
        instrument_type=instrument_type,
        currency=currency,
        identifiers=identifiers,
        quote_selection_policy=quote_selection_policy,
    )


def upsert_market_data(
    *,
    instrument_id: str,
    metric_family: str,
    quote_basis: str,
    as_of_date,
    value: str,
    currency: str,
    provider: str | None,
    status: str,
) -> dict[str, object] | None:
    return shared_store.upsert_market_data(
        get_session_factory(),
        instrument_id=instrument_id,
        metric_family=metric_family,
        quote_basis=quote_basis,
        as_of_date=as_of_date,
        value=value,
        currency=currency,
        provider=provider,
        status=status,
    )


def upsert_market_data_points(
    *,
    instrument_id: str,
    rows: list[dict[str, object]],
) -> int | None:
    return shared_store.upsert_market_data_points(
        get_session_factory(),
        instrument_id=instrument_id,
        rows=rows,
    )


def upsert_corporate_action_event(**kwargs: object) -> dict[str, object] | None:
    return shared_store.upsert_corporate_action_event(
        get_session_factory(),
        **kwargs,
    )


def upsert_source_settings(
    *,
    instrument_id: str,
    source_mode: str,
    source_email: str | None,
    source_location: str | None,
    source_api_profile: str | None,
    source_email_rules: list[dict[str, object]] | None,
    expected_frequency: str | None = None,
    market_calendar: object = _SOURCE_SETTING_UNSET,
    release_lag_days: int | None = None,
) -> dict[str, object] | None:
    optional_semantics: dict[str, object] = {}
    if expected_frequency is not None:
        optional_semantics["expected_frequency"] = expected_frequency
    if market_calendar is not _SOURCE_SETTING_UNSET:
        optional_semantics["market_calendar"] = market_calendar
    if release_lag_days is not None:
        optional_semantics["release_lag_days"] = release_lag_days
    return shared_store.upsert_source_settings(
        get_session_factory(),
        instrument_id=instrument_id,
        source_mode=source_mode,
        source_email=source_email,
        source_location=source_location,
        source_api_profile=source_api_profile,
        source_email_rules=source_email_rules,
        **optional_semantics,
    )


def upsert_quote_selection_policy(
    *,
    instrument_id: str,
    quote_selection_policy: dict[str, object],
) -> dict[str, object] | None:
    return shared_store.upsert_quote_selection_policy(
        get_session_factory(),
        instrument_id=instrument_id,
        quote_selection_policy=quote_selection_policy,
    )


def replace_nav_history(
    *,
    instrument_id: str,
    rows: list[dict[str, object]],
    provider: str | None,
    point_status: str,
    refresh_status: str,
    updated_by: str | None,
    message: str,
    mode: str | None = None,
) -> dict[str, object] | None:
    return shared_store.replace_nav_history(
        get_session_factory(),
        instrument_id=instrument_id,
        rows=rows,
        provider=provider,
        point_status=point_status,
        refresh_status=refresh_status,
        updated_by=updated_by,
        message=message,
        mode=mode,
    )


def update_refresh_status(
    *,
    instrument_id: str,
    status: str,
    message: str,
    updated_by: str | None,
    mode: str | None = None,
) -> dict[str, object] | None:
    return shared_store.update_refresh_status(
        get_session_factory(),
        instrument_id=instrument_id,
        status=status,
        message=message,
        updated_by=updated_by,
        mode=mode,
    )


def archive_instrument(
    *,
    instrument_id: str,
    updated_by: str | None,
) -> dict[str, object] | None:
    return shared_store.archive_instrument(
        get_session_factory(),
        instrument_id=instrument_id,
        updated_by=updated_by,
    )


def restore_instrument(
    *,
    instrument_id: str,
    updated_by: str | None,
) -> dict[str, object] | None:
    return shared_store.restore_instrument(
        get_session_factory(),
        instrument_id=instrument_id,
        updated_by=updated_by,
    )


def instrument_registry_name() -> str:
    return shared_store.instrument_registry_name(get_session_factory())
