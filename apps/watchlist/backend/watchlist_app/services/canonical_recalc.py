from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha1
import json
import math
import statistics
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from watchlist_app.db.models.assets import AssetDetail
from watchlist_app.db.models.read_models import (
    AssetChartReadModel,
    AssetExposureHoldingsReadModel,
    AssetExposureReadModel,
    AssetPerformanceReadModel,
    AssetRatingReadModel,
    AssetRiskReadModel,
    AssetSummaryReadModel,
)
from watchlist_app.repositories.sqlalchemy.assets import SQLAlchemyAssetRepository
from watchlist_app.repositories.sqlalchemy.facts import SQLAlchemyFactsRepository
from watchlist_app.repositories.sqlalchemy.instrument_attributes import (
    SQLAlchemyInstrumentAttributeRepository,
)
from watchlist_app.repositories.sqlalchemy.manual_profiles import (
    SQLAlchemyAssetManualProfileRepository,
)
from watchlist_app.repositories.sqlalchemy.read_models import SQLAlchemyReadModelRepository
from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
from watchlist_app.repositories.sqlalchemy.snapshots import SQLAlchemySnapshotRepository
from watchlist_app.repositories.sqlalchemy.taxonomy import SQLAlchemyTaxonomyRepository
from watchlist_app.reference_data.fund_taxonomy import FUND_TAXONOMY_CODE
from watchlist_app.services.fund_taxonomy import (
    build_taxonomy_context,
    merge_taxonomy_attributes,
)
from watchlist_app.services.read_models import (
    build_watchlist_row_materialization,
    collapse_latest_attribute_values,
    serialize_payload,
)
from watchlist_app.services.recalc_job_ids import make_recalc_dedupe_key, make_recalc_job_id
from watchlist_app.services.shared_instrument_registry import (
    get_shared_instrument,
    list_shared_instruments,
)


DEFAULT_TABS = [
    "overview",
    "quote",
    "performance",
    "risk",
    "price",
    "exposure",
    "people",
    "strategy",
    "documents",
    "research",
    "monitoring",
]

RETURN_NAV_BASIS_PRIORITY = ("nav_with_dividend",)
QUOTE_NAV_BASIS_PRIORITY = ("nav_with_dividend", "nav")
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
        "nav_basis_preference": "auto",
        "default_benchmark_asset_id": None,
        "peer_baseline_asset_ids": [],
    }


def _normalize_nav_settings(payload: dict[str, Any] | None) -> dict[str, Any]:
    normalized = {
        **_default_nav_settings(),
        **(payload or {}),
    }
    if normalized.get("nav_basis_preference") not in {"auto", "nav_with_dividend"}:
        normalized["nav_basis_preference"] = "auto"
    normalized["default_benchmark_asset_id"] = (
        str(normalized.get("default_benchmark_asset_id")).strip() or None
        if normalized.get("default_benchmark_asset_id") is not None
        else None
    )
    normalized["peer_baseline_asset_ids"] = [
        str(value).strip()
        for value in (normalized.get("peer_baseline_asset_ids") or [])
        if str(value).strip()
    ]
    return normalized


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _coerce_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


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


def _active_fund_asset_ids(session: Session) -> set[str]:
    local_active_asset_ids = {
        str(asset_id)
        for asset_id in session.scalars(
            select(AssetDetail.asset_id).where(
                AssetDetail.asset_type == "fund",
                AssetDetail.is_active.is_(True),
            )
        ).all()
    }
    shared_active_asset_ids = {
        str(item.get("asset_id"))
        for item in list_shared_instruments(asset_type="fund", limit=None)
        if str(item.get("asset_id") or "").strip()
    }
    return local_active_asset_ids & shared_active_asset_ids


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
    return sha1(serialized.encode("utf-8")).hexdigest()


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
    if base_value <= 0:
        return None
    raw = latest_value / base_value - 1
    if days > 366:
        return (pow(1 + raw, 365.25 / max(days, 1)) - 1) * 100
    return raw * 100


def _annualized_return(latest_value: float, base_value: float, days: int) -> float | None:
    if latest_value <= 0 or base_value <= 0 or days <= 0:
        return None
    return (pow(latest_value / base_value, 365.25 / max(days, 1)) - 1) * 100


def _value_at_or_before(nav_points: list[dict[str, Any]], target_date: date) -> dict[str, Any] | None:
    candidates = [point for point in nav_points if point["as_of_date"] <= target_date]
    return candidates[-1] if candidates else None


def _value_before(nav_points: list[dict[str, Any]], target_date: date) -> dict[str, Any] | None:
    candidates = [point for point in nav_points if point["as_of_date"] < target_date]
    return candidates[-1] if candidates else None


def _compute_drawdown(nav_points: list[dict[str, Any]]) -> float | None:
    if len(nav_points) < 2:
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
    if len(nav_points) < 2:
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
    if len(nav_points) < 2:
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


def _compute_volatility(nav_points: list[dict[str, Any]]) -> float | None:
    returns = _periodic_returns(nav_points)
    if len(returns) < 2:
        return None
    periods_per_year = _annualization_periods_per_year(nav_points, len(returns))
    if periods_per_year is None:
        return None
    return statistics.stdev(returns) * math.sqrt(periods_per_year) * 100


def _compute_sharpe(nav_points: list[dict[str, Any]]) -> float | None:
    returns = _periodic_returns(nav_points)
    if len(returns) < 2:
        return None
    stdev = statistics.stdev(returns)
    periods_per_year = _annualization_periods_per_year(nav_points, len(returns))
    if stdev == 0 or periods_per_year is None:
        return None
    mean_return = statistics.fmean(returns)
    return (mean_return / stdev) * math.sqrt(periods_per_year)


def _periodic_returns(nav_points: list[dict[str, Any]]) -> list[float]:
    returns: list[float] = []
    for previous, current in zip(nav_points, nav_points[1:]):
        if previous["value"] <= 0:
            continue
        returns.append(current["value"] / previous["value"] - 1)
    return returns


def _annualization_periods_per_year(nav_points: list[dict[str, Any]], return_count: int) -> float | None:
    if len(nav_points) < 2 or return_count < 1:
        return None
    elapsed_days = (nav_points[-1]["as_of_date"] - nav_points[0]["as_of_date"]).days
    if elapsed_days <= 0:
        return None
    return return_count / elapsed_days * 365.25


def _compute_downside_deviation(nav_points: list[dict[str, Any]]) -> float | None:
    periodic_returns = _periodic_returns(nav_points)
    downside_returns = [value for value in periodic_returns if value < 0]
    if len(periodic_returns) < 2 or not downside_returns:
        return None
    periods_per_year = _annualization_periods_per_year(nav_points, len(periodic_returns))
    if periods_per_year is None:
        return None
    downside_variance = sum(value ** 2 for value in downside_returns) / len(downside_returns)
    return math.sqrt(max(downside_variance, 0)) * math.sqrt(periods_per_year) * 100


def _compute_sortino(nav_points: list[dict[str, Any]]) -> float | None:
    periodic_returns = _periodic_returns(nav_points)
    downside_returns = [value for value in periodic_returns if value < 0]
    if len(periodic_returns) < 2 or not downside_returns:
        return None
    periods_per_year = _annualization_periods_per_year(nav_points, len(periodic_returns))
    if periods_per_year is None:
        return None
    downside_variance = sum(value ** 2 for value in downside_returns) / len(downside_returns)
    downside_deviation = math.sqrt(max(downside_variance, 0))
    if downside_deviation == 0:
        return None
    mean_return = statistics.fmean(periodic_returns)
    return (mean_return / downside_deviation) * math.sqrt(periods_per_year)


def _monthly_close_points(nav_points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    monthly: dict[tuple[int, int], dict[str, Any]] = {}
    for point in nav_points:
        monthly[(point["as_of_date"].year, point["as_of_date"].month)] = point
    return [monthly[key] for key in sorted(monthly)]


def _monthly_return_series(nav_points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    closes = _monthly_close_points(nav_points)
    returns: list[dict[str, Any]] = []
    for previous, current in zip(closes, closes[1:]):
        if previous["value"] <= 0:
            continue
        returns.append({
            "as_of_date": current["as_of_date"],
            "value": (current["value"] / previous["value"] - 1) * 100,
        })
    return returns


def _monthly_drawdown_series(nav_points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(nav_points) < 2:
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


def _trailing_negative_month_count(monthly_returns: list[dict[str, Any]]) -> int:
    count = 0
    for row in reversed(monthly_returns):
        if row["value"] >= 0:
            break
        count += 1
    return count


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
    return {
        "as_of_date": snapshot.as_of_date.isoformat(),
        "methodology_version": snapshot.methodology_version,
        "source_cutoff_at": _coerce_utc(snapshot.source_cutoff_at).isoformat().replace("+00:00", "Z"),
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


def _group_shared_nav_rows(market_data: list[dict[str, object]]) -> list[dict[str, Any]]:
    quote_basis_map = {
        "official_nav": "nav",
        "nav": "nav",
        "unit_nav": "nav",
        "net_asset_value": "nav",
        "close": "nav",
        "last": "nav",
        "total_return_nav": "nav_with_dividend",
        "nav_with_dividend": "nav_with_dividend",
        "cumulative_nav": "nav_with_dividend",
        "accumulated_nav": "nav_with_dividend",
        "cum_nav": "nav_with_dividend",
        "dividend_adjusted_nav": "nav_with_dividend",
        "reinvested_nav": "nav_with_dividend",
        "adjusted_close": "nav_with_dividend",
    }
    grouped: dict[date, dict[str, Any]] = {}
    for item in market_data:
        if not isinstance(item, dict):
            continue
        quote_basis = str(item.get("quote_basis") or "").strip().lower()
        target_key = quote_basis_map.get(quote_basis)
        value = _safe_decimal(item.get("value"))
        as_of_date_raw = str(item.get("as_of_date") or "").strip()
        if target_key is None or value is None or not as_of_date_raw:
            continue
        try:
            as_of_date = date.fromisoformat(as_of_date_raw)
        except ValueError:
            continue
        row = grouped.setdefault(
            as_of_date,
            {
                "as_of_date": as_of_date,
                "currency": str(item.get("currency") or "USD"),
                "frequency": None,
                "adopted_at": None,
                "nav": None,
                "nav_with_dividend": None,
            },
        )
        row[target_key] = value
    return [grouped[key] for key in sorted(grouped)]


def _group_local_nav_rows(nav_facts) -> list[dict[str, Any]]:
    grouped: dict[date, dict[str, Any]] = {}
    for item in nav_facts:
        row = grouped.setdefault(
            item.as_of_date,
            {
                "as_of_date": item.as_of_date,
                "currency": item.currency,
                "frequency": item.frequency,
                "adopted_at": item.adopted_at,
                "nav": None,
                "nav_with_dividend": None,
            },
        )
        if item.nav_type == "nav":
            row["nav"] = item.value
        elif item.nav_type == "nav_with_dividend":
            row["nav_with_dividend"] = item.value
    return [grouped[key] for key in sorted(grouped)]


def _select_nav_basis_rows(
    rows: list[dict[str, Any]],
    *,
    preference: str,
    allow_ordinary_nav: bool = False,
) -> dict[str, object]:
    normalized_preference = (preference or "auto").strip().lower()
    if normalized_preference == "nav_with_dividend":
        basis_order = (normalized_preference,)
    elif normalized_preference == "nav":
        basis_order = ("nav",) if allow_ordinary_nav else ()
    else:
        basis_order = QUOTE_NAV_BASIS_PRIORITY if allow_ordinary_nav else RETURN_NAV_BASIS_PRIORITY

    for basis in basis_order:
        points = [
            {
                "as_of_date": row["as_of_date"],
                "value": float(row[basis]),
                "currency": row["currency"],
                "adopted_at": row.get("adopted_at"),
            }
            for row in rows
            if row.get(basis) is not None
        ]
        if points:
            return {
                "nav_basis_type": basis,
                "nav_basis_source": "shared" if any(row.get("adopted_at") is None for row in rows) else "local",
                "nav_basis_status": "ready",
                "points": points,
                "rows": rows,
            }

    return {
        "nav_basis_type": None,
        "nav_basis_source": "unavailable",
        "nav_basis_status": "unavailable",
        "points": [],
        "rows": rows,
    }


def _build_chart_payload(asset_id: str, nav_points: list[dict[str, Any]], currency: str) -> dict[str, object]:
    return {
        "asset_id": asset_id,
        "base_series_type": "nav",
        "currency": currency,
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
                "name": f"{asset_id.upper()} NAV",
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
        self.asset_repository = SQLAlchemyAssetRepository()
        self.facts_repository = SQLAlchemyFactsRepository()
        self.attribute_repository = SQLAlchemyInstrumentAttributeRepository()
        self.manual_profile_repository = SQLAlchemyAssetManualProfileRepository()
        self.read_model_repository = SQLAlchemyReadModelRepository()
        self.recalc_repository = SQLAlchemyRecalcJobRepository()
        self.snapshot_repository = SQLAlchemySnapshotRepository()
        self.taxonomy_repository = SQLAlchemyTaxonomyRepository()

    def build_nav_series_payload(
        self,
        session: Session,
        *,
        asset_id: str,
    ) -> dict[str, object]:
        manual_profile = self.manual_profile_repository.get(session, asset_id)
        nav_settings = _normalize_nav_settings(
            manual_profile.nav_settings_json if manual_profile is not None else None
        )
        shared_instrument = get_shared_instrument(asset_id)
        nav_rows = _group_shared_nav_rows(list(shared_instrument.get("market_data", []))) if isinstance(shared_instrument, dict) else []
        if not nav_rows:
            nav_rows = _group_local_nav_rows(
                self.facts_repository.list_nav_facts(
                    session,
                    asset_id=asset_id,
                    nav_type=None,
                    primary_only=True,
                )
            )
        selection = _select_nav_basis_rows(
            nav_rows,
            preference=str(nav_settings.get("nav_basis_preference", "auto")),
        )
        return {
            "asset_id": asset_id,
            "count": len(selection["rows"]),
            "nav_basis_preference": str(nav_settings.get("nav_basis_preference", "auto")),
            "nav_basis_type": selection["nav_basis_type"],
            "nav_basis_source": selection["nav_basis_source"],
            "nav_basis_status": selection["nav_basis_status"],
            "compare_settings": {
                "default_benchmark_asset_id": nav_settings.get("default_benchmark_asset_id"),
                "peer_asset_ids": list(nav_settings.get("peer_baseline_asset_ids") or []),
            },
            "rows": [
                {
                    "date": row["as_of_date"].isoformat(),
                    "nav": float(row["nav"]) if row["nav"] is not None else None,
                    "nav_with_dividend": (
                        float(row["nav_with_dividend"])
                        if row["nav_with_dividend"] is not None
                        else None
                    ),
                    "currency": row["currency"],
                    "frequency": row["frequency"],
                    "adopted_at": (
                        _coerce_utc(row.get("adopted_at")).isoformat().replace("+00:00", "Z")
                        if row.get("adopted_at") is not None
                        else None
                    ),
                }
                for row in selection["rows"]
            ],
            "series": [
                {
                    "date": point["as_of_date"].isoformat(),
                    "nav": round(point["value"], 8),
                }
                for point in selection["points"]
            ],
        }

    def ingest_asset_holding_snapshot(
        self,
        session: Session,
        *,
        asset_id: str,
        as_of_date: date,
        source_cutoff_at: datetime,
        methodology_version: str,
        source_record_id: str | None,
        positions: list[dict[str, Any]],
        auto_recalculate: bool,
    ) -> dict[str, object]:
        payload_hash = _hash_payload(
            {
                "asset_id": asset_id,
                "as_of_date": as_of_date.isoformat(),
                "source_cutoff_at": source_cutoff_at.isoformat(),
                "positions": positions,
            }
        )
        record = self.facts_repository.replace_current_holding_snapshot(
            session,
            holding_snapshot_id=f"holding:{asset_id}:{as_of_date.isoformat()}",
            asset_id=asset_id,
            as_of_date=as_of_date,
            source_cutoff_at=source_cutoff_at,
            methodology_version=methodology_version,
            input_hash=payload_hash,
            source_record_id=source_record_id,
            positions=positions,
        )

        execution = None
        if auto_recalculate:
            execution = self.execute_recalc(
                session,
                asset_id=asset_id,
                job_type="exposure",
                trigger_type="fact_adopted",
                trigger_ref_type="holding_snapshot",
                trigger_ref_id=record.holding_snapshot_id,
            )
        return {
            "asset_id": asset_id,
            "holding_snapshot_id": record.holding_snapshot_id,
            "position_count": len(record.positions),
            "as_of_date": record.as_of_date.isoformat(),
            "auto_recalculated": execution is not None,
            "execution": execution,
        }

    def execute_recalc(
        self,
        session: Session,
        *,
        asset_id: str,
        job_type: str,
        trigger_type: str,
        trigger_ref_type: str | None,
        trigger_ref_id: str | None,
        commit: bool = False,
    ) -> dict[str, object]:
        record = self.recalc_repository.create(
            session,
            recalc_job_id=make_recalc_job_id(),
            job_type=job_type,
            asset_id=asset_id,
            trigger_type=trigger_type,
            trigger_ref_type=trigger_ref_type,
            trigger_ref_id=trigger_ref_id,
            job_status="queued",
            priority=100 if job_type == "all" else 90,
            dedupe_key=make_recalc_dedupe_key(
                job_type=job_type,
                asset_id=asset_id,
                trigger_type=trigger_type,
                trigger_ref_type=trigger_ref_type,
                trigger_ref_id=trigger_ref_id,
            ),
            payload_json={"requested_by": trigger_type},
        )
        self.recalc_repository.mark_running(session, record)
        try:
            result = self._execute_recalc_job(session, asset_id=asset_id, job_type=job_type)
            self.recalc_repository.mark_completed(session, record, payload_json=result)
            if commit:
                session.commit()
            else:
                session.flush()
            return {
                "recalc_job_id": record.recalc_job_id,
                "job_status": "completed",
                "result": result,
            }
        except Exception as exc:
            self.recalc_repository.mark_failed(session, record, error_message=str(exc))
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
        try:
            result = self._execute_recalc_job(
                session,
                asset_id=record.asset_id,
                job_type=record.job_type,
            )
            self.recalc_repository.mark_completed(session, record, payload_json=result)
            if commit:
                session.commit()
            else:
                session.flush()
            return {
                "recalc_job_id": record.recalc_job_id,
                "job_status": "completed",
                "result": result,
            }
        except Exception as exc:
            self.recalc_repository.mark_failed(session, record, error_message=str(exc))
            if commit:
                session.commit()
            else:
                session.flush()
            raise

    def _execute_recalc_job(
        self,
        session: Session,
        *,
        asset_id: str,
        job_type: str,
    ) -> dict[str, object]:
        asset = self.asset_repository.get(session, asset_id)
        if asset is None:
            raise ValueError(f"Asset not found: {asset_id}")

        shared_instrument = get_shared_instrument(asset_id)
        if (
            shared_instrument is not None
            and str(shared_instrument.get("asset_type") or "").strip().lower() == "fund"
        ):
            asset = self.asset_repository.upsert_from_shared_instrument(
                session,
                shared_instrument=shared_instrument,
                detail_view_type=asset.detail_view_type or "fund",
            )

        now = _utcnow()
        nav_facts = self.facts_repository.list_nav_facts(
            session,
            asset_id=asset_id,
            nav_type=None,
            primary_only=True,
        )
        manual_profile = self.manual_profile_repository.get(session, asset_id)
        nav_settings = _normalize_nav_settings(
            manual_profile.nav_settings_json if manual_profile is not None else None
        )
        nav_rows = (
            _group_shared_nav_rows(list((shared_instrument or {}).get("market_data", [])))
            or _group_local_nav_rows(nav_facts)
        )
        nav_selection = _select_nav_basis_rows(
            nav_rows,
            preference=str(nav_settings.get("nav_basis_preference", "auto")),
        )
        quote_selection = _select_nav_basis_rows(
            nav_rows,
            preference=str(nav_settings.get("nav_basis_preference", "auto")),
            allow_ordinary_nav=True,
        )
        holding_snapshot = self.facts_repository.get_current_holding_snapshot(
            session,
            asset_id=asset_id,
        )
        raw_attributes = collapse_latest_attribute_values(
            self.attribute_repository.get_values_for_asset(session, asset_id)
        )
        assignment = self.taxonomy_repository.get_assignment(session, asset_id=asset_id)
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
        current_drawdown = _current_drawdown(nav_selection["points"])
        if current_drawdown is not None:
            watchlist_attributes["current_drawdown"] = current_drawdown

        performance_snapshot = self.snapshot_repository.get_current_performance(session, asset_id)
        risk_snapshot = self.snapshot_repository.get_current_risk(session, asset_id)
        exposure_snapshot = self.snapshot_repository.get_current_exposure(session, asset_id)
        score_snapshot = self.snapshot_repository.get_current_score(session, asset_id)

        if job_type in {"performance", "all"}:
            if nav_selection["points"]:
                performance_snapshot = self._replace_performance_snapshot(
                    session,
                    asset_id=asset_id,
                    nav_points=nav_selection["points"],
                    now=now,
                )
                risk_snapshot = self._replace_risk_snapshot(
                    session,
                    asset_id=asset_id,
                    nav_points=nav_selection["points"],
                    now=now,
                )
            else:
                self.snapshot_repository.clear_performance(session, asset_id=asset_id)
                self.snapshot_repository.clear_risk(session, asset_id=asset_id)
                performance_snapshot = None
                risk_snapshot = None

        if job_type in {"exposure", "all"} and holding_snapshot is not None:
            exposure_snapshot = self._replace_exposure_snapshot(
                session,
                asset_id=asset_id,
                holding_snapshot=holding_snapshot,
                now=now,
            )

        if job_type in {"ratings", "performance", "exposure", "all"}:
            score_snapshot = self._replace_score_snapshot(
                session,
                asset_id=asset_id,
                performance_snapshot=performance_snapshot,
                risk_snapshot=risk_snapshot,
                exposure_snapshot=exposure_snapshot,
                now=now,
            )

        summary_payload = self._summary_payload(
            asset=asset,
            nav_selection=nav_selection,
            performance_snapshot=performance_snapshot,
            risk_snapshot=risk_snapshot,
            exposure_snapshot=exposure_snapshot,
            score_snapshot=score_snapshot,
            attributes=raw_attributes,
            taxonomy_context=taxonomy_context,
            now=now,
        )
        chart_payload = _build_chart_payload(
            asset_id,
            quote_selection["points"],
            quote_selection["points"][-1]["currency"] if quote_selection["points"] else "USD",
        )
        peer_comparison = self._peer_comparison_payload(
            session,
            asset_id=asset_id,
            taxonomy_node=taxonomy_node,
            performance_snapshot=performance_snapshot,
            risk_snapshot=risk_snapshot,
        )
        performance_payload = self._performance_payload(
            performance_snapshot=performance_snapshot,
            peer_comparison=peer_comparison,
            nav_points=nav_selection["points"],
        )
        risk_payload = self._risk_payload(
            risk_snapshot=risk_snapshot,
            performance_snapshot=performance_snapshot,
            peer_comparison=peer_comparison,
            nav_points=nav_selection["points"],
        )
        exposure_payload = self._exposure_payload(exposure_snapshot)
        holdings_payload = self._holdings_payload(holding_snapshot)
        rating_payload = self._rating_payload(score_snapshot)

        for model_class, payload in (
            (AssetSummaryReadModel, summary_payload),
            (AssetChartReadModel, chart_payload),
            (AssetPerformanceReadModel, performance_payload),
            (AssetRiskReadModel, risk_payload),
            (AssetExposureReadModel, exposure_payload),
            (AssetExposureHoldingsReadModel, holdings_payload),
            (AssetRatingReadModel, rating_payload),
        ):
            source_cutoff_at = (
                _coerce_utc(getattr(exposure_snapshot, "source_cutoff_at", None))
                or _coerce_utc(getattr(performance_snapshot, "source_cutoff_at", None))
                or now
            )
            self.read_model_repository.upsert_payload_read_model(
                session,
                model_class=model_class,
                asset_id=asset_id,
                payload_json=payload,
                data_freshness_status=summary_payload["freshness"]["data_freshness_status"],
                last_recalculated_at=now,
                source_cutoff_at=source_cutoff_at,
            )

        self._refresh_watchlist_rows(
            session,
            asset=asset,
            summary_payload=summary_payload,
            attributes=watchlist_attributes,
            peer_comparison=peer_comparison,
            performance_snapshot=performance_snapshot,
            risk_snapshot=risk_snapshot,
            exposure_snapshot=exposure_snapshot,
            score_snapshot=score_snapshot,
            now=now,
        )

        return {
            "asset_id": asset_id,
            "job_type": job_type,
            "performance_snapshot_id": getattr(performance_snapshot, "snapshot_id", None),
            "risk_snapshot_id": getattr(risk_snapshot, "snapshot_id", None),
            "exposure_snapshot_id": getattr(exposure_snapshot, "snapshot_id", None),
            "score_snapshot_id": getattr(score_snapshot, "snapshot_id", None),
            "completed_at": now.isoformat(),
        }

    def _replace_performance_snapshot(
        self,
        session: Session,
        *,
        asset_id: str,
        nav_points: list[dict[str, Any]],
        now: datetime,
    ):
        latest = nav_points[-1]
        as_of_date = latest["as_of_date"]
        windows = {
            "return_ytd": date(as_of_date.year, 1, 1),
            "return_1w": as_of_date - timedelta(days=7),
            "return_mtd": date(as_of_date.year, as_of_date.month, 1),
            "return_1m": as_of_date - timedelta(days=30),
            "return_3m": as_of_date - timedelta(days=90),
            "return_6m": as_of_date - timedelta(days=180),
            "return_1y": as_of_date - timedelta(days=365),
        }
        returns: dict[str, Decimal | None] = {}
        for key, target_date in windows.items():
            base = (
                _value_before(nav_points, target_date)
                if key in {"return_ytd", "return_mtd"}
                else _value_at_or_before(nav_points, target_date)
            )
            if base is None:
                returns[key] = None
                continue
            returns[key] = _safe_decimal(
                _window_return(latest["value"], base["value"], max((as_of_date - base["as_of_date"]).days, 1))
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
            base = _value_at_or_before(nav_points, as_of_date - timedelta(days=lookback_days))
            if base is None:
                annualized_window_returns[key] = None
                continue
            annualized_window_returns[key] = _safe_decimal(
                _annualized_return(
                    latest["value"],
                    base["value"],
                    max((as_of_date - base["as_of_date"]).days, 1),
                )
            )
        max_drawdown = _compute_drawdown(nav_points)
        calmar = annualized_return / abs(max_drawdown) if annualized_return is not None and max_drawdown not in {None, 0} else None
        return self.snapshot_repository.replace_performance(
            session,
            snapshot_id=f"perf:{asset_id}:{as_of_date.isoformat()}:{make_recalc_job_id()}",
            asset_id=asset_id,
            data={
                "as_of_date": as_of_date,
                "source_cutoff_at": _coerce_utc(latest.get("adopted_at")) or now,
                "methodology_version": "canonical-performance/v2",
                "input_hash": _hash_payload({"nav_points": nav_points}),
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
        asset_id: str,
        nav_points: list[dict[str, Any]],
        now: datetime,
    ):
        latest = nav_points[-1]
        volatility = _compute_volatility(nav_points)
        downside_volatility = _compute_downside_deviation(nav_points)
        sharpe_ratio = _compute_sharpe(nav_points)
        sortino_ratio = _compute_sortino(nav_points)
        return self.snapshot_repository.replace_risk(
            session,
            snapshot_id=f"risk:{asset_id}:{latest['as_of_date'].isoformat()}:{make_recalc_job_id()}",
            asset_id=asset_id,
            data={
                "as_of_date": latest["as_of_date"],
                "source_cutoff_at": _coerce_utc(latest.get("adopted_at")) or now,
                "methodology_version": "canonical-risk/v2",
                "input_hash": _hash_payload({"nav_points": nav_points}),
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

    def _replace_exposure_snapshot(
        self,
        session: Session,
        *,
        asset_id: str,
        holding_snapshot,
        now: datetime,
    ):
        top10 = sorted(
            (float(item.portfolio_weight or 0) for item in holding_snapshot.positions),
            reverse=True,
        )[:10]
        avg_duration_candidates = [
            float(item.effective_duration)
            for item in holding_snapshot.positions
            if item.effective_duration is not None
        ]
        avg_ytw_candidates = [
            float(item.yield_to_worst)
            for item in holding_snapshot.positions
            if item.yield_to_worst is not None
        ]
        return self.snapshot_repository.replace_exposure(
            session,
            snapshot_id=f"exposure:{asset_id}:{holding_snapshot.as_of_date.isoformat()}:{make_recalc_job_id()}",
            asset_id=asset_id,
            data={
                "as_of_date": holding_snapshot.as_of_date,
                "source_cutoff_at": holding_snapshot.source_cutoff_at,
                "methodology_version": "canonical-exposure/v2",
                "input_hash": _hash_payload({"positions": len(holding_snapshot.positions)}),
                "calculated_at": now,
                "superseded_at": None,
                "is_current": True,
                "asset_allocation_json": [],
                "sector_allocation_json": [],
                "country_allocation_json": [],
                "currency_allocation_json": [],
                "credit_rating_allocation_json": [],
                "duration_bucket_json": [],
                "maturity_bucket_json": [],
                "yield_bucket_json": [],
                "top10_concentration": _safe_decimal(sum(top10)),
                "holding_count": len(holding_snapshot.positions),
                "bond_count": None,
                "equity_count": None,
                "other_count": None,
                "cash_ratio": None,
                "leverage_ratio": None,
                "weighted_duration": _safe_decimal(statistics.mean(avg_duration_candidates)) if avg_duration_candidates else None,
                "weighted_maturity": None,
                "weighted_yield_to_worst": _safe_decimal(statistics.mean(avg_ytw_candidates)) if avg_ytw_candidates else None,
                "avg_credit_rating": None,
                "reported_turnover": None,
                "style_box_code": None,
            },
        )

    def _replace_score_snapshot(
        self,
        session: Session,
        *,
        asset_id: str,
        performance_snapshot,
        risk_snapshot,
        exposure_snapshot,
        now: datetime,
    ):
        scores = [
            _safe_float(getattr(performance_snapshot, "return_1y", None)),
            _safe_float(getattr(risk_snapshot, "sharpe_ratio", None)),
            _safe_float(getattr(exposure_snapshot, "weighted_yield_to_worst", None)),
        ]
        valid_scores = [value for value in scores if value is not None]
        overall_score = statistics.mean(valid_scores) if valid_scores else None
        if overall_score is None:
            overall_rating = None
            analyst_stance = "Unrated"
        elif overall_score >= 10:
            overall_rating = 5
            analyst_stance = "High Conviction"
        elif overall_score >= 6:
            overall_rating = 4
            analyst_stance = "Positive"
        elif overall_score >= 2:
            overall_rating = 3
            analyst_stance = "Watch"
        else:
            overall_rating = 2
            analyst_stance = "Cautious"
        return self.snapshot_repository.replace_score(
            session,
            snapshot_id=f"score:{asset_id}:{now.date().isoformat()}:{make_recalc_job_id()}",
            asset_id=asset_id,
            data={
                "as_of_date": now.date(),
                "source_cutoff_at": now,
                "methodology_version": "canonical-score/v2",
                "input_hash": _hash_payload({"score": overall_score}),
                "calculated_at": now,
                "superseded_at": None,
                "is_current": True,
                "overall_score": _safe_decimal(overall_score),
                "overall_rating": overall_rating,
                "analyst_stance": analyst_stance,
                "people_score": None,
                "process_score": None,
                "exposure_score": _safe_decimal(_safe_float(getattr(exposure_snapshot, "weighted_yield_to_worst", None))),
                "risk_score": _safe_decimal(_safe_float(getattr(risk_snapshot, "sharpe_ratio", None))),
                "price_score": _safe_decimal(_safe_float(getattr(performance_snapshot, "return_1y", None))),
                "operations_score": None,
                "fit_score": None,
                "confidence_score": None,
            },
        )

    def _summary_payload(
        self,
        *,
        asset,
        nav_selection: dict[str, object],
        performance_snapshot,
        risk_snapshot,
        exposure_snapshot,
        score_snapshot,
        attributes: dict[str, object],
        taxonomy_context: dict[str, object],
        now: datetime,
    ) -> dict[str, object]:
        last_nav_date = (
            nav_selection["points"][-1]["as_of_date"].isoformat()
            if nav_selection["points"]
            else None
        )
        freshness_status = "fresh" if nav_selection["points"] else "pending_recalc"
        return {
            "asset_id": asset.asset_id,
            "fund_name": asset.asset_name,
            "ticker_or_isin": asset.primary_identifier_value or asset.asset_id.upper(),
            "rating_as_of": now.date().isoformat(),
            "management_firm_name": str(asset.metadata_json.get("management_firm_name") or "") or None,
            "overall_rating": getattr(score_snapshot, "overall_rating", None),
            "analyst_stance": getattr(score_snapshot, "analyst_stance", "Unrated"),
            "instrument_attributes": attributes,
            "taxonomy": taxonomy_context,
            "key_stats": [
                {"label": "Last NAV Date", "value": last_nav_date or "—"},
                {
                    "label": "MTD Return",
                    "value": (
                        f"{float(performance_snapshot.return_mtd):.2f}%"
                        if getattr(performance_snapshot, "return_mtd", None) is not None
                        else "—"
                    ),
                },
                {
                    "label": "Annualized Return",
                    "value": (
                        f"{float(performance_snapshot.annualized_return):.2f}%"
                        if getattr(performance_snapshot, "annualized_return", None) is not None
                        else "—"
                    ),
                },
                {
                    "label": "Volatility",
                    "value": (
                        f"{float(risk_snapshot.volatility):.2f}%"
                        if getattr(risk_snapshot, "volatility", None) is not None
                        else "—"
                    ),
                },
                {
                    "label": "Holdings",
                    "value": str(getattr(exposure_snapshot, "holding_count", "—") or "—"),
                },
            ],
            "freshness": {
                "data_freshness_status": freshness_status,
                "last_fact_update_at": (
                    _coerce_utc(getattr(exposure_snapshot, "source_cutoff_at", None))
                    or _coerce_utc(getattr(performance_snapshot, "source_cutoff_at", None))
                    or now
                ).isoformat().replace("+00:00", "Z"),
                "last_recalculated_at": now.isoformat().replace("+00:00", "Z"),
                "last_successful_snapshot_at": now.isoformat().replace("+00:00", "Z"),
                "staleness_reason": None if nav_selection["points"] else "No canonical series available.",
            },
            "quick_monitoring_items": [],
            "tabs": DEFAULT_TABS,
        }

    def _peer_comparison_payload(
        self,
        session: Session,
        *,
        asset_id: str,
        taxonomy_node,
        performance_snapshot,
        risk_snapshot,
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

        nodes = self.taxonomy_repository.list_nodes(session, taxonomy_code=FUND_TAXONOMY_CODE)
        node_by_id = {str(node.node_id): node for node in nodes}
        assignments = self.taxonomy_repository.list_assignments(
            session,
            taxonomy_code=FUND_TAXONOMY_CODE,
        )
        active_peer_asset_ids = _active_fund_asset_ids(session)
        assigned_node_by_asset = {
            str(assignment.asset_id): node_by_id.get(str(assignment.node_id))
            for assignment in assignments
            if assignment.node_id and str(assignment.asset_id) in active_peer_asset_ids
        }
        performance_by_asset = {}
        for snapshot in self.snapshot_repository.list_current_performance(
            session,
            asset_ids=sorted(active_peer_asset_ids),
        ):
            performance_by_asset.setdefault(str(snapshot.asset_id), snapshot)
        if performance_snapshot is not None and asset_id in active_peer_asset_ids:
            performance_by_asset[str(asset_id)] = performance_snapshot
        risk_by_asset = {}
        for snapshot in self.snapshot_repository.list_current_risk(
            session,
            asset_ids=sorted(active_peer_asset_ids),
        ):
            risk_by_asset.setdefault(str(snapshot.asset_id), snapshot)
        if risk_snapshot is not None and asset_id in active_peer_asset_ids:
            risk_by_asset[str(asset_id)] = risk_snapshot

        selected_peer_node_id = assigned_path_node_ids[-1]
        selected_asset_ids: list[str] = []
        for candidate_node_id in reversed(assigned_path_node_ids):
            candidate_asset_ids = [
                candidate_asset_id
                for candidate_asset_id, assigned_node in assigned_node_by_asset.items()
                if candidate_node_id in _node_path_node_ids(assigned_node)
                and candidate_asset_id in performance_by_asset
            ]
            selected_peer_node_id = candidate_node_id
            selected_asset_ids = sorted(candidate_asset_ids)
            if len(selected_asset_ids) >= PEER_METRIC_MIN_SAMPLE:
                break

        if (
            asset_id not in selected_asset_ids
            and performance_snapshot is not None
            and asset_id in active_peer_asset_ids
        ):
            selected_asset_ids = sorted([*selected_asset_ids, asset_id])

        peer_node = node_by_id.get(selected_peer_node_id) or taxonomy_node
        fallback_levels = max(
            0,
            len(assigned_path_node_ids) - 1 - assigned_path_node_ids.index(selected_peer_node_id),
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
            for candidate_asset_id in selected_asset_ids:
                candidate_snapshot = (
                    performance_by_asset.get(candidate_asset_id)
                    if source == "performance"
                    else risk_by_asset.get(candidate_asset_id)
                )
                candidate_value = _safe_float(getattr(candidate_snapshot, attr, None))
                if candidate_value is not None:
                    samples.append((candidate_asset_id, candidate_value))
            if len(samples) < PEER_METRIC_MIN_SAMPLE:
                continue

            ranking = _rank_metric_value(
                value=target_value,
                samples=samples,
                direction=direction,
            )
            peer_values = [
                value
                for candidate_asset_id, value in samples
                if candidate_asset_id != asset_id
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
            "sample_count": len(selected_asset_ids),
            "metrics": metric_rows,
            "summary": {
                "return_percentile": return_percentile,
                "risk_percentile": risk_percentile,
                "risk_adjusted_percentile": risk_adjusted_percentile,
                "overall_percentile": _mean_optional(
                    [return_percentile, risk_percentile, risk_adjusted_percentile]
                ),
            },
        }

    def _performance_payload(
        self,
        *,
        performance_snapshot,
        peer_comparison: dict[str, object] | None,
        nav_points: list[dict[str, Any]],
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
            "snapshot_metadata": _snapshot_metadata(performance_snapshot),
        }

    def _risk_payload(
        self,
        *,
        risk_snapshot,
        performance_snapshot,
        peer_comparison: dict[str, object] | None,
        nav_points: list[dict[str, Any]],
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
                "exposure_risk_score": None,
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
            "snapshot_metadata": _snapshot_metadata(risk_snapshot),
        }

    def _exposure_payload(self, exposure_snapshot) -> dict[str, object]:
        return {
            "allocation_blocks": {},
            "style_box": {
                "weighted_duration": _safe_float(getattr(exposure_snapshot, "weighted_duration", None)),
                "yield_to_worst": _safe_float(getattr(exposure_snapshot, "weighted_yield_to_worst", None)),
            } if exposure_snapshot is not None else None,
            "liquidity_leverage": None,
            "valuation_statistics": None,
            "holdings_summary": {
                "total_holdings": getattr(exposure_snapshot, "holding_count", None),
                "top10_concentration": _safe_float(getattr(exposure_snapshot, "top10_concentration", None)),
            } if exposure_snapshot is not None else None,
            "snapshot_metadata": None,
        }

    def _holdings_payload(self, holding_snapshot) -> dict[str, object]:
        rows = []
        if holding_snapshot is not None:
            rows = [
                {
                    "holding_name": item.holding_name,
                    "holding_type": item.holding_type,
                    "portfolio_weight": _safe_float(item.portfolio_weight),
                    "market_value": _safe_float(item.market_value),
                    "quantity": _safe_float(item.quantity),
                    "currency": item.currency,
                    "market_price": _safe_float(item.market_price),
                    "yield_to_worst": _safe_float(item.yield_to_worst),
                    "effective_duration": _safe_float(item.effective_duration),
                    "credit_rating": item.credit_rating,
                }
                for item in holding_snapshot.positions
            ]
        return {"rows": rows, "page": 1, "page_size": len(rows), "total_rows": len(rows)}

    def _rating_payload(self, score_snapshot) -> dict[str, object]:
        return {
            "overall_rating": getattr(score_snapshot, "overall_rating", None),
            "overall_score": _safe_float(getattr(score_snapshot, "overall_score", None)),
            "analyst_stance": getattr(score_snapshot, "analyst_stance", "Unrated"),
            "methodology_version": "house-rating/v2",
            "dimension_scores": [],
            "override_info": None,
        }

    def _refresh_watchlist_rows(
        self,
        session: Session,
        *,
        asset,
        summary_payload: dict[str, object],
        attributes: dict[str, object],
        peer_comparison: dict[str, object] | None,
        performance_snapshot,
        risk_snapshot,
        exposure_snapshot,
        score_snapshot,
        now: datetime,
    ) -> None:
        existing_rows = self.read_model_repository.list_watchlist_rows_for_asset(session, asset.asset_id)
        if not existing_rows:
            return
        freshness = summary_payload.get("freshness", {})
        row_attributes = {**attributes, **_peer_watchlist_attributes(peer_comparison)}
        for row in existing_rows:
            self.read_model_repository.upsert_watchlist_row(
                session,
                watchlist_id=row.watchlist_id,
                asset_id=asset.asset_id,
                data=build_watchlist_row_materialization(
                    watchlist_id=row.watchlist_id,
                    asset_id=asset.asset_id,
                    asset_type=asset.asset_type,
                    source_row=row,
                    display_name=asset.asset_name,
                    share_class=None,
                    ticker_or_isin=asset.primary_identifier_value,
                    management_firm_name=str(asset.metadata_json.get("management_firm_name") or "") or None,
                    overall_rating=getattr(score_snapshot, "overall_rating", None),
                    analyst_stance=getattr(score_snapshot, "analyst_stance", None),
                    attributes=row_attributes,
                    freshness_status=str(freshness.get("data_freshness_status") or "fresh"),
                    last_fact_update_at=_coerce_utc(
                        datetime.fromisoformat(str(freshness["last_fact_update_at"]).replace("Z", "+00:00"))
                    ) if freshness.get("last_fact_update_at") else None,
                    last_recalculated_at=now,
                    last_successful_snapshot_at=now,
                    staleness_reason=freshness.get("staleness_reason"),
                )
                | {
                    "return_ytd": getattr(performance_snapshot, "return_ytd", None),
                    "return_1w": getattr(performance_snapshot, "return_1w", None),
                    "return_mtd": getattr(performance_snapshot, "return_mtd", None),
                    "return_1m": getattr(performance_snapshot, "return_1m", None),
                    "return_1y": getattr(performance_snapshot, "return_1y", None),
                    "annualized_return": getattr(performance_snapshot, "annualized_return", None),
                    "max_drawdown": getattr(performance_snapshot, "max_drawdown", None),
                    "volatility": getattr(risk_snapshot, "volatility", None),
                    "sharpe_ratio": getattr(risk_snapshot, "sharpe_ratio", None),
                    "duration": getattr(exposure_snapshot, "weighted_duration", None),
                    "yield_to_worst": getattr(exposure_snapshot, "weighted_yield_to_worst", None),
                    "aum": None,
                    "avg_credit_rating": getattr(exposure_snapshot, "avg_credit_rating", None),
                    "exposure_updated_at": getattr(exposure_snapshot, "calculated_at", None),
                    "last_nav_date": (
                        date.fromisoformat(str(summary_payload["key_stats"][0]["value"]))
                        if summary_payload.get("key_stats")
                        and str(summary_payload["key_stats"][0].get("value") or "") != "—"
                        else None
                    ),
                },
            )
