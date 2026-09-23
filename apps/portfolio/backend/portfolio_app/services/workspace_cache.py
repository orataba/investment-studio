from __future__ import annotations

from collections.abc import Callable, Hashable
from copy import deepcopy
from datetime import date
import json
from typing import TypeVar

from portfolio_app.services.source_cache import get_source_value
from portfolio_app.services.workspace_read_models import (
    PUBLISHED_SURFACES, WORKSPACE_ANALYSIS_VERSION, read_workspace_projection,
)

from portfolio_app.db.models import PortfolioCalculationStateModel, PortfolioRecordModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.daily_snapshots import (
    PortfolioCalculationUnavailable,
    _financial_read_generation_in_session,
    _state_requires_refresh,
    build_materialized_contribution_report,
    build_materialized_holdings_workspace,
    build_materialized_performance_report,
)


T = TypeVar("T")

def _date_key(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def snapshot_projection_identity(state, portfolio) -> tuple:
    return (
        portfolio.portfolio_name, state.refresh_request_id, state.refreshed_at,
        _date_key(state.refreshed_from), _date_key(state.refreshed_to),
    )


def _snapshot_fingerprint_in_session(session, portfolio_id: str) -> tuple | None:
    state = session.get(PortfolioCalculationStateModel, portfolio_id)
    if state is None or state.daily_snapshot_status not in {"current", "failed"}:
        return None
    if state.daily_snapshot_status == "failed":
        try:
            if _financial_read_generation_in_session(session, portfolio_id) is None:
                return None
        except PortfolioCalculationUnavailable:
            return None
    elif _state_requires_refresh(session, portfolio_id):
        return None
    portfolio = session.get(PortfolioRecordModel, portfolio_id)
    return snapshot_projection_identity(state, portfolio) if portfolio is not None else None


def _snapshot_fingerprint(portfolio_id: str) -> tuple | None:
    with get_session_factory()() as session:
        return _snapshot_fingerprint_in_session(session, portfolio_id)


def _cache_key(
    portfolio_id: str,
    surface: str,
    args: tuple[Hashable, ...],
    fingerprint: tuple[str | None, ...],
) -> tuple[Hashable, ...]:
    return (get_session_factory(), WORKSPACE_ANALYSIS_VERSION, portfolio_id, surface, *args, *fingerprint)


def _get_cached_portfolio_value(
    portfolio_id: str,
    *,
    surface: str,
    args: tuple[Hashable, ...] = (),
    builder: Callable[[], T],
) -> T:
    projection_key = None

    def source_key():
        nonlocal projection_key
        fingerprint = _snapshot_fingerprint(portfolio_id)
        if fingerprint is None:
            projection_key = None
            return None
        projection_key = (surface, *args, *fingerprint)
        return _cache_key(portfolio_id, surface, args, fingerprint)

    def read_or_build():
        if surface in PUBLISHED_SURFACES:
            if projection_key is not None:
                published = read_workspace_projection(portfolio_id, surface, projection_key)
                if published is not None:
                    return published
        return builder()

    return get_source_value(source_key=source_key, builder=read_or_build)


def holdings_analysis_args(
    as_of_date: date | None, risk_policy: dict[str, object], configuration_version: int,
) -> tuple:
    return (
        _date_key(as_of_date),
        json.dumps(risk_policy, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
        configuration_version,
    )


def get_cached_holdings_analytics_workspace(
    portfolio_id: str,
    *,
    as_of_date: date | None,
    risk_policy: dict[str, object],
    taxonomy_configuration_version: int,
    builder: Callable[[], T],
    response_projection: Callable[[T], T] | None = None,
) -> T:
    value = _get_cached_portfolio_value(
        portfolio_id,
        surface="holdings_analytics",
        args=holdings_analysis_args(as_of_date, risk_policy, taxonomy_configuration_version),
        builder=builder,
    )
    # A projection must leave the cached value untouched. Copy only its retained
    # fields before the caller adds live quality/task information to the result.
    return deepcopy(response_projection(value) if response_projection else value)


def get_cached_portfolio_risk_basis(
    portfolio_id: str,
    *,
    as_of_date: date | None,
    builder: Callable[[], T],
) -> T:
    return deepcopy(
        _get_cached_portfolio_value(
            portfolio_id,
            surface="portfolio_risk_basis",
            args=(_date_key(as_of_date),),
            builder=builder,
        )
    )


def get_cached_research_analysis(
    portfolio_id: str,
    *,
    planning_state_fingerprint: str | None,
    as_of_date: date | None,
    lookback_days: int,
    comparator_taxonomy_node_id: str | None,
    builder: Callable[[], T],
) -> T:
    if planning_state_fingerprint is None:
        return builder()
    return deepcopy(
        _get_cached_portfolio_value(
            portfolio_id,
            surface="research_analysis",
            args=(
                planning_state_fingerprint,
                _date_key(as_of_date),
                lookback_days,
                comparator_taxonomy_node_id,
            ),
            builder=builder,
        )
    )


def get_cached_materialized_holdings_workspace(
    portfolio_id: str,
    *,
    as_of_date: date | None = None,
) -> dict[str, object] | None:
    return _get_cached_portfolio_value(
        portfolio_id,
        surface="holdings",
        args=(_date_key(as_of_date),),
        builder=lambda: build_materialized_holdings_workspace(portfolio_id, as_of_date=as_of_date),
    )


def get_cached_materialized_performance_report(
    portfolio_id: str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, object] | None:
    return _get_cached_portfolio_value(
        portfolio_id,
        surface="performance",
        args=(_date_key(start_date), _date_key(end_date)),
        builder=lambda: build_materialized_performance_report(
            portfolio_id,
            start_date=start_date,
            end_date=end_date,
        ),
    )


def get_cached_materialized_contribution_report(
    portfolio_id: str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    group_key: str | None = None,
) -> dict[str, object] | None:
    # Contribution reports are inexpensive materialized reads but can be very
    # large. Caching every axis/group multiplied resident memory without
    # improving the underlying query path.
    return build_materialized_contribution_report(
        portfolio_id,
        start_date=start_date,
        end_date=end_date,
        axis=axis,
        group_key=group_key,
    )
