from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from math import sqrt
from typing import Iterable, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from portfolio_ops_instrument_core import (
    CANONICAL_QUOTE_RESOLVER_STRATEGY_VERSION,
    QUOTE_SELECTION_POLICY_VERSION,
    CanonicalQuoteSeriesResolution,
    resolve_role_quote_series_in_session,
)
from portfolio_ops_instrument_core.db_models import Instrument

from portfolio_app.services.calculation_frequency import CalculationFrequency, period_end_date
from portfolio_app.services.canonical_quotes import valuation_freshness_profile
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.instrument_registry import InstrumentRegistryError

SUPPORTED_CHART_RANGE_KEYS: tuple[str, ...] = ("1m", "3m", "6m", "ytd", "1y", "all")
HOLDINGS_PRICE_CHART_RANGE_KEYS: tuple[str, ...] = ("1m", "3m", "6m", "1y")
ASSET_RISK_WINDOW_DAYS: dict[str, int] = {
    "1m": 31,
    "3m": 92,
    "6m": 183,
    "1y": 366,
}
ASSET_RISK_MIN_RETURN_OBSERVATIONS: dict[CalculationFrequency, dict[str, int]] = {
    "daily": {
        "1m": 10,
        "3m": 30,
        "6m": 60,
        "1y": 120,
    },
    "weekly": {
        "1m": 3,
        "3m": 6,
        "6m": 12,
        "1y": 24,
    },
    "monthly": {
        "1m": 2,
        "3m": 2,
        "6m": 4,
        "1y": 6,
    },
}
ASSET_RISK_MAX_START_GAP_DAYS: dict[CalculationFrequency, int] = {
    "daily": 10,
    "weekly": 21,
    "monthly": 45,
}
ASSET_RISK_MIN_WINDOW_COVERAGE_RATIO = 0.8
DAYS_PER_YEAR = 365.25
CanonicalMarketDataRole = Literal["chart", "total_return"]
_BLOCKING_SERIES_REASON_CODES = frozenset(
    {
        "partial_series",
        "rejected_observation",
        "withdrawn_observation",
        "late_observation",
        "freshness_limit_exceeded",
    }
)


@dataclass(frozen=True)
class CanonicalInstrumentMarketData:
    """One-session, role-locked market-data input for chart and risk consumers."""

    as_of_date: date
    instrument_core_by_id: dict[str, dict[str, object]]
    windows_by_role: dict[
        CanonicalMarketDataRole,
        dict[str, CanonicalQuoteSeriesResolution],
    ]
    requested_instrument_ids: tuple[str, ...]
    missing_instrument_ids: frozenset[str]


def _normalized_instrument_ids(instrument_ids: Iterable[str]) -> list[str]:
    return sorted(
        {
            normalized
            for raw_instrument_id in instrument_ids
            if (normalized := str(raw_instrument_id or "").strip())
        }
    )


def _normalized_roles(
    roles: Iterable[CanonicalMarketDataRole],
) -> tuple[CanonicalMarketDataRole, ...]:
    normalized: list[CanonicalMarketDataRole] = []
    for role in roles:
        if role not in {"chart", "total_return"}:
            raise ValueError("Canonical instrument market data supports chart and total_return roles only.")
        if role not in normalized:
            normalized.append(role)
    if not normalized:
        raise ValueError("At least one canonical market-data role is required.")
    return tuple(normalized)


def _instrument_core(instrument: Instrument) -> dict[str, object]:
    return {
        "instrument_id": str(instrument.instrument_id),
        "instrument_name": str(instrument.instrument_name),
        "instrument_type": str(instrument.instrument_type),
        "currency": str(instrument.currency).strip().upper(),
        "identifiers": [
            {
                "identifier_type": str(identifier.identifier_type),
                "identifier_value": str(identifier.identifier_value),
                "is_primary": bool(identifier.is_primary),
            }
            for identifier in sorted(
                instrument.identifiers,
                key=lambda item: (
                    not bool(item.is_primary),
                    str(item.identifier_type),
                    str(item.identifier_value),
                ),
            )
        ],
    }


def lock_instrument_market_data_in_session(
    session: Session,
    *,
    instrument_ids: Iterable[str],
    as_of_date: date,
    roles: Iterable[CanonicalMarketDataRole] = ("chart", "total_return"),
) -> CanonicalInstrumentMarketData:
    """Lock canonical series without provider, flat-detail, or cross-role fallback.

    Each role is resolved since inception so all chart and drawdown windows come
    from a single selected series. Query growth depends on the instrument/role
    set, never on the number of calendar days in the requested history.
    """

    normalized_ids = _normalized_instrument_ids(instrument_ids)
    normalized_roles = _normalized_roles(roles)
    instruments = session.scalars(
        select(Instrument)
        .options(selectinload(Instrument.identifiers))
        .where(Instrument.instrument_id.in_(normalized_ids))
        .order_by(Instrument.instrument_id)
    ).all() if normalized_ids else []
    instruments_by_id = {
        str(instrument.instrument_id): instrument
        for instrument in instruments
    }
    instrument_core_by_id = {
        instrument_id: _instrument_core(instrument)
        for instrument_id, instrument in instruments_by_id.items()
    }
    windows_by_role: dict[
        CanonicalMarketDataRole,
        dict[str, CanonicalQuoteSeriesResolution],
    ] = {}
    for role in normalized_roles:
        role_windows: dict[str, CanonicalQuoteSeriesResolution] = {}
        for instrument_id in normalized_ids:
            instrument = instruments_by_id.get(instrument_id)
            if instrument is None:
                continue
            freshness_policy = valuation_freshness_profile(
                str(instrument.instrument_type)
            ).resolver_policy
            role_windows[instrument_id] = resolve_role_quote_series_in_session(
                session,
                resolver_strategy_version=CANONICAL_QUOTE_RESOLVER_STRATEGY_VERSION,
                quote_selection_policy_version=QUOTE_SELECTION_POLICY_VERSION,
                instrument_id=instrument_id,
                role=role,
                currency=str(instrument.currency).strip().upper(),
                range_mode="since_inception",
                start_date=None,
                end_date=as_of_date,
                freshness_policy=freshness_policy,
            )
        windows_by_role[role] = role_windows
    return CanonicalInstrumentMarketData(
        as_of_date=as_of_date,
        instrument_core_by_id=instrument_core_by_id,
        windows_by_role=windows_by_role,
        requested_instrument_ids=tuple(normalized_ids),
        missing_instrument_ids=frozenset(
            set(normalized_ids).difference(instruments_by_id)
        ),
    )


def lock_instrument_market_data(
    instrument_ids: Iterable[str],
    *,
    as_of_date: date,
    roles: Iterable[CanonicalMarketDataRole] = ("chart", "total_return"),
) -> CanonicalInstrumentMarketData:
    try:
        with get_session_factory()() as session:
            return lock_instrument_market_data_in_session(
                session,
                instrument_ids=instrument_ids,
                as_of_date=as_of_date,
                roles=roles,
            )
    except InstrumentRegistryError:
        raise
    except Exception as error:
        raise InstrumentRegistryError(
            "Failed to lock canonical instrument market data."
        ) from error


def canonical_series_points(
    market_data: CanonicalInstrumentMarketData,
    *,
    instrument_id: str,
    role: CanonicalMarketDataRole,
) -> list[dict[str, object]]:
    """Return adopted points only when the locked role window is fully usable."""

    window = market_data.windows_by_role.get(role, {}).get(
        str(instrument_id or "").strip()
    )
    if (
        window is None
        or window.role != role
        or window.end_date != market_data.as_of_date
        or window.resolution_status != "resolved"
        or window.coverage_status != "complete"
        or window.freshness_status != "current"
        or _BLOCKING_SERIES_REASON_CODES.intersection(window.reason_codes)
    ):
        return []
    return [
        {
            "date": point.observation_date,
            "date_iso": point.observation_date.isoformat(),
            "value": float(point.value),
            "currency": window.currency,
            "metric_family": window.metric_family,
            "quote_basis": window.quote_basis,
        }
        for point in window.points
        if point.observation_date <= market_data.as_of_date
    ]


def canonical_series_failure_reasons(
    market_data: CanonicalInstrumentMarketData,
    *,
    instrument_id: str,
    role: CanonicalMarketDataRole,
) -> list[str]:
    normalized_id = str(instrument_id or "").strip()
    if normalized_id in market_data.missing_instrument_ids:
        return ["instrument_not_found"]
    window = market_data.windows_by_role.get(role, {}).get(normalized_id)
    if window is None:
        return ["role_not_locked"]
    if canonical_series_points(
        market_data,
        instrument_id=normalized_id,
        role=role,
    ):
        return []
    reasons = list(window.reason_codes)
    if window.coverage_status != "complete" and "incomplete_coverage" not in reasons:
        reasons.append("incomplete_coverage")
    if window.freshness_status != "current" and "non_current_endpoint" not in reasons:
        reasons.append("non_current_endpoint")
    return reasons or ["insufficient_history"]


def empty_instrument_trend_metrics(
    *,
    selected_basis: str | None = None,
    holding_start_date: date | None = None,
    calculation_frequency: CalculationFrequency = "daily",
) -> dict[str, object]:
    return {
        "instrument_trend_as_of_date": None,
        "instrument_trend_basis": selected_basis,
        "instrument_risk_frequency": calculation_frequency,
        "instrument_return_1w": None,
        "instrument_return_mtd": None,
        "instrument_return_ytd": None,
        "instrument_return_1y": None,
        "instrument_volatility_1m": None,
        "instrument_volatility_3m": None,
        "instrument_volatility_6m": None,
        "instrument_volatility_1y": None,
        "instrument_return_series_1m": {"first_return_start_date": None, "points": []},
        "instrument_return_series_3m": {"first_return_start_date": None, "points": []},
        "instrument_return_series_6m": {"first_return_start_date": None, "points": []},
        "instrument_return_series_1y": {"first_return_start_date": None, "points": []},
        "instrument_return_series_all": {"first_return_start_date": None, "points": []},
        "instrument_holding_return_series": {"first_return_start_date": None, "points": []},
        "instrument_current_drawdown": None,
        "instrument_max_drawdown": None,
        "instrument_holding_max_drawdown": None,
        "instrument_holding_start_date": holding_start_date.isoformat() if holding_start_date else None,
    }


def empty_instrument_holdings_market_profile(
    *,
    holding_start_date: date | None = None,
    calculation_frequency: CalculationFrequency = "daily",
) -> dict[str, object]:
    return {
        "price_chart_1m": [],
        "price_chart_3m": [],
        "price_chart_6m": [],
        "price_chart_1y": [],
        **empty_instrument_trend_metrics(
            holding_start_date=holding_start_date,
            calculation_frequency=calculation_frequency,
        ),
    }


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_chart_range_key(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in SUPPORTED_CHART_RANGE_KEYS else "6m"


def _range_start_date(*, as_of_date: date, range_key: str) -> date | None:
    if range_key == "1m":
        return as_of_date - timedelta(days=31)
    if range_key == "3m":
        return as_of_date - timedelta(days=92)
    if range_key == "6m":
        return as_of_date - timedelta(days=183)
    if range_key == "1y":
        return as_of_date - timedelta(days=366)
    if range_key == "ytd":
        return date(as_of_date.year, 1, 1)
    return None


def _downsample_points(points: list[dict[str, object]], max_points: int | None) -> list[dict[str, object]]:
    if max_points is None or max_points <= 0 or len(points) <= max_points:
        return points
    if max_points == 1:
        return [points[-1]]

    sampled_indices: set[int] = {0, len(points) - 1}
    for sample_index in range(1, max_points - 1):
        raw_index = round(sample_index * (len(points) - 1) / (max_points - 1))
        sampled_indices.add(raw_index)
    return [points[index] for index in sorted(sampled_indices)]


def _latest_point_on_or_before(
    points: list[dict[str, object]],
    target_date: date,
) -> dict[str, object] | None:
    latest: dict[str, object] | None = None
    for point in points:
        point_date = point.get("date")
        if isinstance(point_date, date) and point_date <= target_date:
            latest = point
    return latest


def _first_point_on_or_after(
    points: list[dict[str, object]],
    target_date: date,
) -> dict[str, object] | None:
    for point in points:
        point_date = point.get("date")
        if isinstance(point_date, date) and point_date >= target_date:
            return point
    return None


def _return_between_points(
    start_point: dict[str, object] | None,
    end_point: dict[str, object] | None,
) -> float | None:
    if start_point is None or end_point is None:
        return None
    start_date = start_point.get("date")
    end_date = end_point.get("date")
    if isinstance(start_date, date) and isinstance(end_date, date) and start_date >= end_date:
        return None
    start_value = _safe_float(start_point.get("value"))
    end_value = _safe_float(end_point.get("value"))
    if start_value is None or end_value is None or abs(start_value) <= 1e-12:
        return None
    return end_value / start_value - 1


def _window_points(
    points: list[dict[str, object]],
    *,
    as_of_date: date,
    days: int,
    max_anchor_gap_days: int | None = None,
) -> list[dict[str, object]]:
    start_date = as_of_date - timedelta(days=days)
    anchor_point = _latest_point_on_or_before(points, start_date)
    visible_points = [
        point
        for point in points
        if isinstance(point.get("date"), date) and start_date < point["date"] <= as_of_date
    ]
    if anchor_point is not None:
        anchor_date = anchor_point.get("date")
        if (
            max_anchor_gap_days is None
            or not isinstance(anchor_date, date)
            or (start_date - anchor_date).days <= max_anchor_gap_days
        ):
            visible_points = [anchor_point, *visible_points]
    return visible_points


def _points_since(
    points: list[dict[str, object]],
    *,
    start_date: date | None,
) -> list[dict[str, object]]:
    if start_date is None:
        return points
    anchor_point = _latest_point_on_or_before(points, start_date) or _first_point_on_or_after(points, start_date)
    if anchor_point is None:
        return []
    anchor_date = anchor_point.get("date")
    if not isinstance(anchor_date, date):
        return []
    return [point for point in points if isinstance(point.get("date"), date) and point["date"] >= anchor_date]


def _sample_stddev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((value - mean) * (value - mean) for value in values) / (len(values) - 1)
    return sqrt(max(variance, 0.0))


def _period_return_observations(points: list[dict[str, object]]) -> tuple[list[float], date | None, date | None]:
    returns: list[float] = []
    first_return_start_date: date | None = None
    last_return_end_date: date | None = None
    previous_date: date | None = None
    previous_value: float | None = None
    for point in points:
        point_date = point.get("date")
        value = _safe_float(point.get("value"))
        if value is None or not isinstance(point_date, date):
            continue
        if previous_value is not None and previous_value > 1e-12 and previous_date is not None and previous_date < point_date:
            returns.append(value / previous_value - 1)
            if first_return_start_date is None:
                first_return_start_date = previous_date
            last_return_end_date = point_date
        previous_date = point_date
        previous_value = value
    return returns, first_return_start_date, last_return_end_date


def _period_return_series(
    points: list[dict[str, object]],
) -> tuple[list[dict[str, object]], date | None, date | None]:
    returns: list[dict[str, object]] = []
    first_return_start_date: date | None = None
    last_return_end_date: date | None = None
    previous_date: date | None = None
    previous_value: float | None = None
    for point in points:
        point_date = point.get("date")
        value = _safe_float(point.get("value"))
        if value is None or not isinstance(point_date, date):
            continue
        if previous_value is not None and previous_value > 1e-12 and previous_date is not None and previous_date < point_date:
            returns.append(
                {
                    "start_date": previous_date.isoformat(),
                    "date": point_date.isoformat(),
                    "value": value / previous_value - 1,
                }
            )
            if first_return_start_date is None:
                first_return_start_date = previous_date
            last_return_end_date = point_date
        previous_date = point_date
        previous_value = value
    return returns, first_return_start_date, last_return_end_date


def _points_for_calculation_frequency(
    points: list[dict[str, object]],
    *,
    calculation_frequency: CalculationFrequency,
    final_date: date | None,
) -> list[dict[str, object]]:
    if calculation_frequency == "daily":
        return points

    sampled_by_period: dict[date, dict[str, object]] = {}
    for point in points:
        point_date = point.get("date")
        if not isinstance(point_date, date):
            continue
        target_date = period_end_date(point_date, calculation_frequency, final_date=final_date)
        current_point = sampled_by_period.get(target_date)
        current_date = current_point.get("date") if isinstance(current_point, dict) else None
        if not isinstance(current_date, date) or point_date >= current_date:
            sampled_by_period[target_date] = point
    return [sampled_by_period[target_date] for target_date in sorted(sampled_by_period)]


def _annualized_volatility(
    points: list[dict[str, object]],
    *,
    calculation_frequency: CalculationFrequency = "daily",
    final_date: date | None = None,
    min_return_observations: int = 2,
    required_start_date: date | None = None,
    max_start_gap_days: int | None = None,
    min_elapsed_days: int | None = None,
) -> float | None:
    sampled_points = _points_for_calculation_frequency(
        points,
        calculation_frequency=calculation_frequency,
        final_date=final_date,
    )
    returns, first_return_start_date, last_return_end_date = _period_return_observations(sampled_points)
    if len(returns) < min_return_observations:
        return None
    stddev = _sample_stddev(returns)
    if stddev is None or first_return_start_date is None or last_return_end_date is None:
        return None
    if required_start_date is not None and max_start_gap_days is not None:
        start_gap_days = abs((first_return_start_date - required_start_date).days)
        if start_gap_days > max_start_gap_days:
            return None
    elapsed_days = (last_return_end_date - first_return_start_date).days
    if elapsed_days <= 0:
        return None
    if min_elapsed_days is not None and elapsed_days < min_elapsed_days:
        return None
    periods_per_year = float(len(returns)) / float(elapsed_days) * DAYS_PER_YEAR
    return stddev * sqrt(periods_per_year)


def _annualized_window_volatility(
    points: list[dict[str, object]],
    *,
    range_key: str,
    as_of_date: date,
    calculation_frequency: CalculationFrequency = "daily",
) -> float | None:
    window_days = ASSET_RISK_WINDOW_DAYS[range_key]
    window_start_date = as_of_date - timedelta(days=window_days)
    max_start_gap_days = ASSET_RISK_MAX_START_GAP_DAYS[calculation_frequency]
    window_points = _window_points(
        points,
        as_of_date=as_of_date,
        days=window_days,
        max_anchor_gap_days=max_start_gap_days,
    )
    return _annualized_volatility(
        window_points,
        calculation_frequency=calculation_frequency,
        final_date=as_of_date,
        min_return_observations=ASSET_RISK_MIN_RETURN_OBSERVATIONS[calculation_frequency][range_key],
        required_start_date=window_start_date,
        max_start_gap_days=max_start_gap_days,
        min_elapsed_days=int(window_days * ASSET_RISK_MIN_WINDOW_COVERAGE_RATIO),
    )


def _empty_return_series_payload() -> dict[str, object]:
    return {"first_return_start_date": None, "points": []}


def _return_series_payload(
    points: list[dict[str, object]],
    *,
    calculation_frequency: CalculationFrequency = "daily",
    final_date: date | None = None,
    min_return_observations: int = 2,
) -> dict[str, object]:
    sampled_points = _points_for_calculation_frequency(
        points,
        calculation_frequency=calculation_frequency,
        final_date=final_date,
    )
    returns, first_return_start_date, last_return_end_date = _period_return_series(sampled_points)
    if len(returns) < min_return_observations or first_return_start_date is None or last_return_end_date is None:
        return _empty_return_series_payload()
    return {
        "first_return_start_date": first_return_start_date.isoformat(),
        "points": returns,
    }


def _window_return_series_payload(
    points: list[dict[str, object]],
    *,
    range_key: str,
    as_of_date: date,
    calculation_frequency: CalculationFrequency = "daily",
) -> dict[str, object]:
    window_days = ASSET_RISK_WINDOW_DAYS[range_key]
    window_start_date = as_of_date - timedelta(days=window_days)
    max_start_gap_days = ASSET_RISK_MAX_START_GAP_DAYS[calculation_frequency]
    window_points = _window_points(
        points,
        as_of_date=as_of_date,
        days=window_days,
        max_anchor_gap_days=max_start_gap_days,
    )
    sampled_points = _points_for_calculation_frequency(
        window_points,
        calculation_frequency=calculation_frequency,
        final_date=as_of_date,
    )
    returns, first_return_start_date, last_return_end_date = _period_return_series(sampled_points)
    if len(returns) < ASSET_RISK_MIN_RETURN_OBSERVATIONS[calculation_frequency][range_key]:
        return _empty_return_series_payload()
    if first_return_start_date is None or last_return_end_date is None:
        return _empty_return_series_payload()
    start_gap_days = abs((first_return_start_date - window_start_date).days)
    if start_gap_days > max_start_gap_days:
        return _empty_return_series_payload()
    elapsed_days = (last_return_end_date - first_return_start_date).days
    if elapsed_days <= 0 or elapsed_days < int(window_days * ASSET_RISK_MIN_WINDOW_COVERAGE_RATIO):
        return _empty_return_series_payload()
    return {
        "first_return_start_date": first_return_start_date.isoformat(),
        "points": returns,
    }


def _max_drawdown(points: list[dict[str, object]]) -> float | None:
    if len(points) < 2:
        return None
    peak: float | None = None
    max_drawdown = 0.0
    has_value = False
    for point in points:
        value = _safe_float(point.get("value"))
        if value is None:
            continue
        has_value = True
        peak = value if peak is None else max(peak, value)
        if peak > 1e-12:
            max_drawdown = min(max_drawdown, value / peak - 1)
    return max_drawdown if has_value else None


def _current_drawdown(points: list[dict[str, object]]) -> float | None:
    values = [_safe_float(point.get("value")) for point in points]
    valid_values = [value for value in values if value is not None]
    if not valid_values:
        return None
    end_value = valid_values[-1]
    peak_value = max(valid_values)
    return end_value / peak_value - 1 if peak_value > 1e-12 else None


def _period_return(
    points: list[dict[str, object]],
    *,
    end_point: dict[str, object] | None,
    anchor_date: date,
) -> float | None:
    start_point = _latest_point_on_or_before(points, anchor_date)
    return _return_between_points(start_point, end_point)


def build_instrument_trend_metrics_from_points(
    selected_points: list[dict[str, object]],
    *,
    as_of_date: date,
    selected_basis: str | None,
    holding_start_date: date | None = None,
    calculation_frequency: CalculationFrequency = "daily",
) -> dict[str, object]:
    if not selected_points:
        return empty_instrument_trend_metrics(
            selected_basis=selected_basis,
            holding_start_date=holding_start_date,
            calculation_frequency=calculation_frequency,
        )

    end_point = selected_points[-1]
    end_date = end_point.get("date") if isinstance(end_point.get("date"), date) else as_of_date
    month_start = date(end_date.year, end_date.month, 1)
    year_start = date(end_date.year, 1, 1)
    holding_points = _points_since(selected_points, start_date=holding_start_date) if holding_start_date else []

    return {
        "instrument_trend_as_of_date": end_date.isoformat(),
        "instrument_trend_basis": selected_basis,
        "instrument_risk_frequency": calculation_frequency,
        "instrument_return_1w": _period_return(
            selected_points,
            end_point=end_point,
            anchor_date=end_date - timedelta(days=7),
        ),
        "instrument_return_mtd": _period_return(
            selected_points,
            end_point=end_point,
            anchor_date=month_start - timedelta(days=1),
        ),
        "instrument_return_ytd": _period_return(
            selected_points,
            end_point=end_point,
            anchor_date=year_start - timedelta(days=1),
        ),
        "instrument_return_1y": _period_return(
            selected_points,
            end_point=end_point,
            anchor_date=end_date - timedelta(days=365),
        ),
        "instrument_volatility_1m": _annualized_window_volatility(
            selected_points,
            range_key="1m",
            as_of_date=end_date,
            calculation_frequency=calculation_frequency,
        ),
        "instrument_volatility_3m": _annualized_window_volatility(
            selected_points,
            range_key="3m",
            as_of_date=end_date,
            calculation_frequency=calculation_frequency,
        ),
        "instrument_volatility_6m": _annualized_window_volatility(
            selected_points,
            range_key="6m",
            as_of_date=end_date,
            calculation_frequency=calculation_frequency,
        ),
        "instrument_volatility_1y": _annualized_window_volatility(
            selected_points,
            range_key="1y",
            as_of_date=end_date,
            calculation_frequency=calculation_frequency,
        ),
        "instrument_return_series_1m": _window_return_series_payload(
            selected_points,
            range_key="1m",
            as_of_date=end_date,
            calculation_frequency=calculation_frequency,
        ),
        "instrument_return_series_3m": _window_return_series_payload(
            selected_points,
            range_key="3m",
            as_of_date=end_date,
            calculation_frequency=calculation_frequency,
        ),
        "instrument_return_series_6m": _window_return_series_payload(
            selected_points,
            range_key="6m",
            as_of_date=end_date,
            calculation_frequency=calculation_frequency,
        ),
        "instrument_return_series_1y": _window_return_series_payload(
            selected_points,
            range_key="1y",
            as_of_date=end_date,
            calculation_frequency=calculation_frequency,
        ),
        "instrument_return_series_all": _return_series_payload(
            selected_points,
            calculation_frequency=calculation_frequency,
            final_date=end_date,
        ),
        "instrument_holding_return_series": (
            _return_series_payload(
                holding_points,
                calculation_frequency=calculation_frequency,
                final_date=end_date,
            )
            if holding_start_date
            else _empty_return_series_payload()
        ),
        "instrument_current_drawdown": _current_drawdown(selected_points),
        "instrument_max_drawdown": _max_drawdown(selected_points),
        "instrument_holding_max_drawdown": _max_drawdown(holding_points) if holding_start_date else None,
        "instrument_holding_start_date": holding_start_date.isoformat() if holding_start_date else None,
    }


def build_instrument_trend_metrics_from_market_data(
    market_data: CanonicalInstrumentMarketData,
    *,
    instrument_id: str,
    holding_start_date: date | None = None,
    calculation_frequency: CalculationFrequency = "daily",
) -> dict[str, object]:
    window = market_data.windows_by_role.get("total_return", {}).get(
        str(instrument_id or "").strip()
    )
    selected_points = canonical_series_points(
        market_data,
        instrument_id=instrument_id,
        role="total_return",
    )
    return build_instrument_trend_metrics_from_points(
        selected_points,
        as_of_date=market_data.as_of_date,
        selected_basis=(window.quote_basis if window is not None else None),
        holding_start_date=holding_start_date,
        calculation_frequency=calculation_frequency,
    )


def build_instrument_trend_metrics(
    instrument_id: str,
    *,
    as_of_date: date,
    holding_start_date: date | None = None,
    calculation_frequency: CalculationFrequency = "daily",
) -> dict[str, object]:
    market_data = lock_instrument_market_data(
        [instrument_id],
        as_of_date=as_of_date,
        roles=("total_return",),
    )
    return build_instrument_trend_metrics_from_market_data(
        market_data,
        instrument_id=instrument_id,
        holding_start_date=holding_start_date,
        calculation_frequency=calculation_frequency,
    )


def build_instrument_price_chart_from_market_data(
    market_data: CanonicalInstrumentMarketData,
    *,
    instrument_id: str,
    range_key: str | None = None,
    max_points: int | None = None,
) -> dict[str, object] | None:
    instrument_core = market_data.instrument_core_by_id.get(
        str(instrument_id or "").strip()
    )
    if instrument_core is None:
        return None
    as_of_date = market_data.as_of_date
    normalized_range_key = normalize_chart_range_key(range_key)
    selected_points = canonical_series_points(
        market_data,
        instrument_id=instrument_id,
        role="chart",
    )
    window = market_data.windows_by_role.get("chart", {}).get(
        str(instrument_id or "").strip()
    )

    range_start_date = _range_start_date(as_of_date=as_of_date, range_key=normalized_range_key)
    visible_points = (
        [
            point
            for point in selected_points
            if range_start_date is None or point["date"] >= range_start_date
        ]
        if selected_points
        else []
    )
    if not visible_points and selected_points:
        visible_points = [selected_points[-1]]

    visible_points = _downsample_points(visible_points, max_points=max_points)
    point_values = [float(point["value"]) for point in visible_points]
    first_value = point_values[0] if point_values else None
    last_value = point_values[-1] if point_values else None
    change_value = (last_value - first_value) if first_value is not None and last_value is not None else None
    change_pct = (
        (change_value / first_value)
        if change_value is not None and first_value is not None and abs(first_value) > 1e-9
        else None
    )

    return {
        "instrument_core": instrument_core,
        "as_of_date": as_of_date.isoformat(),
        "range_key": normalized_range_key,
        "chart_basis": window.quote_basis if window is not None else None,
        "metric_family": (
            window.metric_family if window is not None else None
        ),
        "currency": str(instrument_core["currency"]),
        "points": [
            {
                "date": point["date_iso"],
                "value": float(point["value"]),
            }
            for point in visible_points
        ],
        "summary": {
            "point_count": len(visible_points),
            "change_value": change_value,
            "change_pct": change_pct,
            "high": max(point_values) if point_values else None,
            "low": min(point_values) if point_values else None,
        },
    }


def build_instrument_price_chart(
    instrument_id: str,
    *,
    as_of_date: date,
    range_key: str | None = None,
    max_points: int | None = None,
) -> dict[str, object] | None:
    market_data = lock_instrument_market_data(
        [instrument_id],
        as_of_date=as_of_date,
        roles=("chart",),
    )
    return build_instrument_price_chart_from_market_data(
        market_data,
        instrument_id=instrument_id,
        range_key=range_key,
        max_points=max_points,
    )


def build_instrument_sparkline_from_market_data(
    market_data: CanonicalInstrumentMarketData,
    *,
    instrument_id: str,
    range_key: str | None = "6m",
    max_points: int = 48,
) -> list[dict[str, object]]:
    chart = build_instrument_price_chart_from_market_data(
        market_data,
        instrument_id=instrument_id,
        range_key=range_key,
        max_points=max_points,
    )
    if not isinstance(chart, dict):
        return []
    points = chart.get("points", [])
    if not isinstance(points, list):
        return []
    return [
        {
            "date": str(point.get("date") or ""),
            "value": float(point.get("value")),
        }
        for point in points
        if isinstance(point, dict) and _safe_float(point.get("value")) is not None
    ]


def build_instrument_sparkline(
    instrument_id: str,
    *,
    as_of_date: date,
    range_key: str | None = "6m",
    max_points: int = 48,
) -> list[dict[str, object]]:
    market_data = lock_instrument_market_data(
        [instrument_id],
        as_of_date=as_of_date,
        roles=("chart",),
    )
    return build_instrument_sparkline_from_market_data(
        market_data,
        instrument_id=instrument_id,
        range_key=range_key,
        max_points=max_points,
    )


def _chart_points_payload(chart: dict[str, object] | None) -> list[dict[str, object]]:
    points = chart.get("points", []) if isinstance(chart, dict) else []
    if not isinstance(points, list):
        return []
    return [
        {
            "date": str(point.get("date") or ""),
            "value": float(point.get("value")),
        }
        for point in points
        if isinstance(point, dict) and _safe_float(point.get("value")) is not None
    ]


def build_instrument_holdings_market_profile_from_market_data(
    market_data: CanonicalInstrumentMarketData,
    *,
    instrument_id: str,
    holding_start_date: date | None = None,
    max_points: int = 48,
    calculation_frequency: CalculationFrequency = "daily",
) -> dict[str, object]:
    charts = {
        f"price_chart_{range_key}": _chart_points_payload(
            build_instrument_price_chart_from_market_data(
                market_data,
                instrument_id=instrument_id,
                range_key=range_key,
                max_points=max_points,
            )
        )
        for range_key in HOLDINGS_PRICE_CHART_RANGE_KEYS
    }
    return {
        **charts,
        **build_instrument_trend_metrics_from_market_data(
            market_data,
            instrument_id=instrument_id,
            holding_start_date=holding_start_date,
            calculation_frequency=calculation_frequency,
        ),
    }


def build_instrument_holdings_market_profiles(
    market_data: CanonicalInstrumentMarketData,
    *,
    instrument_ids: Iterable[str] | None = None,
    holding_start_dates: dict[str, date] | None = None,
    max_points: int = 48,
    calculation_frequency: CalculationFrequency = "daily",
) -> dict[str, dict[str, object]]:
    resolved_ids = _normalized_instrument_ids(
        instrument_ids
        if instrument_ids is not None
        else market_data.requested_instrument_ids
    )
    start_dates = holding_start_dates or {}
    return {
        instrument_id: build_instrument_holdings_market_profile_from_market_data(
            market_data,
            instrument_id=instrument_id,
            holding_start_date=start_dates.get(instrument_id),
            max_points=max_points,
            calculation_frequency=calculation_frequency,
        )
        for instrument_id in resolved_ids
        if instrument_id in market_data.instrument_core_by_id
    }


def build_instrument_holdings_market_profile(
    instrument_id: str,
    *,
    as_of_date: date,
    holding_start_date: date | None = None,
    max_points: int = 48,
    calculation_frequency: CalculationFrequency = "daily",
) -> dict[str, object]:
    market_data = lock_instrument_market_data(
        [instrument_id],
        as_of_date=as_of_date,
        roles=("chart", "total_return"),
    )
    if instrument_id not in market_data.instrument_core_by_id:
        return empty_instrument_holdings_market_profile(
            holding_start_date=holding_start_date,
            calculation_frequency=calculation_frequency,
        )
    return build_instrument_holdings_market_profile_from_market_data(
        market_data,
        instrument_id=instrument_id,
        holding_start_date=holding_start_date,
        max_points=max_points,
        calculation_frequency=calculation_frequency,
    )


__all__ = [
    "CanonicalInstrumentMarketData",
    "HOLDINGS_PRICE_CHART_RANGE_KEYS",
    "SUPPORTED_CHART_RANGE_KEYS",
    "build_instrument_holdings_market_profile",
    "build_instrument_holdings_market_profile_from_market_data",
    "build_instrument_holdings_market_profiles",
    "build_instrument_price_chart",
    "build_instrument_price_chart_from_market_data",
    "build_instrument_sparkline",
    "build_instrument_sparkline_from_market_data",
    "build_instrument_trend_metrics",
    "build_instrument_trend_metrics_from_market_data",
    "build_instrument_trend_metrics_from_points",
    "canonical_series_failure_reasons",
    "canonical_series_points",
    "empty_instrument_holdings_market_profile",
    "empty_instrument_trend_metrics",
    "lock_instrument_market_data",
    "lock_instrument_market_data_in_session",
    "normalize_chart_range_key",
]
