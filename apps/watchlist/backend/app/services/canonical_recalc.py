from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha1
import json
import math
import statistics
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.read_models import (
    AssetChartReadModel,
    AssetExposureHoldingsReadModel,
    AssetExposureReadModel,
    AssetPerformanceReadModel,
    AssetRatingReadModel,
    AssetRiskReadModel,
    AssetSummaryReadModel,
)
from app.repositories.sqlalchemy.assets import SQLAlchemyAssetRepository
from app.repositories.sqlalchemy.facts import SQLAlchemyFactsRepository
from app.repositories.sqlalchemy.instrument_attributes import (
    SQLAlchemyInstrumentAttributeRepository,
)
from app.repositories.sqlalchemy.manual_profiles import (
    SQLAlchemyAssetManualProfileRepository,
)
from app.repositories.sqlalchemy.read_models import SQLAlchemyReadModelRepository
from app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
from app.repositories.sqlalchemy.snapshots import SQLAlchemySnapshotRepository
from app.services.read_models import (
    build_watchlist_row_materialization,
    collapse_latest_attribute_values,
    serialize_payload,
)
from app.services.recalc_job_ids import make_recalc_job_id
from app.services.shared_instrument_registry import get_shared_instrument


DEFAULT_TABS = [
    "summary",
    "chart",
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

NAV_BASIS_PRIORITY = ("nav_with_dividend", "nav")


def _default_nav_settings() -> dict[str, Any]:
    return {
        "nav_basis_preference": "auto",
        "source_mode": "manual",
        "source_email": "",
        "source_location": "Manual upload",
        "source_api_profile": "",
        "default_benchmark_asset_id": None,
        "peer_baseline_asset_ids": [],
    }


def _normalize_nav_settings(payload: dict[str, Any] | None) -> dict[str, Any]:
    normalized = {
        **_default_nav_settings(),
        **(payload or {}),
    }
    if normalized.get("nav_basis_preference") not in {"auto", "nav_with_dividend", "nav"}:
        normalized["nav_basis_preference"] = "auto"
    if normalized.get("source_mode") not in {"manual", "email", "api"}:
        normalized["source_mode"] = "manual"
    normalized["source_email"] = str(normalized.get("source_email") or "")
    normalized["source_location"] = str(normalized.get("source_location") or "Manual upload")
    normalized["source_api_profile"] = str(normalized.get("source_api_profile") or "")
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
    return float(numeric) if numeric is not None else None


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
    if len(nav_points) < 3:
        return None
    returns: list[float] = []
    for previous, current in zip(nav_points, nav_points[1:]):
        if previous["value"] <= 0:
            continue
        returns.append(current["value"] / previous["value"] - 1)
    if len(returns) < 2:
        return None
    return statistics.stdev(returns) * math.sqrt(252) * 100


def _compute_sharpe(nav_points: list[dict[str, Any]]) -> float | None:
    volatility = _compute_volatility(nav_points)
    if volatility in {None, 0}:
        return None
    latest = nav_points[-1]
    first = nav_points[0]
    annualized = _annualized_return(
        latest["value"],
        first["value"],
        max((latest["as_of_date"] - first["as_of_date"]).days, 1),
    )
    if annualized is None:
        return None
    return annualized / volatility


def _periodic_returns(nav_points: list[dict[str, Any]]) -> list[float]:
    returns: list[float] = []
    for previous, current in zip(nav_points, nav_points[1:]):
        if previous["value"] <= 0:
            continue
        returns.append(current["value"] / previous["value"] - 1)
    return returns


def _compute_downside_deviation(nav_points: list[dict[str, Any]]) -> float | None:
    returns = [value for value in _periodic_returns(nav_points) if value < 0]
    if len(returns) < 2:
        return None
    downside_variance = sum(value ** 2 for value in returns) / len(returns)
    return math.sqrt(max(downside_variance, 0)) * math.sqrt(252) * 100


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
        "close": "nav",
        "last": "nav",
        "total_return_nav": "nav_with_dividend",
        "nav_with_dividend": "nav_with_dividend",
        "adjusted_close": "nav_with_dividend",
    }
    grouped: dict[date, dict[str, Any]] = {}
    for item in market_data:
        if not isinstance(item, dict):
            continue
        target_key = quote_basis_map.get(str(item.get("quote_basis") or "").strip())
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
) -> dict[str, object]:
    normalized_preference = (preference or "auto").strip().lower()
    if normalized_preference in {"nav", "nav_with_dividend"}:
        basis_order = (normalized_preference,)
    else:
        basis_order = NAV_BASIS_PRIORITY

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
            "source_settings": {
                "source_mode": str(nav_settings.get("source_mode", "manual")),
                "source_email": str(nav_settings.get("source_email", "")),
                "source_location": str(nav_settings.get("source_location", "Manual upload")),
                "source_api_profile": str(nav_settings.get("source_api_profile", "")),
            },
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
            dedupe_key=f"{job_type}:{asset_id}:{make_recalc_job_id()}",
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
        nav_selection = _select_nav_basis_rows(
            _group_shared_nav_rows(list((get_shared_instrument(asset_id) or {}).get("market_data", [])))
            or _group_local_nav_rows(nav_facts),
            preference=str(nav_settings.get("nav_basis_preference", "auto")),
        )
        holding_snapshot = self.facts_repository.get_current_holding_snapshot(
            session,
            asset_id=asset_id,
        )
        attributes = collapse_latest_attribute_values(
            self.attribute_repository.get_values_for_asset(session, asset_id)
        )

        performance_snapshot = self.snapshot_repository.get_current_performance(session, asset_id)
        risk_snapshot = self.snapshot_repository.get_current_risk(session, asset_id)
        exposure_snapshot = self.snapshot_repository.get_current_exposure(session, asset_id)
        score_snapshot = self.snapshot_repository.get_current_score(session, asset_id)

        if job_type in {"performance", "all"} and nav_selection["points"]:
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
            attributes=attributes,
            now=now,
        )
        chart_payload = _build_chart_payload(
            asset_id,
            nav_selection["points"],
            nav_selection["points"][-1]["currency"] if nav_selection["points"] else "USD",
        )
        performance_payload = self._performance_payload(
            performance_snapshot=performance_snapshot,
            nav_points=nav_selection["points"],
        )
        risk_payload = self._risk_payload(
            risk_snapshot=risk_snapshot,
            performance_snapshot=performance_snapshot,
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
            attributes=attributes,
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
            "return_1m": as_of_date - timedelta(days=30),
            "return_3m": as_of_date - timedelta(days=90),
            "return_6m": as_of_date - timedelta(days=180),
            "return_1y": as_of_date - timedelta(days=365),
        }
        returns: dict[str, Decimal | None] = {}
        for key, target_date in windows.items():
            base = _value_at_or_before(nav_points, target_date)
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
        sharpe_ratio = _compute_sharpe(nav_points)
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
                "downside_volatility": None,
                "sharpe_ratio": _safe_decimal(sharpe_ratio),
                "sortino_ratio": None,
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
            "category_name": str(asset.metadata_json.get("category_name") or "Unclassified"),
            "overall_rating": getattr(score_snapshot, "overall_rating", None),
            "analyst_stance": getattr(score_snapshot, "analyst_stance", "Unrated"),
            "instrument_attributes": attributes,
            "key_stats": [
                {"label": "Last NAV Date", "value": last_nav_date or "—"},
                {
                    "label": "1M Return",
                    "value": (
                        f"{float(performance_snapshot.return_1m):.2f}%"
                        if getattr(performance_snapshot, "return_1m", None) is not None
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

    def _performance_payload(
        self,
        *,
        performance_snapshot,
        nav_points: list[dict[str, Any]],
    ) -> dict[str, object]:
        trailing_returns: list[dict[str, Any]] = []
        annual_returns = _compute_calendar_year_returns(nav_points)
        growth_chart_series: list[dict[str, Any]] = []
        if performance_snapshot is not None:
            trailing_returns = [
                {"window": "1W", "investment_nav": _safe_float(performance_snapshot.return_1w), "category_nav": None, "index_nav": None},
                {"window": "1M", "investment_nav": _safe_float(performance_snapshot.return_1m), "category_nav": None, "index_nav": None},
                {"window": "YTD", "investment_nav": _safe_float(performance_snapshot.return_ytd), "category_nav": None, "index_nav": None},
                {"window": "3M", "investment_nav": _safe_float(performance_snapshot.return_3m), "category_nav": None, "index_nav": None},
                {"window": "6M", "investment_nav": _safe_float(performance_snapshot.return_6m), "category_nav": None, "index_nav": None},
                {"window": "1Y", "investment_nav": _safe_float(performance_snapshot.return_1y), "category_nav": None, "index_nav": None},
                {"window": "3Y", "investment_nav": _safe_float(performance_snapshot.return_3y_annualized), "category_nav": None, "index_nav": None},
                {"window": "5Y", "investment_nav": _safe_float(performance_snapshot.return_5y_annualized), "category_nav": None, "index_nav": None},
                {"window": "Ann.", "investment_nav": _safe_float(performance_snapshot.annualized_return), "category_nav": None, "index_nav": None},
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
            "ranking": None,
            "snapshot_metadata": _snapshot_metadata(performance_snapshot),
        }

    def _risk_payload(
        self,
        *,
        risk_snapshot,
        performance_snapshot,
        nav_points: list[dict[str, Any]],
    ) -> dict[str, object]:
        volatility = _safe_float(getattr(risk_snapshot, "volatility", None))
        annualized_return = _safe_float(getattr(performance_snapshot, "annualized_return", None))
        drawdown_summary = _compute_drawdown_summary(nav_points)
        current_watch = _build_current_risk_watch(nav_points, drawdown_summary)
        risk_structure = _build_risk_structure(nav_points, drawdown_summary, current_watch)
        change_monitor = _build_risk_change_monitor(nav_points, drawdown_summary, current_watch)
        return {
            "risk_overview": {
                "exposure_risk_score": None,
                "risk_level": _risk_level_label(volatility),
                "risk_vs_category": "peer_set_pending",
                "return_vs_category": "peer_set_pending",
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
                {"metric": "volatility", "investment": volatility, "category": None, "index": None},
                {"metric": "sharpe_ratio", "investment": _safe_float(getattr(risk_snapshot, "sharpe_ratio", None)), "category": None, "index": None},
                {"metric": "max_drawdown", "investment": _safe_float(getattr(performance_snapshot, "max_drawdown", None)), "category": None, "index": None},
                {"metric": "calmar_ratio", "investment": _safe_float(getattr(performance_snapshot, "calmar", None)), "category": None, "index": None},
                {"metric": "annualized_return", "investment": annualized_return, "category": None, "index": None},
            ],
            "drawdown_summary": drawdown_summary,
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
                    category_name=summary_payload.get("category_name"),
                    overall_rating=getattr(score_snapshot, "overall_rating", None),
                    analyst_stance=getattr(score_snapshot, "analyst_stance", None),
                    attributes=attributes,
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
