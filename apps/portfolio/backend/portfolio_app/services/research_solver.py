from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from math import sqrt

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from portfolio_app.services.calculation_frequency import (
    CalculationFrequency,
    calculation_frequency_profile,
    infer_observation_frequency,
    period_end_date,
)
from portfolio_app.services.market_data import is_usable_market_data_point
import portfolio_app.services.performance as performance_service
from portfolio_app.services.ledger import build_account_workspace
from portfolio_app.services.performance import build_holdings_report
from portfolio_app.services.portfolio_store import (
    get_portfolio,
    list_accounts,
    list_target_set_lines,
    list_target_sets,
    list_taxonomies,
    list_taxonomy_assignments,
    list_taxonomy_nodes,
    list_transactions,
)

ROOT_SCOPE_MEMBER_ID = "__portfolio_root__"
ROOT_SCOPE_LABEL = "Top Level"
TARGET_DIMENSION_SCOPE_DEFAULT = "scope_default"
TARGET_DIMENSION_WEIGHT = "weight"
TARGET_DIMENSION_RISK_BUDGET = "risk_budget"
TARGET_MEMBER_NODE = "taxonomy_node"
TARGET_MEMBER_INSTRUMENT = "instrument"
TARGET_MEMBER_CASH = "cash_bucket"
CAPITAL_MODE_UNIT_NOTIONAL = "unit_notional"
CAPITAL_MODE_FIXED_GROSS = "fixed_gross"
CAPITAL_MODE_TARGET_VOLATILITY = "target_volatility"
RESEARCH_COVARIANCE_MODEL_ID = "ewma_vol_shrinkage_corr_covariance"
RESEARCH_COVARIANCE_PARAMETERS: dict[str, object] = {
    "min_observations": 52,
    "vol_decay": 0.97,
    "corr_min_observations": 52,
    "corr_shrinkage": 0.15,
}
RESEARCH_RISK_CONTRIBUTION_MODE = "signed"
RESEARCH_MAX_RISK_BUDGET_SHARE_GAP = 0.05


@dataclass(frozen=True)
class ScopeMemberRecord:
    member_type: str
    member_id: str
    label: str
    taxonomy_node_id: str | None = None
    default_target_dimension: str | None = None


@dataclass(frozen=True)
class MemberSeries:
    member: ScopeMemberRecord
    nav: pd.Series
    returns: pd.Series
    cumulative_return: float
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScopeTargetSolveResult:
    scope_node_id: str | None
    scope_label: str
    scope_path: str
    default_target_dimension: str
    scope_depth: int
    member_source: str
    return_series: pd.Series
    current_return_series: pd.Series
    member_target_rows: list[dict[str, object]]
    leaf_target_rows: list[dict[str, object]]
    solve_event: dict[str, object]
    scope_solve_events: list[dict[str, object]]
    warnings: list[str]
    resolved_target_rows: list[dict[str, object]]


@dataclass(frozen=True)
class RiskBudgetProblem:
    bucket_ids: list[str]
    covariance: np.ndarray
    target_risk_shares: np.ndarray
    lower_bounds: np.ndarray
    upper_bounds: np.ndarray
    reference_weights: np.ndarray
    contribution_mode: str = RESEARCH_RISK_CONTRIBUTION_MODE


@dataclass(frozen=True)
class RiskBudgetSolution:
    bucket_ids: list[str]
    weights: np.ndarray
    achieved_risk_shares: np.ndarray
    objective_value: float
    max_abs_share_gap: float
    iterations: int
    message: str
    solver_kind: str
    contribution_mode: str


@dataclass(frozen=True)
class LocalRiskBudgetSolve:
    weights: np.ndarray
    max_abs_share_gap: float | None
    solver_kind: str
    solver_detail: str | None
    covariance_model: str | None
    covariance_observations: int
    risk_contribution_mode: str | None
    message: str | None = None


@dataclass(frozen=True)
class TaxonomyResearchState:
    portfolio_id: str
    planning_taxonomy_id: str
    taxonomy_name: str
    root_default_target_dimension: str
    base_currency: str
    as_of_date: date
    node_by_id: dict[str, dict[str, object]]
    children_by_parent: dict[str | None, list[str]]
    node_path_by_id: dict[str, str]
    node_depth_by_id: dict[str, int]
    node_subtree_by_id: dict[str, set[str]]
    direct_assignments_by_node: dict[str, list[dict[str, object]]]
    target_sets_by_scope_type: dict[tuple[str | None, str], list[dict[str, object]]]
    target_lines_by_set_id: dict[str, dict[tuple[str, str], dict[str, object]]]
    account_name_by_id: dict[str, str]
    instrument_detail_cache: dict[str, dict[str, object] | None]
    direct_fx_instruments: dict[tuple[str, str], str]
    frozen_taxonomy_node_ids: frozenset[str]


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_iso_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return date.fromisoformat(normalized[:10])
    except ValueError:
        return None


def _normalized_currency(value: object, *, fallback: str = "USD") -> str:
    normalized = str(value or "").strip().upper()
    return normalized or fallback


def _period_active(*, effective_from: object, effective_to: object, as_of_date: date) -> bool:
    lower = _parse_iso_date(effective_from)
    upper = _parse_iso_date(effective_to)
    if lower and as_of_date < lower:
        return False
    if upper and as_of_date > upper:
        return False
    return True


def _build_direct_fx_instrument_map() -> dict[tuple[str, str], str]:
    direct_instruments: dict[tuple[str, str], str] = {}
    payload = performance_service.get_platform_fx_rates()
    if not isinstance(payload, dict):
        return direct_instruments
    for item in payload.get("rates", []):
        if not is_usable_market_data_point(item):
            continue
        if str(item.get("source_kind") or "") != "direct":
            continue
        base_currency = _normalized_currency(item.get("base_currency"), fallback="")
        quote_currency = _normalized_currency(item.get("quote_currency"), fallback="")
        instrument_id = str(item.get("instrument_id") or "").strip()
        if base_currency and quote_currency and instrument_id:
            direct_instruments[(base_currency, quote_currency)] = instrument_id
    return direct_instruments


def _instrument_detail(
    state: TaxonomyResearchState,
    instrument_id: str,
) -> dict[str, object] | None:
    if instrument_id not in state.instrument_detail_cache:
        state.instrument_detail_cache[instrument_id] = performance_service.get_registry_instrument_detail(instrument_id)
    return state.instrument_detail_cache[instrument_id]


def _candidate_quote_bases(detail: dict[str, object]) -> list[str]:
    policy = detail.get("quote_selection_policy", {})
    candidate_bases: list[str] = []
    if isinstance(policy, dict):
        # Research should consume total-return series whenever the shared
        # registry provides one. Statement valuation still uses the separate
        # valuation role; this path is specifically for return/risk simulation.
        for role in ("total_return", "chart", "valuation", "reference"):
            raw_values = policy.get(role)
            if not isinstance(raw_values, list):
                continue
            for raw_value in raw_values:
                value = str(raw_value or "").strip()
                if value and value not in candidate_bases:
                    candidate_bases.append(value)
    return candidate_bases


def _selected_price_points(
    detail: dict[str, object],
    *,
    end_date: date,
) -> list[tuple[date, float, str]]:
    market_data = detail.get("market_data", [])
    if not isinstance(market_data, list):
        return []
    points_by_basis: dict[str, list[tuple[date, float, str]]] = defaultdict(list)
    for raw_point in market_data:
        if not is_usable_market_data_point(raw_point):
            continue
        point_date = _parse_iso_date(raw_point.get("as_of_date"))
        point_value = _safe_float(raw_point.get("value"))
        quote_basis = str(raw_point.get("quote_basis") or "").strip()
        if point_date is None or point_value is None or not quote_basis or point_date > end_date:
            continue
        points_by_basis[quote_basis].append(
            (
                point_date,
                point_value,
                _normalized_currency(raw_point.get("currency"), fallback=str(detail.get("currency") or "USD")),
            )
        )
    for points in points_by_basis.values():
        points.sort(key=lambda item: item[0])

    for quote_basis in _candidate_quote_bases(detail):
        if points_by_basis.get(quote_basis):
            return points_by_basis[quote_basis]
    return []


def _convert_price_to_base(
    state: TaxonomyResearchState,
    *,
    point_date: date,
    value: float,
    point_currency: str,
) -> float | None:
    normalized_currency = _normalized_currency(point_currency, fallback=state.base_currency)
    if normalized_currency == state.base_currency:
        return value
    fx = performance_service.resolve_fx_rate_on(
        as_of_date=point_date,
        base_currency=normalized_currency,
        quote_currency=state.base_currency,
        direct_instruments=state.direct_fx_instruments,
        instrument_detail_cache=state.instrument_detail_cache,
    )
    rate = _safe_float((fx or {}).get("rate"))
    if rate is None or rate <= 0:
        return None
    return float(value) * rate


def _build_instrument_nav_series(
    state: TaxonomyResearchState,
    *,
    instrument_id: str,
    start_date: date,
    end_date: date,
) -> tuple[pd.Series, list[str]]:
    detail = _instrument_detail(state, instrument_id)
    if not isinstance(detail, dict):
        raise ValueError(f"Instrument detail for {instrument_id} is unavailable.")

    selected_points = _selected_price_points(detail, end_date=end_date)
    warnings: list[str] = []
    if not selected_points:
        raise ValueError(f"{instrument_id} does not have usable market history for the requested period.")

    rows: list[tuple[date, float]] = []
    for point_date, point_value, point_currency in selected_points:
        base_value = _convert_price_to_base(
            state,
            point_date=point_date,
            value=point_value,
            point_currency=point_currency,
        )
        if base_value is None:
            continue
        rows.append((point_date, base_value))
    if not rows:
        raise ValueError(f"{instrument_id} does not have FX-complete market history for the requested period.")

    series = pd.Series({point_date: base_value for point_date, base_value in rows}, dtype="float64").sort_index()
    visible = series.loc[(series.index >= start_date) & (series.index <= end_date)]
    if visible.empty:
        visible = series.loc[series.index <= end_date]
    if visible.empty:
        raise ValueError(f"{instrument_id} does not have any observations on or before the selected end date.")
    if visible.index[0] > start_date:
        warnings.append(
            f"{instrument_id} history starts on {visible.index[0].isoformat()}, so the research window is clipped for this member."
        )
    return visible, warnings


def _build_cash_nav_series(
    *,
    start_date: date,
    end_date: date,
) -> pd.Series:
    calendar = pd.date_range(start=start_date, end=end_date, freq="D").date
    if len(calendar) == 0:
        calendar = [start_date]
    return pd.Series(1.0, index=pd.Index(calendar, dtype="object"), dtype="float64")


def _node_is_cash_subtree(state: TaxonomyResearchState, node_id: str) -> bool:
    subtree = state.node_subtree_by_id.get(node_id, {node_id})
    has_cash_assignment = False
    for subtree_node_id in subtree:
        for assignment in state.direct_assignments_by_node.get(subtree_node_id, []):
            target_scope = str(assignment.get("target_scope") or "")
            if target_scope == TARGET_MEMBER_INSTRUMENT:
                return False
            if target_scope == TARGET_MEMBER_CASH:
                has_cash_assignment = True
    return has_cash_assignment


def _member_is_cash_like(state: TaxonomyResearchState, member: ScopeMemberRecord) -> bool:
    if member.member_type == TARGET_MEMBER_CASH:
        return True
    if member.member_type != TARGET_MEMBER_NODE:
        return False
    return _node_is_cash_subtree(state, member.member_id)


def _scope_is_frozen(state: TaxonomyResearchState, scope_node_id: str | None) -> bool:
    if scope_node_id is None:
        return False
    return scope_node_id in state.frozen_taxonomy_node_ids


def _member_is_frozen(
    state: TaxonomyResearchState,
    *,
    scope_node_id: str | None,
    member: ScopeMemberRecord,
) -> bool:
    if _member_is_cash_like(state, member):
        return False
    if _scope_is_frozen(state, scope_node_id):
        return True
    return member.member_type == TARGET_MEMBER_NODE and member.member_id in state.frozen_taxonomy_node_ids


def _annualized_portfolio_volatility(
    return_window: pd.DataFrame,
    weights: pd.Series,
    *,
    lookback_days: int,
) -> float | None:
    if return_window.empty or weights.empty:
        return None
    aligned = return_window.reindex(columns=weights.index, fill_value=0.0).dropna(how="all")
    if aligned.shape[0] < 2:
        return None
    try:
        covariance = _estimate_covariance(
            aligned,
            model_id=RESEARCH_COVARIANCE_MODEL_ID,
            lookback_days=lookback_days,
            parameters=RESEARCH_COVARIANCE_PARAMETERS,
        )
    except ValueError:
        return None
    ordered_weights = weights.reindex(covariance.index, fill_value=0.0).astype("float64")
    matrix = covariance.to_numpy(dtype="float64")
    vector = ordered_weights.to_numpy(dtype="float64")
    variance = float(vector @ matrix @ vector)
    if variance <= 1e-12:
        return 0.0
    return float(sqrt(max(variance, 0.0)))


def _allocate_cash_weights(
    *,
    cash_index: list[str],
    preferred_weights: pd.Series,
    total_cash_weight: float,
) -> pd.Series:
    if not cash_index:
        return pd.Series(dtype="float64")
    clipped_total = float(total_cash_weight)
    preferred = preferred_weights.reindex(cash_index, fill_value=0.0).astype("float64")
    preferred_total = float(preferred.sum())
    if abs(clipped_total) <= 1e-12:
        return pd.Series(0.0, index=cash_index, dtype="float64")
    if preferred_total > 1e-12:
        return preferred / preferred_total * clipped_total
    return pd.Series(clipped_total / float(len(cash_index)), index=cash_index, dtype="float64")


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


def _pair_periods_per_year(returns: pd.DataFrame, left_column: object, right_column: object) -> float:
    if left_column not in returns.columns or right_column not in returns.columns:
        return 1.0
    pair = returns[[left_column, right_column]].dropna(how="any")
    return _infer_periods_per_year(_index_dates(pair.index))


def _annualize_pairwise_covariance(covariance: pd.DataFrame, returns: pd.DataFrame) -> pd.DataFrame:
    if covariance.empty:
        return covariance
    cleaned = _clean_return_frame(returns).reindex(columns=covariance.columns)
    annualized = covariance.copy().astype("float64")
    for row_label in covariance.index:
        for column_label in covariance.columns:
            factor = _pair_periods_per_year(cleaned, row_label, column_label)
            annualized.loc[row_label, column_label] = float(covariance.loc[row_label, column_label]) * factor
    return annualized


def _clean_return_frame(returns: pd.DataFrame) -> pd.DataFrame:
    if returns.empty:
        return pd.DataFrame()
    cleaned = returns.copy()
    cleaned = cleaned.replace([np.inf, -np.inf], np.nan)
    cleaned = cleaned.sort_index().dropna(how="all")
    return cleaned.astype("float64")


def _select_return_window(
    returns: pd.DataFrame,
    *,
    lookback_days: int,
    min_observations: int,
) -> pd.DataFrame:
    cleaned = _clean_return_frame(returns)
    if cleaned.empty:
        raise ValueError("Covariance estimation requires non-empty returns.")
    end_date = max(cleaned.index)
    start_date = end_date - pd.Timedelta(days=max(int(lookback_days), 1))
    start_day = start_date.date() if hasattr(start_date, "date") else start_date
    window = cleaned.loc[cleaned.index >= start_day].copy()
    required = max(int(min_observations), 2)
    if len(window) < required:
        raise ValueError(
            "Covariance estimation requires at least "
            f"{required} return observations in the selected lookback window."
        )
    return window


def _pair_observation_count(returns: pd.DataFrame, left_label: object, right_label: object) -> int:
    pair_values = returns.loc[:, [left_label, right_label]].to_numpy(dtype="float64")
    return int(np.isfinite(pair_values).all(axis=1).sum())


def _validate_pairwise_return_coverage(
    returns: pd.DataFrame,
    *,
    min_observations: int,
    label: str,
) -> None:
    required = max(int(min_observations), 2)
    columns = list(returns.columns)
    insufficient: list[str] = []
    for row_index, row_label in enumerate(columns):
        for column_label in columns[row_index:]:
            count = _pair_observation_count(returns, row_label, column_label)
            if count < required:
                insufficient.append(f"{row_label}/{column_label}: {count}")
    if insufficient:
        raise ValueError(
            f"{label} requires at least {required} overlapping observations for every active return pair. "
            f"Insufficient pairs: {', '.join(insufficient[:8])}."
        )


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
    _validate_pairwise_return_coverage(
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


def _estimate_pairwise_sample_covariance(returns: pd.DataFrame, *, min_observations: int) -> pd.DataFrame:
    _validate_pairwise_return_coverage(
        returns,
        min_observations=min_observations,
        label="Sample covariance estimation",
    )
    values = returns.to_numpy(dtype="float64")
    covariance = np.zeros((values.shape[1], values.shape[1]), dtype="float64")
    for row_index in range(values.shape[1]):
        for column_index in range(row_index, values.shape[1]):
            pair_values = values[:, [row_index, column_index]]
            valid_values = pair_values[np.isfinite(pair_values).all(axis=1)]
            centered = valid_values - valid_values.mean(axis=0, keepdims=True)
            pair_covariance = float(np.sum(centered[:, 0] * centered[:, 1]) / float(len(valid_values) - 1))
            covariance[row_index, column_index] = pair_covariance
            covariance[column_index, row_index] = pair_covariance
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


def _estimate_ewma_vol_shrinkage_corr_covariance(
    returns: pd.DataFrame,
    *,
    lookback_days: int,
    parameters: dict[str, object],
) -> pd.DataFrame:
    vol_min_observations = int(parameters.get("min_observations", 2))
    vol_window = _select_return_window(
        returns,
        lookback_days=lookback_days,
        min_observations=vol_min_observations,
    )
    vol_decay = float(parameters.get("vol_decay", parameters.get("decay", 0.97)))
    ewma_covariance = _estimate_ewma_covariance(
        vol_window,
        decay=vol_decay,
        min_observations=vol_min_observations,
    )
    annualized_ewma_covariance = _annualize_pairwise_covariance(ewma_covariance, vol_window)
    ewma_vol = np.sqrt(np.maximum(np.diag(annualized_ewma_covariance.to_numpy(dtype="float64")), 1e-12))

    corr_min_observations = int(parameters.get("corr_min_observations", parameters.get("min_observations", 2)))
    corr_window = _select_return_window(
        returns,
        lookback_days=int(parameters.get("corr_lookback_days", lookback_days)),
        min_observations=corr_min_observations,
    )
    _validate_pairwise_return_coverage(
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
) -> pd.DataFrame:
    parameters = dict(parameters or {})
    min_observations = int(parameters.get("min_observations", 2))
    if model_id in {"sample_covariance", "simple_covariance", "lw_covariance", "lw", "ewma_covariance"}:
        window = _select_return_window(
            returns,
            lookback_days=lookback_days,
            min_observations=min_observations,
        )
    if model_id in {"sample_covariance", "simple_covariance"}:
        covariance = _estimate_pairwise_sample_covariance(window, min_observations=min_observations)
        covariance = _annualize_pairwise_covariance(covariance, window)
    elif model_id in {"lw_covariance", "lw"}:
        covariance = _estimate_ledoit_wolf_covariance(window)
        covariance = covariance * _infer_periods_per_year(_index_dates(window.dropna(how="any").index))
    elif model_id == "ewma_covariance":
        covariance = _estimate_ewma_covariance(
            window,
            decay=float(parameters.get("decay", 0.94)),
            min_observations=min_observations,
        )
        covariance = _annualize_pairwise_covariance(covariance, window)
    elif model_id in {"ewma_vol_shrinkage_corr_covariance", "ewma_vol_corr_covariance"}:
        covariance = _estimate_ewma_vol_shrinkage_corr_covariance(
            returns,
            lookback_days=lookback_days,
            parameters=parameters,
        )
    else:
        raise ValueError(f"Unsupported covariance model: {model_id}.")

    shrinkage = float(parameters.get("shrinkage", 0.0))
    if shrinkage:
        covariance = _apply_diagonal_shrinkage(covariance, shrinkage)

    matrix = covariance.to_numpy(dtype="float64")
    if not np.isfinite(matrix).all():
        raise ValueError("Covariance estimation produced non-finite values.")
    matrix = 0.5 * (matrix + matrix.T)
    if len(matrix):
        eigenvalues, eigenvectors = np.linalg.eigh(matrix)
        clipped = np.clip(eigenvalues, 1e-12, None)
        matrix = (eigenvectors * clipped) @ eigenvectors.T
        matrix = 0.5 * (matrix + matrix.T)
    return pd.DataFrame(matrix, index=covariance.index, columns=covariance.columns)


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
        contributions = np.abs(signed)
    else:
        raise ValueError(f"Unsupported risk contribution mode: {contribution_mode}.")
    contribution_total = float(contributions.sum())
    if contribution_total <= 1e-12:
        return np.full(len(weights), 1.0 / max(len(weights), 1), dtype="float64")
    shares = contributions / contribution_total
    if not np.isfinite(shares).all():
        raise ValueError("Risk contribution produced non-finite shares.")
    return shares


def _project_to_bounded_simplex(
    weights: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
) -> np.ndarray:
    projected = np.clip(np.asarray(weights, dtype="float64"), lower, upper)
    total = float(projected.sum())
    if abs(total - 1.0) <= 1e-10:
        return projected
    if total < 1.0:
        deficit = 1.0 - total
        capacity = upper - projected
        available = float(capacity.sum())
        if available + 1e-12 < deficit:
            raise ValueError("Risk budget bounds are infeasible: cannot reach total weight 1.")
        if available > 0:
            projected = projected + deficit * capacity / available
    else:
        excess = total - 1.0
        reducible = projected - lower
        available = float(reducible.sum())
        if available + 1e-12 < excess:
            raise ValueError("Risk budget bounds are infeasible: cannot reduce total weight to 1.")
        if available > 0:
            projected = projected - excess * reducible / available
    if abs(float(projected.sum()) - 1.0) > 1e-8:
        raise ValueError("Failed to project risk budget weights to bounded simplex.")
    return projected


def _validate_risk_budget_problem(problem: RiskBudgetProblem) -> None:
    count = len(problem.bucket_ids)
    if count == 0:
        raise ValueError("Risk budget problem must contain at least one bucket.")
    if problem.covariance.shape != (count, count):
        raise ValueError("Risk budget covariance shape does not match bucket dimension.")
    if problem.target_risk_shares.shape != (count,):
        raise ValueError("Risk budget target shares shape is invalid.")
    if problem.lower_bounds.shape != (count,) or problem.upper_bounds.shape != (count,):
        raise ValueError("Risk budget bounds shape is invalid.")
    if problem.reference_weights.shape != (count,):
        raise ValueError("Risk budget reference weights shape is invalid.")
    if np.any(problem.upper_bounds < problem.lower_bounds):
        raise ValueError("Risk budget upper bounds cannot be smaller than lower bounds.")
    if abs(float(problem.target_risk_shares.sum()) - 1.0) > 1e-8:
        raise ValueError("Risk budget target shares must sum to 1.")


def _build_risk_budget_initial_guesses(
    problem: RiskBudgetProblem,
    reference_weights: np.ndarray,
) -> list[np.ndarray]:
    diagonal = np.diag(problem.covariance)
    vol = np.sqrt(np.maximum(diagonal, 1e-12))
    candidates = [
        reference_weights,
        problem.target_risk_shares,
        np.ones(len(problem.bucket_ids), dtype="float64"),
        problem.target_risk_shares / vol,
        problem.target_risk_shares / np.maximum(diagonal, 1e-12),
        0.5 * reference_weights + 0.5 * (problem.target_risk_shares / vol),
    ]
    rng = np.random.default_rng(0)
    for _ in range(8):
        candidates.append(rng.dirichlet(np.ones(len(problem.bucket_ids), dtype="float64")))

    guesses: list[np.ndarray] = []
    seen: set[tuple[float, ...]] = set()
    for candidate in candidates:
        vector = np.asarray(candidate, dtype="float64")
        total = float(vector.sum())
        if total <= 1e-12:
            vector = np.ones(len(problem.bucket_ids), dtype="float64") / float(len(problem.bucket_ids))
        else:
            vector = vector / total
        projected = _project_to_bounded_simplex(vector, problem.lower_bounds, problem.upper_bounds)
        key = tuple(np.round(projected, 12))
        if key in seen:
            continue
        seen.add(key)
        guesses.append(projected)
    return guesses


def _solve_regularized_risk_budget_slsqp(
    problem: RiskBudgetProblem,
    *,
    reference_weights: np.ndarray,
    initial_guesses: list[np.ndarray],
    max_iterations: int,
) -> RiskBudgetSolution:
    def objective(weights: np.ndarray) -> float:
        shares = _risk_contribution_shares(
            problem.covariance,
            weights,
            contribution_mode=problem.contribution_mode,
        )
        share_gap = shares - problem.target_risk_shares
        reference_gap = weights - reference_weights
        return (
            float(np.max(np.abs(share_gap)) ** 2)
            + 1e-2 * float(share_gap @ share_gap)
            + 1e-4 * float(reference_gap @ reference_gap)
        )

    constraints = [{"type": "eq", "fun": lambda weights: float(np.sum(weights) - 1.0)}]
    bounds = list(zip(problem.lower_bounds.tolist(), problem.upper_bounds.tolist()))
    best_solution: tuple[np.ndarray, np.ndarray, float, int, str] | None = None
    failures: list[str] = []
    for x0 in initial_guesses:
        result = minimize(
            objective,
            x0=x0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"ftol": 1e-12, "maxiter": max_iterations, "disp": False},
        )
        if not result.success:
            failures.append(str(result.message))
            continue
        weights = np.asarray(result.x, dtype="float64")
        shares = _risk_contribution_shares(
            problem.covariance,
            weights,
            contribution_mode=problem.contribution_mode,
        )
        share_gap = shares - problem.target_risk_shares
        candidate = (
            weights,
            shares,
            float(np.max(np.abs(share_gap))),
            int(getattr(result, "nit", 0)),
            str(result.message),
        )
        if best_solution is None or candidate[2] < best_solution[2] - 1e-12:
            best_solution = candidate
        elif best_solution is not None and abs(candidate[2] - best_solution[2]) <= 1e-12:
            if objective(candidate[0]) < objective(best_solution[0]) - 1e-18:
                best_solution = candidate

    if best_solution is None:
        detail = "" if not failures else f": {'; '.join(sorted(set(failures)))}"
        raise ValueError(f"Risk budget solver failed{detail}")

    weights, shares, max_abs_share_gap, iterations, message = best_solution
    return RiskBudgetSolution(
        bucket_ids=problem.bucket_ids,
        weights=weights,
        achieved_risk_shares=shares,
        objective_value=float(objective(weights)),
        max_abs_share_gap=max_abs_share_gap,
        iterations=iterations,
        message=message,
        solver_kind="slsqp",
        contribution_mode=problem.contribution_mode,
    )


def _solve_minimax_risk_budget_slsqp(
    problem: RiskBudgetProblem,
    *,
    reference_weights: np.ndarray,
    initial_guesses: list[np.ndarray],
    max_iterations: int,
) -> RiskBudgetSolution | None:
    def share_gap(weights: np.ndarray) -> np.ndarray:
        return (
            _risk_contribution_shares(
                problem.covariance,
                weights,
                contribution_mode=problem.contribution_mode,
            )
            - problem.target_risk_shares
        )

    constraints = [{"type": "eq", "fun": lambda variables: float(np.sum(variables[:-1]) - 1.0)}]
    for index in range(len(problem.bucket_ids)):
        constraints.append(
            {
                "type": "ineq",
                "fun": lambda variables, index=index: float(variables[-1] - share_gap(variables[:-1])[index]),
            }
        )
        constraints.append(
            {
                "type": "ineq",
                "fun": lambda variables, index=index: float(variables[-1] + share_gap(variables[:-1])[index]),
            }
        )

    bounds = list(zip(problem.lower_bounds.tolist(), problem.upper_bounds.tolist())) + [(0.0, 1.0)]
    best_solution: tuple[np.ndarray, np.ndarray, float, float, int, str] | None = None
    for guess in initial_guesses:
        initial_gap = float(np.max(np.abs(share_gap(guess))))
        result = minimize(
            lambda variables: float(variables[-1]),
            x0=np.concatenate([guess, [initial_gap]]),
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"ftol": 1e-15, "maxiter": max_iterations, "disp": False},
        )
        if not result.success:
            continue
        weights = np.asarray(result.x[:-1], dtype="float64")
        shares = _risk_contribution_shares(
            problem.covariance,
            weights,
            contribution_mode=problem.contribution_mode,
        )
        gap = shares - problem.target_risk_shares
        reference_gap = weights - reference_weights
        secondary_score = float(gap @ gap) + 1e-4 * float(reference_gap @ reference_gap)
        candidate = (
            weights,
            shares,
            float(np.max(np.abs(gap))),
            secondary_score,
            int(getattr(result, "nit", 0)),
            str(result.message),
        )
        if best_solution is None or candidate[2] < best_solution[2] - 1e-9:
            best_solution = candidate
        elif best_solution is not None and abs(candidate[2] - best_solution[2]) <= 1e-9:
            if candidate[3] < best_solution[3] - 1e-12:
                best_solution = candidate

    if best_solution is None:
        return None

    weights, shares, max_abs_share_gap, objective_value, iterations, message = best_solution
    return RiskBudgetSolution(
        bucket_ids=problem.bucket_ids,
        weights=weights,
        achieved_risk_shares=shares,
        objective_value=objective_value,
        max_abs_share_gap=max_abs_share_gap,
        iterations=iterations,
        message=f"{message} (minimax refinement)",
        solver_kind="slsqp_minimax",
        contribution_mode=problem.contribution_mode,
    )


def _solve_risk_budget_problem(
    problem: RiskBudgetProblem,
    *,
    max_iterations: int = 500,
) -> RiskBudgetSolution:
    _validate_risk_budget_problem(problem)
    reference_weights = _project_to_bounded_simplex(
        problem.reference_weights,
        problem.lower_bounds,
        problem.upper_bounds,
    )
    initial_guesses = _build_risk_budget_initial_guesses(problem, reference_weights)
    primary = _solve_regularized_risk_budget_slsqp(
        problem,
        reference_weights=reference_weights,
        initial_guesses=initial_guesses,
        max_iterations=max_iterations,
    )
    if primary.max_abs_share_gap <= RESEARCH_MAX_RISK_BUDGET_SHARE_GAP:
        best = primary
    else:
        minimax = _solve_minimax_risk_budget_slsqp(
            problem,
            reference_weights=reference_weights,
            initial_guesses=[*initial_guesses, primary.weights],
            max_iterations=max(max_iterations * 4, 1500),
        )
        best = (
            minimax
            if minimax is not None and minimax.max_abs_share_gap < primary.max_abs_share_gap - 1e-9
            else primary
        )
    if best.max_abs_share_gap > RESEARCH_MAX_RISK_BUDGET_SHARE_GAP + 1e-12:
        raise ValueError(
            "Risk budget solver could not satisfy target risk shares within "
            f"{RESEARCH_MAX_RISK_BUDGET_SHARE_GAP:.2%}; achieved max gap {best.max_abs_share_gap:.2%}."
        )
    achieved = np.asarray(best.achieved_risk_shares, dtype="float64")
    if best.contribution_mode == "signed" and float(achieved.min()) < -1e-12:
        raise ValueError("Risk budget solver produced a negative signed risk share.")
    return best


def _solve_risk_budget_weights(
    *,
    target_shares: np.ndarray,
    return_window: pd.DataFrame,
    reference_weights: np.ndarray | None,
    lookback_days: int,
) -> LocalRiskBudgetSolve:
    count = len(target_shares)
    cleaned_observation_count = int(len(_clean_return_frame(return_window)))
    if count == 1:
        return LocalRiskBudgetSolve(
            weights=np.asarray([1.0], dtype="float64"),
            max_abs_share_gap=0.0,
            solver_kind="single-member",
            solver_detail=None,
            covariance_model=None,
            covariance_observations=0,
            risk_contribution_mode=None,
        )

    if cleaned_observation_count < 2:
        raise ValueError(
            "Risk budget solve requires at least two aligned return observations; "
            f"got {cleaned_observation_count}."
        )

    reference = (
        np.asarray(reference_weights, dtype="float64")
        if reference_weights is not None and len(reference_weights) == count
        else np.asarray(target_shares, dtype="float64")
    )
    reference = _normalize_positive_vector(reference)
    target = _normalize_positive_vector(np.asarray(target_shares, dtype="float64"))
    covariance = _estimate_covariance(
        return_window,
        model_id=RESEARCH_COVARIANCE_MODEL_ID,
        lookback_days=lookback_days,
        parameters=RESEARCH_COVARIANCE_PARAMETERS,
    )
    if covariance.shape != (count, count):
        raise ValueError("Risk covariance dimension does not match selected scope members.")
    primary_problem = RiskBudgetProblem(
        bucket_ids=list(return_window.columns),
        covariance=covariance.to_numpy(dtype="float64"),
        target_risk_shares=target,
        lower_bounds=np.zeros(count, dtype="float64"),
        upper_bounds=np.ones(count, dtype="float64"),
        reference_weights=reference,
        contribution_mode=RESEARCH_RISK_CONTRIBUTION_MODE,
    )
    solution = _solve_risk_budget_problem(primary_problem)

    return LocalRiskBudgetSolve(
        weights=_normalize_positive_vector(solution.weights),
        max_abs_share_gap=float(solution.max_abs_share_gap),
        solver_kind="risk-budget",
        solver_detail=solution.solver_kind,
        covariance_model=RESEARCH_COVARIANCE_MODEL_ID,
        covariance_observations=cleaned_observation_count,
        risk_contribution_mode=solution.contribution_mode,
        message=solution.message,
    )


def _series_observation_frequency(series: pd.Series) -> CalculationFrequency:
    return infer_observation_frequency(_index_dates(series.index))


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
    return pd.Series({target_date: value for target_date, (_point_date, value) in rows.items()}, dtype="float64").sort_index()


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
        raise ValueError("Selected scope does not contain any research members.")
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
        raise ValueError("Research solve requires at least two aligned observations.")
    return calendar, effective_start


def _align_member_series(
    members: list[ScopeMemberRecord],
    nav_series_by_member: dict[tuple[str, str], pd.Series],
    *,
    start_date: date,
    end_date: date,
    calculation_frequency: CalculationFrequency = "daily",
) -> tuple[list[MemberSeries], list[date], list[str]]:
    periodic_nav_series_by_member = _periodic_series_by_member(
        nav_series_by_member,
        calculation_frequency=calculation_frequency,
        start_date=start_date,
        end_date=end_date,
    )
    calendar, effective_start = _aligned_calendar(
        list(periodic_nav_series_by_member.values()),
        start_date=start_date,
        end_date=end_date,
    )
    rendered: list[MemberSeries] = []
    warnings: list[str] = []
    for member in members:
        raw_series = nav_series_by_member[(member.member_type, member.member_id)].sort_index()
        periodic_series = periodic_nav_series_by_member[(member.member_type, member.member_id)]
        aligned = periodic_series.reindex(calendar)
        first_valid_index = aligned.first_valid_index()
        if first_valid_index is None:
            raise ValueError(f"{member.label} does not have enough history for aligned research dates.")
        base_value = float(aligned.loc[first_valid_index])
        if abs(base_value) <= 1e-12:
            raise ValueError(f"{member.label} starts with a non-positive base value.")
        normalized_nav = aligned / base_value
        actual_returns = normalized_nav.pct_change(fill_method=None)
        if len(calendar) > 0:
            actual_returns.loc[calendar[0]] = np.nan
        returns = actual_returns.astype("float64")
        last_valid_value = normalized_nav.dropna().iloc[-1]
        cumulative_return = float(last_valid_value - 1.0)
        if raw_series.index[0] > start_date:
            warnings.append(
                f"{member.label} history begins on {raw_series.index[0].isoformat()}, clipping selected research start to {effective_start.isoformat()}."
            )
        rendered.append(
            MemberSeries(
                member=member,
                nav=normalized_nav,
                returns=returns,
                cumulative_return=cumulative_return,
            )
        )
    return rendered, calendar, warnings


def _scope_target_sets(
    state: TaxonomyResearchState,
    *,
    scope_node_id: str | None,
    target_set_type: str,
    as_of_date: date,
) -> dict[str, object] | None:
    candidates = state.target_sets_by_scope_type.get((scope_node_id, target_set_type), [])
    configured = [
        item
        for item in candidates
        if str(item.get("status") or "active") == "active"
        and _period_active(
            effective_from=item.get("effective_from"),
            effective_to=item.get("effective_to"),
            as_of_date=as_of_date,
        )
    ]
    if not configured:
        return None
    configured.sort(
        key=lambda item: (
            str(item.get("effective_from") or ""),
            str(item.get("target_set_id") or ""),
        ),
        reverse=True,
    )
    return configured[0]


def _resolve_dimension_target_rows(
    state: TaxonomyResearchState,
    *,
    scope_node_id: str | None,
    scope_members: list[ScopeMemberRecord],
    as_of_date: date,
    selected_dimension: str,
) -> tuple[list[dict[str, object]], list[str]]:
    warnings: list[str] = []

    resolved_dimension = (
        _scope_default_target_dimension(state, scope_node_id)
        if selected_dimension == TARGET_DIMENSION_SCOPE_DEFAULT
        else selected_dimension
    )

    candidate_types = ["taa", "saa"]
    line_keys = [(member.member_type, member.member_id) for member in scope_members]

    enabled_field = "weight_enabled" if resolved_dimension == TARGET_DIMENSION_WEIGHT else "risk_budget_enabled"
    value_field = "target_weight" if resolved_dimension == TARGET_DIMENSION_WEIGHT else "target_risk_share"
    for target_set_type in candidate_types:
        target_set = _scope_target_sets(
            state,
            scope_node_id=scope_node_id,
            target_set_type=target_set_type,
            as_of_date=as_of_date,
        )
        if target_set is None or not bool(target_set.get(enabled_field)):
            continue
        line_map = state.target_lines_by_set_id.get(str(target_set.get("target_set_id") or ""), {})
        rendered_rows: list[dict[str, object]] = []
        complete = True
        for member in scope_members:
            line = line_map.get((member.member_type, member.member_id))
            if line is None or line.get(value_field) is None:
                complete = False
                break
            rendered_rows.append(
                {
                    "member_type": member.member_type,
                    "member_id": member.member_id,
                    "label": member.label,
                    "taxonomy_node_id": member.taxonomy_node_id,
                    "default_target_dimension": member.default_target_dimension,
                    "selected_dimension": resolved_dimension,
                    "selected_value": float(line.get(value_field)),
                    "target_weight": _safe_float(line.get("target_weight")),
                    "target_risk_share": _safe_float(line.get("target_risk_share")),
                    "source_target_set_id": target_set.get("target_set_id"),
                    "source_target_set_type": target_set_type,
                }
            )
        if complete and len(rendered_rows) == len(line_keys):
            selected_total = sum(float(row["selected_value"]) for row in rendered_rows)
            expected_total = (
                0.0
                if resolved_dimension == TARGET_DIMENSION_RISK_BUDGET
                and all(_member_is_cash_like(state, member) for member in scope_members)
                else 1.0
            )
            if any(float(row["selected_value"]) < -1e-12 for row in rendered_rows):
                raise ValueError(f"{target_set.get('name') or target_set_type} has negative {resolved_dimension} targets.")
            if abs(selected_total - expected_total) > 1e-6:
                raise ValueError(
                    f"{target_set.get('name') or target_set_type} {resolved_dimension} targets must sum to "
                    f"{expected_total:.6f}; got {selected_total:.6f}."
                )
            return rendered_rows, warnings

    if len(scope_members) == 1:
        member = scope_members[0]
        selected_value = (
            0.0
            if resolved_dimension == TARGET_DIMENSION_RISK_BUDGET and _member_is_cash_like(state, member)
            else 1.0
        )
        return [
            {
                "member_type": member.member_type,
                "member_id": member.member_id,
                "label": member.label,
                "taxonomy_node_id": member.taxonomy_node_id,
                "default_target_dimension": member.default_target_dimension,
                "selected_dimension": resolved_dimension,
                "selected_value": selected_value,
                "target_weight": 1.0 if resolved_dimension == TARGET_DIMENSION_WEIGHT else None,
                "target_risk_share": selected_value if resolved_dimension == TARGET_DIMENSION_RISK_BUDGET else None,
                "source_target_set_id": None,
                "source_target_set_type": None,
                "source_label_override": "Single Member",
            }
        ], warnings

    scope_label = str(state.node_by_id.get(scope_node_id, {}).get("node_name") or ROOT_SCOPE_LABEL)
    raise ValueError(
        f"{scope_label} has no active complete {resolved_dimension} target set for the requested scope members."
    )


def _scope_members(
    state: TaxonomyResearchState,
    *,
    scope_node_id: str | None,
) -> tuple[list[ScopeMemberRecord], str]:
    child_node_ids = state.children_by_parent.get(scope_node_id, [])
    if child_node_ids:
        return (
            [
                ScopeMemberRecord(
                    member_type=TARGET_MEMBER_NODE,
                    member_id=node_id,
                    label=str(state.node_by_id[node_id]["node_name"]),
                    taxonomy_node_id=node_id,
                    default_target_dimension=str(state.node_by_id[node_id].get("default_target_dimension") or TARGET_DIMENSION_WEIGHT),
                )
                for node_id in child_node_ids
            ],
            "child_sleeves",
        )

    if scope_node_id is None:
        raise ValueError("Portfolio root scope requires at least one top-level sleeve.")

    direct_assignments = state.direct_assignments_by_node.get(scope_node_id, [])
    if not direct_assignments:
        raise ValueError("Selected scope does not have child sleeves or direct assigned members.")

    members: list[ScopeMemberRecord] = []
    for assignment in direct_assignments:
        target_scope = str(assignment.get("target_scope") or "")
        target_entity_id = str(assignment.get("target_entity_id") or "")
        if target_scope == TARGET_MEMBER_INSTRUMENT:
            detail = _instrument_detail(state, target_entity_id)
            label = str((detail or {}).get("instrument_name") or target_entity_id)
        elif target_scope == TARGET_MEMBER_CASH:
            label = state.account_name_by_id.get(target_entity_id, target_entity_id)
        else:
            continue
        members.append(
            ScopeMemberRecord(
                member_type=target_scope,
                member_id=target_entity_id,
                label=label,
                taxonomy_node_id=scope_node_id,
                default_target_dimension=str(
                    state.node_by_id.get(scope_node_id, {}).get("default_target_dimension") or TARGET_DIMENSION_WEIGHT
                ),
            )
        )
    if not members:
        raise ValueError("Selected scope does not have supported direct members.")
    return members, "direct_members"


def _scope_source_frequencies(
    state: TaxonomyResearchState,
    *,
    scope_node_id: str | None,
    start_date: date,
    end_date: date,
) -> list[CalculationFrequency]:
    if scope_node_id is None:
        node_ids = set(state.node_by_id)
    else:
        node_ids = state.node_subtree_by_id.get(scope_node_id, {scope_node_id})
    frequencies: list[CalculationFrequency] = []
    seen_instrument_ids: set[str] = set()
    for node_id in node_ids:
        for assignment in state.direct_assignments_by_node.get(node_id, []):
            if str(assignment.get("target_scope") or "") != TARGET_MEMBER_INSTRUMENT:
                continue
            instrument_id = str(assignment.get("target_entity_id") or "").strip()
            if not instrument_id or instrument_id in seen_instrument_ids:
                continue
            seen_instrument_ids.add(instrument_id)
            detail = _instrument_detail(state, instrument_id)
            if not isinstance(detail, dict):
                continue
            dates = [
                point_date
                for point_date, _point_value, _point_currency in _selected_price_points(detail, end_date=end_date)
                if start_date <= point_date <= end_date
            ]
            frequencies.append(_series_observation_frequency(pd.Series(1.0, index=pd.Index(dates, dtype="object"))))
    return frequencies


def _scope_default_target_dimension(state: TaxonomyResearchState, scope_node_id: str | None) -> str:
    if scope_node_id:
        return str(state.node_by_id.get(scope_node_id, {}).get("default_target_dimension") or TARGET_DIMENSION_WEIGHT)
    return str(state.root_default_target_dimension or TARGET_DIMENSION_WEIGHT)


def _solver_return_window(
    *,
    members: list[ScopeMemberRecord],
    nav_series_by_member: dict[tuple[str, str], pd.Series],
    as_of_date: date,
    lookback_days: int,
    calculation_frequency: CalculationFrequency,
) -> pd.DataFrame:
    if not members:
        return pd.DataFrame()
    start_date = as_of_date - pd.Timedelta(days=max(int(lookback_days), 1))
    start_day = start_date.date() if hasattr(start_date, "date") else start_date
    try:
        aligned_members, _calendar, _warnings = _align_member_series(
            members,
            nav_series_by_member,
            start_date=start_day,
            end_date=as_of_date,
            calculation_frequency=calculation_frequency,
        )
    except ValueError:
        return pd.DataFrame()
    frame = pd.DataFrame(
        {
            f"{member.member.member_type}::{member.member.member_id}": member.returns
            for member in aligned_members
        }
    )
    return frame.astype("float64")


def _series_to_nav(return_series: pd.Series, *, as_of_date: date) -> pd.Series:
    if return_series.empty:
        return pd.Series({as_of_date: 1.0}, dtype="float64")
    return (1.0 + return_series.astype("float64")).cumprod()


def _weighted_complete_return_series(return_window: pd.DataFrame, weights: pd.Series) -> pd.Series:
    if return_window.empty or weights.empty:
        return pd.Series(dtype="float64")
    aligned_weights = weights.reindex(return_window.columns, fill_value=0.0).astype("float64")
    active_columns = [column for column in return_window.columns if abs(float(aligned_weights.get(column, 0.0))) > 1e-12]
    if not active_columns:
        return pd.Series(0.0, index=return_window.index, dtype="float64")
    active_returns = return_window.reindex(columns=active_columns)
    complete_mask = active_returns.notna().all(axis=1)
    result = pd.Series(np.nan, index=return_window.index, dtype="float64")
    result.loc[complete_mask] = (
        active_returns.loc[complete_mask] * aligned_weights.reindex(active_columns, fill_value=0.0)
    ).sum(axis=1)
    return result.astype("float64")


def _estimate_scope_risk_share_map(
    *,
    members: list[ScopeMemberRecord],
    nav_series_by_member: dict[tuple[str, str], pd.Series],
    risk_keys: list[str],
    weights_by_key: pd.Series,
    as_of_date: date,
    lookback_days: int,
    calculation_frequency: CalculationFrequency,
    contribution_mode: str,
) -> dict[str, float]:
    if not risk_keys:
        return {}
    risky_weights = weights_by_key.reindex(risk_keys, fill_value=0.0).astype("float64")
    if float(np.abs(risky_weights.to_numpy(dtype="float64")).sum()) <= 1e-12:
        return {key: 0.0 for key in risk_keys}
    if len(risk_keys) == 1:
        return {risk_keys[0]: 1.0}
    member_by_key = {f"{member.member_type}::{member.member_id}": member for member in members}
    solver_members = [member_by_key[key] for key in risk_keys if key in member_by_key]
    if len(solver_members) != len(risk_keys):
        return {}
    return_window = _solver_return_window(
        members=solver_members,
        nav_series_by_member=nav_series_by_member,
        as_of_date=as_of_date,
        lookback_days=lookback_days,
        calculation_frequency=calculation_frequency,
    )
    if return_window.empty:
        return {}
    try:
        covariance = _estimate_covariance(
            return_window.reindex(columns=risk_keys),
            model_id=RESEARCH_COVARIANCE_MODEL_ID,
            lookback_days=lookback_days,
            parameters=RESEARCH_COVARIANCE_PARAMETERS,
        )
        shares = _risk_contribution_shares(
            covariance.reindex(index=risk_keys, columns=risk_keys).to_numpy(dtype="float64"),
            risky_weights.to_numpy(dtype="float64"),
            contribution_mode=contribution_mode,
        )
    except ValueError:
        return {}
    return {key: float(shares[index]) for index, key in enumerate(risk_keys)}


def _solve_current_scope(
    state: TaxonomyResearchState,
    *,
    scope_node_id: str | None,
    as_of_date: date,
    lookback_days: int,
    calculation_frequency: CalculationFrequency,
    target_dimension: str,
    capital_mode: str,
    gross_exposure: float | None,
    target_volatility: float | None,
    max_gross_exposure: float | None,
    apply_capital_overlay: bool,
) -> ScopeTargetSolveResult:
    scope_label = str(state.node_by_id.get(scope_node_id, {}).get("node_name") or ROOT_SCOPE_LABEL)
    scope_path = state.node_path_by_id.get(scope_node_id or ROOT_SCOPE_MEMBER_ID, ROOT_SCOPE_LABEL)
    scope_depth = int(state.node_depth_by_id.get(scope_node_id, 0))
    default_target_dimension = _scope_default_target_dimension(state, scope_node_id)
    start_date = as_of_date - pd.Timedelta(days=max(int(lookback_days), 1))
    start_day = start_date.date() if hasattr(start_date, "date") else start_date

    members, member_source = _scope_members(state, scope_node_id=scope_node_id)
    nav_series_by_member: dict[tuple[str, str], pd.Series] = {}
    current_nav_series_by_member: dict[tuple[str, str], pd.Series] = {}
    member_by_key: dict[str, ScopeMemberRecord] = {}
    child_results_by_key: dict[str, ScopeTargetSolveResult] = {}
    child_scope_solve_events: list[dict[str, object]] = []
    warnings: list[str] = []

    for member in members:
        member_key = f"{member.member_type}::{member.member_id}"
        member_by_key[member_key] = member
        if member.member_type == TARGET_MEMBER_NODE:
            child_result = _solve_current_scope(
                state,
                scope_node_id=member.member_id,
                as_of_date=as_of_date,
                lookback_days=lookback_days,
                calculation_frequency=calculation_frequency,
                target_dimension=TARGET_DIMENSION_SCOPE_DEFAULT,
                capital_mode=capital_mode,
                gross_exposure=gross_exposure,
                target_volatility=target_volatility,
                max_gross_exposure=max_gross_exposure,
                apply_capital_overlay=False,
            )
            child_results_by_key[member_key] = child_result
            child_scope_solve_events.extend(child_result.scope_solve_events)
            nav_series_by_member[(member.member_type, member.member_id)] = _series_to_nav(
                child_result.return_series,
                as_of_date=as_of_date,
            )
            current_nav_series_by_member[(member.member_type, member.member_id)] = _series_to_nav(
                child_result.current_return_series,
                as_of_date=as_of_date,
            )
            warnings.extend(child_result.warnings)
        elif member.member_type == TARGET_MEMBER_CASH:
            cash_nav = _build_cash_nav_series(
                start_date=start_day,
                end_date=as_of_date,
            )
            nav_series_by_member[(member.member_type, member.member_id)] = cash_nav
            current_nav_series_by_member[(member.member_type, member.member_id)] = cash_nav
        else:
            instrument_nav, instrument_warnings = _build_instrument_nav_series(
                state,
                instrument_id=member.member_id,
                start_date=start_day,
                end_date=as_of_date,
            )
            nav_series_by_member[(member.member_type, member.member_id)] = instrument_nav
            current_nav_series_by_member[(member.member_type, member.member_id)] = instrument_nav
            warnings.extend(instrument_warnings)

    current_actual_rows, current_actual_warnings = _current_scope_actuals(
        state,
        scope_node_id=scope_node_id,
        as_of_date=as_of_date,
    )
    warnings.extend(current_actual_warnings)
    current_actual_weight_by_key = {
        f"{item['member_type']}::{item['member_id']}": float(_safe_float(item.get("current_weight")) or 0.0)
        for item in current_actual_rows
        if item.get("member_type") in {TARGET_MEMBER_NODE, TARGET_MEMBER_INSTRUMENT, TARGET_MEMBER_CASH}
    }

    resolved_rows, resolution_warnings = _resolve_dimension_target_rows(
        state,
        scope_node_id=scope_node_id,
        scope_members=members,
        as_of_date=as_of_date,
        selected_dimension=target_dimension,
    )
    warnings.extend(resolution_warnings)

    member_keys = [f"{member.member_type}::{member.member_id}" for member in members]
    target_dimension_used = str(resolved_rows[0]["selected_dimension"]) if resolved_rows else TARGET_DIMENSION_WEIGHT
    target_values = pd.Series(
        {
            f"{row['member_type']}::{row['member_id']}": float(row["selected_value"])
            for row in resolved_rows
        },
        dtype="float64",
    ).reindex(member_keys, fill_value=0.0)
    configured_weight_by_key = {
        f"{row['member_type']}::{row['member_id']}": _safe_float(row.get("target_weight"))
        for row in resolved_rows
    }
    cash_like_keys = [key for key in member_keys if _member_is_cash_like(state, member_by_key[key])]
    frozen_keys = [
        key
        for key in member_keys
        if _member_is_frozen(
            state,
            scope_node_id=scope_node_id,
            member=member_by_key[key],
        )
    ]
    fixed_weight_targets = pd.Series(
        {
            key: current_actual_weight_by_key[key]
            if key in current_actual_weight_by_key
            else float(configured_weight_by_key.get(key) or 0.0)
            for key in frozen_keys
        },
        dtype="float64",
    ).clip(lower=0.0)
    risk_keys = [key for key in member_keys if key not in cash_like_keys and key not in frozen_keys]
    non_cash_keys = [key for key in member_keys if key not in cash_like_keys]
    preferred_cash_weights = pd.Series(
        {
            f"{row['member_type']}::{row['member_id']}": float(_safe_float(row.get("target_weight")) or 0.0)
            for row in resolved_rows
            if f"{row['member_type']}::{row['member_id']}" in cash_like_keys
        },
        dtype="float64",
    ).reindex(cash_like_keys, fill_value=0.0)
    fixed_total = min(max(float(fixed_weight_targets.sum()), 0.0), 1.0)

    if target_dimension_used == TARGET_DIMENSION_RISK_BUDGET:
        base_cash_total = min(max(float(preferred_cash_weights.sum()), 0.0), 1.0)
        fixed_total = min(fixed_total, max(1.0 - base_cash_total, 0.0))
        if risk_keys:
            if len(risk_keys) > 1:
                solver_members = [member_by_key[key] for key in risk_keys]
                return_window = _solver_return_window(
                    members=solver_members,
                    nav_series_by_member=nav_series_by_member,
                    as_of_date=as_of_date,
                    lookback_days=lookback_days,
                    calculation_frequency=calculation_frequency,
                )
            else:
                return_window = pd.DataFrame()
            reference = pd.Series(current_actual_weight_by_key, dtype="float64").reindex(risk_keys, fill_value=0.0)
            risk_solve = _solve_risk_budget_weights(
                target_shares=target_values.reindex(risk_keys, fill_value=0.0).to_numpy(dtype="float64"),
                return_window=return_window,
                reference_weights=reference.to_numpy(dtype="float64"),
                lookback_days=lookback_days,
            )
            solved_weights = risk_solve.weights
            risk_gap = risk_solve.max_abs_share_gap
            solver_kind = risk_solve.solver_kind
            implementation_weights = pd.Series(0.0, index=member_keys, dtype="float64")
            implementation_weights.loc[frozen_keys] = fixed_weight_targets.reindex(frozen_keys, fill_value=0.0)
            implementation_weights.loc[risk_keys] = solved_weights * max(1.0 - base_cash_total - fixed_total, 0.0)
            implementation_weights.loc[cash_like_keys] = _allocate_cash_weights(
                cash_index=cash_like_keys,
                preferred_weights=preferred_cash_weights,
                total_cash_weight=1.0
                - float(implementation_weights.loc[risk_keys].sum())
                - float(implementation_weights.loc[frozen_keys].sum()),
            )
        else:
            risk_solve = LocalRiskBudgetSolve(
                weights=np.asarray([], dtype="float64"),
                max_abs_share_gap=0.0,
                solver_kind="fixed-members" if frozen_keys else "single-member",
                solver_detail=None,
                covariance_model=None,
                covariance_observations=0,
                risk_contribution_mode=None,
            )
            implementation_weights = pd.Series(0.0, index=member_keys, dtype="float64")
            implementation_weights.loc[frozen_keys] = fixed_weight_targets.reindex(frozen_keys, fill_value=0.0)
            implementation_weights.loc[cash_like_keys] = _allocate_cash_weights(
                cash_index=cash_like_keys,
                preferred_weights=preferred_cash_weights,
                total_cash_weight=1.0 - float(implementation_weights.loc[frozen_keys].sum()),
            )
            risk_gap = 0.0
            solver_kind = "fixed-members" if frozen_keys else "single-member"
    else:
        risk_solve = LocalRiskBudgetSolve(
            weights=np.asarray([], dtype="float64"),
            max_abs_share_gap=None,
            solver_kind="weight-fixed-members" if frozen_keys else "weight",
            solver_detail=None,
            covariance_model=None,
            covariance_observations=0,
            risk_contribution_mode=None,
        )
        implementation_weights = pd.Series(0.0, index=member_keys, dtype="float64")
        implementation_weights.loc[frozen_keys] = fixed_weight_targets.reindex(frozen_keys, fill_value=0.0)
        active_weight_keys = [key for key in member_keys if key not in cash_like_keys and key not in frozen_keys]
        active_weight_targets = target_values.reindex(active_weight_keys, fill_value=0.0)
        active_weight_total = float(active_weight_targets.sum())
        active_budget = max(
            1.0 - float(implementation_weights.loc[frozen_keys].sum()) - float(preferred_cash_weights.sum()),
            0.0,
        )
        if active_weight_keys:
            if active_weight_total > 1e-12:
                implementation_weights.loc[active_weight_keys] = active_weight_targets / active_weight_total * active_budget
            else:
                implementation_weights.loc[active_weight_keys] = active_budget / float(len(active_weight_keys))
        implementation_weights.loc[cash_like_keys] = _allocate_cash_weights(
            cash_index=cash_like_keys,
            preferred_weights=preferred_cash_weights,
            total_cash_weight=1.0
            - float(implementation_weights.loc[active_weight_keys].sum())
            - float(implementation_weights.loc[frozen_keys].sum()),
        )
        risk_gap = None
        solver_kind = "weight-fixed-members" if frozen_keys else "weight"

    overlay_applies_to_risk_sleeves = apply_capital_overlay and capital_mode in {
        CAPITAL_MODE_FIXED_GROSS,
        CAPITAL_MODE_TARGET_VOLATILITY,
    }
    if overlay_applies_to_risk_sleeves and non_cash_keys:
        non_cash_total = float(implementation_weights.reindex(non_cash_keys, fill_value=0.0).sum())
        if non_cash_total > 1e-12:
            implementation_weights.loc[non_cash_keys] = (
                implementation_weights.reindex(non_cash_keys, fill_value=0.0) / non_cash_total
            )
            implementation_weights.loc[cash_like_keys] = 0.0
        else:
            implementation_weights.loc[non_cash_keys] = 1.0 / float(len(non_cash_keys))
            implementation_weights.loc[cash_like_keys] = 0.0
            warnings.append(
                f"{scope_label} had no positive risky target weight before capital overlay, so Research used equal risky-sleeve weights."
            )

    estimated_risk_sleeve_volatility = None
    effective_gross_exposure = None
    risky_allocation_scaling_factor = None
    if overlay_applies_to_risk_sleeves and non_cash_keys:
        risky_weights = implementation_weights.reindex(non_cash_keys, fill_value=0.0)
        if capital_mode == CAPITAL_MODE_FIXED_GROSS:
            effective_gross_exposure = float(gross_exposure or 1.0)
        elif capital_mode == CAPITAL_MODE_TARGET_VOLATILITY:
            solver_members = [member_by_key[key] for key in non_cash_keys]
            return_window = _solver_return_window(
                members=solver_members,
                nav_series_by_member=nav_series_by_member,
                as_of_date=as_of_date,
                lookback_days=lookback_days,
                calculation_frequency=calculation_frequency,
            )
            estimated_risk_sleeve_volatility = _annualized_portfolio_volatility(
                return_window,
                risky_weights,
                lookback_days=lookback_days,
            )
            if estimated_risk_sleeve_volatility and estimated_risk_sleeve_volatility > 0 and target_volatility:
                effective_gross_exposure = float(target_volatility) / float(estimated_risk_sleeve_volatility)
            else:
                effective_gross_exposure = 1.0
                if target_volatility:
                    warnings.append(
                        f"{scope_label} could not estimate risky-sleeve volatility on {as_of_date.isoformat()}, so capital overlay stayed at unit gross."
                    )
            if max_gross_exposure is not None:
                effective_gross_exposure = min(float(effective_gross_exposure), float(max_gross_exposure))
        if effective_gross_exposure is not None:
            risky_allocation_scaling_factor = float(effective_gross_exposure)
            implementation_weights.loc[non_cash_keys] = risky_weights * risky_allocation_scaling_factor
            if cash_like_keys:
                implementation_weights.loc[cash_like_keys] = _allocate_cash_weights(
                    cash_index=cash_like_keys,
                    preferred_weights=preferred_cash_weights,
                    total_cash_weight=1.0
                    - float(implementation_weights.loc[non_cash_keys].sum()),
                )
            else:
                residual_cash = 1.0 - float(implementation_weights.loc[non_cash_keys].sum())
                if abs(residual_cash) > 1e-9:
                    warnings.append(
                        f"{scope_label} has no cash-like member, so {residual_cash:.2%} capital-overlay residual is implicit cash."
                    )

    for row in resolved_rows:
        member_key = f"{row['member_type']}::{row['member_id']}"
        row["implementation_weight"] = float(implementation_weights.get(member_key, 0.0))

    current_weights = pd.Series(current_actual_weight_by_key, dtype="float64").reindex(member_keys, fill_value=0.0)
    current_risk_share_by_key = _estimate_scope_risk_share_map(
        members=members,
        nav_series_by_member=current_nav_series_by_member,
        risk_keys=non_cash_keys,
        weights_by_key=current_weights,
        as_of_date=as_of_date,
        lookback_days=lookback_days,
        calculation_frequency=calculation_frequency,
        contribution_mode=risk_solve.risk_contribution_mode or RESEARCH_RISK_CONTRIBUTION_MODE,
    )
    for key in cash_like_keys:
        current_risk_share_by_key[key] = 0.0
    gap_turnover = float(0.5 * np.abs(implementation_weights - current_weights).sum())
    max_weight_gap = float(np.max(np.abs(current_weights - implementation_weights))) if len(member_keys) else 0.0
    solve_event = {
        "as_of_date": as_of_date.isoformat(),
        "scope_node_id": scope_node_id,
        "scope_label": scope_label,
        "scope_path": scope_path,
        "scope_depth": scope_depth,
        "requested_target_dimension": target_dimension,
        "taxonomy_default_target_dimension": default_target_dimension,
        "target_dimension": target_dimension_used,
        "solver_kind": solver_kind,
        "solver_detail": risk_solve.solver_detail,
        "solver_message": risk_solve.message,
        "covariance_model": risk_solve.covariance_model,
        "covariance_observations": risk_solve.covariance_observations,
        "risk_contribution_mode": risk_solve.risk_contribution_mode,
        "calculation_frequency": calculation_frequency,
        "gap_turnover": gap_turnover,
        "current_weight_total": float(current_weights.sum()),
        "target_weight_total": float(implementation_weights.sum()),
        "max_weight_gap": max_weight_gap,
        "max_risk_share_gap": risk_gap,
        "estimated_risk_sleeve_volatility": estimated_risk_sleeve_volatility,
        "target_volatility": target_volatility if apply_capital_overlay else None,
        "gross_exposure": effective_gross_exposure,
        "risky_allocation_scaling_factor": risky_allocation_scaling_factor,
        "member_count": len(members),
        "scope_solve_count": len(child_scope_solve_events) + 1,
    }

    summary_rows: list[dict[str, object]] = []
    leaf_rows: list[dict[str, object]] = []
    for member_key in member_keys:
        member = member_by_key[member_key]
        resolved_target = next(
            (
                row
                for row in resolved_rows
                if row["member_type"] == member.member_type and row["member_id"] == member.member_id
            ),
            None,
        )
        current_weight = _safe_float(current_weights.get(member_key))
        implementation_weight = _safe_float(implementation_weights.get(member_key))
        current_risk_share = _safe_float(current_risk_share_by_key.get(member_key))
        summary_rows.append(
            {
                "member_type": member.member_type,
                "member_id": member.member_id,
                "label": member.label,
                "default_target_dimension": member.default_target_dimension,
                "selected_target_dimension": resolved_target.get("selected_dimension") if resolved_target else None,
                "source_target_set_type": resolved_target.get("source_target_set_type") if resolved_target else None,
                "current_weight": current_weight,
                "current_risk_share": current_risk_share,
                "target_weight": implementation_weight,
                "weight_change": (
                    float(implementation_weight - current_weight)
                    if implementation_weight is not None and current_weight is not None
                    else None
                ),
                "configured_weight": _safe_float(resolved_target.get("target_weight")) if resolved_target else None,
                "configured_risk_share": _safe_float(resolved_target.get("target_risk_share")) if resolved_target else None,
                "selected_target_value": _safe_float(resolved_target.get("selected_value")) if resolved_target else None,
            }
        )
        child_result = child_results_by_key.get(member_key)
        if child_result is not None:
            for child_leaf in child_result.leaf_target_rows:
                child_current_weight = _safe_float(child_leaf.get("current_weight"))
                child_target_weight = _safe_float(child_leaf.get("target_weight"))
                child_current_risk_share = _safe_float(child_leaf.get("current_risk_share"))
                child_risk_target = _safe_float(child_leaf.get("configured_risk_share"))
                target_weight = (
                    None
                    if implementation_weight is None or child_target_weight is None
                    else float(implementation_weight * child_target_weight)
                )
                current_leaf_weight = (
                    None
                    if current_weight is None or child_current_weight is None
                    else float(current_weight * child_current_weight)
                )
                leaf_rows.append(
                    {
                        "member_type": child_leaf.get("member_type"),
                        "member_id": child_leaf.get("member_id"),
                        "label": child_leaf.get("label"),
                        "scope_path": child_leaf.get("scope_path") or child_result.scope_path,
                        "member_path": child_leaf.get("member_path"),
                        "default_target_dimension": child_leaf.get("default_target_dimension"),
                        "selected_target_dimension": child_leaf.get("selected_target_dimension"),
                        "source_target_set_type": child_leaf.get("source_target_set_type"),
                        "current_weight": current_leaf_weight,
                        "current_risk_share": child_current_risk_share,
                        "target_weight": target_weight,
                        "weight_change": (
                            float(target_weight - current_leaf_weight)
                            if target_weight is not None and current_leaf_weight is not None
                            else None
                        ),
                        "configured_weight": child_leaf.get("configured_weight"),
                        "configured_risk_share": child_risk_target,
                        "selected_target_value": child_leaf.get("selected_target_value"),
                    }
                )
            continue
        member_path = f"{scope_path} / {member.label}" if scope_path else member.label
        leaf_rows.append(
            {
                "member_type": member.member_type,
                "member_id": member.member_id,
                "label": member.label,
                "scope_path": scope_path,
                "member_path": member_path,
                "default_target_dimension": member.default_target_dimension,
                "selected_target_dimension": resolved_target.get("selected_dimension") if resolved_target else None,
                "source_target_set_type": resolved_target.get("source_target_set_type") if resolved_target else None,
                "current_weight": current_weight,
                "current_risk_share": current_risk_share,
                "target_weight": implementation_weight,
                "weight_change": (
                    float(implementation_weight - current_weight)
                    if implementation_weight is not None and current_weight is not None
                    else None
                ),
                "configured_weight": _safe_float(resolved_target.get("target_weight")) if resolved_target else None,
                "configured_risk_share": _safe_float(resolved_target.get("target_risk_share")) if resolved_target else None,
                "selected_target_value": _safe_float(resolved_target.get("selected_value")) if resolved_target else None,
            }
        )

    scope_return_window = _solver_return_window(
        members=members,
        nav_series_by_member=nav_series_by_member,
        as_of_date=as_of_date,
        lookback_days=lookback_days,
        calculation_frequency=calculation_frequency,
    )
    if scope_return_window.empty:
        scope_returns = pd.Series(dtype="float64")
    else:
        scope_returns = _weighted_complete_return_series(scope_return_window, implementation_weights)
    current_scope_return_window = _solver_return_window(
        members=members,
        nav_series_by_member=current_nav_series_by_member,
        as_of_date=as_of_date,
        lookback_days=lookback_days,
        calculation_frequency=calculation_frequency,
    )
    if current_scope_return_window.empty:
        current_scope_returns = pd.Series(dtype="float64")
    else:
        current_scope_returns = _weighted_complete_return_series(current_scope_return_window, current_weights)

    return ScopeTargetSolveResult(
        scope_node_id=scope_node_id,
        scope_label=scope_label,
        scope_path=scope_path,
        default_target_dimension=default_target_dimension,
        scope_depth=scope_depth,
        member_source=member_source,
        return_series=scope_returns.astype("float64"),
        current_return_series=current_scope_returns.astype("float64"),
        member_target_rows=summary_rows,
        leaf_target_rows=leaf_rows,
        solve_event=solve_event,
        scope_solve_events=[*child_scope_solve_events, deepcopy(solve_event)],
        warnings=list(dict.fromkeys(item for item in warnings if item)),
        resolved_target_rows=deepcopy(resolved_rows),
    )


def _current_scope_actuals(
    state: TaxonomyResearchState,
    *,
    scope_node_id: str | None,
    as_of_date: date,
) -> tuple[list[dict[str, object]], list[str]]:
    portfolio = get_portfolio(state.portfolio_id)
    if portfolio is None:
        raise ValueError("Portfolio not found.")
    accounts = list_accounts(state.portfolio_id)
    transactions = list_transactions(state.portfolio_id)
    statement = build_holdings_report(
        portfolio,
        accounts,
        transactions,
        as_of_date=as_of_date,
    )
    account_workspace = build_account_workspace(
        state.portfolio_id,
        accounts,
        transactions,
        base_currency=state.base_currency,
        as_of_date=as_of_date,
    )

    position_value_by_instrument: dict[str, float] = {}
    for position in list(statement.get("positions") or []):
        instrument_id = str(position.get("instrument_id") or "")
        if instrument_id:
            position_value_by_instrument[instrument_id] = float(_safe_float(position.get("market_value_base")) or 0.0)

    visible_cash_accounts = [
        account_row
        for account_row in list(account_workspace.get("accounts") or [])
        if str((account_row.get("account") or {}).get("account_type") or "") == "deposit_account"
    ]
    cash_value_by_account = {
        str((account_row.get("account") or {}).get("account_id") or ""): float(
            _safe_float(account_row.get("account_value_base")) or 0.0
        )
        for account_row in visible_cash_accounts
        if str((account_row.get("account") or {}).get("account_id") or "")
    }

    direct_position_membership: dict[str, str] = {}
    direct_cash_membership: dict[str, str] = {}
    for node_id, assignments in state.direct_assignments_by_node.items():
        for assignment in assignments:
            target_scope = str(assignment.get("target_scope") or "")
            target_entity_id = str(assignment.get("target_entity_id") or "")
            if target_scope == TARGET_MEMBER_INSTRUMENT:
                direct_position_membership[target_entity_id] = node_id
            elif target_scope == TARGET_MEMBER_CASH:
                direct_cash_membership[target_entity_id] = node_id

    node_value_map: dict[str, float] = {node_id: 0.0 for node_id in state.node_by_id}
    unassigned_value = 0.0

    for instrument_id, market_value_base in position_value_by_instrument.items():
        node_id = direct_position_membership.get(instrument_id)
        if not node_id:
            unassigned_value += market_value_base
            continue
        current_node_id = node_id
        while current_node_id:
            node_value_map[current_node_id] = float(node_value_map.get(current_node_id, 0.0) + market_value_base)
            current_node_id = str(state.node_by_id.get(current_node_id, {}).get("parent_taxonomy_node_id") or "") or None

    for account_id, cash_value_base in cash_value_by_account.items():
        node_id = direct_cash_membership.get(account_id)
        if not node_id:
            unassigned_value += cash_value_base
            continue
        current_node_id = node_id
        while current_node_id:
            node_value_map[current_node_id] = float(node_value_map.get(current_node_id, 0.0) + cash_value_base)
            current_node_id = str(state.node_by_id.get(current_node_id, {}).get("parent_taxonomy_node_id") or "") or None

    scope_members, member_source = _scope_members(state, scope_node_id=scope_node_id)
    scope_total_value = (
        sum(node_value_map.get(node_id, 0.0) for node_id in state.children_by_parent.get(None, [])) + unassigned_value
        if scope_node_id is None
        else node_value_map.get(scope_node_id, 0.0)
    )
    if abs(scope_total_value) <= 1e-9:
        scope_total_value = 0.0

    rendered_rows: list[dict[str, object]] = []
    warnings: list[str] = []
    for member in scope_members:
        if member.member_type == TARGET_MEMBER_NODE:
            actual_value = node_value_map.get(member.member_id, 0.0)
        elif member.member_type == TARGET_MEMBER_INSTRUMENT:
            actual_value = position_value_by_instrument.get(member.member_id, 0.0)
        else:
            actual_value = cash_value_by_account.get(member.member_id, 0.0)
        actual_weight = actual_value / scope_total_value if abs(scope_total_value) > 1e-9 else None
        rendered_rows.append(
            {
                "member_type": member.member_type,
                "member_id": member.member_id,
                "label": member.label,
                "current_weight": actual_weight,
                "current_value_base": actual_value,
            }
        )

    if scope_node_id is None and abs(unassigned_value) > 1e-9:
        warnings.append("Current portfolio still has unassigned holdings or cash outside the selected planning taxonomy.")
        rendered_rows.append(
            {
                "member_type": "unassigned",
                "member_id": "unassigned",
                "label": "Unassigned",
                "current_weight": (unassigned_value / scope_total_value) if abs(scope_total_value) > 1e-9 else None,
                "current_value_base": unassigned_value,
            }
        )

    return rendered_rows, warnings


def _build_leaf_target_weight_gaps(
    *,
    leaf_target_rows: list[dict[str, object]],
    base_currency: str,
) -> list[dict[str, object]]:
    gaps: list[dict[str, object]] = []
    for row in leaf_target_rows:
        current_weight = _safe_float(row.get("current_weight"))
        target_weight = _safe_float(row.get("target_weight"))
        gap = (
            None
            if current_weight is None or target_weight is None
            else float(target_weight - current_weight)
        )
        action = "Review"
        if gap is not None:
            if gap > 0.01:
                action = "Increase"
            elif gap < -0.01:
                action = "Reduce"
            else:
                action = "Hold"
        gaps.append(
            {
                "member_type": row.get("member_type"),
                "member_id": row.get("member_id"),
                "label": row.get("label"),
                "current_weight": current_weight,
                "target_weight": target_weight,
                "gap": gap,
                "current_value_base": None,
                "base_currency": base_currency,
                "action": action,
            }
        )
    gaps.sort(key=lambda item: abs(_safe_float(item.get("gap")) or 0.0), reverse=True)
    return gaps


def _build_taxonomy_state(
    portfolio_id: str,
    *,
    planning_taxonomy_id: str,
    as_of_date: date,
    frozen_taxonomy_node_ids: list[str] | None = None,
) -> TaxonomyResearchState:
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise ValueError("Portfolio not found.")

    taxonomy = next(
        (
            item
            for item in list_taxonomies(portfolio_id)
            if str(item.get("taxonomy_id") or "") == planning_taxonomy_id
        ),
        None,
    )
    if taxonomy is None:
        raise ValueError("Planning taxonomy not found.")

    node_rows = [
        item
        for item in list_taxonomy_nodes(portfolio_id)
        if str(item.get("taxonomy_id") or "") == planning_taxonomy_id and str(item.get("status") or "") == "active"
    ]
    node_rows.sort(
        key=lambda item: (
            str(item.get("parent_taxonomy_node_id") or ""),
            int(item.get("sort_order") or 0),
            str(item.get("node_name") or ""),
            str(item.get("taxonomy_node_id") or ""),
        )
    )
    node_by_id = {str(item["taxonomy_node_id"]): item for item in node_rows}
    children_by_parent: dict[str | None, list[str]] = defaultdict(list)
    for node in node_rows:
        parent_id = str(node.get("parent_taxonomy_node_id") or "") or None
        children_by_parent[parent_id].append(str(node["taxonomy_node_id"]))

    node_path_by_id = {ROOT_SCOPE_MEMBER_ID: ROOT_SCOPE_LABEL}
    node_depth_by_id: dict[str, int] = {}

    def assign_paths(parent_id: str | None, parent_path: str, depth: int) -> None:
        for node_id in children_by_parent.get(parent_id, []):
            node = node_by_id[node_id]
            node_path_by_id[node_id] = f"{parent_path} / {node['node_name']}" if parent_path else str(node["node_name"])
            node_depth_by_id[node_id] = depth
            assign_paths(node_id, node_path_by_id[node_id], depth + 1)

    assign_paths(None, ROOT_SCOPE_LABEL, 1)

    node_subtree_by_id: dict[str, set[str]] = {}

    def collect_subtree(node_id: str) -> set[str]:
        subtree = {node_id}
        for child_id in children_by_parent.get(node_id, []):
            subtree.update(collect_subtree(child_id))
        node_subtree_by_id[node_id] = subtree
        return subtree

    for root_id in children_by_parent.get(None, []):
        collect_subtree(root_id)

    assignments = [
        item
        for item in list_taxonomy_assignments(portfolio_id)
        if str(item.get("taxonomy_id") or "") == planning_taxonomy_id
        and str(item.get("status") or "") == "active"
        and _period_active(
            effective_from=item.get("effective_from"),
            effective_to=item.get("effective_to"),
            as_of_date=as_of_date,
        )
    ]
    assignments.sort(
        key=lambda item: (
            str(item.get("taxonomy_node_id") or ""),
            str(item.get("target_scope") or ""),
            str(item.get("target_entity_id") or ""),
        )
    )
    direct_assignments_by_node: dict[str, list[dict[str, object]]] = defaultdict(list)
    for assignment in assignments:
        direct_assignments_by_node[str(assignment["taxonomy_node_id"])].append(assignment)

    target_sets = [
        item
        for item in list_target_sets(portfolio_id, taxonomy_id=planning_taxonomy_id)
        if str(item.get("status") or "") == "active"
    ]
    target_sets_by_scope_type: dict[tuple[str | None, str], list[dict[str, object]]] = defaultdict(list)
    for item in target_sets:
        comparator_node_id = str(item.get("comparator_taxonomy_node_id") or "") or None
        target_sets_by_scope_type[(comparator_node_id, str(item.get("target_set_type") or ""))].append(item)

    target_lines_by_set_id: dict[str, dict[tuple[str, str], dict[str, object]]] = defaultdict(dict)
    for line in list_target_set_lines(portfolio_id, taxonomy_id=planning_taxonomy_id):
        target_set_id = str(line.get("target_set_id") or "")
        if not target_set_id:
            continue
        member_key = (str(line.get("target_member_type") or ""), str(line.get("target_member_id") or ""))
        target_lines_by_set_id[target_set_id][member_key] = line

    account_name_by_id = {
        str(item.get("account_id") or ""): str(item.get("account_name") or "")
        for item in list_accounts(portfolio_id)
        if item.get("account_id")
    }

    return TaxonomyResearchState(
        portfolio_id=portfolio_id,
        planning_taxonomy_id=planning_taxonomy_id,
        taxonomy_name=str(taxonomy.get("name") or planning_taxonomy_id),
        root_default_target_dimension=str(taxonomy.get("root_default_target_dimension") or TARGET_DIMENSION_WEIGHT),
        base_currency=_normalized_currency(portfolio.get("base_currency")),
        as_of_date=as_of_date,
        node_by_id=node_by_id,
        children_by_parent=children_by_parent,
        node_path_by_id=node_path_by_id,
        node_depth_by_id=node_depth_by_id,
        node_subtree_by_id=node_subtree_by_id,
        direct_assignments_by_node=direct_assignments_by_node,
        target_sets_by_scope_type=target_sets_by_scope_type,
        target_lines_by_set_id=target_lines_by_set_id,
        account_name_by_id=account_name_by_id,
        instrument_detail_cache={},
        direct_fx_instruments=_build_direct_fx_instrument_map(),
        frozen_taxonomy_node_ids=frozenset(
            str(item).strip() for item in (frozen_taxonomy_node_ids or []) if str(item).strip()
        ),
    )


def build_research_scope_options(
    portfolio_id: str,
    *,
    planning_taxonomy_id: str | None,
    as_of_date: date,
) -> list[dict[str, object]]:
    if not planning_taxonomy_id:
        return []
    state = _build_taxonomy_state(
        portfolio_id,
        planning_taxonomy_id=planning_taxonomy_id,
        as_of_date=as_of_date,
    )
    options: list[dict[str, object]] = [
        {
            "taxonomy_node_id": None,
            "label": ROOT_SCOPE_LABEL,
            "path": ROOT_SCOPE_LABEL,
            "depth": 0,
            "default_target_dimension": state.root_default_target_dimension,
            "has_children": bool(state.children_by_parent.get(None)),
        }
    ]
    for node_id in state.node_by_id:
        node = state.node_by_id[node_id]
        options.append(
            {
                "taxonomy_node_id": node_id,
                "label": str(node.get("node_name") or node_id),
                "path": state.node_path_by_id.get(node_id, str(node.get("node_name") or node_id)),
                "depth": state.node_depth_by_id.get(node_id, 0),
                "default_target_dimension": str(node.get("default_target_dimension") or TARGET_DIMENSION_WEIGHT),
                "has_children": bool(state.children_by_parent.get(node_id)),
            }
            )
    return options


def build_research_calculation_frequency_profile(
    portfolio_id: str,
    *,
    planning_taxonomy_id: str | None,
    comparator_taxonomy_node_id: str | None,
    as_of_date: date,
    lookback_days: int,
    requested_frequency: str,
) -> dict[str, object]:
    if not planning_taxonomy_id:
        return calculation_frequency_profile(
            requested_frequency=requested_frequency,
            source_frequencies=[],
        )
    state = _build_taxonomy_state(
        portfolio_id,
        planning_taxonomy_id=planning_taxonomy_id,
        as_of_date=as_of_date,
    )
    if comparator_taxonomy_node_id and comparator_taxonomy_node_id not in state.node_by_id:
        raise ValueError("Selected research scope was not found in the planning taxonomy.")
    start_date = as_of_date - pd.Timedelta(days=max(int(lookback_days), 1))
    start_day = start_date.date() if hasattr(start_date, "date") else start_date
    source_frequencies = _scope_source_frequencies(
        state,
        scope_node_id=comparator_taxonomy_node_id,
        start_date=start_day,
        end_date=as_of_date,
    )
    return calculation_frequency_profile(
        requested_frequency=requested_frequency,
        source_frequencies=source_frequencies,
    )


def solve_current_target_weights(
    portfolio_id: str,
    *,
    planning_taxonomy_id: str,
    comparator_taxonomy_node_id: str | None,
    as_of_date: date,
    lookback_days: int,
    calculation_frequency: str = "auto",
    target_dimension: str,
    capital_mode: str,
    gross_exposure: float | None,
    target_volatility: float | None,
    max_gross_exposure: float | None,
    frozen_taxonomy_node_ids: list[str] | None,
) -> dict[str, object]:
    state = _build_taxonomy_state(
        portfolio_id,
        planning_taxonomy_id=planning_taxonomy_id,
        as_of_date=as_of_date,
        frozen_taxonomy_node_ids=frozen_taxonomy_node_ids,
    )
    if comparator_taxonomy_node_id and comparator_taxonomy_node_id not in state.node_by_id:
        raise ValueError("Selected research scope was not found in the planning taxonomy.")
    start_date = as_of_date - pd.Timedelta(days=max(int(lookback_days), 1))
    start_day = start_date.date() if hasattr(start_date, "date") else start_date
    source_frequencies = _scope_source_frequencies(
        state,
        scope_node_id=comparator_taxonomy_node_id,
        start_date=start_day,
        end_date=as_of_date,
    )
    frequency_profile = calculation_frequency_profile(
        requested_frequency=calculation_frequency,
        source_frequencies=source_frequencies,
    )
    resolved_calculation_frequency = str(frequency_profile["resolved_frequency"])

    scope_result = _solve_current_scope(
        state,
        scope_node_id=comparator_taxonomy_node_id,
        as_of_date=as_of_date,
        lookback_days=lookback_days,
        calculation_frequency=resolved_calculation_frequency,  # type: ignore[arg-type]
        target_dimension=target_dimension,
        capital_mode=capital_mode,
        gross_exposure=gross_exposure,
        target_volatility=target_volatility,
        max_gross_exposure=max_gross_exposure,
        apply_capital_overlay=comparator_taxonomy_node_id is None,
    )
    actual_rows, actual_warnings = _current_scope_actuals(
        state,
        scope_node_id=comparator_taxonomy_node_id,
        as_of_date=as_of_date,
    )
    warnings = list(dict.fromkeys([*scope_result.warnings, *actual_warnings]))
    target_weight_gaps = _build_leaf_target_weight_gaps(
        leaf_target_rows=scope_result.leaf_target_rows,
        base_currency=state.base_currency,
    )
    return {
        "portfolio_id": portfolio_id,
        "planning_taxonomy_id": planning_taxonomy_id,
        "planning_taxonomy_name": state.taxonomy_name,
        "scope": {
            "taxonomy_node_id": comparator_taxonomy_node_id,
            "label": scope_result.scope_label,
            "path": scope_result.scope_path,
            "depth": scope_result.scope_depth,
            "default_target_dimension": scope_result.default_target_dimension,
            "member_source": scope_result.member_source,
        },
        "member_targets": deepcopy(scope_result.member_target_rows),
        "leaf_targets": deepcopy(scope_result.leaf_target_rows),
        "actual_rows": deepcopy(actual_rows),
        "resolved_target_rows": deepcopy(scope_result.resolved_target_rows),
        "solve_event": deepcopy(scope_result.solve_event),
        "scope_solve_events": deepcopy(scope_result.scope_solve_events),
        "target_weight_gaps": target_weight_gaps,
        "warnings": warnings,
        "return_observations": float(len(scope_result.return_series)),
        "calculation_frequency": frequency_profile,
    }
