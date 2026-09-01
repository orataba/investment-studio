from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha1
import json
import math
import statistics
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from portfolio_ops_instrument_core import (
    FUND_INSTRUMENT_TYPES,
    FUND_TOTAL_RETURN_QUOTE_BASES,
    QUOTE_BASIS_METRIC_FAMILY,
    resolve_quote_return_semantics,
    validate_market_data_identity,
)

from watchlist_app.db.models.instruments import InstrumentDetail
from watchlist_app.db.models.read_models import (
    InstrumentChartReadModel,
    InstrumentExposureHoldingsReadModel,
    InstrumentExposureReadModel,
    InstrumentPerformanceReadModel,
    InstrumentRiskReadModel,
    InstrumentSummaryReadModel,
)
from watchlist_app.repositories.sqlalchemy.instruments import SQLAlchemyInstrumentRepository
from watchlist_app.repositories.sqlalchemy.facts import SQLAlchemyFactsRepository
from watchlist_app.repositories.sqlalchemy.instrument_attributes import (
    SQLAlchemyInstrumentAttributeRepository,
)
from watchlist_app.repositories.sqlalchemy.manual_profiles import (
    SQLAlchemyInstrumentManualProfileRepository,
)
from watchlist_app.repositories.sqlalchemy.read_models import SQLAlchemyReadModelRepository
from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
from watchlist_app.repositories.sqlalchemy.snapshots import SQLAlchemySnapshotRepository
from watchlist_app.repositories.sqlalchemy.taxonomy import SQLAlchemyTaxonomyRepository
from watchlist_app.reference_data.instrument_taxonomy import INSTRUMENT_TAXONOMY_CODE
from watchlist_app.services.instrument_taxonomy import (
    build_taxonomy_context,
    merge_taxonomy_attributes,
)
from watchlist_app.services.instrument_resolution import (
    LOCAL_DETAIL_INSTRUMENT_TYPES,
    sync_local_instrument,
)
from watchlist_app.services.calculation_frequency import (
    assess_latest_observation_freshness,
    build_calculation_frequency_context,
)
from watchlist_app.services.read_models import (
    build_watchlist_row_materialization,
    collapse_latest_attribute_values,
    serialize_payload,
)
from watchlist_app.services.materialization_policy import (
    WATCHLIST_MATERIALIZATION_VERSION,
)
from watchlist_app.services.recalc_job_ids import make_recalc_dedupe_key, make_recalc_job_id
from watchlist_app.services.return_windows import (
    RETURN_WINDOW_POLICY_VERSION,
    annualized_return_percent,
    named_return_window_spec,
    period_return_percent,
    resolve_return_window,
    return_window_metadata,
)
from watchlist_app.services.shared_instrument_registry import (
    SharedInstrumentRegistryError,
    get_shared_instrument,
    list_shared_instruments,
)


FUND_DETAIL_TABS = [
    "overview",
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

LISTED_DETAIL_TABS = {
    "etf": ["overview", "research", "performance", "risk", "price", "portfolio", "monitoring"],
    "equity": [
        "overview",
        "research",
        "performance",
        "risk",
        "price",
        "fundamentals",
        "events",
        "monitoring",
    ],
    "index": ["overview", "research", "performance", "risk", "price", "methodology", "monitoring"],
}

PERFORMANCE_METHODOLOGY_VERSION = "canonical-performance/v8"
RISK_METHODOLOGY_VERSION = "canonical-risk/v7"
PEER_COMPARISON_POLICY_VERSION = "peer-comparison/v4"


class RecalcJobLeaseLostError(RuntimeError):
    """Raised when an obsolete worker tries to publish a recalculation."""


class RecalcJobAlreadyRunningError(RuntimeError):
    """Raised when synchronous work would overlap an active instrument job."""


UNIT_OR_RAW_QUOTE_BASES = {
    "close",
    "last",
    "official_nav",
}
TOTAL_RETURN_QUOTE_BASES = {
    "total_return_nav",
    "adjusted_close",
}
QUOTE_BASIS_LABELS = {
    "official_nav": "Unit NAV",
    "close": "Close",
    "last": "Last Price",
    "total_return_nav": "Cumulative NAV",
    "adjusted_close": "Adjusted Close",
}
# Percentile/rank output needs at least four independent peers. Smaller
# cohorts still expose their median and sample size, but are labelled limited
# rather than presenting a statistically brittle rank.
PEER_PERCENTILE_MIN_PEERS = 4
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
        "default_benchmark_instrument_id": None,
        "peer_baseline_instrument_ids": [],
    }


def _normalize_nav_settings(payload: dict[str, Any] | None) -> dict[str, Any]:
    normalized = {
        **_default_nav_settings(),
        **(payload or {}),
    }
    if normalized.get("nav_basis_preference") not in {"auto", "nav_with_dividend"}:
        normalized["nav_basis_preference"] = "auto"
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


def _allows_listed_price_return_basis(instrument_type: object) -> bool:
    return str(instrument_type or "").strip().lower() in {"equity", "etf", "index"}


def _normalize_quote_basis(value: object) -> str:
    return str(value or "").strip().lower()


def _quote_basis_row_slot(quote_basis: object) -> str | None:
    normalized = _normalize_quote_basis(quote_basis)
    if normalized in TOTAL_RETURN_QUOTE_BASES:
        return "nav_with_dividend"
    if normalized in UNIT_OR_RAW_QUOTE_BASES:
        return "nav"
    return None


def _quote_basis_metric_family(quote_basis: object) -> str:
    normalized = _normalize_quote_basis(quote_basis)
    return str(QUOTE_BASIS_METRIC_FAMILY.get(normalized) or "")


def _quote_basis_label(quote_basis: object) -> str:
    normalized = _normalize_quote_basis(quote_basis)
    return QUOTE_BASIS_LABELS.get(normalized, normalized.replace("_", " ").title() or "Quote")


def _quote_basis_date_label(quote_basis: object) -> str:
    metric_family = _quote_basis_metric_family(quote_basis)
    return "Last Price Date" if metric_family == "price" else "Last NAV Date"


def _quote_basis_series_type(quote_basis: object) -> str:
    normalized = _normalize_quote_basis(quote_basis)
    metric_family = _quote_basis_metric_family(normalized)
    if metric_family == "price":
        return "price"
    if normalized in TOTAL_RETURN_QUOTE_BASES:
        return "total_return_nav"
    return "nav"


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _coerce_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_source_watermark(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _coerce_utc(value)
    normalized = str(value or "").strip()
    if not normalized:
        return None
    try:
        return _coerce_utc(datetime.fromisoformat(normalized.replace("Z", "+00:00")))
    except ValueError:
        return None


def _shared_calculation_watermark(
    shared_instrument: dict[str, object] | None,
) -> datetime | None:
    if not isinstance(shared_instrument, dict):
        return None
    candidates = [
        parsed
        for parsed in (
            _parse_source_watermark(shared_instrument.get("market_data_updated_at")),
            _parse_source_watermark(
                shared_instrument.get("calculation_inputs_updated_at")
            ),
        )
        if parsed is not None
    ]
    return max(candidates) if candidates else None


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


def _weighted_holding_metric(
    positions: list[object],
    metric_name: str,
) -> tuple[Decimal | None, Decimal, Decimal]:
    total_reported_weight = Decimal("0")
    covered_weight = Decimal("0")
    weighted_total = Decimal("0")
    for position in positions:
        weight = _safe_decimal(getattr(position, "portfolio_weight", None))
        if weight is None or weight <= 0:
            continue
        total_reported_weight += weight
        metric = _safe_decimal(getattr(position, metric_name, None))
        if metric is None:
            continue
        covered_weight += weight
        weighted_total += weight * metric
    weighted_value = (
        weighted_total / covered_weight if covered_weight > 0 else None
    )
    return weighted_value, covered_weight, total_reported_weight


def _string_list(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    return []


def _node_path_node_ids(node: object | None) -> list[str]:
    return _string_list(getattr(node, "path_node_ids_json", None))


def _node_path_labels(node: object | None) -> list[str]:
    return _string_list(getattr(node, "path_labels_json", None))


def _peer_taxonomy_is_comparable(node: object | None) -> bool:
    node_id = str(getattr(node, "node_id", "") or "").strip()
    return bool(node_id) and bool(getattr(node, "is_leaf", False)) and not (
        node_id.endswith("-unclassified")
        or node_id.endswith("-other")
        or node_id.startswith("equity-market-")
    )


def _peer_geographic_exposure(
    node: object | None,
    attributes: dict[str, object] | None,
) -> str | None:
    instrument_type = str(getattr(node, "instrument_type", "") or "").strip().lower()
    if instrument_type == "equity":
        root_node_id = next(
            (
                value
                for value in _node_path_node_ids(node)
                if value.startswith("equity-market-")
            ),
            "",
        )
        return root_node_id.removeprefix("equity-market-") or None
    value = (attributes or {}).get("primary_geographic_exposure")
    normalized = str(value or "").strip()
    return normalized or None


def _active_peer_instrument_ids(session: Session) -> set[str]:
    supported_types = tuple(sorted(LOCAL_DETAIL_INSTRUMENT_TYPES))
    local_active_instrument_ids = {
        str(instrument_id)
        for instrument_id in session.scalars(
            select(InstrumentDetail.instrument_id).where(
                InstrumentDetail.instrument_type.in_(supported_types),
                InstrumentDetail.is_active.is_(True),
            )
        ).all()
    }
    shared_active_instrument_ids = {
        str(item.get("instrument_id"))
        for instrument_type in supported_types
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
    row = by_key.get("return_1y")
    if not row or row.get("percentile") is None:
        return None
    return {
        "metric_key": "return_1y",
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


def _performance_payload_with_peer_comparison(
    payload: dict[str, object],
    peer_comparison: dict[str, object],
) -> dict[str, object]:
    peer_metrics = {
        str(row.get("metric_key") or ""): row
        for row in peer_comparison.get("metrics") or []
        if isinstance(row, dict)
    }
    window_metric = {
        "1W": "return_1w",
        "1M": "return_1m",
        "MTD": "return_mtd",
        "YTD": "return_ytd",
        "3M": "return_3m",
        "6M": "return_6m",
        "1Y": "return_1y",
        "3Y": "return_3y_annualized",
        "5Y": "return_5y_annualized",
        "Ann.": "annualized_return",
    }
    trailing_returns = []
    for raw_row in payload.get("trailing_returns") or []:
        if not isinstance(raw_row, dict):
            continue
        row = dict(raw_row)
        metric = peer_metrics.get(window_metric.get(str(row.get("window")), ""), {})
        row["category_nav"] = _safe_float(metric.get("peer_median"))
        trailing_returns.append(row)
    return {
        **payload,
        "trailing_returns": trailing_returns,
        "ranking": _primary_peer_ranking(peer_comparison),
        "peer_comparison": peer_comparison,
    }


def _risk_payload_with_peer_comparison(
    payload: dict[str, object],
    peer_comparison: dict[str, object],
) -> dict[str, object]:
    peer_metrics = {
        str(row.get("metric_key") or ""): row
        for row in peer_comparison.get("metrics") or []
        if isinstance(row, dict)
    }
    risk_metrics = []
    for raw_row in payload.get("risk_metrics") or []:
        if not isinstance(raw_row, dict):
            continue
        row = dict(raw_row)
        metric = peer_metrics.get(str(row.get("metric") or ""), {})
        row["category"] = _safe_float(metric.get("peer_median"))
        risk_metrics.append(row)
    risk_overview = payload.get("risk_overview")
    if isinstance(risk_overview, dict):
        risk_overview = {
            **risk_overview,
            "risk_vs_category": None,
            "return_vs_category": None,
        }
    return {
        **payload,
        "risk_overview": risk_overview,
        "risk_metrics": risk_metrics,
        "peer_comparison": peer_comparison,
    }


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
    if not isinstance(peer_comparison, dict) or peer_comparison.get("status") not in {
        "ready",
        "limited_sample",
    }:
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


def _numeric_nav_points(nav_points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the ordered, finite, positive numeric path used by analytics.

    Shared market-data values are deliberately kept as ``Decimal`` objects at
    the repository boundary.  The analytics kernels, however, use the
    ``statistics`` and ``math`` modules, which operate on floats (and raise
    when a ``Decimal`` is multiplied by a float square-root factor).  Keeping
    this coercion at the calculation boundary makes every path-dependent
    metric use the same validated numeric series and also makes the helpers
    fail closed for malformed points.
    """

    normalized: list[dict[str, Any]] = []
    for point in nav_points:
        if not isinstance(point, dict) or not isinstance(point.get("as_of_date"), date):
            continue
        value = _safe_float(point.get("value"))
        if value is None or value <= 0:
            continue
        normalized.append({**point, "value": value})
    return sorted(normalized, key=lambda item: item["as_of_date"])


def _compute_drawdown(nav_points: list[dict[str, Any]]) -> float | None:
    nav_points = _numeric_nav_points(nav_points)
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
    nav_points = _numeric_nav_points(nav_points)
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
    nav_points = _numeric_nav_points(nav_points)
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
    nav_points = _numeric_nav_points(nav_points)
    returns = _periodic_returns(nav_points)
    if len(returns) < 2:
        return None
    periods_per_year = _annualization_periods_per_year(nav_points, len(returns))
    if periods_per_year is None:
        return None
    return statistics.stdev(returns) * math.sqrt(periods_per_year) * 100


def _compute_sharpe(nav_points: list[dict[str, Any]]) -> float | None:
    nav_points = _numeric_nav_points(nav_points)
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
    nav_points = _numeric_nav_points(nav_points)
    returns: list[float] = []
    for previous, current in zip(nav_points, nav_points[1:], strict=False):
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
    nav_points = _numeric_nav_points(nav_points)
    periodic_returns = _periodic_returns(nav_points)
    if len(periodic_returns) < 2 or not any(value < 0 for value in periodic_returns):
        return None
    periods_per_year = _annualization_periods_per_year(nav_points, len(periodic_returns))
    if periods_per_year is None:
        return None
    downside_variance = (
        sum(min(value, 0.0) ** 2 for value in periodic_returns)
        / len(periodic_returns)
    )
    return math.sqrt(max(downside_variance, 0)) * math.sqrt(periods_per_year) * 100


def _compute_sortino(nav_points: list[dict[str, Any]]) -> float | None:
    nav_points = _numeric_nav_points(nav_points)
    periodic_returns = _periodic_returns(nav_points)
    if len(periodic_returns) < 2 or not any(value < 0 for value in periodic_returns):
        return None
    periods_per_year = _annualization_periods_per_year(nav_points, len(periodic_returns))
    if periods_per_year is None:
        return None
    downside_variance = (
        sum(min(value, 0.0) ** 2 for value in periodic_returns)
        / len(periodic_returns)
    )
    downside_deviation = math.sqrt(max(downside_variance, 0))
    if downside_deviation == 0:
        return None
    mean_return = statistics.fmean(periodic_returns)
    return (mean_return / downside_deviation) * math.sqrt(periods_per_year)


def _monthly_close_points(nav_points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nav_points = _numeric_nav_points(nav_points)
    monthly: dict[tuple[int, int], dict[str, Any]] = {}
    for point in nav_points:
        monthly[(point["as_of_date"].year, point["as_of_date"].month)] = point
    return [monthly[key] for key in sorted(monthly)]


def _calendar_month_index(value: date) -> int:
    return value.year * 12 + value.month - 1


def _is_next_calendar_month(previous: date, current: date) -> bool:
    return _calendar_month_index(current) - _calendar_month_index(previous) == 1


def _monthly_return_series(nav_points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    closes = _monthly_close_points(nav_points)
    returns: list[dict[str, Any]] = []
    for previous, current in zip(closes, closes[1:], strict=False):
        if (
            not _is_next_calendar_month(
                previous["as_of_date"],
                current["as_of_date"],
            )
            or previous["value"] <= 0
        ):
            continue
        returns.append({
            "as_of_date": current["as_of_date"],
            "value": (current["value"] / previous["value"] - 1) * 100,
        })
    return returns


def _numeric_monthly_returns(
    monthly_returns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in monthly_returns:
        if not isinstance(row, dict) or not isinstance(row.get("as_of_date"), date):
            continue
        value = _safe_float(row.get("value"))
        if value is None or not math.isfinite(value):
            continue
        normalized.append({**row, "value": value})
    return sorted(normalized, key=lambda item: item["as_of_date"])


def _monthly_drawdown_series(nav_points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nav_points = _numeric_nav_points(nav_points)
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
    monthly_returns = _numeric_monthly_returns(monthly_returns)
    if len(monthly_returns) < window:
        return []
    rolling: list[dict[str, Any]] = []
    for index in range(window - 1, len(monthly_returns)):
        window_rows = monthly_returns[index - window + 1:index + 1]
        if any(
            not _is_next_calendar_month(
                previous["as_of_date"],
                current["as_of_date"],
            )
            for previous, current in zip(
                window_rows,
                window_rows[1:],
                strict=False,
            )
        ):
            continue
        window_values = [float(row["value"]) / 100 for row in window_rows]
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
    monthly_returns = _numeric_monthly_returns(monthly_returns)
    count = 0
    next_row: dict[str, Any] | None = None
    for row in reversed(monthly_returns):
        if next_row is not None and not _is_next_calendar_month(
            row["as_of_date"],
            next_row["as_of_date"],
        ):
            break
        if row["value"] >= 0:
            break
        count += 1
        next_row = row
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
    worst_abs = (
        abs(max_drawdown)
        if isinstance(max_drawdown, (int, float)) and max_drawdown not in {None, 0}
        else None
    )
    drawdown_ratio = (
        abs(current_drawdown) / worst_abs
        if current_drawdown is not None and worst_abs
        else None
    )
    return {
        "overall_level": None,
        "rows": [
            {
                "signal": "Rolling Annualized Volatility",
                "level": None,
                "reading": (
                    f"{_format_percent(latest_rolling_vol)} · own-history median "
                    f"{_format_percent(rolling_vol_median)} · "
                    f"{_format_number(rolling_vol_pct, 0)}th percentile"
                    if latest_rolling_vol is not None
                    and rolling_vol_median is not None
                    and rolling_vol_pct is not None
                    else "Need more contiguous monthly history"
                ),
            },
            {
                "signal": "Current Drawdown",
                "level": None,
                "reading": (
                    f"{_format_percent(current_drawdown)} · "
                    f"{_format_number(drawdown_ratio * 100, 0)}% of observed maximum"
                    if current_drawdown is not None and drawdown_ratio is not None
                    else _format_percent(current_drawdown)
                ),
            },
            {
                "signal": "Latest Month",
                "level": None,
                "reading": (
                    f"Return {_format_percent(latest_monthly_return)} · "
                    f"drawdown {_format_percent(latest_monthly_drawdown)} · "
                    f"{trailing_negative_months} consecutive down month(s)"
                ),
            },
        ],
        "note": "Observed path statistics only. No cross-asset High/Normal label or investment recommendation is inferred.",
    }


def _build_risk_structure(nav_points: list[dict[str, Any]], drawdown_summary: dict[str, Any] | None) -> dict[str, Any]:
    volatility = _compute_volatility(nav_points)
    downside_deviation = _compute_downside_deviation(nav_points)
    max_drawdown = drawdown_summary.get("maximum") if isinstance(drawdown_summary, dict) else None
    recovery_months = drawdown_summary.get("max_duration_months") if isinstance(drawdown_summary, dict) else None

    if volatility is None and downside_deviation is None and max_drawdown is None:
        return {"rows": []}

    downside_ratio = (
        downside_deviation / volatility
        if volatility not in {None, 0} and downside_deviation is not None
        else None
    )
    rows = [
        {
            "characteristic": "Observed Path",
            "reading": f"Vol {_format_percent(volatility)} · Max DD {_format_percent(max_drawdown)}",
            "interpretation": "Annualized volatility and maximum drawdown from the selected canonical series.",
        },
        {
            "characteristic": "Downside Shape",
            "reading": f"Downside Dev {_format_percent(downside_deviation)} · Ratio {_format_number(downside_ratio, 2)}",
            "interpretation": "Downside deviation divided by total volatility; no qualitative threshold is applied.",
        },
        {
            "characteristic": "Recovery Profile",
            "reading": f"Max DD {_format_percent(max_drawdown)} · Duration {recovery_months if recovery_months is not None else '—'} mo",
            "interpretation": "Longest observed peak-to-underwater duration; an open drawdown remains open rather than being labelled recovered.",
        },
    ]
    return {"rows": rows}


def _build_risk_change_monitor(nav_points: list[dict[str, Any]], drawdown_summary: dict[str, Any] | None) -> dict[str, Any]:
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
            "watch": None,
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
            "watch": None,
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
            "watch": None,
        },
        {
            "signal": "Recent Return Pressure",
            "current": _format_percent(latest_monthly_return),
            "baseline": f"Median month {_format_percent(median_monthly_return)}" if median_monthly_return is not None else "—",
            "change": f"{trailing_negative_months} trailing down month(s)",
            "watch": None,
        },
    ]
    return {
        "rows": rows,
        "note": "Current observations compared with the instrument's own history; no alert threshold is inferred.",
    }


def _compute_calendar_year_returns(nav_points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered_points = _numeric_nav_points(nav_points)
    years = sorted({point["as_of_date"].year for point in ordered_points}, reverse=True)
    annual_returns: list[dict[str, Any]] = []
    latest_as_of = ordered_points[-1]["as_of_date"] if ordered_points else None
    for year in years:
        start_of_year = date(year, 1, 1)
        end_of_year = min(date(year, 12, 31), latest_as_of) if latest_as_of is not None else date(year, 12, 31)
        window = resolve_return_window(
            ordered_points,
            requested_start_date=start_of_year,
            requested_end_date=end_of_year,
            anchor_mode="strictly_before",
        )
        if (
            window is None
            or window.anchor_date.year != year - 1
            or window.end_date.year != year
        ):
            continue
        annual_returns.append(
            {
                "year": year,
                "investment_nav": period_return_percent(window),
                "category_nav": None,
                "index_nav": None,
                **return_window_metadata(window),
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
        "calculated_at": _coerce_utc(snapshot.calculated_at).isoformat().replace("+00:00", "Z"),
    }


def _performance_window_metadata(
    nav_points: list[dict[str, Any]],
    window_name: str,
) -> dict[str, object]:
    if len(nav_points) < 2:
        return {}
    as_of_date = nav_points[-1]["as_of_date"]
    if window_name in {"1W", "1M", "MTD", "YTD", "3M", "6M", "1Y"}:
        spec = named_return_window_spec(window_name, as_of_date)
        window = resolve_return_window(
            nav_points,
            requested_start_date=spec.requested_start_date,
            requested_end_date=spec.requested_end_date,
            anchor_mode=spec.anchor_mode,
        )
    elif window_name in {"3Y", "5Y"}:
        spec = named_return_window_spec(window_name, as_of_date)
        window = resolve_return_window(
            nav_points,
            requested_start_date=spec.requested_start_date,
            requested_end_date=spec.requested_end_date,
            anchor_mode=spec.anchor_mode,
        )
    elif window_name == "Ann.":
        window = resolve_return_window(
            nav_points,
            requested_start_date=nav_points[0]["as_of_date"],
            requested_end_date=as_of_date,
            anchor_mode="on_or_before",
        )
    else:
        window = None
    return return_window_metadata(window) if window is not None else {}


def _growth_of_100(nav_points: list[dict[str, Any]]) -> float | None:
    nav_points = _numeric_nav_points(nav_points)
    if len(nav_points) < 2 or nav_points[0]["value"] <= 0:
        return None
    return nav_points[-1]["value"] / nav_points[0]["value"] * 100


def _validated_shared_quote_point(
    item: object,
    *,
    expected_currency: str,
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    if str(item.get("status") or "").strip().lower() != "complete":
        return None

    quote_basis = _normalize_quote_basis(item.get("quote_basis"))
    metric_family = str(item.get("metric_family") or "").strip().lower()
    row_slot = _quote_basis_row_slot(quote_basis)
    if row_slot is None:
        return None
    try:
        validate_market_data_identity(
            metric_family=metric_family,
            quote_basis=quote_basis,
        )
    except ValueError:
        return None

    value = _safe_decimal(item.get("value"))
    currency = str(item.get("currency") or "").strip().upper()
    as_of_date_raw = str(item.get("as_of_date") or "").strip()
    if (
        value is None
        or not value.is_finite()
        or value <= 0
        or not currency
        or currency != expected_currency
        or not as_of_date_raw
    ):
        return None
    try:
        as_of_date = date.fromisoformat(as_of_date_raw)
    except ValueError:
        return None

    return {
        "as_of_date": as_of_date,
        "value": value,
        "currency": currency,
        "frequency": "daily",
        "adopted_at": None,
        "metric_family": metric_family,
        "quote_basis": quote_basis,
        "provider": item.get("provider"),
        "status": "complete",
        "source": "shared",
        "row_slot": row_slot,
    }


def _group_shared_nav_rows(
    market_data: list[dict[str, object]],
    *,
    expected_currency: str,
) -> list[dict[str, Any]]:
    grouped: dict[date, dict[str, Any]] = {}
    for item in market_data:
        point = _validated_shared_quote_point(
            item,
            expected_currency=expected_currency,
        )
        if point is None:
            continue
        as_of_date = point["as_of_date"]
        target_key = point["row_slot"]
        row = grouped.setdefault(
            as_of_date,
            {
                "as_of_date": as_of_date,
                "currency": point["currency"],
                "frequency": point["frequency"],
                "adopted_at": None,
                "nav": None,
                "nav_with_dividend": None,
                "basis_metadata": {},
            },
        )
        if row.get("frequency") is None:
            row["frequency"] = point["frequency"]
        row[target_key] = point["value"]
        row["basis_metadata"][target_key] = {
            "metric_family": point["metric_family"],
            "quote_basis": point["quote_basis"],
            "provider": point["provider"],
            "status": point["status"],
        }
    return [grouped[key] for key in sorted(grouped)]


def _shared_quote_points_by_basis(
    market_data: list[dict[str, object]],
    *,
    expected_currency: str,
) -> dict[str, list[dict[str, Any]]]:
    points_by_basis: dict[str, list[dict[str, Any]]] = {}
    for item in market_data:
        point = _validated_shared_quote_point(
            item,
            expected_currency=expected_currency,
        )
        if point is None:
            continue
        points_by_basis.setdefault(point["quote_basis"], []).append(
            {
                "as_of_date": point["as_of_date"],
                "value": float(point["value"]),
                "currency": point["currency"],
                "frequency": point["frequency"],
                "adopted_at": point["adopted_at"],
                "metric_family": point["metric_family"],
                "quote_basis": point["quote_basis"],
                "provider": point["provider"],
                "status": point["status"],
                "source": point["source"],
            }
        )
    for points in points_by_basis.values():
        points.sort(key=lambda point: point["as_of_date"])
    return points_by_basis


def _quote_policy_candidates(
    shared_instrument: dict[str, object] | None,
    *,
    role: str,
) -> list[str]:
    if not isinstance(shared_instrument, dict):
        return []
    raw_policy = shared_instrument.get("quote_selection_policy")
    if not isinstance(raw_policy, dict):
        return []
    raw_values = raw_policy.get(role)
    if not isinstance(raw_values, list):
        return []
    candidates: list[str] = []
    for raw_value in raw_values:
        quote_basis = _normalize_quote_basis(raw_value)
        if _quote_basis_row_slot(quote_basis) is not None and quote_basis not in candidates:
            candidates.append(quote_basis)
    return candidates


def _selection_candidates(
    shared_instrument: dict[str, object] | None,
    *,
    role: str,
    preference: str,
    instrument_type: object,
) -> list[str]:
    normalized_preference = (preference or "auto").strip().lower()
    candidates = _quote_policy_candidates(shared_instrument, role=role)
    if not candidates:
        return []
    normalized_instrument_type = str(instrument_type or "").strip().lower()
    if normalized_instrument_type in FUND_INSTRUMENT_TYPES:
        if role in {"trading", "valuation", "reference"}:
            return [basis for basis in candidates if basis == "official_nav"]
        if normalized_preference == "nav":
            return []
        return [basis for basis in candidates if basis in FUND_TOTAL_RETURN_QUOTE_BASES]

    allow_ordinary_nav = _allows_listed_price_return_basis(normalized_instrument_type)
    if normalized_preference == "nav_with_dividend":
        return [basis for basis in candidates if basis in TOTAL_RETURN_QUOTE_BASES]
    if normalized_preference == "nav":
        return (
            [basis for basis in candidates if basis in UNIT_OR_RAW_QUOTE_BASES]
            if allow_ordinary_nav
            else []
        )

    if allow_ordinary_nav:
        return candidates
    return [basis for basis in candidates if basis in TOTAL_RETURN_QUOTE_BASES]


def _return_kind_for_quote_basis(
    quote_basis: str | None,
    *,
    shared_instrument: dict[str, object] | None,
    instrument_type: object,
) -> str | None:
    if quote_basis == "official_nav":
        return "unit_nav_return"
    source_settings = (
        shared_instrument.get("source_settings")
        if isinstance(shared_instrument, dict)
        else None
    )
    resolved = resolve_quote_return_semantics(
        instrument_type=instrument_type,
        quote_basis=quote_basis,
        source_settings=source_settings if isinstance(source_settings, dict) else None,
    )
    if resolved in {"total_return", "price_return"}:
        return resolved
    if (
        str(instrument_type or "").strip().lower() != "index"
        and quote_basis in {"close", "last"}
    ):
        return "price_return"
    return None


def _current_fund_nav_projection(
    shared_instrument: dict[str, object] | None,
) -> dict[str, object] | None:
    if not isinstance(shared_instrument, dict):
        return None
    current_run_id = str(
        shared_instrument.get("current_fund_nav_projection_run_id") or ""
    ).strip()
    runs = shared_instrument.get("fund_nav_projection_runs")
    if not current_run_id or not isinstance(runs, list):
        return None
    return next(
        (
            run
            for run in runs
            if isinstance(run, dict)
            and str(run.get("fund_nav_projection_run_id") or "") == current_run_id
        ),
        None,
    )


def _select_quote_series(
    points_by_basis: dict[str, list[dict[str, Any]]],
    *,
    shared_instrument: dict[str, object] | None,
    role: str,
    preference: str,
    instrument_type: object,
) -> dict[str, object]:
    for quote_basis in _selection_candidates(
        shared_instrument,
        role=role,
        preference=preference,
        instrument_type=instrument_type,
    ):
        points = [
            point
            for point in list(points_by_basis.get(quote_basis) or [])
            if str(point.get("status") or "").strip().lower() == "complete"
            and _normalize_quote_basis(point.get("quote_basis")) == quote_basis
            and str(point.get("metric_family") or "").strip().lower()
            == _quote_basis_metric_family(quote_basis)
        ]
        if not points:
            continue
        return_kind = _return_kind_for_quote_basis(
            quote_basis,
            shared_instrument=shared_instrument,
            instrument_type=instrument_type,
        )
        row_slot = (
            "nav_with_dividend"
            if return_kind == "total_return"
            else _quote_basis_row_slot(quote_basis)
        )
        metric_family = str(points[-1]["metric_family"])
        projection = (
            _current_fund_nav_projection(shared_instrument)
            if str(instrument_type or "").strip().lower() in FUND_INSTRUMENT_TYPES
            and quote_basis == "total_return_nav"
            else None
        )
        projection_status = str(
            (projection or {}).get("projection_status") or "ready"
        ).strip().lower()
        projection_evidence = (projection or {}).get("evidence")
        segment_breaks = (
            list(projection_evidence.get("segment_breaks") or [])
            if isinstance(projection_evidence, dict)
            else []
        )
        return {
            "nav_basis_type": row_slot,
            "nav_basis_source": str(points[-1].get("source") or "shared"),
            "nav_basis_status": (
                "partial" if projection_status == "partial" else "ready"
            ),
            "points": points,
            "rows": [],
            "selected_role": role,
            "selected_metric_family": metric_family,
            "selected_quote_basis": quote_basis,
            "selected_series_type": _quote_basis_series_type(quote_basis),
            "selected_series_label": (
                f"{_quote_basis_label(quote_basis)} · Total Return"
                if return_kind == "total_return" and quote_basis not in TOTAL_RETURN_QUOTE_BASES
                else _quote_basis_label(quote_basis)
            ),
            "selected_date_label": _quote_basis_date_label(quote_basis),
            "return_kind": return_kind,
            "return_series_status": projection_status,
            "return_anchor_date": (projection or {}).get("anchor_date"),
            "return_segment_breaks": segment_breaks,
        }

    return {
        "nav_basis_type": None,
        "nav_basis_source": "unavailable",
        "nav_basis_status": "unavailable",
        "points": [],
        "rows": [],
        "selected_role": role,
        "selected_metric_family": None,
        "selected_quote_basis": None,
        "selected_series_type": None,
        "selected_series_label": None,
        "selected_date_label": None,
        "return_kind": None,
        "return_series_status": "unavailable",
        "return_anchor_date": None,
        "return_segment_breaks": [],
    }


def _rows_with_selected_series(
    rows: list[dict[str, Any]],
    selection: dict[str, object],
) -> list[dict[str, Any]]:
    row_slot = selection.get("nav_basis_type")
    points = selection.get("points")
    selected_quote_basis = _normalize_quote_basis(
        selection.get("selected_quote_basis")
    )
    if row_slot not in {"nav", "nav_with_dividend"} or not isinstance(points, list):
        return rows

    grouped: dict[date, dict[str, Any]] = {row["as_of_date"]: dict(row) for row in rows}
    for row in grouped.values():
        row["basis_metadata"] = dict(row.get("basis_metadata") or {})
        if row_slot == "nav_with_dividend" and selected_quote_basis:
            raw_metadata = row["basis_metadata"].get("nav")
            if (
                isinstance(raw_metadata, dict)
                and _normalize_quote_basis(raw_metadata.get("quote_basis"))
                == selected_quote_basis
            ):
                row["nav"] = None
                row["basis_metadata"].pop("nav", None)

    for point in points:
        if not isinstance(point, dict) or not isinstance(point.get("as_of_date"), date):
            continue
        point_date = point["as_of_date"]
        row = grouped.setdefault(
            point_date,
            {
                "as_of_date": point_date,
                "currency": str(point["currency"]),
                "frequency": point.get("frequency"),
                "adopted_at": point.get("adopted_at"),
                "nav": None,
                "nav_with_dividend": None,
                "basis_metadata": {},
            },
        )
        row[row_slot] = _safe_decimal(point.get("value"))
        row["currency"] = str(point["currency"])
        if row.get("frequency") is None:
            row["frequency"] = point.get("frequency")
        row["basis_metadata"][row_slot] = {
            "metric_family": point.get("metric_family"),
            "quote_basis": point.get("quote_basis"),
            "provider": point.get("provider"),
            "status": point.get("status"),
        }

    return [grouped[key] for key in sorted(grouped)]


def _selection_metadata(selection: dict[str, object]) -> dict[str, object]:
    return {
        "role": selection.get("selected_role"),
        "metric_family": selection.get("selected_metric_family"),
        "quote_basis": selection.get("selected_quote_basis"),
        "series_type": selection.get("selected_series_type"),
        "basis_type": selection.get("nav_basis_type"),
        "label": selection.get("selected_series_label"),
        "date_label": selection.get("selected_date_label"),
        "return_kind": selection.get("return_kind"),
        "return_series_status": selection.get("return_series_status"),
        "return_anchor_date": selection.get("return_anchor_date"),
        "return_segment_breaks": list(
            selection.get("return_segment_breaks") or []
        ),
    }


def _latest_selection_snapshot(
    selection: dict[str, object] | None,
) -> dict[str, object] | None:
    if not isinstance(selection, dict):
        return None
    points = selection.get("points")
    if not isinstance(points, list) or not points:
        return None
    latest_point = points[-1]
    if not isinstance(latest_point, dict):
        return None
    as_of_date = latest_point.get("as_of_date")
    value = latest_point.get("value")
    if not isinstance(as_of_date, date) or isinstance(value, bool) or not isinstance(
        value,
        (int, float),
    ):
        return None
    return {
        **_selection_metadata(selection),
        "date": as_of_date.isoformat(),
        "value": round(float(value), 4),
        "currency": str(latest_point.get("currency") or "") or None,
    }


def _build_chart_payload(
    instrument_id: str,
    nav_points: list[dict[str, Any]],
    currency: str,
    *,
    selection: dict[str, object] | None = None,
    valuation_selection: dict[str, object] | None = None,
    total_return_selection: dict[str, object] | None = None,
) -> dict[str, object]:
    metadata = _selection_metadata(selection or {})
    series_label = str(metadata.get("label") or "Quote")
    return {
        "instrument_id": instrument_id,
        "base_series_type": str(metadata.get("series_type") or "quote"),
        "selected_series": metadata,
        "latest_values": {
            "valuation": _latest_selection_snapshot(valuation_selection),
            "total_return": _latest_selection_snapshot(total_return_selection),
        },
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
        self.facts_repository = SQLAlchemyFactsRepository()
        self.attribute_repository = SQLAlchemyInstrumentAttributeRepository()
        self.manual_profile_repository = SQLAlchemyInstrumentManualProfileRepository()
        self.read_model_repository = SQLAlchemyReadModelRepository()
        self.recalc_repository = SQLAlchemyRecalcJobRepository()
        self.snapshot_repository = SQLAlchemySnapshotRepository()
        self.taxonomy_repository = SQLAlchemyTaxonomyRepository()

    def build_nav_series_payload(
        self,
        session: Session,
        *,
        instrument_id: str,
    ) -> dict[str, object]:
        manual_profile = self.manual_profile_repository.get(session, instrument_id)
        nav_settings = _normalize_nav_settings(
            manual_profile.nav_settings_json if manual_profile is not None else None
        )
        shared_instrument = get_shared_instrument(instrument_id)
        shared_instrument_type = (
            str(shared_instrument.get("instrument_type") or "").strip().lower()
            if isinstance(shared_instrument, dict)
            else ""
        )
        shared_currency = (
            str(shared_instrument.get("currency") or "").strip().upper()
            if isinstance(shared_instrument, dict)
            else ""
        )
        local_instrument = self.instrument_repository.get(session, instrument_id)
        instrument_type = shared_instrument_type or getattr(
            local_instrument,
            "instrument_type",
            None,
        )
        nav_rows = (
            _group_shared_nav_rows(
                list(shared_instrument.get("market_data", [])),
                expected_currency=shared_currency,
            )
            if isinstance(shared_instrument, dict)
            else []
        )
        quote_points_by_basis = (
            _shared_quote_points_by_basis(
                list(shared_instrument.get("market_data", [])),
                expected_currency=shared_currency,
            )
            if isinstance(shared_instrument, dict)
            else {}
        )
        nav_basis_preference = str(nav_settings.get("nav_basis_preference", "auto"))
        selection = _select_quote_series(
            quote_points_by_basis,
            shared_instrument=shared_instrument,
            role="total_return",
            preference=nav_basis_preference,
            instrument_type=instrument_type,
        )
        nav_rows = _rows_with_selected_series(nav_rows, selection)
        selection["rows"] = nav_rows
        source_settings = (
            dict(shared_instrument.get("source_settings") or {})
            if isinstance(shared_instrument, dict)
            else {}
        )
        frequency_context = build_calculation_frequency_context(
            selection["points"],
            expected_frequency=source_settings.get("expected_frequency"),
            market_calendar=str(source_settings.get("market_calendar") or "") or None,
        )
        calculation_dates = {
            point["as_of_date"]
            for point in frequency_context["points"]
            if isinstance(point, dict) and isinstance(point.get("as_of_date"), date)
        }
        return {
            "instrument_id": instrument_id,
            "count": len(selection["rows"]),
            "nav_basis_preference": nav_basis_preference,
            "nav_basis_type": selection["nav_basis_type"],
            "nav_basis_source": selection["nav_basis_source"],
            "nav_basis_status": selection["nav_basis_status"],
            "selected_role": selection.get("selected_role"),
            "selected_metric_family": selection.get("selected_metric_family"),
            "selected_quote_basis": selection.get("selected_quote_basis"),
            "selected_series_type": selection.get("selected_series_type"),
            "selected_series_label": selection.get("selected_series_label"),
            "selected_date_label": selection.get("selected_date_label"),
            "return_kind": selection.get("return_kind"),
            "return_series_status": selection.get("return_series_status"),
            "return_anchor_date": selection.get("return_anchor_date"),
            "return_segment_breaks": list(
                selection.get("return_segment_breaks") or []
            ),
            "calculation_frequency_profile": frequency_context["profile"],
            "compare_settings": {
                "default_benchmark_instrument_id": nav_settings.get("default_benchmark_instrument_id"),
                "peer_instrument_ids": list(nav_settings.get("peer_baseline_instrument_ids") or []),
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
                    "selected_basis_type": selection.get("nav_basis_type"),
                    "selected_value": (
                        float(row.get(selection["nav_basis_type"]))
                        if selection.get("nav_basis_type") in {"nav", "nav_with_dividend"}
                        and row.get(selection["nav_basis_type"]) is not None
                        else None
                    ),
                    "calculation_included": row["as_of_date"] in calculation_dates,
                }
                for row in selection["rows"]
            ],
        }

    def ingest_instrument_holding_snapshot(
        self,
        session: Session,
        *,
        instrument_id: str,
        as_of_date: date,
        source_cutoff_at: datetime,
        methodology_version: str,
        source_record_id: str | None,
        positions: list[dict[str, Any]],
        auto_recalculate: bool,
    ) -> dict[str, object]:
        session.scalar(
            select(InstrumentDetail.instrument_id)
            .where(InstrumentDetail.instrument_id == instrument_id)
            .with_for_update()
        )
        canonical_positions = sorted(
            positions,
            key=lambda item: json.dumps(
                serialize_payload(item),
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        source_cutoff_at = _coerce_utc(source_cutoff_at)
        payload_hash = _hash_payload(
            {
                "instrument_id": instrument_id,
                "as_of_date": as_of_date.isoformat(),
                "source_cutoff_at": source_cutoff_at.isoformat(),
                "methodology_version": methodology_version,
                "source_record_id": source_record_id,
                "positions": canonical_positions,
            }
        )
        write_result = self.facts_repository.append_holding_snapshot(
            session,
            holding_snapshot_id=f"holding:{instrument_id}:{as_of_date.isoformat()}:{payload_hash}",
            instrument_id=instrument_id,
            as_of_date=as_of_date,
            source_cutoff_at=source_cutoff_at,
            methodology_version=methodology_version,
            input_hash=payload_hash,
            source_record_id=source_record_id,
            positions=canonical_positions,
        )
        record = write_result.record

        execution = None
        if auto_recalculate and write_result.became_current:
            execution = self.execute_recalc(
                session,
                instrument_id=instrument_id,
                job_type="exposure",
                trigger_type="fact_adopted",
                trigger_ref_type="holding_snapshot",
                trigger_ref_id=record.holding_snapshot_id,
            )
        return {
            "instrument_id": instrument_id,
            "holding_snapshot_id": record.holding_snapshot_id,
            "position_count": len(record.positions),
            "as_of_date": record.as_of_date.isoformat(),
            "created": write_result.created,
            "is_current": record.is_current,
            "auto_recalculated": execution is not None,
            "execution": execution,
        }

    def execute_recalc(
        self,
        session: Session,
        *,
        instrument_id: str,
        job_type: str,
        trigger_type: str,
        trigger_ref_type: str | None,
        trigger_ref_id: str | None,
        commit: bool = False,
    ) -> dict[str, object]:
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
        lease_token = str(record.lease_token or "")
        try:
            with session.begin_nested():
                result = self._execute_recalc_job(
                    session,
                    instrument_id=record.instrument_id,
                    job_type=record.job_type,
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
        source_watermark = str(result.get("source_watermark_at_end") or "").strip() or None
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
                    trigger_ref_type="market_data_updated_at",
                    trigger_ref_id=source_watermark,
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
    ) -> dict[str, object]:
        instrument = self.instrument_repository.get(session, instrument_id)
        if instrument is None:
            raise ValueError(f"Instrument not found: {instrument_id}")

        shared_instrument = get_shared_instrument(instrument_id)
        source_watermark_at_start = _shared_calculation_watermark(shared_instrument)
        shared_instrument_type = (
            str(shared_instrument.get("instrument_type") or "").strip().lower()
            if isinstance(shared_instrument, dict)
            else ""
        )
        if (
            shared_instrument is not None
            and shared_instrument_type in LOCAL_DETAIL_INSTRUMENT_TYPES
        ):
            instrument = sync_local_instrument(session, shared_instrument)
            if instrument is None:
                raise RuntimeError("Supported Watchlist instrument failed to materialize")

        now = _utcnow()
        manual_profile = self.manual_profile_repository.get(session, instrument_id)
        nav_settings = _normalize_nav_settings(
            manual_profile.nav_settings_json if manual_profile is not None else None
        )
        shared_market_data = list((shared_instrument or {}).get("market_data", []))
        shared_currency = str((shared_instrument or {}).get("currency") or "").strip().upper()
        nav_rows = _group_shared_nav_rows(
            shared_market_data,
            expected_currency=shared_currency,
        )
        quote_points_by_basis = _shared_quote_points_by_basis(
            shared_market_data,
            expected_currency=shared_currency,
        )
        uses_shared_market_data = isinstance(shared_instrument, dict)
        nav_basis_preference = str(nav_settings.get("nav_basis_preference", "auto"))
        nav_selection = _select_quote_series(
            quote_points_by_basis,
            shared_instrument=shared_instrument,
            role="total_return",
            preference=nav_basis_preference,
            instrument_type=instrument.instrument_type,
        )
        nav_rows = _rows_with_selected_series(nav_rows, nav_selection)
        nav_selection["rows"] = nav_rows
        source_settings = (
            dict(shared_instrument.get("source_settings") or {})
            if isinstance(shared_instrument, dict)
            else {}
        )
        frequency_context = build_calculation_frequency_context(
            nav_selection["points"],
            expected_frequency=source_settings.get("expected_frequency"),
            market_calendar=str(source_settings.get("market_calendar") or "") or None,
        )
        calculation_nav_points = frequency_context["points"]
        calculation_frequency_profile = frequency_context["profile"]
        quote_selection = _select_quote_series(
            quote_points_by_basis,
            shared_instrument=shared_instrument,
            role="chart",
            preference=nav_basis_preference,
            instrument_type=instrument.instrument_type,
        )
        valuation_selection = _select_quote_series(
            quote_points_by_basis,
            shared_instrument=shared_instrument,
            role="valuation",
            preference=nav_basis_preference,
            instrument_type=instrument.instrument_type,
        )
        market_data_source_cutoff = (
            source_watermark_at_start
            if uses_shared_market_data and source_watermark_at_start is not None
            else now
        )
        holding_snapshot = self.facts_repository.get_current_holding_snapshot(
            session,
            instrument_id=instrument_id,
        )
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
        current_drawdown = _current_drawdown(calculation_nav_points)
        if current_drawdown is not None:
            watchlist_attributes["current_drawdown"] = current_drawdown
        else:
            watchlist_attributes.pop("current_drawdown", None)

        performance_snapshot = self.snapshot_repository.get_current_performance(session, instrument_id)
        risk_snapshot = self.snapshot_repository.get_current_risk(session, instrument_id)
        exposure_snapshot = self.snapshot_repository.get_current_exposure(session, instrument_id)

        if job_type in {"performance", "all"}:
            if calculation_nav_points:
                performance_snapshot = self._replace_performance_snapshot(
                    session,
                    instrument_id=instrument_id,
                    nav_points=calculation_nav_points,
                    calculation_frequency_profile=calculation_frequency_profile,
                    source_cutoff_at=market_data_source_cutoff,
                    now=now,
                )
                risk_snapshot = self._replace_risk_snapshot(
                    session,
                    instrument_id=instrument_id,
                    nav_points=calculation_nav_points,
                    calculation_frequency_profile=calculation_frequency_profile,
                    source_cutoff_at=market_data_source_cutoff,
                    now=now,
                )
            else:
                self.snapshot_repository.clear_performance(session, instrument_id=instrument_id)
                self.snapshot_repository.clear_risk(session, instrument_id=instrument_id)
                performance_snapshot = None
                risk_snapshot = None

        if job_type in {"exposure", "all"} and holding_snapshot is not None:
            exposure_snapshot = self._replace_exposure_snapshot(
                session,
                instrument_id=instrument_id,
                holding_snapshot=holding_snapshot,
                now=now,
            )

        materialized_market_source_cutoff = (
            _coerce_utc(getattr(performance_snapshot, "source_cutoff_at", None))
            or _coerce_utc(getattr(risk_snapshot, "source_cutoff_at", None))
            or market_data_source_cutoff
        )

        summary_payload = self._summary_payload(
            instrument=instrument,
            nav_selection=nav_selection,
            valuation_selection=valuation_selection,
            performance_snapshot=performance_snapshot,
            risk_snapshot=risk_snapshot,
            exposure_snapshot=exposure_snapshot,
            attributes=raw_attributes,
            taxonomy_context=taxonomy_context,
            calculation_frequency_profile=calculation_frequency_profile,
            source_settings=source_settings,
            source_cutoff_at=materialized_market_source_cutoff,
            now=now,
        )
        chart_payload = _build_chart_payload(
            instrument_id,
            quote_selection["points"],
            (
                quote_selection["points"][-1]["currency"]
                if quote_selection["points"]
                else shared_currency
            ),
            selection=quote_selection,
            valuation_selection=valuation_selection,
            total_return_selection=nav_selection,
        )
        peer_comparison = self._peer_comparison_payload(
            session,
            instrument_id=instrument_id,
            taxonomy_node=taxonomy_node,
            performance_snapshot=performance_snapshot,
            risk_snapshot=risk_snapshot,
        )
        performance_payload = self._performance_payload(
            performance_snapshot=performance_snapshot,
            peer_comparison=peer_comparison,
            nav_points=calculation_nav_points,
            calculation_frequency_profile=calculation_frequency_profile,
        )
        risk_payload = self._risk_payload(
            risk_snapshot=risk_snapshot,
            performance_snapshot=performance_snapshot,
            peer_comparison=peer_comparison,
            nav_points=calculation_nav_points,
            calculation_frequency_profile=calculation_frequency_profile,
        )
        exposure_payload = self._exposure_payload(exposure_snapshot)
        holdings_payload = self._holdings_payload(holding_snapshot)
        for model_class, payload in (
            (InstrumentSummaryReadModel, summary_payload),
            (InstrumentChartReadModel, chart_payload),
            (InstrumentPerformanceReadModel, performance_payload),
            (InstrumentRiskReadModel, risk_payload),
            (InstrumentExposureReadModel, exposure_payload),
            (InstrumentExposureHoldingsReadModel, holdings_payload),
        ):
            self.read_model_repository.upsert_payload_read_model(
                session,
                model_class=model_class,
                instrument_id=instrument_id,
                payload_json=payload,
                data_freshness_status=summary_payload["freshness"]["data_freshness_status"],
                last_recalculated_at=now,
                source_cutoff_at=materialized_market_source_cutoff,
                materialization_version=WATCHLIST_MATERIALIZATION_VERSION,
            )

        self._refresh_watchlist_rows(
            session,
            instrument=instrument,
            summary_payload=summary_payload,
            attributes=watchlist_attributes,
            performance_snapshot=performance_snapshot,
            risk_snapshot=risk_snapshot,
            exposure_snapshot=exposure_snapshot,
            now=now,
        )

        shared_instrument_at_end = (
            get_shared_instrument(instrument_id) if uses_shared_market_data else shared_instrument
        )
        source_watermark_at_end = _shared_calculation_watermark(
            shared_instrument_at_end
        )
        source_changed_during_recalc = bool(
            uses_shared_market_data and source_watermark_at_end != source_watermark_at_start
        )

        return {
            "instrument_id": instrument_id,
            "job_type": job_type,
            "performance_snapshot_id": getattr(performance_snapshot, "snapshot_id", None),
            "risk_snapshot_id": getattr(risk_snapshot, "snapshot_id", None),
            "exposure_snapshot_id": getattr(exposure_snapshot, "snapshot_id", None),
            "source_watermark_at_start": (
                source_watermark_at_start.isoformat().replace("+00:00", "Z")
                if source_watermark_at_start is not None
                else None
            ),
            "source_watermark_at_end": (
                source_watermark_at_end.isoformat().replace("+00:00", "Z")
                if source_watermark_at_end is not None
                else None
            ),
            "source_changed_during_recalc": source_changed_during_recalc,
            "completed_at": now.isoformat(),
        }

    def _replace_performance_snapshot(
        self,
        session: Session,
        *,
        instrument_id: str,
        nav_points: list[dict[str, Any]],
        calculation_frequency_profile: dict[str, object],
        source_cutoff_at: datetime,
        now: datetime,
    ):
        latest = nav_points[-1]
        as_of_date = latest["as_of_date"]
        windows = {
            "return_ytd": "YTD",
            "return_1w": "1W",
            "return_mtd": "MTD",
            "return_1m": "1M",
            "return_3m": "3M",
            "return_6m": "6M",
            "return_1y": "1Y",
        }
        returns: dict[str, Decimal | None] = {}
        for key, window_name in windows.items():
            spec = named_return_window_spec(window_name, as_of_date)
            window = resolve_return_window(
                nav_points,
                requested_start_date=spec.requested_start_date,
                requested_end_date=spec.requested_end_date,
                anchor_mode=spec.anchor_mode,
            )
            returns[key] = (
                _safe_decimal(period_return_percent(window))
                if window is not None
                else None
            )

        since_inception_window = resolve_return_window(
            nav_points,
            requested_start_date=nav_points[0]["as_of_date"],
            requested_end_date=as_of_date,
            anchor_mode="on_or_before",
        )
        annualized_return = (
            annualized_return_percent(since_inception_window)
            if since_inception_window is not None
            else None
        )
        annualized_windows = {
            "return_3y_annualized": "3Y",
            "return_5y_annualized": "5Y",
            "return_10y_annualized": "10Y",
        }
        annualized_window_returns: dict[str, Decimal | None] = {}
        for key, window_name in annualized_windows.items():
            spec = named_return_window_spec(window_name, as_of_date)
            window = resolve_return_window(
                nav_points,
                requested_start_date=spec.requested_start_date,
                requested_end_date=spec.requested_end_date,
                anchor_mode=spec.anchor_mode,
            )
            annualized_window_returns[key] = (
                _safe_decimal(annualized_return_percent(window))
                if window is not None
                else None
            )
        max_drawdown = _compute_drawdown(nav_points)
        calmar = annualized_return / abs(max_drawdown) if annualized_return is not None and max_drawdown not in {None, 0} else None
        return self.snapshot_repository.replace_performance(
            session,
            snapshot_id=f"perf:{instrument_id}:{as_of_date.isoformat()}:{make_recalc_job_id()}",
            instrument_id=instrument_id,
            data={
                "as_of_date": as_of_date,
                "source_cutoff_at": source_cutoff_at,
                "methodology_version": PERFORMANCE_METHODOLOGY_VERSION,
                "input_hash": _hash_payload(
                    {
                        "nav_points": nav_points,
                        "calculation_frequency_profile": calculation_frequency_profile,
                        "return_window_policy": RETURN_WINDOW_POLICY_VERSION,
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
        source_cutoff_at: datetime,
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
                "source_cutoff_at": source_cutoff_at,
                "methodology_version": RISK_METHODOLOGY_VERSION,
                "input_hash": _hash_payload(
                    {
                        "nav_points": nav_points,
                        "calculation_frequency_profile": calculation_frequency_profile,
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

    def _replace_exposure_snapshot(
        self,
        session: Session,
        *,
        instrument_id: str,
        holding_snapshot,
        now: datetime,
    ):
        top10 = sorted(
            (float(item.portfolio_weight or 0) for item in holding_snapshot.positions),
            reverse=True,
        )[:10]
        weighted_duration, duration_weight, total_reported_weight = _weighted_holding_metric(
            holding_snapshot.positions,
            "effective_duration",
        )
        weighted_ytw, ytw_weight, _ = _weighted_holding_metric(
            holding_snapshot.positions,
            "yield_to_worst",
        )
        duration_weight_coverage = (
            duration_weight / total_reported_weight * Decimal("100")
            if total_reported_weight > 0
            else None
        )
        ytw_weight_coverage = (
            ytw_weight / total_reported_weight * Decimal("100")
            if total_reported_weight > 0
            else None
        )
        return self.snapshot_repository.replace_exposure(
            session,
            snapshot_id=f"exposure:{instrument_id}:{holding_snapshot.as_of_date.isoformat()}:{make_recalc_job_id()}",
            instrument_id=instrument_id,
            data={
                "as_of_date": holding_snapshot.as_of_date,
                "source_cutoff_at": holding_snapshot.source_cutoff_at,
                "methodology_version": "canonical-exposure/v3",
                "input_hash": _hash_payload(
                    {
                        "holding_snapshot_id": holding_snapshot.holding_snapshot_id,
                        "holding_input_hash": holding_snapshot.input_hash,
                        "methodology_version": "canonical-exposure/v3",
                    }
                ),
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
                "reported_weight_total": total_reported_weight,
                "duration_weight_coverage": duration_weight_coverage,
                "ytw_weight_coverage": ytw_weight_coverage,
                "weighted_duration": weighted_duration,
                "weighted_maturity": None,
                "weighted_yield_to_worst": weighted_ytw,
                "avg_credit_rating": None,
                "reported_turnover": None,
                "style_box_code": None,
            },
        )

    def _summary_payload(
        self,
        *,
        instrument,
        nav_selection: dict[str, object],
        valuation_selection: dict[str, object] | None = None,
        performance_snapshot,
        risk_snapshot,
        exposure_snapshot,
        attributes: dict[str, object],
        taxonomy_context: dict[str, object],
        source_cutoff_at: datetime,
        now: datetime,
        calculation_frequency_profile: dict[str, object] | None = None,
        source_settings: dict[str, object] | None = None,
    ) -> dict[str, object]:
        calculation_frequency_profile = calculation_frequency_profile or {}
        source_settings = source_settings or {}
        valuation_selection = valuation_selection or {}
        valuation_points = valuation_selection.get("points")
        if not isinstance(valuation_points, list):
            valuation_points = []
        total_return_points = nav_selection.get("points")
        if not isinstance(total_return_points, list):
            total_return_points = []
        latest_unit_nav_point = (
            valuation_points[-1]
            if valuation_points
            and valuation_selection.get("selected_quote_basis") == "official_nav"
            else None
        )
        latest_cumulative_nav_point = (
            total_return_points[-1]
            if total_return_points
            and nav_selection.get("selected_quote_basis") == "total_return_nav"
            else None
        )
        latest_observation_date = (
            nav_selection["points"][-1]["as_of_date"]
            if nav_selection["points"]
            else None
        )
        latest_observation_date_text = (
            latest_observation_date.isoformat()
            if isinstance(latest_observation_date, date)
            else None
        )
        selected_series = _selection_metadata(nav_selection)
        date_label = str(selected_series.get("date_label") or "Last Quote Date")
        return_series_status = str(
            nav_selection.get("return_series_status") or ""
        ).strip().lower()
        return_segment_breaks = list(
            nav_selection.get("return_segment_breaks") or []
        )
        has_unconfirmed_return_break = bool(
            return_series_status == "partial" and return_segment_breaks
        )
        resolved_frequency = str(
            calculation_frequency_profile.get("resolved_frequency") or "daily"
        ).strip().lower()
        resolved_frequency = "daily"
        observation_freshness = assess_latest_observation_freshness(
            latest_observation_date=(
                latest_observation_date
                if isinstance(latest_observation_date, date)
                else None
            ),
            current_date=now.date(),
            resolved_frequency=resolved_frequency,
            expected_frequency=source_settings.get("expected_frequency"),
            market_calendar=source_settings.get("market_calendar"),
            release_lag_days=source_settings.get("release_lag_days"),
        )
        freshness_status = (
            "unavailable"
            if not nav_selection["points"]
            else "partial"
            if has_unconfirmed_return_break
            else "stale"
            if observation_freshness["status"] == "stale"
            else "fresh"
        )
        staleness_reason = (
            "No canonical series available."
            if not nav_selection["points"]
            else "Canonical total-return series stops at an unconfirmed return-series event."
            if has_unconfirmed_return_break
            else observation_freshness.get("reason")
            if observation_freshness["status"] == "stale"
            else None
        )
        return {
            "instrument_id": instrument.instrument_id,
            "instrument_name": instrument.instrument_name,
            "ticker_or_isin": instrument.primary_identifier_value or instrument.instrument_id.upper(),
            "management_firm_name": str(instrument.metadata_json.get("management_firm_name") or "") or None,
            "instrument_attributes": attributes,
            "taxonomy": taxonomy_context,
            "selected_series": selected_series,
            "series_snapshot": {
                "nav_basis_type": nav_selection.get("nav_basis_type"),
                "nav_basis_source": nav_selection.get("nav_basis_source"),
                "selected_role": nav_selection.get("selected_role"),
                "selected_metric_family": nav_selection.get("selected_metric_family"),
                "selected_quote_basis": nav_selection.get("selected_quote_basis"),
                "selected_series_type": nav_selection.get("selected_series_type"),
                "selected_series_label": nav_selection.get("selected_series_label"),
                "selected_date_label": nav_selection.get("selected_date_label"),
                "return_kind": nav_selection.get("return_kind"),
                "return_series_status": nav_selection.get("return_series_status"),
                "return_anchor_date": nav_selection.get("return_anchor_date"),
                "return_segment_breaks": list(
                    nav_selection.get("return_segment_breaks") or []
                ),
                "latest_nav": (
                    latest_unit_nav_point["value"]
                    if latest_unit_nav_point is not None
                    else None
                ),
                "latest_nav_date": (
                    latest_unit_nav_point["as_of_date"].isoformat()
                    if latest_unit_nav_point is not None
                    else None
                ),
                "latest_nav_with_dividend": (
                    latest_cumulative_nav_point["value"]
                    if latest_cumulative_nav_point is not None
                    else None
                ),
                "latest_nav_with_dividend_date": (
                    latest_cumulative_nav_point["as_of_date"].isoformat()
                    if latest_cumulative_nav_point is not None
                    else None
                ),
            },
            "key_stats": [
                {
                    "label": date_label,
                    "value": latest_observation_date_text or "—",
                },
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
                "last_fact_update_at": source_cutoff_at.isoformat().replace("+00:00", "Z"),
                "last_recalculated_at": now.isoformat().replace("+00:00", "Z"),
                "last_successful_snapshot_at": now.isoformat().replace("+00:00", "Z"),
                "staleness_reason": staleness_reason,
                "latest_observation_date": latest_observation_date_text,
                "expected_latest_date": observation_freshness.get(
                    "expected_latest_date"
                ),
                "observation_lag_days": observation_freshness.get("lag_days"),
            },
            "quick_monitoring_items": [],
            "tabs": (
                FUND_DETAIL_TABS
                if str(instrument.instrument_type) in {"public_fund", "private_fund"}
                else LISTED_DETAIL_TABS[str(instrument.instrument_type)]
            ),
        }

    def _peer_comparison_context(self, session: Session) -> dict[str, object]:
        nodes = self.taxonomy_repository.list_nodes(
            session,
            taxonomy_code=INSTRUMENT_TAXONOMY_CODE,
        )
        node_by_id = {str(node.node_id): node for node in nodes}
        assignments = self.taxonomy_repository.list_assignments(
            session,
            taxonomy_code=INSTRUMENT_TAXONOMY_CODE,
        )
        active_peer_instrument_ids = _active_peer_instrument_ids(session)
        assigned_node_by_asset = {
            str(assignment.instrument_id): node_by_id.get(str(assignment.node_id))
            for assignment in assignments
            if assignment.node_id
            and str(assignment.instrument_id) in active_peer_instrument_ids
        }
        attributes_by_asset: dict[str, dict[str, object]] = {}
        for value in self.attribute_repository.get_values_for_assets(
            session,
            sorted(active_peer_instrument_ids),
        ):
            attributes = attributes_by_asset.setdefault(str(value.instrument_id), {})
            attributes.setdefault(str(value.attribute_key), value.value_json)
        performance_by_asset: dict[str, object] = {}
        for snapshot in self.snapshot_repository.list_current_performance(
            session,
            instrument_ids=sorted(active_peer_instrument_ids),
        ):
            if snapshot.methodology_version == PERFORMANCE_METHODOLOGY_VERSION:
                performance_by_asset.setdefault(str(snapshot.instrument_id), snapshot)
        risk_by_asset: dict[str, object] = {}
        for snapshot in self.snapshot_repository.list_current_risk(
            session,
            instrument_ids=sorted(active_peer_instrument_ids),
        ):
            if snapshot.methodology_version == RISK_METHODOLOGY_VERSION:
                risk_by_asset.setdefault(str(snapshot.instrument_id), snapshot)
        return {
            "node_by_id": node_by_id,
            "assigned_node_by_asset": assigned_node_by_asset,
            "attributes_by_asset": attributes_by_asset,
            "active_peer_instrument_ids": active_peer_instrument_ids,
            "performance_by_asset": performance_by_asset,
            "risk_by_asset": risk_by_asset,
        }

    def _peer_comparison_payload(
        self,
        session: Session,
        *,
        instrument_id: str,
        taxonomy_node,
        performance_snapshot,
        risk_snapshot,
        context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        assigned_path_node_ids = _node_path_node_ids(taxonomy_node)
        if taxonomy_node is None or not assigned_path_node_ids:
            return {
                "status": "missing_taxonomy",
                "comparison_policy_version": PEER_COMPARISON_POLICY_VERSION,
                "as_of_date": (
                    performance_snapshot.as_of_date.isoformat()
                    if performance_snapshot is not None
                    else None
                ),
                "taxonomy_code": INSTRUMENT_TAXONOMY_CODE,
                "assigned_node_id": None,
                "assigned_path": [],
                "peer_node_id": None,
                "peer_path": [],
                "fallback_levels": 0,
                "candidate_count": 0,
                "sample_count": 0,
                "peer_dimensions": {},
                "excluded_dimension_mismatch_count": 0,
                "excluded_mismatched_as_of_count": 0,
                "metrics": [],
                "summary": {},
            }

        if not _peer_taxonomy_is_comparable(taxonomy_node):
            return {
                "status": "taxonomy_not_comparable",
                "comparison_policy_version": PEER_COMPARISON_POLICY_VERSION,
                "as_of_date": (
                    performance_snapshot.as_of_date.isoformat()
                    if performance_snapshot is not None
                    else None
                ),
                "taxonomy_code": INSTRUMENT_TAXONOMY_CODE,
                "assigned_node_id": getattr(taxonomy_node, "node_id", None),
                "assigned_path": _node_path_labels(taxonomy_node),
                "peer_node_id": None,
                "peer_path": [],
                "fallback_levels": 0,
                "candidate_count": 0,
                "sample_count": 0,
                "peer_dimensions": {},
                "excluded_dimension_mismatch_count": 0,
                "excluded_mismatched_as_of_count": 0,
                "metrics": [],
                "summary": {},
            }

        context = context or self._peer_comparison_context(session)
        node_by_id = dict(context["node_by_id"])
        assigned_node_by_asset = dict(context["assigned_node_by_asset"])
        attributes_by_asset = dict(context.get("attributes_by_asset") or {})
        active_peer_instrument_ids = set(context["active_peer_instrument_ids"])
        performance_by_asset = dict(context["performance_by_asset"])
        if performance_snapshot is not None and instrument_id in active_peer_instrument_ids:
            performance_by_asset[str(instrument_id)] = performance_snapshot
        risk_by_asset = dict(context["risk_by_asset"])
        if risk_snapshot is not None and instrument_id in active_peer_instrument_ids:
            risk_by_asset[str(instrument_id)] = risk_snapshot

        target_geographic_exposure = _peer_geographic_exposure(
            taxonomy_node,
            attributes_by_asset.get(instrument_id),
        )
        if target_geographic_exposure is None:
            return {
                "status": "missing_peer_dimension",
                "comparison_policy_version": PEER_COMPARISON_POLICY_VERSION,
                "as_of_date": (
                    performance_snapshot.as_of_date.isoformat()
                    if performance_snapshot is not None
                    else None
                ),
                "taxonomy_code": INSTRUMENT_TAXONOMY_CODE,
                "assigned_node_id": getattr(taxonomy_node, "node_id", None),
                "assigned_path": _node_path_labels(taxonomy_node),
                "peer_node_id": None,
                "peer_path": [],
                "fallback_levels": 0,
                "candidate_count": 0,
                "sample_count": 0,
                "peer_dimensions": {},
                "excluded_dimension_mismatch_count": 0,
                "excluded_mismatched_as_of_count": 0,
                "metrics": [],
                "summary": {
                    "missing_dimensions": ["primary_geographic_exposure"],
                },
            }

        performance_as_of_date = getattr(
            performance_snapshot, "as_of_date", None
        )
        comparable_performance_ids = {
            candidate_instrument_id
            for candidate_instrument_id, candidate_snapshot in performance_by_asset.items()
            if getattr(candidate_snapshot, "as_of_date", None)
            == performance_as_of_date
        }

        selected_peer_node_id = assigned_path_node_ids[-1]
        peer_node = node_by_id.get(selected_peer_node_id) or taxonomy_node
        taxonomy_peer_instrument_ids = sorted(
            candidate_instrument_id
            for candidate_instrument_id, assigned_node in assigned_node_by_asset.items()
            if candidate_instrument_id != instrument_id
            and str(getattr(assigned_node, "node_id", "") or "") == selected_peer_node_id
        )
        eligible_peer_instrument_ids = sorted(
            candidate_instrument_id
            for candidate_instrument_id in taxonomy_peer_instrument_ids
            if _peer_geographic_exposure(
                assigned_node_by_asset.get(candidate_instrument_id),
                attributes_by_asset.get(candidate_instrument_id),
            )
            == target_geographic_exposure
        )
        selected_peer_instrument_ids = sorted(
            candidate_instrument_id
            for candidate_instrument_id in eligible_peer_instrument_ids
            if candidate_instrument_id in comparable_performance_ids
        )
        excluded_mismatched_as_of_count = sum(
            1
            for candidate_instrument_id in eligible_peer_instrument_ids
            if (
                performance_by_asset.get(candidate_instrument_id) is not None
                and getattr(
                    performance_by_asset[candidate_instrument_id],
                    "as_of_date",
                    None,
                )
                != performance_as_of_date
            )
        )
        comparison_instrument_ids = [instrument_id, *eligible_peer_instrument_ids]
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
            mismatched_as_of_count = 0
            target_as_of_date = getattr(target_snapshot, "as_of_date", None)
            for candidate_instrument_id in comparison_instrument_ids:
                candidate_snapshot = (
                    performance_by_asset.get(candidate_instrument_id)
                    if source == "performance"
                    else risk_by_asset.get(candidate_instrument_id)
                )
                if (
                    candidate_snapshot is not None
                    and getattr(candidate_snapshot, "as_of_date", None)
                    != target_as_of_date
                ):
                    if candidate_instrument_id != instrument_id:
                        mismatched_as_of_count += 1
                    continue
                candidate_value = _safe_float(getattr(candidate_snapshot, attr, None))
                if candidate_value is not None:
                    samples.append((candidate_instrument_id, candidate_value))
            peer_values = [
                value
                for candidate_instrument_id, value in samples
                if candidate_instrument_id != instrument_id
            ]
            if not peer_values:
                continue
            ranking_samples = list(samples)
            if instrument_id not in {sample_id for sample_id, _ in ranking_samples}:
                ranking_samples.append((instrument_id, target_value))
            ranking = (
                _rank_metric_value(
                    value=target_value,
                    samples=ranking_samples,
                    direction=direction,
                )
                if len(peer_values) >= PEER_PERCENTILE_MIN_PEERS
                else {
                    "rank": None,
                    "percentile": None,
                    "quartile": None,
                    "sample_count": len(peer_values) + 1,
                }
            )
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
                    "as_of_date": (
                        target_as_of_date.isoformat()
                        if isinstance(target_as_of_date, date)
                        else None
                    ),
                    "excluded_mismatched_as_of_count": mismatched_as_of_count,
                    **ranking,
                }
            )

        ranked_metric_count = sum(
            1 for row in metric_rows if row.get("percentile") is not None
        )
        status = (
            "ready"
            if ranked_metric_count
            else "limited_sample"
            if metric_rows
            else "insufficient_data"
        )
        return {
            "status": status,
            "comparison_policy_version": PEER_COMPARISON_POLICY_VERSION,
            "as_of_date": (
                performance_as_of_date.isoformat()
                if isinstance(performance_as_of_date, date)
                else None
            ),
            "taxonomy_code": INSTRUMENT_TAXONOMY_CODE,
            "assigned_node_id": getattr(taxonomy_node, "node_id", None),
            "assigned_path": _node_path_labels(taxonomy_node),
            "peer_node_id": selected_peer_node_id,
            "peer_path": _node_path_labels(peer_node),
            "fallback_levels": 0,
            "candidate_count": len(eligible_peer_instrument_ids),
            "sample_count": len(selected_peer_instrument_ids),
            "peer_dimensions": {
                "primary_geographic_exposure": target_geographic_exposure,
            },
            "excluded_dimension_mismatch_count": (
                len(taxonomy_peer_instrument_ids) - len(eligible_peer_instrument_ids)
            ),
            "excluded_mismatched_as_of_count": excluded_mismatched_as_of_count,
            "metrics": metric_rows,
            "summary": {
                "available_metric_count": len(metric_rows),
                "ranked_metric_count": ranked_metric_count,
                "minimum_peers_for_percentile": PEER_PERCENTILE_MIN_PEERS,
            },
        }

    def current_peer_comparison(
        self,
        session: Session,
        *,
        instrument_id: str,
        context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        context = context or self._peer_comparison_context(session)
        assigned_node_by_asset = dict(context["assigned_node_by_asset"])
        performance_by_asset = dict(context["performance_by_asset"])
        risk_by_asset = dict(context["risk_by_asset"])
        return self._peer_comparison_payload(
            session,
            instrument_id=instrument_id,
            taxonomy_node=assigned_node_by_asset.get(instrument_id),
            performance_snapshot=performance_by_asset.get(instrument_id),
            risk_snapshot=risk_by_asset.get(instrument_id),
            context=context,
        )

    def peer_watchlist_attribute_overrides(
        self,
        session: Session,
        *,
        instrument_ids: list[str],
    ) -> dict[str, dict[str, object]]:
        if not instrument_ids:
            return {}
        context = self._peer_comparison_context(session)
        return {
            instrument_id: _peer_watchlist_attributes(
                self.current_peer_comparison(
                    session,
                    instrument_id=instrument_id,
                    context=context,
                )
            )
            for instrument_id in instrument_ids
        }

    def apply_current_peer_comparison(
        self,
        session: Session,
        *,
        instrument_id: str,
        performance_payload: dict[str, object] | None = None,
        risk_payload: dict[str, object] | None = None,
    ) -> tuple[dict[str, object] | None, dict[str, object] | None]:
        try:
            peer_comparison = self.current_peer_comparison(
                session,
                instrument_id=instrument_id,
            )
        except SharedInstrumentRegistryError as error:
            peer_comparison = {
                "status": "unavailable",
                "reason": str(error),
                "comparison_policy_version": PEER_COMPARISON_POLICY_VERSION,
                "metrics": [],
                "summary": {},
            }
        return (
            _performance_payload_with_peer_comparison(
                performance_payload,
                peer_comparison,
            )
            if performance_payload is not None
            else None,
            _risk_payload_with_peer_comparison(risk_payload, peer_comparison)
            if risk_payload is not None
            else None,
        )

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
            "1M": _safe_float(peer_metrics_by_key.get("return_1m", {}).get("peer_median")),
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
                {"window": "1M", "investment_nav": _safe_float(performance_snapshot.return_1m), "category_nav": category_value_by_window["1M"], "index_nav": None},
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
                {
                    **row,
                    **_performance_window_metadata(nav_points, str(row["window"])),
                }
                for row in trailing_returns
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
            "return_window_policy": RETURN_WINDOW_POLICY_VERSION,
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
        has_unresolved_gaps = int(
            calculation_frequency_profile.get("gap_count") or 0
        ) > 0
        volatility = _safe_float(getattr(risk_snapshot, "volatility", None))
        annualized_return = _safe_float(getattr(performance_snapshot, "annualized_return", None))
        drawdown_summary = _compute_drawdown_summary(nav_points)
        current_drawdown = _current_drawdown(nav_points)
        current_watch = _build_current_risk_watch(nav_points, drawdown_summary)
        risk_structure = _build_risk_structure(nav_points, drawdown_summary)
        change_monitor = _build_risk_change_monitor(nav_points, drawdown_summary)
        peer_metrics_by_key = {
            str(row.get("metric_key")): row
            for row in (peer_comparison or {}).get("metrics", [])
            if isinstance(row, dict)
        }
        return {
            "risk_overview": {
                "exposure_risk_score": None,
                "risk_level": None,
                "risk_vs_category": None,
                "return_vs_category": None,
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
            "data_quality": {
                "status": (
                    "partial_missing_observations"
                    if has_unresolved_gaps
                    else "ready"
                ),
                "gap_count": int(
                    calculation_frequency_profile.get("gap_count") or 0
                ),
                "gap_detection_basis": calculation_frequency_profile.get(
                    "gap_detection_basis"
                ),
                "note": (
                    "Metrics use the available canonical observations; missing sessions may understate intragap path variation."
                    if has_unresolved_gaps
                    else None
                ),
            },
            "calculation_frequency_profile": calculation_frequency_profile,
            "snapshot_metadata": _snapshot_metadata(risk_snapshot),
        }

    def _exposure_payload(self, exposure_snapshot) -> dict[str, object]:
        return {
            "allocation_blocks": {},
            "style_box": {
                "weighted_duration": _safe_float(getattr(exposure_snapshot, "weighted_duration", None)),
                "yield_to_worst": _safe_float(getattr(exposure_snapshot, "weighted_yield_to_worst", None)),
                "reported_weight_total": _safe_float(getattr(exposure_snapshot, "reported_weight_total", None)),
                "duration_weight_coverage": _safe_float(getattr(exposure_snapshot, "duration_weight_coverage", None)),
                "yield_to_worst_weight_coverage": _safe_float(getattr(exposure_snapshot, "ytw_weight_coverage", None)),
            } if exposure_snapshot is not None else None,
            "liquidity_leverage": None,
            "valuation_statistics": None,
            "holdings_summary": {
                "total_holdings": getattr(exposure_snapshot, "holding_count", None),
                "top10_concentration": _safe_float(getattr(exposure_snapshot, "top10_concentration", None)),
            } if exposure_snapshot is not None else None,
            "snapshot_metadata": _snapshot_metadata(exposure_snapshot),
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
        return {
            "rows": rows,
            "page": 1,
            "page_size": len(rows),
            "total_rows": len(rows),
            "snapshot_metadata": _snapshot_metadata(holding_snapshot),
        }

    def _refresh_watchlist_rows(
        self,
        session: Session,
        *,
        instrument,
        summary_payload: dict[str, object],
        attributes: dict[str, object],
        performance_snapshot,
        risk_snapshot,
        exposure_snapshot,
        now: datetime,
    ) -> None:
        existing_rows = self.read_model_repository.list_watchlist_rows_for_instrument(session, instrument.instrument_id)
        if not existing_rows:
            return
        freshness = summary_payload.get("freshness", {})
        row_attributes = dict(attributes)
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
                    "materialization_version": WATCHLIST_MATERIALIZATION_VERSION,
                    "return_ytd": getattr(performance_snapshot, "return_ytd", None),
                    "return_1w": getattr(performance_snapshot, "return_1w", None),
                    "return_mtd": getattr(performance_snapshot, "return_mtd", None),
                    "return_1m": getattr(performance_snapshot, "return_1m", None),
                    "return_3m": getattr(performance_snapshot, "return_3m", None),
                    "return_6m": getattr(performance_snapshot, "return_6m", None),
                    "return_1y": getattr(performance_snapshot, "return_1y", None),
                    "annualized_return": getattr(performance_snapshot, "annualized_return", None),
                    "return_3y": getattr(performance_snapshot, "return_3y_annualized", None),
                    "return_5y": getattr(performance_snapshot, "return_5y_annualized", None),
                    "max_drawdown": getattr(performance_snapshot, "max_drawdown", None),
                    "volatility": getattr(risk_snapshot, "volatility", None),
                    "sharpe_ratio": getattr(risk_snapshot, "sharpe_ratio", None),
                    "duration": getattr(exposure_snapshot, "weighted_duration", None),
                    "yield_to_worst": getattr(exposure_snapshot, "weighted_yield_to_worst", None),
                    "aum": None,
                    "avg_credit_rating": getattr(exposure_snapshot, "avg_credit_rating", None),
                    "exposure_updated_at": getattr(exposure_snapshot, "source_cutoff_at", None),
                    "last_nav_date": getattr(
                        performance_snapshot, "as_of_date", None
                    ),
                },
            )
