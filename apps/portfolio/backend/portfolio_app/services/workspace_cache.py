from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from datetime import date
import json
from threading import RLock
from time import monotonic
from typing import TypeVar

from portfolio_app.db.models import PortfolioCalculationStateModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.daily_snapshots import (
    build_materialized_contribution_report,
    build_materialized_holdings_workspace,
    build_materialized_performance_report,
)


T = TypeVar("T")

WORKSPACE_CACHE_TTL_SECONDS = 60.0
WORKSPACE_CACHE_MAX_ENTRIES = 64
WORKSPACE_CACHE_MAX_VALUE_BYTES = 2 * 1024 * 1024
WORKSPACE_CACHE_MAX_TOTAL_BYTES = 32 * 1024 * 1024


@dataclass
class _CacheEntry:
    expires_at: float
    value: object
    approx_size_bytes: int


_cache: OrderedDict[tuple[Hashable, ...], _CacheEntry] = OrderedDict()
_cache_lock = RLock()
_cache_total_size_bytes = 0


def _date_key(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _snapshot_fingerprint(portfolio_id: str) -> tuple[str | None, str | None, str | None, str | None] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        state = session.get(PortfolioCalculationStateModel, portfolio_id)
        if state is None or state.daily_snapshot_status != "current":
            return None
        return (
            state.refresh_request_id,
            state.refreshed_at,
            _date_key(state.refreshed_from),
            _date_key(state.refreshed_to),
        )


def _read_cache(cache_key: tuple[Hashable, ...]) -> object | None:
    global _cache_total_size_bytes
    now = monotonic()
    with _cache_lock:
        entry = _cache.get(cache_key)
        if entry is None:
            return None
        if entry.expires_at <= now:
            _cache.pop(cache_key, None)
            _cache_total_size_bytes -= entry.approx_size_bytes
            return None
        _cache.move_to_end(cache_key)
        return entry.value


def _write_cache(cache_key: tuple[Hashable, ...], value: object) -> None:
    global _cache_total_size_bytes
    approx_size_bytes = len(
        json.dumps(
            value,
            default=str,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    if approx_size_bytes > WORKSPACE_CACHE_MAX_VALUE_BYTES:
        return
    expires_at = monotonic() + WORKSPACE_CACHE_TTL_SECONDS
    with _cache_lock:
        previous = _cache.pop(cache_key, None)
        if previous is not None:
            _cache_total_size_bytes -= previous.approx_size_bytes
        _cache[cache_key] = _CacheEntry(
            expires_at=expires_at,
            value=value,
            approx_size_bytes=approx_size_bytes,
        )
        _cache_total_size_bytes += approx_size_bytes
        _cache.move_to_end(cache_key)
        while (
            len(_cache) > WORKSPACE_CACHE_MAX_ENTRIES
            or _cache_total_size_bytes > WORKSPACE_CACHE_MAX_TOTAL_BYTES
        ):
            _, evicted = _cache.popitem(last=False)
            _cache_total_size_bytes -= evicted.approx_size_bytes


def _cache_key(
    portfolio_id: str,
    surface: str,
    args: tuple[Hashable, ...],
    fingerprint: tuple[str | None, str | None, str | None, str | None],
) -> tuple[Hashable, ...]:
    return (portfolio_id, surface, *args, *fingerprint)


def _get_cached_portfolio_value(
    portfolio_id: str,
    *,
    surface: str,
    args: tuple[Hashable, ...] = (),
    builder: Callable[[], T],
) -> T:
    fingerprint = _snapshot_fingerprint(portfolio_id)
    if fingerprint is not None:
        cached_value = _read_cache(_cache_key(portfolio_id, surface, args, fingerprint))
        if cached_value is not None:
            return cached_value  # type: ignore[return-value]

    value = builder()
    refreshed_fingerprint = _snapshot_fingerprint(portfolio_id)
    if refreshed_fingerprint is not None:
        _write_cache(_cache_key(portfolio_id, surface, args, refreshed_fingerprint), value)
    return value


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
