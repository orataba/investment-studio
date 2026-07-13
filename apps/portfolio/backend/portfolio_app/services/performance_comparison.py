from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
from json import dumps
from math import isclose, sqrt

from sqlalchemy import select

from portfolio_ops_instrument_core.models import TOTAL_RETURN_QUOTE_BASES

from portfolio_app.db.models import PortfolioDailySnapshotModel, PortfolioRecordModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.daily_snapshots import (
    DAILY_SNAPSHOT_CALCULATION_VERSION,
    ensure_portfolio_daily_snapshots,
)
from portfolio_app.services.calculation_frequency import infer_observation_frequency
from portfolio_app.services.instrument_charts import (
    ASSET_RISK_MAX_START_GAP_DAYS,
    canonical_series_failure_reasons,
    canonical_series_points,
    lock_instrument_market_data_in_session,
)
from portfolio_app.services.instrument_registry import InstrumentRegistryError
from portfolio_app.services.performance import (
    PerformanceDataIntegrityError,
    _compound_returns,
    _periods_per_year_from_observations,
    _sample_correlation,
    _sample_covariance,
    _sample_stddev,
    _twr_window_reliability,
    build_portfolio_performance_report_from_snapshots,
)
from portfolio_app.services.performance_reliability import (
    build_performance_history_reliability,
)


PERFORMANCE_COMPARISON_METHOD_VERSION = (
    "performance-comparison.v20260713.materialized-twr-canonical-total-return.v2"
)


def _safe_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def authoritative_calmar_ratio(
    annualized_return: object,
    max_drawdown: object,
) -> float | None:
    resolved_return = _safe_float(annualized_return)
    resolved_drawdown = _safe_float(max_drawdown)
    return (
        resolved_return / abs(resolved_drawdown)
        if resolved_return is not None
        and resolved_drawdown is not None
        and resolved_drawdown < -1e-12
        else None
    )


def _snapshot_payload(row: PortfolioDailySnapshotModel) -> dict[str, object]:
    if not isinstance(row.snapshot_json, dict):
        raise PerformanceDataIntegrityError(
            f"Materialized snapshot '{row.portfolio_id}/{row.as_of_date.isoformat()}' payload must be an object."
        )
    payload = dict(row.snapshot_json)
    payload_date = payload.get("as_of_date")
    try:
        parsed_payload_date = (
            payload_date
            if isinstance(payload_date, date)
            else date.fromisoformat(str(payload_date or "")[:10])
        )
    except ValueError as error:
        raise PerformanceDataIntegrityError(
            f"Materialized snapshot '{row.portfolio_id}/{row.as_of_date.isoformat()}' has an invalid date."
        ) from error
    if parsed_payload_date != row.as_of_date:
        raise PerformanceDataIntegrityError(
            f"Materialized snapshot '{row.portfolio_id}/{row.as_of_date.isoformat()}' date columns disagree."
        )

    payload_return = _safe_float(payload.get("daily_twr"))
    column_return = _safe_float(row.daily_twr)
    if (payload_return is None) != (column_return is None) or (
        payload_return is not None
        and column_return is not None
        and not isclose(payload_return, column_return, rel_tol=0.0, abs_tol=1e-12)
    ):
        raise PerformanceDataIntegrityError(
            f"Materialized snapshot '{row.portfolio_id}/{row.as_of_date.isoformat()}' TWR columns disagree."
        )
    if column_return is not None and column_return < -1.0:
        raise PerformanceDataIntegrityError(
            f"Materialized snapshot '{row.portfolio_id}/{row.as_of_date.isoformat()}' has an impossible TWR below -100%."
        )
    if str(payload.get("nav_coverage_state") or "") != str(
        row.nav_coverage_state
    ):
        raise PerformanceDataIntegrityError(
            f"Materialized snapshot '{row.portfolio_id}/{row.as_of_date.isoformat()}' NAV coverage columns disagree."
        )
    if payload.get("twr_state") not in {
        "linked",
        "carry_forward",
        "broken",
        "reanchor",
        "no_anchor",
    }:
        raise PerformanceDataIntegrityError(
            f"Materialized snapshot '{row.portfolio_id}/{row.as_of_date.isoformat()}' has an invalid TWR state."
        )
    if payload.get("twr_reliability_status") not in {
        "reliable",
        "qualified",
        "unavailable",
    }:
        raise PerformanceDataIntegrityError(
            f"Materialized snapshot '{row.portfolio_id}/{row.as_of_date.isoformat()}' has an invalid TWR reliability status."
        )
    if not isinstance(payload.get("twr_reliability_reasons"), list):
        raise PerformanceDataIntegrityError(
            f"Materialized snapshot '{row.portfolio_id}/{row.as_of_date.isoformat()}' TWR reliability reasons must be a list."
        )
    if not isinstance(payload.get("return_observation_eligible"), bool):
        raise PerformanceDataIntegrityError(
            f"Materialized snapshot '{row.portfolio_id}/{row.as_of_date.isoformat()}' return eligibility must be explicit."
        )
    payload["as_of_date"] = row.as_of_date
    payload["daily_twr"] = column_return
    return payload


def _empty_metrics() -> dict[str, float | None]:
    return {
        "period_return": None,
        "annualized_return": None,
        "annualized_volatility": None,
        "annualized_downside_volatility": None,
        "sharpe_ratio": None,
        "sortino_ratio": None,
        "current_drawdown": None,
        "max_drawdown": None,
        "calmar_ratio": None,
    }


def _empty_relative_metrics() -> dict[str, float | None]:
    return {
        "excess_return": None,
        "tracking_error": None,
        "information_ratio": None,
        "beta": None,
        "correlation": None,
        "upside_capture": None,
        "downside_capture": None,
        "capture_ratio": None,
    }


def _empty_differences() -> dict[str, float | None]:
    return {
        key: None
        for key in (
            "period_return",
            "annualized_return",
            "annualized_volatility",
            "annualized_downside_volatility",
            "sharpe_ratio",
            "sortino_ratio",
            "current_drawdown",
            "max_drawdown",
            "calmar_ratio",
        )
    }


def _metrics_from_returns(
    returns_by_date: dict[date, float],
    *,
    start_anchor_date: date,
    base_currency: str,
) -> dict[str, float | None]:
    ordered = sorted(returns_by_date.items())
    if not ordered:
        return _empty_metrics()
    growth_index = 1.0

    def snapshot(
        point_date: date,
        *,
        point_return: float | None,
        eligible: bool,
    ) -> dict[str, object]:
        nonlocal growth_index
        beginning_nav = growth_index
        if point_return is not None:
            growth_index *= 1.0 + point_return
        return {
            "as_of_date": point_date,
            "base_currency": base_currency,
            "nav_coverage_state": "complete",
            "nav_coverage_reason_codes": [],
            "book_pnl_coverage_state": "complete",
            "book_pnl_coverage_reason_codes": [],
            "stale_price_flag": False,
            "stale_fx_flag": False,
            "market_observation_count": 1 if eligible else 0,
            "return_observation_eligible": eligible,
            "twr_state": "linked",
            "twr_reliability_status": "reliable",
            "twr_reliability_reasons": [],
            "nav": growth_index,
            "beginning_nav": beginning_nav,
            "ending_nav": growth_index,
            "pending_settlement": 0.0,
            "realized_pnl": None,
            "unrealized_pnl": None,
            "income_cash_amount": None,
            "expense_cash_amount": None,
            "cash_currency_gains": None,
            "instrument_currency_gains": None,
            "return_of_capital_amount": None,
            "total_pnl": None,
            "external_cash_in": 0.0,
            "external_cash_out": 0.0,
            "net_external_inflow": 0.0,
            "absolute_change": None,
            "delta": None,
            "daily_twr": point_return,
            "cumulative_twr": None,
            "drawdown": None,
        }

    snapshots = [
        snapshot(start_anchor_date, point_return=None, eligible=False),
        *[
            snapshot(point_date, point_return=point_return, eligible=True)
            for point_date, point_return in ordered
        ],
    ]
    report = build_portfolio_performance_report_from_snapshots(
        {
            "portfolio_id": "performance-comparison-series",
            "base_currency": base_currency,
            "valuation_timezone": "UTC",
            "valuation_cutoff_policy": "latest_complete_eod",
        },
        snapshots,
        transactions=[],
    )
    summary = report["summary"]
    annualized_return = _safe_float(summary.get("annualized_twr"))
    max_drawdown = _safe_float(summary.get("max_drawdown"))
    return {
        "period_return": _safe_float(summary.get("cumulative_twr")),
        "annualized_return": annualized_return,
        "annualized_volatility": _safe_float(summary.get("annualized_volatility")),
        "annualized_downside_volatility": _safe_float(
            summary.get("annualized_downside_volatility")
        ),
        "sharpe_ratio": _safe_float(summary.get("sharpe_ratio")),
        "sortino_ratio": _safe_float(summary.get("sortino_ratio")),
        "current_drawdown": _safe_float(summary.get("current_drawdown")),
        "max_drawdown": max_drawdown,
        "calmar_ratio": authoritative_calmar_ratio(
            annualized_return,
            max_drawdown,
        ),
    }


def _relative_metrics(
    portfolio_returns: dict[date, float],
    benchmark_returns: dict[date, float],
    *,
    start_anchor_date: date,
) -> dict[str, float | None]:
    common_dates = sorted(set(portfolio_returns).intersection(benchmark_returns))
    if len(common_dates) < 2:
        return _empty_relative_metrics()
    portfolio_values = [portfolio_returns[item] for item in common_dates]
    benchmark_values = [benchmark_returns[item] for item in common_dates]
    active_values = [
        portfolio_returns[item] - benchmark_returns[item] for item in common_dates
    ]
    periods_per_year = _periods_per_year_from_observations(
        observation_count=len(active_values),
        start_date=start_anchor_date,
        end_date=common_dates[-1],
    )
    active_standard_deviation = _sample_stddev(active_values)
    tracking_error = (
        active_standard_deviation * sqrt(periods_per_year)
        if active_standard_deviation is not None and periods_per_year is not None
        else None
    )
    active_annualized_mean = (
        sum(active_values) / len(active_values) * periods_per_year
        if periods_per_year is not None
        else None
    )
    benchmark_variance = _sample_covariance(benchmark_values, benchmark_values)
    covariance = _sample_covariance(portfolio_values, benchmark_values)
    up_dates = [item for item in common_dates if benchmark_returns[item] > 0.0]
    down_dates = [item for item in common_dates if benchmark_returns[item] < 0.0]
    upside_benchmark = _compound_returns([benchmark_returns[item] for item in up_dates])
    upside_portfolio = _compound_returns([portfolio_returns[item] for item in up_dates])
    downside_benchmark = _compound_returns([benchmark_returns[item] for item in down_dates])
    downside_portfolio = _compound_returns([portfolio_returns[item] for item in down_dates])
    upside_capture = (
        upside_portfolio / upside_benchmark
        if upside_portfolio is not None
        and upside_benchmark is not None
        and abs(upside_benchmark) > 1e-12
        else None
    )
    downside_capture = (
        downside_portfolio / downside_benchmark
        if downside_portfolio is not None
        and downside_benchmark is not None
        and abs(downside_benchmark) > 1e-12
        else None
    )
    portfolio_period_return = _compound_returns(portfolio_values)
    benchmark_period_return = _compound_returns(benchmark_values)
    return {
        "excess_return": (
            portfolio_period_return - benchmark_period_return
            if portfolio_period_return is not None
            and benchmark_period_return is not None
            else None
        ),
        "tracking_error": tracking_error,
        "information_ratio": (
            active_annualized_mean / tracking_error
            if active_annualized_mean is not None
            and tracking_error is not None
            and tracking_error > 1e-12
            else None
        ),
        "beta": (
            covariance / benchmark_variance
            if covariance is not None
            and benchmark_variance is not None
            and benchmark_variance > 1e-12
            else None
        ),
        "correlation": _sample_correlation(portfolio_values, benchmark_values),
        "upside_capture": upside_capture,
        "downside_capture": downside_capture,
        "capture_ratio": (
            upside_capture / downside_capture
            if upside_capture is not None
            and downside_capture is not None
            and abs(downside_capture) > 1e-12
            else None
        ),
    }


def _comparison_points(
    portfolio_returns: dict[date, float],
    benchmark_returns: dict[date, float],
    *,
    start_anchor_date: date,
) -> list[dict[str, object]]:
    portfolio_index = 1.0
    benchmark_index = 1.0
    points: list[dict[str, object]] = [
        {
            "date": start_anchor_date,
            "portfolio_index": portfolio_index,
            "benchmark_index": benchmark_index,
            "difference": 0.0,
        }
    ]
    for point_date in sorted(portfolio_returns):
        portfolio_index *= 1.0 + portfolio_returns[point_date]
        benchmark_index *= 1.0 + benchmark_returns[point_date]
        points.append(
            {
                "date": point_date,
                "portfolio_index": portfolio_index,
                "benchmark_index": benchmark_index,
                "difference": portfolio_index - benchmark_index,
            }
        )
    return points


def _fingerprint(payload: dict[str, object]) -> str:
    serialized = dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return f"sha256:{sha256(serialized.encode('utf-8')).hexdigest()}"


def _unavailable_payload(
    *,
    portfolio_id: str,
    benchmark_instrument_id: str,
    benchmark_name: str | None,
    base_currency: str,
    start_date: date,
    end_date: date,
    as_of_date: date,
    reasons: list[str],
    coverage: dict[str, object],
    history_reliability: dict[str, object],
    lineage: dict[str, object],
) -> dict[str, object]:
    return {
        "portfolio_id": portfolio_id,
        "benchmark_instrument_id": benchmark_instrument_id,
        "benchmark_name": benchmark_name,
        "base_currency": base_currency,
        "benchmark_currency": coverage.get("benchmark_currency"),
        "market_data_role": "total_return",
        "requested_start_date": start_date,
        "requested_end_date": end_date,
        "as_of_date": as_of_date,
        "status": "unavailable",
        "unavailable_reasons": list(dict.fromkeys(reasons)),
        "coverage": coverage,
        "history_reliability": history_reliability,
        "portfolio_metrics": _empty_metrics(),
        "benchmark_metrics": _empty_metrics(),
        "relative_metrics": _empty_relative_metrics(),
        "differences": _empty_differences(),
        "points": [],
        "lineage": lineage,
    }


def build_performance_comparison(
    *,
    portfolio_id: str,
    benchmark_instrument_id: str,
    start_date: date,
    end_date: date,
    as_of_date: date,
) -> dict[str, object] | None:
    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date")
    if end_date > as_of_date:
        raise ValueError("end_date must be on or before as_of_date")
    normalized_benchmark_id = str(benchmark_instrument_id or "").strip()
    if not normalized_benchmark_id:
        raise ValueError("benchmark_instrument_id is required")

    ensure_portfolio_daily_snapshots(portfolio_id)
    session_factory = get_session_factory()
    with session_factory() as session:
        if session.get_bind().dialect.name == "postgresql":
            session.connection(
                execution_options={"isolation_level": "REPEATABLE READ"}
            )
        portfolio = session.get(PortfolioRecordModel, portfolio_id)
        if portfolio is None:
            return None
        base_currency = str(portfolio.base_currency or "").strip().upper()
        if base_currency not in {"USD", "HKD", "CNY"}:
            raise PerformanceDataIntegrityError(
                f"Portfolio '{portfolio_id}' has an invalid base currency fact."
            )
        snapshot_rows = session.scalars(
            select(PortfolioDailySnapshotModel)
            .where(
                PortfolioDailySnapshotModel.portfolio_id == portfolio_id,
                PortfolioDailySnapshotModel.as_of_date >= start_date,
                PortfolioDailySnapshotModel.as_of_date <= end_date,
            )
            .order_by(PortfolioDailySnapshotModel.as_of_date)
        ).all()
        snapshots = [_snapshot_payload(row) for row in snapshot_rows]
        if any(
            str(snapshot.get("base_currency") or "").strip().upper()
            != base_currency
            for snapshot in snapshots
        ):
            raise PerformanceDataIntegrityError(
                f"Portfolio '{portfolio_id}' materialized snapshot currency does not match its base currency."
            )
        try:
            market_data = lock_instrument_market_data_in_session(
                session,
                instrument_ids=[normalized_benchmark_id],
                as_of_date=as_of_date,
                roles=("total_return",),
            )
        except InstrumentRegistryError:
            raise
        except Exception as error:
            raise InstrumentRegistryError(
                "Failed to lock canonical benchmark total-return data."
            ) from error

        benchmark_core = market_data.instrument_core_by_id.get(normalized_benchmark_id)
        benchmark_name = (
            str(benchmark_core.get("instrument_name") or normalized_benchmark_id)
            if benchmark_core is not None
            else None
        )
        window = market_data.windows_by_role.get("total_return", {}).get(
            normalized_benchmark_id
        )
        dependency = (
            window.calculation_dependency.model_dump(mode="json")
            if window is not None
            else None
        )
        snapshot_versions = sorted(
            {
                str(snapshot.get("calculation_version") or "")
                for snapshot in snapshots
            }
        )
        portfolio_lineage = {
            "portfolio_id": portfolio_id,
            "base_currency": base_currency,
            "requested_start_date": start_date.isoformat(),
            "requested_end_date": end_date.isoformat(),
            "calculation_version": (
                snapshot_versions[0] if len(snapshot_versions) == 1 else None
            ),
            "snapshot_calculated_at": sorted(
                {str(row.calculated_at) for row in snapshot_rows}
            ),
            "snapshot_dates": [row.as_of_date.isoformat() for row in snapshot_rows],
            "snapshot_facts": [
                {
                    "as_of_date": row.as_of_date.isoformat(),
                    "calculated_at": str(row.calculated_at),
                    "daily_twr": snapshot.get("daily_twr"),
                    "return_observation_eligible": snapshot.get(
                        "return_observation_eligible"
                    ),
                    "twr_state": snapshot.get("twr_state"),
                    "twr_reliability_status": snapshot.get(
                        "twr_reliability_status"
                    ),
                    "twr_reliability_reasons": list(
                        snapshot.get("twr_reliability_reasons") or []
                    ),
                }
                for row, snapshot in zip(snapshot_rows, snapshots, strict=True)
            ],
        }
        portfolio_lineage["fingerprint"] = _fingerprint(portfolio_lineage)
        lineage: dict[str, object] = {
            "method_version": PERFORMANCE_COMPARISON_METHOD_VERSION,
            "portfolio": portfolio_lineage,
            "benchmark": dependency,
        }
        lineage["fingerprint"] = _fingerprint(
            {
                "method_version": PERFORMANCE_COMPARISON_METHOD_VERSION,
                "portfolio_id": portfolio_id,
                "benchmark_instrument_id": normalized_benchmark_id,
                "base_currency": base_currency,
                "portfolio_fingerprint": portfolio_lineage["fingerprint"],
                "benchmark_fingerprint": (
                    dependency.get("fingerprint")
                    if isinstance(dependency, dict)
                    else None
                ),
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "as_of_date": as_of_date.isoformat(),
            }
        )

        portfolio_eligible = {
            snapshot["as_of_date"]: point_return
            for snapshot in snapshots
            if bool(snapshot.get("return_observation_eligible"))
            and isinstance(snapshot.get("as_of_date"), date)
            and (point_return := _safe_float(snapshot.get("daily_twr"))) is not None
        }
        coverage: dict[str, object] = {
            "required_observation_count": len(portfolio_eligible),
            "aligned_observation_count": 0,
            "coverage_ratio": 0.0 if portfolio_eligible else None,
            "comparison_start_boundary_date": start_date - timedelta(days=1),
            "benchmark_start_anchor_date": None,
            "benchmark_start_anchor_gap_days": None,
            "calculation_frequency": None,
            "first_aligned_date": None,
            "last_aligned_date": None,
            "benchmark_currency": window.currency if window is not None else None,
            "benchmark_quote_basis": window.quote_basis if window is not None else None,
            "benchmark_metric_family": window.metric_family if window is not None else None,
            "benchmark_resolution_status": (
                window.resolution_status if window is not None else None
            ),
            "benchmark_coverage_status": (
                window.coverage_status if window is not None else None
            ),
            "benchmark_freshness_status": (
                window.freshness_status if window is not None else None
            ),
            "benchmark_reliability_status": (
                window.reliability_status if window is not None else None
            ),
            "benchmark_reason_codes": (
                list(window.reason_codes) if window is not None else []
            ),
            "portfolio_twr_reliability_status": None,
            "portfolio_twr_reliability_reasons": [],
        }
        reasons: list[str] = []
        if not snapshots:
            reasons.append("portfolio_snapshot_window_empty")
        if snapshot_versions != [DAILY_SNAPSHOT_CALCULATION_VERSION]:
            reasons.append("portfolio_snapshot_version_mismatch")
        reliability = _twr_window_reliability(snapshots) if snapshots else {"linkable": False}
        coverage["portfolio_twr_reliability_status"] = reliability.get(
            "twr_reliability_status"
        )
        coverage["portfolio_twr_reliability_reasons"] = list(
            reliability.get("twr_reliability_reasons") or []
        )
        if not bool(reliability.get("linkable")):
            reasons.append("portfolio_twr_unreliable")
        elif reliability.get("twr_reliability_status") != "reliable":
            reasons.append("portfolio_twr_not_reliable")
        if len(portfolio_eligible) < 2:
            reasons.append("insufficient_portfolio_return_observations")
        if window is None:
            reasons.extend(
                canonical_series_failure_reasons(
                    market_data,
                    instrument_id=normalized_benchmark_id,
                    role="total_return",
                )
            )
        else:
            if window.role != "total_return":
                reasons.append("benchmark_role_mismatch")
            if window.quote_basis not in TOTAL_RETURN_QUOTE_BASES:
                reasons.append("benchmark_not_true_total_return_basis")
            if window.reliability_status != "reliable":
                reasons.append("benchmark_reliability_not_reliable")
            if str(window.currency or "").strip().upper() != base_currency:
                reasons.append("benchmark_currency_mismatch")

        benchmark_points = canonical_series_points(
            market_data,
            instrument_id=normalized_benchmark_id,
            role="total_return",
        )
        if not benchmark_points:
            reasons.extend(
                canonical_series_failure_reasons(
                    market_data,
                    instrument_id=normalized_benchmark_id,
                    role="total_return",
                )
            )
        benchmark_value_by_date = {
            point_date: point_value
            for point in benchmark_points
            if isinstance((point_date := point.get("date")), date)
            and (point_value := _safe_float(point.get("value"))) is not None
            and point_date <= end_date
        }
        start_boundary = start_date - timedelta(days=1)
        anchor_dates = [
            point_date
            for point_date in benchmark_value_by_date
            if point_date <= start_boundary
        ]
        anchor_date = max(anchor_dates) if anchor_dates else None
        if anchor_date is None:
            reasons.append("benchmark_start_anchor_missing")
        missing_dates = sorted(set(portfolio_eligible).difference(benchmark_value_by_date))
        if missing_dates:
            reasons.append("benchmark_date_coverage_incomplete")

        aligned_dates = sorted(
            set(portfolio_eligible).intersection(benchmark_value_by_date)
        )
        calculation_frequency = (
            infer_observation_frequency(sorted(portfolio_eligible))
            if len(portfolio_eligible) >= 2
            else None
        )
        anchor_gap_days = (
            (start_boundary - anchor_date).days if anchor_date is not None else None
        )
        if (
            anchor_gap_days is not None
            and calculation_frequency is not None
            and anchor_gap_days
            > ASSET_RISK_MAX_START_GAP_DAYS[calculation_frequency]
        ):
            reasons.append("benchmark_start_anchor_too_old")
        coverage.update(
            {
                "aligned_observation_count": len(aligned_dates),
                "coverage_ratio": (
                    len(aligned_dates) / len(portfolio_eligible)
                    if portfolio_eligible
                    else None
                ),
                "benchmark_start_anchor_date": anchor_date,
                "benchmark_start_anchor_gap_days": anchor_gap_days,
                "calculation_frequency": calculation_frequency,
                "first_aligned_date": aligned_dates[0] if aligned_dates else None,
                "last_aligned_date": aligned_dates[-1] if aligned_dates else None,
            }
        )
        history_reliability = build_performance_history_reliability(
            {
                "start_date": start_boundary,
                "end_date": aligned_dates[-1] if aligned_dates else None,
                "snapshot_count": len(snapshots),
                "return_observation_count": len(portfolio_eligible),
                "risk_return_observation_count": len(aligned_dates),
            }
        )
        if reasons:
            return _unavailable_payload(
                portfolio_id=portfolio_id,
                benchmark_instrument_id=normalized_benchmark_id,
                benchmark_name=benchmark_name,
                base_currency=base_currency,
                start_date=start_date,
                end_date=end_date,
                as_of_date=as_of_date,
                reasons=reasons,
                coverage=coverage,
                history_reliability=history_reliability,
                lineage=lineage,
            )

        assert anchor_date is not None
        previous_benchmark_value = benchmark_value_by_date[anchor_date]
        benchmark_returns: dict[date, float] = {}
        for point_date in sorted(portfolio_eligible):
            point_value = benchmark_value_by_date[point_date]
            if previous_benchmark_value <= 0.0:
                raise PerformanceDataIntegrityError(
                    "Canonical benchmark total-return series contains a non-positive anchor."
                )
            benchmark_returns[point_date] = point_value / previous_benchmark_value - 1.0
            previous_benchmark_value = point_value

        portfolio_metrics = _metrics_from_returns(
            portfolio_eligible,
            start_anchor_date=start_boundary,
            base_currency=base_currency,
        )
        benchmark_metrics = _metrics_from_returns(
            benchmark_returns,
            start_anchor_date=start_boundary,
            base_currency=base_currency,
        )
        if not bool(history_reliability["annualized_return_eligible"]):
            for metrics in (portfolio_metrics, benchmark_metrics):
                metrics["annualized_return"] = None
                metrics["calmar_ratio"] = None
        relative_metrics = _relative_metrics(
            portfolio_eligible,
            benchmark_returns,
            start_anchor_date=start_boundary,
        )
        differences = {
            key: (
                portfolio_metrics[key] - benchmark_metrics[key]
                if portfolio_metrics[key] is not None
                and benchmark_metrics[key] is not None
                else None
            )
            for key in _empty_differences()
        }
        points = _comparison_points(
            portfolio_eligible,
            benchmark_returns,
            start_anchor_date=start_boundary,
        )
        return {
            "portfolio_id": portfolio_id,
            "benchmark_instrument_id": normalized_benchmark_id,
            "benchmark_name": benchmark_name,
            "base_currency": base_currency,
            "benchmark_currency": window.currency if window is not None else None,
            "market_data_role": "total_return",
            "requested_start_date": start_date,
            "requested_end_date": end_date,
            "as_of_date": as_of_date,
            "status": "ready",
            "unavailable_reasons": [],
            "coverage": coverage,
            "history_reliability": history_reliability,
            "portfolio_metrics": portfolio_metrics,
            "benchmark_metrics": benchmark_metrics,
            "relative_metrics": relative_metrics,
            "differences": differences,
            "points": points,
            "lineage": lineage,
        }


__all__ = [
    "PERFORMANCE_COMPARISON_METHOD_VERSION",
    "authoritative_calmar_ratio",
    "build_performance_comparison",
]
