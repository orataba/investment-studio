from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from portfolio_ops_instrument_core import instrument_store as shared_store

from platform_app.db.session import get_session_factory


DEFAULT_REGISTRY_NAME = shared_store.DEFAULT_REGISTRY_NAME
EMPTY_STORE = shared_store.EMPTY_STORE
_normalize_store = shared_store._normalize_store
StaleFundNavPublicationError = shared_store.StaleFundNavPublicationError
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


def list_instrument_ids_with_nav_history_before(
    *,
    instrument_ids: Iterable[str],
    before_date: date,
) -> set[str]:
    return shared_store.list_instrument_ids_with_nav_history_before(
        get_session_factory(),
        instrument_ids=instrument_ids,
        before_date=before_date,
    )


def list_stale_current_fund_nav_projections(
    *,
    method_version: str,
    include_inactive: bool = False,
    instrument_ids: Iterable[str] | None = None,
) -> list[dict[str, str]]:
    return shared_store.list_stale_current_fund_nav_projections(
        get_session_factory(),
        method_version=method_version,
        include_inactive=include_inactive,
        instrument_ids=instrument_ids,
    )


def get_instrument(instrument_id: str) -> dict[str, object] | None:
    return shared_store.get_instrument(get_session_factory(), instrument_id)


def get_instrument_summaries(
    instrument_ids: list[str] | set[str] | tuple[str, ...],
) -> dict[str, dict[str, object] | None]:
    return shared_store.get_instrument_summaries(
        get_session_factory(),
        instrument_ids,
    )


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


def find_instrument_by_broker_identifier(
    *,
    broker: str,
    identifier_type: str,
    identifier_value: str,
    include_inactive: bool = False,
) -> dict[str, object] | None:
    return shared_store.find_instrument_by_broker_identifier(
        get_session_factory(),
        broker=broker,
        identifier_type=identifier_type,
        identifier_value=identifier_value,
        include_inactive=include_inactive,
    )


def create_instrument(
    *,
    instrument_name: str,
    instrument_type: str,
    currency: str,
    identifiers: list[dict[str, object]],
    quote_selection_policy: dict[str, object] | None = None,
    broker_identifiers: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return shared_store.create_instrument(
        get_session_factory(),
        instrument_name=instrument_name,
        instrument_type=instrument_type,
        currency=currency,
        identifiers=identifiers,
        quote_selection_policy=quote_selection_policy,
        broker_identifiers=broker_identifiers,
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
    nav_lineage: dict[str, object] | None = None,
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
        nav_lineage=nav_lineage,
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


def upsert_price_bars(
    *,
    instrument_id: str,
    rows: list[dict[str, object]],
) -> int | None:
    return shared_store.upsert_price_bars(
        get_session_factory(),
        instrument_id=instrument_id,
        rows=rows,
    )


def get_price_bars(
    *,
    instrument_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int | None = None,
) -> list[dict[str, object]]:
    return shared_store.get_price_bars(
        get_session_factory(),
        instrument_id=instrument_id,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )


def get_price_bar_coverage(*, instrument_id: str) -> dict[str, object]:
    return shared_store.get_price_bar_coverage(
        get_session_factory(),
        instrument_id=instrument_id,
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
    return_semantics: str | None = None,
) -> dict[str, object] | None:
    optional_semantics: dict[str, object] = {}
    if expected_frequency is not None:
        optional_semantics["expected_frequency"] = expected_frequency
    if market_calendar is not _SOURCE_SETTING_UNSET:
        optional_semantics["market_calendar"] = market_calendar
    if release_lag_days is not None:
        optional_semantics["release_lag_days"] = release_lag_days
    if return_semantics is not None:
        optional_semantics["return_semantics"] = return_semantics
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


def publish_fund_nav_history(
    *,
    instrument_id: str,
    rows: list[dict[str, object]],
    projection_run: dict[str, object],
    current_fund_nav_event_ids: Iterable[str],
    current_fund_nav_reinvestment_evidence_ids: Iterable[str],
    event_revisions: Iterable[dict[str, object]] = (),
    reinvestment_evidence_revisions: Iterable[dict[str, object]] = (),
    adjustment_factors: Iterable[dict[str, object]] = (),
    expected_market_data_updated_at: str | None,
    refresh_status: str,
    updated_by: str | None,
    message: str,
    mode: str | None = None,
) -> dict[str, object] | None:
    return shared_store.publish_fund_nav_history(
        get_session_factory(),
        instrument_id=instrument_id,
        rows=rows,
        projection_run=projection_run,
        current_fund_nav_event_ids=current_fund_nav_event_ids,
        current_fund_nav_reinvestment_evidence_ids=(
            current_fund_nav_reinvestment_evidence_ids
        ),
        event_revisions=event_revisions,
        reinvestment_evidence_revisions=reinvestment_evidence_revisions,
        adjustment_factors=adjustment_factors,
        expected_market_data_updated_at=expected_market_data_updated_at,
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
