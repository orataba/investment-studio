from __future__ import annotations

from copy import deepcopy
from datetime import date
from math import sqrt

import numpy as np
import pandas as pd

from portfolio_app.db.models import PortfolioRecordModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.calculation_frequency import CalculationFrequency
from portfolio_app.services.holdings_market_profile import is_cash_holding_instrument_id
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
SUPPORTED_CALCULATION_FREQUENCIES = {"auto", "daily", "weekly", "monthly"}


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


def _normalized_calculation_frequency(value: object) -> str:
    normalized = str(value or "auto").strip().lower()
    return normalized if normalized in SUPPORTED_CALCULATION_FREQUENCIES else "auto"


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
        "calculation_frequency": "auto",
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
        "calculation_frequency": _normalized_calculation_frequency(source.get("calculation_frequency")),
        "missing_return_policy": missing_return_policy,
        "contribution_mode": _normalized_contribution_mode(source.get("contribution_mode")),
    }


def _resolved_frequency(policy: dict[str, object], calculation_frequency: CalculationFrequency | None) -> CalculationFrequency:
    configured = str(policy.get("calculation_frequency") or "auto")
    if configured in {"daily", "weekly", "monthly"}:
        return configured  # type: ignore[return-value]
    return calculation_frequency or "daily"


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
    *,
    calculation_frequency: CalculationFrequency | None = None,
) -> dict[str, object]:
    normalized_policy = normalize_portfolio_risk_policy(policy if isinstance(policy, dict) else None)
    resolved_frequency = _resolved_frequency(normalized_policy, calculation_frequency)
    lookback_days = int(normalized_policy["lookback_days"])
    parameters = risk_policy_covariance_parameters(resolved_frequency, lookback_days)
    return {
        **deepcopy(normalized_policy),
        "model_role": "production",
        "resolved_calculation_frequency": resolved_frequency,
        "parameters": parameters,
        "parameters_by_frequency": {
            frequency: risk_policy_covariance_parameters(frequency, lookback_days)
            for frequency in ("daily", "weekly", "monthly")
        },
    }


def get_portfolio_risk_policy(
    portfolio_id: str,
    *,
    calculation_frequency: CalculationFrequency | None = None,
) -> dict[str, object] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        portfolio = session.get(PortfolioRecordModel, portfolio_id)
        if portfolio is None:
            return None
        policy = normalize_portfolio_risk_policy(
            portfolio.risk_policy_json if isinstance(portfolio.risk_policy_json, dict) else None,
        )
        return portfolio_risk_model_snapshot(policy, calculation_frequency=calculation_frequency)


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


def _returns_by_date(row: dict[str, object]) -> pd.Series:
    values: dict[date, float] = {}
    for point in _return_series_points(row):
        if not isinstance(point, dict):
            continue
        raw_date = point.get("date")
        raw_value = _safe_float(point.get("value"))
        if raw_date is None or raw_value is None:
            continue
        try:
            point_date = date.fromisoformat(str(raw_date)[:10])
        except ValueError:
            continue
        values[point_date] = raw_value
    return pd.Series(values, dtype="float64").sort_index()


def _clear_forward_risk_fields(row: dict[str, object], *, cash: bool = False) -> None:
    row["forward_risk_share"] = 0.0 if cash else None
    row["forward_contribution_to_variance"] = 0.0 if cash else None
    row["forward_annualized_volatility"] = 0.0 if cash else None
    row["forward_risk_observation_count"] = 0 if cash else None
    row["forward_risk_status"] = "cash" if cash else "unavailable"


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

    snapshot = portfolio_risk_model_snapshot(risk_policy, calculation_frequency=calculation_frequency)
    workspace["risk_policy"] = snapshot

    active: list[tuple[str, dict[str, object], pd.Series]] = []
    errors: list[str] = []
    for index, raw_row in enumerate(rows):
        if not isinstance(raw_row, dict):
            continue
        if _holding_row_is_cash(raw_row):
            _clear_forward_risk_fields(raw_row, cash=True)
            continue
        weight = _safe_float(raw_row.get("allocation"))
        market_value = _safe_float(raw_row.get("market_value_base"))
        has_exposure = abs(weight or 0.0) > 1e-12 or abs(market_value or 0.0) > 1e-9
        if not has_exposure:
            _clear_forward_risk_fields(raw_row)
            raw_row["forward_risk_status"] = "no_exposure"
            continue
        if weight is None:
            _clear_forward_risk_fields(raw_row)
            errors.append(f"Forward RC requires a current portfolio weight for {_row_label(raw_row)}.")
            continue
        series = _returns_by_date(raw_row)
        if series.empty:
            _clear_forward_risk_fields(raw_row)
            errors.append(f"Forward RC requires full-history return series for {_row_label(raw_row)}.")
            continue
        active.append((_row_key(raw_row, index), raw_row, series))

    if errors:
        workspace["forward_risk"] = {
            "status": "unavailable",
            "errors": errors,
            "risk_model": snapshot,
        }
        return workspace
    if not active:
        workspace["forward_risk"] = {
            "status": "unavailable",
            "errors": ["Forward RC requires at least one active non-cash holding."],
            "risk_model": snapshot,
        }
        return workspace

    returns = pd.DataFrame({key: series for key, _row, series in active}).sort_index()
    weights = np.asarray([_safe_float(row.get("allocation")) or 0.0 for _key, row, _series in active], dtype="float64")
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
        for _key, row, _series in active:
            _clear_forward_risk_fields(row)
        workspace["forward_risk"] = {
            "status": "unavailable",
            "errors": [str(error)],
            "risk_model": snapshot,
        }
        return workspace

    if not np.isfinite(variance) or variance <= 1e-12:
        for _key, row, _series in active:
            _clear_forward_risk_fields(row)
        workspace["forward_risk"] = {
            "status": "unavailable",
            "errors": ["Forward RC requires positive finite portfolio variance."],
            "risk_model": snapshot,
        }
        return workspace

    for index, (_key, row, _series) in enumerate(active):
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
    }
    return workspace
