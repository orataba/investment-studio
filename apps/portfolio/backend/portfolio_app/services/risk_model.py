from __future__ import annotations

from copy import deepcopy
from datetime import date
from math import sqrt

import numpy as np
import pandas as pd

from portfolio_app.db.models import PortfolioRecordModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.calculation_frequency import CalculationFrequency
from portfolio_app.services.holdings_market_profile import (
    is_cash_holding_instrument_id,
    is_pending_monetary_holding,
)
from portfolio_app.services.research_solver import (
    RESEARCH_COVARIANCE_MODEL_ID,
    RESEARCH_DEFAULT_MISSING_RETURN_POLICY,
    RESEARCH_RISK_CONTRIBUTION_MODE,
    SUPPORTED_RESEARCH_LOOKBACK_DAYS,
    estimate_covariance,
    normalize_missing_return_policy,
    prepare_return_window_for_covariance,
    research_covariance_parameters_for_window,
    research_min_observations_for_window,
    research_window_start_date,
    risk_contribution_shares,
)

PORTFOLIO_RISK_POLICY_MODEL_NAME = "Production Risk Model"
DEFAULT_PORTFOLIO_RISK_LOOKBACK_DAYS = 90
PORTFOLIO_RISK_WINDOW_LABELS = {
    30: "1M",
    90: "3M",
    180: "6M",
    366: "12M",
    730: "24M",
}
SUPPORTED_COVARIANCE_MODELS = {
    "ewma_vol_shrinkage_corr_covariance",
    "ewma_covariance",
    "sample_covariance",
}
SUPPORTED_CONTRIBUTION_MODES = {"signed", "abs"}


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: object, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _normalize_risk_window(value: object) -> int:
    lookback_days = _safe_int(value, DEFAULT_PORTFOLIO_RISK_LOOKBACK_DAYS)
    if lookback_days not in SUPPORTED_RESEARCH_LOOKBACK_DAYS:
        labels = ", ".join(PORTFOLIO_RISK_WINDOW_LABELS[days] for days in sorted(SUPPORTED_RESEARCH_LOOKBACK_DAYS))
        raise ValueError(f"Risk window must be one of {labels}.")
    return lookback_days


def _normalized_covariance_model(value: object) -> str:
    normalized = str(value or RESEARCH_COVARIANCE_MODEL_ID).strip().lower()
    return normalized if normalized in SUPPORTED_COVARIANCE_MODELS else RESEARCH_COVARIANCE_MODEL_ID


def _normalized_contribution_mode(value: object) -> str:
    normalized = str(value or RESEARCH_RISK_CONTRIBUTION_MODE).strip().lower()
    return normalized if normalized in SUPPORTED_CONTRIBUTION_MODES else RESEARCH_RISK_CONTRIBUTION_MODE


def normalize_portfolio_risk_policy(
    raw_policy: dict[str, object] | None,
) -> dict[str, object]:
    source: dict[str, object] = {
        "covariance_model_id": RESEARCH_COVARIANCE_MODEL_ID,
        "lookback_days": DEFAULT_PORTFOLIO_RISK_LOOKBACK_DAYS,
        "calculation_frequency": "daily",
        "missing_return_policy": RESEARCH_DEFAULT_MISSING_RETURN_POLICY,
        "contribution_mode": RESEARCH_RISK_CONTRIBUTION_MODE,
    }
    if isinstance(raw_policy, dict):
        source.update({key: value for key, value in raw_policy.items() if value is not None})

    lookback_days = _normalize_risk_window(source.get("lookback_days"))
    missing_return_policy = normalize_missing_return_policy(
        source.get("missing_return_policy") or RESEARCH_DEFAULT_MISSING_RETURN_POLICY
    )
    return {
        "model_name": str(source.get("model_name") or PORTFOLIO_RISK_POLICY_MODEL_NAME),
        "covariance_model_id": _normalized_covariance_model(source.get("covariance_model_id")),
        "lookback_days": lookback_days,
        "calculation_frequency": "daily",
        "missing_return_policy": missing_return_policy,
        "contribution_mode": _normalized_contribution_mode(source.get("contribution_mode")),
    }


def risk_min_observations_for_window(
    calculation_frequency: CalculationFrequency,
    lookback_days: int,
) -> int:
    return research_min_observations_for_window(calculation_frequency, lookback_days)


def risk_window_label(lookback_days: int) -> str:
    return PORTFOLIO_RISK_WINDOW_LABELS[_normalize_risk_window(lookback_days)]


def risk_policy_covariance_parameters(
    calculation_frequency: CalculationFrequency,
    lookback_days: int,
) -> dict[str, object]:
    return research_covariance_parameters_for_window(calculation_frequency, lookback_days)


def portfolio_risk_model_snapshot(
    policy: dict[str, object],
) -> dict[str, object]:
    normalized_policy = normalize_portfolio_risk_policy(policy if isinstance(policy, dict) else None)
    resolved_frequency: CalculationFrequency = "daily"
    lookback_days = int(normalized_policy["lookback_days"])
    parameters = risk_policy_covariance_parameters(resolved_frequency, lookback_days)
    return {
        **deepcopy(normalized_policy),
        "model_role": "production",
        "resolved_calculation_frequency": resolved_frequency,
        "parameters": parameters,
        "parameters_by_frequency": {
            "daily": risk_policy_covariance_parameters("daily", lookback_days)
        },
    }


def get_portfolio_risk_policy(
    portfolio_id: str,
) -> dict[str, object] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        portfolio = session.get(PortfolioRecordModel, portfolio_id)
        if portfolio is None:
            return None
        policy = normalize_portfolio_risk_policy(
            portfolio.risk_policy_json if isinstance(portfolio.risk_policy_json, dict) else None,
        )
        return portfolio_risk_model_snapshot(policy)


def update_portfolio_risk_policy(
    portfolio_id: str,
    payload: dict[str, object],
) -> dict[str, object] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        portfolio = session.get(PortfolioRecordModel, portfolio_id)
        if portfolio is None:
            return None
        policy = normalize_portfolio_risk_policy(payload)
        portfolio.risk_policy_json = deepcopy(policy)

        session.commit()
        return portfolio_risk_model_snapshot(policy)


def _holding_row_is_cash(row: dict[str, object]) -> bool:
    instrument_core = row.get("instrument_core") if isinstance(row.get("instrument_core"), dict) else {}
    instrument_id = str(instrument_core.get("instrument_id") or row.get("line_id") or "").strip()
    return (
        str(instrument_core.get("instrument_type") or "").strip().lower() == "cash"
        or is_cash_holding_instrument_id(instrument_id)
    )


def _holding_row_is_derivative(row: dict[str, object]) -> bool:
    return (
        row.get("holding_category") == "derivatives"
        or str(row.get("holding_kind") or "").strip().lower()
        in {"derivative_contract", "option_obligation"}
        or bool(row.get("derivative_contract_id"))
        or isinstance(row.get("derivative_contract"), dict)
    )


def _row_label(row: dict[str, object]) -> str:
    instrument_core = row.get("instrument_core") if isinstance(row.get("instrument_core"), dict) else {}
    return str(
        instrument_core.get("instrument_name")
        or instrument_core.get("instrument_id")
        or row.get("line_id")
        or "Holding"
    )


def _row_key(row: dict[str, object], index: int) -> str:
    instrument_core = row.get("instrument_core") if isinstance(row.get("instrument_core"), dict) else {}
    base_key = str(row.get("line_id") or instrument_core.get("instrument_id") or f"holding_{index}").strip()
    return f"{base_key}::{index}"


def _return_series_points(row: dict[str, object]) -> list[dict[str, object]]:
    payload = row.get("instrument_return_series_all")
    if not isinstance(payload, dict):
        return []
    points = payload.get("points")
    return points if isinstance(points, list) else []


def _return_series_with_periods(row: dict[str, object]) -> tuple[pd.Series, dict[date, date]]:
    values: dict[date, float] = {}
    starts: dict[date, date] = {}
    for point in _return_series_points(row):
        if not isinstance(point, dict):
            raise ValueError("return series contains a non-object observation")
        raw_date = point.get("date")
        raw_value = _safe_float(point.get("value"))
        if raw_date is None or raw_value is None:
            raise ValueError("return series contains an observation without a period end or finite value")
        try:
            point_date = date.fromisoformat(str(raw_date)[:10])
        except ValueError as error:
            raise ValueError(f"return series contains invalid period end {raw_date}") from error
        raw_start_date = point.get("start_date")
        if raw_start_date is None:
            raise ValueError(f"return ending {point_date.isoformat()} is missing its period start")
        try:
            start_date = date.fromisoformat(str(raw_start_date)[:10])
        except ValueError as error:
            raise ValueError(f"return ending {point_date.isoformat()} has invalid period start {raw_start_date}") from error
        if start_date >= point_date:
            raise ValueError(
                f"return period {start_date.isoformat()} -> {point_date.isoformat()} is not strictly increasing"
            )
        if point_date in values:
            raise ValueError(f"return series contains duplicate period end {point_date.isoformat()}")
        if not np.isfinite(raw_value):
            raise ValueError(f"return ending {point_date.isoformat()} is non-finite")
        values[point_date] = raw_value
        starts[point_date] = start_date
    ordered_period_ends = sorted(values)
    for index in range(1, len(ordered_period_ends)):
        previous_end = ordered_period_ends[index - 1]
        current_end = ordered_period_ends[index]
        current_start = starts[current_end]
        if current_start != previous_end:
            raise ValueError(
                "return series is not contiguous: "
                f"period ending {current_end.isoformat()} starts at "
                f"{current_start.isoformat()} instead of the preceding "
                f"period end {previous_end.isoformat()}"
            )
    return pd.Series(values, dtype="float64").sort_index(), starts


def _forward_risk_coverage_snapshot(
    returns: pd.DataFrame,
    *,
    as_of_date: date,
    lookback_days: int,
    missing_return_policy: str,
    labels_by_key: dict[str, str],
) -> dict[str, object]:
    window_start_date = research_window_start_date(as_of_date, lookback_days)
    window = returns.loc[
        (returns.index > window_start_date) & (returns.index <= as_of_date)
    ].sort_index()
    missing_mask = window.isna().any(axis=1) if not window.empty else pd.Series(dtype="bool")
    complete = window.loc[~missing_mask] if not window.empty else window
    missing_rows: list[dict[str, object]] = []
    if not window.empty:
        for raw_date, row in window.loc[missing_mask].iterrows():
            missing_rows.append(
                {
                    "date": pd.Timestamp(raw_date).date().isoformat(),
                    "missing_members": [
                        labels_by_key.get(str(column), str(column))
                        for column, value in row.items()
                        if pd.isna(value)
                    ],
                }
            )
    latest_complete_date = pd.Timestamp(max(complete.index)).date() if len(complete) else None
    missing_row_count = int(missing_mask.sum()) if len(missing_mask) else 0
    return {
        "policy": missing_return_policy,
        "window_start_date": window_start_date.isoformat(),
        "window_end_date": as_of_date.isoformat(),
        "return_interval": (
            f"({window_start_date.isoformat()} EOD, {as_of_date.isoformat()} EOD]"
        ),
        "rows_before": int(len(window)),
        "rows_after": int(len(complete)),
        "complete_row_count": int(len(complete)),
        "missing_row_count": missing_row_count,
        "missing_row_fraction": float(missing_row_count / len(window)) if len(window) else 0.0,
        "missing_rows": missing_rows,
        "latest_complete_date": latest_complete_date.isoformat() if latest_complete_date else None,
        "trailing_staleness_days": (
            int((as_of_date - latest_complete_date).days)
            if latest_complete_date is not None
            else None
        ),
    }


def _validate_aligned_return_periods(
    complete_returns: pd.DataFrame,
    *,
    period_starts_by_key: dict[str, dict[date, date]],
    labels_by_key: dict[str, str],
) -> None:
    for raw_end_date in complete_returns.index:
        end_date = pd.Timestamp(raw_end_date).date()
        starts_by_member = {
            labels_by_key.get(key, key): period_starts_by_key.get(key, {}).get(end_date)
            for key in complete_returns.columns
        }
        missing_members = [label for label, start_date in starts_by_member.items() if start_date is None]
        if missing_members:
            raise ValueError(
                f"Forward risk contribution return ending {end_date.isoformat()} is missing period starts for "
                f"{', '.join(missing_members)}."
            )
        distinct_starts = sorted({start_date for start_date in starts_by_member.values() if start_date is not None})
        if len(distinct_starts) > 1:
            rendered = ", ".join(
                f"{label}={start_date.isoformat() if start_date else 'missing'}"
                for label, start_date in starts_by_member.items()
            )
            raise ValueError(
                f"Forward risk contribution requires identical return periods; ending {end_date.isoformat()} "
                f"has mismatched starts: {rendered}."
            )


def _daily_mark_to_last_return_matrix(
    active: list[tuple[str, dict[str, object], pd.Series, dict[date, date]]],
) -> tuple[pd.DataFrame, dict[str, dict[date, date]]]:
    nav_by_key: dict[str, pd.Series] = {}
    for key, _row, series, period_starts in active:
        ordered = series.sort_index()
        first_end_date = pd.Timestamp(ordered.index[0]).date()
        growth = 1.0
        nav_points: dict[date, float] = {period_starts[first_end_date]: growth}
        for raw_end_date, raw_return in ordered.items():
            growth *= 1.0 + float(raw_return)
            nav_points[pd.Timestamp(raw_end_date).date()] = growth
        nav_by_key[key] = pd.Series(nav_points, dtype="float64").sort_index()

    aligned_nav = pd.DataFrame(nav_by_key).sort_index().ffill()
    returns = aligned_nav.pct_change(fill_method=None)
    aligned_period_starts: dict[str, dict[date, date]] = {
        key: {} for key in nav_by_key
    }
    ordered_dates = [pd.Timestamp(raw_date).date() for raw_date in returns.index]
    for index in range(1, len(ordered_dates)):
        start_date = ordered_dates[index - 1]
        end_date = ordered_dates[index]
        for key in returns.columns:
            if pd.notna(returns.at[end_date, key]):
                aligned_period_starts[str(key)][end_date] = start_date
    return returns, aligned_period_starts


def _clear_forward_risk_fields(
    row: dict[str, object],
    *,
    modeled_zero: bool = False,
    status: str | None = None,
) -> None:
    row["forward_risk_share"] = 0.0 if modeled_zero else None
    row["forward_contribution_to_variance"] = 0.0 if modeled_zero else None
    row["forward_annualized_volatility"] = 0.0 if modeled_zero else None
    row["forward_risk_observation_count"] = 0 if modeled_zero else None
    row["forward_risk_status"] = status or (
        "modeled_zero" if modeled_zero else "unavailable"
    )


def _forward_risk_scope_disclosure(
    workspace: dict[str, object],
    *,
    calculation_frequency: CalculationFrequency,
) -> dict[str, object]:
    summary = (
        workspace.get("analytics_scope_summary")
        if isinstance(workspace.get("analytics_scope_summary"), dict)
        else {}
    )
    return {
        "scope_name": summary.get("scope_name") or "Modeled Market Sleeve",
        "scope_policy_versions": list(summary.get("scope_policy_versions") or []),
        "configuration_versions": list(summary.get("configuration_versions") or []),
        "total_nav": summary.get("total_nav"),
        "modeled_net_exposure": summary.get("modeled_net_exposure"),
        "modeled_gross_exposure": summary.get("modeled_gross_exposure"),
        "excluded_carrying_value": summary.get("excluded_carrying_value"),
        "excluded_liability": summary.get("excluded_liability"),
        "cash_unallocated_exposure": summary.get("cash_unallocated_exposure"),
        "coverage_ratio": summary.get("coverage_ratio"),
        "excluded_rows": list(summary.get("excluded_rows") or []),
        "calculation_frequency": calculation_frequency,
    }


def enrich_holdings_forward_risk(
    workspace: dict[str, object],
    *,
    as_of_date: date,
    calculation_frequency: CalculationFrequency,
    risk_policy: dict[str, object],
) -> dict[str, object]:
    rows = workspace.get("rows")
    if not isinstance(rows, list):
        workspace["risk_policy"] = risk_policy
        workspace["forward_risk"] = {
            "status": "unavailable",
            "errors": ["Holdings workspace rows are unavailable."],
        }
        return workspace

    snapshot = portfolio_risk_model_snapshot(risk_policy)
    workspace["risk_policy"] = snapshot
    scope_disclosure = _forward_risk_scope_disclosure(
        workspace,
        calculation_frequency=calculation_frequency,
    )

    base_currency = str(workspace.get("base_currency") or "").strip().upper()
    if not base_currency:
        for raw_row in rows:
            if isinstance(raw_row, dict):
                _clear_forward_risk_fields(raw_row)
        workspace["forward_risk"] = {
            "status": "unavailable",
            "errors": ["Forward RC requires the portfolio base currency."],
            "risk_model": snapshot,
            **scope_disclosure,
        }
        return workspace

    active: list[tuple[str, dict[str, object], pd.Series, dict[date, date]]] = []
    errors: list[str] = []
    total_nav = _safe_float(scope_disclosure.get("total_nav"))
    if total_nav is None or not np.isfinite(total_nav) or total_nav <= 1e-12:
        errors.append("Forward RC requires positive finite total NAV.")
    for index, raw_row in enumerate(rows):
        if not isinstance(raw_row, dict):
            continue
        instrument_core = raw_row.get("instrument_core") if isinstance(raw_row.get("instrument_core"), dict) else {}
        instrument_currency = str(instrument_core.get("currency") or "").strip().upper()
        market_value = _safe_float(raw_row.get("market_value_base"))
        has_exposure = abs(market_value or 0.0) > 1e-9
        is_derivative = _holding_row_is_derivative(raw_row)
        is_cash = _holding_row_is_cash(raw_row)
        is_pending = is_pending_monetary_holding(raw_row)
        is_base_currency_monetary = not has_exposure or (
            base_currency and instrument_currency == base_currency
        )
        if raw_row.get("risk_eligible") is not True:
            if is_derivative:
                _clear_forward_risk_fields(raw_row, status="excluded")
                continue
            modeled_zero = (is_cash or is_pending) and is_base_currency_monetary
            _clear_forward_risk_fields(raw_row, modeled_zero=modeled_zero)
            if (is_cash or is_pending) and has_exposure and not is_base_currency_monetary:
                raw_row["forward_risk_status"] = (
                    "pending_settlement" if is_pending else "cash_unallocated"
                )
                errors.append(
                    "Forward RC requires an FX total-return series for non-base "
                    f"monetary exposure {_row_label(raw_row)} "
                    f"({instrument_currency or 'unknown'} versus {base_currency})."
                )
            elif not modeled_zero:
                raw_row["forward_risk_status"] = (
                    "pending_settlement"
                    if is_pending
                    else "cash_unallocated"
                    if is_cash
                    else "policy_excluded"
                )
                if has_exposure and not is_cash and not is_pending:
                    errors.append(
                        "Forward RC cannot model policy-excluded market exposure "
                        f"{_row_label(raw_row)} as zero risk."
                    )
            continue
        if is_derivative:
            _clear_forward_risk_fields(raw_row, status="excluded")
            continue
        if is_pending:
            _clear_forward_risk_fields(
                raw_row, modeled_zero=is_base_currency_monetary
            )
            if not is_base_currency_monetary:
                raw_row["forward_risk_status"] = "pending_settlement"
            continue
        if is_cash:
            if is_base_currency_monetary:
                _clear_forward_risk_fields(raw_row, modeled_zero=True)
            else:
                _clear_forward_risk_fields(raw_row)
                errors.append(
                    f"Forward RC requires an FX total-return series for non-base cash {_row_label(raw_row)} "
                    f"({instrument_currency or 'unknown'} versus {base_currency or 'unknown'})."
                )
            continue
        if not has_exposure:
            _clear_forward_risk_fields(raw_row)
            raw_row["forward_risk_status"] = "no_exposure"
            continue
        if not instrument_currency:
            _clear_forward_risk_fields(raw_row)
            errors.append(f"Forward RC requires a return currency for {_row_label(raw_row)}.")
            continue
        if instrument_currency != base_currency:
            _clear_forward_risk_fields(raw_row)
            errors.append(
                f"Forward RC requires base-currency total returns; {_row_label(raw_row)} is "
                f"{instrument_currency} while the portfolio base currency is {base_currency}."
            )
            continue
        try:
            series, period_starts = _return_series_with_periods(raw_row)
        except ValueError as error:
            _clear_forward_risk_fields(raw_row)
            errors.append(f"Forward RC {_row_label(raw_row)} {error}.")
            continue
        if series.empty:
            _clear_forward_risk_fields(raw_row)
            errors.append(f"Forward RC requires full-history return series for {_row_label(raw_row)}.")
            continue
        active.append((_row_key(raw_row, index), raw_row, series, period_starts))

    if errors:
        for _key, row, _series, _starts in active:
            _clear_forward_risk_fields(row)
        workspace["forward_risk"] = {
            "status": "unavailable",
            "errors": errors,
            "risk_model": snapshot,
            **scope_disclosure,
        }
        return workspace
    if not active:
        workspace["forward_risk"] = {
            "status": "unavailable",
            "errors": ["Modeled sleeve risk requires at least one eligible risky holding."],
            "risk_model": snapshot,
            **scope_disclosure,
        }
        return workspace

    returns, period_starts_by_key = _daily_mark_to_last_return_matrix(active)
    labels_by_key = {key: _row_label(row) for key, row, _series, _starts in active}
    modeled_exposures = np.asarray(
        [
            _safe_float(row.get("market_value_base")) or 0.0
            for _key, row, _series, _starts in active
        ],
        dtype="float64",
    )
    assert total_nav is not None
    weights = modeled_exposures / total_nav
    coverage_snapshot = _forward_risk_coverage_snapshot(
        returns,
        as_of_date=as_of_date,
        lookback_days=int(snapshot["lookback_days"]),
        missing_return_policy=str(snapshot["missing_return_policy"]),
        labels_by_key=labels_by_key,
    )
    try:
        parameters = dict(snapshot.get("parameters") if isinstance(snapshot.get("parameters"), dict) else {})
        coverage = prepare_return_window_for_covariance(
            returns,
            lookback_days=int(snapshot["lookback_days"]),
            min_observations=int(parameters.get("min_observations", 2)),
            label="Forward risk contribution",
            missing_return_policy=str(snapshot["missing_return_policy"]),
            calculation_frequency=calculation_frequency,
            as_of_date=as_of_date,
        )
        _validate_aligned_return_periods(
            coverage.returns,
            period_starts_by_key=period_starts_by_key,
            labels_by_key=labels_by_key,
        )
        covariance = estimate_covariance(
            returns,
            model_id=str(snapshot["covariance_model_id"]),
            lookback_days=int(snapshot["lookback_days"]),
            parameters=parameters,
            missing_return_policy=str(snapshot["missing_return_policy"]),
            calculation_frequency=calculation_frequency,
            as_of_date=as_of_date,
        )
        covariance_matrix = covariance.to_numpy(dtype="float64")
        marginal = covariance_matrix @ weights
        signed_contributions = weights * marginal
        variance = float(weights @ marginal)
        shares = risk_contribution_shares(
            covariance_matrix,
            weights,
            contribution_mode=str(snapshot["contribution_mode"]),
        )
    except ValueError as error:
        for _key, row, _series, _starts in active:
            _clear_forward_risk_fields(row)
        workspace["forward_risk"] = {
            "status": "unavailable",
            "errors": [str(error)],
            "risk_model": snapshot,
            "coverage": coverage_snapshot,
            **scope_disclosure,
        }
        return workspace

    if not np.isfinite(variance) or variance <= 1e-12:
        for _key, row, _series, _starts in active:
            _clear_forward_risk_fields(row)
        workspace["forward_risk"] = {
            "status": "unavailable",
            "errors": ["Forward RC requires positive finite portfolio variance."],
            "risk_model": snapshot,
            "coverage": coverage_snapshot,
            **scope_disclosure,
        }
        return workspace

    for index, (_key, row, _series, _starts) in enumerate(active):
        row["forward_risk_share"] = float(shares[index])
        row["forward_contribution_to_variance"] = float(signed_contributions[index])
        own_variance = float(covariance_matrix[index, index])
        row["forward_annualized_volatility"] = sqrt(max(own_variance, 0.0))
        row["forward_risk_observation_count"] = int(len(coverage.returns))
        row["forward_risk_status"] = "ok"

    workspace["forward_risk"] = {
        "status": "ok",
        "errors": [],
        "risk_model": snapshot,
        "portfolio_variance": variance,
        "portfolio_volatility": sqrt(variance),
        "observation_count": int(len(coverage.returns)),
        "coverage": coverage_snapshot,
        "modeled_weight_basis": "total_nav_zero_return_cash_and_derivatives",
        **scope_disclosure,
    }
    return workspace
