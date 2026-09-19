from __future__ import annotations

from collections.abc import Callable, Hashable
from copy import deepcopy
from datetime import date
import json
from typing import TypeVar

from portfolio_app.services.source_cache import get_source_value

from portfolio_app.db.models import PortfolioCalculationStateModel, PortfolioRecordModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.daily_snapshots import (
    _state_requires_refresh,
    build_materialized_contribution_report,
    build_materialized_holdings_workspace,
    build_materialized_performance_report,
)


T = TypeVar("T")

def _date_key(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _snapshot_fingerprint(portfolio_id: str) -> tuple[str | None, ...] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        state = session.get(PortfolioCalculationStateModel, portfolio_id)
        if state is None or state.daily_snapshot_status != "current":
            return None
        if _state_requires_refresh(session, portfolio_id):
            return None
        portfolio = session.get(PortfolioRecordModel, portfolio_id)
        if portfolio is None:
            return None
        return (
            portfolio.portfolio_name,
            state.refresh_request_id,
            state.refreshed_at,
            _date_key(state.refreshed_from),
            _date_key(state.refreshed_to),
        )


def _cache_key(
    portfolio_id: str,
    surface: str,
    args: tuple[Hashable, ...],
    fingerprint: tuple[str | None, ...],
) -> tuple[Hashable, ...]:
    return (get_session_factory(), portfolio_id, surface, *args, *fingerprint)


def _get_cached_portfolio_value(
    portfolio_id: str,
    *,
    surface: str,
    args: tuple[Hashable, ...] = (),
    builder: Callable[[], T],
) -> T:
    def source_key():
        fingerprint = _snapshot_fingerprint(portfolio_id)
        if fingerprint is None:
            return None
        return _cache_key(portfolio_id, surface, args, fingerprint)

    return get_source_value(source_key=source_key, builder=builder)


def get_cached_holdings_analytics_workspace(
    portfolio_id: str,
    *,
    as_of_date: date | None,
    risk_policy: dict[str, object],
    analytics_policy_version: int,
    builder: Callable[[], T],
    response_projection: Callable[[T], T] | None = None,
) -> T:
    value = _get_cached_portfolio_value(
        portfolio_id,
        surface="holdings_analytics",
        args=(
            _date_key(as_of_date),
            json.dumps(risk_policy, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
            analytics_policy_version,
        ),
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


def preload_portfolio_workspace_cache(portfolio_id: str) -> dict[str, object]:
    warmed: list[str] = []
    errors: list[str] = []

    def warm(label: str, callback: Callable[[], object]) -> None:
        try:
            callback()
            warmed.append(label)
        except Exception as exc:  # pragma: no cover - background warmup must not fail foreground requests.
            errors.append(f"{label}: {exc}")

    warm("performance", lambda: get_cached_materialized_performance_report(portfolio_id))
    return {
        "portfolio_id": portfolio_id,
        "warmed_surfaces": warmed,
        "errors": errors,
    }
