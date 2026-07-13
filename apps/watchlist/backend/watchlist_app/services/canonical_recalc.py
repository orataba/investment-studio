from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
import math
import statistics
from typing import Any, Mapping

from pydantic import ValidationError

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from portfolio_ops_instrument_core.db_models import (
    Instrument as CanonicalInstrument,
    InstrumentIdentifier as CanonicalInstrumentIdentifier,
)
from portfolio_ops_instrument_core.models import (
    CanonicalQuoteResolution,
    CanonicalQuoteSeriesResolution,
)
from portfolio_ops_instrument_core.quote_resolver import (
    CANONICAL_QUOTE_RESOLVER_STRATEGY_VERSION,
    QUOTE_SELECTION_POLICY_VERSION,
    resolve_quote_series_observation_at,
    resolve_role_quote_series_in_session,
)

from watchlist_app.db.models.instruments import InstrumentDetail
from watchlist_app.db.models.read_models import (
    InstrumentChartReadModel,
    InstrumentPerformanceReadModel,
    InstrumentRiskReadModel,
    InstrumentSummaryReadModel,
    WatchlistRowReadModel,
)
from watchlist_app.repositories.sqlalchemy.instruments import SQLAlchemyInstrumentRepository
from watchlist_app.repositories.sqlalchemy.instrument_attributes import (
    SQLAlchemyInstrumentAttributeRepository,
)
from watchlist_app.repositories.sqlalchemy.manual_profiles import (
    SQLAlchemyInstrumentManualProfileRepository,
)
from watchlist_app.repositories.sqlalchemy.read_models import SQLAlchemyReadModelRepository
from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
from watchlist_app.repositories.sqlalchemy.research_ratings import SQLAlchemyResearchRatingRepository
from watchlist_app.repositories.sqlalchemy.snapshots import SQLAlchemySnapshotRepository
from watchlist_app.repositories.sqlalchemy.taxonomy import SQLAlchemyTaxonomyRepository
from watchlist_app.reference_data.fund_taxonomy import FUND_TAXONOMY_CODE
from watchlist_app.services.fund_taxonomy import (
    build_taxonomy_context,
    merge_taxonomy_attributes,
)
from watchlist_app.services.calculation_frequency import (
    build_calculation_frequency_context,
)
from watchlist_app.services.investment_analytics import (
    build_investment_analytics_payload as _investment_analytics_payload,
    compute_annualized_return as _annualized_return,
    compute_calmar_ratio as _compute_calmar_ratio,
    compute_downside_deviation as _compute_downside_deviation,
    compute_sharpe as _compute_sharpe,
    compute_sortino as _compute_sortino,
    compute_volatility as _compute_volatility,
    monthly_return_series as _monthly_return_series,
    trailing_negative_month_count as _trailing_negative_month_count,
)
from watchlist_app.services.read_models import (
    build_watchlist_row_materialization,
    collapse_latest_attribute_values,
    serialize_payload,
)
from watchlist_app.services.quote_consumer_policy import (
    ConsumerFreshnessProfile,
    quote_consumer_dependency,
    watchlist_freshness_profile,
)
from watchlist_app.services.quote_resolution_summary import (
    CanonicalQuoteSeriesResolutionSummary,
)
from watchlist_app.services.research_ratings import serialize_research_rating
from watchlist_app.services.recalc_job_ids import make_recalc_dedupe_key, make_recalc_job_id
from watchlist_app.services.shared_instrument_registry import list_shared_instruments


DEFAULT_TABS = [
    "overview",
    "quote",
    "performance",
    "risk",
    "price",
    "people",
    "strategy",
    "documents",
    "research",
    "monitoring",
]


class RecalcJobLeaseLostError(RuntimeError):
    """Raised when an obsolete worker tries to publish a recalculation."""


class RecalcJobAlreadyRunningError(RuntimeError):
    """Raised when synchronous work would overlap an active instrument job."""


PEER_METRIC_MIN_SAMPLE = 2
PEER_COMPARISON_METRICS = [
    {
        "metric_key": "return_1w",
        "label": "1W Return",
        "source": "performance",
        "attr": "return_1w",
        "direction": "higher",
        "domain": "return",
        "format": "percent",
    },
    {
        "metric_key": "return_1m",
        "label": "1M Return",
        "source": "performance",
        "attr": "return_1m",
        "direction": "higher",
        "domain": "return",
        "format": "percent",
    },
    {
        "metric_key": "return_mtd",
        "label": "MTD Return",
        "source": "performance",
        "attr": "return_mtd",
        "direction": "higher",
        "domain": "return",
        "format": "percent",
    },
    {
        "metric_key": "return_ytd",
        "label": "YTD Return",
        "source": "performance",
        "attr": "return_ytd",
        "direction": "higher",
        "domain": "return",
        "format": "percent",
    },
    {
        "metric_key": "return_3m",
        "label": "3M Return",
        "source": "performance",
        "attr": "return_3m",
        "direction": "higher",
        "domain": "return",
        "format": "percent",
    },
    {
        "metric_key": "return_6m",
        "label": "6M Return",
        "source": "performance",
        "attr": "return_6m",
        "direction": "higher",
        "domain": "return",
        "format": "percent",
    },
    {
        "metric_key": "return_1y",
        "label": "1Y Return",
        "source": "performance",
        "attr": "return_1y",
        "direction": "higher",
        "domain": "return",
        "format": "percent",
    },
    {
        "metric_key": "return_3y_annualized",
        "label": "3Y Ann. Return",
        "source": "performance",
        "attr": "return_3y_annualized",
        "direction": "higher",
        "domain": "return",
        "format": "percent",
    },
    {
        "metric_key": "return_5y_annualized",
        "label": "5Y Ann. Return",
        "source": "performance",
        "attr": "return_5y_annualized",
        "direction": "higher",
        "domain": "return",
        "format": "percent",
    },
    {
        "metric_key": "annualized_return",
        "label": "Ann. Return",
        "source": "performance",
        "attr": "annualized_return",
        "direction": "higher",
        "domain": "return",
        "format": "percent",
    },
    {
        "metric_key": "volatility",
        "label": "Ann. Vol",
        "source": "risk",
        "attr": "volatility",
        "direction": "lower",
        "domain": "risk",
        "format": "percent",
    },
    {
        "metric_key": "max_drawdown",
        "label": "Max Drawdown",
        "source": "performance",
        "attr": "max_drawdown",
        "direction": "higher",
        "domain": "risk",
        "format": "percent",
    },
    {
        "metric_key": "sharpe_ratio",
        "label": "Sharpe",
        "source": "risk",
        "attr": "sharpe_ratio",
        "direction": "higher",
        "domain": "risk_adjusted",
        "format": "ratio",
    },
    {
        "metric_key": "sortino_ratio",
        "label": "Sortino",
        "source": "risk",
        "attr": "sortino_ratio",
        "direction": "higher",
        "domain": "risk_adjusted",
        "format": "ratio",
    },
    {
        "metric_key": "calmar",
        "label": "Calmar",
        "source": "performance",
        "attr": "calmar",
        "direction": "higher",
        "domain": "risk_adjusted",
        "format": "ratio",
    },
]


def _default_nav_settings() -> dict[str, Any]:
    return {
        "default_benchmark_instrument_id": None,
        "peer_baseline_instrument_ids": [],
    }


def _normalize_nav_settings(payload: dict[str, Any] | None) -> dict[str, Any]:
    source = payload or {}
    normalized = _default_nav_settings()
    normalized["default_benchmark_instrument_id"] = source.get(
        "default_benchmark_instrument_id"
    )
    normalized["peer_baseline_instrument_ids"] = source.get(
        "peer_baseline_instrument_ids"
    ) or []
    normalized["default_benchmark_instrument_id"] = (
        str(normalized.get("default_benchmark_instrument_id")).strip() or None
        if normalized.get("default_benchmark_instrument_id") is not None
        else None
    )
    normalized["peer_baseline_instrument_ids"] = [
        str(value).strip()
        for value in (normalized.get("peer_baseline_instrument_ids") or [])
        if str(value).strip()
    ]
    return normalized


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _current_valuation_date() -> date:
    return date.today()


def _coerce_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_market_data_input_watermark(value: object) -> datetime | None:
    """Parse canonical registry lineage without inventing a calculation time."""

    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return _coerce_utc(parsed)


def _safe_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    normalized = str(value).strip().replace(",", "")
    if not normalized:
        return None
    try:
        return Decimal(normalized)
    except Exception:
        return None


def _safe_float(value: object) -> float | None:
    numeric = _safe_decimal(value)
    if numeric is None:
        return None
    result = float(numeric)
    return result if math.isfinite(result) else None


def _string_list(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    return []


def _node_path_node_ids(node: object | None) -> list[str]:
    return _string_list(getattr(node, "path_node_ids_json", None))


def _node_path_labels(node: object | None) -> list[str]:
    return _string_list(getattr(node, "path_labels_json", None))


def _active_fund_instrument_ids(session: Session) -> set[str]:
    local_active_instrument_ids = {
        str(instrument_id)
        for instrument_id in session.scalars(
            select(InstrumentDetail.instrument_id).where(
                InstrumentDetail.instrument_type.in_(("fund", "etf")),
                InstrumentDetail.is_active.is_(True),
            )
        ).all()
    }
    shared_active_instrument_ids = {
        str(item.get("instrument_id"))
        for instrument_type in ("fund", "etf")
        for item in list_shared_instruments(instrument_type=instrument_type, limit=None)
        if str(item.get("instrument_id") or "").strip()
    }
    return local_active_instrument_ids & shared_active_instrument_ids


def _quantile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    clamped = min(max(percentile, 0.0), 1.0)
    index = (len(ordered) - 1) * clamped
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[int(index)]
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _rank_metric_value(
    *,
    value: float,
    samples: list[tuple[str, float]],
    direction: str,
) -> dict[str, object]:
    if direction == "lower":
        better_count = sum(1 for _, candidate in samples if candidate < value)
        equal_count = sum(1 for _, candidate in samples if candidate == value)
    else:
        better_count = sum(1 for _, candidate in samples if candidate > value)
        equal_count = sum(1 for _, candidate in samples if candidate == value)
    rank = better_count + 1
    sample_count = len(samples)
    tie_adjusted_rank = better_count + ((equal_count + 1) / 2)
    percentile = (
        None
        if sample_count < 2
        else ((sample_count - tie_adjusted_rank) / (sample_count - 1)) * 100
    )
    if percentile is None:
        quartile = None if sample_count == 0 else min(4, max(1, math.ceil(rank / sample_count * 4)))
    elif percentile >= 75:
        quartile = 1
    elif percentile >= 50:
        quartile = 2
    elif percentile >= 25:
        quartile = 3
    else:
        quartile = 4
    return {
        "rank": rank,
        "percentile": percentile,
        "quartile": quartile,
        "sample_count": sample_count,
    }


def _mean_optional(values: list[float | None]) -> float | None:
    valid = [value for value in values if value is not None]
    return statistics.mean(valid) if valid else None


def _primary_peer_ranking(peer_comparison: dict[str, object] | None) -> dict[str, object] | None:
    if not isinstance(peer_comparison, dict) or peer_comparison.get("status") != "ready":
        return None
    metric_rows = peer_comparison.get("metrics")
    if not isinstance(metric_rows, list):
        return None
    by_key = {
        str(row.get("metric_key")): row
        for row in metric_rows
        if isinstance(row, dict)
    }
    for metric_key in ("annualized_return", "return_1y", "return_ytd", "return_1m"):
        row = by_key.get(metric_key)
        if not row:
            continue
        return {
            "metric_key": metric_key,
            "metric_label": row.get("label"),
            "quartile": row.get("quartile"),
            "percentile": row.get("percentile"),
            "rank": row.get("rank"),
            "sample_count": row.get("sample_count"),
            "peer_group": " / ".join(
                str(item)
                for item in (peer_comparison.get("peer_path") or [])
                if str(item).strip()
            )
            or None,
        }
    return None


PEER_WATCHLIST_PERCENTILE_ATTRIBUTE_KEYS = {
    "return_1w": "peer_return_1w_percentile",
    "return_1m": "peer_return_1m_percentile",
    "return_ytd": "peer_return_ytd_percentile",
    "return_1y": "peer_return_1y_percentile",
    "return_3y_annualized": "peer_return_3y_percentile",
    "return_5y_annualized": "peer_return_5y_percentile",
    "annualized_return": "peer_annualized_return_percentile",
    "volatility": "peer_volatility_percentile",
    "max_drawdown": "peer_max_drawdown_percentile",
    "sharpe_ratio": "peer_sharpe_percentile",
    "calmar": "peer_calmar_percentile",
}
PEER_WATCHLIST_MATERIALIZED_ATTRIBUTE_KEYS = {
    *PEER_WATCHLIST_PERCENTILE_ATTRIBUTE_KEYS.values(),
    "peer_overall_percentile",
    "peer_return_percentile",
    "peer_risk_percentile",
    "peer_risk_adjusted_percentile",
    "peer_sample_count",
    "peer_group",
}


def _peer_watchlist_attributes(peer_comparison: dict[str, object] | None) -> dict[str, object]:
    if not isinstance(peer_comparison, dict) or peer_comparison.get("status") != "ready":
        return {}

    attributes: dict[str, object] = {}
    metrics = peer_comparison.get("metrics")
    if isinstance(metrics, list):
        for row in metrics:
            if not isinstance(row, dict):
                continue
            metric_key = str(row.get("metric_key") or "")
            attribute_key = PEER_WATCHLIST_PERCENTILE_ATTRIBUTE_KEYS.get(metric_key)
            if attribute_key is None:
                continue
            percentile = _safe_float(row.get("percentile"))
            if percentile is not None:
                attributes[attribute_key] = percentile

    summary = peer_comparison.get("summary")
    if isinstance(summary, dict):
        for source_key, attribute_key in (
            ("overall_percentile", "peer_overall_percentile"),
            ("return_percentile", "peer_return_percentile"),
            ("risk_percentile", "peer_risk_percentile"),
            ("risk_adjusted_percentile", "peer_risk_adjusted_percentile"),
        ):
            percentile = _safe_float(summary.get(source_key))
            if percentile is not None:
                attributes[attribute_key] = percentile

    sample_count = _safe_float(peer_comparison.get("sample_count"))
    if sample_count is not None:
        attributes["peer_sample_count"] = sample_count

    peer_path = [
        str(item).strip()
        for item in (peer_comparison.get("peer_path") or [])
        if str(item).strip()
    ]
    if peer_path:
        attributes["peer_group"] = " / ".join(peer_path)

    return attributes


def _hash_payload(payload: object) -> str:
    serialized = json.dumps(
        serialize_payload(payload),
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sha256:{sha256(serialized.encode('utf-8')).hexdigest()}"


def _primary_identifier(shared_instrument: dict[str, object] | None) -> str | None:
    if not isinstance(shared_instrument, dict):
        return None
    identifiers = shared_instrument.get("identifiers")
    if not isinstance(identifiers, list):
        return None
    primary = next(
        (
            item
            for item in identifiers
            if isinstance(item, dict) and bool(item.get("is_primary"))
        ),
        None,
    )
    fallback = next((item for item in identifiers if isinstance(item, dict)), None)
    candidate = primary or fallback
    if not isinstance(candidate, dict):
        return None
    value = str(candidate.get("identifier_value") or "").strip()
    return value or None


def _window_return(latest_value: float, base_value: float, days: int) -> float | None:
    if latest_value <= 0 or base_value <= 0 or days <= 0:
        return None
    raw = latest_value / base_value - 1
    if days > 366:
        return (pow(1 + raw, 365.25 / max(days, 1)) - 1) * 100
    return raw * 100


def _value_at_or_before(nav_points: list[dict[str, Any]], target_date: date) -> dict[str, Any] | None:
    candidates = [point for point in nav_points if point["as_of_date"] <= target_date]
    return candidates[-1] if candidates else None


def _value_before(nav_points: list[dict[str, Any]], target_date: date) -> dict[str, Any] | None:
    candidates = [point for point in nav_points if point["as_of_date"] < target_date]
    return candidates[-1] if candidates else None


def _has_valid_nav_values(nav_points: list[dict[str, Any]]) -> bool:
    observation_dates = [point.get("as_of_date") for point in nav_points]
    return (
        len(observation_dates) == len(set(observation_dates))
        and all(isinstance(observation_date, date) for observation_date in observation_dates)
        and all(
            _safe_float(point.get("value")) is not None
            and float(point["value"]) > 0
            for point in nav_points
        )
    )


def _compute_drawdown(nav_points: list[dict[str, Any]]) -> float | None:
    if len(nav_points) < 2 or not _has_valid_nav_values(nav_points):
        return None
    peak_value = nav_points[0]["value"]
    max_drawdown = 0.0
    for point in nav_points:
        value = point["value"]
        if value > peak_value:
            peak_value = value
        if peak_value <= 0:
            continue
        drawdown = value / peak_value - 1
        max_drawdown = min(max_drawdown, drawdown)
    return max_drawdown * 100


def _current_drawdown(nav_points: list[dict[str, Any]]) -> float | None:
    if len(nav_points) < 2 or not _has_valid_nav_values(nav_points):
        return None
    peak_value = nav_points[0]["value"]
    current_drawdown = 0.0
    for point in nav_points:
        value = point["value"]
        if value > peak_value:
            peak_value = value
        if peak_value <= 0:
            continue
        current_drawdown = value / peak_value - 1
    return current_drawdown * 100


def _compute_drawdown_summary(nav_points: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(nav_points) < 2 or not _has_valid_nav_values(nav_points):
        return None

    peak_point = nav_points[0]
    worst_drawdown = 0.0
    worst_peak = peak_point
    worst_valley = peak_point
    longest_drawdown_days = 0
    active_drawdown_peak = peak_point

    for point in nav_points:
        if point["value"] >= peak_point["value"]:
            peak_point = point
            active_drawdown_peak = point
            continue

        if active_drawdown_peak["value"] <= 0:
            continue

        drawdown = point["value"] / active_drawdown_peak["value"] - 1
        drawdown_days = (point["as_of_date"] - active_drawdown_peak["as_of_date"]).days
        longest_drawdown_days = max(longest_drawdown_days, max(drawdown_days, 0))
        if drawdown < worst_drawdown:
            worst_drawdown = drawdown
            worst_peak = active_drawdown_peak
            worst_valley = point

    return {
        "maximum": worst_drawdown * 100,
        "peak_date": worst_peak["as_of_date"].isoformat(),
        "valley_date": worst_valley["as_of_date"].isoformat(),
        "max_duration_months": round(longest_drawdown_days / 30.4375) if longest_drawdown_days else 0,
    }


def _monthly_drawdown_series(nav_points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(nav_points) < 2 or not _has_valid_nav_values(nav_points):
        return []
    peak_value = nav_points[0]["value"]
    monthly_minimums: dict[tuple[int, int], dict[str, Any]] = {}
    for point in nav_points:
        if point["value"] > peak_value:
            peak_value = point["value"]
        if peak_value <= 0:
            continue
        drawdown = (point["value"] / peak_value - 1) * 100
        bucket = (point["as_of_date"].year, point["as_of_date"].month)
        previous = monthly_minimums.get(bucket)
        if previous is None or drawdown <= previous["value"]:
            monthly_minimums[bucket] = {
                "as_of_date": point["as_of_date"],
                "value": drawdown,
            }
    return [monthly_minimums[key] for key in sorted(monthly_minimums)]


def _rolling_annualized_volatility(monthly_returns: list[dict[str, Any]], window: int = 12) -> list[dict[str, Any]]:
    if len(monthly_returns) < window:
        return []
    rolling: list[dict[str, Any]] = []
    for index in range(window - 1, len(monthly_returns)):
        window_values = [row["value"] / 100 for row in monthly_returns[index - window + 1:index + 1]]
        if len(window_values) < 2:
            continue
        try:
            stdev = statistics.stdev(window_values)
        except statistics.StatisticsError:
            continue
        rolling.append({
            "as_of_date": monthly_returns[index]["as_of_date"],
            "value": stdev * math.sqrt(12) * 100,
        })
    return rolling


def _median_value(values: list[float]) -> float | None:
    if not values:
        return None
    try:
        return statistics.median(values)
    except statistics.StatisticsError:
        return None


def _percentile_rank(values: list[float], current: float) -> float | None:
    if not values:
        return None
    below_or_equal = sum(1 for value in values if value <= current)
    return below_or_equal / len(values) * 100


def _format_percent(value: float | None, digits: int = 1) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    return f"{value:.{digits}f}%"


def _format_number(value: float | None, digits: int = 2) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    return f"{value:.{digits}f}"


def _build_current_risk_watch(nav_points: list[dict[str, Any]], drawdown_summary: dict[str, Any] | None) -> dict[str, Any]:
    monthly_returns = _monthly_return_series(nav_points)
    monthly_drawdowns = _monthly_drawdown_series(nav_points)
    rolling_vol = _rolling_annualized_volatility(monthly_returns)
    current_drawdown = _current_drawdown(nav_points)
    latest_monthly_return = monthly_returns[-1]["value"] if monthly_returns else None
    latest_monthly_drawdown = monthly_drawdowns[-1]["value"] if monthly_drawdowns else None
    latest_rolling_vol = rolling_vol[-1]["value"] if rolling_vol else None
    rolling_vol_median = _median_value([row["value"] for row in rolling_vol])
    rolling_vol_pct = _percentile_rank([row["value"] for row in rolling_vol], latest_rolling_vol) if latest_rolling_vol is not None else None
    max_drawdown = drawdown_summary.get("maximum") if isinstance(drawdown_summary, dict) else None
    trailing_negative_months = _trailing_negative_month_count(monthly_returns)
    overall_score = 0

    def _level_score(level: str) -> int:
        return 2 if level == "High" else 1 if level == "Elevated" else 0

    if latest_rolling_vol is None or rolling_vol_median is None or rolling_vol_pct is None:
        volatility_signal = {
            "signal": "Volatility Regime",
            "level": "N/A",
            "reading": "N/A · Need more 12M rolling history",
        }
    else:
        multiple = latest_rolling_vol / rolling_vol_median if rolling_vol_median not in {None, 0} else None
        level = (
            "High"
            if rolling_vol_pct >= 90 or (multiple is not None and multiple >= 1.4)
            else "Elevated"
            if rolling_vol_pct >= 75 or (multiple is not None and multiple >= 1.2)
            else "Normal"
        )
        overall_score += _level_score(level)
        volatility_signal = {
            "signal": "Volatility Regime",
            "level": level,
            "reading": f"{level} · {_format_percent(latest_rolling_vol)} vs median {_format_percent(rolling_vol_median)} ({_format_number(rolling_vol_pct, 0)}th pct)",
        }

    if current_drawdown is None:
        drawdown_signal = {
            "signal": "Drawdown Pressure",
            "level": "N/A",
            "reading": "N/A · No drawdown history",
        }
    else:
        worst_abs = abs(max_drawdown) if isinstance(max_drawdown, (int, float)) and max_drawdown not in {None, 0} else None
        ratio = abs(current_drawdown) / worst_abs if worst_abs else 0
        level = (
            "High"
            if current_drawdown <= -8 or ratio >= 0.6
            else "Elevated"
            if current_drawdown <= -4 or ratio >= 0.35
            else "Normal"
        )
        overall_score += _level_score(level)
        ratio_text = f", {_format_number(ratio * 100, 0)}% of worst" if worst_abs else ""
        drawdown_signal = {
            "signal": "Drawdown Pressure",
            "level": level,
            "reading": f"{level} · {_format_percent(current_drawdown)} current{ratio_text}",
        }

    if latest_monthly_return is None and latest_monthly_drawdown is None:
        loss_signal = {
            "signal": "Recent Loss Pressure",
            "level": "N/A",
            "reading": "N/A · Need recent monthly history",
        }
    else:
        level = (
            "High"
            if trailing_negative_months >= 3 or (latest_monthly_return is not None and latest_monthly_return <= -3)
            else "Elevated"
            if trailing_negative_months >= 2 or (latest_monthly_return is not None and latest_monthly_return <= -1.5)
            else "Normal"
        )
        overall_score += _level_score(level)
        loss_signal = {
            "signal": "Recent Loss Pressure",
            "level": level,
            "reading": f"{level} · latest month {_format_percent(latest_monthly_return)}; latest monthly drawdown {_format_percent(latest_monthly_drawdown)}; {trailing_negative_months} down month(s)",
        }

    recovery_signal = {
        "signal": "Recovery State",
        "level": "Elevated" if current_drawdown is not None and current_drawdown <= -2 else "Normal",
        "reading": (
            f"Elevated · Currently {_format_percent(current_drawdown)} below prior high watermark"
            if current_drawdown is not None and current_drawdown <= -2
            else "Normal · At or back near high watermark"
        ),
    }
    overall_score += _level_score(recovery_signal["level"])

    overall_level = "High" if overall_score >= 5 else "Elevated" if overall_score >= 2 else "Normal"
    return {
        "overall_level": overall_level,
        "rows": [
            {
                "signal": "Overall Watch",
                "level": overall_level,
                "reading": f"{overall_level} · {overall_score} signal point(s)",
            },
            volatility_signal,
            drawdown_signal,
            loss_signal,
            recovery_signal,
        ],
        "note": "Heuristic watch flags surface current risk pressure from drawdown, rolling volatility, recent losses, and recovery state. They do not forecast returns.",
    }


def _build_risk_structure(nav_points: list[dict[str, Any]], drawdown_summary: dict[str, Any] | None, current_watch: dict[str, Any] | None) -> dict[str, Any]:
    volatility = _compute_volatility(nav_points)
    downside_deviation = _compute_downside_deviation(nav_points)
    max_drawdown = drawdown_summary.get("maximum") if isinstance(drawdown_summary, dict) else None
    recovery_months = drawdown_summary.get("max_duration_months") if isinstance(drawdown_summary, dict) else None
    current_watch_level = current_watch.get("overall_level") if isinstance(current_watch, dict) else None

    if volatility is None and downside_deviation is None and max_drawdown is None:
        return {"rows": []}

    downside_ratio = (
        downside_deviation / volatility
        if volatility not in {None, 0} and downside_deviation is not None
        else None
    )
    rows = [
        {
            "characteristic": "Risk Style",
            "reading": f"Vol {_format_percent(volatility)} · Max DD {_format_percent(max_drawdown)}",
            "interpretation": (
                "Insufficient history to classify the long-run risk amplitude."
                if volatility is None or max_drawdown is None
                else
                "Low-amplitude path. Capital preservation matters more than benchmark capture."
                if volatility < 8 and abs(max_drawdown) < 10
                else "Balanced amplitude. Drawdowns matter, but the path is still broadly manageable."
                if volatility < 15 and abs(max_drawdown) < 20
                else "High-amplitude path. Position sizing and liquidity discipline matter."
            ),
        },
        {
            "characteristic": "Downside Shape",
            "reading": f"Downside Dev {_format_percent(downside_deviation)} · Ratio {_format_number(downside_ratio, 2)}",
            "interpretation": (
                "Insufficient history to characterize downside concentration."
                if downside_ratio is None
                else
                "Downside volatility runs materially below total volatility."
                if downside_ratio < 0.7
                else "Downside moves account for a large share of total volatility."
                if downside_ratio > 0.9
                else "Downside contribution is meaningful but not dominant."
            ),
        },
        {
            "characteristic": "Recovery Profile",
            "reading": f"Max DD {_format_percent(max_drawdown)} · Duration {recovery_months if recovery_months is not None else '—'} mo",
            "interpretation": (
                "Insufficient history to classify recovery behavior."
                if recovery_months is None or max_drawdown is None
                else
                "Drawdowns have historically taken time to repair."
                if recovery_months > 12
                else "Recovery profile is moderate."
                if recovery_months > 4
                else "Historically, major setbacks healed relatively quickly."
            ),
        },
        {
            "characteristic": "Current Regime",
            "reading": f"Watch {current_watch_level or '—'}",
            "interpretation": (
                "Current pressure indicators are elevated; recent path deserves closer monitoring."
                if current_watch_level == "High"
                else "Some pressure indicators are above baseline; position sizing and timing matter."
                if current_watch_level == "Elevated"
                else "Current pressure indicators look contained relative to history."
            ),
        },
    ]
    return {"rows": rows}


def _build_risk_change_monitor(nav_points: list[dict[str, Any]], drawdown_summary: dict[str, Any] | None, current_watch: dict[str, Any] | None) -> dict[str, Any]:
    monthly_returns = _monthly_return_series(nav_points)
    monthly_drawdowns = _monthly_drawdown_series(nav_points)
    rolling_vol = _rolling_annualized_volatility(monthly_returns)
    latest_rolling_vol = rolling_vol[-1]["value"] if rolling_vol else None
    rolling_vol_median = _median_value([row["value"] for row in rolling_vol])
    current_drawdown = _current_drawdown(nav_points)
    worst_drawdown = drawdown_summary.get("maximum") if isinstance(drawdown_summary, dict) else None
    latest_monthly_drawdown = monthly_drawdowns[-1]["value"] if monthly_drawdowns else None
    worst_monthly_drawdown = min((row["value"] for row in monthly_drawdowns), default=None)
    latest_monthly_return = monthly_returns[-1]["value"] if monthly_returns else None
    median_monthly_return = _median_value([row["value"] for row in monthly_returns])
    trailing_negative_months = _trailing_negative_month_count(monthly_returns)
    watch_map = {
        row.get("signal"): row.get("level")
        for row in (current_watch.get("rows") if isinstance(current_watch, dict) else [])
        if isinstance(row, dict)
    }
    rows = [
        {
            "signal": "Rolling Ann. Vol",
            "current": _format_percent(latest_rolling_vol),
            "baseline": f"Median {_format_percent(rolling_vol_median)}" if rolling_vol_median is not None else "—",
            "change": (
                f"{'+' if latest_rolling_vol >= rolling_vol_median else ''}{_format_percent(latest_rolling_vol - rolling_vol_median)}"
                if latest_rolling_vol is not None and rolling_vol_median is not None
                else "—"
            ),
            "watch": watch_map.get("Volatility Regime") or "N/A",
        },
        {
            "signal": "Current Drawdown",
            "current": _format_percent(current_drawdown),
            "baseline": f"Worst {_format_percent(worst_drawdown)}" if worst_drawdown is not None else "—",
            "change": (
                f"{_format_number(abs(current_drawdown) / abs(worst_drawdown) * 100, 0)}% of worst"
                if current_drawdown is not None and worst_drawdown not in {None, 0}
                else "—"
            ),
            "watch": watch_map.get("Drawdown Pressure") or "N/A",
        },
        {
            "signal": "Latest Monthly Drawdown",
            "current": _format_percent(latest_monthly_drawdown),
            "baseline": f"Worst {_format_percent(worst_monthly_drawdown)}" if worst_monthly_drawdown is not None else "—",
            "change": (
                f"{_format_number(abs(latest_monthly_drawdown) / abs(worst_monthly_drawdown) * 100, 0)}% of worst"
                if latest_monthly_drawdown is not None and worst_monthly_drawdown not in {None, 0}
                else "—"
            ),
            "watch": watch_map.get("Recent Loss Pressure") or "N/A",
        },
        {
            "signal": "Recent Return Pressure",
            "current": _format_percent(latest_monthly_return),
            "baseline": f"Median month {_format_percent(median_monthly_return)}" if median_monthly_return is not None else "—",
            "change": f"{trailing_negative_months} trailing down month(s)",
            "watch": watch_map.get("Recent Loss Pressure") or "N/A",
        },
    ]
    return {
        "rows": rows,
        "note": "Change monitor highlights where current risk conditions sit relative to the fund's own recent history.",
    }


def _compute_calendar_year_returns(nav_points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    years = sorted({point["as_of_date"].year for point in nav_points}, reverse=True)
    annual_returns: list[dict[str, Any]] = []
    latest_as_of = nav_points[-1]["as_of_date"] if nav_points else None
    for year in years:
        start_of_year = date(year, 1, 1)
        end_of_year = min(date(year, 12, 31), latest_as_of) if latest_as_of is not None else date(year, 12, 31)
        start_point = _value_at_or_before(nav_points, start_of_year)
        if start_point is None:
            start_point = next((point for point in nav_points if point["as_of_date"].year == year), None)
        end_point = _value_at_or_before(nav_points, end_of_year)
        if start_point is None or end_point is None:
            continue
        if end_point["as_of_date"] <= start_point["as_of_date"] and end_point is not start_point:
            continue
        annual_returns.append(
            {
                "year": year,
                "investment_nav": _window_return(
                    end_point["value"],
                    start_point["value"],
                    max((end_point["as_of_date"] - start_point["as_of_date"]).days, 1),
                ),
                "category_nav": None,
                "index_nav": None,
            }
        )
    return annual_returns


def _snapshot_metadata(snapshot) -> dict[str, object] | None:
    if snapshot is None:
        return None
    watermark = _coerce_utc(snapshot.market_data_input_watermark_at)
    return {
        "as_of_date": snapshot.as_of_date.isoformat(),
        "methodology_version": snapshot.methodology_version,
        "calculated_at": _coerce_utc(snapshot.calculated_at).isoformat().replace("+00:00", "Z"),
        "market_data_input_watermark_at": (
            watermark.isoformat().replace("+00:00", "Z")
            if watermark is not None
            else None
        ),
    }


def _snapshot_pair_lineage(
    performance_snapshot,
    risk_snapshot,
) -> dict[str, object] | None:
    """Return the shared lineage of one atomic performance/risk pair.

    A one-sided, cross-date, cross-watermark, or separately calculated pair is
    damaged state.  It may remain available as historical evidence, but it is
    not eligible for current metrics or peer ranking.
    """

    if performance_snapshot is None or risk_snapshot is None:
        return None
    performance_as_of = getattr(performance_snapshot, "as_of_date", None)
    risk_as_of = getattr(risk_snapshot, "as_of_date", None)
    if not isinstance(performance_as_of, date) or performance_as_of != risk_as_of:
        return None
    performance_calculated_at = _coerce_utc(
        getattr(performance_snapshot, "calculated_at", None)
    )
    risk_calculated_at = _coerce_utc(getattr(risk_snapshot, "calculated_at", None))
    if (
        performance_calculated_at is None
        or performance_calculated_at != risk_calculated_at
    ):
        return None
    performance_watermark = _coerce_utc(
        getattr(performance_snapshot, "market_data_input_watermark_at", None)
    )
    risk_watermark = _coerce_utc(
        getattr(risk_snapshot, "market_data_input_watermark_at", None)
    )
    if performance_watermark is None or performance_watermark != risk_watermark:
        return None
    performance_methodology = str(
        getattr(performance_snapshot, "methodology_version", "") or ""
    ).strip()
    risk_methodology = str(
        getattr(risk_snapshot, "methodology_version", "") or ""
    ).strip()
    if not performance_methodology or not risk_methodology:
        return None
    return {
        "as_of_date": performance_as_of,
        "calculated_at": performance_calculated_at,
        "market_data_input_watermark_at": performance_watermark,
        "performance_methodology_version": performance_methodology,
        "risk_methodology_version": risk_methodology,
    }


def _growth_of_100(nav_points: list[dict[str, Any]]) -> float | None:
    if len(nav_points) < 2 or nav_points[0]["value"] <= 0:
        return None
    return nav_points[-1]["value"] / nav_points[0]["value"] * 100


def _risk_level_label(volatility: float | None) -> str | None:
    if volatility is None:
        return None
    if volatility < 8:
        return "low"
    if volatility < 15:
        return "moderate"
    return "high"


def _canonical_instrument(session: Session, instrument_id: str) -> CanonicalInstrument:
    instrument = session.get(CanonicalInstrument, instrument_id)
    if instrument is None:
        raise ValueError(f"Canonical instrument not found: {instrument_id}")
    return instrument


def _resolve_canonical_series(
    session: Session,
    *,
    instrument_id: str,
    role: str,
    valuation_date: date,
    range_mode: str = "since_inception",
    start_date: date | None = None,
    instrument: CanonicalInstrument | None = None,
    consumer_profile: ConsumerFreshnessProfile | None = None,
) -> CanonicalQuoteSeriesResolution:
    instrument = instrument or _canonical_instrument(session, instrument_id)
    consumer_profile = consumer_profile or watchlist_freshness_profile(
        instrument.instrument_type
    )
    return resolve_role_quote_series_in_session(
        session,
        resolver_strategy_version=CANONICAL_QUOTE_RESOLVER_STRATEGY_VERSION,
        instrument_id=instrument_id,
        role=role,
        currency=instrument.currency,
        range_mode=range_mode,
        start_date=start_date,
        end_date=valuation_date,
        freshness_policy=consumer_profile.resolver_policy,
        quote_selection_policy_version=QUOTE_SELECTION_POLICY_VERSION,
    )


def _canonical_points(window: CanonicalQuoteSeriesResolution) -> list[dict[str, Any]]:
    return [
        {
            "as_of_date": point.observation_date,
            "value": float(point.value),
            "observation_id": point.observation_id,
            "revision_id": point.revision_id,
            "revision_number": point.revision_number,
            "payload_hash": point.payload_hash,
            "source_ref": point.source_ref,
            "source_published_at": point.source_published_at,
            "ingested_at": point.ingested_at,
        }
        for point in window.points
    ]


def _full_quote_resolution_payload(
    window: CanonicalQuoteSeriesResolution,
    consumer_profile: ConsumerFreshnessProfile,
) -> dict[str, object]:
    return {
        **window.model_dump(mode="json"),
        "consumer_freshness_profile": consumer_profile.payload(),
        "consumer_dependency": quote_consumer_dependency(
            canonical_dependency_fingerprint=(
                window.calculation_dependency.fingerprint
            ),
            consumer_profile=consumer_profile,
        ),
    }


def _quote_resolution_summary_payload(
    window: CanonicalQuoteSeriesResolution,
    consumer_profile: ConsumerFreshnessProfile,
) -> dict[str, object]:
    """Return bounded lineage for persisted and presentation read models.

    The canonical resolver's full observation series and revision manifest are
    calculation inputs.  Duplicating them into every read model makes storage
    and response size grow with both history length and projection count.  The
    summary preserves the contract, quality state, counts, and fingerprints
    required to explain and invalidate a projection without copying those
    unbounded inputs.
    """

    return CanonicalQuoteSeriesResolutionSummary.from_resolution(
        window,
        consumer_profile,
    ).model_dump(mode="json")


_PERFORMANCE_LAST_GOOD_FIELDS = frozenset(
    {
        "growth_chart_series",
        "annual_returns",
        "trailing_returns",
        "ranking",
        "peer_comparison",
        "calculation_frequency_profile",
        "snapshot_metadata",
    }
)
_RISK_LAST_GOOD_FIELDS = frozenset(
    {
        "risk_overview",
        "scatter_points",
        "risk_metrics",
        "drawdown_summary",
        "current_drawdown",
        "risk_structure",
        "current_watch",
        "change_monitor",
        "calculation_frequency_profile",
        "snapshot_metadata",
    }
)


def _project_persisted_analytics_payload(
    payload: object,
    *,
    allowed_fields: frozenset[str],
) -> dict[str, object]:
    """Project a last-good artifact through an explicit positive allowlist."""

    if not isinstance(payload, Mapping):
        return {}
    return {
        field_name: payload[field_name]
        for field_name in sorted(allowed_fields)
        if field_name in payload
    }


def _persisted_historical_quote_resolution(
    payload: object,
) -> CanonicalQuoteSeriesResolutionSummary | None:
    """Accept only the current bounded schema as preserved snapshot lineage."""

    if not isinstance(payload, Mapping):
        return None
    candidate = payload.get("historical_quote_resolution")
    try:
        return CanonicalQuoteSeriesResolutionSummary.model_validate(candidate)
    except ValidationError:
        return None


def _aligned_historical_quote_resolutions(
    performance_payload: object,
    risk_payload: object,
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    performance_resolution = _persisted_historical_quote_resolution(
        performance_payload
    )
    risk_resolution = _persisted_historical_quote_resolution(risk_payload)
    if performance_resolution is None or risk_resolution is None:
        return None, None
    if (
        performance_resolution.instrument_id != risk_resolution.instrument_id
        or performance_resolution.role != risk_resolution.role
        or performance_resolution.calculation_dependency.fingerprint
        != risk_resolution.calculation_dependency.fingerprint
        or performance_resolution.consumer_dependency.fingerprint
        != risk_resolution.consumer_dependency.fingerprint
    ):
        return None, None
    return (
        performance_resolution.model_dump(mode="json"),
        risk_resolution.model_dump(mode="json"),
    )


def _endpoint_is_resolved(
    endpoint: CanonicalQuoteResolution | None,
) -> bool:
    return bool(
        endpoint is not None
        and endpoint.resolution_status == "resolved"
        and endpoint.value is not None
    )


def _stable_reason_codes(
    window: CanonicalQuoteSeriesResolution,
    endpoint: CanonicalQuoteResolution | None,
) -> list[str]:
    result: list[str] = []
    for reason_code in [
        *(endpoint.reason_codes if endpoint is not None else []),
        *window.reason_codes,
    ]:
        normalized = str(reason_code).strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _endpoint_state(
    window: CanonicalQuoteSeriesResolution,
    endpoint: CanonicalQuoteResolution | None,
) -> str:
    if _endpoint_is_resolved(endpoint):
        return "resolved"
    reason_codes = set(_stable_reason_codes(window, endpoint))
    if window.freshness_status == "late" or (
        endpoint is not None and endpoint.freshness_status == "late"
    ):
        return "stale"
    if reason_codes.intersection(
        {"partial_series", "rejected_observation", "withdrawn_observation"}
    ):
        return "partial"
    return "unavailable"


def _last_successful_snapshot_at(performance_snapshot, risk_snapshot) -> datetime | None:
    performance_time = _coerce_utc(
        getattr(performance_snapshot, "calculated_at", None)
    )
    risk_time = _coerce_utc(getattr(risk_snapshot, "calculated_at", None))
    if performance_time is not None and risk_time is not None:
        return min(performance_time, risk_time)
    # The Watchlist analytics materialization is an atomic performance+risk
    # pair. A one-sided row indicates damaged/incomplete state, not success.
    return None


def _selection_metadata(window: CanonicalQuoteSeriesResolution) -> dict[str, object]:
    quote_basis = window.quote_basis
    metric_family = window.metric_family
    label = quote_basis.replace("_", " ").title() if quote_basis else None
    series_type = (
        "total_return_nav"
        if window.role == "total_return" and metric_family == "nav"
        else metric_family
    )
    return {
        "role": window.role,
        "metric_family": metric_family,
        "quote_basis": quote_basis,
        "series_type": series_type,
        "basis_type": "nav_with_dividend" if window.role == "total_return" else None,
        "label": label,
        "date_label": "Last Price Date" if metric_family == "price" else "Last NAV Date",
        "quote_series_id": window.quote_series_id,
        "policy_version": window.quote_selection_policy_version,
        "policy_revision": window.quote_selection_policy_revision,
    }


def _build_chart_payload(
    instrument_id: str,
    window: CanonicalQuoteSeriesResolution,
    consumer_profile: ConsumerFreshnessProfile,
) -> dict[str, object]:
    nav_points = _canonical_points(window)
    metadata = _selection_metadata(window)
    series_label = str(metadata.get("label") or "Quote")
    return {
        "instrument_id": instrument_id,
        "base_series_type": str(metadata.get("series_type") or "quote"),
        "selected_series": metadata,
        "currency": window.currency,
        "resolution": _quote_resolution_summary_payload(window, consumer_profile),
        "consumer_freshness_profile": consumer_profile.payload(),
        "date_range": (
            {
                "start": nav_points[0]["as_of_date"].isoformat(),
                "end": nav_points[-1]["as_of_date"].isoformat(),
            }
            if nav_points
            else None
        ),
        "series": [
            {
                "name": f"{instrument_id.upper()} {series_label}",
                "metric_family": metadata.get("metric_family"),
                "quote_basis": metadata.get("quote_basis"),
                "role": metadata.get("role"),
                "points": [
                    {"date": point["as_of_date"].isoformat(), "value": round(point["value"], 4)}
                    for point in nav_points
                ],
            }
        ],
        "available_compare_targets": [],
    }


class CanonicalRecalcService:
    def __init__(self) -> None:
        self.instrument_repository = SQLAlchemyInstrumentRepository()
        self.attribute_repository = SQLAlchemyInstrumentAttributeRepository()
        self.manual_profile_repository = SQLAlchemyInstrumentManualProfileRepository()
        self.read_model_repository = SQLAlchemyReadModelRepository()
        self.recalc_repository = SQLAlchemyRecalcJobRepository()
        self.research_rating_repository = SQLAlchemyResearchRatingRepository()
        self.snapshot_repository = SQLAlchemySnapshotRepository()
        self.taxonomy_repository = SQLAlchemyTaxonomyRepository()

    def build_nav_series_payload(
        self,
        session: Session,
        *,
        instrument_id: str,
        valuation_date: date,
    ) -> dict[str, object]:
        manual_profile = self.manual_profile_repository.get(session, instrument_id)
        nav_settings = _normalize_nav_settings(
            manual_profile.nav_settings_json if manual_profile is not None else None
        )
        canonical_instrument = _canonical_instrument(session, instrument_id)
        consumer_profile = watchlist_freshness_profile(
            canonical_instrument.instrument_type
        )
        window = _resolve_canonical_series(
            session,
            instrument_id=instrument_id,
            role="total_return",
            valuation_date=valuation_date,
            instrument=canonical_instrument,
            consumer_profile=consumer_profile,
        )
        selection = _selection_metadata(window)
        resolved_points = _canonical_points(window)
        frequency_context = build_calculation_frequency_context(resolved_points)
        calculation_dates = {
            point["as_of_date"]
            for point in frequency_context["points"]
            if isinstance(point, dict) and isinstance(point.get("as_of_date"), date)
        }
        latest_point = resolved_points[-1] if resolved_points else None
        previous_point = resolved_points[-2] if len(resolved_points) >= 2 else None
        latest_value = latest_point["value"] if latest_point is not None else None
        previous_value = previous_point["value"] if previous_point is not None else None
        empty_statistics = {
            "latest_date": None,
            "latest_value": None,
            "latest_change": None,
            "latest_change_percent": None,
        }
        selected_statistics = {
            "latest_date": (
                latest_point["as_of_date"].isoformat() if latest_point is not None else None
            ),
            "latest_value": latest_value,
            "latest_change": (
                latest_value - previous_value
                if latest_value is not None and previous_value is not None
                else None
            ),
            "latest_change_percent": (
                (latest_value / previous_value - 1) * 100
                if latest_value is not None and previous_value not in {None, 0}
                else None
            ),
        }
        return {
            "instrument_id": instrument_id,
            "valuation_date": valuation_date.isoformat(),
            "count": window.observation_count,
            "nav_basis_type": selection["basis_type"],
            "nav_basis_source": "canonical_resolver",
            "nav_basis_status": window.resolution_status,
            "selected_role": selection["role"],
            "selected_metric_family": selection["metric_family"],
            "selected_quote_basis": selection["quote_basis"],
            "selected_series_type": selection["series_type"],
            "selected_series_label": selection["label"],
            "selected_date_label": selection["date_label"],
            "resolution": _full_quote_resolution_payload(window, consumer_profile),
            "consumer_freshness_profile": consumer_profile.payload(),
            "calculation_frequency_profile": frequency_context["profile"],
            "basis_statistics": {
                "nav": empty_statistics,
                "nav_with_dividend": selected_statistics,
            },
            "compare_settings": {
                "default_benchmark_instrument_id": nav_settings.get("default_benchmark_instrument_id"),
                "peer_instrument_ids": list(nav_settings.get("peer_baseline_instrument_ids") or []),
            },
            "rows": [
                {
                    "date": observation.observation_date.isoformat(),
                    "nav": None,
                    "nav_with_dividend": (
                        float(observation.value)
                        if observation.status == "complete" and observation.value is not None
                        else None
                    ),
                    "currency": window.currency,
                    "frequency": None,
                    "adopted_at": (
                        _coerce_utc(observation.ingested_at).isoformat().replace("+00:00", "Z")
                        if observation.ingested_at is not None
                        else None
                    ),
                    "status": observation.status,
                    "observation_id": observation.observation_id,
                    "revision_id": observation.revision_id,
                    "revision_number": observation.revision_number,
                    "payload_hash": observation.payload_hash,
                    "source_ref": observation.source_ref,
                    "source_published_at": (
                        _coerce_utc(observation.source_published_at).isoformat().replace("+00:00", "Z")
                        if observation.source_published_at is not None
                        else None
                    ),
                    "selected_basis_type": selection["basis_type"],
                    "selected_value": (
                        float(observation.value)
                        if observation.status == "complete" and observation.value is not None
                        else None
                    ),
                    "calculation_included": (
                        window.resolution_status == "resolved"
                        and observation.status == "complete"
                        and observation.observation_date in calculation_dates
                    ),
                }
                for observation in window.observations
            ],
        }

    def build_investment_analytics_payload(
        self,
        session: Session,
        *,
        instrument_id: str,
        benchmark_instrument_id: str | None,
        rolling_window_months: int,
        valuation_date: date,
    ) -> dict[str, object]:
        def _calculation_window(
            target_instrument_id: str,
        ) -> tuple[
            CanonicalQuoteSeriesResolution,
            list[dict[str, Any]],
            ConsumerFreshnessProfile,
            CanonicalQuoteResolution | None,
        ]:
            canonical_instrument = _canonical_instrument(
                session, target_instrument_id
            )
            consumer_profile = watchlist_freshness_profile(
                canonical_instrument.instrument_type
            )
            window = _resolve_canonical_series(
                session,
                instrument_id=target_instrument_id,
                role="total_return",
                valuation_date=valuation_date,
                instrument=canonical_instrument,
                consumer_profile=consumer_profile,
            )
            frequency_context = build_calculation_frequency_context(_canonical_points(window))
            endpoint = (
                resolve_quote_series_observation_at(
                    window, requested_as_of_date=valuation_date
                )
                if window.quote_series_id is not None
                else None
            )
            return (
                window,
                list(frequency_context["points"]),
                consumer_profile,
                endpoint,
            )

        (
            fund_window,
            fund_points,
            fund_consumer_profile,
            fund_endpoint,
        ) = _calculation_window(instrument_id)
        (
            benchmark_window,
            benchmark_points,
            benchmark_consumer_profile,
            benchmark_endpoint,
        ) = (
            _calculation_window(benchmark_instrument_id)
            if benchmark_instrument_id is not None
            else (None, [], None, None)
        )
        payload = _investment_analytics_payload(
            fund_points,
            benchmark_points=benchmark_points,
            benchmark_instrument_id=benchmark_instrument_id,
            rolling_window_months=rolling_window_months,
        )
        payload["valuation_date"] = valuation_date.isoformat()
        payload["quote_resolutions"] = {
            "fund": _quote_resolution_summary_payload(
                fund_window, fund_consumer_profile
            ),
            "benchmark": (
                _quote_resolution_summary_payload(
                    benchmark_window, benchmark_consumer_profile
                )
                if benchmark_window is not None
                and benchmark_consumer_profile is not None
                else None
            ),
        }
        payload["calculation_states"] = {
            "fund": {
                "current_endpoint_state": _endpoint_state(
                    fund_window, fund_endpoint
                ),
                "analysis_as_of_date": (
                    fund_points[-1]["as_of_date"].isoformat()
                    if fund_points
                    else None
                ),
                "reason_codes": _stable_reason_codes(
                    fund_window, fund_endpoint
                ),
            },
            "benchmark": (
                {
                    "current_endpoint_state": _endpoint_state(
                        benchmark_window, benchmark_endpoint
                    ),
                    "analysis_as_of_date": (
                        benchmark_points[-1]["as_of_date"].isoformat()
                        if benchmark_points
                        else None
                    ),
                    "reason_codes": _stable_reason_codes(
                        benchmark_window, benchmark_endpoint
                    ),
                }
                if benchmark_window is not None
                else None
            ),
        }
        return payload

    def execute_recalc(
        self,
        session: Session,
        *,
        instrument_id: str,
        job_type: str,
        trigger_type: str,
        trigger_ref_type: str | None,
        trigger_ref_id: str | None,
        valuation_date: date | None = None,
        commit: bool = False,
    ) -> dict[str, object]:
        resolved_valuation_date = valuation_date or _current_valuation_date()
        try:
            with session.begin_nested():
                self.recalc_repository.acquire_instrument_lock(
                    session,
                    instrument_id=instrument_id,
                    wait=True,
                )
                running = self.recalc_repository.find_running_job(
                    session,
                    instrument_id=instrument_id,
                    for_update=True,
                )
                if running is not None:
                    raise RecalcJobAlreadyRunningError(
                        f"Instrument {instrument_id} already has running recalc job "
                        f"{running.recalc_job_id}."
                    )
                record = self.recalc_repository.find_open_job(
                    session,
                    instrument_id=instrument_id,
                    job_type=job_type,
                    for_update=True,
                )
                if record is None:
                    record = self.recalc_repository.create(
                        session,
                        recalc_job_id=make_recalc_job_id(),
                        job_type=job_type,
                        instrument_id=instrument_id,
                        trigger_type=trigger_type,
                        trigger_ref_type=trigger_ref_type,
                        trigger_ref_id=trigger_ref_id,
                        job_status="queued",
                        priority=100 if job_type == "all" else 90,
                        dedupe_key=make_recalc_dedupe_key(
                            job_type=job_type,
                            instrument_id=instrument_id,
                        ),
                        payload_json={"requested_by": trigger_type},
                    )
                else:
                    record.payload_json = {
                        **(record.payload_json or {}),
                        "executed_by": trigger_type,
                    }
                self.recalc_repository.mark_running(session, record)
        except IntegrityError as error:
            raise RecalcJobAlreadyRunningError(
                f"Instrument {instrument_id} acquired a concurrent recalc job."
            ) from error

        lease_token = str(record.lease_token or "")
        try:
            with session.begin_nested():
                result = self._execute_recalc_job(
                    session,
                    instrument_id=instrument_id,
                    job_type=job_type,
                    valuation_date=resolved_valuation_date,
                )
                if not self.recalc_repository.mark_completed(
                    session,
                    record,
                    lease_token=lease_token,
                    payload_json=result,
                ):
                    raise RecalcJobLeaseLostError(
                        f"Recalc lease lost before completion: {record.recalc_job_id}"
                    )
                self._enqueue_source_change_follow_up(session, record=record, result=result)
            if commit:
                session.commit()
            else:
                session.flush()
            return {
                "recalc_job_id": record.recalc_job_id,
                "job_status": "completed",
                "result": result,
            }
        except RecalcJobLeaseLostError:
            session.rollback()
            raise
        except Exception as exc:
            if not self.recalc_repository.mark_failed(
                session,
                record,
                lease_token=lease_token,
                error_message=str(exc),
            ):
                session.rollback()
                raise RecalcJobLeaseLostError(
                    f"Recalc lease lost while recording failure: {record.recalc_job_id}"
                ) from exc
            if commit:
                session.commit()
            else:
                session.flush()
            raise

    def execute_claimed_job(
        self,
        session: Session,
        *,
        record,
        commit: bool = False,
    ) -> dict[str, object]:
        valuation_date = _current_valuation_date()
        lease_token = str(record.lease_token or "")
        try:
            with session.begin_nested():
                result = self._execute_recalc_job(
                    session,
                    instrument_id=record.instrument_id,
                    job_type=record.job_type,
                    valuation_date=valuation_date,
                )
                if not self.recalc_repository.mark_completed(
                    session,
                    record,
                    lease_token=lease_token,
                    payload_json=result,
                ):
                    raise RecalcJobLeaseLostError(
                        f"Recalc lease lost before completion: {record.recalc_job_id}"
                    )
                self._enqueue_source_change_follow_up(session, record=record, result=result)
            if commit:
                session.commit()
            else:
                session.flush()
            return {
                "recalc_job_id": record.recalc_job_id,
                "job_status": "completed",
                "result": result,
            }
        except RecalcJobLeaseLostError:
            session.rollback()
            raise
        except Exception as exc:
            if not self.recalc_repository.mark_failed(
                session,
                record,
                lease_token=lease_token,
                error_message=str(exc),
            ):
                session.rollback()
                raise RecalcJobLeaseLostError(
                    f"Recalc lease lost while recording failure: {record.recalc_job_id}"
                ) from exc
            if commit:
                session.commit()
            else:
                session.flush()
            raise

    def _enqueue_source_change_follow_up(
        self,
        session: Session,
        *,
        record,
        result: dict[str, object],
    ) -> None:
        if not bool(result.get("source_changed_during_recalc")):
            return
        dependency_fingerprint = str(
            result.get("source_dependency_fingerprint_at_end") or ""
        ).strip() or None
        existing = self.recalc_repository.find_open_job(
            session,
            instrument_id=record.instrument_id,
            job_type="all",
        )
        if existing is not None:
            return
        try:
            with session.begin_nested():
                self.recalc_repository.create(
                    session,
                    recalc_job_id=make_recalc_job_id(),
                    job_type="all",
                    instrument_id=record.instrument_id,
                    trigger_type="source_changed_during_recalc",
                    trigger_ref_type="canonical_quote_dependency",
                    trigger_ref_id=dependency_fingerprint,
                    job_status="queued",
                    priority=100,
                    dedupe_key=make_recalc_dedupe_key(
                        job_type="all",
                        instrument_id=record.instrument_id,
                    ),
                    payload_json={"requested_by": "source_changed_during_recalc"},
                )
        except IntegrityError:
            # Another scheduler observed the same watermark concurrently.
            return

    def _execute_recalc_job(
        self,
        session: Session,
        *,
        instrument_id: str,
        job_type: str,
        valuation_date: date,
    ) -> dict[str, object]:
        instrument = self.instrument_repository.get(session, instrument_id)
        if instrument is None:
            raise ValueError(f"Instrument not found: {instrument_id}")
        canonical_instrument = _canonical_instrument(session, instrument_id)
        instrument.instrument_name = canonical_instrument.instrument_name
        instrument.instrument_type = canonical_instrument.instrument_type
        primary_identifier = session.scalar(
            select(CanonicalInstrumentIdentifier)
            .where(CanonicalInstrumentIdentifier.instrument_id == instrument_id)
            .order_by(
                CanonicalInstrumentIdentifier.is_primary.desc(),
                CanonicalInstrumentIdentifier.instrument_identifier_id,
            )
            .limit(1)
        )
        instrument.primary_identifier_type = (
            primary_identifier.identifier_type if primary_identifier is not None else None
        )
        instrument.primary_identifier_value = (
            primary_identifier.identifier_value if primary_identifier is not None else None
        )
        if canonical_instrument.instrument_type in {"fund", "etf", "index"}:
            instrument.detail_view_type = canonical_instrument.instrument_type
        now = _utcnow()
        market_data_input_watermark_at = _parse_market_data_input_watermark(
            canonical_instrument.market_data_updated_at
        )
        market_data_input_watermark_status = (
            "known" if market_data_input_watermark_at is not None else "unknown"
        )
        market_data_input_watermark_reason_code = (
            "canonical_instrument_market_data_updated_at"
            if market_data_input_watermark_at is not None
            else "missing_or_invalid_canonical_market_data_updated_at"
        )
        consumer_profile = watchlist_freshness_profile(
            canonical_instrument.instrument_type
        )
        total_return_window = _resolve_canonical_series(
            session,
            instrument_id=instrument_id,
            role="total_return",
            valuation_date=valuation_date,
            instrument=canonical_instrument,
            consumer_profile=consumer_profile,
        )
        chart_window = _resolve_canonical_series(
            session,
            instrument_id=instrument_id,
            role="chart",
            valuation_date=valuation_date,
            instrument=canonical_instrument,
            consumer_profile=consumer_profile,
        )
        total_return_endpoint = (
            resolve_quote_series_observation_at(
                total_return_window,
                requested_as_of_date=valuation_date,
            )
            if total_return_window.quote_series_id is not None
            else None
        )
        canonical_nav_points = _canonical_points(total_return_window)
        frequency_context = build_calculation_frequency_context(canonical_nav_points)
        calculation_nav_points = frequency_context["points"]
        calculation_frequency_profile = frequency_context["profile"]
        nav_metadata = _selection_metadata(total_return_window)
        nav_selection = {
            "nav_basis_type": nav_metadata["basis_type"],
            "nav_basis_source": "canonical_resolver",
            "nav_basis_status": total_return_window.resolution_status,
            "points": calculation_nav_points,
            "selected_role": nav_metadata["role"],
            "selected_metric_family": nav_metadata["metric_family"],
            "selected_quote_basis": nav_metadata["quote_basis"],
            "selected_series_type": nav_metadata["series_type"],
            "selected_series_label": nav_metadata["label"],
            "selected_date_label": nav_metadata["date_label"],
        }
        raw_attributes = collapse_latest_attribute_values(
            self.attribute_repository.get_values_for_asset(session, instrument_id)
        )
        assignment = self.taxonomy_repository.get_assignment(session, instrument_id=instrument_id)
        taxonomy_node = (
            self.taxonomy_repository.get_node(session, node_id=str(assignment.node_id))
            if assignment is not None and assignment.node_id
            else None
        )
        taxonomy_context = build_taxonomy_context(taxonomy_node)
        watchlist_attributes = merge_taxonomy_attributes(
            taxonomy_context=taxonomy_context,
            instrument_attributes=raw_attributes,
        )
        current_endpoint_resolved = _endpoint_is_resolved(total_return_endpoint)
        current_drawdown = _current_drawdown(calculation_nav_points)
        if current_endpoint_resolved and current_drawdown is not None:
            watchlist_attributes["current_drawdown"] = current_drawdown

        performance_snapshot = self.snapshot_repository.get_current_performance(session, instrument_id)
        risk_snapshot = self.snapshot_repository.get_current_risk(session, instrument_id)
        existing_performance_read_model = self.read_model_repository.get_performance(
            session, instrument_id
        )
        existing_risk_read_model = self.read_model_repository.get_risk(
            session, instrument_id
        )
        research_rating = self.research_rating_repository.get_current(session, instrument_id)

        analysis_window = total_return_window
        analysis_nav_points = calculation_nav_points
        analysis_frequency_profile = calculation_frequency_profile
        snapshot_pair_complete = (
            _snapshot_pair_lineage(performance_snapshot, risk_snapshot) is not None
        )
        historical_calculation_state = (
            "last_good_preserved" if snapshot_pair_complete else "unavailable"
        )
        snapshot_replaced = False

        if job_type in {"performance", "all"}:
            if calculation_nav_points and current_endpoint_resolved:
                performance_snapshot = self._replace_performance_snapshot(
                    session,
                    instrument_id=instrument_id,
                    nav_points=calculation_nav_points,
                    quote_window=total_return_window,
                    consumer_profile=consumer_profile,
                    calculation_frequency_profile=calculation_frequency_profile,
                    market_data_input_watermark_at=(
                        market_data_input_watermark_at
                    ),
                    now=now,
                )
                risk_snapshot = self._replace_risk_snapshot(
                    session,
                    instrument_id=instrument_id,
                    nav_points=calculation_nav_points,
                    calculation_frequency_profile=calculation_frequency_profile,
                    quote_dependency=quote_consumer_dependency(
                        canonical_dependency_fingerprint=(
                            total_return_window.calculation_dependency.fingerprint
                        ),
                        consumer_profile=consumer_profile,
                    ),
                    market_data_input_watermark_at=(
                        market_data_input_watermark_at
                    ),
                    now=now,
                )
                historical_calculation_state = "current_endpoint"
                snapshot_replaced = True
            elif (
                calculation_nav_points
                and not snapshot_pair_complete
            ):
                historical_as_of_date = calculation_nav_points[-1]["as_of_date"]
                historical_window = _resolve_canonical_series(
                    session,
                    instrument_id=instrument_id,
                    role="total_return",
                    valuation_date=historical_as_of_date,
                    instrument=canonical_instrument,
                    consumer_profile=consumer_profile,
                )
                historical_endpoint = (
                    resolve_quote_series_observation_at(
                        historical_window,
                        requested_as_of_date=historical_as_of_date,
                    )
                    if historical_window.quote_series_id is not None
                    else None
                )
                if _endpoint_is_resolved(historical_endpoint):
                    historical_points = _canonical_points(historical_window)
                    historical_frequency_context = (
                        build_calculation_frequency_context(historical_points)
                    )
                    analysis_window = historical_window
                    analysis_nav_points = historical_frequency_context["points"]
                    analysis_frequency_profile = historical_frequency_context[
                        "profile"
                    ]
                    performance_snapshot = self._replace_performance_snapshot(
                        session,
                        instrument_id=instrument_id,
                        nav_points=analysis_nav_points,
                        quote_window=historical_window,
                        consumer_profile=consumer_profile,
                        calculation_frequency_profile=(
                            analysis_frequency_profile
                        ),
                        market_data_input_watermark_at=(
                            market_data_input_watermark_at
                        ),
                        now=now,
                    )
                    risk_snapshot = self._replace_risk_snapshot(
                        session,
                        instrument_id=instrument_id,
                        nav_points=analysis_nav_points,
                        calculation_frequency_profile=(
                            analysis_frequency_profile
                        ),
                        quote_dependency=quote_consumer_dependency(
                            canonical_dependency_fingerprint=(
                                historical_window.calculation_dependency.fingerprint
                            ),
                            consumer_profile=consumer_profile,
                        ),
                        market_data_input_watermark_at=(
                            market_data_input_watermark_at
                        ),
                        now=now,
                    )
                    historical_calculation_state = (
                        "historical_as_of_last_observation"
                    )
                    snapshot_replaced = True

        snapshot_pair_lineage = _snapshot_pair_lineage(
            performance_snapshot, risk_snapshot
        )
        preserve_last_good = bool(
            not snapshot_replaced
            and historical_calculation_state == "last_good_preserved"
            and snapshot_pair_lineage is not None
        )
        preserved_performance_resolution: dict[str, object] | None = None
        preserved_risk_resolution: dict[str, object] | None = None
        if preserve_last_good:
            (
                preserved_performance_resolution,
                preserved_risk_resolution,
            ) = _aligned_historical_quote_resolutions(
                (
                    existing_performance_read_model.payload_json
                    if existing_performance_read_model is not None
                    else None
                ),
                (
                    existing_risk_read_model.payload_json
                    if existing_risk_read_model is not None
                    else None
                ),
            )
        endpoint_observation_date = (
            total_return_endpoint.observation_date
            if total_return_endpoint is not None
            else None
        )
        pair_reason_codes: list[str] = []
        if snapshot_pair_lineage is None:
            pair_reason_codes.append("analytics_snapshot_pair_unaligned")
        else:
            if (
                endpoint_observation_date is not None
                and snapshot_pair_lineage["as_of_date"]
                != endpoint_observation_date
            ):
                pair_reason_codes.append("analytics_snapshot_endpoint_date_mismatch")
            if (
                snapshot_pair_lineage["market_data_input_watermark_at"]
                != market_data_input_watermark_at
            ):
                pair_reason_codes.append("analytics_snapshot_watermark_mismatch")
        if (
            preserve_last_good
            and (
                preserved_performance_resolution is None
                or preserved_risk_resolution is None
            )
        ):
            pair_reason_codes.append(
                "analytics_historical_dependency_manifest_unavailable"
            )
        publish_current_metrics = bool(
            current_endpoint_resolved
            and snapshot_pair_lineage is not None
            and not pair_reason_codes
        )
        calculation_state = {
            "current_endpoint_state": _endpoint_state(
                total_return_window, total_return_endpoint
            ),
            "historical_calculation_state": historical_calculation_state,
            "analytics_snapshot_state": (
                "current_aligned" if publish_current_metrics else "unqualified"
            ),
            "history_as_of_date": (
                snapshot_pair_lineage["as_of_date"].isoformat()
                if snapshot_pair_lineage is not None
                else None
            ),
            "reason_codes": list(
                dict.fromkeys(
                    [
                        *_stable_reason_codes(
                            total_return_window, total_return_endpoint
                        ),
                        *pair_reason_codes,
                    ]
                )
            ),
        }

        summary_payload = self._summary_payload(
            instrument=instrument,
            nav_selection=nav_selection,
            quote_window=total_return_window,
            endpoint_resolution=total_return_endpoint,
            performance_snapshot=performance_snapshot,
            risk_snapshot=risk_snapshot,
            research_rating=research_rating,
            attributes=raw_attributes,
            taxonomy_context=taxonomy_context,
            consumer_profile=consumer_profile,
            calculation_state=calculation_state,
            publish_current_metrics=publish_current_metrics,
            market_data_input_watermark_at=(
                market_data_input_watermark_at
            ),
            market_data_input_watermark_status=(
                market_data_input_watermark_status
            ),
            market_data_input_watermark_reason_code=(
                market_data_input_watermark_reason_code
            ),
            now=now,
        )
        chart_payload = _build_chart_payload(
            instrument_id, chart_window, consumer_profile
        )
        chart_endpoint = (
            resolve_quote_series_observation_at(
                chart_window,
                requested_as_of_date=valuation_date,
            )
            if chart_window.quote_series_id is not None
            else None
        )
        chart_endpoint_state = _endpoint_state(chart_window, chart_endpoint)
        chart_payload["calculation_state"] = {
            "current_endpoint_state": chart_endpoint_state,
            "current_endpoint_observation_date": (
                chart_endpoint.observation_date.isoformat()
                if chart_endpoint is not None
                and chart_endpoint.resolution_status == "resolved"
                else None
            ),
            "reason_codes": _stable_reason_codes(
                chart_window, chart_endpoint
            ),
        }
        peer_comparison = self._peer_comparison_payload(
            session,
            instrument_id=instrument_id,
            taxonomy_node=taxonomy_node,
            performance_snapshot=performance_snapshot,
            risk_snapshot=risk_snapshot,
            publish_current_metrics=publish_current_metrics,
            target_frequency_profile=analysis_frequency_profile,
            target_pair_lineage=snapshot_pair_lineage,
        )
        if preserve_last_good and existing_performance_read_model is not None:
            performance_payload = _project_persisted_analytics_payload(
                existing_performance_read_model.payload_json,
                allowed_fields=_PERFORMANCE_LAST_GOOD_FIELDS,
            )
        else:
            performance_payload = self._performance_payload(
                performance_snapshot=performance_snapshot,
                peer_comparison=peer_comparison,
                nav_points=([] if preserve_last_good else analysis_nav_points),
                calculation_frequency_profile=(
                    build_calculation_frequency_context([])["profile"]
                    if preserve_last_good
                    else analysis_frequency_profile
                ),
            )
        performance_payload["quote_resolution"] = _quote_resolution_summary_payload(
            total_return_window, consumer_profile
        )
        performance_payload["historical_quote_resolution"] = (
            preserved_performance_resolution
            if preserve_last_good
            else _quote_resolution_summary_payload(
                analysis_window, consumer_profile
            )
        )
        performance_payload["endpoint_resolution"] = (
            total_return_endpoint.model_dump(mode="json")
            if total_return_endpoint is not None
            else None
        )
        performance_payload["calculation_state"] = calculation_state
        performance_payload["peer_comparison"] = peer_comparison
        performance_payload["ranking"] = _primary_peer_ranking(peer_comparison)
        if peer_comparison.get("status") != "ready":
            trailing_returns = performance_payload.get("trailing_returns")
            if isinstance(trailing_returns, list):
                performance_payload["trailing_returns"] = [
                    {**row, "category_nav": None}
                    if isinstance(row, dict)
                    else row
                    for row in trailing_returns
                ]
        if preserve_last_good and existing_risk_read_model is not None:
            risk_payload = _project_persisted_analytics_payload(
                existing_risk_read_model.payload_json,
                allowed_fields=_RISK_LAST_GOOD_FIELDS,
            )
        else:
            risk_payload = self._risk_payload(
                risk_snapshot=risk_snapshot,
                performance_snapshot=performance_snapshot,
                peer_comparison=peer_comparison,
                nav_points=([] if preserve_last_good else analysis_nav_points),
                calculation_frequency_profile=(
                    build_calculation_frequency_context([])["profile"]
                    if preserve_last_good
                    else analysis_frequency_profile
                ),
            )
        risk_payload["quote_resolution"] = _quote_resolution_summary_payload(
            total_return_window, consumer_profile
        )
        risk_payload["historical_quote_resolution"] = (
            preserved_risk_resolution
            if preserve_last_good
            else _quote_resolution_summary_payload(
                analysis_window, consumer_profile
            )
        )
        risk_payload["endpoint_resolution"] = (
            total_return_endpoint.model_dump(mode="json")
            if total_return_endpoint is not None
            else None
        )
        risk_payload["calculation_state"] = calculation_state
        if peer_comparison.get("status") != "ready":
            risk_overview = risk_payload.get("risk_overview")
            if isinstance(risk_overview, dict):
                risk_payload["risk_overview"] = {
                    **risk_overview,
                    "risk_vs_category": None,
                    "return_vs_category": None,
                }
            risk_metrics = risk_payload.get("risk_metrics")
            if isinstance(risk_metrics, list):
                risk_payload["risk_metrics"] = [
                    {**row, "category": None}
                    if isinstance(row, dict)
                    else row
                    for row in risk_metrics
                ]
        summary_freshness_status = str(
            summary_payload["freshness"]["data_freshness_status"]
        )
        chart_freshness_status = (
            (
                "partial"
                if chart_window.coverage_status == "partial"
                else "fresh"
            )
            if chart_endpoint_state == "resolved"
            else (
                chart_endpoint_state
                if chart_endpoint_state in {"stale", "partial"}
                else "unavailable"
            )
        )
        for model_class, payload, freshness_status in (
            (
                InstrumentSummaryReadModel,
                summary_payload,
                summary_freshness_status,
            ),
            (InstrumentChartReadModel, chart_payload, chart_freshness_status),
            (
                InstrumentPerformanceReadModel,
                performance_payload,
                summary_freshness_status,
            ),
            (
                InstrumentRiskReadModel,
                risk_payload,
                summary_freshness_status,
            ),
        ):
            self.read_model_repository.upsert_payload_read_model(
                session,
                model_class=model_class,
                instrument_id=instrument_id,
                payload_json=payload,
                data_freshness_status=freshness_status,
                last_recalculated_at=now,
                market_data_input_watermark_at=(
                    market_data_input_watermark_at
                ),
            )

        self._refresh_watchlist_rows(
            session,
            instrument=instrument,
            summary_payload=summary_payload,
            attributes=watchlist_attributes,
            peer_comparison=peer_comparison,
            performance_snapshot=performance_snapshot,
            risk_snapshot=risk_snapshot,
            research_rating=research_rating,
            publish_current_metrics=publish_current_metrics,
            market_data_input_watermark_at=(
                market_data_input_watermark_at
            ),
            now=now,
        )
        self._invalidate_changed_peer_cohorts(
            session,
            changed_instrument_id=instrument_id,
            current_peer_comparison=peer_comparison,
            invalidated_at=now,
        )

        start_fingerprint = _hash_payload(
            {
                "total_return": quote_consumer_dependency(
                    canonical_dependency_fingerprint=(
                        total_return_window.calculation_dependency.fingerprint
                    ),
                    consumer_profile=consumer_profile,
                )["fingerprint"],
                "chart": quote_consumer_dependency(
                    canonical_dependency_fingerprint=(
                        chart_window.calculation_dependency.fingerprint
                    ),
                    consumer_profile=consumer_profile,
                )["fingerprint"],
            }
        )
        total_return_window_at_end = _resolve_canonical_series(
            session,
            instrument_id=instrument_id,
            role="total_return",
            valuation_date=valuation_date,
            instrument=canonical_instrument,
            consumer_profile=consumer_profile,
        )
        chart_window_at_end = _resolve_canonical_series(
            session,
            instrument_id=instrument_id,
            role="chart",
            valuation_date=valuation_date,
            instrument=canonical_instrument,
            consumer_profile=consumer_profile,
        )
        end_fingerprint = _hash_payload(
            {
                "total_return": quote_consumer_dependency(
                    canonical_dependency_fingerprint=(
                        total_return_window_at_end.calculation_dependency.fingerprint
                    ),
                    consumer_profile=consumer_profile,
                )["fingerprint"],
                "chart": quote_consumer_dependency(
                    canonical_dependency_fingerprint=(
                        chart_window_at_end.calculation_dependency.fingerprint
                    ),
                    consumer_profile=consumer_profile,
                )["fingerprint"],
            }
        )
        source_changed_during_recalc = end_fingerprint != start_fingerprint

        return {
            "instrument_id": instrument_id,
            "job_type": job_type,
            "valuation_date": valuation_date.isoformat(),
            "performance_snapshot_id": getattr(performance_snapshot, "snapshot_id", None),
            "risk_snapshot_id": getattr(risk_snapshot, "snapshot_id", None),
            "source_dependency_fingerprint_at_start": start_fingerprint,
            "source_dependency_fingerprint_at_end": end_fingerprint,
            "source_changed_during_recalc": source_changed_during_recalc,
            "completed_at": now.isoformat(),
        }

    def _replace_performance_snapshot(
        self,
        session: Session,
        *,
        instrument_id: str,
        nav_points: list[dict[str, Any]],
        quote_window: CanonicalQuoteSeriesResolution,
        consumer_profile: ConsumerFreshnessProfile,
        calculation_frequency_profile: dict[str, object],
        market_data_input_watermark_at: datetime | None,
        now: datetime,
    ):
        latest = nav_points[-1]
        as_of_date = latest["as_of_date"]
        windows = {
            "return_ytd": date(as_of_date.year, 1, 1) - timedelta(days=1),
            "return_1w": as_of_date - timedelta(days=7),
            "return_mtd": date(as_of_date.year, as_of_date.month, 1) - timedelta(days=1),
            "return_1m": as_of_date - timedelta(days=30),
            "return_3m": as_of_date - timedelta(days=90),
            "return_6m": as_of_date - timedelta(days=180),
            "return_1y": as_of_date - timedelta(days=365),
        }
        returns: dict[str, Decimal | None] = {}
        boundary_resolutions: dict[str, object] = {}
        for key, target_date in windows.items():
            boundary = resolve_quote_series_observation_at(
                quote_window,
                requested_as_of_date=target_date,
            )
            boundary_resolutions[key] = boundary.model_dump(mode="json")
            if boundary.resolution_status != "resolved" or boundary.value is None:
                returns[key] = None
                continue
            returns[key] = _safe_decimal(
                _window_return(
                    latest["value"],
                    float(boundary.value),
                    max((as_of_date - boundary.observation_date).days, 1),
                )
            )

        first = nav_points[0]
        annualized_return = _annualized_return(
            latest["value"],
            first["value"],
            max((as_of_date - first["as_of_date"]).days, 1),
        )
        annualized_windows = {
            "return_3y_annualized": round(365.25 * 3),
            "return_5y_annualized": round(365.25 * 5),
            "return_10y_annualized": round(365.25 * 10),
        }
        annualized_window_returns: dict[str, Decimal | None] = {}
        for key, lookback_days in annualized_windows.items():
            boundary = resolve_quote_series_observation_at(
                quote_window,
                requested_as_of_date=as_of_date - timedelta(days=lookback_days),
            )
            boundary_resolutions[key] = boundary.model_dump(mode="json")
            if boundary.resolution_status != "resolved" or boundary.value is None:
                annualized_window_returns[key] = None
                continue
            annualized_window_returns[key] = _safe_decimal(
                _annualized_return(
                    latest["value"],
                    float(boundary.value),
                    max((as_of_date - boundary.observation_date).days, 1),
                )
            )
        max_drawdown = _compute_drawdown(nav_points)
        calmar = _compute_calmar_ratio(
            annualized_return,
            max_drawdown,
            max((as_of_date - first["as_of_date"]).days, 1),
        )
        return self.snapshot_repository.replace_performance(
            session,
            snapshot_id=f"perf:{instrument_id}:{as_of_date.isoformat()}:{make_recalc_job_id()}",
            instrument_id=instrument_id,
            data={
                "as_of_date": as_of_date,
                "market_data_input_watermark_at": (
                    market_data_input_watermark_at
                ),
                "methodology_version": "canonical-performance/v6",
                "input_hash": _hash_payload(
                    {
                        "nav_points": nav_points,
                        "calculation_frequency_profile": calculation_frequency_profile,
                        "quote_dependency": quote_consumer_dependency(
                            canonical_dependency_fingerprint=(
                                quote_window.calculation_dependency.fingerprint
                            ),
                            consumer_profile=consumer_profile,
                        ),
                        "boundary_resolutions": boundary_resolutions,
                    }
                ),
                "calculated_at": now,
                "superseded_at": None,
                "is_current": True,
                "return_ytd": returns["return_ytd"],
                "return_1w": returns["return_1w"],
                "return_mtd": returns["return_mtd"],
                "return_1m": returns["return_1m"],
                "return_3m": returns["return_3m"],
                "return_6m": returns["return_6m"],
                "return_1y": returns["return_1y"],
                "return_3y_annualized": annualized_window_returns["return_3y_annualized"],
                "return_5y_annualized": annualized_window_returns["return_5y_annualized"],
                "return_10y_annualized": annualized_window_returns["return_10y_annualized"],
                "max_drawdown": _safe_decimal(max_drawdown),
                "calmar": _safe_decimal(calmar),
                "annualized_return": _safe_decimal(annualized_return),
            },
        )

    def _replace_risk_snapshot(
        self,
        session: Session,
        *,
        instrument_id: str,
        nav_points: list[dict[str, Any]],
        calculation_frequency_profile: dict[str, object],
        quote_dependency: dict[str, object],
        market_data_input_watermark_at: datetime | None,
        now: datetime,
    ):
        latest = nav_points[-1]
        volatility = _compute_volatility(nav_points)
        downside_volatility = _compute_downside_deviation(nav_points)
        sharpe_ratio = _compute_sharpe(nav_points)
        sortino_ratio = _compute_sortino(nav_points)
        return self.snapshot_repository.replace_risk(
            session,
            snapshot_id=f"risk:{instrument_id}:{latest['as_of_date'].isoformat()}:{make_recalc_job_id()}",
            instrument_id=instrument_id,
            data={
                "as_of_date": latest["as_of_date"],
                "market_data_input_watermark_at": (
                    market_data_input_watermark_at
                ),
                "methodology_version": "canonical-risk/v6",
                "input_hash": _hash_payload(
                    {
                        "nav_points": nav_points,
                        "calculation_frequency_profile": calculation_frequency_profile,
                        "quote_dependency": quote_dependency,
                    }
                ),
                "calculated_at": now,
                "superseded_at": None,
                "is_current": True,
                "volatility": _safe_decimal(volatility),
                "downside_volatility": _safe_decimal(downside_volatility),
                "sharpe_ratio": _safe_decimal(sharpe_ratio),
                "sortino_ratio": _safe_decimal(sortino_ratio),
                "alpha": None,
                "beta": None,
                "r_squared": None,
                "up_capture": None,
                "down_capture": None,
                "tracking_error": None,
                "information_ratio": None,
            },
        )

    def _summary_payload(
        self,
        *,
        instrument,
        nav_selection: dict[str, object],
        quote_window: CanonicalQuoteSeriesResolution,
        endpoint_resolution: CanonicalQuoteResolution | None,
        performance_snapshot,
        risk_snapshot,
        research_rating,
        attributes: dict[str, object],
        taxonomy_context: dict[str, object],
        consumer_profile: ConsumerFreshnessProfile,
        calculation_state: dict[str, object],
        publish_current_metrics: bool,
        market_data_input_watermark_at: datetime | None,
        market_data_input_watermark_status: str,
        market_data_input_watermark_reason_code: str,
        now: datetime,
    ) -> dict[str, object]:
        last_nav_date = (
            nav_selection["points"][-1]["as_of_date"].isoformat()
            if nav_selection["points"]
            else None
        )
        selected_series = _selection_metadata(quote_window)
        date_label = str(selected_series.get("date_label") or "Last Quote Date")
        endpoint_state = str(
            calculation_state.get("current_endpoint_state") or "unavailable"
        )
        has_historical_snapshot = bool(
            performance_snapshot is not None and risk_snapshot is not None
        )
        if endpoint_state == "resolved" and publish_current_metrics:
            freshness_status = (
                "partial"
                if quote_window.coverage_status == "partial"
                else "fresh"
            )
        elif endpoint_state == "resolved" and has_historical_snapshot:
            freshness_status = "partial"
        elif endpoint_state == "stale" and has_historical_snapshot:
            freshness_status = "stale"
        elif endpoint_state == "partial" and has_historical_snapshot:
            freshness_status = "partial"
        else:
            freshness_status = "unavailable"
        reason_codes = list(calculation_state.get("reason_codes") or [])
        if freshness_status != "fresh" and not reason_codes:
            reason_codes = [f"current_endpoint_{endpoint_state}"]
        last_successful_snapshot_at = _last_successful_snapshot_at(
            performance_snapshot, risk_snapshot
        )
        watermark_iso = (
            market_data_input_watermark_at.isoformat().replace("+00:00", "Z")
            if market_data_input_watermark_at is not None
            else None
        )
        return {
            "instrument_id": instrument.instrument_id,
            "fund_name": instrument.instrument_name,
            "ticker_or_isin": instrument.primary_identifier_value or instrument.instrument_id.upper(),
            "management_firm_name": str(instrument.metadata_json.get("management_firm_name") or "") or None,
            "research_rating": serialize_research_rating(research_rating),
            "instrument_attributes": attributes,
            "taxonomy": taxonomy_context,
            "selected_series": selected_series,
            "quote_resolution": _quote_resolution_summary_payload(
                quote_window, consumer_profile
            ),
            "endpoint_resolution": (
                endpoint_resolution.model_dump(mode="json")
                if endpoint_resolution is not None
                else None
            ),
            "nav_snapshot": {
                "nav_basis_type": nav_selection.get("nav_basis_type"),
                "nav_basis_source": nav_selection.get("nav_basis_source"),
                "selected_role": nav_selection.get("selected_role"),
                "selected_metric_family": nav_selection.get("selected_metric_family"),
                "selected_quote_basis": nav_selection.get("selected_quote_basis"),
                "selected_series_type": nav_selection.get("selected_series_type"),
                "selected_series_label": nav_selection.get("selected_series_label"),
                "selected_date_label": nav_selection.get("selected_date_label"),
                "latest_nav": (
                    nav_selection["points"][-1]["value"]
                    if publish_current_metrics
                    and nav_selection.get("nav_basis_type") == "nav"
                    and nav_selection["points"]
                    else None
                ),
                "latest_nav_with_dividend": (
                    nav_selection["points"][-1]["value"]
                    if publish_current_metrics
                    and nav_selection.get("nav_basis_type") == "nav_with_dividend"
                    and nav_selection["points"]
                    else None
                ),
            },
            "key_stats": [
                {"label": date_label, "value": last_nav_date or "—"},
                {
                    "label": "MTD Return",
                    "value": (
                        f"{float(performance_snapshot.return_mtd):.2f}%"
                        if publish_current_metrics
                        and getattr(performance_snapshot, "return_mtd", None) is not None
                        else "—"
                    ),
                },
                {
                    "label": "Annualized Return",
                    "value": (
                        f"{float(performance_snapshot.annualized_return):.2f}%"
                        if publish_current_metrics
                        and getattr(performance_snapshot, "annualized_return", None) is not None
                        else "—"
                    ),
                },
                {
                    "label": "Volatility",
                    "value": (
                        f"{float(risk_snapshot.volatility):.2f}%"
                        if publish_current_metrics
                        and getattr(risk_snapshot, "volatility", None) is not None
                        else "—"
                    ),
                },
            ],
            "freshness": {
                "data_freshness_status": freshness_status,
                "last_recalculated_at": now.isoformat().replace("+00:00", "Z"),
                "last_successful_snapshot_at": (
                    last_successful_snapshot_at.isoformat().replace("+00:00", "Z")
                    if last_successful_snapshot_at is not None
                    else None
                ),
                "staleness_reason_codes": reason_codes,
                "staleness_reason": ", ".join(reason_codes) or None,
                "market_data_input_watermark_at": watermark_iso,
                "market_data_input_watermark_status": (
                    market_data_input_watermark_status
                ),
                "market_data_input_watermark_reason_code": (
                    market_data_input_watermark_reason_code
                ),
                "knowledge_cutoff_at": None,
            },
            "consumer_freshness_profile": consumer_profile.payload(),
            "calculation_state": calculation_state,
            "quick_monitoring_items": [],
            "tabs": DEFAULT_TABS,
        }

    def _peer_comparison_payload(
        self,
        session: Session,
        *,
        instrument_id: str,
        taxonomy_node,
        performance_snapshot,
        risk_snapshot,
        publish_current_metrics: bool,
        target_frequency_profile: dict[str, object],
        target_pair_lineage: dict[str, object] | None,
    ) -> dict[str, object]:
        assigned_path_node_ids = _node_path_node_ids(taxonomy_node)
        if taxonomy_node is None or not assigned_path_node_ids:
            return {
                "status": "missing_taxonomy",
                "taxonomy_code": FUND_TAXONOMY_CODE,
                "assigned_node_id": None,
                "assigned_path": [],
                "peer_node_id": None,
                "peer_path": [],
                "fallback_levels": 0,
                "sample_count": 0,
                "metrics": [],
                "summary": {},
            }

        target_frequency = str(
            target_frequency_profile.get("resolved_frequency") or "unresolved"
        ).strip()
        if (
            not publish_current_metrics
            or target_pair_lineage is None
        ):
            return {
                "status": "target_not_current",
                "taxonomy_code": FUND_TAXONOMY_CODE,
                "assigned_node_id": getattr(taxonomy_node, "node_id", None),
                "assigned_path": _node_path_labels(taxonomy_node),
                "peer_node_id": None,
                "peer_path": [],
                "fallback_levels": 0,
                "sample_count": 0,
                "metrics": [],
                "summary": {},
                "cohort": {
                    "coverage_status": "unavailable",
                    "as_of_date": None,
                    "resolved_frequency": target_frequency or None,
                    "eligible_instrument_count": 0,
                    "excluded_reason_counts": {
                        "target_not_current": 1,
                    },
                },
            }

        nodes = self.taxonomy_repository.list_nodes(session, taxonomy_code=FUND_TAXONOMY_CODE)
        node_by_id = {str(node.node_id): node for node in nodes}
        assignments = self.taxonomy_repository.list_assignments(
            session,
            taxonomy_code=FUND_TAXONOMY_CODE,
        )
        active_peer_instrument_ids = _active_fund_instrument_ids(session)
        assigned_node_by_asset = {
            str(assignment.instrument_id): node_by_id.get(str(assignment.node_id))
            for assignment in assignments
            if assignment.node_id and str(assignment.instrument_id) in active_peer_instrument_ids
        }
        raw_performance_by_asset = {}
        for snapshot in self.snapshot_repository.list_current_performance(
            session,
            instrument_ids=sorted(active_peer_instrument_ids),
        ):
            raw_performance_by_asset.setdefault(str(snapshot.instrument_id), snapshot)
        if performance_snapshot is not None and instrument_id in active_peer_instrument_ids:
            raw_performance_by_asset[str(instrument_id)] = performance_snapshot
        raw_risk_by_asset = {}
        for snapshot in self.snapshot_repository.list_current_risk(
            session,
            instrument_ids=sorted(active_peer_instrument_ids),
        ):
            raw_risk_by_asset.setdefault(str(snapshot.instrument_id), snapshot)
        if risk_snapshot is not None and instrument_id in active_peer_instrument_ids:
            raw_risk_by_asset[str(instrument_id)] = risk_snapshot

        performance_read_model_by_asset = {
            str(read_model.instrument_id): read_model
            for read_model in session.scalars(
                select(InstrumentPerformanceReadModel).where(
                    InstrumentPerformanceReadModel.instrument_id.in_(
                        sorted(active_peer_instrument_ids)
                    )
                )
            ).all()
        }
        target_as_of_date = target_pair_lineage["as_of_date"]
        target_performance_methodology = str(
            target_pair_lineage["performance_methodology_version"]
        )
        target_risk_methodology = str(
            target_pair_lineage["risk_methodology_version"]
        )
        performance_by_asset: dict[str, object] = {}
        risk_by_asset: dict[str, object] = {}
        excluded_reason_counts: dict[str, int] = {}

        def exclude(reason_code: str) -> None:
            excluded_reason_counts[reason_code] = (
                excluded_reason_counts.get(reason_code, 0) + 1
            )

        for candidate_instrument_id in sorted(active_peer_instrument_ids):
            candidate_performance = raw_performance_by_asset.get(
                candidate_instrument_id
            )
            candidate_risk = raw_risk_by_asset.get(candidate_instrument_id)
            candidate_lineage = _snapshot_pair_lineage(
                candidate_performance, candidate_risk
            )
            if candidate_lineage is None:
                exclude("snapshot_pair_unaligned")
                continue
            if candidate_lineage["as_of_date"] != target_as_of_date:
                exclude("as_of_date_mismatch")
                continue
            if (
                candidate_lineage["performance_methodology_version"]
                != target_performance_methodology
                or candidate_lineage["risk_methodology_version"]
                != target_risk_methodology
            ):
                exclude("methodology_mismatch")
                continue

            if candidate_instrument_id == instrument_id:
                performance_by_asset[candidate_instrument_id] = candidate_performance
                risk_by_asset[candidate_instrument_id] = candidate_risk
                continue

            read_model = performance_read_model_by_asset.get(
                candidate_instrument_id
            )
            if read_model is None:
                exclude("missing_performance_read_model")
                continue
            calculation_state = (
                read_model.payload_json.get("calculation_state", {})
                if isinstance(read_model.payload_json, dict)
                else {}
            )
            frequency_profile = (
                read_model.payload_json.get("calculation_frequency_profile", {})
                if isinstance(read_model.payload_json, dict)
                else {}
            )
            if (
                read_model.data_freshness_status != "fresh"
                or not isinstance(calculation_state, dict)
                or calculation_state.get("current_endpoint_state") != "resolved"
                or calculation_state.get("analytics_snapshot_state")
                != "current_aligned"
                or calculation_state.get("historical_calculation_state")
                != "current_endpoint"
            ):
                exclude("endpoint_not_current")
                continue
            if (
                not isinstance(frequency_profile, dict)
                or str(
                    frequency_profile.get("resolved_frequency") or "unresolved"
                ).strip()
                != target_frequency
            ):
                exclude("calculation_frequency_mismatch")
                continue
            if (
                _coerce_utc(read_model.market_data_input_watermark_at)
                != candidate_lineage["market_data_input_watermark_at"]
                or _coerce_utc(read_model.last_recalculated_at)
                != candidate_lineage["calculated_at"]
            ):
                exclude("read_model_lineage_mismatch")
                continue
            performance_by_asset[candidate_instrument_id] = candidate_performance
            risk_by_asset[candidate_instrument_id] = candidate_risk

        selected_peer_node_id = assigned_path_node_ids[-1]
        selected_instrument_ids: list[str] = []
        for candidate_node_id in reversed(assigned_path_node_ids):
            candidate_instrument_ids = [
                candidate_instrument_id
                for candidate_instrument_id, assigned_node in assigned_node_by_asset.items()
                if candidate_node_id in _node_path_node_ids(assigned_node)
                and candidate_instrument_id in performance_by_asset
            ]
            selected_peer_node_id = candidate_node_id
            selected_instrument_ids = sorted(candidate_instrument_ids)
            if len(selected_instrument_ids) >= PEER_METRIC_MIN_SAMPLE:
                break

        if (
            instrument_id not in selected_instrument_ids
            and performance_snapshot is not None
            and instrument_id in active_peer_instrument_ids
        ):
            selected_instrument_ids = sorted([*selected_instrument_ids, instrument_id])

        peer_node = node_by_id.get(selected_peer_node_id) or taxonomy_node
        fallback_levels = max(
            0,
            len(assigned_path_node_ids) - 1 - assigned_path_node_ids.index(selected_peer_node_id),
        )
        cohort_source_fingerprint = _hash_payload(
            {
                "taxonomy_code": FUND_TAXONOMY_CODE,
                "peer_node_id": selected_peer_node_id,
                "as_of_date": target_as_of_date,
                "resolved_frequency": target_frequency,
                "performance_methodology_version": (
                    target_performance_methodology
                ),
                "risk_methodology_version": target_risk_methodology,
                "members": [
                    {
                        "instrument_id": candidate_instrument_id,
                        "assigned_node_id": getattr(
                            assigned_node_by_asset.get(candidate_instrument_id),
                            "node_id",
                            None,
                        ),
                        "performance_input_hash": getattr(
                            performance_by_asset.get(candidate_instrument_id),
                            "input_hash",
                            None,
                        ),
                        "risk_input_hash": getattr(
                            risk_by_asset.get(candidate_instrument_id),
                            "input_hash",
                            None,
                        ),
                    }
                    for candidate_instrument_id in selected_instrument_ids
                ],
            }
        )
        metric_rows: list[dict[str, object]] = []
        for definition in PEER_COMPARISON_METRICS:
            metric_key = str(definition["metric_key"])
            attr = str(definition["attr"])
            source = str(definition["source"])
            direction = str(definition["direction"])
            target_snapshot = performance_snapshot if source == "performance" else risk_snapshot
            target_value = _safe_float(getattr(target_snapshot, attr, None))
            if target_value is None:
                continue

            samples: list[tuple[str, float]] = []
            for candidate_instrument_id in selected_instrument_ids:
                candidate_snapshot = (
                    performance_by_asset.get(candidate_instrument_id)
                    if source == "performance"
                    else risk_by_asset.get(candidate_instrument_id)
                )
                candidate_value = _safe_float(getattr(candidate_snapshot, attr, None))
                if candidate_value is not None:
                    samples.append((candidate_instrument_id, candidate_value))
            if len(samples) < PEER_METRIC_MIN_SAMPLE:
                continue

            ranking = _rank_metric_value(
                value=target_value,
                samples=samples,
                direction=direction,
            )
            peer_values = [
                value
                for candidate_instrument_id, value in samples
                if candidate_instrument_id != instrument_id
            ] or [value for _, value in samples]
            metric_rows.append(
                {
                    "metric_key": metric_key,
                    "label": definition["label"],
                    "domain": definition["domain"],
                    "format": definition["format"],
                    "direction": direction,
                    "value": target_value,
                    "peer_median": _quantile(peer_values, 0.5),
                    "peer_p25": _quantile(peer_values, 0.25),
                    "peer_p75": _quantile(peer_values, 0.75),
                    "peer_sample_count": len(peer_values),
                    "cohort_as_of_date": target_as_of_date.isoformat(),
                    "cohort_frequency": target_frequency,
                    **ranking,
                }
            )

        status = "ready" if metric_rows else "insufficient_data"
        percentile_by_domain: dict[str, list[float | None]] = {}
        for row in metric_rows:
            domain = str(row.get("domain") or "")
            percentile_by_domain.setdefault(domain, []).append(
                _safe_float(row.get("percentile"))
            )
        return_percentile = _mean_optional(percentile_by_domain.get("return", []))
        risk_percentile = _mean_optional(percentile_by_domain.get("risk", []))
        risk_adjusted_percentile = _mean_optional(
            percentile_by_domain.get("risk_adjusted", [])
        )
        return {
            "status": status,
            "taxonomy_code": FUND_TAXONOMY_CODE,
            "assigned_node_id": getattr(taxonomy_node, "node_id", None),
            "assigned_path": _node_path_labels(taxonomy_node),
            "peer_node_id": selected_peer_node_id,
            "peer_path": _node_path_labels(peer_node),
            "fallback_levels": fallback_levels,
            "sample_count": len(selected_instrument_ids),
            "metrics": metric_rows,
            "summary": {
                "return_percentile": return_percentile,
                "risk_percentile": risk_percentile,
                "risk_adjusted_percentile": risk_adjusted_percentile,
                "overall_percentile": _mean_optional(
                    [return_percentile, risk_percentile, risk_adjusted_percentile]
                ),
            },
            "cohort": {
                "coverage_status": (
                    "qualified" if status == "ready" else "insufficient_data"
                ),
                "as_of_date": target_as_of_date.isoformat(),
                "resolved_frequency": target_frequency,
                "performance_methodology_version": (
                    target_performance_methodology
                ),
                "risk_methodology_version": target_risk_methodology,
                "eligible_instrument_count": len(performance_by_asset),
                "selected_instrument_count": len(selected_instrument_ids),
                "member_instrument_ids": selected_instrument_ids,
                "source_fingerprint": cohort_source_fingerprint,
                "excluded_reason_counts": {
                    key: excluded_reason_counts[key]
                    for key in sorted(excluded_reason_counts)
                },
            },
        }

    def _invalidate_changed_peer_cohorts(
        self,
        session: Session,
        *,
        changed_instrument_id: str,
        current_peer_comparison: dict[str, object] | None,
        invalidated_at: datetime,
    ) -> None:
        """Invalidate every persisted peer view touched by a source change.

        Peer ranks are cross-sectional.  Updating one member cannot leave the
        other members marked ready against an older cohort fingerprint.  This
        method runs in the same transaction as the changed member's recalc;
        matching members from the new cohort remain valid, while older cohort
        views and screener attributes are cleared atomically.
        """

        current_peer = (
            current_peer_comparison
            if isinstance(current_peer_comparison, dict)
            else {}
        )
        current_cohort = (
            current_peer.get("cohort")
            if isinstance(current_peer.get("cohort"), dict)
            else {}
        )
        current_fingerprint = str(
            current_cohort.get("source_fingerprint") or ""
        )
        current_peer_node_id = str(current_peer.get("peer_node_id") or "")
        affected_instrument_ids: set[str] = set()
        performance_models = session.scalars(
            select(InstrumentPerformanceReadModel)
        ).all()
        for read_model in performance_models:
            candidate_instrument_id = str(read_model.instrument_id)
            if candidate_instrument_id == changed_instrument_id:
                continue
            payload = (
                dict(read_model.payload_json)
                if isinstance(read_model.payload_json, dict)
                else {}
            )
            stored_peer = payload.get("peer_comparison")
            if not isinstance(stored_peer, dict):
                continue
            stored_cohort = stored_peer.get("cohort")
            if not isinstance(stored_cohort, dict):
                continue
            stored_members = {
                str(member_id)
                for member_id in stored_cohort.get("member_instrument_ids", [])
                if str(member_id).strip()
            }
            stored_peer_node_id = str(stored_peer.get("peer_node_id") or "")
            is_affected = (
                changed_instrument_id in stored_members
                or bool(
                    current_peer_node_id
                    and stored_peer_node_id == current_peer_node_id
                )
            )
            if not is_affected:
                continue
            stored_fingerprint = str(
                stored_cohort.get("source_fingerprint") or ""
            )
            if (
                current_fingerprint
                and stored_fingerprint == current_fingerprint
                and stored_peer.get("status") == "ready"
            ):
                continue

            stale_peer = {
                "status": "cohort_stale",
                "taxonomy_code": stored_peer.get("taxonomy_code"),
                "assigned_node_id": stored_peer.get("assigned_node_id"),
                "assigned_path": list(stored_peer.get("assigned_path") or []),
                "peer_node_id": stored_peer.get("peer_node_id"),
                "peer_path": list(stored_peer.get("peer_path") or []),
                "fallback_levels": stored_peer.get("fallback_levels", 0),
                "sample_count": 0,
                "metrics": [],
                "summary": {},
                "cohort": {
                    **stored_cohort,
                    "coverage_status": "stale",
                    "invalidated_at": invalidated_at.isoformat().replace(
                        "+00:00", "Z"
                    ),
                    "invalidation_reason": "peer_source_membership_or_snapshot_changed",
                    "changed_instrument_id": changed_instrument_id,
                },
            }
            payload["peer_comparison"] = stale_peer
            payload["ranking"] = None
            trailing_returns = payload.get("trailing_returns")
            if isinstance(trailing_returns, list):
                payload["trailing_returns"] = [
                    {
                        **row,
                        "category_nav": None,
                    }
                    if isinstance(row, dict)
                    else row
                    for row in trailing_returns
                ]
            read_model.payload_json = payload
            affected_instrument_ids.add(candidate_instrument_id)

            risk_read_model = session.get(
                InstrumentRiskReadModel, candidate_instrument_id
            )
            if risk_read_model is not None and isinstance(
                risk_read_model.payload_json, dict
            ):
                risk_payload = dict(risk_read_model.payload_json)
                risk_overview = risk_payload.get("risk_overview")
                if isinstance(risk_overview, dict):
                    risk_payload["risk_overview"] = {
                        **risk_overview,
                        "risk_vs_category": None,
                        "return_vs_category": None,
                    }
                risk_metrics = risk_payload.get("risk_metrics")
                if isinstance(risk_metrics, list):
                    risk_payload["risk_metrics"] = [
                        {**row, "category": None}
                        if isinstance(row, dict)
                        else row
                        for row in risk_metrics
                    ]
                risk_read_model.payload_json = risk_payload

        if affected_instrument_ids:
            for row in session.scalars(
                select(WatchlistRowReadModel).where(
                    WatchlistRowReadModel.instrument_id.in_(
                        sorted(affected_instrument_ids)
                    )
                )
            ).all():
                attributes = dict(row.attributes_json or {})
                row.attributes_json = {
                    key: value
                    for key, value in attributes.items()
                    if key not in PEER_WATCHLIST_MATERIALIZED_ATTRIBUTE_KEYS
                }
        session.flush()

    def _performance_payload(
        self,
        *,
        performance_snapshot,
        peer_comparison: dict[str, object] | None,
        nav_points: list[dict[str, Any]],
        calculation_frequency_profile: dict[str, object],
    ) -> dict[str, object]:
        trailing_returns: list[dict[str, Any]] = []
        annual_returns = _compute_calendar_year_returns(nav_points)
        growth_chart_series: list[dict[str, Any]] = []
        peer_metrics_by_key = {
            str(row.get("metric_key")): row
            for row in (peer_comparison or {}).get("metrics", [])
            if isinstance(row, dict)
        }
        category_value_by_window = {
            "1W": _safe_float(peer_metrics_by_key.get("return_1w", {}).get("peer_median")),
            "MTD": _safe_float(peer_metrics_by_key.get("return_mtd", {}).get("peer_median")),
            "YTD": _safe_float(peer_metrics_by_key.get("return_ytd", {}).get("peer_median")),
            "3M": _safe_float(peer_metrics_by_key.get("return_3m", {}).get("peer_median")),
            "6M": _safe_float(peer_metrics_by_key.get("return_6m", {}).get("peer_median")),
            "1Y": _safe_float(peer_metrics_by_key.get("return_1y", {}).get("peer_median")),
            "3Y": _safe_float(peer_metrics_by_key.get("return_3y_annualized", {}).get("peer_median")),
            "5Y": _safe_float(peer_metrics_by_key.get("return_5y_annualized", {}).get("peer_median")),
            "Ann.": _safe_float(peer_metrics_by_key.get("annualized_return", {}).get("peer_median")),
        }
        if performance_snapshot is not None:
            trailing_returns = [
                {"window": "1W", "investment_nav": _safe_float(performance_snapshot.return_1w), "category_nav": category_value_by_window["1W"], "index_nav": None},
                {"window": "MTD", "investment_nav": _safe_float(performance_snapshot.return_mtd), "category_nav": category_value_by_window["MTD"], "index_nav": None},
                {"window": "YTD", "investment_nav": _safe_float(performance_snapshot.return_ytd), "category_nav": category_value_by_window["YTD"], "index_nav": None},
                {"window": "3M", "investment_nav": _safe_float(performance_snapshot.return_3m), "category_nav": category_value_by_window["3M"], "index_nav": None},
                {"window": "6M", "investment_nav": _safe_float(performance_snapshot.return_6m), "category_nav": category_value_by_window["6M"], "index_nav": None},
                {"window": "1Y", "investment_nav": _safe_float(performance_snapshot.return_1y), "category_nav": category_value_by_window["1Y"], "index_nav": None},
                {"window": "3Y", "investment_nav": _safe_float(performance_snapshot.return_3y_annualized), "category_nav": category_value_by_window["3Y"], "index_nav": None},
                {"window": "5Y", "investment_nav": _safe_float(performance_snapshot.return_5y_annualized), "category_nav": category_value_by_window["5Y"], "index_nav": None},
                {"window": "Ann.", "investment_nav": _safe_float(performance_snapshot.annualized_return), "category_nav": category_value_by_window["Ann."], "index_nav": None},
            ]
            trailing_returns = [
                row for row in trailing_returns if any(row[key] is not None for key in ("investment_nav", "category_nav", "index_nav"))
            ]
        growth_of_100 = _growth_of_100(nav_points)
        if growth_of_100 is not None:
            growth_chart_series = [{"name": "Growth of 100", "value": growth_of_100}]
        return {
            "growth_chart_series": growth_chart_series,
            "annual_returns": annual_returns,
            "trailing_returns": trailing_returns,
            "ranking": _primary_peer_ranking(peer_comparison),
            "peer_comparison": peer_comparison,
            "calculation_frequency_profile": calculation_frequency_profile,
            "snapshot_metadata": _snapshot_metadata(performance_snapshot),
        }

    def _risk_payload(
        self,
        *,
        risk_snapshot,
        performance_snapshot,
        peer_comparison: dict[str, object] | None,
        nav_points: list[dict[str, Any]],
        calculation_frequency_profile: dict[str, object],
    ) -> dict[str, object]:
        volatility = _safe_float(getattr(risk_snapshot, "volatility", None))
        annualized_return = _safe_float(getattr(performance_snapshot, "annualized_return", None))
        drawdown_summary = _compute_drawdown_summary(nav_points)
        current_drawdown = _current_drawdown(nav_points)
        current_watch = _build_current_risk_watch(nav_points, drawdown_summary)
        risk_structure = _build_risk_structure(nav_points, drawdown_summary, current_watch)
        change_monitor = _build_risk_change_monitor(nav_points, drawdown_summary, current_watch)
        peer_metrics_by_key = {
            str(row.get("metric_key")): row
            for row in (peer_comparison or {}).get("metrics", [])
            if isinstance(row, dict)
        }
        peer_summary = peer_comparison.get("summary") if isinstance(peer_comparison, dict) else {}
        risk_percentile = (
            _safe_float(peer_summary.get("risk_percentile")) if isinstance(peer_summary, dict) else None
        )
        return_percentile = (
            _safe_float(peer_summary.get("return_percentile")) if isinstance(peer_summary, dict) else None
        )
        return {
            "risk_overview": {
                "risk_level": _risk_level_label(volatility),
                "risk_vs_category": risk_percentile,
                "return_vs_category": return_percentile,
            }
            if risk_snapshot is not None or performance_snapshot is not None
            else None,
            "scatter_points": (
                [
                    {
                        "name": "Investment",
                        "return": annualized_return,
                        "volatility": volatility,
                    }
                ]
                if annualized_return is not None and volatility is not None
                else []
            ),
            "risk_metrics": [
                {"metric": "volatility", "investment": volatility, "category": _safe_float(peer_metrics_by_key.get("volatility", {}).get("peer_median")), "index": None},
                {"metric": "sharpe_ratio", "investment": _safe_float(getattr(risk_snapshot, "sharpe_ratio", None)), "category": _safe_float(peer_metrics_by_key.get("sharpe_ratio", {}).get("peer_median")), "index": None},
                {"metric": "max_drawdown", "investment": _safe_float(getattr(performance_snapshot, "max_drawdown", None)), "category": _safe_float(peer_metrics_by_key.get("max_drawdown", {}).get("peer_median")), "index": None},
                {"metric": "calmar_ratio", "investment": _safe_float(getattr(performance_snapshot, "calmar", None)), "category": _safe_float(peer_metrics_by_key.get("calmar", {}).get("peer_median")), "index": None},
                {"metric": "annualized_return", "investment": annualized_return, "category": _safe_float(peer_metrics_by_key.get("annualized_return", {}).get("peer_median")), "index": None},
            ],
            "drawdown_summary": drawdown_summary,
            "current_drawdown": current_drawdown,
            "risk_structure": risk_structure,
            "current_watch": current_watch,
            "change_monitor": change_monitor,
            "calculation_frequency_profile": calculation_frequency_profile,
            "snapshot_metadata": _snapshot_metadata(risk_snapshot),
        }

    def _refresh_watchlist_rows(
        self,
        session: Session,
        *,
        instrument,
        summary_payload: dict[str, object],
        attributes: dict[str, object],
        peer_comparison: dict[str, object] | None,
        performance_snapshot,
        risk_snapshot,
        research_rating,
        publish_current_metrics: bool,
        market_data_input_watermark_at: datetime | None,
        now: datetime,
    ) -> None:
        existing_rows = self.read_model_repository.list_watchlist_rows_for_instrument(session, instrument.instrument_id)
        if not existing_rows:
            return
        freshness = summary_payload.get("freshness", {})
        row_attributes = {**attributes, **_peer_watchlist_attributes(peer_comparison)}
        for row in existing_rows:
            self.read_model_repository.upsert_watchlist_row(
                session,
                watchlist_id=row.watchlist_id,
                instrument_id=instrument.instrument_id,
                data=build_watchlist_row_materialization(
                    watchlist_id=row.watchlist_id,
                    instrument_id=instrument.instrument_id,
                    instrument_type=instrument.instrument_type,
                    source_row=row,
                    display_name=instrument.instrument_name,
                    share_class=None,
                    ticker_or_isin=instrument.primary_identifier_value,
                    management_firm_name=str(instrument.metadata_json.get("management_firm_name") or "") or None,
                    research_rating=getattr(research_rating, "rating_value", None),
                    research_rating_as_of=getattr(research_rating, "as_of_date", None),
                    research_rating_updated_at=getattr(research_rating, "created_at", None),
                    attributes=row_attributes,
                    freshness_status=str(freshness.get("data_freshness_status") or "fresh"),
                    market_data_input_watermark_at=(
                        market_data_input_watermark_at
                    ),
                    last_recalculated_at=now,
                    last_successful_snapshot_at=(
                        _last_successful_snapshot_at(
                            performance_snapshot, risk_snapshot
                        )
                    ),
                    staleness_reason=freshness.get("staleness_reason"),
                )
                | {
                    "return_ytd": getattr(performance_snapshot, "return_ytd", None) if publish_current_metrics else None,
                    "return_1w": getattr(performance_snapshot, "return_1w", None) if publish_current_metrics else None,
                    "return_mtd": getattr(performance_snapshot, "return_mtd", None) if publish_current_metrics else None,
                    "return_1m": getattr(performance_snapshot, "return_1m", None) if publish_current_metrics else None,
                    "return_1y": getattr(performance_snapshot, "return_1y", None) if publish_current_metrics else None,
                    "annualized_return": getattr(performance_snapshot, "annualized_return", None) if publish_current_metrics else None,
                    "return_3y": getattr(performance_snapshot, "return_3y_annualized", None) if publish_current_metrics else None,
                    "return_5y": getattr(performance_snapshot, "return_5y_annualized", None) if publish_current_metrics else None,
                    "max_drawdown": getattr(performance_snapshot, "max_drawdown", None) if publish_current_metrics else None,
                    "volatility": getattr(risk_snapshot, "volatility", None) if publish_current_metrics else None,
                    "sharpe_ratio": getattr(risk_snapshot, "sharpe_ratio", None) if publish_current_metrics else None,
                    "last_nav_date": (
                        date.fromisoformat(str(summary_payload["key_stats"][0]["value"]))
                        if summary_payload.get("key_stats")
                        and str(summary_payload["key_stats"][0].get("value") or "") != "—"
                        else None
                    ),
                },
            )
