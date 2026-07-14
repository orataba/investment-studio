from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import ceil, sqrt

import numpy as np
import pandas as pd

from portfolio_app.services.calculation_frequency import (
    CalculationFrequency,
    infer_observation_frequency,
    period_end_date,
)


DEFAULT_COVARIANCE_MODEL_ID = "ewma_vol_shrinkage_corr_covariance"


COVARIANCE_FREQUENCY_PARAMETERS: dict[CalculationFrequency, dict[str, object]] = {
    "daily": {
        "min_observations": 45,
        "vol_decay": 0.9945,
        "corr_min_observations": 45,
        "corr_shrinkage": 0.15,
        "max_period_staleness_days": 0,
    },
    "weekly": {
        "min_observations": 9,
        "vol_decay": 0.9737,
        "corr_min_observations": 9,
        "corr_shrinkage": 0.15,
        "max_period_staleness_days": 4,
    },
    "monthly": {
        "min_observations": 3,
        "vol_decay": 0.8909,
        "corr_min_observations": 3,
        "corr_shrinkage": 0.15,
        "max_period_staleness_days": 7,
    },
}


MIN_OBSERVATION_COVERAGE_RATIO = 0.75


WINDOW_MONTHS_BY_LOOKBACK_DAYS = {
    30: 1.0,
    90: 3.0,
    180: 6.0,
    366: 12.0,
    730: 24.0,
}


OBSERVATIONS_PER_MONTH_BY_FREQUENCY: dict[CalculationFrequency, float] = {
    "daily": 20.0,
    "weekly": 4.0,
    "monthly": 1.0,
}


DEFAULT_RISK_CONTRIBUTION_MODE = "signed"


COVARIANCE_PSD_TOLERANCE = 1e-10


ABS_RC_SMOOTHING_EPS = 1e-12


MISSING_RETURN_POLICY_STRICT = "strict"


MISSING_RETURN_POLICY_COMPLETE_CASE_DROP = "complete_case_drop"


DEFAULT_MISSING_RETURN_POLICY = MISSING_RETURN_POLICY_STRICT


COMPLETE_CASE_DROP_MAX_MISSING_ROW_FRACTION = 0.10


COMPLETE_CASE_DROP_MAX_TRAILING_STALENESS_DAYS: dict[CalculationFrequency, int] = {
    "daily": 5,
    "weekly": 14,
    "monthly": 62,
}


SUPPORTED_RISK_LOOKBACK_DAYS = frozenset(WINDOW_MONTHS_BY_LOOKBACK_DAYS)


@dataclass(frozen=True)
class ReturnCoveragePolicyResult:
    returns: pd.DataFrame
    policy: str
    rows_before: int
    rows_after: int
    missing_row_count: int
    missing_row_fraction: float
    dropped_rows: list[dict[str, object]]
    latest_complete_date: date | None
    trailing_staleness_days: int | None


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _risk_window_months(lookback_days: int) -> int:
    try:
        months = WINDOW_MONTHS_BY_LOOKBACK_DAYS[int(lookback_days)]
    except (KeyError, TypeError, ValueError) as error:
        labels = ", ".join(f"{int(months)}M" for months in WINDOW_MONTHS_BY_LOOKBACK_DAYS.values())
        raise ValueError(f"Risk window must be one of {labels}.") from error
    return int(months)


def risk_window_start_date(end_date: date, lookback_days: int) -> date:
    months = _risk_window_months(lookback_days)
    return (pd.Timestamp(end_date) - pd.DateOffset(months=months)).date()


def _annualized_portfolio_volatility(
    return_window: pd.DataFrame,
    weights: pd.Series,
    *,
    as_of_date: date,
    lookback_days: int,
    calculation_frequency: CalculationFrequency,
    missing_return_policy: str,
    risk_model_config: dict[str, object] | None = None,
) -> float:
    if return_window.empty or weights.empty:
        raise ValueError("Target-volatility overlay requires non-empty aligned risky return history.")
    missing_columns = [str(column) for column in weights.index if column not in return_window.columns]
    if missing_columns:
        raise ValueError(
            "Target-volatility overlay is missing risky return columns: "
            f"{', '.join(missing_columns[:8])}."
        )
    aligned = return_window.reindex(columns=weights.index).dropna(how="all")
    if aligned.shape[0] < 2:
        raise ValueError("Target-volatility overlay requires at least two aligned risky return observations.")
    covariance = _estimate_covariance(
        aligned,
        model_id=_risk_model_covariance_model_id(risk_model_config),
        lookback_days=lookback_days,
        parameters=_risk_model_covariance_parameters(risk_model_config, calculation_frequency, lookback_days),
        missing_return_policy=missing_return_policy,
        calculation_frequency=calculation_frequency,
        as_of_date=as_of_date,
    )
    ordered_weights = weights.reindex(covariance.index, fill_value=0.0).astype("float64")
    matrix = covariance.to_numpy(dtype="float64")
    vector = ordered_weights.to_numpy(dtype="float64")
    variance = float(vector @ matrix @ vector)
    if variance <= 1e-12:
        return 0.0
    return float(sqrt(max(variance, 0.0)))


def _covariance_parameters(calculation_frequency: CalculationFrequency) -> dict[str, object]:
    try:
        return dict(COVARIANCE_FREQUENCY_PARAMETERS[calculation_frequency])
    except KeyError as error:
        raise ValueError(f"Unsupported calculation frequency: {calculation_frequency}.") from error


def covariance_parameters(calculation_frequency: CalculationFrequency) -> dict[str, object]:
    return _covariance_parameters(calculation_frequency)


def min_observations_for_window(
    calculation_frequency: CalculationFrequency,
    lookback_days: int,
) -> int:
    months = _risk_window_months(lookback_days)
    observations_per_month = OBSERVATIONS_PER_MONTH_BY_FREQUENCY[calculation_frequency]
    expected_observations = months * observations_per_month
    return max(2, int(ceil(expected_observations * MIN_OBSERVATION_COVERAGE_RATIO)))


def covariance_parameters_for_window(
    calculation_frequency: CalculationFrequency,
    lookback_days: int,
) -> dict[str, object]:
    parameters = _covariance_parameters(calculation_frequency)
    min_observations = min_observations_for_window(calculation_frequency, lookback_days)
    parameters["min_observations"] = min_observations
    parameters["corr_min_observations"] = min_observations
    return parameters


def _risk_model_covariance_model_id(risk_model_config: dict[str, object] | None) -> str:
    if not isinstance(risk_model_config, dict):
        return DEFAULT_COVARIANCE_MODEL_ID
    return str(risk_model_config.get("covariance_model_id") or DEFAULT_COVARIANCE_MODEL_ID)


def _risk_model_contribution_mode(risk_model_config: dict[str, object] | None) -> str:
    if not isinstance(risk_model_config, dict):
        return DEFAULT_RISK_CONTRIBUTION_MODE
    return str(risk_model_config.get("contribution_mode") or DEFAULT_RISK_CONTRIBUTION_MODE)


def _risk_model_covariance_parameters(
    risk_model_config: dict[str, object] | None,
    calculation_frequency: CalculationFrequency,
    lookback_days: int,
) -> dict[str, object]:
    if isinstance(risk_model_config, dict):
        parameters_by_frequency = risk_model_config.get("parameters_by_frequency")
        if isinstance(parameters_by_frequency, dict):
            frequency_parameters = parameters_by_frequency.get(calculation_frequency)
            if isinstance(frequency_parameters, dict):
                return dict(frequency_parameters)
        parameters = risk_model_config.get("parameters")
        if isinstance(parameters, dict):
            return dict(parameters)
    return covariance_parameters_for_window(calculation_frequency, lookback_days)


def _normalize_missing_return_policy(value: object) -> str:
    normalized = str(value or DEFAULT_MISSING_RETURN_POLICY).strip().lower()
    if normalized in {MISSING_RETURN_POLICY_STRICT, MISSING_RETURN_POLICY_COMPLETE_CASE_DROP}:
        return normalized
    raise ValueError(f"Unsupported missing-return policy: {value}.")


def normalize_missing_return_policy(value: object) -> str:
    return _normalize_missing_return_policy(value)


def _max_complete_case_drop_staleness_days(calculation_frequency: CalculationFrequency) -> int:
    try:
        return COMPLETE_CASE_DROP_MAX_TRAILING_STALENESS_DAYS[calculation_frequency]
    except KeyError as error:
        raise ValueError(f"Unsupported calculation frequency: {calculation_frequency}.") from error


def _infer_periods_per_year(dates: list[date]) -> float:
    if len(dates) < 2:
        return 1.0
    timestamps = pd.to_datetime(pd.Series(list(dates))).drop_duplicates().sort_values()
    elapsed_days = int((timestamps.iloc[-1] - timestamps.iloc[0]).days)
    if elapsed_days <= 0:
        return 1.0
    gaps = [
        int((timestamps.iloc[index] - timestamps.iloc[index - 1]).days)
        for index in range(1, len(timestamps))
        if int((timestamps.iloc[index] - timestamps.iloc[index - 1]).days) > 0
    ]
    median_gap = sorted(gaps)[len(gaps) // 2] if gaps else 1
    observation_span_days = elapsed_days + median_gap
    if observation_span_days <= 0:
        return 1.0
    return float(len(timestamps)) / float(observation_span_days) * 365.25


def infer_periods_per_year(dates: list[date]) -> float:
    """Expose the covariance kernel's empirical annualization convention."""

    return _infer_periods_per_year(dates)


def _index_dates(index: pd.Index) -> list[date]:
    dates: list[date] = []
    for item in index.tolist():
        if item is None:
            continue
        timestamp = pd.Timestamp(item)
        if pd.isna(timestamp):
            continue
        dates.append(timestamp.date())
    return dates


def _annualize_covariance(covariance: pd.DataFrame, returns: pd.DataFrame) -> pd.DataFrame:
    if covariance.empty:
        return covariance
    cleaned = _clean_return_frame(returns).reindex(columns=covariance.columns)
    complete = cleaned.dropna(how="any")
    return covariance.copy().astype("float64") * _infer_periods_per_year(_index_dates(complete.index))


def _clean_return_frame(returns: pd.DataFrame) -> pd.DataFrame:
    if returns.empty:
        return pd.DataFrame()
    cleaned = returns.copy()
    cleaned = cleaned.replace([np.inf, -np.inf], np.nan)
    cleaned = cleaned.sort_index().dropna(how="all")
    return cleaned.astype("float64")


def _format_index_sample(index: pd.Index, *, limit: int = 5) -> str:
    rendered: list[str] = []
    for item in index.tolist()[:limit]:
        try:
            rendered.append(pd.Timestamp(item).date().isoformat())
        except (TypeError, ValueError):
            rendered.append(str(item))
    return ", ".join(rendered)


def _validate_complete_return_coverage(
    returns: pd.DataFrame,
    *,
    min_observations: int,
    label: str,
) -> None:
    required = max(int(min_observations), 2)
    if returns.empty:
        raise ValueError(f"{label} requires non-empty aligned returns.")
    missing_mask = returns.isna().any(axis=1)
    if bool(missing_mask.any()):
        missing_rows = returns.loc[missing_mask]
        missing_dates = _format_index_sample(missing_rows.index)
        raise ValueError(
            f"{label} requires complete aligned return observations; "
            f"missing return values were found on {missing_dates}."
        )
    if len(returns) < required:
        raise ValueError(
            f"{label} requires at least {required} complete aligned return observations; got {len(returns)}."
        )


def _return_window_for_lookback(
    returns: pd.DataFrame,
    *,
    lookback_days: int,
) -> pd.DataFrame:
    cleaned = _clean_return_frame(returns)
    if cleaned.empty:
        raise ValueError("Covariance estimation requires non-empty returns.")
    end_date = max(cleaned.index)
    start_day = risk_window_start_date(pd.Timestamp(end_date).date(), lookback_days)
    return cleaned.loc[cleaned.index >= start_day].copy()


def _render_missing_return_rows(returns: pd.DataFrame) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if returns.empty:
        return rows
    for index, row in returns.loc[returns.isna().any(axis=1)].iterrows():
        rows.append(
            {
                "date": pd.Timestamp(index).date().isoformat(),
                "missing_members": [str(column) for column, value in row.items() if pd.isna(value)],
            }
        )
    return rows


def _apply_missing_return_policy(
    returns: pd.DataFrame,
    *,
    min_observations: int,
    label: str,
    missing_return_policy: str,
    calculation_frequency: CalculationFrequency,
    as_of_date: date | None,
) -> ReturnCoveragePolicyResult:
    policy = _normalize_missing_return_policy(missing_return_policy)
    required = max(int(min_observations), 2)
    if returns.empty:
        raise ValueError(f"{label} requires non-empty aligned returns.")

    missing_mask = returns.isna().any(axis=1)
    missing_count = int(missing_mask.sum())
    missing_fraction = float(missing_count / max(len(returns), 1))
    dropped_rows = _render_missing_return_rows(returns)

    if policy == MISSING_RETURN_POLICY_STRICT:
        _validate_complete_return_coverage(
            returns,
            min_observations=min_observations,
            label=label,
        )
        complete = returns.copy()
    else:
        complete = returns.loc[~missing_mask].copy()
        if missing_count and missing_fraction > COMPLETE_CASE_DROP_MAX_MISSING_ROW_FRACTION + 1e-12:
            raise ValueError(
                f"{label} complete-case drop would remove {missing_count} of {len(returns)} return rows "
                f"({missing_fraction:.2%}), exceeding the "
                f"{COMPLETE_CASE_DROP_MAX_MISSING_ROW_FRACTION:.2%} limit. "
                f"Missing rows: {_format_index_sample(returns.loc[missing_mask].index)}."
            )
        if len(complete) < required:
            raise ValueError(
                f"{label} complete-case drop requires at least {required} complete aligned return observations; "
                f"got {len(complete)} after dropping {missing_count} rows."
            )
        if as_of_date is not None and len(complete):
            latest_date = pd.Timestamp(max(complete.index)).date()
            staleness_days = int((as_of_date - latest_date).days)
            max_staleness_days = _max_complete_case_drop_staleness_days(calculation_frequency)
            if staleness_days > max_staleness_days:
                raise ValueError(
                    f"{label} complete-case drop latest complete return observation is "
                    f"{latest_date.isoformat()} ({staleness_days} days before {as_of_date.isoformat()}); "
                    f"maximum allowed for {calculation_frequency} is {max_staleness_days} days."
                )
    if len(complete) < required:
        raise ValueError(f"{label} requires at least {required} complete aligned return observations; got {len(complete)}.")

    latest_complete_date = pd.Timestamp(max(complete.index)).date() if len(complete) else None
    trailing_staleness_days = (
        int((as_of_date - latest_complete_date).days)
        if as_of_date is not None and latest_complete_date is not None
        else None
    )
    return ReturnCoveragePolicyResult(
        returns=complete.astype("float64"),
        policy=policy,
        rows_before=int(len(returns)),
        rows_after=int(len(complete)),
        missing_row_count=missing_count,
        missing_row_fraction=missing_fraction,
        dropped_rows=dropped_rows,
        latest_complete_date=latest_complete_date,
        trailing_staleness_days=trailing_staleness_days,
    )


def _prepare_return_window_for_covariance(
    returns: pd.DataFrame,
    *,
    lookback_days: int,
    min_observations: int,
    label: str,
    missing_return_policy: str = DEFAULT_MISSING_RETURN_POLICY,
    calculation_frequency: CalculationFrequency = "daily",
    as_of_date: date | None = None,
) -> ReturnCoveragePolicyResult:
    window = _return_window_for_lookback(returns, lookback_days=lookback_days)
    return _apply_missing_return_policy(
        window,
        min_observations=min_observations,
        label=label,
        missing_return_policy=missing_return_policy,
        calculation_frequency=calculation_frequency,
        as_of_date=as_of_date,
    )


def prepare_return_window_for_covariance(
    returns: pd.DataFrame,
    *,
    lookback_days: int,
    min_observations: int,
    label: str,
    missing_return_policy: str = DEFAULT_MISSING_RETURN_POLICY,
    calculation_frequency: CalculationFrequency = "daily",
    as_of_date: date | None = None,
) -> ReturnCoveragePolicyResult:
    return _prepare_return_window_for_covariance(
        returns,
        lookback_days=lookback_days,
        min_observations=min_observations,
        label=label,
        missing_return_policy=missing_return_policy,
        calculation_frequency=calculation_frequency,
        as_of_date=as_of_date,
    )


def _select_return_window(
    returns: pd.DataFrame,
    *,
    lookback_days: int,
    min_observations: int,
) -> pd.DataFrame:
    window = _return_window_for_lookback(returns, lookback_days=lookback_days)
    _validate_complete_return_coverage(
        window,
        min_observations=min_observations,
        label="Covariance estimation",
    )
    return window


def _apply_diagonal_shrinkage(covariance: pd.DataFrame, shrinkage: float) -> pd.DataFrame:
    if not 0.0 <= shrinkage <= 1.0:
        raise ValueError("Covariance shrinkage must be in [0, 1].")
    matrix = covariance.to_numpy(dtype="float64")
    diagonal = np.diag(np.diag(matrix))
    shrunk = (1.0 - shrinkage) * matrix + shrinkage * diagonal
    return pd.DataFrame(shrunk, index=covariance.index, columns=covariance.columns)


def _estimate_ewma_covariance(returns: pd.DataFrame, *, decay: float, min_observations: int) -> pd.DataFrame:
    if not 0.0 < decay < 1.0:
        raise ValueError("EWMA decay must be in (0, 1).")
    _validate_complete_return_coverage(
        returns,
        min_observations=min_observations,
        label="EWMA covariance estimation",
    )
    values = returns.to_numpy(dtype="float64")
    covariance = np.zeros((values.shape[1], values.shape[1]), dtype="float64")
    for row_index in range(values.shape[1]):
        for column_index in range(row_index, values.shape[1]):
            pair_values = values[:, [row_index, column_index]]
            valid_mask = np.isfinite(pair_values).all(axis=1)
            valid_values = pair_values[valid_mask]
            periods = len(valid_values)
            raw_weights = np.asarray([decay ** (periods - 1 - index) for index in range(periods)], dtype="float64")
            weights = raw_weights / float(raw_weights.sum())
            mean = np.average(valid_values, axis=0, weights=weights)
            centered = valid_values - mean
            pair_covariance = float(
                sum(
                    float(weight) * float(left) * float(right)
                    for weight, (left, right) in zip(weights, centered, strict=True)
                )
            )
            covariance[row_index, column_index] = pair_covariance
            covariance[column_index, row_index] = pair_covariance
    return pd.DataFrame(covariance, index=returns.columns, columns=returns.columns)


def _estimate_sample_covariance(returns: pd.DataFrame, *, min_observations: int) -> pd.DataFrame:
    _validate_complete_return_coverage(
        returns,
        min_observations=min_observations,
        label="Sample covariance estimation",
    )
    values = returns.to_numpy(dtype="float64")
    centered = values - values.mean(axis=0, keepdims=True)
    covariance = centered.T @ centered / float(len(values) - 1)
    return pd.DataFrame(covariance, index=returns.columns, columns=returns.columns)


def _estimate_ledoit_wolf_covariance(returns: pd.DataFrame) -> pd.DataFrame:
    complete_returns = returns.dropna(how="any")
    values = complete_returns.to_numpy(dtype="float64")
    n_samples, n_features = values.shape
    if n_samples <= 1:
        raise ValueError("Ledoit-Wolf covariance requires at least two observations.")

    sample = np.cov(values, rowvar=False, ddof=0)
    if sample.ndim == 0:
        sample = np.array([[float(sample)]])
    mu = np.trace(sample) / float(n_features)
    target = np.eye(n_features, dtype="float64") * float(mu)
    centered = values - values.mean(axis=0, keepdims=True)
    beta_hat = 0.0
    for row in centered:
        outer = np.outer(row, row) - sample
        beta_hat += float(np.sum(outer * outer))
    beta_hat /= float(n_samples**2)
    delta_hat = float(np.sum((sample - target) ** 2))
    shrinkage = 1.0 if delta_hat <= 1e-18 else min(max(beta_hat / delta_hat, 0.0), 1.0)
    covariance = shrinkage * target + (1.0 - shrinkage) * sample
    return pd.DataFrame(covariance, index=returns.columns, columns=returns.columns)


def _finalize_covariance(covariance: pd.DataFrame) -> pd.DataFrame:
    matrix = covariance.to_numpy(dtype="float64")
    if not np.isfinite(matrix).all():
        raise ValueError("Covariance estimation produced non-finite values.")
    matrix = 0.5 * (matrix + matrix.T)
    if len(matrix):
        eigenvalues, eigenvectors = np.linalg.eigh(matrix)
        scale = max(float(np.max(np.abs(eigenvalues))), float(np.max(np.abs(np.diag(matrix)))), 1.0)
        min_eigenvalue = float(eigenvalues.min())
        if min_eigenvalue < -COVARIANCE_PSD_TOLERANCE * scale:
            raise ValueError(
                "Covariance estimation produced a non-positive-semidefinite matrix; "
                f"minimum eigenvalue is {min_eigenvalue:.6g}."
            )
        clipped = np.clip(eigenvalues, 1e-12, None)
        matrix = (eigenvectors * clipped) @ eigenvectors.T
        matrix = 0.5 * (matrix + matrix.T)
    return pd.DataFrame(matrix, index=covariance.index, columns=covariance.columns)


def _estimate_ewma_vol_shrinkage_corr_covariance(
    returns: pd.DataFrame,
    *,
    lookback_days: int,
    parameters: dict[str, object],
    missing_return_policy: str = DEFAULT_MISSING_RETURN_POLICY,
    calculation_frequency: CalculationFrequency = "daily",
    as_of_date: date | None = None,
) -> pd.DataFrame:
    vol_min_observations = int(parameters.get("min_observations", 2))
    vol_coverage = _prepare_return_window_for_covariance(
        returns,
        lookback_days=lookback_days,
        min_observations=vol_min_observations,
        label="Volatility covariance estimation",
        missing_return_policy=missing_return_policy,
        calculation_frequency=calculation_frequency,
        as_of_date=as_of_date,
    )
    vol_window = vol_coverage.returns
    vol_decay = float(parameters.get("vol_decay", parameters.get("decay", 0.97)))
    ewma_covariance = _estimate_ewma_covariance(
        vol_window,
        decay=vol_decay,
        min_observations=vol_min_observations,
    )
    annualized_ewma_covariance = _annualize_covariance(ewma_covariance, vol_window)
    ewma_vol = np.sqrt(np.maximum(np.diag(annualized_ewma_covariance.to_numpy(dtype="float64")), 1e-12))

    corr_min_observations = int(parameters.get("corr_min_observations", parameters.get("min_observations", 2)))
    corr_coverage = _prepare_return_window_for_covariance(
        returns,
        lookback_days=int(parameters.get("corr_lookback_days", lookback_days)),
        min_observations=corr_min_observations,
        label="Correlation estimation",
        missing_return_policy=missing_return_policy,
        calculation_frequency=calculation_frequency,
        as_of_date=as_of_date,
    )
    corr_window = corr_coverage.returns
    _validate_complete_return_coverage(
        corr_window,
        min_observations=corr_min_observations,
        label="Correlation estimation",
    )
    corr_matrix = corr_window.corr().to_numpy(dtype="float64")
    if not np.isfinite(corr_matrix).all():
        raise ValueError("Correlation estimation requires non-zero variance for every active return series.")
    corr_matrix = 0.5 * (corr_matrix + corr_matrix.T)
    np.fill_diagonal(corr_matrix, 1.0)

    corr_shrinkage = float(parameters.get("corr_shrinkage", 0.0))
    if not 0.0 <= corr_shrinkage <= 1.0:
        raise ValueError("corr_shrinkage must be in [0, 1].")
    identity = np.eye(len(corr_matrix), dtype="float64")
    shrunk_corr = (1.0 - corr_shrinkage) * corr_matrix + corr_shrinkage * identity
    covariance = np.diag(ewma_vol) @ shrunk_corr @ np.diag(ewma_vol)
    return pd.DataFrame(covariance, index=returns.columns, columns=returns.columns)


def _estimate_covariance(
    returns: pd.DataFrame,
    *,
    model_id: str,
    lookback_days: int,
    parameters: dict[str, object] | None = None,
    missing_return_policy: str = DEFAULT_MISSING_RETURN_POLICY,
    calculation_frequency: CalculationFrequency = "daily",
    as_of_date: date | None = None,
) -> pd.DataFrame:
    parameters = dict(parameters or {})
    min_observations = int(parameters.get("min_observations", 2))
    if model_id in {"sample_covariance", "simple_covariance", "lw_covariance", "lw", "ewma_covariance"}:
        coverage = _prepare_return_window_for_covariance(
            returns,
            lookback_days=lookback_days,
            min_observations=min_observations,
            label="Covariance estimation",
            missing_return_policy=missing_return_policy,
            calculation_frequency=calculation_frequency,
            as_of_date=as_of_date,
        )
        window = coverage.returns
    if model_id in {"sample_covariance", "simple_covariance"}:
        covariance = _estimate_sample_covariance(window, min_observations=min_observations)
        covariance = _annualize_covariance(covariance, window)
    elif model_id in {"lw_covariance", "lw"}:
        covariance = _estimate_ledoit_wolf_covariance(window)
        covariance = covariance * _infer_periods_per_year(_index_dates(window.dropna(how="any").index))
    elif model_id == "ewma_covariance":
        covariance = _estimate_ewma_covariance(
            window,
            decay=float(parameters.get("decay", 0.94)),
            min_observations=min_observations,
        )
        covariance = _annualize_covariance(covariance, window)
    elif model_id in {"ewma_vol_shrinkage_corr_covariance", "ewma_vol_corr_covariance"}:
        covariance = _estimate_ewma_vol_shrinkage_corr_covariance(
            returns,
            lookback_days=lookback_days,
            parameters=parameters,
            missing_return_policy=missing_return_policy,
            calculation_frequency=calculation_frequency,
            as_of_date=as_of_date,
        )
    else:
        raise ValueError(f"Unsupported covariance model: {model_id}.")

    shrinkage = float(parameters.get("shrinkage", 0.0))
    if shrinkage:
        covariance = _apply_diagonal_shrinkage(covariance, shrinkage)

    return _finalize_covariance(covariance)


def estimate_covariance(
    returns: pd.DataFrame,
    *,
    model_id: str,
    lookback_days: int,
    parameters: dict[str, object] | None = None,
    missing_return_policy: str = DEFAULT_MISSING_RETURN_POLICY,
    calculation_frequency: CalculationFrequency = "daily",
    as_of_date: date | None = None,
) -> pd.DataFrame:
    return _estimate_covariance(
        returns,
        model_id=model_id,
        lookback_days=lookback_days,
        parameters=parameters,
        missing_return_policy=missing_return_policy,
        calculation_frequency=calculation_frequency,
        as_of_date=as_of_date,
    )


def _normalize_positive_vector(values: np.ndarray) -> np.ndarray:
    vector = np.clip(np.asarray(values, dtype="float64"), 0.0, None)
    total = float(vector.sum())
    if total <= 1e-12:
        raise ValueError("Target vector must contain at least one positive value.")
    return vector / total


def _risk_contribution_shares(
    covariance: np.ndarray,
    weights: np.ndarray,
    *,
    contribution_mode: str,
) -> np.ndarray:
    mode = contribution_mode.strip().lower()
    marginal = covariance @ weights
    signed = weights * marginal
    if mode == "signed":
        contributions = signed
    elif mode == "abs":
        contributions = np.sqrt(np.square(signed) + ABS_RC_SMOOTHING_EPS)
    else:
        raise ValueError(f"Unsupported risk contribution mode: {contribution_mode}.")
    contribution_total = float(contributions.sum())
    if contribution_total <= 1e-12:
        raise ValueError("Risk contribution requires positive aggregate portfolio variance.")
    shares = contributions / contribution_total
    if not np.isfinite(shares).all():
        raise ValueError("Risk contribution produced non-finite shares.")
    return shares


def risk_contribution_shares(
    covariance: np.ndarray,
    weights: np.ndarray,
    *,
    contribution_mode: str,
) -> np.ndarray:
    return _risk_contribution_shares(covariance, weights, contribution_mode=contribution_mode)


def _series_observation_frequency(series: pd.Series) -> CalculationFrequency:
    return infer_observation_frequency(_index_dates(series.index))


def _max_period_staleness_days(calculation_frequency: CalculationFrequency) -> int:
    return int(_covariance_parameters(calculation_frequency).get("max_period_staleness_days", 0))


def _periodic_nav_series(
    series: pd.Series,
    *,
    calculation_frequency: CalculationFrequency,
    start_date: date,
    end_date: date,
) -> pd.Series:
    if series.empty:
        return pd.Series(dtype="float64")
    visible = series.loc[(series.index >= start_date) & (series.index <= end_date)].sort_index()
    if visible.empty:
        return pd.Series(dtype="float64")
    rows: dict[date, tuple[date, float]] = {}
    for raw_date, raw_value in visible.items():
        point_date = raw_date if isinstance(raw_date, date) else pd.Timestamp(raw_date).date()
        point_value = _safe_float(raw_value)
        if point_value is None:
            continue
        target_date = period_end_date(point_date, calculation_frequency, final_date=end_date)
        current = rows.get(target_date)
        if current is None or point_date >= current[0]:
            rows[target_date] = (point_date, point_value)
    if calculation_frequency != "daily":
        max_stale_days = _max_period_staleness_days(calculation_frequency)
        for target_date, (point_date, _point_value) in rows.items():
            stale_days = (target_date - point_date).days
            if stale_days >= max_stale_days:
                raise ValueError(
                    f"{calculation_frequency.title()} risk alignment found a stale observation: "
                    f"period ending {target_date.isoformat()} uses {point_date.isoformat()} "
                    f"({stale_days} days old)."
                )
    return pd.Series({target_date: value for target_date, (_point_date, value) in rows.items()}, dtype="float64").sort_index()


def align_nav_series_to_calculation_frequency(
    series: pd.Series,
    *,
    calculation_frequency: CalculationFrequency,
    start_date: date,
    end_date: date,
) -> pd.Series:
    """Use the risk kernel's period-end alignment and staleness policy."""

    return _periodic_nav_series(
        series,
        calculation_frequency=calculation_frequency,
        start_date=start_date,
        end_date=end_date,
    )


def _periodic_series_by_member(
    nav_series_by_member: dict[tuple[str, str], pd.Series],
    *,
    calculation_frequency: CalculationFrequency,
    start_date: date,
    end_date: date,
) -> dict[tuple[str, str], pd.Series]:
    return {
        member_key: _periodic_nav_series(
            series,
            calculation_frequency=calculation_frequency,
            start_date=start_date,
            end_date=end_date,
        )
        for member_key, series in nav_series_by_member.items()
    }


def _aligned_calendar(
    series_list: list[pd.Series],
    *,
    start_date: date,
    end_date: date,
) -> tuple[list[date], date]:
    if not series_list:
        raise ValueError("Selected scope does not contain any aligned series.")
    first_dates = []
    calendar_points: set[date] = set()
    for series in series_list:
        visible = series.loc[(series.index >= start_date) & (series.index <= end_date)]
        if visible.empty:
            visible = series.loc[series.index <= end_date]
        if visible.empty:
            raise ValueError("Selected scope contains a member without usable history in the requested window.")
        first_dates.append(visible.index[0])
        calendar_points.update(visible.index.tolist())
    effective_start = max([start_date, *first_dates])
    calendar = sorted(item for item in calendar_points if effective_start <= item <= end_date)
    if len(calendar) < 2:
        raise ValueError("Risk calculation requires at least two aligned observations.")
    return calendar, effective_start
